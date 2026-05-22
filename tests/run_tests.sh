#!/bin/bash
# Nova Test Runner
# Compiles and runs all test_*.nova files, reports pass/fail

NOVA=${NOVA:-bin/nova}
AS=as
LD=ld
PASS=0
FAIL=0
SKIP=0
TOTAL=0

echo "=== Nova Test Suite ==="
echo "Compiler: $NOVA"
echo ""

for test_file in tests/test_*.nova; do
    test_name=$(basename "$test_file" .nova)
    TOTAL=$((TOTAL + 1))

    # Skip tests that need special handling
    case "$test_name" in
        test_import|test_import_lib|test_fileio|test_io_random)
            echo "  SKIP  $test_name (requires special setup)"
            SKIP=$((SKIP + 1))
            continue
            ;;
        test_ffi|test_ffi_extended)
            echo "  SKIP  $test_name (requires --link-libc; run via 'make ffi FILE=tests/$test_name.nova')"
            SKIP=$((SKIP + 1))
            continue
            ;;
    esac

    # M7/M8/M9 tests need combined source files (strip import lines to avoid double-include)
    INPUT="$test_file"
    case "$test_name" in
        test_m7_core)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/core/soul.nova src/runtime/scheduler.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_m8_mind)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/core/soul.nova src/runtime/scheduler.nova \
                src/mind/academic.nova src/mind/experiential.nova src/mind/emotion.nova \
                src/mind/memory.nova src/mind/reasoning.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_flow_ops)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/core/soul.nova src/runtime/scheduler.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_m9_fullsystem)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/core/soul.nova src/runtime/scheduler.nova \
                src/mind/academic.nova src/mind/experiential.nova src/mind/emotion.nova \
                src/mind/memory.nova src/mind/reasoning.nova \
                "$test_file" > /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_scheduler)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/runtime/scheduler.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_mind)
            cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
                src/core/node.nova src/core/channel.nova src/core/path.nova \
                src/runtime/scheduler.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_runtime)
            cat src/runtime/syscall.nova src/runtime/alloc.nova \
                src/runtime/string.nova src/runtime/io.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_tensor)
            cat src/runtime/tensor.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
        test_csv)
            cat src/runtime/csv.nova \
                > /tmp/nova_combined_test.nova
            grep -v '^import ' "$test_file" >> /tmp/nova_combined_test.nova
            INPUT="/tmp/nova_combined_test.nova"
            ;;
    esac

    # Compile
    $NOVA "$INPUT" -o /tmp/nova_test.s 2>/tmp/nova_test_err.txt
    if [ $? -ne 0 ]; then
        echo "  FAIL  $test_name (compile error)"
        cat /tmp/nova_test_err.txt
        FAIL=$((FAIL + 1))
        continue
    fi

    # Assemble
    $AS -o /tmp/nova_test.o /tmp/nova_test.s 2>/tmp/nova_test_err.txt
    if [ $? -ne 0 ]; then
        echo "  FAIL  $test_name (assembly error)"
        cat /tmp/nova_test_err.txt
        FAIL=$((FAIL + 1))
        continue
    fi

    # Link
    $LD -o /tmp/nova_test /tmp/nova_test.o 2>/tmp/nova_test_err.txt
    if [ $? -ne 0 ]; then
        echo "  FAIL  $test_name (link error)"
        cat /tmp/nova_test_err.txt
        FAIL=$((FAIL + 1))
        continue
    fi

    # Run with timeout
    timeout 10 /tmp/nova_test > /tmp/nova_test_out.txt 2>&1
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "  PASS  $test_name"
        PASS=$((PASS + 1))
    elif [ $EXIT_CODE -eq 124 ]; then
        echo "  FAIL  $test_name (timeout)"
        FAIL=$((FAIL + 1))
    else
        echo "  FAIL  $test_name (exit code: $EXIT_CODE)"
        cat /tmp/nova_test_out.txt
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== Results ==="
echo "  Total:   $TOTAL"
echo "  Passed:  $PASS"
echo "  Failed:  $FAIL"
echo "  Skipped: $SKIP"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "SOME TESTS FAILED"
    exit 1
else
    echo "ALL TESTS PASSED"
    exit 0
fi
