#!/bin/bash
# Builds the endpoint and its image on a Linux host with Docker. OPENSSL_PREFIX is an OpenSSL 3.5 or later install
# (web_quic needs it, Web ADR 0014 §3.3); CEESAFEC is the compiler to build with.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
: "${OPENSSL_PREFIX:?set OPENSSL_PREFIX to an OpenSSL 3.5 install}"
: "${CEESAFEC:=ceesafec}"
lib=$OPENSSL_PREFIX/lib64
[ -d "$lib" ] || lib=$OPENSSL_PREFIX/lib
export CPATH=$OPENSSL_PREFIX/include LIBRARY_PATH=$lib LD_LIBRARY_PATH=$lib
(cd "$here/.." && "$CEESAFEC" fetch >/dev/null && "$CEESAFEC" build -o out/endpoint)
ctx=$(mktemp -d)
cp "$here/Dockerfile" "$here/run_endpoint.sh" "$ctx/"
cp "$here/../out/endpoint" "$ctx/endpoint"
mkdir "$ctx/lib"
cp -L "$lib/libssl.so.3" "$lib/libcrypto.so.3" "$ctx/lib/"
docker build -q -t ceesafe-web-endpoint:latest "$ctx"
rm -r "$ctx"
