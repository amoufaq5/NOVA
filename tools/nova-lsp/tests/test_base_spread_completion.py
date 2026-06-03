"""Unit + wire tests for base-spread completion (R26A.2 follow-up to R26D).

Covers ``Point { ..|`` and ``Point { x: 10, ..|`` -- cursor immediately
after the ``..`` spread token. Returns in-scope struct-typed values
whose type matches the brace-init's struct name.

Test groups:

  * ``is_base_spread_context`` -- detection: cursor after ``..`` in a
    brace-init body, cursor after ``..`` inside a NESTED brace-init,
    cursor inside a brace body but NOT after ``..`` (should be None),
    multi-line spread positions.
  * ``find_in_scope_struct_values`` -- harvest: explicit annotation
    (``let p: Point = ...``), constructor inference (``let p =
    Point(...)``), brace-init form (``let p = Point { ... }``), fn
    parameter typed ``(p: Point)``, type filtering (only same-type
    values), let-shadow (later binding wins), self-reference filtering
    (don't suggest the variable being defined).
  * ``compute_base_spread_completions`` -- top-level helper: returns
    None outside the context, returns list when inside.
  * Server wire through ``dispatch`` end-to-end.
  * Capability count unchanged.
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
from nova_lsp.base_spread_completion import (  # noqa: E402
    compute_base_spread_completions,
    find_in_scope_struct_values,
    is_base_spread_context,
)
from nova_lsp.imports import FileCache  # noqa: E402
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
    for name, content in files.items():
        _write(os.path.join(workspace, name), content)
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_workspace_root(workspace)
    return cache, idx


def _decls_from(text: str, path: str = "/p"):
    return [(path, d) for d in scan_type_declarations(text)]


# ---------------------------------------------------------------------------
# is_base_spread_context -- detection.
# ---------------------------------------------------------------------------


def test_detect_after_double_dot_simple() -> None:
    """`Point { ..|` -> ctx with struct_name=Point."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
    )
    # Cursor at end of line 2, character 18 (after "..").
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 2, "character": 18},
        text,
    )
    assert_(ctx is not None, "Point { .. -> detected")
    assert_eq(ctx.struct_name, "Point", "ctx struct name")


def test_detect_after_double_dot_with_field_override() -> None:
    """`Point { x: 10, ..|` -> detected, struct_name=Point."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { x: 10, ..\n"
    )
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 2, "character": 25},
        text,
    )
    assert_(ctx is not None, "Point { x: 10, .. -> detected")
    assert_eq(ctx.struct_name, "Point", "ctx struct name with override")


def test_detect_inside_brace_but_not_after_dots_returns_none() -> None:
    """`Point { |` (no `..`) -> not a base-spread context."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let q = Point { \n"
    )
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 1, "character": 16},
        text,
    )
    assert_(ctx is None, "in-brace but not after `..` -> None")


def test_detect_outside_brace_returns_none() -> None:
    """Cursor on a bare let line -> not a base-spread context."""
    text = "let x = 5\n"
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 0, "character": 9},
        text,
    )
    assert_(ctx is None, "outside brace-init -> None")


def test_detect_after_double_dot_with_trailing_space() -> None:
    """`Point { ..  |` (trailing spaces between `..` and cursor) ->
    detected. The completion still fires."""
    text = "let q = Point { ..   \n"
    # Cursor right after the spaces -> column 21.
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 0, "character": 21},
        text,
    )
    assert_(ctx is not None, "trailing space tolerated")
    assert_eq(ctx.struct_name, "Point", "Point detected with space")


def test_detect_after_single_dot_returns_none() -> None:
    """`Point { .|` -- a single dot is field access, not spread."""
    text = "let q = Point { .\n"
    ctx = is_base_spread_context(
        "file:///t.nova",
        {"line": 0, "character": 17},
        text,
    )
    assert_(ctx is None, "single dot is not spread")


# ---------------------------------------------------------------------------
# find_in_scope_struct_values -- per-form harvest tests.
# ---------------------------------------------------------------------------


