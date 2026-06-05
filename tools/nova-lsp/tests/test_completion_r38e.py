"""Unit + wire tests for R38E `textDocument/completion`.

Covers:

  * Capability advertisement — `completionProvider` advertises the
    expanded trigger character set + `resolveProvider: false`.
  * Keywords + primitive types — every NOVA keyword + primitive type
    surfaces in the completion list.
  * Snippets — all 8 snippets ship with multi-placeholder
    `${N:placeholder}` text + `insertTextFormat: 2`.
  * In-scope identifiers — variables declared in the enclosing scope
    surface; declared-later vars don't appear in the visible_at
    lookup (forward-decl exclusion is a soft policy).
  * Imported modules — top-level `fn` declarations in imported files
    surface as Function items.
  * Stdlib combinators (R37E) — `list_map`, `list_filter`, etc. appear
    even when the user hasn't explicitly imported `src/stdlib/list.nova`.
  * Context detection — top-level vs let-rhs vs member-access vs
    in-match-arm vs import-path.
  * Fuzzy + prefix matching helpers (used by tests, not by server).
  * Server-level wire tests through `dispatch`.
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
from nova_lsp.completion import (  # noqa: E402
    INSERT_TEXT_PLAIN,
    INSERT_TEXT_SNIPPET,
    KIND_CLASS,
    KIND_FUNCTION,
    KIND_KEYWORD,
    KIND_SNIPPET,
    KIND_VARIABLE,
    NOVA_KEYWORDS,
    NOVA_PRIMITIVE_TYPES,
    NOVA_SNIPPETS,
    NOVA_STDLIB_FUNCTIONS,
    collect_imported_fns,
    compute_r38e_completions,
    detect_context,
    fuzzy_match,
    prefix_match,
    scan_document,
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
    assert actual == expected, (
        f"FAIL [{label}]: expected {expected!r}, got {actual!r}"
    )


def assert_in(needle, haystack, label):
    global _assertions
    _assertions += 1
    assert needle in haystack, (
        f"FAIL [{label}]: {needle!r} not found in {haystack!r}"
    )


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _labels(items):
    return [it["label"] for it in items]


def _by_label(items, label):
    matches = [it for it in items if it["label"] == label]
    return matches


# ---------------------------------------------------------------------------
# Pure-helper tests.
# ---------------------------------------------------------------------------


def test_keywords_list_includes_all_specified() -> None:
    """The R38E brief specifies a minimum keyword set — verify each
    member is present."""
    required = ["fn", "let", "if", "else", "while", "for", "match",
                "return", "import", "enum", "struct", "true", "false"]
    for kw in required:
        assert_in(kw, NOVA_KEYWORDS, f"keyword {kw!r}")


def test_primitive_types_list_includes_floats() -> None:
    """R38A added `float` — confirm it's in the primitive-type list."""
    for t in ("int", "str", "bool", "list", "float"):
        assert_in(t, NOVA_PRIMITIVE_TYPES, f"primitive {t!r}")


def test_snippet_count_at_least_eight() -> None:
    """Brief targets 8+ snippets."""
    assert_(len(NOVA_SNIPPETS) >= 8, "snippet count")


def test_snippet_fn_has_placeholders() -> None:
    """The fn snippet should carry ${1:name}, ${2:params}, ${3:body}."""
    fn_snip = next(s for s in NOVA_SNIPPETS if s.label == "fn")
    assert_in("${1:name}", fn_snip.insert_text, "fn $1:name")
    assert_in("${2:params}", fn_snip.insert_text, "fn $2:params")
    assert_in("${3:body}", fn_snip.insert_text, "fn $3:body")


def test_snippet_match_has_four_placeholders() -> None:
    """match snippet: ${1:expr}, ${2:pat}, ${3:result}, ${4:default}."""
    match_snip = next(s for s in NOVA_SNIPPETS if s.label == "match")
    for ph in ("${1:expr}", "${2:pat}", "${3:result}", "${4:default}"):
        assert_in(ph, match_snip.insert_text, f"match {ph}")


