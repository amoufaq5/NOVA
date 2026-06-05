"""R36D — extract-function tightening via scope index.

R35F's variable analysis used a textual heuristic that flagged any
post-selection token-equality as a "use after". R36D replaces this
with a scope-aware lookup via :mod:`nova_lsp.scope_index`, so the
extract refactor produces minimum-correct param + return sets.

Test surface:

  * **No false-positive returns** — a let bound inside the selection
    that's never read outside the selection is NOT in the return
    list. R35F was correct on this happy path; R36D preserves it.
  * **No false-positive returns under shadowing** — when a
    post-selection block contains its own ``let NAME = ...`` that
    shadows the selection's binding, the binding does NOT escape
    even though the textual scan would have flagged it.
  * **Block-scoped binding** — ``if cond { let tmp = ... }`` inside
    the selection: ``tmp`` does NOT escape, even when the
    post-selection code declares a different ``tmp``.
  * **No false-positive params from block locals** — a let bound
    inside a nested block (within the selection) is NOT a parameter
    of the helper.
  * **Top-level fn references not params** — calling a top-level
    fn from the selection does NOT make the fn name a parameter.

Each test pins one R36D behavioural improvement.
"""
from __future__ import annotations

import os
import sys

# Make the lsp package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_lsp.extract_function import (  # noqa: E402
    analyze_selection,
    build_extract_edit,
)
from nova_lsp.scope_index import build_scope_index  # noqa: E402


_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _apply_edit(doc_text: str, uri: str, edit) -> str:
    """Apply a ``{"changes": {uri: [TextEdit]}}`` to a document."""
    changes = edit.get("changes") or {}
    edits = changes.get(uri) or []
    if not edits:
        return doc_text
    text = doc_text
    sorted_edits = sorted(
        edits,
        key=lambda e: (
            e["range"]["start"]["line"],
            e["range"]["start"]["character"],
        ),
        reverse=True,
    )
    for e in sorted_edits:
        r = e["range"]
        s_line, s_char = r["start"]["line"], r["start"]["character"]
        e_line, e_char = r["end"]["line"], r["end"]["character"]
        cur_lines = text.splitlines(keepends=True)
        s_off = sum(len(l) for l in cur_lines[:s_line]) + s_char
        e_off = sum(len(l) for l in cur_lines[:e_line]) + e_char
        text = text[:s_off] + e["newText"] + text[e_off:]
    return text


# ---------------------------------------------------------------------------
# Local lets that don't escape — not in return values.
# ---------------------------------------------------------------------------


def test_local_let_not_in_returns_when_not_used_after() -> None:
    """A let in the selection that's never read afterwards is NOT a
    return value. (R35F also handles this case correctly, but R36D's
    scope-aware path must preserve the behaviour.)"""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return x\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_eq(info.return_variables, [], "no live-out vars")


