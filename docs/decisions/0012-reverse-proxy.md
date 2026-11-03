# Web ADR 0012 — The reverse proxy, and what M7 left open

**Status:** accepted 2026-11-03 (M8). The owner accepted every recommendation in §6:
- requests relay the body, then the response, and only tunnels are full duplex;
- HTTP/2 to upstreams;
- rendezvous hashing;
- an untrusted source on a PROXY-protocol listener is closed;
- both families of forwarding fields;
- 504 for a connect timeout.
**Scope:**
- Implementation plan §7 M8: `web_proxy`, which covers upstream pools, health, balancing, forwarding fields, the PROXY
  protocol, the relay, retries, 502 and 504, and tunnels.
- M7's open items, which the owner asked to fold into M8 (2026-11-03). They are listed in §3.10.

## 1. Context

What M0–M7 provide (checked 2026-11-03 on `main` at `629ea9b`, ceesafec 0.10.3):

- **The client is most of an upstream pool.**
  - `Client.send(request, body: borrow mut dyn BodySource)` (`web_client/src/client.cee:191`) pools connections per
    origin (Web ADR 0009 §3.4).
  - Redirects (`maxRedirects`) and retries (`RetryPolicy`, §3.8) are options, and both can be turned off.
  - `ClientResponse.read` streams the body.

  A proxy still lacks four things:
  - it can read a response field only by name (`header`, `client.cee:650`); it cannot list them all;
  - `host` is refused as a request field (`client.cee:40`), so the client's `Host` cannot be kept;
  - there is no Upgrade other than WebSocket's (`connectWebSocket`), and no way to take the connection's raw bytes after
    a 101;
  - the client speaks HTTP/1.1 only (Web ADR 0011 §6.2).

  All four are code in this repo.
- **The peer's address is known.**
  - `ConnectionContext.peerAddress()` and `localAddress()` (`web_core/src/context.cee:52`) reach every handler, over
    HTTP/1.1 and HTTP/2 (`web_http2/src/serve.cee:250`).
  - lib/std's `SocketAddress` holds the IP's bytes (`V4([u8; 4], u16)`, `V6([u8; 16], u16, u32)`), and
    `SocketAddresses.parseIp` reads one (CeeSafe `lib/std/net.cee:22`, `:128`).

  So a CIDR list needs no upstream work.
- **Each accepted connection passes through an `Acceptor`** (`web_transport/src/plain.cee:82`) before its context is
  built from the transport's addresses (`web_transport/src/server.cee`, `worker`). A PROXY protocol header comes before
  TLS's ClientHello, so it is read there.
- **Framing is already strict on both sides.**
  - `web_http1` rejects both Content-Length and Transfer-Encoding, differing Content-Lengths, and any coding but
    `chunked`: in requests (`head.cee:4`) and in responses (`client.cee:34`).
  - HTTP/2 checks RFC 9113 §8.1.1 (content-length against DATA) in `validate.cee`.
- **Reads may be cancelled; writes may not** (Web ADR 0001 §3.4). M6's session and M7's connection task each relay in
  both directions with one task: they race reads, then write (Web ADR 0010 §1). A tunnel between two transports can do
  the same: race a read on each side, then write what arrived to the other side.
- **An `Exchange` can hand over its raw bytes** after `switchProtocols` (`readRaw`, `writeRaw`, `exchange.cee:688`).

## 2. Options

**Where the proxy sits.**

| | A. A handler (proposed) | B. Its own `ConnectionHandler` |
|---|---|---|
| Shape | `ReverseProxy` implements `Application` (and so `Handler<S>`); a route or the whole server can be a proxy | it takes connections from `ConnectionServer` and speaks HTTP itself |
| HTTP/1.1 and HTTP/2 in front | both, through the drivers M2 and M7 built | it would need both drivers again |
| Routing, middleware, drain, limits | reused | duplicated |
| Cost | a request is parsed once into a `Request` and copied into a `ClientRequest` | the head could be forwarded nearly as bytes |

A is the only option that keeps one HTTP implementation per protocol (Web ADR 0004 §3.2). The copy of the head costs
less than an upstream round trip.

**How a request's body and its response move.**

| | A. Request body first, then the response (proposed for requests) | B. Both at once (full duplex) |
|---|---|---|
| What it allows | every HTTP/1.1 exchange; an upstream that answers early, such as 413, ends the upload | also bidirectional streaming, such as gRPC's bidi calls over HTTP/2 |
| How | `Client.send` reads the body through a `BodySource` that borrows the `Exchange`, then the response is copied to the `Exchange` | one task races both reads and writes both ways, as the tunnel does; `Client.send` would have to return before the body is sent |
| Cost | none new | a new client API (a request body you write to while you read the response) |

