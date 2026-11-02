# Web ADR 0001 — Transport ownership: buffers, sockets, cancellation and async operations

- Status: **Accepted** by the owner, 2026-11-02 (every question of §6 as recommended). §3.5 (no token type) and §3.1
  (no lease types) carry the owner's earlier decision of 2026-11-02 (implementation plan §11.4).
- Scope: who owns bytes, sockets and in-flight operations in CeeSafe.Web; the `Transport` contract every protocol
  engine's driver uses; partial I/O, cancellation and disposal. Milestones M0–M1, and every later one.

## 1. Context

The roadmap asked for `OwnedBuffer`, `BufferSlice`, `ReadLease`, `WriteLease`, `AsyncRead`, `AsyncWrite`,
`CancellationToken` and `Deadline`, with the rule "no buffer may be recycled while an outstanding async operation
references it". CeeSafe already guarantees most of that:

- An async fn's frame never moves and is freed only after its task returns (CeeSafe ADR 0031 §3; `guide/async.md`,
  "How it runs"). A borrow held across `await` is sound.
- A socket operation's record lives in the awaiting frame. A cancelled operation that the kernel still holds is
  cancelled in the kernel and waited for before the task leaves, so the buffer outlives the kernel's use (CeeSafe
  ADR 0035 §3.3). A read that completed before the cancellation keeps its bytes (§3.4).
- A buffer lent to an operation is a `SliceMut<u8>` borrow. While it lives, the owner cannot be written, moved or
  dropped (borrow checker; `tests/probes/request-lifetime-fail.cee` shows N0240).
- Cancellation is structured: a cancelled task's next wait returns `Err(Cancelled)`; scopes cancel siblings on the
  first error; `Async.shield(ms, body)` protects cleanup (`guide/async.md`, "Cancellation").

Constraints found while drafting (2026-11-02, `tests/probes/`):

- **`impl` only for concrete, unscoped types** (`spec/type-system.md:1133`; `impl-generic-fail.cee`,
  `impl-scoped-fail.cee`). A generic wrapper such as `TlsTransport<T: Transport>` cannot implement `Transport`.
- `dyn` interfaces may have async methods (CeeSafe ADR 0036), and the compiler reserves room for the largest
  implementation (`transport-dyn.cee`).
- An async call through `dyn` that may reach itself (a wrapper of `dyn Transport` that is itself a `dyn Transport`)
  is recursion through `await`: N0343, fixable only with `Async.boxed` (an allocation per call).
- New I/O primitives can only be added in CeeSafe (`Async.park` is library-only, N0345).

## 2. Options

| | A. Use the language's guarantees (proposed) | B. Runtime leases and tokens, as in the roadmap |
|---|---|---|
| Borrowed bytes | `Slice<u8>`, `SliceMut<u8>`, `StringView<r>` | `BufferSlice`, `ReadLease`, `WriteLease` |
| Recycling safety | compile time (borrows + frames that never move) | run-time checks on each lease |
| Cancellation | task cancellation, `race`, `shield`, `AsyncSignal` | `CancellationToken` checked at each step |
| Composition | `Box<dyn Transport>` at the connection edge, concrete types below it | generic wrappers, which v0 cannot implement interfaces for |

**A.** B adds run-time cost to prove what the compiler proves. A token would also bypass ADR 0035's rule: a token
that fires while the kernel holds a buffer cannot wait for the kernel the way task cancellation does.

## 3. Decision

### 3.1 Bytes

- **Borrowed bytes** are `Slice<u8>`, `SliceMut<u8>` and `StringView<r>`. There are no lease types.
- **`OwnedBuffer`** (`web_core`, `@noncopy`): a fixed-capacity byte buffer from an allocator. It has a length, a
  capacity, `spare()` (a `SliceMut` of the free tail) and `commit(n)`. Allocation failure is a value
  (`Result<_, AllocError>`). Drop frees.
- **`PooledBuffer`**: the same interface over a slot of a `BufferPool` (a CeeSafe `Pool` of fixed-size blocks). Drop
  returns the slot. Because a slot can only be dropped when nothing borrows it, a pooled buffer cannot return to the
  pool while an operation still uses it.
- Buffers do not grow implicitly. Code that needs more than a limit reports it (`NetError.LimitExceeded`).

### 3.2 The `Transport` interface (`web_core`)

