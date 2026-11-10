"""QUIC interop for web_quic (Web ADR 0014 §4, stage 2), through examples/hq over real UDP, with ngtcp2's own example
client speaking hq-interop (HTTP/0.9 over QUIC). The client is an independent QUIC stack's endpoint: ngtcp2's library
as we vendor it, but its example program's connection handling, TLS session and hq-interop codec, not ours.

The expected bytes are the files the script writes (random, compared by SHA-256); a refused or missing path is an
empty answer (web_quic/src/hq.cee).

The client: ngtcp2 1.26.0's `examples/client.cc` with the OpenSSL backend and the hq codec. Upstream's Makefile wires
the hq codec only for wolfSSL (`wsslhqclient`), so it is built by hand from the same sources, with
`-DWITH_EXAMPLE_OSSL -DWITH_EXAMPLE_HQ_PROTO_CODEC` and `hq_client_proto_codec.cc` in place of the HTTP/3 codec
(docs/milestones/M10.md, stage 2's third part, has the commands). It does not verify the server's certificate.

Build the example first (cd examples/hq && ceesafec build), then:
    python3 tests/interop/quic.py --client PATH/TO/osslhqclient [path to the server]
"""
import argparse, hashlib, os, pathlib, shutil, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CERTS = ROOT / "packages/web_tls/tests/certs"
PORT = 14433

checks = []


def check(name, cond, got=""):
    checks.append(bool(cond))
    print(("ok  " if cond else "FAIL") + f" {name}: {got}")


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def fetch(client, paths, download, *extra, timeout=120):
    """Runs the client for `paths` on one connection: (exit code, seconds, its output's tail)."""
    shutil.rmtree(download, ignore_errors=True)
    download.mkdir(parents=True)
    uris = [f"https://localhost:{PORT}{p}" for p in paths]
    cmd = [client, "--quiet", "--no-quic-dump", "--no-http-dump", "--exit-on-all-streams-close",
           f"--download={download}", *extra, "127.0.0.1", str(PORT), *uris]
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, timeout, "timed out"
    tail = r.stdout.decode(errors="replace").strip().splitlines()[-3:]
    return r.returncode, time.monotonic() - start, " | ".join(tail)


def same(served, download, names):
    """The names whose download differs from the served file (missing counts as different)."""
    bad = []
    for n in names:
        got = download / n
        if not got.exists() or digest(got) != digest(served / n):
            bad.append(n)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True, help="ngtcp2's hq-interop client (osslhqclient)")
    ap.add_argument("server", nargs="?", default=str(ROOT / "examples/hq/out/hq"))
    args = ap.parse_args()
    work = pathlib.Path(tempfile.mkdtemp(prefix="cw-quic-"))
    served = work / "root"
    served.mkdir()
    sizes = {"empty.bin": 0, "one.bin": 1, "small.bin": 1000, "mtu.bin": 1452, "large.bin": 5_000_000,
             "huge.bin": 32 * 1024 * 1024}
    for n, size in sizes.items():
        (served / n).write_bytes(os.urandom(size))
    many = [f"m{i:03}.bin" for i in range(250)]
    for i, n in enumerate(many):
        (served / n).write_bytes(os.urandom(100 + 997 * i))
    env = dict(os.environ, CW_ROOT=str(served), CW_PORT=str(PORT), CW_CERT=str(CERTS / "server.pem"),
               CW_KEY=str(CERTS / "server.key"))
    proc = subprocess.Popen([args.server], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        line = proc.stdout.readline().decode().strip()
        check("the server starts", line == "hq: listening", line)
        run(args.client, served, work / "download", sizes, many)
        check("the server is still running", proc.poll() is None, f"exit {proc.poll()}")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)
    print(f"{sum(checks)} of {len(checks)} checks passed")
    sys.exit(0 if all(checks) else 1)


