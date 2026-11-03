# Web ADR 0002 — TLS provider: OpenSSL behind a sans-I/O shim

- Status: **Accepted** by the owner, 2026-11-02 (every question of §6 as recommended). The provider, system
  OpenSSL, is the owner's earlier decision of 2026-11-02 (implementation plan §11.2).
- Scope: which TLS library `web_tls` uses, how CeeSafe code drives it, the C shim's contract, the configuration it
  enforces, and how it is built on each platform. Milestones M4 (TLS), M5 (client verification) and M10 (QUIC
  crypto).

## 1. Context

- The roadmap asks for an established, audited C-compatible TLS implementation behind a narrow adapter: TLS 1.2 and
  1.3, ALPN, SNI, mTLS, session resumption, certificate rotation and handshake limits.
- CeeSafe calls C through `extern "C"` declarations; every call is `unsafe` with a `Safety:` comment
  (`guide/unsafe-and-c.md`). It cannot call C macros. Many OpenSSL entry points are macros over `SSL_ctrl` and
  `SSL_CTX_ctrl`: `SSL_set_tlsext_host_name`, `SSL_CTX_set_min_proto_version`, `SSL_CTX_set_max_proto_version`,
  `SSL_CTX_set_tlsext_servername_callback`, and the ticket key callback setters.
- A library package can ship C files (`c = [...]`, compiled for every package: CeeSafe `src/main.cee:758`), but cannot
  yet name system libraries to link. That is upstream U8 (`docs/ceesafe-upstream-work.md`).
- The interpreter runs no C (CeeSafe `spec/type-system.md` §28.6), so anything using `web_tls` is tested natively.
- Web ADR 0001: a transport is a concrete type implementing `Transport`; I/O happens only in CeeSafe's `lib/std`.

## 2. Options for driving OpenSSL

| | A. Memory BIOs, sans-I/O (proposed) | B. A socket BIO on the descriptor | C. Custom BIO callbacks into CeeSafe |
|---|---|---|---|
| Who does I/O | CeeSafe (`lib/std`), through `PlainStream` | OpenSSL, on a non-blocking fd | OpenSSL calls back into CeeSafe |
| Fits completion I/O (IOCP, io_uring) | yes | no: OpenSSL issues its own syscalls | awkward: the callbacks are synchronous |
| C ↔ CeeSafe surface | plain functions with byte buffers | small, but breaks ADR 0035 | exported callbacks with re-entrancy |

**A.** OpenSSL never touches a socket, so TLS works over every reactor and over `MockTransport`.

## 3. Decision

### 3.1 Versions and linking

- **OpenSSL ≥ 3.0 LTS** for M4. **≥ 3.5 from M10**: its QUIC TLS API is what ngtcp2's `ngtcp2_crypto_ossl` uses, so
  TLS and QUIC share one provider. The shim fails the build below the minimum (`#if OPENSSL_VERSION_NUMBER < …
  #error`).
- Linked as a system library: `links = ["ssl", "crypto"]` once U8 lands. Until then, builds pass
  `--cflag -lssl --cflag -lcrypto` (and include and library paths); see `packages/web_tls/ceesafe.toml`. Merging M4 to
  main waits for U8.
- Where it comes from: Debian/Ubuntu `libssl-dev`; macOS Homebrew `openssl@3` (not the system LibreSSL); Windows
  vcpkg `openssl:x64-windows` (or the Shining Light installers), with paths given in the manifest's
  `[target.windows]` table (U8). CI records the exact version per host (`OpenSSL_version(OPENSSL_VERSION)`).

### 3.2 The shim (`packages/web_tls/c/tls_shim.c`)

One opaque context per connection: `SSL*`, a read BIO and a write BIO (`BIO_s_mem`, each with a size limit), and a
small error buffer. **The contract:**

