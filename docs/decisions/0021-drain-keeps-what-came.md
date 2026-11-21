# Web ADR 0021 — A drain keeps the request, or the connection, that came in its turn

**Status:** accepted 2026-11-21 (the owner, on an independent benchmark of httpserver against Bandit and Cowboy:
"Yes, go ahead" to reproducing its crash tail and fixing it). Implemented (§4).
**Scope:** `web_core`'s `drainBegun` and `drainTaken` (new), `web_http1`'s `nextHead`, `web_transport`'s two accept
loops. `drainStarted` is unchanged.

## 1. Context

An independent benchmark ran httpserver (at `f44fc0d`, CeeSafe.Web `9c38d14`) on one Linux core under an open-loop
healthy load, 3 000 requests a second over 30 keep-alive connections, beside requests that crash. A crash costs its
connection, and a worker is replaced after 100 of them and drains. Healthy requests' p99 was 0.8 to 0.9 s and their
p99.9 1.0 s at 20, 200 and 1 000 crashes a second, with one, two or four workers; Bandit's p99 was 3.5 to 4.4 ms.
Every worst case was 1 000 ms: Web ADR 0019's `drainLingerMs`.

Reproduced on a Xeon D-2123IT (Linux 6.8, epoll), httpserver's `bench` pinned to one core, with a probe that logs each
slow request beside the server's lines: at every replacement exactly one connection's request, sent as the drain
began, was never read; the connection was closed at the end of the linger, 1 000 ms after the drain, and the client
sent it again. Three runs of 12 s at 20 crashes a second: p99.9 823 to 824 ms, 182 requests over 100 ms each (two
replacements, 91 requests each: those due on that connection in that second). Not on macOS (kqueue).

The cause is the race in `nextHead`'s idle wait: the read against `drainStarted`. Its read is a grandchild of the
race (`Transports.readWithin` runs it under `Async.deadline`), and a race's cancellation reaches it only once the
deadline's join has run. When the drain and the request's bytes come in one turn of the executor, the drain's child
returns first and the read, already woken, takes the bytes from the socket and returns them. `Async.race` keeps a
late child's value in `Raced.other` only when the first child returned `Ok`; `drainStarted` returns `Err(Cancelled)`,
so the race is that error and the bytes are dropped. Before Web ADR 0019 the connection then closed at once (the 1.2 to
1.5 % of healthy requests retried in httpserver's benchmark); since, it waits out the linger first.

The accept loops race `incoming.next()` against `drainStarted` the same way: a connection accepted in the drain's
turn is dropped, and its client sees it closed.

## 2. Decision

- **`drainBegun<T>(drain) -> Result<T?, NetError>`** waits for the drain and returns `Ok(null)`, for a race whose
  operation wraps its value, `Ok(Some(v))`. Both children returning `Ok`, a value of the drain's turn is
  `Raced.other`.
- **`drainTaken<T>(raced) -> T?`** is the operation's value whichever child returned first, or null when the drain
  came first and the operation had nothing.
- **`nextHead`** races the idle read against `drainBegun`: bytes that came with the drain are a request, served (its
  response says `connection: close`); nothing is the linger, as before.
- **The accept loops** race the accept against `drainBegun`: a connection that came with the drain is served by the
  draining server (its first request within the linger), not dropped.
- `drainStarted` stays for operations whose result can be dropped (a turn of the accept loop, a worker waiting).

## 3. Consequences

- A replaced worker costs a healthy client nothing it can see: no request read and dropped, no connection accepted
  and closed.
- `drainStarted`'s doc says when to use `drainBegun` instead.
- The public API gains two functions (`docs/api/web_core.md`).

## 4. Implementation and measurements

`packages/web_core/src/drain.cee`, `packages/web_http1/src/serve.cee` (`nextHead`), `packages/web_transport/src/server.cee`
(`acceptLoop`, `nextStream`). `web_core`'s tests: `drainTaken` on each outcome, the drain first with the
operation's value among them, and `drainBegun` against an operation before and after a drain. The interleaving
itself is the executor's, which a test cannot force; it is measured.

The probe on the Xeon, one core for the server, `bench` with one worker, 10 to 12 s each, before → after:

| | p99 | p99.9 | worst | over 100 ms |
|---|---|---|---|---|
| 20 crashes/s (three runs) | 0.4 → 0.3 ms | 823 → 2.0 to 4.3 ms | 1 003 → 7 to 12 ms | 182 → 0 |
| 200 crashes/s | 805 → 6.2 ms | 988 → 13.5 ms | 1 007 → 18 ms | 1 292 → 0 |
| 1 000 crashes/s | 767 → 13.2 ms | 987 → 18.4 ms | 1 015 → 24 ms | 958 → 0 |
| 200 crashes/s, per-request isolation | 675 → 0.4 ms | 973 → 1.4 ms | 1 003 → 3 ms | 787 → 0 |
