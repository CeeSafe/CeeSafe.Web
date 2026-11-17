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
with a worker per core, and keeps connections alive. Kestrel is the exception on one point: it cannot serve both
protocols on one cleartext endpoint, so its h2c is on `$CW_H2C_PORT` (`CW_PORT` + 1 by default). TLS is not part of
the comparison yet.

## The servers

| Name | What | Pinned by | Build |
|---|---|---|---|
| `hyper` | Hyper 1.12.0 with hyper-util 0.1.21's auto builder, no framework | `rust/Cargo.lock` | `cargo build --release` in `rust/` |
| `axum` | Axum 0.8.9 (`axum::serve`) | `rust/Cargo.lock` | the same |
| `actix` | Actix Web 4.20.0 (actix-http 3.22.0) | `rust/Cargo.lock` | the same |
| `kestrel` | ASP.NET Core 10 minimal APIs on Kestrel | `kestrel/global.json` (SDK 10.0.401, no roll-forward) | `dotnet publish -c Release -o out` in `kestrel/` |
| `h2o` | h2o master `d3e43fcf` (2026-11-14), with mruby | `h2o/build.sh` | `sh h2o/build.sh` (needs cmake, ruby, bison, OpenSSL 3) |

- **The Rust servers** share `rust/common`: the address, the bodies made once, and `/json`'s object. They run on
  Tokio 1.54.1, or Actix's own workers, a worker per core. The release profile is opt-level 3, one codegen unit, LTO.
- **Kestrel's** fixed bodies are byte arrays made once. `/json` uses System.Text.Json's source generator.
- **h2o** has had no release since 2.2.6 (2019); its maintained code is master, so a commit is pinned. h2o has no
  handler for bytes held in memory, so `start.py` writes `/small`, `/4k` and `/64k` as files for its file handler.
  `/stream`, `/json` (mruby-json, bundled with h2o) and `/echo` are mruby handlers. `start.py` writes the
  configuration for `$CW_PORT` and replaces itself with h2o; `$CW_H2O` names another h2o binary.

## Run

```sh
python3 benchmarks/servers/conform.py --port 8090 [--h2c-port 8091] --name axum   # a server already running
python3 benchmarks/http/run.py --server axum --modes h1,h2c --routes small,4k,64k,stream,json,echo --no-alloc
```

The results belong to the benchmark machine (Web ADR 0015 §6: alfaisal, once the owner says it is clear). A run on a
laptop with the load generator beside the server checks the pipeline only.
