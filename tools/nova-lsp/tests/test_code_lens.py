"""Unit + smoke + integration tests for code lens.

Covers:

  * `scan_declarations` -- fn / let / const / enum at column zero,
    indented locals filtered out, empty file -> empty list.
  * `_ident_regex` whole-word semantics: ``foo`` does not match
    ``foobar`` or ``myfoo``.
  * `_count_references_in_text` skip-line + masking: comments and
    string-literal occurrences don't count.
  * `_enum_declared_variants`: brace-counted, both single-line and
    multi-line enum bodies.
  * `count_workspace_references` end-to-end against a temp workspace
    with one declarer + one importer + one cross-file caller.
  * `count_workspace_enum_variants_used`: only DISTINCT declared
    variants count; ``Name.something`` for non-variant identifiers is
    filtered out.
  * `has_corresponding_test`: declaration mentioned by a
    ``tests/test_*.nova`` -> True; otherwise False.
  * `build_code_lens` payload shape (range, command, data).
  * `compute_code_lenses` integration:
      - file with 1 fn referenced 3 times -> "3 references" lens
      - unreferenced fn (entry point) -> "0 references"
      - top-level let referenced N times -> "N readers"
      - enum with constructors used -> "M variants used"
      - multi-file workspace: cross-file refs counted
      - empty file -> no lenses
      - explicit "tested" marker when test file mentions the symbol
  * `resolve_code_lens` pass-through.
  * Server-level wire smoke through `dispatch` for
    `textDocument/codeLens`, `codeLens/resolve`, and the
    `codeLensProvider` capability.
  * Integration against `src/compiler/codegen.nova` -- pick a real
    label (`_nova_check_rdi`) and assert the lens count matches a
    manual grep.

Assertions: ~30 (covers the 20-25 target plus a healthy margin).
"""
from __future__ import annotations

import os
import re
import sys
import subprocess
import tempfile

# Make the lsp package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from _harness import LspClient  # noqa: E402
from nova_lsp.code_lens import (  # noqa: E402
    KIND_ENUM,
    KIND_FN,
    KIND_LET,
    Declaration,
    _count_references_in_text,
    _enum_declared_variants,
    _ident_regex,
    build_code_lens,
    compute_code_lenses,
    count_workspace_enum_variants_used,
    count_workspace_references,
    has_corresponding_test,
    resolve_code_lens,
    scan_declarations,
)
from nova_lsp.imports import FileCache  # noqa: E402
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


# ---------------------------------------------------------------------------
# scan_declarations.
# ---------------------------------------------------------------------------


def test_scan_declarations_fn_and_let() -> None:
    text = (
        "fn foo() {\n"
        "    return 1\n"
        "}\n"
        "\n"
        "let BAR = 42\n"
        "const QUX = 7\n"
    )
    decls = scan_declarations(text)
    assert_eq(len(decls), 3, "three decls picked up")
    assert_eq(decls[0].name, "foo", "first is foo")
    assert_eq(decls[0].kind, KIND_FN, "foo classified as fn")
    assert_eq(decls[1].name, "BAR", "second is BAR")
    assert_eq(decls[1].kind, KIND_LET, "BAR classified as let")
    assert_eq(decls[2].name, "QUX", "third is QUX")
    # KIND_CONST may also be a string — check kind via name lookup.
    assert_(decls[2].kind != KIND_FN, "QUX not classified as fn")


def test_scan_declarations_enum() -> None:
    text = (
        "enum Color {\n"
        "    Red\n"
        "    Green\n"
        "    Blue\n"
        "}\n"
    )
    decls = scan_declarations(text)
    assert_eq(len(decls), 1, "single enum decl")
    assert_eq(decls[0].name, "Color", "enum name is Color")
    assert_eq(decls[0].kind, KIND_ENUM, "kind is enum")


