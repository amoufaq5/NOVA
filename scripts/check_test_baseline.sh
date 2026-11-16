#!/bin/bash
# Run tests/run_tests.sh and compare its failures with tests/known_failures.txt.
#
# Exit 1 if any test fails that is not listed (a regression). A listed test
# that now passes only prints a note: remove it from the list in the same PR.
#
# The runner itself exits non-zero whenever anything fails, so its exit code
# is not used; the comparison is the gate.

cd "$(dirname "$0")/.." || exit 2
KNOWN=${KNOWN_FAILURES:-tests/known_failures.txt}
OUT=$(mktemp)
trap 'rm -f "$OUT" "$OUT.f" "$OUT.k"' EXIT

bash tests/run_tests.sh >"$OUT" 2>&1
grep -E '^  (Passed|Failed|Skipped):' "$OUT"

grep -E '^  FAIL  test_' "$OUT" | awk '{print $2}' | sort -u >"$OUT.f"
grep -v '^#' "$KNOWN" | grep -v '^$' | sort -u >"$OUT.k"

NEW=$(comm -23 "$OUT.f" "$OUT.k")
FIXED=$(comm -13 "$OUT.f" "$OUT.k")

if [ -n "$FIXED" ]; then
    echo ""
    echo "Listed in $KNOWN but passing now (remove them from the list):"
    echo "$FIXED" | sed 's/^/  /'
fi
if [ -n "$NEW" ]; then
    echo ""
    echo "REGRESSION: failing tests not in $KNOWN:"
    echo "$NEW" | sed 's/^/  /'
    echo ""
    for t in $NEW; do
        grep -E "^  FAIL  $t " "$OUT" | head -1
    done
    exit 1
fi
echo ""
echo "No regressions against $KNOWN."