def test_snippet_closure_has_pipes() -> None:
    """closure: |${1:x}| ${2:expr}."""
    snip = next(s for s in NOVA_SNIPPETS if s.label == "closure")
    assert_in("|${1:x}|", snip.insert_text, "closure pipes")
    assert_in("${2:expr}", snip.insert_text, "closure body")


def test_snippet_import_quotes() -> None:
    """import snippet: import \"${1:path}\"."""
    snip = next(s for s in NOVA_SNIPPETS if s.label == "import")
    assert_in('"${1:path}"', snip.insert_text, "import quotes")


def test_stdlib_includes_list_map_filter_fold() -> None:
    """R37E stdlib combinators must be present."""
    for name in ("list_map", "list_filter", "list_fold", "list_take", "list_drop"):
        assert_in(name, NOVA_STDLIB_FUNCTIONS, f"stdlib {name!r}")


def test_stdlib_signature_shape() -> None:
    """Stdlib signatures should start with `fn ` and include the name."""
    sig = NOVA_STDLIB_FUNCTIONS["list_map"]
    assert_in("fn list_map", sig, "list_map signature")


def test_fuzzy_match_case_insensitive() -> None:
    """`lst_m` should fuzzy-match `list_map` (subsequence) and the
    case-insensitive variants."""
    assert_(fuzzy_match("lst_m", "list_map"), "lst_m -> list_map")
    assert_(fuzzy_match("LST", "list_map"), "LST case-insensitive")
    assert_(fuzzy_match("lm", "list_map"), "lm subsequence")
    assert_(not fuzzy_match("xyz", "list_map"), "xyz no match")


def test_fuzzy_match_empty_prefix_matches() -> None:
    """Empty prefix matches everything."""
    assert_(fuzzy_match("", "anything"), "empty prefix")


def test_prefix_match_case_insensitive() -> None:
    """`LIST` should prefix-match `list_map` case-insensitively."""
    assert_(prefix_match("LIST", "list_map"), "LIST prefix")
    assert_(prefix_match("list", "list_map"), "list prefix")
    assert_(not prefix_match("map", "list_map"), "map not prefix")


# ---------------------------------------------------------------------------
# detect_context: pure trigger-detection.
# ---------------------------------------------------------------------------


def test_detect_context_at_top_level() -> None:
    """Empty line cursor -> at_top_level=True."""
    ctx = detect_context("", 0, 0)
    assert_(ctx.at_top_level, "empty line")


def test_detect_context_after_let_rhs() -> None:
    """`let x = ` -> after_let_rhs=True."""
    text = "let x = "
    ctx = detect_context(text, 0, len(text))
    assert_(ctx.after_let_rhs, "after let rhs")


def test_detect_context_after_member_access() -> None:
    """`var.` -> after_member_access=True."""
    text = "let v = box."
    ctx = detect_context(text, 0, len(text))
    assert_(ctx.after_member_access, "after member access")


def test_detect_context_member_access_with_partial() -> None:
    """`var.fi` should still register member access on `var`."""
    text = "let v = box.fi"
    ctx = detect_context(text, 0, len(text))
    assert_(ctx.after_member_access, "member access partial ident")


def test_detect_context_in_import_path() -> None:
    """`import "...` -> in_import_path=True."""
    text = 'import "src/util'
    ctx = detect_context(text, 0, len(text))
    assert_(ctx.in_import_path, "in import path")


def test_detect_context_not_match_when_no_match() -> None:
    """Plain top-level code without match keyword -> in_match_arm=False."""
    text = "let x = 1\nfn foo() {\n  return x\n}"
    ctx = detect_context(text, 2, 4)
    # Inside fn body but no match — should be False.
    assert_(not ctx.in_match_arm, "non-match body")


