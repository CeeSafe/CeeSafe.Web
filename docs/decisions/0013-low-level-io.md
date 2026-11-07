# Web ADR 0013 — Low-level I/O: profiling, buffers, vectored writes, sending files, static files

**Status:** accepted 2026-11-06 (M9). The owner accepted each recommendation in §6:
- U22 (an open file's modification time and kind) in CeeSafe;
- a new `web_static` package;
- symbolic links under the root followed, with lexical path rules;
- several ranges in one request ignored (200, the whole file);
- a strong `ETag` from the size and the modification time in nanoseconds;
- io_uring measured, and recommended to CeeSafe, not overridden here.
**Scope:** implementation plan §7 M9. It covers:
- a first profile, and buffers pooled by what it shows;
- a vectored write fast path;
- `sendFile` for static files without TLS, and read + encrypt with it;
- a static-file handler with `ETag`, `If-None-Match` and Range;
- io_uring as the Linux default, evaluated with numbers.

kTLS is left for its own ADR, as the plan says.

## 1. Context

What M0–M8 provide (checked 2026-11-06 on `main` at `10fc4e6`, ceesafec 0.11.0):

- **Sending a file is in lib/std (U7, CeeSafe ADR 0051, released in 0.9.0).**
  - `AsyncTcpStream.sendFile(file, offset, length)` is one step, and `sendFileAll` loops.
  - Each platform uses its own call: `sendfile` on Linux and macOS, and `TransmitFile` on IOCP.
  - Where the kernel refuses, lib/std falls back to a bounded copy: 64 KiB reads with `pread` semantics on a worker
    thread. This happens on WSAPoll, for a file that is not regular, and on `EINVAL` or `ENOSYS`.
  - The offset is explicit and the file's position never moves, so one `File` can be sent on many streams at once.
- **`Transport` has no file operation** (`web_core/src/transport.cee:18`). It has `read`, `write`, `writeVectored`
  and `shutdownWrite`, plus the addresses, ALPN and peer identity.
  - `PlainStream` wraps an `AsyncTcpStream`, and `web_tls`'s stream wraps a `PlainStream`.
  - Interfaces have no default methods, so a new method is written in every implementation. In the packages there
    are five: `PlainStream`, `TcpTransport`, `TlsTransport`, `MockTransport` and `web_http`'s `NoTransport`. There
    are also seven test doubles in tests and the HTTP/1.1 fuzzer.
- **Vectored writes exist**:
  - in `web_http`'s HTTP/1.1 head and body (`exchange.cee`, `stream.cee`);
  - in the client and WebSocket (`web_client`);
  - in TLS, which gathers into one record (`web_tls/src/transport.cee`);
  - in the plain stream, through lib/std's `IoParts` (U3, CeeSafe ADR 0045).

  HTTP/2 is not on the list: it builds each frame's header and payload into one buffer.
  Whether the remaining copies matter is for the profile to say (§3.1).
- **`BufferPool` exists and nothing uses it** (`web_core/src/buffer.cee:114`). It keeps up to `maxIdle` buffers of
  one size. Each connection allocates its own read and write buffers.
- **The file API** (CeeSafe spec `type-system.md` §26.3, §26.8, §26.10; checked again with 0.19.1, 2026-11-06):
  - `File.open` and `read`, which reads sequentially;
  - since 0.19.0 (CeeSafe ADR 0075 §2.1, §2.2), `f.readAt(buffer, offset)` and `f.length()`, which take
    `borrow this` (0.19.1) and do not move the position, with `AsyncFiles` versions on a worker thread;
  - `File.kind(path)`, which follows symbolic links;
  - `Directory.list`.

  So the size and a positioned read exist. A static-file handler still lacks:
  - **the modification time**, needed for `ETag` and `Last-Modified`;
  - **the kind of an open file.** `File.kind` takes a path, and opening a FIFO to check it blocks until a writer
    comes.

  Neither can be written in CeeSafe.Web without C, so they go upstream (§3.4, U22).
- **Measuring.**
  - `wrk`, `h2load` and `ab` are installed on this Mac, and Instruments is available. The Debian host has `perf`.
  - `CEESAFE_ALLOC_REPORT=1` prints a program's allocation counts (CeeSafe's `allocount.c`).
  - `benchmarks/overload` is M1's admission gate, not a throughput benchmark. There is no request-rate benchmark yet.
