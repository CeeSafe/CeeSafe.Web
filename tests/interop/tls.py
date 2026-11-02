"""TLS interop for web_tls (Web ADR 0002 §5), through examples/https over real TCP, with three independent clients:
Python's ssl module (its own OpenSSL build), curl, and the openssl s_client CLI.

Build the example first (cd examples/https && ceesafec build), then run it with each Python whose TLS library differs
(on macOS: the system's, LibreSSL 2.8, TLS 1.2 only; and Homebrew's, OpenSSL 3):
    python3 tests/interop/tls.py [--openssl PATH] [path to the server]
"""
import argparse, concurrent.futures, http.client, os, pathlib, shutil, socket, ssl, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CERTS = ROOT / "packages/web_tls/tests/certs"
PORT = 18443

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


def context(minimum=None, maximum=None, ciphers=None, alpn=("http/1.1",)):
    c = ssl.create_default_context(cafile=str(CERTS / "ca.pem"))
    if minimum:
        c.minimum_version = minimum
    if maximum:
        c.maximum_version = maximum
    if ciphers:
        c.set_ciphers(ciphers)
    if alpn:
        c.set_alpn_protocols(list(alpn))
    return c


def handshake(ctx, host="localhost"):
    """The negotiated (version, cipher, alpn), or the exception's text."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                return s.version(), s.cipher()[0], s.selected_alpn_protocol()
    except (ssl.SSLError, OSError) as e:
        return f"refused: {e}"


def request(conn, method, path, body=None):
    conn.request(method, path, body=body)
    r = conn.getresponse()
    return r.status, r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--openssl", default="/opt/homebrew/opt/openssl@3/bin/openssl")
    ap.add_argument("server", nargs="?", default=str(ROOT / "examples/https/out/https"))
    args = ap.parse_args()
    env = dict(os.environ, CW_PORT=str(PORT), CW_CERT=str(CERTS / "server.pem"), CW_KEY=str(CERTS / "server.key"))
    proc = subprocess.Popen([args.server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        run(args, proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    flood(args.server, True)
    our_client(args)
    sys.exit(0 if all(checks) else 1)


GO = ROOT / "tests/interop/go"


def go_binary():
    go = shutil.which("go")
    if not go:
        return None
    out = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "cw-gointerop"
    subprocess.run([go, "build", "-o", str(out), "."], cwd=GO, check=True)
    return str(out)


def go_client(args):
    """Go's crypto/tls as the client: TLS 1.3; TLS 1.2 with each ECDHE-ECDSA suite Go has (the AEAD ones accepted, CBC
    refused); Go's insecure ECDHE-ECDSA suites refused."""
    g = go_binary()
    if not g:
        print("     (no go)")
        return
    lines = subprocess.run([g, "client", str(PORT), str(CERTS / "ca.pem")], capture_output=True, text=True,
                           timeout=60).stdout.splitlines()
    tls13 = [l for l in lines if l.startswith("tls13 ")]
    check("go: TLS 1.3", tls13 and " ok TLS 1.3 " in tls13[0] and "alpn=http/1.1 answered=true" in tls13[0], tls13)
    aead = [l for l in lines if l.startswith("tls12:") and ("GCM" in l.split()[0] or "CHACHA20" in l.split()[0])]
    check("go: every TLS 1.2 ECDHE-ECDSA AEAD suite", aead and all(" ok TLS 1.2 " in l and "answered=true" in l
                                                                    for l in aead), [l.split()[0] for l in aead])
    weak = [l for l in lines if (l.startswith("tls12:") and l not in aead) or l.startswith("weak:")]
    check("go: CBC and RC4 suites refused", weak and all(" refused " in l for l in weak), len(weak))


