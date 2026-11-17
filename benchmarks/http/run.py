#!/usr/bin/env python3
"""M9's request-rate benchmark (Web ADR 0013 §3.1), against benchmarks/http's server.

usage: python3 benchmarks/http/run.py [--duration 10] [--conns 1,10,100] [--modes h1,h2c,tls-h1,tls-h2]
                                      [--routes small,4k,64k,stream,json,echo,file] [--json out.json]
                                      [--nginx | --server hyper|axum|actix|kestrel|h2o|relic|relic-native]

For each mode (HTTP/1.1 and h2c in clear, HTTP/1.1 and h2 over TLS), route and connection count, h2load runs for
`duration` seconds after a one-second warm-up, one request at a time on each connection (`-m 1`, so HTTP/2 is not
favoured by multiplexing). Every request's latency comes from h2load's `--log-file`, so the percentiles are this
script's own, from the raw samples. Reported:
- requests a second, and latency p50, p95, p99 and p99.9 in microseconds;
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

`--server` runs them against one of the comparison servers of Web ADR 0015 §6 (`benchmarks/servers/`, built first
as its README says): Hyper, Axum, Actix Web, Kestrel, h2o, or Relic 2.0 on either adapter (`relic`, `relic-native`).
They serve HTTP/1.1 and h2c (Relic HTTP/1.1 only), not TLS, and have no `/file`; the modes and routes they lack are
left out. Allocations do not apply to them; the peak RSS does.

Before any load, `benchmarks/servers/conform.py` checks the server (CeeSafe.Web's too) on every route over HTTP/1.1
and h2c, against bodies that script builds itself; a server that fails it is not measured (plan §8: correctness
first). `/json` serialises `{"message":"Hello, World!"}` for each request.

`--json` writes `{"environment": …, "results": […]}`. The environment is what Web ADR 0005 §2.4 asks each run to
record: the machine (CPU, threads, memory), the OS and kernel, the load average before the run, the compiler and its
flags (ceesafec, the C compiler, or the comparison server's toolchain and pinned versions), the reactor, the
concurrency, the payloads, the TLS settings, the warm-up and the load generator. `benchmarks/report.py` writes the
report from these files (Web ADR 0015 §6).
"""
import argparse, datetime, json, os, pathlib, platform, re, shutil, socket, ssl, subprocess, sys, tempfile
import threading, time

HERE = pathlib.Path(__file__).resolve().parent
SERVER = HERE / "out" / "bench_http"
PORT = 18190
# The allocation runs: (warm-up, counted) requests per route; the large bodies need fewer.
COUNTS = {"small": (2000, 20000), "4k": (2000, 20000), "64k": (500, 5000), "stream": (100, 1000),
          "json": (2000, 20000), "echo": (2000, 20000), "file": (100, 1000), "file1g": (2, 10)}
ROUTES = {"small": "/small", "4k": "/4k", "64k": "/64k", "stream": "/stream", "json": "/json", "echo": "/echo",
          "file": "/file", "file1g": "/file"}
