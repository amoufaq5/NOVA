"""Unit + smoke + integration tests for type hierarchy.

Covers:

  * `scan_type_declarations` — enum / struct / type alias at column zero.
  * `scan_enum_variants` — single-line and multi-line R17A bodies with
    typed payloads.
  * `prepare_type_hierarchy` — cursor on an enum decl, on a type alias,
    on a `Name::Variant` use, on whitespace, on a fn (returns None).
  * `supertypes` — type alias -> RHS base, enum -> empty, variant ->
    parent enum.
  * `subtypes` — enum -> variants, type alias -> aliases pointing back,
    struct/variant -> empty.
  * Cross-file: enum declared in file A, used in file B -> preparing
    on the use in B finds the decl in A.
  * Server-level wire smoke through `dispatch` for all three handlers
    plus the `typeHierarchyProvider` capability advertisement.
  * Integration on `tests/test_sum_types.nova` — R17A's reference enum
    fixture (Option, Result, Shape, Tree).
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
from nova_lsp.type_hierarchy import (  # noqa: E402
    KIND_ENUM,
    KIND_ENUM_MEMBER,
    KIND_STRUCT,
    KIND_TYPE,
    SYMBOL_KIND_ENUM,
    SYMBOL_KIND_ENUM_MEMBER,
    SYMBOL_KIND_STRUCT,
    SYMBOL_KIND_TYPE_PARAMETER,
    build_type_hierarchy_item,
    build_variant_hierarchy_item,
    prepare_type_hierarchy,
    scan_enum_variants,
    scan_type_declarations,
    subtypes,
    supertypes,
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
# scan_type_declarations.
# ---------------------------------------------------------------------------


def test_scan_type_declarations_enum_single_line() -> None:
    text = "enum Direction { North, South, East, West }\n"
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 1, "single enum decl")
    assert_eq(decls[0].name, "Direction", "enum name")
    assert_eq(decls[0].kind, KIND_ENUM, "kind is enum")
    assert_eq(decls[0].line, 0, "decl line")
    assert_eq(decls[0].body_end_line, 0, "single-line body ends on decl line")


def test_scan_type_declarations_enum_multi_line() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 1, "single multi-line enum")
    assert_eq(decls[0].name, "Shape", "enum name")
    assert_eq(decls[0].body_end_line, 4, "multi-line body end_line")


def test_scan_type_declarations_type_alias() -> None:
    text = "type ID = int\n"
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 1, "one type alias")
    assert_eq(decls[0].name, "ID", "alias name")
    assert_eq(decls[0].kind, KIND_TYPE, "kind is type")
    assert_eq(decls[0].alias_rhs, "int", "alias RHS captured")


def test_scan_type_declarations_struct() -> None:
    text = (
        "struct Point {\n"
        "    x: int\n"
        "    y: int\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 1, "one struct")
    assert_eq(decls[0].kind, KIND_STRUCT, "kind is struct")


def test_scan_type_declarations_mixed() -> None:
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "type UserID = int\n"
        "struct Point { x: int, y: int }\n"
    )
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 3, "three top-level type decls")
    names = [d.name for d in decls]
    assert_eq(names, ["Option", "UserID", "Point"], "decls in source order")


def test_scan_type_declarations_skips_indented() -> None:
    text = (
        "fn helper() {\n"
        "    type Local = int\n"
        "}\n"
        "enum TopLevel { A, B }\n"
    )
    decls = scan_type_declarations(text)
    assert_eq(len(decls), 1, "indented decls filtered")
    assert_eq(decls[0].name, "TopLevel", "only TopLevel surfaces")


# ---------------------------------------------------------------------------
# scan_enum_variants.
# ---------------------------------------------------------------------------


def test_scan_enum_variants_multi_line_with_payloads() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    shape_decl = type_decl_by_name(decls, "Shape")
    assert_(shape_decl is not None, "Shape decl found")
    variants = scan_enum_variants(text, shape_decl)
    assert_eq(len(variants), 3, "three variants")
    by_name = {v.name: v for v in variants}
    assert_eq(by_name["Circle"].arity, 1, "Circle has 1 payload field")
    assert_eq(by_name["Rect"].arity, 2, "Rect has 2 payload fields")
    assert_eq(by_name["Triangle"].arity, 3, "Triangle has 3 payload fields")


def test_scan_enum_variants_nullary_variant() -> None:
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
    )
    decls = scan_type_declarations(text)
    option_decl = type_decl_by_name(decls, "Option")
    variants = scan_enum_variants(text, option_decl)
    assert_eq(len(variants), 2, "two variants")
    by_name = {v.name: v for v in variants}
    assert_eq(by_name["Some"].arity, 1, "Some has 1 payload")
    assert_eq(by_name["None"].arity, 0, "None has no payload (nullary)")


# ---------------------------------------------------------------------------
# prepare_type_hierarchy.
# ---------------------------------------------------------------------------


def test_prepare_on_enum_declaration() -> None:
    text = "enum Option {\n    Some(int)\n    None\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on 'O' of `Option`.
        result = prepare_type_hierarchy(_uri(path), 0, 7, text, cache)
        assert_(result is not None, "prepare on enum decl is non-None")
        assert_eq(len(result), 1, "prepare returns single-element list")
        item = result[0]
        assert_eq(item["name"], "Option", "item.name = Option")
        assert_eq(item["kind"], SYMBOL_KIND_ENUM, "item.kind = Enum (10)")
        assert_eq(item["uri"], _uri(path), "item.uri matches")
        assert_eq(item["range"]["start"]["line"], 0, "range start line")
        assert_eq(item["range"]["end"]["line"], 3, "range end line (body close)")


def test_prepare_on_type_alias_declaration() -> None:
    text = "type ID = int\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on 'I' of `ID`.
        result = prepare_type_hierarchy(_uri(path), 0, 5, text, cache)
        assert_(result is not None, "prepare on type alias is non-None")
        item = result[0]
        assert_eq(item["name"], "ID", "item.name = ID")
        assert_eq(item["kind"], SYMBOL_KIND_TYPE_PARAMETER,
                  "type alias kind = TypeParameter")


def test_prepare_on_whitespace_returns_none() -> None:
    text = "enum Foo {\n    A\n    B\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on space (line 1, col 2 -> inside whitespace).
        result = prepare_type_hierarchy(_uri(path), 1, 2, text, cache)
        assert_(result is None, "prepare on whitespace returns None")


def test_prepare_on_fn_name_returns_none() -> None:
    text = (
        "enum Status { Open, Closed }\n"
        "fn handler() { return 1 }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on 'handler' (a fn, not a type).
        result = prepare_type_hierarchy(_uri(path), 1, 4, text, cache)
        assert_(result is None, "prepare on fn name returns None")


def test_prepare_on_enum_use_resolves_decl() -> None:
    """Cursor on a `Name::Variant` use resolves to the enum's decl
    (when on the enum-name token) or to the variant item (when on the
    variant-name token)."""
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
        "fn use() {\n"
        "    return Option::Some(42)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on `Option` in `Option::Some(42)`.
        result = prepare_type_hierarchy(_uri(path), 5, 12, text, cache)
        assert_(result is not None, "prepare on use is non-None")
        assert_eq(result[0]["name"], "Option", "resolved to Option")
        assert_eq(result[0]["kind"], SYMBOL_KIND_ENUM, "kind Enum")
        # Cursor on `Some` (the variant token, columns 19..23 in the call line).
        result2 = prepare_type_hierarchy(_uri(path), 5, 21, text, cache)
        assert_(result2 is not None, "prepare on variant use is non-None")
        item = result2[0]
        assert_eq(item["name"], "Option::Some", "variant name is qualified")
        assert_eq(item["kind"], SYMBOL_KIND_ENUM_MEMBER, "variant kind = EnumMember")


# ---------------------------------------------------------------------------
# supertypes.
# ---------------------------------------------------------------------------


def test_supertypes_of_enum_is_empty() -> None:
    """Enums have no inheritance — supertypes returns empty list."""
    text = "enum Color { Red, Green, Blue }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = supertypes(item, cache)
        assert_eq(result, [], "enum supertypes empty")


def test_supertypes_of_struct_is_empty() -> None:
    text = "struct Point { x: int, y: int }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = supertypes(item, cache)
        assert_eq(result, [], "struct supertypes empty")


def test_supertypes_of_type_alias_returns_rhs() -> None:
    """`type ID = int` supertypes returns the int builtin placeholder."""
    text = "type ID = int\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = supertypes(item, cache)
        assert_eq(len(result), 1, "one supertype")
        assert_eq(result[0]["name"], "int", "supertype is int")


def test_supertypes_of_type_alias_chain() -> None:
    """`type UserID = ID` where `type ID = int` — supertypes resolves to ID."""
    text = "type ID = int\ntype UserID = ID\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        decls = scan_type_declarations(text)
        user_id = type_decl_by_name(decls, "UserID")
        item = build_type_hierarchy_item(user_id, path)
        result = supertypes(item, cache)
        assert_eq(len(result), 1, "one supertype")
        assert_eq(result[0]["name"], "ID", "supertype is ID")
        # ID itself is a type, so kind = TypeParameter.
        assert_eq(result[0]["kind"], SYMBOL_KIND_TYPE_PARAMETER, "ID kind")


def test_supertypes_of_variant_returns_parent_enum() -> None:
    """The supertype of an enum variant is the parent enum."""
    text = "enum Option {\n    Some(int)\n    None\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        decls = scan_type_declarations(text)
        option_decl = type_decl_by_name(decls, "Option")
        variants = scan_enum_variants(text, option_decl)
        some_variant = next(v for v in variants if v.name == "Some")
        item = build_variant_hierarchy_item(some_variant, option_decl, path)
        result = supertypes(item, cache)
        assert_eq(len(result), 1, "variant has single supertype (parent enum)")
        assert_eq(result[0]["name"], "Option", "parent is Option")
        assert_eq(result[0]["kind"], SYMBOL_KIND_ENUM, "parent kind Enum")


# ---------------------------------------------------------------------------
# subtypes.
# ---------------------------------------------------------------------------


def test_subtypes_of_enum_returns_variants() -> None:
    """`subtypes(enum Option)` returns Some + None as variant items."""
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = subtypes(item, cache, idx)
        assert_eq(len(result), 2, "two subtypes")
        names = sorted(r["name"] for r in result)
        assert_eq(names, ["Option::None", "Option::Some"],
                  "variants reported with qualified names")
        for r in result:
            assert_eq(r["kind"], SYMBOL_KIND_ENUM_MEMBER,
                      "variant kind is EnumMember")


def test_subtypes_of_shape_enum_returns_three_variants() -> None:
    """R17A's Shape fixture: Circle, Rect, Triangle."""
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = subtypes(item, cache, idx)
        assert_eq(len(result), 3, "three Shape variants")
        names = sorted(r["name"].split("::")[1] for r in result)
        assert_eq(names, ["Circle", "Rect", "Triangle"], "Shape variants")


