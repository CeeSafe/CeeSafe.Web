# CeeSafe.Web decisions

Web ADRs are numbered separately from CeeSafe's own ADRs. Cite them as "Web ADR 0003 §3.2" and CeeSafe's as "CeeSafe
ADR 0035". Each ADR states its context, options, decision, consequences, tests and the questions for the owner. An
accepted ADR is changed only by an amendment, recorded in it.

| ADR | Title | Status | Milestones |
|---|---|---|---|
| [0001](0001-transport-ownership.md) | Transport ownership: buffers, sockets, cancellation and async operations | Accepted 2026-11-02 | M0–M1 |
| [0002](0002-tls-provider.md) | TLS provider: OpenSSL behind a sans-I/O shim | Accepted 2026-11-02 | M4, M5, M10 |
| [0003](0003-request-lifetime.md) | Request lifetime: headers and body across `await` and streaming | Accepted 2026-11-02 | M2–M3 |
| [0004](0004-protocol-boundaries.md) | Protocol boundaries: sans-I/O engines, shared semantics, package layering | Accepted 2026-11-02 | all |
| [0005](0005-quality-gates.md) | Quality gates: conformance, faults, fallbacks, benchmarks, release blockers | Accepted 2026-11-02 | all |
| [0006](0006-upstream-dependencies-and-toolchain.md) | Upstream dependencies and toolchain | Accepted 2026-11-02 | all |
| [0007](0007-handler-model.md) | Handler model: handlers, middleware, routing and application state | Accepted 2026-11-02 (§6.2 pending M2's spike) | M2 on |
| [0008](0008-streaming-and-sse.md) | Streaming responses and Server-Sent Events | Accepted 2026-11-02 | M3 on |
| [0009](0009-http-client.md) | The HTTP client: engine, pool, Happy Eyeballs, redirects, retries | Accepted 2026-11-02 | M5 on |
| [0010](0010-websockets.md) | WebSockets: the engine, the upgrade, the session, permessage-deflate, the client | Accepted 2026-11-03 | M6 |
| [0011](0011-http2.md) | HTTP/2: HPACK, frames, streams as tasks, flow control, push, the flood guards | Accepted 2026-11-03 | M7 |
| [0012](0012-reverse-proxy.md) | The reverse proxy, and what M7 left open | Accepted 2026-11-03 | M8 |
| [0013](0013-low-level-io.md) | Low-level I/O: profiling, buffers, vectored writes, sending files, static files | Accepted 2026-11-06 | M9 |
| [0014](0014-quic-and-http3.md) | HTTP/3 and QUIC: ngtcp2 and nghttp3 behind a sans-I/O shim | Accepted 2026-11-09 | M10 |
| [0015](0015-v1-hardening.md) | v1.0 hardening: the audit, the fuzz farm, the API freeze, portability, the benchmark report | Accepted 2026-11-11 | v1.0 |

The language facts the ADRs rely on are checked by the programs in `tests/probes/` (results in its README).

## Answers (the owner, 2026-11-02: every recommendation accepted)

| ADR | Question | Answer |
|---|---|---|
| 0001 §6.1 | Is the `dyn Transport` edge acceptable, at one indirect call per I/O operation? | yes |
| 0001 §6.2 | Should a poisoned transport close itself, or only report `Closed`? | report only; the driver decides |
| 0002 §6.1 | Route OpenSSL's allocations through CeeSafe's allocator in test builds? | yes |
| 0002 §6.2 | OpenSSL ≥ 3.5 from M4 instead of M10? | no: 3.0 LTS for M4, 3.5 from M10 |
| 0003 §6.1 | Fixed inline header list (64), with 431 past it? | yes |
| 0003 §6.2 | Automatic owned requests, or explicit `toOwned`? | explicit |
| 0004 §6.1 | Should `web_http3` use `web_tls`, or its own OpenSSL shim? | `web_tls` |
| 0005 §4.1 | 24 CPU-hours of fuzzing per changed engine per milestone? | yes, nightly |
| 0005 §4.2 | A 5 % benchmark regression gate? | yes, on a dedicated machine |
| 0006 §4.1 | List U10 (generic impls) with CeeSafe now? | as wanted, not scheduled |
| 0007 §6.1 | Hook middleware by default, wrapping as an opt-in? | yes |
| 0007 §6.2 | Application-declared request extensions, or a fixed set? | M2's spike decides; recorded as an amendment |

## Answers (the owner, 2026-11-03)

| ADR | Question | Answer |
|---|---|---|
| 0010 §6.1 | Where does Autobahn run? | Docker on this Mac |
| 0010 §6.2 | permessage-deflate in pure CeeSafe, or deferred? | pure CeeSafe, in M6 |
| 0010 §6.3 | A WebSocket client too? | yes, in `web_client` |
| 0010 §6.4 | Which origins by default? | the same origin, with an allow-list |
| 0011 §6.1 | Which ways in to HTTP/2? | TLS ALPN `h2` and `h2c` by prior knowledge; no Upgrade |
| 0011 §6.2 | HTTP/2 in the client too? | not in M7 |
| 0011 §6.3 | Stream priority? | ignored; streams served fairly |
| 0011 §6.4 | Server push? | supported |
| 0012 §6.1 | Full duplex for requests in M8? | no: requests relay the body, then the response; tunnels are full duplex |
| 0012 §6.2 | HTTP/2 to upstreams in M8? | yes, through the client's HTTP/2 |
| 0012 §6.3 | Consistent hashing? | rendezvous |
| 0012 §6.4 | An untrusted source on a PROXY-protocol listener? | closed |
| 0012 §6.5 | Forwarding fields by default? | both `Forwarded` and `X-Forwarded-*` |
| 0012 §6.6 | A connect timeout? | 504 |
| 0012 (M8, h2spec §8.1.2.6) | Hold responses until a declared-length body ends? | no: early responses stay, and §8.1.2.6 #1 and #2 are a recorded h2spec timing case (`docs/milestones/M8.md`) |
| 0013 §6.1 | U22, an open file's modification time and kind, in CeeSafe? | yes |
| 0013 §6.2 | Static files in a new package? | yes: `web_static` |
| 0013 §6.3 | Symbolic links under the root? | followed, with lexical path rules |
| 0013 §6.4 | Several ranges in one request? | ignored: 200 with the whole file |
| 0013 §6.5 | The `ETag`'s form? | strong, from the size and the modification time in nanoseconds |
| 0013 §6.6 | io_uring as the Linux default? | measured, and recommended to CeeSafe; not overridden here |

## Answers (the owner, 2026-11-11)

| ADR | Question | Answer |
|---|---|---|
| 0015 §7.1 | The external audit? | a funded programme (OSTIF or the Sovereign Tech Fund) first; a paid firm if none accepts within three months |
| 0015 §7.2 | The fuzz farm? | a nightly GitHub Actions workflow, one job per harness |
| 0015 §7.3 | The API freeze? | committed `ceesafec doc` pages checked in CI; frozen at v1.0.0-rc.1 |
| 0015 §7.4 | FreeBSD? | supported if U23 lands in CeeSafe before v1.0, otherwise best effort |
| 0015 §7.5 | The benchmark machine? | alfaisal, the owner's server, once its services are moved off |
