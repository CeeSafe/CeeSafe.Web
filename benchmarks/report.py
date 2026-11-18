#!/usr/bin/env python3
"""The benchmark report (Web ADR 0015 §6), and the regression gate (Web ADR 0005 §2.4), from run.py's JSON files.

usage: python3 benchmarks/report.py RUN.json... [-o docs/benchmarks/v1.0.md] [--title TEXT]
                                    [--baseline OLD.json...] [--gate 5] [--advisory]

Each RUN.json is one `benchmarks/http/run.py --json` file: CeeSafe.Web's server, nginx or a comparison server. The
report has:
- the sources: each file with the start of its SHA-256, so the raw results beside the report are the ones it read;
- each run's environment (Web ADR 0005 §2.4: the machine, OS and kernel, the load before the run, the compiler and
  flags, the reactor, the concurrency, the payloads, the TLS settings and the warm-up), and a warning when the runs
  were not on one machine with one kernel;
- for each mode and route, every server's rows: requests a second, latency p50, p95, p99 and p99.9 in microseconds,
  the peak RSS, the CPU (cores kept busy, and microseconds a request) and the failed requests;
- CeeSafe.Web's allocations per request;
- bytes per idle connection, and TLS handshakes a second (oha), where the runs measured them.

Files from before run.py recorded an environment (a bare list of rows) are read too; their environment is "not
recorded", and rows without a p95 show "–".

With --baseline (the previous release's CeeSafe.Web runs, on the same machine), each of CeeSafe.Web's rows is set
against the baseline's row of the same reactor, mode, route and connections. A row regresses when its requests a
second fall, or its p99 rises, by more than --gate per cent (5, Web ADR 0005 §2.4). The exit status is 1 if a row
regresses, and 2 if the baseline was not run on the same machine and kernel (the gate cannot be judged), unless
--advisory, which reports the same and exits 0. Without --baseline it exits 0.
"""
import argparse, hashlib, json, pathlib, sys

GATE = 5.0
MODES = ("h1", "h2c", "tls-h1", "tls-h2")
ROUTES = ("small", "4k", "64k", "stream", "json", "echo", "file", "file1g")
# What h2load negotiated on a TLS row (run.py records it).
TLS_FIELDS = ("tls_protocol", "tls_cipher", "tls_key_exchange", "alpn")


class Run:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        raw = self.path.read_bytes()
        self.sha = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        if isinstance(data, list):
            self.env, self.results = None, data
        else:
            self.env, self.results = data.get("environment"), data.get("results", [])

    def same_host(self):
        """What has to match for two runs to be compared: the machine and the kernel."""
        if not self.env:
            return None
        return json.dumps({"machine": self.env.get("machine"), "kernel": self.env.get("kernel")}, sort_keys=True)


def label(r):
    reactor = r.get("reactor")
    return r["server"] + (f" ({reactor})" if reactor and reactor != "default" else "")


def cell(value):
    return "–" if value is None or value == "" else str(value)


def toolchain_text(t):
    if not t:
        return "–"
    return "; ".join(f"{k} {v}" if k != "flags" else f"flags: {v}" for k, v in t.items() if v)


def environment_table(runs):
    names = [r.path.name for r in runs]
    out = ["| | " + " | ".join(f"`{n}`" for n in names) + " |", "|---" * (len(runs) + 1) + "|"]

    def field(title, get):
        values = []
        for r in runs:
            try:
                values.append(cell(get(r.env)) if r.env else "not recorded")
            except (KeyError, TypeError):
                values.append("–")
        out.append(f"| {title} | " + " | ".join(v.replace("|", "\\|") for v in values) + " |")

    field("date (UTC)", lambda e: e["date_utc"])
    field("commit", lambda e: e["commit"])
    field("CPU", lambda e: e["machine"]["cpu"])
    field("logical CPUs", lambda e: e["machine"]["logical_cpus"])
    field("memory (MiB)", lambda e: e["machine"]["memory_mib"])
    field("architecture", lambda e: e["machine"]["arch"])
    field("OS", lambda e: e["os"])
    field("kernel", lambda e: e["kernel"])
    field("load average before (1, 5, 15 min)", lambda e: ", ".join(str(x) for x in e["load_average_before"]))
    field("server", lambda e: e["server"])
    field("compiler and flags", lambda e: toolchain_text(e["toolchain"]))
    field("reactor", lambda e: e["reactor"])
    field("load generator", lambda e: e["load_generator"])
    field("method", lambda e: e["method"])
    field("CPU", lambda e: e["cpu"])
    field("idle connections", lambda e: e["idle"])
    field("TLS handshakes", lambda e: e["handshake_generator"])
    field("duration (s)", lambda e: e["duration_s"])
    field("warm-up (s)", lambda e: e["warm_up_s"])
    field("connections", lambda e: ", ".join(str(c) for c in e["conns"]))
    field("modes", lambda e: ", ".join(e["modes"]))
    field("routes", lambda e: ", ".join(e["routes"]))
    field("TLS", lambda e: toolchain_text(e["tls"]) if e["tls"] else "not measured")
    return out


