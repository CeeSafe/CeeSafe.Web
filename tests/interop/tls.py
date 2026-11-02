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
    sys.exit(0 if all(checks) else 1)


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
    else:
        print(f"     (no {o})")

    conn = http.client.HTTPSConnection("localhost", PORT, context=context(), timeout=10)
    request(conn, "POST", "/stop")
    conn.close()
    try:
        out, _ = proc.communicate(timeout=10)
        check("drained and exited 0", proc.returncode == 0 and b"https: drained" in out, (proc.returncode, out))
    except subprocess.TimeoutExpired:
        check("drained and exited 0", False, "timeout")


if __name__ == "__main__":
    main()
