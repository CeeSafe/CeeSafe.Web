# Toolchain

Policy (implementation plan §11.6, Web ADR 0006): **main builds with released ceesafec only.** Each milestone records
the minimum release it needs. CI runs the released binary; a separate, non-blocking job builds against CeeSafe main.
While a U item is being developed in CeeSafe, `~/dev/CeeSafe/out/ceesafec` may be used locally on a branch, but code
that needs an unreleased compiler is not merged to main.

## Minimum ceesafec per milestone

| Milestone | Minimum | Needs | Status |
|---|---|---|---|
| M0 contracts, engines, MockTransport | **0.5.1** | async interfaces, `dyn` async methods (ADR 0036), regions, completion I/O (ADR 0035) | released |
| M1 transport lifecycle | **0.7.0** | B2 fixed; U1 addresses, IPv6, options and U2 error kinds (ADR 0044); U3 `IoParts` (ADR 0045); U4 `Async.deadline` (ADR 0046); B4 fixed; T1 for the cancel sweep. B5 for the overload gate | merged to main (`docs/milestones/M1.md`) |
| M2 HTTP/1.1 fast path | 0.7.0 | U3 vectored send | released |
| M4 TLS | 0.7.x | U8 manifest `links` (OpenSSL) | upstream, not started |
| M5 client | 0.7.x | U5 DNS | upstream, not started |
| M9 sendfile | later | U7 | upstream, not started |
| M10 HTTP/3 | later | U6 UDP | upstream, not started |

## Current machine (2026-11-02)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | **0.7.0** (release, tag `v0.7.0` = CeeSafe `b2bef17`) | the toolchain for main |
| `~/dev/CeeSafe/tools/cancel_sweep.py` | same as at `v0.7.0` | `tools/run_tests.py --cancel-sweep` (the release does not install it) |

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