SERVERS = HERE.parent / "servers"
CONFORM = SERVERS / "conform.py"
# The comparison servers (Web ADR 0015 §6): the command that runs each, and its h2c port's offset from PORT when it
# cannot serve h2c on the HTTP/1.1 port (Kestrel). Each listens on 127.0.0.1:CW_PORT.
COMPARISON = {
    "hyper": [str(SERVERS / "rust" / "target" / "release" / "bench_hyper")],
    "axum": [str(SERVERS / "rust" / "target" / "release" / "bench_axum")],
    "actix": [str(SERVERS / "rust" / "target" / "release" / "bench_actix")],
    "kestrel": [str(SERVERS / "kestrel" / "out" / "bench_kestrel")],
    "h2o": [sys.executable, str(SERVERS / "h2o" / "start.py")],
    "relic": [str(SERVERS / "relic" / "out" / "bundle" / "bin" / "main")],
    "relic-native": [str(SERVERS / "relic" / "out" / "bundle" / "bin" / "main")],
}
H2C_OFFSET = {"kestrel": 1}
# Relic 2.0's two adapters, one binary: relic_io (dart:io) and relic_native (its Zig server). Neither has HTTP/2.
SERVER_ENV = {"relic": {"CW_ADAPTER": "io"}, "relic-native": {"CW_ADAPTER": "native"}}
NO_H2C = {"relic", "relic-native"}
COMPARISON_MODES = ("h1", "h2c")
COMPARISON_ROUTES = ("small", "4k", "64k", "stream", "json", "echo")
PAYLOADS = {"small": "13 bytes", "4k": "4096 bytes", "64k": "65536 bytes",
            "stream": "16 KiB written 64 times, no length", "json": "27 bytes, serialised for each request",
            "echo": "a 4096-byte request body, sent back", "file": "1 MiB by sendfile", "file1g": "1 GiB by sendfile"}
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
    negotiated = {}
    for line in r.stdout.splitlines():
        if line.startswith("requests:"):
            parts = line.replace(",", "").split()
            done, failed = int(parts[parts.index("succeeded") - 1]), int(parts[parts.index("failed") - 1])
        # Over TLS, h2load prints what it negotiated: these are the run's TLS settings.
        for field, key in (("TLS Protocol:", "tls_protocol"), ("Cipher:", "tls_cipher"),
                           ("Server Temp Key:", "tls_key_exchange"), ("Application protocol:", "alpn")):
            if line.startswith(field):
                negotiated[key] = line[len(field):].strip()
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
            "p50_us": pct(0.50), "p95_us": pct(0.95), "p99_us": pct(0.99), "p999_us": pct(0.999),
            "samples": len(samples), **(negotiated if mode.startswith("tls") else {})}


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
    ap.add_argument("--server", choices=sorted(COMPARISON))
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
    name = "nginx" if args.nginx else args.server or "ceesafe.web"
    if args.server:
        run_modes = [m for m in modes if m in COMPARISON_MODES and not (m == "h2c" and name in NO_H2C)]
        run_routes = [r for r in routes if r in COMPARISON_ROUTES]
    else:
        run_modes, run_routes = modes, [r for r in routes if not (args.nginx and r == "echo")]
    if name == "ceesafe.web":
        build(args.ceesafec)  # first, so the environment reads the library the server links
    env = environment(args, name, run_modes, run_routes, conns)
    try:
        if args.nginx:
            run_nginx(args, modes, routes, conns, scratch, results)
        elif args.server:
            run_comparison(args, modes, routes, conns, scratch, body, results)
        else:
            s = Server(None, scratch)
            ok = conform("ceesafe.web", PORT, PORT)
            s.stop(base_url("h1"))
            if not ok:
                sys.exit("run: ceesafe.web failed the conformance check; nothing measured")
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
        pathlib.Path(args.json).write_text(json.dumps({"environment": env, "results": results}, indent=1))


def first_line(command):
    """The first line `command` prints (standard output, else standard error), or None if it cannot run."""
    try:
        r = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (r.stdout.strip() or r.stderr.strip()).splitlines()
    return lines[0].strip() if lines else None


def sysctl(name):
    return first_line(["sysctl", "-n", name])


def machine():
    """The CPU's model, its logical CPUs and the memory in MiB, from /proc on Linux and sysctl elsewhere."""
    cpu = memory = None
    if platform.system() == "Linux":
        info = pathlib.Path("/proc/cpuinfo").read_text()
        m = re.search(r"^(model name|Model)\s*:\s*(.+)$", info, re.M)
        cpu = m.group(2).strip() if m else None
        m = re.search(r"^MemTotal:\s*(\d+) kB", pathlib.Path("/proc/meminfo").read_text(), re.M)
        memory = int(m.group(1)) // 1024 if m else None
    elif platform.system() == "Darwin":
        cpu = sysctl("machdep.cpu.brand_string")
        size = sysctl("hw.memsize")
        memory = int(size) // 1048576 if size and size.isdigit() else None
    else:
        cpu = sysctl("hw.model")
        size = sysctl("hw.physmem")
        memory = int(size) // 1048576 if size and size.isdigit() else None
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "memory_mib": memory, "arch": platform.machine()}