- **Reactors.** Linux uses epoll unless `CEESAFE_REACTOR_URING=1`, and Windows uses IOCP unless
  `CEESAFE_REACTOR_POLL=1`.
  - M8 ran its gate under both Linux reactors, and found and fixed one io_uring bug (`92f23fb`).
  - Which reactor is the default is CeeSafe's runtime decision, not CeeSafe.Web's.

## 2. Options

- **Profile, then change; or change by expectation.** The plan says profile first. Pools and fast paths added
  without numbers would be complexity that might buy nothing.
- **Where `sendFile` lives.**
  - **(a) A `Transport` method.** Each transport sends a file its own way: plain does it without copying; TLS
    reads into a buffer and encrypts; the mock copies.
  - **(b) A downcast in the HTTP/1.1 driver** to `PlainStream`. CeeSafe has no downcast, and it would lose the
    wrapper.
  - **(c) Only in a static-file handler that owns the socket.** Handlers never own the socket.

  (a) is the only one that fits Web ADR 0004.
- **HTTP/2 and files.** DATA frames need a frame header before each piece, and flow control decides the pieces, so
  the kernel cannot send the file straight to the socket. HTTP/2 always reads into a buffer and frames it. HTTP/3 is
  the same.
- **A static-file handler's package.**
  - In `web_server`, which would grow a file system dependency that most programs do not need.
  - In a new `web_static`, on `web_core`, `web_http` and `web_server`.
- **Several ranges in one request.**
  - Answer them as `multipart/byteranges`, as nginx and Go do.
  - Or ignore Range and send the whole file, which RFC 9110 §14.2 allows.
- **Symbolic links under the root.**
  - Follow them, like nginx's default (`disable_symlinks off`).
  - Or refuse any path whose resolution leaves the root. That needs a canonical path, a second upstream function.

## 3. Decision (recommended)

### 3.1 Stage 1: a profile, before anything changes

- **`benchmarks/http`**, a server and a script that record the following, each over HTTP/1.1, h2c and TLS:
  - requests a second and latency (p50, p99, p99.9) with `wrk` and `h2load`, at 1, 10 and 100 connections, each
    with keep-alive;
  - `CEESAFE_ALLOC_REPORT`'s allocations per request (the count at the end, less the count after warm-up, divided
    by the requests);
  - peak RSS;
  - a CPU profile: Instruments' Time Profiler on macOS and `perf record -g` on Debian, giving the top 20 frames.

  Its routes are a fixed 13-byte body, a 4 KiB body, a 64 KiB body, a streamed 1 MiB body and a POST echo.
- **The baseline is recorded before stage 2 starts** (`docs/milestones/M9.md`), with the tree, the compiler, the
  machine and each command.
- **nginx on the same machine is the reference** (its version recorded with the numbers): the same routes, one worker per core, `sendfile on`. It is
  reported next to our numbers, not used as a target.

### 3.2 Stage 2: buffers and vectored writes, where the profile points

- **Buffers come from a pool per connection worker** (Web ADR 0005: each worker serves one connection at a time). A
  pool per worker has no lock and is reused between connections.
  - The buffer size comes from the profile: the size that serves most requests without growing.
  - A buffer that grew is not returned.
  - `maxIdle` is per worker.
- **Vectored writes everywhere a header precedes a payload**, where the profile shows the copy:
  - HTTP/2 frames (the 9-byte header and the payload);
  - chunked framing (the size line, the data and CRLF);
  - WebSocket frames.
- **Each change is kept only if it moves a number** in the profile, and is recorded with both numbers. A change
  that does not is reverted.
- The sweeps cover every pool's allocation failure: a failed `take` makes a buffer the old way.

### 3.3 Stage 3: `Transport.sendFile`, and `Exchange.sendFile`

