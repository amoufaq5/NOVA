#!/bin/bash
# R15B — Stereo SAD benchmark: WASM v128 SIMD vs WASM scalar.
#
# Measures the same workload (sum-of-absolute-differences over 1024 i32
# lanes, 1000 trials = ~1M lane-ops) under two NOVA programs compiled to
# WASM:
#   1. wasm_sad_simd.nova: calls simd_sum_abs_diff (-> v128.load +
#      i32x4.sub + i32x4.abs + i32x4.add + extract_lane)
#   2. wasm_sad_scalar.nova: open-coded scalar loop with load_i32 +
#      subtract + abs
#
# Expected: 2-3x speedup. WASM v128 is 128-bit (4 i32 lanes), so the
# theoretical ceiling is ~half AVX2's 8-lane width. The actual speedup
# observed depends on wasmtime's JIT inlining of the v128 ops vs the
# Cranelift codegen of the scalar loop.
#
# Skips cleanly if wat2wasm / wasmtime aren't installed.

set -u
cd "$(dirname "$0")/.."

NOVA=${NOVA:-bin/nova}

if [ ! -x "$NOVA" ]; then
    echo "(skip: nova compiler missing -- run 'make' first)"
    exit 0
fi
if ! command -v wat2wasm >/dev/null 2>&1; then
    echo "(skip: wat2wasm not installed)"
    exit 0
fi
if ! command -v wasmtime >/dev/null 2>&1; then
    echo "(skip: wasmtime not installed)"
    exit 0
fi

SCALAR_SRC=/tmp/wasm_sad_scalar.nova
SIMD_SRC=/tmp/wasm_sad_simd.nova

# ---- shared prelude ---------------------------------------------------------

cat > /tmp/wasm_sad_prelude.nova <<'NOVA_EOF'
fn store_i32_le(buf, off, v) {
    store8(int_add(buf, off),                  v % 256)
    store8(int_add(buf, int_add(off, 1)),      int_div(v, 256) % 256)
    store8(int_add(buf, int_add(off, 2)),      int_div(v, 65536) % 256)
    store8(int_add(buf, int_add(off, 3)),      int_div(v, 16777216) % 256)
}

fn load_i32_le(buf, off) {
    let b0 = load8(int_add(buf, off))
    let b1 = load8(int_add(buf, int_add(off, 1)))
    let b2 = load8(int_add(buf, int_add(off, 2)))
    let b3 = load8(int_add(buf, int_add(off, 3)))
    return b0 + int_mul(b1, 256) + int_mul(b2, 65536) + int_mul(b3, 16777216)
}

fn fill_array(buf, n, m, c) {
    let i = 0
    while i < n {
        let v = (int_mul(i, m) + c) % 128
        store_i32_le(buf, int_mul(i, 4), v)
        i = i + 1
    }
}

fn scalar_sad(a, b, n) {
    let acc = 0
    let i = 0
    while i < n {
        let off = int_mul(i, 4)
        let av = load_i32_le(a, off)
        let bv = load_i32_le(b, off)
        let d = av - bv
        if d < 0 { d = 0 - d }
        acc = acc + d
        i = i + 1
    }
    return acc
}
NOVA_EOF

# ---- scalar variant ---------------------------------------------------------

cat > "$SCALAR_SRC" <<'NOVA_EOF'
fn run() {
    let n = 1024
    let trials = 1000
    let bytes = int_mul(n, 4)
    let a = alloc(bytes + 32)
    let b = alloc(bytes + 32)
    fill_array(a, n, 13, 7)
    fill_array(b, n, 17, 3)
    let r = 0
    let ti = 0
    while ti < trials {
        r = scalar_sad(a, b, n)
        ti = ti + 1
    }
    print("scalar result=")
    print_int(r)
    println("")
}

run()
NOVA_EOF

cat /tmp/wasm_sad_prelude.nova "$SCALAR_SRC" > /tmp/wasm_sad_scalar_full.nova

