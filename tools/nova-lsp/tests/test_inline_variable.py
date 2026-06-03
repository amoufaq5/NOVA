"""Unit + integration tests for the inline-variable refactor.

Covers the R25F module at ``nova_lsp/inline_variable.py``:

  * ``find_let_at`` — locates the ``let`` binding at a cursor
    position; rejects non-let lines and empty RHS forms.
  * ``analyze_scope`` — walks the enclosing fn body for use sites;
    refuses on reassignment + closure capture.
  * ``detect_side_effects`` — conservative call-shape detector.
  * ``build_inline_edit`` — full ``WorkspaceEdit`` construction with
    parenthesised RHS substitution + let-line removal.
  * ``build_inline_action`` — top-level wrapper; surfaces the
    side-effect warning suffix in the action title.
  * Server-level wire smoke through ``dispatch`` for the full
    ``textDocument/codeAction`` round trip with a recognised let.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Make the lsp package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from _harness import LspClient  # noqa: E402
from nova_lsp.inline_variable import (  # noqa: E402
    KIND_REFACTOR_INLINE,
    InlineInfo,
    LetBinding,
    UseSite,
    analyze_scope,
    build_inline_action,
    build_inline_edit,
    detect_side_effects,
    find_let_at,
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


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _apply_edit(doc_text: str, uri: str, edit) -> str:
    """Apply a ``{"changes": {uri: [TextEdit]}}`` to a document.

    The inline-variable edit emits multiple ranged edits per call (one
    per use site plus the let-line removal); we apply them last-first so
    earlier offsets don't shift.
    """
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
# find_let_at — locate the binding.
# ---------------------------------------------------------------------------


def test_find_let_at_simple_let() -> None:
    """A clean `let x = 42` line under the cursor returns a binding."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    println(x)\n"
        "}\n"
    )
    pos = {"line": 1, "character": 8}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is not None, "let detected")
    assert_eq(binding.name, "x", "name parsed")
    assert_eq(binding.rhs, "42", "rhs parsed")
    assert_eq(binding.line, 1, "line preserved")


def test_find_let_at_with_type_annotation() -> None:
    """`let x: int = 7` — type annotation between name and `=`."""
    src = (
        "fn run() {\n"
        "    let x: int = 7\n"
        "    return x\n"
        "}\n"
    )
    pos = {"line": 1, "character": 4}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is not None, "annotated let detected")
    assert_eq(binding.name, "x", "name parsed with annotation")
    assert_eq(binding.rhs, "7", "rhs parsed past annotation")


def test_find_let_at_complex_rhs() -> None:
    """Compound RHS expression preserved verbatim."""
    src = (
        "fn run(a, b) {\n"
        "    let total = a + b * 2\n"
        "    return total\n"
        "}\n"
    )
    pos = {"line": 1, "character": 4}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is not None, "complex let detected")
    assert_eq(binding.rhs, "a + b * 2", "compound rhs preserved")


def test_find_let_at_non_let_line() -> None:
    """Cursor on a non-let line returns None."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    println(x)\n"
        "}\n"
    )
    pos = {"line": 2, "character": 4}  # `println(x)`
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is None, "non-let line rejected")


def test_find_let_at_out_of_bounds() -> None:
    """Cursor past EOF returns None."""
    src = "fn run() { let x = 1 }\n"
    pos = {"line": 99, "character": 0}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is None, "OOB position rejected")


def test_find_let_at_empty_rhs_rejected() -> None:
    """`let x =` (no RHS) -> None."""
    src = (
        "fn run() {\n"
        "    let x =\n"
        "    return 0\n"
        "}\n"
    )
    pos = {"line": 1, "character": 4}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is None, "empty RHS rejected")


def test_find_let_at_trailing_comment() -> None:
    """A trailing `// comment` is stripped from the RHS."""
    src = (
        "fn run() {\n"
        "    let x = 42  // the answer\n"
        "    return x\n"
        "}\n"
    )
    pos = {"line": 1, "character": 4}
    binding = find_let_at(_uri("/t.nova"), pos, src)
    assert_(binding is not None, "let with trailing comment detected")
    assert_eq(binding.rhs, "42", "rhs strips trailing comment")


# ---------------------------------------------------------------------------
# analyze_scope — use sites + refusal cases.
# ---------------------------------------------------------------------------


def test_analyze_scope_single_use() -> None:
    """`let x = 42; println(x)` finds 1 use site."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    println(x)\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is not None, "scope analysed")
    assert_eq(len(info.uses), 1, "one use of x")
    assert_eq(info.uses[0].line, 2, "use is on line 2")