def operating_system():
    if platform.system() == "Linux":
        try:
            m = re.search(r'^PRETTY_NAME="?([^"\n]+)', pathlib.Path("/etc/os-release").read_text(), re.M)
            return m.group(1) if m else "Linux"
        except OSError:
            return "Linux"
    if platform.system() == "Darwin":
        return "macOS " + (first_line(["sw_vers", "-productVersion"]) or "")
    return platform.system() + " " + platform.release()


def lock_version(lock, package):
    """`package`'s version in a Cargo.lock or pubspec.lock, or None."""
    try:
        text = lock.read_text()
    except OSError:
        return None
    m = re.search(r'name = "' + re.escape(package) + r'"\nversion = "([^"]+)"', text)  # Cargo.lock
    if not m:
        m = re.search(r"^  " + re.escape(package) + r":\n(?:    .*\n)*?    version: \"([^\"]+)\"", text, re.M)
    return m.group(1) if m else None


def toolchain(args, name):
    """What built the measured server, and with what flags."""
    if name == "ceesafe.web":
        cc = os.environ.get("CC", "cc")
        return {"ceesafec": first_line([args.ceesafec, "--version"]), "cc": cc, "cc_version": first_line([cc, "--version"]),
                "flags": "ceesafec build's defaults: -std=c11, -O2, -ffp-contract=off -fno-math-errno "
                         "(CeeSafe tooling.md §8.3)"}
    if name in ("hyper", "axum", "actix"):
        lock = SERVERS / "rust" / "Cargo.lock"
        crate = {"hyper": "hyper", "axum": "axum", "actix": "actix-web"}[name]
        return {"rustc": first_line(["rustc", "--version"]), crate: lock_version(lock, crate),
                "tokio": lock_version(lock, "tokio"), "hyper": lock_version(lock, "hyper"),
                "flags": "cargo build --release: opt-level 3, fat LTO, codegen-units 1, panic abort "
                         "(benchmarks/servers/rust/Cargo.toml)"}
    if name == "kestrel":
        return {"dotnet": first_line(["dotnet", "--version"]), "flags": "dotnet publish -c Release"}
    if name == "h2o":
        h2o = os.environ.get("CW_H2O") or str(SERVERS / "h2o" / "out" / "bin" / "h2o")
        commit = re.search(r"^COMMIT=(\w+)", (SERVERS / "h2o" / "build.sh").read_text(), re.M)
        return {"h2o": first_line([h2o, "--version"]), "commit": commit.group(1) if commit else None,
                "flags": "cmake -DCMAKE_BUILD_TYPE=Release -DWITH_MRUBY=ON"}
    if name in ("relic", "relic-native"):
        return {"dart": first_line(["dart", "--version"]),
                "relic": lock_version(SERVERS / "relic" / "pubspec.lock", "relic"),
                "adapter": SERVER_ENV[name]["CW_ADAPTER"], "flags": "dart build cli (AOT)"}
    if name == "nginx":
        return {"nginx": first_line(["nginx", "-v"]), "flags": "the distribution's build"}
    return {}


def environment(args, name, modes, routes, conns):
    """Web ADR 0005 §2.4's record of a run."""
    commit = first_line(["git", "-C", str(HERE), "rev-parse", "HEAD"])
    dirty = subprocess.run(["git", "-C", str(HERE), "status", "--porcelain"], capture_output=True, text=True).stdout
    return {
        "date_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": (commit or "unknown") + (" (with uncommitted changes)" if dirty.strip() else ""),
        "machine": machine(),
        "os": operating_system(),
        "kernel": " ".join(platform.uname()[i] for i in (0, 2, 3)),
        "load_average_before": [round(x, 2) for x in os.getloadavg()],
        "server": name,
        "toolchain": toolchain(args, name),
        "reactor": REACTOR if name == "ceesafe.web" else None,
        "load_generator": first_line(["h2load", "--version"]),
        "method": "h2load, one request at a time on each connection (-m 1), latency from its --log-file",
        "duration_s": args.duration,
        "warm_up_s": 1,
        "conns": conns,
        "modes": modes,
        "routes": routes,
        "payloads": {route: PAYLOADS[route] for route in routes if route in PAYLOADS},
        "tls": ({"server_library": linked_openssl(name), "certificate": "self-signed ECDSA P-256",
                 "settings": "the server's defaults; each TLS row records what h2load negotiated"}
                if any(m.startswith("tls") for m in modes) else None),
    }