Tunnels (§3.8) are full duplex in any case: a WebSocket needs it. Question 1 asks whether requests need it in M8 too.

## 3. Decision

### 3.1 The shape (`web_proxy`)

- **`ReverseProxy`** is an `Application`. It is built from:
  - an `Upstream` (backends, a balancing policy, health checks);
  - `ProxyOptions` (forwarding fields, trusted proxies, retries, timeouts, Host).
- **Several upstreams** are several `ReverseProxy` values, each on its own route.
- **One `Client` per upstream**, with redirects off, its own retry policy off (the proxy retries, §3.7), and limits
  taken from the proxy's options. Its pool is the upstream's pool: each backend is one origin.
- **Health checking runs as a task** that the application runs beside `server.run`:
  `proxy.monitor(drain)` in the same scope. It ends with the drain.

### 3.2 Backends and balancing

- **A backend** is a URL (`http` or `https`) and a weight, and has state:
  - requests in flight (an atomic counter);
  - passive failures;
  - whether it is ejected, and until when.
- **The policies:**
  - **round robin:** weighted smooth round robin, as nginx does it;
  - **least requests:** the power of two choices. Pick two healthy backends at random, weighted, and take the one with
    fewer requests in flight;
  - **consistent hash:** rendezvous (highest random weight) hashing over a key, either the client's address (§3.4) or
    a named field. For each request the backend with the highest `hash(key, backend)` wins. When a backend is removed,
    only its own keys move.
- **The hash** is SipHash-1-3 keyed from `Random` (U16), so clients cannot choose keys that collide. Its expected
  values come from the reference implementation's vectors.
- **No healthy backend:** 503. If every backend is ejected, the policy ignores ejection ("panic mode", as Envoy does),
  because sending somewhere beats failing everything. This is an option, on by default.

### 3.3 Health

- **Passive.** Each failure counts against its backend, and an answered request resets the count. The failures are:
  - a connection refused or reset;
  - a TLS failure;
  - a malformed response;
  - a timeout;
  - a 5xx, only if the options say so.

  After `consecutiveFailures` (default 5) the backend is ejected for `baseEjectionMs` × 2^n, where n counts its
  ejections in a row, capped at `maxEjectionMs`. At most half the backends are ejected at once.
- **Active.** Every `intervalMs` the monitor sends `GET path` to each backend, with `timeoutMs`. Responses count
  toward a threshold:
  - `healthyAfter` 2xx or 3xx responses in a row mark the backend healthy;
  - `unhealthyAfter` other results in a row mark it unhealthy.

  The checks run in a task set (CeeSafe ADR 0053), one child per backend.

### 3.4 Who the client is: trusted proxies and forwarding fields

- **`trustedProxies`** is a list of CIDR blocks, IPv4 and IPv6. The default list is empty.
- **The client's address** is found as follows:
  - If the peer is not trusted, the client is the peer, and incoming `Forwarded` and `X-Forwarded-*` fields are
    ignored.
  - If the peer is trusted, walk the forwarded addresses from the right. Each trusted address is skipped. The first
    untrusted address is the client.
  - If every address is trusted, the left-most is the client.
  - An unparseable element ends the walk: the last trusted hop is the client.
- **Which field is read:** `Forwarded` (RFC 7239) is read if it is present. Otherwise `X-Forwarded-For` is read. If a
  request carries both and they disagree, `Forwarded` wins. The choice is an option.
- **Parsing.** `Forwarded` follows RFC 7239 §4:
  - quoted strings;
  - IPv6 in `"[...]"`, with a port;
  - `unknown` and obfuscated `_x` identifiers, which end the walk as untrusted.

  Its expected values come from RFC 7239's own examples.
- **What is sent upstream:**
  - `Forwarded: for=…;proto=…;host=…`, appended to the trusted chain or started fresh. A field from an untrusted peer
    is removed, never extended.
  - `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host`, by the same rule.

  Each of the two families can be turned on or off. Both are on by default.
- **`Via`** (RFC 9110 §7.6.3) is optional and off by default.
- **The `Exchange`** gains `clientAddress()`, which gives what this section decides, so a handler behind the proxy's
  own listener sees the real client too.

### 3.5 What is forwarded