| Function | Ownership and lengths | Result |
|---|---|---|
| `wt_ctx_new(config*) -> wt_ctx*` / `wt_ctx_free(wt_ctx*)` | the context owns `SSL*` and both BIOs; free is idempotent-safe once | NULL on allocation failure |
| `wt_feed(ctx, const u8* in, size_t len) -> i64` | copies at most `len` bytes of ciphertext into the read BIO, never keeps `in` | bytes taken (may be < len when the BIO is full), or a negative `wt_status` |
| `wt_take(ctx, u8* out, size_t cap) -> i64` | copies at most `cap` bytes of pending ciphertext out | bytes copied, 0 if none |
| `wt_handshake(ctx) -> i32` | — | `WT_DONE`, `WT_WANT_READ`, `WT_WANT_WRITE`, `WT_CLOSED`, `WT_ERROR` |
| `wt_read(ctx, u8* out, size_t cap) -> i64` | plaintext into `out`, at most `cap` | bytes, or a negative `wt_status` |
| `wt_write(ctx, const u8* in, size_t len) -> i64` | encrypts from `in` during the call only | bytes accepted, or a negative `wt_status` |
| `wt_shutdown(ctx) -> i32` | queues close_notify | as `wt_handshake` |
| `wt_alpn(ctx, u8* out, size_t cap) -> i64`, `wt_peer_cert_der(ctx, u8* out, size_t cap) -> i64`, `wt_sni(...)` | copies out; never returns OpenSSL-owned pointers | length, or −needed if `cap` is too small |
| `wt_error(ctx, u8* out, size_t cap) -> u64` | the `ERR_get_error` code, its text copied into `out` | the code |
| macro wrappers: `wt_set_hostname`, `wt_set_versions(min,max)`, `wt_set_sni_callback`, `wt_set_ticket_keys` | plain functions, one line each | as OpenSSL |

**Rules:**

- No pointer from CeeSafe is kept after a call returns.
- No OpenSSL-owned pointer is returned to CeeSafe; data is always copied out.
- Every length is a `size_t`, checked.
- `SSL_get_error` is mapped to the `wt_status` enum in one place.
- The shim performs no I/O and no allocation of its own beyond OpenSSL's.
- Every wrapper is listed in this table. A new wrapper is an amendment here.

On the CeeSafe side, `TlsTransport.read` loops: try `wt_read`; on want-read, read ciphertext from the `PlainStream`
and `wt_feed` it; on want-write, `wt_take` and write. It does the same for `write` and the handshake. Every loop is
bounded by the deadlines and limits of §3.4.

### 3.3 Configuration the server and client enforce

- TLS 1.2 and 1.3 only. TLS 1.2 is limited to ECDHE with AEAD suites (AES-GCM, ChaCha20-Poly1305).
- `SSL_OP_NO_RENEGOTIATION`, `SSL_OP_NO_COMPRESSION`, and `SSL_OP_CIPHER_SERVER_PREFERENCE` on servers.
- `SSL_MODE_RELEASE_BUFFERS`. Partial writes stay off, so the shim's write semantics are all-or-want.
- ALPN: `h2`, `http/1.1` (`h3` belongs to QUIC). The negotiated value is exposed through `Transport.alpn()` (Web
  ADR 0001 §3.2).
- **Server:** SNI selects a certificate set through a `CertResolver` interface. Certificates and keys hot-reload by
  swapping an `Arc<CertSet>`; connections already handshaking keep the old set. Session tickets use rotating keys
  (current + previous), rotated by a timer task.
- **mTLS:** `require` or `optional`. The chain is verified against a configured CA store, and verification failure
  fails the handshake (fail closed). The verified peer certificate is exposed to handlers as DER plus parsed subject
  and SAN fields.
- **Client (M5):** hostname verification with `X509_VERIFY_PARAM_set1_host` and
  `X509_CHECK_FLAG_NEVER_CHECK_SUBJECT` (SAN only, no CN fallback). Trust from `SSL_CTX_set_default_verify_paths`,
  plus the Windows certificate store (imported by the shim) and the macOS system bundle. Verification can never be
  turned off by a single flag; a custom verifier is an explicit interface.

### 3.4 Limits

- A handshake deadline (default 10 s).
- A cap on the ciphertext the handshake may consume (default 64 KiB) and on certificate-chain size.
- Each BIO is limited to 2 records (about 34 KiB).
- A cap on concurrent handshakes per listener (admission, Web ADR 0005 gates).
- `SSL_CTX_set_max_cert_list`.

