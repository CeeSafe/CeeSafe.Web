"""Mutant check for tests/fuzz/proxy: each mutant puts one defect back into a copy of the tree, the fuzzer is rebuilt
and run for up to 60 s, and it must report a broken invariant.

usage: python3 tests/fuzz/proxy/mutants.py <a copy of the tree> <ceesafec>  (it edits the copy it is given)
"""
import os, subprocess, sys

tree, cc = sys.argv[1], sys.argv[2]
FWD = "packages/web_proxy/src/forwarded.cee"
PH = "packages/web_transport/src/proxyheader.cee"
MUTANTS = [
    ("quoted-pair ignored when splitting the list", FWD,
     "                    if (quoted && b == 92) {\n                        k += 2; // a quoted-pair",
     "                    if (false && quoted && b == 92) {\n                        k += 2; // a quoted-pair"),
    ("a field ending inside quotes is split anyway", FWD,
     "                if (end && quoted) {", "                if (false && end && quoted) {"),
    ("empty elements counted as malformed hops", FWD,
     "                if (element.length > 0) {\n                    list.push(forElement(element)?)?;",
     "                if (true) {\n                    list.push(forElement(element)?)?;"),
    ("for values not unescaped", FWD, "            text.push(value[k + 1])?;", "            text.push(b)?;"),
    ("clientOf: a hidden hop skipped", FWD, "            Hop.Hidden => { return Ok(ClientIs.Hidden); }",
     "            Hop.Hidden => {}"),
    ("unassigned v2 family accepted", PH, "    if (family > 3 || transport > 2) {", "    if (transport > 2) {"),
    ("UNSPEC transport keeps the addresses", PH, "    if (family == 0 || transport == 0) {", "    if (family == 0) {"),
    ("a zone in a TCP6 address accepted", PH,
     "    if (hasZone(text)) {\n        return Err(ProxyError.Malformed);", "    if (false) {\n        return Err(ProxyError.Malformed);"),
    ("UNKNOWN's line not ignored", PH, "(line.length == 7 || line[7] == 32)) {", "line.length == 7) {"),
    ("a prefix mask one bit short", PH, "0xFF00u32 >> truncate<u32>(left)", "0xFF00u32 >> truncate<u32>(left + 1)"),
    ("v1 port with a leading zero accepted", PH, "(text.length > 1 && text[0] == 48)", "(false)"),
    ("v2 length not checked against the header", PH, "    if (header.length != 16 + length) {",
     "    if (header.length < 16 + length) {"),
]
fuzz = os.path.join(tree, "tests/fuzz/proxy")
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
        r = subprocess.run([os.path.join(fuzz, "out/fuzz_proxy")], cwd=fuzz, capture_output=True, text=True,
                           env=dict(os.environ, CW_FUZZ_SECONDS="60", CW_FUZZ_SEED="7"))
        out = (r.stdout + r.stderr).splitlines()
        if r.returncode != 0 and len(out) > 1 and "invariant broken" in out[0]:
            caught += 1
            print(f"caught   {name}: {out[1]}")
        else:
            print(f"MISSED   {name}: exit {r.returncode}, {' '.join(out[:2])}")
    finally:
        open(path, "w").write(original)
print(f"{caught} of {len(MUTANTS)} mutants caught")
