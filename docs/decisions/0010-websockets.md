# Web ADR 0010 — WebSockets

**Status:** accepted 2026-11-03 (M6). §6's four questions were answered by the owner: Autobahn on this Mac's Docker;
permessage-deflate in pure CeeSafe; a client too; same-origin by default. **Scope:** implementation plan §7 M6:
`web_ws` (RFC 6455 and RFC 7692), the upgrade in `web_http`, and the client's connect in `web_client`.

## 1. Context

What M0–M5 provide, and what CeeSafe allows (checked 2026-11-03 on ceesafec 0.9.4):

- **The upgrade has no way through yet.** A handler gets an `Exchange` (`web_http`), which frames a response as
  HTTP/1.1. Nothing sends a 101, and nothing hands over the connection's bytes afterwards.
- **Reads may be cancelled; writes may not** (Web ADR 0001 §3.4). A cancelled read keeps the bytes the kernel
  delivered. A cancelled or failed write poisons the transport. So a session may race reads, timers and channel
  receives, but never a write: this is M3's option C again (Web ADR 0008 §2).
- **A session can hold the exchange.** Probe `tests/probes/ws-session-region.cee` builds and gives the expected
  output, interpreted and native. In it:
  - a `Session<region r>` holds a `borrow mut(r) Exch<r>`;
  - its `receive` method races a read through the exchange against an outbound channel and a timer;
  - it writes through the exchange after the race.

  Two rules shaped it:
  - Race arms cannot capture disjoint fields of one `this` when one arm changes it (N0241). So the race lives in a
    top-level function that takes the fields as separate parameters, as `web_sse`'s `wake` does.
  - A field that is already a reference cannot be borrowed again into a local (N0246).
- **No SHA-1 or base64 in lib/std.** `Sec-WebSocket-Accept` needs both. They are ordinary library code, so `web_ws`
  implements them, checked against RFC 3174's and RFC 4648's vectors and RFC 6455 §1.3's worked example.
- **No public random source in lib/std.** The runtime seeds `HashMap` from the OS (`ceesafe_random_key`), but a
  program cannot ask for random bytes. A client needs them: `Sec-WebSocket-Key` (RFC 6455 §4.1), and every frame's
  masking key, which "MUST be derived from a strong source of entropy" (§5.3). That is upstream item **U16**. A server
  needs no randomness.

## 2. Options for the session's API

| Option | How an application uses it | Cost |
|---|---|---|
| A. Callbacks (`interface WebSocketHandler { onMessage … }`) | the session calls the handler for each message | the handler must reach the session to reply. An interface method cannot name the session's region (methods take no region parameters), and replies through a queue deadlock when the queue is full |
| B. Two channels, inbound and outbound, with the application in its own task | `recvAsync` and `sendAsync` | the session cannot wait for room in the inbound channel and for outbound messages at once (a race arm cannot move the message it sends), so a slow application blocks pings and outbound messages, and can deadlock with them |
| **C. Pull: `ws.receive(into)` and `ws.send(...)`** | a loop in the handler | `receive` reads only when asked, so a slow application slows the peer through TCP (backpressure for free). Other tasks send through a bounded queue, which `receive` drains while it waits |

**C** is chosen. It is also the shape of the widely used libraries (Go's gorilla/websocket, Rust's tungstenite).

## 3. Decision

### 3.1 The engine (`web_ws`, sans-I/O)

- **Frames.** A `FrameDecoder` takes bytes as they arrive and yields frame heads and payload pieces:
  - It handles 7-, 16- and 64-bit lengths. A length past `maxFrameBytes`, or with the 64-bit form's top bit set, is
    a protocol error.
  - The RSV bits must be 0 unless an extension negotiated them (RSV1 for permessage-deflate). Opcodes 3–7 and 11–15
    are refused.
  - Masking follows the role: a server refuses unmasked frames, and a client refuses masked ones (§5.1).
  - Control frames carry at most 125 bytes and are never fragmented (§5.5).

  A `FrameWriter` writes heads, and masks with a key the caller gives it, since the engine has no random source.
- **Text.** UTF-8 is validated incrementally, across fragments and across reads, with a DFA (RFC 3629 §4: no
  overlongs, no surrogates, nothing above U+10FFFF). A message that fails is closed with 1007. A partial sequence is
  refused as soon as no valid completion exists, so Autobahn's "fail fast" cases pass.
- **Messages.** Fragments are assembled up to `maxMessageBytes`; past it, the connection closes with 1009. Control
  frames may arrive between fragments. A continuation with no message open, or a new data frame while one is open,
  is a protocol error (1002).
- **Close.**
  - Codes are checked against §7.4: 1000–1003, 1007–1011 and 3000–4999 are accepted. 1004–1006, 1015 and the rest
    get 1002.
  - A close payload of exactly 1 byte, or with a reason that is not UTF-8, also gets 1002.
  - The engine tracks the handshake's state: open, closing (we sent a Close), and closed.