Exceeding any limit closes the connection with an alert where possible.

### 3.5 Memory and threads

- OpenSSL 3 is thread-safe for shared `SSL_CTX` use. An `SSL*` is used by one task at a time. A connection task under
  `spawnSend` may move between worker threads between `await`s, but never runs on two at once, which OpenSSL allows.
- OpenSSL's own allocations are outside CeeSafe's accounting. In test builds they are routed through CeeSafe's
  allocator with `CRYPTO_set_mem_functions` (decided, §6.1), so `--check-leaks` and `CEESAFE_FAIL_ALLOC_AT` also
  cover OpenSSL. Release builds keep OpenSSL's allocator.

## 4. Consequences

- `web_tls` is the only package that links OpenSSL. Web ADR 0004's layering (checked by `tools/check_layers.py`)
  keeps every other package OpenSSL-free.
- TLS tests are native only (no C in the interpreter). Protocol tests above TLS run over plain `MockTransport` in the
  interpreter.
- Upstream dependency: U8 (manifest links) before M4 merges.

## 5. Tests (M4 exit)

- Interop: OpenSSL `s_client`/`s_server`, curl, Go `crypto/tls`, and `testssl.sh` (no weak protocols or suites).
- Negative tests, all failing closed: expired, not yet valid, wrong host, unknown CA, bad signature, truncated
  handshake, oversized record, renegotiation attempt, and a client certificate missing under `require`.
- Limits: a handshake flood stays within the caps; a slow handshake hits its deadline.
- The shim under ASan + UBSan; a libFuzzer harness feeding arbitrary ciphertext through `wt_feed`/`wt_read`.

## 6. Questions for the owner (answered 2026-11-02: each as recommended)

1. Route OpenSSL's allocations through CeeSafe's allocator in test builds (§3.5)? Recommended: yes. **Yes.**
2. Require OpenSSL ≥ 3.5 from M4 instead of M10, to test one version range only? Recommended: no. 3.0 LTS is what
   Debian 12 and Ubuntu 22.04 ship; raise the floor at M10. **No: 3.0 for M4, 3.5 from M10.**

## 7. Amendment 1 (2026-11-02, M4 stage 1: the shim as built)

1. **Configuration functions**, which §3.2's table did not list. A configuration is one `SSL_CTX`; connections hold
   their own references, so freeing it while they live is safe.

   | Function | Result |
   |---|---|
   | `wt_cfg_new(server) -> SSL_CTX*` | §3.3's settings: TLS 1.2–1.3, `ECDHE+AESGCM:ECDHE+CHACHA20` for 1.2, no renegotiation, no compression, server cipher preference, release buffers, `max_cert_list` 64 KiB; a client verifies its peer. NULL on failure |
   | `wt_cfg_free(ctx)` | drops this reference |
   | `wt_cfg_use_pem(ctx, chain, len, key, len) -> i32` | the leaf first, then the chain; checks the key matches. 1 on success |
   | `wt_cfg_trust_pem(ctx, pem, len) -> i32` | adds each certificate as a root; the number added |
   | `wt_cfg_trust_default(ctx) -> i32` | OpenSSL's default CA locations |
   | `wt_cfg_set_alpn(ctx, wire, len) -> i32` | the protocols in preference order (wire form, at most 64 bytes): offered by a client; a server picks its own first that the client offers, and with none in common goes on without ALPN (`SSL_TLSEXT_ERR_NOACK`), so a client without `h2` still gets HTTP/1.1 |

2. **Connection functions added:** `wt_ctx_new(cfg, server, host, len)` (a client's `host` is copied and verified
   against the SAN, as an IP address when it parses as one; SNI is sent for names only); `wt_pending(ctx)`;
   `wt_version(ctx)` (`0x0303`, `0x0304`); `wt_reason(ctx)` (why the last `WT_ERROR` happened, in web_core's
   `TlsReason` order: a failed certificate check before the handshake ends is `Certificate`, an alert from the peer
   `Alert`, another TLS error `Handshake` or `Protocol` by whether the handshake had finished, anything else
   `Internal`); `wt_openssl_version(out, cap)`. `wt_error` returns the text's length through a `size_t*`.