def testssl(args):
    """testssl.sh against the server: protocols (-p), cipher categories (-s), forward secrecy (-f) and known
    vulnerabilities (-U), with no finding at LOW or above. Not the certificate checks (-S) or HTTP headers (-h): the
    test PKI is generated for tests (no revocation URLs, a 100-year lifetime), and HSTS is the application's."""
    ts = shutil.which("testssl.sh")
    if not ts:
        print("     (no testssl.sh)")
        return
    out = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "cw-testssl.json"
    out.unlink(missing_ok=True)
    subprocess.run([ts, "--quiet", "--color", "0", "--warnings", "off", "-p", "-s", "-f", "-U", "--add-ca", str(CERTS / "ca.pem"),
                    "--ip", "127.0.0.1", "--jsonfile", str(out), f"localhost:{PORT}"], capture_output=True, timeout=600)
    import json
    findings = json.loads(out.read_text())
    bad = [f"{f['id']}: {f['finding']}" for f in findings if f.get("severity") in ("LOW", "MEDIUM", "HIGH", "CRITICAL")]
    for b in bad:
        print(f"     testssl: {b}")
    check("testssl.sh: no finding at LOW or above", not bad, f"{len(findings)} results, {len(bad)} at LOW or above")


def our_client(args):
    """web_tls as the client (examples/tls_get) against openssl s_server and Go's server."""
    get = ROOT / "examples/tls_get/out/tls_get"
    if not get.exists():
        print("     (examples/tls_get not built)")
        return
    port = PORT + 2

    def fetch(host="localhost"):
        env = dict(os.environ, CW_PORT=str(port), CW_CA=str(CERTS / "ca.pem"), CW_HOST=host)
        return subprocess.run([str(get)], env=env, capture_output=True, text=True, timeout=20).stdout

    def serve(cmd):
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        return p

    o = args.openssl
    for flag, version in (("-tls1_3", "TLS 1.3"), ("-tls1_2", "TLS 1.2")):
        p = serve([o, "s_server", "-accept", str(port), "-cert", str(CERTS / "server.pem"), "-key",
                   str(CERTS / "server.key"), "-alpn", "http/1.1", "-www", flag, "-naccept", "2"])
        out = fetch()
        p.kill()
        check(f"tls_get against s_server {flag}", f"tls_get: {version} alpn=http/1.1" in out and "200 ok" in out,
              out.splitlines()[:2])
    p = serve([o, "s_server", "-accept", str(port), "-cert", str(CERTS / "stranger.pem"), "-key",
               str(CERTS / "stranger.key"), "-www", "-naccept", "2"])
    out = fetch()
    p.kill()
    check("tls_get refuses s_server's unknown CA", "tls_get: failed" in out and "certificate" in out.lower(),
          out.strip().splitlines()[-1:])
    g = go_binary()
    if g:
        p = serve([g, "server", str(port), str(CERTS / "server.pem"), str(CERTS / "server.key")])
        out = fetch()
        p.kill()
        check("tls_get against Go's server", "tls_get: TLS 1.3 alpn=http/1.1" in out and "hello from go" in out,
              out.splitlines()[:1])