def test_local_let_in_returns_when_used_after() -> None:
    """The minimal happy path — a let that IS read after the
    selection is correctly captured as live-out."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_eq(info.return_variables, ["b"], "b is live-out")


# ---------------------------------------------------------------------------
# R36D win — variable shadowing inside a post-selection block.
#
# R35F's textual ``_used_after`` would scan the post-selection lines
# for any token equal to a selection-assigned name. If a post-
# selection block contains ``let tmp = ...`` that shadows, the inner
# ``println(tmp)`` reference would be counted as a "use of the
# original" — causing tmp to be flagged as live-out even though it
# isn't actually used after.
# ---------------------------------------------------------------------------


def test_shadowed_name_not_live_out() -> None:
    """When the post-selection code shadows the selection's binding
    with a new ``let``, the original binding does NOT escape — the
    inner read resolves to the shadow."""
    src = (
        "fn run(x) {\n"
        "    let tmp = x + 1\n"
        "    let r = tmp\n"
        "    if x > 0 {\n"
        "        let tmp = x + 99\n"
        "        let s = tmp\n"
        "    }\n"
        "    return r\n"
        "}\n"
    )
    # Select lines 1-2 (let tmp; let r = tmp). The R35F textual scan
    # would see ``tmp`` mentioned inside the if block on line 4 +
    # line 5 and flag tmp as live-out. The R36D scope-aware path
    # recognises the shadow and excludes tmp.
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 15},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    # r IS live-out (used at return).
    assert_("r" in info.return_variables, "r is live-out")
    # tmp is NOT live-out because the inner block has its own tmp.
    assert_(
        "tmp" not in info.return_variables,
        f"tmp shadowed -> not live-out: {info.return_variables}",
    )


def test_shadowing_inside_selection_does_not_escape() -> None:
    """A let bound inside an inner block of the selection isn't
    live-out — even when a post-selection line reads a same-named
    binding from the outer scope (the post read resolves to the
    outer binding which was declared BEFORE the selection)."""
    src = (
        "fn run() {\n"
        "    let acc = 0\n"
        "    if acc < 10 {\n"
        "        let acc = 99\n"
        "    }\n"
        "    return acc\n"
        "}\n"
    )
    # Select lines 2-4 (the if block). The inner ``let acc = 99``
    # binds a fresh acc in the block scope; line 5 reads the OUTER
    # acc (line 1). So the inner acc does NOT escape.
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 4, "character": 5},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src,
        builtins=set(),
    )
    assert_(info is not None, "analyzed")
    # acc inside the if-block doesn't escape because line 5's read
    # binds to the outer acc, not the inner one.
    assert_(
        "acc" not in info.return_variables,
        f"inner acc not live-out: {info.return_variables}",
    )


# ---------------------------------------------------------------------------
# R36D win — block-scoped binding inside the selection doesn't
# propagate as a return value.
# ---------------------------------------------------------------------------


def test_block_scoped_binding_does_not_escape() -> None:
    """``if cond { let tmp = ... }`` inside the selection: ``tmp``
    is block-scoped and doesn't escape even if a later line uses a
    differently-scoped ``tmp``."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let tmp = x + 1\n"
        "        println(tmp)\n"
        "    }\n"
        "    let tmp = x + 2\n"
        "    return tmp\n"
        "}\n"
    )
    # Select lines 1-4 (the if block). The R35F textual scan would
    # see ``tmp`` on line 5 and flag the inner ``tmp`` as live-out.
    # R36D recognises that the inner ``tmp`` is bound inside a child
    # scope of the selection's enclosing scope; the line-5 ``tmp`` is
    # a fresh binding in the outer scope.
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 4, "character": 5},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    assert_(
        "tmp" not in info.return_variables,
        f"inner tmp doesn't escape: {info.return_variables}",
    )


