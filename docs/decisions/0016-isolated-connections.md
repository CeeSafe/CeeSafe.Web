# Web ADR 0016 — Isolated connections and streams: a trap ends one connection or one stream, not the process

**Status:** accepted 2026-11-19. The owner: "I accept ADR 0016's five questions as written" (§7, every recommendation).
Written at the owner's request ("Move httpserver onto spawnIsolated and Process.spawnWith", then "Web ADR first"),
from CeeSafe ADR 0175 §5 and §9 step 4, which leave this change to CeeSafe.Web. The connections' part is implemented
with 0.75.0 (§9); the streams' part waits for U25.
**Scope:** `web_transport` (`ConnectionServer`, `PlainStream`, `TcpTransport`), `web_http2` and `web_http3` (a
stream's task), `web_server` (`Server.runIsolated`), the toolchain minimum (0.75.0), one upstream item (U25). No
change to the engines, which stay sans-I/O (Web ADR 0004), and none to `ConnectionServer.run`, which stays as it is.

## 1. Context

Checked 2026-11-19 on `main` at `17f4495`, with ceesafec 0.75.0 (the release, osx-arm64; the archive matches the
release's `SHA256SUMS`).

- **What a trap does today.** A trap (an index out of bounds, an overflow, an unwrapped `Err`) is a panic, and a
  panic ends the process (CeeSafe type-system.md §14.5). One request whose handler traps ends every connection of
  its process. httpserver's answer is a supervisor and a worker process a core (its `src/supervisor.cee`): a trap
  costs one worker's connections and not the server's.
- **What 0.75.0 adds** (CeeSafe ADR 0175, released 2026-11-19): `await set.spawnIsolated(crashed, async move () => …)`,
  a task-set child that is the root of an *isolation unit*. A trap in the unit ends the unit and nothing else,
  unless C frames lie between the executor and the trap or a `Critical` is held, which end the process as before.
  Everything the unit held is leaked and its bytes counted (`TaskCrash.leakedBytes`, `Isolation.report()`).
  `Isolation.watch(set, channel)` puts a `TaskCrash` in a channel for each crash. `AsyncTcpStream.closer()` is a
  `StreamCloser`, a second hold on the socket whose `shutdown()` ends the connection for the peer, whoever holds
  the stream. Its captures must be `Send` and hold no loan (N0334, N0349).
- **Why it cannot be done above CeeSafe.Web.** A connection's handler gets `conn: borrow mut dyn Transport` and
  `ctx: borrow ConnectionContext` (`packages/web_transport/src/server.cee:28`), and an `Application` gets
  `request: borrow Request` and `exchange: borrow mut Exchange`. An isolated child can hold none of these. What
  must be isolated is the task that owns the connection, and that task is `ConnectionServer`'s.
- **How connections are served today.** `ConnectionServer.run` (`server.cee:235`) starts `limits.workers` worker
  tasks in an `Async.scope`. Each loops accept, handshake, serve, close (`server.cee:272`). They take turns at the
  source last in, first out (`Turns`), so the frames that serve are the ones that served last. The workers are the
  admission limit: when all are busy nobody accepts, and the kernel's backlog holds the rest. A worker is a scope
  child, and its frame is in the scope's. It borrows the source, the acceptor, the handler, the limits, the drain
  and the counters, so it cannot be isolated.
- **How streams are served today.** HTTP/2 starts each stream's handler with
  `set.trySpawn(async move () => streamTask(…))` (`packages/web_http2/src/serve.cee:371`). It refuses the stream
  with `REFUSED_STREAM` when the set is full. The child owns clones of the application's `Arc` and of the
  stream's channels, and talks to the connection's task only through channels. HTTP/3 does the same
  (`packages/web_http3/src/server.cee:448`, `H3_REQUEST_REJECTED`). These children already have the shape of an
  isolation unit; what is missing is a non-waiting `trySpawnIsolated` (§5).
- **What the probe showed** (§8): a connection served as an isolated child that owns its `PlainStream` and clones
  of an `Arc<Box<dyn ConnectionHandler + Send + Share>>`, an `Arc<Box<dyn Acceptor + Send + Share>>` and the
  `Drain` passes the checker. It makes a `ConnectionContext` that borrows its own drain. When the handler traps,
  the child ends alone, the server receives the `TaskCrash`, and the peer sees the end once the kept closer is
  shut. The other connections, one of them open across the trap, go on being served.

## 2. Decision

### 2.1 `ConnectionServer.runIsolated`

```ceesafe
public async fn runIsolated(
    incoming: borrow dyn Incoming,
    acceptor: Arc<Box<dyn Acceptor + Send + Share>>,
    handler: Arc<Box<dyn ConnectionHandler + Send + Share>>,
    limits: borrow ConnectionLimits,
    drain: borrow Drain,
) -> Result<ServerReport, NetError>
```

- **One accepting task and one isolated child a connection.** The task loops in an
  `Async.tasks(limits.workers, …)` set: it takes the next stream, keeps its closer (§2.2), and starts
  `await set.spawnIsolated(NetError.Reset, async move () => serveOne(…))`. The child owns the stream and clones of
  the acceptor, the handler, the drain (`drain.share()`) and the counters (an `Arc<Counters>`). It reads the PROXY
  header, hands the stream to the acceptor, makes the `ConnectionContext` from its own drain, serves, and closes,
  exactly as a worker does now (`server.cee:337` on).
- **Admission is the set's limit.** `spawnIsolated` waits while `limits.workers` children run (CeeSafe §40.5), so
  nothing is accepted while all are busy and the kernel's backlog holds the rest, as today. `limits.workers` keeps
  its meaning: the most connections served at once.
- **The drain** is today's: the accepting task stops at once; the children have `limits.graceMs` to finish and are
  then cancelled, and the report says `forced`.
- **A crash** is counted in a new `ServerReport.crashed` and is not a `failed` connection. The error the set
  receives for it (`NetError.Reset`) is never reported, so no `NetError` variant is added: a new variant would
  break every `match` on `NetError` without a `_` arm, as `AsyncError.Closed` did (`docs/toolchain.md`, 0.44.0).
- **`ServerReport.workersUsed`** becomes, under `runIsolated`, the most connections served at once. Its doc comment
  says both.
- **`run` is unchanged.** It keeps its borrows, its worker frames that are reused, and its cost. A program chooses.

### 2.2 The closer, and what the accepting task keeps

- `TcpTransport.closer()` and `PlainStream.closer()`: `StreamCloser?`. It is the stream's `AsyncTcpStream.closer()`,
  or null for a mock or where the system gave none.
- The accepting task takes the closer before the child gets the stream. It keeps the child's number (what
  `spawnIsolated` returned) and the closer in a table.
- The task races the source against the set's crash channel (`Isolation.watch`, capacity `limits.workers`) and a
  channel of finished children:
  - **A child that ends** sends its connection's number on the finished channel as its last step (`trySend`;
    the channel holds `limits.workers`, so it is never full). The task drops that closer.
  - **A child that crashes** sends nothing. Its `TaskCrash` names it; the task shuts its closer (`shutdown()`,
    so the peer sees the end and stops waiting) and drops it.