- **`Transport` gains:**

  ```ceesafe
  /// Sends `length` bytes of `file` from `offset`, without moving its position; all of them, or an error. A file
  /// that ends first is `Err(Io(InvalidInput))`, its bytes before the end sent.
  async fn sendFile(borrow mut this, file: borrow File, offset: u64, length: u64) -> Result<void, NetError>;
  ```

  Each implementation:
  - **`PlainStream` and `TcpTransport`:** `AsyncTcpStream.sendFileAll`. It copies nothing on Linux, macOS and
    Windows IOCP, and falls back to lib/std's bounded copy where the kernel refuses.
  - **`web_tls`:** positioned reads (`AsyncFiles.readAt`) of up to one TLS record (16 KiB) into a pooled buffer,
    each encrypted and written.
  - **`NoTransport`:** fails, as its `write` does.
  - **`MockTransport` and the test doubles:** copy. The mock counts the calls, so that tests can see which path was
    taken.
- **`Exchange.sendFile(file, offset, length)`** is a body write. It works like `write`: the head goes first if it has
  not, the declared length is checked, and the response is not complete until all of it is sent.
  - **HTTP/1.1** calls `Transport.sendFile` after flushing what is buffered.
  - **A chunked response** frames it as one chunk: the size line, the file, then CRLF.
  - **HTTP/2** reads pieces of up to the flow-control window, at most 16 KiB each, into a pooled buffer, and sends
    them as DATA.
- **A file that changes while it is sent is never reported as complete.**
  - A file that ends early fails the send.
  - The connection is aborted, like M8's short upstream body: the HTTP/1.1 connection closes, and HTTP/2 sends
    RST_STREAM.
  - A file that grows is sent to the declared length only.

### 3.4 Upstream: U22, a file's modification time and kind

`readAt` and `length` arrived in 0.19.0 (CeeSafe ADR 0075), so U22 is what remains. It is a new entry in
`docs/ceesafe-upstream-work.md`, for the owner to do in CeeSafe (implementation plan §11 item 3):

```ceesafe
// File (type-system.md §26.3), from the open file: fstat / GetFileInformationByHandleEx
public fn modified(borrow this) -> Result<UnixTime, IoError>;   // §26.11's type; nanoseconds where kept
public fn kind(borrow this) -> Result<PathKind, IoError>;
// File.openFile(path): open for reading without blocking on a FIFO (O_NONBLOCK, cleared after open), or refuse a
// path that is not a regular file. Either form is enough.
// AsyncFiles.modified and AsyncFiles.kind: the same, on a worker thread
```

- **Both read the open file** (`fstat`), not the path. So the time and kind describe the file that is sent, not one
  renamed into its place in between.
- **Stages 1 to 3 do not need U22.** Stage 3 uses `readAt`. Stage 4 needs U22 for its validators, and to refuse
  FIFOs and devices safely.

### 3.5 Stage 4: `web_static`

A new package on `web_core`, `web_http` and `web_server` (Web ADR 0004 §3 gains the row).
`StaticFiles.create(root, options)` is a handler for GET and HEAD:

- **The path:**
  - The request's path is percent-decoded once.
  - It is refused with 404, never 400, so that probing learns nothing, if it contains any of the following:
    - a zero byte;
    - a backslash;
    - a segment that is `.` or `..`;
    - a segment that is empty, except a trailing one;
    - on Windows, a drive letter or `:`.
  - It is then joined to `root`. The check is lexical: no request path names a file outside `root` except through a
    symbolic link already under it (§6.3).
- **What is served:**
  - A regular file is served. A directory is served as its `index.html` if `options.index` is set (it is by
    default); otherwise it is 404. There is no listing.
  - Anything else is 404: a device, a socket or a FIFO.
  - The file is opened without blocking, then its kind, length and time are read from the open file (U22).
- **Validators** (RFC 9110 §8.8):
  - The `ETag` is strong: `"<size in hex>-<modification time in nanoseconds, in hex>"`, the same idea as nginx's.
  - `Last-Modified` is the modification time, in IMF-fixdate.
