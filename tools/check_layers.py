#!/usr/bin/env python3
"""Check that the packages' dependencies follow the allowed layering (Web ADR 0004 §3).

usage: python3 tools/check_layers.py [--root DIR]

Fails if a package depends on something its layer does not allow, if a dependency is not a sibling
path under packages/, if the graph has a cycle, or if a package is missing from ALLOWED (or vice versa).
Changing ALLOWED is an ADR 0004 change: edit the ADR's table in the same commit.
"""
import argparse
import pathlib
import re
import sys

# Each package and the packages it MAY depend on (directly). Keep in sync with Web ADR 0004 §3.
ALLOWED = {
    "web_core": set(),
    "web_transport": {"web_core"},
    "web_tls": {"web_core", "web_transport"},
    "web_http": {"web_core"},
    "web_http1": {"web_core", "web_http"},
    "web_sse": {"web_core", "web_http"},
    "web_ws": {"web_core", "web_http"},
    "web_http2": {"web_core", "web_http"},
    "web_http3": {"web_core", "web_http", "web_tls"},
    "web_client": {"web_core", "web_transport", "web_http", "web_http1", "web_http2"},
    "web_server": {"web_core", "web_transport", "web_http", "web_http1", "web_http2", "web_sse", "web_ws"},
    "web_proxy": {"web_core", "web_transport", "web_http", "web_client", "web_server"},
}

# Packages that must never be depended on by these (a lower layer importing a higher one would be caught by
# ALLOWED already; this names the property the layering exists for).
NO_TLS = {"web_core", "web_transport", "web_http", "web_http1", "web_http2", "web_sse", "web_ws", "web_client",
          "web_server", "web_proxy"}


def read_manifest(path: pathlib.Path):
    """(name, {dependency: path}) from a ceesafe.toml, enough of TOML for our manifests."""
    name = None
    deps = {}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"\[([A-Za-z0-9_.\-]+)\]", line)
        if m:
            section = m.group(1)
            continue
        if section == "package":
            m = re.fullmatch(r'name\s*=\s*"([^"]+)"', line)
            if m:
                name = m.group(1)
        elif section == "dependencies":
            m = re.fullmatch(r'([A-Za-z0-9_]+)\s*=\s*\{\s*path\s*=\s*"([^"]+)"\s*\}', line)
            if not m:
                raise ValueError(f"{path}: unsupported dependency form (only path dependencies): {raw}")
            deps[m.group(1)] = m.group(2)
    if name is None:
        raise ValueError(f"{path}: no [package] name")
    return name, deps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=pathlib.Path(__file__).resolve().parent.parent, type=pathlib.Path)
    args = ap.parse_args()
    packages_dir = args.root / "packages"
    errors = []
    graph = {}
    for manifest in sorted(packages_dir.glob("*/ceesafe.toml")):
        directory = manifest.parent.name
        name, deps = read_manifest(manifest)
        if name != directory:
            errors.append(f"{manifest}: name '{name}' differs from its directory '{directory}'")
        if not (manifest.parent / "ceesafe.lock").exists():
            errors.append(f"{name}: no ceesafe.lock (run ceesafec fetch)")
        for dep, rel in deps.items():
            if rel != f"../{dep}":
                errors.append(f"{name}: dependency '{dep}' must be path \"../{dep}\", is \"{rel}\"")
        graph[name] = set(deps)

    for name in sorted(set(ALLOWED) - set(graph)):
        errors.append(f"{name}: in ALLOWED but there is no packages/{name}/ceesafe.toml")
    for name in sorted(set(graph) - set(ALLOWED)):
        errors.append(f"{name}: not in ALLOWED (a new package is an ADR 0004 change)")

    for name, deps in sorted(graph.items()):
        for dep in sorted(deps - ALLOWED.get(name, set())):
            errors.append(f"{name} -> {dep}: not allowed by the layering (Web ADR 0004 §3)")

    # Transitive closure: the TLS-free property must hold through indirect dependencies too.
    def closure(n, seen=None):
        seen = set() if seen is None else seen
        for d in graph.get(n, ()):
            if d not in seen:
                seen.add(d)
                closure(d, seen)
        return seen

    for name in sorted(NO_TLS & set(graph)):
        if "web_tls" in closure(name):
            errors.append(f"{name}: reaches web_tls, so every program using it would link OpenSSL")

    # Cycles (ceesafec reports N0327 too; this names the edge).
    state = {}

    def visit(n, stack):
        state[n] = 1
        for d in sorted(graph.get(n, ())):
            if state.get(d) == 1:
                errors.append("cycle: " + " -> ".join(stack + [n, d]))
            elif state.get(d) is None:
                visit(d, stack + [n])
        state[n] = 2

    for n in sorted(graph):
        if state.get(n) is None:
            visit(n, [])

    if errors:
        for e in errors:
            print("check_layers: " + e, file=sys.stderr)
        return 1
    print(f"check_layers: {len(graph)} packages, layering ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