def run(client, served, download, sizes, many):
    # quic-interop-runner's "transfer": several files on one connection, each on its own stream.
    names = ["small.bin", "large.bin"]
    rc, secs, tail = fetch(client, ["/" + n for n in names], download)
    check("transfer: 1000 B and 5 MB on one connection", rc == 0 and not same(served, download, names),
          f"exit {rc}, {secs:.2f} s, differ {same(served, download, names)} {tail}")

    # Edge sizes: an empty file, one byte, one datagram's worth.
    names = ["empty.bin", "one.bin", "mtu.bin"]
    rc, secs, tail = fetch(client, ["/" + n for n in names], download)
    check("edge sizes: 0, 1 and 1452 bytes", rc == 0 and not same(served, download, names),
          f"exit {rc}, differ {same(served, download, names)} {tail}")

    # 32 MiB: past the connection's flow-control window (16 MiB), so credit must come back as the client reads.
    rc, secs, tail = fetch(client, ["/huge.bin"], download)
    check("32 MiB, twice the connection's window", rc == 0 and not same(served, download, ["huge.bin"]),
          f"exit {rc}, {secs:.2f} s, {32 / secs if secs else 0:.1f} MiB/s {tail}")

    # quic-interop-runner's "multiplexing": many streams on one connection, more than the 100 a client may have open
    # at first, so the server must raise the limit as streams close (RFC 9000 §4.6).
    rc, secs, tail = fetch(client, ["/" + n for n in many], download)
    check("multiplexing: 250 files on one connection", rc == 0 and not same(served, download, many),
          f"exit {rc}, {secs:.2f} s, differ {len(same(served, download, many))} {tail}")

    # Loss, simulated by the client on what it sends (-t) and what it receives (-r). Lost server packets make the
    # server retransmit from the bytes it holds (Web ADR 0014 amendment 2 item 2).
    for t, r in (("0.1", "0"), ("0", "0.1"), ("0.1", "0.1"), ("0.25", "0.25")):
        rc, secs, tail = fetch(client, ["/small.bin", "/large.bin"], download, f"--tx-loss={t}", f"--rx-loss={r}")
        check(f"loss: {t} sent, {r} received; 5 MB whole",
              rc == 0 and not same(served, download, ["small.bin", "large.bin"]),
              f"exit {rc}, {secs:.2f} s, differ {same(served, download, ['small.bin', 'large.bin'])} {tail}")

    # Refused and missing paths: an empty answer, and the connection carries on with the next stream.
    refused = ["/missing.bin", "/../etc/passwd", "/a%5Cb", "/c:x"]
    rc, secs, tail = fetch(client, [*refused, "/small.bin"], download)
    empty = all(not f.exists() or f.stat().st_size == 0
                for f in download.iterdir() if f.name != "small.bin")
    # Each answer ends with its FIN at once: the client's idle timeout (30 s) is not what ends the connection.
    check("refused and missing paths answer empty at once, then a file", rc == 0 and empty and secs < 5 and
          not same(served, download, ["small.bin"]),
          f"exit {rc}, {secs:.2f} s, files {sorted((f.name, f.stat().st_size) for f in download.iterdir())} {tail}")

    # Many connections in turn: each is routed, served and ended, and the server's table does not fill.
    fails = 0
    start = time.monotonic()
    for _ in range(200):
        rc, _, _ = fetch(client, ["/small.bin"], download, timeout=30)
        if rc != 0 or same(served, download, ["small.bin"]):
            fails += 1
    check("200 connections in turn", fails == 0, f"{fails} failed, {time.monotonic() - start:.1f} s")

    # Connections at once.
    procs = []
    for k in range(20):
        d = download.parent / f"par{k}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        cmd = [client, "--quiet", "--no-quic-dump", "--no-http-dump", "--exit-on-all-streams-close",
               f"--download={d}", "127.0.0.1", str(PORT), f"https://localhost:{PORT}/large.bin"]
        procs.append((d, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
    bad = 0
    for d, p in procs:
        try:
            rc = p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        if rc != 0 or same(served, d, ["large.bin"]):
            bad += 1
    check("20 connections at once, 5 MB each", bad == 0, f"{bad} failed")


if __name__ == "__main__":
    main()
