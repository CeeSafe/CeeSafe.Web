# Upstream work in CeeSafe for CeeSafe.Web (U1–U9 and tooling)

**For:** a separate session working in `~/dev/CeeSafe` (main at `5fa4316`, ceesafec 0.6.0-dev).
**From:** `~/dev/CeeSafe.Web/docs/implementation-plan.md` §5 (accepted 2026-11-02).
**Status:** see "Status at 0.9.2", "Status at 0.9.1", "Status at 0.9.0", "Status at 0.8.0" and "Status at 0.7.0" below. File references in the item sections were checked against `5fa4316`;
re-check them before editing.

## Status at 0.9.2 (released 2026-11-02, tag `v0.9.2` = CeeSafe `47474db`)

| Item | Status | Checked by |
|---|---|---|
| **B9** a generic struct's method cannot pass `this` as `dyn I` under a generic impl | **fixed** (`34bb24f`) | `tests/probes/generic-impl-dyn-in-generic-bug.cee` checks, prints 7 three times, interpreted and native |
| **B10** async interface boxed as `dyn`, never called through it: C does not compile | **fixed** (`b147ae9`) | `tests/probes/async-dyn-uncalled-bug.cee` builds and prints 1 |
| **B11** call through a returned `borrow Box<dyn I>` panics the compiler | **fixed** (`b0a9886`) | `tests/probes/borrowed-box-dyn-call-bug.cee` prints 7, interpreted and native |
| **B12** generic impl passing `this` as `dyn` emits the uninstantiated type | **fixed** (`7be63c6`) | `tests/probes/generic-impl-this-dyn-bug.cee` builds and prints 7 |

M2's task 5 (`web_server`) is unblocked: `Server<S>`, its tests and `examples/server` build and pass on 0.9.2.
Opened since, while building M3's `web_sse`: **U13** (`Channel.tryRecv`, wanted, blocks nothing) and **B13** (an
`Async.scope` with a droppable error type does not build; tests avoid it). While building M4's `web_tls`: **U14**
(C allocations outside the leak check, for OpenSSL in tests) and **T3** (four warnings in Windows builds). While
building M5's `web_client`: **B14** (`dyn I + Send` as a type argument in an expression does not parse) and **B15**
(async calls through `dyn I` and `dyn I + Send` collide in C; **blocks the client natively**).

## Status at 0.9.1 (released 2026-11-02, tag `v0.9.1` = CeeSafe `74226b0`)