- **Hop-by-hop fields are removed in both directions** (RFC 9110 §7.6.1):
  - `Connection` and every field it names;
  - `Proxy-Connection`, `Keep-Alive`, `TE`, `Transfer-Encoding` and `Upgrade`.

  `TE: trailers` is sent upstream only when the proxy can relay trailers (§3.10), which it can over both protocols.
- **`Host`:**
  - Kept by default. The client gains a request authority that is sent as `Host` (or `:authority`) instead of the
    URL's.
  - `ProxyOptions.host` can rewrite it to the backend's.
- **Framing is never copied.** Content-Length is sent only when the body's length is known. Otherwise the body goes
  chunked over HTTP/1.1, or as DATA over HTTP/2.
- **`Max-Forwards`** is decremented on TRACE and OPTIONS (RFC 9110 §7.6.2). Neither is answered locally.
- **The response's fields** are copied, minus hop-by-hop fields. The client gains `ClientResponse.fields()` to list
  them. `Date` and `Server` are passed through; the server does not add its own.

### 3.6 The PROXY protocol (`ProxyProtocolAcceptor`)

- **A wrapper over another `Acceptor`.**
  - From a source in its `trusted` list, it reads the PROXY header (v1 text, or v2 binary) within `deadline`. Then it
    calls the inner acceptor (the TLS handshake, or none).
  - It wraps the resulting transport so that `peerAddress()` and `localAddress()` report the header's addresses.
  - The context, logs and §3.4 all see the real client.
- **Exactly the header is read**, so no byte of the ClientHello or the request is taken:
  - v2: the 16-byte prefix, then the length it gives;
  - v1: byte by byte up to CRLF, at most 107 bytes.

  The extra reads touch at most 107 bytes per connection, once.
- **v2's `LOCAL` command and `UNSPEC` family** keep the socket's addresses. TLVs are skipped and bounded by the length.
- **A source that is not trusted is closed** before anything is read (question 4). A trusted source that sends no
  valid header within the deadline is closed too.
- **Expected values** come from the HAProxy specification (`proxy-protocol.txt`): its v1 examples and v2 layout, and
  headers that HAProxy itself sends.

### 3.7 The relay, retries and errors

- **The request body** is a `BodySource` that reads from the `Exchange`:
  - Its length is the request's length if it is known.
  - It can rewind only while the bytes read so far fit in `retryBufferBytes` (default 64 KiB): those bytes are kept so
    that a retry can resend them.
  - Backpressure comes from the awaits: the proxy reads from the client only as fast as the upstream takes.
- **The response** is read from the `ClientResponse` and written to the `Exchange`, chunk by chunk. Each write waits:
  - for the client's socket over HTTP/1.1;
  - for the flow-control window over HTTP/2.
- **Retry only before the first byte of the upstream's response head**, and only when all of these hold:
  - the method is idempotent, or the request carries `Idempotency-Key`;
  - the body can rewind;
  - the retry budget allows it.

  The budget is a fraction of requests (`budgetPercent`, default 20) plus a small floor, as `RetryPolicy` already
  counts it (Web ADR 0009 §3.8). A retry goes to a different backend if there is one.
- **502 or 504, decided before the head is sent:**

| Upstream outcome | Status |
|---|---|
| no healthy backend | 503 |
| connection refused or reset, DNS failure, TLS failure | 502 |
| a malformed or ambiguous response head (both Content-Length and Transfer-Encoding, differing lengths, …) | 502; the upstream connection is discarded |
| the upstream closes before a whole head | 502 |
| connecting takes longer than `connectMs`, or the head takes longer than `firstByteMs` | 504 |

- **After the head is sent**, a failure aborts the response:
  - HTTP/1.1: the connection is closed with the body short of its Content-Length, or without the last chunk;
  - HTTP/2: RST_STREAM with INTERNAL_ERROR.

  A truncated message is never presented as complete. `Exchange` gains `abort()`, which guarantees this whatever the
  framing. These cases include an upstream that ends early, an idle timeout, and a malformed chunk.
- **The upstream's ambiguous framing never reaches the client.** It is refused as a 502 before any byte is sent. The
  client's ambiguous framing never reaches the upstream: `web_http1` refuses it as a 400 first.

### 3.8 Tunnels: Upgrade (WebSocket and others)

- A request with `Upgrade` is forwarded with its `Connection: upgrade` and `Upgrade` kept. This is the one exception
  to §3.5.
- If the upstream answers 101, the proxy calls `exchange.switchProtocols(protocol)` and takes the upstream connection
  out of the pool. The client gains an Upgrade that returns its raw transport.
