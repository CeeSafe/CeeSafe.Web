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
