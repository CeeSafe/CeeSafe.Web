# Web ADR 0020 — An isolated request's file goes to its connection, and out with sendfile

**Status:** accepted 2026-11-21 (the owner: "Tackle still open items"; it is Web ADR 0017's question 6, recommended
there as a follow-up). Implemented (§4).
**Scope:** `web_http`'s `Exchange.sendFileOwned` and `StreamSink.sendFile` (a method with a default), `web_http1`'s
isolated relay (a `ToConnection.File` message). HTTP/2 and HTTP/3 send a file as DATA, as before.

## 1. Context

An isolated HTTP/1.1 request (Web ADR 0017) runs in a task of its own and writes through channels to its connection's
task. `Exchange.sendFile` lends the exchange the handler's `File`, and a loan cannot cross into another task: so an
isolated request's file was read 16 KiB at a time on a worker thread and its bytes copied through the channel, where
an exchange over the connection hands the file to the kernel (`sendfile`, Web ADR 0013 §3.3). A static file served
with requests isolated lost `sendfile`. A `File` cannot be duplicated (lib/std has no `dup`), so the file itself must
move.

## 2. Decision

- **`Exchange.sendFileOwned(file: File, offset, length)`** takes the file, where `sendFile` borrows it. Over an
  HTTP/1.1 connection it is `sendFile`. Over a stream it counts the length against the declared one as `sendFile`
  does and gives the file to the stream's sink.
- **`StreamSink.sendFile(file, offset, length)`** has a default that reads the file in pieces and sends them with
  `sendData`, as every sink did: HTTP/2's and HTTP/3's keep it.
- **The isolated request's sink** posts `ToConnection.File(file, offset, length)`; the connection's task sends it
  through its own exchange's `sendFile`, with `sendfile` over a plain stream, after what the request wrote before and
  before what it writes after, since the channel keeps their order.
- An error the connection's task meets sending it (the file shorter than `length`, the client gone, the write
  timeout) closes the connection, as on the direct path, but is not returned to the handler, whose `sendFileOwned`
  returned once the file left.

## 3. Consequences

- A handler that opens the file for the response (a static-file handler) should give it: `sendFileOwned`. One that
  must keep the file (to send it twice) lends it with `sendFile`, and an isolated request then copies it.
- The public API gains a method on `Exchange` and one on `StreamSink`, with a default (`docs/api/web_http.md`).

## 4. Implementation

`packages/web_http/src/exchange.cee` (`sendFileOwned`, and `fileCounted`, which `sendFile` shares),
`packages/web_http/src/stream.cee` (the default), `packages/web_http1/src/isolated.cee` (the message, the relay, the
sink's override). `web_http1`'s test `a_file_given_is_sent_the_same_isolated`: bytes 2 to 11 of a file, given by the
handler, are the same bytes in the same writes direct and isolated, and the next request on the connection is
answered; HEAD is the same both ways. Without the sink's override (the copy) the writes differ and the test fails.
httpserver's `httpserver_static` gives its files with `sendFileOwned`.
