#!/usr/bin/env python3
"""HTTP/1.1 conformance of web_http1 against h11 (implementation plan §7, M2 exit: "a vector suite derived from h11
plus hand-written smuggling cases").

usage: python3 tests/conformance/http1.py [--seed N] [--generated N] [-v]

Each vector is one request, sent to examples/http1's POST /echo, followed on the same connection by a sentinel
request (GET /hello). The oracle is h11 (python-hyper/h11), playing the server for the same bytes:

- h11 rejects the vector: the server must reject it too (a 4xx or 5xx, and no answer to the sentinel).
- h11 accepts it with body B: the server must echo exactly B and then answer the sentinel; that is, both agree where
  the request ends. A disagreement there is the shape of request smuggling, and is always a failure.
- The server rejects what h11 accepts: allowed only for the strict choices this server makes and lists in STRICT,
  each from RFC 9112 (which allows either); anything else is a failure.

Prints a summary and every failure; exit 0 if none.
"""
import argparse
import os
import pathlib
import random
import socket
import subprocess
import sys

import h11

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXAMPLE = ROOT / "examples" / "http1"
SENTINEL = b"GET /hello HTTP/1.1\r\nHost: s\r\n\r\n"

# Where this server is deliberately stricter than h11, with the rule that allows it. A vector the server rejects
# and h11 accepts must carry one of these tags.
STRICT = {
    "bare-lf": "RFC 9112 §2.2: a recipient MAY recognize a bare LF; this server rejects it",
    "ws-before-colon": "RFC 9112 §5.1: MUST reject (h11 also rejects; listed for completeness)",
    "obs-fold": "RFC 9112 §5.2: a server MUST reject or replace obs-fold; this server rejects",
    "cl-list": "RFC 9112 §6.3: a list of equal Content-Lengths MAY be accepted; this server accepts it",
    "te-1.0": "RFC 9112 §6.1: Transfer-Encoding in HTTP/1.0 MUST be treated as faulty; this server rejects",
    "host": "RFC 9112 §3.2: a Host that is not a valid uri-host is rejected",
    "target": "RFC 9112 §3.2: a target not in a form fitting the method is rejected",
    "ctl-in-value": "RFC 9110 §5.5: a control byte in a field value is rejected",
    "chunk-ext": "RFC 9112 §7.1.1: chunk extensions are bounded and may not hold controls",
    "chunk-size": "RFC 9112 §7.1: a chunk size of more than 15 hex digits is refused as an overflow attempt",
    "te-unsupported": "RFC 9112 §6.1: a coding the server does not decode is 501",
    "method": "RFC 9110 §9: a method that is not a token is rejected",
    "cl-and-te": "RFC 9112 §6.3 item 3: a server MAY reject a request with both Content-Length and Transfer-Encoding "
                 "(h11 lets Transfer-Encoding win); this server rejects, as a smuggling defence",
    "version": "RFC 9110 §15.6.6: a major version other than 1 over HTTP/1 framing is 505 (h11 accepts any x.y)",
}


# Where this server accepts what h11 rejects, because RFC 9112 says it should. The server must still frame the
# request exactly (its echo is checked against the body after the skipped part).
LENIENT = {
    "empty-lines": "RFC 9112 §2.2: a server SHOULD ignore at least one empty line before the request-line (h11 rejects)",
}


def h11_oracle(data: bytes):
    """('reject',), ('incomplete',) or ('accept', body, keep) for the first request of `data`: keep is whether h11
    would read another request on the connection after answering it."""
    conn = h11.Connection(our_role=h11.SERVER)
    conn.receive_data(data + SENTINEL)
    body = b""
    try:
        while True:
            ev = conn.next_event()
            if ev is h11.NEED_DATA or ev is h11.PAUSED:
                return ("incomplete",)
            if isinstance(ev, h11.Data):
                body += ev.data
            if isinstance(ev, h11.EndOfMessage):
                break
            if isinstance(ev, h11.ConnectionClosed):
                return ("incomplete",)
    except h11.RemoteProtocolError:
        return ("reject",)
    conn.send(h11.Response(status_code=200, headers=[("Content-Length", str(len(body)))]))
    conn.send(h11.Data(data=body))
    conn.send(h11.EndOfMessage())
    keep = conn.our_state is h11.DONE and conn.their_state is h11.DONE
    return ("accept", body, keep)