```ceesafe
public interface Transport {
    /// Reads at least 1 byte into `buffer`, or 0 at the end of the stream. `buffer` must not be empty.
    async fn read(borrow mut this, buffer: SliceMut<u8>) -> Result<usize, NetError>;
    /// Writes every byte, or fails. After a failure the transport is poisoned (§3.4).
    async fn write(borrow mut this, bytes: Slice<u8>) -> Result<void, NetError>;
    /// One vectored step: the number of bytes written (≥ 1), possibly fewer than given. Until CeeSafe's U3, TCP
    /// implements it as one `write` of the first part.
    async fn writeVectored(borrow mut this, parts: Slice<Slice<u8>>) -> Result<usize, NetError>;
    /// Ends this side's writing (TCP FIN, TLS close_notify + FIN).
    async fn shutdownWrite(borrow mut this) -> Result<void, NetError>;
    /// The application protocol agreed for this connection (ALPN), or Alpn.None.
    fn alpn(borrow this) -> Alpn;
    /// Peer and local addresses, for logs and ConnectionContext.
    fn peer(borrow this) -> PeerInfo;
}
```

The exact signatures are settled in the M0 implementation. `NetError` is `web_core`'s enum: `Cancelled`, `TimedOut`,
`Closed` (the stream ended, or the transport is poisoned), `Reset`, `LimitExceeded`, `OutOfMemory`, `Tls(TlsReason)`
and `Io(IoError)`. Reasons are enums, never strings (CeeSafe: a `StringView` in an enum needs a region). The
conversion from `AsyncError` is one function in `web_core`.

### 3.3 Implementations and composition

Interfaces can be implemented only by concrete, unscoped types (§1), so:

- `web_transport`: `TcpTransport` (over `lib/std`'s `AsyncTcpStream`), `MockTransport` (scripted, §3.7), and
  `PlainStream`, a concrete struct over the enum `Plain { Tcp(TcpTransport), Mock(MockTransport) }`, dispatched by
  `match`. All three implement `Transport`.
- `web_tls`: `TlsTransport` holds a `PlainStream` (concrete, so no recursion through `dyn`) and implements
  `Transport`.
- **The connection edge is `Box<dyn Transport>`.** The listener produces a `PlainStream`. An `Acceptor` interface
  (`web_transport`) turns it into a `Box<dyn Transport>`: the plain acceptor boxes it, and `web_tls`'s acceptor
  completes a handshake and boxes a `TlsTransport`. Above the edge, drivers and bodies use
  `borrow mut dyn Transport`. That costs one indirect call per I/O operation and lets every layer except `web_tls`
  build without OpenSSL (Web ADR 0004 §3). Shown working in `transport-dyn.cee` (prints `1007`, interpreter and
  native).
- Protocol engines never see a transport (Web ADR 0004); only their drivers do.
- If CeeSafe gains generic impls (upstream U10), `TlsTransport<T: Transport>` can replace the concrete form. Nothing
  above the edge changes.

### 3.4 Partial I/O and failure

- `read` returns 1..`buffer.length` bytes, or 0 for the end of the stream. An empty buffer is a caller error and
  returns `Err(LimitExceeded)` without I/O, so 0 is never ambiguous.
- A `read` cancelled after the kernel completed it returns the bytes (`Ok(n)`); the next call sees the cancellation
  (CeeSafe ADR 0035 §3.4). A cancelled read therefore never loses data, and the transport stays usable.
- `write` writes everything or fails. A failed or cancelled `write` may have sent part of the bytes, so the
  transport is **poisoned**: every later `write`, `writeVectored` and `shutdownWrite` returns `Err(Closed)`, and the
  connection must be closed. Reads still work, so a peer's final bytes can be drained.
- `writeVectored` reports the bytes written. The caller advances through its parts; a helper in `web_core` loops.

### 3.5 Cancellation and deadlines

- **No token type.** Work is cancelled by cancelling its task: a scope's sibling fails, a `race` is lost, or an
  owner's scope is cancelled.
- **From outside the task tree** (server shutdown, a client request its caller abandoned): race the work against an
  `AsyncSignal`, `Async.race(body, async () => { await signal.wait()?; return Err(NetError.Cancelled); })`. The body
  loses and is cancelled and awaited properly. If this proves clumsy, the fix is an upstream ADR for a scope handle,
  not a token.
- **`Deadline`** (`web_core`): an absolute time on `Timer.now()` (ms), copyable, with `remaining()`.
  `withDeadline(deadline, timedOut, body)` races the body against a sleep until the deadline. Once CeeSafe has U4,
  it becomes a call to `Async.timeout`.
- **Cleanup that must finish** (TLS close_notify, an HTTP/2 GOAWAY) runs in `Async.shield(ms, …)`, bounded in time.

### 3.6 Sockets and disposal

- A `Transport` owns its descriptor. Drop closes it, immediately and without blocking.
- A graceful close is explicit: `shutdownWrite`, then read until 0 or a deadline (bounded), then drop. A helper in
  `web_core` does it. Abortive close (RST) needs `SO_LINGER` 0 (upstream U1).
- A `ConnectionContext` (`web_core`) carries the connection's id, peer, start time and a `draining` signal. It is
  owned by the connection task and lent to handlers.

### 3.7 MockTransport

A scripted transport for tests. It delivers its input in fragments: every split point for short inputs, seeded
random splits for long ones. It can inject `Reset`, the end of the stream, and short `writeVectored` steps at given
offsets, and it records what was written. It never suspends unless asked to (so it can also test cancellation at a
suspension point). It runs in the interpreter, so engine and driver tests are deterministic.

## 4. Consequences

- `web_core` defines `Transport`, `NetError`, `OwnedBuffer`, `PooledBuffer`, `BufferPool`, `Deadline`,
  `withDeadline`, `ConnectionContext`, `Alpn` and `PeerInfo`. `web_transport` defines `TcpTransport`,
  `MockTransport`, `PlainStream`, `Acceptor` and the plain acceptor.
- There is one indirect call per I/O operation at the edge. It is measured in M1 against a statically dispatched
  variant (`--explain-frames` and a microbenchmark) and accepted if under 1 % of a request's cost.
- The roadmap's `BufferSlice`, `ReadLease`, `WriteLease`, `AsyncRead`/`AsyncWrite` (merged into `Transport`) and
  `CancellationToken` are not built.

## 5. Tests (M0 exit)

- Fragmentation: every engine and driver test runs over `MockTransport` at every split.
- Cancellation: cancel during `read`, `write` and `writeVectored`, with the operation in flight and with it idle.
  Then: no loss on read, poisoning on write, the frame freed, `live=0`, under ASan.
- Lifetime: `request-lifetime-fail.cee`-style check-fail probes for buffer reuse during an operation.
- CeeSafe's cancel sweep and allocation-failure sweep over the transport tests (upstream T1 if they need a package
  mode).

