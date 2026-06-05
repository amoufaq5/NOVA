#!/bin/bash
# Windows ARM64 (PE32+ AArch64) emitter assertions.
#
# Runs through `make smoke-winarm64` and then verifies binary
# invariants that don't need an ARM64-Windows host to validate:
#
#   1. The .exe files exist and `file` reports them as
#      PE32+ executable Aarch64 for MS Windows.
#   2. The COFF header machine type is 0xAA64
#      (IMAGE_FILE_MACHINE_ARM64). The PE signature lives at
#      offset 0x3C; the 4-byte machine field follows 4 bytes
#      after the signature ("PE\0\0" + Machine = signature+4).
#   3. The KERNEL32.DLL import is present in both binaries.
#   4. The BCRYPT.DLL import is present in secure_random_winarm64.exe.
#   5. The IAT call sequence appears in the .text disassembly
#      (`adrp x16, __imp_*` followed by `ldr x16, [x16, ...]` and
#      `blr x16`).
#
# Exits 0 on full pass, 1 on any assertion failure. Used by CI
# to gate the winarm64 backend without needing real ARM-Windows
# hardware in the loop.

set -e
cd "$(dirname "$0")/.."

HELLO=bin/hello_winarm64.exe
SR=bin/secure_random_winarm64.exe

# Skip cleanly if toolchain not present (matches Makefile policy).
if ! command -v clang >/dev/null 2>&1 || \
   ! command -v lld-link >/dev/null 2>&1 || \
   ! command -v llvm-dlltool >/dev/null 2>&1 || \
   ! command -v llvm-readobj >/dev/null 2>&1 || \
   ! command -v llvm-objdump >/dev/null 2>&1; then
    echo "(skip: winarm64 toolchain not available)"
    exit 0
fi

# Build the binaries.
make -s smoke-winarm64 >/dev/null 2>&1 || { echo "FAIL: smoke-winarm64 build failed"; exit 1; }

fail() {
    echo "FAIL: $1"
    exit 1
}

# 1. file format check.
file "$HELLO" | grep -q "PE32+ executable.*Aarch64" \
    || fail "$HELLO not PE32+ Aarch64"
file "$SR" | grep -q "PE32+ executable.*Aarch64" \
    || fail "$SR not PE32+ Aarch64"
echo "OK: file format PE32+ Aarch64 (both binaries)"

# 2. COFF machine type 0xAA64 at signature+4.
for bin in "$HELLO" "$SR"; do
    llvm-readobj --file-headers "$bin" | grep -q "IMAGE_FILE_MACHINE_ARM64 (0xAA64)" \
        || fail "$bin: machine type != 0xAA64"
done
echo "OK: COFF machine type 0xAA64 (both binaries)"

# Direct byte check: read PE signature offset at file offset 0x3C, then
# read the 4-byte machine field at signature+4. The Machine field is
# the FIRST 2 bytes of the COFF header (which follows the PE\0\0
# signature), so it sits at signature+4..+5. Little-endian: 64 AA
# == 0xAA64.
for bin in "$HELLO" "$SR"; do
    sigoff=$(od -An -tu4 -N4 -j 60 "$bin" | tr -d ' ')
    machine_bytes=$(od -An -tx1 -N2 -j $((sigoff + 4)) "$bin" | tr -d ' ')
    if [ "$machine_bytes" != "64aa" ]; then
        fail "$bin: raw machine bytes at offset $((sigoff + 4)) = $machine_bytes (expected 64aa)"
    fi
done
echo "OK: raw COFF machine bytes 64 AA at signature+4 (little-endian 0xAA64)"

# 3. KERNEL32.DLL import in both binaries.
for bin in "$HELLO" "$SR"; do
    llvm-objdump -p "$bin" | grep -q "KERNEL32.DLL" \
        || fail "$bin: KERNEL32.DLL import missing"
done
echo "OK: KERNEL32.DLL import (both binaries)"

# 4. BCRYPT.DLL import in secure_random binary.
llvm-objdump -p "$SR" | grep -q "BCRYPT.DLL" \
    || fail "$SR: BCRYPT.DLL import missing"
echo "OK: BCRYPT.DLL import (secure_random)"

# 5. IAT call sequence in disassembly. Look for the `adrp x16, ...`
# followed by `ldr x16` + `blr x16` pattern that warm_call_import
# emits. We just check the three instructions appear in the .text
# section of hello_winarm64.exe.
disasm=$(llvm-objdump -d "$HELLO")
echo "$disasm" | grep -q "adrp\sx16" || fail "$HELLO: no 'adrp x16' (IAT load)"
echo "$disasm" | grep -q "ldr\sx16" || fail "$HELLO: no 'ldr x16' (IAT deref)"
echo "$disasm" | grep -q "blr\sx16" || fail "$HELLO: no 'blr x16' (indirect call)"
echo "OK: IAT call sequence (adrp x16 / ldr x16 / blr x16) in $HELLO"

# 6. Standard ARM64 prologue is present.
echo "$disasm" | grep -q "stp\sx29, x30, \[sp, #-0x10\]!" \
    || fail "$HELLO: ARM64 prologue missing"
echo "OK: ARM64 prologue (stp x29, x30, [sp, #-0x10]!) present"

# 7. Binary sizes (sanity bound; real Windows binaries should be small).
hello_size=$(stat -c %s "$HELLO")
sr_size=$(stat -c %s "$SR")
if [ "$hello_size" -lt 1024 ] || [ "$hello_size" -gt 20480 ]; then
    fail "$HELLO size $hello_size out of expected [1024, 20480] range"
fi
if [ "$sr_size" -lt 1024 ] || [ "$sr_size" -gt 20480 ]; then
    fail "$SR size $sr_size out of expected [1024, 20480] range"
fi
echo "OK: binary sizes ($hello_size, $sr_size bytes)"

echo ""
echo "=== test_winarm64_emitter: PASS ==="