def payloads(runs):
    every = {}
    for r in runs:
        every.update((r.env or {}).get("payloads") or {})
    return [f"- `/{k}`: {every[k]}" for k in sorted(every, key=lambda k: order(("", k)))]


def order(key):
    mode, route = key
    return (MODES.index(mode) if mode in MODES else len(MODES), mode,
            ROUTES.index(route) if route in ROUTES else len(ROUTES), route)


def result_tables(runs):
    rows = [r for run in runs for r in run.results if "requests_per_s" in r]
    groups = {}
    for r in rows:
        groups.setdefault((r["mode"], r["route"]), []).append(r)
    out = []
    for key in sorted(groups, key=order):
        group = sorted(groups[key], key=lambda r: (r["conns"], -r["requests_per_s"]))
        throughput = any("mib_per_s" in r for r in group)
        out += ["", f"### {key[0]}, `/{key[1]}`", "",
                "| server | conns | req/s |" + (" MiB/s |" if throughput else "") +
                " p50 µs | p95 µs | p99 µs | p99.9 µs | peak RSS KiB | CPU cores | CPU µs/req | failed |",
                "|---|---:|---:|" + ("---:|" if throughput else "") + "---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in group:
            out.append(f"| {label(r)} | {r['conns']} | {r['requests_per_s']} |" +
                       (f" {cell(r.get('mib_per_s'))} |" if throughput else "") +
                       f" {cell(r.get('p50_us'))} | {cell(r.get('p95_us'))} | {cell(r.get('p99_us'))} |"
                       f" {cell(r.get('p999_us'))} | {cell(r.get('peak_rss_kib'))} | {cell(r.get('cpu_cores'))} |"
                       f" {cell(r.get('cpu_us_per_request'))} | {r['failed']} |")
        negotiated = sorted({(label(r),) + tuple(r.get(k) or "?" for k in TLS_FIELDS)
                             for r in group if any(r.get(k) for k in TLS_FIELDS)})
        if negotiated:
            out += [""] + [f"Negotiated by {n[0]}: " + ", ".join(n[1:]) + "." for n in negotiated]
    return out


def allocation_table(runs):
    rows = [r for run in runs for r in run.results if "allocations_per_request" in r]
    if not rows:
        return []
    out = ["", "## Allocations per request", "", "| server | mode | route | allocations per request |",
           "|---|---|---|---:|"]
    for r in sorted(rows, key=lambda r: (label(r),) + order((r["mode"], r["route"]))):
        out.append(f"| {label(r)} | {r['mode']} | `/{r['route']}` | {cell(r['allocations_per_request'])} |")
    return out


def idle_table(runs):
    rows = [r for run in runs for r in run.results if "bytes_per_idle_connection" in r]
    if not rows:
        return []
    out = ["", "## Bytes per idle connection", "",
           "HTTP/1.1 connections held idle after one request each: the server's RSS growth over their count. RSS "
           "moves by pages and an allocator may keep what it had, so it is an estimate.", "",
           "| server | mode | connections | still open | RSS before KiB | RSS after KiB | bytes per connection |",
           "|---|---|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: (label(r), r["mode"])):
        out.append(f"| {label(r)} | {r['mode']} | {r['idle_connections']} | {r['idle_open']} | "
                   f"{r['rss_before_kib']} | {r['rss_after_kib']} | {cell(r['bytes_per_idle_connection'])} |")
    return out


def handshake_table(runs):
    rows = [r for run in runs for r in run.results if "handshakes_per_s" in r]
    if not rows:
        return []
    out = ["", "## TLS handshakes", "",
           "oha with keep-alive off on `/small`: each request is a new connection and a full handshake; the latency "
           "includes it.", "",
           "| server | mode | conns | handshakes/s | p50 µs | p99 µs | p99.9 µs | CPU cores | CPU µs/handshake | "
           "errors |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: (label(r), r["mode"], r["conns"])):
        out.append(f"| {label(r)} | {r['mode']} | {r['conns']} | {cell(r['handshakes_per_s'])} | "
                   f"{cell(r.get('p50_us'))} | {cell(r.get('p99_us'))} | {cell(r.get('p999_us'))} | "
                   f"{cell(r.get('cpu_cores'))} | {cell(r.get('cpu_us_per_request'))} | "
                   f"{cell(r.get('errors', r.get('error')))} |")
    return out


def measured(runs):
    """CeeSafe.Web's rows by (reactor, mode, route, connections)."""
    rows = {}
    for run in runs:
        for r in run.results:
            if r.get("server") == "ceesafe.web" and "requests_per_s" in r:
                rows[(r.get("reactor") or "default", r["mode"], r["route"], r["conns"])] = r
    return rows


def change(new, old):
    return None if not old else (new - old) * 100.0 / old


def regression_section(runs, baseline, gate):
    """The section, whether a row regressed, and whether the runs and the baseline share a machine and kernel."""
    hosts = {r.same_host() for r in runs + baseline}
    same = None not in hosts and len(hosts) == 1
    now, then = measured(runs), measured(baseline)
    out = ["", "## Against the baseline", "",
           "Baseline: " + ", ".join(f"`{b.path.name}` (`{b.sha[:12]}`)" for b in baseline) + ".",
           f"A row regresses when its requests a second fall, or its p99 rises, by more than {gate:g} %."]
    if not same:
        out.append("**The baseline was not run on the same machine and kernel as these runs (or one of them did not "
                   "record its environment), so the gate cannot be judged.**")
    out += ["", "| reactor | mode | route | conns | req/s | baseline | change | p99 µs | baseline | change | |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    regressed = False
    for key in sorted(set(now) & set(then), key=lambda k: (k[0],) + order((k[1], k[2])) + (k[3],)):
        n, o = now[key], then[key]
        rate = change(n["requests_per_s"], o["requests_per_s"])
        p99 = change(n["p99_us"], o["p99_us"]) if n.get("p99_us") and o.get("p99_us") else None
        bad = (rate is not None and rate < -gate) or (p99 is not None and p99 > gate)
        regressed |= bad
        out.append(f"| {key[0]} | {key[1]} | `/{key[2]}` | {key[3]} | {n['requests_per_s']} | {o['requests_per_s']} | "
                   f"{'–' if rate is None else f'{rate:+.1f} %'} | {cell(n.get('p99_us'))} | {cell(o.get('p99_us'))} | "
                   f"{'–' if p99 is None else f'{p99:+.1f} %'} | {'**regressed**' if bad else ''} |")
    missing = sorted(set(then) - set(now), key=lambda k: (k[0],) + order((k[1], k[2])) + (k[3],))
    if missing:
        out += ["", "Not measured this time (in the baseline): " +
                ", ".join(f"{k[0]} {k[1]} `/{k[2]}` at {k[3]}" for k in missing) + "."]
    out += ["", "Result: " + ("**a row regressed.**" if regressed else "no row regressed.")]
    return out, regressed, same


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", help="run.py --json files")
    ap.add_argument("-o", "--output", help="write the report here (default: standard output)")
    ap.add_argument("--title", default="CeeSafe.Web benchmarks")
    ap.add_argument("--baseline", nargs="+", default=[], help="the previous release's run.py --json files")
    ap.add_argument("--gate", type=float, default=GATE, help="per cent (default 5)")
    ap.add_argument("--advisory", action="store_true", help="report the gate, but exit 0")
    args = ap.parse_args()
    runs = [Run(p) for p in args.runs]
    baseline = [Run(p) for p in args.baseline]

    out = [f"# {args.title}", "",
           "Written by `benchmarks/report.py` from the raw results below (`benchmarks/http/run.py --json`); "
           "edit those, not this page.", "", "## Sources", ""]
    out += [f"- `{r.path.as_posix()}` (SHA-256 `{r.sha[:12]}…`), {len(r.results)} rows" for r in runs]
    out += ["", "## Environment", ""] + environment_table(runs)
    hosts = {r.same_host() for r in runs}
    if None in hosts or len(hosts) > 1:
        out += ["", "**These runs were not all on one machine with one kernel (or did not all record it): compare "
                "their rows with care.**"]
    listed = payloads(runs)
    if listed:
        out += ["", "Payloads:", ""] + listed
    out += (["", "## Results"] + result_tables(runs) + allocation_table(runs) + idle_table(runs) +
            handshake_table(runs))
    status = 0
    if baseline:
        section, regressed, same = regression_section(runs, baseline, args.gate)
        out += section
        if not same:
            status = 2
        elif regressed:
            status = 1
    text = "\n".join(out) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    if status == 1:
        print("report: a row regressed by more than the gate", file=sys.stderr)
    elif status == 2:
        print("report: the baseline was not on the same machine and kernel; the gate cannot be judged",
              file=sys.stderr)
    sys.exit(0 if args.advisory else status)


if __name__ == "__main__":
    main()