## 6. Questions for the owner (answered 2026-11-02: each as recommended)

1. Is the `dyn Transport` edge acceptable (§3.3), given its cost of one indirect call per I/O operation, in exchange
   for TLS-free layers and no generic impls? Recommended: yes. **Yes.**
2. Should the poisoning rule (§3.4) close the connection automatically, or only report `Closed`? Recommended:
   report only, and let the driver decide (an HTTP/1.1 driver closes; a proxy may log first). **Report only.**

## 7. Amendment 1 (2026-11-02, implementing `web_core`)

Found while implementing against the ceesafec 0.6.0 release. The decision's intent is unchanged; these are its
v0-expressible forms:

1. **`PooledBuffer` is `BufferPool` + `OwnedBuffer`, recycled by move** (§3.1). A CeeSafe `PoolBox` is scoped (it
   carries a loan of its pool, so it cannot be stored in a field; `spec/type-system.md` §19.6), and a custom allocator
   must be a struct without fields. Instead:
   - `BufferPool.take()` hands out an `OwnedBuffer`, and `give(buffer)` takes it back by move;
   - a buffer still borrowed by an operation cannot be moved, so it cannot return to the pool while in use. That is the
     same guarantee, enforced by the borrow checker;
   - a buffer dropped instead of given back is freed;
   - a pool belongs to one task.
2. **`writeVectored` takes `borrow IoParts`**, a struct of up to four slices (§3.2). There are no slices of slices in
   v0 (N0246, `tests/probes/slice-of-slices-fail.cee`). Four parts cover an HTTP/1.1 head and body, and a chunk's
   size line, data and CRLF.
3. **`Transport.alpn` is declared `@effects()`** (§3.2). An undeclared sync method called through `dyn` counts as
   possibly blocking, so async code could not call it (N0295).
4. **`withDeadline(d, body)` is `deadlineElapsed<T>(d)`** (§3.5), a task that never returns `Ok`, used as the second
   child of `Async.race`. A generic wrapper over a closure is not expressible today: see CeeSafe bug B1 and upstream
   U4 (`docs/ceesafe-upstream-work.md`).
5. **`Transport.peer()` and `PeerInfo` move to M1.** They need CeeSafe's `SocketAddress` (upstream U1), which is on
   CeeSafe main but not in the 0.6.0 release. For the same reason, `NetErrors.fromIo` cannot yet map
   `ConnectionReset` to `Reset` (U2).
6. Methods cannot have type parameters of their own (N0216), so generic helpers are top-level functions
   (`deadlineElapsed<T>`). Type arguments on a method call (`d.expire<u32>()`) do not parse.

## 8. Amendment 2 (2026-11-02, implementing M1's lifecycle)

Found while building M1's connection server against the 0.6.0 release. The decision's intent is unchanged:

1. **`Acceptor.accept` and `Connector.connect` take a `deadline`** (§3.3). The acceptor bounds its handshake's own
   I/O (`Transports.readWithin`/`writeWithin`), because a server cannot race it: a race child cannot move a captured
   value (N0251), and the acceptor takes the stream by value.
