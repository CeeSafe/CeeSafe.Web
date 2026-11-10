"""HTTP/3 interop for web_http3 with curl (Web ADR 0014 §6 and amendment 4 item 6), through examples/h3 over real
UDP. curl's HTTP/3 is its own client code over nghttp3 and a QUIC stack of its build (ngtcp2, or OpenSSL's), so its
requests, its connection reuse and its reading of answers are independent of this server.

The expected bytes are examples/h3's routes, by their definition:
    GET  /       "hello, h3"
    GET  /big    8 MiB, byte i = 'a' + i % 26
    POST /echo   the request's body back
    GET  /echo   405 (the route is POST only)
curl verifies the server's certificate against the test CA (packages/web_tls/tests/certs/ca.pem), for `localhost`.

--curl is the command that runs curl, split as a shell would: a curl with HTTP/3 (`curl -V` lists HTTP3). Where the
host's curl has none, a container on the host's network serves, with the certificates mounted at the same path:
    python3 tests/interop/curl_h3.py --curl "docker run --rm -i --network host -v $PWD:$PWD cw-curl-h3 curl"
Build the example first (cd examples/h3 && ceesafec build).
"""
import argparse, os, pathlib, shlex, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CERTS = ROOT / "packages/web_tls/tests/certs"
PORT = 14443
BIG = bytes(97 + i % 26 for i in range(8 * 1024 * 1024))

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


def curl(prefix, paths, *extra, data=None, timeout=120):
    """Runs curl for `paths`, in one invocation: (exit code, the bodies, one line per transfer from -w)."""
    # %{stderr} sends the -w line apart from the bodies: HTTP version, status, and new connections made.
    # examples/h3 binds 127.0.0.1; without --resolve, curl may race ::1 and fail on its "connection refused".
    cmd = [*prefix, "--http3-only", "--silent", "--show-error", "--cacert", str(CERTS / "ca.pem"),
           "--resolve", f"localhost:{PORT}:127.0.0.1",
           "--write-out", "%{stderr}%{http_version} %{http_code} %{num_connects}\\n", *extra]
    cmd += [f"https://localhost:{PORT}{p}" for p in paths]
    try:
        r = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, b"", ["timed out"]
    return r.returncode, r.stdout, r.stderr.decode(errors="replace").strip().splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--curl", default="curl", help="the command that runs curl (default: curl)")
    ap.add_argument("server", nargs="?", default=str(ROOT / "examples/h3/out/h3"))
    args = ap.parse_args()
    prefix = shlex.split(args.curl)
    version = subprocess.run([*prefix, "-V"], stdout=subprocess.PIPE).stdout.decode(errors="replace")
    print(version.splitlines()[0] if version else "curl did not run")
    if "HTTP3" not in version:
        print("this curl has no HTTP/3")
        sys.exit(2)
    env = dict(os.environ, CW_PORT=str(PORT), CW_CERT=str(CERTS / "server.pem"), CW_KEY=str(CERTS / "server.key"))
    proc = subprocess.Popen([args.server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        line = proc.stdout.readline().decode().strip()
        check("the server starts", line == "h3: listening", line)
        run(prefix)
        check("the server is still running", proc.poll() is None, f"exit {proc.poll()}")
    finally:
        if proc.poll() is None:
            proc.kill()
    print(f"{sum(checks)} of {len(checks)} checks passed")
    sys.exit(0 if all(checks) else 1)


def run(prefix):
    rc, out, w = curl(prefix, ["/"])
    check("GET /", rc == 0 and out == b"hello, h3" and w == ["3 200 1"], f"exit {rc}, {w}")

    rc, out, w = curl(prefix, ["/big"])
    check("GET /big: 8 MiB", rc == 0 and out == BIG and w == ["3 200 1"], f"exit {rc}, {len(out)} bytes, {w}")

    # Three transfers in one invocation: curl reuses the connection, so only the first makes one.
    rc, out, w = curl(prefix, ["/", "/big", "/"])
    check("three requests on one connection", rc == 0 and out == b"hello, h3" + BIG + b"hello, h3" and
          w == ["3 200 1", "3 200 0", "3 200 0"], f"exit {rc}, {w}")

    rc, out, w = curl(prefix, ["/", "/", "/", "/"], "--parallel", "--parallel-immediate")
    check("four requests in parallel", rc == 0 and out == b"hello, h3" * 4 and len(w) == 4 and
          all(x.startswith("3 200") for x in w), f"exit {rc}, {w}")

    body = os.urandom(3_000_000)
    rc, out, w = curl(prefix, ["/echo"], "--data-binary", "@-", data=body)
    check("POST /echo, 3 MB back", rc == 0 and out == body and w == ["3 200 1"], f"exit {rc}, {len(out)} bytes, {w}")

    rc, out, w = curl(prefix, ["/echo"])
    check("GET /echo: 405, an empty body", rc == 0 and out == b"" and w == ["3 405 1"], f"exit {rc}, {w}")

    rc, out, w = curl(prefix, ["/"], "--head")
    check("HEAD /: no body", rc == 0 and out.startswith(b"HTTP/3 200") and b"hello" not in out,
          f"exit {rc}, {out[:40]!r}")

    fails = 0
    for _ in range(50):
        rc, out, _ = curl(prefix, ["/"], timeout=30)
        if rc != 0 or out != b"hello, h3":
            fails += 1
    check("50 connections in turn", fails == 0, f"{fails} failed")


if __name__ == "__main__":
    main()
