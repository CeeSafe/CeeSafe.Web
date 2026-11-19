# The comparison servers

Web ADR 0015 §6 and implementation plan §8: the servers CeeSafe.Web's benchmark report compares with, each answering
the same routes with the same bytes. `benchmarks/http/run.py --server <name>` measures one; without `--server` it
measures CeeSafe.Web's own server (`benchmarks/http`), which answers the same routes.

## The contract

`conform.py` states it and checks it. `run.py` runs it against every server, CeeSafe.Web's included, before any load,
and measures nothing from a server that fails it.

| Route | Answer |
|---|---|
| `GET /small` | 200, `hello, world\n` (13 bytes) |
| `GET /4k`, `GET /64k` | 200, 4096 or 65536 bytes: byte i is `'a' + i % 26` |
| `GET /stream` | 200, 1 MiB: a 16 KiB piece of that alphabet written 64 times, with no declared length |
| `GET /json` | 200, `application/json`, `{"message":"Hello, World!"}`, serialised for each request |
| `POST /echo` | 200, the request body back (at most 1 MiB) |

Every server listens on `127.0.0.1:$CW_PORT` (8090 by default) for HTTP/1.1 and h2c (HTTP/2 with prior knowledge),
with a worker per core, and keeps connections alive. Two exceptions:
- **Kestrel** cannot serve both protocols on one cleartext endpoint, so its h2c is on `$CW_H2C_PORT` (`CW_PORT` + 1
  by default).
- **Relic** has no HTTP/2 on either adapter, so it is checked and measured over HTTP/1.1 only (`conform.py
  --no-h2c`).

### TLS

With `CW_CERT` and `CW_KEY` (a PEM certificate chain and key), every server speaks TLS on `CW_PORT` instead, and
chooses HTTP/1.1 or HTTP/2 by ALPN (Relic: HTTP/1.1 only). Kestrel then needs no h2c port. `conform.py --tls` checks
the same routes over TLS, and `run.py` runs it before any TLS row. Each server uses the TLS library it is usually
deployed with, so a TLS row compares whole stacks:

| Server | TLS library |
|---|---|
| CeeSafe.Web, h2o | OpenSSL: the libcrypto each links (on the benchmark machine, 3.5.9; h2o through `OPENSSL_ROOT_DIR`) |
| Hyper, Axum, Actix Web | rustls 0.23.46 with its default provider, aws-lc-rs 1.19.0 (the owner's choice, 2026-11-19); tokio-rustls 0.26.6 for Hyper and Axum, Actix's `rustls-0_23` feature |
| Kestrel | `SslStream`: OpenSSL on Linux (`libssl.so.3` as the loader finds it, so `env.sh`'s `LD_LIBRARY_PATH` decides), Apple's TLS on macOS, which agrees on TLS 1.2 |
| Relic (`relic_io`) | dart:io's `SecurityContext`: BoringSSL, built into the Dart runtime |
| Relic (`relic_native`) | tls.zig, inside `relic_native`: TLS 1.3 only |

Each TLS row records what h2load negotiated (protocol, cipher, key exchange, ALPN), and the run's environment names
the server's library (`tls.server_library`).

- **Axum:** `axum::serve` has no TLS, so with a certificate the router is served as Axum's own low-level TLS example
  does: hyper-util's auto builder (the one `axum::serve` uses) over tokio-rustls.
- **Relic's `relic_io`:** `serve(noOfIsolates: n, securityContext: c)` fails, since it sends `c` to each isolate it
  spawns and Dart cannot send a `SecurityContext` between isolates. So the program spawns the isolates itself, each
  with its own context, binding the port shared as `serve` does.
- **The certificate** is `run.py`'s self-signed P-256 one. Under LibreSSL (macOS's `openssl`) it needs
  `ec_param_enc:named_curve` and a subjectAltName, or rustls refuses it.
- **Relic's native adapter** speaks TLS 1.3 only, so `conform.py` and `run.py`'s idle connections need a Python whose
  `ssl` has it: not Xcode's Python 3.9 (LibreSSL 2.8.3).

## The servers

| Name | What | Pinned by | Build |
|---|---|---|---|
| `hyper` | Hyper 1.12.0 with hyper-util 0.1.21's auto builder, no framework | `rust/Cargo.lock` | `cargo build --release` in `rust/` |
| `axum` | Axum 0.8.9 (`axum::serve`) | `rust/Cargo.lock` | the same |
| `actix` | Actix Web 4.20.0 (actix-http 3.22.0) | `rust/Cargo.lock` | the same |
| `kestrel` | ASP.NET Core 10 minimal APIs on Kestrel | `kestrel/global.json` (SDK 10.0.401, no roll-forward) | `dotnet publish -c Release -o out` in `kestrel/` |
| `h2o` | h2o master `d3e43fcf` (2026-11-14), with mruby | `h2o/build.sh` | `sh h2o/build.sh` (needs cmake, ruby, bison, OpenSSL 3) |
| `relic` | Serverpod's Relic 2.0.0 on Dart, `relic_io` (dart:io's server; `app.serve`) | `relic/pubspec.lock` | `dart pub get && dart build cli -o out` in `relic/` (Dart 3.14.1) |
| `relic-native` | Relic 2.0.0 on `relic_native` 2.0.0 (its HTTP server in Zig; `app.serveNative`) | the same | the same binary, `CW_ADAPTER=native` |