def test_subtypes_of_struct_is_empty() -> None:
    text = "struct Point { x: int, y: int }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        decls = scan_type_declarations(text)
        item = build_type_hierarchy_item(decls[0], path)
        result = subtypes(item, cache, idx)
        assert_eq(result, [], "struct has no subtypes")


def test_subtypes_of_type_alias_returns_dependents() -> None:
    """`subtypes(type Base = int)` returns every `type X = Base` alias."""
    text = (
        "type Base = int\n"
        "type Alias1 = Base\n"
        "type Alias2 = Base\n"
        "type Unrelated = str\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        decls = scan_type_declarations(text)
        base_decl = type_decl_by_name(decls, "Base")
        item = build_type_hierarchy_item(base_decl, path)
        result = subtypes(item, cache, idx)
        names = sorted(r["name"] for r in result)
        assert_eq(names, ["Alias1", "Alias2"],
                  "aliases pointing at Base reported as subtypes")


# ---------------------------------------------------------------------------
# Cross-file: enum in file A, used in file B.
# ---------------------------------------------------------------------------


def test_cross_file_use_finds_decl() -> None:
    """Enum declared in `decl.nova`, used in `user.nova` — preparing
    on the use in `user.nova` resolves to the enum in `decl.nova`."""
    decl_text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
    )
    user_text = (
        'import "./decl.nova"\n'
        "fn use() {\n"
        "    return Option::Some(42)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decl.nova")
        user_path = os.path.join(ws, "user.nova")
        _write(decl_path, decl_text)
        _write(user_path, user_text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        # Cursor on `Option` in the use site (line 2 of user.nova).
        result = prepare_type_hierarchy(
            _uri(user_path), 2, 12, user_text, cache,
            workspace_index=idx,
        )
        assert_(result is not None, "cross-file prepare is non-None")
        item = result[0]
        assert_eq(item["name"], "Option", "resolved to Option")
        # URI points to the declaration file, not the use site.
        assert_eq(item["uri"], _uri(decl_path),
                  "uri points to decl.nova, not user.nova")


def test_cross_file_subtypes_round_trip() -> None:
    """Full round trip: prepare on use site, then subtypes returns
    the same variants as if we had prepared on the decl."""
    decl_text = (
        "enum Color {\n"
        "    Red\n"
        "    Green\n"
        "    Blue\n"
        "}\n"
    )
    user_text = (
        'import "./decl.nova"\n'
        "fn pick() {\n"
        "    return Color::Red\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decl.nova")
        user_path = os.path.join(ws, "user.nova")
        _write(decl_path, decl_text)
        _write(user_path, user_text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        # Prepare on use site.
        result = prepare_type_hierarchy(
            _uri(user_path), 2, 12, user_text, cache,
            workspace_index=idx,
        )
        assert_(result is not None, "prepare succeeded cross-file")
        item = result[0]
        # Subtypes via the item -> should return 3 Color variants.
        subs = subtypes(item, cache, idx)
        assert_eq(len(subs), 3, "three Color variants resolved cross-file")
        names = sorted(s["name"].split("::")[1] for s in subs)
        assert_eq(names, ["Blue", "Green", "Red"], "variant names match")


# ---------------------------------------------------------------------------
# Server-level dispatch (wire smoke).
# ---------------------------------------------------------------------------


def test_server_type_hierarchy_capability_advertised() -> None:
    """`typeHierarchyProvider: True` must appear in the init capabilities."""
    client = LspClient()
    init = client.initialize()
    caps = init["result"]["capabilities"]
    assert_eq(caps.get("typeHierarchyProvider"), True,
              "typeHierarchyProvider in capabilities")


def test_server_prepare_type_hierarchy_wire() -> None:
    """End-to-end: open a file, ask for prepareTypeHierarchy on an enum."""
    text = (
        "enum Option {\n"
        "    Some(int)\n"
        "    None\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/prepareTypeHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 7}},
        )
        result = resp["result"]
        assert_(result is not None, "prepareTypeHierarchy returns non-None")
        assert_eq(len(result), 1, "single-element response")
        assert_eq(result[0]["name"], "Option", "item name")
        assert_eq(result[0]["kind"], SYMBOL_KIND_ENUM, "kind Enum")


def test_server_subtypes_wire() -> None:
    """End-to-end: prepare then subtypes on an enum returns variants."""
    text = (
        "enum Status {\n"
        "    Open\n"
        "    Closed\n"
        "    Pending\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        prep = client.request(
            "textDocument/prepareTypeHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 7}},
        )
        item = prep["result"][0]
        resp = client.request(
            "typeHierarchy/subtypes",
            {"item": item},
        )
        result = resp["result"]
        assert_eq(len(result), 3, "three Status variants")
        names = sorted(r["name"].split("::")[1] for r in result)
        assert_eq(names, ["Closed", "Open", "Pending"], "variant names")


def test_server_supertypes_wire() -> None:
    """End-to-end: enum supertypes returns empty list."""
    text = "enum Direction { North, South }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        prep = client.request(
            "textDocument/prepareTypeHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 7}},
        )
        item = prep["result"][0]
        resp = client.request(
            "typeHierarchy/supertypes",
            {"item": item},
        )
        result = resp["result"]
        assert_eq(result, [], "enum supertypes empty over the wire")


def test_server_supertypes_type_alias_wire() -> None:
    """type alias supertypes returns the base type."""
    text = "type ID = int\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        prep = client.request(
            "textDocument/prepareTypeHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 5}},
        )
        item = prep["result"][0]
        resp = client.request(
            "typeHierarchy/supertypes",
            {"item": item},
        )
        result = resp["result"]
        assert_eq(len(result), 1, "one supertype")
        assert_eq(result[0]["name"], "int", "supertype is int builtin")


