"""Client interop for web_client (Web ADR 0009 §5), through examples/fetch over real TCP against servers the client
did not come from: Python's http.server (plain and over Python's ssl), and examples/https (web_server + web_tls).

The expected outcomes come from RFC 9110 §15.4 (redirect methods), RFC 9112 §6-7 (framing, chunked, keep-alive) and
Web ADR 0009 §3.4-3.7 (reuse, retry, the redirect limit), and are observed on the server's side where they can be.

Build both examples first (cd examples/fetch && ceesafec build; cd examples/https && ceesafec build), then:
    python3 tests/interop/client.py
"""
import http.server, os, pathlib, ssl, subprocess, sys, threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
CERTS = ROOT / "packages/web_tls/tests/certs"
FETCH = ROOT / "examples/fetch/out/fetch"
HTTPS = ROOT / "examples/https/out/https"
HTTPS_PORT = 18444

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


class Log:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = []  # (connection id, method, path, host, body)
        self.connections = 0

    def connection(self):
        with self.lock:
            self.connections += 1
            return self.connections

    def add(self, entry):
        with self.lock:
            self.requests.append(entry)

    def reset(self):
        with self.lock:
            self.requests = []
            self.connections = 0


def handler_for(log, other_origin):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.cid = log.connection()

        def log_message(self, *a):
            pass

        def body(self):
            n = int(self.headers.get("content-length") or 0)
            return self.rfile.read(n) if n else b""

        def reply(self, status, data=b"", headers=(), close=False):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            if close:
                self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(data)
            if close:
                self.close_connection = True

        def handle_any(self):
            data = self.body()
            log.add((self.cid, self.command, self.path, self.headers.get("host"), data))
            p = self.path
            if p == "/hello":
                return self.reply(200, b"hello")
            if p == "/method":  # what arrived: the method and the body
                return self.reply(200, self.command.encode() + b" " + data)
            if p == "/chunked":
                self.send_response(200)
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                for part in (b"hello", b", ", b"chunked world"):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                self.wfile.write(b"0\r\n\r\n")
                return
            if p == "/eof":  # no framing: the body ends with the connection (RFC 9112 §6.3 rule 8)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"until the end")
                self.close_connection = True
                return
            if p == "/closes":  # keep-alive by its head, then the server closes anyway: a reuse race
                self.reply(200, b"closing")
                self.close_connection = True
                return
            if p.startswith("/r") and p[2:5].isdigit():  # /r301 ... /r308 → /method
                return self.reply(int(p[2:5]), b"moved", [("location", "/method")])
            if p == "/away":
                return self.reply(302, b"", [("location", f"http://127.0.0.1:{other_origin()}/hello")])
            if p == "/loop":
                return self.reply(302, b"", [("location", "/loop")])
            return self.reply(404, b"no")

        do_GET = do_POST = do_PUT = do_HEAD = handle_any

    return H


def serve(log, other_origin, tls=None):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_for(log, other_origin))
    if tls:
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.load_cert_chain(str(CERTS / f"{tls}.pem"), str(CERTS / f"{tls}.key"))
        s.socket = c.wrap_socket(s.socket, server_side=True)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def fetch(url, method="GET", body=None, repeat=1, ca=CERTS / "ca.pem"):
    env = dict(os.environ, CW_URL=url, CW_METHOD=method, CW_REPEAT=str(repeat))
    env.pop("CW_BODY", None)
    if body is not None:
        env["CW_BODY"] = body
    if ca:
        env["CW_CA"] = str(ca)
    r = subprocess.run([str(FETCH)], env=env, capture_output=True, timeout=60)
    return r.stdout.decode(errors="replace").splitlines()


