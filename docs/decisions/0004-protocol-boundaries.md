# Web ADR 0004 — Protocol boundaries: sans-I/O engines, shared semantics, package layering

- Status: **Accepted** by the owner, 2026-11-02 (the question of §6 as recommended). The split into separate
  packages is the owner's earlier decision of 2026-11-02 (implementation plan §11.1).
- Scope: what a protocol engine may do; how engines meet the shared HTTP semantics; which package may depend on
  which. Every milestone.

## 1. Context

- The roadmap's core rule: "Protocol engines must not own the event loop. HTTP/3 uses UDP/QUIC while sharing
  protocol-neutral request semantics with HTTP/1.1 and HTTP/2."
- In CeeSafe, all files of one package share one scope (`guide/packages.md`, "Using a dependency"). Directories
  inside a package isolate nothing; only package boundaries do. There are no re-exports, so a facade package
  re-exporting the others is not possible either.
- The interpreter runs CeeSafe deterministically but runs no C. Code without I/O can be tested there quickly and
  exhaustively.

## 2. Options

| | A. Sans-I/O engines + async drivers (proposed) | B. Async engines that read and write themselves |
|---|---|---|
| Engine | a state machine over bytes; no `await` | async fns over `Transport` |
| Fuzzing | direct: bytes in, events out | needs a mock transport and an executor |
| Reuse | H2's engine over TLS, H3's over QUIC streams, the client and the server share engines | per transport |
| Cancellation points | only in drivers | everywhere |

**A.**

## 3. Decision

### 3.1 Engines

An engine (HTTP/1.1, HPACK + HTTP/2, QPACK + HTTP/3, WebSocket, SSE framing, PROXY protocol) is a struct with
methods, and:

- **no `await`, no timers, no sockets, no threads.** It never calls `Timer.now()`. When it needs time (an HTTP/2
  PING timeout, a WebSocket close timeout), it reports a deadline as data, and the driver enforces it;
- **bytes in:** `receive(borrow mut this, input: Slice<u8>) -> Result<usize, ProtocolError>` consumes a prefix and
  returns the count consumed. Unconsumed bytes are offered again with more;
- **events out:** `next(borrow mut this) -> Event?`. `Event` is the engine's enum; a variant carrying views has a
  region parameter tied to the input buffer;
- **bytes out:** `encode…(borrow mut this, …, out: borrow mut OwnedBuffer) -> Result<void, ProtocolError>` appends to
  a caller's buffer, never allocating behind its back. Allocation failure is a value;
- **limits** are constructor parameters, and every limit has a test;
- **deterministic:** the same inputs in the same splits give the same events. This is the fuzzing and interpreter
  contract.

### 3.2 Drivers

A driver is an async fn in the protocol's package, for example
`web_http1.serve(conn: borrow mut dyn Transport, ctx, handler, limits)`. It:

- owns the read and write buffers for the connection;
- reads, feeds the engine, dispatches events to the `Handler` (Web ADR 0007), and writes;
- applies deadlines (`withDeadline`) and admission;
- is where cancellation happens: every `await` is in a driver.

The server (`web_server`) picks a driver by ALPN or prior knowledge, and the client (`web_client`) does the same.

### 3.3 Shared semantics (`web_http`)

`Method`, `StatusCode`, `Version`, `HeaderName`, `HeaderList<r>`, `Request<r>`, `OwnedRequest`, `ResponseHead`,
`RequestBody<r>`, `ResponseWriter<r>`, `Limits`, `Handler<S>` and `Middleware` (Web ADR 0007). Protocol errors map to
a `web_http.ProtocolError` with one status (or a connection close) per reason. Every protocol produces the same
`Request<r>` (Web ADR 0003).

### 3.4 Multiplexed protocols

- HTTP/2 and HTTP/3: one **connection task** owns the engine and the transport. Each request stream is a child task
  in the connection task's `Async.scope`, which runs the handler.
- Streams and the connection talk only through bounded channels:
  - an inbox of DATA per stream, bounded by the stream's receive window;
  - an outbox of frames into the connection, bounded so that a fast handler waits for the flow-control window.
- Cancelling a stream cancels its child task. Cancelling the connection cancels its scope: GOAWAY, then the grace
  period, then cancel (as M1's drain).

### 3.5 Package layering (checked by `tools/check_layers.py`)

| Package | May depend on |
|---|---|
| `web_core` | — |
| `web_transport` | `web_core` |
| `web_tls` | `web_core`, `web_transport` |
| `web_http` | `web_core` |
| `web_http1`, `web_sse`, `web_ws`, `web_http2` | `web_core`, `web_http` |
| `web_http3` | `web_core`, `web_http`, `web_tls` |
| `web_client` | `web_core`, `web_transport`, `web_http`, `web_http1`, `web_http2` |
| `web_server` | `web_core`, `web_transport`, `web_http`, `web_http1`, `web_http2`, `web_sse`, `web_ws` |
| `web_proxy` | `web_core`, `web_transport`, `web_http`, `web_client`, `web_server` |

The table encodes two properties:

- **Engines cannot reach sockets.** The protocol packages do not depend on `web_transport`; they see only
  `web_core`'s `Transport` interface, which drivers receive as `borrow mut dyn Transport`.
- **Only TLS users link OpenSSL.** No package except `web_tls` and `web_http3` reaches `web_tls`. TLS enters a
  server or client through the `Acceptor`/`Connector` interfaces (Web ADR 0001 §3.3); an application that wants TLS
  depends on `web_tls` itself and passes its acceptor in. The checker verifies this through indirect dependencies
  too.

There is **no facade package** (no re-exports). Applications import the packages they use, usually `web_server` and
`web_http`, plus `web_tls` for TLS. Changing the table is an amendment to this ADR, made in the same commit as the
script.

## 4. Consequences

- Engines are fuzzed directly and tested in the interpreter over every input split.
- Protocol packages are small and independently versionable. A bug in one cannot reach another's internals: there
  are no `public` items across packages unless intended.
- There are twelve manifests and lock files. `ceesafec fetch` must be run in a package after its dependencies change.

## 5. Tests

- `tools/check_layers.py` in CI (verified 2026-11-02: passes; a planted `web_http → web_tls` edge fails with nine
  errors, the edge itself and the eight packages that then reach OpenSSL).
- For each engine: a fragmentation suite, a limits suite and a fuzz harness (Web ADR 0005).

## 6. Questions for the owner (answered 2026-11-02: as recommended)

1. Should `web_http3` depend on `web_tls`, or on OpenSSL directly through its own shim? Recommended: `web_tls`, for
   shared certificate configuration (`CertResolver`). **`web_tls`.**
