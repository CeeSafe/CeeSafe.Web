"""WebSocket interop for web_ws (Web ADR 0010 §5), through examples/ws_echo over real TCP: Python's `websockets`
library (an independent client), raw sockets for what a library will not send, and stress for the M6 gate
(malformed frames from many clients; a client that stops reading).

Expected outcomes follow RFC 6455 (§4.2.2 the handshake, §4.4 the version, §5.5.2-3 ping and pong, §7.4 close
codes, §10.2 origins) and Web ADR 0010 §3.3-3.4 (keep-alive, same origin, the write deadline).

Build the example first (cd examples/ws_echo && ceesafec build), then:
    python3 tests/interop/ws.py
"""
import asyncio, base64, os, pathlib, random, socket, struct, subprocess, sys, time

import websockets

ROOT = pathlib.Path(__file__).resolve().parents[2]
ECHO = ROOT / "examples/ws_echo/out/ws_echo"
PORT = 19101

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {str(got)[:160]}")


def start(**env):
    e = dict(os.environ, CW_PORT=str(PORT), **{k: str(v) for k, v in env.items()})
    p = subprocess.Popen([str(ECHO)], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    line = p.stdout.readline().decode().strip()
    if line != "ws_echo: listening":
        p.kill()
        raise SystemExit(f"ws_echo did not start: {line}")
    return p


def stop(p):
    p.kill()
    p.wait()


def raw_upgrade(extra="", version="13"):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: {version}\r\n{extra}\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        b = s.recv(1)
        if not b:
            break
        head += b
    return s, head.decode(errors="replace")


def frame(op, payload, fin=True, mask=True):
    m = os.urandom(4) if mask else b""
    n = len(payload)
    if n < 126:
        h = bytes([(0x80 if fin else 0) | op, (0x80 if mask else 0) | n])
    elif n < 65536:
        h = bytes([(0x80 if fin else 0) | op, (0x80 if mask else 0) | 126]) + struct.pack("!H", n)
    else:
        h = bytes([(0x80 if fin else 0) | op, (0x80 if mask else 0) | 127]) + struct.pack("!Q", n)
    body = bytes(b ^ m[i % 4] for i, b in enumerate(payload)) if mask else payload
    return h + m + body


def read_frame(s):
    def exactly(n):
        out = b""
        while len(out) < n:
            b = s.recv(n - len(out))
            if not b:
                raise EOFError
            out += b
        return out
    h = exactly(2)
    n = h[1] & 127
    if n == 126:
        n = struct.unpack("!H", exactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", exactly(8))[0]
    return h[0] & 0x0F, exactly(n)


async def library_checks():
    url = f"ws://127.0.0.1:{PORT}/"
    async with websockets.connect(url, max_size=None) as ws:
        names = [type(e).__name__ for e in ws.extensions]
        check("permessage-deflate agreed (zlib inflates our echoes)", "PerMessageDeflate" in names, names)
        for msg in ["hello", "", "κόσμε", "x" * 70000]:
            await ws.send(msg)
            got = await ws.recv()
            check(f"echo text ({len(msg)} chars)", got == msg, len(got))
        blob = os.urandom(1 << 20)
        await ws.send(blob)
        got = await ws.recv()
        check("echo 1 MiB binary", got == blob, len(got))
        pong = await ws.ping(b"are you there")
        latency = await asyncio.wait_for(pong, 5)
        check("ping answered", latency is not None, latency)
        text = "the quick brown fox jumps over the lazy dog " * 2000
        await ws.send(text)
        check("echo of 88000 compressible chars", await ws.recv() == text)
        await ws.close(4000, "bye")
        check("close code echoed", ws.close_rcvd is not None and ws.close_rcvd.code == 4000, ws.close_rcvd)
    # Without compression too.
    async with websockets.connect(url, compression=None) as ws:
        await ws.send("plain")
        check("echo without compression", await ws.recv() == "plain" and not ws.extensions, ws.extensions)
    # Ten clients at once, interleaved.
    async def one(i):
        async with websockets.connect(url) as ws:
            for k in range(20):
                await ws.send(f"{i}:{k}")
                if await ws.recv() != f"{i}:{k}":
                    return False
        return True
    results = await asyncio.gather(*(one(i) for i in range(10)))
    check("ten clients interleaved", all(results), results)
    # Origins: same-origin accepted, another refused with 403.
    async with websockets.connect(url, origin=f"http://127.0.0.1:{PORT}") as ws:
        await ws.send("same")
        check("same origin accepted", await ws.recv() == "same")
    try:
        async with websockets.connect(url, origin="http://evil.example"):
            check("another origin refused", False, "accepted")
    except Exception as e:  # InvalidStatus, or the legacy client's InvalidStatusCode
        status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
        check("another origin refused", status == 403, repr(e))


async def allowed_origin_check():
    url = f"ws://127.0.0.1:{PORT}/"
    async with websockets.connect(url, origin="https://app.example") as ws:
        await ws.send("allowed")
        check("an allowed origin (CW_ORIGIN)", await ws.recv() == "allowed")


def raw_checks():
    s, head = raw_upgrade(version="8")
    check("version 8: 426 with Sec-WebSocket-Version: 13",
          head.startswith("HTTP/1.1 426") and "sec-websocket-version: 13" in head.lower(), head.split("\r\n")[0])
    s.close()
    # Malformed frames, each from a fresh connection: the Close code the server sends back (RFC 6455 §7.4.1).
    cases = [
        ("unmasked frame", frame(1, b"a", mask=False), 1002),
        ("reserved opcode", bytes([0x83, 0x80]) + os.urandom(4), 1002),
        ("RSV1 without an extension", bytes([0xC1, 0x80]) + os.urandom(4), 1002),
        ("fragmented ping", frame(9, b"", fin=False), 1002),
        ("bad UTF-8", frame(1, b"\xc0\x80"), 1007),
        ("1-byte close", frame(8, b"\x03"), 1002),
    ]
    for name, data, want in cases:
        s, head = raw_upgrade()
        s.sendall(data)
        try:
            op, payload = read_frame(s)
            code = struct.unpack("!H", payload[:2])[0] if op == 8 and len(payload) >= 2 else None
        except (EOFError, OSError) as e:
            op, code = None, repr(e)
        check(f"malformed: {name} -> {want}", op == 8 and code == want, (op, code))
        s.close()


def keepalive_check():
    s, head = raw_upgrade()
    start_t = time.monotonic()
    op1, _ = read_frame(s)
    t1 = time.monotonic() - start_t
    op2, payload = read_frame(s)
    t2 = time.monotonic() - start_t
    code = struct.unpack("!H", payload[:2])[0] if op2 == 8 else None
    check("keep-alive: a ping after 200 ms of quiet", op1 == 9 and 0.15 < t1 < 1.5, (op1, round(t1, 3)))
    check("keep-alive: unanswered, closed with 1011 after 200 ms more", op2 == 8 and code == 1011 and t2 > t1 + 0.15,
          (op2, code, round(t2, 3)))
    s.close()


def malformed_flood():
    """200 clients each send random bytes after the handshake; the server must stay up and serve a new client."""
    rng = random.Random(6455)
    for i in range(200):
        s, head = raw_upgrade()
        if not head.startswith("HTTP/1.1 101"):
            check("flood: handshake", False, head[:40])
            return
        s.sendall(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300))))
        s.close()
    s, head = raw_upgrade()
    s.sendall(frame(1, b"still here"))
    op, payload = read_frame(s)
    check("after 200 clients of random bytes, an echo", op == 1 and payload == b"still here", payload)
    s.close()


