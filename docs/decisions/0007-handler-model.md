# Web ADR 0007 — Handler model: handlers, middleware, routing and application state

- Status: **Accepted** by the owner, 2026-11-02 (every question of §6 as recommended; §6.2 is settled by M2's
  spike, as recommended).
- Scope: the application-facing API of `web_http` and `web_server`: how a handler is written, how middleware
  composes, how routes are stored, how state reaches handlers, and how errors become responses. Milestone M2 (first
  use), stable from v0.1.

## 1. Context

What CeeSafe allows, checked 2026-11-02 in `tests/probes/`:

- **Closures:** a closure stored in a field cannot be called (CeeSafe `AGENTS.md`, "Common mistakes"). Handlers
  cannot be stored closures.
- **Interfaces:** an interface may be generic (`interface Handler<S>`). `dyn Handler<S>` works with async methods
  (`handler-hooks.cee`, prints `1015`).
- **Impls:** only for concrete, unscoped types. A generic middleware `Logged<H: Handler>` cannot implement
  `Handler` (Web ADR 0001 §1).
- **Wrapping through `dyn`:** a middleware that holds `Box<dyn Handler<S>>` and awaits it is recursion through
  `await` (N0343). It compiles only with `Async.boxed(alloc, …)`, which costs one frame allocation per layer per
  request (`handler-boxed.cee`).
- **Bodies and writers:** request bodies and response writers are concrete, region-scoped types (Web ADR 0003 §3.3,
  §3.5).
- **Methods:** a method cannot declare its own region. Elided regions in method signatures work (Web ADR 0003 §1).

## 2. Options for middleware

| | A. Hook pipeline (proposed default) | B. Wrapping through `dyn` + `Async.boxed` | C. Generic wrappers |
|---|---|---|---|
| Shape | `before(ctx, request) -> Continue/Respond`, `after(ctx, response head)` | `handle(…)` calls `next.handle(…)` | `Logged<H>` |
| Allocation | none | one frame per layer per request (an arena per request makes it cheap) | none |
| Expressiveness | covers auth, logging, headers, CORS, limits, metrics | everything, including catching errors and timing the whole call | everything |
| Possible in v0 | yes | yes | no (needs U10) |

**A as the default, B available** for middleware that must wrap the handler.

## 3. Decision

### 3.1 Handler

```ceesafe
public interface Handler<S> {
    async fn handle(
        borrow this,
        ctx: borrow mut RequestContext<S>,
        request: borrow Request,
        body: borrow mut RequestBody,
        response: borrow mut ResponseWriter,
    ) -> Result<void, HandlerError>;
}
```

- A handler is a struct implementing `Handler<S>`. It has its own fields: configuration, and `Arc`s to shared
  services.
- `S` is the application's state type. The server owns one `S` and lends it as `ctx.state()`, a `borrow S`. Mutable
  shared state is the application's own `AsyncMutex`/atomics inside `S`.
- `RequestContext<S>` holds:
  - the `ConnectionContext` (peer, id, `draining`);
  - route parameters (views into the target);
  - the request's `Deadline`;
  - a per-request scratch arena (for `Async.boxed` and small allocations, reset after the request);
  - a fixed set of typed extension slots that middleware fills (e.g. `authenticated: Principal?`). There is no
    `Any`; extension types are fields the application declares in `S`'s companion `RequestExt` type, if needed.
- `HandlerError` is an enum. `Status(StatusCode)` produces that response if no head was sent. `Io(NetError)` closes
  the connection (H1) or resets the stream (H2/H3). `Internal` gives 500, or a reset if a head was sent. There are no
  strings; reasons are logged by code.
- **Handlers must not panic.** `!` and `panic` abort the process in CeeSafe. Server code bans them outside tests
  (a CI grep), and handler examples use `?`.

### 3.2 Middleware

```ceesafe
public enum Flow { Continue, Responded }

public interface Middleware<S> {
    async fn before(borrow this, ctx: borrow mut RequestContext<S>, request: borrow Request,
                    response: borrow mut ResponseWriter) -> Result<Flow, HandlerError>;
    async fn after(borrow this, ctx: borrow mut RequestContext<S>, request: borrow Request,
                   head: borrow mut ResponseHead) -> Result<void, HandlerError>;
}
```

- The pipeline is a `Vector<Box<dyn Middleware<S>>>`. The server runs every `before` in order:
  - `Responded` (a middleware answered: 401, a CORS preflight) skips the handler and the remaining `before`s;
  - then the handler runs;
  - then every `after` in reverse order. `after` runs just before the response head is sent, so it can add headers.

  No allocation is involved (`handler-hooks.cee`).
- **Wrapping middleware** (option B), for cases hooks cannot express (timing the whole call including the body,
  converting errors): a `WrappingHandler<S>` holds `next: Box<dyn Handler<S>>` and awaits it through
  `Async.boxed(ctx.arena(), next.handle(…))`, with the arena's allocation failure turned into `HandlerError.Internal`
  (`handler-boxed.cee`). It is documented as the costlier form.
- Per-route middleware: a route can carry its own pipeline, which runs after the global one.

### 3.3 Routing

- The `Router<S>` is a radix tree over path segments. Each node can hold:
  - literal children;
  - one `:param` child;
  - one `*rest` child;
  - per-method leaves of `Box<dyn Handler<S>>`.