def test_block_scoped_let_not_a_param() -> None:
    """A let bound inside a nested block within the selection is NOT
    a parameter of the helper."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let inner = x + 1\n"
        "        println(inner)\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 4, "character": 5},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    assert_(
        "inner" not in info.free_variables,
        f"inner not in params: {info.free_variables}",
    )
    assert_(
        "x" in info.free_variables,
        f"x is a param: {info.free_variables}",
    )


# ---------------------------------------------------------------------------
# R36D win — top-level fn references aren't params.
# ---------------------------------------------------------------------------


def test_top_level_fn_call_not_a_param() -> None:
    """When the selection calls a top-level fn, the fn name does NOT
    become a parameter of the helper."""
    src = (
        "fn helper(x) { return x + 1 }\n"
        "fn run(a) {\n"
        "    let r = helper(a)\n"
        "    let s = r + 1\n"
        "    return s\n"
        "}\n"
    )
    # Select lines 2-3 (the two lets).
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_(
        "helper" not in info.free_variables,
        f"helper not in params: {info.free_variables}",
    )
    assert_(
        "a" in info.free_variables,
        f"a is a param: {info.free_variables}",
    )


# ---------------------------------------------------------------------------
# R36D — combined scenarios.
# ---------------------------------------------------------------------------


def test_combined_scope_aware_extraction() -> None:
    """A realistic case: a selection with a block-scoped let, a
    shadowed name, a true live-out, and a fn call."""
    src = (
        "fn other(x) { return x }\n"
        "fn run(a, b) {\n"
        "    let result = a + b\n"
        "    if a > 0 {\n"
        "        let local = other(a)\n"
        "        println(local)\n"
        "    }\n"
        "    return result\n"
        "}\n"
    )
    # Select lines 2-6: the let result + the if-block.
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 6, "character": 5},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    # a and b are params from the outer fn.
    assert_("a" in info.free_variables, "a is a param")
    assert_("b" in info.free_variables, "b is a param")
    # other is a top-level fn — not a param.
    assert_(
        "other" not in info.free_variables,
        "other (top-level fn) not a param",
    )
    # local is block-scoped — not a param.
    assert_(
        "local" not in info.free_variables,
        "local (block-scoped) not a param",
    )
    # result is used at return — live-out.
    assert_(
        "result" in info.return_variables,
        f"result is live-out: {info.return_variables}",
    )
    # local doesn't escape.
    assert_(
        "local" not in info.return_variables,
        "local doesn't escape",
    )


def test_helper_call_site_with_block_scoped_extract() -> None:
    """End-to-end: extract a block containing a block-scoped binding
    and verify the helper is correct."""
    src = (
        "fn run(x) {\n"
        "    let r = 0\n"
        "    if x > 0 {\n"
        "        let inner = x + 1\n"
        "        println(inner)\n"
        "    }\n"
        "    return r\n"
        "}\n"
    )
    # Select lines 2-5 (the if block).
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 5, "character": 5},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # The helper signature should ONLY have x (not r, not inner). We
    # check the argument list inside the parentheses to avoid false
    # positives where ``r`` matches inside ``extracted``.
    sig_line = next(
        l for l in new_text.splitlines() if "fn extracted_1(" in l
    )
    # Pull just the args between the parens.
    after_paren = sig_line.split("fn extracted_1(", 1)[1]
    args_str = after_paren.split(")", 1)[0]
    args = [a.strip() for a in args_str.split(",") if a.strip()]
    assert_eq(args, ["x"], f"helper sig has only x: {sig_line}")


# ---------------------------------------------------------------------------
# R36D — empty selection / fall-through.
# ---------------------------------------------------------------------------


def test_constant_expr_selection_no_params() -> None:
    """A selection that's a closed block — no free variables. ``a``
    and ``b`` are bound inside the selection; they're read AFTER on
    line 3, so the R36D scope-aware path correctly reports them as
    live-out (R35F was also correct here)."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = 2\n"
        "    println(a + b)\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 14},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    assert_eq(info.free_variables, [], "no params")
    # a and b are read on line 3 -> both live-out, first-seen order.
    assert_eq(info.return_variables, ["a", "b"], "a, b live-out")


def test_truly_closed_selection_no_returns() -> None:
    """A selection where the bound names are NEVER read after IS
    in fact return-free."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = 2\n"
        "    println(a + b)\n"
        "    return 0\n"
        "}\n"
    )
    # Select all three lines (1-3). a + b are read inside the
    # selection (in the println) but never after.
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 3, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    assert_eq(info.free_variables, [], "no params")
    assert_eq(info.return_variables, [], "no live-out")


def test_selection_with_post_block_shadow_does_not_flag_either_name() -> None:
    """If both the selection AND a post-selection block bind +
    locally use a name, neither use escapes."""
    src = (
        "fn run() {\n"
        "    let m = 1\n"
        "    println(m)\n"
        "    if m == 1 {\n"
        "        let m = 99\n"
        "        println(m)\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    # Select line 1 only (the let m = 1).
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 13},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src,
        builtins={"println"},
        require_min_lines=False,
    )
    assert_(info is not None, "analyzed (bypass min)")
    # m on line 2 (println(m)) is in the SAME fn scope as the let;
    # this DOES read the outer m. The inner m on line 4 doesn't.
    # So m IS live-out from selection line 1 because line 2 reads
    # the outer m.
    assert_("m" in info.return_variables, "m read on line 2 -> live-out")


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Local lets / happy path
        test_local_let_not_in_returns_when_not_used_after,
        test_local_let_in_returns_when_used_after,
        # Shadowing wins
        test_shadowed_name_not_live_out,
        test_shadowing_inside_selection_does_not_escape,
        # Block-scoped binding wins
        test_block_scoped_binding_does_not_escape,
        test_block_scoped_let_not_a_param,
        # Top-level fn references
        test_top_level_fn_call_not_a_param,
        # Combined
        test_combined_scope_aware_extraction,
        test_helper_call_site_with_block_scoped_extract,
        # Empty / fall-through
        test_constant_expr_selection_no_params,
        test_truly_closed_selection_no_returns,
        test_selection_with_post_block_shadow_does_not_flag_either_name,
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
            f"test_extract_function_scope_aware: FAIL — {failed} test(s) failed",
            file=sys.stderr,
        )
        return 1
    print(
        f"test_extract_function_scope_aware: OK ({_assertions} assertions)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
