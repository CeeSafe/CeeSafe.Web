# web_quic

- [enum QuicError](#enum-quicerror)
- [enum QuicEvent](#enum-quicevent)
- [enum QuicIdChange](#enum-quicidchange)
- [struct QuicHeader](#struct-quicheader)
- [struct QuicAddress](#struct-quicaddress)
- [struct QuicTls](#struct-quictls)
- [fn quicDatagramSize](#fn-quicdatagramsize)
- [fn quicEventBufferSize](#fn-quiceventbuffersize)
- [struct QuicIds](#struct-quicids)
- [struct QuicConnection](#struct-quicconnection)
- [struct QuicOut](#struct-quicout)
- [enum QuicRetryCheck](#enum-quicretrycheck)
- [struct QuicRetry](#struct-quicretry)
- [struct HqFiles](#struct-hqfiles)
- [struct QuicLibrary](#struct-quiclibrary)
- [struct QuicServerOptions](#struct-quicserveroptions)
- [interface QuicApplication](#interface-quicapplication)
- [struct QuicLink](#struct-quiclink)
- [struct QuicServer](#struct-quicserver)

## enum QuicError

```ceesafe
public enum QuicError {
    Draining,
    IdleTimeout,
    NotInitial,
    StreamsBlocked,
    NoMemory,
    BufferTooSmall(usize),
    Failed(i32),
    Tls,
    RetryWanted,
}
```

How a QUIC operation failed (Web ADR 0014 §3.4).

## enum QuicEvent

```ceesafe
public enum QuicEvent {
    HandshakeDone,
    StreamData(i64, usize, bool),
    StreamOpened(i64),
    StreamClosed(i64, u64?),
    StreamReset(i64, u64),
    StopSending(i64, u64),
    StreamWritable(i64),
    StreamsAvailable(u64),
    EarlyData(i64),
}
```

What happened on a connection, from `nextEvent`.

## enum QuicIdChange

```ceesafe
public enum QuicIdChange {
    Issued(usize),
    Retired(usize),
}
```

A change to a server connection's IDs (`QuicConnection.nextIdChange`), the ID of this many bytes in the caller's
buffer.

## struct QuicHeader

```ceesafe
public struct QuicHeader {
    public dcid: [u8; 20];
    public dcidLength: usize;
    public initial: bool;
    public version: u32;
}
```

What a datagram's header says, before any connection reads it (RFC 9000 §17).

### QuicHeader.of

```ceesafe
public fn of(datagram: Slice<u8>) -> QuicHeader?
```

The header of `datagram`, or null if it is not a QUIC packet. Short headers are read with this package's IDs'
length, 20 bytes.
Safety: the shim reads the datagram and writes at most 20 bytes into a local array, during the call only.

### QuicHeader.id

```ceesafe
public fn id(borrow this) -> Slice<u8>
```

The destination connection ID.

## struct QuicAddress

```ceesafe
public struct QuicAddress {
    public bytes: [u8; 19];
}
```

A UDP endpoint as the shim takes it: family (4 or 6), the port big-endian, then 16 address bytes.

### QuicAddress.socketAddress

```ceesafe
public fn socketAddress(borrow this) -> SocketAddress?
```

The socket address this is (a zone of 0 for IPv6), or null if the bytes are neither form.

### QuicAddress.of

```ceesafe
public fn of(a: borrow SocketAddress) -> QuicAddress
```

The shim's form of `a`. A zone is not carried (a link-local peer's zone is the socket's).

## struct QuicTls

```ceesafe
@threadSafe
@noncopy
public struct QuicTls {
    drop { … }
}
```

A TLS context for QUIC: `web_tls`'s configuration, whose SSL_CTX the shim holds a reference to. A server's
configuration offers ALPN `h3` (`Alpn.Http3`); it cannot be shared with a TCP listener's, whose list differs.
Thread-safe by this package's claim (`trusted`, as web_tls's TlsConfig): the shim never changes a qs_tls after
making it, and OpenSSL 3 lets threads share an SSL_CTX, making each connection's SSL from it under its own lock.

### QuicTls.of

```ceesafe
public fn of(config: borrow TlsConfig) -> Result<QuicTls, QuicError>
```

The QUIC context of `config`.
Safety: `quicContext` is the configuration's live SSL_CTX, and qs_tls_new takes its own reference to it
(SSL_CTX_up_ref) before returning, so the context outlives `config` if need be.

### QuicTls.serverFromPem

```ceesafe
public fn serverFromPem(chain: Slice<u8>, key: Slice<u8>, protocols: Slice<Alpn>) -> Result<QuicTls, QuicError>
```

A server's context for the PEM certificate chain `chain` and its key, offering `protocols` (ALPN, in order of
preference): `web_tls`'s `TlsConfig.server` and `setAlpn`, for a program that needs no other TLS setting.

### QuicTls.clientFromPem

```ceesafe
public fn clientFromPem(trust: Slice<u8>, protocols: Slice<Alpn>) -> Result<QuicTls, QuicError>
```

A client's context trusting the PEM roots in `trust`, offering `protocols`.

### QuicTls.allowEarlyData

```ceesafe
public fn allowEarlyData(borrow mut this)
```

Lets a server's connections accept 0-RTT (Web ADR 0014 §3.7: off unless called). Early data's protection against
replay is OpenSSL's single-use tickets, kept in this context's session cache: each ticket's early data is taken
at most once, and nowhere else. A replay that comes before the client's own copy still runs once (RFC 9001
§9.2), so HTTP/3 answers 425 to early requests but GET, HEAD and OPTIONS. Call before the server starts.
Safety: `raw` is this context's handle; the shim sets a flag read when each connection is made.

### QuicTls.isServer

```ceesafe
public fn isServer(borrow this) -> bool
```

Whether this is a server's.

## fn quicDatagramSize

```ceesafe
public fn quicDatagramSize() -> usize
```

The buffer `datagram` writes into: 1452 bytes, ngtcp2's default largest datagram (`max_tx_udp_payload_size`,
1500 - 48, `ngtcp2_settings.c`), which fits IPv6 under a 1500-byte MTU.

## fn quicEventBufferSize

```ceesafe
public fn quicEventBufferSize() -> usize
```

The buffer `nextEvent` takes, which every event's data fits: the largest UDP payload over IPv4, since a peer's
datagram, and the stream data in it, may be larger than ours.

## struct QuicIds

```ceesafe
public struct QuicIds {
    public scid: [u8; 20];
    public dcid: [u8; 20];
    public resetSecret: [u8; 32];
}
```

A connection's random material: its source ID, and the secret its stateless-reset tokens come from.

### QuicIds.random

```ceesafe
public fn random() -> Result<QuicIds, QuicError>
```

Fresh random IDs and secret (lib/std's `Random.fill`, the system's CSPRNG).

## struct QuicConnection

```ceesafe
@noncopy
public struct QuicConnection {
    drop { … }
}
```

One QUIC connection, client or server.

### QuicConnection.client

```ceesafe
public fn client(
    tls: borrow QuicTls,
    host: StringView,
    local: borrow QuicAddress,
    remote: borrow QuicAddress,
    ids: borrow QuicIds,
    now: u64,
) -> Result<QuicConnection, QuicError>
```

A client connection to `remote` from `local`, for `host` (its SNI, and the name its certificate is verified
against).
Safety: the shim copies every byte it is given and keeps no pointer to them; it returns a fresh handle or null.

### QuicConnection.server

```ceesafe
public fn server(
    tls: borrow QuicTls,
    packet: Slice<u8>,
    local: borrow QuicAddress,
    remote: borrow QuicAddress,
    ids: borrow QuicIds,
    now: u64,
) -> Result<QuicConnection, QuicError>
```

A server connection for the client Initial `packet`, which arrived at `local` from `remote`. The packet is
not consumed: `feed` it next.
Safety: as `client`.

### QuicConnection.serverAfterRetry

```ceesafe
public fn serverAfterRetry(
    tls: borrow QuicTls,
    packet: Slice<u8>,
    local: borrow QuicAddress,
    remote: borrow QuicAddress,
    ids: borrow QuicIds,
    original: Slice<u8>,
    now: u64,
) -> Result<QuicConnection, QuicError>
```

A server connection for a client's Initial that carried a valid Retry token (`QuicRetryCheck.AdmitRetried`):
`original` is the destination ID of the client's first Initial, which the token held (RFC 9000 §7.3).

### QuicConnection.feed

```ceesafe
public fn feed(
    borrow mut this,
    remote: borrow QuicAddress,
    ecn: u32,
    datagram: Slice<u8>,
    now: u64,
) -> Result<void, QuicError>
```

Feeds one datagram from `remote`, with its ECN mark. `QuicError.RetryWanted` comes only from a server
connection's first datagram.
Safety: the shim reads the datagram and the address during the call only.

### QuicConnection.datagram

```ceesafe
public fn datagram(borrow mut this, out: SliceMut<u8>, now: u64) -> Result<QuicOut?, QuicError>
```

The next datagram to send, written into `out`: its length and ECN mark, or null when there is nothing to send
now. `out` should hold `quicDatagramSize()` bytes.
Safety: the shim writes at most `out.length` bytes into `out`.

### QuicConnection.closeDatagram

```ceesafe
public fn closeDatagram(borrow mut this, code: u64?, out: SliceMut<u8>, now: u64) -> Result<usize, QuicError>
```

A CONNECTION_CLOSE for the error that ended the connection, or NO_ERROR, or the application's `code`; its
length in `out`. Send it, then drop the connection.
Safety: as `datagram`.

### QuicConnection.nextTimeout

```ceesafe
public fn nextTimeout(borrow this) -> u64?
```

When the connection's next timer is due, in nanoseconds on the caller's clock; null for never.
Safety: no arguments but the handle.

### QuicConnection.onTimeout

```ceesafe
public fn onTimeout(borrow mut this, now: u64) -> Result<void, QuicError>
```

Runs the timers due at `now`.
Safety: as `nextTimeout`.

### QuicConnection.handshakeDone

```ceesafe
public fn handshakeDone(borrow this) -> bool
```

Whether the handshake has completed.
Safety: as `nextTimeout`.

### QuicConnection.alpn

```ceesafe
public fn alpn(borrow this) -> Alpn
```

The application protocol the handshake chose (`Alpn.Http3` for `h3`, `Alpn.HqInterop` for `hq-interop`).
Safety: the shim writes at most 16 bytes into a local array.

### QuicConnection.openStream

```ceesafe
public fn openStream(borrow mut this) -> Result<i64, QuicError>
```

Opens a bidirectional stream: its ID.
Safety: as `nextTimeout`.

### QuicConnection.peerApplicationClose

```ceesafe
public fn peerApplicationClose(borrow this) -> u64?
```

The application error code the peer closed the connection with (CONNECTION_CLOSE of type 0x1d, RFC 9000
§19.19), or null if it has not closed it so.
Safety: the shim writes the one value.

### QuicConnection.migrate

```ceesafe
public fn migrate(borrow mut this, local: borrow QuicAddress, now: u64) -> Result<void, QuicError>
```

Moves a client connection to the local address `local`, a socket the caller has bound and now sends from and
reads (RFC 9000 §9.2): ngtcp2 validates the new path with the server, then uses it. A server cannot migrate.
Safety: the shim reads the address during the call only.

### QuicConnection.ticket

```ceesafe
public fn ticket(borrow this, out: SliceMut<u8>) -> usize
```

A ticket for resuming with the same server later (`resume`), into `out`: its length, or 0 while there is none
(the server's ticket comes after the handshake). A client's.
Safety: the shim writes at most `out.length` bytes.

### QuicConnection.resume

```ceesafe
public fn resume(borrow mut this, ticket: Slice<u8>) -> Result<bool, QuicError>
```

Resumes this new client connection with `ticket`, before its first datagram: true if 0-RTT is possible, so
streams opened and written now go out as early data. A ticket that cannot be used is `Failed`.
Safety: the shim reads the ticket during the call only.

### QuicConnection.probeTimeout

```ceesafe
public fn probeTimeout(borrow this) -> u64
```

The probe timeout (RFC 9002 §6.2.1), in nanoseconds.
Safety: as `nextTimeout`.

### QuicConnection.allAcknowledged

```ceesafe
public fn allAcknowledged(borrow this) -> bool
```

Whether the peer has acknowledged everything written on every stream: a graceful close now loses nothing.
Safety: as `nextTimeout`.

### QuicConnection.resumed

```ceesafe
public fn resumed(borrow this) -> bool
```

After the handshake, whether it resumed an earlier session (the ticket was accepted), on either side.
Safety: as `nextTimeout`.

### QuicConnection.earlyDataAccepted

```ceesafe
public fn earlyDataAccepted(borrow this) -> bool
```

After the handshake, whether the server accepted this client's early data. When it did not, the server
ignored it, and QUIC sends those bytes again after the handshake by itself: writing them again would send them
twice (tests/fuzz/quic's stream data check).
Safety: as `nextTimeout`.

### QuicConnection.retried

```ceesafe
public fn retried(borrow this) -> bool
```

Whether the server sent a Retry before this connection (a client's view): its transport parameters carry
`retry_source_connection_id`, which ngtcp2 has checked against the Retry it received (RFC 9000 §7.3).
Safety: as `nextTimeout`.

### QuicConnection.openUniStream

```ceesafe
public fn openUniStream(borrow mut this) -> Result<i64, QuicError>
```

Opens a unidirectional stream: its ID (HTTP/3's control and QPACK streams, Web ADR 0014 amendment 4 item 2).
Safety: as `nextTimeout`.

### QuicConnection.creditOnConsume

```ceesafe
public fn creditOnConsume(borrow mut this)
```

From now on, a stream's flow-control credit goes back to the peer only through `consume`, not when its data's
event is taken. The peer then sends no more than the user has consumed plus the windows (1 MiB a stream,
16 MiB the connection).
Safety: as `nextTimeout`.

### QuicConnection.consume

```ceesafe
public fn consume(borrow mut this, stream: i64, n: u64)
```

Gives the peer back `n` bytes of credit on `stream` and on the connection (with `creditOnConsume`).
Safety: as `nextTimeout`.

### QuicConnection.writeStream

```ceesafe
public fn writeStream(borrow mut this, stream: i64, bytes: Slice<u8>, fin: bool) -> Result<usize, QuicError>
```

Queues `bytes` on `stream`, and ends the stream after them when `fin`: how many were taken, fewer when the
stream already holds a megabyte the peer has not acknowledged (wait for `StreamWritable`). `datagram` sends
them.
Safety: the shim copies at most `bytes.length` bytes.

### QuicConnection.resetStream

```ceesafe
public fn resetStream(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Resets our side of `stream` with the application's `code`, and asks the peer to stop sending on it.
Safety: as `nextTimeout`.

### QuicConnection.resetSending

```ceesafe
public fn resetSending(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Resets only our sending side of `stream` (RESET_STREAM, RFC 9000 §3.1); the peer's data still arrives.
Safety: as `nextTimeout`.

### QuicConnection.stopReading

```ceesafe
public fn stopReading(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Asks the peer to stop sending on `stream` (STOP_SENDING, RFC 9000 §3.5); our side may still send.
Safety: as `nextTimeout`.

### QuicConnection.nextEvent

```ceesafe
public fn nextEvent(borrow mut this, buffer: SliceMut<u8>) -> Result<QuicEvent?, QuicError>
```

The next event, its data (for `StreamData`) copied into `buffer`, which should hold `quicEventBufferSize()`
bytes; null when there is none. Drain it after every `feed`, `datagram` and `onTimeout`.
Safety: the shim writes at most `buffer.length` bytes into `buffer`, and the four values.

### QuicConnection.nextIdChange

```ceesafe
public fn nextIdChange(borrow mut this, buffer: SliceMut<u8>) -> QuicIdChange?
```

The next change to a server's connection IDs, the ID copied into `buffer` (at least 20 bytes); null when there
is none. They are apart from `nextEvent`, so a server can route an ID it issued before it sends the datagram
that announces it: a client may use a new ID at once (RFC 9000 §5.1.2), even in its Initial packets.
Safety: the shim writes at most `buffer.length` bytes into `buffer` (none if fewer than 20), and the length.

## struct QuicOut

```ceesafe
public struct QuicOut {
    public length: usize;
    public ecn: u32;
    public to: QuicAddress;
}
```

A datagram `datagram` wrote: its length in the caller's buffer, its ECN mark, and where it goes, which ngtcp2
chooses: the peer's address, or a new one it migrated to (RFC 9000 §9) or that is being validated (§8.2).

## enum QuicRetryCheck

```ceesafe
public enum QuicRetryCheck {
    SendRetry(usize),
    Admit,
    AdmitRetried([u8; 20], usize),
    Drop,
}
```

What a server does with a client's Initial for an unknown ID (RFC 9000 §8.1; `QuicRetry.check`).

## struct QuicRetry

```ceesafe
public struct QuicRetry
```

Address validation with Retry (RFC 9000 §8.1): sans-I/O, as the listener uses it.

### QuicRetry.check

```ceesafe
public fn check(
    key: [u8; 32],
    datagram: Slice<u8>,
    from: borrow QuicAddress,
    require: bool,
    now: u64,
    out: SliceMut<u8>,
) -> QuicRetryCheck
```

The decision for the client Initial `datagram` from `from` at `now`, with tokens from `key`. With `require`,
an Initial without a token gets a Retry (written to `out`, which a datagram's size always fits); an Initial
with a Retry token is admitted if the token is valid, whatever `require` says.
Safety: the shim reads the datagram, the address and the key, and writes at most `out.length` bytes and 20
into a local array, during the call only.

## struct HqFiles

```ceesafe
public struct HqFiles
```

The files under a root, over hq-interop.

### HqFiles.create

```ceesafe
public fn create(root: StringView) -> Result<HqFiles, AllocError>
```

Serves the files under `root` (a directory path without a trailing `/`).

Implements `QuicApplication`.

## struct QuicLibrary

```ceesafe
public struct QuicLibrary
```

The libraries `web_quic` runs on.

### QuicLibrary.ngtcp2Version

```ceesafe
public fn ngtcp2Version() -> u32
```

ngtcp2's version, as `0xMMmmpp` (1.26.0 is `0x011a00`).
Safety: the call takes no arguments and returns a number; ngtcp2's static version record is read in C only.

### QuicLibrary.opensslVersion

```ceesafe
public fn opensslVersion() -> u64
```

The OpenSSL the process runs with, as `OpenSSL_version_num()`: `0xMNN00PP0` (3.5.7 is `0x30500070`).
Safety: the call takes no arguments and returns a number.

### QuicLibrary.initialise

```ceesafe
public fn initialise() -> bool
```

Initialises ngtcp2's OpenSSL backend, once per process; `false` if OpenSSL refused.
Safety: the call takes no arguments; OpenSSL's one-time initialisation is thread-safe (OPENSSL_init_ssl).

## struct QuicServerOptions

```ceesafe
public struct QuicServerOptions {
    public maxConnections: u32;
    public inboundQueue: u32;
    public creditOnRead: bool;
    public resetKey: [u8; 32]?;
    public resetsPerSecond: u32;
    public retryAbove: u32;
    public retryKey: [u8; 32]?;
}
```

How a server admits connections.

### QuicServerOptions.defaults

```ceesafe
public fn defaults() -> QuicServerOptions
```

1000 connections, 64 datagrams each, credit given back on read, a random reset key, 100 resets a second, and
Retry from 500 live connections on, with a random key.

## interface QuicApplication

```ceesafe
public interface QuicApplication {
    async fn connection(borrow this, link: borrow mut QuicLink) -> Result<void, QuicError>;
}
```

What runs on each connection: reads its events with `link.next`, until the connection ends.

## struct QuicLink

```ceesafe
public struct QuicLink
```

A server connection's handle for its application.

### QuicLink.next

```ceesafe
public async fn next(borrow mut this, buffer: SliceMut<u8>) -> Result<QuicEvent, QuicError>
```

The connection's next event, its data (for `StreamData`, and the connection-ID events, which are handled here)
in `buffer` (`quicEventBufferSize()` bytes). It sends what the connection has first, then waits for a datagram
or the connection's next timer. `Draining` and `IdleTimeout` end the connection; return then.

### QuicLink.poll

```ceesafe
public async fn poll(borrow mut this, buffer: SliceMut<u8>) -> Result<QuicEvent?, QuicError>
```

The connection's next event if it has one now, its data in `buffer`, without sending or waiting; null when it
has none. Connection-ID events are handled here. With `waitWith`, it lets an application also wait for its own
messages (HTTP/3's request tasks).

### QuicLink.waitWith

```ceesafe
public async fn waitWith<M: Send>(
    borrow mut this,
    messages: borrow Channel<M>,
    capMs: u64,
) -> Result<M?, QuicError>
```

Sends what the connection has, then waits for a datagram (fed to the connection), the connection's next timer
(run), or a message on `messages` (returned); at most `capMs`. Then `poll` returns what the datagram or the timer
made. Both may come at once: a datagram is never dropped for a message, nor a message for a datagram.
`Draining` when the listener has gone.

### QuicLink.openStream

```ceesafe
public fn openStream(borrow mut this) -> Result<i64, QuicError>
```

Opens a bidirectional stream: its ID.

### QuicLink.openUniStream

```ceesafe
public fn openUniStream(borrow mut this) -> Result<i64, QuicError>
```

Opens a unidirectional stream: its ID.

### QuicLink.consume

```ceesafe
public fn consume(borrow mut this, stream: i64, n: u64)
```

Gives the peer back `n` bytes of credit on `stream`, when the server's `creditOnRead` is false.

### QuicLink.writeStream

```ceesafe
public fn writeStream(borrow mut this, stream: i64, bytes: Slice<u8>, fin: bool) -> Result<usize, QuicError>
```

Queues bytes on a stream (`QuicConnection.writeStream`); `next` sends them.

### QuicLink.resetStream

```ceesafe
public fn resetStream(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Resets our side of a stream.

### QuicLink.resetSending

```ceesafe
public fn resetSending(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Resets only our sending side of a stream (`QuicConnection.resetSending`).

### QuicLink.stopReading

```ceesafe
public fn stopReading(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>
```

Asks the peer to stop sending on a stream (`QuicConnection.stopReading`).

### QuicLink.alpn

```ceesafe
public fn alpn(borrow this) -> Alpn
```

The application protocol the handshake chose.

### QuicLink.peerAddress

```ceesafe
public fn peerAddress(borrow this) -> SocketAddress
```

The peer's address.

### QuicLink.handshakeDone

```ceesafe
public fn handshakeDone(borrow this) -> bool
```

Whether the handshake has completed.

### QuicLink.allAcknowledged

```ceesafe
public fn allAcknowledged(borrow this) -> bool
```

Whether the peer has acknowledged everything written on every stream (`QuicConnection.allAcknowledged`).

### QuicLink.number

```ceesafe
public fn number(borrow this) -> u32
```

The connection's number, unique among the server's connections while it lives.

### QuicLink.close

```ceesafe
public async fn close(borrow mut this, code: u64?) -> Result<void, QuicError>
```

Sends a CONNECTION_CLOSE with the application's `code`, or for the error that ended the connection.

## struct QuicServer

```ceesafe
public struct QuicServer
```

The QUIC server.

### QuicServer.serve

```ceesafe
public async fn serve(
    socket: borrow AsyncUdpSocket,
    local: SocketAddress,
    tls: Arc<QuicTls>,
    app: Arc<Box<dyn QuicApplication + Send + Share>>,
    options: QuicServerOptions,
) -> Result<void, QuicError>
```

Serves QUIC on `socket` with `tls` (a server's, ALPN `h3` or another the clients speak) and `app`, until
cancelled. `local` is the socket's address, as the connections' path names it.