3. **`wt_feed` returns `WT_FULL`** when the read BIO already holds §3.4's two records; the transport treats it as a
   protocol error, since a peer that is speaking TLS never gets that far ahead of what OpenSSL consumes.
4. **The end of the stream without close_notify** is a read of 0, not an error. HTTP/1.1's framing (a length, or the
   last chunk) finds a body cut short; a body framed by the connection's close is HTTP/1.0's, which this server sends
   only to HTTP/1.0 clients.
5. **TlsTransport reads and writes its `PlainStream` directly**, not through `Transports.readWithin` (`dyn
   Transport`). Through `dyn`, the read could be a `TlsTransport`'s own, and CeeSafe refuses an async frame that may
   contain itself (N0343).
6. **Not Send.** A configuration holds a raw pointer, which CeeSafe v0 does not let a library outside lib/std declare
   thread-safe (`@threadSafe`, spec §38.3). M1's connection server runs its workers as tasks of one thread, so nothing
   needs it yet. Sharing a configuration across threads (and `Arc<CertSet>` hot reload, §3.3) will need that from
   CeeSafe: an upstream item, raised when M4's stage 4 gets there.
7. **Allocation routing (§3.5, §6.1) is not done in stage 1.** OpenSSL keeps global allocations until the process
   exits, so routing them through CeeSafe's allocator needs the leak check to tell those apart. It comes with stage 3
   and its own tests.

## 8. Amendment 2 (2026-11-02, M4 stage 3)

1. **§6.1 waits for CeeSafe (the owner's choice, 2026-11-02).** Routing OpenSSL's allocations through CeeSafe's
   allocator in tests cannot work yet: CeeSafe's leak check counts every live allocation at the end of a test, and
   OpenSSL keeps global state (error strings, providers) until the process exits. It needs a C allocation category
   that fault injection can fail but the leak check leaves out: upstream **U14**. Until then OpenSSL keeps its own
   allocator; LeakSanitizer (Linux) and the fuzz harness cover the shim's leaks.
2. **Windows.** `web_tls`'s manifest has a `[target.windows]` table naming `libssl` and `libcrypto` from
   `$OPENSSL_LIB_DIR`, with headers from `$OPENSSL_DIR/include`. The directory depends on the installer and the
   machine (Shining Light: `lib\VC\arm64\MD` or `lib\VC\x64\MD`), so it is the builder's to set (CeeSafe ADR 0047
   §3.1). macOS uses Homebrew's `openssl@3`; Linux the system's.
3. **Versions tested:** OpenSSL 3.0.22 (Debian 12, the floor), 3.6.4 (macOS, Homebrew) and 4.1.0 (Windows 11 ARM64,
   Shining Light). The shim uses only 3.0's API, so it compiles unchanged on 4.1.
4. **testssl.sh's gate** is its protocol, cipher, forward-secrecy and vulnerability checks (`-p -s -f -U`). Its
   certificate checks judge the test PKI (generated for tests: no revocation URLs, a 100-year lifetime), and HSTS is
   the application's to send.
5. **The fuzz harness** (`tests/fuzz/tls/fuzz_shim.c`) is guided by the shim's own coverage: the system OpenSSL is not
   instrumented. That suits its purpose, the shim's handling of whatever OpenSSL returns for arbitrary input.
   OpenSSL itself is fuzzed continuously by OSS-Fuzz.

## 9. Amendment 3 (2026-11-02, M4 stage 4; the owner's choices of 2026-11-02)

1. **SNI is a table of names in the shim**, not a `CertResolver` interface called back from C (§3.3).
   `TlsConfig.addName(name, chain, key)`: an exact name ("api.example.com") or a wildcard for one label
   ("*.example.com"), case-insensitive, with an exact name winning over a wildcard. Any other name, or none, gets the
   default certificate. The shim's servername callback (`pick_certificate`) switches the connection to the entry's
   `SSL_CTX`. A dynamic resolver (ACME) may come later, as an addition.
