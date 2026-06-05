#!/bin/bash
# R11D — SAD (Sum-of-Absolute-Differences) microbench
# Compares scalar NOVA loop vs `simd_sum_abs_diff` AVX2 builtin on 1024
# int32 lanes. Reports wall-clock timing for both paths and the speedup
# ratio. Expected on Linux x86-64: 4-8x SIMD wins on AVX2 hosts; the
# scalar reference itself pays for NOVA's smart-op pointer-checks per
# element, so the ratio is often higher.
#
# Skips cleanly if the SIMD bench source can't be compiled (e.g. nova
# binary not built yet).

set -e
cd "$(dirname "$0")/.."

NOVA=${NOVA:-bin/nova}
AS=${AS:-as}
LD=${LD:-ld}

if [ ! -x "$NOVA" ]; then
    echo "(skip: nova compiler missing -- run 'make' first)"
    exit 0
fi

BENCH=examples/bench_simd_sad.nova

# The bench source is generated here so the script stands alone -- the
# generated file is dropped in examples/ for reproducibility / git diff.
cat > "$BENCH" <<'NOVA_EOF'
// R11D SAD benchmark — scalar vs SIMD on 1024 int32 lanes.
//
// Allocates two 1024-element int32 buffers, fills them with deterministic
// patterns, then computes sum(abs(a[i]-b[i])) over the full buffer using:
//   1. a pure NOVA scalar loop
//   2. simd_sum_abs_diff() (AVX2 on Linux x86-64; scalar fallback on
//      macOS, Windows, WASM; NEON on ARM64/winARM64)
// and prints both timings + speedup ratio.

import "std/io"

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

// Deterministic fill keeping all values in [0, 127] so abs(a-b) <= 127 and
// the lanewise i32 accumulator never overflows over 1024 elements
// (max = 1024 * 127 = 130048 < 2^31).
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

fn main() {
    let n = 1024
    let bytes = int_mul(n, 4)
    println("=== R11D SAD benchmark (scalar vs SIMD) ===")
    print("  elements: ")
    print_int(n)
    println("")

    let a = alloc(bytes + 32)
    let b = alloc(bytes + 32)
    fill_array(a, n, 13, 7)
    fill_array(b, n, 17, 3)

    // Warm-up.
    let _wa = scalar_sad(a, b, 256)
    let _wb = simd_sum_abs_diff(a, b, 256)

    // Average over multiple trials so the 1024-element SIMD run is
    // measurable above the nanotime resolution floor.
    let trials = 200
    let t0 = nanotime()
    let trial_i = 0
    let scalar_r = 0
    while trial_i < trials {
        scalar_r = scalar_sad(a, b, n)
        trial_i = trial_i + 1
    }
    let t1 = nanotime()
    let scalar_ns = t1 - t0

    let t2 = nanotime()
    trial_i = 0
    let simd_r = 0
    while trial_i < trials {
        simd_r = simd_sum_abs_diff(a, b, n)
        trial_i = trial_i + 1
    }
    let t3 = nanotime()
    let simd_ns = t3 - t2

    print("  scalar result: ")
    print_int(scalar_r)
    println("")
    print("  SIMD   result: ")
    print_int(simd_r)
    println("")
    if scalar_r == simd_r {
        println("  MATCH: scalar and SIMD agree.")
    } else {
        println("  MISMATCH: scalar != SIMD")
        exit(1)
    }

    print("  scalar avg (ns): ")
    print_int(int_div(scalar_ns, trials))
    println("")
    print("  SIMD   avg (ns): ")
    print_int(int_div(simd_ns, trials))
    println("")

    if simd_ns > 0 {
        let ratio_x100 = int_div(int_mul(scalar_ns, 100), simd_ns)
        print("  speedup (x100): ")
        print_int(ratio_x100)
        println("")
        let whole = int_div(ratio_x100, 100)
        let frac  = ratio_x100 % 100
        print("  speedup: ~")
        print_int(whole)
        print(".")
        if frac < 10 { print("0") }
        print_int(frac)
        println("x")
    } else {
        println("  SIMD time too small to measure")
    }
}

main()
NOVA_EOF

OUT_S=/tmp/bench_simd_sad.s
OUT_O=/tmp/bench_simd_sad.o
OUT_BIN=/tmp/bench_simd_sad

$NOVA "$BENCH" -o "$OUT_S"
$AS -o "$OUT_O" "$OUT_S" 2>&1 | grep -v "Warning: end of file" || true
$LD -o "$OUT_BIN" "$OUT_O"
"$OUT_BIN"
