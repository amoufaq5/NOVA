#!/bin/bash
# tests/test_wasi_preopens.sh -- WASI preopens / filesystem smoke test.
#
# R8A: validates that a NOVA program compiled to WASM can drive the full
# wasi_snapshot_preview1 filesystem surface (path_open, fd_read, fd_write,
# fd_close, path_filestat_get) end-to-end under wasmtime with --dir=/tmp
# preopens. The companion examples/wasi_file_roundtrip.nova exercises the
# new low-level NOVA builtins (wasi_open, wasi_read, wasi_write, wasi_seek,
# wasi_close, wasi_filestat).
#
# Pipeline:
#   1. bin/nova examples/wasi_file_roundtrip.nova --target=wasm ...
#   2. wat2wasm ... -> .wasm
#   3. wasmtime --dir=/tmp ... (preopen[0] maps /tmp -> dirfd 3)
#   4. inspect imports section -- expect 12 wasi_snapshot_preview1 imports
#   5. assert /tmp/wasi_rt.txt contains "hello wasi" (10 bytes)
#
# Skips cleanly (exit 0) if wat2wasm or wasmtime aren't on PATH. CI
# treats a missing-toolchain skip as a pass; only a positive failure
# (wasm built but didn't behave) returns non-zero.

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

WAT=bin/wasi_file_roundtrip.wat
WASM=bin/wasi_file_roundtrip.wasm
OUT=/tmp/wasi_rt.txt

echo "--- WASI preopens smoke test (R8A) ---"

if [ ! -x bin/nova ]; then
    echo "FAIL: bin/nova missing -- run `make bin/nova` first"
    exit 1
fi

mkdir -p bin
rm -f "$WAT" "$WASM" "$OUT"

# Step 1: NOVA -> WAT
bin/nova examples/wasi_file_roundtrip.nova --target=wasm -o "$WAT" >/dev/null
if [ ! -s "$WAT" ]; then
    echo "FAIL: wat output empty"
    exit 1
fi
echo "compiled: examples/wasi_file_roundtrip.nova -> $WAT ($(wc -l < "$WAT") lines)"

# Step 2: WAT -> WASM
if ! command -v wat2wasm >/dev/null 2>&1; then
    echo "(skip: wat2wasm not available -- apt install wabt)"
    exit 0
fi
wat2wasm "$WAT" -o "$WASM"
echo "assembled: $WASM ($(stat -c%s "$WASM") bytes)"

# Step 3: verify imports section
if command -v wasm-objdump >/dev/null 2>&1; then
    echo "--- imports ---"
    wasm-objdump -x "$WASM" | grep "wasi_snapshot_preview1\." | sed 's/^/  /'
    expected_imports="fd_write fd_read fd_close fd_seek path_open path_filestat_get args_sizes_get args_get environ_sizes_get environ_get random_get proc_exit"
    for imp in $expected_imports; do
        if ! wasm-objdump -x "$WASM" | grep -q "wasi_snapshot_preview1.$imp"; then
            echo "FAIL: missing import wasi_snapshot_preview1.$imp"
            exit 1
        fi
    done
    echo "all 12 wasi_snapshot_preview1 imports present"
fi

# Step 4: run under wasmtime with --dir=/tmp
if ! command -v wasmtime >/dev/null 2>&1; then
    echo "(skip: wasmtime not on PATH -- install via https://wasmtime.dev/install.sh)"
    exit 0
fi
echo "--- wasmtime --dir=/tmp $WASM ---"
wasmtime --dir=/tmp "$WASM"
rc=$?
if [ "$rc" != "0" ]; then
    echo "FAIL: wasmtime exit=$rc"
    exit 1
fi

# Step 5: verify the file content
if [ ! -f "$OUT" ]; then
    echo "FAIL: $OUT was not created"
    exit 1
fi
got="$(cat "$OUT")"
if [ "$got" != "hello wasi" ]; then
    echo "FAIL: $OUT content mismatch: got=[$got] expected=[hello wasi]"
    exit 1
fi
size=$(stat -c%s "$OUT")
if [ "$size" != "10" ]; then
    echo "FAIL: $OUT size=$size, expected 10"
    exit 1
fi
echo "$OUT verified: 10 bytes, content=[hello wasi]"

echo "PASS: WASI preopens smoke test"