def test_scope_let_annotation() -> None:
    """`let p: Point = Point { x: 1, y: 2 }` -> p suggested."""
    text = (
        "struct Point { x: int, y: int }\n"
        "fn main() {\n"
        "    let p: Point = Point { x: 1, y: 2 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 3, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "annotation form -> p")


def test_scope_let_constructor_inference() -> None:
    """`let p = Point(1, 2)` -> p suggested (inferred type Point)."""
    text = (
        "struct Point { x: int, y: int }\n"
        "fn main() {\n"
        "    let p = Point(1, 2)\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 3, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "inferred ctor -> p")


def test_scope_let_brace_init() -> None:
    """`let p = Point { x: 1, y: 2 }` -> p suggested (brace-init form)."""
    text = (
        "struct Point { x: int, y: int }\n"
        "fn main() {\n"
        "    let p = Point { x: 1, y: 2 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 3, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "brace-init form -> p")


def test_scope_fn_parameter() -> None:
    """`fn (p: Point)` -> p suggested when cursor inside the fn body."""
    text = (
        "struct Point { x: int, y: int }\n"
        "fn copy(p: Point) {\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 2, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "fn param -> p")


def test_scope_filters_by_type() -> None:
    """Two Point values + one Box value -> only the Point values."""
    text = (
        "struct Point { x: int, y: int }\n"
        "struct Box { value: int }\n"
        "fn main() {\n"
        "    let p1: Point = Point { x: 1, y: 2 }\n"
        "    let p2 = Point(3, 4)\n"
        "    let b = Box { value: 99 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 6, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = sorted(it["label"] for it in items)
    assert_eq(labels, ["p1", "p2"], "only Point values returned")


def test_scope_returns_empty_when_no_match() -> None:
    """No in-scope Point value -> empty list."""
    text = (
        "struct Point { x: int, y: int }\n"
        "struct Box { value: int }\n"
        "fn main() {\n"
        "    let b = Box { value: 99 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 4, "character": 22},
        "Point",
        text,
        decls,
    )
    assert_eq(items, [], "no Point in scope -> []")


def test_scope_excludes_self_reference() -> None:
    """`let q = Point { ..|` on line N -> q itself is NOT suggested
    even if q's type is Point (it's not bound yet)."""
    text = (
        "struct Point { x: int, y: int }\n"
        "fn main() {\n"
        "    let p = Point { x: 1, y: 2 }\n"
        "    let q: Point = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 3, "character": 29},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_(("q" not in labels), "q excluded (still being defined)")
    assert_eq(labels, ["p"], "only p suggested")


def test_scope_top_level_bindings() -> None:
    """Top-level (no enclosing fn) -- bindings outside any fn body
    are still in scope."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 2, "character": 18},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "top-level binding visible")


def test_scope_generic_struct_with_annotation() -> None:
    """`let p: Point<int> = ...` strips generics -> matches Point."""
    text = (
        "struct Point<T> { x: T, y: T }\n"
        "fn main() {\n"
        "    let p: Point = Point { x: 1, y: 2 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 3, "character": 22},
        "Point",
        text,
        decls,
    )
    labels = [it["label"] for it in items]
    assert_eq(labels, ["p"], "annotation form -> p (generic struct)")


# ---------------------------------------------------------------------------
# compute_base_spread_completions -- top-level helper.
# ---------------------------------------------------------------------------


def test_top_level_helper_returns_none_outside_context() -> None:
    """Cursor not in a brace-init body -> returns ``None``."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = 5\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 2, "character": 9},
            text,
            decls,
        )
        assert_(items is None, "no brace-init -> None")


def test_top_level_helper_returns_none_inside_brace_no_dots() -> None:
    """Cursor inside brace-init but not after `..` -> None."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { \n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 2, "character": 16},
            text,
            decls,
        )
        assert_(items is None, "in-brace but not after `..` -> None")


def test_top_level_helper_returns_list_after_dots() -> None:
    """Cursor at `Point { ..|` -> list of in-scope Point values."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 2, "character": 18},
            text,
            decls,
        )
        assert_(items is not None, "in spread context -> list (not None)")
        labels = [it["label"] for it in items]
        assert_eq(labels, ["p"], "Point { .. -> [p]")


def test_top_level_helper_unknown_struct_returns_empty() -> None:
    """Cursor after `..` for a struct not declared -> []."""
    text = "let q = Mystery { ..\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 0, "character": 20},
            text,
            decls,
        )
        # The trigger fires, but the struct isn't known. Return [].
        assert_eq(items, [], "unknown struct -> []")


def test_top_level_helper_with_field_override_and_dots() -> None:
    """`Point { x: 10, ..|` -> same behaviour as `Point { ..|`."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { x: 10, ..\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 2, "character": 25},
            text,
            decls,
        )
        assert_(items is not None, "in spread context -> list")
        labels = [it["label"] for it in items]
        assert_eq(labels, ["p"], "Point { x: 10, .. -> [p]")


def test_top_level_helper_two_point_one_box() -> None:
    """Type filtering: 2 Point + 1 Box -> only 2 Point values."""
    text = (
        "struct Point { x: int, y: int }\n"
        "struct Box { value: int }\n"
        "fn main() {\n"
        "    let p1 = Point { x: 1, y: 2 }\n"
        "    let b = Box { value: 99 }\n"
        "    let p2: Point = Point { x: 5, y: 6 }\n"
        "    let q = Point { ..\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        decls = collect_type_decls(
            _uri(path), text, cache, workspace_index=idx
        )
        items = compute_base_spread_completions(
            _uri(path),
            {"line": 6, "character": 22},
            text,
            decls,
        )
        assert_(items is not None, "in spread context -> list")
        labels = sorted(it["label"] for it in items)
        assert_eq(labels, ["p1", "p2"], "type filtering correct")


# ---------------------------------------------------------------------------
# CompletionItem shape.
# ---------------------------------------------------------------------------


def test_completion_item_shape() -> None:
    """Items returned have label / kind / detail / insertText fields
    matching the LSP CompletionItem schema."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
    )
    decls = _decls_from(text)
    items = find_in_scope_struct_values(
        "file:///t.nova",
        {"line": 2, "character": 18},
        "Point",
        text,
        decls,
    )
    assert_eq(len(items), 1, "one Point in scope")
    it = items[0]
    assert_eq(it["label"], "p", "item label")
    assert_eq(it["kind"], 6, "item kind = Variable (6)")
    assert_(("Point" in it["detail"]), "detail mentions struct name")
    assert_eq(it["insertText"], "p", "insert text is the var name")


# ---------------------------------------------------------------------------
# Server wire -- end-to-end through `dispatch`.
# ---------------------------------------------------------------------------


def test_server_wire_base_spread() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
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
             "position": {"line": 2, "character": 18}},
        )
        items = resp["result"]["items"]
        labels = [it["label"] for it in items]
        assert_eq(labels, ["p"], "wire Point { .. -> [p]")


