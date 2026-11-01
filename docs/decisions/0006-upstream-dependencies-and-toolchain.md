# Web ADR 0006 — Upstream dependencies and toolchain

- Status: **Accepted** by the owner, 2026-11-02 (the question of §4 as recommended). The owner had already decided
  on 2026-11-02 to do the upstream items in CeeSafe (implementation plan §11.3) and adopted the toolchain policy
  (§11.6).
- Scope: what CeeSafe.Web needs from CeeSafe, the rule against working around it, and which ceesafec each
  milestone requires.

## 1. Context

- A library package cannot add async I/O primitives: `Async.park`/`Async.waker` are library-only (N0345, CeeSafe
  `spec/type-system.md:3358`), and `AsyncOp` is private (`lib/std/async.cee:94`).
- Drafting M0 found two language limits besides (`tests/probes/`):
  - impls only for concrete, unscoped types (N0105/N0267);
  - no region parameters on methods (N0216).

  The M0 ADRs design around both (Web ADR 0001 §3.3, 0003 §3.3).
- `~/.ceesafe/bin/ceesafec` is 0.3.2. CeeSafe main is 0.6.0-dev; the last release is 0.5.1.

## 2. Decision

### 2.1 Upstream items

The work is specified in `docs/ceesafe-upstream-work.md`, which is the handoff to CeeSafe.

| Item | Content | Needed by |
|---|---|---|
| U1 | socket addresses, IPv6, listen options (backlog, reuse-port), stream options, peer/local address, `shutdownRead` | M1 |
| U2 | `IoError` network kinds | M1 |
| U3 | vectored send | M2 (fast path) |
| U4 | `Async.timeout` | M1 |
| U5 | DNS on a bounded worker pool | M5 |
| U6 | UDP with `recvmsg`/`sendmsg`, ECN, packet info, GSO | M10 |
| U7 | `sendFile` | M9 |
| U8 | manifest `links`, include and library dirs, per-target tables | M4 (merging) |
| U9 | std `HashMap` (optional) | scale of router and pools |
| U10 | generic impls and impls for scoped types (optional) | simplifies Web ADR 0001 §3.3 and 0003 §3.3; not blocking |
| T1 | the sweep tools on a package directory | M1 exit |

### 2.2 No workarounds

CeeSafe.Web never replaces a missing primitive with `unsafe`, private C, or declarations of runtime symbols that
`lib/std` owns. Until an item lands:

- the milestone's code that needs it waits, or uses the documented interim (the `--cflag` link flags for U8; a
  single `write` for `writeVectored`, Web ADR 0001 §3.2);
- the interim is listed in the milestone's record and removed when the item lands.

The only C in CeeSafe.Web is the OpenSSL shim (Web ADR 0002) and, from M10, the vendored QUIC libraries.

### 2.3 Toolchain policy

- **main uses released ceesafec only.** `docs/toolchain.md` keeps the minimum per milestone: 0.5.1 for M0, and the
  release with U1/U2/U4 (expected 0.6.0) for M1.
- CI's required job uses that release. A non-blocking job builds against CeeSafe main, to see breakage early.
- While a U item is in development, `~/dev/CeeSafe/out/ceesafec` may be used locally on a branch. Code that needs an
  unreleased compiler is not merged to main.
- `tests/probes/` is re-run with every new ceesafec (Web ADR 0005). A probe whose outcome changes is reported, and
  the ADR it supports is revisited.

## 3. Consequences

- The milestone schedule (plan §9) depends on CeeSafe's releases; ADR 0005's release gate checks the minimum
  version.
- **Immediate action:** update `~/.ceesafe/bin/ceesafec` from 0.3.2 to 0.5.1. The M0 manifests and probes were also
  checked with 0.3.2 and 0.6.0-dev (`docs/toolchain.md`).

## 4. Questions for the owner (answered 2026-11-02: as recommended)

1. Should U10 (generic impls) go on CeeSafe's list now, or only once the workarounds of Web ADR 0001/0003 hurt?
   Recommended: list it as wanted, not scheduled. **Listed as wanted, not scheduled** (`docs/ceesafe-upstream-work.md`
   U10).
