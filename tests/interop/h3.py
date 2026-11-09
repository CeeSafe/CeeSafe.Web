"""HTTP/3 interop for web_http3 (Web ADR 0014 amendment 4 item 6), through examples/h3 over real UDP, with ngtcp2's own
example client in its HTTP/3 build (`osslclient`, ngtcp2 1.26.0's `examples/client.cc` with nghttp3 and OpenSSL),
built as tests/interop/quic.py says.

The client's HTTP/3 is nghttp3 as we vendor it, but its connection handling, its requests and its reading of answers
are its own. The expected bytes are examples/h3's routes, by their definition:
    GET  /       "hello, h3"
    GET  /big    8 MiB, byte i = 'a' + i % 26
    POST /echo   the request's body back
    GET  /echo   405 (the route is POST only): an empty body
It does not verify the server's certificate.

Build the example first (cd examples/h3 && ceesafec build), then:
    python3 tests/interop/h3.py --client PATH/TO/osslclient [path to the server]
"""
import argparse, os, pathlib, shutil, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CERTS = ROOT / "packages/web_tls/tests/certs"
PORT = 14443
BIG = bytes(97 + i % 26 for i in range(8 * 1024 * 1024))

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


def fetch(client, paths, download, *extra, timeout=120):
    """Runs the client for `paths` on one connection: (exit code, seconds, its output's tail)."""
    shutil.rmtree(download, ignore_errors=True)
    download.mkdir(parents=True)
    uris = [f"https://localhost:{PORT}{p}" for p in paths]
    cmd = [client, "--quiet", "--no-quic-dump", "--no-http-dump", "--exit-on-all-streams-close",
           f"--download={download}", *extra, "127.0.0.1", str(PORT), *uris]
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, timeout, "timed out"
    tail = r.stdout.decode(errors="replace").strip().splitlines()[-3:]
    return r.returncode, time.monotonic() - start, " | ".join(tail)


def read(download, name):
    f = download / name
    return f.read_bytes() if f.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True, help="ngtcp2's HTTP/3 client (osslclient)")
    ap.add_argument("server", nargs="?", default=str(ROOT / "examples/h3/out/h3"))
    args = ap.parse_args()
    work = pathlib.Path(tempfile.mkdtemp(prefix="cw-h3-"))
    env = dict(os.environ, CW_PORT=str(PORT), CW_CERT=str(CERTS / "server.pem"), CW_KEY=str(CERTS / "server.key"))
    proc = subprocess.Popen([args.server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        line = proc.stdout.readline().decode().strip()
        check("the server starts", line == "h3: listening", line)
        run(args.client, work)
        check("the server is still running", proc.poll() is None, f"exit {proc.poll()}")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)
    print(f"{sum(checks)} of {len(checks)} checks passed")
    sys.exit(0 if all(checks) else 1)


def run(client, work):
    d = work / "download"
    rc, secs, tail = fetch(client, ["/", "/big"], d)
    check("GET / and /big on one connection", rc == 0 and read(d, "index.html") == b"hello, h3" and
          read(d, "big") == BIG, f"exit {rc}, {secs:.2f} s {tail}")

    body = os.urandom(3_000_000)
    (work / "post.bin").write_bytes(body)
    rc, secs, tail = fetch(client, ["/echo"], d, "--http-method=POST", f"--data={work / 'post.bin'}")
    check("POST /echo, 3 MB back", rc == 0 and read(d, "echo") == body, f"exit {rc}, {secs:.2f} s {tail}")

    rc, secs, tail = fetch(client, ["/echo"], d)
    check("GET /echo: 405, an empty body", rc == 0 and read(d, "echo") == b"", f"exit {rc} {tail}")

    # Many requests on one connection: past the 100 streams a client may have open at first (RFC 9000 §4.6).
    rc, secs, tail = fetch(client, ["/"], d, "--nstreams=150")
    check("150 requests on one connection", rc == 0 and read(d, "index.html") == b"hello, h3",
          f"exit {rc}, {secs:.2f} s {tail}")

    for t, r in (("0.1", "0"), ("0", "0.1"), ("0.1", "0.1")):
        rc, secs, tail = fetch(client, ["/big"], d, f"--tx-loss={t}", f"--rx-loss={r}")
        check(f"loss {t} sent, {r} received: 8 MiB whole", rc == 0 and read(d, "big") == BIG,
              f"exit {rc}, {secs:.2f} s {tail}")

    fails = 0
    start = time.monotonic()
    for _ in range(200):
        rc, _, _ = fetch(client, ["/"], d, timeout=30)
        if rc != 0 or read(d, "index.html") != b"hello, h3":
            fails += 1
    check("200 connections in turn", fails == 0, f"{fails} failed, {time.monotonic() - start:.1f} s")

    procs = []
    for k in range(20):
        dk = work / f"par{k}"
        dk.mkdir()
        cmd = [client, "--quiet", "--no-quic-dump", "--no-http-dump", "--exit-on-all-streams-close",
               f"--download={dk}", "127.0.0.1", str(PORT), f"https://localhost:{PORT}/big"]
        procs.append((dk, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
    bad = 0
    for dk, p in procs:
        try:
            rc = p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        if rc != 0 or read(dk, "big") != BIG:
            bad += 1
    check("20 connections at once, 8 MiB each", bad == 0, f"{bad} failed")


if __name__ == "__main__":
    main()
