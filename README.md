# CeeSafe.Web

A networking and web framework written in [CeeSafe](https://ceesafe.github.io/CeeSafe/): HTTP/1.1, streaming and SSE,
TLS, an HTTP client, WebSockets, HTTP/2, a reverse proxy and HTTP/3. It aims for bounded memory, predictable
backpressure and standards compliance.

**Status:** M0 (contracts and ADRs). The packages exist but are empty; nothing is usable yet.

- Plan: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Decisions: [`docs/decisions/`](docs/decisions/README.md)
- Work needed in CeeSafe itself: [`docs/ceesafe-upstream-work.md`](docs/ceesafe-upstream-work.md)
- Toolchain: [`docs/toolchain.md`](docs/toolchain.md)

## Packages

| Package | Milestone | Contents |
|---|---|---|
| `web_core` | M0 | buffers, `Deadline`, `NetError`, the `Transport` interface, `ConnectionContext` |
| `web_transport` | M0–M1 | TCP and mock transports, the acceptor interfaces, the listener with admission and drain |
| `web_http` | M2 | protocol-neutral HTTP: requests, bodies, responses, handlers, middleware |
| `web_http1` | M2 | the HTTP/1.1 engine and driver |
| `web_sse` | M3 | Server-Sent Events |
| `web_tls` | M4 | TLS over OpenSSL |
| `web_client` | M5 | the HTTP client |
| `web_ws` | M6 | WebSockets |
| `web_http2` | M7 | HTTP/2 and HPACK |
| `web_proxy` | M8 | the reverse proxy |
| `web_http3` | M10 | HTTP/3 and QPACK over QUIC |
| `web_server` | M1–M3 | server, router, middleware pipeline |

Allowed dependencies: Web ADR 0004 §3.5, checked by `python3 tools/check_layers.py`.

## Working on it

```sh
python3 tools/check_layers.py
cd packages/web_core && ceesafec fetch && ceesafec check
```

See [`AGENTS.md`](AGENTS.md) for the project's rules.
