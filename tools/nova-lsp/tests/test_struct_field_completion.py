"""Unit + wire + integration tests for struct brace-init field
completion (R26D).

Covers:

  * ``is_brace_init_context`` -- single-line and multi-line brace
    bodies, generic struct names (``Box<int> {``), block-expr ``{``
    that ISN'T a brace-init (lowercase / no preceding IDENT), nested
    init bodies, statement-boundary aborts, in-string / in-comment
    false positives.
  * ``get_already_specified_fields`` -- empty body, partially typed
    body, body that spans lines, body containing a nested brace-init
    (the nested fields should NOT be harvested as outer-level).
  * ``compute_field_completions`` -- full field list, partial
    exclusion (already-specified fields filtered out), all-fields
    specified -> empty list, unknown struct -> empty list, threshold
    for the ``..base`` spread suggestion.
  * ``compute_struct_field_completions`` -- top-level helper, wires
    detection + harvest + completion. Returns ``None`` outside the
    context so the caller falls through to R24E.
  * Server-level wire tests through ``dispatch`` so the completion
    handler returns the focused list end-to-end.
  * Cross-file: struct in file A, brace-init in file B (via import +
    via workspace index without import).
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
from nova_lsp.imports import FileCache  # noqa: E402
from nova_lsp.struct_field_completion import (  # noqa: E402
    compute_field_completions,
    compute_struct_field_completions,
    get_already_specified_fields,
    is_brace_init_context,
    scan_struct_fields,
)
from nova_lsp.type_completion import (  # noqa: E402
    collect_type_decls,
    compute_type_aware_completions,
)
from nova_lsp.type_hierarchy import (  # noqa: E402
    scan_type_declarations,
)
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex  # noqa: E402


_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, (
        f"FAIL [{label}]: expected {expected!r}, got {actual!r}"
    )


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _setup_workspace(workspace: str, files: dict) -> tuple:
    """Write `files` (name -> text) under `workspace` and return
    (file_cache, workspace_index) primed with their contents."""
    for name, content in files.items():
        _write(os.path.join(workspace, name), content)
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_workspace_root(workspace)
    return cache, idx


def _decls_from(text: str, path: str = "/p"):
    """Helper: build the (path, TypeDecl) list expected by
    ``compute_field_completions`` from in-memory ``text``."""
    return [(path, d) for d in scan_type_declarations(text)]


# ---------------------------------------------------------------------------
# is_brace_init_context -- pure trigger detection.
# ---------------------------------------------------------------------------


def test_brace_init_single_line_after_open() -> None:
    """`Point { ` -> detected as a brace-init for `Point`."""
    text = "let p = Point { "
    ctx = is_brace_init_context(0, 16, text)
    assert_(ctx is not None, "Point { detected")
    assert_eq(ctx.struct_name, "Point", "struct name")
    assert_eq(ctx.open_lb_col, 14, "open `{` column")
    assert_eq(ctx.open_lb_line, 0, "open `{` line")


def test_brace_init_with_partial_field() -> None:
    """`Point { x: 10, ` -> still detected, cursor sits after the
    comma so the existing line-local detector wouldn't fire."""
    text = "let p = Point { x: 10, "
    ctx = is_brace_init_context(0, 23, text)
    assert_(ctx is not None, "Point { x: 10, detected")
    assert_eq(ctx.struct_name, "Point", "struct name with partial")


def test_brace_init_multi_line() -> None:
    """Body that spans lines -- cursor on a later line still picks up
    the opening `{` from a prior line."""
    text = (
        "let p = Point {\n"
        "    x: 10,\n"
        "    \n"
        "}\n"
    )
    # Cursor is on line 2 at column 4 (after `    `).
    ctx = is_brace_init_context(2, 4, text)
    assert_(ctx is not None, "multi-line detected")
    assert_eq(ctx.struct_name, "Point", "multi-line struct name")
    assert_eq(ctx.open_lb_line, 0, "multi-line open line")


def test_brace_init_generic_struct() -> None:
    """`Box<int> {` -- the generic args between the name and the `{`
    should be skipped over."""
    text = "let b = Box<int> { "
    ctx = is_brace_init_context(0, 19, text)
    assert_(ctx is not None, "Box<int> { detected")
    assert_eq(ctx.struct_name, "Box", "Box (stripped of generics)")


def test_brace_init_nested_inner_wins() -> None:
    """Inside a nested init body, the innermost `{` wins."""
    text = "let p = Wrapper { inner: Point { x: 10, "
    ctx = is_brace_init_context(0, 40, text)
    assert_(ctx is not None, "nested detected")
    assert_eq(ctx.struct_name, "Point", "innermost struct wins")