- The proxy then pumps bytes both ways in one task, as the WebSocket session and the HTTP/2 connection task do (§1):
  - race a read on each side, then write what came to the other side;
  - when one side reads 0, shut down writing on the other;
  - the tunnel ends when both sides have closed, after `tunnelIdleMs` with no bytes, or at the drain's grace period.
- Any other answer is relayed as an ordinary response, and the connection stays HTTP.
- Over an HTTP/2 front end, Upgrade does not exist. Extended CONNECT (RFC 8441) is not in M8.

### 3.9 HTTP/2 to upstreams

This builds on §3.10's client. A backend URL may ask for HTTP/2:
- `https` with ALPN `h2`;
- `http` with prior knowledge, which must be set explicitly.

One pooled connection then carries many streams, up to the peer's SETTINGS_MAX_CONCURRENT_STREAMS. The client opens
another connection past that.

### 3.10 What M7 left open, done in M8

| Item | Where M7 left it | What M8 does |
|---|---|---|
| Request trailers | Web ADR 0011 §7, "Known gaps": not passed to handlers | `Exchange.trailers()` after the body's end, over HTTP/1.1 (chunked) and HTTP/2 (the engine's `Trailers` event, `connection.cee:528`). The proxy relays trailers both ways. |
| A client that half-closes | §7: a read of 0 ends the connection at once, cancelling streams in flight (`serve.cee:209`) | After a read of 0, the connection stops reading but finishes the streams it has (bounded by the drain's grace period), then closes |
| The HPACK encoder's dynamic table | §4: "until M9 decides on dynamic table use" | Moved into M8: the encoder indexes fields within the peer's SETTINGS_HEADER_TABLE_SIZE. It never indexes `authorization`, `cookie` or `set-cookie` values, which it marks never-indexed (RFC 7541 §7.1.3). Checked against RFC 7541 Appendix C.3, C.5 and C.6 as an encoder, not only as a decoder. |
| HTTP/2 in the client | §6.2: "not in M7" | `web_client` speaks HTTP/2 (§3.9), with the M7 engine in client mode: its own preface, even stream IDs refused, push disabled (SETTINGS_ENABLE_PUSH = 0). Interop: our server, nghttpd, and a Go `net/http` server. |
| 24 CPU-hours of fuzzing for `web_http2` | Web ADR 0005 §2.2 requires it; M7 ran 30 minutes | Running since 2026-11-03 on Debian (four seeds × 6 h). The result goes into `docs/milestones/M8.md`. The client mode joins the fuzzer. |
| A Debian run of the sweeps | the run on 0.10.3 lost its SSH connection after one package | Run again on Debian with M8's packages |

Not reopened, because these were the owner's decisions:
- priority, which is ignored (Web ADR 0011 §6.3);
- `Upgrade: h2c`, which is not supported (§6.1).

CeeSafe's U18 and U19 stay with CeeSafe.

## 4. Consequences

- **`web_client` grows**:
  - HTTP/2;
  - a request authority;
  - the response's fields;
  - a raw Upgrade.

  `web_client` may already depend on `web_http2` (Web ADR 0004 §3.5). `web_proxy` stays within its allowed list.
- **`web_http` and `web_server` grow:**
  - `Exchange.trailers()`, `abort()` and `clientAddress()`;
  - the trusted-proxy list is in `web_server`'s options, so `clientAddress()` means the same with or without the proxy.
- **A tunnel is one task** (§3.8). While a write waits for one side, that task does not read the other. Kernel buffers
  absorb this, as they do for the WebSocket session. A tunnel whose two peers each write without reading can stall.
  That stall is a property of those peers, but it is tested (§5).
- **Without question 1's full duplex, gRPC's bidi streaming does not pass through the proxy.** Unary and
  server-streaming calls do.

## 5. Plan and tests (the M8 exit gate, plan §7)

Stages, each committed and checked:
1. **The M7 items in the engine and driver:** trailers, half-close, and the encoder's dynamic table.
2. **The client:** request authority, `fields()`, raw Upgrade, then HTTP/2.
3. **`web_proxy`:** forwarding fields and trusted proxies, the PROXY protocol, balancing and health, the relay, retries
   and errors, tunnels.
4. **The gate.**

Tests, with oracles outside the code:
- **Plan §7's exit criteria:**
  - A spoofed `X-Forwarded-For` or `Forwarded` from an untrusted peer is ignored: the upstream sees the peer.
  - With a trusted chain, the right-most untrusted address is the client.
  - Ambiguous framing is refused on both sides: a request with CL+TE gets 400, and an upstream response with CL+TE or
    differing lengths gets 502. Neither reaches the other side.
  - A partial upstream response:
    - an upstream that closes mid-body after Content-Length: 1000;
    - one that ends mid-chunk.

    Over HTTP/1.1 the client's connection closes short of the body's end. Over HTTP/2 it gets RST_STREAM. curl reports
    the transfer as incomplete (`curl: (18)`), never as a success.
- **Forwarding:** RFC 7239's examples, parsed and produced. The hop-by-hop removal covers the fields `Connection`
  names (RFC 9110 §7.6.1).
- **PROXY protocol:** HAProxy's v1 examples and v2 layout. HAProxy, in Docker on this Mac, sends `send-proxy` and
  `send-proxy-v2` headers to the listener. Expected results:
  - a header split over many reads is read correctly;
  - an untrusted source is closed;
  - a trusted source with a short or late header is closed at the deadline.
- **Balancing:**
  - round robin, which is deterministic: its order is checked against nginx's published smooth weighted sequence for
    weights 5, 1, 1;
  - least requests, against counters;
  - consistent hash: removing one of n backends moves only that backend's keys.
- **Health:**
  - ejection and its backoff;
  - panic mode;
  - the active check's thresholds, with mock backends that fail on schedule.
- **Retries:**
  - none after the head;
  - none for a POST without `Idempotency-Key`;
  - the budget caps them;
  - a retry goes to the other backend.
- **502 and 504:** each row of §3.7's table, with a mock upstream for each outcome.
- **Tunnels:**
  - Autobahn's fuzzingclient through the proxy to our `ws_echo`, with the same results as without the proxy;
  - a tunnel whose two peers write at once;
  - a drain during a tunnel.
- **Streaming and backpressure:**
  - a slow client against a fast upstream: the proxy's memory stays bounded and the upstream waits;
  - the reverse: a fast client against a slow upstream;
  - a 1 GB response through the proxy, checked by SHA-256 at both ends.
- **HTTP/2 to upstreams:**
  - our client against our server, nghttpd and Go;
  - h2load through the proxy.
- **The M7 items:**
  - trailers relayed, checked with curl and nghttp;
  - half-close, checked with a client that sends `shutdown(SHUT_WR)` after its requests and still gets every response;
  - the encoder against RFC 7541 Appendix C.3, C.5 and C.6, and h2spec still at 145/145.
- **Sweeps** over `web_proxy`'s and the changed packages' tests, and the Debian run of every package.
- **Fuzzing:**
  - a PROXY-protocol and `Forwarded` parser fuzzer;
  - the HTTP/2 fuzzer with the client mode;
  - 24 CPU-hours each (Web ADR 0005 §2.2).

## 6. Questions for the owner (answered 2026-11-03: each as recommended)

1. **Full duplex for requests in M8?** Recommended: **no, tunnels only.**
   - Requests relay the body first, then the response (§2).
   - Bidirectional streaming, as gRPC's bidi calls use, needs a client API in which the request body is written while
     the response is read. It is better done when there is a user for it.
   - Tunnels are full duplex either way.
2. **HTTP/2 to upstreams in M8 (§3.9), using the client's HTTP/2?** Recommended: **yes.** The client's HTTP/2 was left
   for later in M7, and you asked for M7's open items in M8. A proxy is where it pays: one connection per backend
   instead of eight.
3. **Consistent hashing: rendezvous (proposed) or a ring?**
   - Rendezvous costs O(n) hashes per request, needs no table, and moves only the removed backend's keys.
   - A ring (Ketama) or Maglev is O(1) or O(log n), at the cost of a table.

   Recommended: **rendezvous.** Upstreams of tens of backends make the O(n) cost negligible.
4. **A PROXY-protocol listener and a source that is not trusted:**
   - close it (proposed: nothing on that listener is served without a verified header);
   - or serve it plainly with the socket's addresses.

   Recommended: **close.**
5. **Forwarding fields by default:** both `Forwarded` and `X-Forwarded-*` (proposed), or only one? Recommended:
   **both.** Backends read one or the other, and either can be turned off.
6. **A connect timeout: 504 (proposed) or 502?** Recommended: **504.**
   - The gateway did not get a response in time, which is RFC 9110 §15.6.5's case.
   - nginx does the same.
   - A refused connection stays 502.

## 7. Amendment 1 (2026-11-04, stage 2; the owner chose A): who reads a shared HTTP/2 connection

**What §3.9 left out.** An HTTP/2 connection carries many requests at once. Something has to read it all the time:
- to hand each response's frames to its request;
- to answer PING and SETTINGS;
- to see GOAWAY.

On the server that is the connection task, in the connection's task set (Web ADR 0011 §3.2). A `Client` is a plain
value whose methods are async. It has no task of its own, and CeeSafe has none to give it: "There are no detached
tasks" (CeeSafe ADR 0053 §1; spec §40.5).

| | A. `client.run(drain)`, run by the application (proposed) | B. The waiting request reads for everyone | C. A session per origin, held by its caller |
|---|---|---|---|
| Shape | `Client.run` is an async fn that the application runs beside its work, in the same scope, as `proxy.monitor` (§3.1). It owns a task set with one connection task per HTTP/2 connection. Requests reach those tasks through bounded channels, as the server's streams do. | No new task. Whichever request is waiting holds the connection, reads it, and passes other streams' frames on through channels. | `client.http2(origin, async (session) => …)`: the connection lives as long as the closure. |
| HTTP/2 when not run | the client stays HTTP/1.1 (HTTP/2 is opt-in: `ClientOptions.http2`, with `run`) | always | only inside the closure |
| PING, SETTINGS, GOAWAY between requests | answered | not read until the next request comes | answered while the session lives |
| Cancellation | a request's cancellation resets its stream; the connection task carries on | a cancelled reader must hand its role to another request mid-read: new and delicate | as A |
| The proxy | `proxy.run(drain)` runs the health monitor and `client.run` together | nothing to run | the proxy holds one session per backend for its lifetime: A in all but name |
| Code | the server driver's pattern, reused (channels, a task set, `StreamPort`'s client twin) | a new pattern with no precedent here | as A, less general |

