"""Tests for `textDocument/hover` `///` doc-comment surfacing.

Covers:

  * Symbol with no doc comment -> hover returns just the signature.
  * Symbol with a single `/// hello world` line -> docs = "hello world".
  * Multi-line doc comment -> line breaks preserved as `\\n`.
  * Markdown inside the doc (bullets, code blocks) -> preserved verbatim.
  * Doc above `fn`, `let`, `const`, `type` (where applicable to current
    syntax) — verifies the extractor is declaration-keyword agnostic.
  * Cross-file: hover on `foo` in file A where `foo` is declared in
    file B -> docs come from B's file.
  * Stop rule: the doc block stops at the first non-`///` non-blank
    line, AND at the first blank line.
  * Plain `//` (single-slash) comments are NOT picked up.
  * Indented `///` lines are picked up (`    /// foo` -> "foo").
  * `///` with no following space is preserved (`///hello` -> "hello").
  * Empty `///` line becomes an empty markdown line (paragraph break).
  * The whole hover payload is markdown with a fenced `nova` code block
    plus the docs separated by an `---` horizontal rule.
  * Integration: a NOVA source file in `/home/user/NOVA/src/` with a
    `///` doc inserted above a real fn -> hovering at a call site in a
    sibling file returns the expected docs.
  * Server capability count is unchanged (11 capabilities).
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

from _harness import LspClient
from nova_lsp.hover_docs import (
    collect_doc_lines,
    extract_doc_comment,
    extract_doc_comment_from_text,
    render_hover_markdown,
)
from nova_lsp.server import server_capabilities


_assertions = 0


def assert_(cond, label: str) -> None:
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label: str) -> None:
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


def assert_contains(haystack: str, needle: str, label: str) -> None:
    global _assertions
    _assertions += 1
    assert needle in haystack, (
        f"FAIL [{label}]: expected {needle!r} in {haystack!r}"
    )


# ---------------------------------------------------------------------------
# Unit tests for hover_docs.extract_doc_comment_from_text.
# ---------------------------------------------------------------------------


def test_no_doc_returns_empty() -> None:
    text = "fn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text, 0), "",
              "no doc above first-line fn returns empty")
    text2 = "// plain comment\nfn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text2, 1), "",
              "plain // is not a doc comment")


def test_single_line_doc() -> None:
    text = "/// hello world\nfn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text, 1), "hello world",
              "single-line /// docs extracted")


def test_multi_line_doc() -> None:
    text = (
        "/// Line one.\n"
        "/// Line two.\n"
        "/// Line three.\n"
        "fn foo() {}\n"
    )
    assert_eq(extract_doc_comment_from_text(text, 3),
              "Line one.\nLine two.\nLine three.",
              "multi-line doc preserves line breaks")


def test_markdown_preserved() -> None:
    text = (
        "/// Examples:\n"
        "///   - bullet one\n"
        "///   - bullet two\n"
        "/// ```\n"
        "/// fib(10) == 55\n"
        "/// ```\n"
        "fn fib(n) {}\n"
    )
    docs = extract_doc_comment_from_text(text, 6)
    assert_contains(docs, "- bullet one", "bullet preserved")
    assert_contains(docs, "- bullet two", "second bullet preserved")
    assert_contains(docs, "```", "code-fence preserved")
    assert_contains(docs, "fib(10) == 55", "code-block content preserved")
    # Verify the leading two-space indentation under "Examples:" is
    # preserved (markdown bullet indentation matters).
    assert_contains(docs, "  - bullet one", "bullet indentation preserved")


def test_stop_rule_blank_line() -> None:
    text = (
        "/// header doc, NOT for foo\n"
        "\n"
        "/// real doc for foo\n"
        "fn foo() {}\n"
    )
    # Stop-at-blank: only the line immediately above (after no blank)
    # is taken; the doc block separated by the blank is ignored.
    assert_eq(extract_doc_comment_from_text(text, 3), "real doc for foo",
              "blank line terminates doc-block walk")


def test_stop_rule_plain_comment() -> None:
    text = (
        "/// header doc\n"
        "// plain comment in the middle\n"
        "/// real doc\n"
        "fn foo() {}\n"
    )
    assert_eq(extract_doc_comment_from_text(text, 3), "real doc",
              "plain // terminates the walk")


def test_stop_rule_code_line() -> None:
    text = (
        "/// docs for previous fn\n"
        "fn other() {}\n"
        "/// docs for this fn\n"
        "fn foo() {}\n"
    )
    assert_eq(extract_doc_comment_from_text(text, 3), "docs for this fn",
              "fn declaration terminates the walk")


def test_empty_doc_line_becomes_blank() -> None:
    text = (
        "/// paragraph one\n"
        "///\n"
        "/// paragraph two\n"
        "fn foo() {}\n"
    )
    docs = extract_doc_comment_from_text(text, 3)
    assert_eq(docs, "paragraph one\n\nparagraph two",
              "empty /// becomes blank line in markdown")


def test_indented_doc_lines() -> None:
    # A `///` comment indented by 4 spaces should still be recognised.
    text = (
        "fn outer() {\n"
        "    /// inner doc\n"
        "    fn inner() {}\n"
        "}\n"
    )
    assert_eq(extract_doc_comment_from_text(text, 2), "inner doc",
              "indented /// still recognised")


def test_one_space_strip() -> None:
    # `///hello` (no space) should produce "hello" — no space to consume.
    text = "///hello\nfn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text, 1), "hello",
              "///no-space preserved")
    # `///  hello` (two spaces) should produce " hello" — only one
    # consumed, so the extra indent is preserved (markdown significance).
    text2 = "///  hello\nfn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text2, 1), " hello",
              "/// only consumes one space")


def test_four_slash_not_doc() -> None:
    # `////` is a visual divider, not a doc comment.
    text = (
        "////////////////\n"
        "/// real doc\n"
        "fn foo() {}\n"
    )
    docs = extract_doc_comment_from_text(text, 2)
    assert_eq(docs, "real doc", "//// divider is not a doc comment")


def test_top_of_file_no_off_by_one() -> None:
    text = "/// at line zero\nfn foo() {}\n"
    # def_line=1, doc on line 0 — walks back to line 0, picks it up,
    # stops at i=-1.
    assert_eq(extract_doc_comment_from_text(text, 1), "at line zero",
              "walk terminates at beginning of file")


def test_def_line_zero_returns_empty() -> None:
    # No line above line 0 to walk — empty result.
    text = "fn foo() {}\n"
    assert_eq(extract_doc_comment_from_text(text, 0), "",
              "def_line=0 returns empty (nothing above)")


def test_collect_doc_lines_order() -> None:
    # collect_doc_lines returns in source order (top-to-bottom).
    lines = [
        "/// first",
        "/// second",
        "/// third",
        "fn foo() {}",
    ]
    out = collect_doc_lines(lines, 3)
    assert_eq(out, ["/// first", "/// second", "/// third"],
              "collect_doc_lines preserves source order")


# ---------------------------------------------------------------------------
# Tests for the on-disk extractor (extract_doc_comment).
# ---------------------------------------------------------------------------


def test_extract_from_disk() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "hello.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write("/// hello disk\nfn foo() {}\n")
        assert_eq(extract_doc_comment(path, 1), "hello disk",
                  "on-disk extract works")


def test_extract_missing_file_empty() -> None:
    assert_eq(extract_doc_comment("/nonexistent/__nope__.nova", 1), "",
              "missing file returns empty")


# ---------------------------------------------------------------------------
# Tests for the markdown renderer.
# ---------------------------------------------------------------------------


def test_render_no_docs_signature_only() -> None:
    md = render_hover_markdown("fn foo()", "")
    assert_eq(md, "```nova\nfn foo()\n```",
              "empty docs -> bare fenced code block")


def test_render_with_docs_has_separator() -> None:
    md = render_hover_markdown("fn foo()", "Does the thing.")
    assert_contains(md, "```nova", "fenced code block present")
    assert_contains(md, "fn foo()", "signature present")
    assert_contains(md, "---", "horizontal rule separator present")
    assert_contains(md, "Does the thing.", "docs present")


# ---------------------------------------------------------------------------
# End-to-end: drive `textDocument/hover` through the LSP harness.
# ---------------------------------------------------------------------------


def test_hover_single_file_no_docs() -> None:
    """fn with no doc comment -> hover returns just the signature."""
    source = "fn alpha(x) { return x }\nfn main() { alpha(1) }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, source)
        # Hover on `alpha` in the call site (line 1, character 12-13)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 1, "character": 13}},
        )
        result = resp["result"]
        contents = result["contents"]
        assert_eq(contents["kind"], "markdown",
                  "hover content is markdown")
        assert_contains(contents["value"], "fn alpha(x)",
                        "signature in hover")
        assert_("---" not in contents["value"],
                "no doc separator when no docs present")


def test_hover_single_file_with_docs() -> None:
    """fn with /// docs above it -> hover returns sig + docs."""
    source = (
        "/// Doubles a number.\n"
        "/// Examples:\n"
        "///   double(3) == 6\n"
        "fn double(x) {\n"
        "    return x + x\n"
        "}\n"
        "fn main() { double(7) }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "d.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, source)
        # Hover on `double` in the call site (line 6 char 14).
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 6, "character": 14}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "fn double(x)", "signature in hover")
        assert_contains(value, "Doubles a number.", "first doc line in hover")
        assert_contains(value, "Examples:", "second doc line in hover")
        assert_contains(value, "double(3) == 6", "third doc line in hover")
        assert_contains(value, "---", "doc separator present")


