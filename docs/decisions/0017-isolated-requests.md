# Web ADR 0017 — Isolated requests: a handler that traps is answered 500, and its connection goes on

**Status:** accepted 2026-11-20. The owner: "I accept all the above ADRs as written" (§7, every recommendation).
Proposed 2026-11-20, written at the owner's request to close the gaps between httpserver and Elixir's web
servers ("We need to tackle where Elixir is ahead", then "Draft ADRs for all gaps"). It is the gap of a request
that traps: Cowboy answers it 500 from the connection's process and keeps the connection; CeeSafe.Web, under Web ADR
0016, ends the connection with no answer.
**Scope:** `web_http1` (a second driver entry, `serveIsolated`), `web_http` (`Limits.isolateRequests`, a default
method on `Application`, an HTTP/1.1 sink), `web_http2` and `web_http3` (500 in place of a reset when no head left),
`web_server` and httpserver's `Service` (choosing the entry). No change to the engines, which stay sans-I/O (Web ADR
0004), and none to `ConnectionServer.run` or `runIsolated` (Web ADR 0016 §2.1), which stay as they are.

## 1. Context

Checked 2026-11-20 on `main` at `4d71b12`, with ceesafec 0.80.0.

- **What a trap costs today.** Under `ConnectionServer.runIsolated` (Web ADR 0016 §2.1), the unit is the connection:
  a handler that traps ends the connection's task, the accepting task shuts its kept closer (§2.2), and the client
  reads the end of the stream with no response. Every request pipelined behind it is lost, and the client's
  keep-alive connection with it. What the unit held is leaked (CeeSafe ADR 0175 §2.4): the connection's buffers, its
  OpenSSL state when it is TLS (Web ADR 0002), and its descriptor, which is not counted. httpserver's
  `tools/supervisor_check.py --isolated` expects exactly that: `get("/crash") is None` (lines 102, 117, 124).
- **Streams already have the better shape.** An HTTP/2 stream's handler runs in its own isolated task
  (`web_http2/src/serve.cee:399`, `trySpawnIsolated`), which owns a `StreamKit`, a `StreamContext` and clones of
  its channels, and talks to the connection's task only through them (`StreamPort`, `serve.cee:778`). The
  connection's task owns the socket, the TLS state and the HPACK tables, and survives the stream's crash
  (`crashed`, `serve.cee:647`). HTTP/3 does the same (`web_http3/src/server.cee:571`). But the crash is answered
  with `RST_STREAM INTERNAL_ERROR` (HTTP/3: `H3_INTERNAL_ERROR`) even when the handler sent no head, where a 500
  would tell the client what happened and could carry nothing wrong.
- **Why HTTP/1.1 was not done this way.** Web ADR 0016 §3 weighed "isolate only the handler call" and set it aside:
  the handler borrows the request and the exchange from the connection's frame, so it cannot be an isolated child
  (no loan, N0349; captures `Send`, N0334) "without copying the request and proxying the exchange through channels,
  as HTTP/2 does. For HTTP/1.1 that is a second design of the driver."
- **What makes it smaller than a second design.** `Exchange` is one type with two back ends
  (`web_http/src/exchange.cee:52`): the connection (`create`, HTTP/1.1) or a `StreamSink` (`forStream`, line 163),
  whose four calls are the request body in and the response head, data and trailers out
  (`web_http/src/stream.cee:7`). A stream exchange already frames the response for a peer that frames it again:
  `finish` gives the head a `content-length` whenever it knows one (`exchange.cee:1086`), and `finishBody` leaves
  the unread body to the driver (line 1200). So an HTTP/1.1 request can be served as a stream: the handler gets a
  stream exchange over channels, and the connection's task turns head and data into HTTP/1.1 bytes, as the HTTP/2
  connection turns them into frames.
- **The applications see no difference.** httpserver's `App.handle` (`src/app.cee:522`) runs the request's
  middleware, timeout, budget (ADR 0170) and access log inside `Application.handle`, so they run inside the unit
  whichever back end the exchange has. What they cannot do on a stream is switch protocols
  (`exchange.cee:984`), which §2.4 handles.

## 2. Decision

### 2.1 `Limits.isolateRequests` and `web_http1.serveIsolated`

```ceesafe
// web_http
public struct Limits { …; public isolateRequests: bool; }   // false by default

// web_http1
public async fn serveIsolated(
    conn: borrow mut dyn Transport,
    connection: borrow ConnectionContext,
    app: borrow Arc<Box<dyn Application + Send + Share>>,
    limits: borrow Limits,
    already: Slice<u8>,
) -> Result<void, NetError>
```

