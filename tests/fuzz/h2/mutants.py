"""Mutant check for tests/fuzz/h2: each mutant puts one defect back into a copy of the tree, the fuzzer is rebuilt
and run for up to 60 s, and it must report a broken invariant.

usage: python3 tests/fuzz/h2/mutants.py <a copy of the tree> <ceesafec>  (it edits the copy it is given)
"""
import os, subprocess, sys

tree, cc = os.path.abspath(sys.argv[1]), sys.argv[2]
CONN = "packages/web_http2/src/connection.cee"
HPACK = "packages/web_http2/src/hpack.cee"
VALIDATE = "packages/web_http2/src/validate.cee"
MUTANTS = [
    # The two defects this fuzzer found, put back.
    ("a control byte in a field name accepted (§8.2.1)", VALIDATE,
     "c <= 32 || c == 58", "c == 0 || c == 10 || c == 13 || c == 32 || c == 58"),
    ("RST_STREAM for a zero WINDOW_UPDATE on an idle stream (§5.1, §6.4)", CONN,
     "if (!this.streams.contains(id) && this.idle(id)) {", "if (false) {"),
    # The client role.
    ("an interim 1xx head reported as the response", CONN, "if (shape.status < 200) {", "if (shape.status < 100) {"),
    ("a head ending the stream despite its content-length", CONN, "mismatch = end && n != 0;", "mismatch = false;"),
    ("DATA ending short of content-length", CONN,
     "if (s.received > n || (end && s.received != n)) {", "if (s.received > n) {"),
    ("a HEAD response's content-length taken as its body", CONN,
     "let noContent = s.headRequest || shape.status == 204", "let noContent = shape.status == 204"),
    ("trailers ending a body short of content-length", CONN,
     "Some(n) => { mismatch = s.received != n; }", "Some(n) => { mismatch = false; }"),
    ("DATA before the response's head", CONN, "        if (beforeHead) {", "        if (false) {"),
    ("a server's HEADERS on an even stream ignored", CONN,
     "return if (id % 2 == 1) id >= this.nextOwn else true;", "return if (id % 2 == 1) id >= this.nextOwn else false;"),
    ("GOAWAY naming the client's own next stream", CONN,
     "Frames.writeGoAway(mut this.out, this.lastStream,", "Frames.writeGoAway(mut this.out, this.nextOwn,"),
    ("a PING acknowledged with other data", CONN, "bytes[i] = payload[i];", "bytes[i] = payload[7 - i];"),
    # HPACK's dynamic table.
    ("the encoder's cap ignored", HPACK,
     "let size = if (peerLimit < this.cap) peerLimit else this.cap;", "let size = peerLimit;"),
    ("a sensitive field indexed", HPACK,
     "if (sensitive || cost > this.table.maxSize / 2) {", "if (cost > this.table.maxSize / 2) {"),
    ("the smallest table size not signalled (RFC 7541 §4.2)", HPACK,
     "if (this.smallest < this.target) {", "if (false) {"),
]
fuzz = os.path.join(tree, "tests/fuzz/h2")
caught = 0
for name, rel, old, new in MUTANTS:
    path = os.path.join(tree, rel)
    original = open(path).read()
    if original.count(old) != 1:
        print(f"SKIP (pattern not found once): {name}")
        continue
    open(path, "w").write(original.replace(old, new))
    try:
        b = subprocess.run([cc, "build", "--cflag", "-fsanitize=address,undefined", "--cflag", "-fno-omit-frame-pointer"],
                           cwd=fuzz, capture_output=True, text=True)
        if b.returncode != 0:
            print(f"BUILD FAILED: {name}\n{b.stdout[-400:]}{b.stderr[-400:]}")
            continue
        r = subprocess.run([os.path.join(fuzz, "out/fuzz_h2")], cwd=fuzz, capture_output=True, text=True,
                           env=dict(os.environ, CW_FUZZ_SECONDS="60", CW_FUZZ_SEED="7"))
        out = (r.stdout + r.stderr).splitlines()
        at = next((i for i, line in enumerate(out) if "invariant broken" in line), None)
        if r.returncode != 0 and at is not None and at + 1 < len(out):
            caught += 1
            print(f"caught   {name}: {out[at + 1]}")
        else:
            print(f"MISSED   {name}: exit {r.returncode}, {' '.join(out[:2])[:160]}")
    finally:
        open(path, "w").write(original)
print(f"{caught} of {len(MUTANTS)} mutants caught")