def renegotiate(sc):
    """A TLS 1.2 client asks to renegotiate (s_client's R command; not with -ign_eof, which turns commands off): the
    server refuses (SSL_OP_NO_RENEGOTIATION sends a no_renegotiation alert), and the client gives up the connection."""
    p = subprocess.Popen(sc + ["-tls1_2"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(1)
    try:
        p.stdin.write(b"R\n")
        p.stdin.flush()
        time.sleep(1)
        p.stdin.close()
    except BrokenPipeError:
        pass
    p.wait(timeout=10)
    text = p.stdout.read().decode(errors="replace")
    check("renegotiation refused", "RENEGOTIATING" in text and "no renegotiation" in text and p.returncode != 0,
          [l for l in text.splitlines() if "renegotiation" in l][:1])


def flood(server, openssl_ok):
    """200 connections that never finish a handshake, against 64 workers and a 1 s handshake deadline: each is closed
    by its deadline (in at most 4 rounds of 64), memory stays bounded, and a real client is served afterwards."""
    port = PORT + 1
    env = dict(os.environ, CW_PORT=str(port), CW_CERT=str(CERTS / "server.pem"), CW_KEY=str(CERTS / "server.key"),
               CW_WORKERS="64", CW_HANDSHAKE_MS="1000")
    proc = subprocess.Popen([server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        time.sleep(1.5)  # the readiness probe's own connection times out first
        before = rss_kib(proc.pid)
        socks = []
        refused = 0
        start = time.time()
        for i in range(200):
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=10)
                if i % 2:
                    s.sendall(bytes([22, 3, 1, 2, 0, 1, 0, 1, 252, 3, 3]))  # a ClientHello's first 11 of 517 bytes
                socks.append(s)
            except OSError:
                refused += 1  # the kernel's listen backlog is full: admission, outside the server
        peak = before
        open_ = set(range(len(socks)))
        for s in socks:
            s.setblocking(False)
        while open_ and time.time() - start < 10:
            for i in list(open_):
                try:
                    if socks[i].recv(4096) == b"":
                        open_.discard(i)
                except BlockingIOError:
                    pass
                except OSError:
                    open_.discard(i)  # reset
            peak = max(peak, rss_kib(proc.pid))
            time.sleep(0.05)
        took = time.time() - start
        for s in socks:
            s.close()
        check("flood: every stalled handshake closed by its deadline", not open_ and took < 6,
              f"{len(socks) - len(open_)} of {len(socks)} accepted closed in {took:.1f} s (at most 4 rounds of 64 at 1 s);"
              f" {refused} refused by the kernel's backlog")
        check("flood: memory bounded", peak - before < 16 * 1024, f"RSS {before} KiB before, {peak} KiB at most")
        conn = http.client.HTTPSConnection("localhost", port, context=context(), timeout=10)
        check("flood: a real client is served afterwards", request(conn, "GET", "/") == (200, b"hello, tls"))
        conn.close()
    finally:
        proc.kill()


def rss_kib(pid):
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip())


def run(args, proc):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            break
        except OSError:
            time.sleep(0.05)

    # ---- Python's ssl ----
    print(f"     client: Python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}")
    got = handshake(context())
    best = "TLSv1.3" if ssl.HAS_TLSv1_3 else "TLSv1.2"  # LibreSSL 2.8 (macOS's Python 3.9) has no TLS 1.3
    check(f"{best}, ALPN http/1.1", isinstance(got, tuple) and got[0] == best and got[2] == "http/1.1", got)
    got = handshake(context(maximum=ssl.TLSVersion.TLSv1_2))
    check("TLS 1.2 with ECDHE and an AEAD", isinstance(got, tuple) and got[0] == "TLSv1.2" and got[1].startswith("ECDHE")
          and ("GCM" in got[1] or "CHACHA20" in got[1]), got)
    weak = context(maximum=ssl.TLSVersion.TLSv1_2, ciphers="ECDHE-ECDSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA")
    got = handshake(weak)
    check("TLS 1.2 CBC suites refused", isinstance(got, str), got)
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.load_verify_locations(str(CERTS / "ca.pem"))
    try:
        import warnings
        warnings.simplefilter("ignore", DeprecationWarning)  # offering TLS 1.1 is the point
        old.minimum_version = ssl.TLSVersion.TLSv1_1
        old.maximum_version = ssl.TLSVersion.TLSv1_1
        old.set_ciphers("ALL:@SECLEVEL=0")
        got = handshake(old)
        check("TLS 1.1 refused", isinstance(got, str), got)
    except (ValueError, ssl.SSLError) as e:
        print(f"     (this Python cannot offer TLS 1.1: {e})")
    got = handshake(context(alpn=None))
    check("no ALPN offered: still served", isinstance(got, tuple) and got[2] is None, got)

    conn = http.client.HTTPSConnection("localhost", PORT, context=context(), timeout=10)
    replies = [request(conn, "GET", "/") for _ in range(5)]
    check("five requests on one connection", replies == [(200, b"hello, tls")] * 5, replies[0])
    body = os.urandom(1 << 20)
    status, echoed = request(conn, "POST", "/echo", body)
    check("1 MiB echo", status == 200 and echoed == body, (status, len(echoed)))
    conn.close()

    def client(_):
        c = http.client.HTTPSConnection("localhost", PORT, context=context(), timeout=10)
        out = [request(c, "GET", "/") for _ in range(5)]
        c.close()
        return out

    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        results = [r for rs in pool.map(client, range(32)) for r in rs]
    check("32 connections x 5 requests, 16 at once", results == [(200, b"hello, tls")] * 160, len(results))

    # ---- curl ----
    curl = shutil.which("curl")
    if curl:
        base = [curl, "-sS", "--cacert", str(CERTS / "ca.pem"), "--resolve", f"localhost:{PORT}:127.0.0.1"]
        url = f"https://localhost:{PORT}"
        r = subprocess.run(base + ["-w", "|%{http_version}|%{ssl_verify_result}", f"{url}/"], capture_output=True)
        check("curl GET", r.returncode == 0 and r.stdout == b"hello, tls|1.1|0", r.stdout or r.stderr)
        r = subprocess.run(base + ["--data-binary", "@-", f"{url}/echo"], input=body, capture_output=True)
        check("curl 1 MiB echo", r.returncode == 0 and r.stdout == body, (r.returncode, len(r.stdout)))
        r = subprocess.run([curl, "-sS", f"{url}/", "--resolve", f"localhost:{PORT}:127.0.0.1"], capture_output=True)
        check("curl without the CA refuses the server", r.returncode != 0, r.stderr.decode().strip()[:80])
    else:
        print("     (no curl)")

    # ---- openssl s_client ----
    o = args.openssl
    if os.path.exists(o):
        req = b"GET / HTTP/1.1\r\nhost: localhost\r\nconnection: close\r\n\r\n"
        sc = [o, "s_client", "-connect", f"127.0.0.1:{PORT}", "-servername", "localhost",
              "-verify_hostname", "localhost", "-CAfile", str(CERTS / "ca.pem"), "-verify_return_error", "-ign_eof"]
        r = subprocess.run(sc + ["-alpn", "http/1.1"], input=req, capture_output=True, timeout=10)
        text = (r.stdout + r.stderr).decode(errors="replace")
        check("s_client: verified, TLS 1.3, http/1.1, answered",
              "Verify return code: 0 (ok)" in text and "New, TLSv1.3" in text and "ALPN protocol: http/1.1" in text
              and "hello, tls" in text, [l.strip() for l in text.splitlines() if "Negotiated TLS1.3 group" in l])
        if not checks[-1]:
            print(text)
        r = subprocess.run(sc + ["-tls1_2", "-brief"], input=req, capture_output=True, timeout=10)
        text = (r.stdout + r.stderr).decode(errors="replace")
        check("s_client TLS 1.2", "TLSv1.2" in text and "hello, tls" in text, [l for l in text.splitlines() if "Protocol" in l])
        r = subprocess.run(sc + ["-tls1_2", "-cipher", "AES128-SHA256:AES256-SHA:ECDHE-ECDSA-AES128-SHA"], input=req,
                           capture_output=True, timeout=10)
        check("s_client: non-AEAD TLS 1.2 suites refused", r.returncode != 0 and b"hello, tls" not in r.stdout,
              r.returncode)
        renegotiate([o, "s_client", "-connect", f"127.0.0.1:{PORT}", "-servername", "localhost", "-CAfile",
                     str(CERTS / "ca.pem")])
    else:
        print(f"     (no {o})")

    go_client(args)
    testssl(args)

    conn = http.client.HTTPSConnection("localhost", PORT, context=context(), timeout=10)
    check("still serving after the refused and failed handshakes", request(conn, "GET", "/") == (200, b"hello, tls"))
    request(conn, "POST", "/stop")
    conn.close()
    try:
        out, _ = proc.communicate(timeout=10)
        check("drained and exited 0", proc.returncode == 0 and b"https: drained" in out, (proc.returncode, out))
    except subprocess.TimeoutExpired:
        check("drained and exited 0", False, "timeout")


if __name__ == "__main__":
    main()