def slow_consumer_check():
    """A client sends 1 MiB messages and never reads: the server blocks on the write, gives up at the write
    deadline (1 s here), and closes; meanwhile another client is served."""
    s, head = raw_upgrade()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    blob = os.urandom(1 << 20)
    sent = 0
    s.settimeout(0.5)
    began = time.monotonic()
    try:
        for _ in range(64):
            s.sendall(frame(2, blob))
            sent += 1
    except (socket.timeout, OSError):
        pass
    # While it is stuck, another client is served at once.
    t, _ = raw_upgrade()
    t.sendall(frame(1, b"other"))
    op, payload = read_frame(t)
    check("slow consumer: another client served meanwhile", payload == b"other", payload)
    t.close()
    # The stuck connection is closed after the write deadline. Reading would let the server write again, so wait past
    # the deadline first, then count what arrives before the end: a server that closed sends little of what it echoed.
    time.sleep(3)
    s.settimeout(10)
    received = 0
    closed = False
    try:
        while True:
            b = s.recv(65536)
            if not b:
                closed = True
                break
            received += len(b)
    except (ConnectionResetError, OSError):
        closed = True
    check("slow consumer: closed after the write deadline", closed and received < sent * len(blob) // 2,
          (sent, received))
    s.close()


def main():
    p = start(CW_PING_MS=0, CW_WRITE_TIMEOUT_MS=1000)
    try:
        asyncio.run(library_checks())
        raw_checks()
        malformed_flood()
        slow_consumer_check()
    finally:
        stop(p)
    p = start(CW_PING_MS=200, CW_PONG_MS=200)
    try:
        keepalive_check()
    finally:
        stop(p)
    p = start(CW_ORIGIN="https://app.example")
    try:
        asyncio.run(allowed_origin_check())
    finally:
        stop(p)
    print(f"ws interop: {sum(checks)}/{len(checks)} passed")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
