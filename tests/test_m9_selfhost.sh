#!/bin/bash
# M9 Self-Hosting Verification Test
# Proves that the Nova compiler can compile itself to produce
# an identical binary (fixpoint achieved).

set -e

COMPILER_SRC="/tmp/nova_compiler_src_m9.nova"
STAGE2_ASM="/tmp/m9_stage2.s"
STAGE3_ASM="/tmp/m9_stage3.s"

echo "=== M9: Self-Hosting Verification ==="

# Step 1: Concatenate compiler source
echo "Step 1: Bundling compiler source..."
cat src/compiler/ast.nova \
    src/compiler/lexer.nova \
    src/compiler/parser.nova \
    src/compiler/codegen.nova \
    src/compiler/compiler.nova \
    > "$COMPILER_SRC"
LINES=$(wc -l < "$COMPILER_SRC")
echo "  Compiler source: $LINES lines"

# Step 2: Stage 1 compiler (bin/nova) compiles compiler source -> stage2
echo "Step 2: bin/nova compiles compiler source -> stage2..."
./bin/nova "$COMPILER_SRC" -o "$STAGE2_ASM" > /dev/null
as -o /tmp/m9_stage2.o "$STAGE2_ASM"
ld -o /tmp/m9_stage2 /tmp/m9_stage2.o
echo "  Stage 2 binary built."

# Step 3: Stage 2 compiles compiler source -> stage3
echo "Step 3: Stage 2 compiles compiler source -> stage3..."
/tmp/m9_stage2 "$COMPILER_SRC" -o "$STAGE3_ASM" > /dev/null
echo "  Stage 3 assembly generated."

# Step 4: Compare stage2 and stage3 assembly
echo "Step 4: Comparing stage2.s and stage3.s..."
S2_SIZE=$(wc -c < "$STAGE2_ASM")
S3_SIZE=$(wc -c < "$STAGE3_ASM")
S2_MD5=$(md5sum "$STAGE2_ASM" | cut -d' ' -f1)
S3_MD5=$(md5sum "$STAGE3_ASM" | cut -d' ' -f1)

echo "  Stage 2: $S2_SIZE bytes, MD5 $S2_MD5"
echo "  Stage 3: $S3_SIZE bytes, MD5 $S3_MD5"

if diff -q "$STAGE2_ASM" "$STAGE3_ASM" > /dev/null 2>&1; then
    echo ""
    echo "SELF-HOSTING VERIFIED: Stage 2 and Stage 3 outputs are identical."
    echo "The Nova compiler successfully compiles itself to a fixpoint."
    echo ""
    echo "=== M9 PASSED ==="
    exit 0
else
    echo ""
    echo "SELF-HOSTING FAILED: Stage 2 and Stage 3 outputs differ."
    diff "$STAGE2_ASM" "$STAGE3_ASM" | head -20
    echo ""
    echo "=== M9 FAILED ==="
    exit 1
fi