def test_analyze_scope_multiple_uses() -> None:
    """Three uses, all detected."""
    src = (
        "fn run(a, b, c) {\n"
        "    let x = a\n"
        "    return b + x + c + x + x\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is not None, "scope analysed")
    assert_eq(len(info.uses), 3, "three uses of x")


def test_analyze_scope_zero_uses() -> None:
    """Unused let — analysis still succeeds (allows the cleanup)."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    return 0\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is not None, "unused let still inlineable (cleanup)")
    assert_eq(len(info.uses), 0, "zero uses")


def test_analyze_scope_refuses_reassignment() -> None:
    """`let x = 1; x = 2; use(x)` -> None (refuse)."""
    src = (
        "fn run() {\n"
        "    let x = 1\n"
        "    x = 2\n"
        "    return x\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is None, "reassignment refuses inline")


def test_analyze_scope_refuses_top_level() -> None:
    """`let` declared outside any fn -> None."""
    src = (
        "let GLOBAL = 42\n"
        "fn run() { return GLOBAL }\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 0, "character": 0}, src)
    info = analyze_scope(binding, src)
    assert_(info is None, "top-level let refused")


def test_analyze_scope_local_to_fn_a() -> None:
    """`let x` declared in fn A; another fn B uses an identifier
    `x` of its own — analyse_scope must restrict uses to fn A."""
    src = (
        "fn a(p) {\n"
        "    let x = p\n"
        "    return x\n"
        "}\n"
        "fn b(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is not None, "analysed")
    # Only 1 use (the `return x` inside fn a), NOT the `x + 1` in fn b.
    assert_eq(len(info.uses), 1, "scope walk stops at fn boundary")
    assert_eq(info.uses[0].line, 2, "use is inside fn a")


def test_analyze_scope_refuses_closure_capture() -> None:
    """`let x` captured by a nested `fn` -> refuse."""
    src = (
        "fn outer() {\n"
        "    let x = 42\n"
        "    fn inner() {\n"
        "        return x\n"
        "    }\n"
        "    return inner()\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is None, "closure capture refuses inline")


def test_analyze_scope_excludes_string_literal_match() -> None:
    """`use("x")` — the `x` inside the string is NOT a use site."""
    src = (
        "fn run() {\n"
        "    let x = 1\n"
        "    println(\"x is unused here\")\n"
        "    return x\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    assert_(info is not None, "scope analysed")
    # Only 1 use (the `return x`); the `x` inside the string is ignored.
    assert_eq(len(info.uses), 1, "string literal contents excluded")
    assert_eq(info.uses[0].line, 3, "use is the return line")


# ---------------------------------------------------------------------------
# detect_side_effects.
# ---------------------------------------------------------------------------


def test_detect_side_effects_function_call() -> None:
    assert_(detect_side_effects("compute()"), "compute() is side-effecting")
    assert_(detect_side_effects("foo(1, 2)"), "foo(1, 2) is side-effecting")
    assert_(
        detect_side_effects("compute() + 1"),
        "compute() + 1 is side-effecting",
    )


def test_detect_side_effects_pure_expression() -> None:
    assert_(not detect_side_effects("a + b"), "a + b is pure")
    assert_(not detect_side_effects("42"), "literal is pure")
    assert_(not detect_side_effects("x"), "identifier is pure")
    assert_(not detect_side_effects(""), "empty rhs is pure")


def test_detect_side_effects_parenthesised_pure() -> None:
    """`(a + b) * c` — the `(` is preceded by whitespace, not an
    identifier, so it's a grouping paren not a call."""
    assert_(
        not detect_side_effects("(a + b) * c"),
        "grouped sub-expression is pure",
    )


def test_detect_side_effects_keyword_paren_ignored() -> None:
    """`if (x > 0)` shouldn't trip the heuristic — `if(` is control
    flow, not a call. (NOVA usually writes `if x > 0` but defensive
    is cheap.)"""
    assert_(
        not detect_side_effects("if(x > 0) { 1 } else { 0 }"),
        "if(...) is not a call",
    )


# ---------------------------------------------------------------------------
# build_inline_edit — output shape.
# ---------------------------------------------------------------------------


def test_build_edit_single_use_replaced() -> None:
    """`let x = 42; println(x)` -> `println(42)` (and let removed)."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    println(x)\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    edit = build_inline_edit(info, src)
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "println((42))" in new_text,
        f"use replaced with wrapped RHS: {new_text!r}",
    )
    assert_(
        "let x" not in new_text,
        f"let line removed: {new_text!r}",
    )


def test_build_edit_three_uses_all_replaced() -> None:
    """Three uses -> three replacements, let removed."""
    src = (
        "fn run(a, b, c) {\n"
        "    let x = a\n"
        "    return b + x + c + x + x\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    edit = build_inline_edit(info, src)
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "let x" not in new_text,
        "let removed",
    )
    # Count `(a)` substitutions on the return line.
    return_line = next(
        l for l in new_text.splitlines() if "return" in l
    )
    count = return_line.count("(a)")
    assert_eq(count, 3, f"three (a) substitutions in: {return_line!r}")


def test_build_edit_compound_rhs_parens() -> None:
    """`let y = 1 + 2; use(y)` -> `use((1 + 2))` — parens preserve
    precedence."""
    src = (
        "fn run() {\n"
        "    let y = 1 + 2\n"
        "    return y * 3\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    edit = build_inline_edit(info, src)
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "(1 + 2) * 3" in new_text,
        f"parenthesised rhs preserves precedence: {new_text!r}",
    )


def test_build_edit_workspace_edit_shape() -> None:
    """``build_inline_edit`` returns the standard ``{"changes":
    {uri: [TextEdit]}}`` shape with multiple ranged edits."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    return x + x\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    edit = build_inline_edit(info, src)
    assert_("changes" in edit, "WorkspaceEdit has changes key")
    assert_(_uri("/t.nova") in edit["changes"], "uri keyed")
    edits = edit["changes"][_uri("/t.nova")]
    # 2 use replacements + 1 let-line removal.
    assert_eq(len(edits), 3, f"3 TextEdits (2 uses + 1 removal): {edits!r}")


def test_build_edit_chained_let_inlining() -> None:
    """`let x = 1 + 2; let y = x * 3` — x inlined: `let y = (1 + 2) * 3`."""
    src = (
        "fn run() {\n"
        "    let x = 1 + 2\n"
        "    let y = x * 3\n"
        "    return y\n"
        "}\n"
    )
    binding = find_let_at(_uri("/t.nova"), {"line": 1, "character": 4}, src)
    info = analyze_scope(binding, src)
    edit = build_inline_edit(info, src)
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "let y = (1 + 2) * 3" in new_text,
        f"chained let inlined: {new_text!r}",
    )