- **Handshake helpers.**
  - `acceptKey(key)` returns base64(SHA-1(key + the GUID)).
  - The upgrade request is validated per §4.2.1:
    - `GET`, HTTP/1.1, with a `Host`;
    - `Upgrade` contains the token `websocket` and `Connection` contains `Upgrade`, both case-insensitive;
    - `Sec-WebSocket-Key` is base64 of exactly 16 bytes;
    - `Sec-WebSocket-Version` is `13`.
  - The subprotocol is the first in the server's list of preferences that the client offered (§4.2.2).

### 3.2 The upgrade (`web_http`)

- `Exchange` gains the switch to another protocol:
  - **The switch.** It is allowed only before anything else was sent, and only for a request without a body. It
    sends the head the handler set, with status 101, and flushes it.
  - **Afterwards,** the exchange gives raw reads and writes:
    - reads return the bytes already received after the head first, then the transport's;
    - reads may be cancelled, as transport reads may;
    - writes go to the socket under `Limits.writeTimeoutMs`.
  - **The end.** The connection is never reused: the server closes it when the handler returns.

  The exact signatures are settled in stage 2 with a probe (§5).
- **Refusals** are ordinary responses from the exchange, sent before any switch:
  - 400 for a malformed upgrade;
  - 426 with `Sec-WebSocket-Version: 13` for another version (§4.4);
  - 403 for a refused origin (§3.4).

### 3.3 The session (`web_ws`)

- **Accepting.** `web_ws.accept(exchange, options)` is a top-level function, since it takes a region. It validates
  the request, checks the origin, negotiates the subprotocol and extensions, switches, and returns a
  `WebSocket<r>`.
- **`receive(into)`** gives the next whole message: `Text` or `Binary` into `into`, or `Closed(code, reason)`.
  - **Ping.** A ping is answered with a pong carrying the same payload; a pong is noted.
  - **Peer's Close.** It is answered with a Close of the same code (1000 if there was none). The session then waits
    for the peer's end of the stream, at most `closeTimeoutMs`, and returns `Closed`.
  - **Protocol errors.** These include bad framing, bad UTF-8 and over-limit messages. The session sends a Close
    with the matching code, waits as above, and returns `Closed` with that code.
  - **Meanwhile.** While it waits for a frame it races the read against the outbound queue and the keep-alive
    timer, and writes what they produce:
    - a queued message is sent;
    - after `pingIntervalMs` of quiet a ping goes out;
    - a peer that does not answer a ping within `pongTimeoutMs` is closed with 1011 (going away is 1001).
- **Sending.** `send(Text|Binary, bytes)` writes one message as one frame, for messages up to `maxFrameBytes`, and
  as fragments of `fragmentBytes` past that. `close(code, reason)` starts the handshake; the next `receive`
  finishes it.
- **The outbound queue.** `ws.sender()` gives a `Send` handle for other tasks: a bounded channel of owned messages,
  with M3's overflow policies `Block` or `Disconnect`. Sending from another task never writes; only the session's
  task writes.
- **Options** (`WebSocketOptions`), with these defaults:

  | Option | Default |
  |---|---|
  | `maxFrameBytes` | 1 MiB |
  | `maxMessageBytes` | 16 MiB |
  | `fragmentBytes` | 64 KiB |
  | `pingIntervalMs` | 30 s |
  | `pongTimeoutMs` | 30 s |
  | `closeTimeoutMs` | 5 s |
  | `queueLength` | 64 |
  | `subprotocols` | none |
  | `allowedOrigins` | none: same-origin only, §3.4 |
  | `deflate` | on, §3.5 |

### 3.4 Origins (the owner's choice: same origin)

A request without `Origin` is accepted: browsers always send it, so its absence means a non-browser client (RFC
6455 §10.2). A request whose `Origin` names the request's own scheme, host and port is accepted. Any other origin
must be in `allowedOrigins`, compared exactly after ASCII lower-casing; otherwise the response is 403. This blocks
cross-site WebSocket hijacking by default.

### 3.5 permessage-deflate (RFC 7692; the owner's choice: pure CeeSafe)

- **Inflate (RFC 1951)** handles stored, fixed and dynamic Huffman blocks, written in CeeSafe in `web_ws` as a
  streaming, sans-I/O decoder. It keeps a window of 2^`window_bits` bytes, and its output is bounded by
  `maxMessageBytes`, so a decompression bomb ends at the limit (1009).
- **Deflate** compresses with a hash-chain match finder over the window, emitting fixed or dynamic Huffman blocks,
  then the `00 00 FF FF` flush that RFC 7692 §7.2.1 removes.
