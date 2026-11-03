# Web ADR 0009 — The HTTP client

**Status:** accepted 2026-11-02 (M5): §6's three questions answered by the owner, each as recommended. **Scope:** implementation plan §7 M5: `web_client`
over HTTP/1.1, with its sans-I/O half in `web_http1`.

## 1. Context

What M0–M4 already provide, and what CeeSafe allows (checked 2026-11-02 on ceesafec 0.9.2):

- **TLS without a dependency.** `web_transport`'s `Connector` interface turns a `PlainStream` into a transport;
  `web_tls.TlsConnector` implements it. Web ADR 0004 keeps `web_client` below `web_tls`, so an application passes its
  connector in, and a program without TLS never links OpenSSL.
- **DNS.** lib/std's `AsyncDns.resolve(host, port) -> Vector<SocketAddress>` (U5, CeeSafe ADR 0048).
- **Body framing.** `web_http`'s `ChunkDecoder` and `Limits` decode a response body exactly as they decode a
  request's.
- **A shared pool needs `Send` connections.** Concurrent requests share one pool, so the pool needs interior
  mutability. lib/std offers that only through `AsyncMutex<T: Send>` (and `Mutex`, `Channel`); there is no
  single-thread cell. A `Box<dyn Transport>` is not `Send`, and `TlsTransport` cannot be either: it holds a raw
  pointer (probe: `AsyncMutex<Vector<Box<dyn Conn>>>` is N0334).

  CeeSafe's answer is CeeSafe ADR 0025 §3.2: a package whose manifest says `trusted = true` may mark its own types
  `@threadSafe`, making their thread-safety the package's claim. `ceesafec build` lists trusted dependencies, so a
  reviewer sees who carries the argument. For `web_tls` the claim holds:
  - OpenSSL 3 documents an `SSL_CTX` as safe to share across threads, and the shim changes its own state under
    OpenSSL's lock (Web ADR 0002 amendment 3);
  - an `SSL` is used by one task at a time, which `borrow mut` guarantees;
  - its read-only getters are only reached through shared borrows.

## 2. Options for sharing the pool

| Option | Concurrent requests on one client | Cost |
|---|---|---|
| A. `borrow mut Client` per request | no: one at a time | none |
| B. A pool task owning the connections, reached by channels | yes | channels also need `Send` values |
| **C. `AsyncMutex<Pool>` holding `Box<dyn Transport + Send>`** | yes, and across threads later | `web_tls` declares `trusted = true` and marks `TlsConfig`, `TlsTransport` and `TlsSession` `@threadSafe`; `Connector` returns `Box<dyn Transport + Send>` |

**C** is proposed (§6.1).

## 3. Decision

### 3.1 The engine (web_http1, sans-I/O)

- `encodeRequestHead(method, target, headers, framing, out)` writes a request head: the request line, `host`
  first, then the caller's fields, then the framing (`content-length`, or `transfer-encoding: chunked`).
- `ResponseScanner` and `parseResponseHead` are the server's `HeadScanner` and `parseHead` mirrored, as strict:
  - the status line `HTTP/1.1 200 OK` (RFC 9112 §4): three digits, any reason phrase;
  - field rules as for requests (RFC 9112 §5), and the limits of `Limits`.
- `responseFraming(method, status, headers)` follows RFC 9112 §6.3:
  - no body for a HEAD request, 1xx, 204 and 304;
  - else `Transfer-Encoding` ending in chunked;
  - else `Content-Length` (conflicting values are an error);
  - else until the connection closes.
- Interim 1xx responses are skipped, except 101, which this client never asks for, so it is an error.

### 3.2 URLs (web_http)

`Url.parse(text)` takes absolute `http` and `https` URLs (RFC 3986 §3): a scheme, a host (a name, IPv4, or `[IPv6]`),
an optional port, and a path with an optional query. The fragment is dropped.

- Userinfo (`user:pass@`) is refused: RFC 9110 §4.2.4 deprecates it.
- Credentials go in an `Authorization` field.
- `Url.resolve(location)` resolves a redirect's `Location` (RFC 3986 §5.2).

### 3.3 The client API (web_client)

