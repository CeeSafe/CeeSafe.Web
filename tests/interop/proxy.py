"""M8's exit gate for the reverse proxy (implementation plan §7, Web ADR 0012) over real TCP: examples/proxy in front
of a Python backend that reports what reached it, so every expected value comes from outside the code under test.

  1. forwarding: a spoofed X-Forwarded-For / Forwarded from an untrusted client is replaced by the real peer (§3.4);
     a field the client's Connection names, and TE, do not reach the backend (RFC 9110 §7.6.1);
  2. a partial upstream response (Content-Length 1 000 000, 100 000 bytes, then the backend closes) is never
     presented as complete (§3.7): over HTTP/1.1 curl exits 18, "transfer closed with outstanding read data
     remaining"; over h2c the stream is reset and curl exits 92;
  3. 1 GiB down (ADR 0012 §5's gate) and 1 GiB up (with CW_MAX_BODY raised), over HTTP/1.1 and over h2c: the
     SHA-256 on each side is the same; at the default limit (8 MiB) a larger upload is 413;
  4. h2load -n 20000 -c 10 -m 10 over h2c through the proxy to examples/server: every request succeeds;
  5. HAProxy (official Docker image) in front, sending PROXY protocol v1 and v2 (§3.4, amendment 2): the backend's
     X-Forwarded-For is the client address HAProxy saw (HAProxy also sends it as x-haproxy-src); a trusted source
     that sends no header is dropped.

Build examples/proxy and examples/server first (ceesafec build in each), then:
    python3 tests/interop/proxy.py [--skip-haproxy] [--gib N]
"""
import argparse, hashlib, http.client, http.server, json, os, pathlib, socket, socketserver, subprocess, sys, \
    tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROXY = ROOT / "examples/proxy/out/proxy"
SERVER = ROOT / "examples/server/out/server"
BACKEND, FRONT, H2BACKEND, H2FRONT, PPFRONT, HAPROXY = 18101, 18100, 18103, 18102, 18104, 18105
BLOCK = hashlib.sha256(b"cw-m8").digest() * 2048  # 64 KiB, the download's repeating content

results = []


def check(name, cond, got):
    results.append(cond)
    print(("ok  " if cond else "FAIL") + f" {name}: {got}", flush=True)


