# Toolchain

Policy (implementation plan §11.6, Web ADR 0006): **main builds with released ceesafec only.** Each milestone records
the minimum release it needs. CI runs the released binary; a separate, non-blocking job builds against CeeSafe main.
While a U item is being developed in CeeSafe, `~/dev/CeeSafe/out/ceesafec` may be used locally on a branch, but code
that needs an unreleased compiler is not merged to main.

## Minimum ceesafec per milestone

| Milestone | Minimum | Needs | Status |
|---|---|---|---|
| M0 contracts, engines, MockTransport | **0.5.1** | async interfaces, `dyn` async methods (ADR 0036), regions, completion I/O (ADR 0035) | released |
| M1 transport lifecycle | 0.6.0 (expected) | U1 socket options/IPv6, U2 error kinds, U4 `Async.timeout` | upstream, not started |
| M2 HTTP/1.1 fast path | 0.6.0 (expected) | U3 vectored send | upstream, not started |
| M4 TLS | 0.6.x | U8 manifest `links` (OpenSSL) | upstream, not started |
| M5 client | 0.6.x | U5 DNS | upstream, not started |
| M9 sendfile | 0.7.0 | U7 | upstream, not started |
| M10 HTTP/3 | 0.7.0 | U6 UDP | upstream, not started |

## Current machine (2026-11-02)

| Binary | Version | Use |
|---|---|---|
| `~/.ceesafe/bin/ceesafec` (on `PATH`) | **0.3.2** | too old for the policy: **update to the 0.5.1 release** |
| `~/dev/CeeSafe/out/ceesafec` | 0.6.0-dev (`5fa4316`) | local checks only |

The M0 package manifests and `tests/probes/` were checked with both binaries on 2026-11-02 (`ceesafec fetch`, `ceesafec
check` in every package; all clean).

## External tools (from M2/M4 on)

| Tool | Version | For |
|---|---|---|
| C compiler | clang (macOS), GCC 12 (Debian 12), clang + `cl` (Windows 11) | native builds, sanitizers |
| OpenSSL | ≥ 3.0 (M4), ≥ 3.5 (M10) | `web_tls`, QUIC crypto |
| Python | 3.10+ | `tools/`, oracles (`h11`, `h2`, `hpack`) |
