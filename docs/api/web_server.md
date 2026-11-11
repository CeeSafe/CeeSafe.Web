# web_server

- [struct RequestContext](#struct-requestcontext)
- [interface Handler](#interface-handler)
- [enum Flow](#enum-flow)
- [interface Middleware](#interface-middleware)
- [enum RouteError](#enum-routeerror)
- [fn maxParams](#fn-maxparams)
- [struct Params](#struct-params)
- [enum RouteMatch](#enum-routematch)
- [struct Router](#struct-router)
- [struct Range](#struct-range)
- [fn percentDecode](#fn-percentdecode)
- [struct Server](#struct-server)
- [struct HttpService](#struct-httpservice)
- [fn serviceOf](#fn-serviceof)
- [fn applicationOf](#fn-applicationof)

## struct RequestContext

```ceesafe
public struct RequestContext<region r, S>
```

What a handler knows besides the request.

### RequestContext.create

```ceesafe
public fn create(
    state: borrow(r) S,
    connection: borrow(r) ConnectionContext<r>,
    router: borrow(r) Router<S>,
    path: StringView<r>,
    params: Params,
) -> RequestContext<r, S>
```

A context for one request.

### RequestContext.state

```ceesafe
public fn state(borrow this) -> borrow(r) S
```

The application's state.

### RequestContext.connection

```ceesafe
public fn connection(borrow this) -> borrow(r) ConnectionContext<r>
```

The connection: its id, addresses, age, and whether the server is draining.

### RequestContext.param

```ceesafe
public fn param(borrow this, name: StringView) -> StringView<r>?
```

The route parameter `name` (`:name` or `*name` in the pattern), as sent (percent-encoded), or null.

### RequestContext.paramDecoded

```ceesafe
public fn paramDecoded(borrow this, name: StringView, out: borrow mut Vector<u8>) -> bool
```

The route parameter `name`, percent-decoded into `out` (cleared first); false if there is none, or `out`
could not grow.

## interface Handler

```ceesafe
public interface Handler<S> {
    async fn handle(borrow this, ctx: borrow mut RequestContext<S>, request: borrow Request, exchange: borrow mut Exchange,) -> Result<void, HandlerError>;
}
```

Handles requests for an application whose state is `S`. A handler is a struct with its own fields
(configuration, `Arc`s to services); the state is lent per request as `ctx.state()`.

## enum Flow

```ceesafe
public enum Flow {
    Continue,
    Responded,
}
```

Whether a middleware's `before` let the request through.

## interface Middleware

```ceesafe
public interface Middleware<S> {
    async fn before(borrow this, ctx: borrow mut RequestContext<S>, request: borrow Request, exchange: borrow mut Exchange,) -> Result<Flow, HandlerError>;
    async fn after(borrow this, request: borrow Request, head: borrow mut ResponseHead) -> Result<void, HandlerError>;
}
```

Hooks around every handler (Web ADR 0007 §3.2). `before` hooks run in order; `after` hooks run in reverse order
just before the response head is sent, and may add fields.

## enum RouteError

```ceesafe
public enum RouteError {
    BadPattern,
    Conflict,
    UnsupportedMethod,
    OutOfMemory,
}
```

Why a route could not be added.

## fn maxParams

```ceesafe
public fn maxParams() -> u32
```

The most parameters one route may have.

## struct Params

```ceesafe
public struct Params
```

The parameters of a match.

### Params.empty

```ceesafe
public fn empty() -> Params
```

No parameters.

### Params.count

```ceesafe
public fn count(borrow this) -> u32
```

How many.

## enum RouteMatch

```ceesafe
public enum RouteMatch {
    Found(u32),
    MethodNotAllowed(u32),
    NotFound,
}
```

The outcome of routing a path.

## struct Router

```ceesafe
public struct Router<S>
```

Routes requests to handlers of application state `S`.

### Router.create

```ceesafe
public fn create() -> Result<Router<S>, RouteError>
```

An empty router.

### Router.add

```ceesafe
public fn add(
    borrow mut this,
    method: Method,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

Routes `method` requests for `pattern` to `handler`. A pattern is `/`, or `/`-separated segments: literal
text, `:name` (one segment) or, last, `*name` (the rest of the path, possibly empty).

### Router.get

```ceesafe
public fn get(
    borrow mut this,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

GET (and so HEAD) `pattern`.

### Router.post

```ceesafe
public fn post(
    borrow mut this,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

POST `pattern`.

### Router.put

```ceesafe
public fn put(
    borrow mut this,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

PUT `pattern`.

### Router.delete

```ceesafe
public fn delete(
    borrow mut this,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

DELETE `pattern`.

### Router.patch

```ceesafe
public fn patch(
    borrow mut this,
    pattern: StringView,
    handler: Box<dyn Handler<S> + Send + Share>,
) -> Result<void, RouteError>
```

PATCH `pattern`.

### Router.find

```ceesafe
public fn find(borrow this, method: Method, path: Slice<u8>, params: borrow mut Params) -> RouteMatch
```

Routes `method` and `path` (starting with `/`), filling `params`. HEAD falls back to GET (RFC 9110 §9.3.2).

### Router.handler

```ceesafe
public fn handler(borrow this, index: u32) -> borrow Box<dyn Handler<S> + Send + Share>
```

The handler at `index` (from `RouteMatch.Found`).

### Router.paramRange

```ceesafe
public fn paramRange(borrow this, params: borrow Params, name: StringView) -> Range?
```

The value of the parameter `name` among `params`, as a range of the path, or null.

## struct Range

```ceesafe
public struct Range {
    public start: usize;
    public end: usize;
}
```

A range of a path.

## fn percentDecode

```ceesafe
public fn percentDecode(s: Slice<u8>, out: borrow mut Vector<u8>) -> bool
```

Percent-decodes `s` into `out`; `false` if `out` is full.

## struct Server

```ceesafe
public struct Server<S: Send + Share>
```

An HTTP server for an application whose state is `S`. Its state, handlers and middleware are `Send`: HTTP/2 runs
each request in its own task.

### Server.create

```ceesafe
public fn create(state: S, limits: Limits) -> Result<Server<S>, RouteError>
```

A server owning `state`, with no routes and no middleware; `limits` bound each connection's requests.

### Server.use

```ceesafe
public fn use(borrow mut this, m: Box<dyn Middleware<S> + Send + Share>) -> Result<void, RouteError>
```

Adds `m` after the middleware already added: its `before` runs after theirs, its `after` before theirs.

### Server.advertiseHttp3

```ceesafe
public fn advertiseHttp3(borrow mut this, port: u16)
```

Advertises HTTP/3 on UDP `port` of the same host (Web ADR 0014 §3.8): every HTTP/1.1 and HTTP/2 response
gains `alt-svc: h3=":<port>"; ma=86400` (RFC 7838 §3), unless the handler or a middleware set its own `Alt-Svc`.
Serve the same application there with `applicationOf` and `web_http3`'s `Http3Server.serve`.

### Server.router

```ceesafe
public fn router(borrow mut this) -> borrow mut Router<S>
```

The router, to add routes.

### Server.state

```ceesafe
public fn state(borrow this) -> borrow S
```

The application's state.

### Server.limits

```ceesafe
public fn limits(borrow this) -> borrow Limits
```

The HTTP limits each connection's requests are held to.

### Server.run

```ceesafe
public async fn run(
    this,
    incoming: borrow dyn Incoming,
    acceptor: borrow dyn Acceptor,
    limits: borrow ConnectionLimits,
    drain: borrow Drain,
) -> Result<ServerReport, NetError>
```

Serves connections from `incoming` (a `Listener`, or a `MockIncoming` in tests) through `acceptor` with M1's
connection server, until `drain` finishes or `incoming` ends (`ConnectionServer.run`). A connection is HTTP/2
when TLS negotiated `h2`, or when it starts with HTTP/2's preface (prior knowledge, RFC 9113 §3.3); HTTP/1.1
otherwise. The server moves into an `Arc` shared by every connection and HTTP/2 stream.

Implements `ConnectionHandler`.

Implements `Application`.

## struct HttpService

```ceesafe
public struct HttpService
```

An application served over HTTP/1.1 and HTTP/2: the protocol is chosen per connection (Web ADR 0011 §3.2).

Implements `ConnectionHandler`.

## fn serviceOf

```ceesafe
public fn serviceOf<S: Send + Share>(server: Server<S>) -> Result<HttpService, NetError>
```

The service of `server`, which it owns from now on.

## fn applicationOf

```ceesafe
public fn applicationOf<S: Send + Share>(
    server: Server<S>,
) -> Result<Arc<Box<dyn Application + Send + Share>>, NetError>
```

`server` as a shared application, which it owns from now on: the one `serviceOf` serves. Serve it over TCP with
`HttpService.of` and, in the same program, over HTTP/3 with `web_http3`'s `Http3Server.serve`, so both run the
same routes, middleware and state (Web ADR 0014 amendment 4 item 1). `web_server` itself never depends on
`web_http3`.
