# CeeSafe.Web — Verified Implementation Plan

**Date:** 2026-11-02
**Inputs:** `~/Downloads/CeeSafe_Web_Networking_Roadmap.md` (the roadmap), `~/dev/CeeSafe` at `5fa4316` (main, ceesafec 0.6.0-dev, last release 0.5.1).
**Status:** accepted by the owner, 2026-11-02, with the decisions in §11. Every claim about CeeSafe cites the file it comes from; nothing below has been built or run yet.

---

## 1. Verdict

The roadmap's layering, priorities and gates are sound. Its core rule ("protocol engines must not own the event loop") matches how CeeSafe's async runtime already works. The milestone order is mostly right, and the M10-in-parallel note is reasonable.

It was written without three facts about CeeSafe, and each one changes the plan:

1. **CeeSafe.Web cannot add I/O primitives itself.** `Async.park()` and `Async.waker()` are library-only (N0345, `spec/type-system.md:3358`), and `AsyncOp` is a private struct in `lib/std/async.cee:94`. So every new socket operation has to land **upstream in CeeSafe's `lib/std` and runtime**, through CeeSafe's spec-first ADR process. That includes UDP, IPv6, socket options, vectored writes, sendfile, peer addresses and async DNS. This is the plan's critical path, and the roadmap has no track for it.
2. **The language already provides much of M0.** Borrow-checked `Slice`/`SliceMut`, frames that never move, structured cancellation, and ADR 0035's "cancel waits for the kernel" rule already guarantee "no buffer recycled while an async op references it". `CancellationToken`, `ReadLease` and `WriteLease` would duplicate or fight the language. M0 should define fewer types, not more.
3. **A package has one flat namespace.** "All files of one package share one scope" (`guide/packages.md`). The suggested `src/core/ src/http1/ …` folders would not isolate anything. The layers should be **separate library packages** with path dependencies. The dependency graph then enforces the layering too: `http1` cannot import `tls`.

---

## 2. What CeeSafe provides today (verified)

| Capability | Where | Relevance |
|---|---|---|
| Stackless async/await. Frames have a compile-time size and never move (`--explain-frames`) | ADR 0031; `guide/async.md` "How it runs" | Memory per connection is **known at compile time**. A buffer borrowed across `await` is sound. |
| Structured concurrency: `Async.scope`, `scopeIn(alloc, …)`, `spawn`, a child per loop iteration | ADR 0031, 0033; `guide/async.md` | One task per connection inside the accept loop's scope. A child's block is freed when it returns (`runtime/hosted/ceesafe_rt_async.c:1738`). |
| Cancellation as a flag. The next wait returns `Err(Cancelled)`. `Async.shield(ms, body)` for cleanup. `Async.race`, `Async.all` | `lib/core/async.cee`; `guide/async.md` "Cancellation" | Graceful drain and deadlines (race against `Timer.sleep`). A token type is not needed. |
| Completion I/O: IOCP (Windows default), io_uring (Linux, opt-in `CEESAFE_REACTOR_URING=1`), plus epoll, kqueue and poll. Cancelling waits for the kernel and loses no value | ADR 0035 §3, §6; `lib/std/async.cee:94-135` | Zero-copy reads into caller buffers are already safe. |
| Work-stealing executor: `Async.runWorkers(n, …)`, `s.spawnSend` | ADR 0038 | Multi-core server. Request and connection types must be `Send`. |
| Async methods in interfaces, both generic (static) and `dyn` | ADR 0036; `guide/generics.md` | `Transport`, `BodyReader` and `Handler` as interfaces. Static dispatch gives the "compile-time specialisation" the roadmap wants. |
| Region parameters on structs holding views | ADR 0006; `guide/ownership.md:264` | `Request<region r>` with header `StringView<r>` into the connection buffer. The compiler enforces that views do not outlive their storage, which is ADR-003's job. |
| Allocators: `Pool<T>`, arenas, scratch frames, custom allocators. Allocation failure is a value | `guide/memory.md`; `docs/limits.md` §2 | Admission control: a bounded `Pool` of connection frames through `Async.scopeIn`. |
| Channels with `sendAsync`/`recvAsync`, `AsyncMutex` | ADR 0032 | Bounded SSE/WebSocket outbound queues. |
| C interop. Library packages can ship C (`c = [...]`, compiled for every package: `src/main.cee:758`). Imported structs and unions, variadic calls | §28, ADR 0041 | TLS and QUIC engines over C libraries. |
| Fault tools: `CEESAFE_FAIL_ALLOC_AT` sweep, `tools/cancel_sweep.py` (cancels at every suspension), `--check-leaks`, ASan/UBSan/TSan contract steps | `AGENTS.md` Tools; `spec/testing.md` | The M1 exit gate ("no leaked tasks or sockets") can reuse these. |
| Interpreter with a virtual clock and deterministic task order, using real sockets | `guide/async.md` | Deterministic protocol tests. **It runs no C**, so TLS and QUIC tests need a native build. |

### Gaps found (not in the roadmap)