2. **Hot reload swaps inside the shim**, not through `Arc<CertSet>`. `TlsConfig.reload(chain, key)` builds a new
   `SSL_CTX` and swaps it in under OpenSSL's own lock (`CRYPTO_THREAD_lock`). New handshakes present it; connections
   already made keep theirs (OpenSSL counts references). This needs no Send/Share from CeeSafe, and it works across
   threads. `reload`, `addName` and `rotateTicketKeys` take `borrow this`, so they can be called while a
   `TlsAcceptor` serves (`acceptor.configuration()`).
3. **Session tickets** use the shim's keys (`SSL_CTX_set_tlsext_ticket_key_evp_cb`: AES-256-CBC with HMAC-SHA256,
   OpenSSL 3's EVP callback):
   - `rotateTicketKeys` makes a new current key;
   - a ticket from the previous key still resumes and is renewed;
   - an older ticket gets a full handshake;
   - the keys are made with `RAND_bytes` and kept in the shim, never in CeeSafe memory.

   A client keeps a `TlsSession` (`TlsTransport.session()`, after reading, since TLS 1.3 sends its tickets after
   the handshake) and offers it with `TlsTransport.connectResuming`.
4. **Client certificates.** `requireClientCertificates(roots)` or `requestClientCertificates(roots)` (optional).
   - A certificate that does not verify fails the handshake in both modes (fail closed). Without one, `require`
     fails and `request` lets the client in with an empty identity.
   - The roots and the names sent in CertificateRequest are set on each connection, so a switch to an SNI entry's
     configuration keeps them.
   - The verified identity reaches handlers through `ConnectionContext` (Web ADR 0001 amendment 4).
   - A client presents one with `TlsConfig.clientCertificate(chain, key)`.
5. **Every server configuration has a session id context** ("web_tls"). Resumption and client certificates need one,
   and a context switch copies the new configuration's.
6. **New shim functions**, added to §3.2's table: `wt_cfg_add_name`, `wt_cfg_reload`, `wt_cfg_rotate_tickets`,
   `wt_cfg_client_auth`, `wt_ctx_session`, `wt_session_free`, `wt_ctx_offer_session`, `wt_ctx_resumed`,
   `wt_peer_subject` and `wt_peer_names`. `wt_peer_cert_der` now returns only a verified certificate.

## 10. Amendment 4 (2026-11-03, M5: the system's roots on Windows)

Found by running `examples/fetch` on Windows 11 ARM64 (OpenSSL 4.1.0): `trustSystem` rejected example.com, because
OpenSSL's default verify locations on Windows are a directory under its install that nothing fills, not the system's
certificate store.

1. **`wt_cfg_trust_default`** now also loads the Windows store, through OpenSSL's own `org.openssl.winstore://`
   loader (`SSL_CTX_load_verify_store`, OpenSSL 3.2 and later). Unix is unchanged: OpenSSL's default bundle or
   directory.
2. **On Windows, OpenSSL 3.2 or later** is required for `trustSystem`. With an older OpenSSL it trusts only the
   default locations, as before. `trustPem` works with every version.
3. **A limit we cannot lift from OpenSSL:** Windows installs some roots only when its own TLS stack (Schannel) first
   needs one, and OpenSSL's loader reads only the roots already in the store. On the test VM, www.google.com
   verified at once, but example.com (SSL.com and Comodo roots) only after `curl.exe` (Schannel) had fetched it. A
   server whose root is not yet installed fails with `TlsReason.Certificate`. A deployment that must not depend on
   that passes its roots with `trustPem`.
4. Checked on 2026-11-03 (docs/milestones/M5.md):
   - with the system's roots, `examples/fetch` gets www.google.com, and example.com after the root is installed;
   - expired.badssl.com and wrong.host.badssl.com are refused;
   - web_tls's 14 tests pass on Windows, macOS and Debian.

## 11. Amendment 5 (2026-11-03, on ceesafec 0.10.1: U14 and U17)