def test_detect_context_in_match_arm_simple() -> None:
    """Cursor inside `match X { ` should set in_match_arm=True."""
    text = "fn f(x) {\n  match x {\n    "
    # Cursor is on the third line (index 2).
    ctx = detect_context(text, 2, 4)
    assert_(ctx.in_match_arm, "in match arm")


# ---------------------------------------------------------------------------
# scan_document.
# ---------------------------------------------------------------------------


def test_scan_document_finds_fn() -> None:
    """`fn greet(name) {}` should appear in scan.fns."""
    scan = scan_document("fn greet(name) {\n  return name\n}\n")
    assert_eq(len(scan.fns), 1, "single fn")
    assert_eq(scan.fns[0].name, "greet", "fn name")
    assert_eq(scan.fns[0].params, "name", "fn params")
    assert_eq(scan.fns[0].signature, "fn greet(name)", "fn signature")


def test_scan_document_finds_let() -> None:
    """`let TAU = 6` should appear in scan.lets."""
    scan = scan_document("let TAU = 6\n")
    assert_eq(len(scan.lets), 1, "single let")
    assert_eq(scan.lets[0].name, "TAU", "let name")
    assert_eq(scan.lets[0].signature, "let TAU = 6", "let signature")


def test_scan_document_finds_enum_and_struct() -> None:
    """`enum E {}` and `struct S {}` register in their respective lists."""
    scan = scan_document("enum E { A, B }\nstruct S { x: int }\n")
    assert_eq(len(scan.enums), 1, "single enum")
    assert_eq(scan.enums[0].name, "E", "enum name")
    assert_eq(len(scan.structs), 1, "single struct")
    assert_eq(scan.structs[0].name, "S", "struct name")


def test_scan_document_finds_imports() -> None:
    """`import "foo.nova"` lands in scan.imports."""
    scan = scan_document('import "foo.nova"\nimport "bar/baz.nova"\n')
    assert_eq(len(scan.imports), 2, "two imports")
    assert_in("foo.nova", scan.imports, "first import")
    assert_in("bar/baz.nova", scan.imports, "second import")


# ---------------------------------------------------------------------------
# compute_r38e_completions: the main entry point.
# ---------------------------------------------------------------------------


def test_top_level_completions_include_keywords() -> None:
    """Top-level position should surface declaration keywords."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    labels = _labels(items)
    for kw in ("fn", "let", "import", "enum", "struct"):
        assert_in(kw, labels, f"top-level keyword {kw!r}")


def test_top_level_completions_include_primitive_types() -> None:
    """Primitive types should be in the result."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    labels = _labels(items)
    for t in ("int", "str", "bool", "list", "float"):
        assert_in(t, labels, f"primitive {t!r}")


def test_top_level_completions_include_snippets() -> None:
    """At least one item per snippet should appear."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    snippet_items = [it for it in items if it.get("kind") == KIND_SNIPPET]
    snippet_labels = {it["label"] for it in snippet_items}
    for label in ("fn", "let", "if", "match", "while", "import", "enum", "closure"):
        assert_in(label, snippet_labels, f"snippet {label!r}")


def test_snippet_item_has_insert_text_format_2() -> None:
    """Snippet items must declare `insertTextFormat: 2` so the editor
    processes the `${N:placeholder}` markers as tab stops."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    fn_snippets = [
        it for it in items
        if it["label"] == "fn" and it.get("kind") == KIND_SNIPPET
    ]
    assert_eq(len(fn_snippets), 1, "single fn snippet")
    assert_eq(
        fn_snippets[0]["insertTextFormat"],
        INSERT_TEXT_SNIPPET,
        "fn snippet insertTextFormat",
    )


def test_snippet_fn_insertText_has_three_placeholders() -> None:
    """The `fn` snippet's insertText carries 3 placeholders."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    fn_snip = next(
        it for it in items
        if it["label"] == "fn" and it.get("kind") == KIND_SNIPPET
    )
    text = fn_snip["insertText"]
    for ph in ("${1:name}", "${2:params}", "${3:body}"):
        assert_in(ph, text, f"fn snippet {ph}")


def test_keyword_item_kind() -> None:
    """Bare keyword items should carry kind=14 (Keyword)."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    fn_keywords = [
        it for it in items
        if it["label"] == "fn" and it.get("kind") == KIND_KEYWORD
    ]
    assert_eq(len(fn_keywords), 1, "single fn keyword item")
    assert_eq(fn_keywords[0]["kind"], 14, "Keyword kind")


