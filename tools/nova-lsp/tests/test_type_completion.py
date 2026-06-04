"""Unit + smoke + integration tests for type-aware completion (R24E).

Covers:

  * ``detect_trigger`` — every recognised trigger context plus the
    rejected non-triggers (whitespace, unrelated method calls).
  * ``variable_type_name`` — annotation / inference / param lookup.
  * ``scan_struct_fields`` — field extraction from R23A's
    generic-aware struct grammar.
  * ``compute_type_aware_completions`` — direct invocation against
    in-memory documents with a primed FileCache + WorkspaceSymbolIndex.
  * Server-level wire tests through ``dispatch`` so the completion
    handler's two-layered "type-aware preempts generic" wiring is
    exercised end-to-end.
  * Cross-file: enum declared in file A, completion site in file B.
  * Integration on ``tests/test_sum_types.nova`` and
    ``tests/test_generic_struct.nova`` — the reference fixtures from
    R17A and R23A.
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
from nova_lsp.type_completion import (  # noqa: E402
    TRIGGER_ENUM_VARIANT,
    TRIGGER_FIELD_ACCESS,
    TRIGGER_GENERIC_ARG,
    TRIGGER_NONE,
    TRIGGER_TYPE_ANNOTATION,
    compute_type_aware_completions,
    detect_trigger,
    scan_struct_fields,
    variable_type_name,
)
from nova_lsp.type_hierarchy import (  # noqa: E402
    KIND_STRUCT,
    scan_type_declarations,
    type_decl_by_name,
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
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


# ---------------------------------------------------------------------------
# detect_trigger — pure trigger-detection.
# ---------------------------------------------------------------------------


def test_detect_trigger_enum_variant() -> None:
    info = detect_trigger("Option::", 8)
    assert_eq(info.kind, TRIGGER_ENUM_VARIANT, "Option:: kind")
    assert_eq(info.payload, "Option", "Option:: payload")


def test_detect_trigger_enum_variant_with_partial() -> None:
    """User typing `Option::No` — partial identifier `No` should be
    stripped so the trigger still resolves to TRIGGER_ENUM_VARIANT."""
    info = detect_trigger("Option::No", 10)
    assert_eq(info.kind, TRIGGER_ENUM_VARIANT, "partial kind")
    assert_eq(info.payload, "Option", "partial payload")


def test_detect_trigger_field_access() -> None:
    info = detect_trigger("box.", 4)
    assert_eq(info.kind, TRIGGER_FIELD_ACCESS, "box. kind")
    assert_eq(info.payload, "box", "box. payload")


def test_detect_trigger_field_access_with_partial() -> None:
    info = detect_trigger("point.x", 7)
    assert_eq(info.kind, TRIGGER_FIELD_ACCESS, "point.x kind")
    assert_eq(info.payload, "point", "point.x payload")


def test_detect_trigger_type_annotation_let() -> None:
    info = detect_trigger("let x: ", 7)
    assert_eq(info.kind, TRIGGER_TYPE_ANNOTATION, "let x: kind")


def test_detect_trigger_type_annotation_indented_let() -> None:
    """Triggers must work at any indentation since `let` can appear
    inside fn bodies."""
    info = detect_trigger("    let inner: ", 15)
    assert_eq(info.kind, TRIGGER_TYPE_ANNOTATION, "indented let kind")


def test_detect_trigger_type_annotation_fn_param() -> None:
    info = detect_trigger("fn foo(p: ", 10)
    assert_eq(info.kind, TRIGGER_TYPE_ANNOTATION, "fn param kind")


def test_detect_trigger_type_annotation_second_fn_param() -> None:
    info = detect_trigger("fn foo(a: int, b: ", 18)
    assert_eq(info.kind, TRIGGER_TYPE_ANNOTATION, "second fn param kind")


def test_detect_trigger_generic_arg() -> None:
    info = detect_trigger("let r: Box<", 11)
    assert_eq(info.kind, TRIGGER_GENERIC_ARG, "Box< kind")


def test_detect_trigger_none_on_function_call() -> None:
    """`println(` is a function call paren, not a type-aware trigger."""
    info = detect_trigger("println(", 8)
    assert_eq(info.kind, TRIGGER_NONE, "println( falls back")


def test_detect_trigger_none_on_whitespace() -> None:
    info = detect_trigger("    ", 4)
    assert_eq(info.kind, TRIGGER_NONE, "whitespace returns none")


def test_detect_trigger_none_on_bare_identifier() -> None:
    """`Opt` alone (the user is typing a prefix) should fall back to
    text-based completion — no `::` / `.` / `:` to anchor a context."""
    info = detect_trigger("Opt", 3)
    assert_eq(info.kind, TRIGGER_NONE, "bare ident returns none")


# ---------------------------------------------------------------------------
# variable_type_name — annotation / inference / param.
# ---------------------------------------------------------------------------


def test_variable_type_name_let_annotation() -> None:
    text = "let b: Box = Box(42)\n"
    assert_eq(variable_type_name(text, "b"), "Box", "let annotation")


def test_variable_type_name_let_generic_annotation() -> None:
    text = "let b: Box<int> = Box(42)\n"
    assert_eq(variable_type_name(text, "b"), "Box", "generic annotation")


def test_variable_type_name_let_constructor_inference() -> None:
    text = "let b = Box(42)\n"
    assert_eq(variable_type_name(text, "b"), "Box", "constructor inference")


def test_variable_type_name_fn_param() -> None:
    text = "fn foo(b: Box) {\n    println(b.value)\n}\n"
    assert_eq(variable_type_name(text, "b"), "Box", "fn param")


def test_variable_type_name_unknown_returns_none() -> None:
    text = "let other = 5\n"
    assert_(variable_type_name(text, "missing") is None,
            "unknown variable returns None")


# ---------------------------------------------------------------------------
# scan_struct_fields — single-line and multi-line.
# ---------------------------------------------------------------------------


def test_scan_struct_fields_multi_line() -> None:
    text = (
        "struct Pair {\n"
        "    first: int\n"
        "    second: str\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    pair = type_decl_by_name(decls, "Pair")
    fields = scan_struct_fields(text, pair)
    names = [f.name for f in fields]
    assert_eq(names, ["first", "second"], "fields source order")


def test_scan_struct_fields_semicolon_separators() -> None:
    text = (
        "struct Triple {\n"
        "    a: int;\n"
        "    b: str;\n"
        "    c: bool\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    triple = type_decl_by_name(decls, "Triple")
    fields = scan_struct_fields(text, triple)
    assert_eq(len(fields), 3, "three semicolon-separated fields")
    assert_eq([f.name for f in fields], ["a", "b", "c"], "names")


# ---------------------------------------------------------------------------
# compute_type_aware_completions — direct invocation.
# ---------------------------------------------------------------------------


def _setup_workspace(workspace: str, files: dict) -> tuple:
    """Write `files` (name -> text) under `workspace` and return
    (file_cache, workspace_index) primed with their contents."""
    for name, content in files.items():
        _write(os.path.join(workspace, name), content)
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_workspace_root(workspace)
    return cache, idx


def test_completions_option_returns_some_none() -> None:
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "let x = Option::\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 4, "character": 16},
            text,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "Option:: returns a list (not None)")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["None", "Some"], "Option variants")


def test_completions_result_returns_ok_err() -> None:
    text = (
        "enum Result {\n"
        "    Ok(int)\n"
        "    Err(str)\n"
        "}\n"
        "let r = Result::\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 4, "character": 16},
            text,
            cache,
            workspace_index=idx,
        )
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["Err", "Ok"], "Result variants")


def test_completions_shape_returns_three_variants() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
        "let s = Shape::\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 5, "character": 15},
            text,
            cache,
            workspace_index=idx,
        )
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["Circle", "Rect", "Triangle"], "Shape variants")
        # Triangle's arity surfaces in the detail string.
        tri = next(it for it in items if it["label"] == "Triangle")
        assert_("(_, _, _)" in tri["detail"], "Triangle detail shows arity")


def test_completions_field_access_box() -> None:
    text = (
        "struct Box<T> {\n"
        "    value: T\n"
        "}\n"
        "let b: Box<int> = Box(42)\n"
        "let x = b.\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 4, "character": 10},
            text,
            cache,
            workspace_index=idx,
        )
        labels = [it["label"] for it in items]
        assert_eq(labels, ["value"], "Box.value")


def test_completions_field_access_pair_two_fields() -> None:
    text = (
        "struct Pair<A, B> {\n"
        "    first: A,\n"
        "    second: B\n"
        "}\n"
        "let p: Pair<int, str> = Pair(1, \"two\")\n"
        "let x = p.\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 5, "character": 10},
            text,
            cache,
            workspace_index=idx,
        )
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["first", "second"], "Pair fields")


def test_completions_type_annotation_includes_enums_and_structs() -> None:
    text = (
        "enum Color { Red, Green, Blue }\n"
        "struct Point { x: int, y: int }\n"
        "let p: \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 2, "character": 7},
            text,
            cache,
            workspace_index=idx,
        )
        labels = [it["label"] for it in items]
        assert_("Color" in labels, "Color in type-annot list")
        assert_("Point" in labels, "Point in type-annot list")
        # Primitives are always available.
        for prim in ("int", "str", "bool"):
            assert_(prim in labels, f"primitive {prim!r} in list")


def test_completions_unknown_enum_returns_empty_list() -> None:
    """`Foo::` where `Foo` isn't a known enum returns an empty list —
    NOT None — so the caller doesn't fall through to the generic
    fallback (the user asked for variants, not the world)."""
    text = (
        "enum Real { A, B }\n"
        "let x = Foo::\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 1, "character": 13},
            text,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "trigger was recognised (not None)")
        assert_eq(items, [], "unknown enum -> empty list")


def test_completions_no_trigger_returns_none() -> None:
    """Non-trigger context returns None so the caller falls back to
    text-based completion."""
    text = "let x = 5\nprint\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 1, "character": 5},
            text,
            cache,
            workspace_index=idx,
        )
        assert_(items is None, "no trigger returns None")


# ---------------------------------------------------------------------------
# Cross-file: enum in file A, completion in file B.
# ---------------------------------------------------------------------------


def test_completions_cross_file_enum() -> None:
    file_a = (
        "enum Direction {\n"
        "    North\n"
        "    South\n"
        "    East\n"
        "    West\n"
        "}\n"
    )
    file_b = (
        "import \"types.nova\"\n"
        "let d = Direction::\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path_b = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(
            ws, {"types.nova": file_a, "main.nova": file_b}
        )
        items = compute_type_aware_completions(
            _uri(path_b),
            {"line": 1, "character": 19},
            file_b,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "trigger recognised cross-file")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["East", "North", "South", "West"],
                  "cross-file Direction variants")


def test_completions_cross_file_without_import() -> None:
    """Even without an explicit `import` we still surface enums from
    sibling files via the workspace index (R8C)."""
    file_a = "enum Status {\n    Open\n    Closed\n}\n"
    file_b = "let s = Status::\n"
    with tempfile.TemporaryDirectory() as ws:
        path_b = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(
            ws, {"sib.nova": file_a, "main.nova": file_b}
        )
        items = compute_type_aware_completions(
            _uri(path_b),
            {"line": 0, "character": 16},
            file_b,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "trigger recognised without import")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["Closed", "Open"], "cross-file Status without import")


# ---------------------------------------------------------------------------
# Server wire — `dispatch` end-to-end.
# ---------------------------------------------------------------------------


def test_server_completion_capability_still_advertised() -> None:
    """The completion capability still advertises `.` + `(` triggers —
    R24E did NOT change the capability shape, only the response logic.
    The total advertised-capability count stays the same as before
    R24E (no new capability key was added)."""
    with tempfile.TemporaryDirectory() as ws:
        client = LspClient()
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        cp = caps["completionProvider"]
        assert_("." in cp["triggerCharacters"], ". still a trigger")
        assert_("(" in cp["triggerCharacters"], "( still a trigger")
        # Expected: 16 *Provider keys plus textDocumentSync. R24E does
        # NOT add a new capability — it only deepens what
        # completionProvider returns. R33D later added
        # `documentLinkProvider`, bumping the snapshot to 17.
        provider_keys = sorted(
            k for k in caps.keys()
            if k.endswith("Provider") or k == "textDocumentSync"
        )
        # Concrete snapshot: 16 providers + 1 sync key = 17 total.
        assert_eq(len(provider_keys), 17,
                  "capability key count 17 (R33D documentLink added)")
        # And completionProvider IS one of them.
        assert_("completionProvider" in provider_keys,
                "completionProvider still advertised")


def test_server_completion_wire_enum_variants() -> None:
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "let x = Option::\n"
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
             "position": {"line": 4, "character": 16}},
        )
        items = resp["result"]["items"]
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["None", "Some"], "wire Option variants")


def test_server_completion_wire_field_access() -> None:
    text = (
        "struct Box {\n"
        "    value: int\n"
        "}\n"
        "let b: Box = Box(42)\n"
        "let x = b.\n"
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
             "position": {"line": 4, "character": 10}},
        )
        items = resp["result"]["items"]
        labels = [it["label"] for it in items]
        assert_eq(labels, ["value"], "wire Box.value")


def test_server_completion_wire_fallback_returns_builtins() -> None:
    """When no trigger applies (cursor mid-line in a fresh fn body) the
    server falls back to the generic builtins + user-fn list."""
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
        # Builtins must be there.
        assert_("println" in labels, "fallback list includes println")
        # User fn must be there.
        assert_("foo" in labels, "fallback list includes user fn")


def test_server_completion_wire_type_annotation() -> None:
    text = (
        "enum Color { Red, Green, Blue }\n"
        "struct Point { x: int, y: int }\n"
        "let p: \n"
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
             "position": {"line": 2, "character": 7}},
        )
        items = resp["result"]["items"]
        labels = {it["label"] for it in items}
        assert_("Color" in labels, "Color in type-annot wire list")
        assert_("Point" in labels, "Point in type-annot wire list")
        assert_("int" in labels, "int primitive present")
        # Generic builtins should NOT bleed in — we returned the focused
        # list verbatim.
        assert_("println" not in labels, "println NOT in type-annot list")


# ---------------------------------------------------------------------------
# Integration on the R17A + R23A reference fixtures.
# ---------------------------------------------------------------------------


def test_integration_sum_types_fixture() -> None:
    """Use R17A's enum fixture and assert all four enums surface their
    variants under the `::` trigger."""
    fixture = "/home/user/NOVA/tests/test_sum_types.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_sum_types.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(fixture)

    # Synthesize a cursor position after `Option::` in the fixture. The
    # exact line doesn't matter; we look it up.
    lines = text.splitlines()
    cursor_line = next(
        i for i, ln in enumerate(lines) if "Option::Some" in ln
    )
    cursor_col = lines[cursor_line].find("Option::") + len("Option::")

    items = compute_type_aware_completions(
        _uri(fixture),
        {"line": cursor_line, "character": cursor_col},
        text,
        cache,
        workspace_index=idx,
    )
    assert_(items is not None, "Option:: triggers in fixture")
    labels = sorted(it["label"] for it in items)
    assert_eq(labels, ["None", "Some"], "fixture Option variants")
    print(f"  test_sum_types.nova Option:: -> {labels}")


def test_integration_generic_struct_fixture() -> None:
    """Use R23A's generic-struct fixture and assert ``b.`` (where
    ``b: Box<int>``) suggests the ``value`` field."""
    fixture = "/home/user/NOVA/tests/test_generic_struct.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_generic_struct.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(fixture)

    # Find the line that does `b.value` — pretend the cursor is right
    # after the `.`.
    lines = text.splitlines()
    cursor_line = next(
        i for i, ln in enumerate(lines)
        if "b.value" in ln and "assert" in ln
    )
    dot_pos = lines[cursor_line].find("b.") + 2

    items = compute_type_aware_completions(
        _uri(fixture),
        {"line": cursor_line, "character": dot_pos},
        text,
        cache,
        workspace_index=idx,
    )
    assert_(items is not None, "b. triggers in fixture")
    labels = [it["label"] for it in items]
    assert_("value" in labels, "fixture Box.value visible")
    print(f"  test_generic_struct.nova b. -> {labels}")


# ---------------------------------------------------------------------------


def main() -> int:
    test_detect_trigger_enum_variant()
    test_detect_trigger_enum_variant_with_partial()
    test_detect_trigger_field_access()
    test_detect_trigger_field_access_with_partial()
    test_detect_trigger_type_annotation_let()
    test_detect_trigger_type_annotation_indented_let()
    test_detect_trigger_type_annotation_fn_param()
    test_detect_trigger_type_annotation_second_fn_param()
    test_detect_trigger_generic_arg()
    test_detect_trigger_none_on_function_call()
    test_detect_trigger_none_on_whitespace()
    test_detect_trigger_none_on_bare_identifier()
    test_variable_type_name_let_annotation()
    test_variable_type_name_let_generic_annotation()
    test_variable_type_name_let_constructor_inference()
    test_variable_type_name_fn_param()
    test_variable_type_name_unknown_returns_none()
    test_scan_struct_fields_multi_line()
    test_scan_struct_fields_semicolon_separators()
    test_completions_option_returns_some_none()
    test_completions_result_returns_ok_err()
    test_completions_shape_returns_three_variants()
    test_completions_field_access_box()
    test_completions_field_access_pair_two_fields()
    test_completions_type_annotation_includes_enums_and_structs()
    test_completions_unknown_enum_returns_empty_list()
    test_completions_no_trigger_returns_none()
    test_completions_cross_file_enum()
    test_completions_cross_file_without_import()
    test_server_completion_capability_still_advertised()
    test_server_completion_wire_enum_variants()
    test_server_completion_wire_field_access()
    test_server_completion_wire_fallback_returns_builtins()
    test_server_completion_wire_type_annotation()
    test_integration_sum_types_fixture()
    test_integration_generic_struct_fixture()
    print(f"test_type_completion: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
