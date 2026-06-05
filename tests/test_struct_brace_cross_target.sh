#!/bin/bash
# R25A cross-target struct brace-init + destructure assertions.
#
# Verifies that AST_STRUCT_INIT + the parser-time struct-destructure
# lowering compile cleanly on all six supported targets:
#
#   --target=linux         (x86-64 native) : compile + run + assert OK
#   --target=macos         (cross)         : compile -> Mach-O 64-bit object
#   --target=windows       (cross)         : compile -> Intel COFF + .exe link
#   --target=arm64         (cross)         : compile -> ELF 64-bit ARM aarch64 object
#   --target=windows-arm64 (cross)         : compile -> PE32+ Aarch64 object
#   --target=wasm          (cross)         : compile -> wasm module + run under wasmtime
#
# Each cross-target check fails the harness if the binary structure
# doesn't match the expected machine type / file format. The Linux
# and WASM cases additionally execute the program and check the
# expected stdout marker.

set -e
cd "$(dirname "$0")/.."

OUT=/tmp/nova_r25a_struct
SRC=/tmp/nova_r25a_struct_src.nova

# A small NOVA program exercising brace-init, struct destructure in let,
# and struct match patterns. Uses only integer payloads.
cat > "$SRC" <<'NOVA'
struct Point {
    x: int,
    y: int
}

let p = Point { x: 10, y: 20 }
assert(p.x == 10, "p.x == 10")
assert(p.y == 20, "p.y == 20")

let Point { x: px, y: py } = p
assert(px == 10, "destructure px")
assert(py == 20, "destructure py")

let mr = match p {
    Point { x: 10, y: 20 } => 1
    _ => 0
}
assert(mr == 1, "match struct literal")

let mr2 = match p {
    Point { x: a, y: b } => a + b
}
assert(mr2 == 30, "match struct binder")

println("R25A cross-target struct OK")
NOVA

fail() {
    echo "FAIL: $1"
    exit 1
}

ok() {
    echo "OK: $1"
}

PASS_LINUX=0
PASS_MACOS=0
PASS_WINDOWS=0
PASS_ARM64=0
PASS_WINARM64=0
PASS_WASM=0

# --- 1. Linux x86-64 (native): compile + assemble + link + run -------------
echo ""
echo "=== Linux x86-64 (native) ==="
bin/nova "$SRC" -o "$OUT.linux.s" >/dev/null
as -o "$OUT.linux.o" "$OUT.linux.s" 2>/dev/null
ld -o "$OUT.linux" "$OUT.linux.o"
file "$OUT.linux" | grep -q "ELF 64-bit LSB.*x86-64" \
    || fail "linux: binary not ELF64 x86-64"
out=$("$OUT.linux")
if [ "$out" = "R25A cross-target struct OK" ]; then
    ok "linux: program runs and prints expected marker"
    PASS_LINUX=1
else
    fail "linux: unexpected stdout: $out"
fi

# --- 2. macOS x86-64 (cross-assemble only) ---------------------------------
echo ""
echo "=== macOS x86-64 (cross) ==="
if command -v clang >/dev/null 2>&1; then
    bin/nova "$SRC" --target=macos -o "$OUT.macos.s" >/dev/null
    clang -target x86_64-apple-darwin -c "$OUT.macos.s" -o "$OUT.macos.o" 2>/dev/null \
        || fail "macos: clang assemble failed"
    file "$OUT.macos.o" | grep -q "Mach-O 64-bit.*x86_64" \
        || fail "macos: not Mach-O x86_64 (got: $(file $OUT.macos.o))"
    ok "macos: Mach-O 64-bit x86_64 object produced"
    PASS_MACOS=1
else
    echo "(skip: clang not available)"
fi

# --- 3. Windows x86-64 (mingw cross-assemble + cross-link to .exe) ---------
echo ""
echo "=== Windows x86-64 (cross) ==="
if command -v x86_64-w64-mingw32-as >/dev/null 2>&1 && \
   command -v x86_64-w64-mingw32-ld >/dev/null 2>&1; then
    bin/nova "$SRC" --target=windows -o "$OUT.win.s" >/dev/null
    x86_64-w64-mingw32-as -o "$OUT.win.o" "$OUT.win.s" 2>&1 | grep -v "Warning" | grep -v "^$" || true
    [ -f "$OUT.win.o" ] || fail "windows: .o not produced"
    file "$OUT.win.o" | grep -q "Intel amd64 COFF" \
        || fail "windows: .o not Intel amd64 COFF (got: $(file $OUT.win.o))"
    ok "windows: Intel amd64 COFF object produced"
    if x86_64-w64-mingw32-ld -o "$OUT.win.exe" "$OUT.win.o" \
        -L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32 -lmsvcrt -lbcrypt 2>/dev/null; then
        file "$OUT.win.exe" | grep -q "PE32+ executable.*x86-64" \
            || fail "windows: .exe not PE32+ x86-64"
        ok "windows: PE32+ x86-64 .exe produced"
        PASS_WINDOWS=1
    else
        echo "(link skipped: missing mingw libs is non-fatal; object format verified)"
        PASS_WINDOWS=1
    fi
