# Web ADR 0014 — HTTP/3 and QUIC: ngtcp2 and nghttp3 behind a sans-I/O shim

**Status:** accepted 2026-11-09 (M10). The owner accepted each recommendation in §7:
- OpenSSL ≥ 3.5 only for `web_quic`, with `web_tls` kept at ≥ 3.0;
- ngtcp2's OpenSSL backend, pinned to 1.26.0, with its one rule in the shim;
- a C shim that queues the libraries' events, not callbacks into CeeSafe;
- two packages, `web_quic` and `web_http3`;
- CI's Linux jobs on `ubuntu-26.04` with one `ubuntu-24.04` job without the QUIC packages, and OpenSSL 3.5 LTS built
  into a prefix on hanan;
- 0-RTT off by default, safe methods only, and 425 for the rest;
- no HTTP/3 in `web_client` in M10.
**Scope:** implementation plan §7 M10. It covers:
- OpenSSL 3.5, and where it is required;
- ngtcp2 and nghttp3, vendored, with ngtcp2's OpenSSL crypto backend;
- a sans-I/O QUIC adapter over `AsyncUdpSocket`, with GSO and ECN;
- connection IDs and routing, stateless resets, and Retry for address validation;
- HTTP/3 streams mapped into `web_http`, and QPACK with a blocked-streams limit;
- 0-RTT for safe methods only, `Alt-Svc` from the HTTP/1.1 and HTTP/2 listeners, migration, and GOAWAY with drain.

The plan's choices stand (§3, §11.2): ngtcp2 and nghttp3, sans-I/O C, over CeeSafe's UDP, with one TLS provider, OpenSSL.

## 1. Context

Checked 2026-11-09 on `main` at `1b98ea7` (v0.4.0), with ceesafec 0.22.0.

- **`web_http3` is a placeholder.** It has a two-line `src/lib.cee`, no tests, and the dependencies Web ADR 0004 §3
  gives it: `web_core`, `web_http` and `web_tls`.
- **UDP is in lib/std** (U6, CeeSafe ADR 0050, released in 0.9.0).
  - `AsyncUdpSocket` has `bind(address, options)` with `reuseAddress`, `reusePort`, `v6Only` and the buffer sizes.
  - It has `sendTo`, `recvFrom`, and `sendMsg` and `recvMsg`. These carry GSO segments, ECN and the local address
    (`IP_PKTINFO`). This is what QUIC needs.
  - It works under epoll, io_uring, kqueue, IOCP (`WSARecvMsg`) and WSAPoll.
- **`web_http`'s multiplexed exchange is protocol-neutral.**
  - `Exchange.forStream` takes a `dyn StreamSink`: `readBody`, `sendHead`, `sendData`, `push` and `copyTrailers`
    (`web_http/src/stream.cee:7`).
  - HTTP/2 implements it per stream (Web ADR 0011 §3.2). An HTTP/3 request stream can implement the same, so
    handlers, routing and middleware need no change.
- **C reaches CeeSafe.Web only through a shim behind a stated contract.**
  - `web_tls` compiles `c/tls_shim.c` from its manifest (`c = [...]`, CeeSafe ADR 0047). It links OpenSSL per system
    (`[target.*]`).
  - ADR 0002 §2 chose plain functions over byte buffers. It rejected callbacks into CeeSafe (option C): they are
    synchronous and re-entrant.
  - The project's rules allow C only in the OpenSSL shim and "the vendored QUIC libraries, each behind the contract
    its ADR states". This ADR is that contract.
- **ngtcp2 1.26.0 and nghttp3 1.19.0** are the current releases (2026-11-02). Both are MIT-licensed and sans-I/O:
  the application owns the sockets and the clock.
  - **The release tarballs** match GitHub's recorded digests:
    - `ngtcp2-1.26.0.tar.xz` is SHA-256 `7804aa0f…a7b085e`;
    - `nghttp3-1.19.0.tar.xz` is `228e17f5…a9209e155`.
  - **The C that would be vendored:** about 34 500 lines for ngtcp2 (46 files in `lib/`, plus `crypto/shared.c` and
    `crypto/ossl/`) and 19 500 for nghttp3 (31 files in `lib/`, plus `sfparse`). `version.h` comes prebuilt in each
    tarball.
  - **ngtcp2's OpenSSL backend.** `libngtcp2_crypto_ossl` needs OpenSSL ≥ 3.5, whose QUIC TLS API it uses. Its README
    still calls it **experimental**.
  - **Its one rule:** keep the `ngtcp2_conn` alive until `SSL_free`, or call `SSL_set_app_data(ssl, NULL)` before
    `SSL_free`. OpenSSL may still hold crypto data and the transport parameters until then.
