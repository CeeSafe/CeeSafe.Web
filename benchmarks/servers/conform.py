#!/usr/bin/env python3
"""The comparison servers' contract (Web ADR 0015 §6, plan §8): every server answers the same routes with the same
bytes, over HTTP/1.1 and h2c, before any load is run against it.

usage: python3 benchmarks/servers/conform.py --port 18190 [--h2c-port 18191 | --no-h2c] [--name axum]

The server must already listen on 127.0.0.1:PORT, for HTTP/1.1 and h2c alike, or for h2c on H2C_PORT when it cannot
serve both on one cleartext port (Kestrel). `--no-h2c` checks HTTP/1.1 alone, for a server without HTTP/2 (Relic). The expected bodies are built here, from the contract below, not
taken from any server:

  GET  /small    200, "hello, world\\n" (13 bytes)
  GET  /4k       200, 4096 bytes: byte i is 'a' + i % 26
  GET  /64k      200, 65536 bytes, the same alphabet
  GET  /stream   200, 1 MiB: a 16 KiB piece of that alphabet, 64 times (the alphabet restarts with each piece)
  GET  /json     200, content-type application/json, {"message":"Hello, World!"} (no spaces)
  POST /echo     200, the request's body back (here 100,000 and 0 bytes)

HTTP/1.1 is checked on one kept-alive connection (`http.client`), every route twice, so a server that closes after a
response fails. h2c is HTTP/2 with prior knowledge (`nghttp`, from nghttp2, on PATH): each route once, the status and
content type from `nghttp -v`'s frames, the body from a second request without `-v`. Exit status 0 if every check passes.
"""
import argparse, http.client, os, random, re, shutil, subprocess, sys, tempfile


def alphabet(n):
    return bytes(97 + i % 26 for i in range(n))


SMALL = b"hello, world\n"
PIECE = alphabet(16384)
GETS = {
    "/small": SMALL,
    "/4k": alphabet(4096),
    "/64k": alphabet(65536),
    "/stream": PIECE * 64,
    "/json": b'{"message":"Hello, World!"}',
}
ECHOES = [bytes(random.Random(7).randrange(256) for _ in range(100000)), b""]


def check_h1(port, problems):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    for _ in range(2):
        for path, want in GETS.items():
            conn.request("GET", path)
            r = conn.getresponse()
            body = r.read()
            if r.status != 200 or body != want:
                problems.append(f"h1 GET {path}: status {r.status}, {len(body)} bytes; wanted 200, {len(want)} bytes"
                                + ("" if r.status != 200 else ", the same bytes"))
            if path == "/json" and (r.getheader("content-type") or "").split(";")[0].strip() != "application/json":
                problems.append(f"h1 GET /json: content-type {r.getheader('content-type')!r}")
            if r.getheader("connection", "").lower() == "close" or r.will_close:
                problems.append(f"h1 GET {path}: the server closes the connection")
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        for data in ECHOES:
            conn.request("POST", "/echo", body=data, headers={"content-type": "application/octet-stream"})
            r = conn.getresponse()
            body = r.read()
            if r.status != 200 or body != data:
                problems.append(f"h1 POST /echo ({len(data)} bytes): status {r.status}, {len(body)} bytes back")
    conn.close()


def nghttp(port, path, data, problems):
    """One h2c request, made twice: (status, headers, body), or Nones. With `-v` nghttp prints the frames, from which
    the response's header fields are read (`recv (stream_id=N) name: value`), mixed with the body; without it, it
    prints the body alone."""
    with tempfile.TemporaryDirectory() as d:
        url = f"http://127.0.0.1:{port}{path}"
        extra = []
        if data is not None:
            src = os.path.join(d, "request-body")
            with open(src, "wb") as f:
                f.write(data)
            extra = ["-d", src]
        p = subprocess.run(["nghttp", "-v"] + extra + [url], capture_output=True, timeout=30)
        headers = {}
        for line in p.stdout.decode(errors="replace").splitlines():
            m = re.search(r"\] recv \(stream_id=\d+\) ([^:\s][^:]*|:status): (.*)$", line)
            if m:
                headers.setdefault(m.group(1).lower(), m.group(2).strip())
        if ":status" not in headers:
            problems.append(f"h2c {path}: no response ({p.stderr.decode(errors='replace')[-200:].strip()})")
            return None, None, None
        body = subprocess.run(["nghttp"] + extra + [url], capture_output=True, timeout=30).stdout
        return int(headers[":status"]), headers, body


def check_h2c(port, problems):
    for path, want in GETS.items():
        status, headers, body = nghttp(port, path, None, problems)
        if status is None:
            continue
        if status != 200 or body != want:
            problems.append(f"h2c GET {path}: status {status}, {len(body)} bytes; wanted 200, {len(want)} bytes")
        if path == "/json" and headers.get("content-type", "").split(";")[0].strip() != "application/json":
            problems.append(f"h2c GET /json: content-type {headers.get('content-type')!r}")
    for data in ECHOES[:1]:
        status, _, body = nghttp(port, "/echo", data, problems)
        if status is not None and (status != 200 or body != data):
            problems.append(f"h2c POST /echo ({len(data)} bytes): status {status}, {len(body)} bytes back")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--h2c-port", type=int, help="h2c's port, when it is not --port")
    ap.add_argument("--no-h2c", action="store_true", help="the server has no HTTP/2: check HTTP/1.1 alone")
    ap.add_argument("--name", default="server")
    a = ap.parse_args()
    if not a.no_h2c and shutil.which("nghttp") is None:
        sys.exit("conform: nghttp (nghttp2) is not on PATH")
    problems = []
    check_h1(a.port, problems)
    if not a.no_h2c:
        check_h2c(a.h2c_port or a.port, problems)
    h2c = "no h2c (--no-h2c)" if a.no_h2c else f"{len(GETS) + 1} h2c"
    print(f"conform {a.name}: {len(GETS) * 2 + 2} HTTP/1.1 requests on one connection, {h2c}; "
          f"{len(problems)} problems")
    for p in problems:
        print("  FAIL " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
