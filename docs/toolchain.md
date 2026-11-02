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
| M2 HTTP/1.1 fast path | 0.8.0 | U3 vectored send | released |
| M4 TLS | 0.8.0 | U8 manifest `links` (OpenSSL, ADR 0047) | released |
| M5 client | 0.8.0 | U5 DNS (ADR 0048) | released |
| M9 sendfile | later | U7 | upstream, not started |
| M10 HTTP/3 | later | U6 UDP | upstream, not started |

## Current machine (2026-11-02)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | **0.8.0** (release, tag `v0.8.0` = CeeSafe `2c32172`) | the toolchain for main; `ceesafec test` runs the tests and both sweeps |

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