def test_in_scope_function_appears_in_completion() -> None:
    """`fn greet(name)` declared earlier surfaces in the result."""
    src = (
        "fn greet(name) {\n"
        "  return name\n"
        "}\n"
        "fn main() {\n"
        "  let x = \n"
        "}\n"
    )
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text=src,
        doc_path=None,
        # Cursor on `let x = ` line inside main
        position={"line": 4, "character": 10},
    )
    labels = _labels(items)
    assert_in("greet", labels, "in-scope fn `greet`")


def test_in_scope_parameter_appears() -> None:
    """fn parameter is visible inside the body."""
    src = (
        "fn process(payload, count) {\n"
        "  let x = \n"
        "}\n"
    )
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text=src,
        doc_path=None,
        position={"line": 1, "character": 10},
    )
    labels = _labels(items)
    assert_in("payload", labels, "param `payload`")
    assert_in("count", labels, "param `count`")


def test_declared_later_var_excluded() -> None:
    """A `let` declared AFTER the cursor doesn't appear in the
    in-scope list — forward-decl exclusion is a soft policy."""
    src = (
        "fn main() {\n"
        "  let cursor_here = 1\n"
        "  let LATER_VAR = 2\n"
        "}\n"
    )
    # Cursor between the two lets (line 1, column 18 after `= 1`)
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text=src,
        doc_path=None,
        position={"line": 1, "character": 18},
    )
    labels = _labels(items)
    # cursor_here is bound at line 1 — visible_at returns the binding
    # because decl_line <= line (1 <= 1).
    assert_in("cursor_here", labels, "cursor_here visible")
    # LATER_VAR is bound at line 2 — should NOT be visible at line 1.
    assert_(
        "LATER_VAR" not in labels,
        f"LATER_VAR should not be in {labels}",
    )


def test_top_level_let_visible_inside_fn() -> None:
    """Top-level `let TAU = 6` is visible inside every fn body."""
    src = (
        "let TAU = 6\n"
        "fn main() {\n"
        "  let x = \n"
        "}\n"
    )
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text=src,
        doc_path=None,
        position={"line": 2, "character": 10},
    )
    labels = _labels(items)
    assert_in("TAU", labels, "top-level let TAU")


def test_stdlib_combinators_in_completion() -> None:
    """R37E stdlib functions surface regardless of import status."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    labels = _labels(items)
    for name in ("list_map", "list_filter", "list_fold"):
        assert_in(name, labels, f"stdlib {name!r}")


def test_stdlib_completion_has_detail_signature() -> None:
    """Stdlib item's `detail` field carries the signature."""
    items = compute_r38e_completions(
        uri="file:///tmp/a.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    list_map = next(
        it for it in items
        if it["label"] == "list_map"
        and it.get("kind") == KIND_FUNCTION
    )
    assert_in("fn list_map", list_map["detail"], "list_map detail")


def test_imported_fn_appears_in_completion() -> None:
    """Top-level `fn` in an imported file shows up."""
    with tempfile.TemporaryDirectory() as workspace:
        util_path = os.path.join(workspace, "util.nova")
        _write(
            util_path,
            "/// Compute a SHA256 digest.\n"
            "fn sha256(input) {\n  return input\n}\n",
        )
        main_path = os.path.join(workspace, "main.nova")
        main_text = 'import "util.nova"\nfn main() {\n  let x = \n}\n'
        items = compute_r38e_completions(
            uri="file://" + main_path,
            doc_text=main_text,
            doc_path=main_path,
            position={"line": 2, "character": 10},
        )
        labels = _labels(items)
        assert_in("sha256", labels, "imported fn `sha256`")
        # The doc comment ("Compute a SHA256 digest.") should be in
        # the documentation field.
        sha = next(
            it for it in items
            if it["label"] == "sha256" and it.get("kind") == KIND_FUNCTION
        )
        assert_("documentation" in sha, "sha256 has documentation")
        doc_val = sha["documentation"]["value"]
        assert_in("SHA256", doc_val, "doc mentions SHA256")


