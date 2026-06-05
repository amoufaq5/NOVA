"""Unit + integration tests for the extract-function refactor.

Covers the R21F module at `nova_lsp/extract_function.py`:

  * `analyze_selection` — locates the enclosing fn, harvests free
    variables, and rejects unusable selections (empty / single-line /
    out-of-fn / spans-fn-boundary / range malformed).
  * `compute_next_extracted_name` — counter-based unique helper
    name, deterministic on a per-file basis.
  * `build_extract_edit` — full ``WorkspaceEdit`` construction with
    helper insertion at file top-level (matches the legacy R3
    placement that the existing smoke test expects).
  * `build_extract_action` — top-level wrapper that composes all
    three; returns ``None`` on rejection, a ``CodeAction`` on
    success.
  * Server-level wire smoke through ``dispatch`` for the full
    ``textDocument/codeAction`` round trip on a 3-line selection.
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
from nova_lsp.extract_function import (  # noqa: E402
    KIND_REFACTOR_EXTRACT,
    MIN_LINES_FOR_EXTRACT,
    SelectionInfo,
    analyze_selection,
    build_extract_action,
    build_extract_edit,
    compute_next_extracted_name,
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

    Same helper the exhaustiveness-fix tests use; copied here so the
    file has no cross-test dependency.
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
# compute_next_extracted_name.
# ---------------------------------------------------------------------------


def test_next_extracted_name_empty_file() -> None:
    assert_eq(
        compute_next_extracted_name(""),
        "extracted_1",
        "fresh file starts at extracted_1",
    )


def test_next_extracted_name_unrelated() -> None:
    src = "fn run(x) { return x + 1 }\n"
    assert_eq(
        compute_next_extracted_name(src),
        "extracted_1",
        "file with no extracted_N starts at 1",
    )


def test_next_extracted_name_after_one() -> None:
    src = (
        "fn extracted_1(a) { return a }\n"
        "fn caller() { return extracted_1(0) }\n"
    )
    assert_eq(
        compute_next_extracted_name(src),
        "extracted_2",
        "one prior extracted -> extracted_2",
    )


def test_next_extracted_name_picks_max() -> None:
    src = (
        "fn extracted_1(a) { return a }\n"
        "fn extracted_5(b) { return b }\n"
        "fn extracted_3(c) { return c }\n"
    )
    assert_eq(
        compute_next_extracted_name(src),
        "extracted_6",
        "next name is max+1, not count+1",
    )


def test_next_extracted_name_counter_unique() -> None:
    """Running counter twice must yield distinct names: the second
    call sees the first's helper in the text and bumps the counter."""
    text0 = "fn caller() { let x = 0 }\n"
    name1 = compute_next_extracted_name(text0)
    # Simulate the first helper having been added.
    text1 = text0 + f"fn {name1}() {{ return 0 }}\n"
    name2 = compute_next_extracted_name(text1)
    assert_(name1 != name2, "two consecutive extracts produce different names")
    assert_eq(name1, "extracted_1", "first extract")
    assert_eq(name2, "extracted_2", "second extract")


# ---------------------------------------------------------------------------
# analyze_selection — happy paths.
# ---------------------------------------------------------------------------