- **What a crash leaves** is what CeeSafe ADR 0175 §2.4 says. The child's memory is leaked and counted. Its
  socket's descriptor is leaked and not counted; the shut socket holds no buffers worth speaking of, but the
  descriptor counts against the process's limit. So does a TLS connection's OpenSSL state, which is leaked whole.
  A program that runs long decides from `Isolation.report()` when to be replaced. httpserver's worker drains past
  a threshold of crashes or leaked bytes, and its supervisor starts another (§4).

### 2.3 Streams: HTTP/2 and HTTP/3

- A stream's task is started with `set.trySpawnIsolated(crashed, …)` (U25, §5) where it is now started with
  `trySpawn`. A full set refuses the stream as today.
- The connection's task watches its set (`Isolation.watch`) and keeps the child's number in the stream's `Link`.
  On a crash it resets the stream: HTTP/2 `RST_STREAM` with `INTERNAL_ERROR` (RFC 9113 §7); HTTP/3 the stream's
  reset with `H3_INTERNAL_ERROR` (RFC 9114 §8.1). It then drops the `Link`, as for a stream the client reset.
- The crashed task's `StreamKit` is not returned to the pool, and a new one is made when the pool is empty.
- Inside an isolated connection (§2.1) a stream is a unit of its own (CeeSafe ADR 0175 §2.2). A trap in a stream's
  handler costs that stream; a trap in the connection's own task (the frame loop, HPACK) costs the connection.
