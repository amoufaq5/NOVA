#!/bin/bash
# R15B — WASM v128 SIMD intrinsics integration test.
#
# Compiles tests/test_simd_wasm_v128.nova with --target=wasm, converts
# WAT -> WASM via wat2wasm, then runs under wasmtime to verify the v128
# SIMD lowering produces bit-identical results to the native AVX2 / NEON
# paths. Also confirms wasm-objdump sees v128 instructions present.
#
# Skips cleanly if wat2wasm / wasmtime / wasm-objdump aren't installed.

set -u

cd "$(dirname "$0")/.."

NOVA=${NOVA:-bin/nova}
TEST=tests/test_simd_wasm_v128.nova
WAT=/tmp/test_simd_wasm_v128.wat
WASM=/tmp/test_simd_wasm_v128.wasm

if ! command -v wat2wasm >/dev/null 2>&1; then
    echo "(skip: wat2wasm not installed -- apt install wabt)"
    exit 0
fi
if ! command -v wasmtime >/dev/null 2>&1; then
    echo "(skip: wasmtime not installed)"
    exit 0
fi

echo "=== Step 1: NOVA --target=wasm ==="
"$NOVA" "$TEST" --target=wasm -o "$WAT" 2>&1 | tail -3
if [ ! -f "$WAT" ]; then
    echo "FAIL: NOVA did not produce $WAT"
    exit 1
fi
echo "WAT: $(wc -l < $WAT) lines, $(stat -c%s $WAT) bytes"

echo ""
echo "=== Step 2: wat2wasm ==="
if ! wat2wasm "$WAT" -o "$WASM"; then
    echo "FAIL: wat2wasm validation failed"
    exit 1
fi
echo "WASM: $(stat -c%s $WASM) bytes"

echo ""
echo "=== Step 3: wasmtime validation ==="
if ! wasmtime compile "$WASM" -o /tmp/test_simd_wasm_v128.cwasm 2>&1; then
    echo "FAIL: wasmtime compile rejected the module"
    exit 1
fi
echo "wasmtime compile: OK (module valid)"

if command -v wasm-objdump >/dev/null 2>&1; then
    echo ""
    echo "=== Step 4: wasm-objdump v128 instructions present ==="
    V128_COUNT=$(wasm-objdump -d "$WASM" 2>/dev/null | grep -cE 'v128\.|i32x4\.|i8x16\.|i16x8\.' || true)
    echo "v128 instructions emitted: $V128_COUNT"
    if [ "$V128_COUNT" -lt 20 ]; then
        echo "FAIL: expected >=20 v128 instructions (got $V128_COUNT)"
        exit 1
    fi
fi

echo ""
echo "=== Step 5: wasmtime run ==="
OUTPUT=$(wasmtime "$WASM" 2>&1)
echo "$OUTPUT"
if echo "$OUTPUT" | grep -q "ALL WASM V128 SIMD TESTS PASSED"; then
    echo ""
    echo "PASS: WASM v128 SIMD lowering verified end-to-end"
    exit 0
else
    echo ""
    echo "FAIL: did not see PASS banner"
    exit 1
fi
