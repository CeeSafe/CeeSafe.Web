# Web ADR 0015 — v1.0 hardening: the audit, the fuzz farm, the API freeze, portability, the benchmark report

**Status:** accepted 2026-11-11. The owner accepted the recommendations of §7.1–7.4, and answered §7.5 with
alfaisal (§6).
**Scope:** implementation plan §7 "v1.0 hardening":
- an external audit of the parsers and of the TLS and QUIC adapters;
- a continuous fuzz farm;
- an API freeze through `ceesafec doc` diffs;
- portability to Linux, macOS, Windows and FreeBSD;
- the benchmark report, published with full environment details.

Web ADR 0005's gates stay as they are. This ADR adds what v1.0 needs on top of them, and says which of those things the
owner has to provide (money, machines, a decision) and which this repository can do itself.

## 1. Context

Checked 2026-11-11 on `main` at `82a3a73` (v0.5.0 plus the 0.27.0 toolchain, plan §11.13), with ceesafec 0.27.0.

- **What would be audited.** These face the network:
  - the HTTP/1.1 head parser and chunked decoder (`web_http1`);
  - HPACK and HTTP/2 frames and streams (`web_http2`);
  - WebSocket frames and permessage-deflate (`web_ws`);
  - the proxy's forwarding and PROXY-protocol listener (`web_proxy`);
  - static files' path rules and ranges (`web_static`);
  - three C shims: `packages/web_tls/c/tls_shim.c` (OpenSSL), `packages/web_quic/c/quic_shim.c` (ngtcp2) and
    `packages/web_http3/c/h3_shim.c` (nghttp3).

  None of it has been reviewed by anyone from outside the project.
- **Fuzzing today.** There are six harnesses under `tests/fuzz/`.
  - Four are CeeSafe programs: `http1`, `h2`, `ws` and `proxy`. They are mutational and run for `CW_FUZZ_SECONDS`.
  - Two are libFuzzer harnesses over the C shims: `tls` and `h3`.
  - `quic_shim.c` has no harness (`docs/milestones/M10.md`, "Fuzzing the HTTP/3 shim").
  - Each milestone's 24 CPU-hours (Web ADR 0005 §2.3, §4.1) were run by hand on hanan. The owner's answer to §4.1
    was "nightly", but nothing runs nightly yet.
  - hanan also runs production services (zatca, the mail server, cosmoproxy and others). So a fuzz job there is
    `nice` 19, idle I/O, and capped at 6 GB.
- **The API.**
  - `ceesafec doc` writes a deterministic Markdown page of a package's public items (CeeSafe `spec/tooling.md`
    §1.2: "the same files give the same bytes"). `ceesafec doc --missing` lists the public items without a doc
    comment.
  - On 2026-11-11, `--missing` listed none in any of the 14 packages, which hold 248 top-level public items. Web ADR
    0005 §2.6's blocker "a public API without documentation" is therefore met today.
  - Nothing yet records the API between releases. #8's change to `Send + Share` (plan §11.13) was found by the
    compiler, not by a diff.
- **Portability.**
  - CI runs macos-15, ubuntu-26.04 (epoll and io_uring), ubuntu-24.04 and windows-2022 (IOCP and WSAPoll). The
    owner's Windows 11 ARM64 VM passes v0.5.0 (`docs/milestones/M10.md`).
  - Nothing runs on FreeBSD. CeeSafe's release v0.27.0 ships binaries for linux-x64 and arm64, osx-x64 and arm64,
    and win-x64 and arm64, plus a C seed tarball, but no FreeBSD binary.
  - CeeSafe's runtime already selects kqueue for `__FreeBSD__` (`runtime/hosted/ceesafe_rt_async.c:49`), but no CI
    builds or runs it there.
  - GitHub has no FreeBSD runner. A FreeBSD VM can run inside an Ubuntu job (for example vmactions/freebsd-vm),
    more slowly than a native runner.
