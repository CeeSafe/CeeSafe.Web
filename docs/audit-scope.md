# CeeSafe.Web — scope for an external security audit

**For:** the funded audit programme (OSTIF or the Sovereign Tech Fund) or, failing one within three months, a paid firm
(Web ADR 0015 §2; implementation plan §11, item 14). **Prepared:** 2026-11-11, on `main` at v0.5.0 plus the 0.27.0
toolchain. **Contact:** the project owner, through github.com/CeeSafe/CeeSafe.Web.

## 1. What CeeSafe.Web is

A networking and web framework: HTTP/1.1, HTTP/2 and HTTP/3 servers, an HTTP client, TLS, WebSockets, Server-Sent
Events, a reverse proxy and static files. It is written in CeeSafe, a memory-safe systems language compiled ahead of
time to C. Ownership and borrowing are checked at compile time, and there is no garbage collector
(https://ceesafe.github.io/CeeSafe/). The only C is three shims over OpenSSL, ngtcp2 and nghttp3, each behind a
contract stated in its ADR (`docs/decisions/`).

Every protocol engine is sans-I/O (Web ADR 0004): it takes bytes and returns bytes and events, with no sockets,
timers or `await`. So each can be read, tested and fuzzed on its own.

## 2. Threat model

- **Untrusted:** every byte from the network peer, on every listener, server and client side alike. That includes
  bytes before and after TLS and QUIC packet protection, PROXY-protocol headers on a listener that trusts no source,
  and a backend's responses to the proxy.
- **Trusted:**
  - the application that links the library, with its handlers, configuration and certificates;
  - the operating system;
  - OpenSSL, ngtcp2 and nghttp3 as libraries, though not the shims' use of them;
  - the CeeSafe compiler and runtime. CeeSafe's own soundness is CeeSafe's audit, not this one, but findings that
    show it failing are welcome.
- **Wanted from the audit:**
  - memory-safety faults in the C shims;
  - request smuggling and desynchronisation (HTTP/1.1 framing, HTTP/2 to HTTP/1.1 in the proxy);
  - resource-exhaustion paths: floods, slow peers, compression bombs, unbounded queues;
  - header and path handling that lets a peer reach what it should not (static files' path rules, forwarding
    headers);
  - TLS and QUIC state misuse (0-RTT replay, session tickets, Retry and address validation, stateless reset).

## 3. In scope

Lines are counted on 2026-11-11 (`src/*.cee`, and the C files as they are).

| Area | Code | Lines | Design |
|---|---|---|---|
| HTTP/1.1 head parser, chunked bodies, keep-alive | `packages/web_http1/src/` (`head.cee`, `serve.cee`, `client.cee`) | 880 | Web ADR 0003, plan §7 M2 |
| HTTP semantics shared by every version | `packages/web_http/src/` | 3,391 | Web ADR 0003, 0007 |
| HTTP/2 frames, HPACK, streams, flow control, flood guards | `packages/web_http2/src/` | 3,554 | Web ADR 0011 |
| WebSocket frames, handshake, permessage-deflate (its own inflate) | `packages/web_ws/src/` | 2,443 | Web ADR 0010 |
| Reverse proxy: forwarding headers, PROXY protocol v1 and v2, balancing | `packages/web_proxy/src/` | 1,824 | Web ADR 0012 |
| Static files: path rules, ranges, conditional requests | `packages/web_static/src/` | 851 | Web ADR 0013 §3.4–3.6 |
| QUIC over UDP (CeeSafe side) | `packages/web_quic/src/` | 2,126 | Web ADR 0014 |
| HTTP/3 (CeeSafe side) | `packages/web_http3/src/` | 1,486 | Web ADR 0014 |
| **C:** the OpenSSL shim | `packages/web_tls/c/tls_shim.c` | 1,181 | Web ADR 0002 |
| **C:** the ngtcp2 shim | `packages/web_quic/c/quic_shim.c` | 1,408 | Web ADR 0014 §3 |
| **C:** the nghttp3 shim | `packages/web_http3/c/h3_shim.c` | 876 | Web ADR 0014 §3 |

In all, 16,555 lines of CeeSafe and 3,465 of C. The C shims are the priority: they are the only code where the
compiler's checks do not apply.

## 4. Out of scope

- The vendored ngtcp2 1.26.0 and nghttp3 1.19.0 (`third_party/`), and OpenSSL. They are fuzzed upstream (OSS-Fuzz),
  but their use by the shims is in scope.
- `web_core`, `web_transport`, `web_server`, `web_client` and `web_sse`, except where a finding in scope runs
  through them. They hold the connection drivers and the API rather than the parsing.
- Examples, benchmarks and tests.

## 5. Evidence so far

Each item has its date, command and result in `docs/milestones/`.

- **Fuzzing (24 CPU-hours per engine, Web ADR 0005 §2.3):**
  - `tests/fuzz/http1`: 4 seeds × 24 h, 910 M inputs, clean (M2).
  - `tests/fuzz/h2`, server and client roles with HPACK: 24 CPU-hours twice, clean (M8).
  - `tests/fuzz/proxy`: 24 CPU-hours, 1.1 billion cases, clean (M8).
  - `tests/fuzz/tls`, libFuzzer over `tls_shim.c`: 2 workers × 12 h, clean (M4).
  - `tests/fuzz/ws`: only a 120-second run is recorded (M6). It never had its 24 CPU-hours, so the first nightly
    runs are its first long ones.
  - `tests/fuzz/h3`, libFuzzer over `h3_shim.c`: 24 CPU-hours (M10, 2026-11-10 and 11). It found four misuses in
    the harness's own call order and no defect in the shim.
  - `tests/fuzz/quic`, libFuzzer over `quic_shim.c` and ngtcp2: a client and a server in one process, with real
    packet protection or null protection (M10, "Fuzzing the QUIC shim", 2026-11-11).
    - It found one assertion reachable from a peer's Initial. Only a guess of 160 random bits reaches it in
      web_quic, and the shim now guards it.
    - Its runs so far are only 10 minutes. Its 24 CPU-hours come from the nightly job.
  - From Web ADR 0015 §3, every harness runs nightly in GitHub Actions (`.github/workflows/fuzz.yml`).
- **Conformance:**
  - h2spec 145/145 over TLS (M7);
  - Autobahn, 517 cases as server and as client: 514 OK, 3 informational, none failed (M6);
  - quic-interop-runner against other implementations, and curl over HTTP/3 (M10).
- **Faults:**
  - every package's tests run under ASan and UBSan;
  - an allocation-failure sweep fails each allocation in turn, through OpenSSL's allocations too (CeeSafe U14);
  - a cancellation sweep;
  - CI runs these on Linux, macOS and Windows (`.github/workflows/ci.yml`).
- **Known weaknesses already handled:** the HTTP/2 flood classes (CVE-2023-44487 rapid reset, CVE-2024-27316
  CONTINUATION, and others) are acceptance criteria in Web ADR 0011, not follow-ups.
- **A dependency warning:** OpenSSL 3.5.0 to 3.5.5 corrupt the session cache when an allocation fails
  (openssl/openssl#28632, fixed in 3.5.6). The v0.5.0 release notes advise 3.5.6 or later.

## 6. How to build and run

```sh
# ceesafec 0.27.0 (docs/toolchain.md); OpenSSL >= 3.0 for web_tls, >= 3.5 for web_quic and web_http3
python3 tools/run_tests.py --sanitize --alloc-sweep --cancel-sweep   # every package, every mode
(cd tests/fuzz/http1 && ceesafec build --cflag -fsanitize=address,undefined && CW_FUZZ_SECONDS=60 ./out/fuzz_http1)
```

The libFuzzer harnesses' build lines are at the top of `tests/fuzz/tls/fuzz_shim.c`, `tests/fuzz/h3/fuzz_h3.c` and
`tests/fuzz/quic/fuzz_quic.c`.

## 7. What the audit returns

- A report with each finding's severity, the code (`file:line`), and a reproduction where there is one.
- Findings are fixed before v1.0 if they are high or critical (Web ADR 0005 §2.6), and the rest are tracked as
  issues.
- The report is published with v1.0, after the fixes.