class Backend(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/headers":
            body = json.dumps({"peer": self.client_address[0],
                               "headers": [[k.lower(), v] for k, v in self.headers.items()]}).encode()
            self.answer(body)
        elif self.path == "/short":
            self.send_response(200)
            self.send_header("content-length", "1000000")
            self.end_headers()
            self.wfile.write(b"x" * 100000)
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
        elif self.path.startswith("/bytes/"):
            n = int(self.path[7:])
            self.send_response(200)
            self.send_header("content-length", str(n))
            self.end_headers()
            while n > 0:
                k = min(n, len(BLOCK))
                self.wfile.write(BLOCK[:k])
                n -= k
        else:
            self.send_error(404)

    def do_POST(self):
        h = hashlib.sha256()
        total = 0
        if self.headers.get("transfer-encoding", "").lower() == "chunked":
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b""):
                        pass
                    break
                left = size
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    h.update(chunk)
                    left -= len(chunk)
                    total += len(chunk)
                self.rfile.readline()
        else:
            left = int(self.headers.get("content-length", "0"))
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                h.update(chunk)
                left -= len(chunk)
                total += len(chunk)
        self.answer(f"{total} {h.hexdigest()}".encode())

    def answer(self, body):
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(exe, env, ready):
    p = subprocess.Popen([str(exe)], env=dict(os.environ, **env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    line = p.stdout.readline().decode().strip()
    if line != ready:
        p.kill()
        sys.exit(f"{exe.name} did not start: {line}")
    return p


def curl(*args):
    return subprocess.run(["curl", "-sS", *args], capture_output=True)


def headers_seen(port, extra):
    r = curl(f"http://127.0.0.1:{port}/headers", *sum([["-H", h] for h in extra], []))
    seen = json.loads(r.stdout)
    fields = {}
    for k, v in seen["headers"]:
        fields.setdefault(k, []).append(v)
    return seen["peer"], fields


def forwarding():
    peer, f = headers_seen(FRONT, ["X-Forwarded-For: 1.2.3.4", "Forwarded: for=1.2.3.4", "Connection: x-hop",
                                   "X-Hop: secret", "TE: trailers", "X-End: kept"])
    check("spoofed X-Forwarded-For replaced", f.get("x-forwarded-for") == ["127.0.0.1"], f.get("x-forwarded-for"))
    check("spoofed Forwarded replaced", f.get("forwarded") == ['for=127.0.0.1;proto=http;host="127.0.0.1:18100"'],
          f.get("forwarded"))
    check("Connection-named field dropped", "x-hop" not in f, f.get("x-hop"))
    check("TE dropped", "te" not in f, f.get("te"))
    check("end-to-end field kept", f.get("x-end") == ["kept"], f.get("x-end"))


def partial():
    for proto in ([], ["--http2-prior-knowledge"]):
        r = curl(*proto, "-o", os.devnull, f"http://127.0.0.1:{FRONT}/short")
        # HTTP/1.1 ends the connection short of its length (curl 18); HTTP/2 resets the stream (curl 92).
        want = 92 if proto else 18
        check(f"partial upstream body {'h2c' if proto else 'HTTP/1.1'}: curl exit {want}", r.returncode == want,
              (r.returncode, r.stderr.decode().strip()[:100]))


def big(gib, scratch):
    n = gib << 30
    expected = hashlib.sha256()
    for _ in range(n // len(BLOCK)):
        expected.update(BLOCK)
    expected.update(BLOCK[:n % len(BLOCK)])
    want = expected.hexdigest()
    upload = pathlib.Path(scratch) / "upload.bin"
    with open(upload, "wb") as f:
        for _ in range(n // len(BLOCK)):
            f.write(BLOCK)
        f.write(BLOCK[:n % len(BLOCK)])
    for proto in ([], ["--http2-prior-knowledge"]):
        name = "h2c" if proto else "HTTP/1.1"
        t = time.time()
        p = subprocess.Popen(["curl", "-sS", *proto, f"http://127.0.0.1:{FRONT}/bytes/{n}"], stdout=subprocess.PIPE)
        got = hashlib.sha256()
        size = 0
        while chunk := p.stdout.read(1 << 20):
            got.update(chunk)
            size += len(chunk)
        p.wait()
        check(f"{gib} GiB down {name}", p.returncode == 0 and size == n and got.hexdigest() == want,
              f"{size} bytes, {time.time() - t:.1f} s, exit {p.returncode}")
        t = time.time()
        # -T streams the file (--data-binary @file would read 1 GiB into curl's memory).
        r = curl(*proto, "-X", "POST", "-T", str(upload), "-H", "content-type: application/octet-stream",
                 f"http://127.0.0.1:{FRONT}/sha")
        check(f"{gib} GiB up {name}", r.returncode == 0 and r.stdout.decode() == f"{n} {want}",
              f"{r.stdout.decode()[:90]}, {time.time() - t:.1f} s")
    upload.unlink()


def limited(scratch):
    proxy = start(PROXY, {"CW_PORT": str(H2FRONT), "CW_BACKENDS": f"http://127.0.0.1:{BACKEND}"}, "proxy: listening")
    body = pathlib.Path(scratch) / "nine.bin"
    body.write_bytes(b"z" * (9 << 20))
    try:
        r = curl("-o", os.devnull, "-w", "%{http_code}", "--data-binary", f"@{body}", f"http://127.0.0.1:{H2FRONT}/sha")
        check("9 MiB up at the default limit: 413", r.stdout == b"413", r.stdout.decode())
    finally:
        proxy.kill()
        body.unlink()


def load():
    server = start(SERVER, {"CW_PORT": str(H2BACKEND)}, "server: listening")
    proxy = start(PROXY, {"CW_PORT": str(H2FRONT), "CW_BACKENDS": f"http://127.0.0.1:{H2BACKEND}"}, "proxy: listening")
    try:
        r = subprocess.run(["h2load", "-n", "20000", "-c", "10", "-m", "10", f"http://127.0.0.1:{H2FRONT}/"],
                           capture_output=True, text=True)
        line = [l for l in r.stdout.splitlines() if l.startswith("requests:")]
        rate = [l for l in r.stdout.splitlines() if l.startswith("finished in")]
        check("h2load through the proxy", bool(line) and "20000 succeeded" in line[0] and "0 failed" in line[0],
              (line[0] if line else r.stdout[-300:]) + (" | " + rate[0] if rate else ""))
    finally:
        proxy.kill()
        server.kill()


def haproxy(scratch):
    cfg = pathlib.Path(scratch) / "haproxy.cfg"
    cfg.write_text(f"""global
    log stdout format raw local0
defaults
    mode http
    timeout connect 5s
    timeout client 30s
    timeout server 30s
frontend v1
    bind :8001
    http-request set-header x-haproxy-src %[src]
    default_backend v1
frontend v2
    bind :8002
    http-request set-header x-haproxy-src %[src]
    default_backend v2
backend v1
    server p host.docker.internal:{PPFRONT} send-proxy
backend v2
    server p host.docker.internal:{PPFRONT} send-proxy-v2
""")
    name = f"cw-haproxy-{os.getpid()}"
    run = subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"{HAPROXY}:8001",
                          "-p", f"{HAPROXY + 1}:8002", "-v", f"{cfg}:/usr/local/etc/haproxy/haproxy.cfg:ro",
                          "haproxy:3.0"], capture_output=True, text=True)
    if run.returncode != 0:
        check("HAProxy started", False, run.stderr.strip()[-200:])
        return
    trusted = "127.0.0.0/8,::1/128,192.168.0.0/16,172.16.0.0/12,10.0.0.0/8"
    proxy = start(PROXY, {"CW_PORT": str(PPFRONT), "CW_BACKENDS": f"http://127.0.0.1:{BACKEND}",
                          "CW_PROXY_PROTOCOL": trusted}, "proxy: listening")
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", HAPROXY), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        time.sleep(1)
        for version, port in (("v1", HAPROXY), ("v2", HAPROXY + 1)):
            peer, f = headers_seen(port, ["X-Forwarded-For: 6.6.6.6"])
            src = f.get("x-haproxy-src", ["?"])[0]
            check(f"PROXY {version}: X-Forwarded-For is HAProxy's client", f.get("x-forwarded-for") == [src] and
                  src not in ("?", "6.6.6.6"), (f.get("x-forwarded-for"), src))
        r = curl("-m", "5", f"http://127.0.0.1:{PPFRONT}/headers")
        check("a trusted source without a PROXY header is dropped", r.returncode in (52, 56) and not r.stdout,
              (r.returncode, r.stderr.decode().strip()[:80]))
    finally:
        proxy.kill()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-haproxy", action="store_true")
    ap.add_argument("--gib", type=int, default=1)
    args = ap.parse_args()
    backend = ThreadingServer(("127.0.0.1", BACKEND), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    proxy = start(PROXY, {"CW_PORT": str(FRONT), "CW_BACKENDS": f"http://127.0.0.1:{BACKEND}",
                          "CW_MAX_BODY": str((args.gib << 30) + 1)}, "proxy: listening")
    scratch = tempfile.mkdtemp(prefix="cw-proxy-")
    try:
        forwarding()
        partial()
        big(args.gib, scratch)
    finally:
        proxy.kill()
    limited(scratch)
    load()
    if not args.skip_haproxy:
        haproxy(scratch)
    backend.shutdown()
    print(f"proxy gate: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
