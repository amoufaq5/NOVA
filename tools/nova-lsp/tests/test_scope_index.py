"""Unit tests for the R36D scope index.

Covers :mod:`nova_lsp.scope_index`:

  * Top-level fn declarations registered in the FILE scope.
  * Nested block introduces a child scope.
  * Function parameters land in the FN scope's bindings.
  * Local ``let`` bindings register in their containing scope.
  * ``scope_at(line, col)`` returns the most-nested scope.
  * ``scope_at`` for out-of-fn positions falls back to FILE scope.
  * ``resolve("x", line, col)`` returns the correct declaring
    scope, including the shadowing case.
  * ``live_at_range`` returns identifiers read inside a range that
    are declared in an outer scope.
  * ``assigned_used_after`` returns names assigned in a range that
    are read after — minus the cases where a nested block shadows
    the binding.

Tests are written in the same lightweight ``assert_`` /
``assert_eq`` style as the rest of the LSP test suite so failures
point straight at the regression.
"""
from __future__ import annotations

import os
import sys

# Make the lsp package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_lsp.scope_index import (  # noqa: E402
    SCOPE_BLOCK,
    SCOPE_FILE,
    SCOPE_FN,
    Scope,
    ScopeIndex,
    build_scope_index,
)


_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# Scope tree shape.
# ---------------------------------------------------------------------------


def test_empty_source_has_file_scope() -> None:
    """A zero-line file still produces a usable index with a FILE
    scope at the root."""
    idx = build_scope_index("")
    assert_eq(idx.file_scope.kind, SCOPE_FILE, "root is FILE")
    assert_eq(idx.file_scope.parent, None, "FILE has no parent")
    assert_eq(idx.file_scope.children, [], "FILE has no children")


def test_top_level_fn_registered_in_file_scope() -> None:
    """A top-level ``fn`` declaration registers the fn's name in the
    FILE scope's bindings."""
    src = (
        "fn run(x) {\n"
        "    return x\n"
        "}\n"
    )
    idx = build_scope_index(src)
    assert_("run" in idx.file_scope.bindings, "run registered in FILE")
    assert_eq(
        idx.file_scope.bindings["run"], 0,
        "run declared on line 0",
    )


def test_multiple_top_level_fns_all_registered() -> None:
    """Every top-level ``fn`` in the file is captured."""
    src = (
        "fn alpha() { return 0 }\n"
        "fn beta() { return 1 }\n"
        "fn gamma() { return 2 }\n"
    )
    idx = build_scope_index(src)
    for name in ("alpha", "beta", "gamma"):
        assert_(name in idx.file_scope.bindings, f"{name} registered")
    assert_eq(
        len(idx.file_scope.children), 3,
        "three FN scopes nested under FILE",
    )
    for child in idx.file_scope.children:
        assert_eq(child.kind, SCOPE_FN, "child is FN")