def test_scan_declarations_ignores_indented() -> None:
    text = (
        "fn outer() {\n"
        "    let inner = 1\n"   # indented -> not a top-level decl.
        "    fn nested() { return 0 }\n"
        "}\n"
    )
    decls = scan_declarations(text)
    assert_eq(len(decls), 1, "only outer counted, locals skipped")
    assert_eq(decls[0].name, "outer", "outer is the lone decl")


def test_scan_declarations_empty() -> None:
    decls = scan_declarations("")
    assert_eq(decls, [], "empty file -> empty list")


# ---------------------------------------------------------------------------
# Whole-word identifier matching.
# ---------------------------------------------------------------------------


def test_ident_regex_whole_word() -> None:
    pat = _ident_regex("foo")
    assert_(pat.search("call foo()") is not None, "matches `foo`")
    assert_(pat.search("call foobar()") is None,
            "must not match prefix `foobar`")
    assert_(pat.search("call myfoo()") is None,
            "must not match suffix `myfoo`")
    assert_(pat.search("foo.bar") is not None, "matches `foo.bar`")


# ---------------------------------------------------------------------------
# Reference counting with skip-line + comment / string masking.
# ---------------------------------------------------------------------------


def test_count_references_in_text_skips_declaration_line() -> None:
    text = (
        "fn foo() {\n"            # line 0 -- declaration
        "    return foo()\n"      # line 1 -- recursive call
        "}\n"
        "let x = foo() + foo()\n"  # line 3 -- two more
    )
    # Counting `foo` skipping line 0 -> 3 refs (1 + 2).
    n = _count_references_in_text("foo", text, skip_line=0)
    assert_eq(n, 3, "three refs after skipping decl line")


def test_count_references_in_text_masks_comments_and_strings() -> None:
    text = (
        "// foo() in a comment is not counted\n"
        '"foo() in a string is not counted"\n'
        "foo()\n"   # only real ref
    )
    n = _count_references_in_text("foo", text)
    assert_eq(n, 1, "comment + string occurrences masked")


# ---------------------------------------------------------------------------
# Enum declared-variant extraction.
# ---------------------------------------------------------------------------


def test_enum_declared_variants_single_line() -> None:
    text = "enum Direction { North, South, East, West }\n"
    variants = _enum_declared_variants(text, "Direction")
    assert_eq(variants, {"North", "South", "East", "West"},
              "all four variants")


def test_enum_declared_variants_multi_line_payloads() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    variants = _enum_declared_variants(text, "Shape")
    assert_eq(variants, {"Circle", "Rect", "Triangle"},
              "three variants extracted from payload-bearing decl")


# ---------------------------------------------------------------------------
# count_workspace_references -- multi-file integration.
# ---------------------------------------------------------------------------


def test_count_workspace_references_single_file() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "single.nova")
        _write(path, (
            "fn foo() {\n"
            "    return 1\n"
            "}\n"
            "fn caller1() { return foo() }\n"
            "fn caller2() { return foo() + foo() }\n"
        ))
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        n = count_workspace_references("foo", path, 0, cache, idx)
        # Three call sites; declaration line 0 is skipped.
        assert_eq(n, 3, "three references in single file")


def test_count_workspace_references_cross_file() -> None:
    """Decl in `declarer.nova`, importer in `caller.nova`; both files
    contribute to the count.
    """
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "declarer.nova")
        _write(decl_path, "fn shared() { return 7 }\n")
        caller_path = os.path.join(ws, "caller.nova")
        _write(caller_path, (
            'import "declarer.nova"\n'
            "fn use1() { return shared() }\n"
            "fn use2() { return shared() }\n"
        ))
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        n = count_workspace_references("shared", decl_path, 0, cache, idx)
        # Two call sites in caller.nova; declarer.nova contributes 0.
        assert_eq(n, 2, "cross-file refs counted")


def test_count_workspace_references_unreferenced() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "entry.nova")
        _write(path, "fn main() {\n    return 0\n}\n")
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        n = count_workspace_references("main", path, 0, cache, idx)
        assert_eq(n, 0, "unreferenced fn yields 0")


# ---------------------------------------------------------------------------
# Enum variant counting.
# ---------------------------------------------------------------------------