# ---------------------------------------------------------------------------
# Integration against R17A's test_sum_types.nova.
# ---------------------------------------------------------------------------


def test_integration_test_sum_types_option_navigation() -> None:
    """Open R17A's reference fixture; assert hierarchy navigation
    works end-to-end on the Option, Result, and Shape enums."""
    fixture = "/home/user/NOVA/tests/test_sum_types.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_sum_types.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    decls = scan_type_declarations(text)
    decl_names = sorted(d.name for d in decls)
    print(f"  test_sum_types.nova type decls: {decl_names}")
    # R17A declares Option, Result, Shape, Tree.
    assert_("Option" in decl_names, "Option declared in fixture")
    assert_("Result" in decl_names, "Result declared in fixture")
    assert_("Shape" in decl_names, "Shape declared in fixture")
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(fixture)

    # Option subtypes -> Some + None.
    option_decl = type_decl_by_name(decls, "Option")
    option_item = build_type_hierarchy_item(option_decl, fixture)
    option_variants = subtypes(option_item, cache, idx)
    option_names = sorted(v["name"].split("::")[1] for v in option_variants)
    assert_eq(option_names, ["None", "Some"], "Option variants are Some + None")

    # Shape subtypes -> Circle, Rect, Triangle.
    shape_decl = type_decl_by_name(decls, "Shape")
    shape_item = build_type_hierarchy_item(shape_decl, fixture)
    shape_variants = subtypes(shape_item, cache, idx)
    shape_names = sorted(v["name"].split("::")[1] for v in shape_variants)
    assert_eq(shape_names, ["Circle", "Rect", "Triangle"],
              "Shape variants resolved")

    # Result subtypes -> Ok + Err.
    result_decl = type_decl_by_name(decls, "Result")
    result_item = build_type_hierarchy_item(result_decl, fixture)
    result_variants = subtypes(result_item, cache, idx)
    result_names = sorted(v["name"].split("::")[1] for v in result_variants)
    assert_eq(result_names, ["Err", "Ok"], "Result variants are Ok + Err")

    # Supertypes: all enums return empty.
    assert_eq(supertypes(option_item, cache), [], "Option supertypes empty")
    assert_eq(supertypes(shape_item, cache), [], "Shape supertypes empty")

    # Prepare on the Option decl line works.
    prep = prepare_type_hierarchy(
        _uri(fixture),
        option_decl.line,
        option_decl.name_char_start,
        text,
        cache,
        workspace_index=idx,
    )
    assert_(prep is not None, "prepare on Option succeeds")
    assert_eq(prep[0]["name"], "Option", "prepare resolves to Option")
    print(f"  Option declared at line {option_decl.line}, "
          f"ends at {option_decl.body_end_line}, "
          f"{len(option_variants)} subtype variants")


