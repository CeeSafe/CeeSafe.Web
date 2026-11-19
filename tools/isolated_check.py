#!/usr/bin/env python3
"""Checks ConnectionServer.runIsolated (Web ADR 0016) with examples/http1: a request that traps costs its connection
and nothing else.

usage: python3 tools/isolated_check.py [--port 18710] [--traps 200]   (after `ceesafec build` in examples/http1)

With CW_ISOLATED=1 the example serves each connection in an isolated task. The check traps on a keep-alive
connection while another keep-alive connection stays open, then traps `--traps` times more, and checks that every
other request is answered, that each trap's peer saw its connection end, that the process printed one "ended alone"
line a trap, and that its descriptors grew by about one a trap (the crashed connections' sockets are leaked, CeeSafe
ADR 0175 §2.4). Then `/stop` drains it. Last, without CW_ISOLATED, a trap ends the process, as before.

The trapping clients do not half-close their side first: with ceesafec 0.75.0 on macOS, a closer cannot end a
connection whose peer has already sent its FIN (docs/ceesafe-upstream-work.md, B21).
"""
import argparse, http.client, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent.parent
BINARY = HERE / "examples" / "http1" / "out" / "http1"
args = argparse.ArgumentParser()
args.add_argument("--port", type=int, default=18710)
args.add_argument("--traps", type=int, default=200)
a = args.parse_args()

problems = []
def expect(what, ok):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        problems.append(what)

def descriptors(pid):
    """How many descriptors the process holds, or None."""
    fd = pathlib.Path(f"/proc/{pid}/fd")
    if fd.exists():
        return len(list(fd.iterdir()))
    try:
        out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
        return max(0, len(out.splitlines()) - 1)
    except (OSError, subprocess.SubprocessError):
        return None

def start(isolated):
    env = dict(os.environ, CW_PORT=str(a.port), CW_WORKERS="16")
    if isolated:
        env["CW_ISOLATED"] = "1"
    p = subprocess.Popen([str(BINARY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        try:
            c = http.client.HTTPConnection("127.0.0.1", a.port, timeout=2)
            c.request("GET", "/hello")
            c.getresponse().read()
            c.close()
            return p
        except OSError:
            time.sleep(0.05)
    p.kill()
    sys.exit("isolated_check: the example did not start")

def ask(conn, path):
    """(status, body), or the name of the error."""
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read()
    except (OSError, http.client.HTTPException) as e:
        return type(e).__name__

if not BINARY.exists():
    sys.exit(f"isolated_check: build {BINARY} first (ceesafec build in examples/http1)")

p = start(True)
try:
    kept = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
    expect("a keep-alive connection is answered", ask(kept, "/hello") == (200, b"hello, world"))
    trapping = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
    expect("the connection answers before its trap", ask(trapping, "/hello") == (200, b"hello, world"))
    got = ask(trapping, "/trap")
    expect(f"the request that traps gets no answer, and its connection ends ({got})", not isinstance(got, tuple))
    expect("the other keep-alive connection is still answered", ask(kept, "/hello") == (200, b"hello, world"))

    before = descriptors(p.pid)
    ended = 0
    t0 = time.time()
    for _ in range(a.traps):
        c = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
        if not isinstance(ask(c, "/trap"), tuple):
            ended += 1
        c.close()
    took = time.time() - t0
    expect(f"{a.traps} more traps: each connection ended, none waited for its timeout ({ended}, {took:.1f} s)",
           ended == a.traps and took < a.traps * 0.5)
    fresh = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
    expect("a new connection is answered after them", ask(fresh, "/hello") == (200, b"hello, world"))
    expect("so is the old keep-alive one", ask(kept, "/hello") == (200, b"hello, world"))
    time.sleep(0.2)
    after = descriptors(p.pid)
    if before is not None and after is not None:
        grew = after - before
        expect(f"descriptors grew by about one a trap ({grew} for {a.traps})", a.traps - 5 <= grew <= a.traps + 5)

    stop = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
    ask(stop, "/stop")
    for c in (kept, trapping, fresh, stop):
        c.close()
    try:
        status = p.wait(timeout=40)
    except subprocess.TimeoutExpired:
        status = None
    expect(f"/stop drains the server and it ends (status {status})", status == 0)
finally:
    if p.poll() is None:
        p.kill()
err = p.stderr.read()
alone = err.count("ended alone)")
expect(f"one 'ended alone' line a trap ({alone} for {a.traps + 1})", alone == a.traps + 1)

p = start(False)
try:
    c = http.client.HTTPConnection("127.0.0.1", a.port, timeout=3)
    got = ask(c, "/trap")
    try:
        status = p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        status = None
    expect(f"without CW_ISOLATED a trap ends the process ({got}, status {status})", status not in (None, 0))
finally:
    if p.poll() is None:
        p.kill()

print("isolated_check: " + ("all passed" if not problems else f"{len(problems)} failed"))
sys.exit(1 if problems else 0)