def test_hover_let_with_docs() -> None:
    source = (
        "/// The mathematical constant pi.\n"
        "let PI = 3\n"
        "fn area(r) { return PI * r * r }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "math.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, source)
        # Hover on `PI` in `area`'s body (line 2, character 21)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 2, "character": 21}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "let PI", "let signature in hover")
        assert_contains(value, "mathematical constant pi",
                        "let doc in hover")


def test_hover_cross_file_docs() -> None:
    """Doc on declaration in B.nova surfaces when hovering in A.nova."""
    src_a = (
        'import "./b.nova"\n'
        'fn main() {\n'
        '    helper(1)\n'
        '}\n'
    )
    src_b = (
        "/// Helper does the actual work.\n"
        "/// Returns 2x the input.\n"
        "fn helper(x) {\n"
        "    return x + x\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path_a = os.path.join(ws, "a.nova")
        path_b = os.path.join(ws, "b.nova")
        with open(path_a, "w", encoding="utf-8") as f:
            f.write(src_a)
        with open(path_b, "w", encoding="utf-8") as f:
            f.write(src_b)
        client = LspClient()
        client.initialize(ws)
        uri_a = client.open(path_a, src_a)
        # Hover on `helper` in `a.nova` (line 2, character 6)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri_a}, "position": {"line": 2, "character": 6}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "fn helper(x)",
                        "cross-file signature in hover")
        assert_contains(value, "Helper does the actual work.",
                        "cross-file docs line 1")
        assert_contains(value, "Returns 2x the input.",
                        "cross-file docs line 2")