- **Where the trap is under C, the process still ends.** The QUIC connection's task calls ngtcp2 and nghttp3. Only
  their callbacks into CeeSafe run under C frames, and those are the shims' own code, not the application's. A
  stream's handler never runs under them.

### 2.4 `web_server` and the examples

- `Server.runIsolated(incoming, acceptor: Box<dyn Acceptor + Send + Share>, limits, drain)` is `Server.run` over
  `ConnectionServer.runIsolated`, with its `HttpService` (the connection handler, which holds the application's
  `Arc`) boxed into an `Arc` of its own.
- `examples/http1` gets a `--isolated` switch, and a route that traps, for the check in §8.

### 2.5 Toolchain

`runIsolated`, `closer()` and the streams' `trySpawnIsolated` need 0.75.0 and U25. Every package's minimum rises to
the release that has U25, recorded in `docs/toolchain.md` with the run that checked it, as each rise has been.
The plain `run` keeps working with it unchanged.

## 3. Options weighed

- **Isolate the existing workers** (each worker an isolated child looping as now). A trap would end a worker and
  every connection it would have served after, and the scope would have to start a replacement. Worse, a worker
  borrows the source and the turns, which an isolated child cannot. Rejected.
- **Isolate only the handler call** (an isolated child per request, inside the connection's task). The handler
  borrows the request and the exchange from the connection's frame, so it cannot be a child that holds no loan
  without copying the request and proxying the exchange through channels, as HTTP/2 does. For HTTP/1.1 that is a
  second design of the driver. The connection is the unit HTTP/1.1 can lose without harm to others.
- **Make `run` itself isolating**, with no second entry point. It would change the cost and the shape of every
  program's server (an allocation a connection instead of reused worker frames, `Arc` arguments). The owner's
  rule since M1 is that what costs is opt-in.
- **Process isolation only** (httpserver's supervisor). Kept, for what isolated tasks do not cover: `unsafe`
  code, C libraries, a computation that never awaits. It costs a worker's connections for each trap.

## 4. Consequences

- **A server can lose one connection, or one HTTP/2 or HTTP/3 stream, to a trap**, and goes on serving the rest in
  the same process. For httpserver, a worker no longer ends at a handler's trap. It drains when
  `Isolation.report()` passes a bound the application sets (crashes, leaked bytes), and the supervisor starts its
  replacement. Requests in flight on it finish first.
- **What is leaked is the owner's to bound.** Each crash leaks the connection's memory (counted) and its descriptor
  (not counted). A server that crashes often is recycled often. A server that crashes always has a fault, and
  the panic line names the unit (`… (isolated task 5731: ended alone)`).
- **Cost** (CeeSafe ADR 0175 §10's numbers, to be measured here): an isolated child that allocates, waits once and
  returns cost 255 to 273 ns, against 138 to 156 ns for an ordinary one. A connection's frame is allocated for
  the connection and freed after, where a worker's was reused. The resident size then follows the connections
  served at once, which is what `Turns` was written to get (cee-proxy's soak). The benchmark gate (Web ADR 0005
  §4.2) decides whether `runIsolated` may be the default of anything; until then it is opt-in.
- **State the handler shares** with other connections through `Arc`, channels and `Share` types follows CeeSafe ADR
  0175 §2.6. A `Mutex` held at the trap is poisoned. A change to several atomics that must agree needs a
  `Critical`. `web_core`'s own shared state (`Drain`: a channel and one `AtomicBool`) needs none.

## 5. Upstream: U25, a non-waiting isolated spawn

`trySpawn` exists so that a task which cannot wait, here the connection's frame loop, can start a child or refuse at
once. `spawnIsolated` always waits for room (CeeSafe ADR 0175 §2.1); 0.75.0 has no `trySpawnIsolated`. A frame loop
that awaited room would stop reading frames until a stream ended, and the streams wait for that loop to send their
data. U25 asks CeeSafe for `set.trySpawnIsolated(crashed, async move () => …)`: `u64?`, the child's number, or null
when the set is full, as `trySpawn`'s `bool`. It is recorded in `docs/ceesafe-upstream-work.md` with this ADR.
CeeSafe.Web's rule (no workarounds for CeeSafe gaps) leaves the streams' part waiting for it. The connections' part
(§2.1, §2.2) needs only 0.75.0.

## 6. What it does not do

- No isolation for `ConnectionServer.run`, for the proxy's tunnels, for WebSocket sessions beyond their connection,
  or for the client.
- No restart policy: what to do after many crashes is the application's (httpserver: drain and be replaced).
- No protection from `unsafe` code or from C (CeeSafe ADR 0175 §3); the process is still the unit for those.

## 7. Questions for the owner

1. **A second entry point, `ConnectionServer.runIsolated`, with `run` unchanged** (§2.1, §3)? *Recommended: yes.* **Accepted 2026-11-19.**
2. **A crash costs the connection, whose socket the server shuts through the kept closer, and its memory and
   descriptor are leaked** (§2.2)? *Recommended: yes*: CeeSafe ADR 0175 §2.4 leaves no sound alternative. **Accepted 2026-11-19.**
3. **No new `NetError` variant**: a crash is `ServerReport.crashed`, and the set's error for it is never seen
   (§2.1)? *Recommended: yes.* **Accepted 2026-11-19.**
4. **Streams isolated with U25's `trySpawnIsolated`**, asked of CeeSafe now, with the streams' part waiting for it
   (§2.3, §5)? *Recommended: yes.* The alternative is that the frame loop awaits room, which can stall a connection. **Accepted 2026-11-19.**
5. **The minimum rises to the release with U25** for every package (§2.5)? *Recommended: yes*, in one step with
   the streams' part. If U25 is late, the connections' part lands alone with 0.75.0. **Accepted 2026-11-19.**

## 8. Tests

**Run (2026-11-19, macOS arm64, ceesafec 0.75.0 release).** A probe outside the repository: a package depending on
`web_core` and `web_transport` at `17f4495` by path. An `AsyncTcpListener` on 127.0.0.1. Each connection is
`set.spawnIsolated(NetError.Reset, async move () => …)` over `PlainAcceptor` and a `ConnectionHandler` that echoes
and traps (index out of bounds) on a line starting with `x`. The server races `accept` against the crash channel
(`Isolation.watch`) and shuts the crashed child's kept closer. A Python client opened two connections and trapped
one: the trapped one read end-of-stream, the other echoed before and after. Three more trapping connections read
end-of-stream, and a new connection echoed. The process lived, and printed `ceesafe panic: index out of bounds at
src/main.cee:20:31 (isolated task 2: ended alone)` for each trap. `Isolation.report()` counted 1 to 4 crashes and
256 bytes leaked for each. The probe joins `tests/probes/` (with a manifest, as a package) when this is
implemented.

**Proposed:**
- `web_transport` tests (native; the interpreter has no closer, and its `leakedBytes` is 0): a `MockIncoming` whose
  handler traps on the third of five connections; the other four are served, `report.crashed` is 1 and `failed` is
  0. A drain with a crashed child left behind ends within the grace.
- A check that needs real sockets (`tools/isolated_check.py`, as CeeSafe's `tools/isolated_check.py`), on
  `examples/http1 --isolated`. A route that traps, over HTTP/1.1 keep-alive and over TLS: the peer sees the end
  and a second connection is answered. Then 1 000 traps, after which `Isolation.report()` shows 1 000 crashes and
  the process's descriptors grew by 1 000 and no more.
- Streams, once U25 is there: an `h2load` run with a route that traps on one request in 100. Every other request is
  answered, the trapping streams get `RST_STREAM` `INTERNAL_ERROR`, and the connection stays up. The same over
  HTTP/3 with the `h3` client the M10 tests use.
- The sweeps (`run_tests.py --sanitize --alloc-sweep --cancel-sweep`) over the new tests.
- The benchmark (Web ADR 0005 §4.2): `benchmarks/http` with `run` and `runIsolated`, `h2load --h1 -c 64 -t 4`,
  three runs each, on the benchmark machine. The difference is recorded here.
- httpserver: `tools/supervisor_check.py` with a route that traps on an isolated application. One request is lost
  per trap, no worker ends, and the worker drains and is replaced past the threshold.

## 9. Implementation notes (2026-11-19)

The connections' part (§2.1, §2.2, §2.4) is implemented with ceesafec 0.75.0; the streams' part (§2.3) waits for U25.
It differs from §2 in these places.

- **A new connection is put in the table before its task starts** (`Isolated`, `packages/web_transport/src/
  server.cee`), and the task's number is filled in after: a connection can finish before `spawnIsolated` returns.
- **A race that both sides win gives both values** (`Raced.other`). The accepting task keeps a stream or a finished
  connection that arrived in the losing child. The first version dropped them and hung after its last connection.
- **`workersUsed` counts the tasks serving at once**: each task counts itself in and out, and a crash takes its
  task off. Counting the table instead counted finished connections not yet settled.
- **The channels are made in `runIsolated`**, before the set, with the other values its tasks share. Made inside the
  set's body, the watched one is left live in the interpreter (B23).
- **`ConnectionLimits.copy()` and `TrustedProxies.copy()`** are new: the tasks share one copy of the limits in an
  `Arc`, since `ConnectionLimits` holds a `Vector` and is not Copy.
- **The steps of one connection** (PROXY header, acceptor, handler, graceful close) are now one function,
  `serveConnection`, which `run`'s workers and `runIsolated`'s tasks both call. `run` behaves as before.
- **`examples/http1` takes `CW_ISOLATED=1`** rather than a `--isolated` switch, like its other settings, and has
  `GET /trap`.
- **The traps are checked by `tools/isolated_check.py`, not `ceesafec test`**: the test runner reports a test whose
  isolated task trapped as panicked (B22). The `web_transport` tests of `runIsolated` (`tests/isolated.cee`) cover
  admission and the drain. CI runs the check on Linux and macOS.
- **A client that half-closes before reading** is not told its connection ended on macOS (B21). The check's clients
  do not half-close.

**Run (2026-11-19, macOS arm64, the 0.75.0 release):**
- `web_transport`'s tests: 44 passed natively and in the interpreter. The four of `runIsolated` pass the
  allocation-failure sweep (179 runs) and the cancellation sweep (245 runs). `web_server`'s: 27 passed in both.
- `tools/isolated_check.py`, against `examples/http1` with `CW_ISOLATED=1`, passed every step:
  - a trap on a keep-alive connection ends that connection (`RemoteDisconnected`), and another keep-alive
    connection open across it is still answered;
  - 200 more traps each end their connection, in 0.1 s for all 200;
  - a new connection and the old keep-alive one are answered after them;
  - the descriptors grew by 202 for 200 traps;
  - `/stop` drains the server, and it exits with 0;
  - there is one `ended alone` line for each of the 201 traps;
  - without `CW_ISOLATED`, a trap ends the process (status -6), as before.
- `check`, `fmt --check` in the 14 packages and 12 examples, `check_layers.py`, `api_check.py` (new:
  `ConnectionServer.runIsolated`, `Server.runIsolated`, `TcpTransport.closer`, `PlainStream.closer`,
  `ConnectionLimits.copy`, `TrustedProxies.copy`; the field `ServerReport.crashed`).

**Run on Linux** (Ubuntu 24.04, x86-64, the release's linux-x64 binary): `tools/isolated_check.py` passed every
step (200 traps in under 0.1 s, descriptors +202), and `web_transport`'s four tests of `runIsolated` passed. gcc 13.3
warns `-Wclobbered` about two variables of the runtime's jump point (`t`, `outer`, `ceesafe_rt_async.c:3105`,
`:3126`), which the build does not treat as errors.

**Not done yet:** the streams (§2.3, U25); the check over TLS; the benchmark of §8; Windows and FreeBSD, left to CI.