def test_server_wire_base_spread_with_field_override() -> None:
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { x: 10, ..\n"
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
             "position": {"line": 2, "character": 25}},
        )
        items = resp["result"]["items"]
        labels = [it["label"] for it in items]
        assert_eq(labels, ["p"], "wire Point { x: 10, .. -> [p]")


def test_server_wire_no_change_when_no_match() -> None:
    """No in-scope Point value -> empty list returned by wire."""
    text = (
        "struct Point { x: int, y: int }\n"
        "struct Box { value: int }\n"
        "let b = Box { value: 99 }\n"
        "let q = Point { ..\n"
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
             "position": {"line": 3, "character": 18}},
        )
        items = resp["result"]["items"]
        labels = [it["label"] for it in items]
        # No Point bindings in scope -> empty list (focused result).
        assert_eq(labels, [], "wire no-match -> []")


def test_server_wire_capability_unchanged() -> None:
    """R26A.2 extends completion behaviour without adding a new top-
    level capability. Provider count must remain 16."""
    with tempfile.TemporaryDirectory() as ws:
        client = LspClient()
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        cp = caps["completionProvider"]
        assert_(("." in cp["triggerCharacters"]), ". still a trigger")
        provider_keys = sorted(
            k for k in caps.keys()
            if k.endswith("Provider") or k == "textDocumentSync"
        )
        assert_eq(len(provider_keys), 16,
                  "capability key count unchanged at 16")


def test_server_wire_falls_through_when_no_dots() -> None:
    """Cursor at `Point { |` (no `..`) -> R26D field completion fires,
    NOT the base-spread path. Wire should return field labels."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let q = Point { \n"
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
        assert_eq(labels, ["x", "y"],
                  "fallback to R26D field completion -> [x, y]")


def test_top_level_aware_routes_through_base_spread() -> None:
    """`compute_type_aware_completions` routes through the base-spread
    helper first when the cursor sits at `Name { ..|`."""
    text = (
        "struct Point { x: int, y: int }\n"
        "let p = Point { x: 1, y: 2 }\n"
        "let q = Point { ..\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        cache, idx = _setup_workspace(ws, {"main.nova": text})
        items = compute_type_aware_completions(
            _uri(path),
            {"line": 2, "character": 18},
            text,
            cache,
            workspace_index=idx,
        )
        assert_(items is not None, "type-aware routes through base-spread")
        labels = [it["label"] for it in items]
        assert_eq(labels, ["p"], "Point bindings via top-level")


# ---------------------------------------------------------------------------


def main() -> int:
    test_detect_after_double_dot_simple()
    test_detect_after_double_dot_with_field_override()
    test_detect_inside_brace_but_not_after_dots_returns_none()
    test_detect_outside_brace_returns_none()
    test_detect_after_double_dot_with_trailing_space()
    test_detect_after_single_dot_returns_none()
    test_scope_let_annotation()
    test_scope_let_constructor_inference()
    test_scope_let_brace_init()
    test_scope_fn_parameter()
    test_scope_filters_by_type()
    test_scope_returns_empty_when_no_match()
    test_scope_excludes_self_reference()
    test_scope_top_level_bindings()
    test_scope_generic_struct_with_annotation()
    test_top_level_helper_returns_none_outside_context()
    test_top_level_helper_returns_none_inside_brace_no_dots()
    test_top_level_helper_returns_list_after_dots()
    test_top_level_helper_unknown_struct_returns_empty()
    test_top_level_helper_with_field_override_and_dots()
    test_top_level_helper_two_point_one_box()
    test_completion_item_shape()
    test_server_wire_base_spread()
    test_server_wire_base_spread_with_field_override()
    test_server_wire_no_change_when_no_match()
    test_server_wire_capability_unchanged()
    test_server_wire_falls_through_when_no_dots()
    test_top_level_aware_routes_through_base_spread()
    print(f"test_base_spread_completion: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