```ceesafe
var client = Client.create(ClientOptions.defaults(), connector)?;   // connector: a TlsConnector, or none (no https)
var request = ClientRequest.create(Method.Post, "https://api.example.com/items")?;
request.header("content-type", "application/json".bytes())?;
var response = await client.send(request, BodyOf.bytes(json))?;     // or BodyOf.none(), or a streaming source
response.status();  response.header("etag");                         // the head, owned by the response
let n = await response.read(mut buffer)?;                            // the body, streamed
await response.finish()?;                                            // gives the connection back to the pool
```

- **The response.** A `ClientResponse<r>` borrows the client (for the pool) and owns its connection and its head.
  - `finish()` reads what is left of the body, up to a drain limit, and returns a reusable connection to the pool.
  - A response dropped without `finish()` closes its connection: a drop cannot wait for I/O.
- **Request bodies.**
  - `BodyOf.bytes(slice)` is replayable, so it can follow 307/308 redirects and be retried.
  - A `BodySource` interface streams; it is replayable only if it says so.
- **Origins.** A client talks to many origins. Plain `http` needs no connector; `https` without one is
  `Err(NoTls)`.

### 3.4 The pool

- **Keys.** Connections are pooled per origin: scheme, host and port. One client has one connector, so the TLS
  configuration is part of the client, not of the key.
- **Bounds.** `maxPerOrigin` connections in use or idle (others wait, by a per-origin token channel), `maxIdlePerOrigin`
  idle ones, and `idleTimeoutMs`. A connection idle longer is dropped when it is next taken (no reaper task).
- **Reuse.** A connection goes back only after a response that was read to its end and allows keep-alive (HTTP/1.1
  without `Connection: close`).
- **Shutdown.** `client.shutdown()` closes the idle connections at once. Requests in flight end with `Err(Closed)` at
  their next pool operation; their own connections are closed rather than returned.

### 3.5 Connecting: DNS and Happy Eyeballs v2 (RFC 8305)

