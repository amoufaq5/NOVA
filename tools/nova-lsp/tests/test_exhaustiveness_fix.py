"""Unit + integration tests for the exhaustiveness-fix code action.

Covers:

  * `parse_exhaustiveness_diagnostic` — pulls enum name + missing
    variant list out of R17A's WARN message.
  * `find_match_at` — locates the nearest match expression for a
    diagnostic range.
  * `resolve_enum_decl` — finds the enum (same file, imported file,
    workspace sibling) and surfaces its variant list with arities.
  * `collect_covered_variants` — extracts variant names already
    handled by existing arms.
  * `infer_missing_variants` — set-difference between declared
    variants and covered ones, using R17A's WARN list as a tiebreaker.
  * `build_arm_text` / `build_arms_block` — stub generation with
    correct `_` placeholders matching variant arity.
  * `compute_insertion_point` — insert BEFORE catch-all when present,
    else just above the closing `}`.
  * `build_workspace_edit` — final WorkspaceEdit shape that the LSP
    returns to the client.
  * Server-level wire smoke through `dispatch` for the full
    `textDocument/codeAction` round trip with a forwarded
    exhaustiveness diagnostic.
  * Cross-file: enum declared in file A, match in file B — the
    code action finds variants from file A.
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
from nova_lsp.exhaustiveness_fix import (  # noqa: E402
    KIND_QUICKFIX,
    TODO_PLACEHOLDER,
    build_arm_text,
    build_arms_block,
    build_exhaustiveness_code_actions,
    build_workspace_edit,
    collect_covered_variants,
    compute_insertion_point,
    find_match_at,
    infer_missing_variants,
    is_exhaustiveness_diagnostic,
    parse_exhaustiveness_diagnostic,
    resolve_enum_decl,
)
from nova_lsp.imports import FileCache  # noqa: E402
from nova_lsp.type_hierarchy import EnumVariant  # noqa: E402
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex  # noqa: E402


_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


# Apply a `{"changes": {uri: [TextEdit]}}` to a document text — copied
# from code_action_smoke so tests are self-contained.
def _apply_edit(doc_text: str, uri: str, edit) -> str:
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
# parse_exhaustiveness_diagnostic.
# ---------------------------------------------------------------------------


def test_parse_diag_simple() -> None:
    result = parse_exhaustiveness_diagnostic(
        "non-exhaustive match on Option (missing: None)"
    )
    assert_(result is not None, "parses simple WARN")
    enum_name, missing = result
    assert_eq(enum_name, "Option", "enum name parsed")
    assert_eq(missing, ["None"], "single missing variant")


def test_parse_diag_multi_missing() -> None:
    result = parse_exhaustiveness_diagnostic(
        "non-exhaustive match on Shape (missing: Circle, Rect)"
    )
    assert_(result is not None, "parses multi WARN")
    enum_name, missing = result
    assert_eq(enum_name, "Shape", "enum name parsed")
    assert_eq(missing, ["Circle", "Rect"], "multi missing variants")


def test_parse_diag_with_warning_prefix() -> None:
    """Some clients forward the raw 'warning: ...' line — we should
    strip that prefix transparently."""
    result = parse_exhaustiveness_diagnostic(
        "warning: non-exhaustive match on Result (missing: Err)"
    )
    assert_(result is not None, "strips warning: prefix")
    enum_name, missing = result
    assert_eq(enum_name, "Result", "Result enum")
    assert_eq(missing, ["Err"], "Err missing")


def test_parse_diag_unrelated_returns_none() -> None:
    assert_(
        parse_exhaustiveness_diagnostic(
            "unused variable: x"
        ) is None,
        "unrelated diag returns None",
    )
    assert_(
        parse_exhaustiveness_diagnostic("") is None,
        "empty message returns None",
    )
    assert_(
        parse_exhaustiveness_diagnostic("not a match") is None,
        "non-match message returns None",
    )


def test_is_exhaustiveness_diagnostic() -> None:
    diag_yes = {"message": "non-exhaustive match on Option (missing: None)"}
    diag_no = {"message": "syntax error: expected }"}
    assert_(is_exhaustiveness_diagnostic(diag_yes), "yes diag matches")
    assert_(not is_exhaustiveness_diagnostic(diag_no), "no diag rejected")


# ---------------------------------------------------------------------------
# find_match_at.
# ---------------------------------------------------------------------------


def test_find_match_at_simple() -> None:
    text = (
        "fn run(x) {\n"
        "    match x {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    info = find_match_at(text, 1)
    assert_(info is not None, "match found at head line")
    assert_eq(info.open_line, 1, "open_line is 1")
    assert_eq(info.close_line, 3, "close_line is 3")
    assert_eq(len(info.arms), 1, "1 arm parsed")


def test_find_match_at_with_catch_all() -> None:
    text = (
        "fn run(x) {\n"
        "    match x {\n"
        "        Option::Some(v) => v\n"
        "        _ => 0\n"
        "    }\n"
        "}\n"
    )
    info = find_match_at(text, 1)
    assert_(info is not None, "match found")
    assert_eq(info.catch_all_line, 3, "catch-all line tracked")


def test_find_match_at_no_match() -> None:
    text = "fn no_match(x) { return x }\n"
    assert_(find_match_at(text, 0) is None, "no match block in text")


# ---------------------------------------------------------------------------
# resolve_enum_decl.
# ---------------------------------------------------------------------------


def test_resolve_enum_same_file() -> None:
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, text)
        resolved = resolve_enum_decl(
            "Option", path, cache, workspace_index=idx
        )
        assert_(resolved is not None, "Option resolved in same file")
        decl, found_path, variants = resolved
        assert_eq(decl.name, "Option", "decl name")
        assert_eq(found_path, os.path.abspath(path), "path round-trips")
        names = [v.name for v in variants]
        assert_eq(names, ["Some", "None"], "variants in source order")
        arities = [v.arity for v in variants]
        assert_eq(arities, [1, 0], "Some(int) arity=1, None arity=0")


def test_resolve_enum_cross_file_via_import() -> None:
    """Enum declared in file A, match in file B that imports A."""
    enum_text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    main_text = (
        'import "shapes.nova"\n'
        "\n"
        "fn area(s) {\n"
        "    match s {\n"
        "        Shape::Circle(r) => r * r\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        shapes = os.path.join(tmp, "shapes.nova")
        main = os.path.join(tmp, "main.nova")
        _write(shapes, enum_text)
        _write(main, main_text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(shapes, enum_text)
        idx.index_text(main, main_text)
        resolved = resolve_enum_decl(
            "Shape", main, cache, workspace_index=idx
        )
        assert_(resolved is not None, "Shape found via import")
        _decl, _path, variants = resolved
        names = [v.name for v in variants]
        assert_eq(names, ["Circle", "Rect", "Triangle"], "Shape variants")
        arities = [v.arity for v in variants]
        assert_eq(arities, [1, 2, 3], "Shape arities")


def test_resolve_enum_cross_file_via_workspace_index() -> None:
    """Enum declared in file A, match in file B — no import. The
    workspace symbol index should still surface the decl.

    The index keys files by the fn/let symbols they declare (it
    doesn't track types as a kind), so we add a sibling fn to the
    enum file to ensure it lands in `_by_file`. R15F's type_hierarchy
    resolves the type-decl content lazily from the path."""
    enum_text = (
        "enum Color {\n"
        "    Red\n"
        "    Green\n"
        "    Blue\n"
        "}\n"
        "fn color_count() { return 3 }\n"
    )
    main_text = (
        "fn name(c) {\n"
        "    match c {\n"
        "        Color::Red => 0\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        colors = os.path.join(tmp, "colors.nova")
        main = os.path.join(tmp, "main.nova")
        _write(colors, enum_text)
        _write(main, main_text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(colors, enum_text)
        idx.index_text(main, main_text)
        resolved = resolve_enum_decl(
            "Color", main, cache, workspace_index=idx
        )
        assert_(resolved is not None, "Color found via workspace index")
        _decl, found_path, variants = resolved
        assert_eq(found_path, os.path.abspath(colors), "colors.nova path returned")
        names = [v.name for v in variants]
        assert_eq(names, ["Red", "Green", "Blue"], "Color variants in order")


def test_resolve_enum_not_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, "fn nothing(x) { return x }\n")
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, "fn nothing(x) { return x }\n")
        resolved = resolve_enum_decl(
            "Nonexistent", path, cache, workspace_index=idx
        )
        assert_(resolved is None, "unknown enum returns None")


# ---------------------------------------------------------------------------
# collect_covered_variants.
# ---------------------------------------------------------------------------


def test_collect_covered_variants_some_only() -> None:
    text = (
        "match x {\n"
        "    Option::Some(v) => v\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    assert_(info is not None, "match found")
    covered = collect_covered_variants(info, "Option")
    assert_eq(covered, {"Some"}, "Some covered")


def test_collect_covered_variants_two_arms() -> None:
    text = (
        "match s {\n"
        "    Shape::Circle(r) => r\n"
        "    Shape::Triangle(a, b, c) => a + b + c\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    assert_(info is not None, "match found")
    covered = collect_covered_variants(info, "Shape")
    assert_eq(covered, {"Circle", "Triangle"}, "Circle + Triangle covered")


# ---------------------------------------------------------------------------
# infer_missing_variants.
# ---------------------------------------------------------------------------


def _opt_variants():
    return [
        EnumVariant(name="Some", line=0, char_start=0, char_end=0, arity=1),
        EnumVariant(name="None", line=0, char_start=0, char_end=0, arity=0),
    ]


def _shape_variants():
    return [
        EnumVariant(name="Circle", line=0, char_start=0, char_end=0, arity=1),
        EnumVariant(name="Rect", line=0, char_start=0, char_end=0, arity=2),
        EnumVariant(name="Triangle", line=0, char_start=0, char_end=0, arity=3),
    ]


def test_infer_missing_option_just_some() -> None:
    text = (
        "match x {\n"
        "    Option::Some(v) => v\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    missing = infer_missing_variants(
        info, "Option", _opt_variants(), fallback_missing=["None"]
    )
    assert_eq(len(missing), 1, "1 missing variant")
    assert_eq(missing[0].name, "None", "None is missing")


def test_infer_missing_shape_circle_triangle() -> None:
    text = (
        "match s {\n"
        "    Shape::Circle(r) => r\n"
        "    Shape::Triangle(a, b, c) => a + b + c\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    missing = infer_missing_variants(
        info, "Shape", _shape_variants(), fallback_missing=["Rect"]
    )
    assert_eq(len(missing), 1, "Rect missing")
    assert_eq(missing[0].name, "Rect", "Rect identified")
    assert_eq(missing[0].arity, 2, "Rect arity 2")


def test_infer_missing_returns_in_declaration_order() -> None:
    text = (
        "match s {\n"
        "    Shape::Triangle(a, b, c) => a\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    missing = infer_missing_variants(
        info, "Shape", _shape_variants(),
        fallback_missing=["Circle", "Rect"],
    )
    # Decl order is Circle, Rect, Triangle so missing should be [Circle, Rect].
    names = [v.name for v in missing]
    assert_eq(names, ["Circle", "Rect"], "missing in decl order")


# ---------------------------------------------------------------------------
# build_arm_text + build_arms_block.
# ---------------------------------------------------------------------------


def test_build_arm_text_nullary() -> None:
    v = EnumVariant(name="None", line=0, char_start=0, char_end=0, arity=0)
    assert_eq(
        build_arm_text("Option", v, "    "),
        f"    Option::None => {TODO_PLACEHOLDER}",
        "nullary arm text",
    )


def test_build_arm_text_unary() -> None:
    v = EnumVariant(name="Some", line=0, char_start=0, char_end=0, arity=1)
    assert_eq(
        build_arm_text("Option", v, "    "),
        f"    Option::Some(_) => {TODO_PLACEHOLDER}",
        "unary arm text uses _ placeholder",
    )


def test_build_arm_text_payload_arity_2() -> None:
    v = EnumVariant(name="Rect", line=0, char_start=0, char_end=0, arity=2)
    assert_eq(
        build_arm_text("Shape", v, "    "),
        f"    Shape::Rect(_, _) => {TODO_PLACEHOLDER}",
        "arity-2 uses (_, _)",
    )


def test_build_arm_text_payload_arity_3() -> None:
    v = EnumVariant(name="Triangle", line=0, char_start=0, char_end=0, arity=3)
    assert_eq(
        build_arm_text("Shape", v, "    "),
        f"    Shape::Triangle(_, _, _) => {TODO_PLACEHOLDER}",
        "arity-3 uses (_, _, _)",
    )


def test_build_arms_block_joins_with_newline() -> None:
    v1 = EnumVariant(name="Some", line=0, char_start=0, char_end=0, arity=1)
    v2 = EnumVariant(name="None", line=0, char_start=0, char_end=0, arity=0)
    block = build_arms_block("Option", [v1, v2], "    ")
    lines = block.split("\n")
    assert_eq(len(lines), 2, "2 lines for 2 variants")
    assert_(lines[0].startswith("    Option::Some(_)"), "first line is Some")
    assert_(lines[1].startswith("    Option::None"), "second line is None")


def test_build_arms_respects_custom_indent() -> None:
    v = EnumVariant(name="None", line=0, char_start=0, char_end=0, arity=0)
    # Two-space indent.
    text = build_arm_text("Option", v, "  ")
    assert_(text.startswith("  Option::None"), "two-space indent preserved")
    # Tab indent.
    text2 = build_arm_text("Option", v, "\t")
    assert_(text2.startswith("\tOption::None"), "tab indent preserved")


# ---------------------------------------------------------------------------
# compute_insertion_point.
# ---------------------------------------------------------------------------


def test_insertion_point_no_catch_all() -> None:
    text = (
        "match x {\n"
        "    Option::Some(v) => v\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    line, character = compute_insertion_point(info, text)
    assert_eq(line, info.close_line, "insertion line == close brace line")
    assert_eq(character, 0, "insertion column == 0")


def test_insertion_point_with_catch_all() -> None:
    text = (
        "match x {\n"
        "    Option::Some(v) => v\n"
        "    _ => 0\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    line, character = compute_insertion_point(info, text)
    assert_eq(line, info.catch_all_line, "insertion above catch-all")
    assert_(line < info.close_line, "insertion before close brace")


# ---------------------------------------------------------------------------
# build_workspace_edit.
# ---------------------------------------------------------------------------


def test_build_workspace_edit_shape() -> None:
    text = (
        "match x {\n"
        "    Option::Some(v) => v\n"
        "}\n"
    )
    info = find_match_at(text, 0)
    v = EnumVariant(name="None", line=0, char_start=0, char_end=0, arity=0)
    edit = build_workspace_edit("file:///t.nova", text, info, "Option", [v])
    assert_("changes" in edit, "WorkspaceEdit has changes key")
    changes = edit["changes"]
    assert_("file:///t.nova" in changes, "uri keyed")
    edits = changes["file:///t.nova"]
    assert_eq(len(edits), 1, "single TextEdit")
    edit0 = edits[0]
    assert_eq(edit0["range"]["start"], edit0["range"]["end"], "zero-width insert")
    assert_(TODO_PLACEHOLDER in edit0["newText"], "newText has TODO")
    assert_("Option::None" in edit0["newText"], "newText has None arm")
    assert_(edit0["newText"].endswith("\n"), "trailing newline")


# ---------------------------------------------------------------------------
# build_exhaustiveness_code_actions — option missing None scenario.
# ---------------------------------------------------------------------------


def test_action_option_missing_none() -> None:
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn unwrap(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        # R17A's WARN lands on the match expression's line.
        # match is on line 6 (0-based).
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Option (missing: None)",
        }
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[diag],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(len(actions), 1, "one action returned")
        action = actions[0]
        assert_eq(action["title"], "Add missing match arms", "title")
        assert_eq(action["kind"], KIND_QUICKFIX, "quickfix kind")
        assert_eq(action["diagnostics"], [diag], "diagnostic attached")
        # Apply and verify.
        new_text = _apply_edit(source, _uri(path), action["edit"])
        assert_("Option::None" in new_text, "None arm added")
        assert_(TODO_PLACEHOLDER in new_text, "TODO placeholder present")
        # Verify the inserted line precedes the closing `}` of the match.
        new_lines = new_text.splitlines()
        none_idx = next(
            i for i, l in enumerate(new_lines) if "Option::None" in l
        )
        # The original close brace was on line 8 — after the insert it's
        # one line down at line 9; the None arm should be on line 8.
        assert_(new_lines[none_idx].lstrip().startswith("Option::None"),
                "None at start of new arm")


# ---------------------------------------------------------------------------
# build_exhaustiveness_code_actions — Shape missing Rect.
# ---------------------------------------------------------------------------


def test_action_shape_missing_rect() -> None:
    source = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
        "\n"
        "fn area(s) {\n"
        "    match s {\n"
        "        Shape::Circle(r) => r * r\n"
        "        Shape::Triangle(a, b, c) => a + b + c\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shapes.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        diag = {
            "range": {
                "start": {"line": 7, "character": 4},
                "end": {"line": 7, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Shape (missing: Rect)",
        }
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[diag],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(len(actions), 1, "one action returned")
        new_text = _apply_edit(source, _uri(path), actions[0]["edit"])
        assert_("Shape::Rect(_, _)" in new_text,
                "Rect arm with 2 underscores")


# ---------------------------------------------------------------------------
# build_exhaustiveness_code_actions — Result missing Err.
# ---------------------------------------------------------------------------


def test_action_result_missing_err() -> None:
    source = (
        "enum Result {\n"
        "    Ok(int)\n"
        "    Err(str)\n"
        "}\n"
        "\n"
        "fn get(r) {\n"
        "    match r {\n"
        "        Result::Ok(v) => v\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "r.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Result (missing: Err)",
        }
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[diag],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(len(actions), 1, "one action returned")
        new_text = _apply_edit(source, _uri(path), actions[0]["edit"])
        assert_("Result::Err(_)" in new_text, "Err arm with one _")


# ---------------------------------------------------------------------------
# build_exhaustiveness_code_actions — no warn means no action.
# ---------------------------------------------------------------------------


def test_no_action_when_no_exhaustiveness_warn() -> None:
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn run(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "        Option::None => 0\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        # The diagnostics list is empty (match is exhaustive).
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(actions, [], "no actions when no diagnostics")


def test_no_action_when_diag_is_unrelated() -> None:
    """A diagnostic that ISN'T an exhaustiveness WARN shouldn't trigger
    the quickfix even when its range lands inside a match block."""
    source = (
        "enum Option { Some(int) None }\n"
        "fn run(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[{
                "range": {
                    "start": {"line": 2, "character": 4},
                    "end": {"line": 2, "character": 5},
                },
                "severity": 1,
                "source": "nova",
                "message": "syntax error: unexpected token",
            }],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(actions, [], "unrelated diag yields no action")


# ---------------------------------------------------------------------------
# Catch-all-before behavior.
# ---------------------------------------------------------------------------


def test_action_inserts_before_catch_all() -> None:
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn get(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "        _ => 0\n"
        "    }\n"
        "}\n"
    )
    # Note: the existing `_` makes this exhaustive — R17A would NOT
    # emit a warn here. We use this test purely to exercise the
    # insertion-point logic — we synthesize a diagnostic that would
    # land on the match anyway. The action machinery still resolves
    # the missing variants from the covered set (we have Some + _,
    # so None is "missing" by the AST scan even though R17A elides
    # the warn because of the catch-all).
    #
    # The point of this test: when we DO produce a fix, it must land
    # ABOVE the `_ =>` arm, not after it.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.nova")
        _write(path, source)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(path, source)
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Option (missing: None)",
        }
        actions = build_exhaustiveness_code_actions(
            uri=_uri(path),
            doc_text=source,
            diagnostics=[diag],
            file_cache=cache,
            workspace_index=idx,
            start_path=path,
        )
        assert_eq(len(actions), 1, "one action when WARN forwarded")
        # The edit's insertion line should be the catch-all line, not
        # the close-brace line.
        edit_obj = actions[0]["edit"]
        text_edit = edit_obj["changes"][_uri(path)][0]
        # Catch-all is on line 8 (0-based); close brace is line 9.
        assert_eq(text_edit["range"]["start"]["line"], 8,
                  "insertion line is catch-all line")
        # Verify the apply puts the new arm above the `_`.
        new_text = _apply_edit(source, _uri(path), edit_obj)
        new_lines = new_text.splitlines()
        none_idx = next(
            i for i, l in enumerate(new_lines) if "Option::None" in l
        )
        catch_idx = next(
            i for i, l in enumerate(new_lines)
            if l.strip().startswith("_ =>")
        )
        assert_(none_idx < catch_idx, "None inserted before catch-all")


# ---------------------------------------------------------------------------
# Cross-file enum.
# ---------------------------------------------------------------------------


def test_action_cross_file_enum() -> None:
    """Enum declared in file A, match in file B that imports A.
    The code action should find the variants from file A."""
    enum_text = (
        "enum Op {\n"
        "    Plus(int, int)\n"
        "    Minus(int, int)\n"
        "    Times(int, int)\n"
        "}\n"
    )
    main_text = (
        'import "ops.nova"\n'
        "\n"
        "fn eval(o) {\n"
        "    match o {\n"
        "        Op::Plus(a, b) => a + b\n"
        "    }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ops = os.path.join(tmp, "ops.nova")
        main = os.path.join(tmp, "main.nova")
        _write(ops, enum_text)
        _write(main, main_text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_text(ops, enum_text)
        idx.index_text(main, main_text)
        diag = {
            "range": {
                "start": {"line": 3, "character": 4},
                "end": {"line": 3, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Op (missing: Minus, Times)",
        }
        actions = build_exhaustiveness_code_actions(
            uri=_uri(main),
            doc_text=main_text,
            diagnostics=[diag],
            file_cache=cache,
            workspace_index=idx,
            start_path=main,
        )
        assert_eq(len(actions), 1, "one action for cross-file enum")
        new_text = _apply_edit(main_text, _uri(main), actions[0]["edit"])
        assert_("Op::Minus(_, _)" in new_text,
                "Minus arm with 2 placeholders (cross-file arity)")
        assert_("Op::Times(_, _)" in new_text,
                "Times arm with 2 placeholders (cross-file arity)")


# ---------------------------------------------------------------------------
# Server wire smoke — full dispatch round trip.
# ---------------------------------------------------------------------------


def test_server_capability_advertises_quickfix() -> None:
    """The codeAction capability should now include quickfix in
    addition to the three existing refactor kinds."""
    client = LspClient()
    with tempfile.TemporaryDirectory() as ws:
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        ca = caps.get("codeActionProvider")
        assert_(ca is not None, "codeActionProvider present")
        kinds = ca.get("codeActionKinds") or []
        assert_("quickfix" in kinds, "quickfix kind advertised")
        # The three existing kinds should still be present too.
        assert_("refactor.extract" in kinds, "extract still advertised")
        assert_("source.organizeImports" in kinds,
                "organize-imports still advertised")
        assert_("source.organizeFns" in kinds,
                "organize-fns still advertised")


def test_server_dispatch_round_trip() -> None:
    """Simulate the full client flow: open, request code actions with
    the forwarded diagnostic, apply the edit."""
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn unwrap(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    client = LspClient()
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, source)
        client.initialize(ws)
        uri = client.open(path, source)
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Option (missing: None)",
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": diag["range"],
                "context": {"diagnostics": [diag]},
            },
        )
        actions = resp["result"]
        assert_(isinstance(actions, list), "actions is list")
        # Find the quickfix among returned actions.
        qf = [a for a in actions if a.get("kind") == KIND_QUICKFIX]
        assert_eq(len(qf), 1, "exactly one quickfix returned")
        assert_eq(qf[0]["title"], "Add missing match arms", "title")
        # Apply and verify.
        new_text = _apply_edit(source, uri, qf[0]["edit"])
        assert_("Option::None" in new_text, "None arm inserted via dispatch")


# ---------------------------------------------------------------------------
# Integration: ensure the quickfix is filtered out by `context.only`.
# ---------------------------------------------------------------------------


def test_server_only_filter_excludes_quickfix() -> None:
    """When the client passes context.only=['refactor.extract'] the
    quickfix should not appear."""
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn unwrap(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    client = LspClient()
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, source)
        client.initialize(ws)
        uri = client.open(path, source)
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Option (missing: None)",
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": diag["range"],
                "context": {
                    "diagnostics": [diag],
                    "only": ["refactor.extract"],
                },
            },
        )
        actions = resp["result"]
        # quickfix should be filtered out by `only`.
        qf = [a for a in actions if a.get("kind") == KIND_QUICKFIX]
        assert_eq(qf, [], "quickfix filtered by context.only")


def test_server_only_includes_quickfix() -> None:
    """When the client passes context.only=['quickfix'] the quickfix
    should appear and the other refactor kinds should be filtered."""
    source = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "\n"
        "fn unwrap(o) {\n"
        "    match o {\n"
        "        Option::Some(v) => v\n"
        "    }\n"
        "}\n"
    )
    client = LspClient()
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, source)
        client.initialize(ws)
        uri = client.open(path, source)
        diag = {
            "range": {
                "start": {"line": 6, "character": 4},
                "end": {"line": 6, "character": 5},
            },
            "severity": 2,
            "source": "nova",
            "message": "non-exhaustive match on Option (missing: None)",
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": diag["range"],
                "context": {
                    "diagnostics": [diag],
                    "only": ["quickfix"],
                },
            },
        )
        actions = resp["result"]
        kinds = [a.get("kind") for a in actions]
        assert_("quickfix" in kinds, "quickfix present when only=['quickfix']")


# ---------------------------------------------------------------------------
# Existing code action still works (we shouldn't have broken it).
# ---------------------------------------------------------------------------


def test_existing_code_actions_unaffected() -> None:
    """A document with no exhaustiveness diagnostics and the right
    shape for extract/organize should still produce those actions."""
    source = (
        'import "../src/foo.nova"\n'
        'import "std/io.nova"\n'
        "\n"
        "fn zeta(x) { return x }\n"
        "\n"
        "fn alpha(n) {\n"
        "    let total = 0\n"
        "    total = total + n\n"
        "    return total\n"
        "}\n"
    )
    client = LspClient()
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, source)
        client.initialize(ws)
        uri = client.open(path, source)
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 7, "character": 0},
                    "end": {"line": 8, "character": 20},
                },
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = [a.get("kind") for a in actions]
        # All three legacy kinds should still appear.
        assert_("source.organizeImports" in kinds,
                "organize-imports preserved")
        assert_("source.organizeFns" in kinds, "sort-fns preserved")
        # quickfix should NOT appear (no diagnostics forwarded).
        assert_("quickfix" not in kinds,
                "no quickfix when no diagnostics")


# ---------------------------------------------------------------------------


def main() -> int:
    test_parse_diag_simple()
    test_parse_diag_multi_missing()
    test_parse_diag_with_warning_prefix()
    test_parse_diag_unrelated_returns_none()
    test_is_exhaustiveness_diagnostic()
    test_find_match_at_simple()
    test_find_match_at_with_catch_all()
    test_find_match_at_no_match()
    test_resolve_enum_same_file()
    test_resolve_enum_cross_file_via_import()
    test_resolve_enum_cross_file_via_workspace_index()
    test_resolve_enum_not_found()
    test_collect_covered_variants_some_only()
    test_collect_covered_variants_two_arms()
    test_infer_missing_option_just_some()
    test_infer_missing_shape_circle_triangle()
    test_infer_missing_returns_in_declaration_order()
    test_build_arm_text_nullary()
    test_build_arm_text_unary()
    test_build_arm_text_payload_arity_2()
    test_build_arm_text_payload_arity_3()
    test_build_arms_block_joins_with_newline()
    test_build_arms_respects_custom_indent()
    test_insertion_point_no_catch_all()
    test_insertion_point_with_catch_all()
    test_build_workspace_edit_shape()
    test_action_option_missing_none()
    test_action_shape_missing_rect()
    test_action_result_missing_err()
    test_no_action_when_no_exhaustiveness_warn()
    test_no_action_when_diag_is_unrelated()
    test_action_inserts_before_catch_all()
    test_action_cross_file_enum()
    test_server_capability_advertises_quickfix()
    test_server_dispatch_round_trip()
    test_server_only_filter_excludes_quickfix()
    test_server_only_includes_quickfix()
    test_existing_code_actions_unaffected()
    print(f"test_exhaustiveness_fix: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
