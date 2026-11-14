#!/usr/bin/env python3
"""Run CeeSafe.Web's tests (Web ADR 0005 §2.1, amendment 1): `ceesafec test` (CeeSafe ADR 0049) in every package
that has a tests/ directory.

usage: python3 tools/run_tests.py [--ceesafec PATH] [--filter TEXT] [--no-interp] [--sanitize] [--alloc-sweep]
                                  [--cancel-sweep] [--timeout SECONDS]

Tests are `@test` functions in packages/<name>/tests/*.cee, part of their package. Each runs as its own process,
natively, and must pass with no allocation left live (a leak fails it); a test past --timeout (default 120 s) fails.
In the interpreter a package may have a longer limit (INTERP_TIMEOUTS: web_ws, 300 s).
By default every package is run twice, natively and in the interpreter (`--interp`: virtual clock, fixed order). A
package with C of its own (`c = [...]`), or depending on web_tls, is not interpreted: the interpreter runs no C
(CeeSafe spec §28.6; Web ADR 0002 §4).

--sanitize    the native run with ASan and UBSan (`--cflag -fsanitize=address,undefined`).
--alloc-sweep each passing test again once per allocation, that allocation failing (`--sweep alloc`): no panic,
              no leak.
--cancel-sweep each passing test again once per suspension point, cancelled there (`--sweep cancel`, sampled past
              200): no panic, no leak.

Expected values in tests are written by hand from the test's own reasoning or an outside oracle, never captured
from a run (Web ADR 0005 §2.2).
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A package's least --timeout in the interpreter, where one of its tests needs more than the default.
# web_ws: sha1_rfc3174_vectors hashes RFC 3174's million-byte vector, 69-84 s alone on macOS arm64 and past 120 s on
# a CI runner once (pull request #22's ceesafe-main job, 2026-11-14; docs/toolchain.md).
INTERP_TIMEOUTS = {"web_ws": 300}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ceesafec", default=shutil.which("ceesafec") or "ceesafec")
    ap.add_argument("--filter", default="", help="only tests whose name contains this")
    ap.add_argument("--no-interp", action="store_true", help="skip the interpreter run")
    ap.add_argument("--sanitize", action="store_true", help="native run with ASan and UBSan")
    ap.add_argument("--alloc-sweep", action="store_true", help="fail each allocation in turn")
    ap.add_argument("--cancel-sweep", action="store_true", help="cancel at each suspension point in turn")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--exclude", default="", help="packages to leave out, comma-separated (CI's OpenSSL 3.0 job leaves"
                    " out web_quic and web_http3, which need OpenSSL 3.5: Web ADR 0014 §3.3)")
    args = ap.parse_args()
    # One line per run as it ends, even into a pipe: a job killed partway still shows where it got to.
    sys.stdout.reconfigure(line_buffering=True)
    if os.sep in args.ceesafec or "/" in args.ceesafec:
        args.ceesafec = os.path.abspath(args.ceesafec)  # the tests run from each package's directory

    excluded = {n for n in args.exclude.split(",") if n}
    packages = sorted(p.parent for p in (ROOT / "packages").glob("*/tests")
                      if any(p.glob("*.cee")) and p.parent.name not in excluded)
    if not packages:
        print("run_tests: no packages with tests", file=sys.stderr)
        return 1

    native = ["--timeout", str(args.timeout)]
    if args.sanitize:
        native += ["--cflag", "-fsanitize=address,undefined", "--cflag", "-fno-omit-frame-pointer"]
    modes = [("native", native)]
    if not args.no_interp:
        modes.append(("interp", ["--interp"]))  # its --timeout is per package: interp_timeout
    if args.alloc_sweep:
        modes.append(("alloc sweep", native + ["--sweep", "alloc"]))
    if args.cancel_sweep:
        modes.append(("cancel sweep", native + ["--sweep", "cancel"]))

    failed = 0
    skipped = 0
    for package in packages:
        name = package.name
        for mode, flags in modes:
            if mode == "interp" and has_c(package):
                skipped += 1
                print(f"skip {name} ({mode}): it runs C, which the interpreter cannot")
                continue
            command = [args.ceesafec, "test"] + flags
            if mode == "interp":
                command += ["--timeout", str(max(args.timeout, INTERP_TIMEOUTS.get(name, 0)))]
            if args.filter:
                command += ["--filter", args.filter]
            start = time.monotonic()
            r = subprocess.run(command, cwd=package, capture_output=True, text=True)
            took = f"{time.monotonic() - start:.0f} s"
            lines = (r.stdout + r.stderr).rstrip().splitlines()
            summary = next((l for l in reversed(lines) if l.startswith("ceesafec test:")), "no summary")
            if r.returncode == 0:
                print(f"ok   {name} ({mode}): {summary.removeprefix('ceesafec test: ')} ({took})")
            else:
                failed += 1
                print(f"FAIL {name} ({mode}, {took})")
                print("  " + "\n  ".join(lines))
    print(f"run_tests: {len(packages) * len(modes) - failed - skipped} runs passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


def has_c(package: pathlib.Path) -> bool:
    """Whether the package has C files of its own, or depends on web_tls (whose shim is C)."""
    manifest = (package / "ceesafe.toml").read_text()
    return any(line.strip().startswith("c = [") for line in manifest.splitlines()) or "web_tls" in manifest.split(
        "[dependencies]", 1)[-1]


if __name__ == "__main__":
    sys.exit(main())