def test_hover_builtin_no_docs() -> None:
    """Hover over a builtin like `println` shows signature only."""
    source = 'fn main() { println("hi") }\n'
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "p.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, source)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 14}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "println", "builtin name in hover")
        assert_contains(value, "(builtin)", "builtin marker in hover")
        # Builtin has no source line; no docs separator.
        assert_("---" not in value, "no doc separator for builtin")


def test_hover_unknown_symbol_returns_none() -> None:
    source = "fn main() { something_undefined() }\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "u.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, source)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 15}},
        )
        # Unknown symbol returns None (hover is empty).
        assert_eq(resp["result"], None,
                  "unknown identifier returns no hover")


def test_hover_doc_with_live_buffer_overrides_disk() -> None:
    """Editing the doc in the buffer is reflected immediately, even
    before the user saves to disk."""
    initial = (
        "/// initial doc text\n"
        "fn target() { return 1 }\n"
    )
    edited = (
        "/// edited doc text\n"
        "/// extra line\n"
        "fn target() { return 1 }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "buf.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(initial)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, initial)
        # didChange to edited content (full document sync).
        client.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": edited}],
        })
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 2, "character": 4}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "edited doc text",
                        "live buffer doc edits reflected in hover")
        assert_contains(value, "extra line",
                        "live buffer second line reflected")
        assert_("initial doc text" not in value,
                "stale on-disk doc not surfaced over live buffer")


# ---------------------------------------------------------------------------
# Integration: real NOVA source.
#
# Add a `///` doc above a fn in a real-codebase-like file and verify
# hovering at a call site in a sibling file surfaces it. We don't
# touch real src/ files — the test creates a self-contained 2-file
# project that exercises the cross-file import-graph path the same
# way the production codebase does.
# ---------------------------------------------------------------------------