# ---------------------------------------------------------------------------
# build_inline_action — composed end-to-end.
# ---------------------------------------------------------------------------


def test_action_returns_none_on_non_let() -> None:
    """Cursor not on a let line -> None."""
    src = (
        "fn run(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    rng = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 16},
    }
    action = build_inline_action(_uri("/t.nova"), src, rng)
    assert_(action is None, "non-let -> no action")


def test_action_returns_none_on_reassignment() -> None:
    """`let x = 1; x = 2; use(x)` -> None."""
    src = (
        "fn run() {\n"
        "    let x = 1\n"
        "    x = 2\n"
        "    return x\n"
        "}\n"
    )
    rng = {
        "start": {"line": 1, "character": 4},
        "end": {"line": 1, "character": 4},
    }
    action = build_inline_action(_uri("/t.nova"), src, rng)
    assert_(action is None, "reassignment -> no action")


def test_action_title_includes_var_name() -> None:
    """The action's title surfaces the variable name."""
    src = (
        "fn run() {\n"
        "    let total = 1 + 2\n"
        "    return total * 3\n"
        "}\n"
    )
    rng = {
        "start": {"line": 1, "character": 4},
        "end": {"line": 1, "character": 4},
    }
    action = build_inline_action(_uri("/t.nova"), src, rng)
    assert_(action is not None, "action returned")
    assert_(
        "`total`" in action["title"],
        f"title contains var name: {action['title']!r}",
    )
    assert_eq(action["kind"], KIND_REFACTOR_INLINE, "refactor.inline kind")


