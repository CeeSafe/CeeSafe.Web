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
| `generic-impl-dyn-in-generic-bug.cee` | prints `7` three times since 0.9.2; 0.9.1 gave N0268 on `runDyn(this)` in a method of `Wrap<S>` | CeeSafe bug B9; `Server<S>.run` | 0007 |
| `async-dyn-uncalled-bug.cee` | builds and prints `1` since 0.9.2; before, the C did not compile (an undefined `dyn` call frame) | CeeSafe bug B10; `web_server`'s router tests | 0007 |
| `borrowed-box-dyn-call-bug.cee` | prints `7` since 0.9.2; before, the compiler panicked (c_body.cee:1741) | CeeSafe bug B11; `Server<S>`'s dispatch | 0007 |
| `generic-impl-this-dyn-bug.cee` | builds and prints `7` since 0.9.2; before, the C named `Wrap<S>` uninstantiated | CeeSafe bug B12; `impl<S> ConnectionHandler for Server<S>` | 0007 |
| `generic-dyn-box-flow-bug.cee` | prints `2` since 0.9.1; with 0.8.0 and 0.9.0, N0272 on `Generic<S>.add` storing a `Box<dyn Handler<S>>` (the non-generic twin was accepted) | CeeSafe bug B8, fixed in 0.9.1 | 0007 |
| `sse-race-exchange.cee` | prints `3`, `2`, `closed`, interpreted and native | one task races a `borrow mut` method of the exchange against a channel and a timer, then writes after the race | 0008 §1 |
| `dyn-send-type-argument-bug.cee` | checks and prints `1` since 0.9.4; before, N0103 on `Vector<Box<dyn Job + Send>>.create()` | CeeSafe bug B14; `web_client`'s held connection | 0009 |
| `dyn-send-async-frames-bug.cee` | builds and prints `3` since 0.9.4; before, the C defined the dyn-call frame twice | CeeSafe bug B15; blocks `web_client` natively | 0009 |
| `scope-droppable-error-bug.cee` | builds and prints `3` since 0.9.4; before, the C called the error's drop function before declaring it | CeeSafe bug B13; scopes in `web_sse`'s tests | 0008 |
| `ws-session-region.cee` | prints `2`, `3`, `1`, `closed` | a `Session<region r>` holding the exchange races a read against a queue and a timer through a top-level function (N0241 forbids arms over fields of one `this`) | 0010 |
| `ws-link-generic.cee` | prints `3070`, natively too | one session type over two links: a scoped one (`impl<region r> Link for Borrowed<r>`, usable through a bound, not as `dyn`, CeeSafe ADR 0054 §3.2) and an owned one; the session holds `borrow mut(r) L` and races `L`'s read in a generic top-level function | 0010 amendment 2 |
| `h2-stream-tasks.cee` | prints `11`, then `1`, natively too | a connection's streams as task-set children (CeeSafe ADR 0053) sharing the application through `Arc<Box<dyn App + Send>>`; one stream's reset cancels only its handler (a race against its reset channel) | 0011 |
| `sendfile-sigpipe-bug.cee` | prints `rounds 20, failed sends 20`, exit 0; native build only (Linux 0.19.1 and 0.21.0: nothing printed, exit 141, epoll and io_uring; macOS as expected) | CeeSafe bug B19: `sendFileAll` after a reset raises SIGPIPE on Linux | 0013 |

## Runs

| Date | Compiler | Result |
|---|---|---|
| 2026-11-02 | `~/dev/CeeSafe/out/ceesafec` 0.6.0-dev (`5fa4316`) | all as expected, interpreter; `transport-dyn`, `handler-hooks` and `handler-boxed` also built natively (clang, macOS arm64), same output |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.3.2 | `check`: all as expected |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.6.0 (release) | all as expected; the two new probes as listed (the bug reproduces) |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.6.0 (release) | `drain-broadcast` prints `1005` (interpreter and native); `wake-waiters-livelock-bug` reproduces B4 (native, 20/20 hang; 10 sockets 10/10; poll reactor 0/10) |

| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.7.0 (release) | B1–B4 probes fixed: each gives its intended result in the interpreter and natively with `-Werror`; `wake-waiters-livelock-bug` 20/20 natively (kqueue). The `-fail` probes still fail with the same codes. New `async-deadline-void-bug` reproduces B6 |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.8.0 (release) | `async-deadline-void-bug` prints `ok` (interpreter, and natively with `-Werror`): B6 fixed |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.9.2 (release) | B9–B12 probes fixed: each prints its expected value, interpreted and native. New: `sse-race-exchange` as expected; `scope-droppable-error-bug` reproduces B13 (interpreter prints `3`, native build fails with 3 C errors) |
| 2026-11-03 | `~/.ceesafe/bin/ceesafec` 0.9.4 (release) | B13, B14 and B15 probes fixed: each prints its expected value, interpreted and native |
| 2026-11-08 | 0.19.1 and 0.21.0 (releases, linux-x64, SHA256 checked) on Debian 12 (Linux 6.1, x86-64); 0.19.1 on macOS arm64 | `sendfile-sigpipe-bug` reproduces B19 on Linux (exit 141, epoll and io_uring); macOS prints the expected line |

`impl-generic-fail.cee` does not parse, so `ceesafec fmt --check` reports it; the other probes are formatted.