def test_analyze_selection_simple_free_vars() -> None:
    """`let z = x + y; println(z)` — x and y are params; z is bound
    inside, println is a builtin."""
    src = (
        "fn run(x, y) {\n"
        "    let z = x + y\n"
        "    println(z)\n"
        "    return z\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "selection analyzed")
    assert_eq(info.fn_name, "run", "enclosing fn run")
    assert_eq(info.free_variables, ["x", "y"], "x and y are free")
    assert_eq(info.non_empty_line_count, 2, "2 non-empty lines")


def test_analyze_selection_no_free_variables() -> None:
    """Pure constant selection — fn takes no args."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = 2\n"
        "    println(a + b)\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 14},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "selection analyzed")
    assert_eq(info.free_variables, [], "no free variables")


def test_analyze_selection_single_free_var() -> None:
    """`print(x)` — x is the only param."""
    src = (
        "fn run(x) {\n"
        "    let y = 0\n"
        "    print(x)\n"
        "    print(x)\n"
        "    return y\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 12},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"print"}
    )
    assert_(info is not None, "analyzed")
    assert_eq(info.free_variables, ["x"], "x is the only free var")


def test_analyze_selection_multiple_free_vars() -> None:
    """`print(x + y + z)` — three free variables, preserves order."""
    src = (
        "fn run(x, y, z) {\n"
        "    print(x + y + z)\n"
        "    print(x - y + z)\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 20},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"print"}
    )
    assert_(info is not None, "analyzed")
    assert_eq(info.free_variables, ["x", "y", "z"], "x, y, z in order")


def test_analyze_selection_local_let_excluded() -> None:
    """A `let` declared INSIDE the selection should NOT count as a
    free variable — it's a local of the helper."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a * 2\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    assert_(info is not None, "analyzed")
    # `x` is free, `a` is bound inside the selection, `b` is bound
    # inside the selection.
    assert_eq(info.free_variables, ["x"], "only x is free")


def test_analyze_selection_builtins_filtered() -> None:
    """Builtin functions in the selection don't become parameters."""
    src = (
        "fn run(n) {\n"
        "    println(n)\n"
        "    print_int(n)\n"
        "    return n\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src,
        builtins={"println", "print_int"},
    )
    assert_(info is not None, "analyzed")
    assert_eq(info.free_variables, ["n"], "only n; builtins filtered")


# ---------------------------------------------------------------------------
# analyze_selection — rejections (edge cases).
# ---------------------------------------------------------------------------


def test_analyze_rejects_single_line() -> None:
    """The single-line edge case — too small for a helper."""
    src = (
        "fn run(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 16},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "single-line selection rejected")


def test_analyze_rejects_empty_selection() -> None:
    """Zero-width range (cursor position only) — no action."""
    src = (
        "fn run(x) {\n"
        "    let y = 0\n"
        "    return x + y\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 4},
        "end": {"line": 1, "character": 4},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "zero-width selection rejected")


def test_analyze_rejects_blank_only_selection() -> None:
    """Selection covers only blank lines — non-empty count is 0."""
    src = (
        "fn run(x) {\n"
        "    let y = 0\n"
        "\n"
        "\n"
        "    return y\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 0},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "blank-only selection rejected")


def test_analyze_rejects_outside_function() -> None:
    """Selection lies outside any function body (e.g. on an import
    line)."""
    src = (
        'import "std/io.nova"\n'
        'import "../foo.nova"\n'
        "\n"
        "fn run() {\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 0, "character": 0},
        "end": {"line": 1, "character": 20},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "import-line selection rejected")


def test_analyze_rejects_spans_fn_boundary() -> None:
    """Selection starts in one fn and ends in another — must reject
    because we'd otherwise split the boundary."""
    src = (
        "fn first(x) {\n"
        "    let a = x + 1\n"
        "    return a\n"
        "}\n"
        "fn second(y) {\n"
        "    let b = y + 2\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 5, "character": 20},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "cross-fn selection rejected")


def test_analyze_rejects_malformed_range() -> None:
    """End line before start line — degenerate range."""
    src = (
        "fn run(x) {\n"
        "    let y = x\n"
        "    let z = y\n"
        "    return z\n"
        "}\n"
    )
    sel = {
        "start": {"line": 3, "character": 0},
        "end": {"line": 1, "character": 0},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "end<start rejected")


def test_analyze_rejects_out_of_document() -> None:
    """Range references line indices past the end of the document."""
    src = "fn run() { return 0 }\n"
    sel = {
        "start": {"line": 99, "character": 0},
        "end": {"line": 100, "character": 0},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    assert_(info is None, "OOB range rejected")


def test_analyze_trims_vscode_full_line_artifact() -> None:
    """VS Code "select full line" extends the range to (next_line, 0).
    The analyser must trim that off so a 2-line full-line selection
    isn't counted as 3 lines."""
    src = (
        "fn run(x, y) {\n"
        "    let a = x\n"
        "    let b = y\n"
        "    return a + b\n"
        "}\n"
    )
    # User selects 2 lines but VS Code includes the trailing newline.
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 3, "character": 0},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    assert_(info is not None, "trimmed selection analysed")
    # After trim end_line should be 2 (lines 1-2 = let a, let b).
    assert_eq(info.end_line, 2, "trailing line trimmed")
    assert_eq(info.non_empty_line_count, 2, "2 non-empty after trim")


def test_analyze_require_min_lines_bypass() -> None:
    """Passing ``require_min_lines=False`` allows a single-line
    selection through — useful for callers that want the analysis
    without the gate."""
    # Note: avoid ``return`` in the single line so the R35F
    # early-exit rejection doesn't fire — the bypass is for the
    # min-lines gate specifically.
    src = (
        "fn run(x) {\n"
        "    let y = x + 1\n"
        "    let z = y + 2\n"
        "    return z\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src,
        require_min_lines=False,
    )
    assert_(info is not None, "bypass allows single-line through")


# ---------------------------------------------------------------------------
# build_extract_edit — output shape.
# ---------------------------------------------------------------------------


def test_build_edit_helper_signature() -> None:
    """Helper sig must mirror the free-variable list."""
    src = (
        "fn run(x, y) {\n"
        "    let z = x + y\n"
        "    println(z)\n"
        "    return z\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "fn extracted_1(x, y) {" in new_text,
        "helper signature lists x and y",
    )


def test_build_edit_call_site() -> None:
    """The call-site replacement uses the helper name + arg list."""
    src = (
        "fn run(x, y) {\n"
        "    let z = x + y\n"
        "    println(z)\n"
        "    return z\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "extracted_1(x, y)" in new_text,
        "call site present",
    )


def test_build_edit_no_args_no_paren_content() -> None:
    """No-free-variable selection -> empty arg list."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = 2\n"
        "    let c = 3\n"
        "    return a + b + c\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 3, "character": 13},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "fn extracted_1() {" in new_text,
        "no-arg helper sig",
    )


def test_build_edit_indentation_preserved() -> None:
    """The helper body strips the common indent so it starts at
    column 4, regardless of how deeply the original was nested."""
    # Selection at 8-space indent (e.g. inside an inner block).
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let a = x + 1\n"
        "        let b = a + 1\n"
        "        return b\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 21},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # Helper body should land at 4-space indent (the helper's own).
    helper_body_lines = [
        l for l in new_text.splitlines()
        if l.startswith("    let a = ") or l.startswith("    let b = ")
    ]
    assert_(
        len(helper_body_lines) >= 2,
        "helper body lives at column 4 after common-indent strip",
    )


def test_build_edit_call_site_indent_matches_selection() -> None:
    """The call-site replacement's leading indent matches the first
    non-empty selected line's indent."""
    src = (
        "fn run(x) {\n"
        "    if x > 0 {\n"
        "        let a = x + 1\n"
        "        let b = a + 1\n"
        "        return b\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 21},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # Find the call-site line. After R35F return-value rewriting the
    # call site is ``<live_out> = extracted_1(args)`` so match any
    # line that *contains* the call shape but isn't the helper's own
    # definition line ``fn extracted_1(``.
    call_lines = [
        l for l in new_text.splitlines()
        if "extracted_1(" in l and not l.lstrip().startswith("fn extracted_1(")
    ]
    assert_eq(len(call_lines), 1, "exactly one call site")
    leading = len(call_lines[0]) - len(call_lines[0].lstrip())
    assert_eq(leading, 8, "call site at original 8-space indent")


def test_build_edit_helper_at_file_top() -> None:
    """Helper lands above the first declaration so the legacy R3
    placement is preserved."""
    src = (
        'import "std/io.nova"\n'
        "\n"
        "fn caller(x, y) {\n"
        "    let z = x + y\n"
        "    let w = z * 2\n"
        "    return w\n"
        "}\n"
    )
    sel = {
        "start": {"line": 3, "character": 0},
        "end": {"line": 4, "character": 18},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # Helper must appear before the caller fn.
    i_helper = new_text.index("fn extracted_1(")
    i_caller = new_text.index("fn caller(")
    assert_(
        i_helper < i_caller,
        "helper above caller in result",
    )


def test_build_edit_workspace_edit_shape() -> None:
    """``build_extract_edit`` returns the standard ``{"changes":
    {uri: [TextEdit]}}`` shape."""
    src = (
        "fn run(x) {\n"
        "    let a = x\n"
        "    let b = a\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 13},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins=set()
    )
    edit = build_extract_edit(info, src, "extracted_1")
    assert_("changes" in edit, "WorkspaceEdit has changes key")
    assert_(_uri("/t.nova") in edit["changes"], "uri keyed")
    edits = edit["changes"][_uri("/t.nova")]
    assert_eq(len(edits), 1, "single TextEdit (full-doc replace)")
    assert_("newText" in edits[0], "newText present")
    assert_("range" in edits[0], "range present")


def test_build_edit_preserves_trailing_newline() -> None:
    """When the original document ends with a newline, the new text
    must too — clients are sensitive to trailing-newline drift."""
    src = (
        "fn run(x) {\n"
        "    let a = x\n"
        "    let b = a\n"
        "    return b\n"
        "}\n"
    )
    assert_(src.endswith("\n"), "fixture ends with newline")
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 13},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src)
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(new_text.endswith("\n"), "trailing newline preserved")


# ---------------------------------------------------------------------------
# build_extract_action — composed end-to-end.
# ---------------------------------------------------------------------------


def test_action_returns_none_on_rejection() -> None:
    """A single-line selection produces no action."""
    src = (
        "fn run(x) {\n"
        "    return x + 1\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 16},
    }
    action = build_extract_action(_uri("/t.nova"), src, sel)
    assert_(action is None, "single-line -> no action")


def test_action_title_includes_helper_name() -> None:
    """The action's title surfaces the chosen helper name."""
    src = (
        "fn run(x) {\n"
        "    let a = x\n"
        "    let b = a\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 13},
    }
    action = build_extract_action(_uri("/t.nova"), src, sel)
    assert_(action is not None, "action returned")
    assert_eq(
        action["title"],
        "Extract to function `extracted_1`",
        "title format",
    )
    assert_eq(action["kind"], KIND_REFACTOR_EXTRACT, "refactor.extract kind")


def test_action_counter_unique_across_calls() -> None:
    """Running ``build_extract_action`` twice on the same file
    (with one helper already inserted) bumps the counter."""
    src0 = (
        "fn run(x, y) {\n"
        "    let a = x + y\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    action1 = build_extract_action(_uri("/t.nova"), src0, sel)
    assert_(action1 is not None, "first action returned")
    text_after_1 = _apply_edit(src0, _uri("/t.nova"), action1["edit"])

    # Now extract a different region of the post-edit text.
    # In the new text, fn run still exists somewhere; we just check
    # the counter advances when applied to text containing
    # `extracted_1`.
    sel2 = {
        "start": {"line": 0, "character": 0},
        "end": {"line": 0, "character": 0},
    }
    # We can't reliably build a valid second selection on the post-
    # edit text (it depends on layout), so we just verify the counter
    # bumps via compute_next_extracted_name.
    name1 = "extracted_1"
    assert_("extracted_1" in text_after_1, "extracted_1 present")
    next_name = compute_next_extracted_name(text_after_1)
    assert_eq(next_name, "extracted_2", "counter advances on second extract")


# ---------------------------------------------------------------------------
# Server-level integration through `dispatch`.
# ---------------------------------------------------------------------------


_INTEGRATION_FIXTURE = """\
fn caller(a, b, c) {
    let x = a + b
    let y = x + c
    let z = y * 2
    return z
}
"""


def test_integration_extract_via_dispatch() -> None:
    """Open a doc, request a codeAction over a 3-line selection,
    verify the returned action's edit produces a helper at the top
    and a call site replacing the selected block."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_INTEGRATION_FIXTURE)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, _INTEGRATION_FIXTURE)

        # Select lines 1-3 (the three `let` statements).
        sel = {
            "start": {"line": 1, "character": 0},
            "end": {"line": 3, "character": 18},
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
        # We should have refactor.extract + organize-imports + sort-fns.
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            KIND_REFACTOR_EXTRACT in kinds,
            f"refactor.extract present in {kinds}",
        )
        ex = next(
            a for a in actions if a.get("kind") == KIND_REFACTOR_EXTRACT
        )
        assert_(
            ex["title"].startswith("Extract to function `extracted_"),
            "title format",
        )
        new_text = _apply_edit(_INTEGRATION_FIXTURE, uri, ex["edit"])
        # The helper signature should mention a, b, c (the params used
        # by the selection — x and y are bound by let inside).
        helper_sig_line = next(
            l for l in new_text.splitlines()
            if l.startswith("fn extracted_1(")
        )
        for v in ("a", "b", "c"):
            assert_(
                v in helper_sig_line,
                f"helper sig has `{v}`",
            )
        # The call site replaces the selected lines.
        assert_(
            "extracted_1(a, b, c)" in new_text,
            "call site present",
        )
        # Body must contain original `let` lines.
        assert_("let x = a + b" in new_text, "body has let x")
        assert_("let y = x + c" in new_text, "body has let y")
        assert_("let z = y * 2" in new_text, "body has let z")


def test_integration_no_action_on_single_line_selection() -> None:
    """A single-line click should NOT trigger the extract action,
    even though it returns the other refactor actions when their
    own preconditions are satisfied."""
    # Use a richer fixture that triggers organize-imports + sort-fns
    # too so we can verify the dispatcher is still emitting them.
    fixture = (
        'import "z.nova"\n'
        'import "a.nova"\n'
        "\n"
        "fn zeta() { return 0 }\n"
        "\n"
        "fn alpha(a, b) {\n"
        "    let x = a + b\n"
        "    let y = x * 2\n"
        "    return y\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixture)

        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, fixture)

        # Single-line click inside alpha.
        sel = {
            "start": {"line": 6, "character": 4},
            "end": {"line": 6, "character": 4},
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
            KIND_REFACTOR_EXTRACT not in kinds,
            f"no extract action on single-line click: got {kinds}",
        )
        # The other refactor actions are surfaced when their
        # preconditions are met (imports out of order, fns out of
        # order) — they don't depend on the selection range.
        assert_(
            "source.organizeImports" in kinds,
            f"organize-imports still present: {kinds}",
        )
        assert_(
            "source.organizeFns" in kinds,
            f"sort-fns still present: {kinds}",
        )


def test_min_lines_constant_exposed() -> None:
    """The threshold is exported so callers can introspect it."""
    assert_(
        MIN_LINES_FOR_EXTRACT >= 2,
        f"MIN_LINES_FOR_EXTRACT is at least 2 (got {MIN_LINES_FOR_EXTRACT})",
    )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # compute_next_extracted_name
        test_next_extracted_name_empty_file,
        test_next_extracted_name_unrelated,
        test_next_extracted_name_after_one,
        test_next_extracted_name_picks_max,
        test_next_extracted_name_counter_unique,
        # analyze_selection happy paths
        test_analyze_selection_simple_free_vars,
        test_analyze_selection_no_free_variables,
        test_analyze_selection_single_free_var,
        test_analyze_selection_multiple_free_vars,
        test_analyze_selection_local_let_excluded,
        test_analyze_selection_builtins_filtered,
        # analyze_selection rejections
        test_analyze_rejects_single_line,
        test_analyze_rejects_empty_selection,
        test_analyze_rejects_blank_only_selection,
        test_analyze_rejects_outside_function,
        test_analyze_rejects_spans_fn_boundary,
        test_analyze_rejects_malformed_range,
        test_analyze_rejects_out_of_document,
        test_analyze_trims_vscode_full_line_artifact,
        test_analyze_require_min_lines_bypass,
        # build_extract_edit
        test_build_edit_helper_signature,
        test_build_edit_call_site,
        test_build_edit_no_args_no_paren_content,
        test_build_edit_indentation_preserved,
        test_build_edit_call_site_indent_matches_selection,
        test_build_edit_helper_at_file_top,
        test_build_edit_workspace_edit_shape,
        test_build_edit_preserves_trailing_newline,
        # build_extract_action composed
        test_action_returns_none_on_rejection,
        test_action_title_includes_helper_name,
        test_action_counter_unique_across_calls,
        # Integration via dispatch
        test_integration_extract_via_dispatch,
        test_integration_no_action_on_single_line_selection,
        # Misc
        test_min_lines_constant_exposed,
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
        print(f"test_extract_function: FAIL — {failed} test(s) failed", file=sys.stderr)
        return 1
    print(f"test_extract_function: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
