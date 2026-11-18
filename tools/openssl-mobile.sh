#!/bin/sh
# SPDX-License-Identifier: MIT
# Builds OpenSSL as static archives for the phones, for web_tls's [target.android] and [target.ios] (CeeSafe ADR
# 0171): a phone ships no OpenSSL an app may link.
#
#   sh tools/openssl-mobile.sh SOURCE [OUT]
#
# SOURCE is an unpacked OpenSSL 3 source tree (a release's tarball from https://github.com/openssl/openssl/releases;
# check its SHA-256 against the release's). OUT is where the archives go (default ~/.cache/ceesafe/openssl-mobile):
#
#   OUT/android-arm64      Android, arm64-v8a           OPENSSL_ANDROID_DIR for that ABI
#   OUT/android-x86_64     Android, x86_64 (an emulator on an Intel machine)
#   OUT/ios-arm64          an iPhone or iPad            OPENSSL_IOS_DIR for a device
#   OUT/ios-sim-arm64      the simulator on an Apple Silicon Mac
#
# each with lib/libssl.a, lib/libcrypto.a and include/. It needs a Mac with Xcode for the iOS ones and the Android
# NDK (ANDROID_NDK_ROOT, or the newest under ~/Library/Android/sdk/ndk) for Android's; a target whose tools are
# missing is skipped with a line saying so. TARGETS="android-arm64 ios-arm64" builds only those.
set -e
SOURCE=${1:?usage: openssl-mobile.sh SOURCE [OUT]}
OUT=${2:-$HOME/.cache/ceesafe/openssl-mobile}
TARGETS=${TARGETS:-android-arm64 android-x86_64 ios-arm64 ios-sim-arm64}
JOBS=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
[ -f "$SOURCE/Configure" ] || { echo "openssl-mobile: $SOURCE is not an OpenSSL source tree" >&2; exit 1; }
SOURCE=$(cd "$SOURCE" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# What an app's TLS needs and no more: no command-line tool, tests, engines or the legacy provider's file.
OPTIONS="no-shared no-tests no-apps no-docs no-engine no-module no-legacy no-comp no-ui-console"

NDK=${ANDROID_NDK_ROOT:-$(ls -d "$HOME"/Library/Android/sdk/ndk/* 2>/dev/null | sort -V | tail -1)}

build() { # name, the Configure target, then its extra arguments
    name=$1; shift
    work="$OUT/.build-$name"
    rm -rf "$work" "$OUT/$name"
    mkdir -p "$work"
    echo "openssl-mobile: building $name"
    (cd "$work" && "$SOURCE/Configure" "$@" $OPTIONS --prefix="$OUT/$name" --libdir=lib >configure.log 2>&1 &&
        make -j"$JOBS" build_libs >build.log 2>&1 && make install_dev >install.log 2>&1) ||
        { echo "openssl-mobile: $name failed; see $work/*.log" >&2; exit 1; }
    rm -rf "$work"
    echo "openssl-mobile: $OUT/$name"
}

for target in $TARGETS; do
    case $target in
    android-*)
        if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then echo "openssl-mobile: no Android NDK: $target skipped"; continue; fi
        tools=$(ls -d "$NDK"/toolchains/llvm/prebuilt/*/bin | head -1)
        # API 24 (Android 7), ClayWind's floor.
        ANDROID_NDK_ROOT="$NDK" PATH="$tools:$PATH" build "$target" "$target" -D__ANDROID_API__=24
        ;;
    ios-arm64)
        if ! xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1; then echo "openssl-mobile: no iOS SDK: $target skipped"; continue; fi
        build "$target" ios64-xcrun -miphoneos-version-min=15.0
        ;;
    ios-sim-arm64)
        if ! xcrun --sdk iphonesimulator --show-sdk-path >/dev/null 2>&1; then echo "openssl-mobile: no simulator SDK: $target skipped"; continue; fi
        build "$target" iossimulator-arm64-xcrun -mios-simulator-version-min=15.0
        ;;
    *) echo "openssl-mobile: unknown target $target" >&2; exit 1 ;;
    esac
done