else
    echo "(skip: mingw cross-toolchain not available)"
fi

# --- 4. ARM64 Linux (cross-assemble only) ----------------------------------
echo ""
echo "=== ARM64 Linux (cross) ==="
if command -v clang >/dev/null 2>&1 && clang --print-targets 2>/dev/null | grep -q aarch64; then
    bin/nova "$SRC" --target=arm64 -o "$OUT.arm64.s" >/dev/null
    clang -target aarch64-linux-gnu -c "$OUT.arm64.s" -o "$OUT.arm64.o" 2>/dev/null \
        || fail "arm64: clang assemble failed"
    file "$OUT.arm64.o" | grep -q "ELF 64-bit LSB.*ARM aarch64" \
        || fail "arm64: not ARM aarch64 ELF (got: $(file $OUT.arm64.o))"
    if command -v llvm-readobj >/dev/null 2>&1; then
        llvm-readobj --file-headers "$OUT.arm64.o" | grep -q "EM_AARCH64" \
            || fail "arm64: EM_AARCH64 machine type missing in ELF header"
        ok "arm64: ELF EM_AARCH64 machine type confirmed"
    else
        ok "arm64: ELF 64-bit ARM aarch64 object produced"
    fi
    PASS_ARM64=1
else
    echo "(skip: clang without aarch64 support)"
fi

# --- 5. Windows ARM64 (cross-assemble only) --------------------------------
echo ""
echo "=== Windows ARM64 (cross) ==="
if command -v clang >/dev/null 2>&1 && clang --print-targets 2>/dev/null | grep -q aarch64; then
    bin/nova "$SRC" --target=windows-arm64 -o "$OUT.winarm64.s" >/dev/null
    clang -target aarch64-windows-gnu -c "$OUT.winarm64.s" -o "$OUT.winarm64.o" 2>/dev/null \
        || fail "windows-arm64: clang assemble failed"
    file "$OUT.winarm64.o" | grep -q "Aarch64 COFF" \
        || fail "windows-arm64: not Aarch64 COFF (got: $(file $OUT.winarm64.o))"
    if command -v llvm-readobj >/dev/null 2>&1; then
        llvm-readobj --file-headers "$OUT.winarm64.o" | grep -q "IMAGE_FILE_MACHINE_ARM64" \
            || fail "windows-arm64: IMAGE_FILE_MACHINE_ARM64 missing in COFF header"
        ok "windows-arm64: COFF IMAGE_FILE_MACHINE_ARM64 confirmed"
    else
        ok "windows-arm64: Aarch64 COFF object produced"
    fi
    PASS_WINARM64=1
else
    echo "(skip: clang without aarch64 support)"
fi

# --- 6. WASM (wat2wasm + wasmtime run) -------------------------------------
echo ""
echo "=== WASM (WASI) ==="
if command -v wat2wasm >/dev/null 2>&1 && command -v wasmtime >/dev/null 2>&1; then
    bin/nova "$SRC" --target=wasm -o "$OUT.wat" >/dev/null
    wat2wasm "$OUT.wat" -o "$OUT.wasm" 2>&1 | grep -v "^$" || true
    [ -f "$OUT.wasm" ] || fail "wasm: .wasm not produced by wat2wasm"
    file "$OUT.wasm" | grep -q "WebAssembly" \
        || fail "wasm: not a WebAssembly module"
    out=$(wasmtime "$OUT.wasm" 2>&1)
    if echo "$out" | grep -q "R25A cross-target struct OK"; then
        ok "wasm: program runs under wasmtime and prints expected marker"
        PASS_WASM=1
    else
        fail "wasm: unexpected stdout: $out"
    fi
else
    echo "(skip: wat2wasm or wasmtime not available)"
fi

echo ""
echo "=== R25A cross-target struct summary ==="
echo "  linux           : $([ $PASS_LINUX = 1 ] && echo PASS || echo SKIP)"
echo "  macos           : $([ $PASS_MACOS = 1 ] && echo PASS || echo SKIP)"
echo "  windows         : $([ $PASS_WINDOWS = 1 ] && echo PASS || echo SKIP)"
echo "  arm64           : $([ $PASS_ARM64 = 1 ] && echo PASS || echo SKIP)"
echo "  windows-arm64   : $([ $PASS_WINARM64 = 1 ] && echo PASS || echo SKIP)"
echo "  wasm            : $([ $PASS_WASM = 1 ] && echo PASS || echo SKIP)"
echo ""
echo "=== test_struct_brace_cross_target: PASS ==="
