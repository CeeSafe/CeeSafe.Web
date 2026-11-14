# Toolchain

Policy (implementation plan §11.6, Web ADR 0006): **main builds with released ceesafec only.** Each milestone records
the minimum release it needs. CI runs the released binary; a separate, non-blocking job builds against CeeSafe main.
While a U item is being developed in CeeSafe, `~/dev/CeeSafe/out/ceesafec` may be used locally on a branch, but code
that needs an unreleased compiler is not merged to main.

## Minimum ceesafec per milestone

| Milestone | Minimum | Needs | Status |
|---|---|---|---|
| M0 contracts, engines, MockTransport | **0.5.1** | async interfaces, `dyn` async methods (ADR 0036), regions, completion I/O (ADR 0035) | released |
| M1 transport lifecycle | **0.8.0** | 0.7.0's B2/B4 fixes, U1–U4 (ADRs 0044–0046); 0.8.0's B5 fix (overload gate), B6 fix (`void` deadlines) and `ceesafec test` (ADR 0049, the tests) | on main (`docs/milestones/M1.md`) |
| M2 HTTP/1.1 server | **0.9.2** | U3 vectored send, `ceesafec test`; U12 wall clock (ADR 0055, `Date`); `web_server` needs U10 (0.9.0), the B8 fix (0.9.1) and the B9–B12 fixes (0.9.2) | on main (`docs/milestones/M2.md`) |
| M3 streaming and SSE | **0.10.0** | everything M2 needs; U13 (`Channel.tryRecv`, ADR 0060) for `DropOldest` and batched flushes, which 0.9.2's gate did without | gate passed (`docs/milestones/M3.md`) |
| M4 TLS | 0.9.2 | U8 manifest `links` (OpenSSL, ADR 0047), released in 0.8.0; built and tested on 0.9.2 | done (`docs/milestones/M4.md`) |
| M4 TLS, OpenSSL's allocations in the sweep | **0.10.1** | U14 (`CEE_ALLOC_FOREIGN`, ADR 0060) and U17 (`CEE_ALLOC_FOREIGN_INIT`, ADR 0061) | done (Web ADR 0002 amendment 5) |
| M5 client | 0.9.4 | U5 DNS (ADR 0048), released in 0.8.0; B15 (`dyn I + Send` async frames), fixed in 0.9.4; U15 (`HostNotFound`, 0.10.0) only for the message | done (`docs/milestones/M5.md`) |
| M6 WebSockets | **0.10.0** | nothing new for the server (0.9.4); the client needs U16 (`Random.fill`, ADR 0060) | done (`docs/milestones/M6.md`) |
| M7 HTTP/2 | **0.10.3** | B16 (a wake lost on kqueue past ~30 connections), fixed in 0.10.2; B17 (cancellation and the wake list quadratic in parked tasks, CeeSafe ADR 0062), fixed in 0.10.3 | gate passed, on main, released in v0.3.0 (`docs/milestones/M7.md`) |
| M8 reverse proxy | **0.11.0** | U20 (a type that holds loans as a `borrow mut dyn I`, CeeSafe ADR 0064 §3.2) for the request-body relay; ADR 0064 §1's generic methods for `ReverseProxy.route<S>()` | gate passed, on main (#3, 2026-11-06); released with M9 in v0.4.0 (`docs/milestones/M8.md`) |
| M9 low-level I/O | **0.22.0** | `Vector.resize` (ADR 0071, 0.15.0) for `OwnedBuffer.create`; U7 sendfile (ADR 0051); `readAt`/`length` (ADR 0075) for TLS and HTTP/2 file bodies; B19 (`sendFile` to a reset connection raised SIGPIPE on Linux) and U22 (`File.openRegular`, `f.modified()`, `f.kind()`) for `web_static`, both CeeSafe ADR 0078 | done; main's minimum, released in v0.4.0 (`docs/milestones/M9.md`) |
| M10 HTTP/3 | **0.22.0** | U6 UDP (ADR 0050, 0.9.0); nothing newer than M9's minimum | done; main's minimum, released in v0.5.0 (`docs/milestones/M10.md`) |
| After v0.5: `Share` | **0.27.0** | CeeSafe ADR 0084 (0.25.0): `Share` is no longer every `Send` type, and `Arc<T>` needs `T: Share`. What a server or proxy shares across requests is bounded `Send + Share`: `Server<S: Send + Share>`, `Box<dyn Handler<S> + Send + Share>`, `Box<dyn Middleware<S> + Send + Share>`, `Box<dyn Application + Send + Share>`, `Box<dyn QuicApplication + Send + Share>`, and the client's connector, resolver and dialer. `dyn … + Share` exists from 0.25.0, so 0.22.0 cannot build it; 0.27.0 is the newest release (2026-11-10) | main's minimum from 2026-11-10 to 2026-11-14 (#8; implementation plan §11.13) |
| After v0.5: `AsyncError.Closed` | **0.44.0** | CeeSafe ADR 0128 (0.43.0): `AsyncError` gained `Closed`, and `NetErrors.fromAsync` (`packages/web_core/src/errors.cee`) matched it without a `_` arm, so 0.43.0 and later did not check `web_core` or anything that depends on it. The new arm does not compile before 0.43.0. 0.43.0 was not tested; 0.44.0, the newest release (2026-11-14), was | main's minimum from 2026-11-14 (#21; implementation plan §11.15) |

## Current machine (2026-11-06)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | 0.20.0 (release, osx-arm64; found 2026-11-06) | checks every manifest; not yet tested |
| `~/.ceesafe/bin/ceesafec-0.44.0` | **0.44.0** (release, tag `v0.44.0`, osx-arm64; the archive matches the release's `SHA256SUMS` and the binary is byte-identical to the archive's, 2026-11-14; no signature checked) | main's minimum from 2026-11-14 (the `AsyncError.Closed` change, CeeSafe ADR 0128) |
| `~/.ceesafe/bin/ceesafec-0.27.0` | 0.27.0 (release, tag `v0.27.0` = CeeSafe `405dc50`, osx-arm64; the archive matches the release's `SHA256SUMS`, 2026-11-10; no signature checked) | the `Share` change (CeeSafe ADR 0084), branch `toolchain-0.27` |
| `~/.ceesafe/bin/ceesafec-0.22.0` | **0.22.0** (release, tag `v0.22.0` = CeeSafe `f225f7a`, osx-arm64; the archive matches the release's `SHA256SUMS`, signature checked, 2026-11-08) | M9's minimum: B19 and U22 (CeeSafe ADR 0078) |
| `~/.ceesafe/bin/ceesafec-0.19.1` | 0.19.1 (release, tag `v0.19.1` = CeeSafe `619145e`, osx-arm64) | `File.readAt`, `length` (CeeSafe ADR 0075), for M9 stage 3 |
| `~/.ceesafe/bin/ceesafec-0.18.2` | 0.18.2 (release, tag `v0.18.2` = CeeSafe `932bb1c`, osx-arm64) | C backend only |
| `~/.ceesafe/bin/ceesafec-0.18.1` | 0.18.1 (release, osx-arm64; `SHA256SUMS` checked, 2026-11-05) | B18 fixed |
| `~/.ceesafe/bin/ceesafec-0.18.0` | 0.18.0 (release, osx-arm64) | B18's repro only: it does not check CeeSafe.Web |
| `~/.ceesafe/bin/ceesafec-0.17.0` | 0.17.0 (release, osx-arm64) | the last release that builds M8 unchanged |
| `~/.ceesafe/bin/ceesafec-0.11.0` | **0.11.0** (release, tag `v0.11.0` = CeeSafe `45dde8d`; osx-arm64; the archive matches the release's `SHA256SUMS` and the binary is byte-identical to the archive's, checked 2026-11-04) | the toolchain for M8; `ceesafec test` runs the tests and both sweeps |
| `C:\cw-m9\bin\ceesafec.exe` on the Windows 11 ARM64 VM | **0.22.0** (release, win-arm64; the archive matches the release's `SHA256SUMS`, 2026-11-08) | M9's Windows run |
| `/root/cw-m9/ceesafec-0.22.0/ceesafec` on the Debian x86_64 host (hanan) | **0.22.0** (release, linux-x64; the archive matches the release's `SHA256SUMS`, 2026-11-08) | M9 from 0.22.0 |
| `/root/cw-m9/ceesafec-0.19.1/ceesafec` and `/root/cw-m9/ceesafec-0.21.0/ceesafec` on the Debian x86_64 host (hanan) | 0.19.1 and 0.21.0 (releases, linux-x64; archives checked against each release's `SHA256SUMS`, 2026-11-08) | M9 stage 5's runs (0.19.1); B19's probe (both) |
| `/root/cw-m8/ceesafec-0.11.0/ceesafec` on the Debian x86_64 host; `C:\cw-m8\bin\ceesafec.exe` on the Windows 11 ARM64 VM | **0.11.0** (release, linux-x64 and win-arm64) | M8's Debian and Windows runs |
| `~/.ceesafe/bin/ceesafec-0.10.3` | 0.10.3 (release, tag `v0.10.3` = CeeSafe `94a4a92`) | M7 and v0.3.x; main's minimum until M8 merged |
| `/root/cw-m5/ceesafec-0.9.4/ceesafec` on the Debian x86_64 host | **0.9.4** (release, linux-x64, SHA256 checked) | M5's Debian runs |
| `%USERPROFILE%\.ceesafe\bin\ceesafec.exe` on the Windows 11 ARM64 VM | **0.9.4** (release, win-arm64, SHA256 checked) | M4/M5's Windows runs |
| `/root/cw-fuzz/ceesafec-0.8.0/ceesafec`, `/root/cw-tls/ceesafec-0.9.2/ceesafec` on the Debian host | 0.8.0, 0.9.2 | the HTTP and TLS fuzz campaigns only |

0.11.0 (U20, CeeSafe ADR 0064) is M8's minimum: the proxy streams a request body from the client's `Exchange` to the
upstream through `Client.send`'s `borrow mut dyn BodySource`, and its body source holds the exchange's loan. With M8
merged it becomes main's minimum, and CI pins it. On `m8-proxy` at `754d2bd`, every package's tests pass with 0.11.0 in every mode: 43
runs, web_tls's interpreter run skipped (`python3 tools/run_tests.py --sanitize --alloc-sweep --cancel-sweep`,
2026-11-04, macOS arm64). No source change was needed.

0.17.0 (CeeSafe ADR 0073: temporaries, two-phase `mut` arguments, block values, and the N0400 warning for a string
that looks interpolated) only lets more code compile. On `m8-stage3` at `92f23fb`, every package, example and fuzzer
checks with no diagnostic (N0400 fires nowhere), `fmt --check` lists no file, and every package's tests pass in every
mode: 43 runs, web_tls's interpreter run skipped (`python3 tools/run_tests.py --sanitize --alloc-sweep
--cancel-sweep`, 2026-11-05, macOS arm64). Nothing needs it, so M8's minimum stays 0.11.0.

0.18.0 (CeeSafe ADR 0074) does not check CeeSafe.Web: `web_http`'s `Request.headers` (`-> borrow HeaderList<r>` from
`borrow this`) is N0245, and so is everything that depends on `web_http`. The signature is sound; 0.18.0's spec had
no way to write it (`docs/ceesafe-upstream-work.md`, B18; repro `tests/probes/deep-result-borrow-bug.cee`). CI was
unaffected: it pins `CEESAFE_VERSION`.

0.18.1 fixes B18 (CeeSafe ADR 0074 §8). CeeSafe.Web needed no change for it: at `fd5b387` every manifest checks with no
diagnostic, `fmt --check` is clean, and `tools/run_tests.py` with the sanitizers and both sweeps passes 43 runs
(2026-11-05, macOS arm64). Nothing needs 0.18.1, so M8's minimum stays 0.11.0.

0.18.2 changes only the C backend: temporaries are kept out of the debug info (`CEE_TEMP`). With M8's stage-4 tree
every package's tests pass in every mode (`python3 tools/run_tests.py --ceesafec ceesafec-0.18.2 --sanitize
--alloc-sweep --cancel-sweep`: 43 runs, web_tls's interpreter run skipped), and every example and fuzzer checks
(2026-11-06, macOS arm64). M8's minimum stays 0.11.0.

0.19.1 (CeeSafe ADR 0075) adds positioned reads (`File.readAt`, `AsyncFiles.readAt`), `length`, `Clock.monotonicNanos`
and `UnixTime`. With M9's tree at `258adf8` every manifest checks with no diagnostic, `fmt --check` is clean, and
`python3 tools/run_tests.py --ceesafec ceesafec-0.19.1 --sanitize --alloc-sweep --cancel-sweep` passes 43 runs, web_tls's
interpreter run skipped (2026-11-06, macOS arm64). Still open upstream: U18, U19 (`tests/probes/box-send-dyn-lend.cee`
still N0205), U21, and U22 (an open file's modification time and kind), which `web_static` waits for. M9's minimum
is 0.19.1 from stage 2 (the owner's choice, 2026-11-06): `OwnedBuffer.create` fills with `Vector.resize`, and
stage 3 uses `readAt`. CI pins v0.19.1 on `m9-io`. 0.20.0 checks every manifest (27, the benchmark included) with no
diagnostic; its tests have not been run.

0.43.0 (CeeSafe ADR 0128 §2.1) adds a fourth variant to `AsyncError`, `Closed`: "the other side of this channel, or
this endpoint, was closed, so the operation can never succeed". ADR 0128 §5 says the change breaks a `match` over
`AsyncError` that has no `_` arm. `NetErrors.fromAsync` in `web_core` was such a match, so with 0.43.0 and 0.44.0
every package, example and benchmark that depends on `web_core` stopped checking (N0256 at
`packages/web_core/src/errors.cee:40`, then N0364 in each dependent). The fix is one arm, `AsyncError.Closed =>
NetError.Closed`, which is what `NetError.Closed` already meant ("the stream has ended"); 0.27.0 rejects it
(N0200: no variant named `Closed`). No other `match` in the repository needed a change: each other one has a `_`
arm. ADR 0128 §5 also says that no library operation returned `Closed` before 0.43.0, and that only
`SendError.reason` produces it now, so no existing program sees a different value at run time.
Run 2026-11-14 on macOS arm64, branch `toolchain-0.44` (the fix on main at `dab9a35`, then at `fe3ae27`):
- every manifest (33) checks with no diagnostic, `fmt --check` lists no file, `tools/check_layers.py` passes, and
  `tools/api_check.py` matches after `--write` (the doc comment of `NetErrors.fromAsync` only);
- `python3 tools/run_tests.py --ceesafec ceesafec-0.44.0 --sanitize --alloc-sweep --cancel-sweep`, on `dab9a35`
  plus the fix: 53 runs passed, 0 failed, 3 skipped (web_tls, web_quic and web_http3 in the interpreter: they
  call C). No sweep failure in any package.
- On `fe3ae27` (pull request #20 merged: `quic_shim.c` and one new web_quic test), web_quic again with
  `--no-interp --sanitize --alloc-sweep --cancel-sweep`: 23 passed (0 failures
  in 3,830 allocation-sweep and 1,169 cancellation-sweep runs).
- `web_core`'s `errors_from_async` now checks that `AsyncError.Closed` becomes `NetError.Closed`; the expected value
  is ADR 0128 §2.1's definition, not this code's.
- Before the fix, CI's non-blocking `ceesafe-main` job failed with the same N0256 on pull request #20 and on main
  after #18 and #19 merged; the blocking jobs, pinned to v0.27.0, passed.

0.22.0 (CeeSafe ADR 0078, written from this project's B19 and U22) is M9's minimum from 2026-11-08 (the owner's
choice). On Linux, `sendFile` to a reset connection is an error, not SIGPIPE, and an open file's time and kind and
`File.openRegular` are there for `web_static`. CI pins v0.22.0. Run 2026-11-08 on macOS arm64, M9's tree at `1bcf1fc`
plus these documents:
- every manifest (28) checks with no diagnostic, `fmt --check` is clean, and `tools/check_layers.py` passes;
- `python3 tools/run_tests.py --ceesafec ceesafec-0.22.0 --sanitize --alloc-sweep --cancel-sweep`: 42 runs passed, 1
  skipped (web_tls in the interpreter) and 1 failed, web_ws in the interpreter. Its `sha1_rfc3174_vectors` (the 1M-byte
  vector) passed its 120 s limit while the machine's load average was about 42 from the suite's parallel builds.
- Re-run alone, `ceesafec-0.22.0 test --interp --timeout 120` in web_ws passed 26 of 26.
- That test takes 63.6 s on 0.22.0 and 63.6 s on 0.19.1, so the interpreter did not get slower.

0.10.3 (B16, B17, CeeSafe ADR 0062) is M7's minimum: the HTTP/2 server needs both fixes past ~30 connections and
~2 000 streams. With M7 merged it is main's minimum, and CI pins it.

0.10.1 (U17, CeeSafe ADR 0061) is `web_tls`'s minimum: its shim uses `CEE_ALLOC_FOREIGN_INIT`. So it is main's minimum.
CI installs the latest release from `https://ceesafe.github.io/CeeSafe/install.sh` (the CeeSafe organisation's
addresses since 2026-11-03).

0.10.0 (U13-U16, CeeSafe ADR 0060) is M6's minimum, for the WebSocket client, and M3's, for `DropOldest`. It needed
no source change in CeeSafe.Web: no `match` over `IoError` lacked a `_`.

0.9.4 (B13, B14, B15 and T3 fixed) is M5's minimum: `web_client` needs B15's fix. It needed no change in CeeSafe.Web; on
main every package checks and is formatted, and 27 runs pass (2026-11-03, macOS arm64). 0.9.2, 0.9.1 and 0.9.0 needed
none either (`docs/ceesafe-upstream-work.md`, "Status at 0.9.0").

0.7.0 breaks source in two ways that touched CeeSafe.Web: lib/std's new `IoParts` collided with `web_core`'s (Web ADR
0001 amendment 3.1), and `IoError` gained eight variants (a `match` without `_` stops compiling; `web_core`'s have
one).

Runs on 2026-11-02 with 0.7.0 (macOS arm64): see `docs/milestones/M1.md`, "Runs". Earlier, with 0.6.0: M0's five
tests passed (interpreter, native, sanitizers, 371 allocation-failure points).

## External tools (from M2/M4 on)

| Tool | Version | For |
|---|---|---|
| C compiler | clang (macOS), GCC 12 (Debian 12), clang + `cl` (Windows 11) | native builds, sanitizers |
| OpenSSL | ≥ 3.0 (M4), ≥ 3.5 (M10; 3.5.6 or later advised: 3.5.0–3.5.5 can corrupt the session cache when an allocation fails, openssl/openssl#28632, `docs/milestones/M10.md`) | `web_tls`, QUIC crypto |
| Python | 3.10+ | `tools/`, oracles (`h11`, `h2`, `hpack`) |
