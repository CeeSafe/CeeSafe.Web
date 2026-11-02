"""Interop and M3 gate checks for examples/sse (Web ADR 0008 §5), over real TCP.

The client side is written here from the standards, apart from web_sse: HTTP/1.1 chunked decoding (RFC 9112 §7.1)
and the event-stream parser of WHATWG HTML §9.2.6.

Build the example first (cd examples/sse && ceesafec build), then:
    python3 tests/interop/sse.py [--slow-seconds 60] [path to the server]
"""
import argparse, pathlib, socket, subprocess, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
PORT = 18094
TICK_MS, HEARTBEAT_MS = 300, 100

checks = []


def check(name, cond, got):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


class Stream:
    """A GET whose response is read incrementally: head, then the chunked body, then events."""

    def __init__(self, path, headers=()):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=10)
        extra = "".join(f"{k}: {v}\r\n" for k, v in headers)
        self.sock.sendall(f"GET {path} HTTP/1.1\r\nhost: x\r\n{extra}\r\n".encode())
        self.raw = b""
        self.body = b""
        self.ended = False
        while b"\r\n\r\n" not in self.raw:
            self._recv()
        head, self.raw = self.raw.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        self.headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:])}
        # The event-stream parser's state (§9.2.6).
        self.pending = b""
        self.data, self.etype, self.last_id = [], "", ""
        self.comments = 0

    def _recv(self, n=65536):
        b = self.sock.recv(n)
        if not b:
            raise EOFError
        self.raw += b

    def _dechunk(self):
        while not self.ended:
            if b"\r\n" not in self.raw:
                return
            size_line, rest = self.raw.split(b"\r\n", 1)
            size = int(size_line.split(b";")[0], 16)
            if size == 0:
                self.ended = True
                return
            if len(rest) < size + 2:
                return
            self.body += rest[:size]
            assert rest[size:size + 2] == b"\r\n"
            self.raw = rest[size + 2:]

    def events(self, want, timeout=10):
        """Reads until `want` events were dispatched or the body ended; returns them as (type, data, last id)."""
        got = []
        deadline = time.time() + timeout
        while len(got) < want and time.time() < deadline:
            self._dechunk()
            got += self._parse()
            if len(got) >= want or self.ended:
                break
            try:
                self._recv()
            except EOFError:
                break
        return got

    def _parse(self):
        out = []
        buf = self.pending + self.body
        self.body = b""
        while True:
            # A line ends at CRLF, LF or CR (a CR at the very end may be half of a CRLF: wait).
            idx = [i for i in (buf.find(b"\r"), buf.find(b"\n")) if i >= 0]
            if not idx:
                break
            i = min(idx)
            if buf[i:i + 1] == b"\r":
                if i + 1 == len(buf):
                    break
                end = i + 2 if buf[i + 1:i + 2] == b"\n" else i + 1
            else:
                end = i + 1
            line, buf = buf[:i].decode("utf-8"), buf[end:]
            if line == "":
                if self.data:
                    out.append((self.etype or "message", "\n".join(self.data), self.last_id))
                self.data, self.etype = [], ""
            elif line.startswith(":"):
                self.comments += 1
            else:
                name, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if name == "data":
                    self.data.append(value)
                elif name == "event":
                    self.etype = value
                elif name == "id" and "\0" not in value:
                    self.last_id = value
        self.pending = buf
        return out

    def close(self):
        self.sock.close()


def stats():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats", timeout=5) as r:
        open_, produced = r.read().decode().split()
        return int(open_), int(produced)


def rss_kib(pid):
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip())


def wait_closed(limit_s):
    start = time.time()
    while time.time() - start < limit_s:
        if stats()[0] == 0:
            return time.time() - start
        time.sleep(0.01)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slow-seconds", type=int, default=60)
    ap.add_argument("server", nargs="?", default=str(ROOT / "examples/sse/out/sse"))
    args = ap.parse_args()
    env = {"CW_PORT": str(PORT), "CW_TICK_MS": str(TICK_MS), "CW_HEARTBEAT_MS": str(HEARTBEAT_MS)}
    proc = subprocess.Popen([args.server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            stats()
            break
        except OSError:
            time.sleep(0.05)

    # A finite stream: three events, heartbeats between them, then the end of the body.
    s = Stream("/count?n=3")
    ev = s.events(10)
    check("head", s.status == 200 and s.headers.get("content-type") == "text/event-stream"
          and s.headers.get("cache-control") == "no-store" and s.headers.get("transfer-encoding") == "chunked",
          (s.status, s.headers))
    check("three events, ids 1-3", ev == [("message", "1", "1"), ("message", "2", "2"), ("message", "3", "3")], ev)
    check("the body ended", s.ended, s.ended)
    check("heartbeats while quiet", s.comments >= 2, s.comments)
    s.close()

    # Resuming: Last-Event-ID 41 continues at 42.
    s = Stream("/count?n=2", [("Last-Event-ID", "41")])
    ev = s.events(10)
    check("resumes after Last-Event-ID", [e[2] for e in ev] == ["42", "43"], ev)
    s.close()

    # A client that leaves an endless stream: the stream and its producer end within one heartbeat.
    s = Stream("/count")
    s.events(1)
    s.close()
    t = wait_closed(2)
    check(f"closed client frees the stream (heartbeat {HEARTBEAT_MS} ms)", t is not None and t * 1000 < HEARTBEAT_MS + 200, t)
    before = stats()[1]
    time.sleep(TICK_MS * 2 / 1000)
    check("its producer stopped", stats()[1] == before, (before, stats()[1]))

    # The slow-client gate: 1 KiB/s against a producer that would offer 1 KiB events without limit.
    s = Stream("/flood")
    s.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    read = 0
    samples = []
    start = time.time()
    while time.time() - start < args.slow_seconds:
        tick = time.time()
        b = s.sock.recv(1024)
        if not b:
            break
        read += len(b)
        if int(time.time() - start) % 5 == 0 and (not samples or time.time() - samples[-1][0] - start >= 4.5):
            samples.append((time.time() - start, rss_kib(proc.pid)))
        time.sleep(max(0, 1 - (time.time() - tick)))
    _, produced = stats()
    first, last = samples[1][1] if len(samples) > 1 else samples[0][1], samples[-1][1]
    print(f"     rss samples (s, KiB): {[(round(a), b) for a, b in samples]}")
    check(f"RSS flat over {args.slow_seconds} s at 1 KiB/s", last - first <= 256, (first, last))
    backlog = produced * 1024 - read
    check("producer held to the client's pace", backlog < 8 * 1024 * 1024, f"produced {produced} KiB, read {read // 1024} KiB")
    s.close()
    t = wait_closed(2)
    check("closing the slow client frees the flood", t is not None and t < 1, t)

    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/stop", method="POST"), timeout=5).read()
    try:
        out, _ = proc.communicate(timeout=10)
        check("drained and exited 0", proc.returncode == 0 and b"sse: drained" in out, (proc.returncode, out))
    except subprocess.TimeoutExpired:
        proc.kill()
        check("drained and exited 0", False, "timeout")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