# ---------------------------------------------------------------------------


def main() -> int:
    test_scan_type_declarations_enum_single_line()
    test_scan_type_declarations_enum_multi_line()
    test_scan_type_declarations_type_alias()
    test_scan_type_declarations_struct()
    test_scan_type_declarations_mixed()
    test_scan_type_declarations_skips_indented()
    test_scan_enum_variants_multi_line_with_payloads()
    test_scan_enum_variants_nullary_variant()
    test_prepare_on_enum_declaration()
    test_prepare_on_type_alias_declaration()
    test_prepare_on_whitespace_returns_none()
    test_prepare_on_fn_name_returns_none()
    test_prepare_on_enum_use_resolves_decl()
    test_supertypes_of_enum_is_empty()
    test_supertypes_of_struct_is_empty()
    test_supertypes_of_type_alias_returns_rhs()
    test_supertypes_of_type_alias_chain()
    test_supertypes_of_variant_returns_parent_enum()
    test_subtypes_of_enum_returns_variants()
    test_subtypes_of_shape_enum_returns_three_variants()
    test_subtypes_of_struct_is_empty()
    test_subtypes_of_type_alias_returns_dependents()
    test_cross_file_use_finds_decl()
    test_cross_file_subtypes_round_trip()
    test_server_type_hierarchy_capability_advertised()
    test_server_prepare_type_hierarchy_wire()
    test_server_subtypes_wire()
    test_server_supertypes_wire()
    test_server_supertypes_type_alias_wire()
    test_integration_test_sum_types_option_navigation()
    print(f"test_type_hierarchy: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