- **One isolated child a request.** The connection's task reads and parses each head as `serveFrom` does
  (`web_http1/src/serve.cee:32`). Then, in an `Async.tasks(1, NetError.Reset, …)` set it opens once for the
  connection and watches (`Isolation.watch`), it starts
  `set.spawnIsolated(NetError.Reset, async move () => requestTask(…))`. The child owns a copy of the head (as a
  `HeaderBlock`, as a stream's task does), a `StreamContext`-like copy of the connection's context, a clone of the
  application's `Arc`, the connection's kit (§2.3) and clones of two channels: body in and messages out.
- **The child serves the request as a stream's task does** (`serveStream`, `serve.cee:921`): it builds the
  `Request`, makes `Exchange.forStream` over an `H1Port` (a `StreamSink` like `StreamPort`), calls
  `application.handle`, finishes, and sends its kit back. A `HandlerError` is answered inside the child as now
  (`respondError`); only a trap reaches §2.2.
- **The connection's task is the HTTP/1.1 framer.** It reads the request body with the framing it parsed
  (`Content-Length` or chunked, `ChunkDecoder`), sends `100 Continue` when the child first asks for body bytes, and
  passes the bytes on as `BodyPart.Data`, the channel's capacity being the flow control. It writes the response:
  the status line and fields from the head message, the body as identity when the head has `content-length`,
  else chunked (HTTP/1.1) or until close (HTTP/1.0), the trailers in the last chunk. It keeps today's coalescing of
  pipelined responses in `out`. A head field `connection: close` (from `Exchange.closeConnection`) ends the
  connection after the response.
- **Requests stay sequential.** The connection's task starts one child at a time, so pipelining keeps its order and
  the set's limit of 1 is never waited for.
- **The entry is chosen where the protocol is chosen.** `HttpService.serve` (`web_server/src/server.cee:106`) and
  httpserver's `Service.serve` (`src/app.cee:446`) call `serveIsolated` with their `Arc` when
  `limits.isolateRequests`, else `serveFrom` as now. It works under `ConnectionServer.run` and under `runIsolated`.
  Under `runIsolated` a request is a unit inside the connection's unit (CeeSafe ADR 0175 §2.2): a trap in the
  application costs the request; a trap in the driver, the parser or the TLS shim still costs the connection.

### 2.2 What a crash is answered with

The connection's task learns of the crash from the set's watch channel, as HTTP/2's does (`crashOrTimer`,
`serve.cee:749`). Messages the child posted before it trapped may still wait on the out channel, and the two
channels race; so the task first takes every message already waiting, writes what it can, and then decides:

- **No byte of the response has been written** (the head message never came): it writes
  `500 Internal Server Error` with `content-length: 0` and the `date` field when `limits.sendDate`. The
  application's `beforeHead` hooks are not run: they are the application's code, which just trapped, and they would
  run outside the unit. The client so misses the fields its middleware adds (CORS, a request id), which a 500 from
  a broken handler can do without.
- **The connection is kept** when the request's body was read to its end, or what is left of it is drained within
  `limits.drainBytes` (64 KiB by default, `web_http/src/limits.cee:43`) as `Exchange.finishBody` drains it
  (`exchange.cee:1199`), and no `100 Continue` is pending. Otherwise the 500 carries `connection: close` and the
  connection ends after it. The next pipelined request is then served as if nothing happened.
- **Part of the response has been written**: the connection ends at once. A chunked body without its last chunk, or
  a body shorter than its `content-length`, tells the client that the response is incomplete (RFC 9112 §6.3,
  §7.1).
- **A request past its bound** (CeeSafe ADR 0186: `PanicKind.Overran`) is a crash like any other and is answered
  the same way. `TaskCrash.kind` says which it was.
- `Application.crashed(request, crash)` is called (§2.5), and `ServerReport.crashed` counts the crash (Web ADR 0016
  §2.1) whichever unit it was.

### 2.3 What a crash leaves

- **The connection's kit** (`StreamKit`: the request's fields, `carry`, `out`, `pending` of
  `writeBufferBytes`, 16 KiB by default, and a 4 KiB `ResponseHead`; `serve.cee:33`) is lent to each child and
  returned with `Done`, as HTTP/2's kits are. A crashed child keeps it: the connection makes a new one, and the
  leak is that kit plus what the handler allocated, counted by the unit's budget (`TaskCrash.leakedBytes`).
- **Nothing of the connection is leaked**: not its head buffers, its TLS state or its descriptor, which the
  connection's task still owns and closes normally. Under Web ADR 0016 a crash leaked all three.
- The child's clones of the two channels are leaked, so the channels are never closed by their last drop (CeeSafe
  ADR 0175 §2.4). The connection's task never waits for that: the crash is what ends the request.

### 2.4 Requests that can switch protocols run in place

