# Web ADR 0008 — Streaming responses and Server-Sent Events

**Status:** accepted 2026-11-02 (M3). **Scope:** implementation plan §7 M3: the streaming body, `web_sse`, and
disconnect detection.

## 1. Context

M2 already delivered most of M3's tasks 1 and 2, in `web_http.Exchange` (Web ADR 0003 amendment 3):

| Plan item (§7 M3) | Where it is |
|---|---|
| `BodyReader.read(buf)`, `readToEnd(limit)` | `Exchange.read`, `Exchange.readToEnd(into)`: the limit is `into`'s room, and `Limits.maxBodyBytes` |
| `BodyWriter.write` with backpressure from the socket, no internal queue | `Exchange.write`: bytes collect in the connection's response buffer, which is written to the socket when it fills; the write awaits the socket |
| `flush`, `finish` | `Exchange.flush`, `Exchange.finish` |
| chunked response writer | `Encoder`, chosen when a response outgrows the buffer without a declared length (HTTP/1.1) |
| upload limit, min-rate deadline | `Limits.maxBodyBytes` (413), `Limits.minBodyRate` (408) |
| `finish(trailers?)` | **not done**: see §3.5 |

So M3's new work is `web_sse`, a way to notice that the client left while the server has nothing to send, and the
gates.

What CeeSafe allows, checked 2026-11-02 on ceesafec 0.9.2:

- `tests/probes/sse-race-exchange.cee`: one task races a method borrowing the exchange mutably ("the peer closed")
  against a channel receive and a timer, then writes to the exchange after the race. It checks, and prints the
  expected counts interpreted and native.
- A transport `read` may be cancelled: bytes the kernel delivered are kept (Web ADR 0001 §3.4). A cancelled `write`
  poisons the transport. So the stream may race reads, timers and channel receives, but never a write.
- `Channel<T>` (CeeSafe ADR 0032) has `trySend`, `sendAsync` and `recvAsync`, but no receive that does not wait. A
  queue that drops its oldest value when full, and a writer that batches the events already queued before it flushes,
  both need one. That is upstream item **U13** (`docs/ceesafe-upstream-work.md`).

## 2. Options for disconnect detection

| Option | Detects a closed client | Cost |
|---|---|---|
| A. Only by a failed write | at the second heartbeat after the close (the first write after a FIN usually succeeds) | none |
| B. A read-side watcher in a second task | at once | needs the transport lent mutably twice; impossible in CeeSafe |
| **C. The idle wait races a read** | at once while idle; by a failed write while sending | one cancelled read per idle wake-up (event or heartbeat), none while events flow |

**C** is chosen. A client that closes while the server is blocked writing to it gets a failed write, or the write
deadline (`Limits.writeTimeoutMs`) if it stopped reading without closing.

## 3. Decision

### 3.1 `Exchange.peerClosed()` (web_http)

```ceesafe
public async fn peerClosed(borrow mut this) -> Result<void, HandlerError>;
```

It returns `Ok` when the client has closed or reset the connection. It never returns while the connection is open
and quiet, so it is only useful inside a `race`. It is called once the request body is read (`Internal` otherwise).
Bytes the client sends meanwhile (pipelined requests) are kept for the driver, which never reaches them while the
stream runs. When that buffer is full, it stops reading and waits until it is cancelled. Then a failed write detects
the close, as in option A. Reading with no deadline is right here: the heartbeat bounds how long the stream is idle.

### 3.2 Events (web_sse, sans-I/O)

- `Event` owns its fields: `data`, and optionally `id`, `event` (the type) and `retry` (milliseconds). It is owned
  because it crosses a channel between tasks.
- `Events.encode(event, out)` appends the wire form (WHATWG HTML §9.2.5):
  - `event:`, `id:` and `retry:` lines come first, then one `data:` line per line of `data`, then an empty line;
  - `data` is split at LF, CR and CRLF, so a `\r` cannot end a line early on the client;
  - an empty `data` gives one `data:` line, so the client still dispatches the event.
- Validation, before anything is appended:
  - `id` and `event` must not contain LF or CR, and `id` must not contain NUL (§9.2.6 ignores such an id);
  - an invalid event is `Err(EventError)`, and nothing is written.
- The heartbeat is a comment, `:\n`, which clients ignore (§9.2.6). It keeps proxies from timing the connection out,
  and makes a closed connection's write fail.
- `Events.lastEventId(request)` gives the `Last-Event-ID` field's value, if any.

### 3.3 The queue: `EventQueue` (web_sse)

`EventQueue` is a bounded `Channel<Event>` with an overflow policy:

| `Overflow` | When the queue is full, `publish` |
|---|---|
| `Block` | waits for room (`sendAsync`): the producer slows to the client's pace |
| `Disconnect` | closes the queue and returns `Err(Disconnected)`: the stream ends after the events already queued are written, and the producer stops |
| `DropOldest` | drops the oldest queued event and queues the new one. **Needs U13**: until it lands, `create` refuses it with `Err(Unsupported)` |

