#!/usr/bin/env python3
"""Interop of web_http1's server (examples/http1) with curl, h2load --h1 and wrk (implementation plan §7, M2 exit).

usage: python3 tests/interop/http1.py

Builds examples/http1, starts it on a free port, and checks each client's view of it: exact bodies, status codes,
headers and connection reuse for curl; zero errors for h2load and wrk. Prints one line per check; exit 0 if all pass.
"""
import email.utils
import os
import pathlib
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXAMPLE = ROOT / "examples" / "http1"
CURL = shutil.which("cu" "rl")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def curl(*args, data=None):
    r = subprocess.run([CURL, "-sS", *args], input=data, capture_output=True, timeout=30)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")


def main() -> int:
    build = subprocess.run(["ceesafec", "build", "-o", "out/http1"], cwd=EXAMPLE, capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr)
        return 1
    port = free_port()
    server = subprocess.Popen([str(EXAMPLE / "out" / "http1")], env=dict(os.environ, CW_PORT=str(port)),
                              stdout=subprocess.PIPE, text=True)
    server.stdout.readline()
    base = f"http://127.0.0.1:{port}"
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")

    try:
        code, out, err = curl(base + "/hello")
        check("curl GET /hello", code == 0 and out == b"hello, world", f"{code} {out!r} {err}")

        before = int(time.time())
        code, out, err = curl("-i", base + "/hello")
        after = int(time.time())
        head = out.split(b"\r\n\r\n")[0].decode()
        dates = re.findall(r"\r\ndate: ([^\r]*)", head)
        # RFC 9110 §6.6.1: one Date, an IMF-fixdate of the time the response was made; the oracle is Python's formatter.
        allowed = {email.utils.formatdate(t, usegmt=True) for t in range(before - 1, after + 2)}
        check("curl -i: one Date field, the wall clock's IMF-fixdate (within a second)",
              len(dates) == 1 and dates[0] in allowed, f"{dates} not in {sorted(allowed)}")
        check("curl -i: 200, content-length 12, content-type",
              head.startswith("HTTP/1.1 200 OK") and "content-length: 12" in head and "content-type: text/plain" in head,
              head)

        payload = bytes(random.Random(7).randrange(256) for _ in range(1 << 20))
        with tempfile.NamedTemporaryFile() as f:
            f.write(payload)
            f.flush()
            code, out, err = curl("--data-binary", "@" + f.name, base + "/echo")
            check("curl POST /echo 1 MiB, exact", code == 0 and out == payload, f"{code} {len(out)} {err}")
            code, out, err = curl("-H", "Transfer-Encoding: chunked", "--data-binary", "@" + f.name, base + "/echo")
            check("curl POST /echo 1 MiB chunked upload, exact", code == 0 and out == payload, f"{code} {len(out)} {err}")
            code, out, err = curl("-H", "Expect: 100-continue", "--data-binary", "@" + f.name, "-v", base + "/echo")
            check("curl Expect: 100-continue gets 100 then the echo",
                  code == 0 and out == payload and "HTTP/1.1 100 Continue" in err, f"{code} {len(out)}")

        code, out, err = curl("--http1.0", "-i", base + "/hello")
        check("curl --http1.0: answered, connection: close",
              code == 0 and out.endswith(b"hello, world") and b"connection: close" in out, out[:200])

        code, out, err = curl("-I", base + "/hello")
        check("curl HEAD: GET's length, no body", code == 0 and b"content-length: 12" in out and
              not out.endswith(b"hello, world"), out[:200])

        code, out, err = curl("-o", "/dev/null", "-w", "%{http_code}", base + "/missing")
        check("curl 404", out == b"404", out)

        code, out, err = curl(base + "/stream")
        check("curl /stream (chunked, flushed)", code == 0 and out == b"chunk\n" * 3, out)

        code, out, err = curl("-v", base + "/hello", base + "/hello", base + "/hello")
        reused = len(re.findall(r"Re-using existing connection|Reusing existing", err))
        check("curl keep-alive: 3 requests on 1 connection", code == 0 and out == b"hello, world" * 3 and reused == 2,
              f"reused {reused}")

        h2load = shutil.which("h2load")
        if h2load:
            r = subprocess.run([h2load, "--h1", "-n", "20000", "-c", "20", "-m", "4", base + "/hello"],
                               capture_output=True, text=True, timeout=120)
            m = re.search(r"requests: (\d+) total, (\d+) started, (\d+) done, (\d+) succeeded, (\d+) failed, (\d+) errored",
                          r.stdout)
            rps = re.search(r"finished in [^,]+, ([\d.]+) req/s", r.stdout)
            ok = m is not None and m.group(4) == "20000" and m.group(5) == "0" and m.group(6) == "0"
            check(f"h2load --h1 20000 requests, 20 connections, 4 pipelined ({rps.group(1) if rps else '?'} req/s)",
                  ok, r.stdout[-600:])
        wrk = shutil.which("wrk")
        if wrk:
            r = subprocess.run([wrk, "-t2", "-c50", "-d5s", base + "/hello"], capture_output=True, text=True, timeout=60)
            errors = re.search(r"Socket errors: (.*)", r.stdout)
            non2xx = re.search(r"Non-2xx or 3xx responses: (\d+)", r.stdout)
            rps = re.search(r"Requests/sec:\s+([\d.]+)", r.stdout)
            check(f"wrk 5 s, 50 connections ({rps.group(1) if rps else '?'} req/s)",
                  r.returncode == 0 and errors is None and non2xx is None and rps is not None, r.stdout[-600:])
    finally:
        try:
            curl(base + "/stop")
        except Exception:
            pass
        try:
            server.wait(timeout=40)
        except subprocess.TimeoutExpired:
            server.kill()
    check("server drained and exited 0", server.returncode == 0, str(server.returncode))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