def test_action_side_effect_warning_multi_use() -> None:
    """RHS is a call AND there are multiple uses -> warning suffix."""
    src = (
        "fn run() {\n"
        "    let x = compute()\n"
        "    use(x)\n"
        "    use(x)\n"
        "}\n"
    )
    rng = {
        "start": {"line": 1, "character": 4},
        "end": {"line": 1, "character": 4},
    }
    action = build_inline_action(_uri("/t.nova"), src, rng)
    assert_(action is not None, "action returned")
    assert_(
        "warning" in action["title"],
        f"title has warning: {action['title']!r}",
    )


def test_action_no_warning_when_pure() -> None:
    """Pure RHS -> no warning suffix."""
    src = (
        "fn run() {\n"
        "    let x = 42\n"
        "    return x + x\n"
        "}\n"
    )
    rng = {
        "start": {"line": 1, "character": 4},
        "end": {"line": 1, "character": 4},
    }
    action = build_inline_action(_uri("/t.nova"), src, rng)
    assert_(action is not None, "action returned")
    assert_(
        "warning" not in action["title"],
        f"title has no warning: {action['title']!r}",
    )


# ---------------------------------------------------------------------------
# Server-level integration through `dispatch`.
# ---------------------------------------------------------------------------


_INTEGRATION_FIXTURE = """\
fn caller(a, b) {
    let total = a + b
    println(total)
    return total * 2
}
"""