def test_imported_fn_detail_mentions_source() -> None:
    """Imported fn `detail` includes the source file basename."""
    with tempfile.TemporaryDirectory() as workspace:
        util_path = os.path.join(workspace, "util.nova")
        _write(util_path, "fn helper(x) { return x }\n")
        main_path = os.path.join(workspace, "main.nova")
        main_text = 'import "util.nova"\nfn main() { }\n'
        items = compute_r38e_completions(
            uri="file://" + main_path,
            doc_text=main_text,
            doc_path=main_path,
            position={"line": 1, "character": 12},
        )
        helper = next(it for it in items if it["label"] == "helper")
        assert_in("util.nova", helper["detail"], "helper detail mentions util.nova")


def test_collect_imported_fns_resolves_relative_path() -> None:
    """`import "sub/foo.nova"` resolves against the current file's dir."""
    with tempfile.TemporaryDirectory() as workspace:
        sub_dir = os.path.join(workspace, "sub")
        os.makedirs(sub_dir)
        foo_path = os.path.join(sub_dir, "foo.nova")
        _write(foo_path, "fn fooer(a, b) { }\n")
        main_path = os.path.join(workspace, "main.nova")
        main_text = 'import "sub/foo.nova"\n'
        triples = collect_imported_fns(main_text, main_path)
        assert_eq(len(triples), 1, "single resolved import")
        fn_decl, src_path, _docs = triples[0]
        assert_eq(fn_decl.name, "fooer", "fooer name")
        assert_in("sub/foo.nova", src_path.replace(os.sep, "/"), "fooer source path")


def test_collect_imported_fns_silently_skips_missing_file() -> None:
    """Missing imports don't crash — they're silently dropped."""
    with tempfile.TemporaryDirectory() as workspace:
        main_path = os.path.join(workspace, "main.nova")
        main_text = 'import "nope.nova"\n'
        triples = collect_imported_fns(main_text, main_path)
        assert_eq(len(triples), 0, "missing import skipped")


def test_compute_r38e_completions_does_not_crash_on_empty_doc() -> None:
    """Empty document is a valid edge case — should not raise."""
    items = compute_r38e_completions(
        uri="file:///tmp/empty.nova",
        doc_text="",
        doc_path=None,
        position={"line": 0, "character": 0},
    )
    assert_(len(items) > 0, "non-empty result for empty doc")


def test_enum_declaration_surfaces() -> None:
    """A `enum Color { Red, Blue }` declaration surfaces `Color`."""
    src = "enum Color { Red, Blue }\nfn main() { }\n"
    items = compute_r38e_completions(
        uri="file:///tmp/c.nova",
        doc_text=src,
        doc_path=None,
        position={"line": 1, "character": 12},
    )
    labels = _labels(items)
    assert_in("Color", labels, "user enum `Color`")


def test_struct_declaration_surfaces() -> None:
    """`struct Point { x, y }` surfaces `Point`."""
    src = "struct Point { x, y }\nfn main() { }\n"
    items = compute_r38e_completions(
        uri="file:///tmp/p.nova",
        doc_text=src,
        doc_path=None,
        position={"line": 1, "character": 12},
    )
    labels = _labels(items)
    assert_in("Point", labels, "user struct `Point`")


