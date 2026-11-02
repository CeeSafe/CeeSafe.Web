#!/usr/bin/env python3
"""M1's overload gate (implementation plan §7, M1; Web ADR 0005 §2.4), against benchmarks/overload's server.

usage: python3 benchmarks/overload/run.py [--workers 64] [--hold-ms 20] [--waves 3] [--idle 1000] [--requests 3000]

1. Overload. One server with W workers, each request held hold-ms. One wave of W connections (1x), then `waves`
   waves of 10*W at once (10x). Each connection sends 64 bytes, ends its side and reads the echo. The connections
   beyond W wait in the kernel's listen queue: admission is the worker count. Past the queue (the backlog, capped by
   somaxconn: 128 on macOS) the kernel refuses them: on macOS a reset at connect (ECONNRESET), on Linux usually a
   dropped SYN that the client retries. Gates:
   - every connection the server accepted is echoed exactly, and the server failed none;
   - a refused connection fails fast (within 1 s) with a reset or refusal, never a wrong echo;
   - bounded RSS: the server's peak RSS during the 10x waves is at most its 1x peak + 10 % + 1 MiB, and does not
     grow from the first 10x wave to the last (+ 256 KiB);
   - bounded latency for admitted connections: the server's longest service time (request read to echo written) is
     at most hold-ms + 100 ms, however many wait outside. Accept to echo (`connection_max_ms`) also counts the
     client's own delay in sending, so it is reported, not gated.
   The client's latencies (including the wait outside) are reported, not gated.
2. Wake-up scaling (CeeSafe bug B5, fixed in 0.8.0). Two servers with idle+64 workers and no hold: one with no
   other connections, one with `idle` connections parked in a read. Each serves `requests` requests, 32 at a time.
   Every request resolves deadline races (each a cancellation); before the fix each one woke every parked task.
   Gate: the server's CPU time per request during those requests (ps, before and after) with `idle` parked
   connections is at most 1.5x that without + 5 us.

The oracle is this script: the payloads, the echo check, RSS (`ps`) and CPU time (`wait4`) come from outside the
server. Exit 0 if every gate holds.
"""
import argparse
import asyncio
import os
import pathlib
import platform
import resource
import socket
import statistics
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The overload server as a child process, with its RSS sampled every 20 ms."""

    started = []

    def __init__(self, workers: int, hold_ms: int):
        self.port = free_port()
        env = dict(os.environ, CW_PORT=str(self.port), CW_WORKERS=str(workers), CW_HOLD_MS=str(hold_ms),
                   CW_BACKLOG="0")
        self.proc = subprocess.Popen([str(HERE / "out" / "overload")], env=env, stdout=subprocess.PIPE, text=True)
        Server.started.append(self.proc)
        line = self.proc.stdout.readline()
        if not line.startswith("ready"):
            raise SystemExit(f"server did not start: {line!r}")
        self.peak_kib = 0
        self._stop = False
        self._sampler = threading.Thread(target=self._sample, daemon=True)
        self._sampler.start()

    def _sample(self):
        while not self._stop:
            r = subprocess.run(["ps", "-o", "rss=", "-p", str(self.proc.pid)], capture_output=True, text=True)
            if r.stdout.strip():
                self.peak_kib = max(self.peak_kib, int(r.stdout.strip()))
            time.sleep(0.02)

    def cpu_seconds(self) -> float:
        """The server's CPU time so far (user + system), from ps (10 ms resolution)."""
        t = subprocess.run(["ps", "-o", "time=", "-p", str(self.proc.pid)], capture_output=True, text=True).stdout
        minutes, seconds = t.strip().split(":")[-2:]
        return int(minutes) * 60 + float(seconds)

    def reset_peak(self) -> None:
        self.peak_kib = 0

    def stop(self):
        """Sends STOP, waits for the exit; returns (report dict, CPU seconds)."""
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.sendall(b"STOP")
            s.shutdown(socket.SHUT_WR)
            s.recv(16)
        _, status, usage = os.wait4(self.proc.pid, 0)
        self._stop = True
        out = self.proc.stdout.read()
        report = {}
        for line in out.splitlines():
            if line.startswith("report"):
                report = {k: int(v) for k, v in (kv.split("=") for kv in line.split()[1:])}
        if status != 0 or not report:
            raise SystemExit(f"server failed: status {status}, output {out!r}")
        return report, usage.ru_utime + usage.ru_stime


async def request(port: int, k: int):
    """One connection: send 64 bytes, end our side, read the echo. Returns (outcome, seconds), the outcome being
    "ok", "refused" (a reset or refusal: the kernel turned it away) or "wrong" (anything else)."""
    payload = bytes((k + i) % 251 for i in range(64))
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(payload)
        await writer.drain()
        writer.write_eof()
        echo = await reader.read()
        writer.close()
        await writer.wait_closed()
        return ("ok" if echo == payload else "wrong"), time.perf_counter() - start
    except (ConnectionResetError, ConnectionRefusedError):
        return "refused", time.perf_counter() - start
    except OSError:
        return "wrong", time.perf_counter() - start


async def wave(port: int, n: int):
    """(echoed, refused, wrong, the echoed ones' sorted times, the slowest refusal)."""
    results = await asyncio.gather(*(request(port, k) for k in range(n)))
    count = {o: sum(1 for x, _ in results if x == o) for o in ("ok", "refused", "wrong")}
    refused = [t for o, t in results if o == "refused"]
    return (count["ok"], count["refused"], count["wrong"], sorted(t for o, t in results if o == "ok"),
            max(refused, default=0.0))