- **The Rust servers** share `rust/common`: the address, the bodies made once, and `/json`'s object. They run on
  Tokio 1.54.1, or Actix's own workers, a worker per core. The release profile is opt-level 3, one codegen unit, LTO.
- **Kestrel's** fixed bodies are byte arrays made once. `/json` uses System.Text.Json's source generator.
- **h2o** has had no release since 2.2.6 (2019); its maintained code is master, so a commit is pinned. h2o has no
  handler for bytes held in memory, so `start.py` writes `/small`, `/4k` and `/64k` as files for its file handler.
  `/stream`, `/json` (mruby-json, bundled with h2o) and `/echo` are mruby handlers. `start.py` writes the
  configuration for `$CW_PORT` and replaces itself with h2o; `$CW_H2O` names another h2o binary.
- **Relic** is one program for both adapters, chosen by `$CW_ADAPTER` (`io` or `native`), with an isolate per core
  (`noOfIsolates`). `dart build cli` compiles it ahead of time and puts `relic_native`'s prebuilt library
  (`lib/librelic_native.dylib` or `.so`) beside `out/bundle/bin/main`, so no Zig toolchain is needed. Its fixed
  bodies are byte arrays made once, given a MIME type so Relic does not sniff each one. The Dart SDK is pinned by
  this README, not by a file: 3.14.1 (`pubspec.lock` needs 3.13 or later), from Google's archive, checked against
  its `.sha256sum`.

## Run

```sh
python3 benchmarks/servers/conform.py --port 8090 [--h2c-port 8091] --name axum   # a server already running
python3 benchmarks/servers/conform.py --port 8090 --tls --name axum                 # started with CW_CERT, CW_KEY
python3 benchmarks/http/run.py --server axum --modes h1,h2c,tls-h1,tls-h2 --routes small,4k,64k,stream,json,echo --json axum.json
python3 benchmarks/report.py ceesafe-web.json axum.json hyper.json -o docs/benchmarks/v1.0.md   # the report
python3 benchmarks/report.py ceesafe-web.json --baseline previous-release.json      # the 5 % regression gate
```

Each `--json` file records its run's environment (Web ADR 0005 §2.4), which the report prints beside the rows.

The results belong to the benchmark machine (Web ADR 0015 §6: alfaisal, once the owner says it is clear). A run on a
laptop with the load generator beside the server checks the pipeline only.