# ---- SIMD variant -----------------------------------------------------------

cat > "$SIMD_SRC" <<'NOVA_EOF'
fn run() {
    let n = 1024
    let trials = 1000
    let bytes = int_mul(n, 4)
    let a = alloc(bytes + 32)
    let b = alloc(bytes + 32)
    fill_array(a, n, 13, 7)
    fill_array(b, n, 17, 3)
    let r = 0
    let ti = 0
    while ti < trials {
        r = simd_sum_abs_diff(a, b, n)
        ti = ti + 1
    }
    print("simd result=")
    print_int(r)
    println("")
}

run()
NOVA_EOF

cat /tmp/wasm_sad_prelude.nova "$SIMD_SRC" > /tmp/wasm_sad_simd_full.nova

# ---- compile both ---------------------------------------------------------

$NOVA /tmp/wasm_sad_scalar_full.nova --target=wasm -o /tmp/wasm_sad_scalar.wat 2>/dev/null
$NOVA /tmp/wasm_sad_simd_full.nova   --target=wasm -o /tmp/wasm_sad_simd.wat   2>/dev/null

wat2wasm /tmp/wasm_sad_scalar.wat -o /tmp/wasm_sad_scalar.wasm
wat2wasm /tmp/wasm_sad_simd.wat   -o /tmp/wasm_sad_simd.wasm

echo "=== R15B WASM SIMD vs scalar bench ==="
echo "  workload: 1024 i32 lanes x 1000 trials"
echo "  WASM v128 width: 128-bit (4 i32 lanes per op)"
echo ""

# Warm up wasmtime JIT once.
wasmtime /tmp/wasm_sad_simd.wasm   >/dev/null 2>&1 || true
wasmtime /tmp/wasm_sad_scalar.wasm >/dev/null 2>&1 || true

# Real run with /usr/bin/time -f for wallclock.
echo "--- scalar ---"
SCALAR_OUT=$({ time wasmtime /tmp/wasm_sad_scalar.wasm; } 2>&1)
echo "$SCALAR_OUT" | grep -v "^$"
# bash's "time" prints "real    0m1.234s" — extract seconds (1.234).
SCALAR_T=$(echo "$SCALAR_OUT" | awk '/^real/ {
    t=$2;
    sub(/m/, " ", t); sub(/s/, "", t);
    n=split(t, parts, " ");
    printf "%.3f\n", parts[1]*60 + parts[2];
}')

echo ""
echo "--- SIMD ---"
SIMD_OUT=$({ time wasmtime /tmp/wasm_sad_simd.wasm; } 2>&1)
echo "$SIMD_OUT" | grep -v "^$"
SIMD_T=$(echo "$SIMD_OUT" | awk '/^real/ {
    t=$2;
    sub(/m/, " ", t); sub(/s/, "", t);
    n=split(t, parts, " ");
    printf "%.3f\n", parts[1]*60 + parts[2];
}')

echo ""
echo "scalar wallclock: ${SCALAR_T}s"
echo "SIMD   wallclock: ${SIMD_T}s"

# Awk for a fractional ratio.
RATIO=$(awk -v s="$SCALAR_T" -v v="$SIMD_T" 'BEGIN{ if (v+0==0) print "inf"; else printf "%.2f", s/v }')
echo "speedup: ${RATIO}x"

# Check whether wasm-objdump can confirm v128 in the SIMD binary.
if command -v wasm-objdump >/dev/null 2>&1; then
    V128=$(wasm-objdump -d /tmp/wasm_sad_simd.wasm 2>/dev/null | grep -cE 'v128\.|i32x4\.|i8x16\.|i16x8\.')
    SCALAR_V128=$(wasm-objdump -d /tmp/wasm_sad_scalar.wasm 2>/dev/null | grep -cE 'v128\.|i32x4\.|i8x16\.|i16x8\.')
    echo ""
    echo "v128 instructions: SIMD binary=$V128, scalar binary=$SCALAR_V128"
fi
