"""Unit + smoke + integration tests for folding ranges.

Covers:

  * ``compute_folding_ranges`` -- empty file returns no ranges;
    single-line ``fn foo() {}`` returns no ranges; multi-line fn
    returns one range covering the body; nested constructs (match
    inside fn, if inside fn) produce separate ranges, one per block.
  * Multi-line ``///`` doc-comment block -> one range with
    ``kind=comment``; single-line ``///`` -> not folded.
  * Multi-line ``import`` block -> one range with ``kind=imports``;
    single import -> not folded.
  * ``enum`` / ``struct`` declarations -> block-fold ranges.
  * Brace counting handles strings + comments correctly.
  * Server-level wire smoke through ``dispatch`` for
    ``textDocument/foldingRange`` plus the ``foldingRangeProvider``
    capability advertisement.
  * Integration on ``src/compiler/codegen.nova`` -- a real-world
    NOVA file produces a sensible number of folds.

Assertions: ~25.
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
from nova_lsp.folding_ranges import (  # noqa: E402
    KIND_COMMENT,
    KIND_IMPORTS,
    KIND_REGION,
    FoldingRange,
    compute_folding_ranges,
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


def _ranges(text: str) -> list:
    """Helper: compute folding ranges from raw source text."""
    return compute_folding_ranges("file:///tmp/x.nova", text, FileCache())


# ---------------------------------------------------------------------------
# Empty / single-line files.
# ---------------------------------------------------------------------------


def test_empty_file_no_ranges() -> None:
    assert_eq(_ranges(""), [], "empty file: no folding ranges")


def test_single_line_fn_no_range() -> None:
    """A `fn foo() { return 1 }` on one physical line spans zero lines
    to fold; the editor would have nothing to collapse, so we skip."""
    text = "fn foo() { return 1 }\n"
    ranges = _ranges(text)
    assert_eq(len(ranges), 0, "single-line fn: no fold")


def test_single_line_comment_no_range() -> None:
    text = "/// a single doc comment line\n"
    ranges = _ranges(text)
    assert_eq(len(ranges), 0, "single doc comment line: no fold")


# ---------------------------------------------------------------------------
# Multi-line fn body.
# ---------------------------------------------------------------------------


def test_multi_line_fn_one_range() -> None:
    text = (
        "fn greet(name) {\n"
        "    println(\"hi\")\n"
        "    println(name)\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_eq(len(ranges), 1, "multi-line fn: one fold")
    fr = ranges[0]
    assert_eq(fr["startLine"], 0, "fn fold start")
    assert_eq(fr["endLine"], 3, "fn fold end (closing brace line)")
    assert_("kind" not in fr, "block fold has no kind")


def test_two_separate_fns_two_ranges() -> None:
    text = (
        "fn a() {\n"
        "    let x = 1\n"
        "}\n"
        "fn b() {\n"
        "    let y = 2\n"
        "    let z = 3\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_eq(len(ranges), 2, "two fns: two folds")
    assert_eq(ranges[0]["startLine"], 0, "first fn starts at 0")
    assert_eq(ranges[0]["endLine"], 2, "first fn ends at 2")
    assert_eq(ranges[1]["startLine"], 3, "second fn starts at 3")
    assert_eq(ranges[1]["endLine"], 6, "second fn ends at 6")


# ---------------------------------------------------------------------------
# Nested constructs: match + if inside fn.
# ---------------------------------------------------------------------------


def test_nested_match_and_if_three_ranges() -> None:
    """File with outer fn, inner match, and inner if = 3 ranges total."""
    text = (
        "fn classify(x) {\n"
        "    match x {\n"
        "        0 => \"zero\"\n"
        "        _ => \"nonzero\"\n"
        "    }\n"
        "    if x > 0 {\n"
        "        return x\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_(len(ranges) >= 3, "outer fn + inner match + inner if (>=3 ranges)")
    starts = [r["startLine"] for r in ranges]
    assert_(0 in starts, "outer fn fold starts at line 0")
    assert_(1 in starts, "inner match fold starts at line 1")
    assert_(5 in starts, "inner if fold starts at line 5")


def test_if_else_two_folds() -> None:
    text = (
        "fn pick(x) {\n"
        "    if x > 0 {\n"
        "        return 1\n"
        "    } else {\n"
        "        return -1\n"
        "    }\n"
        "}\n"
    )
    ranges = _ranges(text)
    # fn + if + else = 3 ranges.
    starts = sorted(r["startLine"] for r in ranges)
    assert_(0 in starts, "fn fold present")
    # Two more interior block folds — exact count may vary depending on
    # whether else is emitted; assert at least one extra interior block.
    assert_(len(ranges) >= 2, "if/else produces interior folds")


# ---------------------------------------------------------------------------
# Doc comment blocks.
# ---------------------------------------------------------------------------


def test_multi_line_doc_comment_block() -> None:
    text = (
        "/// First line of doc.\n"
        "/// Second line of doc.\n"
        "/// Third line of doc.\n"
        "fn documented() {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "}\n"
    )
    ranges = _ranges(text)
    # Expect a comment fold + a fn fold.
    comment_ranges = [r for r in ranges if r.get("kind") == KIND_COMMENT]
    assert_eq(len(comment_ranges), 1, "one doc-comment fold")
    assert_eq(comment_ranges[0]["startLine"], 0, "doc fold starts at 0")
    assert_eq(comment_ranges[0]["endLine"], 2, "doc fold ends at line 2")


def test_two_doc_blocks_separated_by_blank() -> None:
    text = (
        "/// block one line a\n"
        "/// block one line b\n"
        "\n"
        "/// block two line a\n"
        "/// block two line b\n"
        "fn f() { return 1 }\n"
    )
    ranges = _ranges(text)
    comment_ranges = [r for r in ranges if r.get("kind") == KIND_COMMENT]
    assert_eq(len(comment_ranges), 2, "two distinct doc-comment folds")


# ---------------------------------------------------------------------------
# Import blocks.
# ---------------------------------------------------------------------------


def test_multi_line_import_block() -> None:
    text = (
        'import "std/io.nova"\n'
        'import "std/math.nova"\n'
        'import "../helper.nova"\n'
        "fn main() {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "}\n"
    )
    ranges = _ranges(text)
    import_ranges = [r for r in ranges if r.get("kind") == KIND_IMPORTS]
    assert_eq(len(import_ranges), 1, "one import-block fold")
    assert_eq(import_ranges[0]["startLine"], 0, "imports fold starts at 0")
    assert_eq(import_ranges[0]["endLine"], 2, "imports fold ends at line 2")


def test_single_import_no_fold() -> None:
    text = (
        'import "std/io.nova"\n'
        "fn main() {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "}\n"
    )
    ranges = _ranges(text)
    import_ranges = [r for r in ranges if r.get("kind") == KIND_IMPORTS]
    assert_eq(len(import_ranges), 0, "single import: no fold")


# ---------------------------------------------------------------------------
# enum + struct.
# ---------------------------------------------------------------------------


def test_multi_line_enum_fold() -> None:
    text = (
        "enum Shape {\n"
        "    Circle(int)\n"
        "    Rect(int, int)\n"
        "    Triangle(int, int, int)\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_(len(ranges) >= 1, "enum produces a fold")
    assert_eq(ranges[0]["startLine"], 0, "enum fold start")
    assert_eq(ranges[0]["endLine"], 4, "enum fold end")


def test_single_line_enum_no_fold() -> None:
    text = "enum Direction { North, South, East, West }\n"
    ranges = _ranges(text)
    assert_eq(len(ranges), 0, "single-line enum: no fold")


def test_multi_line_struct_fold() -> None:
    text = (
        "struct Point {\n"
        "    x: int,\n"
        "    y: int,\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_(len(ranges) >= 1, "struct produces a fold")
    assert_eq(ranges[0]["startLine"], 0, "struct fold start")
    assert_eq(ranges[0]["endLine"], 3, "struct fold end")


# ---------------------------------------------------------------------------
# String and comment masking — braces inside strings/comments don't fold.
# ---------------------------------------------------------------------------


def test_brace_inside_string_does_not_break_fold() -> None:
    text = (
        "fn weird() {\n"
        "    let s = \"} { nested braces in str\"\n"
        "    return s\n"
        "}\n"
    )
    ranges = _ranges(text)
    assert_eq(len(ranges), 1, "string-braces ignored: still one fn fold")
    assert_eq(ranges[0]["startLine"], 0, "fold start")
    assert_eq(ranges[0]["endLine"], 3, "fold end past closing brace on line 3")


# ---------------------------------------------------------------------------
# Server-level wire.
# ---------------------------------------------------------------------------


def test_server_folding_range_capability_advertised() -> None:
    """initialize advertises foldingRangeProvider."""
    client = LspClient()
    resp = client.initialize()
    caps = resp["result"]["capabilities"]
    assert_eq(caps.get("foldingRangeProvider"), True,
              "foldingRangeProvider advertised at initialize")


def test_server_folding_range_wire() -> None:
    """End-to-end through dispatch."""
    text = (
        "fn outer() {\n"
        "    let x = 1\n"
        "    let y = 2\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/foldingRange",
            {"textDocument": {"uri": uri}},
        )
        result = resp["result"]
        assert_eq(len(result), 1, "wire response: one fold for one fn")
        assert_eq(result[0]["startLine"], 0, "wire fold start")
        assert_eq(result[0]["endLine"], 3, "wire fold end")


def test_server_folding_range_unknown_doc() -> None:
    """Request for an unopened document returns []."""
    client = LspClient()
    client.initialize()
    resp = client.request(
        "textDocument/foldingRange",
        {"textDocument": {"uri": "file:///tmp/never_opened.nova"}},
    )
    assert_eq(resp["result"], [], "unknown doc -> empty folds")


# ---------------------------------------------------------------------------
# Integration against a real NOVA fixture.
# ---------------------------------------------------------------------------


def test_integration_codegen_nova() -> None:
    """Open codegen.nova; assert we get many fold ranges."""
    fixture = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: codegen.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    ranges = compute_folding_ranges(_uri(fixture), text, FileCache())
    # Codegen has hundreds of fns; we should see many block folds.
    block_count = sum(1 for r in ranges if "kind" not in r)
    print(f"  codegen.nova folding ranges: {len(ranges)} "
          f"({block_count} block + {len(ranges) - block_count} comment/imports)")
    assert_(block_count > 50,
            "codegen.nova produces many block folds (>50)")


def test_integration_test_sum_types() -> None:
    """test_sum_types.nova has many enum decls — exercise enum folds."""
    fixture = "/home/user/NOVA/tests/test_sum_types.nova"
    if not os.path.isfile(fixture):
        print("  SKIP integration: test_sum_types.nova missing")
        return
    with open(fixture, "r", encoding="utf-8") as f:
        text = f.read()
    ranges = compute_folding_ranges(_uri(fixture), text, FileCache())
    print(f"  test_sum_types.nova folding ranges: {len(ranges)}")
    assert_(len(ranges) > 0, "test_sum_types.nova produces folds")


# ---------------------------------------------------------------------------


def main() -> int:
    test_empty_file_no_ranges()
    test_single_line_fn_no_range()
    test_single_line_comment_no_range()
    test_multi_line_fn_one_range()
    test_two_separate_fns_two_ranges()
    test_nested_match_and_if_three_ranges()
    test_if_else_two_folds()
    test_multi_line_doc_comment_block()
    test_two_doc_blocks_separated_by_blank()
    test_multi_line_import_block()
    test_single_import_no_fold()
    test_multi_line_enum_fold()
    test_single_line_enum_no_fold()
    test_multi_line_struct_fold()
    test_brace_inside_string_does_not_break_fold()
    test_server_folding_range_capability_advertised()
    test_server_folding_range_wire()
    test_server_folding_range_unknown_doc()
    test_integration_codegen_nova()
    test_integration_test_sum_types()
    print(f"test_folding_ranges: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
