#!/usr/bin/env python3
"""M9's request-rate benchmark (Web ADR 0013 §3.1), against benchmarks/http's server.

usage: python3 benchmarks/http/run.py [--duration 10] [--conns 1,10,100] [--modes h1,h2c,tls-h1,tls-h2]
                                      [--routes small,4k,64k,stream,echo,file] [--json out.json] [--nginx]

For each mode (HTTP/1.1 and h2c in clear, HTTP/1.1 and h2 over TLS), route and connection count, h2load runs for
`duration` seconds after a one-second warm-up, one request at a time on each connection (`-m 1`, so HTTP/2 is not
favoured by multiplexing). Every request's latency comes from h2load's `--log-file`, so the percentiles are this
script's own, from the raw samples. Reported:
- requests a second, and latency p50, p99 and p99.9 in microseconds;
- the server's peak RSS during the run (`ps`, polled every 100 ms);
- allocations per request: two runs of the server at 10 connections, a warm-up's requests and the warm-up's plus a
  count's (`COUNTS`), each ending with the server's `CEESAFE_ALLOC_REPORT` line; the difference divided by the count.
The server is built first (`ceesafec build`, with the ceesafec on PATH unless --ceesafec). TLS uses a self-signed
P-256 certificate made with `openssl` in a temporary directory.

`/file` is a 1 MiB file sent with `Exchange.sendFile` (Web ADR 0013 §3.3; the server's CW_FILE), the same bytes as
`/stream`, which the handler writes from memory. The route `file1g` is the same `/file` with CW_FILE a 1 GiB file
(ADR 0013 §3.6); it is not in the default routes, and its rows add `mib_per_s` (bytes of whole responses a second).

The reactor is the runtime's choice: CeeSafe's default, or io_uring when CEESAFE_REACTOR_URING=1 is in the
environment, which the server inherits. Each row records which (`reactor`).

`--nginx` runs the same routes against nginx (on PATH), one worker per core, `sendfile on`, the bodies as files
(`/stream` as a 1 MiB file, `/echo` left out): a reference, not a target. Allocations do not apply to it.
"""
import argparse, json, os, pathlib, shutil, socket, ssl, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent
SERVER = HERE / "out" / "bench_http"
PORT = 18190
# The allocation runs: (warm-up, counted) requests per route; the large bodies need fewer.
COUNTS = {"small": (2000, 20000), "4k": (2000, 20000), "64k": (500, 5000), "stream": (100, 1000),
          "echo": (2000, 20000), "file": (100, 1000), "file1g": (2, 10)}
ROUTES = {"small": "/small", "4k": "/4k", "64k": "/64k", "stream": "/stream", "echo": "/echo", "file": "/file",
          "file1g": "/file"}
GIBIBYTE = 1 << 30
REACTOR = "io_uring" if os.environ.get("CEESAFE_REACTOR_URING") == "1" else "default"


def served(scratch, route):
    """The file CW_FILE names for `route`."""
    return scratch / ("file1g" if route == "file1g" else "file")


