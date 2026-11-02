#!/usr/bin/env python3
"""Run CeeSafe.Web's test packages (Web ADR 0005 §2.1).

usage: python3 tools/run_tests.py [--ceesafec PATH] [--sanitize] [--no-native] [--filter TEXT] [--update-lock]
                                  [--alloc-sweep]

A file named native-xfail holds the reason a test's native build is known to fail because of a CeeSafe bug (for
example "B2", docs/ceesafe-upstream-work.md). The test still runs in the interpreter and must pass there; the native
build must still fail, and the runner says XPASS (a failure) once it no longer does, so the marker cannot go stale.

A file named native-env sets the environment of a test's native runs on one platform, to keep a CeeSafe runtime bug
out of the way while it is open. Each line is "<platform> KEY=VALUE <reason>", with platform a prefix of
sys.platform ("darwin", "linux", "win"), for example "darwin CEESAFE_REACTOR_POLL=1 B4". The bug's probe in
tests/probes tracks when the line can go; the runner prints the line on every run so it is not forgotten.

A run that does not finish in time is a failure ("timed out"), not a crash of the runner.

--alloc-sweep (Web ADR 0005 §2.2): after a test passes natively, run it again once per allocation attempt with
that attempt failing (CEESAFE_FAIL_ALLOC_AT). Each run must exit 0 (no panic, no crash) with live=0. Its output
may differ: a test reports the failure as a value.

A test is a directory under tests/ (not tests/probes) holding an executable package (ceesafe.toml, src/main.cee)
and expected.txt, its exact standard output. Each test is checked, then run twice, in the interpreter and natively,
with the runtime's allocation report on. Both runs must print expected.txt exactly, exit 0, and end with live=0
(no leaked allocation). A file named native-only in the test directory skips the interpreter run (C code).

expected.txt is written by hand from the test's own reasoning or an outside oracle, never captured from a run
(Web ADR 0005 §2.2).
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = re.compile(r"ceesafe alloc: attempts=\d+ live=(\d+)\n?")


def run(cmd, cwd, env=None, timeout=120):
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return subprocess.CompletedProcess(cmd, 124, out, f"timed out after {timeout} s\n")


def native_env(t: pathlib.Path):
    """The environment lines of t/native-env that apply here: ({KEY: VALUE}, [reasons])."""
    values, reasons = {}, []
    f = t / "native-env"
    if not f.exists():
        return values, reasons
    for line in f.read_text(encoding="utf-8").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or line.lstrip().startswith("#"):
            continue
        if sys.platform.startswith(parts[0]) and "=" in parts[1]:
            key, value = parts[1].split("=", 1)
            values[key] = value
            reasons.append(f"{parts[1]} ({parts[2] if len(parts) > 2 else 'no reason given'})")
    return values, reasons


def leak_and_strip(stderr: str):
    m = REPORT.search(stderr)
    live = int(m.group(1)) if m else None
    return live, REPORT.sub("", stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ceesafec", default=shutil.which("ceesafec") or "ceesafec")
    ap.add_argument("--sanitize", action="store_true", help="native builds with ASan and UBSan")
    ap.add_argument("--no-native", action="store_true")
    ap.add_argument("--filter", default="")
    ap.add_argument("--update-lock", action="store_true", help="run ceesafec fetch in each test first")
    ap.add_argument("--alloc-sweep", action="store_true", help="fail each allocation in turn (native)")
    args = ap.parse_args()

    tests = sorted(p.parent for p in (ROOT / "tests").glob("**/expected.txt")
                   if "probes" not in p.parts and args.filter in str(p.parent))
    if not tests:
        print("run_tests: no tests found", file=sys.stderr)
        return 1

    env = dict(os.environ, CEESAFE_ALLOC_REPORT="1")
    failures = 0
    work = pathlib.Path(tempfile.mkdtemp(prefix="cw-tests-"))
    for t in tests:
        name = str(t.relative_to(ROOT / "tests"))
        expected = (t / "expected.txt").read_text(encoding="utf-8")
        problems = []

        if args.update_lock or not (t / "ceesafe.lock").exists():
            r = run([args.ceesafec, "fetch", "--offline"], t)
            if r.returncode != 0:
                problems.append("fetch failed:\n" + r.stderr)
        if not problems:
            r = run([args.ceesafec, "check"], t)
            if r.returncode != 0:
                problems.append("check failed:\n" + r.stderr)

        modes = []
        extra_env, env_reasons = native_env(t)
        native_env_vars = dict(env, **extra_env)
        xfail = (t / "native-xfail").read_text(encoding="utf-8").strip() if (t / "native-xfail").exists() else None
        notes = []
        if not problems:
            if not (t / "native-only").exists():
                modes.append(("interp", [args.ceesafec, "run", "--interp"]))
            if not args.no_native:
                exe = work / name.replace("/", "_")
                build = [args.ceesafec, "build", "-o", str(exe)]
                if args.sanitize:
                    build += ["--cflag", "-fsanitize=address,undefined", "--cflag", "-g"]
                r = run(build, t)
                if xfail is not None:
                    if r.returncode == 0:
                        problems.append(f"XPASS: the native build succeeds now; remove native-xfail ({xfail})")
                    else:
                        notes.append(f"native xfail: {xfail}")
                elif r.returncode != 0:
                    problems.append("build failed:\n" + r.stderr)
                else:
                    modes.append(("native", [str(exe)]))
                    notes.extend(f"native env {r}" for r in env_reasons)

        for mode, cmd in modes:
            r = run(cmd, t, env=native_env_vars if mode == "native" else env)
            live, stderr = leak_and_strip(r.stderr)
            if r.returncode != 0:
                problems.append(f"{mode}: exit {r.returncode}\n{stderr}")
            if r.stdout != expected:
                problems.append(f"{mode}: output differs\n--- expected\n{expected}--- got\n{r.stdout}")
            if live is None:
                problems.append(f"{mode}: no allocation report (CEESAFE_ALLOC_REPORT)")
            elif live != 0:
                problems.append(f"{mode}: {live} allocation(s) leaked")
            if stderr.strip():
                problems.append(f"{mode}: unexpected standard error\n{stderr}")

        swept = 0
        if args.alloc_sweep and not problems and any(m == "native" for m, _ in modes):
            exe = [cmd for m, cmd in modes if m == "native"][0]
            first = run(exe, t, env=native_env_vars)
            m = re.search(r"ceesafe alloc: attempts=(\d+)", first.stderr)
            attempts = int(m.group(1)) if m else 0
            for k in range(1, attempts + 1):
                r = run(exe, t, env=dict(native_env_vars, CEESAFE_FAIL_ALLOC_AT=str(k)))
                live, stderr = leak_and_strip(r.stderr)
                if r.returncode != 0:
                    problems.append(f"alloc-sweep {k}/{attempts}: exit {r.returncode}\n{stderr}")
                elif live != 0:
                    problems.append(f"alloc-sweep {k}/{attempts}: {live} allocation(s) leaked")
                if problems:
                    break
            swept = attempts

        if problems:
            failures += 1
            print(f"FAIL {name}")
            for p in problems:
                print("  " + p.replace("\n", "\n  ").rstrip())
        else:
            sweep = f", alloc-sweep {swept}" if args.alloc_sweep and swept else ""
            extra = "".join(f"; {n}" for n in notes)
            print(f"ok   {name} ({', '.join(m for m, _ in modes)}{sweep}{extra})")

    shutil.rmtree(work, ignore_errors=True)
    print(f"run_tests: {len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
