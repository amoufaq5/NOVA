"""Unit + smoke + integration tests for document symbols.

Covers:

  * ``scan_document_symbols`` -- file with 1 fn -> 1 Function symbol;
    let / const / type -> 1 symbol each; enum with N variants -> 1
    enum symbol with N EnumMember children; struct with M fields -> 1
    struct symbol with M Field children.
  * Selection range: covers ONLY the name token (e.g. ``foo`` in
    ``fn foo(...)``); full range: covers the full block through the
    closing brace.
  * Mixed file: top-level fn + let + const + type = 4 symbols.
  * ALL_CAPS lets surface as ``Constant``, mixed-case as ``Variable``.
  * Empty file returns no symbols.
  * Indented declarations (nested let inside fn body) are not surfaced
    as top-level symbols.
  * Single-line enum / struct.
  * Server-level wire smoke through ``dispatch`` for
    ``textDocument/documentSymbol`` plus the ``documentSymbolProvider``
    capability advertisement.
  * Integration on ``tests/test_sum_types.nova`` -- the R17A reference
    fixture with Option / Result / Shape / Tree enums + their variants.

Assertions: ~30.
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
from nova_lsp.document_symbols import (  # noqa: E402
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_ENUM,
    SYMBOL_KIND_ENUM_MEMBER,
    SYMBOL_KIND_FIELD,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_STRUCT,
    SYMBOL_KIND_TYPE_PARAMETER,
    SYMBOL_KIND_VARIABLE,
    compute_document_symbols,
    scan_document_symbols,
)
from nova_lsp.imports import FileCache  # noqa: E402


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


def _symbols(text: str) -> list:
    """Compute LSP wire-shape document symbols for raw source text."""
    return compute_document_symbols("file:///tmp/x.nova", text, FileCache())


# ---------------------------------------------------------------------------
# Empty / trivial files.
# ---------------------------------------------------------------------------


def test_empty_file_no_symbols() -> None:
    assert_eq(_symbols(""), [], "empty file: no document symbols")


def test_only_comments_no_symbols() -> None:
    text = "// just a comment\n// another comment\n"
    assert_eq(_symbols(text), [], "only comments: no document symbols")


# ---------------------------------------------------------------------------
# Single fn.
# ---------------------------------------------------------------------------


def test_single_fn_one_symbol() -> None:
    text = (
        "fn greet(name) {\n"
        "    println(name)\n"
        "}\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 1, "one fn -> one symbol")
    assert_eq(syms[0]["name"], "greet", "symbol name = greet")
    assert_eq(syms[0]["kind"], SYMBOL_KIND_FUNCTION,
              "fn -> SymbolKind.Function (12)")
    # Selection range: just the name token.
    sel = syms[0]["selectionRange"]
    assert_eq(sel["start"]["line"], 0, "selection start line")
    assert_eq(sel["start"]["character"], 3, "selection start char (after 'fn ')")
    assert_eq(sel["end"]["line"], 0, "selection end line")
    assert_eq(sel["end"]["character"], 8, "selection end char (len('greet')+3)")
    # Full range: through closing brace.
    rng = syms[0]["range"]
    assert_eq(rng["start"]["line"], 0, "full range start line")
    assert_eq(rng["end"]["line"], 2, "full range end line (closing brace line)")


def test_single_line_fn() -> None:
    text = "fn one_liner() { return 1 }\n"
    syms = _symbols(text)
    assert_eq(len(syms), 1, "single-line fn -> one symbol")
    assert_eq(syms[0]["name"], "one_liner", "name = one_liner")


# ---------------------------------------------------------------------------
# Let / const / type.
# ---------------------------------------------------------------------------


def test_let_const_type_each_one_symbol() -> None:
    text = (
        "let counter = 0\n"
        "const VERSION = 42\n"
        "type ID = int\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 3, "3 top-level decls -> 3 symbols")
    assert_eq(syms[0]["name"], "counter", "let name")
    assert_eq(syms[0]["kind"], SYMBOL_KIND_VARIABLE,
              "mixed-case let -> Variable")
    assert_eq(syms[1]["name"], "VERSION", "const name")
    assert_eq(syms[1]["kind"], SYMBOL_KIND_CONSTANT,
              "const -> Constant")
    assert_eq(syms[2]["name"], "ID", "type alias name")
    assert_eq(syms[2]["kind"], SYMBOL_KIND_TYPE_PARAMETER,
              "type alias -> TypeParameter")


def test_all_caps_let_is_constant() -> None:
    text = "let TAU = 6\n"
    syms = _symbols(text)
    assert_eq(len(syms), 1, "ALL_CAPS let -> one symbol")
    assert_eq(syms[0]["kind"], SYMBOL_KIND_CONSTANT,
              "ALL_CAPS let -> SymbolKind.Constant")


# ---------------------------------------------------------------------------
# Mixed file.
# ---------------------------------------------------------------------------


def test_mixed_file_four_top_level_symbols() -> None:
    """Mixed fn + let + const + type = 4 top-level symbols."""
    text = (
        "fn add(a, b) {\n"
        "    return a + b\n"
        "}\n"
        "let counter = 0\n"
        "const VERSION = 42\n"
        "type ID = int\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 4, "mixed file -> 4 top-level symbols")
    kinds = [s["kind"] for s in syms]
    assert_eq(kinds, [SYMBOL_KIND_FUNCTION, SYMBOL_KIND_VARIABLE,
                      SYMBOL_KIND_CONSTANT, SYMBOL_KIND_TYPE_PARAMETER],
              "kinds in source order")


# ---------------------------------------------------------------------------
# Nested let inside fn body — should NOT surface as a top-level symbol.
# ---------------------------------------------------------------------------


def test_indented_let_not_top_level() -> None:
    text = (
        "fn outer() {\n"
        "    let inner = 1\n"
        "}\n"
        "let TOP = 2\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 2, "2 top-level (fn + let), not 3")
    names = [s["name"] for s in syms]
    assert_("inner" not in names, "indented `let` excluded from outline")
    assert_("outer" in names, "fn outer surfaced")
    assert_("TOP" in names, "top-level let surfaced")


# ---------------------------------------------------------------------------
# Enum with variants as children.
# ---------------------------------------------------------------------------


def test_enum_with_three_variants_three_children() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 1, "one enum -> one top-level symbol")
    enum_sym = syms[0]
    assert_eq(enum_sym["name"], "Shape", "enum name")
    assert_eq(enum_sym["kind"], SYMBOL_KIND_ENUM, "enum -> SymbolKind.Enum")
    children = enum_sym.get("children", [])
    assert_eq(len(children), 3, "three variant children")
    variant_names = [c["name"] for c in children]
    assert_eq(variant_names, ["Circle", "Rect", "Triangle"],
              "variants in source order")
    for c in children:
        assert_eq(c["kind"], SYMBOL_KIND_ENUM_MEMBER,
                  f"{c['name']} -> SymbolKind.EnumMember (22)")
    # Selection range for each variant is the variant name.
    circle = children[0]
    assert_(circle["selectionRange"]["end"]["character"] >
            circle["selectionRange"]["start"]["character"],
            "variant selection range non-empty")


def test_single_line_enum_with_variants() -> None:
    text = "enum Dir { North, South, East, West }\n"
    syms = _symbols(text)
    assert_eq(len(syms), 1, "single-line enum -> one symbol")
    enum_sym = syms[0]
    assert_eq(enum_sym["name"], "Dir", "enum name")
    children = enum_sym.get("children", [])
    assert_eq(len(children), 4, "four variants on one line")
    assert_eq(sorted(c["name"] for c in children),
              ["East", "North", "South", "West"],
              "single-line variants")


# ---------------------------------------------------------------------------
# Struct fields as children.
# ---------------------------------------------------------------------------


def test_struct_with_fields_as_children() -> None:
    text = (
        "struct Point {\n"
        "    x: int,\n"
        "    y: int,\n"
        "}\n"
    )
    syms = _symbols(text)
    assert_eq(len(syms), 1, "one struct -> one symbol")
    struct_sym = syms[0]
    assert_eq(struct_sym["name"], "Point", "struct name")
    assert_eq(struct_sym["kind"], SYMBOL_KIND_STRUCT, "struct -> SymbolKind.Struct")
    children = struct_sym.get("children", [])
    assert_eq(len(children), 2, "two field children")
    field_names = [c["name"] for c in children]
    assert_eq(sorted(field_names), ["x", "y"], "field names")
    for c in children:
        assert_eq(c["kind"], SYMBOL_KIND_FIELD,
                  f"{c['name']} -> SymbolKind.Field (8)")


# ---------------------------------------------------------------------------
# Full vs selection range semantics.
# ---------------------------------------------------------------------------


def test_full_range_covers_entire_block() -> None:
    text = (
        "fn body(a, b) {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "    return x + y\n"
        "}\n"
    )
    syms = _symbols(text)
    rng = syms[0]["range"]
    assert_eq(rng["start"]["line"], 0, "range starts at decl line")
    assert_eq(rng["end"]["line"], 4, "range ends at closing brace line")
    assert_eq(rng["start"]["character"], 0,
              "range starts at column 0 of decl line")


def test_selection_range_is_only_name_token() -> None:
    text = "fn precise(a) {\n    return a\n}\n"
    syms = _symbols(text)
    sel = syms[0]["selectionRange"]
    assert_eq(sel["start"]["line"], 0, "selection start line")
    assert_eq(sel["start"]["character"], 3, "selection covers 'precise' start")
    assert_eq(sel["end"]["character"], 10, "selection covers 'precise' end")
    # The text "fn precise" -- 'p' starts at col 3, runs through 'e' at col 9,
    # so the half-open end is col 10.


# ---------------------------------------------------------------------------
# Server-level wire.
# ---------------------------------------------------------------------------


def test_server_document_symbol_capability_advertised() -> None:
    client = LspClient()
    resp = client.initialize()
    caps = resp["result"]["capabilities"]
    assert_eq(caps.get("documentSymbolProvider"), True,
              "documentSymbolProvider advertised")


def test_server_document_symbol_wire() -> None:
    """End-to-end through dispatch — one fn + one enum."""
    text = (
        "fn greet(name) {\n"
        "    println(name)\n"
        "}\n"
        "enum Mood {\n"
        "    Happy,\n"
        "    Sad,\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        result = resp["result"]
        assert_eq(len(result), 2, "wire response: 2 top-level symbols")
        assert_eq(result[0]["name"], "greet", "first symbol is fn")
        assert_eq(result[1]["name"], "Mood", "second symbol is enum")
        mood_children = result[1].get("children", [])
        assert_eq(len(mood_children), 2, "Mood has 2 variant children")


def test_server_document_symbol_unknown_doc() -> None:
    """Request for an unopened doc -> empty list."""
    client = LspClient()
    client.initialize()
    resp = client.request(
        "textDocument/documentSymbol",
        {"textDocument": {"uri": "file:///tmp/nope.nova"}},
    )
    assert_eq(resp["result"], [], "unknown doc -> empty symbols")


# ---------------------------------------------------------------------------
# Integration against R17A's reference fixture.
# ---------------------------------------------------------------------------


def test_integration_test_sum_types() -> None:
    """test_sum_types.nova has Option / Result / Shape / Tree enums.
    Verify the outline surfaces each enum with its variants nested."""
    fixture = "/home/user/NOVA/tests/test_sum_types.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_sum_types.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    syms = compute_document_symbols(_uri(fixture), text, FileCache())
    enum_syms = [s for s in syms if s["kind"] == SYMBOL_KIND_ENUM]
    enum_names = sorted(s["name"] for s in enum_syms)
    print(f"  test_sum_types.nova enums: {enum_names}")
    assert_("Option" in enum_names, "Option enum surfaced")
    assert_("Result" in enum_names, "Result enum surfaced")
    assert_("Shape" in enum_names, "Shape enum surfaced")
    # Option should have 2 variants (Some, None).
    option = [s for s in enum_syms if s["name"] == "Option"][0]
    option_variants = sorted(c["name"] for c in option.get("children", []))
    print(f"  Option variants: {option_variants}")
    assert_eq(option_variants, ["None", "Some"],
              "Option has Some + None variants nested")


def test_integration_codegen_nova_outline() -> None:
    """Open codegen.nova; verify many fn symbols surface."""
    fixture = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: codegen.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    syms = compute_document_symbols(_uri(fixture), text, FileCache())
    fn_count = sum(1 for s in syms if s["kind"] == SYMBOL_KIND_FUNCTION)
    print(f"  codegen.nova outline: {len(syms)} symbols ({fn_count} fns)")
    assert_(fn_count > 50, "codegen.nova has many fn symbols (>50)")


# ---------------------------------------------------------------------------


def main() -> int:
    test_empty_file_no_symbols()
    test_only_comments_no_symbols()
    test_single_fn_one_symbol()
    test_single_line_fn()
    test_let_const_type_each_one_symbol()
    test_all_caps_let_is_constant()
    test_mixed_file_four_top_level_symbols()
    test_indented_let_not_top_level()
    test_enum_with_three_variants_three_children()
    test_single_line_enum_with_variants()
    test_struct_with_fields_as_children()
    test_full_range_covers_entire_block()
    test_selection_range_is_only_name_token()
    test_server_document_symbol_capability_advertised()
    test_server_document_symbol_wire()
    test_server_document_symbol_unknown_doc()
    test_integration_test_sum_types()
    test_integration_codegen_nova_outline()
    print(f"test_document_symbols: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