async def steady(port: int, total: int, concurrency: int = 32):
    done = 0
    failed = 0

    async def loop():
        nonlocal done, failed
        while done < total:
            done += 1
            outcome, _ = await request(port, done)
            failed += 0 if outcome == "ok" else 1

    await asyncio.gather(*(loop() for _ in range(concurrency)))
    return failed


async def idle_connections(port: int, n: int):
    conns = []
    for _ in range(n):
        conns.append(await asyncio.open_connection("127.0.0.1", port))
    return conns


def pct(sorted_times, p):
    return sorted_times[min(len(sorted_times) - 1, int(len(sorted_times) * p))] * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--hold-ms", type=int, default=20)
    ap.add_argument("--waves", type=int, default=3)
    ap.add_argument("--idle", type=int, default=1000)
    ap.add_argument("--requests", type=int, default=3000)
    args = ap.parse_args()

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, want), hard))  # the server inherits it

    build = subprocess.run(["ceesafec", "build", "-o", "out/overload"], cwd=HERE, capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr)
        return 1
    version = subprocess.run(["ceesafec", "--version"], capture_output=True, text=True).stdout.strip()
    somax = subprocess.run(["sysctl", "-n", "kern.ipc.somaxconn" if sys.platform == "darwin" else "net.core.somaxconn"],
                           capture_output=True, text=True).stdout.strip()
    print(f"# {time.strftime('%Y-%m-%d %H:%M')} {platform.platform()} {platform.machine()} {os.cpu_count()} CPUs; "
          f"{version}; somaxconn {somax}; RLIMIT_NOFILE {want}")
    gates = []

    # 1. Overload.
    w, hold = args.workers, args.hold_ms
    server = Server(w, hold)
    good, refused, wrong, times, _ = asyncio.run(wave(server.port, w))
    base = server.peak_kib
    print(f"1x  {w:5} connections: {good} echoed, {refused} refused, {wrong} wrong; client ms p50 "
          f"{pct(times, .5):.0f} p99 {pct(times, .99):.0f} max {times[-1] * 1000:.0f}; server peak RSS {base} KiB")
    echoed, wrongs, slowest_refusal = good, wrong, 0.0
    peaks = []
    for i in range(args.waves):
        server.reset_peak()
        good, refused, wrong, times, slow = asyncio.run(wave(server.port, 10 * w))
        peaks.append(server.peak_kib)
        echoed += good
        wrongs += wrong
        slowest_refusal = max(slowest_refusal, slow)
        print(f"10x {10 * w:5} connections (wave {i + 1}): {good} echoed, {refused} refused (slowest "
              f"{slow * 1000:.0f} ms), {wrong} wrong; echoed client ms p50 {pct(times, .5):.0f} p99 "
              f"{pct(times, .99):.0f} max {times[-1] * 1000:.0f}; server peak RSS {server.peak_kib} KiB")
    report, _ = server.stop()
    print(f"server report: {report}")
    gates.append((f"every accepted connection echoed ({echoed} echoed, {report['accepted'] - 1} accepted besides "
                  f"STOP, {report['failed']} failed)",
                  echoed == report["accepted"] - 1 and report["failed"] == 0 and report["handshakes_failed"] == 0))
    gates.append((f"refused connections fail fast and none is wrong (slowest {slowest_refusal * 1000:.0f} ms, "
                  f"{wrongs} wrong)", slowest_refusal < 1.0 and wrongs == 0))
    gates.append((f"bounded RSS: 10x peak <= 1x peak + 10 % + 1 MiB ({max(peaks)} <= {int(base * 1.1) + 1024})",
                  max(peaks) <= base * 1.1 + 1024))
    gates.append((f"no growth across 10x waves ({peaks[-1]} <= {peaks[0]} + 256)", peaks[-1] <= peaks[0] + 256))
    gates.append((f"bounded admitted latency: service max {report['service_max_ms']} ms <= hold {hold} + 100",
                  report["service_max_ms"] <= hold + 100))

    # 2. Wake-up scaling.
    per_request = {}
    for idle in (0, args.idle):
        server = Server(args.idle + 64, 0)

        async def scenario():
            conns = await idle_connections(server.port, idle)
            await asyncio.sleep(0.2)  # every idle connection accepted and parked in its read
            cpu_before = server.cpu_seconds()
            started = time.perf_counter()
            failed = await steady(server.port, args.requests)
            took = time.perf_counter() - started
            cpu = server.cpu_seconds() - cpu_before
            for _, writer in conns:
                writer.close()
            return failed, took, cpu

        failed, took, cpu = asyncio.run(scenario())
        report, _ = server.stop()
        per_request[idle] = cpu / args.requests
        print(f"{idle:5} idle: {args.requests} requests in {took:.2f} s ({args.requests / took:.0f}/s client-bound); "
              f"server CPU {cpu:.3f} s, {per_request[idle] * 1e6:.1f} us per request; {failed} failed; {report}")
        gates.append((f"{idle} idle: no request failed", failed == 0))
    limit = per_request[0] * 1.5 + 5e-6
    gates.append((f"CPU per request with {args.idle} idle <= 1.5x without + 5 us "
                  f"({per_request[args.idle] * 1e6:.1f} <= {limit * 1e6:.1f} us)", per_request[args.idle] <= limit))

    for name, ok in gates:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in gates) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for p in Server.started:  # a driver that fails never leaves a server behind
            if p.poll() is None:
                p.kill()
