#!/bin/sh
# Prints the directory of CeeSafeLibs' package <name> at the commit <package dir>'s manifest pins, cloning it into
# out/ceesafelibs-<rev> first if need be: for builds outside ceesafec, as the fuzz harnesses' (ngtcp2, nghttp3).
#
#   L=$(sh tools/ceesafelibs.sh packages/web_quic ngtcp2)
set -eu
manifest=$1/ceesafe.toml
name=$2
rev=$(sed -n "s/^$name = { git = \"[^\"]*\", rev = \"\([0-9a-f]*\)\".*/\1/p" "$manifest")
[ -n "$rev" ] || { echo "ceesafelibs.sh: $manifest pins no $name" >&2; exit 1; }
dir=out/ceesafelibs-$rev
if [ ! -d "$dir/$name" ]; then
    mkdir -p out
    git clone -q https://github.com/CeeSafe/CeeSafeLibs.git "$dir"
    git -C "$dir" checkout -q "$rev"
fi
echo "$dir/$name"
