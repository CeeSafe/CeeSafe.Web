"""`Exchange.sendFile` end to end (Web ADR 0013 §5): a file through benchmarks/http's `/file` route, over real TCP.

usage: python3 tests/interop/sendfile.py [--size-mib 1024] [--ceesafec ceesafec]

- Every path: the file (random bytes) over HTTP/1.1, TLS with HTTP/1.1, h2c and h2, fetched by curl; the SHA-256 of
  what curl received must equal the SHA-256 of the file, computed here with hashlib (not by the server).
- Lengths: the response's content-length is the file's size.
- A file truncated while it is sent, over each path: curl must not succeed. RFC 9112 §6.3 and RFC 9113 §8.1 make a
  body shorter than its content-length incomplete; curl reports it as 18 (partial file) or 92 (HTTP/2 stream error),
  or 56 (a receive failure) when the connection ends under it.
"""
import argparse, hashlib, os, pathlib, shutil, socket, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks/http"
PORT = 18290
# curl built on OpenSSL with nghttp2 (Homebrew's on macOS: the system's LibreSSL 3.3 refuses the P-256 certificate).
CURL = next((c for c in ("/opt/homebrew/opt/curl/bin/curl", shutil.which("curl")) if c and os.path.exists(c)), "curl")
checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}", flush=True)


def wait_port(port):
    end = time.time() + 10
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    sys.exit(f"nothing listening on {port}")


def certificate(scratch):
    cert, key = scratch / "cert.pem", scratch / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    return cert, key


def start(scratch, served, tls):
    env = dict(os.environ, CW_PORT=str(PORT), CW_FILE=str(served))
    if tls:
        env["CW_CERT"], env["CW_KEY"] = (str(p) for p in tls)
    p = subprocess.Popen([str(BENCH / "out/bench_http")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    line = p.stdout.readline().decode().strip()
    if line != "bench: listening":
        p.kill()
        sys.exit(f"server did not start: {line}")
    wait_port(PORT)
    return p


def fetch_args(mode):
    scheme = "https" if mode.startswith("tls") else "http"
    flags = {"h1": ["--http1.1"], "h2c": ["--http2-prior-knowledge"], "tls-h1": ["--http1.1", "-k"],
             "tls-h2": ["--http2", "-k"]}[mode]
    return [CURL, "-sS", "-D", "-", *flags, f"{scheme}://127.0.0.1:{PORT}/file"]


def fetch(mode, rate=None):
    """curl's exit code, the head it printed, the SHA-256 and length of the body."""
    args = fetch_args(mode)
    if rate:
        args[1:1] = ["--limit-rate", rate]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        b = p.stdout.read(1)
        if not b:
            break
        head += b
    h, n = hashlib.sha256(), 0
    while True:
        chunk = p.stdout.read(1 << 20)
        if not chunk:
            break
        h.update(chunk)
        n += len(chunk)
    p.wait()
    return p.returncode, head.decode(errors="replace"), h.hexdigest(), n, p.stderr.read().decode(errors="replace")


def stop(p, tls):
    import ssl
    raw = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    if tls:
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname, c.verify_mode = False, ssl.CERT_NONE
        c.set_alpn_protocols(["http/1.1"])
        raw = c.wrap_socket(raw)
    raw.sendall(b"POST /stop HTTP/1.1\r\nhost: x\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
    try:
        while raw.recv(4096):
            pass
    except OSError:
        pass
    raw.close()
    try:
        p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mib", type=int, default=1024)
    ap.add_argument("--ceesafec", default="ceesafec")
    args = ap.parse_args()
    r = subprocess.run([args.ceesafec, "build"], cwd=BENCH, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("build failed:\n" + r.stdout + r.stderr)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="cw-sendfile-"))
    try:
        tls = certificate(scratch)
        source = scratch / "source"
        size = args.size_mib << 20
        h = hashlib.sha256()
        with open(source, "wb") as f:
            left = size
            while left:
                block = os.urandom(min(left, 1 << 20))
                h.update(block)
                f.write(block)
                left -= len(block)
        digest = h.hexdigest()
        print(f"file: {size} bytes, sha256 {digest}", flush=True)
        for mode in ("h1", "tls-h1", "h2c", "tls-h2"):
            p = start(scratch, source, tls if mode.startswith("tls") else None)
            try:
                code, head, got, n, err = fetch(mode)
                check(f"{mode}: curl succeeded", code == 0, f"exit {code} {err.strip()[:200]}")
                check(f"{mode}: content-length", f"content-length: {size}" in head.lower(), head.split("\r\n")[0])
                check(f"{mode}: {n} bytes, SHA-256 equal", got == digest and n == size, got)
            finally:
                stop(p, mode.startswith("tls"))
        # Truncated while sent: a copy of 256 MiB (at least 16 MiB), cut to a quarter once curl has started.
        part = max(16, min(256, args.size_mib)) << 20
        for mode in ("h1", "tls-h1", "h2c", "tls-h2"):
            victim = scratch / "victim"
            with open(source, "rb") as src, open(victim, "wb") as dst:
                dst.write(src.read(part))
            p = start(scratch, victim, tls if mode.startswith("tls") else None)
            try:
                args_ = fetch_args(mode)
                args_[1:1] = ["--limit-rate", "8M", "-o", str(scratch / "partial")]
                c = subprocess.Popen(args_, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                time.sleep(1.0)
                os.truncate(victim, part // 4)
                c.wait(timeout=120)
                got = (scratch / "partial").stat().st_size if (scratch / "partial").exists() else 0
                check(f"{mode}: truncated file fails the transfer", c.returncode in (18, 56, 92),
                      f"curl exit {c.returncode}, {got} of {part} bytes")
            finally:
                stop(p, mode.startswith("tls"))
                (scratch / "partial").unlink(missing_ok=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    passed = sum(checks)
    print(f"sendfile: {passed}/{len(checks)} passed")
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