A server may switch protocols only to one the client listed in its `Upgrade` field (RFC 9110 §7.8), and a CONNECT
turns the connection into a tunnel (RFC 9110 §9.3.6). Such a request is served as now, by `exchangeOne` on the
connection (`serve.cee:158`), inside the connection's own unit. A WebSocket session
(`web_ws/src/session.cee:585`) so stays the connection's, and a trap in it costs that connection, which carries
nothing else. Every other request is isolated, and `switchProtocols` on it fails as on a stream: the client did not
ask for it.

### 2.5 `Application.crashed`

```ceesafe
public interface Application {
    …
    /// A request's handler trapped and ended alone (Web ADR 0017): called by the connection after it answered.
    async fn crashed(borrow this, request: borrow Request, crash: borrow TaskCrash) {}
}
```

A default method (CeeSafe ADR 0086; type-system.md §2037), so no implementation has to change. It runs in the
connection's task with the request rebuilt from the head the connection still holds. httpserver's `App` gives it
to its `AccessLog`, so the log has a line for a crashed request, with status 500.

### 2.6 Streams: a 500 in place of a reset

For HTTP/2 and HTTP/3 the stream's `Link` gets `headSent`, set when the connection passes the stream's head to the
engine (`Outgoing.Head`, `serve.cee:447`; `web_http3/src/server.cee:526`). On a crash, after taking the messages
already waiting (§2.2):

- **No head sent:** HEADERS with `:status 500` and `content-length: 0`, ending the stream. If the client is still
  sending the request body, the stream is then reset with `NO_ERROR` (HTTP/2, RFC 9113 §8.1) or stopped with
  `H3_NO_ERROR` (HTTP/3, RFC 9114 §4.1).
- **Head sent:** the reset as now, `INTERNAL_ERROR` or `H3_INTERNAL_ERROR`.

This applies always, not only under `isolateRequests`: Web ADR 0016 §2.3 already isolates every stream.

## 3. Options weighed

- **Keep the connection as the unit and only shut it** (Web ADR 0016 as it is). No cost, but every crash costs the
  client its connection, the requests pipelined on it, and on TLS a new handshake, and leaks the connection's
  buffers, TLS state and descriptor.
- **A rescue writer for the 500.** The accepting task keeps, besides the closer, a way to write one response on the
  connection after its unit crashed. It cannot work over TLS: the record layer's state (keys, sequence numbers) is
  inside the dead unit, half updated perhaps. Over plaintext it would answer 500 but still close the connection
  and leak what Web ADR 0016 leaks.
- **Run the handler in place and catch the trap there.** CeeSafe has no catch: a trap is not an exception, and
  ADR 0175 §4 set unwinding aside. The unit is the only way to survive one.
- **One isolated child for the whole keep-alive connection's requests** (a long-lived request worker per
  connection). One spawn a connection, not a request, but a crash still loses the requests after it, and the
  worker borrows nothing yet owns everything a request needs, which is the per-request design without its gain.
- **Isolated requests always, no setting.** §4 measures a cost of a few microseconds a request; Web ADR 0016 §3
  recorded the owner's rule that what costs is opt-in.

## 4. Consequences

- **A crash costs one request, answered 500**, and the client's connection goes on, as with Cowboy (its stream
  handler answers 500 for a request process that exits). The pipelined requests behind it are answered.
- **The leak per crash shrinks** to the request's kit (about 20 KiB with the default limits, as the
  comment at `web_http2/src/serve.cee:32` says) and what the handler held, with no descriptor and no TLS state.
  httpserver's recycling (`recycleCrashes`, `recycleLeakedBytes`) comes later for the same crash rate.
- **The cost.** Each request pays an isolated spawn, two channel hops each way, a copy of its head into the kit and
  a copy of each body chunk through the channel. A throwaway probe (§8) measured, per request, on macOS arm64 under
  a load average of about 140 on 8 cores: building a small head and body in place, 0.04 to 0.4 µs; the same answered
  through a channel in the same task, 0.2 to 1.1 µs; through an ordinary child, 1.6 to 2.3 µs; through an isolated
  child, 3.2 to 5.2 µs. httpserver answers about 105 000 small requests a second on one core
  (`CEESAFE_FINDINGS.md:44`), about 9.5 µs each, so isolated requests could cost a fifth to two fifths of that on
  the smallest requests, less on real ones. The numbers are noisy and must be taken again on the benchmark machine
  (Web ADR 0005 §4.2) before anything is decided from them. Most of the cost is the spawn: an isolated child cost
  2 to 3 µs more than an ordinary one in the probe, where CeeSafe ADR 0175 §10 measured 255 to 273 ns against 138
  to 156 ns for one that allocates, waits once and returns. Why it is so much more here is not known yet (§8).
- **Static files** sent by an isolated request lose `sendfile`: a stream exchange reads the file and posts its bytes
  (`streamFile`, `exchange.cee:625`). A message that moves the open `File` to the connection's task, which then
  sends it as now, keeps it; it is the first follow-up, and question 6.
