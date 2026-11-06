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
     that sends no header is dropped;
  6. trailers both ways: the backend's response trailers reach curl (HTTP/1.1) and nghttp (h2c), and a client's
     request trailers reach the backend (RFC 9110 §6.5);
  7. half-close: a client that sends its requests and then shutdown(SHUT_WR) still gets every response, over
     HTTP/1.1 (pipelined) and h2c (three streams);
  8. backpressure both ways (§3.5): a client that stops reading stops the backend's writes, and a backend that stops
     reading stops the client's, each within a bounded number of bytes and with the proxy's memory bounded;
  9. a tunnel whose peers write at once: 64 MiB each way over an upgraded connection, SHA-256 equal at both ends;
 10. h2load through the proxy to examples/server over an HTTP/2 (h2c) upstream (CW_UPSTREAM_H2C).

Build examples/proxy and examples/server first (ceesafec build in each), then:
    python3 tests/interop/proxy.py [--skip-haproxy] [--gib N]
"""
import argparse, hashlib, http.client, http.server, json, os, pathlib, socket, socketserver, subprocess, sys, \
    tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROXY = ROOT / "examples/proxy/out/proxy"
SERVER = ROOT / "examples/server/out/server"
BACKEND, FRONT, H2BACKEND, H2FRONT, PPFRONT, HAPROXY = 18101, 18100, 18103, 18102, 18104, 18105
TUNNEL_BACKEND, TUNNEL_FRONT = 18107, 18108
# examples/proxy listens on 127.0.0.1, which a bridged container reaches only through Docker Desktop's
# host.docker.internal; on Linux HAProxy shares the host's network instead.
LINUX = sys.platform.startswith("linux")
BLOCK = hashlib.sha256(b"cw-m8").digest() * 2048  # 64 KiB, the download's repeating content

results = []
progress = {"down": 0, "up": 0}  # bytes the backend has written to /stream, and read from /sink
gate = threading.Event()  # set: /sink reads on


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
        elif self.path == "/trailers":
            self.send_response(200)
            self.send_header("transfer-encoding", "chunked")
            self.send_header("trailer", "x-checksum")
            self.end_headers()
            self.wfile.write(b"5\r\nhello\r\n0\r\nx-checksum: c0ffee\r\n\r\n")
        elif self.path.startswith("/stream/"):
            n = int(self.path[8:])
            progress["down"] = 0
            self.send_response(200)
            self.send_header("content-length", str(n))
            self.end_headers()
            while n > 0:
                k = min(n, len(BLOCK))
                self.wfile.write(BLOCK[:k])
                progress["down"] += k
                n -= k
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
        if self.path == "/sink":
            return self.sink()
        h = hashlib.sha256()
        trailers = []
        total = 0
        if self.headers.get("transfer-encoding", "").lower() == "chunked":
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    while (line := self.rfile.readline()) not in (b"\r\n", b""):
                        trailers.append(line.decode().strip().lower())
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
        if self.path == "/trailers":
            return self.answer(json.dumps(trailers).encode())
        self.answer(f"{total} {h.hexdigest()}".encode())

    def sink(self):
        # Reads 1 MiB, then nothing until `gate` is set: the client's writes must stall behind it.
        left = int(self.headers.get("content-length", "0"))
        progress["up"] = 0
        while left:
            if progress["up"] >= 1 << 20:
                gate.wait()
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            progress["up"] += len(chunk)
            left -= len(chunk)
        self.answer(str(progress["up"]).encode())

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


def rss_kib(p):
    r = subprocess.run(["ps", "-o", "rss=", "-p", str(p.pid)], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def trailers():
    # curl -v does not print trailers; --raw passes the chunked framing, the last chunk's trailers with it.
    r = curl("-v", "--raw", "-o", "-", f"http://127.0.0.1:{FRONT}/trailers")
    check("response trailers to curl (HTTP/1.1)",
          r.stdout == b"5\r\nhello\r\n0\r\nx-checksum: c0ffee\r\n\r\n" and b"< trailer: x-checksum" in r.stderr,
          (r.stdout[:60], [l for l in r.stderr.decode().splitlines() if "checksum" in l]))
    n = subprocess.run(["nghttp", "-v", f"http://127.0.0.1:{FRONT}/trailers"], capture_output=True, text=True)
    check("response trailers to nghttp (h2c)", "x-checksum: c0ffee" in n.stdout,
          [l.strip() for l in n.stdout.splitlines() if "checksum" in l][:2] or n.stdout[-200:])
    with socket.create_connection(("127.0.0.1", FRONT)) as c:
        c.sendall(b"POST /trailers HTTP/1.1\r\nhost: x\r\ntransfer-encoding: chunked\r\nconnection: close\r\n\r\n"
                  b"3\r\nabc\r\n0\r\nx-request-sum: 42\r\n\r\n")
        got = read_all(c)
    check("request trailers to the backend", b'"x-request-sum: 42"' in got, got.split(b"\r\n\r\n")[-1][:80])


def read_all(c):
    out = b""
    while chunk := c.recv(65536):
        out += chunk
    return out


def h2_frame(kind, flags, stream, payload=b""):
    return len(payload).to_bytes(3, "big") + bytes([kind, flags]) + stream.to_bytes(4, "big") + payload


def half_close():
    with socket.create_connection(("127.0.0.1", FRONT)) as c:
        c.sendall(b"GET /headers HTTP/1.1\r\nhost: x\r\n\r\nGET /headers HTTP/1.1\r\nhost: x\r\n\r\n")
        c.shutdown(socket.SHUT_WR)
        got = read_all(c)
    check("half-close HTTP/1.1: both pipelined responses", got.count(b"HTTP/1.1 200 OK") == 2,
          got.count(b"HTTP/1.1 200 OK"))
    # h2c by prior knowledge: three GETs (HPACK static indexes: :method GET 0x82, :scheme http 0x86, :path / 0x84,
    # then /headers as a literal path, and :authority as a literal), then SHUT_WR.
    block = b"\x82\x86\x44\x08/headers\x41\x01x"
    out = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + h2_frame(4, 0, 0)
    for sid in (1, 3, 5):
        out += h2_frame(1, 0x5, sid, block)  # END_STREAM | END_HEADERS
    with socket.create_connection(("127.0.0.1", FRONT)) as c:
        c.sendall(out)
        c.shutdown(socket.SHUT_WR)
        c.settimeout(10)
        data = read_all(c)
    ended, at = set(), 0
    while at + 9 <= len(data):
        n = int.from_bytes(data[at:at + 3], "big")
        kind, flags, sid = data[at + 3], data[at + 4], int.from_bytes(data[at + 5:at + 9], "big") & 0x7FFFFFFF
        if kind in (0, 1) and flags & 1:
            ended.add(sid)
        at += 9 + n
    check("half-close h2c: three streams answered", ended == {1, 3, 5}, sorted(ended))


def backpressure(proxy):
    base = rss_kib(proxy)
    total = 256 << 20
    c = socket.create_connection(("127.0.0.1", FRONT))
    c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    c.sendall(f"GET /stream/{total} HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\r\n".encode())
    got = c.recv(1 << 20)
    time.sleep(3)
    written, rss = progress["down"], rss_kib(proxy)
    check("a client that stops reading stops the backend", written < 64 << 20 and rss - base < 64 << 10,
          f"backend wrote {written >> 20} MiB of {total >> 20} while the client read {len(got) >> 10} KiB; "
          f"proxy RSS +{(rss - base) >> 10} MiB")
    h = hashlib.sha256(got)
    size = len(got)
    while chunk := c.recv(1 << 20):
        h.update(chunk)
        size += len(chunk)
    c.close()
    check("then it reads to the end", size > total and progress["down"] == total, f"{size} bytes on the wire")
    gate.clear()
    c = socket.create_connection(("127.0.0.1", FRONT))
    c.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
    c.sendall(f"POST /sink HTTP/1.1\r\nhost: x\r\ncontent-length: {total}\r\nconnection: close\r\n\r\n".encode())
    sent = [0]

    def send():
        piece = b"u" * 65536
        try:
            while sent[0] < total:
                c.sendall(piece)
                sent[0] += len(piece)
        except OSError:
            pass
    t = threading.Thread(target=send, daemon=True)
    t.start()
    time.sleep(3)
    stalled, rss = sent[0], rss_kib(proxy)
    check("a backend that stops reading stops the client", stalled < 64 << 20 and rss - base < 64 << 10,
          f"client sent {stalled >> 20} MiB of {total >> 20} while the backend read {progress['up'] >> 10} KiB; "
          f"proxy RSS +{(rss - base) >> 10} MiB")
    gate.set()
    t.join(120)
    answer = read_all(c)
    c.close()
    check("then the backend reads it all", answer.endswith(str(total).encode()), answer[-40:])


TUNNEL_BYTES = 64 << 20


def tunnel_backend(server, digests):
    conn, _ = server.accept()
    head = b""
    while b"\r\n\r\n" not in head:
        head += conn.recv(4096)
    rest = head.split(b"\r\n\r\n", 1)[1]
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nconnection: upgrade\r\nupgrade: x-test\r\n\r\n")
    digests["backend got"] = stream_both(conn, rest)
    conn.close()


def stream_both(conn, already):
    """Writes TUNNEL_BYTES of a pattern while reading TUNNEL_BYTES; the SHA-256 of what was read."""
    def write():
        for i in range(TUNNEL_BYTES // len(BLOCK)):
            conn.sendall(BLOCK)
    t = threading.Thread(target=write, daemon=True)
    t.start()
    h, n = hashlib.sha256(already), len(already)
    while n < TUNNEL_BYTES:
        chunk = conn.recv(1 << 20)
        if not chunk:
            break
        h.update(chunk)
        n += len(chunk)
    t.join()
    return h.hexdigest(), n


def tunnel():
    want = hashlib.sha256(BLOCK * (TUNNEL_BYTES // len(BLOCK))).hexdigest()
    server = socket.create_server(("127.0.0.1", TUNNEL_BACKEND))
    digests = {}
    tb = threading.Thread(target=tunnel_backend, args=(server, digests), daemon=True)
    tb.start()
    proxy = start(PROXY, {"CW_PORT": str(TUNNEL_FRONT), "CW_BACKENDS": f"http://127.0.0.1:{TUNNEL_BACKEND}"},
                  "proxy: listening")
    try:
        c = socket.create_connection(("127.0.0.1", TUNNEL_FRONT))
        c.sendall(b"GET /t HTTP/1.1\r\nhost: x\r\nconnection: upgrade\r\nupgrade: x-test\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += c.recv(4096)
        status, rest = head.split(b"\r\n", 1)[0], head.split(b"\r\n\r\n", 1)[1]
        t0 = time.time()
        client_got = stream_both(c, rest)
        tb.join(60)
        c.close()
        check("tunnel, both peers writing at once", status.startswith(b"HTTP/1.1 101") and
              client_got == (want, TUNNEL_BYTES) and digests.get("backend got") == (want, TUNNEL_BYTES),
              f"{status.decode()}; {TUNNEL_BYTES >> 20} MiB each way in {time.time() - t0:.1f} s")
    finally:
        proxy.kill()
        server.close()


def load_h2_upstream():
    server = start(SERVER, {"CW_PORT": str(H2BACKEND)}, "server: listening")
    proxy = start(PROXY, {"CW_PORT": str(H2FRONT), "CW_BACKENDS": f"http://127.0.0.1:{H2BACKEND}",
                          "CW_UPSTREAM_H2C": "1"}, "proxy: listening")
    try:
        r = subprocess.run(["h2load", "-n", "20000", "-c", "10", "-m", "10", f"http://127.0.0.1:{H2FRONT}/"],
                           capture_output=True, text=True)
        line = [l for l in r.stdout.splitlines() if l.startswith("requests:")]
        rate = [l for l in r.stdout.splitlines() if l.startswith("finished in")]
        check("h2load through the proxy, h2c upstream", bool(line) and "20000 succeeded" in line[0] and
              "0 failed" in line[0], (line[0] if line else r.stdout[-300:]) + (" | " + rate[0] if rate else ""))
    finally:
        proxy.kill()
        server.kill()


def haproxy(scratch):
    upstream = "127.0.0.1" if LINUX else "host.docker.internal"
    v1, v2 = (HAPROXY, HAPROXY + 1) if LINUX else (8001, 8002)
    cfg = pathlib.Path(scratch) / "haproxy.cfg"
    cfg.write_text(f"""global
    log stdout format raw local0
defaults
    mode http
    timeout connect 5s
    timeout client 30s
    timeout server 30s
frontend v1
    bind :{v1}
    http-request set-header x-haproxy-src %[src]
    default_backend v1
frontend v2
    bind :{v2}
    http-request set-header x-haproxy-src %[src]
    default_backend v2
backend v1
    server p {upstream}:{PPFRONT} send-proxy
backend v2
    server p {upstream}:{PPFRONT} send-proxy-v2
""")
    name = f"cw-haproxy-{os.getpid()}"
    network = ["--network", "host"] if LINUX else ["-p", f"{HAPROXY}:8001", "-p", f"{HAPROXY + 1}:8002"]
    run = subprocess.run(["docker", "run", "-d", "--rm", "--name", name, *network,
                          "-v", f"{cfg}:/usr/local/etc/haproxy/haproxy.cfg:ro", "haproxy:3.0"],
                         capture_output=True, text=True)
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
        trailers()
        half_close()
        backpressure(proxy)
        big(args.gib, scratch)
    finally:
        proxy.kill()
    limited(scratch)
    load()
    load_h2_upstream()
    tunnel()
    if not args.skip_haproxy:
        haproxy(scratch)
    backend.shutdown()
    print(f"proxy gate: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
