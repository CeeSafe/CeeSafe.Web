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
| M8 reverse proxy | **0.11.0** | U20 (a type that holds loans as a `borrow mut dyn I`, CeeSafe ADR 0064 §3.2) for the request-body relay; ADR 0064 §1's generic methods for `ReverseProxy.route<S>()` | gate passed, on main (#3, 2026-11-06); released with M9 as v0.4 (`docs/milestones/M8.md`) |
| M9 low-level I/O | **0.19.1** | `Vector.resize` (ADR 0071, 0.15.0) for `OwnedBuffer.create`; U7 sendfile (ADR 0051); `readAt`/`length` (ADR 0075) for TLS and HTTP/2 file bodies; U22 for `web_static` | in progress (Web ADR 0013) |
| M10 HTTP/3 | 0.9.0 | U6 UDP (ADR 0050) | released |

## Current machine (2026-11-06)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | 0.20.0 (release, osx-arm64; found 2026-11-06) | checks every manifest; not yet tested |
| `~/.ceesafe/bin/ceesafec-0.19.1` | 0.19.1 (release, tag `v0.19.1` = CeeSafe `619145e`, osx-arm64) | `File.readAt`, `length` (CeeSafe ADR 0075), for M9 stage 3 |
| `~/.ceesafe/bin/ceesafec-0.18.2` | 0.18.2 (release, tag `v0.18.2` = CeeSafe `932bb1c`, osx-arm64) | C backend only |
| `~/.ceesafe/bin/ceesafec-0.18.1` | 0.18.1 (release, osx-arm64; `SHA256SUMS` checked, 2026-11-05) | B18 fixed |
| `~/.ceesafe/bin/ceesafec-0.18.0` | 0.18.0 (release, osx-arm64) | B18's repro only: it does not check CeeSafe.Web |
| `~/.ceesafe/bin/ceesafec-0.17.0` | 0.17.0 (release, osx-arm64) | the last release that builds M8 unchanged |
| `~/.ceesafe/bin/ceesafec-0.11.0` | **0.11.0** (release, tag `v0.11.0` = CeeSafe `45dde8d`; osx-arm64; the archive matches the release's `SHA256SUMS` and the binary is byte-identical to the archive's, checked 2026-11-04) | the toolchain for M8; `ceesafec test` runs the tests and both sweeps |
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
| OpenSSL | ≥ 3.0 (M4), ≥ 3.5 (M10) | `web_tls`, QUIC crypto |
| Python | 3.10+ | `tools/`, oracles (`h11`, `h2`, `hpack`) |