- Matching:
  - the target is percent-decoded per segment, and only for comparison; parameters are views when no decoding was
    needed, and copied into the arena otherwise;
  - literal segments take precedence over a parameter, and a parameter over the wildcard.
- Responses from routing:
  - **404** when no path matches;
  - **405** with `Allow` when the path matches but the method does not;
  - automatic **HEAD** for GET routes; **OPTIONS** with `Allow`, unless a CORS middleware answers.
- Building: `router.get("/users/:id", UserHandler { … })?` moves the handler into a box. Allocation failure is a
  `Result`.
- The tree uses `Vector`s; a `HashMap` (upstream U9) is a later optimisation for wide nodes.

### 3.4 Server

```ceesafe
var server = Server<AppState>.create(state, limits)?;
server.use(Box.create(RequestLog {})?)?;
server.router().get("/", Index {})?;
await server.listen(SocketAddress.v4([0, 0, 0, 0], 8080), PlainAcceptor {})?;
```

- `listen` runs the M1 accept loop. A TLS server passes `web_tls`'s `TlsAcceptor` instead (Web ADR 0004 §3.5).
- `server.shutdown(grace)` raises the drain signal (Web ADR 0001 §3.5).

## 4. Consequences

- One `dyn` call per handler and per middleware hook, per request. This is measured in M2 against a hand-written
  monomorphic handler, and accepted if under 2 % of a minimal request.
- There are no closure handlers. Examples and docs show small structs. A `fn`-pointer adapter (`FnHandler`, holding
  a plain function, not a closure) may be possible and is investigated in M2.
- Generic middleware (option C) becomes possible if CeeSafe gains U10, as an addition.

## 5. Tests

- Probes: `handler-hooks.cee`, `handler-boxed.cee` (kept).
- Middleware order: the `before` order, the reverse `after` order, a `Responded` short-circuit, and errors from each
  stage mapped to the right status.
- Router: precedence; 404/405/`Allow`; HEAD and OPTIONS; percent-encoded segments; property tests against a Python
  reference router.
- No panics: a CI grep for `!` and `panic` outside `tests/` in `web_server`, `web_http` and the engines.

## 6. Questions for the owner (answered 2026-11-02: each as recommended)

1. Is the hooks default, with wrapping as an opt-in, acceptable (§3.2)? Recommended: yes. **Yes.**
2. Typed per-request extensions as fields of an application-declared `RequestExt` (§3.1), or a fixed small set
   chosen by the framework? Recommended: application-declared, as a second type parameter
   (`Handler<S, X>`) if M2's spike shows it reads well; otherwise a fixed set. **As recommended: M2's spike decides,
   and its result is recorded here as an amendment.**

## 7. Amendment 1 (2026-11-02, M2)

1. **The handler takes one `Exchange`** (Web ADR 0003 amendment 3):

   ```ceesafe
   async fn handle(borrow this, ctx: borrow mut RequestContext<S>, request: borrow Request,
                   exchange: borrow mut Exchange) -> Result<void, HandlerError>;
   ```

2. **`after` hooks get the request and the head, not the context.** A head is sent when the handler returns or when
   its body outgrows the response buffer, which can happen inside `exchange.write` while the handler holds
   `borrow mut ctx`. So the exchange calls `Application.beforeHead(request, head)`, and `Middleware.after(request,
   head)` has no `ctx`. A middleware that must carry data from `before` to `after` keeps it in the response head
   (a field) or in its own atomics.
3. **Responses the driver makes itself** (a malformed request, a handler's `Status` error) do not run `after` hooks in
   M2; that is revisited with CORS (M5's middleware set).

## 8. Amendment 2 (2026-11-02, M2: `web_server` merged on ceesafec 0.9.2)

1. **No delegating impls.** CeeSafe 0.9.0's generic impls (U10, CeeSafe ADR 0054) let `web_server` write
   `impl<S> Application for Server<S>` and `impl<S> ConnectionHandler for Server<S>` once, so an application writes
   no glue. This replaces M2.md's interim decision (two delegating impls per application). Using them needed CeeSafe
   0.9.2's fixes B9–B12 (`docs/ceesafe-upstream-work.md`).
2. **`run` instead of `listen` and `shutdown`** (§3.4). `server.run(incoming, acceptor, connectionLimits, drain)`
   serves through M1's `ConnectionServer.run`. The caller binds the `Listener` (or passes a `MockIncoming` in tests)
   and owns the `Drain`, so a shutdown is `drain.start()`, from a signal handler, a route (`ctx.connection().drain()`)
   or a test; the server holds no listener or drain of its own.
3. **Routing answers after the `before` hooks.** The route is found first, then every `before` runs, then the handler
   or the router's own 404/405/204. So middleware sees, can answer and can edit (through `after`) every routed
   response, 404 and 405 included. `OPTIONS *` is 204 with no `Allow`.
4. **`Server<S>.create(state, limits)` takes the HTTP limits** (`web_http.Limits`); the connection limits go to `run`.
5. Tests: `packages/web_server/tests/server.cee` (middleware order and short-circuit, routing responses byte for byte,
   pipelining, and six connections through `Server.run` over `MockIncoming`), `examples/server`, and
   `tests/interop/server.py`.