**Decision: A** (the owner, 2026-11-04).
- **The engine** (`web_http2`, sans-I/O) gains a client role, `H2Connection.client(…)`:
  - it sends the client preface, with SETTINGS_ENABLE_PUSH = 0;
  - it opens odd streams;
  - it checks responses (`:status`, no request pseudo-fields, RFC 9113 §8.3.2);
  - it refuses PUSH_PROMISE (§8.4).

  The stream states, flow control and guards stay shared with the server role.
- **The driver** (`web_client`) works like this:
  - `Client.run` spawns a connection task for each HTTP/2 connection the pool opens.
  - `send` asks the pool for an origin's HTTP/2 connection (ALPN `h2` on `https`, prior knowledge on an `http` backend
    that asks for it).
  - Each stream task talks to its connection task through the channels.
  - A connection at the peer's SETTINGS_MAX_CONCURRENT_STREAMS makes the pool open another.
  - After GOAWAY, a request on a stream above the last stream ID is sent again on a new connection, since GOAWAY
    promises it was not processed (RFC 9113 §6.8).
- **The `ClientResponse`** gets a stream variant, as `Exchange` did (Web ADR 0011 §3.2), so a caller reads both
  protocols the same way.

## 8. Amendment 2 (2026-11-04, stage 3; the owner's choice): the connection worker reads the PROXY header

