# Web ADR 0019 — A drain lets an idle connection's next request in

**Status:** accepted 2026-11-21 (the owner: "Tackle still open items", of which this was the first: httpserver's
healthy requests retried when a worker is replaced). Implemented (§4).
**Scope:** `web_http1`'s `nextHead`, and one field of `web_http`'s `Limits`, `drainLingerMs`. HTTP/2 and HTTP/3,
which say GOAWAY, are unchanged.

## 1. Context

When the server drains, a keep-alive connection waiting for its next request was closed at once. A client that had
just sent a request on it, or sent one before it saw the close, found the connection closed with no answer, and had to
send the request again on a new connection (which a client may do for an idempotent request, RFC 9112 §9.3.1, and
not for another). httpserver drains a worker every time it replaces one, after 100 crashes of its isolated tasks.
Its benchmark against Bandit (httpserver's `docs/elixir-benchmark.md`), at 10 000 healthy requests a second over 64
keep-alive connections and 1000 crashes a second, measured 1.2 to 1.5 % of the healthy requests retried this way;
Bandit, which never replaces anything, none.

## 2. Decision

Once the server drains, an idle HTTP/1.1 connection still waits for its next request for `Limits.drainLingerMs`
(1000 by default, and at most `idleTimeoutMs`). A request that comes is served as any other, and since the server
drains its response says `connection: close` and the connection then closes: the client is answered, and sends its
next request on a new connection, to a worker that is not draining. A connection that sends nothing within the
linger is closed as before. A drain that starts while a connection waits ends that wait and starts the linger. A
connection busy with a request when the drain starts is unchanged: its response says `connection: close`.

`drainLingerMs = 0` closes an idle connection at once, as before this ADR. A server's own grace (`ConnectionLimits.
graceMs`) still bounds the drain: what is left when it ends is closed.

## 3. Consequences

- A drain takes up to `drainLingerMs` longer when idle connections send nothing: one second by default, within the
  grace a server sets for its drain anyway.
- The close is still possible at the linger's end, for a request sent just then; it is the rare case, not every
  request in flight on a connection that happened to be idle.
- The public API gains a field (`docs/api/web_http.md`); a `Limits` made with `defaults()` gets the linger.

## 4. Implementation

`packages/web_http1/src/serve.cee`, `nextHead`: the idle branch reads within the linger while the connection's
drain has started, and a drain that cancels the idle wait goes round to it. `web_http1`'s test
`a_drain_lets_the_next_request_in_and_closes`: on a connection whose server drains, the first of two pipelined
requests is answered with `connection: close` and the second is not read; with `drainLingerMs` 0, nothing is
written. Measured with httpserver in §5.

## 5. Measured

See httpserver's `docs/elixir-benchmark.md`, "After Web ADR 0019". On one Linux core the linger then cost the one
request read in the drain's own turn a second: Web ADR 0021.