2. **The drain is `web_core`'s `Drain`, a broadcast** (§3.5, §3.6). CeeSafe's `AsyncSignal` wakes one waiting task,
   and a drain must wake every worker. `Drain` is a `Channel<u8>` that is never sent to and is closed to start the
   drain, so every waiter's `recvAsync` ends (`tests/probes/drain-broadcast.cee`), plus an `AtomicBool` for cheap
   checks. `drainStarted<T>(drain)` is the race child, as `deadlineElapsed<T>` is for deadlines. The race against
   an `AsyncSignal` of §3.5 is still right for one task.
3. **`ConnectionContext` has a region parameter and lends the server's drain** (`drain()`, `isDraining()`). It is
   lent to handlers, never stored, so the v0 rule that scoped types cannot implement interfaces does not matter.
4. **Deadline-bounded I/O is `Transports.readWithin` and `writeWithin`** (§3.5, amendment 1.4): a generic
   `withDeadline` still waits for U4 (CeeSafe's ADR 0046 draft) or a fix for B1. A write stopped by its deadline
   poisons the transport, as §3.4 requires.
5. **`MinRate`** (`web_core`) computes the slow-sender deadline, sans-I/O: start, plus grace, plus the time the bytes
   received so far buy at the minimum rate.

## 9. Amendment 3 (2026-11-02, moving to ceesafec 0.7.0)

0.7.0 ships CeeSafe ADRs 0044 (addresses, IPv6, socket options, network `IoError` kinds), 0045 (vectored send) and
0046 (`Async.timeout`, `Async.deadline`). The decision's intent is unchanged:

1. **`IoParts` is lib/std's** (CeeSafe ADR 0045 §3.1), which took this ADR's shape: up to eight parts, built by
   value (`IoParts.of(head).then(body)`, null past eight), with `count`, `part`, `totalLength` and `after`.
   `web_core`'s own four-slot `IoParts` is gone, since the two names collided. `Transports.writeAllVectored` loops
   on `writeVectored` over `parts.after(sent)`, and `TcpTransport.writeVectored` is one real vectored send
   (`AsyncTcpStream.writeVectored`) instead of the first part's write.
2. **`Transport` gains `peerAddress()` and `localAddress()`** (`SocketAddress?`, `@effects()`, as `alpn`), which
   replace amendment 1.5's `peer()`/`PeerInfo`. `TcpTransport` reads both once, when the connection is made: null if
   the kernel no longer knows them. The mock has none. `ConnectionContext.withAddresses` carries them to handlers.
3. **`NetErrors.fromIo`** maps `ConnectionReset` and `ConnectionAborted` to `Reset`, and `IoError.TimedOut` (the
   kernel's) to `TimedOut` (amendment 1.5).
4. **`Transports.readWithin` is `Async.deadline`** (§3.5, amendment 2.4). `writeWithin` and `closeGracefully` keep
   their race against `deadlineElapsed` while CeeSafe bug B6 is open: `Async.deadline` over a body of
   `Result<void, E>` crashes `ceesafec check` (`tests/probes/async-deadline-void-bug.cee`). `deadlineElapsed<T>`
   stays for races with more than an operation and a deadline. The frame is about the same: `Async.deadline` is
   lowered as a race (CeeSafe ADR 0046 §3), and `readWithin` went from 1296 to 1168 bytes. **Update (0.8.0):** B6
   is fixed, and `writeWithin` and `closeGracefully` use `Async.deadline` too.
5. **TCP** (§3.3): `TcpTransport.connectTo(SocketAddress)`, `setNoDelay`, and `abort()`, an abortive close (SO_LINGER
   0, so the peer gets a reset), for a connection that broke the protocol and for tests of a peer's reset.
   `Listener.bindWith(SocketAddress, ListenOptions)` sets the backlog, address and port reuse, and dual stack;
   `Listener.localAddress()`. Accepted connections have TCP_NODELAY on unless `withNoDelay(false)`.

## 10. Amendment 4 (2026-11-02, M4: the peer's identity)

1. **`Transport.peerIdentity(into: borrow mut PeerIdentity) -> bool`**, declared `@effects(alloc)`, copies the peer's
   verified identity out of the transport. For TLS that is the client's certificate under client authentication (or
   the server's, on a client). Plain transports return false. CeeSafe interfaces have no default bodies, so every
   `Transport` implements it.
2. **`PeerIdentity`** (web_core) owns its bytes: the certificate (DER), the subject (RFC 2253) and the subject
   alternative names, as OpenSSL prints them.
3. **`ConnectionContext.peerIdentity()`**: the connection server copies the identity into the context, next to the
   addresses, before it lends the transport to the handler. A copy, not a borrow, because the handler then borrows
   the transport mutably. `withPeerIdentity` makes a context with one (the owner's choice, 2026-11-02: through to
   handlers, so `web_server` handlers read `ctx.connection().peerIdentity()`).