def test_visible_at_inside_nested_block() -> None:
    """`visible_at` returns bindings from outer + inner block scopes."""
    src = (
        "fn main() {\n"           # 0
        "  let outer = 1\n"        # 1
        "  if outer > 0 {\n"        # 2
        "    let inner = 2\n"      # 3
        "    let here = \n"        # 4 — cursor on this line
        "  }\n"                    # 5
        "}\n"                      # 6
    )
    idx = build_scope_index(src)
    visible = idx.visible_at(4, 10)
    names = {n for n, _kind, _line in visible}
    assert_in("outer", names, "outer visible in inner block")
    assert_in("inner", names, "inner visible in inner block")
    assert_in("main", names, "fn name visible (file scope)")


def test_visible_at_excludes_inner_block_let_from_outer() -> None:
    """Once we leave the inner block, its lets aren't visible."""
    src = (
        "fn main() {\n"
        "  if 1 {\n"
        "    let inner = 2\n"
        "  }\n"
        "  let here = \n"
        "}\n"
    )
    idx = build_scope_index(src)
    visible = idx.visible_at(4, 10)
    names = {n for n, _kind, _line in visible}
    assert_("inner" not in names, "inner not visible outside its block")


# ---------------------------------------------------------------------------
# Wire tests through dispatch.
# ---------------------------------------------------------------------------


def test_capability_advertises_completion_provider() -> None:
    """Initialize response advertises `completionProvider` with new
    trigger characters and `resolveProvider: false`."""
    client = LspClient()
    init = client.initialize()
    caps = init["result"]["capabilities"]
    cp = caps.get("completionProvider")
    assert_(cp is not None, "completionProvider present")
    triggers = cp["triggerCharacters"]
    for t in (".", ":", " ", "("):
        assert_in(t, triggers, f"trigger {t!r}")
    assert_eq(cp["resolveProvider"], False, "resolveProvider false")


def test_wire_completion_returns_keywords() -> None:
    """End-to-end: open a doc, request completion, see `fn` keyword."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "main.nova")
        _write(path, "")
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, "")
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 0},
            },
        )
        items = resp["result"]["items"]
        labels = _labels(items)
        assert_in("fn", labels, "wire: `fn` keyword")
        assert_in("let", labels, "wire: `let` keyword")
        assert_in("import", labels, "wire: `import` keyword")


def test_wire_completion_returns_snippets() -> None:
    """End-to-end: snippet items reach the client."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "main.nova")
        _write(path, "")
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, "")
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 0},
            },
        )
        items = resp["result"]["items"]
        snippet_items = [it for it in items if it.get("kind") == KIND_SNIPPET]
        snippet_labels = {it["label"] for it in snippet_items}
        for label in ("fn", "let", "match", "while", "closure"):
            assert_in(label, snippet_labels, f"wire snippet {label!r}")


def test_wire_completion_in_scope_var_surfaces() -> None:
    """Vars declared earlier appear in the wire response."""
    src = (
        "let TAU = 6\n"
        "fn main() {\n"
        "  let x = \n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "main.nova")
        _write(path, src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, src)
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": 10},
            },
        )
        labels = _labels(resp["result"]["items"])
        assert_in("TAU", labels, "wire TAU visible")
        assert_in("main", labels, "wire main fn visible")


def test_wire_completion_imported_module_fn() -> None:
    """End-to-end imported-fn discovery via `import "util.nova"`."""
    with tempfile.TemporaryDirectory() as workspace:
        util_path = os.path.join(workspace, "util.nova")
        _write(util_path, "fn sha256(x) { return x }\n")
        main_src = 'import "util.nova"\nfn main() { }\n'
        main_path = os.path.join(workspace, "main.nova")
        _write(main_path, main_src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(main_path, main_src)
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 12},
            },
        )
        labels = _labels(resp["result"]["items"])
        assert_in("sha256", labels, "wire imported sha256")