- **Steps.** Resolve (`Resolver`, default `AsyncDns`), then order the addresses (RFC 8305 §4: alternate families,
  starting with the first address's), then dial (`Dialer`, default TCP).
- **Racing.** An attempt starts every `connectionAttemptDelayMs` (250 ms, §5), or as soon as the previous one fails.
  The first to connect wins, and the rest are cancelled. Each attempt runs in a CeeSafe task set (ADR 0053), so a
  list of any length races without a recursive `await`.
- **Test seams.** `Resolver` and `Dialer` are interfaces, so tests are deterministic: a fixed resolver, and a dialer of
  `MockTransport`s.

### 3.6 Timeouts

| Timeout | Default | What it bounds |
|---|---|---|
| `connectMs` | 10 s | DNS and every attempt of §3.5 |
| `tlsMs` | 10 s | the connector's handshake (its `deadline`) |
| `firstByteMs` | 30 s | from the request's last byte to the response head |
| `totalMs` | none | the whole `send`, redirects and retries included; the body read afterwards has its own per-read deadline from what is left |

Each is a `Deadline` (Web ADR 0001), so its expiry is `Err(TimedOut)` in every case.

### 3.7 Redirects

- **Followed:** 301, 302, 303, 307 and 308, at most `maxRedirects` (10) times. Further ones return the 3xx response.
- **Method.**
  - 303 becomes GET without a body, except after HEAD.
  - 301 and 302 after a POST also become GET, as browsers and curl do (RFC 9110 §15.4.2 allows it; §6.2).
  - 307 and 308 keep the method and the body. With a body that cannot replay, the 3xx response is returned instead.
- **Fields across origins.** When the origin changes (scheme, host or port), `Authorization`, `Cookie` and
  `Proxy-Authorization` are removed.

### 3.8 Retries: `RetryPolicy`

A failed attempt is retried only if all of these hold:

1. **The request is idempotent:** GET, HEAD, OPTIONS, TRACE, PUT or DELETE (RFC 9110 §9.2.2), or it carries
   `Idempotency-Key`.
2. **It is safe to send again:** no byte of the request was written, or the failure is a reuse race. A reuse race is a
   pooled connection that ended (EOF or reset) before any response byte, on the first request sent on it since it was
   idle.
3. **The body can replay**, if there is one.
4. **Within the bounds:** `maxAttempts` (default 2), and the origin's retry budget, a token bucket allowing retries of
   at most 10 % of its requests (plus 3 to start).

A POST without `Idempotency-Key` is never retried, whatever failed. The transport counts bytes written per attempt, so
"no byte written" is a fact, not a guess.

## 4. Consequences

- `web_tls` becomes a trusted package; its `@threadSafe` types carry the argument of §1.
- `Connector.connect` returns `Box<dyn Transport + Send>`. `PlainStream` is `Send`, and `TlsTransport` becomes so by
  `@threadSafe`. M1's `Acceptor` is unchanged.
- One more `Async.race` per request (the first-byte deadline), and a mutex lock per pool operation.

## 5. Tests (the M5 exit gate, plan §7)

- **Engine (web_http1):**
  - response heads and framing against vectors derived from h11's client side;
  - a property test: encoding then parsing a request gives the same request.
- **Client (web_client), deterministic, over a mock dialer:**
  - **No replay after a partial write:** a POST at every write split. For each byte offset where the connection
    fails, the server side sees at most one request, and the client returns the error.
  - **DNS failure:** `Err(HostNotFound)` (`Err(NotFound)` before ceesafec 0.10.0, upstream U15), with no dial.
  - **Timeouts:** each one, with the interpreter's virtual clock.
  - **Shutdown:** a pool shut down with requests in flight.
  - **Reuse races:** a server that closes an idle connection makes a GET retry, and a POST fail.
  - **Happy Eyeballs:** a slow first address; a failing first address; the alternation.
  - **Redirects:** each status's method rule, the limit, and the fields stripped across origins.
  - **The pool:** reuse, `maxPerOrigin` waiting, idle expiry.
- **Interop** (`tests/interop/client.py`): a client example against Python's `http.server`, nginx if present, and
  `examples/https` over TLS, with redirects and chunked bodies.
- **Sweeps:** allocation failure and cancellation over `web_client`'s tests.

## 6. Questions for the owner (answered 2026-11-02: each as recommended)

1. **Sharing the pool (§2).** Make `web_tls` a trusted package (`trusted = true`) so its types are `Send`, and pool
   `Box<dyn Transport + Send>` behind an `AsyncMutex`? Recommended: yes. Otherwise a `Client` serves one request at a
   time (option A).
2. **POST after 301 and 302 (§3.7).** Become GET, as browsers and curl do, or keep POST, as RFC 9110 prefers?
   Recommended: become GET, since that is what servers expect.
3. **Scope of v0.2.** HTTP/1.1 only; HTTP/2 joins the client with M7 (Web ADR 0004: chosen by ALPN). Proxies, cookies
   and decompression are later. Recommended: yes.

## 7. Amendment 1 (2026-11-03, M5 built and merged on ceesafec 0.9.4)

1. **Shutdown's error.** §3.4 says requests in flight end with `Err(Closed)`. As built, they end with
   `Err(ClientError.Shutdown)`, the error a request started after the shutdown also gets. That way a caller can tell
   a shutdown from a peer's close (`Net(Closed)`). A response already being read keeps its connection to the end,
   and `finish` then closes the connection instead of pooling it. Tested by `shutdown_with_requests_in_flight`.
2. **CeeSafe B15 and B14** (`docs/ceesafe-upstream-work.md`) held the client on a branch until 0.9.4 fixed them.
   `Connector` returns `Box<dyn Transport + Send>` (§2). A response holds its connection as a
   `Vector<Box<dyn Transport + Send>>` of at most one, with no wrapper struct.
3. **Interop (§5)** ran against:
   - Python's `http.server`, plain and over Python's `ssl`;
   - `examples/https` (web_server over web_tls);
   - public hosts with the system's roots, on macOS, Debian 12 and Windows 11.

   nginx is not installed on the test machines. `examples/fetch` is the client example.
4. **The system's roots on Windows** needed a change in web_tls (Web ADR 0002 amendment 4).
