# web_client

- [struct ClientRequest](#struct-clientrequest)
- [interface BodySource](#interface-bodysource)
- [struct NoBody](#struct-nobody)
- [struct BytesBody](#struct-bytesbody)
- [struct Client](#struct-client)
- [enum Upgraded](#enum-upgraded)
- [struct Switched](#struct-switched)
- [struct ClientResponse](#struct-clientresponse)
- [interface Resolver](#interface-resolver)
- [struct SystemResolver](#struct-systemresolver)
- [interface DialJob](#interface-dialjob)
- [interface Dialer](#interface-dialer)
- [struct TcpDialer](#struct-tcpdialer)
- [fn interleave](#fn-interleave)
- [fn happyEyeballs](#fn-happyeyeballs)
- [struct RetryPolicy](#struct-retrypolicy)
- [struct ClientOptions](#struct-clientoptions)
- [enum ClientError](#enum-clienterror)
- [struct ClientErrors](#struct-clienterrors)
- [struct WebSocketClientOptions](#struct-websocketclientoptions)
- [struct WebSocketConnection](#struct-websocketconnection)
- [fn clientSession](#fn-clientsession)
- [fn webSocketUrl](#fn-websocketurl)

## struct ClientRequest

```ceesafe
@noncopy
public struct ClientRequest
```

A request: a method, a URL and fields. Framing fields and `host` are the client's to write; `setAuthority` changes
the `host` it writes.

### ClientRequest.create

```ceesafe
public fn create(method: Method, url: StringView) -> Result<ClientRequest, ClientError>
```

A request for `url` (absolute http or https). `Method.Extension` is refused: it has no token to send.

### ClientRequest.header

```ceesafe
public fn header(borrow mut this, name: StringView, value: Slice<u8>) -> Result<void, ClientError>
```

Adds a field. A name that is not a token, a value with CR, LF or NUL, and `host`, `content-length` and
`transfer-encoding` (the client's own) are `BadRequest`.

### ClientRequest.setAuthority

```ceesafe
public fn setAuthority(borrow mut this, authority: Slice<u8>) -> Result<void, ClientError>
```

Sends `authority` (`host[:port]`, RFC 9110 §7.2) as `host` instead of the URL's, while the URL still chooses
where the request goes: a reverse proxy keeps the client's `host` this way (Web ADR 0012 §3.5). A redirect to
another origin goes back to that origin's own authority. Empty, or a byte that cannot be in an authority
(whitespace, a control, `/`, `?`, `#`, `@`), is `BadRequest`.

### ClientRequest.method

```ceesafe
public fn method(borrow this) -> Method
```

The request's method.

### ClientRequest.url

```ceesafe
public fn url(borrow this) -> borrow Url
```

The request's URL.

## interface BodySource

```ceesafe
public interface BodySource {
    @effects()
    fn length(borrow this) -> u64?;
    async fn read(borrow mut this, into: SliceMut<u8>) -> Result<usize, NetError>;
    @effects()
    fn rewind(borrow mut this) -> bool;
    @effects(alloc)
    fn copyTrailers(borrow this, into: borrow mut HeaderBlock) -> Result<void, AllocError>;
}
```

A request body, streamed.

## struct NoBody

```ceesafe
public struct NoBody
```

No body.

Implements `BodySource`.

## struct BytesBody

```ceesafe
@noncopy
public struct BytesBody
```

A body held in memory: its length is known, and it can always replay.

### BytesBody.of

```ceesafe
public fn of(bytes: Slice<u8>) -> Result<BytesBody, ClientError>
```

A body of a copy of `bytes`.

Implements `BodySource`.

## struct Client

```ceesafe
public struct Client
```

An HTTP client: a pool of connections per origin, shared by concurrent requests.

### Client.create

```ceesafe
public fn create(options: ClientOptions, tls: Box<dyn Connector + Send + Share>?) -> Result<Client, ClientError>
```

A client with the system's resolver and TCP. `tls` makes https connections (a web_tls `TlsConnector`); without
one, https URLs are `NoTls`.

### Client.withNetwork

```ceesafe
public fn withNetwork(
    options: ClientOptions,
    tls: Box<dyn Connector + Send + Share>?,
    resolver: Box<dyn Resolver + Send + Share>,
    dialer: Box<dyn Dialer + Send + Share>,
) -> Result<Client, ClientError>
```

A client with its own resolver and dialer (tests: mocks).

### Client.run

```ceesafe
public async fn run(borrow this, drain: borrow Drain) -> Result<void, NetError>
```

Reads and writes the client's HTTP/2 connections until `drain` starts, then lets each finish its streams
(Web ADR 0012 §7). Run it beside the code that sends requests, in the same scope. While it runs, https origins
whose connector negotiates ALPN `h2`, and http origins with `http2Cleartext`, are spoken to over HTTP/2, many
requests sharing a connection; without it the client speaks HTTP/1.1, and a connection that negotiated `h2`
fails with `Http2NotRunning`.

### Client.get

```ceesafe
public async fn get(borrow this, url: StringView) -> Result<ClientResponse, ClientError>
```

GET `url`, without a body.

### Client.send

```ceesafe
public async fn send(
    borrow this,
    request: borrow ClientRequest,
    body: borrow mut dyn BodySource,
) -> Result<ClientResponse, ClientError>
```

Sends `request` with `body`, following redirects and retrying as the options allow; the response's head has
arrived, its body is the caller's to read (then `finish`).

### Client.connectWebSocket

```ceesafe
public async fn connectWebSocket(
    borrow this,
    url: StringView,
    options: borrow WebSocketClientOptions,
) -> Result<WebSocketConnection, ClientError>
```

Opens a WebSocket to `url` (`ws://` or `wss://`): DNS, Happy Eyeballs and TLS as for a request, then the
upgrade (Web ADR 0010 §3.6), all within `options.handshakeMs`. Run the session with `clientSession`. No
redirect is followed: a server that does not switch is `WebSocket(NotSwitched(status))`.

### Client.upgrade

```ceesafe
public async fn upgrade(
    borrow this,
    request: borrow ClientRequest,
    protocol: Slice<u8>,
    body: borrow mut dyn BodySource,
) -> Result<Upgraded, ClientError>
```

Sends `request` asking to switch to `protocol` (RFC 9110 §7.8: `connection: upgrade` and `upgrade: protocol`,
which the client writes in place of the request's own). No redirect is followed and nothing is retried. A 101
gives the connection, which leaves the pool, as `Switched`; any other answer is an ordinary response.
`protocol` is one or more `name[/version]` tokens separated by commas; anything else is `BadRequest`.

### Client.shutdown

```ceesafe
public async fn shutdown(borrow this)
```

Closes the idle connections; requests that start afterwards fail with `Shutdown`.

## enum Upgraded

```ceesafe
public enum Upgraded {
    Switched(Switched),
    Answered(ClientResponse),
}
```

What `Client.upgrade` got: the connection switched, or an ordinary answer.

## struct Switched

```ceesafe
@noncopy
public struct Switched
```

A connection after `101 Switching Protocols`: the bytes are the new protocol's, both ways.

### Switched.fields

```ceesafe
public fn fields(borrow this, into: borrow mut HeaderBlock) -> Result<void, ClientError>
```

Appends a copy of the 101's fields to `into` (`ClientResponse.fields`).

### Switched.early

```ceesafe
public fn early(borrow this) -> Slice<u8>
```

The bytes the server sent after its 101 that were read with it: the new protocol's first, before any read.

### Switched.transport

```ceesafe
public fn transport(borrow mut this) -> borrow mut Box<dyn Transport + Send>
```

The connection, to read and write the new protocol.

## struct ClientResponse

```ceesafe
@noncopy
public struct ClientResponse {
    drop { … }
}
```

A response: its head, and its body to read. Read it to the end and `finish`, to give the connection back to the
pool; dropped otherwise, it closes the connection.

### ClientResponse.status

```ceesafe
public fn status(borrow this) -> u16
```

The status code.

### ClientResponse.url

```ceesafe
public fn url(borrow this) -> borrow Url
```

The URL that answered (after redirects).

### ClientResponse.header

```ceesafe
public fn header(borrow this, name: StringView) -> Vector<u8>?
```

A copy of the first value of field `name`, if present.

### ClientResponse.fields

```ceesafe
public fn fields(borrow this, into: borrow mut HeaderBlock) -> Result<void, ClientError>
```

Appends a copy of every response field to `into`, in order, names lowercased (Web ADR 0012 §3.5: a proxy relays
them).

### ClientResponse.trailers

```ceesafe
public fn trailers(borrow this) -> borrow HeaderBlock
```

The response's trailer fields (RFC 9110 §6.5): empty until `read` has returned 0, and empty if there were none.
From a chunked body's trailer section over HTTP/1.1, from the HEADERS that ended the stream over HTTP/2.

### ClientResponse.isHttp2

```ceesafe
public fn isHttp2(borrow this) -> bool
```

Whether the response came over HTTP/2.

### ClientResponse.read

```ceesafe
public async fn read(borrow mut this, buffer: SliceMut<u8>) -> Result<usize, ClientError>
```

Body bytes into `buffer`: at least one, or 0 at the end.

### ClientResponse.readToEnd

```ceesafe
public async fn readToEnd(borrow mut this, into: borrow mut Vector<u8>, limit: usize) -> Result<void, ClientError>
```

The whole body into `into`, up to `limit` bytes (`Net(LimitExceeded)` past it).

### ClientResponse.finish

```ceesafe
public async fn finish(borrow mut this, client: borrow Client) -> Result<void, ClientError>
```

Reads what is left of the body (up to the client's drain limit) and gives a reusable connection back to
`client`'s pool (the client that sent the request); otherwise the connection closes.

## interface Resolver

```ceesafe
public interface Resolver {
    async fn resolve(borrow this, host: StringView, port: u16) -> Result<Vector<SocketAddress>, NetError>;
}
```

Names to addresses.

## struct SystemResolver

```ceesafe
public struct SystemResolver
```

The system's resolver (lib/std's AsyncDns, CeeSafe ADR 0048). An IP literal resolves to itself without DNS.

Implements `Resolver`.

## interface DialJob

```ceesafe
public interface DialJob {
    async fn dial(borrow mut this) -> Result<PlainStream, NetError>;
}
```

One connection attempt, owned by the task that runs it.

## interface Dialer

```ceesafe
public interface Dialer {
    @effects(alloc)
    fn job(borrow this, address: SocketAddress) -> Result<Box<dyn DialJob>, NetError>;
}
```

Makes connection attempts.

## struct TcpDialer

```ceesafe
public struct TcpDialer
```

TCP.

Implements `Dialer`.

## fn interleave

```ceesafe
public fn interleave(addresses: borrow Vector<SocketAddress>) -> Result<Vector<SocketAddress>, NetError>
```

The addresses in RFC 8305 §4's order: alternating families, starting with the first address's family.

## fn happyEyeballs

```ceesafe
public async fn happyEyeballs(
    addresses: borrow Vector<SocketAddress>,
    dialer: borrow dyn Dialer,
    delayMs: u64,
    deadline: Deadline,
) -> Result<PlainStream, NetError>
```

Happy Eyeballs v2 over `addresses` (already in order): an attempt starts every `delayMs`, or as soon as the one
before failed; the first to connect wins and the others are cancelled. The last failure if every attempt fails;
`TimedOut` at `deadline`.

## struct RetryPolicy

```ceesafe
public struct RetryPolicy {
    public maxAttempts: u32;
    public budgetPercent: u32;
    public budgetInitial: u32;
}
```

When a failed attempt may be sent again (Web ADR 0009 §3.8). Retrying needs all of: an idempotent method or an
`Idempotency-Key`; no byte of the request written, or a reuse race; a body that can replay; and room in
`maxAttempts` and the origin's budget.

### RetryPolicy.defaults

```ceesafe
public fn defaults() -> RetryPolicy
```

2 attempts; 10 % of requests, plus 3 to start.

### RetryPolicy.never

```ceesafe
public fn never() -> RetryPolicy
```

Never retry.

## struct ClientOptions

```ceesafe
public struct ClientOptions {
    public connectMs: u64;
    public tlsMs: u64;
    public firstByteMs: u64;
    public totalMs: u64;
    public connectionAttemptDelayMs: u64;
    public maxPerOrigin: u32;
    public maxIdlePerOrigin: u32;
    public idleTimeoutMs: u64;
    public maxRedirects: u32;
    public maxDrainBytes: u64;
    public limits: Limits;
    public retry: RetryPolicy;
    public http2Cleartext: bool;
}
```

A client's bounds and timeouts. Times are milliseconds; 0 for `totalMs` means none.

### ClientOptions.defaults

```ceesafe
public fn defaults() -> ClientOptions
```

10 s to connect and to handshake, 30 s to the first byte, no total; 250 ms between attempts; 8 connections
per origin, 4 idle for 30 s; 10 redirects; 64 KiB drained.

## enum ClientError

```ceesafe
public enum ClientError {
    BadUrl(UrlError),
    BadRequest,
    NoTls,
    Net(NetError),
    Protocol(ProtocolError),
    Shutdown,
    WebSocket(HandshakeFailure),
    Http2NotRunning,
}
```

Why a request failed.

## struct ClientErrors

```ceesafe
public struct ClientErrors
```

Descriptions.

### ClientErrors.describe

```ceesafe
public fn describe(e: borrow ClientError) -> StringView
```

A short description of `e`, for logs and messages.

## struct WebSocketClientOptions

```ceesafe
public struct WebSocketClientOptions {
    public session: WebSocketOptions;
    public origin: Vector<u8>?;
    public handshakeMs: u64;
    public writeTimeoutMs: u64;
}
```

How a WebSocket client connects, and how its session behaves.

### WebSocketClientOptions.defaults

```ceesafe
public fn defaults() -> WebSocketClientOptions
```

10 s for the handshake, 30 s for each write, no `Origin`, and the session's defaults.

## struct WebSocketConnection

```ceesafe
@noncopy
public struct WebSocketConnection
```

An open WebSocket connection after a successful handshake: the transport, the bytes that came after the 101's
head, and what the server agreed to. `clientSession` runs a session over it; dropping it closes the connection.

### WebSocketConnection.protocol

```ceesafe
public fn protocol(borrow this) -> usize?
```

The index in the offered subprotocols of the one the server chose, if any.

### WebSocketConnection.compressing

```ceesafe
public fn compressing(borrow this) -> bool
```

Whether permessage-deflate was agreed.

Implements `RawLink`.

## fn clientSession

```ceesafe
public fn clientSession<region r>(
    connection: borrow mut(r) WebSocketConnection,
    options: borrow WebSocketClientOptions,
) -> Result<WebSocket<r, WebSocketConnection>, ClientError>
```

The session over `connection`, as the client: it masks what it sends and refuses masked frames. `options` must be
the ones the connection was made with (`subprotocol()` indexes their `subprotocols`).

## fn webSocketUrl

```ceesafe
public fn webSocketUrl(text: Slice<u8>) -> Result<Url, ClientError>
```

`text` as the http(s) URL the handshake is sent to: `ws://` is `http://` and `wss://` is `https://` (RFC 6455
§3); anything else is `BadScheme`. A `#` is `BadCharacter`: a fragment "MUST NOT be used" and the character must
be escaped (§3).
