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

## Runs

| Date | Compiler | Result |
|---|---|---|
| 2026-11-02 | `~/dev/CeeSafe/out/ceesafec` 0.6.0-dev (`5fa4316`) | all as expected, interpreter; `transport-dyn`, `handler-hooks` and `handler-boxed` also built natively (clang, macOS arm64), same output |
| 2026-11-02 | `~/.ceesafe/bin/ceesafec` 0.3.2 | `check`: all as expected |

`impl-generic-fail.cee` does not parse, so `ceesafec fmt --check` reports it; the other probes are formatted.