| Gap | Evidence | Blocks |
|---|---|---|
| IPv4 dotted-quad only, TCP only. No UDP, no IPv6 | `lib/std/async.cee` `bind`/`connect` docs; runtime uses `sockaddr_in` | M5 (Happy Eyeballs), M10 (QUIC) |
| Listen backlog hard-coded to **16** | `runtime/hosted/ceesafe_rt_io.c:433`, `:529` | M1 overload behaviour, every benchmark |
| No socket options (`TCP_NODELAY`, `SO_REUSEPORT`, keep-alive, buffer sizes, `SO_LINGER`), no peer or local address, no `shutdown(read)` | only `SO_REUSEADDR`/`SO_NOSIGPIPE` are set internally | M1, M8 (client IP), benchmarks |
| No vectored I/O (`writev`/`WSASend` with several buffers), no `sendfile`/`TransmitFile`/`splice` | `ceesafe_async_op_*` covers read/send/accept/connect only | M2 (header+body in one syscall), M9 |
| No async DNS. `Async.blocking(getaddrinfo)` would work but is not exposed, and a blocking resolve cannot be cancelled | — | M5 |
| `IoError` has no `ConnectionReset`, `ConnectionAborted`, `TimedOut` or `NotConnected` (they become `Other(i32)`) | `spec/type-system.md:1584` | Deterministic error handling (M1, M5, M8) |
| No public hash map in std (only `SlotMap`, `StableVector`, `Vector`) | `guide/collections.md` | Router, pool-by-origin, HPACK index (all work with tries or vectors at first) |
| No `ceesafec test` command | `guide/ceesafec.md` command table | Test harness (§6) |
| The manifest cannot link system libraries (`-lssl`), only `--cflag` on the command line | `spec/tooling.md` §4.1 | M4: OpenSSL is the chosen provider (§11), so U8 is required |
| CeeSafe cannot call C macros, and many OpenSSL entry points are macros (`SSL_set_tlsext_host_name`, `SSL_CTX_set_min_proto_version`, … over `SSL_ctrl`) | §28 (extern functions only) | M4: needs a small C shim |
| The `ceesafec` on `PATH` is **0.3.2**, which predates ADR 0035 (completion I/O) and ADR 0041 | `~/.ceesafe/bin/ceesafec --version` | Pin ≥ 0.5.1 or use `~/dev/CeeSafe/out/ceesafec` |
| Language constraints: no tuples, no type aliases; a closure stored in a field cannot be called | `AGENTS.md` "Common mistakes" | Handlers and middleware must be `interface`s (`Box<dyn Handler>`) or generics, not stored closures |

---

## 3. Corrections to the roadmap

