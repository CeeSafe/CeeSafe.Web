# Web ADR 0015 — v1.0 hardening: the audit, the fuzz farm, the API freeze, portability, the benchmark report

**Status:** proposed 2026-11-11. The owner answered §7.5 on 2026-11-11 (§6). The other parts wait for answers.
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

**Decision (proposed).**
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
    FreeBSD. This is an upstream item (U-numbered in `docs/ceesafe-upstream-work.md`), owned by the owner as U1–U9
    were.
  - CeeSafe.Web then adds a FreeBSD job, a VM inside an Ubuntu runner, for the sanitizer suite. It covers kqueue,
    and OpenSSL 3.5 from ports for the QUIC packages.
- **B. Best effort in v1.0.** FreeBSD is listed as untested. Users who build from the seed may report issues. It
  becomes supported in 1.x, when A's upstream work lands.
- **C. Dropped from the plan.** v1.0 names Linux, macOS and Windows.

**Recommendation: A if CeeSafe will take the upstream item before v1.0, otherwise B.** It is the owner's call, since
the owner does the upstream work (plan §11.3). Supporting FreeBSD also brings a kqueue that is not macOS's under
test, which is evidence the macOS runs cannot give.

## 6. The benchmark report

**Decision (proposed).**
- `benchmarks/servers/` gets the comparison servers of plan §8: Axum, Actix Web, Hyper, Kestrel and h2o, each with
  equivalent plaintext, JSON and streaming handlers, pinned versions and release builds. nginx is kept, since the
  existing results use it.
- The report is `docs/benchmarks/v1.0.md`. It is made by a script from the recorded JSON, with every field Web ADR
  0005 §2.4 lists (hardware, kernel, compilers and flags, reactor, concurrency, payload, TLS settings, warm-up) and
  the raw results beside it.
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
    - h2load (nghttp2 1.59.0, Ubuntu's), wrk2 (built at `44a94c1`), and a clone of the repository;
    - clang 18 and gcc 13 from Ubuntu.
  - **Smoke test.** `benchmarks/http`'s server builds there and answers one HTTP/1.1 and one h2c request. No load
    has been run.
  - **Not yet installed:**
    - oha, which has no checksummed release to verify;
    - the comparison servers;
    - nginx, whose Ubuntu package would start a service on port 80, so it is to be built into the prefix instead.

## 7. Questions for the owner

1. **The audit (§2):** a funded programme first (OSTIF or the Sovereign Tech Fund), a paid firm, or none for v1.0?
   *Recommended: a programme first, then a firm if none accepts within three months.*
2. **The fuzz farm (§3):** a nightly GitHub Actions workflow, about 22 CPU-hours per harness per night?
   *Recommended: yes.*
3. **The API freeze (§4):** committed `ceesafec doc` pages checked in CI, frozen at v1.0.0-rc.1? *Recommended: yes.*
4. **FreeBSD (§5):** supported (needs the upstream item), best effort, or dropped? *Recommended: supported if the
   upstream item lands before v1.0, otherwise best effort.*
5. **The benchmark machine (§6):** a dedicated rented server, or hanan with the gate advisory? *Recommended: a
   dedicated server.* **Answered 2026-11-11:** alfaisal, the owner's own server, once its services are moved off.

## 8. Tests

**Run (2026-11-11):** `ceesafec-0.27.0 doc --missing` in each of the 14 packages exits 0 with no output. `ceesafec doc`
gives 248 top-level public items (`## ` headings) across them.

**Proposed:**
- `tools/api_check.py` in CI: the committed pages match a regeneration.
- The nightly fuzz workflow, whose first night must find no crash on main. Its runs are recorded as Web ADR 0005
  §2.5 asks.
- A FreeBSD job, if §5 is A.
- The comparison benchmarks, run twice on the chosen machine to measure noise before the 5 % gate is applied.