§3.6 made the PROXY protocol `ProxyProtocolAcceptor`, an `Acceptor` that wrapped another and called it through
`dyn Acceptor`. That is a cycle through `dyn`, so the call was a stacked await (CeeSafe ADR 0056). A frame-stack chunk
that cannot be allocated is ADR 0056 §3.3's panic. The allocation sweep failed on it, and so would a server that ran
out of memory in that path: it would abort rather than drop the connection.

- **The connection worker** (`web_transport`, `ConnectionServer`) reads the header before it calls the acceptor,
  when `ConnectionLimits.proxyProtocolFrom` lists any source.
  - A source not in the list is closed unread.
  - From a listed source the header is read exactly (`readProxyHeader`, as `ProxyProtocol.more` asks), within
    `handshakeMs`.
  - Its addresses go straight into the connection's context. No transport wraps another, and nothing more is boxed.
  - A missing, malformed or late header counts as a failed handshake.
- **The parser** (`ProxyProtocol`, `ProxyHeader`), and the address blocks it uses (`Cidr`, `TrustedProxies`), move to
  `web_transport`. `web_proxy`'s forwarding fields use the same `TrustedProxies`.
- **Tests:**
  - `web_transport/tests/proxyheader.cee`: the parser against HAProxy's specification;
  - the worker at every read size from 1 to 19, with an untrusted source and a source with no header;
  - each with no allocation-sweep failure.
