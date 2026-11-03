"""WebSocket client interop for web_client (Web ADR 0010 §3.6, amendment 2), through examples/ws_client over real TCP:
Python's `websockets` server (an independent implementation, with zlib inside for permessage-deflate) and raw-socket
servers for answers a library will not give.

Expected outcomes follow RFC 6455: §4.1 (the request, and every check of the answer: "the client MUST _Fail the
WebSocket Connection_"), §5.1 and §5.3 (every client frame masked, with a fresh key), §5.5.1 (the close handshake),
§7.4 (close codes), and RFC 7692 §7.1 (permessage-deflate's negotiation).

Build the example first (cd examples/ws_client && ceesafec build), then:
    python3 tests/interop/ws_client.py
"""
import asyncio, base64, hashlib, logging, os, pathlib, ssl, struct, sys

from websockets.asyncio.server import serve

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT = ROOT / "examples/ws_client/out/ws_client"
CERTS = ROOT / "packages/web_tls/tests/certs"
PORT = 19201
logging.getLogger("websockets").setLevel(logging.CRITICAL)  # the refusals below are expected
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {str(got)[:200]}")


async def client(url, **env):
    e = dict(os.environ, CW_URL=url, **{k: str(v) for k, v in env.items()})
    p = await asyncio.create_subprocess_exec(str(CLIENT), env=e, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), 30)
    except asyncio.TimeoutError:
        p.kill()
        return ["(timed out)"]
    return out.decode(errors="replace").splitlines()


async def echo(ws):
    async for message in ws:
        await ws.send(message)


# --- with websockets ---------------------------------------------------------------------------------------------

async def with_library():
    url = f"ws://127.0.0.1:{PORT}/chat"
    async with serve(echo, "127.0.0.1", PORT, max_size=None):
        out = await client(url, CW_SEND="hello|κόσμε|" + "x" * 70000, CW_BINARY=1048576)
        check("echo with permessage-deflate (zlib inflates what we compress)",
              out[:1] == ["ws_client: open protocol=- deflate=1"] and "ws_client: text hello" in out and
              "ws_client: text κόσμε" in out and "ws_client: text " + "x" * 70000 in out and
              "ws_client: binary 1048576 same" in out and out[-1:] == ["ws_client: closed 1000"], out[:3] + out[-2:])
        out = await client(url, CW_SEND="plain", CW_DEFLATE=0)
        check("echo without permessage-deflate", out == ["ws_client: open protocol=- deflate=0",
                                                         "ws_client: text plain", "ws_client: closed 1000"], out)

    async with serve(echo, "127.0.0.1", PORT, compression=None):
        out = await client(url, CW_SEND="a")
        check("an offer the server declines: no compression", out[:1] == ["ws_client: open protocol=- deflate=0"]
              and out[-1:] == ["ws_client: closed 1000"], out)

    async with serve(echo, "127.0.0.1", PORT, subprotocols=["chat.v2", "chat.v1"]):
        out = await client(url, CW_SEND="a", CW_PROTOCOLS="chat.v1,chat.v2")
        check("the server's choice among the offered subprotocols", out[:1] ==
              ["ws_client: open protocol=chat.v2 deflate=1"], out)

    async with serve(echo, "127.0.0.1", PORT, origins=["https://app.test"]):
        out = await client(url, CW_SEND="a", CW_ORIGIN="https://app.test")
        check("an allowed Origin is sent", out[-1:] == ["ws_client: closed 1000"], out)
        out = await client(url, CW_SEND="a")
        check("no Origin: refused with 403, not switched", out ==
              ["ws_client: failed: the server did not switch to WebSocket"], out)

    async def close_4001(ws):
        await ws.recv()
        await ws.close(4001, "bye")

    async with serve(close_4001, "127.0.0.1", PORT):
        out = await client(url, CW_SEND="a|b")
        check("the server closes first: its code (§5.5.1)", out[-1:] == ["ws_client: closed 4001"], out)

    pongs = []

    async def pinger(ws):
        message = await ws.recv()
        waiter = await ws.ping(b"are you there")
        await asyncio.wait_for(waiter, 5)  # answered by the session itself (§5.5.2)
        pongs.append(1)
        await ws.send(message)
        await ws.wait_closed()

    async with serve(pinger, "127.0.0.1", PORT):
        out = await client(url, CW_SEND="a")
        check("a server's ping is answered", pongs == [1] and "ws_client: text a" in out, out)

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(CERTS / "server.pem", CERTS / "server.key")
    async with serve(echo, "127.0.0.1", PORT, ssl=tls):
        out = await client(f"wss://localhost:{PORT}/secure", CW_SEND="over tls", CW_BINARY=100000,
                           CW_CA=CERTS / "ca.pem")
        check("wss: echo over TLS", "ws_client: text over tls" in out and "ws_client: binary 100000 same" in out and
              out[-1:] == ["ws_client: closed 1000"], out)
        out = await client(f"wss://localhost:{PORT}/secure", CW_SEND="x")
        check("wss: a server the roots do not trust is refused", len(out) == 1 and
              out[0].startswith("ws_client: failed:"), out)


