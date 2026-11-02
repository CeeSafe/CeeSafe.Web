# Web ADR 0005 — Quality gates: conformance, faults, fallbacks, benchmarks, release blockers

- Status: **Accepted** by the owner, 2026-11-02 (every question of §4 as recommended).
- Scope: what every change, milestone and release must pass, and how evidence is recorded. It applies the
  implementation plan's §6 (test infrastructure) and §8 (benchmarks), and follows CeeSafe's evidence discipline
  (CeeSafe `AGENTS.md`).

## 1. Context

- The roadmap: "Never trade away correctness or security for benchmark scores." Each milestone has an exit gate.
- CeeSafe provides:
  - the interpreter with a virtual clock (deterministic);
  - `--check-leaks` (`live=0` at exit);
  - the allocation-failure sweep (`CEESAFE_FAIL_ALLOC_AT`, `tools/fault_sweep.py`);
  - the cancellation sweep (`tools/cancel_sweep.py`, a cancellation at every suspension);
  - sanitizer builds;
  - several reactors (`CEESAFE_REACTOR_URING=1`, `CEESAFE_REACTOR_POLL=1`).
- There is no `ceesafec test` command.

## 2. Decision

### 2.1 Every change (pre-merge)

1. In every touched package:
   - `ceesafec check` with no errors;
   - `ceesafec fmt --check` lists no file;
   - `ceesafec doc --missing` lists no public item without a doc comment.
2. `python3 tools/check_layers.py` passes.
3. Tests (`tools/run_tests.py`, built in M0):
   - each test is a small executable package under `tests/<kind>/<name>/` that prints `ok` lines or fails;
   - each runs **twice**, once in the interpreter and once natively; the outputs must be identical;
   - tests that need C (TLS, QUIC) are marked native-only.
4. Native runs use ASan + UBSan (`--cflag -fsanitize=address,undefined`). Tests using `runWorkers`/`spawnSend` also
   run under TSan.
5. Every test runs with `--check-leaks`: `live=0` at exit, and the descriptor count back to its baseline (the runner
   compares `/dev/fd` or the platform equivalent before and after).

### 2.2 Every milestone (exit)

- The milestone's gate from the implementation plan §7, with the results recorded (§2.5).
- **Sweeps** over the milestone's tests:
  - the allocation-failure sweep: every allocation fails in turn, with structures consistent and `live=0`;
  - the cancellation sweep: a cancellation at every suspension, with no leak and no lost value.
- **Fragmentation:** every engine's suite runs over `MockTransport` at every split point (inputs ≤ 4 KiB) and with
  1,000 seeded random splits (larger inputs).
- **Fuzzing:** each engine has a libFuzzer harness built from `--emit c` with ASan. A milestone that adds or changes
  an engine runs it for 24 CPU-hours without a new crash. Corpora are committed under `tests/fuzz/<engine>/`.
- **Oracles** (expected values from outside the code under test):
  - Python `h11`, `h2`, `hpack`;
  - the RFC test vectors (HPACK RFC 7541 C, QPACK);
  - `h2spec`, the Autobahn testsuite, `testssl.sh`, `quic-interop-runner`;
  - curl, `nghttp` and OpenSSL `s_client`.

  Expected results are never regenerated from CeeSafe.Web's own output. Changing one is a reviewed edit with its
  reason.
- **Reactors:** socket tests pass on each reactor of each CI host:
  - macOS: kqueue;
  - Linux: epoll, plus io_uring with `CEESAFE_REACTOR_URING=1`;
  - Windows: IOCP, plus WSAPoll with `CEESAFE_REACTOR_POLL=1`.

### 2.3 Fallback behaviour

Every optimised path has a correct fallback, and both are tested:

- vectored send → sequential writes;
- `sendFile` → read + write;
- io_uring → epoll;
- GSO → per-datagram sends.

A fallback is selected by capability detection, never by catching a crash. The test matrix forces each fallback
with an environment variable.

### 2.4 Benchmarks