| Item | Status | Checked by |
|---|---|---|
| **B8** `Box<dyn I<S>>` parameter not storable when `S` is a type parameter | **fixed** (`1a124ec`: a `dyn` type's interface arguments bring no loans) | `tests/probes/generic-dyn-box-flow-bug.cee` prints 2 |
| **U12** wall-clock time (ADR 0055: `Clock.now() -> UnixTime`) | **released** | every response carries `Date` (Web ADR 0003 amendment 4); interop checks it against Python's clock |

Found since, while finishing `web_server` on 0.9.1: **B9**, **B10**, **B11**, **B12** (below). Together they block M2's
task 5 again (`Server<S>`, its router tests).

## Status at 0.9.0 (released 2026-11-02, tag `v0.9.0` = CeeSafe `b361e63`)

Checked from CeeSafe.Web on 2026-11-02, macOS arm64: every package checks and is formatted; `python3
tools/run_tests.py --sanitize --alloc-sweep --cancel-sweep` passes all 16 runs with no change to CeeSafe.Web.

| Item | Status | Checked by |
|---|---|---|
| U6 UDP (ADR 0050) | **released** | not used yet (M10) |
| U7 sending files (ADR 0051) | **released** | not used yet (M9) |
| U9 `HashMap` (ADR 0052) | **released** | not used yet |
| U10 generic impls, impls for types with region parameters (ADR 0054) | **released** | probe: `impl<S> App for Server<S>` with an async method, called through `borrow dyn App`, prints 42 in the interpreter and natively. `web_server` provides `impl<S> Application for Server<S>` itself, so applications no longer write the two delegating impls |
| U11 task sets (ADR 0053) | **released** | not used yet (M1's workers stay until a milestone needs per-connection tasks) |
| **B8** `Box<dyn I<S>>` parameter not storable when `S` is a type parameter | **open** | `tests/probes/generic-dyn-box-flow-bug.cee` still fails with N0272 on 0.9.0 |
| **U12** wall-clock time | **open** | — |

**Numbering:** this bug was B7 here until 0.9.0. CeeSafe's commit `96503b1` ("B7: closure environment names never
clash with captures") fixed a different bug of CeeSafe's own list, so this one is renumbered B8 to keep them apart.

## Status at 0.8.0 (released 2026-11-02, tag `v0.8.0` = CeeSafe `2c32172`)

| Item | Status | Checked by |
|---|---|---|
| B5 every cancel wakes every socket waiter | **fixed** (`226b514`: a cancel wakes only the tasks it reaches) | `benchmarks/overload/run.py`: server CPU per request with 1000 parked connections no higher than with none (150 vs 290 µs) |
| B6 `Async.deadline` over `Result<void, E>` | **fixed** (`ca4589d`) | probe prints `ok`; `writeWithin` and `closeGracefully` use `Async.deadline` |
| T2 `ceesafec test` (ADR 0049) | **released** | every test is an `@test` now (Web ADR 0005 amendment 1): 50 tests, native, interpreter, sanitizers, both sweeps |
| U8 system libraries in the manifest (ADR 0047) | **released** | not used yet (M4, OpenSSL) |
| U5 DNS for tasks (ADR 0048) | **released** | not used yet (M5) |
| U6, U7, U9, U10, U11 | not in 0.8.0 | — |

No open CeeSafe bug blocked CeeSafe.Web at 0.8.0's release. Found since: **B8** (blocks `web_server`), **U12** (no
wall clock: no `Date` field yet).

## Status at 0.7.0 (released 2026-11-02, tag `v0.7.0` = CeeSafe `b2bef17`)

Checked from CeeSafe.Web on 2026-11-02, macOS arm64 (`docs/milestones/M1.md`, runs):

| Item | Status | Checked by |
|---|---|---|
| U1 addresses, IPv6, socket options (ADR 0044) | **released** | `tests/web_transport/tcp` (IPv6 listener with options, both ends' addresses), `server` (addresses in the context, a real RST) |
| U2 network `IoError` kinds (ADR 0044) | **released** | `tests/web_core/errors`; `server`'s RST read is `Reset` |
| U3 vectored send (ADR 0045) | **released**; CeeSafe.Web uses lib/std's `IoParts` | `tests/web_core/transport`, `tcp` |
| U4 `Async.timeout`/`Async.deadline` (ADR 0046) | **released**, but see B6 | `Transports.readWithin` |
| T1 sweeps take a package | **released** (`--package`) | `tools/run_tests.py --cancel-sweep`: 9 packages, 305 points, 0 fail |
| B1 `@effectsOf` with an async closure | **fixed** | probe prints `70` |
| B2 drop body missing for an unused type | **fixed** | every test builds natively; probe prints `1` |
| B3 race child's captured array loses `const` | **fixed** | probe builds with `-Werror` |
| B4 kqueue livelock in `cee_wake_waiters` | **fixed** | probe 20/20, `server` 25/25 natively on kqueue |
| B5 every cancel wakes every socket waiter | **open** (performance) | read in `ceesafe_rt_async.c` at `v0.7.0`: the filter is unchanged |
| **B6** `Async.deadline` over `Result<void, E>` crashes `check` | **open** (new, below) | `tests/probes/async-deadline-void-bug.cee` |
| U5–U11 | not in 0.7.0 | — |

## Why this has to happen in CeeSafe

CeeSafe.Web is a set of library packages. A library cannot build its own async I/O operations:

- `Async.park()` and `Async.waker()` are library-only. Using them elsewhere is N0345 (`spec/type-system.md:3358`).
- `AsyncOp`, the ADR 0035 operation record, is a private struct in `lib/std/async.cee:94`.
- CeeSafe's rule (`AGENTS.md`): a missing library feature is designed in CeeSafe (spec and ADR first). It is never worked around with `unsafe` or private C.

So every new socket primitive goes into `lib/std` + `runtime/hosted/` through CeeSafe's normal process.

## Process for every item (from CeeSafe `AGENTS.md`)

1. ADR in `spec/decisions/`. The next free number is **0044**. Follow the shape of ADR 0035 or 0041: context, options, proposed decision, consequences, questions for the owner, implementation notes.
2. Spec: `spec/type-system.md` §40.4 (the async library table), §26.2 (`IoError`), `spec/c-backend.md` §6 (runtime calls), `spec/tooling.md` §4 (the manifest), and the API pages.
3. Runtime: `runtime/hosted/ceesafe_rt_io.c` and `ceesafe_rt_async.c`, with declarations in `runtime/include/ceesafe/rt.h`. Then regenerate with `python3 tools/gen_runtime.py`.
4. Library: `lib/std/async.cee` (and `lib/core/async.cee` for U4). Then regenerate with `python3 tools/gen_library.py`. Never edit generated files.
5. **The interpreter:** every new `ceesafe_async_op_*` / socket call needs an interpreter model, because the interpreter runs no C (ADR 0035 §3.6; `spec/intermediate-representation.md` §5.4).
6. **Every backend:**
   - readiness: epoll, kqueue, `poll()`, `WSAPoll`;
   - completion: IOCP (the Windows default), io_uring (opt-in, `CEESAFE_REACTOR_URING=1`).
7. Tests:
   - corpus run-pass / check-fail / emit-c;
   - ASan + UBSan, and TSan for `runWorkers`;
   - `tools/cancel_sweep.py` (cancels at every suspension, including with ops in flight);
   - the `CEESAFE_FAIL_ALLOC_AT` sweep, with `--check-leaks`;
   - the "reactor uring" contract step.

   Expected values come from oracles (Python), never from CeeSafe's own output.
8. `python3 tools/contract_tests.py --ceesafec out/ceesafec` green; `out/ceesafec fmt --check` clean; the guide updated (`guide/async.md` "Waiting for the outside world"); record the work in ROADMAP §10.
9. Keep a known-good `out/ceesafec` before changing the compiler.

---

## U1 — Socket addresses, IPv6 and socket options   *(needed by M1; target 0.6.0)*

**Today:**
- IPv4 dotted-quad strings only; the runtime uses `sockaddr_in` throughout (`ceesafe_rt_io.c:410-472`, `ceesafe_rt_async.c:2751, 2916, 2929`).
- The AcceptEx address buffer is sized for IPv4 (`ceesafe_rt_async.c:2281`).
- The listen backlog is hard-coded to **16** (`ceesafe_rt_io.c:433`, `:529`).
- Only `SO_REUSEADDR` and `SO_NOSIGPIPE` are set, internally.

**Add:**
- `public enum SocketAddress { V4([u8; 4], u16), V6([u8; 16], u16, u32 /* scope id */) }`, with `parse(StringView)` (IPv4, `[v6]:port`, zone ids) and `format` into a writer.
- `AsyncTcpListener.bindWith(address: SocketAddress, options: ListenOptions)`. `ListenOptions` holds `backlog: u32` (default `SOMAXCONN`), `reuseAddress: bool`, `reusePort: bool` (`SO_REUSEPORT`; on Windows refused or ignored, as documented), and `v6Only: bool`. Keep `bind(StringView, u16)` as a wrapper. Use a backlog default of `SOMAXCONN` in the old path too.
- `AsyncTcpStream.connectTo(SocketAddress)`.
- `AsyncTcpStream`:
  - `setNoDelay(bool)`, `setKeepAlive(idle_s?)`, `setSendBuffer(n)`, `setRecvBuffer(n)`, `setLinger(s?)`;
  - `peerAddress()`, `localAddress()`, `shutdownRead()`.
  - All return `Result<_, IoError>` and are not async.
- `AsyncTcpListener.localAddress()` (the existing `port()` stays).
- Apply the same changes to the blocking `ceesafe_tcp_*` path (`ceesafe_rt_io.c:443`), so both APIs agree.

**Runtime notes:**
- Use `sockaddr_storage` everywhere.
- Resize the AcceptEx buffer to `2 * (sizeof(struct sockaddr_in6) + 16)` and use `GetAcceptExSockaddrs`.
- `ConnectEx` needs a bind to `in6addr_any` for V6.
- io_uring's `IORING_OP_ACCEPT` should pass an address buffer if `peerAddress` is to be cheap. Otherwise call `getpeername` on demand, which is simpler.
- Check `sizeof(cee_op) <= sizeof(ceesafe_async_op)` (`_Static_assert`, `ceesafe_rt_async.c:2285`). The record is 192 bytes, mirrored by `AsyncOp.words: [u64; 24]` in `lib/std/async.cee`.

**Tests:**
- IPv6 loopback accept/connect on every backend.
- A backlog test: 1000 simultaneous connects, all accepted.
- Every option round-trips through `getsockopt` (C harness as oracle).
- A peer address matches the connector's local address.

## U2 — More `IoError` kinds   *(M1; 0.6.0)*

**Today:** `IoError` has `NotFound`, `PermissionDenied`, `AlreadyExists`, `IsDirectory`, `NotDirectory`, `InvalidInput`, `BrokenPipe`, `ConnectionRefused`, `AddressInUse`, `OutOfMemory`, and `Other(i32)` (`spec/type-system.md:1584`). Every other network error is `Other(errno)`, which is platform-specific.

**Add:** `ConnectionReset`, `ConnectionAborted`, `NotConnected`, `TimedOut`, `HostUnreachable`, `NetworkUnreachable` and `AddressNotAvailable`, mapped from the POSIX errno values and their Winsock equivalents (`WSAECONNRESET`, …) in `ceesafe_io_kind` (`lib/std/host.cee:38`). Each needs a `message()` text.

**Compatibility:** a `match` on `IoError` without a wildcard becomes non-exhaustive. Check the corpus and `src/` for such matches, and say in the ADR whether this needs a minor version bump.

**Tests:** RST from a peer (`SO_LINGER` 0) gives `ConnectionReset` on read and on write, on every backend; a connect to an unroutable address gives `TimedOut` or `HostUnreachable` (bounded by U4).

## U3 — Vectored send   *(M2; 0.6.0)*

**Today:** `ceesafe_async_op_read/_send/_accept/_connect` only (`rt.h:1183-1187`). An HTTP response's head and body take two syscalls, or a copy.

**Add:**
- `ceesafe_async_op_sendv(op, fd, parts: *const ceesafe_iovec, count)`, which returns the number of bytes written. A partial write is normal.
- `AsyncTcpStream.writeVectored(parts: Slice<Slice<u8>>) -> Result<usize, AsyncError>` (one step: partial count).
- `writeAllVectored(parts)`, which loops and advances across parts.

**Per backend:**
- readiness: `writev` (`sendmsg` with `MSG_NOSIGNAL` on Linux);
- IOCP: `WSASend` with a `WSABUF` array;
- io_uring: `IORING_OP_SENDMSG`.

**Design questions for the ADR:**
- **Where the iovec array lives while in flight.** It must outlive the kernel's use, as the buffer does (ADR 0035 §3.3). Options:
  - a bounded inline array in the op record, e.g. at most 16 parts. This raises `ceesafe_async_op`'s size, and `AsyncOp.words` with it, under the existing `_Static_assert`;
  - the caller's frame, since the frame never moves.

  The inline array is safer and is recommended.
- The cap on parts (`IOV_MAX`), and what happens past it: one step sends the first N parts.
- A cancellation with a vectored send in flight follows ADR 0035 §3.4: a completed send reports what it sent.

**Tests:**
- Parts of sizes 0, 1, and larger than the socket buffer.
- A slow reader, which forces partial writes across part boundaries. Bytes are checked against a Python oracle.
- Cancel in flight under ASan: the frame is freed only after completion.

**API constraint found 2026-11-02:** CeeSafe v0 has no slices or arrays of slices (N0246,
`~/dev/CeeSafe.Web/tests/probes/slice-of-slices-fail.cee`). So `writeVectored(parts: Slice<Slice<u8>>)` cannot be
written. Options for the ADR:
- a fixed-slot parts struct, as CeeSafe.Web's `IoParts<region r>` (`packages/web_core/src/transport.cee`, four slots);
- lifting N0246 for slices of slices as part of U3.

CeeSafe.Web's `Transport.writeVectored` takes `IoParts` and will map onto whichever U3 chooses.

## U4 — `Async.timeout`   *(M1; 0.6.0)*

**Today:** deadlines need `Async.race(body, async () => { await Timer.sleep(ms)?; return Err(TimedOut); })`. That is verbose, and it requires the body's error type to carry `TimedOut`.

**Add:** `Async.timeout(ms, timedOut: E, body) -> Result<T, E>`. The body runs. If it has not returned after `ms`, it is cancelled and awaited, and the result is `Err(timedOut)`. If it returned `Ok` before seeing the cancellation, the value is kept, matching "no value is lost" (§40.7). Like `race`, it allocates nothing: the child's frame is in the caller's. An optional second form is `Async.deadline(at_ms, …)`, an absolute deadline on `Timer.now()`.

**Tests:**
- Timeout firing during a read, a sleep, a channel receive, and a nested scope.
- A body finishing at the same instant (decided by order, deterministic in the interpreter's virtual clock).
- The cancel sweep.

## U5 — DNS resolution, phase A   *(M5; 0.6.x)*

**Add:** `AsyncDns.resolve(host: StringView, port: u16) -> Result<Vector<SocketAddress>, AsyncError>`, on a worker thread (`getaddrinfo`; `GetAddrInfoExW` on Windows), with results in resolver order and both families.

**Design points:**
- `getaddrinfo` cannot be cancelled. On cancellation, the task returns `Err(Cancelled)` at once, and the worker's result is dropped when it finishes. The owned result must not reference the task's frame.
- The pool of resolver workers is bounded, so that a flood of lookups cannot create unbounded threads.
- Errors: a new `DnsError` enum, or `IoError` kinds (`NotFound` for NXDOMAIN, plus `TemporaryFailure`?). Decide in the ADR.
- Interpreter: a model that resolves `localhost` / literals only, or the real call. Decide in the ADR.

Phase B, a native stub resolver over UDP (U6), is CeeSafe.Web's job later, not upstream.

## U6 — UDP   *(M10 HTTP/3, DNS phase B; target 0.7.0; released in 0.9.0)*

**Add:**
- `AsyncUdpSocket.bind(SocketAddress, UdpOptions)`, with options for reuse, buffer sizes, `v6Only`, ECN on (`IP_RECVTOS`/`IPV6_RECVTCLASS`), and `IP_PKTINFO`/`IPV6_RECVPKTINFO`.
- `sendTo(bytes, SocketAddress)` and `recvFrom(buffer) -> (n, SocketAddress)`. There are no tuples, so return a small struct.
- `recvMsg(buffer) -> UdpReceived { length, from, to /* local address */, ecn, truncated }` and `sendMsg(bytes, to, from?, ecn?, segmentSize?)`. `segmentSize` is GSO (`UDP_SEGMENT`) on Linux and a loop elsewhere.
- Optional: a batched receive (`recvmmsg`) as a later step.

**Per backend:**
- readiness: `recvmsg`/`sendmsg`;
- IOCP: `WSARecvMsg`/`WSASendMsg` (obtained through `WSAIoctl`);
- io_uring: `IORING_OP_RECVMSG`/`SENDMSG`.

The `msghdr` and control buffer live in the op record, so the size question of U3 applies again: decide both together.

**Tests:**
- Round trip on v4 and v6.
- ECN bits seen.
- The local address reported correctly on a multi-address host (or on loopback v4 and v6).
- A truncated datagram flagged.
- Cancellation while a receive is in flight.

## U7 — Zero-copy file send   *(M9; 0.7.0; released in 0.9.0)*

**Add:** `AsyncTcpStream.sendFile(file: borrow File, offset: u64, length: u64) -> Result<u64, AsyncError>` (partial count; plus a looping form).

**Per backend:**
- Linux `sendfile`, or `IORING_OP_SPLICE` through a pipe on io_uring;
- macOS/BSD `sendfile` (different signatures);
- Windows `TransmitFile` (overlapped, on IOCP);
- a fallback to read + write in a bounded buffer where unsupported.

**Design point:** the file must stay open while the op is in flight. The borrow already guarantees that, because the frame holds it.

**Tests:** files of 0 bytes, 1 byte, and larger than 4 GiB (offset handling); a slow reader (partial sends); cancel in flight; byte-exact comparison.

## U8 — System libraries in the manifest   **(required before M4; 0.6.x)**

**Why:** CeeSafe.Web uses **system OpenSSL**. Today a package can list C sources (`c = [...]`; every package's are compiled, `src/main.cee:758`) but cannot name libraries to link. Only `--cflag` on the command line can (`spec/tooling.md` §4.1, §6).

**Add to `ceesafe.toml` (`src/packages/manifest.cee`, `spec/tooling.md` §4.1):**

```toml
[package]
links = ["ssl", "crypto"]                 # -lssl -lcrypto (cc/clang/gcc), ssl.lib crypto.lib (cl)
include-dirs = ["/opt/homebrew/opt/openssl@3/include"]   # optional; for this package's own C files
lib-dirs = ["/opt/homebrew/opt/openssl@3/lib"]

[target.windows]
links = ["libssl", "libcrypto"]
include-dirs = ["C:/vcpkg/installed/x64-windows/include"]
lib-dirs = ["C:/vcpkg/installed/x64-windows/lib"]

[target.macos]     # and target.linux
lib-dirs = ["/opt/homebrew/opt/openssl@3/lib"]
```

**Rules for the ADR:**
- A library's `links` and `lib-dirs` propagate to the final executable's link line, in dependency order and deduplicated.
- `include-dirs` apply to that package's C files only.
- Nothing runs at build time (ADR 0013 D5): no pkg-config and no scripts. Paths are literal, or come from an environment variable named in the manifest (e.g. `"$OPENSSL_DIR/lib"`). Decide whether variables are allowed; they help CI.
- Include these fields in the lock/package hash (`src/packages/workspace.cee:144`).
- The `cl` mapping: `/I`, `/LIBPATH:`, `name.lib`.
- `--emit c` / `--emit obj` print the link line, or write it next to the output, for people building by hand.
- Diagnostics: N0321 for a bad field.

**Tests:**
- A package test that links a tiny C static library from `lib-dirs`.
- Propagation through a two-level dependency.
- Windows with `cl` and clang.
- Contract test: the hash changes when `links` changes.

## U9 — `HashMap` in std   *(optional; 0.7.0; released in 0.9.0)*

**Today:** collections are `Vector`, `SlotMap` and `StableVector` (`guide/collections.md`); `src/support` has a `Map` for the compiler only.

**Add:** `HashMap<K, V, A>`, with fallible operations (`put -> Result<…, AllocError>`), the rollback-on-failure rules of `docs/limits.md` §2, insertion-order iteration (determinism, as `src/support`'s map), a hash interface for keys, and a seeded hash for untrusted keys (resistance to HashDoS matters for header and query maps). It could start as a promotion of `src/support`'s map.

**Tests:** the support-library oracle approach (`tools/oracles/support_models.py`), the allocation-failure sweep, and a property test against a Python dict.

## U10 — Generic impls and impls for scoped types   *(optional; found while drafting M0, 2026-11-02; released in 0.9.0)*

**Today:** `impl I for T` requires `T` to be concrete and unscoped (`spec/type-system.md:1133`: "`impl<T> I for
Pair<T>` is future work"). `impl<T: Transport> Transport for Wrap<T>` does not parse (N0105). `impl Body for
RequestBody` where `RequestBody<region r>` is N0267. Separately, an interface method declaring its own region
(`async fn handle<region r>(…)`) is N0216. That one is fine: elided regions work, and the error appears only once an
impl exists.

**Effect on CeeSafe.Web:** the M0 ADRs design around it.
- `Box<dyn Transport>` at the connection edge, with TLS over a concrete enum (Web ADR 0001 §3.3).
- Concrete `RequestBody`/`ResponseWriter` instead of `dyn Body` (Web ADR 0003 §3.3).
- Hook-style middleware, or `Async.boxed` wrapping, instead of generic wrappers (Web ADR 0007 §3.2).

None of this blocks a milestone. Probes: `~/dev/CeeSafe.Web/tests/probes/impl-generic-fail.cee`,
`impl-scoped-fail.cee`, `method-region-fail.cee`.

**If taken up:** an ADR for generic impls covering:
- coherence: one impl per (interface, type constructor);
- bounds on the impl's parameters;
- how `dyn I` vtables are instantiated per type argument;
- whether regions on the implementing type may appear in method signatures;
- monomorphisation cost.

**Status:** wanted, not scheduled.

## B1 — Bug: `@effectsOf` async fn called with an async closure   *(found 2026-11-02, ceesafec 0.6.0; fixed in 0.7.0)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/effectsof-async-bug.cee`:

```ceesafe
@effectsOf(body)
async fn viaAsync(body: borrow fn() -> Result<u32, AsyncError>) -> Result<u32, AsyncError> {
    return body();
}
// …
let a = await viaAsync(async () => await slow(7))?;   // slow sleeps 5 ms, then returns Ok(7)
```

**Observed:**
- `ceesafec check` accepts it.
- `run --interp` gives `a == 0`.
- The native build crashes with exit 139. ASan reports a SEGV at address 0 (a null function pointer), called from
  `cee_exec_loop` (`runtime/hosted/ceesafe_rt_async.c:1788`) via `ceesafe_async_run`.

The same call through a **sync** `@effectsOf fn` works (the corpus form, `lib/core`'s `forEach`).

**Expected:** either the program is rejected (if `@effectsOf` on an async fn is not meant to be supported, which
spec §32.5 / ADR 0037 do not say), or it prints the right value. Safe code must never call a null pointer.

**Why CeeSafe.Web cares:**
- A generic `withDeadline(d, body)` needs this, or U4's `Async.timeout`.
- A closure parameter also cannot be awaited inside an `Async.race` child of an `@effectsOf` function: N0342 when
  awaited, N0295 when called. That is consistent with the spec, but it means U4 is the only way to a generic deadline
  helper.

## B2 — Bug: undefined drop function for an unused socket type   *(found 2026-11-02, 0.6.0 and main; fixed in 0.7.0)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/unused-socket-drop-bug.cee` (eight lines):

```ceesafe
public struct Holder {
    private stream: AsyncTcpStream;
}

fn main() {
    Console.writeLine(1);
}
```

**Observed:**
- `check` and `run --interp` pass.
- `build` fails in the C compiler with `call to undeclared function 'ceeD_14AsyncTcpStream'`. With
  `AsyncTcpListener` the missing function is `ceeD_16AsyncTcpListener`, or `ceeM_16AsyncTcpListener_drop` once a
  function constructs one.

It looks like a struct's drop glue is emitted, but the drop of a lib/std type with a `drop` block is emitted only when
the program reaches that type's other operations.

**Effect:** every program that depends on `web_transport` but never makes a real socket (tests that use only
`MockTransport`) fails to build natively. `tests/web_transport/{mock,acceptor}` carry a `native-xfail` marker until
this is fixed; the runner reports XPASS when it is.

**General form (found in M1, 2026-11-02): it is not about sockets, and it now blocks M1.** Any type with a `drop`
block (`Channel<T>`, `AsyncTcpStream`, …) that a library struct holds, in a program that never reaches the type's
operations: the drop glue `ceeD_<T>` is emitted for every instantiated type and calls the `drop` block
`ceeM_<T>_drop`, which is emitted only when reachable from `main`. From `tests/web_transport/workers`, built with
`--emit c` (0.6.0):

```c
static void ceeD_S7Channel1u8(ceeT_7Channel1u8 *p) {
    ceeM_S7Channel1u8_drop(p);   // never emitted: 'call to undeclared function'
}
```

`web_core`'s `Drain` holds a `Channel<u8>`, so **every native program that imports `web_core` and does not start a
drain fails to build**, the echo example included. The same goes for `web_transport`'s `MockIncoming`
(`Channel<MockTransport>`). **Fix, probably:** when the drop glue of a type is emitted, mark that type's `drop` block
reachable (or emit drop glue only for the types that are reached).

**Effect on CeeSafe.Web:** M1 waits on a branch (`m1`) until a release fixes this. On it, six tests carry
`native-xfail` (B2), and the runner reports XPASS once they build.

## B4 — Bug: kqueue livelock in `cee_wake_waiters` when a cancel unparks socket waiters   *(found 2026-11-02, 0.6.0; fixed in 0.7.0)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/wake-waiters-livelock-bug.cee`. 40 (or 10) connected sockets with a reader
task each, while another task writes a byte to each in turn and resolves an `Async.race` after each write (the loser
parked on a timer). Natively on macOS it hangs **20 of 20 runs** at 100 % CPU; with 10 sockets, 10 of 10. The
interpreter prints `1`, and so does `CEESAFE_REACTOR_POLL=1` (0 hangs of 10).

**Observed** (`sample`): one call `ceesafe_async_cancel → cee_cancel_scope → cee_wake_waiters` that never returns,
spinning through `cee_unpark`. The same signature hung `tests/web_transport/server` about once in eight runs.

**Likely cause** (`runtime/hosted/ceesafe_rt_async.c` at `v0.6.0`):
1. `cee_wake_waiters` walks `cee_exec.parked`, keeping `next = t->next` and then calling `cee_unpark(t)`.
2. Unparking a socket waiter detaches it (`cee_fd_detach` → `cee_reactor_disarm`), which queues an `EV_DELETE` through
   `cee_kqueue_change`.
3. When the 64-entry change buffer is full, `cee_kqueue_change` flushes it with `cee_kqueue_call(&now)`, which
   readies tasks through `cee_fd_ready` → `cee_unpark`. It can unpark the saved `next`, whose `next` link then belongs
   to another list, so the walk never ends.

epoll's disarm calls `epoll_ctl` directly, so Linux may be unaffected; this was not run.

**Fix, probably:** never unpark from inside the change queue's flush while a walk is in progress. For example, flush
without readying, keeping the events for the next wait, or restart the walk from the head after each unpark.

**Effect on CeeSafe.Web:** `tests/web_transport/server` runs natively on macOS with `CEESAFE_REACTOR_POLL=1` (a
`native-env` marker, `tools/run_tests.py`) until this is fixed.

## B5 — Performance: every cancellation wakes every socket waiter   *(found 2026-11-02, 0.6.0; fixed in 0.8.0)*

`cee_wake_waiters` unparks **every** parked task waiting on a descriptor, in the whole executor, whenever any scope
is cancelled, on the readiness backends (kqueue, epoll, poll). Its filter is `!t->on_scope && cancellable`; a
descriptor wait is not `on_op` (that flag marks IOCP completions only, `ceesafe_rt_async.c:110`), so it counts as
cancellable. This was read in the code, not measured. Each such task
retries its system call, gets `EAGAIN` and parks again, rearming the reactor.

A server cancels a scope every time a race resolves: each deadline-bounded read (`Transports.readWithin`), and each
accept that loses to the drain. So with *n* idle connections, every request costs O(*n*) spurious wake-ups, and the
server is O(*n*²) overall. **Wanted:** wake only the tasks of the cancelled scope and its descendants, for example by
walking the scope's own children instead of the executor's parked list. This blocks M1's overload benchmark, but not
M1's correctness.

## B3 — Bug: generated C discards `const` for a race child's captured array   *(found 2026-11-02, 0.6.0; fixed in 0.7.0)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/race-capture-const-warning.cee`: an `Async.race` child that lends a captured
local array as `mut buf[..]`.

**Observed:** the C initialises a `uint8_t *` from a `const uint8_t *` (`-Wincompatible-pointer-types-discards-qualifiers`).
The program runs correctly (prints 7), but `--cflag -Werror` fails.

**Expected:** no warning: the capture is mutable, so its C type should not be const.

## B8 — Bug: a `Box<dyn I<S>>` parameter cannot be stored when `S` is a type parameter   *(found 2026-11-02, 0.8.0; was B7 here; fixed in 0.9.1)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/generic-dyn-box-flow-bug.cee`:

```ceesafe
struct Generic<S> {
    handlers: Vector<Box<dyn Handler<S>>>;
    fn add(borrow mut this, h: Box<dyn Handler<S>>) -> bool {
        match (this.handlers.push(h)) { … }   // N0272
    }
}
```

**Observed:** N0272, "this stores a value borrowed from parameter 'h' into what 'this' refers to". The same method
on a non-generic struct with `Box<dyn Handler<St>>` is accepted, and so is `Holder<T>.add(t: T)` storing a plain `T`.
A free function `fn addFree<S>(r: borrow mut Generic<S>, h: Box<dyn Handler<S>>)` is rejected the same way.

**Expected:** it checks and prints 2. Two rules say so: §37.2, "`Box<dyn I>` carries none" (no loans: what
implements an interface is unscoped); and, where §25.7 rule 5 treats a type mentioning `S` as carrying loans, §25.6
makes `S` a flow name of both `this` and `h`, so the flow is allowed (as it is for `Holder<T>`). Probably the flow
names of a type are not collected through a `dyn` interface's type arguments, while rule 5 still sees `S` there.

**Blocks:** `web_server`'s `Router<S>.add` and `Server<S>`'s middleware list (Web ADR 0007 as the owner chose on
2026-11-02: typed state `S`; with U10 released in 0.9.0, `web_server` implements `Application` for every `Server<S>`
itself). `web_server` waits on branch `m2-server`. It is the only CeeSafe item blocking a CeeSafe.Web milestone.

## B9 — Bug: a generic struct's method cannot pass `this` as `dyn I` when the impl is generic   *(found 2026-11-02, 0.9.1; fixed in 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/generic-impl-dyn-in-generic-bug.cee`. With `impl<S> Run for Wrap<S>`, a
method of `Wrap<S>` calling `runDyn(this)` (`r: borrow dyn Run`) is N0268, "'Wrap<S>' does not implement 'Run'". A
generic free function `fn generic<S>(w: borrow Wrap<S>) => runDyn(w)` is accepted, and so is `main` with a `Wrap<St>`.

**Expected:** it checks and prints 7 three times (ADR 0054 §3.1: the generic impl applies to `Wrap<S>` for every `S`).
The impl lookup probably does not see the struct's own type parameter as matching the impl's.

**Blocks:** `Server<S>.run(listener, …)`, which would hand `this` to `ConnectionServer.run` as a `dyn
ConnectionHandler`. Applications can call `ConnectionServer.run` themselves meanwhile; the method is left out.

## B10 — Bug: an async interface boxed as `dyn` but never called through `dyn` gives C that does not compile   *(found 2026-11-02, 0.9.1; also 0.8.0 and 0.9.0; fixed in 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/async-dyn-uncalled-bug.cee`: `interface Job { async fn run(borrow this) ->
u32; }`, `let b: Box<dyn Job> = Box.create(Seven {})!;` and nothing else. `run --interp` prints 1; `build` fails with
`incomplete definition of type 'struct ceeAF_dyn_D3Job_m0'` (9 errors): the `dyn` table's resume function uses the
`dyn` call's frame struct, which is emitted only when some call goes through `dyn`.

**Expected:** it builds and prints 1.

**Blocks:** `web_server`'s router tests, which add boxed handlers and route paths without calling the handlers.

## B11 — Bug: a call through a returned `borrow Box<dyn I>` panics the compiler   *(found 2026-11-02, 0.9.1; also 0.8.0 and 0.9.0; fixed in 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/borrowed-box-dyn-call-bug.cee`:

```ceesafe
fn item(borrow this, i: usize) -> borrow Box<dyn Run> => borrow this.items[i];
…
Console.writeLine(h.item(0).run());
```

**Observed:** `ceesafe panic: integer overflow at src/backend/c_body.cee:1741:91`, natively and under `--interp`. The
line is the `dyn` branch of `NirRvalue.Ref`: `appendPlaceParts(…, p.projections.count - 1)`, with a place that has no
projections (the temporary holding the returned borrow).

**Expected:** it prints 7.

**Blocks:** `Server<S>`'s dispatch, `this.routes.handler(index).handle(…)`.

## B12 — Bug: a generic impl's method passing `this` as `dyn` emits the uninstantiated type   *(found 2026-11-02, 0.9.0 and 0.9.1; fixed in 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/generic-impl-this-dyn-bug.cee`: `impl<S> Run for Wrap<S>`, and `impl<S> Outer
for Wrap<S> { fn outer(borrow this) -> u32 => runDyn(this); }`. It checks and prints 7 under `--interp`; `build`
fails: the C calls `ceeD_S4Wrap1S((ceeT_4Wrap1S *)…)`, the type with `S` itself, not `Wrap<St>`. With both impls
concrete it builds and prints 7.

**Expected:** it builds and prints 7: the `dyn` table recorded inside an instance of a generic impl is for the
instantiated type.

**Blocks:** `impl<S> ConnectionHandler for Server<S>`, which runs the HTTP/1.1 driver with `this` as the `dyn
Application`.

## U12 — Wall-clock time   *(found 2026-11-02 while writing M2's serializer; needed for `Date`; released in 0.9.1 as ADR 0055)*

**Gap:** lib/std has only the monotonic clock (`Timer.now()`, `lib/core/async.cee:37`); nothing reads the system's
real time, and the runtime has no `CLOCK_REALTIME` call (searched `runtime/` and `lib/` at `v0.8.0`).

**Needed by:** the `Date` response field (RFC 9110 §6.6.1: an origin server with a clock must send it; one without
must not), cookies' `Expires`, logs, and TLS certificate validity (M4, though OpenSSL reads its own clock).

**Wanted:** `Clock.unixMillis() -> u64` (or seconds and nanoseconds), `clock_gettime(CLOCK_REALTIME)` /
`GetSystemTimePreciseAsFileTime`, with the interpreter's virtual clock answering deterministically.

**Effect on CeeSafe.Web until then:** the server sends no `Date`, which RFC 9110 allows a server without a clock to
do. `web_http.HttpDate.format(unixSeconds, …)` is written and tested, so the driver turns `Date` on when U12 lands.

## U13 — `Channel.tryRecv`: a receive that does not wait   *(found 2026-11-02 while designing M3, Web ADR 0008; wanted, not blocking)*

**Gap:** `Channel<T>` (`lib/std/channel.cee`, CeeSafe ADR 0032) can send without waiting (`trySend`), but every
receive waits: `recv` blocks the thread, `recvAsync` parks the task. Nothing takes a value only if one is there.

**Needed by:** `web_sse` (Web ADR 0008 §3.3–3.4):
- `Overflow.DropOldest`: on a full queue, the producer takes and drops the oldest event, then sends;
- batching: after writing one event, the stream writes every event already queued before it flushes once, instead
  of a flush (a socket write) per event.
Later, the same need appears in HTTP/2's per-stream queues (M7) and the proxy (M8).

**Wanted:** `public fn tryRecv(borrow this) -> T?` returns the next value if one is buffered, else `null` at once,
whether the channel is empty or closed. It has no effect, like `trySend`, so async code and `drop` blocks may call
it. With a way to tell "empty" from "closed and empty" if that is cheap (for example `Result<T?, ...>`, or
`isClosed()`), but `null` for both is enough for these uses.

**Effect on CeeSafe.Web until then:** `EventQueue.create(…, Overflow.DropOldest)` returns `Err(Unsupported)`, and the
stream flushes after every event. Nothing else waits.

## U14 — C allocations that fault injection can fail but the leak check leaves out   *(found 2026-11-02, M4; wanted, not blocking)*

**Gap:** `ceesafe_alloc` (`runtime/include/ceesafe/rt.h`) counts every allocation, fails the one numbered
`CEESAFE_FAIL_ALLOC_AT`, and `ceesafec test` fails a test that ends with any of them live. A C library that keeps
global state until the process exits, such as OpenSSL (error strings, providers, its default library context), cannot
be routed through it: every test would report that state as a leak.

**Needed by:** Web ADR 0002 §3.5 and §6.1 (accepted by the owner): OpenSSL's allocations go through CeeSafe's allocator
in test builds (`CRYPTO_set_mem_functions`), so the allocation-failure sweep reaches OpenSSL's out-of-memory paths too.

**Wanted:** an allocator number for C code, for example `CEE_ALLOC_FOREIGN`, whose allocations are counted and
fault-injected like the others, but not reported by the leak check (or reported apart, as "still held by C at exit").
`ceesafe_alloc`, `ceesafe_realloc` and `ceesafe_free` with it, declared in a header a package's C files can include
(today a package's C files are compiled without the runtime's include directory).

**Effect on CeeSafe.Web until then:** OpenSSL keeps its own allocator in every build (Web ADR 0002 amendment 2.1).

## T3 — Windows: the runtime warns under `-Wsign-conversion`   *(found 2026-11-02, 0.9.2, Windows 11 ARM64, clang 23)*

**Observed:** every native build on Windows prints four warnings from the runtime: `implicit conversion changes
signedness: 'unsigned long' to 'long'` at `ceesafe_rt_io.c` (3) and `ceesafe_rt_async.c:2592`, each an
`ioctlsocket(…, FIONBIO, …)`: the SDK's `FIONBIO` macro is an `unsigned long` expression and `ioctlsocket` takes a
`long`. **Wanted:** a cast (`(long)FIONBIO`), so builds on Windows are quiet. Cosmetic.

## B14 — Bug: `dyn I + Send` as a type argument does not parse in an expression   *(found 2026-11-02, 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/dyn-send-type-argument-bug.cee`:
`var v = Vector<Box<dyn Job + Send>>.create();` is `N0103: comparison operators cannot be chained`. The same type
parses in a declaration (`var v: Vector<Box<dyn Job + Send>>`, a field). **Expected:** it checks and prints 1.
**Effect on CeeSafe.Web:** `web_client` wraps the box in a struct (`Live { conn: Box<dyn Transport + Send> }`), which
spells the same thing.

## B15 — Bug: async calls through both `dyn I` and `dyn I + Send` give C that does not compile   *(found 2026-11-02, 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/dyn-send-async-frames-bug.cee`: one function awaits `j.run()` with
`j: borrow mut dyn Job`, another with `j: borrow mut Box<dyn Job + Send>`.

**Observed:** `run --interp` prints 3. `build` fails with 9 C errors: `redefinition of 'ceeAF_dyn_D3Job0_m0'`, and of
its resume function (`ceeAT_…`), its suspension table (`ceeAL_…`) and its frame info (`ceeAN_…`, "dyn Job + Send.run"
next to "dyn Job.run"). The dyn-call frame's C name leaves out `+ Send`, so the two types' frames collide.

**Expected:** it builds and prints 3. The frames could share one definition, since `+ Send` does not change the
layout, or get distinct names.

**Blocks:** M5's `web_client` natively. Its pool holds `Box<dyn Transport + Send>` (Web ADR 0009 §2), and every
program also awaits `dyn Transport` (M1's server, the driver). The mere existence of a `dyn Transport + Send` box in
a program is enough: changing `Connector.connect` to return one broke every native build on main, so that change
waits too (it is on `m5-client`). Its 11 tests pass in the interpreter; native, the
sanitizers and the sweeps wait. The client is on branch `m5-client`.

## B13 — Bug: an `Async.scope` whose error type has a drop gives C that does not compile   *(found 2026-11-02, 0.9.2)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/scope-droppable-error-bug.cee`:

```ceesafe
struct Failure { text: Vector<u8>; }
async fn run() -> Result<u64, Failure> {
    var a: u64 = 0;
    var b: u64 = 0;
    let r: Result<void, Failure> = await Async.scope(Failure { text: Vector<u8>.create() }, (s) => {
        s.spawn(async () => { a = 1; return Ok(); });
        s.spawn(async () => { b = 2; return Ok(); });
    });
    ...
}
```

**Observed:** it checks, and `run --interp` prints 3. `build` fails with 3 C errors: `call to undeclared function
'ceeD_RvS7Failure'`, at the join's `ceeD_RvS7Failure(&f->cee_result); /* a later child's error, not the scope's */`,
then `static declaration of 'ceeD_…' follows non-static declaration`. The drop function of `Result<void, Failure>` is
called before it is declared. The same happens with lib/std's `TestFailure` as the error type, which is how it was
found (`web_sse`'s tests).

**Expected:** it builds and prints 3.

**Effect on CeeSafe.Web:** tests use a copyable error type for scopes (`NetError`, as M1's tests already did). Library
code is not affected so far: its scopes use `NetError` or `HandlerError`.

## B6 — Bug: `Async.deadline` or `Async.timeout` over `Result<void, E>` crashes `ceesafec check`   *(found 2026-11-02, 0.7.0; fixed in 0.8.0)*

**Repro:** `~/dev/CeeSafe.Web/tests/probes/async-deadline-void-bug.cee`:

```ceesafe
async fn work() -> Result<void, E> => Ok();
async fn within(at: u64) -> Result<void, E> {
    return await Async.deadline(at, E.TimedOut, async () => await work());
}
```

**Observed:** `ceesafec check` panics with `index out of bounds at src/main.cee:687:41`, in `report`, while it renders
a diagnostic: `sources[d.file as usize]` is out of range, so some diagnostic carries a file index that is not a
source. `--diagnostics-format=json` panics too, at `src/diagnostics/diagnostic.cee:303:37`. The same with
`Async.timeout(1000, …)`. With `Result<u32, E>` (body and function) it checks, builds and runs.

**Expected:** it checks, and prints `ok`. So two things are probably wrong: the checker reports something for a
`void` body (perhaps from the synthesized `Raced`/join over `void`), and that diagnostic's span points at no file.

**Effect on CeeSafe.Web:** `Transports.writeWithin` and `closeGracefully` (both `Result<void, NetError>`) keep their
race against `deadlineElapsed` (Web ADR 0001 amendment 3.4); `readWithin` uses `Async.deadline`.

## U11 — Spawning into a running scope   *(design question; found while drafting M1, 2026-11-02; released in 0.9.0 as ADR 0053)*

**Today:** `s.spawn` exists only in a scope's synchronous body (spec §40.5: "`s` is not a local, and names the scope
only in `s.spawn(…)`"), and there are no detached tasks. A server therefore cannot start a task per accepted
connection, and an HTTP/2 connection cannot start a task per stream as streams open.

**What works now** (`~/dev/CeeSafe.Web/tests/web_transport/workers`): a fixed set of worker children, each looping
`accept → serve` on one shared listener. Concurrent accepts on one listener work on kqueue, in the interpreter and
natively. The worker count is the connection limit, and each idle connection costs one worker frame (920 bytes in the
probe). It is bounded by construction, but every worker's frame is allocated up front.

**If taken up:** an ADR for a spawn handle that children may hold, for example a `TaskSet<E>` or a scope handle passed
by borrow. It would need:
- the scope-loan rule for what dynamically spawned children capture;
- a bound on how many are live (admission), with a spawn that waits or fails when full;
- allocation from the scope's allocator;
- cancellation and error propagation as for static children.

Without it, M1 and M7 use the worker pattern. **Status:** proposed; the owner decides (`docs/milestones/M0.md`).

## Tooling (small, any time before M1's exit)

- **T1 Sweeps on any package.** CeeSafe.Web's M1 gate reuses `tools/cancel_sweep.py` and `tools/fault_sweep.py`. Check that both accept a package directory, not only corpus files. If they don't, add a `--package <dir>` mode.
- **T2 `ceesafec test`** *(optional; released in 0.8.0 as ADR 0049, and CeeSafe.Web's tests use it)*.

---

## Order and releases

| Release | Items | Unblocks in CeeSafe.Web |
|---|---|---|
| **0.7.0** (released 2026-11-02) | U1, U2, U3, U4, T1; B1–B4 fixed | M1 transport/lifecycle (merged), M2 HTTP/1.1 fast path |
| **0.8.0** (released 2026-11-02) | B5, B6 fixed; U8, U5, T2 | M1's overload gate (passed); M4 TLS (OpenSSL), M5 client |
| **0.9.0** (released 2026-11-02) | U6, U7, U9, U10, U11 | M9 sendfile, M10 HTTP/3; `web_server` without delegating impls |
| **0.9.1** (released 2026-11-02) | B8 fixed; U12 | the `Date` field |
| **0.9.2** (released 2026-11-02) | B9, B10, B11, B12 fixed | `web_server` (M2 task 5) |
| next | B15 fixed (blocks M5); B13, B14 fixed; U13, U14, T3 | `web_sse`'s `DropOldest` and batched flushes (M3); scopes with `TestFailure` in tests |

Suggested ADR batching:
- 0044 covers U1 + U2 (addresses, options and errors: one API surface).
- 0045 covers U3 + U6's op-record sizing (decide the record layout once).
- Separate ADRs for U4, U5, U7 and U8; U9 later.

**Already in place, so not needed upstream:**
- Completion I/O with cancel-waits-for-kernel (ADR 0035).
- `dyn` async methods (ADR 0036).
- Work stealing (ADR 0038).
- Region parameters (ADR 0006).
- Library C files (§28.6).
- Imported unions and variadic calls (ADR 0041).