- **Conditionals** (RFC 9110 §13.2.2's order):
  - `If-Match` failing gives 412.
  - `If-Unmodified-Since` applies only without `If-Match`.
  - `If-None-Match` matching gives 304 for GET and HEAD, with the `ETag` and no body. A weak comparison is used.
  - `If-Modified-Since` applies only without `If-None-Match`.
  - `If-Range`, then Range.
- **Range** (RFC 9110 §14):
  - **One range of `bytes`:** 206 with `Content-Range: bytes a-b/size`. The forms are `a-b`, `a-` and `-n`; an
    end past the file is clipped.
  - **Unsatisfiable:** 416 with `Content-Range: bytes */size`.
  - **Several ranges, a unit other than bytes, or a malformed header:** the Range is ignored, and the answer is 200
    with the whole file (§6.4).
  - **`If-Range`:** the range is used only on a strong match with the `ETag`, or on an exact match with
    `Last-Modified`.
  - `Accept-Ranges: bytes` is on every 200 and 206.
- **`Content-Type`** comes from a small table of extensions, about 40 of them, taken from IANA's and nginx's
  `mime.types`. Text types get `; charset=utf-8`. Anything else is `application/octet-stream`, and the
  `nosniff` header is added.
- **HEAD** gets the same fields as GET and no body.
- **The body:** `Exchange.sendFile`. Without TLS over HTTP/1.1 nothing is copied.
- **Not in M9:** directory listings, precompressed files (`.gz`, `.br`), `Cache-Control` policies beyond an
  `options.cacheControl` string, and `multipart/byteranges`.

### 3.6 Stage 5: io_uring, measured

- `benchmarks/http` runs on the Debian host under epoll and under io_uring with the same tree. The routes add a
  1 GiB file, sent with and without TLS.
- **The numbers go to the owner as a recommendation for CeeSafe's runtime.** The default is CeeSafe's to change, and
  CeeSafe.Web does not override it.
- If io_uring is better, the recommendation names which checks must pass first: M8's gate and Autobahn under
  io_uring, which already pass, and this milestone's.

## 4. Consequences

- M9's speed claims come with the numbers behind them: before and after, with the machine, the tree and the
  commands.
- **`Transport` grows a method**, so every implementation must add it: five in the packages, and seven test doubles.
  None is known outside this repo.
- **Static files without TLS over HTTP/1.1 cost no user-space copy.** With TLS or over HTTP/2 they cost one read
  into a pooled buffer, as in other servers.
- **`web_static` waits for U22.** Stages 1 to 3 do not, and run first.
- **The path rules are lexical**, so a symbolic link under the root can point outside it (§6.3). This is
  documented in `web_static`'s README, as nginx documents it.

## 5. Plan and tests (the M9 exit gate)

Stages, each committed and checked:
1. the profile;
2. pooled buffers and vectored writes;
3. `sendFile` over every transport and protocol;
4. `web_static`;
5. io_uring;
6. the gate.

Tests, with oracles outside the code:
- **Profile:**
  - the baseline and each stage's numbers, recorded with their commands;
  - each change of stage 2 kept only with a number that moved;
  - allocations per request before and after.
- **`sendFile`:**
  - **Zero-copy:** with `MockTransport`'s count, the HTTP/1.1 driver over a plain stream takes the `sendFile` path,
    and on Linux `strace -e trace=sendfile` shows the call. On macOS, `dtruss` where SIP allows, otherwise the counter.
  - **Every path:** a 1 GiB file over HTTP/1.1, TLS, h2c and h2, matching by SHA-256 at both ends. On Windows over
    IOCP (`TransmitFile`) and WSAPoll (the copy).
  - **Lengths and offsets:** past 4 GiB on Linux and macOS (a sparse file).
  - **A file truncated while it is sent:** the client sees an aborted transfer (curl 18 or 92), never success.
- **`web_static`, against RFC 9110's text, not its own output:**
  - each rule of §13.2.2's order, with 200, 206, 304, 412 and 416;
  - Range's forms (§14.1.2's examples: `bytes=0-499`, `500-999`, `-500`, `9500-`), clipped ends, several ranges
    ignored, and `If-Range` with a strong and a weak validator;
  - HEAD's fields equal to GET's;
  - traversal: `..`, `%2e%2e`, `%2f`, `%00`, `\`, `//`, and on Windows `C:` and `..\`, each 404, with a file outside
    the root that must never be read;
  - a FIFO and a directory without `index.html` give 404;
  - curl's `--range`, and `wrk` and `h2load` on a 64 KiB and a 1 MiB file, compared with nginx's numbers.
