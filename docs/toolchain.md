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
| M3 streaming and SSE | **0.9.2** | everything M2 needs; nothing new (U13 is wanted for `DropOldest`) | gate passed (`docs/milestones/M3.md`) |
| M4 TLS | 0.9.2 | U8 manifest `links` (OpenSSL, ADR 0047), released in 0.8.0; built and tested on 0.9.2 | done (`docs/milestones/M4.md`) |
| M5 client | 0.9.4 | U5 DNS (ADR 0048), released in 0.8.0; B15 (`dyn I + Send` async frames), fixed in 0.9.4 | done (`docs/milestones/M5.md`) |
| M9 sendfile | 0.9.0 | U7 (ADR 0051) | released |
| M10 HTTP/3 | 0.9.0 | U6 UDP (ADR 0050) | released |

## Current machine (2026-11-03)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | **0.9.4** (release, tag `v0.9.4` = CeeSafe `92776f7`) | the toolchain for main; `ceesafec test` runs the tests and both sweeps |
| `/root/cw-m5/ceesafec-0.9.4/ceesafec` on the Debian x86_64 host | **0.9.4** (release, linux-x64, SHA256 checked) | M5's Debian runs |
| `%USERPROFILE%\.ceesafe\bin\ceesafec.exe` on the Windows 11 ARM64 VM | **0.9.4** (release, win-arm64, SHA256 checked) | M4/M5's Windows runs |
| `/root/cw-fuzz/ceesafec-0.8.0/ceesafec`, `/root/cw-tls/ceesafec-0.9.2/ceesafec` on the Debian host | 0.8.0, 0.9.2 | the HTTP and TLS fuzz campaigns only |

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