def test_integration_realistic_two_file_project() -> None:
    util = (
        "/// Returns the n-th Fibonacci number.\n"
        "/// Time complexity: O(2^n) (naive recursion).\n"
        "/// Examples:\n"
        "///   fib(0) == 0\n"
        "///   fib(10) == 55\n"
        "fn fib(n) {\n"
        "    if n < 2 { return n }\n"
        "    return fib(n - 1) + fib(n - 2)\n"
        "}\n"
    )
    main = (
        'import "./util.nova"\n'
        '\n'
        'fn main() {\n'
        '    let x = fib(10)\n'
        '    println(x)\n'
        '}\n'
    )
    with tempfile.TemporaryDirectory() as ws:
        upath = os.path.join(ws, "util.nova")
        mpath = os.path.join(ws, "main.nova")
        with open(upath, "w", encoding="utf-8") as f:
            f.write(util)
        with open(mpath, "w", encoding="utf-8") as f:
            f.write(main)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(mpath, main)
        # Hover on `fib` at the call site (line 3, character 13)
        resp = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 3, "character": 13}},
        )
        value = resp["result"]["contents"]["value"]
        assert_contains(value, "fn fib(n)",
                        "integration: signature in hover")
        assert_contains(value, "Returns the n-th Fibonacci number.",
                        "integration: doc line 1")
        assert_contains(value, "Time complexity",
                        "integration: doc line 2")
        assert_contains(value, "fib(10) == 55",
                        "integration: example preserved")


# ---------------------------------------------------------------------------
# Capability count: hover is enhanced, not added as a new capability.
# ---------------------------------------------------------------------------


def test_capability_count_unchanged() -> None:
    caps = server_capabilities()
    # Hover is an enhancement to the existing `hoverProvider`, NOT a
    # new capability. The expected key set grows only when a new LSP
    # capability is wired up (e.g. R15F added `callHierarchyProvider`,
    # R16C added `inlayHintProvider`, R18F added `codeLensProvider`,
    # R19F added `typeHierarchyProvider`, R22C added
    # `foldingRangeProvider` + `documentSymbolProvider`).
    # `semanticTokensProvider` advertises both /full and /range under a
    # single provider key.
    expected_keys = {
        "textDocumentSync",
        "hoverProvider",
        "completionProvider",
        "definitionProvider",
        "renameProvider",
        "referencesProvider",
        "codeActionProvider",
        "workspaceSymbolProvider",
        "semanticTokensProvider",
        "callHierarchyProvider",
        "inlayHintProvider",
        "codeLensProvider",
        "typeHierarchyProvider",
        "foldingRangeProvider",
        "documentSymbolProvider",
        "diagnosticProvider",
    }
    assert_eq(set(caps.keys()), expected_keys,
              "server_capabilities returns the expected provider key set")
    # Hover is still advertised as a plain boolean (no schema change).
    assert_eq(caps["hoverProvider"], True,
              "hoverProvider remains True")
    # Semantic tokens still advertises both range + full.
    assert_eq(caps["semanticTokensProvider"]["range"], True,
              "semanticTokens /range still advertised")
    assert_eq(caps["semanticTokensProvider"]["full"], True,
              "semanticTokens /full still advertised")


# ---------------------------------------------------------------------------


def main() -> int:
    test_no_doc_returns_empty()
    test_single_line_doc()
    test_multi_line_doc()
    test_markdown_preserved()
    test_stop_rule_blank_line()
    test_stop_rule_plain_comment()
    test_stop_rule_code_line()
    test_empty_doc_line_becomes_blank()
    test_indented_doc_lines()
    test_one_space_strip()
    test_four_slash_not_doc()
    test_top_of_file_no_off_by_one()
    test_def_line_zero_returns_empty()
    test_collect_doc_lines_order()
    test_extract_from_disk()
    test_extract_missing_file_empty()
    test_render_no_docs_signature_only()
    test_render_with_docs_has_separator()
    test_hover_single_file_no_docs()
    test_hover_single_file_with_docs()
    test_hover_let_with_docs()
    test_hover_cross_file_docs()
    test_hover_builtin_no_docs()
    test_hover_unknown_symbol_returns_none()
    test_hover_doc_with_live_buffer_overrides_disk()
    test_integration_realistic_two_file_project()
    test_capability_count_unchanged()
    print(f"test_hover_docs: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
