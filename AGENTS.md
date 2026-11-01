<!--CEESAFE START-->

# Using CeeSafe

This package is written in CeeSafe, a memory-safe systems programming language with C#/TypeScript-style syntax that
`ceesafec` compiles ahead of time to C. Ownership and borrowing are checked at compile time; there is no garbage
collector. CeeSafe is new: code that looks right from C#, TypeScript or Rust often is not CeeSafe. Check every edit with the
compiler, and read the guide for agents: https://vkuttyp.github.io/CeeSafe/guide/for-agents (the whole guide and
specification for a model's context: https://vkuttyp.github.io/CeeSafe/llms-full.txt).

## The package

- `ceesafe.toml` names the package, its kind (`executable` or `library`) and its dependencies; `ceesafe.lock` pins them.
- The sources are the `.cee` files under `src/`. A program starts at `fn main()`.
- `ceesafec build` writes to `out/`, which is not committed.

## Commands

Run them in the package's directory; without file arguments they use the package.

| Command | What it does |
|---|---|
| `ceesafec check` | parse, type-check and borrow-check; the fastest way to find errors |
| `ceesafec fix` | apply the fixes the compiler is sure of, then check again |
| `ceesafec explain N0249` | what a diagnostic code means |
| `ceesafec run --interp` | run the program in the interpreter |
| `ceesafec build` | compile through C to `out/<name>` |
| `ceesafec fmt` | format in the one canonical style; `ceesafec fmt --check` lists files that would change |
| `ceesafec fetch` | resolve the dependencies and write `ceesafe.lock` |

Add `--diagnostics-format=json` to `check`, `build`, `run` or `fix` for one JSON document with every diagnostic, its
position and its fixes.

## The MCP server

If you have the `ceesafe` MCP server (`ceesafec mcp`), use its tools: `ceesafe_check` after every edit, `ceesafe_fix` for the
fixes it offers, `ceesafe_explain` for a code, `ceesafe_spec_search` and `ceesafe_spec_section` for the rules, `ceesafe_run` to
run code in a sandbox. The specification is embedded in `ceesafec`, so its answers match the compiler.

## Before you finish

- [ ] `ceesafec check` reports no errors (run `ceesafec fix` first).
- [ ] `ceesafec fmt --check` lists no file.
- [ ] The program does what was asked: `ceesafec run --interp`, or `ceesafec build` and run it.

## Common mistakes

- There is no `~`: complement with `x ^ 0xFFFFFFFF`. There are no type aliases, tuples or `==` on arrays.
- Indexes are `usize`; `0..4` is a range of `i32`. Loop with `for (i in 0..values.length)`.
- `if` and `match` expressions take expressions, not blocks: `let s = if (x < 0) -1 else 1;`.
- A variable that changes is `var`; a `let` cannot be assigned. A `borrow mut` argument is written `mut x`.
- `as` converts only without loss; `truncate<u8>(x)` wraps.
- Bind a constant or a temporary to a local before slicing it or asking its `.length`.
- Pass a function or closure as a parameter and call the parameter; a field holding one cannot be called.
- A `StringView` inside an enum or struct needs a region parameter; for fixed messages, use an enum of reasons.
- A method cannot have the name of a field.

<!--CEESAFE END-->

# CeeSafe.Web project rules

CeeSafe.Web is a networking and web framework written in CeeSafe: a set of library packages under `packages/`, one
per layer. The section above, between the `CEESAFE` markers, is written by `ceesafec agents`; never edit it by hand.

## Governing documents (in order of authority)

1. `docs/implementation-plan.md`: scope, milestones, gates and the owner's decisions (§11).
2. `docs/decisions/*.md`: CeeSafe.Web's ADRs ("Web ADR 0001" …, numbered apart from CeeSafe's own ADRs).
3. `docs/ceesafe-upstream-work.md`: what has to land in CeeSafe itself (U1–U10) before the milestones that need it.
4. CeeSafe's guide and specification (https://vkuttyp.github.io/CeeSafe/; `~/dev/CeeSafe/spec/`).

## Rules

- **Layering.** A package depends only on what Web ADR 0004 §3 allows; `python3 tools/check_layers.py` checks it.
  Nothing below `web_tls` may reach it, so a program without TLS never links OpenSSL.
- **Engines are sans-I/O** (Web ADR 0004): no `await`, no timers, no sockets inside a protocol engine.
- **No workarounds for CeeSafe gaps.** A missing I/O primitive or language feature goes upstream
  (`docs/ceesafe-upstream-work.md`); never `unsafe` or private C instead. The only C is the OpenSSL shim and the
  vendored QUIC libraries, each behind the contract its ADR states.
- **Toolchain** (`docs/toolchain.md`): released ceesafec only on main; record the minimum per milestone.
- **Evidence.** Cite `file:line` and ADR sections. Keep tests that were run (date, command, result) apart from tests
  that are only proposed. Expected values come from oracles outside the code under test.

## Commands

```sh
python3 tools/check_layers.py                          # the dependency layering
(cd packages/<name> && ceesafec fetch && ceesafec check)   # one package; fetch after changing dependencies
(cd packages/<name> && ceesafec fmt --check)
```

## Layout

```text
packages/       one CeeSafe library package per layer (web_core … web_proxy)
docs/           implementation plan, ADRs (decisions/), toolchain, upstream work
tools/          check_layers.py; later the test runner and sweeps
tests/ examples/ benchmarks/ third_party/   (from M0's later tasks on)
```