- **OpenSSL on our machines and CI images** (`openssl version`):

  | Where | OpenSSL | ≥ 3.5 |
  |---|---|---|
  | macOS, Homebrew `openssl@3` | 3.6.4 | yes |
  | Windows 11 ARM64 VM, Shining Light | 4.1.0 | yes (4.x is untested by ngtcp2's README) |
  | hanan, Debian 12 (the benchmark host) | 3.0.22 | **no** |
  | fairooz and alghobashi, Debian 13 | 3.5.7 | yes |
  | GitHub `ubuntu-26.04` (its image's README) | 3.5.5 | yes |
  | GitHub `ubuntu-22.04`, `ubuntu-24.04` | 3.0.x | **no** |

- **`quic-interop-runner` needs Docker on Linux.** hanan has Docker 29.5.2, and fairooz and alghobashi have it too.
  The runner's images bring their own libraries, so the host's OpenSSL does not matter for it.

## 2. Options for driving ngtcp2 and nghttp3

Both libraries report what happened through C callbacks, called during `ngtcp2_conn_read_pkt` and
`nghttp3_conn_read_stream`: stream data, stream open and close, headers, and keys.

| | A. A C shim that queues events (proposed) | B. Exported CeeSafe callbacks | C. QUIC written in CeeSafe |
|---|---|---|---|
| C ↔ CeeSafe surface | plain functions over byte buffers, as ADR 0002 | `@export` functions called from C, re-entrantly | none |
| Re-entrancy | none: CeeSafe polls the queue after each call | a callback runs inside ngtcp2, and must not call back into it | — |
| Copies | callback data copied once into the queue | none extra | — |
| Size | one shim per package, about 1–2k lines of C | smaller C, larger unchecked surface | months; a new QUIC stack to audit |
| Fits the plan | yes | against ADR 0002's reasoning | against §3, which chose ngtcp2 |

**A** is proposed.
- **The callback copies the data.** ngtcp2's and nghttp3's data is valid only during the callback, so CeeSafe would
  copy it in any case.
- **The queue is bounded,** and is drained after every call into the library.

## 3. Decision

### 3.1 Packages and layering

- **`web_quic` (new):** QUIC over ngtcp2.
  - A sans-I/O engine, `QuicConnection`.
  - A driver: the UDP listener, connection-ID routing, and a task per connection.
  - It exposes QUIC streams as byte streams, so HTTP/3 is one user, and WebTransport or MASQUE could be others later.
  - Dependencies: `web_core` and `web_tls`. It needs `web_tls`'s `TlsConfig`, certificates and ALPN; QUIC uses TLS
    1.3 but no TLS record layer.
- **`web_http3`:** HTTP/3 and QPACK over nghttp3.
  - Request streams are mapped to `Exchange.forStream`.
  - `web_server` registers it, as it does HTTP/2.
  - Dependencies: `web_core`, `web_http` and `web_quic`. It no longer needs `web_tls` directly.
- **`web_server` gains `web_http3`, behind an option.** A server without HTTP/3 does not link it.
- **Web ADR 0004 gains an amendment:**
  - a `web_quic` row, and `web_http3`'s row changed;
  - both packages are added to `check_layers.py`'s TLS users. Neither is in `NO_TLS`, since both link OpenSSL, as
    `web_tls` does.

### 3.2 Vendoring

- **`third_party/ngtcp2-1.26.0/` and `third_party/nghttp3-1.19.0/` hold only the sources that are compiled:**
  - ngtcp2's `lib/`, `crypto/shared.c` and `crypto/ossl/`;
  - nghttp3's `lib/` and its bundled `sfparse`;
  - their public headers, licences (`COPYING`) and `AUTHORS`.
- **`tools/vendor_quic.py` makes the copy:**
  - it downloads the release tarballs and checks them against SHA-256 values recorded in the script;
  - it copies the listed files and writes the `config.h` each library needs;
  - it writes `third_party/README.md` (versions, checksums, licences, what was copied).
  - A version change is a run of the script and a commit, never an edit by hand.
- **The C is compiled from the packages' manifests** (`c = [...]`, CeeSafe ADR 0047): ngtcp2 by `web_quic`, nghttp3 by
  `web_http3`. No other build system is used.
- **Warnings:** the vendored files are compiled as their authors compile them. Our shims are compiled with the
  project's flags.

### 3.3 OpenSSL 3.5

- **Only `web_quic` requires OpenSSL ≥ 3.5,** checked when its shim is compiled
  (`#if OPENSSL_VERSION_NUMBER < 0x30500000L` → `#error`).
- **`web_tls` keeps ≥ 3.0** (ADR 0002 §3.1). A server on Debian 12 or Ubuntu 24.04 keeps TLS over HTTP/1.1 and
  HTTP/2, and only HTTP/3 needs the newer library.
  - This differs from the plan's "raise the OpenSSL minimum to 3.5", which was about the provider, not every
    package. §7 asks the owner.
- **ngtcp2's rule is the shim's.** `qs_conn_free` calls `SSL_set_app_data(ssl, NULL)` before `SSL_free`, then frees
  the `ngtcp2_conn` (§1).
- **OpenSSL 4.x (Windows)** is tested in stage 1. If ngtcp2's backend fails on it, Windows uses the 3.5 or 3.6
  series, and the README says so.

### 3.4 The shim's contract (`web_quic/c/quic_shim.c`, `web_http3/c/h3_shim.c`)

- **Handles.** CeeSafe holds opaque handles, `qs_conn *` and `h3_conn *`. Each is created and freed by the shim, and
  freed exactly once, by its owner's drop. No CeeSafe pointer is kept in C past a call.
- **Calls.** Each call takes byte buffers with explicit lengths, and the time as nanoseconds, passed in. The shim never
  reads a clock or touches a socket.
  - Datagrams in: `qs_read(conn, path, ecn, bytes, len, now)`.
  - Datagrams out: `qs_write(conn, out, cap, now, &path, &ecn, &gsoSize)`, which may coalesce up to the GSO count.
  - Stream data: `qs_stream_write(conn, id, bytes, len, fin, now)`.
  - Timers: `qs_expiry(conn)` and `qs_handle_expiry(conn, now)`.
- **Events.** Each library callback appends a record to a bounded queue in the handle: a kind, a stream ID, flags,
  and the data, copied. CeeSafe drains it with `qs_next_event(conn, buf, cap, &event)` after every call.
  - A full queue stops the read, which reports "would block". CeeSafe drains the queue and feeds the datagram again,
    so nothing is lost and memory stays bounded.
- **Errors.** ngtcp2's and nghttp3's error codes become a small `QuicError` set:
  - `Closed`, `Draining`, `Protocol(code)`, `Crypto(alert)`, `IdleTimeout`, `NoMemory` and `Internal`.
  - The library's own code is kept for the log.
- **Memory.**
  - Both libraries take an allocator (`ngtcp2_mem`, `nghttp3_mem`). The shim passes one over `CEE_ALLOC_FOREIGN`, as
    OpenSSL's allocations go (CeeSafe ADR 0060, U14).
  - So the allocation-failure sweep reaches their allocations, and the leak check counts them.
  - Failures become `NoMemory`, and the connection closes with INTERNAL_ERROR.
- **Threads.** A handle is used by one task at a time. The listener's routing table is CeeSafe's, not the shim's.

### 3.5 The QUIC engine and driver (`web_quic`)

- **`QuicConnection` (sans-I/O):**
  - `feed(datagram, path, ecn, now)` and `datagrams(out, now)`;
  - `nextTimeout()` and `onTimeout(now)`;
  - events: handshake done, stream opened, readable, writable and closed, connection closed, and new tokens;
  - `openStream`, `writeStream`, `resetStream` and `close(code)`.
  - No `await`, no timer and no socket (Web ADR 0004).
- **The driver:**
  - **The socket.** One `AsyncUdpSocket` per listener, with `recvMsg` in batches, `sendMsg` with GSO where the system
    has it, and ECN marks read and written.
  - **Routing.** A table from connection ID to the connection's task. Server-chosen IDs are 20 random bytes from
    `Random.fill`.
  - **Unknown IDs.** A datagram for an unknown ID is an Initial (a new connection, under admission) or gets a
    stateless reset (RFC 9000 §10.3). The reset token is derived from a stateless-reset key with HMAC, so tokens
    survive a restart when the key is configured.
  - **Retry** (RFC 9000 §8.1.2) is on when the listener is under load, or always if configured. Its tokens are
    encrypted with a Retry key and carry the client's address and a time; they are accepted for 10 s.
  - **Admission and drain** reuse `web_transport`'s listener ideas, with a connection limit and an Initial-rate
    limit, without depending on it.
- **Migration** (RFC 9000 §9):
  - A client may migrate. ngtcp2 validates the new path, and the routing table follows the connection ID.
  - The server never migrates.
  - `disable_active_migration` is not sent. A preferred address is not offered in M10.
- **Limits** (defaults, configurable):
  - 100 bidirectional streams and 3 unidirectional ones (HTTP/3's control and QPACK's two);
  - a 30 s idle timeout;
  - flow-control windows of 1 MiB per stream and 16 MiB per connection;
  - an anti-amplification limit of 3x, as RFC 9000 requires (ngtcp2's own).

### 3.6 HTTP/3 (`web_http3`)

- **One `nghttp3_conn` per QUIC connection.** Each request stream becomes a task with an
  `Exchange.forStream(H3Stream, …)`, as for HTTP/2 (Web ADR 0011 §3.2).
- **`H3Stream` implements `StreamSink`:**
  - `readBody` reads DATA;
  - `sendHead` sends HEADERS, encoded by QPACK;
  - `sendData` sends DATA, waiting on QUIC flow control;
  - `copyTrailers` copies trailing HEADERS;
  - `push` answers false: server push is not offered in M10.
- **QPACK limits** (RFC 9204):
  - a dynamic table capacity of 4 KiB;
  - `SETTINGS_QPACK_BLOCKED_STREAMS` of 16;
  - a stream blocked past the limit is a connection error, QPACK_DECOMPRESSION_FAILED.
- **Field limits:** `SETTINGS_MAX_FIELD_SECTION_SIZE` from `Limits.maxHeaderBytes`.
- **Validation:**
  - request pseudo-headers as RFC 9114 §4.3.1;
  - `content-length` against DATA;
  - connection-specific fields refused (§4.2).
- **Flood guards,** as M7's (Web ADR 0011): bounds on streams opened then reset (rapid reset), on control-stream
  frames, and on an empty-frame rate. Each is answered with H3_EXCESSIVE_LOAD.
- **GOAWAY and drain.** When the server's drain starts, it sends GOAWAY with the last stream ID accepted, finishes the
  streams below it, then closes with H3_NO_ERROR within the drain deadline.

### 3.7 0-RTT

- **Off by default.** When enabled:
  - early data is accepted only for GET, HEAD and OPTIONS;
  - the request says it came early (`request.earlyData()`), so a handler can refuse it;
  - any other method in 0-RTT is answered 425 Too Early (RFC 8470), which the client retries after the handshake.
- **Replay.** Session tickets come from `web_tls`'s ticket keys (ADR 0002 §3.3). An anti-replay window (a time
  window and a bounded set of seen ticket IDs) limits replays to one per window per server. The README says that
  0-RTT is replayable across servers, as RFC 9001 §9.2 warns.

### 3.8 `Alt-Svc`

- `ServerOptions.http3Port` (null by default). When set, every HTTP/1.1 and HTTP/2 response gains
  `alt-svc: h3=":<port>"; ma=86400`, unless the handler set its own.

## 4. Stages

Each stage is committed and checked.

1. **Vendoring, the shim's skeleton and the build.**
   - `tools/vendor_quic.py` and `third_party/`.
   - `web_quic` and `web_http3` compile the C on macOS, Linux (OpenSSL 3.5) and Windows (OpenSSL 4.1).
   - A loopback handshake test: our engine against itself.
   - The allocator is routed through `CEE_ALLOC_FOREIGN`.
2. **The QUIC engine and driver.**
   - Connection IDs and routing, streams, the idle timeout, and close.
   - Interop with ngtcp2's own example client (`osslclient`), built from the vendored sources.
3. **HTTP/3 and QPACK.**
   - `H3Stream`, and `web_server`'s registration.
   - curl with HTTP/3 (where curl has it) and nghttp3's example client.
4. **The rest of the protocol:** Retry, stateless reset, migration, 0-RTT with 425, `Alt-Svc`, GOAWAY with drain,
   and the flood guards.
5. **The exit.**
   - `quic-interop-runner`: handshake, transfer, retry, resumption, zerortt, multiplexing, and the loss and reorder
     cases.
   - Loss and reordering with `tc netem` on loopback.
   - The QPACK blocked-stream limit, and the 0-RTT safety tests.
   - **Release v0.5.**

## 5. Consequences

- **HTTP/3 serves the same handlers** as HTTP/1.1 and HTTP/2. Middleware and routing are unchanged.
- **Two more C libraries,** of about 54 000 lines, vendored and pinned. They are behind two shims whose contract is §3.4,
  and every allocation is counted and swept.
- **A server that does not enable HTTP/3** links neither library and keeps OpenSSL ≥ 3.0.
- **CI needs OpenSSL ≥ 3.5 on Linux** for `web_quic` and `web_http3` (§7, question 5).
- **ngtcp2's OpenSSL backend is experimental upstream.** The pinned version is what is tested, and §3.3's rule is in the
  shim.

## 6. Tests (M10 exit)

- **Unit tests,** natively, in the interpreter where no C runs, with ASan and UBSan, and under the allocation-failure
  and cancellation sweeps:
  - the event queue's bounds;
  - error mapping;
  - routing;
  - Retry and stateless-reset tokens (RFC 9000 §10.3's construction checked against a Python oracle);
  - QPACK limits;
  - 0-RTT method rules.
- **Interop:**
  - ngtcp2's and nghttp3's example clients;
  - curl;
  - `quic-interop-runner`'s cases (§4, stage 5) against its client images.
- **netem** loss of 5 % and 10 %, and reordering, on loopback (Linux): transfers complete, and SHA-256 matches.
- **Flood guards:** each guard's test, as M7's.

## 7. Questions for the owner (answered 2026-11-09: each as recommended)

1. **OpenSSL ≥ 3.5 only for `web_quic`, with `web_tls` kept at ≥ 3.0?** Recommended: **yes**, so HTTP/1.1 and HTTP/2
   over TLS keep working on Debian 12 and Ubuntu 24.04. The alternative is the plan's literal reading: raise every
   package's minimum to 3.5.
2. **Accept ngtcp2's OpenSSL backend while upstream calls it experimental?** Recommended: **yes**, pinned to 1.26.0,
   with its one rule in the shim (§3.3). The plan chose one TLS provider. The alternatives (aws-lc, wolfSSL) would be
   a second one, to ship and audit.
3. **A C shim that queues the libraries' events (§2 A), not callbacks into CeeSafe?** Recommended: **yes**, as ADR
   0002 chose for OpenSSL.
4. **Two packages, `web_quic` and `web_http3`?** Recommended: **yes**, so QUIC can serve other protocols later. The
   alternative is QUIC inside `web_http3`.
5. **Linux with OpenSSL ≥ 3.5:**
   - **CI:** recommended, move CI's Linux jobs to `ubuntu-26.04` (OpenSSL 3.5.5), keeping one `ubuntu-24.04` job that
     skips `web_quic` and `web_http3` to keep 3.0 tested.
   - **Measuring and netem:** recommended, build OpenSSL 3.5 LTS into `/root/cw-m10/openssl-3.5` on hanan, a prefix
     that installs nothing system-wide. The alternative is fairooz or alghobashi, whose Debian 13 has 3.5.7 but which
     run other services.
6. **0-RTT off by default, safe methods only, 425 for the rest?** Recommended: **yes** (§3.7).
7. **HTTP/3 in `web_client` in M10?** Recommended: **no.** M10 is the server, and the client is a later item. The
   interop runner tests a server against its own client images.

## 8. Amendment 1 (2026-11-09, M10 stage 2: the shim as built)

1. **TLS.**
   - `QuicTls.of(config)` takes `web_tls`'s `TlsConfig` and up-refs its SSL_CTX (Web ADR 0002 amendment 6).
   - A QUIC server's configuration is its own, made from the same PEM with ALPN `h3`, since one SSL_CTX has one ALPN
     list.
   - Each QUIC server SSL is bound to its configuration with `wt_ssl_use_front`, for its session tickets.
2. **`qs_init` routes OpenSSL's allocations first** (`wt_init`), so every allocation of a handshake is counted and
   swept.
3. **Error codes.** The shim classifies ngtcp2's codes (`qs_error_kind`), so no ngtcp2 number appears in CeeSafe.
   - Copying them by hand gave three wrong values out of six (STREAM_ID_BLOCKED, DRAINING and IDLE_CLOSE). Checking
     against `ngtcp2.h` caught them before any test ran.
4. **The event queue.**
   - **Its bound** is 256 events and 256 KiB of data.
   - **Before each datagram** is read, `qs_read` checks there is room for everything one datagram can report: its
     length, and 64 events. Otherwise it answers `WouldBlock`, and the caller drains the queue and feeds the
     datagram again. That is §3.4's "a full queue stops the read", done before the read rather than inside it, since
     ngtcp2 cannot stop mid-packet.
   - **No silent truncation.** An event whose data does not fit the caller's buffer stays queued, and is reported as
     `BufferTooSmall(n)`; `quicEventBufferSize()` (65 527 bytes) is always enough. The first version truncated.
5. **Stream data.** ngtcp2 keeps no copy of the stream data it sends.
   - The shim holds each stream's unacknowledged bytes, at most 1 MiB, and frees them in `acked_stream_data_offset`.
   - `writeStream` takes fewer bytes when a stream is full, and `StreamWritable` says when to try again.
6. **Received stream data.** Its flow-control credit is given back as soon as it is queued. The backpressure is the
   queue's bound (item 4).
7. **Datagrams.** `datagram` writes one packet per call, with data from at most one stream. ngtcp2's default largest
   datagram, 1452 bytes (`ngtcp2_settings.c`), is the out buffer's size.

## 9. Amendment 2 (2026-11-09, M10 stage 2: the driver, and what real UDP found)

This amendment replaces amendment 1's items 4, 5 and 6.

1. **The queue's bound is flow control's** (replaces amendment 1 items 4 and 6).
   - **Why:** one datagram can release far more than its own bytes. ngtcp2 holds data received out of order behind a
     lost packet, and delivers all of it when that packet arrives. No check before a read can bound that, and over
     real UDP on epoll and WSAPoll the fixed 256 KiB queue overflowed (ngtcp2 error -228, the callback's failure).
   - **Now:** a stream's flow-control credit is given back only when CeeSafe takes its data (`qs_next_event`), not when
     it is queued. So the queued data never passes the connection's window, `initial_max_data`, 16 MiB
     (`QS_MAX_DATA`).
   - **The queue grows** within that bound: QS_MAX_DATA + 1 MiB of data and 65 536 events
     (`QS_QUEUE_BYTES_LIMIT`, `QS_QUEUE_EVENTS_LIMIT`). Past them, or if growing fails, the connection fails with
     `NGTCP2_ERR_NOMEM`, which is `QuicError.NoMemory`.
   - **`WouldBlock` is removed:** `feed` never needs to be repeated.
   - **Long data is split:** stream data longer than 65 527 bytes is queued as several events, the FIN with the last, so
     every event fits `quicEventBufferSize()`. `BufferTooSmall` stays for a smaller buffer.
2. **A stream's sent bytes never move** (replaces amendment 1 item 5).
   - **Why:** ngtcp2 keeps pointers into the stream data it was given until that data is acknowledged, and retransmits
     from them. The first buffer `memmove`d the acknowledged prefix away and `realloc`ed to grow, which moved bytes
     ngtcp2 still pointed into. Under loss the peer then received wrong bytes: the 3 MiB transfer's check failed on
     epoll.
   - **Now:** each stream holds its bytes in 16 KiB chunks (`qs_chunk`), filled in order. A chunk is freed only when
     every byte in it is acknowledged; nothing is moved or reallocated while ngtcp2 may hold a pointer into it.
   - **A stream holds at most 1 MiB** (`QS_STREAM_HOLD`), as before: `writeStream` takes less, and `StreamWritable`
     follows an acknowledgement that frees room.
   - **Writing:** `qs_write` hands ngtcp2 the unsent bytes as up to 8 vectors, one per chunk. It sets the FIN only when
     the vectors reach the stream's end, and records the FIN as sent only when it was passed.
   - **Resetting:** `qs_stream_reset` shuts the stream down in ngtcp2 first, then frees its bytes, so ngtcp2 never
     holds a pointer into freed chunks.
3. **The driver** (`web_quic/src/server.cee`, §3.5):
   - **One listener task** owns the UDP socket and the routing table, a vector of (connection ID, connection) scanned
     in order. It is the socket's only user.
   - **Admission:** a version 1 Initial for an unknown ID makes a connection while fewer than `maxConnections` (1000)
     are live. Any other datagram for an unknown ID is dropped; stateless resets are stage 4's.
   - **Each connection runs in its own task**, a child of the listener's task set (`Async.tasks`), with its application
     (`QuicApplication.connection`, driven through `QuicLink`).
   - **Channels:** each connection has an inbound channel of `inboundQueue` (64) datagrams; when it is full, a datagram
     is dropped as a full socket buffer would drop it. Connections send to the listener on one channel: `Send`,
     `Route` and `Unroute` (from `NewConnectionId` and `RetiredConnectionId`, the shim's events 9 and 10), and
     `Ended`.
4. **hq-interop** (`web_quic/src/hq.cee`): HTTP/0.9 over QUIC, as quic-interop-runner's transport cases use it, for
   interop testing only.
   - `GET /path` on a bidirectional stream gets the file's bytes, then FIN.
   - The path guard: it must start with `/`, and may not hold `..`, `\`, `:`, a space or a zero byte. A refused or
     missing file gets an empty answer.
   - Files are read in 64 KiB pieces, and a piece the stream does not take waits for `StreamWritable`.

## 10. Amendment 3 (2026-11-09, M10 stage 2: interop with ngtcp2's client)

1. **`Alpn.HqInterop`** (`hq-interop`):
   - **Why:** an hq-interop client offers only that protocol, so a server offering only `h3` fails its handshake.
     quic-interop-runner's transport cases need it too (stage 5).
   - **Where:** `web_core`'s `Alpn` gains it last, so the earlier variants keep their tags; `web_tls`'s `setAlpn`
     writes it, and `QuicConnection.alpn` recognises it.
   - It is for interop testing over QUIC; nothing over TCP offers it.
2. **The external client** is ngtcp2 1.26.0's example client (`examples/client.cc`), with the OpenSSL backend and the
   hq-interop codec.
   - It is built from the release tarball, the one `tools/vendor_quic.py` checks, since the vendored tree holds only
     the library. It is a test tool: never committed, never linked into a package.
   - **Built by hand:** upstream's Makefile pairs the hq codec only with wolfSSL (`wsslhqclient`). The OpenSSL client
     is the same sources, with `-DWITH_EXAMPLE_HQ_PROTO_CODEC` and `hq_client_proto_codec.cc` in place of the HTTP/3
     codec (M10.md has the commands).
   - **C++23:** the examples need it. Debian 12's g++ 12 fails their configure check, so the interop runs on macOS for
     now; on Linux it comes with quic-interop-runner's images (stage 5).
   - It does not verify the server's certificate, so the handshake's certificate checks stay with our own tests
     (`web_quic`'s loopback tests verify the test CA's "localhost").
3. **`examples/hq`** serves a directory over hq-interop with `QuicServer` and `HqFiles`, on UDP 127.0.0.1:`CW_PORT`.
   `tests/interop/quic.py` drives it with the client.
4. **A fix to hq-interop's requests:** a request is now marked `started` once its whole line has arrived and been
   answered.
   - Before, being started was inferred from the request line having been cleared. A refused path (`..` or `:`)
     returned before clearing it, so its stream never got the FIN, and the client waited for its idle timeout (30 s).
   - The interop run found it. `hq_interop_transfer_over_udp` now asks for `/sub/../small.txt` too, and fails
     against the old code.
5. **A peer's stream limit is raised as its streams close** (RFC 9000 §4.6). The shim's `stream_close` callback
   extends `max_streams` by one for each stream the peer opened, of its own direction (`ngtcp2_conn_extend_max_streams_
   bidi` or `_uni`), as ngtcp2's example server does.
   - **Before,** a client could open only `initial_max_streams_bidi` (100) streams in a connection's whole life. The
     101st waited until the idle timeout.
   - The interop's multiplexing check had asked for exactly 100 files, so it passed. It now asks for 250.
   - Found when designing stage 3, by asking for 150 files on one connection: 100 arrived, and the client then hit its
     idle timeout.

## 11. Amendment 4 (2026-11-09, M10 stage 3: the HTTP/3 design)

1. **Layering: `web_server` does not gain `web_http3`, and `web_http3` does not depend on `web_server`** (replaces
   §3.1's "web_server gains web_http3, behind an option", the question M10.md left open at stage 1).
   - **Why:** a manifest's dependency is not optional, so `web_server` depending on `web_http3` would link ngtcp2,
     nghttp3 and OpenSSL 3.5 into every server.
   - **What web_http3 needs is already below web_server.** HTTP/2's driver serves an `Arc<Box<dyn Application +
     Send>>` and `Limits`, both `web_http`'s (`web_http2.serveH2`), and `web_server`'s `Server<S>` is such an
     `Application`. `web_http3` serves the same type, so its dependencies stay `web_core`, `web_http` and `web_quic`
     (Web ADR 0004 amendment 4, unchanged).
   - **One application over TCP and QUIC:** `web_server` gains `applicationOf(server)`, the `Arc` it builds today
     inside `serviceOf`, and `HttpService.of(app, limits)`. A program serves the one `Arc` with `ConnectionServer.run`
     and `Http3Server.serve`.
   - **`Alt-Svc`** (§3.8) stays `web_server`'s, a header with a port number: it needs no QUIC code.
2. **web_quic gains what HTTP/3 needs:**
   - **unidirectional streams:** `openUniStream`, for the control stream and QPACK's two;
   - **credit returned by the application:** a connection made with `QuicServerOptions.creditOnRead = false` gives a
     stream's credit back only when its user calls `QuicLink.consume(stream, n)`, not when the event is taken. HTTP/3
     consumes at once what nghttp3 reports consumed (framing, control and QPACK streams), and a request body's bytes
     only as its handler reads them, so a slow handler holds back only its own stream (as HTTP/2 does, Web ADR 0011
     §3.2). hq-interop keeps `true`.
3. **The h3 shim (`web_http3/c/h3_shim.c`)** wraps one `nghttp3_conn`, with §3.4's contract: an event queue drained by
   CeeSafe, allocations through `CEE_ALLOC_FOREIGN`, error codes classified in C.
   - **In:** `h3_read(stream, bytes, fin)` calls `nghttp3_conn_read_stream` and returns what it consumed. Its
     callbacks queue events: a request's header fields, its end of headers, DATA, trailers, its end, a stream's reset
     or close, the peer's GOAWAY.
   - **Out:** `h3_pull` returns the next bytes nghttp3 has for a stream (`nghttp3_conn_writev_stream`); `h3_wrote`
     reports what QUIC took (`add_write_offset`), and, since web_quic copies what it takes into its own chunks
     (amendment 2 item 2), also reports it acknowledged (`add_ack_offset`) so nghttp3 frees its buffers at once. A
     stream QUIC holds full is blocked in nghttp3 (`block_stream`) until `StreamWritable` (`unblock_stream`).
   - **Responses:** `h3_respond(stream, fields, hasBody)` (`submit_response` with a data reader) and
     `h3_body(stream, bytes, end)`, which copies into the stream's body queue (at most 256 KiB, then it takes less; an
     event says when there is room). The data reader hands nghttp3 vectors into that queue, and frees what nghttp3
     reports acknowledged. `h3_trailers` and `h3_reset` complete it.
   - **Settings:** QPACK's dynamic table 4 KiB and 16 blocked streams (§3.6); `max_field_section_size` from
     `Limits.maxHeadBytes`.
   - **Validation is nghttp3's** (RFC 9114 §4.1.2 and §4.3: pseudo-header fields, connection-specific fields,
     `content-length` against DATA).
   - **Errors.** A malformed request makes `nghttp3_conn_read_stream` fail, and nghttp3 then allows only
     `nghttp3_conn_del`. So the connection closes with the code nghttp3 infers, H3_MESSAGE_ERROR (0x10e). RFC 9114 §8
     lets an endpoint treat a stream error as a connection error.
     - The same holds for every error from `read`, `pull`, `wrote`, `bind`, `shutdownRead` and `closeStream`: close
       with `H3Errors.closeCode`.
     - Errors from `respond`, `sendBody` and `trailers` concern only their stream.
     - (This replaces the first wording here, "a malformed request is reset with H3_MESSAGE_ERROR by nghttp3 itself",
       which the first test disproved.)
4. **The engine:** `H3Connection` (sans-I/O) over the shim. **The driver:** `Http3Server.serve(socket, local, tls,
   app, limits, options)` runs `web_quic`'s `QuicServer` with an `Http3Application`. Its connection task owns the
   `QuicLink` and the `H3Connection`. Each request runs in its own task with `Exchange.forStream` and an `H3Port`
   implementing `StreamSink` over channels to the connection task, as HTTP/2's `StreamPort` does (Web ADR 0011
   §3.2).
   - **Before the handshake ends,** QUIC's stream events still go to nghttp3: reading the peer's streams needs nothing
     of ours. Only opening and binding our three streams, and flushing, wait for the handshake.
     - A first harness dropped events until then. The server then never saw the client's control and QPACK decoder
       streams, which came with the handshake's last flight, and failed with H3_CLOSED_CRITICAL_STREAM.
   - **`H3Glue`** (`web_http3/src/glue.cee`) is that plumbing, written once over `H3Streams`, which `QuicConnection`
     and `QuicLink` implement. web_quic gains `QuicTls.serverFromPem` and `clientFromPem`, so a package with no
     dependency on `web_tls` can make contexts, and `QuicConnection.peerApplicationClose`, the peer's application
     close code.
5. **A client for the tests only.** The shim can also make a client `nghttp3_conn` (`h3_conn_client`). It is used
   through the non-public `H3Connection.client` and `request` in `web_http3`'s tests: package tests see non-public
   items (as `web_http2`'s tests use `read32`). It is not API, so the owner's answer 7 (no HTTP/3 client) holds.
6. **Interop:** ngtcp2's example client in its HTTP/3 build (`osslclient`, amendment 3 item 2) and curl where it has
   HTTP/3.
7. **One direction at a time:** web_quic gains `resetSending` (RESET_STREAM only) and `stopReading` (STOP_SENDING
   only), on `QuicConnection` and `QuicLink`. nghttp3 asks for each separately (its `reset_stream` and
   `stop_sending` callbacks). `resetStream`, which shuts both down, would also cut a response short when nghttp3 only
   wants a request body stopped.
   - **A fix found by its test:** the shim registered `QuicEvent.StopSending` on ngtcp2's `stream_stop_sending`
     callback. That one reports our own shutdown of a stream's reading, not the peer's STOP_SENDING, which is
     `recv_stop_sending` (ngtcp2 1.24). So a peer's STOP_SENDING was never reported, and our own stops were.
8. **The parts of stage 3:** (a) web_quic's additions (item 2); (b) the shim and `H3Connection`, tested in memory
   with the test client; (c) the driver, `Exchange.forStream`, and tests over UDP; (d) GOAWAY with drain, the flood
   guards (§3.6) and the QPACK limit tests; (e) interop.
9. **The driver, as built (part (c)):**
   - **Waking for requests too.** `QuicLink.next` waits only for a datagram or the connection's timer. web_quic gains
     `QuicLink.poll` (the next event, without waiting) and `QuicLink.waitWith(messages, capMs)`, which also wakes for
     a message on the application's channel. HTTP/3's connection task waits there for its request tasks.
     - **A fix before it shipped:** when a message won the race, the cancelled datagram-or-timer child turned its
       cancellation into a value, "the listener has gone". The race returned it as `other`, and the connection ended
       with `Draining`. The child now lets cancellation stay an error. `six_requests_over_udp` failed with it (3 runs
       of 3) and passes without it.
   - **Request tasks** (`web_http3/src/server.cee`) run `Exchange.forStream` over an `H3Port` (`StreamSink`), as
     HTTP/2's do.
     - Each body piece (at most 64 KiB) is acknowledged to its task once nghttp3's body queue has taken all of it.
     - Response heads and trailers are told apart by whether the head was sent.
     - Field names are lowercased, and connection-specific fields left out (RFC 9114 §4.2).
     - A stream past `requestsPerConnection` (100) is refused with H3_REQUEST_REJECTED.
     - When a response ends before its request's body, STOP_SENDING goes out with H3_NO_ERROR (§4.1.1).
   - **QUIC writes streams in the order they were made** (`qs_stream_get` appends). Before, the newest went first, so
     a response's HEADERS could leave before the QPACK encoder instructions it referred to.
     - ngtcp2's client then found the header block blocked, and dropped the response when its stream closed. Every GET
       through `examples/h3` came back empty, though `/big`, whose head went out first, arrived whole.
     - `older_streams_are_written_first` (web_quic) fails with the old order.
   - **`connection` passes `run`'s end back as it is.** Mapping `Draining` to `Ok` had made the QUIC server send a
     second close, with code 0.
   - **`examples/h3`** serves a `web_server` application through `applicationOf` and `Http3Server.serve`.
     `tests/interop/h3.py` drives it with ngtcp2's HTTP/3 client (`osslclient`).
   - **curl:** its interop is not run on macOS, where the session's tools block curl. It is for stage 5, with
     quic-interop-runner's images.
10. **The guards and the drain, as built (part (d)):**
    - **nghttp3's own flood guard is on.** It counts "glitches" (frames of unknown or unexpected types on control and
      request streams, malformed streams, and more) against a budget of 1 000, refilled at 33 a second, and closes
      with H3_EXCESSIVE_LOAD past it.
      - It runs only through `nghttp3_conn_read_stream2`, which takes the time. The shim had called the version
        without one, which leaves it off, so `H3Connection.read` and `H3Glue.onQuic` now take the caller's clock.
    - **The rapid-reset guard is the driver's** (CVE-2023-44487). Neither library counts a request reset after its
      HEADERS. `Http3Options.resetsPerSecond` (200, as HTTP/2's `H2Guards`) bounds the client's resets of requests
      whose answer is not done; past it the connection closes with H3_EXCESSIVE_LOAD.
    - **Empty DATA frames are not guarded,** unlike §3.6's plan.
      - nghttp3 neither counts them nor reports them: an empty DATA frame makes no event and no allocation.
      - Each is two bytes of stream data under QUIC's flow control, so its cost grows with the bytes received, as any
        data's does. HTTP/2's empty-frame guard exists because there a frame costs a header at no data cost.
    - **QPACK's limits** (4 KiB table, 16 blocked streams) are nghttp3's. The 17th blocked stream closes the
      connection with QPACK_DECOMPRESSION_FAILED (RFC 9204 §2.1.2).
    - **GOAWAY with the drain** (RFC 9114 §5.2):
      - when the server's `Drain` starts, the connection sends GOAWAY's notice (the largest ID);
      - 100 ms later it sends the final GOAWAY, with the lowest stream not processed, and nghttp3 refuses later
        streams with H3_REQUEST_REJECTED;
      - once no request is left and nghttp3 reports itself drained (every stream closed, so every answer
        acknowledged), it closes with H3_NO_ERROR.
      - The drain's deadline is the program's: it cancels `Http3Server.serve` when its grace runs out.
      - The listener still admits connections while draining: each gets the GOAWAY at once.

## 12. Amendment 5 (2026-11-09, M10 stage 4: the rest of the protocol, as built)

1. **`Alt-Svc`** (§3.8). `Server.advertiseHttp3(port)` replaces `ServerOptions.http3Port`, since `web_server` has no
   options struct: a `Server` carries its own.
   - Every response whose request did not come over HTTP/3 gains `alt-svc: h3=":<port>"; ma=86400` (RFC 7838 §3),
     after the middleware's `after` hooks, unless the response already has an `Alt-Svc`.
2. **Stateless reset** (RFC 9000 §10.3; §3.5's "unknown IDs").
   - **The key is the server's:** `QuicServerOptions.resetKey`, 32 bytes, random for each `serve` unless configured.
     Every connection's reset secret is that key, so each token, the first ID's included (now sent in the transport
     parameters, §18.2), can be derived again from the ID alone (`ngtcp2_crypto_generate_stateless_reset_token`).
     - This replaces §3.5's "derived from a stateless-reset key with HMAC": ngtcp2's own derivation (HKDF over the
       ID) does the same with the same properties.
   - **The listener answers** a short-header datagram for one of our 20-byte IDs that no connection has with a
     stateless reset. The reset is one byte shorter than its trigger, so two endpoints cannot answer each other's
     resets forever (§10.3.3), and at most 43 bytes. A trigger too short for the smallest reset ngtcp2 writes
     (21 bytes) gets none.
   - **At most `resetsPerSecond` (100)** resets a second, so the listener cannot be used to reflect a flood (§10.3
     lets an endpoint limit them).
3. **Retry** (RFC 9000 §8.1; §3.5).
   - **When:** `QuicServerOptions.retryAbove` (500, half of `maxConnections`). From that many live connections on, a
     new client's first Initial gets a Retry, which costs the client a round trip and the server no state. 0 means
     always. A Retry token is checked whenever one comes, whatever the load.
   - **Tokens:** `ngtcp2_crypto_generate_retry_token2` under `retryKey` (32 bytes, random for each `serve` unless
     configured). A token is bound to the client's address, its first destination ID and the Retry's new ID, and
     lasts 10 s (§3.5). One that does not verify drops its Initial.
   - **After a Retry,** the connection is made with `QuicConnection.serverAfterRetry`. Its transport parameters
     carry the client's first ID as `original_destination_connection_id`, and the Retry's ID as
     `retry_source_connection_id` (§7.3).
   - **Sans-I/O:** the listener's decision is `QuicRetry.check`, so it is tested without sockets.
     `QuicConnection.retried()` gives the client's view.
   - **§3.5's "encrypted tokens carrying the address and a time"** are ngtcp2's token format (AEAD under a key
     derived from `retryKey`), not one of our own.
4. **Migration** (RFC 9000 §9; §3.5).
   - **The server follows the client.** ngtcp2 validates the new path (§8.2) and chooses where each packet goes.
     `QuicConnection.datagram` now reports that destination (`QuicOut.to`), and `QuicLink` sends each datagram there,
     so its `peerAddress` follows the client. The listener already routes by connection ID, so the new path's
     datagrams reach the same connection.
     - Before, every datagram went to the address of the client's first Initial, so a migrated client heard nothing.
   - **The server never migrates,** and sends no preferred address (§3.5). `QuicConnection.migrate(local)` is the
     client's side: ngtcp2's `initiate_migration`, used by the tests.
5. **0-RTT** (§3.7; the owner's answer 6: off by default, safe methods only).
   - **On per server context:** `QuicTls.allowEarlyData()` enables 0-RTT on each connection, and in the tickets it
     issues (`SSL_set_max_early_data`, `SSL_set_quic_tls_early_data_enabled`). It is per connection, so a shared
     `SSL_CTX` is not changed.
   - **Early data is reported:** ngtcp2 flags stream data that came in 0-RTT (`NGTCP2_STREAM_DATA_FLAG_0RTT`), and the
     shim reports it as `QuicEvent.EarlyData(stream)`.
   - **HTTP/3 answers early requests by method:**
     - anything but GET, HEAD and OPTIONS gets 425 Too Early from the driver itself (RFC 8470 §5.2), and its body
       STOP_SENDING;
     - a safe request runs, and its handler sees `Request.earlyData()` (`Status.tooEarly()` lets it refuse too).
     - The driver starts HTTP/3's own streams when early data first arrives, not only at the handshake's end (RFC 9001
       §4.1.1).
   - **Replay: OpenSSL's protection, not a window of our own** (replaces §3.7's "anti-replay window"). With early data
     enabled, OpenSSL makes tickets single-use, kept in its server session cache. A ticket used a second time gets a
     full handshake, its early data ignored (`SSL_read_early_data(3)`, "REPLAY PROTECTION").
     - This allows no replay at all within one server process, rather than one per window.
     - It holds within one process only: tickets do not survive a restart, and a replay to another server sharing the
       ticket keys is not detected (RFC 9001 §9.2). The README must say so.
   - **A fix this needed in the shim:** a connection whose handshake completed is marked shut down
     (`SSL_set_shutdown`) before `SSL_free`. QUIC never sends TLS's close_notify, so `SSL_free` had taken each
     session for a bad one and removed it from the server's cache (`ssl_clear_bad_session`).
     - Every single-use ticket was therefore gone as soon as the connection that issued it ended, and no resumption
       with 0-RTT could succeed. Stateless tickets (no early data) never depended on the cache, so they were not
       affected.
   - **The client's side** (the tests'): `QuicConnection.ticket` (the TLS session and the server's 0-RTT transport
     parameters, RFC 9000 §7.4.1), `resume`, `resumed` and `earlyDataAccepted`.

## 13. Amendment 6 (2026-11-09, M10 stage 5: what loss and reordering found)

The stage 5 tests ran web_quic's and web_http3's real-UDP tests inside a Linux network namespace whose loopback has
`tc netem` delay (10 ms ± 5 ms), loss (5%) and reordering (25%). Three defects showed up, each invisible on a clean
path, and each is fixed:

1. **A blocked header block was dropped when its stream closed.**
   - QUIC closes a stream once everything on it has arrived. But nghttp3 may still hold a header block that refers
     to QPACK dynamic-table entries whose instructions are late on the encoder stream (RFC 9204 §2.1.2).
   - `nghttp3_conn_close_stream` deletes the stream as it is, so the answer (or request) was lost. This is also why
     ngtcp2's example client lost small answers in stage 3.
   - **Fix:** the h3 shim defers QUIC's close of a bidirectional stream that nghttp3 has not finished reading (no
     `end_stream`) and the peer did not reset. It carries the close out after the read that lets nghttp3 finish.
2. **A draining connection could close with data still unacknowledged.**
   - nghttp3 being drained says nothing of the QPACK encoder stream, which never ends, nor of RESET_STREAM and other
     frames.
   - **Fix:** the drain closes only when `QuicConnection.allAcknowledged()`: every stream's bytes acknowledged, and
     ngtcp2's bytes in flight at 0.
3. **No closing state.**
   - The server sent CONNECTION_CLOSE once and forgot the connection. When that packet was lost, the client's next
     packets found no connection, and the listener answered with a stateless reset, which carries no close code.
   - **Fix:** RFC 9000 §10.2.1's closing state. `QuicLink` keeps the CONNECTION_CLOSE it sent, and for three probe
     timeouts its IDs stay routed and every datagram from the peer gets the close again.
   - What remains: a client that has nothing more to send learns of a lost close only at its idle timeout, as QUIC
     intends.

Two improvements to 0-RTT came with them:

4. **0-RTT packets that arrive before their Initial are held** (RFC 9000 §5.2.2): at most 32, for up to 3 s, then
   handed to the connection that Initial makes. Before, losing or reordering the Initial cost the client all its early
   data.
   - Amendment 9 completes this for a ClientHello that spans two Initial datagrams.
5. **`CW_NGTCP2_LOG`:** set in the environment, it turns on ngtcp2's own log, to stderr. It is how the cases above
   were found.

## 14. Amendment 7 (2026-11-09, M10 stage 5: quic-interop-runner)

1. **The endpoint** (`tests/interop/endpoint`) is the runner's server. It reads the runner's environment (`ROLE`,
   `TESTCASE`, `SSLKEYLOGFILE`) and serves `/www` on UDP 443, with `/certs/cert.pem` and `/certs/priv.key`:
   - hq-interop through `HqFiles` for the transport cases;
   - HTTP/3 through `Http3Server` with web_static's `StaticFiles` for `http3`;
   - Retry for `retry`, 0-RTT for `zerortt`, ChaCha20 only for `chacha20`.
   - It exits 127, as the runner asks, for what it does not do: the client role, QUIC v2 (`v2`), ECN marks (`ecn`), a
     preferred address (`connectionmigration`).
   - It binds `[::]` with IPV6_V6ONLY off, so `ipv6` reaches it as well as the IPv4 cases.
   - Its image (`tests/interop/endpoint/docker`) is the runner's endpoint base (Ubuntu 24.04) with the binary and the
     OpenSSL 3.5 it links. `build.sh` builds both on a Linux host.
2. **Version Negotiation** (RFC 9000 §6) was missing.
   - **Found when:** the runner's simulator starts a test only once the server answers a probe of an unknown version.
     Our listener dropped every packet of another version, so not one test could start.
   - **Now:** the listener answers a long-header packet of another version, in a datagram of at least 1200 bytes
     (§14.1: no amplification), with Version Negotiation listing version 1 and a reserved version (§15). It shares the
     stateless resets' budget.
3. **web_tls gains two settings:**
   - `TlsConfig.setCipherSuites13(list)`, for TLS 1.3's suites (`chacha20` asks for ChaCha20 only);
   - `TlsConfig.writeKeyLog(path)`, an NSS key log, so the runner can decrypt its captures.
   - The key log is a method, not something `SSLKEYLOGFILE` turns on by itself: a variable leaking session keys must
     not be able to enable it in production. The endpoint calls it when the runner sets the variable.
   - Both have a new error, `TlsSetupError.BadSetting`.
4. **The results** (M10.md, stage 5 (b)): with ngtcp2, aioquic, quiche, picoquic and quic-go as clients, every case
   the endpoint supports passed but two:
   - quiche's `multiplexing`, which failed in the client (too many open files);
   - `rebind-port` and `rebind-addr` with picoquic and quic-go, on the runner's path-validation checks. ngtcp2's own
     server fails them the same way, so they are left to ngtcp2 and the clients.

## 15. Amendment 8 (2026-11-09, M10 stage 5: curl, and replay restated)

1. **An issued connection ID is routed before the datagram that announces it.**
   - **Found by curl** (8.14.1, Debian 13's, whose QUIC is OpenSSL's): about one connection in seven never finished
     its handshake. Its client switched to the ID our NEW_CONNECTION_ID frame had just issued, in an Initial packet,
     which RFC 9000 §5.1.2 allows. The listener had not routed that ID yet: `QuicLink` posted `Route` when it read the
     event, after `flush` had already sent the frame. The listener took the packet for a new connection.
   - **Now:** the shim keeps ID changes in their own queue (`qs_next_id`, `QuicConnection.nextIdChange`), and
     `QuicLink.flush` posts them before each datagram. The listener takes its messages in order, so the route is in
     place before the datagram leaves. `QuicEvent.NewConnectionId` and `RetiredConnectionId` are gone; clients do not
     queue ID changes.
   - Not reproduced by an in-package test: ngtcp2's client never changes ID during the handshake. curl is its evidence
     (item 2).
2. **curl** (§6; `tests/interop/curl_h3.py`): GET, an 8 MiB GET, three requests on one connection (curl's own
   reuse), four in parallel, a 3 MB POST echoed, 405, HEAD, and 50 connections in turn, with the certificate verified
   against the test CA. It runs curl through a command prefix, as in a container where the host's curl has no HTTP/3.
3. **Replay, restated** (amendment 5 item 5 said a replay to another server sharing the ticket keys is not detected).
   Single-use tickets live in the issuing context's session cache, and web_tls has no way to share ticket keys, or that
   cache, between configurations. A ticket offered to another server is unknown there: a full handshake, its early
   data ignored. `early_data_once_per_ticket` now shows it. What remains is RFC 9001 §9.2's: a replay that arrives
   before the client's own copy runs once, and the client's request is then sent again after the handshake, so it
   runs twice. That is why only safe methods run early. `packages/web_http3/README.md` says so.

## 16. Amendment 9 (2026-11-13: held 0-RTT and a ClientHello in two datagrams)

A test for amendment 6 item 4 (`packages/web_quic/tests/held.cee`) showed that the holding did not work with this
package's own client.

1. **What went wrong.**
   - A resuming client's ClientHello does not fit one datagram here: its Initial is two datagrams, and 0-RTT
     datagrams follow.
   - The listener released the held 0-RTT datagrams as soon as the first Initial datagram made the connection.
   - ngtcp2 discards a 0-RTT packet that comes after the first Initial and before the 0-RTT keys, which need the
     whole ClientHello (`ngtcp2_conn.c`, `case NGTCP2_PKT_0RTT`: "Discard 0-RTT packet if we don't have a key to
     decrypt it"). It buffers 0-RTT only before the first Initial, which never happens here: the first Initial makes
     the connection.
   - So the released datagrams were discarded, and the client sent their bytes again after the handshake, in 1-RTT.
     Nothing was lost, but nothing held came early: of 4,000 early bytes, about 3,600 arrived late.
   - The same happens without any reordering of the 0-RTT, when the second Initial datagram is lost or late: the
     test's third order.
2. **Fix: the connection keeps 0-RTT datagrams until the handshake is done** (`QuicLink.take`).
   - A 0-RTT datagram that comes before the handshake is done is read, and also kept: at most 32 (`heldMost`), per
     connection.
   - After each later Initial datagram, the kept ones are read again. One that was already read is a duplicate
     packet number, which ngtcp2 ignores.
   - When the handshake is done, they are dropped.
   - The listener's holding (amendment 6 item 4) stays: it covers the time before any connection exists.
3. **The cost** is up to 32 datagrams (about 46 KiB) per connection, for the length of its handshake, and only for
   connections that are sent 0-RTT.
