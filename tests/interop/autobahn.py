"""Autobahn|Testsuite against CeeSafe.Web (Web ADR 0010 §5, the M6 exit gate): its fuzzingclient against
examples/ws_echo, or with --client its fuzzingserver against examples/ws_client (amendment 2).

Runs the crossbario/autobahn-testsuite Docker image (pulled once: `docker pull crossbario/autobahn-testsuite`), which
connects to the echo server through host.docker.internal, then summarises reports/index.json: every case must be OK
or INFORMATIONAL, in its behaviour and its close behaviour. UNIMPLEMENTED cases (permessage-deflate, 12.* and 13.*,
when the extension is not offered) are listed apart; `--require-all` fails on them too.

With --client, the fuzzingserver listens in the container on port 9001, published to this machine, and the client
runs every case against ws://127.0.0.1:9001 (Autobahn's protocol: getCaseCount, runCase, updateReports).

Build the example first (cd examples/ws_echo && ceesafec build; examples/ws_client for --client), then:
    python3 tests/interop/autobahn.py [--client] [--reports DIR] [--cases 1.*,2.*] [--require-all]
"""
import argparse, collections, json, os, pathlib, socket, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
ECHO = ROOT / "examples/ws_echo/out/ws_echo"
CLIENT = ROOT / "examples/ws_client/out/ws_client"
PORT = 9001
GOOD = ("OK", "INFORMATIONAL")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default=tempfile.mkdtemp(prefix="autobahn-"))
    ap.add_argument("--cases", default="*")
    ap.add_argument("--require-all", action="store_true")
    ap.add_argument("--client", action="store_true", help="the fuzzingserver against examples/ws_client")
    args = ap.parse_args()
    reports = pathlib.Path(args.reports).resolve()
    reports.mkdir(parents=True, exist_ok=True)
    config = reports / "config"
    config.mkdir(exist_ok=True)
    if args.client:
        fuzzingserver(args, reports, config)
    else:
        fuzzingclient(args, reports, config)
    summarise(args, reports, "fuzzingserver" if args.client else "fuzzingclient")


def fuzzingserver(args, reports, config):
    (config / "fuzzingserver.json").write_text(json.dumps({
        "url": f"ws://0.0.0.0:{PORT}",
        "outdir": "/reports",
        "cases": args.cases.split(","),
        "exclude-cases": [],
        "exclude-agent-cases": {},
    }, indent=2))
    name = f"cw-fuzzingserver-{os.getpid()}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"{PORT}:{PORT}",
                    "-v", f"{config}:/config", "-v", f"{reports}:/reports", "crossbario/autobahn-testsuite",
                    "wstest", "-m", "fuzzingserver", "-s", "/config/fuzzingserver.json"], check=True,
                   capture_output=True)
    try:
        for _ in range(120):  # the image is amd64: under emulation it starts slowly
            try:
                socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
                time.sleep(2)
                break
            except OSError:
                time.sleep(1)
        env = dict(os.environ, CW_URL=f"ws://127.0.0.1:{PORT}", CW_MODE="autobahn", CW_AGENT="CeeSafe.Web")
        run = subprocess.run([str(CLIENT)], env=env, capture_output=True, text=True)
        lines = run.stdout.splitlines()
        if run.returncode != 0 or "ws_client: reports updated" not in lines:
            print(run.stdout[-2000:], run.stderr[-2000:])
            sys.exit(f"ws_client failed: {run.returncode}")
        failures = [l for l in lines if l.startswith("ws_client: failed")]
        if failures:
            print(f"client reported {len(failures)} failure line(s), e.g. {failures[0]}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def fuzzingclient(args, reports, config):
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


def summarise(args, reports, mode):
    cases = json.load(open(reports / "index.json"))["CeeSafe.Web"]
    counts = collections.Counter(v["behavior"] for v in cases.values())
    failed, unimplemented = [], []
    for k, v in sorted(cases.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
        if v["behavior"] == "UNIMPLEMENTED":
            unimplemented.append(k)
        elif v["behavior"] not in GOOD or v["behaviorClose"] not in GOOD:
            failed.append((k, v["behavior"], v["behaviorClose"]))
    print(f"autobahn {mode}: {len(cases)} cases: {dict(counts)}; reports in {reports}")
    for f in failed:
        print("FAIL", *f)
    if unimplemented:
        groups = sorted({k.rsplit(".", 1)[0] for k in unimplemented})
        print(f"unimplemented: {len(unimplemented)} cases ({', '.join(groups[:6])}{', …' if len(groups) > 6 else ''})")
    sys.exit(1 if failed or (args.require_all and unimplemented) else 0)


if __name__ == "__main__":
    main()
