# Design probes

Small programs that check what the M0 ADRs assume about CeeSafe. Re-run them with every new ceesafec. A changed
outcome means an ADR has to be revisited: for example, a `-fail` probe that starts passing means a v0 limit was lifted
(see `docs/ceesafe-upstream-work.md` U10).

```sh
cd tests/probes
for f in *.cee; do ceesafec check "$f" && ceesafec run --interp "$f"; done
```

| Probe | Expected | What it shows | ADR |
|---|---|---|---|
| `transport-dyn.cee` | prints `1007` | `Box<dyn Transport>` at the edge; TLS-style wrapper over a concrete `PlainStream` enum; a region-scoped `RequestBody` over `borrow mut dyn Transport` | 0001, 0003 |
| `request-lifetime.cee` | prints `1` | request views borrow the connection buffer across `await`; the buffer is reusable afterwards | 0003 |
| `request-lifetime-fail.cee` | N0240 | writing the buffer while a request borrows it is rejected | 0003 |
| `handler-hooks.cee` | prints `1015` | `dyn Handler<S>` with a concrete `RequestBody`; before/after middleware hooks without allocation | 0007 |
| `handler-boxed.cee` | prints `1015` | wrapping middleware through `dyn` needs `Async.boxed` (one frame allocation per layer) | 0007 |
| `impl-generic-fail.cee` | N0105 | `impl<T> I for S<T>` does not exist in v0 | 0001 |
| `impl-scoped-fail.cee` | N0267 | a type with a region parameter cannot implement an interface | 0003 |
| `method-region-fail.cee` | N0216 | a method cannot declare its own region; elided regions in method signatures work | 0003 |
| `slice-of-slices-fail.cee` | N0246 | no slices or arrays of slices, so vectored writes take `IoParts` | 0001 (amended) |
| `unused-socket-drop-bug.cee` | builds and prints `1` (fixed in 0.7.0; 0.6.0 failed in C, undefined `ceeD_14AsyncTcpStream`) | CeeSafe bug B2, which kept M1 off main until 0.7.0 | 0001 |
| `race-capture-const-warning.cee` | builds with `-Werror` and prints `7` (fixed in 0.7.0; 0.6.0 warned that const was discarded) | CeeSafe bug B3 | — |
| `drain-broadcast.cee` | prints `1005` | a closed `Channel` wakes every waiting task (a drain broadcast; `AsyncSignal` wakes one); a context with a region parameter lends it through a `dyn` method | 0001 amendment 2 |
| `wake-waiters-livelock-bug.cee` | prints `1`, natively too (fixed in 0.7.0; 0.6.0 hung on macOS/kqueue at 100 % CPU, 20 of 20 runs) | CeeSafe bug B4 | 0001 |
| `effectsof-async-bug.cee` | prints `70` (fixed in 0.7.0; 0.6.0 printed `0` in the interpreter and crashed natively) | CeeSafe bug B1 | 0001 §3.5 |
| `async-deadline-void-bug.cee` | prints `ok` (fixed in 0.8.0; 0.7.0's `ceesafec check` panicked, "index out of bounds at src/main.cee:687:41") | CeeSafe bug B6: `Async.deadline` over `Result<void, E>` | 0001 amendment 3 |
| `generic-dyn-box-flow-bug.cee` | **should** check and print `2`; N0272 on `Generic<S>.add` storing a `Box<dyn Handler<S>>` (the non-generic twin is accepted) | CeeSafe bug B7; blocks `web_server`'s `Router<S>` (branch `m2-server`) | 0007 |

## Runs

| Date | Compiler | Result |
|---|---|---|
| 2026-11-02 | `~/dev/CeeSafe/out/ceesafec` 0.6.0-dev (`5fa4316`) | all as expected, interpreter; `transport-dyn`, `handler-hooks` and `handler-boxed` also built natively (clang, macOS arm64), same output |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.3.2 | `check`: all as expected |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.6.0 (release) | all as expected; the two new probes as listed (the bug reproduces) |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.6.0 (release) | `drain-broadcast` prints `1005` (interpreter and native); `wake-waiters-livelock-bug` reproduces B4 (native, 20/20 hang; 10 sockets 10/10; poll reactor 0/10) |

| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.7.0 (release) | B1–B4 probes fixed: each gives its intended result in the interpreter and natively with `-Werror`; `wake-waiters-livelock-bug` 20/20 natively (kqueue). The `-fail` probes still fail with the same codes. New `async-deadline-void-bug` reproduces B6 |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.8.0 (release) | `async-deadline-void-bug` prints `ok` (interpreter, and natively with `-Werror`): B6 fixed |

`impl-generic-fail.cee` does not parse, so `ceesafec fmt --check` reports it; the other probes are formatted.
