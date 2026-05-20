#!/bin/bash
# M9 Self-Hosting & Full-System Verification Test
# Part A: Proves that the Nova compiler can compile itself to a fixpoint.
# Part B: Compiles the full cognitive system into a native binary and runs it.

set -e

COMPILER_SRC="/tmp/nova_compiler_src_m9.nova"
STAGE2_ASM="/tmp/m9_stage2.s"
STAGE3_ASM="/tmp/m9_stage3.s"

echo "============================================================"
echo "  M9: Self-Hosting & Full-System Verification"
echo "============================================================"
echo ""

# ---- Part A: Self-Hosting Fixpoint ----

echo "=== Part A: Self-Hosting Fixpoint ==="
echo ""

echo "Step 1: Bundling compiler source..."
cat src/compiler/ast.nova \
    src/compiler/lexer.nova \
    src/compiler/parser.nova \
    src/compiler/ir.nova \
    src/compiler/regalloc.nova \
    src/compiler/lower_x64.nova \
    src/compiler/codegen.nova \
    src/pkg/pkg.nova \
    src/compiler/compiler.nova \
    > "$COMPILER_SRC"
LINES=$(wc -l < "$COMPILER_SRC")
echo "  Compiler source: $LINES lines"

echo "Step 2: bin/nova compiles compiler source -> stage2..."
./bin/nova "$COMPILER_SRC" -o "$STAGE2_ASM" > /dev/null
as -o /tmp/m9_stage2.o "$STAGE2_ASM"
ld -o /tmp/m9_stage2 /tmp/m9_stage2.o
echo "  Stage 2 binary built."

echo "Step 3: Stage 2 compiles compiler source -> stage3..."
/tmp/m9_stage2 "$COMPILER_SRC" -o "$STAGE3_ASM" > /dev/null
echo "  Stage 3 assembly generated."

echo "Step 4: Comparing stage2.s and stage3.s..."
S2_SIZE=$(wc -c < "$STAGE2_ASM")
S3_SIZE=$(wc -c < "$STAGE3_ASM")
S2_MD5=$(md5sum "$STAGE2_ASM" | cut -d' ' -f1)
S3_MD5=$(md5sum "$STAGE3_ASM" | cut -d' ' -f1)

echo "  Stage 2: $S2_SIZE bytes, MD5 $S2_MD5"
echo "  Stage 3: $S3_SIZE bytes, MD5 $S3_MD5"

if diff -q "$STAGE2_ASM" "$STAGE3_ASM" > /dev/null 2>&1; then
    echo ""
    echo "SELF-HOSTING VERIFIED: Stage 2 and Stage 3 are identical."
    echo ""
else
    echo ""
    echo "SELF-HOSTING FAILED: Stage 2 and Stage 3 differ."
    diff "$STAGE2_ASM" "$STAGE3_ASM" | head -20
    echo ""
    echo "=== M9 FAILED (Part A) ==="
    exit 1
fi

# ---- Part B: Full Cognitive System ----

echo "=== Part B: Full Cognitive System ==="
echo ""

FULL_SYSTEM="/tmp/nova_full_system_m9.nova"
FULL_ASM="/tmp/m9_full.s"

echo "Step 5: Bundling full cognitive system..."
cat src/core/moment.nova \
    src/core/signal.nova \
    src/core/similarity.nova \
    src/core/node.nova \
    src/core/channel.nova \
    src/core/path.nova \
    src/mind/academic.nova \
    src/mind/experiential.nova \
    src/mind/emotion.nova \
    src/mind/memory.nova \
    src/mind/reasoning.nova \
    tests/test_m9_fullsystem.nova \
    > "$FULL_SYSTEM"
FULL_LINES=$(wc -l < "$FULL_SYSTEM")
echo "  Full system source: $FULL_LINES lines"

echo "Step 6: Compiling full system with bin/nova..."
./bin/nova "$FULL_SYSTEM" -o "$FULL_ASM"
as -o /tmp/m9_full.o "$FULL_ASM"
ld -o /tmp/m9_full /tmp/m9_full.o
echo "  Full system binary built."

echo "Step 7: Running full cognitive system..."
echo ""
OUTPUT=$(/tmp/m9_full 2>&1)
echo "$OUTPUT"

if echo "$OUTPUT" | grep -q "M9 full system verification PASSED"; then
    echo ""
    echo "FULL SYSTEM VERIFIED."
    echo ""
else
    echo ""
    echo "FULL SYSTEM FAILED."
    echo "=== M9 FAILED (Part B) ==="
    exit 1
fi

# ---- Part C: Agent Compilation ----

echo "=== Part C: Agent Binary ==="
echo ""

AGENT_SRC="/tmp/nova_agent_m9.nova"
AGENT_ASM="/tmp/m9_agent.s"

echo "Step 8: Bundling cognitive agent..."
cat src/core/moment.nova \
    src/core/signal.nova \
    src/core/similarity.nova \
    src/core/node.nova \
    src/core/channel.nova \
    src/core/path.nova \
    src/mind/academic.nova \
    src/mind/experiential.nova \
    src/mind/emotion.nova \
    src/mind/memory.nova \
    src/mind/reasoning.nova \
    src/agent/agent.nova \
    > "$AGENT_SRC"
AGENT_LINES=$(wc -l < "$AGENT_SRC")
echo "  Agent source: $AGENT_LINES lines"

echo "Step 9: Compiling agent with bin/nova..."
./bin/nova "$AGENT_SRC" -o "$AGENT_ASM"
as -o /tmp/m9_agent.o "$AGENT_ASM"
ld -o /tmp/m9_agent /tmp/m9_agent.o
AGENT_SIZE=$(wc -c < /tmp/m9_agent)
echo "  Agent binary built: $AGENT_SIZE bytes."
echo ""
echo "AGENT COMPILATION VERIFIED."
echo ""

# ---- Summary ----

echo "============================================================"
echo "  M9 Results"
echo "============================================================"
echo "  Compiler self-hosting:  PASS (fixpoint verified)"
echo "  Full cognitive system:  PASS (all subsystems functional)"
echo "  Agent compilation:      PASS ($AGENT_SIZE byte binary)"
echo "  Compiler source:        $LINES lines"
echo "  Full system source:     $FULL_LINES lines"
echo "  Agent source:           $AGENT_LINES lines"
echo "============================================================"
echo ""
echo "=== M9 PASSED ==="
