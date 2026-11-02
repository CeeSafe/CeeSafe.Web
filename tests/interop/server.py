"""Interop check of examples/server (Web ADR 0007 §3.4) over real TCP with Python's http.client.

Build the example first (cd examples/server && ceesafec build), then:
    python3 tests/interop/server.py [path to the server]
"""
import http.client, pathlib, subprocess, sys, time

exe = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[2] / "examples/server/out/server")
port = 18093
proc = subprocess.Popen([exe], env={"CW_PORT": str(port)}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
for _ in range(50):
    try:
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/stats"); break
    except OSError:
        time.sleep(0.1)

def req(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict((k.lower(), v) for k, v in r.getheaders()), data

checks = []
def check(name, cond, got):
    checks.append(cond)
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")

s, h, b = req("GET", "/")
check("GET /", s == 200 and b == b"hello, world" and h.get("server") == "ceesafe-web" and h.get("content-type") == "text/plain" and "date" in h, (s, h, b))
s, h, b = req("GET", "/hello/w%C3%B6rld")
check("GET /hello/:name", s == 200 and b == "hello, wörld".encode(), (s, b))
s, h, b = req("POST", "/echo", b"ping pong")
check("POST /echo", s == 200 and b == b"ping pong", (s, b))
s, h, b = req("DELETE", "/echo")
check("DELETE /echo", s == 405 and h.get("allow") == "POST, OPTIONS" and h.get("server") == "ceesafe-web", (s, h.get("allow")))
s, h, b = req("GET", "/nope")
check("GET /nope", s == 404, s)
s, h, b = req("HEAD", "/")
check("HEAD /", s == 200 and h.get("content-length") == "12" and b == b"", (s, h.get("content-length")))
s, h, b = req("GET", "/stats")
# Requests that reached the middleware: the readiness probe, then the 6 above, then this one.
check("GET /stats", s == 200 and b == b"8", b)
s, h, b = req("POST", "/stop")
check("POST /stop", s == 200 and b == b"stopping", b)
try:
    out, _ = proc.communicate(timeout=10)
    check("drained and exited 0", proc.returncode == 0 and b"server: drained" in out, (proc.returncode, out))
except subprocess.TimeoutExpired:
    proc.kill()
    check("drained and exited", False, "timeout")
sys.exit(0 if all(checks) else 1)
