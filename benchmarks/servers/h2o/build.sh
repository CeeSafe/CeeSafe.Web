#!/bin/sh
# Builds h2o at the pinned commit into PREFIX (default ./out), with mruby, as the comparison server (Web ADR 0015 §6).
#
# h2o's last release is 2.2.6 (2019; 2.3.0-beta2 the last tag); its maintained code is master, so a master commit is
# pinned. Needs git, cmake, a C compiler, ruby and bison (for mruby), and OpenSSL 3 (OPENSSL_ROOT_DIR if not found).
#
# usage: sh benchmarks/servers/h2o/build.sh [PREFIX]
set -eu
COMMIT=d3e43fcf00c3e6b17826b95d2d362d646185918a # master, 2026-11-14
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=${1:-$HERE/out}
SRC=$PREFIX/src
if [ ! -d "$SRC/.git" ]; then
    git clone --filter=blob:none https://github.com/h2o/h2o.git "$SRC"
fi
git -C "$SRC" fetch --quiet origin "$COMMIT" 2>/dev/null || true
git -C "$SRC" checkout --quiet "$COMMIT"
mkdir -p "$SRC/build"
cd "$SRC/build"
cmake .. -DCMAKE_BUILD_TYPE=Release -DWITH_MRUBY=ON -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    ${OPENSSL_ROOT_DIR:+-DOPENSSL_ROOT_DIR="$OPENSSL_ROOT_DIR"}
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" h2o
# install, not a copy: the mruby handlers load PREFIX/share/h2o/mruby/preloads.rb
make install
"$PREFIX/bin/h2o" --version
