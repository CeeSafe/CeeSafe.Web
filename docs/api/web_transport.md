# web_transport

- [struct ConnectionLimits](#struct-connectionlimits)
- [struct MockTransport](#struct-mocktransport)
- [struct PlainStream](#struct-plainstream)
- [interface Acceptor](#interface-acceptor)
- [interface Connector](#interface-connector)
- [struct PlainAcceptor](#struct-plainacceptor)
- [struct PlainConnector](#struct-plainconnector)
- [struct Cidr](#struct-cidr)
- [struct TrustedProxies](#struct-trustedproxies)
- [enum ProxyHeader](#enum-proxyheader)
- [enum ProxyError](#enum-proxyerror)
- [struct ProxyProtocol](#struct-proxyprotocol)
- [fn hasZone](#fn-haszone)
- [fn readProxyHeader](#fn-readproxyheader)
- [fn ipText](#fn-iptext)
- [interface Incoming](#interface-incoming)
- [interface ConnectionHandler](#interface-connectionhandler)
- [struct MockIncoming](#struct-mockincoming)
- [struct ServerReport](#struct-serverreport)
- [struct ConnectionServer](#struct-connectionserver)
- [struct TcpTransport](#struct-tcptransport)
- [struct Listener](#struct-listener)

## struct ConnectionLimits

```ceesafe
public struct ConnectionLimits {
    public workers: u32;
    public handshakeMs: u64;
    public headerReadMs: u64;
    public idleMs: u64;
    public writeMs: u64;
    public minBodyRate: u64;
    public minRateGraceMs: u64;
    public readBufferSize: usize;
    public closeLingerMs: u64;
    public graceMs: u64;
    public acceptBackoffMs: u64;
    public proxyProtocolFrom: TrustedProxies;
}
```

What one server admits and how long each step of a connection may take. Times are milliseconds. The defaults are
conservative for a public server; `docs/milestones/M1.md` records where each comes from.

### ConnectionLimits.defaults

```ceesafe
public fn defaults() -> ConnectionLimits
```

The defaults: 256 workers, 10 s handshake and head, 60 s idle, 30 s write, 240 B/s after 5 s, 16 KiB buffer,
1 s linger, 30 s grace, 50 ms accept backoff, no PROXY protocol.

## struct MockTransport

```ceesafe
public struct MockTransport
```

A Transport whose peer is a script: input delivered in chosen fragments, optional failures at chosen offsets, and
everything written recorded. Reads and writes suspend only when asked to (`withPause`), so a test chooses where
cancellation can land. It follows Transport's contract: an empty read buffer is `LimitExceeded`, and a failed or
cancelled write poisons it (Web ADR 0001 §3.4).

### MockTransport.create

```ceesafe
public fn create(input: Slice<u8>) -> Result<MockTransport, AllocError>
```

A transport whose peer sends `input` and then ends its side.

### MockTransport.withAddresses

```ceesafe
public fn withAddresses(this, peer: SocketAddress?, local: SocketAddress?) -> MockTransport
```

The addresses `peerAddress` and `localAddress` report (null by default).

### MockTransport.withCopiesTo

```ceesafe
public fn withCopiesTo(this, copies: borrow Channel<u8>) -> MockTransport
```

Every byte written is also sent to `copies` (dropped if it is full), so a test can see what was written after
handing the transport to a pool.

### MockTransport.withChunk

```ceesafe
public fn withChunk(this, n: usize) -> MockTransport
```

Each read returns at most `n` bytes (n ≥ 1; 0 is taken as 1).

### MockTransport.withSeededSplits

```ceesafe
public fn withSeededSplits(this, seed: u64, max: usize) -> MockTransport
```

Each read returns 1 to `max` bytes, chosen from `seed` (reproducible). A zero seed is replaced by a fixed one.

### MockTransport.withResetAt

```ceesafe
public fn withResetAt(this, offset: usize) -> MockTransport
```

The read that would deliver the byte at `offset` returns `Err(Reset)` instead, after delivering the bytes
before it. Every later read is `Err(Reset)` too.

### MockTransport.withHang

```ceesafe
public fn withHang(this) -> MockTransport
```

At the end of the input, reads wait (until cancelled) instead of returning 0: a peer that never ends its side.

### MockTransport.withPause

```ceesafe
public fn withPause(this, ms: u64) -> MockTransport
```

Each read and write first waits `ms` milliseconds (0: suspends once and resumes at once), so a cancellation
or a deadline can land in it.

### MockTransport.withCompletingReads

```ceesafe
public fn withCompletingReads(this) -> MockTransport
```

With a pause (`withPause`), a read cancelled in its pause still returns its bytes: a completion-based read
(io_uring, IOCP) that finished as it was cancelled (CeeSafe ADR 0035 §3.4). Reads at the end of a hanging input
still wait until cancelled.

### MockTransport.withWriteStep

```ceesafe
public fn withWriteStep(this, n: usize) -> MockTransport
```

Each `writeVectored` step writes at most `n` bytes (0: no limit).

### MockTransport.withWriteFailureAfter

```ceesafe
public fn withWriteFailureAfter(this, n: usize) -> MockTransport
```

After `n` bytes have been written in total, the next write fails with `Err(Reset)` (and poisons). The bytes
before the limit are recorded, as a kernel would have sent them.

### MockTransport.withAlpn

```ceesafe
public fn withAlpn(this, protocol: Alpn) -> MockTransport
```

The protocol `alpn()` reports.

### MockTransport.written

```ceesafe
public fn written(borrow this) -> Slice<u8>
```

Everything written so far.

### MockTransport.sendFileCalls

```ceesafe
public fn sendFileCalls(borrow this) -> usize
```

How many times `sendFile` was called: a test sees which path a driver took (Web ADR 0013 §5).

### MockTransport.consumed

```ceesafe
public fn consumed(borrow this) -> usize
```

How many input bytes have been read.

### MockTransport.isShutdown

```ceesafe
public fn isShutdown(borrow this) -> bool
```

Whether `shutdownWrite` was called.

### MockTransport.isPoisoned

```ceesafe
public fn isPoisoned(borrow this) -> bool
```

Whether a failed or cancelled write poisoned it.

Implements `Transport`.

## struct PlainStream

```ceesafe
public struct PlainStream
```

A plain (not encrypted) transport: TCP, or a mock in tests. TLS (web_tls) wraps one.

### PlainStream.tcp

```ceesafe
public fn tcp(t: TcpTransport) -> PlainStream
```

A TCP connection.

### PlainStream.mock

```ceesafe
public fn mock(m: MockTransport) -> PlainStream
```

A scripted transport, for tests.

### PlainStream.isMock

```ceesafe
public fn isMock(borrow this) -> bool
```

Whether it is a mock.

Implements `Transport`.

## interface Acceptor

```ceesafe
public interface Acceptor {
    async fn accept(borrow this, stream: PlainStream, deadline: Deadline) -> Result<Box<dyn Transport>, NetError>;
}
```

Turns an accepted plain stream into the connection's transport: as it is, or after a TLS handshake (web_tls's
TlsAcceptor). The server takes one, so packages below web_tls never link OpenSSL (Web ADR 0004 §3.5).

## interface Connector

```ceesafe
public interface Connector {
    async fn connect(borrow this, stream: PlainStream, host: StringView, deadline: Deadline,) -> Result<Box<dyn Transport + Send>, NetError>;
}
```

Turns a connected plain stream into the client's transport; `host` is the name a TLS connector verifies.

## struct PlainAcceptor

```ceesafe
public struct PlainAcceptor
```

The acceptor for plain connections: boxes the stream as it is.

Implements `Acceptor`.

## struct PlainConnector

```ceesafe
public struct PlainConnector
```

The connector for plain connections: boxes the stream as it is.

Implements `Connector`.

## struct Cidr

```ceesafe
public struct Cidr
```

A block of addresses: `10.0.0.0/8`, `2001:db8::/32`, or one address (`192.0.2.1`, a /32 or /128).

### Cidr.parse

```ceesafe
public fn parse(text: Slice<u8>) -> Cidr?
```

The block `text` names; null if it is not an address with an optional `/prefix` within its family's length.

### Cidr.contains

```ceesafe
public fn contains(borrow this, address: SocketAddress) -> bool
```

Whether `address` is in the block. An IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is matched as IPv4.

## struct TrustedProxies

```ceesafe
public struct TrustedProxies
```

Trusted addresses as CIDR blocks: the balancers a listener takes PROXY headers from, or the proxies whose forwarding
fields `web_proxy` believes. None by default.

### TrustedProxies.none

```ceesafe
public fn none() -> TrustedProxies
```

No proxy is trusted: every peer is the client.

### TrustedProxies.add

```ceesafe
public fn add(borrow mut this, text: StringView) -> Result<bool, AllocError>
```

Trusts the block `text` (`Cidr.parse`); false if it is not one.

### TrustedProxies.isEmpty

```ceesafe
public fn isEmpty(borrow this) -> bool
```

Whether there are no blocks.

### TrustedProxies.trusts

```ceesafe
public fn trusts(borrow this, address: SocketAddress) -> bool
```

Whether `address` is in one of the blocks.

## enum ProxyHeader

```ceesafe
public enum ProxyHeader {
    Proxied(SocketAddress, SocketAddress),
    Local,
}
```

What a header says.

## enum ProxyError

```ceesafe
public enum ProxyError {
    NotProxy,
    Malformed,
    TooLong,
}
```

Why a header was refused.

## struct ProxyProtocol

```ceesafe
public struct ProxyProtocol
```

Parsing.

### ProxyProtocol.maxV2Bytes

```ceesafe
public fn maxV2Bytes() -> usize
```

The bytes a v2 header may take with its TLVs; past this it is refused (the spec lets a receiver bound it).

### ProxyProtocol.more

```ceesafe
public fn more(got: Slice<u8>) -> Result<usize, ProxyError>
```

How many more bytes the header needs after `got`, the bytes read so far: 0 when it is complete. Reading exactly
this many never takes a byte past the header. With nothing read it is 15, the shortest header of either
version ("PROXY UNKNOWN\r\n"; v2's is 16).

### ProxyProtocol.parse

```ceesafe
public fn parse(header: Slice<u8>) -> Result<ProxyHeader, ProxyError>
```

The header in `header`, which `more` says is complete.

## fn hasZone

```ceesafe
public fn hasZone(text: Slice<u8>) -> bool
```

Whether `text` holds a `%`, which starts an IPv6 zone: no address in a header or a block has one.

## fn readProxyHeader

```ceesafe
public async fn readProxyHeader(conn: borrow mut dyn Transport, deadline: Deadline) -> Result<ProxyHeader, NetError>
```

Reads a PROXY header from `conn` by `deadline`, exactly: each read asks for no more than `ProxyProtocol.more`
says, so the bytes after the header stay unread. `Err(Closed)` if what arrives is not a valid header.

## fn ipText

```ceesafe
public fn ipText(address: SocketAddress, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

`address`'s IP as text, without its port or brackets: `192.0.2.1`, `2001:db8::1` (RFC 5952's form).

## interface Incoming

```ceesafe
public interface Incoming {
    async fn next(borrow this) -> Result<PlainStream, NetError>;
}
```

Where connections come from: a TCP listener, or a queue of mocks in tests. Shared by every worker.

## interface ConnectionHandler

```ceesafe
public interface ConnectionHandler {
    async fn serve(borrow this, conn: borrow mut dyn Transport, ctx: borrow ConnectionContext) -> Result<void, NetError>;
}
```

Serves one connection: the protocol driver (HTTP/1.1, M2) or a test's handler.

## struct MockIncoming

```ceesafe
@noncopy
public struct MockIncoming
```

A queue of scripted connections for tests: push mocks, then close it to end the server's source.

### MockIncoming.create

```ceesafe
public fn create(capacity: usize) -> Result<MockIncoming, NetError>
```

A queue holding up to `capacity` connections not yet accepted.

### MockIncoming.push

```ceesafe
public fn push(borrow this, m: MockTransport) -> Result<void, NetError>
```

Queues a connection; `Err(LimitExceeded)` if the queue is full or closed.

### MockIncoming.close

```ceesafe
public fn close(borrow this)
```

No more connections: once the queue is empty, `next` returns `Err(Closed)`.

Implements `Incoming`.

## struct ServerReport

```ceesafe
public struct ServerReport {
    public accepted: u64;
    public handshakesFailed: u64;
    public served: u64;
    public failed: u64;
    public acceptErrors: u64;
    public forced: bool;
}
```

What a server did, counted by every worker.

## struct ConnectionServer

```ceesafe
public struct ConnectionServer
```

Runs connection servers.

### ConnectionServer.run

```ceesafe
public async fn run(
    incoming: borrow dyn Incoming,
    acceptor: borrow dyn Acceptor,
    handler: borrow dyn ConnectionHandler,
    limits: borrow ConnectionLimits,
    drain: borrow Drain,
) -> Result<ServerReport, NetError>
```

Serves connections from `incoming` with `limits.workers` workers until the drain finishes, or until `incoming`
ends and every connection is done. Once `drain` starts, idle workers stop at once and connections in flight
have `limits.graceMs` to finish; then they are cancelled (`forced` in the report). A connection's failure ends
that connection only.

## struct TcpTransport

```ceesafe
public struct TcpTransport
```

A TCP connection as a Transport. It owns its socket; dropping it closes the socket.

### TcpTransport.fromStream

```ceesafe
public fn fromStream(stream: AsyncTcpStream) -> TcpTransport
```

The transport over an accepted or connected stream. Its addresses are read now, once: null if the kernel no
longer knows them (the peer already reset).

### TcpTransport.connect

```ceesafe
public async fn connect(address: StringView, port: u16) -> Result<TcpTransport, NetError>
```

Connects to `address` (IPv4 or IPv6 text) and `port`.

### TcpTransport.connectTo

```ceesafe
public async fn connectTo(address: SocketAddress) -> Result<TcpTransport, NetError>
```

Connects to `address`.

### TcpTransport.setNoDelay

```ceesafe
public fn setNoDelay(borrow this, on: bool) -> Result<void, NetError>
```

TCP_NODELAY: small writes go out at once (Nagle's algorithm off).

### TcpTransport.abort

```ceesafe
public fn abort(this)
```

Closes abortively: the peer gets a reset (RST) instead of the end of the stream, and unsent bytes are
discarded (SO_LINGER 0). For a connection that broke the protocol, and for tests of a peer's reset.

### TcpTransport.isPoisoned

```ceesafe
public fn isPoisoned(borrow this) -> bool
```

Whether a failed or cancelled write poisoned it (Web ADR 0001 §3.4).

Implements `Transport`.

## struct Listener

```ceesafe
public struct Listener
```

A TCP listener producing PlainStreams, for `ConnectionServer` (its `Incoming`). Accepted connections have
TCP_NODELAY on: a server writes whole responses, and Nagle's algorithm would hold back their last segment.

### Listener.bind

```ceesafe
public fn bind(address: StringView, port: u16) -> Result<Listener, NetError>
```

Listens on `address` (IPv4 or IPv6 text) and `port` (0: any free port), with `ListenOptions.defaults()`.

### Listener.bindWith

```ceesafe
public fn bindWith(address: SocketAddress, options: ListenOptions) -> Result<Listener, NetError>
```

Listens on `address` with `options`: the backlog, address and port reuse, and dual stack (CeeSafe ADR 0044
§3.2).

### Listener.withNoDelay

```ceesafe
public fn withNoDelay(this, on: bool) -> Listener
```

The same listener, accepting with TCP_NODELAY `on` (the default) or off.

### Listener.localAddress

```ceesafe
public fn localAddress(borrow this) -> Result<SocketAddress, NetError>
```

The address it listens on.

### Listener.port

```ceesafe
public fn port(borrow this) -> Result<u16, NetError>
```

The port it listens on.

### Listener.accept

```ceesafe
public async fn accept(borrow this) -> Result<PlainStream, NetError>
```

The next connection; the task waits until one arrives.

Implements `Incoming`.
