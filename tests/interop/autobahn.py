"""Autobahn|Testsuite's fuzzingclient against examples/ws_echo (Web ADR 0010 §5, the M6 exit gate).

Runs the crossbario/autobahn-testsuite Docker image (pulled once: `docker pull crossbario/autobahn-testsuite`), which
connects to the echo server through host.docker.internal, then summarises reports/index.json: every case must be OK
or INFORMATIONAL, in its behaviour and its close behaviour. UNIMPLEMENTED cases (permessage-deflate, 12.* and 13.*,
when the extension is not offered) are listed apart; `--require-all` fails on them too.

Build the example first (cd examples/ws_echo && ceesafec build), then:
    python3 tests/interop/autobahn.py [--reports DIR] [--cases 1.*,2.*] [--require-all]
"""
import argparse, collections, json, os, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
ECHO = ROOT / "examples/ws_echo/out/ws_echo"
PORT = 9001
GOOD = ("OK", "INFORMATIONAL")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default=tempfile.mkdtemp(prefix="autobahn-"))
    ap.add_argument("--cases", default="*")
    ap.add_argument("--require-all", action="store_true")
    args = ap.parse_args()
    reports = pathlib.Path(args.reports).resolve()
    reports.mkdir(parents=True, exist_ok=True)
    config = reports / "config"
    config.mkdir(exist_ok=True)
    (config / "fuzzingclient.json").write_text(json.dumps({
        "outdir": "/reports",
        "servers": [{"agent": "CeeSafe.Web", "url": f"ws://host.docker.internal:{PORT}"}],
        "cases": args.cases.split(","),
        "exclude-cases": [],
        "exclude-agent-cases": {},
    }, indent=2))
    env = dict(os.environ, CW_PORT=str(PORT), CW_PING_MS="0")
    server = subprocess.Popen([str(ECHO)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ready = server.stdout.readline().decode().strip()
    if ready != "ws_echo: listening":
        server.kill()
        sys.exit(f"ws_echo did not start: {ready}")
    try:
        run = subprocess.run(["docker", "run", "--rm", "-v", f"{config}:/config", "-v", f"{reports}:/reports",
                              "crossbario/autobahn-testsuite", "wstest", "-m", "fuzzingclient",
                              "-s", "/config/fuzzingclient.json"], capture_output=True, text=True)
    finally:
        server.kill()
        server.wait()
    if run.returncode != 0:
        print(run.stdout[-2000:], run.stderr[-2000:])
        sys.exit(f"wstest failed: {run.returncode}")
    cases = json.load(open(reports / "index.json"))["CeeSafe.Web"]
    counts = collections.Counter(v["behavior"] for v in cases.values())
    failed, unimplemented = [], []
    for k, v in sorted(cases.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
        if v["behavior"] == "UNIMPLEMENTED":
            unimplemented.append(k)
        elif v["behavior"] not in GOOD or v["behaviorClose"] not in GOOD:
            failed.append((k, v["behavior"], v["behaviorClose"]))
    print(f"autobahn fuzzingclient: {len(cases)} cases: {dict(counts)}; reports in {reports}")
    for f in failed:
        print("FAIL", *f)
    if unimplemented:
        groups = sorted({k.rsplit(".", 1)[0] for k in unimplemented})
        print(f"unimplemented: {len(unimplemented)} cases ({', '.join(groups[:6])}{', …' if len(groups) > 6 else ''})")
    sys.exit(1 if failed or (args.require_all and unimplemented) else 0)


if __name__ == "__main__":
    main()
