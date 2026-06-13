#!/bin/bash
# Cross-compile a NOVA program to a Windows x86-64 .exe.
# Run on Linux with the mingw-w64 toolchain installed
#   (apt-get install gcc-mingw-w64-x86-64 binutils-mingw-w64-x86-64)
# Usage: scripts/build-windows.sh program.nova [output.exe]
set -e
SRC="$1"
OUT="${2:-${SRC%.nova}.exe}"
NOVA="${NOVA:-bin/nova}"
TMP="$(mktemp -d)"
"$NOVA" "$SRC" --target=windows -o "$TMP/p.s"
x86_64-w64-mingw32-as -o "$TMP/p.o" "$TMP/p.s"
x86_64-w64-mingw32-ld -o "$OUT" "$TMP/p.o" \
    -L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32 -lmsvcrt -lbcrypt
rm -rf "$TMP"
echo "Windows executable: $OUT"