def test_brace_init_none_lowercase_ident() -> None:
    """A `{` with a lowercase IDENT before it is a block expression,
    NOT a brace-init."""
    text = "if x { "
    ctx = is_brace_init_context(0, 7, text)
    assert_(ctx is None, "lowercase IDENT -> no brace-init")


def test_brace_init_none_no_ident_before_brace() -> None:
    """Bare `{` with nothing before it is a block expression."""
    text = "do {\n    \n}\n"
    ctx = is_brace_init_context(1, 4, text)
    assert_(ctx is None, "bare { -> no brace-init")


def test_brace_init_none_in_string() -> None:
    """A `Point {` that lives inside a string literal must NOT trip
    the trigger."""
    text = 'let s = "Point { x: 1, "\n'
    ctx = is_brace_init_context(0, 24, text)
    assert_(ctx is None, "in-string brace ignored")


def test_brace_init_none_in_comment() -> None:
    """A `Point {` inside a `//` comment must NOT trip the trigger."""
    text = "// Point { x: 1,\nlet x = 5\n"
    ctx = is_brace_init_context(1, 9, text)
    assert_(ctx is None, "in-comment brace ignored")


def test_brace_init_aborted_on_semicolon() -> None:
    """A `;` at depth 0 terminates the backward scan -- even when a
    `Name {` appears earlier in the file."""
    text = (
        "let p = Point { x: 10 };\n"
        "let q = 5\n"
    )
    ctx = is_brace_init_context(1, 9, text)
    assert_(ctx is None, "semicolon terminates scan")


# ---------------------------------------------------------------------------
# get_already_specified_fields -- partial init harvest.
# ---------------------------------------------------------------------------


def test_specified_fields_empty_body() -> None:
    text = "let p = Point { "
    ctx = is_brace_init_context(0, 16, text)
    assert_(ctx is not None, "ctx present")
    fields = get_already_specified_fields(
        0, 16, text, ctx.open_lb_line, ctx.open_lb_col
    )
    assert_eq(fields, [], "empty body -> no specified fields")


def test_specified_fields_one_typed() -> None:
    text = "let p = Point { x: 10, "
    ctx = is_brace_init_context(0, 23, text)
    fields = get_already_specified_fields(
        0, 23, text, ctx.open_lb_line, ctx.open_lb_col
    )
    assert_eq(fields, ["x"], "x already specified")


def test_specified_fields_two_typed() -> None:
    text = "let p = Triple { a: 1, b: 2, "
    ctx = is_brace_init_context(0, 29, text)
    fields = get_already_specified_fields(
        0, 29, text, ctx.open_lb_line, ctx.open_lb_col
    )
    assert_eq(fields, ["a", "b"], "a + b already specified")


def test_specified_fields_multi_line() -> None:
    text = (
        "let p = Point {\n"
        "    x: 10,\n"
        "    \n"
        "}\n"
    )
    ctx = is_brace_init_context(2, 4, text)
    fields = get_already_specified_fields(
        2, 4, text, ctx.open_lb_line, ctx.open_lb_col
    )
    assert_eq(fields, ["x"], "multi-line x captured")


def test_specified_fields_ignores_nested_init() -> None:
    """A nested `Inner { y: 5 }` shouldn't count `y` as an outer-level
    specified field -- `y` lives at depth 1 of the outer body."""
    text = "let p = Outer { a: Inner { y: 5 }, "
    ctx = is_brace_init_context(0, 35, text)
    fields = get_already_specified_fields(
        0, 35, text, ctx.open_lb_line, ctx.open_lb_col
    )
    assert_eq(fields, ["a"], "only outer-level fields captured")


# ---------------------------------------------------------------------------
# compute_field_completions -- direct invocation.
# ---------------------------------------------------------------------------


def test_field_completions_point_full() -> None:
    text = "struct Point { x: int, y: int }\n"
    decls = _decls_from(text)
    items = compute_field_completions("Point", [], decls, doc_text=text)
    labels = sorted(it["label"] for it in items)
    assert_eq(labels, ["x", "y"], "Point -> x, y")


def test_field_completions_point_x_already_specified() -> None:
    text = "struct Point { x: int, y: int }\n"
    decls = _decls_from(text)
    items = compute_field_completions("Point", ["x"], decls, doc_text=text)
    labels = [it["label"] for it in items]
    assert_eq(labels, ["y"], "x excluded, only y remains")


def test_field_completions_all_specified_returns_empty() -> None:
    text = "struct Point { x: int, y: int }\n"
    decls = _decls_from(text)
    items = compute_field_completions(
        "Point", ["x", "y"], decls, doc_text=text
    )
    assert_eq(items, [], "all fields specified -> empty list")


