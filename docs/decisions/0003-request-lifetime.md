# Web ADR 0003 — Request lifetime: headers and body across `await` and streaming

- Status: **Accepted** by the owner, 2026-11-02 (every question of §6 as recommended).
- Scope: where a request's method, target and headers live; how long views into them are valid; how a handler reads
  the body; how a handler keeps request data beyond its call. Milestones M2–M3 (HTTP/1.1, streaming); the same model
  serves HTTP/2 and HTTP/3 (Web ADR 0004).

## 1. Context

- The roadmap's rule: "Request headers/body views must not outlive backing storage."
- CeeSafe expresses a view stored in a struct with a **region parameter**: `struct Request<region r> { target:
  StringView<r>; … }`. Values of the type cannot outlive the region (`guide/ownership.md:264`). Frames never move, so a
  view can be held across `await` (Web ADR 0001 §1).
- Found while drafting (2026-11-02, `tests/probes/`):
  - a method cannot declare its own region parameter (N0216, `method-region-fail.cee`); **elided** regions in
    method and interface signatures work (`borrow Request`, `request-lifetime.cee`, `handler-hooks.cee`);
  - a type with a region parameter cannot implement an interface (N0267, `impl-scoped-fail.cee`), so a request
    body that borrows its connection cannot be a `dyn Body`;
  - writing the connection buffer while a request still borrows it is rejected (N0240,
    `request-lifetime-fail.cee`). The compiler enforces the roadmap's rule.

## 2. Options

| | A. Views into the connection's head buffer (proposed) | B. Copy every head into owned strings | C. Spans (offsets) into a buffer the request owns |
|---|---|---|---|
| Allocation per request | none | one or more | none, but the buffer moves into the request |
| Safety of views | compiler (regions) | trivially safe | runtime bounds on each access |
| Pipelining | the next request is parsed after this one is dropped, which suits sequential pipelining (plan §11.5) | free | needs buffer swapping |
| Handler API | `borrow Request` (elided region) | `borrow Request` | `borrow Request` |

**A**, with B available on demand (`toOwned`, §3.4).

## 3. Decision

### 3.1 Request head

- The connection task owns a **head buffer** (a `PooledBuffer`, sized by `Limits.maxHeadBytes`, default 16 KiB).
  The HTTP/1.1 engine parses in place. `Request<region r>` holds:
  - `method: Method` (an enum, plus `Extension(StringView<r>)`);
  - `target: StringView<r>` and its parsed parts;
  - `version`;
  - `headers: HeaderList<r>`.

  All of them are views into the head buffer.
- `HeaderList<r>` is a fixed-capacity array of `{ name: HeaderName, value: StringView<r> }`, inline in the connection
  frame. `Limits.maxHeaders` (default 64) bounds it, so there is no allocation per request. `HeaderName` is an enum
  of the common names plus `Other(StringView<r>)`, compared case-insensitively. Lookup is linear (at most 64 entries)
  and returns the first entry or an iterator over all of them.
- Bytes after the head (a body prefix, or pipelined requests) stay in the read buffer for the body reader and the
  next parse.
- HTTP/2 and HTTP/3 decode headers (HPACK/QPACK) into a per-stream head buffer, and the same `Request<r>` points
  into it.

### 3.2 Valid until

- A `Request<r>` is valid while the connection task is handling it: from the end of the head's parse until the
  handler returns **and** the body is finished (read to the end, or drained or abandoned by the driver).
- The connection cannot parse the next request, or reuse the head buffer, while the request lives. The compiler
  rejects it (N0240).
- Handlers receive `request: borrow Request` (elided region). They cannot store it in a longer-lived place: an
  elided borrow cannot escape the call.

### 3.3 Body

- A body cannot be a `dyn` type (§1), so the handler receives a **concrete** `RequestBody` (`web_http`).
  `RequestBody<region r>` borrows its source, either the connection's transport (`borrow mut(r) dyn Transport`, Web
  ADR 0001 §3.3) for H1, or the stream's inbox for H2/H3. It also holds its framing state:

  ```ceesafe
  enum BodyFraming { Empty, Length(u64), Chunked(ChunkState), Stream(StreamId) }
  ```

  It has one `read(mut buffer) -> Result<usize, NetError>` (0 at the end), `readToEnd(limit, mut OwnedBuffer)`,
  `remaining() -> u64?` and `trailers()`.