- **Upgrades and CONNECT** are served as now (§2.4), with the connection as their unit.
- **httpserver.** `tools/supervisor_check.py --isolated` changes its expectations: `/crash` is answered 500 and the
  connection stays open; `/crash/kept` (a trap holding the cache's lock before any write) gets 500; `/crash/gz/60000`
  (a trap half way through a 60 000-byte gzip body, past one 16 KiB buffer) still loses its connection. The access
  log gets a line for each crash.

## 5. Upstream

Nothing is needed from CeeSafe: `spawnIsolated`, `Isolation.watch`, `TaskCrash.kind` and default methods exist in
0.80.0. The isolated spawn's cost (§4) is measured again here first; if it holds on the benchmark machine, it goes
to `docs/ceesafe-upstream-work.md` as a new item with the probe.

## 6. What it does not do

- No isolation of the driver, the parser or the TLS shim: they are the connection's unit (Web ADR 0016).
- No isolation inside an upgraded connection or a tunnel (§2.4).
- No retry of a crashed request: a 500 is the answer, as in Cowboy.
- No 500 once part of the response has left: HTTP/1.1 cannot take back a status it sent.

## 7. Questions for the owner

1. **Isolated requests for HTTP/1.1 as an opt-in, `Limits.isolateRequests` (false by default), through a second
   driver entry `serveIsolated`** (§2.1)? *Recommended: yes*, with the default decided by the benchmark gate once §4's
   cost is measured on the benchmark machine.
2. **After a crash with no response byte out, 500 with `content-length: 0` and no `beforeHead` hooks, the connection
   kept when the body is read or drains within `drainBytes` and no `100 Continue` is pending, else closed; with part
   of the response out, the connection closed** (§2.2)? *Recommended: yes.*
3. **HTTP/2 and HTTP/3 answer 500 in place of the reset when the stream's head was not sent, always** (§2.6)?
   *Recommended: yes*: the streams are isolated already, and the change costs one flag a stream.
4. **Requests with `Upgrade` and CONNECT run in place, in the connection's unit** (§2.4)? *Recommended: yes*: those are
   the only requests that can take the connection over, and the connection carries nothing else afterwards.
5. **A default method `Application.crashed(request, crash)`, called by the connection after its answer** (§2.5)?
   *Recommended: yes*, so an access log sees every request; the default does nothing.
6. **An isolated request's file sent by moving the `File` to the connection's task** (§4), in a step after this one?
   *Recommended: yes, as a follow-up*: until then such a file is copied through the channel.
7. **`Overran` (CeeSafe ADR 0186) answered like a trap, 500** (§2.2), not 503? *Recommended: yes*: a request that
   overran its bound failed in the server, and the log says which it was from `TaskCrash.kind`.

## 8. Tests

**Probe (2026-11-20; throwaway, not committed).** A program built with ceesafec 0.80.0 on macOS arm64 (load average
about 140 on 8 cores). In one `Async.tasks` set, 200 000 times: (0) a small head and body built in place; (1) the
same sent as three messages (head, data, the kit back) through a channel of capacity 4 and received, in the same
task; (2) the same from an ordinary child (`set.spawn`); (3) from an isolated child (`set.spawnIsolated`). Three
rounds, nanoseconds a request: (0) 43 to 526, (1) 161 to 1 345, (2) 1 626 to 2 256, (3) 3 193 to 5 180. The spread is
the machine's load; the order held in every round.

**Proposed:**
- `web_http1` tests, natively and in the interpreter: `serveIsolated` over `MockIncoming`-style transports with an
  application that traps on one request of five pipelined ones: four answered 200, one 500, in order, and the
  connection still open; the same with a request body read half (drained, kept), one past `drainBytes` (closed),
  and an `Expect: 100-continue` never read (closed); a trap after the head was written (the connection ends); an
  `Upgrade: websocket` request served in place; `Application.crashed` called once a crash.
- `web_http2` and `web_http3`: a stream whose handler traps before its head gets HEADERS 500; one that traps after
  its head gets the reset, as now.
- `tools/isolated_check.py`: over plaintext and TLS keep-alive, 200 traps each answered 500 on the same connection,
  the descriptors unchanged, `Isolation.report()` counting 200 crashes; `examples/http1` takes
  `CW_ISOLATE_REQUESTS=1`.
- The sweeps (`run_tests.py --sanitize --alloc-sweep --cancel-sweep`) over the new tests.
- The benchmark (Web ADR 0005 §4.2): `benchmarks/http` with and without `isolateRequests`, `h2load --h1 -c 64 -t 4`,
  three runs each, on the benchmark machine; the difference is recorded here and decides question 1's default.
- httpserver: `supervisor_check.py --isolated` with its expectations changed as §4 says.