def test_field_completions_triple_three_fields() -> None:
    text = "struct Triple { a: int, b: int, c: int }\n"
    decls = _decls_from(text)
    items = compute_field_completions("Triple", [], decls, doc_text=text)
    labels = sorted(it["label"] for it in items if it["label"] != "..")
    assert_eq(labels, ["a", "b", "c"], "Triple -> a, b, c")
    spread_labels = [it["label"] for it in items if it["label"] == ".."]
    assert_eq(spread_labels, [".."], "spread suggestion on 3+ remaining")


def test_field_completions_unknown_struct_returns_empty() -> None:
    text = "struct Point { x: int, y: int }\n"
    decls = _decls_from(text)
    items = compute_field_completions("Foo", [], decls, doc_text=text)
    assert_eq(items, [], "unknown struct -> empty list (focused)")


def test_field_completions_generic_box() -> None:
    """`Box<int>` is recognised; the underlying struct is `Box` with
    field `value`."""
    text = "struct Box<T> { value: T }\n"
    decls = _decls_from(text)
    items = compute_field_completions("Box", [], decls, doc_text=text)
    labels = [it["label"] for it in items]
    assert_eq(labels, ["value"], "Box -> value")


# ---------------------------------------------------------------------------
# compute_struct_field_completions -- top-level helper.
# ---------------------------------------------------------------------------


def test_top_level_helper_returns_none_outside_context() -> None:
    """Cursor not in a brace-init body -> returns ``None`` so the
    caller falls through to R24E's other triggers."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = 5\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path),
            {"line": 1, "character": 9},
            text,
            decls,
        )
        assert_(items is None, "no brace-init -> None")


def test_top_level_helper_returns_fields_in_context() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path),
            {"line": 1, "character": 16},
            text,
            decls,
        )
        assert_(items is not None, "in brace-init -> list (not None)")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["x", "y"], "Point fields")


def test_top_level_helper_excludes_already_specified() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 10, \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path),
            {"line": 1, "character": 23},
            text,
            decls,
        )
        assert_(items is not None, "in brace-init -> list")
        labels = [it["label"] for it in items]
        assert_eq(labels, ["y"], "only y suggested after x typed")


def test_top_level_helper_all_specified_returns_empty() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 10, y: 20, \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path),
            {"line": 1, "character": 30},
            text,
            decls,
        )
        assert_(items is not None, "in brace-init -> list (not None)")
        assert_eq(items, [], "all fields specified -> []")


# ---------------------------------------------------------------------------
# Cross-file: struct in file A, brace-init in file B.
# ---------------------------------------------------------------------------


def test_cross_file_via_import() -> None:
    file_a = "struct Point { x: int, y: int }\n"
    file_b = (
        "import \"types.nova\"\n"
        "let p = Point { \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path_b = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(
            ws, {"types.nova": file_a, "main.nova": file_b}
        )
        decls = collect_type_decls(
            _uri(path_b), file_b, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path_b),
            {"line": 1, "character": 16},
            file_b,
            decls,
        )
        assert_(items is not None, "cross-file ctx detected")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["x", "y"], "cross-file via import -> x, y")


def test_cross_file_via_workspace_index() -> None:
    """Even without an `import` line, the workspace index exposes the
    struct."""
    file_a = "struct Status { code: int, message: str }\n"
    file_b = "let s = Status { \n"
    with tempfile.TemporaryDirectory() as ws:
        path_b = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(
            ws, {"sib.nova": file_a, "main.nova": file_b}
        )
        decls = collect_type_decls(
            _uri(path_b), file_b, cache, workspace_index=idx
        )
        items = compute_struct_field_completions(
            _uri(path_b),
            {"line": 0, "character": 17},
            file_b,
            decls,
        )
        assert_(items is not None, "cross-file via workspace index")
        labels = sorted(it["label"] for it in items if it["label"] != "..")
        assert_eq(labels, ["code", "message"], "Status fields cross-file")


# ---------------------------------------------------------------------------
# Server wire -- `dispatch` end-to-end through textDocument/completion.
# ---------------------------------------------------------------------------


def test_server_wire_brace_init() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/completion",
            {"textDocument": {"uri": uri},
             "position": {"line": 1, "character": 16}},
        )
        items = resp["result"]["items"]
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["x", "y"], "wire Point brace-init fields")


def test_server_wire_brace_init_partial() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 10, \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/completion",
            {"textDocument": {"uri": uri},
             "position": {"line": 1, "character": 23}},
        )
        items = resp["result"]["items"]
        labels = [it["label"] for it in items]
        assert_eq(labels, ["y"], "wire partial init excludes x")


def test_server_wire_fallback_when_no_brace_init() -> None:
    """When the cursor isn't in a brace-init body, the existing
    generic fallback is returned. Ensures R26D doesn't accidentally
    swallow the entire completion handler."""
    text = "fn foo() {\n    pr\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/completion",
            {"textDocument": {"uri": uri},
             "position": {"line": 1, "character": 6}},
        )
        items = resp["result"]["items"]
        labels = {it["label"] for it in items}
        assert_("println" in labels, "fallback list still includes println")
        assert_("foo" in labels, "fallback list still includes user fn")


def test_server_wire_capability_still_advertised() -> None:
    """R26D extends completion behaviour without adding a new top-
    level capability. The provider count should remain the same."""
    with tempfile.TemporaryDirectory() as ws:
        client = LspClient()
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        cp = caps["completionProvider"]
        assert_("." in cp["triggerCharacters"], ". still a trigger")
        # 15 providers + 1 sync key = 16 total (unchanged from R24E).
        provider_keys = sorted(
            k for k in caps.keys()
            if k.endswith("Provider") or k == "textDocumentSync"
        )
        assert_eq(len(provider_keys), 16,
                  "capability key count unchanged at 16")


def test_top_level_aware_routes_through_brace_init() -> None:
    """`compute_type_aware_completions` (the actual server entry
    point) routes through the brace-init helper first when the
    cursor is inside a brace body."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 1, "character": 16},
            text,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "type-aware routes through brace-init")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["x", "y"], "Point fields via top-level")