- The framing variant selects the protocol's decoding. H1 uses `Length` and `Chunked`. H2/H3 use `Stream`, which
  reads from the stream's bounded inbox (Web ADR 0004 §3.4). Dispatch is a `match`, with no `dyn` per read.
- **Single consumer:** the handler gets `borrow mut RequestBody`, so only it reads.
- `Expect: 100-continue`: the driver sends `100 Continue` only on the handler's first `read`. A handler that never
  reads does not trigger it.
- After the handler returns, the driver finishes the body. An unread body is drained up to `Limits.drainBytes`
  (default 64 KiB) within a deadline, or else the H1 connection is closed (H2/H3: `RST_STREAM`/`STOP_SENDING` for
  that stream only).
- Limits: `maxBodyBytes` (413), a minimum-rate deadline (408, then close), and chunk-size overflow (400, then close).

### 3.4 Keeping data beyond the call

- `request.toOwned(alloc) -> Result<OwnedRequest, AllocError>` copies the head into one owned allocation (method,
  target and headers re-pointed into it). `OwnedRequest` has no region, so it can be moved into a `spawnSend` task or
  a channel.
- A body cannot outlive the request. To process a body elsewhere, read it into an `OwnedBuffer`, or stream it into a
  bounded `Channel` the handler fills.

### 3.5 Response

- `ResponseWriter<region r>` (concrete, `web_http`) borrows the connection the same way. It has:
  - `head(status, headers)`, which may be called once;
  - `write(bytes)` (backpressure: awaits the transport or the stream window);
  - `writeVectored(parts)`, `flush()` and `finish(trailers?)`.
- Headers are copied into the connection's write buffer when `head` is called, so a handler may build header values
  in locals.
- The writer chooses the framing: `Content-Length` when the length is given, chunked (H1) or DATA frames (H2/H3)
  otherwise. HEAD, 1xx, 204 and 304 suppress the body.

### 3.6 Send

A connection task runs as a `spawnSend` child (CeeSafe ADR 0038), so everything it owns must be `Send`: buffers,
`Box<dyn Transport>`, and engine state. `Request<r>` and `RequestBody<r>` never leave the task, so they need not be
`Send`. `OwnedRequest` must be.

## 4. Consequences

- There is no allocation per request on the H1 path: the head and read buffers are pooled per connection, and
  headers are an inline array. Memory per connection is fixed and visible in `--explain-frames` (plan §8).
- The connection frame grows by the header array, about 1 KiB at 64 headers × 16 bytes. This is measured in M1.
- Handlers see one concrete body type for H1, H2 and H3.
- If CeeSafe gains impls for scoped types (upstream U10), `RequestBody` could become `dyn Body` for user-defined
  bodies. Not needed for v0.x.

## 5. Tests

- Check-fail probes (kept in `tests/probes/`):
  - the buffer is reused while a request lives (N0240);
  - a handler tries to store `borrow Request` in a struct field outside the call (expected to be rejected; the
    probe is to be written in M0).
- Run-pass:
  - a pipelined burst, where every request sees only its own headers;
  - a handler reads half the body, and the driver drains or closes per limit;
  - `100-continue` sent only on the first read;
  - `toOwned` round-trips every field.
- The allocation-failure sweep covers `toOwned` and `readToEnd`.

## 6. Questions for the owner (answered 2026-11-02: each as recommended)

1. Is an inline `HeaderList` with a fixed capacity (default 64) acceptable, or should it fall back to a pooled
   overflow area? Recommended: fixed. More than 64 headers gets 431, as most servers do. **Fixed, 431 past it.**
2. Should handlers be able to opt into an owned request automatically (a `Handler` flag), or must they call
   `toOwned` explicitly? Recommended: explicitly. **Explicitly.**