- **When:** from M1 on, only after the correctness gates of §2.1–2.2 pass.
- **How to measure:**
  - load at a constant rate (`wrk2`), to avoid coordinated omission;
  - `h2load` for H1, H2 and H3; `oha` for TLS handshakes;
  - report P50, P95, P99 and P99.9, requests per second at a fixed P99 SLO, CPU, allocations, RSS, and bytes per
    idle connection (from `--explain-frames` and RSS).
- **Comparisons:** Axum, Actix Web, Hyper, Kestrel and h2o, in `benchmarks/servers/`, with pinned versions and
  release builds, on identical hardware and configuration.
- **Recording:** each run records the hardware, kernel, compiler and flags, ceesafec version, reactor, concurrency,
  payload, TLS settings and warm-up.
- **Regression gate:** no regression greater than 5 % against the previous CeeSafe.Web release on the same machine,
  unless the release notes justify it.

### 2.5 Evidence

- A gate result names the date, the command, the compiler and the result, recorded in the milestone's section of
  `docs/milestones/<M>.md`.
- Proposed tests are listed separately from tests that were run.
- Code is cited by `file:line`, and ADRs by section.

### 2.6 Release blockers

A release (plan §7) is blocked by any of the following:

- a failing gate of its milestones;
- a known request-smuggling, desync or flood weakness in a shipped protocol (including CVE-2023-44487 and
  CVE-2024-27316 classes for H2);
- an open sanitizer finding;
- a leak in a sweep;
- an untested fallback;
- a public API without documentation;
- a benchmark regression beyond §2.4 without justification;
- a minimum ceesafec that is not a released version (Web ADR 0006).

## 3. Consequences

- CI time grows with the matrix of hosts, reactors and sanitizers. Fuzzing runs nightly, not per change.
- `tools/run_tests.py` and the per-package sweep mode are M0 deliverables. The sweep mode may need CeeSafe's T1.

## 4. Questions for the owner (answered 2026-11-02: each as recommended)

1. Is 24 CPU-hours of fuzzing per changed engine per milestone acceptable? Recommended: yes, nightly. **Yes,
   nightly.**
2. Should the benchmark regression gate be 5 %, or looser until v1.0? Recommended: 5 %, on a dedicated machine.
   **5 %, on a dedicated machine.**

## 5. Amendment 1 (2026-11-02, ceesafec 0.8.0's `ceesafec test`)

CeeSafe ADR 0049 (released in 0.8.0) gives packages a test command. It replaces §2.1.3's runner. The gates are
unchanged:

1. **Tests are `@test` functions in `packages/<name>/tests/*.cee`**, part of their package, so they also reach its
   private items. They replace the executable packages under `tests/<kind>/<name>/` and their `expected.txt`. A check
   is a `Check` call: `Check.equalU64(expected, actual)` or `Check.isTrue(condition, "what")`. Its expected value is
   still written by hand from the test's own reasoning or an outside oracle (§2.2). `tests/probes/` stays as it is.
2. **`ceesafec test` runs each test as its own process.** With `CEESAFE_ALLOC_REPORT` on, a leak fails the test, and
   so does a test that runs past `--timeout`. `tools/run_tests.py` runs it in every package that has tests: natively
   and in the interpreter (§2.1.3's two runs; each must pass, as there is no output to compare). Its other modes:
   - `--sanitize`: `--cflag -fsanitize=address,undefined` (§2.1.4);
   - `--alloc-sweep` and `--cancel-sweep`: `ceesafec test --sweep alloc|cancel`, each test on its own (§2.2).

   The interpreter skips a test that calls C, which replaces the native-only marker.
3. **Descriptors back to the baseline** (§2.1.5): a test that opens sockets counts `/dev/fd` before and after its
   `Async.run` (`descriptorsBackTo`, `packages/web_transport/tests/support.cee`). The harness has no per-test hook for
   this.
4. **No more `native-xfail` or `native-env` markers.** They were for CeeSafe bugs B2 and B4, both fixed in 0.7.0. A
   CeeSafe bug that breaks a test keeps the test failing on a branch until a release fixes it (Web ADR 0006).
5. §3's M0 deliverable is now CeeSafe's (T1, T2).