| Roadmap item | Change | Why |
|---|---|---|
| M0: `CancellationToken` | **Dropped (decided).** Use task cancellation, `Async.race` and `Async.shield`. Add a small `Deadline` value type and a `withDeadline(d, body)` helper over `race` + `Timer.sleep`, which becomes a thin wrapper over U4's `Async.timeout`. To cancel work from outside its task tree (shutdown, a client request abandoned by its caller), race the body against an `AsyncSignal`: `Async.race(body, signal.wait())`. If that proves clumsy, the fix is an upstream ADR for a scope handle, not a token type | A token must be checked by hand at every `await`, and a token firing during an in-flight op would bypass ADR 0035's cancel-and-wait rule. A second mechanism would also diverge from scope semantics (the first error cancels siblings) |
| M0: `BufferSlice`, `ReadLease`, `WriteLease` | **Replaced (decided)** by `Slice<u8>`/`SliceMut<u8>`/`StringView<r>`, borrow-checked. Keep `OwnedBuffer` (`@noncopy`, drop frees) and add `PooledBuffer` (a `PoolBox`-style handle that returns its slot on drop) | Borrows already forbid recycling during an outstanding op (the frame holds the loan, ADR 0035 §3.3). Leases would only be runtime checks of what the compiler proves |
| M0: `AsyncRead`/`AsyncWrite`/`Transport` | Keep them as **one interface** `Transport` with async `read`, `write`, `writeVectored` and `shutdownWrite` (drop closes). Engines are sans-I/O and never see a transport. Drivers take `borrow mut dyn Transport`; the connection edge is `Box<dyn Transport>`; `TlsTransport` wraps a concrete `PlainStream` enum (Web ADR 0001 §3.3) | v0 implements interfaces only for concrete, unscoped types, so a generic `TlsTransport<T>` cannot implement `Transport` (found in M0, `tests/probes/`). The cost is one indirect call per I/O operation, measured in M1 |
| Layout `src/core/ src/http1/ …` | **Separate packages** (§4) | Flat namespace per package |
| "Protocol engines must not own the event loop" | Make it stronger: **engines are sans-I/O**. They are pure state machines over bytes (`feed(input) -> events`, `poll_output(buf)`) with no `await` inside, wrapped by thin async drivers | Engines can be fuzzed and tested in the interpreter; TLS/H2/H3 compose the same way; the C TLS/QUIC engines are sans-I/O too |
| M2: "bounded pipelining" | **Sequential pipelining with write coalescing (decided)**: parse ahead into the read buffer (bounded) and handle requests one at a time, in order. While another complete request is already buffered, append the response to the write buffer instead of flushing; flush once per batch | RFC 9112 requires responses in order, so concurrent handling must buffer later responses (unbounded memory, or head-of-line blocking anyway), and streaming/SSE responses cannot be buffered. Concurrent handling also widens the smuggling and desync surface. Browsers don't pipeline, and HTTP/2 is the real multiplexing answer. Coalescing keeps pipelined benchmarks (TechEmpower plaintext, depth 16) at near-full throughput |
| M4: "established audited TLS" | **OpenSSL (decided)**: system OpenSSL ≥ 3.0 LTS for M4, **≥ 3.5 required from M10**, so one provider serves TLS and QUIC (3.5's QUIC TLS API is supported by ngtcp2). Driven sans-I/O through memory BIOs (or `BIO_new_bio_pair`), so OpenSSL never touches a socket. Linked through U8. A small C shim wraps the macro entry points | Most widely deployed and audited; ALPN, SNI, mTLS, tickets, OCSP; matches the platforms' trust tooling |
| M5: "async DNS" | Phase A: `getaddrinfo` on a worker thread (upstream `AsyncDns.resolve`, shielded and abandon-on-cancel). Phase B: a native stub resolver over UDP, after the UDP primitives exist | Unblocks M5 early |
| M10: "established QUIC provider" | **ngtcp2 + nghttp3** (sans-I/O C) with ngtcp2's OpenSSL ≥ 3.5 crypto backend (`ngtcp2_crypto_ossl`), over upstream UDP with `recvmsg`/`sendmsg`, ECN and GSO. Not msquic, which owns its own threads and loop | Keeps the core rule; one TLS provider |
| Dependency graph | Add **U-track → M1, M4, M5, M9, M10** (§5). M4 also depends on M3, because the TLS stream is a `Transport` that the body streams wrap | Upstream lead time |
| Release numbering | Keep CeeSafe.Web v0.x separate, and record the **minimum ceesafec** for each release (policy in §11.6) | CeeSafe is at 0.6.0-dev; the U-track ships in ceesafec 0.6/0.7 |

---

## 4. Repository layout (revised)

```text
CeeSafe.Web/
  packages/
    web_core/        Bytes, OwnedBuffer, PooledBuffer, Deadline, NetError, Transport interface, ConnectionContext
    web_transport/   TcpTransport, MockTransport, PlainStream, Acceptor/Connector, Listener with admission + drain
    web_http/        protocol-neutral semantics: Method, StatusCode, HeaderList<r>, Request<r>, RequestBody, ResponseWriter, Handler, Middleware
    web_http1/       sans-I/O HTTP/1.1 parser and serializer; async driver
    web_sse/         SSE framing + heartbeat
    web_tls/         OpenSSL adapter (links via U8; C shim for macros, c = [...]), TlsTransport, TlsAcceptor/Connector
    web_client/      pool, DNS, redirects, retry policy
    web_ws/          RFC 6455 engine + driver
    web_http2/       HPACK, frames, stream state machine, flow control
    web_proxy/       forwarding headers, PROXY v1/v2, relay
    web_http3/       QPACK, H3 mapping over ngtcp2/nghttp3 (vendored)
    web_server/      Server builder, Router (radix trie), middleware pipeline, graceful shutdown
  tests/
    probes/  unit/  conformance/  fuzz/  interop/  fault/   (each a test package; see §6)
  examples/  hello/ sse-clock/ echo-ws/ proxy/
  benchmarks/  servers/ (cee, axum, actix, hyper, kestrel, h2o) + harness
  docs/  decisions/ architecture/ protocols/
  third_party/  ngtcp2/ nghttp3/  (pinned, license notes, SHA-256; OpenSSL is a system dependency)
  tools/  run_tests.py  sweep.py  bench.py
```

Separate packages are decided (§11.1). Package names must be identifiers, so the manifests use `web_*` (`CeeSafe.Web` is not a valid name). The allowed dependencies are Web ADR 0004 §3.5's table, checked by `tools/check_layers.py`. Protocol packages depend only on `web_core` and `web_http`, so they never reach sockets. No package reaches `web_tls` except `web_http3`, so programs without TLS never link OpenSSL; TLS enters through the `Acceptor`/`Connector` interfaces. There is no facade package, because CeeSafe has no re-exports: applications import `web_server`, `web_http`, and `web_tls` if they use TLS.

---

## 5. Upstream track (in `~/dev/CeeSafe`, spec and ADR first)

The owner does the U-track in CeeSafe (§11.3). Each ADR lands before the milestone that needs it. Each item is an ADR in `spec/decisions/` (next free number after 0043), then spec §40.4 and the runtime, with tests under ASan, UBSan and TSan plus the cancel sweep. The ADRs are batched so each ceesafec release unblocks one CeeSafe.Web milestone.

| ID | Content | Unblocks | Target |
|---|---|---|---|
| **U1 Socket configuration** | `AsyncTcpListener.bindWith(SocketAddress, ListenOptions{ backlog, reusePort, reuseAddr })`; `AsyncTcpStream.setNoDelay/setKeepAlive/setBufferSizes/setLinger`; `peerAddress()`/`localAddress()`; `shutdownRead`. `SocketAddress` enum `V4([u8;4], u16) | V6([u8;16], u16, scope: u32)` with parse and format. IPv6 throughout | M1 | ceesafec 0.6.0 |
| **U2 Error kinds** | `IoError` gains `ConnectionReset`, `ConnectionAborted`, `NotConnected`, `TimedOut`, `HostUnreachable`, `NetworkUnreachable`, `AddressNotAvailable` (spec §26.2 + `ceesafe_io_kind`) | M1 | 0.6.0 |
| **U3 Vectored send** | `ceesafe_async_op_sendv(op, fd, iov, count)`: readiness `writev`/`sendmsg`; IOCP `WSASend` with a WSABUF array; io_uring `IORING_OP_SENDMSG`. `AsyncTcpStream.writeVectored(Slice<Slice<u8>>)` with partial-write semantics. The iov array lives in `AsyncOp` (raise its 192-byte record or add a bounded inline iov, ≤ 16 entries) | M2 | 0.6.0 |
| **U4 Timeouts** | `Async.timeout(ms, body) -> Result<T, E>` (body's error or `TimedOut`), with no allocation, as `race` | M1 | 0.6.0 |
| **U5 DNS (phase A)** | `AsyncDns.resolve(host, port) -> Vector<SocketAddress>` on a worker (`getaddrinfo`/`GetAddrInfoExW`). If cancelled, the result is dropped when the worker finishes. The worker pool is bounded | M5 | 0.6.x |
| **U6 UDP** | `AsyncUdpSocket` with `bind`, `sendTo`, `recvFrom`, `recvMsg` (ECN, local address via `IP_PKTINFO`), `sendMsg` with GSO where available; IOCP `WSARecvMsg`; io_uring | M10, DNS phase B | 0.7.0 |
| **U7 Zero-copy file send** | `AsyncTcpStream.sendFile(File, offset, length)`: `sendfile` (Linux, macOS, BSD), `TransmitFile` (Windows), `IORING_OP_SPLICE`; falls back to read+write | M9 | 0.7.0 |
| **U8 Manifest links** (**required**) | `links = ["ssl", "crypto"]`, include and library search paths, and per-platform tables (`[target.windows] links = ["libssl", "libcrypto"]`, paths for vcpkg or Shining Light builds), since Windows has no pkg-config. A library's links propagate to the final executable's link line. Nothing runs at build time (ADR 0013 D5 holds) | M4 (OpenSSL) | 0.6.x, before M4 |
| **U9 std hash map** (optional) | `HashMap<K, V, A>` with insertion order and fallible ops, as in `src/support` | router and pool scale | 0.7.0 |
| **U10 Generic and scoped impls** (optional, found in M0) | `impl<T> I for S<T>` and impls for types with region parameters (today N0105/N0267, `spec/type-system.md:1133`) | simplifies Web ADRs 0001 §3.3, 0003 §3.3, 0007; not blocking | wanted, not scheduled |

**Rule (from CeeSafe `AGENTS.md`):** never work around a missing primitive with `unsafe` or private C in CeeSafe.Web. The only C in CeeSafe.Web is the OpenSSL shim (macro wrappers and BIO plumbing) and the vendored QUIC engines, each behind a documented adapter contract (ownership, lengths, errors).

---

## 6. Test infrastructure (built in M0)

- **`tools/run_tests.py`**: discovers `tests/**/` packages (each a small executable that prints `ok <name>` or fails) and runs each **in the interpreter** (`ceesafec run --interp`: deterministic virtual clock) and **natively** (`ceesafec build`, under `--cflag -fsanitize=address,undefined` and a TSan run for `runWorkers` tests). Outputs must match between the two. This is a lighter copy of CeeSafe's `tools/run_corpus.py` conventions.
- **Fault sweeps**: reuse CeeSafe's `CEESAFE_FAIL_ALLOC_AT` (fail each allocation in turn) and `tools/cancel_sweep.py` (cancel at each suspension). First check that both accept an arbitrary package; if they do not, contribute a `--package` option upstream. Leak gate: `live=0` at exit (`--check-leaks`).
- **MockTransport**: a scripted `Transport` that delivers input as fragments (every split point for short inputs, random seeded splits for long ones), injects `WouldBlock`, `ConnectionReset` and short writes, and records output. Every engine test runs over it.
- **Oracles** (CeeSafe's evidence discipline: expected values come from outside the code under test): Python `h11`/`h2`/`hpack` for parse and serialise vectors, curl and `nghttp`, `h2spec`, the Autobahn testsuite, `testssl.sh`, `quic-interop-runner`, and the HPACK/QPACK test vectors from the RFCs.
- **Fuzzing**: the sans-I/O engines are built with `--emit c`, and a libFuzzer harness (`LLVMFuzzerTestOneInput` → exported `@export fn fuzz_h1(ptr, len)`) is fuzzed with ASan. Corpora go under `tests/fuzz/<engine>/`.
- **CI**: macOS arm64, Debian 12 x86-64 (GCC 12), Windows 11 (clang + cl), matching CeeSafe's test hosts. One matrix leg with `CEESAFE_REACTOR_URING=1` and one with `CEESAFE_REACTOR_POLL=1`.

---

## 7. Milestones in detail

The roadmap's exit gates are kept; the additions are in bold. Each milestone ends with: `ceesafec check` + `fmt --check` clean in every package, interpreter and native outputs equal, sanitizers clean, and the sweeps green.

### M0 — Contracts and ADRs (≈ 1–2 weeks)
**Tasks**
1. Bootstrap: `ceesafe.toml` per package (`kind = "library"`), `examples/hello` executable, `ceesafec agents` in each package. Toolchain: update `~/.ceesafe/bin/ceesafec` from 0.3.2 to the 0.5.1 release; write `docs/toolchain.md` with the minimum ceesafec per milestone (§11.6).
2. ADRs in `docs/decisions/` (**Web ADRs 0001–0007, accepted 2026-11-02** with every recommendation; they supersede the summaries below where they differ, notably on `dyn Transport` and the hook-style middleware):
   - **ADR-001 Transport ownership.** Buffers are `Slice`/`SliceMut` borrows for one op; `OwnedBuffer`/`PooledBuffer` own them; the socket is owned by its `Transport` (drop closes); cancellation is task cancellation. Partial I/O: `read` returns 0..len with 0 meaning EOF; `write` writes all or errors; `writeVectored` returns the count written. Cites ADR 0035 §3.3–3.4.
   - **ADR-002 TLS provider.** System OpenSSL (≥ 3.0 for M4, ≥ 3.5 from M10), linked through U8, driven sans-I/O over memory BIOs; the C shim's contract (which macros it wraps, ownership, lengths, error mapping from `SSL_get_error`); supported versions and the upgrade policy; how each OS provides it (distro packages, Homebrew, vcpkg).
   - **ADR-003 Request lifetime.** `Request<region conn>`: method, target and header views point into the connection's read buffer, valid until the body is consumed or the handler returns. `request.toOwned()` gives an owned copy for handlers that spawn. The body is a `BodyReader` borrowing the connection, so the connection cannot read the next request while the body is alive (the compiler enforces it).
   - **ADR-004 Protocol boundaries.** Sans-I/O engine shape (`feed`/`next_event`/`write_into`), events enum, the protocol-neutral `web_http` types; how H1, H2 and H3 map to them.
   - **ADR-005 Quality gates.** This plan's §6 and §8.
   - **ADR-006 Upstream dependencies and toolchain.** The U-track, which ceesafec each milestone needs, and the pinning policy of §11.6.
   - **ADR-007 Handler model.** `interface Handler { async fn handle(borrow this, req: borrow mut Request, res: borrow mut ResponseWriter) -> Result<void, HandlerError>; }` with generic composition for middleware (`Logged<H: Handler>`) and `Box<dyn Handler>` only at the router's leaves (closures in fields cannot be called).
3. `web_core`: `OwnedBuffer`, `PooledBuffer` (over `Pool`), `Deadline`, `NetError` (wraps `AsyncError` + protocol reasons as an enum, not strings), `Transport` interface, `ConnectionContext` (id, peer address, created_at, `draining` signal).
4. `web_transport`: `MockTransport`; `TcpTransport` over today's `AsyncTcpStream` (IPv4 only until U1).
5. `tools/run_tests.py`, CI skeleton.

**Exit:** ADRs accepted; MockTransport fragmentation, cancellation (cancel during read/write, frame freed, no loss) and lifetime tests green in interpreter + native + ASan. **Also: `--explain-frames` output for a stub connection task is recorded as the baseline memory per connection.**

### M1 — Transport and connection lifecycle (≈ 2–3 weeks; needs U1, U2, U4)
**Tasks**
1. `Listener`: `bindWith` (backlog, reuse-port), the accept loop in `Async.scopeIn(connectionPool, …)` so that **the pool's capacity is the admission limit**. When the pool is full, apply the configured policy: stop accepting (the kernel backlog absorbs) or accept-and-close with a minimal 503 for H1.
2. Per-connection budget: a fixed read buffer (default 16 KiB, max header size + slack) from a `Pool`; write buffers bounded; an `ConnectionLimits` struct.
3. Deadlines: header-read timeout, idle keep-alive timeout, body-read min-rate (slowloris), write timeout. Each is a `withDeadline` around the op.
4. Graceful drain: `Server.shutdown(grace_ms)` sets `draining` (an `AsyncSignal`), stops the accept loop, lets in-flight requests finish, closes idle keep-alive connections, and after the grace period cancels the scope. Cleanup runs inside `Async.shield`.
5. Multi-core: `Async.runWorkers(n)` + `spawnSend`, one listener with work stealing; measure against N listeners with `SO_REUSEPORT` (U1).

**Exit:** no leaked tasks or sockets (`live=0`, fd count back to baseline) under the alloc sweep, the cancel sweep, and peer RST at every step; overload test (10× pool capacity of connections) shows bounded RSS and bounded latency for admitted connections; shutdown finishes within grace + ε. **Measured: memory per idle connection.**

### M2 — HTTP/1.1 server (≈ 3–4 weeks; needs U3 for the fast path)
**Tasks**
1. `web_http`: `Method` (enum + extension token), `StatusCode`, `HeaderName` (case-insensitive, interned common names), `HeaderMap<r>` (a small vector of views, linear search ≤ 32, then an index), `Request<r>`, `ResponseHead`, `Body` enum {Empty, Full(OwnedBuffer), Stream}.
2. `web_http1` sans-I/O parser: an incremental state machine with limits on request line, header count, header bytes and total head bytes. RFC 9112 strictness: reject bare LF/CR in fields, obs-fold, whitespace before the colon, invalid tokens, `Transfer-Encoding` + `Content-Length` together (400 + close), multiple differing `Content-Length`, TE other than final `chunked`, chunk-size overflow, chunk extensions (bounded), trailers (bounded). Absolute-form / authority-form / asterisk-form targets; `Host` required for 1.1.
3. Serializer: status line + headers into the write buffer; Content-Length or chunked chosen by the body; `Date` header cached once a second; HEAD suppresses the body but keeps the length; 1xx/204/304 have no body.
4. Driver: read → parse → dispatch to the `Handler` → write (vectored head + body through U3) → keep-alive decision (`Connection: close`, HTTP/1.0 rules, an unread body is drained up to a limit or the connection closed). `Expect: 100-continue` sends 100 only when the handler first reads the body. Sequential pipelining with a bounded parse-ahead and write coalescing: while another complete request is already in the read buffer, the response is appended to the write buffer instead of flushed, and the batch is flushed when the buffer is empty of complete requests or the write buffer reaches its limit. A streaming response always flushes before the next request is handled.
5. `web_server`: `Router` (radix trie over path segments, params as views, method matching, 404/405 with `Allow`), `Server` builder, the first example.

**Exit:** RFC 9110/9112 conformance (a vector suite derived from `h11` + hand-written smuggling cases: CL.TE, TE.CL, TE.TE obfuscations, duplicate CL, space-before-colon, NUL bytes, oversized chunk sizes); **parser fuzzed 24 h with no crash or leak**; pipelined order test (100 requests in one write); **coalescing test: 16 pipelined requests produce one flush, and a streaming response in the middle is flushed before the next request is handled**; interop with curl, `wrk`, `h2load --h1`.

### M3 — Streaming and SSE (≈ 2 weeks)
**Tasks**
1. `BodyReader` (single consumer, borrows the connection; `read(buf)`, `readToEnd(limit)`), and `BodyWriter`: `write` awaits the socket, so backpressure comes from the socket with no internal queue; `flush`; `finish(trailers?)`.
2. Chunked response writer; a streaming request body with an upload limit and a min-rate deadline.
3. `web_sse`: event framing (`id`, `event`, `data` multi-line, `retry`), heartbeat comments through `Async.race(next_event, sleep(hb))`, `Last-Event-ID`; a producer → consumer `Channel<Event>` with **bounded capacity and a configured overflow policy** (block the producer / drop the oldest / disconnect).
4. Disconnect detection: a read-side EOF watcher raced with the stream, so a closed client cancels the producer.

**Exit:** slow client test (reader at 1 KB/s, producer at 100 MB/s) keeps the server's RSS flat; client disconnect frees the producer task and buffers within 1 heartbeat; cancel sweep over the SSE example. **Release v0.1** (M0–M3).

### M4 — TLS (≈ 3–4 weeks; parallel with M2–M3 after M1; needs U8)
**Tasks**
1. `web_tls/ceesafe.toml` links `ssl` and `crypto` through U8, with Windows entries. CI installs OpenSSL on each host (Debian `libssl-dev`, Homebrew `openssl@3`, vcpkg on Windows) and records the exact version. The shim fails the build below 3.0 (`OPENSSL_VERSION_NUMBER`). Configuration: TLS 1.2 (ECDHE + AEAD only) and 1.3, `SSL_OP_NO_RENEGOTIATION`, no compression, no CBC suites, `SSL_MODE_ENABLE_PARTIAL_WRITE` off, `SSL_MODE_RELEASE_BUFFERS` on.
2. A C shim `web_tls/c/tls_shim.c`: an opaque context holding `SSL*` and two memory BIOs (`BIO_s_mem`, or one `BIO_new_bio_pair` with bounded sizes); `tls_feed(ctx, in, len)` writes ciphertext into the read BIO; `tls_take_output(ctx, out, cap)` drains the write BIO; `tls_read_plain`, `tls_write_plain` and `tls_handshake_step` map `SSL_get_error` to one enum (want-read, want-write, closed, error with the `ERR_get_error` code). It also wraps the macro entry points (`SSL_set_tlsext_host_name`, `SSL_CTX_set_min_proto_version`, `SSL_CTX_set_max_proto_version`, the ticket key callback) as plain functions. OpenSSL never does I/O and never keeps a CeeSafe pointer after a call returns. The contract is in ADR-002. Error strings are copied into a caller buffer, never retained.
3. `TlsTransport` (over a concrete `PlainStream`, Web ADR 0001 §3.3) implements `Transport`, and `TlsAcceptor`/`TlsConnector` produce it: the handshake under a deadline and a bound on the bytes it may consume; `close_notify`; ALPN (`h2`, `http/1.1`); an SNI callback to a `CertResolver` interface; mTLS (`require`/`optional`, a chain verified against a configured CA store, failing closed); session tickets with key rotation; certificate hot reload (an atomic swap of an `Arc<CertSet>`).
4. Client side for M5: hostname verification through `X509_VERIFY_PARAM_set1_host` with `X509_CHECK_FLAG_NEVER_CHECK_SUBJECT` (SAN only, no CN fallback); trust store from `SSL_CTX_set_default_verify_paths`, plus the Windows certificate store (imported through the shim) and the macOS keychain bundle.

**Exit:** interop with OpenSSL `s_client`/`s_server`, curl, Go `crypto/tls`, and `testssl.sh` with no weak suites; negative tests (expired, wrong host, unknown CA, bad signature, truncated handshake, oversized records) all fail closed; a handshake flood stays within its limits; ASan over the shim.

### M5 — HTTP client (≈ 3 weeks; needs U1, U5)
**Tasks:** connection pool per (scheme, host, port, tls-config) with max idle, max per origin and idle timeout; Happy Eyeballs v2 (RFC 8305) through `Async.race` over staggered connects; redirects (limit, strip auth across origins, 303 → GET); request and response streaming; timeouts (connect, TLS, first byte, total); `RetryPolicy` is explicit: retry is **only** for idempotent methods or requests carrying an `Idempotency-Key`, and only when no byte of the request was written, or the error is a connection reuse race; budgets per origin.

**Exit:** no replay of a POST after a partial write (fault test at every write split); DNS failure, timeouts, a pool shut down with requests in flight, and a server close during reuse are all deterministic. **Release v0.2.**

### M6 — WebSockets (≈ 2–3 weeks)
**Tasks:** upgrade over H1 (`Sec-WebSocket-Key`/`Accept`, version 13, subprotocols); a sans-I/O frame codec (masking required client→server, rejected server→client; 7/16/64-bit lengths with a cap; control frames ≤ 125 bytes and never fragmented; UTF-8 validation of text, incremental across fragments); message assembly with a limit; ping/pong auto-reply and keep-alive; the close handshake with codes and a timeout; bounded outbound `Channel`; `permessage-deflate` last and optional (needs a zlib choice: vendored miniz or a native inflate).

**Exit:** Autobahn fuzzingclient all-pass (or a documented "non-strict"), malformed-frame and slow-consumer stress tests pass.

### M7 — HTTP/2 (≈ 5–6 weeks)
**Tasks:** frame codec; HPACK (static table, a dynamic table with a size limit from SETTINGS, Huffman, every RFC 7541 Appendix C vector); a stream state machine per RFC 9113 §5.1; connection and stream flow control with WINDOW_UPDATE policy; a concurrent stream limit; CONTINUATION limits (**CVE-2024-27316-class flood guard**); a rapid-reset guard (**CVE-2023-44487**: a budget on RST_STREAM per unit of time); a SETTINGS/PING flood guard; GOAWAY drain that fits M1's shutdown. Design: one connection task owns the engine; each stream is a child task in the connection's scope. Streams talk to the connection through bounded channels; the response body's backpressure comes from the flow-control window.

**Exit:** `h2spec` 100 %, HPACK vectors, flow-control and cancel stress tests (cancel a stream mid-body; cancel the connection with 100 streams), `h2load` interop. **Release v0.3.**

### M8 — Reverse proxy (≈ 3–4 weeks)
**Tasks:** upstream pools (M5 client); active and passive health checks; load-balancing policies (round robin, least-requests, consistent hash); hop-by-hop header removal (RFC 9110 §7.6.1, including the headers named in `Connection`); `Forwarded`/`X-Forwarded-*` produced and parsed with a **trusted-proxies CIDR list** (the right-most untrusted address is the client); PROXY protocol v1/v2 on the listener (trusted sources only, with a deadline); streaming relay with backpressure in both directions; a retry budget (a fraction of requests), and a retry only before the first upstream response byte; deterministic 502 vs 504; upgrades (WebSocket) relayed as opaque tunnels.

**Exit:** a spoofed `X-Forwarded-For` from an untrusted peer is ignored; ambiguous framing is rejected on both sides; a partial upstream response gives a defined outcome (an aborted client connection, never a truncated message presented as complete).

### M9 — Low-level I/O (≈ 2–3 weeks; needs U3, U7)
**Tasks:** first profile (`benchmarks/`, perf/Instruments, allocation counts from CeeSafe's `allocount.c`); pooled buffers sized by profile; a vectored write fast path everywhere; `sendFile` for static files without TLS (with TLS: read + encrypt, or kTLS later as its own ADR); a static-file handler with `ETag`/`If-None-Match`/Range; evaluate io_uring as the Linux default with the numbers. **Release v0.4.**

### M10 — HTTP/3 and QUIC (≈ 6–8 weeks; needs U6; may start after M3 + M4)
**Tasks:** raise the OpenSSL minimum to 3.5 (ADR-002); vendor ngtcp2 + nghttp3 with ngtcp2's OpenSSL crypto backend (`ngtcp2_crypto_ossl`); a sans-I/O QUIC adapter over `AsyncUdpSocket` (U6) with GSO/ECN; connection IDs and routing; a stateless-reset key; Retry tokens for address validation; H3 stream mapping into `web_http`; QPACK with a blocked-streams limit; 0-RTT accepted **only** for safe methods, with replay protection noted in the request; `Alt-Svc` advertising from the H1/H2 listeners; migration policy; GOAWAY/drain.

**Exit:** `quic-interop-runner` handshake/transfer/retry/resumption/zerortt/loss cases pass; packet loss and reordering (netem) tests; QPACK blocked-stream limits; 0-RTT safety tests. **Release v0.5.**

### v1.0 hardening
An external audit of the parsers + TLS/QUIC adapters; a continuous fuzz farm; API freeze through `ceesafec doc` diffs; portability (Linux, macOS, Windows, FreeBSD); the benchmark report published with full environment details.

---

## 8. Benchmark plan (from M1)

- `benchmarks/servers/`: equivalent plaintext, JSON and streaming handlers in CeeSafe.Web, Axum, Actix Web, Hyper, Kestrel and h2o, built release-mode with pinned versions.
- Load: `wrk2` (constant rate, so latency is correct under coordinated omission), `h2load` (H1, H2, H3), `oha` for TLS handshakes, the Autobahn perf cases for WebSocket.
- Report P50/P95/P99/P99.9, req/s at a fixed P99 SLO, CPU, allocations, RSS, and **bytes per idle connection** (CeeSafe's compile-time frame size makes this a strong claim). Record the hardware, kernel, compiler, flags and reactor (`epoll`/`uring`/`kqueue`/`IOCP`).
- A gate per release: no regression > 5 % against the previous CeeSafe.Web release on the same machine. Correctness tests always run before benchmarks.

---

## 9. Schedule (indicative, one to two engineers)

```text
Weeks  1-2   M0                         | U1, U2, U4 ADRs + implementation (CeeSafe)
Weeks  3-5   M1                         | U3 (vectored send)
Weeks  6-9   M2                         | M4 starts (TLS shim)            | U5 (DNS)
Weeks 10-11  M3  -> v0.1                | M4 continues                    | U8 (manifest links) by week 9
Weeks 12-14  M5  -> v0.2 (with M4)      | U6 (UDP), U7 (sendfile)
Weeks 15-17  M6                         | M10 spike (ngtcp2 adapter)
Weeks 18-23  M7  -> v0.3
Weeks 24-27  M8, M9 -> v0.4
Weeks 28-35  M10 -> v0.5
```

## 10. Risks

| Risk | Mitigation |
|---|---|
| Upstream ADR lead time stalls milestones | Start U1–U4 in week 1; M0 and the engines need no new primitives (they are sans-I/O plus MockTransport) |
| The interpreter runs no C, so TLS/QUIC miss deterministic interpreter runs | Engines over a `NullTls` transport in the interpreter; native-only suites for the shims, under ASan |
| Region-parameterised `Request<r>` gets awkward across `await` and `spawnSend` | ADR-003 offers `toOwned()`; the spike in M0 tries a real handler with streaming before committing |
| The `AsyncOp` 192-byte record is too small for vectored and msg ops | U3 resizes it under its own size assertion (ADR 0035 §4) |
| H2/H3 flood CVEs | Guards are acceptance criteria, not follow-ups (M7, M10) |
| OpenSSL versions differ across hosts (distro 3.0, Homebrew 3.x, vcpkg), and Windows has no standard install | The shim checks the version at build time; CI records the exact version per host; ADR-002 names the supported range; U8 gives Windows explicit paths |
| The C shim grows with every OpenSSL macro it wraps | Each wrapper is a one-line function listed in ADR-002's contract; ASan + fuzzing cover the shim; review against "C only behind a documented contract" |
| U8 slips and blocks M4 | M4 tasks 2–3 can be developed with `--cflag -lssl -lcrypto` on the command line until U8 ships; merging to main waits for U8 |

## 11. Decisions (recorded 2026-11-02, by the owner)

1. **Package split: separate `web_*` library packages** (§4), with path dependencies and a CI check of the allowed dependency DAG.
2. **TLS provider: system OpenSSL**, ≥ 3.0 LTS for M4 and ≥ 3.5 from M10 (one provider for TLS and QUIC). Consequences: U8 is required before M4; a C shim wraps OpenSSL's macro entry points; sans-I/O over memory BIOs (§3, M4).
3. **Upstream: the owner does U1–U9 in CeeSafe** as ADRs, each landing before the milestone that needs it (§5).
4. **`CancellationToken`, `ReadLease` and `WriteLease` are dropped** in favour of the language's mechanisms. Kept: `OwnedBuffer`, `PooledBuffer`, `Deadline` + `withDeadline`. Cancellation from outside the task tree: `Async.race(body, signal.wait())` (§3).
5. **Pipelining: sequential, with bounded parse-ahead and write coalescing** (§3, M2).
6. **Toolchain: released versions only.**
   - M0: ceesafec 0.5.1; update `~/.ceesafe/bin/ceesafec` (currently 0.3.2).
   - M1 onward: the release that ships U1, U2 and U4 (expected 0.6.0). Each milestone records its minimum in `docs/toolchain.md`.
   - CI: the main job uses the released binary; a non-blocking job builds against CeeSafe main.
   - While developing U items, `~/dev/CeeSafe/out/ceesafec` is used locally on a branch. CeeSafe.Web code that needs an unreleased compiler is not merged to main.
7. **Connections get tasks through connection workers (recorded 2026-11-02)**, for M1–M3: N workers each loop accept → serve on one shared source, so N is the admission limit (`docs/milestones/M0.md`, option A). Spawning into a running scope (U11) is proposed to CeeSafe before M7, where per-stream tasks matter most. M1's task 1 changes accordingly: admission is the worker count, not `Async.scopeIn`'s pool (`docs/milestones/M1.md`).
8. **The first release is v0.2.0, of main with M0–M6 (recorded 2026-11-03).** By the time M2's 24-hour fuzz run ended, main also held M4–M6, so v0.1 (M0–M3) and v0.2 (M5) are released together as v0.2.0: an annotated tag and a GitHub release, with the library packages at version 0.2.0. Later releases keep §7's numbering: v0.3 with M7.
9. **v0.3.0 is main with M7 (recorded 2026-11-03).** The owner, on M7's gate (`docs/milestones/M7.md`): merge `m7-http2` to main and release it as §7 says, an annotated tag and a GitHub release, with the library packages at 0.3.0. Its minimum compiler is ceesafec 0.10.3 (CeeSafe.Web's B16 and B17, `docs/toolchain.md`).
10. **v0.3.1 is v0.3.0 with one HTTP/2 fix (recorded 2026-11-04).** The owner: release M8's `9f0e5ee` (DATA for a stream the client reset is STREAM_CLOSED, not INTERNAL_ERROR) from main as a patch, with the library packages at 0.3.1, and correct v0.3.0's release notes, whose h2spec 145/145 was a single run (`docs/milestones/M7.md`). The minimum compiler stays ceesafec 0.10.3.
11. **v0.4.0 is main with M8 and M9 (recorded 2026-11-08).** The owner: merge `m9-io` to main and release it as §7 says, an annotated tag and a GitHub release, with the library packages at 0.4.0 (`web_static` new). Its minimum compiler is ceesafec 0.22.0 (CeeSafe ADR 0078: B19 and U22, `docs/toolchain.md`).