def linked_openssl(name):
    """The version of the libcrypto CeeSafe.Web's server links (`ldd`, or `otool -L` on macOS), asked of the
    library itself: the `openssl` on PATH may be another (macOS's is LibreSSL). nginx's is `nginx -V`'s."""
    if name == "nginx":
        r = subprocess.run(["nginx", "-V"], capture_output=True, text=True)
        m = re.search(r"built with (.+)", r.stderr + r.stdout)
        return m.group(1).strip() if m else None
    if name != "ceesafe.web":
        return None
    import ctypes
    darwin = platform.system() == "Darwin"
    r = subprocess.run(["otool", "-L", str(SERVER)] if darwin else ["ldd", str(SERVER)], capture_output=True,
                       text=True)
    for line in r.stdout.splitlines():
        if "libcrypto" in line:
            path = line.split()[0] if darwin else line.split("=>")[-1].split("(")[0].strip()
            try:
                version = ctypes.CDLL(path).OpenSSL_version
                version.restype = ctypes.c_char_p
                return version(0).decode() + f" ({path})"
            except (OSError, AttributeError):
                return path
    return None


def conform(name, port, h2c_port):
    """benchmarks/servers/conform.py against the running server: True if every check passes."""
    h2c = ["--no-h2c"] if name in NO_H2C else ["--h2c-port", str(h2c_port)]
    r = subprocess.run([sys.executable, str(CONFORM), "--port", str(port), *h2c, "--name", name],
                       capture_output=True, text=True)
    print(r.stdout.strip(), flush=True)
    return r.returncode == 0


class Comparison:
    """One comparison server, started with CW_PORT=PORT; stopped with SIGTERM."""

    def __init__(self, name, scratch):
        self.h2c_port = PORT + H2C_OFFSET.get(name, 0)
        env = dict(os.environ, CW_PORT=str(PORT), CW_H2C_PORT=str(self.h2c_port), **SERVER_ENV.get(name, {}))
        self.log = open(scratch / f"{name}.log", "ab")
        self.p = subprocess.Popen(COMPARISON[name], env=env, stdout=self.log, stderr=subprocess.STDOUT)
        wait_port(PORT)
        if name not in NO_H2C:
            wait_port(self.h2c_port)

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.log.close()


def run_comparison(args, modes, routes, conns, scratch, body, results):
    global PORT
    name = args.server
    if not os.path.exists(COMPARISON[name][-1]):
        sys.exit(f"run: {COMPARISON[name][-1]} is not built (benchmarks/servers/README.md)")
    s = Comparison(name, scratch)
    ok = conform(name, PORT, s.h2c_port)
    s.stop()
    if not ok:
        sys.exit(f"run: {name} failed the conformance check; nothing measured")
    plain = PORT
    try:
        for mode in [m for m in modes if m in COMPARISON_MODES and not (m == "h2c" and name in NO_H2C)]:
            for route in [r for r in routes if r in COMPARISON_ROUTES]:
                posted = body if route == "echo" else None
                for c in conns:
                    s = Comparison(name, scratch)
                    PORT = s.h2c_port if mode == "h2c" else plain
                    peak = Peak(s.p.pid)
                    peak.start()
                    r = h2load(mode, ROUTES[route], c, args.duration, scratch, posted)
                    peak.done = True
                    PORT = plain
                    s.stop()
                    r.update(server=name, mode=mode, route=route, conns=c, peak_rss_kib=peak.peak)
                    results.append(r)
                    print(row(r), flush=True)
    finally:
        PORT = plain


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
            f"{r['requests_per_s']} | {r.get('mib_per_s', '')} | {r['p50_us']} | {r['p95_us']} | {r['p99_us']} | "
            f"{r['p999_us']} | "
            f"{r.get('peak_rss_kib', '')} | {r['failed']} |")


if __name__ == "__main__":
    main()