def server_view(port: int, data: bytes):
    """What the server did with `data` + the sentinel: ('reject', status) or ('accept', body, sentinel_answered) or
    ('none',) when it sent nothing."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data + SENTINEL)
        s.shutdown(socket.SHUT_WR)
        raw = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                raw += chunk
        except socket.timeout:
            pass
    client = h11.Connection(our_role=h11.CLIENT)
    # Two GETs' worth of client state, so h11 accepts two responses: the method of each matters only for HEAD.
    responses = []
    client.send(h11.Request(method="POST", target="/echo", headers=[("Host", "x"), ("Transfer-Encoding", "chunked")]))
    client.send(h11.EndOfMessage())
    client.receive_data(raw)
    client.receive_data(b"")
    status, body = None, b""
    try:
        while True:
            ev = client.next_event()
            if ev is h11.NEED_DATA or isinstance(ev, h11.ConnectionClosed):
                break
            if isinstance(ev, h11.InformationalResponse):
                continue
            if isinstance(ev, h11.Response):
                status, body = ev.status_code, b""
            elif isinstance(ev, h11.Data):
                body += ev.data
            elif isinstance(ev, h11.EndOfMessage):
                responses.append((status, body))
                if len(responses) == 1 and client.our_state is h11.DONE and client.their_state is h11.DONE:
                    client.start_next_cycle()
                    client.send(h11.Request(method="GET", target="/hello", headers=[("Host", "s")]))
                    client.send(h11.EndOfMessage())
                else:
                    break
    except h11.RemoteProtocolError as e:
        return ("garbled", str(e), raw[:300])
    if not responses:
        return ("none",)
    first_status, first_body = responses[0]
    if first_status >= 400:
        return ("reject", first_status, len(responses))
    sentinel = len(responses) > 1 and responses[1] == (200, b"hello, world")
    return ("accept", first_body, sentinel)


def handwritten():
    """(name, bytes, strict-tag or None). The smuggling cases of the plan, and the strict choices."""
    R = b"POST /echo HTTP/1.1\r\nHost: a\r\n"
    v = [
        ("plain length", R + b"Content-Length: 5\r\n\r\nhello", None),
        ("plain chunked", R + b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n", None),
        ("CL.TE", R + b"Content-Length: 6\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\nX", "cl-and-te"),
        ("TE.CL", R + b"Transfer-Encoding: chunked\r\nContent-Length: 3\r\n\r\n8\r\nSMUGGLED\r\n0\r\n\r\n",
         "cl-and-te"),
        ("TE.TE xchunked", R + b"Transfer-Encoding: xchunked\r\n\r\n0\r\n\r\n", "te-unsupported"),
        ("TE.TE space before colon", R + b"Transfer-Encoding : chunked\r\n\r\n0\r\n\r\n", "ws-before-colon"),
        ("TE.TE tab value", R + b"Transfer-Encoding:\tchunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n", None),
        ("TE.TE two fields", R + b"Transfer-Encoding: chunked\r\nTransfer-Encoding: x\r\n\r\n0\r\n\r\n", "te-unsupported"),
        ("TE.TE chunked, identity", R + b"Transfer-Encoding: chunked, identity\r\n\r\n0\r\n\r\n", "te-unsupported"),
        ("TE.TE upper case", R + b"Transfer-Encoding: CHUNKED\r\n\r\n5\r\nhello\r\n0\r\n\r\n", None),
        ("TE.TE vertical tab", R + b"Transfer-Encoding: \x0bchunked\r\n\r\n0\r\n\r\n", "ctl-in-value"),
        ("TE.TE folded", R + b"Transfer-Encoding: x\r\n chunked\r\n\r\n0\r\n\r\n", "obs-fold"),
        ("duplicate CL equal", R + b"Content-Length: 5\r\nContent-Length: 5\r\n\r\nhello", None),
        ("duplicate CL differ", R + b"Content-Length: 5\r\nContent-Length: 6\r\n\r\nhello!", None),
        ("CL list equal", R + b"Content-Length: 5, 5\r\n\r\nhello", "cl-list"),
        ("CL list differ", R + b"Content-Length: 5, 6\r\n\r\nhello!", None),
        ("CL plus sign", R + b"Content-Length: +5\r\n\r\nhello", None),
        ("CL negative", R + b"Content-Length: -1\r\n\r\n", None),
        ("CL leading zeros", R + b"Content-Length: 005\r\n\r\nhello", None),
        ("CL space inside", R + b"Content-Length: 5 5\r\n\r\nhello", None),
        ("CL hex", R + b"Content-Length: 0x5\r\n\r\nhello", None),
        ("CL huge", R + b"Content-Length: 99999999999999999999\r\n\r\n", None),
        ("space before colon", R + b"Content-Length : 5\r\n\r\nhello", "ws-before-colon"),
        ("NUL in value", R + b"X: a\x00b\r\nContent-Length: 0\r\n\r\n", "ctl-in-value"),
        ("bare CR in value", R + b"X: a\rb\r\nContent-Length: 0\r\n\r\n", "ctl-in-value"),
        ("bare LF line ends", b"POST /echo HTTP/1.1\nHost: a\nContent-Length: 5\n\nhello", "bare-lf"),
        ("obs-fold", R + b"X: a\r\n b\r\nContent-Length: 0\r\n\r\n", "obs-fold"),
        ("chunk size overflow", R + b"Transfer-Encoding: chunked\r\n\r\nFFFFFFFFFFFFFFFFF1\r\nx\r\n0\r\n\r\n", "chunk-size"),
        ("chunk size 16 digits", R + b"Transfer-Encoding: chunked\r\n\r\n0000000000000005\r\nhello\r\n0\r\n\r\n", "chunk-size"),
        ("chunk size leading zeros", R + b"Transfer-Encoding: chunked\r\n\r\n005\r\nhello\r\n0\r\n\r\n", None),
        ("chunk size negative", R + b"Transfer-Encoding: chunked\r\n\r\n-5\r\nhello\r\n0\r\n\r\n", None),
        ("chunk extension", R + b"Transfer-Encoding: chunked\r\n\r\n5;a=b\r\nhello\r\n0\r\n\r\n", None),
        ("chunk extension NUL", R + b"Transfer-Encoding: chunked\r\n\r\n5;a\x00\r\nhello\r\n0\r\n\r\n", "chunk-ext"),
        ("chunk missing CRLF", R + b"Transfer-Encoding: chunked\r\n\r\n5\r\nhelloX\r\n0\r\n\r\n", None),
        ("chunk bare LF size", R + b"Transfer-Encoding: chunked\r\n\r\n5\nhello\r\n0\r\n\r\n", "bare-lf"),
        ("trailers", R + b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\nX-T: 1\r\n\r\n", None),
        ("no Host", b"POST /echo HTTP/1.1\r\nContent-Length: 0\r\n\r\n", None),
        ("two Hosts", R + b"Host: b\r\nContent-Length: 0\r\n\r\n", None),
        ("Host with space", b"POST /echo HTTP/1.1\r\nHost: a b\r\nContent-Length: 0\r\n\r\n", "host"),
        ("TE in HTTP/1.0", b"POST /echo HTTP/1.0\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", "te-1.0"),
        ("HTTP/1.0 length", b"POST /echo HTTP/1.0\r\nContent-Length: 2\r\n\r\nhi", None),
        ("HTTP/2.0 version", b"POST /echo HTTP/2.0\r\nHost: a\r\nContent-Length: 0\r\n\r\n", None),
        ("lower-case version", b"POST /echo http/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n", None),
        ("double space", b"POST  /echo HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n", None),
        ("method with @", b"P@ST /echo HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n", "method"),
        ("NUL in target", b"POST /ec\x00ho HTTP/1.1\r\nHost: a\r\nContent-Length: 0\r\n\r\n", None),
        ("empty lines first", b"\r\n\r\n" + R + b"Content-Length: 2\r\n\r\nhi", "empty-lines"),
        ("absolute-form", b"POST http://a/echo HTTP/1.1\r\nHost: a\r\nContent-Length: 2\r\n\r\nhi", "target"),
    ]
    return v


def generated(rng: random.Random, n: int):
    """Requests built from random choices among valid and invalid pieces of each part."""
    names = [b"Content-Length", b"content-length", b"CONTENT-LENGTH", b"Transfer-Encoding", b"transfer-encoding",
             b"X-A", b"X_B", b"X.C", b"X(D)", b"X\xe9", b"", b"X Y"]
    seps = [b": ", b":", b":\t", b" : ", b"  :", b":  "]
    ends = [b"\r\n", b"\r\n", b"\r\n", b"\n", b"\r"]
    cl_values = [b"5", b"005", b"5, 5", b"5,6", b"+5", b"-5", b" 5 ", b"5 ", b"0x5", b"", b"18446744073709551616"]
    te_values = [b"chunked", b"Chunked", b"chunked ", b" chunked", b"chunked, chunked", b"gzip, chunked",
                 b"chunked;q=1", b"identity", b"chunked\x0b", b"x"]
    bodies_len = [b"hello"]
    chunked_bodies = [b"5\r\nhello\r\n0\r\n\r\n", b"5;e\r\nhello\r\n0\r\n\r\n", b"005\r\nhello\r\n0\r\n\r\n",
                      b"5\r\nhello\r\n0\r\nT: 1\r\n\r\n", b"5\r\nhello\n0\r\n\r\n", b"5 \r\nhello\r\n0\r\n\r\n",
                      b"g\r\nhello\r\n0\r\n\r\n", b"0\r\n\r\n"]
    out = []
    for i in range(n):
        end = rng.choice(ends) if rng.random() < 0.15 else b"\r\n"
        version = rng.choice([b"HTTP/1.1"] * 6 + [b"HTTP/1.0", b"HTTP/1.2", b"HTTP/3.0", b"HTTP/1.1 "])
        head = b"POST /echo " + version + end + b"Host: a" + end
        framing = rng.choice(["cl", "te", "both", "none", "dup"])
        body = b""
        fields = []
        if framing in ("cl", "both", "dup"):
            fields.append((rng.choice(names[:3]), rng.choice(cl_values)))
            body = rng.choice(bodies_len)
        if framing == "dup":
            fields.append((rng.choice(names[:3]), rng.choice(cl_values)))
        if framing in ("te", "both"):
            fields.append((rng.choice(names[3:5]), rng.choice(te_values)))
            body = rng.choice(chunked_bodies)
        if rng.random() < 0.3:
            fields.append((rng.choice(names[5:]), b"v"))
        rng.shuffle(fields)
        for name, value in fields:
            sep = rng.choice(seps) if rng.random() < 0.2 else b": "
            head += name + sep + value + (rng.choice(ends) if rng.random() < 0.05 else end)
        out.append((f"generated #{i}", head + end + body, None))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--generated", type=int, default=2000)
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    build = subprocess.run(["ceesafec", "build", "-o", "out/http1"], cwd=EXAMPLE, capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr)
        return 1
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([str(EXAMPLE / "out" / "http1")], env=dict(os.environ, CW_PORT=str(port)),
                              stdout=subprocess.PIPE, text=True)
    server.stdout.readline()

    vectors = handwritten() + generated(random.Random(args.seed), args.generated)
    counts = {"agree-accept": 0, "agree-reject": 0, "strict": 0, "lenient": 0, "incomplete": 0}
    failures = []
    try:
        for name, data, tag in vectors:
            oracle = h11_oracle(data)
            if oracle[0] == "incomplete":
                counts["incomplete"] += 1  # h11 waits for more (it reads the sentinel as body): nothing to compare
                continue
            view = server_view(port, data)
            if oracle[0] == "reject":
                if view[0] in ("reject", "none"):
                    counts["agree-reject"] += 1
                elif tag in LENIENT and view[0] == "accept":
                    # The same request without what the RFC says to skip must be framed alike by both.
                    trimmed = data.lstrip(b"\r\n")
                    again = h11_oracle(trimmed)
                    if again[0] == "accept" and again[1] == view[1]:
                        counts["lenient"] += 1
                    else:
                        failures.append((name, data, f"lenient, but framed differently: h11 {again}, server {view}"))
                else:
                    failures.append((name, data, f"h11 rejects; server {view}"))
                continue
            body, keep = oracle[1], oracle[2]
            if view[0] == "accept":
                if view[1] == body and view[2] == keep:
                    counts["agree-accept"] += 1
                else:
                    failures.append((name, data, f"h11 body {body!r} keep {keep}; server body {view[1]!r}, "
                                                 f"sentinel answered {view[2]}"))
            elif view[0] == "reject":
                reason = tag if tag in STRICT else strict_reason(data)
                if reason:
                    counts["strict"] += 1
                    if args.v:
                        print(f"strict ({reason}): {name}: {data!r} -> {view[1]}")
                else:
                    failures.append((name, data, f"h11 accepts (body {body!r}); server rejects {view[1]}, no STRICT reason"))
            else:
                failures.append((name, data, f"h11 accepts; server {view}"))
    finally:
        try:
            with socket.create_connection(("127.0.0.1", port)) as s:
                s.sendall(b"GET /stop HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n")
                s.recv(1024)
        except OSError:
            pass
        try:
            server.wait(timeout=40)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"conformance: {len(vectors)} vectors ({len(handwritten())} hand-written, {args.generated} generated, seed "
          f"{args.seed}): {counts['agree-accept']} accepted alike, {counts['agree-reject']} rejected alike, "
          f"{counts['strict']} rejected by the server only (strict, RFC-allowed), {counts['lenient']} accepted by the "
          f"server only (lenient, RFC-advised), {counts['incomplete']} incomplete "
          f"for h11; {len(failures)} failures")
    for name, data, why in failures[:40]:
        print(f"FAIL {name}: {data!r}\n     {why}")
    return 0 if not failures else 1


def strict_reason(data: bytes):
    """The STRICT tag that explains the server rejecting a generated vector h11 accepts, or None."""
    head = data.split(b"\r\n\r\n")[0]
    line = head.split(b"\n")[0].rstrip(b"\r")
    lower = head.lower()
    if b"content-length" in lower and b"transfer-encoding" in lower:
        return "cl-and-te"
    if line.endswith(b" ") or (b" HTTP/" in line and not line.split(b" HTTP/")[-1].startswith(b"1.")):
        return "version"
    if b"\n" in data.replace(b"\r\n", b""):
        return "bare-lf"
    if b"\r" in data.replace(b"\r\n", b""):
        return "ctl-in-value"
    if b" :" in head or b"\t:" in head:
        return "ws-before-colon"
    if b"\x0b" in head:
        return "ctl-in-value"
    if b"HTTP/1.0" in head and b"transfer-encoding" in head.lower():
        return "te-1.0"
    return None


if __name__ == "__main__":
    sys.exit(main())