def test_integration_inline_via_dispatch() -> None:
    """Open a doc, put the cursor on a `let`, request codeAction,
    apply the returned edit, verify the result inlines correctly."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_INTEGRATION_FIXTURE)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, _INTEGRATION_FIXTURE)

        # Cursor on the `let total = a + b` line.
        sel = {
            "start": {"line": 1, "character": 8},
            "end": {"line": 1, "character": 8},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            KIND_REFACTOR_INLINE in kinds,
            f"refactor.inline present in {kinds}",
        )
        inline = next(
            a for a in actions if a.get("kind") == KIND_REFACTOR_INLINE
        )
        assert_(
            "`total`" in inline["title"],
            f"title surfaces var name: {inline['title']!r}",
        )
        new_text = _apply_edit(_INTEGRATION_FIXTURE, uri, inline["edit"])
        # The let line should be gone.
        assert_(
            "let total" not in new_text,
            f"let removed: {new_text!r}",
        )
        # Both uses replaced with `(a + b)`.
        assert_(
            "println((a + b))" in new_text,
            f"println use replaced: {new_text!r}",
        )
        assert_(
            "(a + b) * 2" in new_text,
            f"return use replaced with precedence preserved: {new_text!r}",
        )


def test_integration_capability_registered() -> None:
    """The server advertises `refactor.inline` in its codeActionKinds
    on initialize."""
    with tempfile.TemporaryDirectory() as workspace:
        client = LspClient()
        init = client.initialize(workspace)
        caps = init["result"]["capabilities"]
        kinds = caps["codeActionProvider"]["codeActionKinds"]
        assert_(
            KIND_REFACTOR_INLINE in kinds,
            f"refactor.inline advertised: {kinds}",
        )


def test_integration_no_action_on_non_let_line() -> None:
    """Cursor on a non-let line -> the inline action is absent
    (but other refactor actions remain available when their
    preconditions are met)."""
    fixture = (
        "fn run(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixture)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, fixture)

        # Cursor on the return line.
        sel = {
            "start": {"line": 1, "character": 4},
            "end": {"line": 1, "character": 4},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            KIND_REFACTOR_INLINE not in kinds,
            f"inline absent for non-let: {kinds}",
        )


def test_integration_refuses_reassigned_let() -> None:
    """End-to-end refusal: cursor on a let whose name is reassigned
    later -> no inline action (but other actions when their
    preconditions are met)."""
    fixture = (
        "fn run() {\n"
        "    let x = 1\n"
        "    x = 2\n"
        "    return x\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixture)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, fixture)

        sel = {
            "start": {"line": 1, "character": 4},
            "end": {"line": 1, "character": 4},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            KIND_REFACTOR_INLINE not in kinds,
            f"reassigned let refuses inline: {kinds}",
        )


def test_integration_only_filter_respected() -> None:
    """When the client passes `context.only: [refactor.inline]`,
    other refactor actions are suppressed."""
    fixture = (
        "fn run() {\n"
        "    let x = 42\n"
        "    return x\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixture)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, fixture)

        sel = {
            "start": {"line": 1, "character": 4},
            "end": {"line": 1, "character": 4},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {
                    "only": [KIND_REFACTOR_INLINE],
                    "diagnostics": [],
                },
            },
        )
        actions = resp["result"]
        for a in actions:
            assert_(
                a.get("kind", "").startswith("refactor.inline"),
                f"only inline kinds when filtered: {a.get('kind')!r}",
            )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # find_let_at
        test_find_let_at_simple_let,
        test_find_let_at_with_type_annotation,
        test_find_let_at_complex_rhs,
        test_find_let_at_non_let_line,
        test_find_let_at_out_of_bounds,
        test_find_let_at_empty_rhs_rejected,
        test_find_let_at_trailing_comment,
        # analyze_scope
        test_analyze_scope_single_use,
        test_analyze_scope_multiple_uses,
        test_analyze_scope_zero_uses,
        test_analyze_scope_refuses_reassignment,
        test_analyze_scope_refuses_top_level,
        test_analyze_scope_local_to_fn_a,
        test_analyze_scope_refuses_closure_capture,
        test_analyze_scope_excludes_string_literal_match,
        # detect_side_effects
        test_detect_side_effects_function_call,
        test_detect_side_effects_pure_expression,
        test_detect_side_effects_parenthesised_pure,
        test_detect_side_effects_keyword_paren_ignored,
        # build_inline_edit
        test_build_edit_single_use_replaced,
        test_build_edit_three_uses_all_replaced,
        test_build_edit_compound_rhs_parens,
        test_build_edit_workspace_edit_shape,
        test_build_edit_chained_let_inlining,
        # build_inline_action composed
        test_action_returns_none_on_non_let,
        test_action_returns_none_on_reassignment,
        test_action_title_includes_var_name,
        test_action_side_effect_warning_multi_use,
        test_action_no_warning_when_pure,
        # Integration via dispatch
        test_integration_inline_via_dispatch,
        test_integration_capability_registered,
        test_integration_no_action_on_non_let_line,
        test_integration_refuses_reassigned_let,
        test_integration_only_filter_respected,
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
        print(f"test_inline_variable: FAIL — {failed} test(s) failed", file=sys.stderr)
        return 1
    print(f"test_inline_variable: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
