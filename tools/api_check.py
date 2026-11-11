#!/usr/bin/env python3
"""Check that docs/api/ matches the packages' public API (Web ADR 0015 §4).

usage: python3 tools/api_check.py [--write] [--ceesafec PATH]

For each package under packages/, `ceesafec doc --title <name>` gives a Markdown page of its public items, the same
bytes for the same sources (CeeSafe spec/tooling.md §1.2). The page is committed as docs/api/<name>.md. Without
--write, this fails if a committed page differs from a fresh one, is missing, or names no package; with --write, it
rewrites them. A pull request that changes a page changes the public API and says so. From v1.0.0-rc.1, a change
that removes or alters an item, rather than adding one, waits for 2.0.
"""
import argparse
import difflib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "docs" / "api"


def page(ceesafec, package):
    r = subprocess.run([ceesafec, "doc", "--title", package.name], cwd=package, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"{package.name}: ceesafec doc failed:\n{r.stdout}{r.stderr}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ceesafec", default="ceesafec")
    args = ap.parse_args()
    packages = sorted(p.parent for p in (ROOT / "packages").glob("*/ceesafe.toml"))
    API.mkdir(parents=True, exist_ok=True)
    failed = []
    for package in packages:
        fresh = page(args.ceesafec, package)
        path = API / f"{package.name}.md"
        old = path.read_text() if path.exists() else None
        if old == fresh:
            continue
        if args.write:
            path.write_text(fresh)
            print(f"wrote {path.relative_to(ROOT)}")
            continue
        failed.append(package.name)
        diff = difflib.unified_diff((old or "").splitlines(True), fresh.splitlines(True),
                                    f"docs/api/{package.name}.md (committed)", f"{package.name} (sources)")
        sys.stdout.writelines(diff)
    names = {p.name for p in packages}
    stale = sorted(p for p in API.glob("*.md") if p.stem not in names)
    for p in stale:
        if args.write:
            p.unlink()
            print(f"removed {p.relative_to(ROOT)}")
        else:
            failed.append(p.name)
            print(f"docs/api/{p.name}: no package of that name")
    if failed:
        sys.exit(f"public API changed: {', '.join(failed)}. Run python3 tools/api_check.py --write, and say in the "
                 "pull request what changed (Web ADR 0015 §4).")
    if not args.write:
        print(f"docs/api: {len(packages)} packages match their sources")


if __name__ == "__main__":
    main()
