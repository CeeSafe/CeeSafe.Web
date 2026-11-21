# Web ADR 0022 — An isolated request's small response goes back with its kit, in one message

**Status:** accepted 2026-11-21 (the owner: "Take both in one run", after it was measured as a small gain).
Implemented (§4).
**Scope:** `web_http1`'s isolated relay (`isolated.cee`: `RequestKit.held`, `Held`, `RequestPort`'s sink methods, the
connection's handling of `Done`). No public API change.

## 1. Context

An independent benchmark found per-request isolation (Web ADR 0017) serving 30 to 40 % fewer HTTP/1.1 requests a
second than isolation per connection. Reproduced on a Xeon D-2123IT, httpserver's `bench` with one worker on one core,
`/small` at 64 connections: 72 800 requests a second direct, 47 200 isolated, about 6 µs more for each request, all of
it in user space (both make one `read` and one `sendto` a request).

An isolated request's small response took three messages to its connection: the head (`Head`, a `HeaderBlock`
allocated for it), the body (`Data`, the bytes copied into a `Vector` allocated for them) and `Done` with the kit.
Each is a channel send, a wake of the connection's task and a turn of its relay; with the two allocations, about
1.9 µs of the 6.

## 2. Decision

The request's sink holds a response that can wait. Its head, and its body when the body comes whole after it, are
kept in the request's kit (`Held`: a `HeaderBlock` and a `Vector<u8>` the kit keeps between requests), and the
connection applies and writes them when the kit comes back with `Done`, before finishing the response as it did after
`Data`. The sink sends what it holds as the old messages, and goes on as before, whenever something must reach the
connection first: a body sent in more than one piece (a flush, a stream), trailers, a file (Web ADR 0020), a wait for
body bytes (`WantBody`) or for the client's close (`WatchPeer`).

A request that traps holding a response has sent nothing, so it is answered 500, as one that trapped before its head
was.

## 3. Consequences

- A small response is one message, and once the kit's buffers have grown no allocation for its body.
- The gain is smaller than the share of the messages suggested: the rest of the cost is the request's task itself
  (§4), which this does not change.

## 4. Implementation and measurements

`packages/web_http1/src/isolated.cee`. `web_http1`'s 40 tests pass unchanged, among them every isolated case (pipelined
requests, bodies read in pieces, trailers, streamed responses, `HEAD`, `100-continue`, a file, a trap before and
after the head, a hundred requests in order). A uprobe on the bench at `/small`: 149 001 sends to the connection for
149 296 requests, one each (three before).

`/small`, 64 connections, isolated, one core, alternating runs: 47 200 to 48 300 requests a second against 45 400 to
47 100 before, 1 to 6 %, within the runs' spread. The profile after it: per request, the C compiler clearing and
copying the relay's large async frames, about 1.4 µs; the head parsed a second time in the request's task, about
1.0 µs; spawning, waiting for and waking the request's task, about 1 µs. The first is CeeSafe's (its code generation),
taken up there.