- **A fuzz target over the path rules and the Range parser.** Its oracle checks that the result is a file under the
  root, or a refusal, and that a range is within the file. It runs 24 CPU-hours (Web ADR 0005 §2.2).
- **Sweeps** over `web_static` and the changed packages, every package on Debian and Windows, and M8's gate again.
- **io_uring:** the numbers, and M8's gate under it.

## 6. Questions for the owner (answered 2026-11-06: each as recommended)

1. **U22 in CeeSafe?** Recommended: **yes.** It needs an open file's modification time and kind, and a way to
   open a path without blocking on a FIFO, with `AsyncFiles` versions. 0.19.0 already brought `readAt` and
   `length`. Without U22 there are no validators (`ETag`, `Last-Modified`, 304, `If-Range`). Stages 1 to 3 go first
   meanwhile.
2. **A new `web_static` package?** Recommended: **yes**, so that a server without static files does not depend on
   file code. The alternative is a module in `web_server`.
3. **Symbolic links under the root?** Recommended: **followed, with lexical path rules**, as nginx does by default.
   The files under the root are the operator's. Refusing links that leave the root needs `realpath`, a second
   upstream function, and still races a link changed after the check. An option for it can come later with its own
   upstream item.
4. **Several ranges in one request?** Recommended: **ignored**, answered 200 with the whole file (RFC 9110 §14.2
   allows it). `multipart/byteranges` is rarely used by clients. Many overlapping ranges were the "Apache Killer"
   denial of service (CVE-2011-3192), and its guard is code that is easier not to have.
5. **The `ETag`'s form?** Recommended: **strong, from the size and the modification time in nanoseconds**, so that
   `If-Range` works. A file rewritten within the same nanosecond with the same size would keep its `ETag`. File
   systems with coarse timestamps make that likelier; the README says so.
6. **io_uring as the Linux default?** Recommended: **measure, and send the numbers to CeeSafe as a
   recommendation**. CeeSafe.Web does not override the runtime's default.

## 7. Amendment 1 (2026-11-07, M9 stage 3: `sendFile` as built)

Three details of §3.3 changed in the building; the design is otherwise as written.

1. **The file is `borrow mut File`, not `borrow File`.** In 0.19.1, `File.readAt` takes `borrow this`, but
   `AsyncFiles.readAt` and `AsyncFiles.length` take `borrow mut File` (lib/std `async.cee:978`, `:986`; why is
   CeeSafe's to say, and a `borrow File` version would be an upstream request, not needed now). TLS and HTTP/2 read the file that way, so `Transport.sendFile` and
   `Exchange.sendFile` take `borrow mut File`. A plain stream passes it on to lib/std's `sendFileAll`, which takes
   `borrow File`. The cost: one open `File` cannot be sent on two streams at once; each request opens its own, as a
   static-file handler does anyway.
2. **1 MiB slices, each within the write timeout.** M1's write timeout bounds each write (`writeTimeoutMs`). One
   deadline over a 1 GiB file would end slow but honest downloads, and none would leave a stalled client holding the
   connection, so the file goes in slices of 1 MiB, each under its own deadline (`Transports.sendFileWithin`): a
   client must take 1 MiB per `writeTimeoutMs` (30 s by default). A chunked response sends each slice as its own
   chunk, not the whole file as one.
3. **The copying transports share one helper.** Interfaces have no default methods, so `Transports.copyFile` (16 KiB
   positioned reads, each written) is what `TlsTransport`, `MockTransport` and the test doubles call. TLS's piece
   is a stack buffer in the task's frame, not a pooled one: the pool was not needed once stage 2's copies were gone.

A failed send marks the exchange done and not to be kept, so a handler that ignores the error still never completes
the response: no last chunk over HTTP/1.1, no END_STREAM over HTTP/2 (tests `*_when_the_handler_ignores_it` in
`packages/web_server/tests/sendfile.cee`).
