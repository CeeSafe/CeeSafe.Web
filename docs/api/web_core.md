# web_core

- [struct OwnedBuffer](#struct-ownedbuffer)
- [struct BufferPool](#struct-bufferpool)
- [struct ConnectionContext](#struct-connectioncontext)
- [struct Deadline](#struct-deadline)
- [struct MinRate](#struct-minrate)
- [fn deadlineElapsed](#fn-deadlineelapsed)
- [struct Drain](#struct-drain)
- [fn drainStarted](#fn-drainstarted)
- [enum TlsReason](#enum-tlsreason)
- [enum NetError](#enum-neterror)
- [struct NetErrors](#struct-neterrors)
- [struct PeerIdentity](#struct-peeridentity)
- [enum Alpn](#enum-alpn)
- [interface Transport](#interface-transport)
- [struct Transports](#struct-transports)

## struct OwnedBuffer

```ceesafe
@noncopy
public struct OwnedBuffer
```

A fixed-capacity byte buffer with a readable window: bytes are written into `spare()` and made readable with
`commit(n)`, then read from `readable()` and released with `consume(n)`. It never grows; a write past its capacity
is `NetError.LimitExceeded`. Lending `spare()` or `readable()` to an operation borrows the buffer, so it cannot be
changed, moved, recycled or dropped while the operation uses it (Web ADR 0001 §1).

### OwnedBuffer.create

```ceesafe
public fn create(capacity: usize) -> Result<OwnedBuffer, AllocError>
```

A buffer of `capacity` bytes, all of them spare.

### OwnedBuffer.capacity

```ceesafe
public fn capacity(borrow this) -> usize
```

The fixed number of bytes it holds.

### OwnedBuffer.length

```ceesafe
public fn length(borrow this) -> usize
```

The number of readable bytes.

### OwnedBuffer.isEmpty

```ceesafe
public fn isEmpty(borrow this) -> bool
```

Whether nothing is readable.

### OwnedBuffer.spareLength

```ceesafe
public fn spareLength(borrow this) -> usize
```

The number of bytes `spare()` offers now (without compacting).

### OwnedBuffer.readable

```ceesafe
public fn readable(borrow this) -> Slice<u8>
```

The readable bytes.

### OwnedBuffer.spare

```ceesafe
public fn spare(borrow mut this) -> SliceMut<u8>
```

The writable tail, after the readable bytes. Call `compact()` first to make room freed by `consume`.

### OwnedBuffer.commit

```ceesafe
public fn commit(borrow mut this, n: usize) -> Result<void, NetError>
```

Makes the first `n` spare bytes readable (after a read into `spare()`).

### OwnedBuffer.consume

```ceesafe
public fn consume(borrow mut this, n: usize) -> Result<void, NetError>
```

Releases the first `n` readable bytes. When nothing is left readable, the window returns to the start.

### OwnedBuffer.compact

```ceesafe
public fn compact(borrow mut this)
```

Moves the readable bytes to the start, so all free space is spare.

### OwnedBuffer.append

```ceesafe
public fn append(borrow mut this, bytes: Slice<u8>) -> Result<void, NetError>
```

Appends `bytes` to the readable bytes, compacting if that makes room.

### OwnedBuffer.truncate

```ceesafe
public fn truncate(borrow mut this, length: usize)
```

Keeps only the first `length` readable bytes, forgetting those written after them (to undo a partial write of
several appends). A `length` at or past the readable length changes nothing.

### OwnedBuffer.clear

```ceesafe
public fn clear(borrow mut this)
```

Forgets every readable byte.

## struct BufferPool

```ceesafe
public struct BufferPool
```

Recycles buffers of one size. A buffer goes back with `give`, by move: a buffer that an operation still borrows
cannot be moved, so it cannot return to the pool while in use (Web ADR 0001 §3.1). A buffer dropped instead of
given back is simply freed. A pool belongs to one task; it is not shared between tasks.

### BufferPool.create

```ceesafe
public fn create(bufferSize: usize, maxIdle: usize) -> BufferPool
```

A pool of `bufferSize`-byte buffers keeping at most `maxIdle` of them for reuse. Allocates nothing yet.

### BufferPool.bufferSize

```ceesafe
public fn bufferSize(borrow this) -> usize
```

The size of the buffers it hands out.

### BufferPool.idle

```ceesafe
public fn idle(borrow this) -> usize
```

How many buffers wait for reuse.

### BufferPool.take

```ceesafe
public fn take(borrow mut this) -> Result<OwnedBuffer, AllocError>
```

An empty buffer: a recycled one if any, else a new one.

### BufferPool.give

```ceesafe
public fn give(borrow mut this, buffer: OwnedBuffer)
```

Takes a buffer back for reuse, emptied. One of another size, or beyond `maxIdle`, is freed instead.

## struct ConnectionContext

```ceesafe
public struct ConnectionContext<region r>
```

A connection's identity, addresses, timing and the server's drain, owned by its connection task and lent to
handlers.

### ConnectionContext.create

```ceesafe
public fn create(id: u64, acceptedAt: u64, drain: borrow(r) Drain) -> ConnectionContext<r>
```

The context of connection `id`, accepted at `acceptedAt` on `Timer.now()`'s clock, served under `drain`.

### ConnectionContext.acceptedNow

```ceesafe
public fn acceptedNow(id: u64, drain: borrow(r) Drain) -> ConnectionContext<r>
```

The context of connection `id`, accepted now.

### ConnectionContext.withAddresses

```ceesafe
public fn withAddresses(this, peer: SocketAddress?, local: SocketAddress?) -> ConnectionContext<r>
```

The same context with the connection's addresses (`Transport.peerAddress`, `localAddress`).

### ConnectionContext.withPeerIdentity

```ceesafe
public fn withPeerIdentity(this, identity: PeerIdentity) -> ConnectionContext<r>
```

The same context with the peer's verified identity (`Transport.peerIdentity`).

### ConnectionContext.peerIdentity

```ceesafe
public fn peerIdentity(borrow this) -> borrow PeerIdentity
```

The peer's verified identity: a TLS client certificate under client authentication; empty otherwise.

### ConnectionContext.peerAddress

```ceesafe
public fn peerAddress(borrow this) -> SocketAddress?
```

The peer's address; null if unknown (a mock).

### ConnectionContext.localAddress

```ceesafe
public fn localAddress(borrow this) -> SocketAddress?
```

The address the connection arrived at; null if unknown.

### ConnectionContext.id

```ceesafe
public fn id(borrow this) -> u64
```

The connection's id, unique within one server.

### ConnectionContext.acceptedAtMs

```ceesafe
public fn acceptedAtMs(borrow this) -> u64
```

When it was accepted, on `Timer.now()`'s clock.

### ConnectionContext.ageMs

```ceesafe
public fn ageMs(borrow this) -> u64
```

Milliseconds since it was accepted.

### ConnectionContext.drain

```ceesafe
public fn drain(borrow this) -> borrow(r) Drain
```

The server's drain: a connection between requests races its idle wait against it (`drainStarted`).

### ConnectionContext.isDraining

```ceesafe
public fn isDraining(borrow this) -> bool
```

Whether the server is draining: finish the request in flight, then close.

## struct Deadline

```ceesafe
public struct Deadline
```

A point on `Timer.now()`'s clock (milliseconds), or never. Copyable; compare with `remaining` or `isExpired`.

### Deadline.never

```ceesafe
public fn never() -> Deadline
```

A deadline that never passes.

### Deadline.after

```ceesafe
public fn after(ms: u64) -> Deadline
```

`ms` milliseconds from now (saturating: a huge `ms` is never).

### Deadline.atInstant

```ceesafe
public fn atInstant(instant: u64) -> Deadline
```

The deadline at `instant` on `Timer.now()`'s clock.

### Deadline.isNever

```ceesafe
public fn isNever(this) -> bool
```

Whether it never passes.

### Deadline.instant

```ceesafe
public fn instant(this) -> u64
```

Its time on `Timer.now()`'s clock; the largest u64 for never.

### Deadline.remaining

```ceesafe
public fn remaining(this) -> u64
```

Milliseconds left: 0 once it has passed; the largest u64 for never.

### Deadline.isExpired

```ceesafe
public fn isExpired(this) -> bool
```

Whether it has passed.

### Deadline.earlier

```ceesafe
public fn earlier(this, other: Deadline) -> Deadline
```

The earlier of the two.

## struct MinRate

```ceesafe
public struct MinRate
```

A minimum receive rate, the guard against slow senders (slowloris; implementation plan M1 task 3). After a grace
period, the bytes received must keep up with `bytesPerSecond` on average since the start. Sans-I/O: the driver
records what it reads and bounds each read by `deadline()`.

### MinRate.create

```ceesafe
public fn create(bytesPerSecond: u64, graceMs: u64, startedAt: u64) -> MinRate
```

A guard from `startedAt` (on `Timer.now()`'s clock). A rate of 0 turns it off.

### MinRate.startingNow

```ceesafe
public fn startingNow(bytesPerSecond: u64, graceMs: u64) -> MinRate
```

A guard from now.

### MinRate.off

```ceesafe
public fn off() -> MinRate
```

A guard that never expires.

### MinRate.record

```ceesafe
public fn record(borrow mut this, n: usize)
```

Records `n` bytes received.

### MinRate.receivedBytes

```ceesafe
public fn receivedBytes(borrow this) -> u64
```

The bytes received so far.

### MinRate.deadline

```ceesafe
public fn deadline(borrow this) -> Deadline
```

When the next byte is due: the start, plus the grace, plus the time the bytes so far buy at the rate. Never if
the guard is off (or the sum overflows).

## fn deadlineElapsed

```ceesafe
public async fn deadlineElapsed<T>(deadline: Deadline) -> Result<T, NetError>
```

Waits until `deadline` passes, then returns `Err(NetError.TimedOut)`; a cancelled task returns
`Err(NetError.Cancelled)`. It never returns `Ok`. To bound one operation, use CeeSafe's `Async.deadline` (ADR 0046),
as `Transports.readWithin` does; this is for a race with more than the operation and the deadline in it
(Web ADR 0001 §3.5):

```ceesafe
let r: Result<Raced<usize>, NetError> =
    await Async.race(async () => await t.read(buffer), async () => await deadlineElapsed<usize>(d));
```

## struct Drain

```ceesafe
@noncopy
public struct Drain
```

A server's drain: once started, every task waiting on it wakes, and every later check sees it. One drain serves any
number of waiters (unlike CeeSafe's `AsyncSignal`, which wakes one task): it is a channel that is never sent to
and is closed to start the drain, so each waiter's `recvAsync` sees the end (`tests/probes/drain-broadcast.cee`).
It is shared by borrow, or, for a task that may hold no loan (an HTTP/2 stream's), by `share`; starting it is
idempotent and has no effects, so a signal handler's task may call it.

### Drain.create

```ceesafe
public fn create() -> Result<Drain, NetError>
```

A drain not started. Allocates the channel and the flag once.

### Drain.share

```ceesafe
public fn share(borrow this) -> Drain
```

Another handle to the same drain: started when it is, waking with it.

### Drain.start

```ceesafe
public fn start(borrow this)
```

Starts the drain: wakes every waiter. Calling it again does nothing more.

### Drain.isDraining

```ceesafe
public fn isDraining(borrow this) -> bool
```

Whether the drain has started.

### Drain.wait

```ceesafe
public async fn wait(borrow this) -> Result<void, NetError>
```

Waits until the drain starts (at once if it has). `Err(Cancelled)` if the task is cancelled first.

## fn drainStarted

```ceesafe
public async fn drainStarted<T>(drain: borrow Drain) -> Result<T, NetError>
```

Waits until `drain` starts, then returns `Err(NetError.Cancelled)`; it never returns `Ok`. So it is the second
child of an `Async.race` that stops an operation when a drain starts, as `deadlineElapsed` does for a deadline;
the caller tells the two apart with `drain.isDraining()`:

```ceesafe
let r: Result<Raced<PlainStream>, NetError> =
    await Async.race(async () => await listener.accept(), async () => await drainStarted<PlainStream>(drain));
```

## enum TlsReason

```ceesafe
public enum TlsReason {
    Handshake,
    Certificate,
    Protocol,
    Alert,
    Internal,
}
```

Why a TLS operation failed. Set by web_tls (Web ADR 0002); the codes behind it are logged there.

## enum NetError

```ceesafe
public enum NetError {
    Cancelled,
    TimedOut,
    Closed,
    Reset,
    LimitExceeded,
    OutOfMemory,
    Tls(TlsReason),
    Io(IoError),
}
```

Why a network operation failed (Web ADR 0001 §3.2).

## struct NetErrors

```ceesafe
public struct NetErrors
```

Conversions to NetError and its descriptions. (An enum has no functions of its own, type-system.md §15.1.)

### NetErrors.fromAsync

```ceesafe
public fn fromAsync(e: AsyncError) -> NetError
```

The NetError for an async operation's failure. `AsyncError.Closed` (CeeSafe ADR 0128 §2.1, ceesafec 0.43.0):
the other side, or this endpoint, was closed, so the operation can never succeed; that is `Closed`.

### NetErrors.fromIo

```ceesafe
public fn fromIo(e: IoError) -> NetError
```

The NetError for a platform I/O error (CeeSafe ADR 0044 §3.4). A broken pipe means the peer is gone, so the
stream is closed; a reset or an abort is `Reset`; the kernel giving up (a connect, a keep-alive probe) is
`TimedOut`. Every other kind stays `Io`.

### NetErrors.fromAlloc

```ceesafe
public fn fromAlloc(e: AllocError) -> NetError
```

The NetError for a failed allocation.

### NetErrors.describe

```ceesafe
public fn describe(e: borrow NetError) -> StringView
```

A short static description, for logs.

## struct PeerIdentity

```ceesafe
@noncopy
public struct PeerIdentity
```

A verified certificate: its DER bytes, its subject (RFC 2253: "CN=test client,O=Example") and its subject
alternative names ("DNS:a.example, IP Address:127.0.0.1, email:x@example"). Empty when the peer presented none.

### PeerIdentity.empty

```ceesafe
public fn empty() -> PeerIdentity
```

No identity.

### PeerIdentity.copy

```ceesafe
public fn copy(borrow this) -> Result<PeerIdentity, AllocError>
```

A copy, for a task that cannot borrow the connection's (an HTTP/2 stream's).

### PeerIdentity.isEmpty

```ceesafe
public fn isEmpty(borrow this) -> bool
```

Whether there is none.

### PeerIdentity.certificate

```ceesafe
public fn certificate(borrow this) -> Slice<u8>
```

The certificate, DER.

### PeerIdentity.subject

```ceesafe
public fn subject(borrow this) -> Slice<u8>
```

The subject, RFC 2253.

### PeerIdentity.names

```ceesafe
public fn names(borrow this) -> Slice<u8>
```

The subject alternative names, comma-separated as OpenSSL prints them.

### PeerIdentity.certificateBuffer

```ceesafe
public fn certificateBuffer(borrow mut this) -> borrow mut Vector<u8>
```

Where a transport writes the certificate (cleared first).

### PeerIdentity.subjectBuffer

```ceesafe
public fn subjectBuffer(borrow mut this) -> borrow mut Vector<u8>
```

Where a transport writes the subject (cleared first).

### PeerIdentity.namesBuffer

```ceesafe
public fn namesBuffer(borrow mut this) -> borrow mut Vector<u8>
```

Where a transport writes the names (cleared first).

## enum Alpn

```ceesafe
public enum Alpn {
    None,
    Http11,
    Http2,
    Other,
    Http3,
    HqInterop,
}
```

The application protocol agreed for a connection (TLS ALPN, Web ADR 0002 §3.3).

## interface Transport

```ceesafe
public interface Transport {
    async fn read(borrow mut this, buffer: SliceMut<u8>) -> Result<usize, NetError>;
    async fn write(borrow mut this, bytes: Slice<u8>) -> Result<void, NetError>;
    async fn writeVectored(borrow mut this, parts: borrow IoParts) -> Result<usize, NetError>;
    async fn shutdownWrite(borrow mut this) -> Result<void, NetError>;
    async fn sendFile(borrow mut this, file: borrow mut File, offset: u64, length: u64) -> Result<void, NetError>;
    @effects()
    fn peerAddress(borrow this) -> SocketAddress?;
    @effects()
    fn localAddress(borrow this) -> SocketAddress?;
    @effects()
    fn alpn(borrow this) -> Alpn;
    @effects(alloc)
    fn peerIdentity(borrow this, into: borrow mut PeerIdentity) -> bool;
}
```

An async byte stream with explicit close semantics (Web ADR 0001 §3.2). The transport owns its socket: dropping it
closes at once. Implementations are concrete types (TCP, TLS, mock); drivers use them as `borrow mut dyn
Transport`, and a connection holds one as `Box<dyn Transport>` (§3.3).

## struct Transports

```ceesafe
public struct Transports
```

Helpers over any transport.

### Transports.filePiece

```ceesafe
public fn filePiece() -> usize
```

The most `copyFile` reads at once. Each read is a round trip to a worker thread (CeeSafe ADR 0075 §2.1), so
the piece is large; the transport splits it into records or frames.

### Transports.fileBuffer

```ceesafe
@effects(alloc)
public fn fileBuffer(length: u64) -> Result<Vector<u8>, NetError>
```

A zeroed buffer for `copyFile`'s pieces: `filePiece()` bytes, or `length` if less.

### Transports.copyFile

```ceesafe
public async fn copyFile(
    t: borrow mut dyn Transport,
    file: borrow mut File,
    offset: u64,
    length: u64,
) -> Result<void, NetError>
```

Sends `length` bytes of `file` from `offset` through `t.write`, in pieces of at most `filePiece()` bytes read with
`AsyncFiles.readAt` on a worker thread (CeeSafe ADR 0075 §2.1): `sendFile` for a transport that cannot hand a
file to the kernel. A file that ends first is `Err(Io(InvalidInput))`, as lib/std's `sendFileAll` reports it.

### Transports.writeAllVectored

```ceesafe
public async fn writeAllVectored(t: borrow mut dyn Transport, parts: borrow IoParts) -> Result<void, NetError>
```

Writes every byte of `parts` (lib/std's `IoParts`, ADR 0045 §3.1), looping over `writeVectored`'s partial
steps: each step sends what is left, `parts.after(sent)`.

### Transports.writeAllVectoredWithin

```ceesafe
public async fn writeAllVectoredWithin(
    t: borrow mut dyn Transport,
    parts: borrow IoParts,
    deadline: Deadline,
) -> Result<void, NetError>
```

Writes every byte of `parts` as `writeAllVectored`, bounded by `deadline`: `Err(TimedOut)` once it passes,
and the transport is then poisoned (§3.4).

### Transports.readWithin

```ceesafe
public async fn readWithin(
    t: borrow mut dyn Transport,
    buffer: SliceMut<u8>,
    deadline: Deadline,
) -> Result<usize, NetError>
```

Reads as `read`, bounded by `deadline` (CeeSafe's `Async.deadline`, ADR 0046): `Err(TimedOut)` once it passes.
A read cancelled by the deadline after the kernel completed it is not lost: its bytes come with the next read
(Web ADR 0001 §3.4).

### Transports.writeWithin

```ceesafe
public async fn writeWithin(
    t: borrow mut dyn Transport,
    bytes: Slice<u8>,
    deadline: Deadline,
) -> Result<void, NetError>
```

Writes as `write`, bounded by `deadline`: `Err(TimedOut)` once it passes. A write stopped by the deadline may
have sent part of the bytes, so the transport is poisoned (Web ADR 0001 §3.4): the write timeout of M1 ends
the connection.

### Transports.sendFileWithin

```ceesafe
public async fn sendFileWithin(
    t: borrow mut dyn Transport,
    file: borrow mut File,
    offset: u64,
    length: u64,
    deadline: Deadline,
) -> Result<void, NetError>
```

Sends `length` bytes of `file` from `offset` as `sendFile`, bounded by `deadline`: `Err(TimedOut)` once it passes,
and the transport is then poisoned (§3.4). A caller sending a large file gives each slice its own deadline.

### Transports.closeGracefully

```ceesafe
public async fn closeGracefully(
    t: borrow mut dyn Transport,
    scratch: SliceMut<u8>,
    deadline: Deadline,
) -> Result<void, NetError>
```

Closes gracefully: ends writing, then reads (into `scratch`, discarding) until the peer ends its side or
`deadline` passes. Dropping the transport afterwards closes the socket. `Err(TimedOut)` if the peer did not
finish in time.