# --- raw servers -------------------------------------------------------------------------------------------------

class Raw:
    """A one-connection server: reads the request, answers with `answer(request)`, then runs `then(reader, writer)`."""

    def __init__(self, answer, then=None):
        self.answer, self.then, self.requests = answer, then, []

    async def handle(self, reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(request.decode())
            writer.write(self.answer(request.decode()))
            await writer.drain()
            if self.then:
                await self.then(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def run(self, **env):
        server = await asyncio.start_server(self.handle, "127.0.0.1", PORT)
        async with server:
            return await client(f"ws://127.0.0.1:{PORT}/raw", **env)


def field(request, name):
    for line in request.split("\r\n")[1:]:
        k, _, v = line.partition(":")
        if k.strip().lower() == name:
            return v.strip()
    return None


def accept_of(request):
    return base64.b64encode(hashlib.sha1(field(request, "sec-websocket-key").encode() + GUID).digest()).decode()


def switching(extra="", accept=None, upgrade="Upgrade: websocket\r\n", connection="Connection: Upgrade\r\n"):
    def answer(request):
        a = accept if accept is not None else accept_of(request)
        return (f"HTTP/1.1 101 Switching Protocols\r\n{upgrade}{connection}Sec-WebSocket-Accept: {a}\r\n"
                f"{extra}\r\n").encode()
    return answer


async def read_frame(reader):
    """One client frame: (fin, opcode, masked, mask, payload unmasked)."""
    b0, b1 = await reader.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if b1 & 0x80 else b""
    data = await reader.readexactly(n)
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return b0 & 0x80 != 0, b0 & 0x0F, b1 & 0x80 != 0, mask, data


def server_frame(op, payload, mask=None):
    n = len(payload)
    b1 = (0x80 if mask else 0) | (n if n < 126 else 126)
    head = bytes([0x80 | op, b1]) + (struct.pack("!H", n) if n >= 126 else b"")
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        head += mask
    return head + payload


async def raw_servers():
    frames = []

    async def collect(reader, writer):
        for _ in range(3):  # two texts and the Close
            f = await read_frame(reader)
            frames.append(f)
            if f[1] == 1:
                writer.write(server_frame(1, f[4]))
            elif f[1] == 8:
                writer.write(server_frame(8, f[4]))
            await writer.drain()

    raw = Raw(switching(), collect)
    out = await raw.run(CW_SEND="first|second", CW_DEFLATE=0)
    request = raw.requests[0] if raw.requests else ""
    lines = request.split("\r\n")
    key = field(request, "sec-websocket-key") or ""
    check("the request (§4.1): GET, Upgrade, Connection, version 13, Host",
          lines[0] == "GET /raw HTTP/1.1" and field(request, "upgrade").lower() == "websocket" and
          "upgrade" in field(request, "connection").lower() and field(request, "sec-websocket-version") == "13" and
          field(request, "host") == f"127.0.0.1:{PORT}" and field(request, "sec-websocket-extensions") is None,
          lines[:3])
    check("the key: base64 of 16 bytes (§4.1 rule 7)", len(base64.b64decode(key, validate=True)) == 16, key)
    check("every client frame is masked (§5.1)", len(frames) == 3 and all(f[2] for f in frames),
          [(f[1], f[2]) for f in frames])
    check("each frame has its own masking key (§5.3)", len({f[3] for f in frames}) == 3, [f[3].hex() for f in frames])
    check("the payloads unmask to what was sent", [f[4] for f in frames[:2]] == [b"first", b"second"] and
          frames[2][4] == b"\x03\xe8", [f[4] for f in frames])
    check("the echoes reach the client, then the close", out == [
        "ws_client: open protocol=- deflate=0", "ws_client: text first", "ws_client: text second",
        "ws_client: closed 1000"], out)
    raw2 = Raw(switching(), collect)
    await raw2.run(CW_SEND="x", CW_DEFLATE=0)
    key2 = field(raw2.requests[0], "sec-websocket-key") if raw2.requests else None
    check("a fresh key per connection", key2 is not None and key2 != key, (key, key2))

    async def nothing(reader, writer):
        await asyncio.sleep(0.5)

    for name, answer, env, expected in [
        ("a wrong Sec-WebSocket-Accept (§4.1 rule 4)", switching(accept=base64.b64encode(b"x" * 20).decode()), {},
         "the server's Sec-WebSocket-Accept is wrong"),
        ("no Sec-WebSocket-Accept", lambda r: b"HTTP/1.1 101 OK\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n",
         {}, "the server's Sec-WebSocket-Accept is wrong"),
        ("no Upgrade field (§4.1 rule 2)", switching(upgrade=""), {}, "the server's answer is not a WebSocket upgrade"),
        ("no Connection: upgrade (§4.1 rule 3)", switching(connection="Connection: keep-alive\r\n"), {},
         "the server's answer is not a WebSocket upgrade"),
        ("permessage-deflate not offered (§4.1 rule 5)",
         switching("Sec-WebSocket-Extensions: permessage-deflate\r\n"), {"CW_DEFLATE": 0},
         "the server agreed to an extension not offered"),
        ("an unknown extension", switching("Sec-WebSocket-Extensions: x-webkit-deflate-frame\r\n"), {},
         "the server agreed to an extension not offered"),
        ("client_max_window_bits without a value (RFC 7692 §7.1.2.2)",
         switching("Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n"), {},
         "the server agreed to an extension not offered"),
        ("a subprotocol not offered (§4.1 rule 6)", switching("Sec-WebSocket-Protocol: chat.v3\r\n"),
         {"CW_PROTOCOLS": "chat.v1"}, "the server chose a subprotocol not offered"),
        ("a subprotocol when none was offered", switching("Sec-WebSocket-Protocol: chat.v1\r\n"), {},
         "the server chose a subprotocol not offered"),
        ("a 200 instead of 101", lambda r: b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", {},
         "the server did not switch to WebSocket"),
    ]:
        out = await Raw(answer, nothing).run(CW_SEND="x", **env)
        check(name, out == ["ws_client: failed: " + expected], out)

    # The server's window from server_max_window_bits, and our compressor within client_max_window_bits: zlib
    # (wbits -9) inflates what the client sends, and the client inflates what zlib compresses.
    import zlib
    got = []

    async def deflating(reader, writer):
        f = await read_frame(reader)
        inflater = zlib.decompressobj(-9)
        got.append((f[0], f[1], f[4] and inflater.decompress(f[4] + b"\x00\x00\xff\xff")))
        c = zlib.compressobj(9, zlib.DEFLATED, -9)
        body = c.compress(b"compressed by zlib " * 20) + c.flush(zlib.Z_SYNC_FLUSH)
        writer.write(bytes([0xC1, 126]) + struct.pack("!H", len(body) - 4) + body[:-4])  # RSV1, text
        await writer.drain()
        await read_frame(reader)  # the client's Close
        writer.write(server_frame(8, b"\x03\xe8"))
        await writer.drain()

    out = await Raw(switching("Sec-WebSocket-Extensions: permessage-deflate; server_max_window_bits=9; "
                              "client_max_window_bits=9\r\n"), deflating).run(CW_SEND="zlib, please " * 30)
    check("window bits agreed (9): zlib inflates the client's message, the client inflates zlib's",
          got == [(True, 1, ("zlib, please " * 30).encode())] and
          "ws_client: text " + "compressed by zlib " * 20 in out and out[-1:] == ["ws_client: closed 1000"],
          (got[:1], out))

    closes = []

    async def masked(reader, writer):
        await read_frame(reader)  # the client's text
        writer.write(server_frame(1, b"masked", mask=b"\x01\x02\x03\x04"))
        await writer.drain()
        closes.append(await read_frame(reader))

    out = await Raw(switching(), masked).run(CW_SEND="x", CW_DEFLATE=0)
    check("a masked frame from the server: the client closes with 1002 (§5.1)", closes and closes[0][1] == 8 and
          closes[0][4][:2] == b"\x03\xea" and out[-1:] == ["ws_client: closed 1002"], (closes, out))


async def main():
    if not CLIENT.exists():
        raise SystemExit("build examples/ws_client first")
    await with_library()
    await raw_servers()
    print(f"ws client interop: {sum(checks)}/{len(checks)} passed")
    sys.exit(0 if all(checks) else 1)


asyncio.run(main())