def test_fn_parameters_in_fn_scope() -> None:
    """Function parameters land in the FN scope's bindings."""
    src = (
        "fn run(a, b, c) {\n"
        "    return a + b + c\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    for p in ("a", "b", "c"):
        assert_(p in fn.bindings, f"param {p} in fn bindings")


def test_fn_let_bindings_registered() -> None:
    """``let`` bindings declared directly in the fn body land in the
    FN scope."""
    src = (
        "fn run() {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "    return x + y\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    assert_("x" in fn.bindings, "x in fn bindings")
    assert_("y" in fn.bindings, "y in fn bindings")
    assert_eq(fn.bindings["x"], 1, "x on line 1")
    assert_eq(fn.bindings["y"], 2, "y on line 2")


def test_nested_block_creates_child_scope() -> None:
    """An ``if`` block opens a new BLOCK scope as a child of the FN
    scope."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let a = x\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    assert_eq(len(fn.children), 1, "one BLOCK child")
    block = fn.children[0]
    assert_eq(block.kind, SCOPE_BLOCK, "child is BLOCK")
    assert_("a" in block.bindings, "a in block bindings")


def test_block_binding_not_in_fn_scope() -> None:
    """A let bound inside a block is NOT registered in the outer
    fn scope."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let tmp = x + 1\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    assert_(
        "tmp" not in fn.bindings,
        "tmp does NOT leak to fn scope",
    )


def test_nested_blocks_three_levels() -> None:
    """Nested ``if`` -> ``while`` -> ``if`` builds a 3-level chain."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        while x > 1 {\n"
        "            if x > 2 {\n"
        "                let a = x\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    b1 = fn.children[0]
    b2 = b1.children[0]
    b3 = b2.children[0]
    assert_eq(b1.kind, SCOPE_BLOCK, "b1 BLOCK")
    assert_eq(b2.kind, SCOPE_BLOCK, "b2 BLOCK")
    assert_eq(b3.kind, SCOPE_BLOCK, "b3 BLOCK")
    assert_("a" in b3.bindings, "a in innermost block")


# ---------------------------------------------------------------------------
# scope_at.
# ---------------------------------------------------------------------------


def test_scope_at_returns_file_for_top_level_position() -> None:
    """A position outside any fn returns the FILE scope."""
    src = (
        'import "std/io.nova"\n'
        "\n"
        "fn run() { return 0 }\n"
    )
    idx = build_scope_index(src)
    s = idx.scope_at(0, 0)
    assert_eq(s.kind, SCOPE_FILE, "import line resolves to FILE")


def test_scope_at_returns_fn_for_position_in_body() -> None:
    """A position on a body line of a fn returns the FN scope."""
    src = (
        "fn run(x) {\n"
        "    let a = x\n"
        "    return a\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.scope_at(2, 4)
    assert_eq(s.kind, SCOPE_FN, "fn body resolves to FN")
    assert_eq(s.fn_name, "run", "fn name preserved")


def test_scope_at_returns_block_for_nested_position() -> None:
    """A position inside an ``if`` body returns the BLOCK scope."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let a = x\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.scope_at(2, 8)
    assert_eq(s.kind, SCOPE_BLOCK, "inside if -> BLOCK")


def test_scope_at_out_of_document() -> None:
    """A position past the end of the document falls back to FILE."""
    src = "fn run() { return 0 }\n"
    idx = build_scope_index(src)
    s = idx.scope_at(99, 0)
    assert_eq(s.kind, SCOPE_FILE, "OOB position -> FILE")


# ---------------------------------------------------------------------------
# resolve.
# ---------------------------------------------------------------------------


def test_resolve_finds_fn_parameter() -> None:
    """``resolve("x", line, col)`` returns the FN scope when ``x`` is
    a parameter."""
    src = (
        "fn run(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.resolve("x", 1, 11)
    assert_(s is not None, "x resolves")
    assert_eq(s.kind, SCOPE_FN, "x in FN scope")


def test_resolve_finds_local_let() -> None:
    """``resolve("a", line, col)`` returns the FN scope when ``a``
    was bound by ``let`` earlier in the body."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    return a\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.resolve("a", 2, 11)
    assert_(s is not None, "a resolves")
    assert_eq(s.kind, SCOPE_FN, "a in FN scope")
    assert_eq(s.bindings.get("a"), 1, "a bound on line 1")


def test_resolve_returns_none_for_unknown() -> None:
    """A name not declared anywhere returns ``None``."""
    src = (
        "fn run() {\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.resolve("does_not_exist", 1, 4)
    assert_eq(s, None, "unknown -> None")


def test_resolve_inner_let_shadows_outer() -> None:
    """An inner ``let x`` shadows the outer ``x``; resolve returns
    the inner scope for positions inside the inner block."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let x = 99\n"
        "        return x\n"
        "    }\n"
        "    return x\n"
        "}\n"
    )
    idx = build_scope_index(src)
    inner = idx.resolve("x", 3, 15)  # inside the if block
    outer = idx.resolve("x", 5, 11)  # back in the fn body
    assert_(inner is not None, "inner resolves")
    assert_(outer is not None, "outer resolves")
    assert_eq(inner.kind, SCOPE_BLOCK, "inner is BLOCK")
    assert_eq(outer.kind, SCOPE_FN, "outer is FN")
    assert_(inner is not outer, "different scopes")


def test_resolve_finds_top_level_fn() -> None:
    """A reference to a top-level fn from another fn resolves to the
    FILE scope."""
    src = (
        "fn helper(x) { return x }\n"
        "fn caller() { return helper(0) }\n"
    )
    idx = build_scope_index(src)
    s = idx.resolve("helper", 1, 21)
    assert_(s is not None, "helper resolves")
    assert_eq(s.kind, SCOPE_FILE, "top-level fn in FILE")


def test_resolve_block_let_not_visible_from_outer() -> None:
    """A let bound inside a block isn't visible from outside the
    block."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let inner = x\n"
        "    }\n"
        "    return inner\n"
        "}\n"
    )
    idx = build_scope_index(src)
    s = idx.resolve("inner", 4, 11)
    assert_eq(s, None, "inner not visible after block closes")


# ---------------------------------------------------------------------------
# live_at_range.
# ---------------------------------------------------------------------------


def test_live_at_range_picks_up_params() -> None:
    """Identifiers in the range that are fn params count as live-in
    (extract-fn params)."""
    src = (
        "fn run(x, y) {\n"
        "    let z = x + y\n"
        "    println(z)\n"
        "    return z\n"
        "}\n"
    )
    idx = build_scope_index(src)
    live = idx.live_at_range(1, 2, builtins={"println"})
    # x and y come from the outer fn scope -> free.
    assert_("x" in live, "x is live-in")
    assert_("y" in live, "y is live-in")
    # z is bound inside the selection -> not live-in.
    assert_("z" not in live, "z (selection-local) not live-in")


def test_live_at_range_excludes_block_local_let() -> None:
    """A let bound inside a nested block within the selection is
    NOT a live-in."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let tmp = x + 1\n"
        "        println(tmp)\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    idx = build_scope_index(src)
    live = idx.live_at_range(1, 4, builtins={"println"})
    assert_("x" in live, "x is live-in")
    assert_("tmp" not in live, "tmp (block-local) not live-in")


def test_live_at_range_excludes_top_level_fns() -> None:
    """A reference to a top-level fn from inside the selection is NOT
    a live-in — top-level fns are callable without being params."""
    src = (
        "fn helper(x) { return x }\n"
        "fn caller(a) {\n"
        "    let r = helper(a)\n"
        "    let s = r + 1\n"
        "    return s\n"
        "}\n"
    )
    idx = build_scope_index(src)
    live = idx.live_at_range(2, 3, builtins=set())
    assert_("a" in live, "a is live-in")
    assert_("helper" not in live, "top-level fn not live-in")


def test_live_at_range_order_preserved() -> None:
    """Live-ins are returned in first-seen source order."""
    src = (
        "fn run(a, b, c) {\n"
        "    let x = c + b + a\n"
        "    return x\n"
        "}\n"
    )
    idx = build_scope_index(src)
    live = idx.live_at_range(1, 1, builtins=set())
    # The selection mentions c, b, a in that order on the let line.
    assert_eq(live, ["c", "b", "a"], "first-seen order preserved")


# ---------------------------------------------------------------------------
# assigned_used_after.
# ---------------------------------------------------------------------------


def test_assigned_used_after_picks_up_escaping_let() -> None:
    """A let bound inside the selection that's read after counts as
    live-out."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    idx = build_scope_index(src)
    out = idx.assigned_used_after(1, 2)
    # b is the only one read after (line 3); a is not (line 2 was
    # inside the selection).
    assert_("b" in out, "b is live-out")


def test_assigned_used_after_excludes_non_escaping_let() -> None:
    """A let inside the selection that's never read again is NOT
    live-out."""
    src = (
        "fn run(x) {\n"
        "    let temp = x + 1\n"
        "    println(temp)\n"
        "    return x\n"
        "}\n"
    )
    idx = build_scope_index(src)
    out = idx.assigned_used_after(1, 2)
    # `temp` is only used inside the selection itself.
    assert_("temp" not in out, "temp doesn't escape")


def test_assigned_used_after_excludes_inner_block_binding() -> None:
    """A let bound inside a nested block inside the selection (that
    block doesn't outlive the selection) doesn't escape."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let tmp = x + 1\n"
        "    }\n"
        "    let tmp = x + 2\n"
        "    return tmp\n"
        "}\n"
    )
    idx = build_scope_index(src)
    # Select lines 1-3 (the if block). The inner ``tmp`` is bound to
    # the inner block; the outer ``tmp`` at line 4 is a SEPARATE
    # binding. So `tmp` should NOT be live-out for selection 1-3.
    out = idx.assigned_used_after(1, 3)
    assert_("tmp" not in out, "inner block-tmp doesn't escape")


def test_assigned_used_after_with_shadowing() -> None:
    """When the post-selection code defines a shadow with the same
    name, the inner binding shadows — so the SELECTION's binding does
    NOT escape (the read resolves to the shadow, not the selection's
    binding)."""
    src = (
        "fn run(x) {\n"
        "    let y = x\n"
        "    if x > 0 {\n"
        "        let y = x + 1\n"
        "        println(y)\n"
        "    }\n"
        "    return y\n"
        "}\n"
    )
    idx = build_scope_index(src)
    # Select lines 3-4 (inner block lines). The inner `y` is in the
    # block scope. After the selection (line 6 ``return y``), the
    # read resolves to the OUTER y (line 1), not the inner.
    out = idx.assigned_used_after(3, 4)
    assert_("y" not in out, "inner y doesn't escape via shadow")


def test_assigned_used_after_bare_assignment() -> None:
    """A bare ``NAME = expr`` re-assignment counts when the variable
    is read after the selection."""
    src = (
        "fn run() {\n"
        "    let total = 0\n"
        "    total = total + 1\n"
        "    total = total + 2\n"
        "    return total\n"
        "}\n"
    )
    idx = build_scope_index(src)
    out = idx.assigned_used_after(2, 3)
    assert_("total" in out, "total is live-out")


def test_assigned_used_after_order_preserved() -> None:
    """Returns are ordered by first-seen assignment."""
    src = (
        "fn run(n) {\n"
        "    let a = n + 1\n"
        "    let b = a + 1\n"
        "    let c = b + 1\n"
        "    return a + b + c\n"
        "}\n"
    )
    idx = build_scope_index(src)
    out = idx.assigned_used_after(1, 3)
    assert_eq(out, ["a", "b", "c"], "first-seen-assignment order")


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_string_literal_brace_does_not_open_scope() -> None:
    """A ``{`` inside a string literal does NOT open a scope."""
    src = (
        'fn run() {\n'
        '    let s = "this has { inside"\n'
        '    let t = "and } too"\n'
        '    return s\n'
        '}\n'
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    assert_eq(
        len(fn.children), 0,
        "no fake BLOCK from string-literal brace",
    )


def test_line_comment_brace_does_not_open_scope() -> None:
    """A ``{`` after ``//`` doesn't open a scope."""
    src = (
        "fn run() {\n"
        "    let a = 1 // here's a { brace\n"
        "    return a\n"
        "}\n"
    )
    idx = build_scope_index(src)
    fn = idx.file_scope.children[0]
    assert_eq(
        len(fn.children), 0,
        "no fake BLOCK from comment brace",
    )


def test_multiple_fns_each_has_independent_scope() -> None:
    """Two top-level fns each get their own FN scope; bindings don't
    leak across."""
    src = (
        "fn first(a) {\n"
        "    let x = a\n"
        "    return x\n"
        "}\n"
        "fn second(b) {\n"
        "    let y = b\n"
        "    return y\n"
        "}\n"
    )
    idx = build_scope_index(src)
    first_fn = idx.file_scope.children[0]
    second_fn = idx.file_scope.children[1]
    assert_("x" in first_fn.bindings, "x in first")
    assert_("y" in second_fn.bindings, "y in second")
    assert_(
        "y" not in first_fn.bindings,
        "y doesn't leak into first",
    )
    assert_(
        "x" not in second_fn.bindings,
        "x doesn't leak into second",
    )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Scope tree shape
        test_empty_source_has_file_scope,
        test_top_level_fn_registered_in_file_scope,
        test_multiple_top_level_fns_all_registered,
        test_fn_parameters_in_fn_scope,
        test_fn_let_bindings_registered,
        test_nested_block_creates_child_scope,
        test_block_binding_not_in_fn_scope,
        test_nested_blocks_three_levels,
        # scope_at
        test_scope_at_returns_file_for_top_level_position,
        test_scope_at_returns_fn_for_position_in_body,
        test_scope_at_returns_block_for_nested_position,
        test_scope_at_out_of_document,
        # resolve
        test_resolve_finds_fn_parameter,
        test_resolve_finds_local_let,
        test_resolve_returns_none_for_unknown,
        test_resolve_inner_let_shadows_outer,
        test_resolve_finds_top_level_fn,
        test_resolve_block_let_not_visible_from_outer,
        # live_at_range
        test_live_at_range_picks_up_params,
        test_live_at_range_excludes_block_local_let,
        test_live_at_range_excludes_top_level_fns,
        test_live_at_range_order_preserved,
        # assigned_used_after
        test_assigned_used_after_picks_up_escaping_let,
        test_assigned_used_after_excludes_non_escaping_let,
        test_assigned_used_after_excludes_inner_block_binding,
        test_assigned_used_after_with_shadowing,
        test_assigned_used_after_bare_assignment,
        test_assigned_used_after_order_preserved,
        # Edge cases
        test_string_literal_brace_does_not_open_scope,
        test_line_comment_brace_does_not_open_scope,
        test_multiple_fns_each_has_independent_scope,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}", file=sys.stderr)
            failed += 1
        except Exception as e:  # pragma: no cover — defensive
            print(f"ERROR: {t.__name__}: {e!r}", file=sys.stderr)
            failed += 1
    if failed:
        print(
            f"test_scope_index: FAIL — {failed} test(s) failed",
            file=sys.stderr,
        )
        return 1
    print(f"test_scope_index: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