- `queue.close()` ends the stream after the queued events; `publish` after a close is `Err(Closed)`.
- `EventQueue` is `@noncopy`, and `share()` gives the producer its own handle.

### 3.4 The stream: `EventStream.run` (web_sse)

```ceesafe
public async fn run(exchange: borrow mut Exchange, queue: borrow EventQueue, options: borrow StreamOptions)
    -> Result<StreamEnd, HandlerError>;
```

1. It sets `content-type: text/event-stream` and `cache-control: no-store`. No length is declared, so HTTP/1.1 uses
   chunked framing. Then it sends the head and flushes.
2. It loops over `race(peerClosed, race(queue.next(), sleep(options.heartbeatMs)))`:
   - an event is encoded and written, then flushed (U13 will flush once for all events already queued);
   - a heartbeat writes `:\n` and flushes;
   - the queue's end finishes the response (`StreamEnd.Finished`);
   - a closed client returns `StreamEnd.ClientClosed` and closes the queue, so a blocked producer wakes with
     `Err(Closed)`.
3. A failed write closes the queue too, and returns the error.

The producer is an `EventSource<C>`, run with the stream by `web_sse.serveEvents(exchange, queue, options, source,
context)`, a top-level generic function (CeeSafe v0 methods take no type parameters of their own). `C` is what the
producer reads besides its own fields, typically the application's state. `serveEvents` races the stream against
the producer:
- the producer's arm publishes, closes the queue, then waits, so it never wins: the stream always decides;
- when the producer is done, the stream sends what is queued and ends;
- when the stream ends first (the client left, or a write failed), the race cancels the producer at once, wherever it
  is waiting.

A producer that is merely told through the queue ("`publish` fails") would run until its next `publish`. In
`examples/sse` that is a whole tick (found by `tests/interop/sse.py`: 300 ms against a 100 ms heartbeat). Cancelling
it frees it at once. A handler that wants its own arrangement calls `EventStream.run` directly.

`StreamOptions.defaults()` sets a 15 s heartbeat. That is below the common 30–60 s idle timeouts of proxies and load
balancers.

### 3.5 Not in M3

- **Trailers on a chunked response** (`finish(trailers)`): nothing in M3 uses them. They come with gRPC over HTTP/2
  (M7), where they carry the status.
- **Fan-out to many subscribers** (a broadcast hub): an application builds it from `EventQueue`s. A library hub
  waits for a real use.

## 4. Consequences

- A slow client slows a `Block` producer to its pace. Memory per stream is bounded by the queue's capacity times the
  largest event, plus the connection's buffers.
- One cancelled read per idle wake-up: at a 15 s heartbeat this costs nothing measurable.
- `DropOldest` and write batching wait for U13; the API already names them, so adding them changes no signature.

## 5. Tests (the M3 exit gate, plan §7)

- `web_sse` unit tests:
  - encoding against vectors written from WHATWG §9.2, including CR, CRLF, an empty `data`, and invalid `id`s;
  - each overflow policy;
  - the stream over `MockTransport`: events, heartbeats under the interpreter's virtual clock, the client closing,
    and the queue ending.
- Disconnect: a mock that ends its input while the stream is idle frees a blocked producer within one heartbeat.
- Slow client (`tests/interop/sse.py`): a reader at 1 KB/s against a producer offering 100 MB/s keeps the server's
  RSS flat (sampled over 60 s, first sample against last), and a client that closes frees the producer within one
  heartbeat.
- The cancel sweep and the allocation-failure sweep over `web_sse`'s tests (`tools/run_tests.py`).
- `examples/sse`: a counter stream that resumes from `Last-Event-ID`, and a flood stream for the slow-client gate.

## 6. Amendment 1 (2026-11-03, on ceesafec 0.10.0: U13)

CeeSafe 0.10.0 adds `Channel.tryRecv` and `isClosed` (CeeSafe ADR 0060; upstream U13). So the two things §3.3,
§3.4 and §4 left for later are done, and no signature changed except as noted.

1. **`Overflow.DropOldest`.** On a full queue, `publish` takes the oldest event out (`tryRecv`) and tries again, until
   its event is in. It never waits.
   - Another producer can take the room first; each pass then drops one more of the oldest events, and the loop ends
     once a send gets in.
   - A closed queue is `Err(Closed)`, checked first with `isClosed`.
   - `QueueError.Unsupported` is gone: it named only this policy.
2. **One flush for a burst.** After an event wakes the stream, it writes that event and every event already queued
   behind it (`EventQueue.tryNext`), then flushes once. The bytes are the same; there are fewer socket writes and,
   in HTTP/1.1, fewer chunks.
   - A burst is at most the queue's capacity (`EventQueue.capacity()`). A producer that refills the queue as fast as
     it drains cannot hold back the flush.
3. **Tests:**
   - `overflow_policies` publishes four events into a queue of two under `DropOldest` and reads the last two, then
     the end.
   - `events_then_the_end` expects two events queued before the stream starts in one chunk (`2e`: 11 + 35 bytes,
     computed in Python).

