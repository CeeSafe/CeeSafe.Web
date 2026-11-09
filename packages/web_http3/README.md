# web_http3

HTTP/3 (RFC 9114) and QPACK (RFC 9204) for a CeeSafe.Web server, over web_quic's QUIC (Web ADR 0014). The protocol
work is nghttp3's and ngtcp2's, vendored; this package drives them and runs the same `Application` as HTTP/1.1 and
HTTP/2.

```ceesafe
// The server that also answers over TCP: applicationOf shares it with HTTP/3.
server.advertiseHttp3(443); // HTTP/1.1 and HTTP/2 responses gain `alt-svc: h3=":443"; ma=86400`
let app = applicationOf(server)?;
let tls = Arc<QuicTls>.create(QuicTls.of(config)?)?; // a server's TlsConfig, with ALPN h3
let socket = AsyncUdpSocket.bind(local, UdpOptions.defaults())?;
await Http3Server.serve(socket, local, tls, app, Limits.defaults(), drain, Http3Options.defaults())?;
```

`examples/h3` is a whole program, and `tests/interop/endpoint` serves quic-interop-runner.

## What it does

- **Requests** run as over HTTP/1.1 and HTTP/2: the same handlers, middleware and `Limits`; a request's head may be at
  most `limits.maxHeadBytes`.
- **Per connection:** at most `requestsPerConnection` requests at once (100); past it, a stream is refused with
  H3_REQUEST_REJECTED. More than `resetsPerSecond` (200) requests reset by the client before their answer is done
  closes the connection with H3_EXCESSIVE_LOAD (rapid reset, CVE-2023-44487).
- **Drain:** when the shared `Drain` starts, each connection sends GOAWAY, finishes the requests it has, and closes once
  the client has acknowledged everything.
- **QUIC** (web_quic's `QuicServerOptions`): Retry past `retryAbove` live connections, stateless resets, Version
  Negotiation, the client's migration, and the closing state.

## 0-RTT and replay

0-RTT is **off** unless `QuicTls.allowEarlyData()` is called on the server's context. With it, a client resuming a
session may send requests in its first flight, before the handshake has proved who it is. Such data can be replayed:
an attacker who captured it can send it again (RFC 8446 §8, RFC 9001 §9.2). What this server does about it:

- **Only safe methods run early.** A GET, HEAD or OPTIONS that came in 0-RTT runs, and its handler sees
  `request.earlyData()`. Any other method gets 425 Too Early (RFC 8470) without reaching a handler, and the client
  sends it again after the handshake. A handler whose GET has an effect should check `earlyData()` and answer
  `Status.tooEarly()` itself.
- **A ticket's early data is taken at most once.** With early data on, OpenSSL issues single-use tickets kept in the
  server's session cache. A ticket used a second time, or offered to another server or to this one after a restart,
  is unknown there: that handshake is a full one, and its early data is ignored.

What that does **not** prevent:

- **One replay can still run.** An attacker who sends a captured first flight before the client's own copy arrives
  uses the ticket first; the client's early data is then refused and sent again after the handshake. The same safe
  request runs twice. This is why only safe methods run early.
- **Clients retry.** A client whose answer was lost may send the request again on a new connection. That is not
  0-RTT's doing, but early data does not change it.
- **Servers behind one name** each take a ticket only where it was issued. A client sent to another server does a
  full handshake there: no replay, and no 0-RTT either.

Leave 0-RTT off unless the latency matters and every GET, HEAD and OPTIONS the application serves is safe to run twice.