CeeSafe 0.10.0 adds `CEE_ALLOC_FOREIGN` and `ceesafe/foreign.h` (CeeSafe ADR 0060; upstream U14), and 0.10.1 adds
`CEE_ALLOC_FOREIGN_INIT` (ADR 0061; upstream U17). So §3.5 and §6.1's routing is built, and it replaces amendment
2.1. It was developed on the branch `u14-openssl-alloc`, which waited for U17 and then for item 5's finding.

1. **The routing.**
   - `wt_cfg_new`, the first thing the shim makes, sets `CRYPTO_set_mem_functions` once (`CRYPTO_THREAD_run_once`,
     which allocates nothing) to wrappers over `ceesafe_alloc`, `ceesafe_realloc` and `ceesafe_free` with
     `CEE_ALLOC_FOREIGN`.
   - If something else in the process used OpenSSL first, OpenSSL refuses and keeps its own allocator. That is safe:
     every block is freed by the allocator that made it.
   - A TLS fetch then counts 13,602 allocation attempts, against 79 without OpenSSL's. It ends with `live=0` and
     `foreign held=8139`, OpenSSL's global state, which is not a leak.
2. **In every build, not only test builds (§3.5 said test builds).**
   - A package's C file cannot tell a test build from another: the manifest has no per-profile define.
   - Without counting (CeeSafe ADR 0059: only a report, `CEESAFE_FAIL_ALLOC_AT` or `ceesafec test` turns it on),
     `ceesafe_alloc` is the platform's `malloc` behind one branch.
   - So the routing costs a release build nothing measurable, and it keeps one code path.
3. **The fuzz harness** compiles the shim without the CeeSafe runtime. `-DWT_STANDALONE` leaves OpenSSL its own
   allocator there.
4. **OpenSSL's one-time initialisation cannot survive a failed allocation**, so it runs under
   `CEE_ALLOC_FOREIGN_INIT` (CeeSafe ADR 0061, ceesafec 0.10.1; upstream U17).
   - Under `CEE_ALLOC_FOREIGN`, the allocation sweep failed at points 1-5 and 12-14 of `alpn_is_the_servers_choice`
     (status 137, no output). Each was inside `ossl_init_base`, `ossl_init_load_crypto_nodelete` or
     `do_err_strings_init`.
   - The cause: `CRYPTO_malloc`'s failure path raises an error, which calls `OPENSSL_init_crypto` inside the
     `pthread_once` already running. macOS aborts that; Linux would deadlock.
   - Now `wt_cfg_new`'s once-only setup calls `OPENSSL_init_ssl` itself, right after `CRYPTO_set_mem_functions`. A
     thread-local flag sends those allocations to the new number, and every later one goes to `CEE_ALLOC_FOREIGN`.
5. **A peer that is never told.** At points 13169-13175 of about 13,180, `handshake_and_echo` sometimes hung: 1 run
   in 2 to 15 under ASan, 1 in 180 without it.
   - The server's echo `SSL_write_ex` failed (`SSL_ERROR_SSL`), and the server dropped its poisoned transport with
     the client's bytes still unread. Its close was therefore a reset.
   - `tcpdump` on `lo0` (2026-11-03) shows the reset right behind the server's last data segment, carrying that
     segment's end as its sequence number.
   - In the runs that ended, the client's kernel accepted the reset. In the hung run it rejected it, and the socket
     stayed ESTABLISHED: the reset was out of sequence by the time it was handled, apparently processed before the
     data segment just ahead of it (RFC 5961 drops it).
   - The closed server never sent it again, so the client's read, which had no deadline, waited for ever.
   - A plain-TCP program that closes the same way woke the reader 72 times out of 72, so CeeSafe's reactor is not at
     fault, and neither is the shim.
   - **Fix:** the test's reads are bounded by 10 s (`bounded` in `tests/support.cee`), as every read of a real client
     or server is: a lost peer now fails the test instead of hanging it. `web_client` and the connection server
     already bound every read.
6. **Runs (2026-11-03, macOS arm64, ceesafec 0.10.1, the release):**
   - `ceesafec test`: 14 passed.
   - The allocation sweep with ASan and UBSan: 2800 runs, none failed.
   - The cancellation sweep: 660 runs, none failed.
