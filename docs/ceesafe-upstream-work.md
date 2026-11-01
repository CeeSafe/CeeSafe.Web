# Upstream work in CeeSafe for CeeSafe.Web (U1–U9 and tooling)

**For:** a separate session working in `~/dev/CeeSafe` (main at `5fa4316`, ceesafec 0.6.0-dev).
**From:** `~/dev/CeeSafe.Web/docs/implementation-plan.md` §5 (accepted 2026-11-02).
**Status:** proposed work, not started. File references were checked against `5fa4316`; re-check them before editing.

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

## U6 — UDP   *(M10 HTTP/3, DNS phase B; target 0.7.0)*

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

## U7 — Zero-copy file send   *(M9; 0.7.0)*

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

## U9 — `HashMap` in std   *(optional; 0.7.0)*

**Today:** collections are `Vector`, `SlotMap` and `StableVector` (`guide/collections.md`); `src/support` has a `Map` for the compiler only.

**Add:** `HashMap<K, V, A>`, with fallible operations (`put -> Result<…, AllocError>`), the rollback-on-failure rules of `docs/limits.md` §2, insertion-order iteration (determinism, as `src/support`'s map), a hash interface for keys, and a seeded hash for untrusted keys (resistance to HashDoS matters for header and query maps). It could start as a promotion of `src/support`'s map.

**Tests:** the support-library oracle approach (`tools/oracles/support_models.py`), the allocation-failure sweep, and a property test against a Python dict.

## U10 — Generic impls and impls for scoped types   *(optional; found while drafting M0, 2026-11-02)*

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

## B1 — Bug: `@effectsOf` async fn called with an async closure   *(found 2026-11-02, ceesafec 0.6.0)*

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

## Tooling (small, any time before M1's exit)

- **T1 Sweeps on any package.** CeeSafe.Web's M1 gate reuses `tools/cancel_sweep.py` and `tools/fault_sweep.py`. Check that both accept a package directory, not only corpus files. If they don't, add a `--package <dir>` mode.
- **T2 `ceesafec test`** *(optional)*. There is no test command (`guide/ceesafec.md`). CeeSafe.Web will use its own runner first, so this is only worth an ADR if more packages want it.

---

## Order and releases

| Release | Items | Unblocks in CeeSafe.Web |
|---|---|---|
| **0.6.0** | U1, U2, U3, U4 (+ T1) | M1 transport/lifecycle, M2 HTTP/1.1 fast path |
| **0.6.x** | U8 (by CeeSafe.Web week 9), U5 | M4 TLS (OpenSSL), M5 client |
| **0.7.0** | U6, U7, U9 | M9 sendfile, M10 HTTP/3 |
| unscheduled | U10 | simplifications only |

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