def gibibyte(path):
    """The 1 GiB file `file1g` serves: the 1 MiB alphabet file 1024 times, written a mebibyte at a time."""
    piece = bytes(97 + i % 26 for i in range(1048576))
    with open(path, "wb") as f:
        for _ in range(GIBIBYTE // len(piece)):
            f.write(piece)
    return path


def mebibyte(path):
    """The 1 MiB file `/file` serves: the alphabet `/stream` repeats."""
    path.write_bytes(bytes(97 + i % 26 for i in range(1048576)))
    return path


def build(ceesafec):
    r = subprocess.run([ceesafec, "build"], cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("build failed:\n" + r.stdout + r.stderr)


def certificate(scratch):
    cert, key = scratch / "cert.pem", scratch / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    return cert, key


def wait_port(port, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    sys.exit(f"nothing listening on {port}")


class Server:
    def __init__(self, tls, scratch, alloc=False, route=None):
        env = dict(os.environ, CW_PORT=str(PORT), CW_WORKERS="256", CW_FILE=str(served(scratch, route)))
        if alloc:
            env["CEESAFE_ALLOC_REPORT"] = "1"
        if tls:
            env["CW_CERT"], env["CW_KEY"] = (str(p) for p in tls)
        self.p = subprocess.Popen([str(SERVER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        line = self.p.stdout.readline().decode().strip()
        if line != "bench: listening":
            self.p.kill()
            sys.exit(f"server did not start: {line}")

    def stop(self, url):
        raw = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        if url.startswith("https"):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.set_alpn_protocols(["http/1.1"])
            raw = context.wrap_socket(raw)
        raw.sendall(b"POST /stop HTTP/1.1\r\nhost: x\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
        try:
            while raw.recv(4096):
                pass
        except OSError:
            pass
        raw.close()
        try:
            out, _ = self.p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            out, _ = self.p.communicate()
        return out.decode()


def base_url(mode):
    scheme = "https" if mode.startswith("tls") else "http"
    return f"{scheme}://127.0.0.1:{PORT}"


class Peak(threading.Thread):
    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid, self.peak, self.done = pid, 0, False

    def run(self):
        while not self.done:
            r = subprocess.run(["ps", "-o", "rss=", "-p", str(self.pid)], capture_output=True, text=True)
            if r.stdout.strip():
                self.peak = max(self.peak, int(r.stdout.strip()))
            time.sleep(0.1)


def h2load(mode, path, conns, duration, scratch, body):
    log = scratch / "latency.log"
    args = ["h2load", "-D", str(duration), "--warm-up-time", "1", "-c", str(conns), "-m", "1",
            "--log-file", str(log), *(["--h1"] if mode.endswith("h1") else [])]
    if body:
        args += ["-d", str(body)]
    r = subprocess.run(args + [base_url(mode) + path], capture_output=True, text=True)
    done = failed = 0
    for line in r.stdout.splitlines():
        if line.startswith("requests:"):
            parts = line.replace(",", "").split()
            done, failed = int(parts[parts.index("succeeded") - 1]), int(parts[parts.index("failed") - 1])
    samples = []
    if log.exists():
        for line in log.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "200":
                samples.append(int(fields[2]))
        log.unlink()
    samples.sort()

    def pct(q):
        return samples[min(len(samples) - 1, int(q * len(samples)))] if samples else None
    return {"requests_per_s": round(done / duration), "succeeded": done, "failed": failed,
            "p50_us": pct(0.50), "p99_us": pct(0.99), "p999_us": pct(0.999), "samples": len(samples)}


def allocations(mode, route, tls, scratch, body):
    path = ROUTES[route]
    warm, count = COUNTS[route]
    counts = []
    for n in (warm, warm + count):
        s = Server(tls, scratch, alloc=True, route=route)
        args = ["h2load", "-n", str(n), "-c", "10", "-m", "1", *(["--h1"] if mode.endswith("h1") else [])]
        if body:
            args += ["-d", str(body)]
        subprocess.run(args + [base_url(mode) + path], capture_output=True)
        out = s.stop(base_url(mode))
        # With TLS a second line reports OpenSSL's held allocations (`foreign held=`); the count is `attempts=`.
        line = [l for l in out.splitlines() if l.startswith("ceesafe alloc:") and "attempts=" in l]
        if not line:
            return None
        counts.append(int(line[-1].split("attempts=")[1].split()[0]))
    # The stop request is in both runs, so it cancels out.
    return round((counts[1] - counts[0]) / count, 2)


def nginx_config(scratch, root):
    conf = scratch / "nginx.conf"
    conf.write_text(f"""worker_processes auto;
daemon off;
error_log {scratch}/nginx-error.log;
pid {scratch}/nginx.pid;
events {{ worker_connections 1024; }}
http {{
    access_log off;
    sendfile on;
    tcp_nopush on;
    keepalive_requests 1000000;
    default_type application/octet-stream;
    server {{
        listen 127.0.0.1:{PORT};
        listen 127.0.0.1:{PORT + 1} ssl;
        http2 on;
        ssl_certificate {scratch}/cert.pem;
        ssl_certificate_key {scratch}/key.pem;
        root {root};
    }}
}}
""")
    return conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=10)
    ap.add_argument("--conns", default="1,10,100")
    ap.add_argument("--modes", default="h1,h2c,tls-h1,tls-h2")
    ap.add_argument("--routes", default="small,4k,64k,stream,echo,file")
    ap.add_argument("--ceesafec", default="ceesafec")
    ap.add_argument("--json")
    ap.add_argument("--nginx", action="store_true")
    ap.add_argument("--no-alloc", action="store_true")
    args = ap.parse_args()
    conns = [int(c) for c in args.conns.split(",")]
    modes, routes = args.modes.split(","), args.routes.split(",")
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="cw-bench-"))
    tls = certificate(scratch)
    body = scratch / "body"
    body.write_bytes(b"x" * 4096)
    mebibyte(scratch / "file")
    if "file1g" in routes:
        gibibyte(scratch / "file1g")
    results = []
    try:
        if args.nginx:
            run_nginx(args, modes, routes, conns, scratch, results)
        else:
            build(args.ceesafec)
            for mode in modes:
                for route in routes:
                    posted = body if route == "echo" else None
                    for c in conns:
                        s = Server(tls if mode.startswith("tls") else None, scratch, route=route)
                        peak = Peak(s.p.pid)
                        peak.start()
                        r = h2load(mode, ROUTES[route], c, args.duration, scratch, posted)
                        peak.done = True
                        s.stop(base_url(mode))
                        r.update(server="ceesafe.web", mode=mode, route=route, conns=c, peak_rss_kib=peak.peak,
                                 reactor=REACTOR)
                        if route == "file1g":
                            r["mib_per_s"] = round(r["succeeded"] * GIBIBYTE / args.duration / 1048576)
                        results.append(r)
                        print(row(r), flush=True)
                    if not args.no_alloc:
                        a = allocations(mode, route, tls if mode.startswith("tls") else None, scratch,
                                        posted)
                        results.append({"server": "ceesafe.web", "mode": mode, "route": route,
                                        "reactor": REACTOR, "allocations_per_request": a})
                        print(f"| {mode} | {route} | allocations per request | {a} |", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=1))


def run_nginx(args, modes, routes, conns, scratch, results):
    global PORT
    root = scratch / "root"
    root.mkdir()
    (root / "small").write_bytes(b"hello, world\n")
    for name, size in (("4k", 4096), ("64k", 65536), ("stream", 1048576), ("file", 1048576)):
        (root / name).write_bytes(bytes(97 + i % 26 for i in range(size)))
    if "file1g" in routes:
        gibibyte(root / "file1g")
    conf = nginx_config(scratch, root)
    p = subprocess.Popen(["nginx", "-c", str(conf), "-p", str(scratch)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    plain = PORT
    try:
        wait_port(plain)
        wait_port(plain + 1)
        for mode in modes:
            PORT = plain + 1 if mode.startswith("tls") else plain
            for route in routes:
                if route == "echo":
                    continue
                for c in conns:
                    path = "/file1g" if route == "file1g" else ROUTES[route]
                    r = h2load(mode, path, c, args.duration, scratch, None)
                    r.update(server="nginx", mode=mode, route=route, conns=c)
                    if route == "file1g":
                        r["mib_per_s"] = round(r["succeeded"] * GIBIBYTE / args.duration / 1048576)
                    results.append(r)
                    print(row(r), flush=True)
    finally:
        PORT = plain
        p.terminate()
        p.wait()


def row(r):
    return (f"| {r['server']} | {r.get('reactor', '')} | {r['mode']} | {r['route']} | {r['conns']} | "
            f"{r['requests_per_s']} | {r.get('mib_per_s', '')} | {r['p50_us']} | {r['p99_us']} | {r['p999_us']} | "
            f"{r.get('peak_rss_kib', '')} | {r['failed']} |")


if __name__ == "__main__":
    main()