# ---------------------------------------------------------------------------
# Integration on the R25A brace-init fixture.
# ---------------------------------------------------------------------------


def test_integration_brace_init_fixture() -> None:
    """Use R25A's `tests/test_struct_brace_init.nova` fixture and
    assert `Box { ` suggests `value` and `Point { ` suggests `x, y`."""
    fixture = "/home/user/NOVA/tests/test_struct_brace_init.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_struct_brace_init.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(fixture)

    decls = collect_type_decls(
        _uri(fixture), text, cache, workspace_index=idx
    )

    # Simulate a cursor right after `Box {` on a synthetic line
    # appended after the fixture's existing content.
    synthetic = text + "\nlet _ = Box { \n"
    cursor_line = synthetic.rstrip("\n").count("\n")
    items = compute_struct_field_completions(
        _uri(fixture),
        {"line": cursor_line, "character": 14},
        synthetic,
        decls,
    )
    assert_(items is not None, "fixture Box { triggers")
    labels = [it["label"] for it in items]
    assert_("value" in labels, "fixture Box -> value")
    print(f"  test_struct_brace_init.nova Box -> {labels}")

    synthetic2 = text + "\nlet _ = Point { \n"
    cursor_line2 = synthetic2.rstrip("\n").count("\n")
    items2 = compute_struct_field_completions(
        _uri(fixture),
        {"line": cursor_line2, "character": 16},
        synthetic2,
        decls,
    )
    assert_(items2 is not None, "fixture Point { triggers")
    labels2 = sorted(it["label"] for it in items2)
    assert_eq(labels2, ["x", "y"], "fixture Point -> x, y")
    print(f"  test_struct_brace_init.nova Point -> {labels2}")


# ---------------------------------------------------------------------------


def main() -> int:
    test_brace_init_single_line_after_open()
    test_brace_init_with_partial_field()
    test_brace_init_multi_line()
    test_brace_init_generic_struct()
    test_brace_init_nested_inner_wins()
    test_brace_init_none_lowercase_ident()
    test_brace_init_none_no_ident_before_brace()
    test_brace_init_none_in_string()
    test_brace_init_none_in_comment()
    test_brace_init_aborted_on_semicolon()
    test_specified_fields_empty_body()
    test_specified_fields_one_typed()
    test_specified_fields_two_typed()
    test_specified_fields_multi_line()
    test_specified_fields_ignores_nested_init()
    test_field_completions_point_full()
    test_field_completions_point_x_already_specified()
    test_field_completions_all_specified_returns_empty()
    test_field_completions_triple_three_fields()
    test_field_completions_unknown_struct_returns_empty()
    test_field_completions_generic_box()
    test_top_level_helper_returns_none_outside_context()
    test_top_level_helper_returns_fields_in_context()
    test_top_level_helper_excludes_already_specified()
    test_top_level_helper_all_specified_returns_empty()
    test_cross_file_via_import()
    test_cross_file_via_workspace_index()
    test_server_wire_brace_init()
    test_server_wire_brace_init_partial()
    test_server_wire_fallback_when_no_brace_init()
    test_server_wire_capability_still_advertised()
    test_top_level_aware_routes_through_brace_init()
    test_integration_brace_init_fixture()
    print(f"test_struct_field_completion: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