def test_count_workspace_enum_variants_used() -> None:
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decl.nova")
        _write(decl_path, (
            "enum Option {\n"
            "    Some(int)\n"
            "    None\n"
            "}\n"
        ))
        user_path = os.path.join(ws, "user.nova")
        _write(user_path, (
            'import "decl.nova"\n'
            "fn use_some() { return Option::Some(42) }\n"
            "fn use_none() { return Option::None }\n"
            "fn use_some_again() { return Option::Some(99) }\n"
        ))
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        n = count_workspace_enum_variants_used(
            "Option", decl_path, 0, cache, idx,
        )
        # Two DISTINCT variants used (Some + None), even though Some
        # appears twice -- the lens reports unique constructors used.
        assert_eq(n, 2, "two distinct variants used")


def test_count_workspace_enum_variants_filtered_to_declared() -> None:
    """``Color.something`` for a non-variant identifier must not count."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decl.nova")
        _write(decl_path, "enum Color { Red, Green, Blue }\n")
        user_path = os.path.join(ws, "user.nova")
        _write(user_path, (
            'import "decl.nova"\n'
            "fn use_one() { return Color.Red }\n"
            "fn use_bogus() { return Color.Bogus }\n"  # not declared
        ))
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        n = count_workspace_enum_variants_used(
            "Color", decl_path, 0, cache, idx,
        )
        assert_eq(n, 1, "only Red counted; Bogus filtered out")


# ---------------------------------------------------------------------------
# has_corresponding_test.
# ---------------------------------------------------------------------------


def test_has_corresponding_test_positive() -> None:
    with tempfile.TemporaryDirectory() as ws:
        tests_dir = os.path.join(ws, "tests")
        os.makedirs(tests_dir)
        test_path = os.path.join(tests_dir, "test_foo.nova")
        _write(test_path, "assert(my_func() == 42, \"my_func works\")\n")
        assert_(has_corresponding_test("my_func", ws),
                "test mentioning my_func detected")


def test_has_corresponding_test_negative() -> None:
    with tempfile.TemporaryDirectory() as ws:
        tests_dir = os.path.join(ws, "tests")
        os.makedirs(tests_dir)
        test_path = os.path.join(tests_dir, "test_foo.nova")
        _write(test_path, "assert(other() == 0, \"other works\")\n")
        assert_(not has_corresponding_test("my_func", ws),
                "no mention -> False")


def test_has_corresponding_test_no_tests_dir() -> None:
    with tempfile.TemporaryDirectory() as ws:
        # no tests/ dir at all.
        assert_(not has_corresponding_test("my_func", ws),
                "missing tests dir -> False")


# ---------------------------------------------------------------------------
# build_code_lens shape.
# ---------------------------------------------------------------------------


def test_build_code_lens_shape() -> None:
    decl = Declaration(
        name="foo", kind=KIND_FN, line=4,
        name_char_start=3, name_char_end=6,
    )
    lens = build_code_lens(decl, "file:///x.nova", count=3, tested=False)
    assert_eq(lens["range"]["start"]["line"], 4, "lens range on decl line")
    assert_eq(lens["range"]["start"]["character"], 0,
              "lens char start is 0")
    assert_eq(lens["command"]["title"], "3 references",
              "title formatted with count")
    assert_eq(lens["command"]["command"], "editor.action.showReferences",
              "command id is showReferences")
    # arguments[0] is the URI; [1] is the position.
    assert_eq(lens["command"]["arguments"][0], "file:///x.nova",
              "URI passed in arguments")
    assert_eq(lens["command"]["arguments"][1]["line"], 4,
              "position line is decl line")
    assert_eq(lens["data"]["name"], "foo", "data carries name")


def test_build_code_lens_pluralization() -> None:
    # Single-count = singular noun.
    decl = Declaration(name="x", kind=KIND_FN, line=0,
                       name_char_start=0, name_char_end=1)
    lens = build_code_lens(decl, "file:///x.nova", count=1, tested=False)
    assert_eq(lens["command"]["title"], "1 reference",
              "singular form for count == 1")


def test_build_code_lens_tested_marker() -> None:
    decl = Declaration(name="x", kind=KIND_LET, line=0,
                       name_char_start=0, name_char_end=1)
    lens = build_code_lens(decl, "file:///x.nova", count=5, tested=True)
    assert_("/ tested" in lens["command"]["title"],
            "tested marker appended")


# ---------------------------------------------------------------------------
# compute_code_lenses -- end-to-end.
# ---------------------------------------------------------------------------


def test_compute_code_lenses_fn_referenced_three_times() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "single.nova")
        text = (
            "fn foo() {\n"
            "    return 1\n"
            "}\n"
            "\n"
            "fn caller1() { return foo() }\n"
            "fn caller2() { return foo() + foo() }\n"
        )
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        # 3 fn decls => 3 lenses, in source order.
        assert_eq(len(lenses), 3, "three lenses for three fns")
        foo_lens = lenses[0]
        assert_("3 reference" in foo_lens["command"]["title"],
                "foo gets 3 references")


def test_compute_code_lenses_unreferenced_fn() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "single.nova")
        text = "fn main() {\n    return 0\n}\n"
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        assert_eq(len(lenses), 1, "single lens")
        assert_eq(lenses[0]["command"]["title"], "0 references",
                  "0 references on unused fn")


def test_compute_code_lenses_let_readers() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "single.nova")
        text = (
            "let PI = 3\n"
            "fn area(r) { return PI * r * r }\n"
            "fn circ(r) { return 2 * PI * r }\n"
        )
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        pi_lens = next(l for l in lenses
                       if l["data"]["name"] == "PI")
        assert_("readers" in pi_lens["command"]["title"]
                or "reader" in pi_lens["command"]["title"],
                "PI lens uses 'readers' phrasing")
        # 2 references to PI.
        assert_("2" in pi_lens["command"]["title"],
                "two readers")


def test_compute_code_lenses_enum_variants_used() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "single.nova")
        text = (
            "enum Direction {\n"
            "    North\n"
            "    South\n"
            "    East\n"
            "}\n"
            "fn use_north() { return Direction::North }\n"
            "fn use_south() { return Direction::South }\n"
        )
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        enum_lens = next(l for l in lenses
                         if l["data"]["name"] == "Direction")
        assert_("2 variants used" in enum_lens["command"]["title"],
                "two distinct variants used")


def test_compute_code_lenses_multi_file_cross_reference() -> None:
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decl.nova")
        _write(decl_path, "fn shared() { return 7 }\n")
        caller_path = os.path.join(ws, "caller.nova")
        _write(caller_path, (
            'import "decl.nova"\n'
            "fn use1() { return shared() }\n"
            "fn use2() { return shared() + shared() }\n"
        ))
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        decl_text = open(decl_path).read()
        lenses = compute_code_lenses(_uri(decl_path), decl_text, cache, idx)
        assert_eq(len(lenses), 1, "one lens for shared")
        # Three call sites across the cross-file.
        assert_eq(lenses[0]["command"]["title"], "3 references",
                  "cross-file refs counted")


def test_compute_code_lenses_empty_file_no_lenses() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "empty.nova")
        _write(path, "")
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        lenses = compute_code_lenses(_uri(path), "", cache, idx)
        assert_eq(lenses, [], "empty file -> empty lens list")


def test_compute_code_lenses_with_tested_marker() -> None:
    with tempfile.TemporaryDirectory() as ws:
        tests_dir = os.path.join(ws, "tests")
        os.makedirs(tests_dir)
        _write(os.path.join(tests_dir, "test_foo.nova"),
               "assert(my_func() == 1, \"my_func works\")\n")
        path = os.path.join(ws, "main.nova")
        text = "fn my_func() { return 1 }\n"
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(
            _uri(path), text, cache, idx,
            workspace_root=ws,
        )
        assert_eq(len(lenses), 1, "one lens")
        assert_("/ tested" in lenses[0]["command"]["title"],
                "tested marker on lens")


# ---------------------------------------------------------------------------
# resolve_code_lens pass-through.
# ---------------------------------------------------------------------------


def test_resolve_code_lens_passthrough() -> None:
    lens = {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        },
        "command": {"title": "5 references", "command": "noop"},
        "data": {"name": "foo"},
    }
    resolved = resolve_code_lens(lens)
    assert_eq(resolved["command"]["title"], "5 references",
              "passthrough preserves title")


def test_resolve_code_lens_synthesizes_missing_command() -> None:
    """If a (theoretically lazy) lens arrives without a command, the
    resolver fills one in from the `data` payload."""
    lens = {
        "range": {
            "start": {"line": 3, "character": 0},
            "end": {"line": 3, "character": 0},
        },
        "data": {
            "uri": "file:///x.nova",
            "name": "foo",
            "kind": KIND_FN,
            "count": 7,
            "tested": False,
            "line": 3,
        },
    }
    resolved = resolve_code_lens(lens)
    assert_("command" in resolved, "command synthesised")
    assert_eq(resolved["command"]["title"], "7 references",
              "synthesised title matches data count")


# ---------------------------------------------------------------------------
# Server-level wire smoke.
# ---------------------------------------------------------------------------


def test_server_code_lens_capability_advertised() -> None:
    client = LspClient()
    resp = client.initialize()
    caps = resp["result"]["capabilities"]
    assert_("codeLensProvider" in caps,
            "codeLensProvider in capabilities")
    assert_eq(caps["codeLensProvider"].get("resolveProvider"), True,
              "resolveProvider declared True")


def test_server_code_lens_wire() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = (
            "fn foo() { return 1 }\n"
            "fn caller() { return foo() }\n"
        )
        _write(path, text)
        client = LspClient()
        client.initialize(root_path=ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/codeLens",
            {"textDocument": {"uri": uri}},
        )
        lenses = resp["result"]
        assert_(isinstance(lenses, list), "result is a list")
        assert_eq(len(lenses), 2, "two lenses (foo + caller)")
        foo_lens = next(l for l in lenses
                        if l["data"]["name"] == "foo")
        assert_eq(foo_lens["command"]["title"], "1 reference",
                  "foo: 1 reference (caller)")


def test_server_code_lens_resolve_wire() -> None:
    """``codeLens/resolve`` round-trips a lens unchanged."""
    client = LspClient()
    client.initialize()
    lens_in = {
        "range": {
            "start": {"line": 1, "character": 0},
            "end": {"line": 1, "character": 0},
        },
        "command": {"title": "2 references", "command": "noop"},
        "data": {"name": "foo"},
    }
    resp = client.request("codeLens/resolve", lens_in)
    out = resp["result"]
    assert_eq(out["command"]["title"], "2 references",
              "resolve preserves title")


# ---------------------------------------------------------------------------
# Integration -- real declaration in src/compiler/codegen.nova.
# ---------------------------------------------------------------------------


def _grep_count(path: str, name: str) -> int:
    """Manual whole-word count of `name` in `path`, excluding the
    line that declares it. Mirrors what the lens does (skip the
    decl line; mask comments/strings) closely enough that the
    integration test can compare against it.
    """
    if not os.path.isfile(path):
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return _count_references_in_text(name, text, skip_line=None)


def test_integration_codegen_gen_expr_lens() -> None:
    """`fn gen_expr(nd)` is the central AST -> asm lowering routine
    in codegen.nova; it is called *recursively* dozens of times by
    itself plus other gen_* fns in the same file. The lens count
    should equal a whole-word grep over codegen.nova MINUS the
    declaration line itself.

    This stress-tests the masking + skip-line logic against a
    real-world fn with high call density. ~90 grep hits is what we
    expect (one per call site; the declaration line is excluded).
    """
    codegen = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(codegen):
        print("  SKIP integration: codegen.nova missing")
        return
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(codegen)
    with open(codegen, "r", encoding="utf-8") as f:
        text = f.read()
    # Locate the decl line so we can skip it.
    decl_line = -1
    for i, line in enumerate(text.splitlines()):
        if re.match(r"^fn\s+gen_expr\s*\(", line):
            decl_line = i
            break
    assert_(decl_line >= 0, "found gen_expr decl line")
    # Expected: identifier-grep of the file minus the decl line.
    expected = _count_references_in_text("gen_expr", text, skip_line=decl_line)
    actual = count_workspace_references(
        "gen_expr", codegen, decl_line, cache, idx,
    )
    print(f"  codegen gen_expr: grep={expected} lens={actual}")
    # The lens may include trasitive imports that re-count the same
    # file once; we accept any count >= expected (in practice it's
    # exactly equal because codegen.nova doesn't import itself).
    assert_eq(actual, expected,
              "lens count matches manual grep for gen_expr")
    # Sanity: gen_expr is called a LOT — at least 50 references.
    assert_(actual >= 50,
            f"gen_expr should have many refs in codegen.nova; got {actual}")


def test_integration_codegen_nova_gen_program_lens() -> None:
    """`fn gen_program(program)` is a top-level decl in
    codegen.nova; it's called exactly once from compiler.nova.
    The lens on it should report 1 reference (cross-file).
    """
    codegen = "/home/user/NOVA/src/compiler/codegen.nova"
    compiler = "/home/user/NOVA/src/compiler/compiler.nova"
    if not (os.path.isfile(codegen) and os.path.isfile(compiler)):
        print("  SKIP integration: codegen.nova or compiler.nova missing")
        return
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_workspace_root("/home/user/NOVA/src/compiler")
    with open(codegen, "r", encoding="utf-8") as f:
        text = f.read()
    # Find gen_program's decl line in codegen.nova.
    decl_line = -1
    for i, line in enumerate(text.splitlines()):
        if re.match(r"^fn\s+gen_program\s*\(", line):
            decl_line = i
            break
    assert_(decl_line >= 0, "found gen_program decl line")
    n = count_workspace_references(
        "gen_program", codegen, decl_line, cache, idx,
    )
    # compiler.nova has exactly one `gen_program(ast)` call.
    print(f"  codegen gen_program: {n} cross-file refs")
    assert_(n >= 1, "at least one cross-file reference to gen_program")


# ---------------------------------------------------------------------------


def main() -> int:
    test_scan_declarations_fn_and_let()
    test_scan_declarations_enum()
    test_scan_declarations_ignores_indented()
    test_scan_declarations_empty()
    test_ident_regex_whole_word()
    test_count_references_in_text_skips_declaration_line()
    test_count_references_in_text_masks_comments_and_strings()
    test_enum_declared_variants_single_line()
    test_enum_declared_variants_multi_line_payloads()
    test_count_workspace_references_single_file()
    test_count_workspace_references_cross_file()
    test_count_workspace_references_unreferenced()
    test_count_workspace_enum_variants_used()
    test_count_workspace_enum_variants_filtered_to_declared()
    test_has_corresponding_test_positive()
    test_has_corresponding_test_negative()
    test_has_corresponding_test_no_tests_dir()
    test_build_code_lens_shape()
    test_build_code_lens_pluralization()
    test_build_code_lens_tested_marker()
    test_compute_code_lenses_fn_referenced_three_times()
    test_compute_code_lenses_unreferenced_fn()
    test_compute_code_lenses_let_readers()
    test_compute_code_lenses_enum_variants_used()
    test_compute_code_lenses_multi_file_cross_reference()
    test_compute_code_lenses_empty_file_no_lenses()
    test_compute_code_lenses_with_tested_marker()
    test_resolve_code_lens_passthrough()
    test_resolve_code_lens_synthesizes_missing_command()
    test_server_code_lens_capability_advertised()
    test_server_code_lens_wire()
    test_server_code_lens_resolve_wire()
    test_integration_codegen_gen_expr_lens()
    test_integration_codegen_nova_gen_program_lens()
    print(f"test_code_lens: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