- **Negotiation.**
  - Supported: `server_no_context_takeover`, `client_no_context_takeover`, `server_max_window_bits` and
    `client_max_window_bits` (8–15).
  - The server accepts the first offer it can honour, and declines the extension otherwise.
  - Window bits of 8 are treated as 9, as zlib does, and the response says so.
- **Context.** With context takeover, both directions keep their window across messages, which costs memory per
  connection. `deflate` can be switched off, and the memory is counted in the ADR's tests.
- **Correctness oracle.** Python's `zlib` makes the test inputs, both compressed by zlib and to decompress with zlib.
  A fuzz target runs the inflater under the sanitizers.

### 3.6 The client (`web_client`; the owner's choice: a client too)

- **Connecting.** `WebSocketClient.connect(url, options)` dials as the client does (DNS, Happy Eyeballs and TLS
  through the `Connector`). It writes the upgrade request and checks the 101 response:
  - `Sec-WebSocket-Accept` must match;
  - the subprotocol and extensions must be ones it offered.
- **No pool.** An upgraded connection never enters the pool.
- **Layering.** It needs `web_client` to depend on `web_ws`, which Web ADR 0004 §3 does not list yet; that is an
  amendment there.
- **Blocked by U16:** the key and the masking keys need random bytes. Until CeeSafe provides them, the client stays on
  a branch, the same as M5 did with B15. Nothing in it uses a fixed or predictable key.

## 4. Consequences

- **The only C stays OpenSSL's shim and QUIC's libraries.** Compression is CeeSafe code with its own tests and fuzzing.
- **Servers never need randomness.** Only the client waits for U16.
- **One task per connection.** One cancelled read happens per wake-up: an outbound message or a ping. None happens
  while frames arrive.
- **What it costs per connection.** The read buffer (`Limits`), the message buffer (up to `maxMessageBytes`), and
  with deflate the two windows (64 KiB at 15 bits) plus the compressor's hash table.

## 5. Plan and tests (the M6 exit gate, plan §7)

1. **Engine:**
   - SHA-1 (RFC 3174 §7.3's vectors), base64 (RFC 4648 §10), and `acceptKey` (RFC 6455 §1.3's example);
   - the frame decoder and writer;
   - UTF-8 (Markus Kuhn's stress cases);
   - messages and close codes;
   - every byte of every test input fed at every split.
2. **Server:** the exchange's switch, `accept`, `WebSocket`, `examples/ws_echo`, and `tests/interop/ws.py` (Python's
   `websockets` library if installed, else a raw socket client). Autobahn's **fuzzingclient** runs against
   `examples/ws_echo`, with every case passing, or "non-strict" with the reason recorded. Also:
   - malformed frames;
   - a slow consumer (the peer stops reading: the write deadline);
   - a slow producer (the queue's policies).
3. **permessage-deflate:**
   - inflate and deflate against zlib's vectors and round trips;
   - a fuzz target;
   - Autobahn's cases 12 and 13.
4. **Client:** blocked by U16; then Autobahn's **fuzzingserver** against a client example.
- **Sweeps:** allocation failure and cancellation over `web_ws`'s tests (`tools/run_tests.py`).

## 6. Questions for the owner (answered 2026-11-03)

1. **Where Autobahn runs:** Docker on this Mac.
2. **permessage-deflate:** in pure CeeSafe, in M6.
3. **A client:** yes, in `web_client`, with Autobahn's fuzzingserver.
4. **Origins:** same-origin by default, with an allow-list.

## 7. Amendment 1 (2026-11-03, M6 stages 2 and 3, as built)

1. **The switch (§3.2)** is `Exchange.switchProtocols(protocol)`.
   - It sets 101 and adds `upgrade: <protocol>` and `connection: upgrade` through `ResponseHead.upgradeTo`, since
     handlers may not set `Connection`.
   - It sends the head with the handler's other fields, and marks the exchange done and the connection not
     reusable.
   - Then `readRaw(buffer)` gives the bytes received after the head first, and `writeRaw(bytes)` writes within
     `Limits.writeTimeoutMs`. `switched()` tells a driver which path was taken.
2. **`accept(exchange, request, options, secure)`.**
   - It takes the request separately: handlers receive it beside the exchange.
   - It takes `secure` from the application, because neither the exchange nor the connection context records TLS.
3. **permessage-deflate (§3.5).**
   - The compressor writes one fixed-Huffman block per message and keeps no context, which RFC 7692 §7.2.2 allows
     whatever the agreement. It respects `server_max_window_bits` (8-15).
   - The inflater always keeps a 15-bit window, so any client window works, and the response never names
     `client_max_window_bits`.
   - The response repeats `server_no_context_takeover`, `client_no_context_takeover` and `server_max_window_bits`
     when they were offered.
   - With `client_no_context_takeover`, the inflater drops its window after each message.
   - A compressed message is collected whole and inflated at its end, bounded by `maxMessageBytes` (1009 past it,
     1007 if it does not inflate). Its text is checked after inflating.