- **Benchmarks.**
  - `benchmarks/http` and `benchmarks/overload` exist. `benchmarks/http/results/` holds runs from 2026-11-06 to
    11-08 against nginx only, under epoll and io_uring.
  - Plan §8 and Web ADR 0005 §2.4 name Axum, Actix Web, Hyper, Kestrel and h2o in `benchmarks/servers/`, which
    does not exist.
  - The 5 % regression gate needs a dedicated machine (the owner's answer to Web ADR 0005 §4.2). The runs so far
    were on hosts that also run other work.

## 2. The external audit

**Options.**
- **A. A paid audit by a firm** (for example Trail of Bits, NCC Group, X41 or Cure53), scoped to §1's list. It is
  typically several person-weeks. The owner chooses the firm and the budget. Findings come in a report; v1.0 waits
  for its high and critical findings to be fixed.
- **B. A funded audit** through a programme that pays for open-source audits (OSTIF, the Sovereign Tech Fund). There
  is no cost to the owner, but there is an application and a queue of months.
- **C. No external audit for v1.0.** v1.0 ships with its fuzzing and conformance evidence, and its release notes say
  it has not been audited. An audit follows in a 1.x release.

**Recommendation: B, with A as the fallback if no programme accepts it within three months.** In either case, this
repository prepares the audit's scope document (`docs/audit-scope.md`): the code and its line counts, the threat
model (an untrusted peer on every listener, a trusted application), what each shim's contract promises, and the
evidence so far (fuzz hours, h2spec, Autobahn, the interop runner). The firm or programme then starts from it.

## 3. The continuous fuzz farm

**Options.**
- **A. A scheduled GitHub Actions workflow.**
  - It runs nightly, with one job per harness. Each job is up to 5.5 hours on a 4-vCPU public-repository runner,
    which is about 22 CPU-hours per harness per night, at no cost to a public repository.
  - The corpus is kept between nights in the Actions cache, and its minimised form is committed at release.
  - A crash, leak or timeout uploads its input as an artefact and opens an issue.
  - The libFuzzer harnesses (`tls`, `h3`, later `quic`) run with `-fork`. The CeeSafe harnesses run with
    `CW_FUZZ_SECONDS`.
- **B. ClusterFuzzLite** (Google's OSS-Fuzz runner for CI). It takes only libFuzzer-style harnesses, so the four
  CeeSafe harnesses would need a libFuzzer entry point first.
- **C. OSS-Fuzz itself.** Acceptance is not guaranteed for a project in a new language, and it has the same harness
  requirement as B.
- **D. A systemd timer on hanan, or on a dedicated machine.** On hanan, it shares a host that runs production.

**Recommendation: A.** It needs no machine and no application, and it makes the 24 CPU-hours per engine automatic
instead of a hand-run step. B and C can follow, once CeeSafe can build a libFuzzer entry point (proposed upstream if
wanted, not scheduled). The `quic_shim.c` harness is built as part of this work, from seeds recorded from a real
handshake. Its packet protection is the hard part (M10.md).

## 4. The API freeze

**Decision.**
- `docs/api/<package>.md` is generated by `ceesafec doc` for each package and committed.
- `tools/api_check.py` regenerates the pages, and fails CI if a page is out of date. `ceesafec doc --missing` also
  fails CI for any undocumented public item.
- A pull request that changes a page changes the public API, and says so in its description.
- From v1.0, a change that removes or changes an item (rather than adding one) waits for a 2.0, as semver requires.
  Before v1.0, such changes are allowed but visible.
- The freeze is declared at v1.0.0-rc.1. Between the release candidate and v1.0.0, only additions and fixes are
  allowed.

**Limit.** A Markdown diff shows that a signature changed, not whether the change breaks callers. A change to a
bound (as in #8) is a breaking change even when it reads as small. The reviewer judges this from the diff. A
mechanical semver check is a CeeSafe tooling question (proposed upstream if wanted).

## 5. FreeBSD

**Options.**
- **A. Supported in v1.0.**
  - CeeSafe first needs a FreeBSD build of `ceesafec` (from the seed, or a release binary), and its own CI on
    FreeBSD. This is U23 in `docs/ceesafe-upstream-work.md`, owned by the owner as U1–U9 were.
  - CeeSafe.Web then adds a FreeBSD job, a VM inside an Ubuntu runner, for the sanitizer suite. It covers kqueue,
    and OpenSSL 3.5 from ports for the QUIC packages.
- **B. Best effort in v1.0.** FreeBSD is listed as untested. Users who build from the seed may report issues. It
  becomes supported in 1.x, when A's upstream work lands.
- **C. Dropped from the plan.** v1.0 names Linux, macOS and Windows.

**Recommendation: A if CeeSafe will take the upstream item before v1.0, otherwise B.** It is the owner's call, since
the owner does the upstream work (plan §11.3). Supporting FreeBSD also brings a kqueue that is not macOS's under
test, which is evidence the macOS runs cannot give.

## 6. The benchmark report

**Decision.**
- `benchmarks/servers/` gets the comparison servers of plan §8: Axum, Actix Web, Hyper, Kestrel and h2o, each with
  equivalent plaintext, JSON and streaming handlers, pinned versions and release builds. nginx is kept, since the
  existing results use it.
  - **Written (2026-11-17)**, on branch `bench-servers` (`benchmarks/servers/README.md`):
    - **The contract** (`benchmarks/servers/conform.py`): `/small`, `/4k`, `/64k`, `/stream`, `/json` and `/echo`,
      the bodies `benchmarks/http`'s server already sent. `/json` (`{"message":"Hello, World!"}`, serialised for each
      request) is new on both sides; CeeSafe.Web's uses CeeSafeLibs' `json` (`JsonWriter`, pinned as `web_quic`
      pins ngtcp2).
    - **The servers:** Hyper 1.12.0, Axum 0.8.9 and Actix Web 4.20.0 (one Cargo workspace, `Cargo.lock` committed);
      Kestrel on .NET 10 (SDK 10.0.401 by `global.json`); h2o master `d3e43fcf`, which has had no release since 2.2.6
      (2019). Relic 2.0.0 was added at the owner's request ("Can you add Relic 2.0 into the servers."). It is
      Serverpod's Dart server (Dart 3.14.1, `pubspec.lock` committed), on both of its adapters: `relic_io` and
      `relic_native` (2.0's server in Zig). Both speak HTTP/1.1 only.
    - **`benchmarks/http/run.py --server <name>`** measures any of them. It runs `conform.py` first, CeeSafe.Web's
      server included, and measures nothing from a server that fails it (plan §8).
    - **Not yet in it:** TLS, and Kestrel's h2c on the HTTP/1.1 port. Kestrel cannot serve h2c on a cleartext
      endpoint that also speaks HTTP/1.1, so its h2c has a port of its own.
  - **Run (2026-11-17, macOS 27.0.1 on an Apple M1, Rust 1.99.1, .NET SDK 10.0.401, h2load from nghttp2):**
    - `conform.py` passes on all six servers: 12 HTTP/1.1 requests on one kept-alive connection and 6 h2c requests
      each, 0 problems. It passes on both Relic adapters over HTTP/1.1 (`--no-h2c`), with Dart 3.14.1 (the SDK
      checked against its published SHA-256).
    - It failed where it should: on CeeSafe.Web's server built before `/json` (404), and on Kestrel with both
      protocols on one cleartext port (no h2c response).
    - `run.py` ran each server, 2 s at 10 connections on four routes over h1 and h2c (Relic h1 only), with 0
      failed requests. That
      checks the pipeline only; the numbers are not results (a laptop, the load generator beside the server).
- The report is `docs/benchmarks/v1.0.md`. It is made by a script from the recorded JSON, with every field Web ADR
  0005 §2.4 lists (hardware, kernel, compilers and flags, reactor, concurrency, payload, TLS settings, warm-up) and
  the raw results beside it.
  - **Written (2026-11-17), on branch `bench-report`:**
    - **`run.py --json`** writes `{"environment", "results"}`. The environment records:
      - the machine: CPU, logical CPUs, memory and architecture;
      - the OS, the kernel, the commit, and the load average before the run;
      - the compiler and flags: ceesafec and the C compiler with `ceesafec build`'s `-O2`, or the comparison
        server's toolchain and its pinned versions from `Cargo.lock`, `pubspec.lock` or `build.sh`;
      - the reactor, the load generator, the duration and warm-up, the connections, the modes, the routes and their
        payloads;
      - for TLS, the libcrypto the server links, as reported by the library itself. The `openssl` on PATH may be a
        different one (macOS's is LibreSSL).

      Each TLS row records what h2load negotiated: the protocol, cipher, key exchange and ALPN. Each row also gains
      a p95.
    - **`benchmarks/report.py`** writes the report from those files:
      - the sources, with their SHA-256;
      - the environments side by side, with a warning when the runs were not on one machine and kernel;
      - each mode and route's rows for every server;
      - the allocations per request.

      Files from before the environment was recorded still read, marked "not recorded".
    - **The regression gate:** `--baseline <previous release's files>` sets each CeeSafe.Web row against the
      baseline's. A fall in requests a second, or a rise in p99, of more than `--gate` (5 %) is a regression, and
      the script exits 1. It exits 2 when the baseline was not on the same machine and kernel, because the gate
      cannot be judged. `--advisory` reports the same and exits 0.
  - **Run (2026-11-17, the M1 laptop, 2 s at 10 connections):**
    - `run.py` ran CeeSafe.Web over h1, h2c, tls-h1 and tls-h2, and Hyper over h1 and h2c.
    - Every TLS row negotiated TLSv1.3, `TLS_AES_256_GCM_SHA384` and X25519, with ALPN `http/1.1` or `h2` as the
      mode asks. The library read was OpenSSL 3.6.4, where the `openssl` on PATH is LibreSSL 3.3.6.
    - `report.py` read those files and the 2026-11-07 ones.
    - Against a copy of a run with one row's requests a second raised by 20 %, the gate exited 1 and marked that row
      alone. Against the run itself, it exited 0. With `--advisory`, it exited 0. Against the 2026-11-06 file,
      which recorded no environment, it exited 2.
    - The laptop's load average was 42 during the run, so the numbers are not results.
  - **Added (2026-11-18), on branch `bench-cpu-idle-tls`:** three more of Web ADR 0005 §2.4's measures, each in
    `run.py`'s rows and in its own table of `report.py`.
    - **CPU.** It is the server's process and children's user and system time over each run, from `/proc` on Linux
      and `ps` elsewhere. nginx's workers are its master's children. It is reported as the cores kept busy over the
      run's wall time, warm-up included, and as microseconds a request at the measured rate. The load generator's CPU
      is not in it.
    - **Bytes per idle connection** (`--idle`, 200 by default). The run makes HTTP/1.1 connections, in clear and
      over TLS, and holds each idle for a second after one request. The server's RSS growth, divided by their
      count, is the measure. The run also checks that every connection is still open at the measurement.
      - 200 is below CeeSafe.Web's `CW_WORKERS=256`, which bounds the connections it holds (plan §11.7).
      - h2c's idle connections are not measured yet.
    - **TLS handshakes:** oha with keep-alive off on tls-h1, so each request is a new connection and a full
      handshake.
      - tls-h2 is not used for this. With `--http2`, oha 1.16 keeps its connections despite `--disable-keepalive`;
        it measured 52,026 "handshakes" a second. A handshake costs the same whichever protocol ALPN picks.
      - The requests still in flight at the deadline are counted apart (`cut_at_deadline`), not as errors.
  - **Run (2026-11-18, the M1 laptop, 2 s at 10 connections, load average 2.2):** CeeSafe.Web, nginx (Homebrew's)
    and Hyper. The numbers check the pipeline, not results, but two of them already stand out:
    - **Idle connections.** All 200 were still open at the measurement each time.

      | server | bytes per idle connection, HTTP/1.1 | over TLS |
      |---|---:|---:|
      | CeeSafe.Web | 70,615 | 137,544 |
      | Hyper | 28,672 | not measured |
      | nginx | 8,028 | 28,836 |

      Plan §8 expects bytes per idle connection to be a strong claim for CeeSafe.Web.
      - **Where the bytes went** (2026-11-18). Measured with macOS's `heap` on the server, before and after 200 idle
        connections, each connection held these blocks:
        - four of 16 KiB: two head buffers (`maxHeadBytes`), and the write buffer `out` and the response buffer
          `pending` (`writeBufferBytes`);
        - one of 4 KiB, the response head;
        - one of 256 bytes.

        That is 68 KiB, from `packages/web_http1/src/serve.cee`, allocated at a connection's start and kept for its
        life.
        - The 256 connection workers' frames, 14 KB each, are made at startup and are not counted per connection.
        - An earlier guess on this page named `readBufferSize`. That is not among them.
      - **Changed** (the owner, asked how far to go: "Lazy and small buffers"). `serve.cee` now sizes the head
        buffers:
        - The head buffer starts at 4 KiB. It is reserved up to `maxHeadBytes` only for a longer head, and a head
          past the limit is still 431.
        - The second head buffer starts empty. It takes `maxHeadBytes` only when bytes follow a head, or when
          `Exchange` reads a body or a peer's close into it (`roomInCarry`).
        - `out` and `pending` keep 16 KiB, because `pending` is what gives a response shorter than it a
          `Content-Length`.
        - `OwnedBuffer.reserve`, new in `web_core`, is the explicit reallocation. Buffers still never grow by
          themselves (Web ADR 0001 §3.1).

        Measured afterwards (`heap`, then `run.py`):

        | connection | bytes | before |
        |---|---:|---:|
        | idle GET, `heap` | 40 KiB | 68 KiB |
        | after a request body, `heap` | 56 KiB | 68 KiB |
        | idle HTTP/1.1, `run.py` | 41,943 | 70,615 |
        | idle over TLS, `run.py` | 108,626 | 137,544 |

        - **Allocations per request** are unchanged: main and the branch gave the same on all 12 h1 and h2c routes,
          for example 0 on `/small` and 1 on `/echo`.
        - **Tests:** every package's tests pass, and so do the allocation and cancellation sweeps of `web_core`,
          `web_http` and `web_http1`: 3,135 sweep runs and 0 failures, with ceesafec 0.62.2.
        - **Still to account for:** nginx holds 8 KB, and its TLS connections about 21 KB more than its plain ones.
      - **Where a TLS connection's further bytes went** (2026-11-18, macOS 27.0.1 on the M1, ceesafec 0.62.2,
        Homebrew's OpenSSL 3.6.4). `heap --showSizes` on `benchmarks/http`'s server, before and after 200 idle
        HTTP/1.1 connections (one request each, then held), gave 102,659 bytes a connection over TLS and 41,215 in
        clear. Each block's site is from `malloc_history` in a second run with `MallocStackLogging=1`, which moves
        every block up one size class, so the sizes below are from the first run.

        | held by | blocks | bytes |
        |---|---|---:|
        | `web_http1`'s `serve.cee`, as in clear | 16 KiB × 2 (`out`, `pending`), 4 KiB × 2 (head buffer, response head) | 40,960 |
        | `TlsTransport.incoming` (`packages/web_tls/src/transport.cee:105`) | 17,413 bytes, which macOS's allocator rounds to 20 KiB | 20,480 |
        | `TlsTransport.outgoing` (`transport.cee:106`) | 16 KiB | 16,384 |
        | OpenSSL's `SSL_new` (`wt_ctx_new`) | the connection object, 6 KiB | 6,144 |
        | OpenSSL's record layers (`tls1_change_cipher_state`), one each way | 5 KiB × 2 | 10,240 |
        | OpenSSL and the transport's smaller blocks | 1 KiB × 4, and about 40 blocks under 1 KiB | 8,451 |

        - **OpenSSL holds about 24.6 KB** a connection, near nginx's 21 KB over its plain connections. Its record
          buffers are already released between records (`SSL_MODE_RELEASE_BUFFERS`), and the memory BIOs hold
          nothing when idle.
        - **The rest of the gap is the transport's own 36 KiB.** Both buffers stay allocated for the connection's
          life, and an idle connection waits in `pull` with `incoming` lent to the read.
          - `outgoing` holds nothing between writes: `sendOutput` takes the shim's ciphertext into it and writes it
            before it returns.
          - `incoming` holds bytes between reads only when a read was cancelled, or when the shim took fewer than
            were read.
      - **Changed** (the owner, asked how far to go: "Small, grow on need"). In `transport.cee`:
        - Both buffers start at 4 KiB (`FIRST_BYTES`).
        - `outgoing` is reserved up to 16 KiB when the shim holds more ciphertext than it, on a write of application
          data.
        - `incoming` is reserved up to a record (17,413 bytes) when a read fills it.
        - Neither grows during the handshake or for an alert, and a failed reservation keeps the small buffer, which
          still carries every byte in more reads and writes. A connection keeps what it reserved.

        Measured afterwards (2026-11-18, the M1, ceesafec 0.70.0 for both builds):

        | | main | the branch |
        |---|---:|---:|
        | idle TLS connection after `/small`, `heap` | 102,659 | 73,987 |
        | allocations per request, tls-h1 (`/small`, `/4k`, `/64k`, `/echo`) | 7, 7, 23, 8 | 7, 7, 23, 8 |
        | allocations per request, tls-h2 (the same) | 17.80, 17.84, 46.00, 20.78 | 17.76, 17.70, 46.01, 20.67 |

        - **Tests:** every package's tests pass (26 runs, 2 interpreter runs skipped for C). `web_tls`'s allocation
          sweep (3,590 runs) and cancellation sweep (969 runs) pass with 0 failures.
        - **Not measured:** throughput. Other builds kept the laptop's load average between 26 and 120, and plain
          HTTP/1.1, whose code did not change, fell as far as TLS. Interleaved 3 s runs of tls-h1 `/64k` and `/echo`
          put main and the branch within each other's noise. Once a connection's buffers have grown, its path is
          main's. It is to be measured on rq.
        - `run.py`'s RSS-based idle figure gave 4,178 bytes for main's TLS connections in that run, which `heap`
          contradicts; under that load it is not a measurement.
    - **TLS handshakes at 10 connections:**

      | server | handshakes a second | CPU per handshake | cores kept busy |
      |---|---:|---:|---:|
      | CeeSafe.Web | 3,671 | 251 µs | 0.92 |
      | nginx | 7,671 | 390 µs | 2.99 |

      CeeSafe.Web's handshakes are bounded by one core, while nginx spreads them over its workers.
  - **Added (2026-11-18), on branch `bench-slo`: requests a second at a fixed p99, under load at a constant rate.**
    `run.py --slo-p99-ms <target>` adds a row for each h1 and tls-h1 row of `--slo-routes` (`/small` and `/json`
    by default).
    - **The load.** wrk2 (`$CW_WRK2`, else on PATH) runs at constant rates. A request's latency counts from when it
      was due, so a stall cannot hide behind the requests that waited for it (coordinated omission).
    - **The search.** Six probes of `--slo-duration` seconds (30) bisect from 0 up to h2load's rate for the same
      row. The row's figure is the highest rate that wrk2 achieved to 97 %, with p99 within the target and no
      errors.
    - **What is left out.** wrk2 has no HTTP/2, and `/echo` would need a Lua script.
    - **The report.** `report.py` gives these rows their own table, and the environment records wrk2 and the
      target.
  - **Tested (2026-11-18) on the FreeBSD 15.1 PC.** wrk2 was built there at `44a94c1`, which took two changes:
    - `CC=cc`, since FreeBSD has no `gcc`;
    - the Lua bytecode compiled from C (`luajit -b` to a `.c` file), since LLVM's linker refuses LuaJIT 2.0's
      object file ("SHT_STRTAB string table section … is non-null terminated").

    rq's wrk2, built with gcc on Linux, needed neither. At 10 connections, on CeeSafe.Web's server:

    | target | mode | h2load's closed-loop rate | rate within the target | its p99 |
    |---|---|---:|---:|---:|
    | 5 ms | h1 | 145,705 | 143,428 | 2.33 ms |
    | 5 ms | tls-h1 | 80,507 | 79,249 | 2.97 ms |
    | 1 ms | h1, tls-h1, `/small` and `/json` | | 0 | over 1.17 ms |

    The 1 ms result is wrk2's floor, not the server's. wrk2's p99 stayed at 1.17 ms or more even at 2,277 a
    second, where h2load's closed-loop p99 at 145,000 a second was 70 µs. wrk2 schedules requests with
    millisecond timers, so a target must sit well above its floor, which was about 2 ms at full rate. The report
    lists every probe.
  - **Fixed on the way:** the server's CPU read as 0 on FreeBSD. FreeBSD's `ps` takes everything after `pid=` in
    `-o pid=,ppid=,…` as one column's header, so `run.py` now passes one `-o` a column. CPU read 0.89 cores there
    afterwards, and macOS still reads it.
- **The machine (the owner's choice):**
  - **A.** A dedicated bare-metal server, rented by the month (for example a Hetzner AX-line machine, or similar),
    used for nothing else. The load generator runs on a second machine or on pinned cores.
  - **B.** hanan, with its other services' load recorded alongside each run. This does not meet "dedicated"
    (Web ADR 0005 §4.2), so the 5 % gate would be advisory.

  **Recommendation: A**, since the regression gate and a published comparison both depend on it.

  **The owner's answer (2026-11-11): A, on alfaisal.** It is the owner's own machine, a Xeon E-2136 (6 cores, 12
  threads, up to 4.5 GHz) with 31 GB, NVMe, Ubuntu 24.04 and kernel 6.8.0-146.
  - **Not yet idle.** On the day, alfaisal still ran production containers: zatca, s3zatca, cosmoproxy,
    CosmoProduction, sqlserver, zatca.seq and autoheal.
  - **The order.** The owner moves them off first, and no benchmark runs there until the owner says it is clear.
  - **Set up (2026-11-11), in `/root/cw-bench`:**
    - ceesafec 0.27.0 (linux-x64, checked against the release's SHA256SUMS);
    - OpenSSL 3.5.9 built into `openssl-3.5`, from the tarball checked against
      `603f5602…b859a` (`env.sh` sets PATH, CPATH, LIBRARY_PATH and LD_LIBRARY_PATH as CI does);
    - h2load (nghttp2 1.59.0, Ubuntu's), wrk2 (built at `44a94c1`), oha 1.16.0 (the release's `oha-linux-amd64`;
      it has no published checksum, so its SHA-256 as downloaded is recorded: `620bb9e1…391716`), and a clone of
      the repository;
    - clang 18 and gcc 13 from Ubuntu.
  - **Smoke test.** `benchmarks/http`'s server builds there and answers one HTTP/1.1 and one h2c request. No load
    has been run.
  - **Not yet installed:**
    - the comparison servers;
    - nginx, whose Ubuntu package would start a service on port 80, so it is to be built into the prefix instead.

  **The owner's answer (2026-11-17): rq instead of alfaisal.** In the owner's words: "use this new box instead of
  alfaisal. ssh root@rq.iserveus.com".
  - **The machine.** Its hostname is bijuserver. It has a Xeon D-2123IT (4 cores, 8 threads, 2.2 to 3.0 GHz), 31 GB,
    NVMe, Ubuntu 24.04.5 and kernel 6.8.0-149.
  - **Not yet idle.** On the day, it ran the production containers alfaisal had: zatca, s3zatca, cosmoproxy,
    CosmoProduction, sqlserver, zatca.seq, sqlbackup, sql-watchdog and autoheal, plus a .NET service on ports 80 and
    443.
  - **The order.** Asked, the owner chose to set the machine up now and to run no load until they say the services
    are off.
  - **Set up (2026-11-17 to 18), in `/root/cw-bench`, at nice 19:**
    - ceesafec 0.62.2 (linux-x64), checked against the release's SHA256SUMS;
    - OpenSSL 3.5.9 into `openssl-3.5`, checked against `603f5602…b859a`;
    - h2load from nghttp2 1.59.0 (Ubuntu's), wrk2 built at `44a94c1`, and oha 1.16.0 (SHA-256 `620bb9e1…391716`, the
      same as on alfaisal);
    - nginx 1.30.6, built into the prefix against OpenSSL 3.5.9. Its tarball's SHA-256 as downloaded is
      `3cf46f5e…b8b6c8`; nginx publishes a signature, not a checksum;
    - clang 18 and gcc 13 from Ubuntu;
    - the comparison servers' toolchains: Rust 1.99.1 (rustup), .NET SDK 10.0.401 (dotnet-install), and Dart 3.14.1,
      checked against its published SHA-256;
    - `env.sh`, which sets PATH, CPATH, LIBRARY_PATH and LD_LIBRARY_PATH as CI does. It also sets SSL_CERT_FILE,
      because OpenSSL 3.5.9's own directory has no CA certificates.
  - **Conformance (2026-11-18, `conform.py` against each server, no load):** 0 problems on CeeSafe.Web, Hyper, Axum,
    Actix Web, Kestrel, h2o, and Relic on both adapters. Two servers needed fixes first:
    - **Kestrel** would not start. `RollForward=Disable` asked for exactly .NET 10.0.0, and the pinned SDK brings
      10.0.12. It had started on the Mac only because 10.0.0 happened to be installed there, so the Mac's runs used
      that unpatched runtime. It now runs on the newest 10.0 patch (`LatestPatch`), and the run records which
      (#40).
    - **h2o** failed at its mruby handlers with "cannot load such file -- …/preloads.rb". Started as root, h2o drops
      to `nobody`, which cannot read `/root`. `start.py` now says `user: root` when started as root.

## 7. Questions for the owner

1. **The audit (§2):** a funded programme first (OSTIF or the Sovereign Tech Fund), a paid firm, or none for v1.0?
   *Recommended: a programme first, then a firm if none accepts within three months.* **Accepted 2026-11-11.**
2. **The fuzz farm (§3):** a nightly GitHub Actions workflow, about 22 CPU-hours per harness per night?
   *Recommended: yes.* **Accepted 2026-11-11.**
3. **The API freeze (§4):** committed `ceesafec doc` pages checked in CI, frozen at v1.0.0-rc.1? *Recommended: yes.*
   **Accepted 2026-11-11.**
4. **FreeBSD (§5):** supported (needs the upstream item), best effort, or dropped? *Recommended: supported if the
   upstream item lands before v1.0, otherwise best effort.* **Accepted 2026-11-11.** The upstream item is U23.
5. **The benchmark machine (§6):** a dedicated rented server, or hanan with the gate advisory? *Recommended: a
   dedicated server.* **Answered 2026-11-11:** alfaisal, the owner's own server, once its services are moved off.

## 8. Tests

**Run (2026-11-11):** `ceesafec-0.27.0 doc --missing` in each of the 14 packages exits 0 with no output. `ceesafec doc`
gives 248 top-level public items (`## ` headings) across them.

**Proposed:**
- `tools/api_check.py` in CI: the committed pages match a regeneration.
- The nightly fuzz workflow, whose first night must find no crash on main. Its runs are recorded as Web ADR 0005
  §2.5 asks.
- A FreeBSD job, if §5 is A. *Done 2026-11-16* (pull request #30): CI's `freebsd` job runs FreeBSD 15.1 (amd64) in a
  VM inside an Ubuntu job (`vmactions/freebsd-vm`, pinned), with the released ceesafec (0.53.0, CeeSafe ADR 0149)
  and the base system's OpenSSL 3.5.6; `web_tls` and `web_quic` name it with `[target.freebsd]`. The matrix's steps
  pass there, with three differences:
  - the VM has 12 GB: with the default 6 GB, three packages' interpreter runs were killed for memory;
  - `web_ws` ran natively only until main's minimum was 0.57.1 (2026-11-16, #32): in FreeBSD's interpreter it grew
    past 11.4 GB (3.35 GB on macOS), CeeSafe's U24; with 0.57.1 its 26 tests pass there in the interpreter;
  - the sweeps link OpenSSL 3.5.9, built in the job, as the Linux sweep job does: with 3.5.6 a failed allocation
    left a lock of OpenSSL's decoder cache held, and `web_quic`'s `early_data_before_its_initial_is_held` waited for
    ever at allocation 2916 (reproduced on the owner's FreeBSD 15.1 PC; with 3.5.9 its sweep passes, and so do
    `web_quic`'s, `web_http3`'s and `web_tls`'s whole sweeps there).
- The comparison benchmarks, run twice on the chosen machine to measure noise before the 5 % gate is applied.