def main():
    a_log, b_log = Log(), Log()
    b = serve(b_log, lambda: 0)
    a = serve(a_log, lambda: b.server_address[1])
    A = f"http://127.0.0.1:{a.server_address[1]}"
    B = b.server_address[1]

    out = fetch(A + "/hello")
    check("GET", out == [f"fetch: 200 {A}/hello", "hello"], out)
    check("GET: Host is the authority", a_log.requests and a_log.requests[0][3] == f"127.0.0.1:{a.server_address[1]}",
          a_log.requests[:1])

    out = fetch(A + "/chunked")
    check("chunked body", out[1:] == ["hello, chunked world"], out)
    out = fetch(A + "/eof")
    check("body to the end of the connection", out[1:] == ["until the end"], out)
    out = fetch(A + "/method", "POST", "a body")
    check("POST with a body", out[1:] == ["POST a body"], out)

    a_log.reset()
    out = fetch(A + "/hello", repeat=3)
    check("three GETs", out.count("hello") == 3, out)
    check("reuse: one connection for three requests", a_log.connections == 1, a_log.connections)

    a_log.reset()
    out = fetch(A + "/closes", repeat=2)
    check("reuse race: both GETs succeed", out.count("closing") == 2, out)
    check("reuse race: a second connection", a_log.connections == 2, a_log.connections)

    # RFC 9110 §15.4: 303 → GET; 301 and 302 after a POST → GET (the owner's choice, ADR 0009 §3.7); 307 and 308
    # keep the method and the body.
    for status, want in ((301, "GET "), (302, "GET "), (303, "GET "), (307, "POST x=1"), (308, "POST x=1")):
        a_log.reset()
        out = fetch(f"{A}/r{status}", "POST", "x=1")
        check(f"POST then {status}", out == [f"fetch: 200 {A}/method", want], out)
        check(f"POST then {status}: no resend of the first", [r[1] for r in a_log.requests] == ["POST", want.split()[0]],
              [r[1] for r in a_log.requests])

    b_log.reset()
    out = fetch(A + "/away")
    check("cross-origin redirect", out == [f"fetch: 200 http://127.0.0.1:{B}/hello", "hello"], out)
    check("cross-origin redirect: Host is the new authority", b_log.requests and b_log.requests[0][3] == f"127.0.0.1:{B}",
          b_log.requests[:1])

    a_log.reset()
    out = fetch(A + "/loop")
    check("redirect limit: the last 302 is the answer", out[:1] == [f"fetch: 302 {A}/loop"], out)
    check("redirect limit: 1 + 10 requests", len(a_log.requests) == 11, len(a_log.requests))

    out = fetch("http://127.0.0.1:1/hello")
    check("connection refused", out and out[0].startswith("fetch: failed:"), out)
    out = fetch("http://name.invalid/hello")
    # CeeSafe 0.10.0 (ADR 0060, upstream U15): a name that does not resolve is IoError.HostNotFound.
    check("DNS failure", out and out[0] == "fetch: failed: Host not found", out)

    # TLS against Python's ssl (a TLS stack the client did not come from), verified as localhost by the test CA.
    t_log = Log()
    t = serve(t_log, lambda: 0, tls="server")
    T = f"https://localhost:{t.server_address[1]}"
    out = fetch(T + "/chunked")
    check("https (Python ssl): chunked", out == [f"fetch: 200 {T}/chunked", "hello, chunked world"], out)
    t_log.reset()
    out = fetch(T + "/hello", repeat=3)
    check("https (Python ssl): reuse", out.count("hello") == 3 and t_log.connections == 1, (out, t_log.connections))
    out = fetch(T + "/hello", ca=CERTS / "other-ca.pem")
    check("https: an unknown CA is refused", out and out[0].startswith("fetch: failed:"), out)
    w = serve(Log(), lambda: 0, tls="wronghost")  # example.com only
    out = fetch(f"https://localhost:{w.server_address[1]}/hello")
    check("https: a certificate without the name is refused", out and out[0].startswith("fetch: failed:"), out)

    # TLS against examples/https (web_server over web_tls).
    env = dict(os.environ, CW_PORT=str(HTTPS_PORT), CW_CERT=str(CERTS / "server.pem"), CW_KEY=str(CERTS / "server.key"))
    proc = subprocess.Popen([str(HTTPS)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ready = proc.stdout.readline().decode().strip()
        check("examples/https starts", ready == "https: listening", ready)
        H = f"https://localhost:{HTTPS_PORT}"
        out = fetch(H + "/", repeat=2)
        check("https (examples/https): GET twice", out == [f"fetch: 200 {H}/", "hello, tls"] * 2, out)
        out = fetch(H + "/echo", "POST", "over tls")
        check("https (examples/https): POST", out[1:] == ["over tls"], out)
    finally:
        proc.kill()
        proc.wait()

    for s in (a, b, t, w):
        s.shutdown()
    print(f"client interop: {sum(checks)}/{len(checks)} passed")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