def test_wire_completion_stdlib_combinators() -> None:
    """End-to-end stdlib R37E combinators reach the wire."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "main.nova")
        _write(path, "")
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, "")
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 0},
            },
        )
        labels = _labels(resp["result"]["items"])
        for name in ("list_map", "list_filter", "list_fold"):
            assert_in(name, labels, f"wire stdlib {name!r}")


def test_wire_completion_preserves_legacy_builtins() -> None:
    """R38E enrichment doesn't drop the legacy builtin list — `println`
    still surfaces through the fallback layer."""
    src = (
        "let TAU = 6\n"
        "fn main() {\n"
        "  println\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "main.nova")
        _write(path, src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, src)
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": 4},
            },
        )
        labels = _labels(resp["result"]["items"])
        for builtin in ("println", "len", "list_new", "concat", "str_eq"):
            assert_in(builtin, labels, f"wire builtin {builtin!r}")


def test_wire_completion_type_aware_preempt_for_enum() -> None:
    """`Option::` still triggers the R24E focused list (no R38E
    enrichment)."""
    src = (
        "enum Option { Some, None }\n"
        "fn main() {\n"
        "  let x = Option::\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "m.nova")
        _write(path, src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, src)
        resp = client.request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": 18},
            },
        )
        labels = _labels(resp["result"]["items"])
        assert_in("Some", labels, "Option::Some")
        assert_in("None", labels, "Option::None")
        # When R24E preempts, the R38E keywords are NOT in the list —
        # the focused list replaces the generic one.
        assert_("fn" not in labels, f"R24E preempt drops keywords: {labels}")


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Pure-helper
        test_keywords_list_includes_all_specified,
        test_primitive_types_list_includes_floats,
        test_snippet_count_at_least_eight,
        test_snippet_fn_has_placeholders,
        test_snippet_match_has_four_placeholders,
        test_snippet_closure_has_pipes,
        test_snippet_import_quotes,
        test_stdlib_includes_list_map_filter_fold,
        test_stdlib_signature_shape,
        test_fuzzy_match_case_insensitive,
        test_fuzzy_match_empty_prefix_matches,
        test_prefix_match_case_insensitive,
        # Context detection
        test_detect_context_at_top_level,
        test_detect_context_after_let_rhs,
        test_detect_context_after_member_access,
        test_detect_context_member_access_with_partial,
        test_detect_context_in_import_path,
        test_detect_context_not_match_when_no_match,
        test_detect_context_in_match_arm_simple,
        # Document scan
        test_scan_document_finds_fn,
        test_scan_document_finds_let,
        test_scan_document_finds_enum_and_struct,
        test_scan_document_finds_imports,
        # compute_r38e_completions
        test_top_level_completions_include_keywords,
        test_top_level_completions_include_primitive_types,
        test_top_level_completions_include_snippets,
        test_snippet_item_has_insert_text_format_2,
        test_snippet_fn_insertText_has_three_placeholders,
        test_keyword_item_kind,
        test_in_scope_function_appears_in_completion,
        test_in_scope_parameter_appears,
        test_declared_later_var_excluded,
        test_top_level_let_visible_inside_fn,
        test_stdlib_combinators_in_completion,
        test_stdlib_completion_has_detail_signature,
        test_imported_fn_appears_in_completion,
        test_imported_fn_detail_mentions_source,
        test_collect_imported_fns_resolves_relative_path,
        test_collect_imported_fns_silently_skips_missing_file,
        test_compute_r38e_completions_does_not_crash_on_empty_doc,
        test_enum_declaration_surfaces,
        test_struct_declaration_surfaces,
        test_visible_at_inside_nested_block,
        test_visible_at_excludes_inner_block_let_from_outer,
        # Wire tests
        test_capability_advertises_completion_provider,
        test_wire_completion_returns_keywords,
        test_wire_completion_returns_snippets,
        test_wire_completion_in_scope_var_surfaces,
        test_wire_completion_imported_module_fn,
        test_wire_completion_stdlib_combinators,
        test_wire_completion_preserves_legacy_builtins,
        test_wire_completion_type_aware_preempt_for_enum,
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
            f"test_completion_r38e: FAIL — {failed} test(s) failed",
            file=sys.stderr,
        )
        return 1
    print(f"test_completion_r38e: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
