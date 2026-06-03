"""Unit + smoke + integration tests for inlay hints.

Covers:

  * `_split_param_names` -- bare params, defaults, leading `mut`,
    empty arg list.
  * `_walk_args` -- single arg, multi arg, nested calls, multi-line
    call, mismatched-paren bailout, comments/strings ignored.
  * `_has_explicit_name_label` -- positive & negative cases.
  * `_infer_literal_type` -- int / float / str / bool / nil + non-
    literal RHS returns None.
  * `compute_inlay_hints` end-to-end:
      - imported callee -> hints emitted at correct positions
      - workspace-fallback callee
      - builtins -> no hints (silent)
      - mismatched arg count -> hints capped at min(args, params)
      - nested calls -> outer call's arg positions correct
      - multi-line call -> hint at the right (line, column)
      - viewport range filter applied
      - explicit named arg skipped
      - string/number literal args still annotated
  * Server-level wire smoke through `dispatch` for
    `textDocument/inlayHint` and the `inlayHintProvider` capability.
  * Integration against `src/compiler/codegen.nova` -- assert a known
    call site (`_cg_ht_set(cg_fn_modules, mfn_name, cg_source_file)`)
    receives the right parameter labels.

Assertions: ~30 (a bit over the 20-25 target so we get real coverage).
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
from nova_lsp.inlay_hints import (  # noqa: E402
    INLAY_HINT_KIND_PARAMETER,
    INLAY_HINT_KIND_TYPE,
    _has_explicit_name_label,
    _infer_literal_type,
    _split_param_names,
    _walk_args,
    compute_inlay_hints,
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
# _split_param_names.
# ---------------------------------------------------------------------------


def test_split_param_names_basic() -> None:
    assert_eq(_split_param_names("a, b, c"), ["a", "b", "c"], "three params")
    assert_eq(_split_param_names(""), [], "empty list")
    assert_eq(_split_param_names("   "), [], "whitespace-only list")
    assert_eq(_split_param_names("x"), ["x"], "single param")


def test_split_param_names_with_default_and_mut() -> None:
    # default value clause is stripped.
    assert_eq(_split_param_names("x = 1, y"), ["x", "y"], "default stripped")
    # leading `mut ` modifier is stripped.
    assert_eq(_split_param_names("mut x, y"), ["x", "y"], "mut prefix stripped")


# ---------------------------------------------------------------------------
# _walk_args -- argument-position parsing.
# ---------------------------------------------------------------------------


def test_walk_args_single_line_two_args() -> None:
    # `foo(a, b)` -- the opening `(` is at column 3.
    lines = ["foo(a, b)"]
    args = _walk_args(lines, 0, 3)
    assert_(args is not None, "args found")
    assert_eq(len(args), 2, "two args")
    assert_eq((args[0].line, args[0].char), (0, 4), "arg 0 position")
    assert_eq((args[1].line, args[1].char), (0, 7), "arg 1 position")


def test_walk_args_nested_call() -> None:
    # `foo(bar(c), d)` -- outer paren at column 3.
    lines = ["foo(bar(c), d)"]
    args = _walk_args(lines, 0, 3)
    assert_eq(len(args), 2, "outer call has two args")
    # arg[0] is `bar(c)` -- starts at column 4 ('b' of bar).
    assert_eq((args[0].line, args[0].char), (0, 4), "outer arg 0 starts at bar")
    # arg[1] is `d` -- starts at column 12.
    assert_eq((args[1].line, args[1].char), (0, 12), "outer arg 1 starts at d")


def test_walk_args_multi_line() -> None:
    lines = [
        "foo(",
        "  a,",
        "  b,",
        "  c",
        ")",
    ]
    args = _walk_args(lines, 0, 3)
    assert_eq(len(args), 3, "three args across lines")
    assert_eq((args[0].line, args[0].char), (1, 2), "arg 0 on line 1 col 2")
    assert_eq((args[1].line, args[1].char), (2, 2), "arg 1 on line 2 col 2")
    assert_eq((args[2].line, args[2].char), (3, 2), "arg 2 on line 3 col 2")


def test_walk_args_no_args() -> None:
    lines = ["foo()"]
    args = _walk_args(lines, 0, 3)
    assert_eq(args, [], "empty arg list -> empty result")


def test_walk_args_string_with_comma() -> None:
    # comma inside the string must NOT split the args.
    lines = ['foo("hi, there", 5)']
    args = _walk_args(lines, 0, 3)
    assert_eq(len(args), 2, "two args despite comma in string")
    # arg 0's text is the masked-string version (a `"  "` of equal length).
    assert_(args[0].text.startswith('"'), "arg 0 text begins with a quote")


def test_walk_args_unclosed_returns_none() -> None:
    # Closing `)` never appears -> bailout.
    lines = ["foo(a, b"]
    args = _walk_args(lines, 0, 3)
    assert_(args is None, "unclosed call -> None")


# ---------------------------------------------------------------------------
# _has_explicit_name_label.
# ---------------------------------------------------------------------------


def test_has_explicit_name_label() -> None:
    assert_(_has_explicit_name_label("foo: 1"), "explicit named arg detected")
    assert_(not _has_explicit_name_label("foo"),
            "bare ident is not a named arg")
    assert_(not _has_explicit_name_label("5"),
            "number is not a named arg")
    # `::` is a path separator, not a name label.
    assert_(not _has_explicit_name_label("std::foo"),
            "double-colon path is not a named arg")


# ---------------------------------------------------------------------------
# _infer_literal_type.
# ---------------------------------------------------------------------------


def test_infer_literal_type_basic() -> None:
    assert_eq(_infer_literal_type("5"), "int", "decimal int")
    assert_eq(_infer_literal_type("0x1f"), "int", "hex int")
    assert_eq(_infer_literal_type("3.14"), "float", "float")
    assert_eq(_infer_literal_type('"hi"'), "str", "string")
    assert_eq(_infer_literal_type("true"), "bool", "bool true")
    assert_eq(_infer_literal_type("false"), "bool", "bool false")
    assert_eq(_infer_literal_type("nil"), "nil", "nil")
    # Non-literal RHS returns None.
    assert_eq(_infer_literal_type("a + b"), None, "arithmetic -> None")
    assert_eq(_infer_literal_type("foo()"), None, "call expr -> None")
    assert_eq(_infer_literal_type("some_var"), None, "bare ident -> None")


# ---------------------------------------------------------------------------
# compute_inlay_hints -- end-to-end against a tiny workspace.
# ---------------------------------------------------------------------------


def test_inlay_hints_basic_two_args() -> None:
    """`foo(a, b)` where `foo(x, y)` is defined -> 2 parameter hints."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x, y) {\n    return x + y\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = 'import "decls.nova"\n\nfn main() {\n    foo(1, 2)\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        # Only parameter hints (no `let` here).
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(len(param_hints), 2, "two parameter hints")
        assert_eq(param_hints[0]["label"], "x:", "first hint labels x")
        assert_eq(param_hints[1]["label"], "y:", "second hint labels y")
        # Positions: foo(1, 2)  -- col of `1` is 8, col of `2` is 11.
        assert_eq(param_hints[0]["position"]["line"], 3, "hint 0 on line 3")
        assert_eq(param_hints[0]["position"]["character"], 8,
                  "hint 0 col 8")
        assert_eq(param_hints[1]["position"]["character"], 11,
                  "hint 1 col 11")


def test_inlay_hints_nested_call() -> None:
    """`foo(bar(c), d)` -- the outer hints anchor on `bar(...)`'s
    starting column, and the inner `bar` call also receives hints."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(
            decl_path,
            "fn foo(outer1, outer2) {\n"
            "    return outer1\n"
            "}\n"
            "fn bar(inner1) {\n"
            "    return inner1\n"
            "}\n",
        )
        caller_path = os.path.join(ws, "caller.nova")
        text = (
            'import "decls.nova"\n'
            "\n"
            "fn main() {\n"
            "    foo(bar(c), d)\n"
            "}\n"
        )
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        # Expect 3 total:
        #   foo's outer1 -> at `b` of `bar(`
        #   foo's outer2 -> at `d`
        #   bar's inner1 -> at `c`
        labels = sorted((h["label"], h["position"]["character"])
                        for h in param_hints)
        assert_eq(len(param_hints), 3, "three hints for nested call")
        # outer1 at column 8 (the `b` of `bar`).
        assert_(("outer1:", 8) in labels, "outer1 hint at column 8")
        # outer2 at column 16 (the `d`).
        assert_(("outer2:", 16) in labels, "outer2 hint at column 16")
        # inner1 at column 12 (the `c`).
        assert_(("inner1:", 12) in labels, "inner1 hint at column 12")


def test_inlay_hints_mismatched_arg_count() -> None:
    """Variadic / extra positional args -- emit hints up to
    min(args, params), never more."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x, y) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        # Three args passed, only two declared.
        text = 'import "decls.nova"\n\nfn main() {\n    foo(1, 2, 3)\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(len(param_hints), 2, "hints capped at param count")


def test_inlay_hints_builtin_callee_no_hints() -> None:
    """`println(x)` -- builtin, not a top-level fn, no source location.
    No hints expected; the implementation must NOT crash."""
    with tempfile.TemporaryDirectory() as ws:
        caller_path = os.path.join(ws, "caller.nova")
        text = 'fn main() {\n    println("hi")\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(param_hints, [], "no hints for builtin")


def test_inlay_hints_literal_args_still_emitted() -> None:
    """String / number literal args MUST still get hints -- the
    parameter name is precisely what disambiguates `foo(42, true)`."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path,
               "fn process(count, enable) {\n    return count\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = (
            'import "decls.nova"\n'
            "\n"
            "fn main() {\n"
            "    process(42, true)\n"
            "}\n"
        )
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(len(param_hints), 2, "literals still annotated")
        labels = [h["label"] for h in param_hints]
        assert_eq(labels, ["count:", "enable:"], "literal arg labels")


def test_inlay_hints_named_arg_skipped() -> None:
    """When the source already writes `name: value` we skip the hint
    to avoid the redundant double-label."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x, y) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        # `y` is explicitly named; the second hint should be dropped.
        text = 'import "decls.nova"\n\nfn main() {\n    foo(1, y: 2)\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        labels = [h["label"] for h in hints
                  if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(labels, ["x:"], "only first hint, named arg skipped")


def test_inlay_hints_range_filter() -> None:
    """Range filter -- hints outside the viewport are excluded."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = (
            'import "decls.nova"\n'
            "\n"
            "fn main() {\n"
            "    foo(1)\n"   # line 3 -- inside the viewport
            "    foo(2)\n"   # line 4 -- outside the viewport
            "}\n"
        )
        _write(caller_path, text)
        cache = FileCache()
        # Viewport: only line 3.
        viewport = {
            "start": {"line": 3, "character": 0},
            "end": {"line": 3, "character": 200},
        }
        hints = compute_inlay_hints(_uri(caller_path), viewport, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(len(param_hints), 1, "only line-3 hint kept")
        assert_eq(param_hints[0]["position"]["line"], 3,
                  "hint on line 3")


def test_inlay_hints_workspace_fallback() -> None:
    """Callee defined in a sibling file outside the import graph.
    The workspace symbol index is the fallback resolver."""
    with tempfile.TemporaryDirectory() as ws:
        # NO `import` connecting caller -> sibling.
        sibling_path = os.path.join(ws, "sibling.nova")
        _write(sibling_path,
               "fn helper(prefix, value) {\n    return prefix\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = 'fn main() {\n    helper("hi", 42)\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        hints = compute_inlay_hints(
            _uri(caller_path), None, text, cache,
            workspace_index=idx,
        )
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        labels = [h["label"] for h in param_hints]
        assert_eq(labels, ["prefix:", "value:"],
                  "workspace fallback resolved params")


def test_inlay_hints_type_hint_on_let_literal() -> None:
    """`let x = 5` -- emit `: int` type hint after the name."""
    with tempfile.TemporaryDirectory() as ws:
        caller_path = os.path.join(ws, "caller.nova")
        text = 'fn main() {\n    let x = 5\n    let s = "hi"\n}\n'
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        type_hints = [h for h in hints if h["kind"] == INLAY_HINT_KIND_TYPE]
        labels = [h["label"] for h in type_hints]
        assert_eq(sorted(labels), [": int", ": str"],
                  "int + str type hints emitted")
        # Type hint sits AFTER the `x` token of `let x` (4-space indent
        # + `let ` + `x` = column 9).
        int_hint = next(h for h in type_hints if h["label"] == ": int")
        assert_eq(int_hint["position"]["character"], 9,
                  "type hint anchored right after the name")


def test_inlay_hints_keyword_not_call_site() -> None:
    """`if foo(x)` -- the `if` is a keyword (we already mask
    keyword-`(` from outgoing-call scanning), but the *real* call
    `foo(x)` inside it still gets a hint."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = (
            'import "decls.nova"\n'
            "\n"
            "fn main() {\n"
            "    if foo(1) { return 1 }\n"
            "}\n"
        )
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        # Only one hint for `foo(1)` -- the `if` is not a call.
        assert_eq(len(param_hints), 1, "exactly one hint, if not counted")
        assert_eq(param_hints[0]["label"], "x:", "hint labels x")


def test_inlay_hints_call_in_comment_ignored() -> None:
    """A call-like pattern inside a `//` comment must not trigger hints."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = (
            'import "decls.nova"\n'
            "\n"
            "fn main() {\n"
            "    // foo(99)\n"
            "    foo(1)\n"
            "}\n"
        )
        _write(caller_path, text)
        cache = FileCache()
        hints = compute_inlay_hints(_uri(caller_path), None, text, cache)
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        # The commented `foo(99)` must NOT emit a hint; the real call does.
        assert_eq(len(param_hints), 1, "only the real call produces a hint")
        assert_eq(param_hints[0]["position"]["line"], 4,
                  "hint on the real call line")


# ---------------------------------------------------------------------------
# Server-level wire smoke.
# ---------------------------------------------------------------------------


def test_server_inlay_hint_capability_advertised() -> None:
    """`initialize` response advertises `inlayHintProvider`."""
    client = LspClient()
    resp = client.initialize()
    caps = resp["result"]["capabilities"]
    assert_("inlayHintProvider" in caps, "inlayHintProvider in capabilities")


def test_server_inlay_hint_wire() -> None:
    """End-to-end through `dispatch` -- the response shape is
    `{"result": [InlayHint, ...]}`."""
    with tempfile.TemporaryDirectory() as ws:
        decl_path = os.path.join(ws, "decls.nova")
        _write(decl_path, "fn foo(x, y) {\n    return x\n}\n")
        caller_path = os.path.join(ws, "caller.nova")
        text = 'import "decls.nova"\n\nfn main() {\n    foo(1, 2)\n}\n'
        _write(caller_path, text)
        client = LspClient()
        client.initialize(root_path=ws)
        uri = client.open(caller_path, text)
        resp = client.request(
            "textDocument/inlayHint",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 10, "character": 0},
                },
            },
        )
        hints = resp["result"]
        assert_(isinstance(hints, list), "result is a list")
        # Two param hints for the call.
        param_hints = [h for h in hints
                       if h["kind"] == INLAY_HINT_KIND_PARAMETER]
        assert_eq(len(param_hints), 2,
                  "two parameter hints over the wire")


# ---------------------------------------------------------------------------
# Integration -- real call site in src/compiler/codegen.nova.
# ---------------------------------------------------------------------------


def test_integration_codegen_cg_ht_set_call_site() -> None:
    """`_cg_ht_set(ht, key, val)` is declared early in codegen.nova
    and called from a handful of late-file sites (e.g.
        `_cg_ht_set(cg_fn_modules, mfn_name, cg_source_file)`)
    The inlay hints should label those three args ht / key / val.

    We discover the actual call-site line number dynamically (rather
    than pinning it) so the test stays robust as parallel agents
    grow codegen.nova between runs.
    """
    codegen = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(codegen):
        print("  SKIP integration: codegen.nova missing")
        return
    with open(codegen, "r", encoding="utf-8") as f:
        text = f.read()
    # Find the line of the first `_cg_ht_set(` call site (not the decl).
    target_line = -1
    for i, line in enumerate(text.splitlines()):
        stripped = line.lstrip()
        if stripped.startswith("fn "):
            continue
        if "_cg_ht_set(" in line:
            target_line = i
            break
    assert_(target_line >= 0, "found a _cg_ht_set call site")
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(codegen)
    # Center the viewport on the discovered line +/- 2 so we catch
    # at least one call and stay fast.
    viewport = {
        "start": {"line": max(0, target_line - 1), "character": 0},
        "end": {"line": target_line + 2, "character": 0},
    }
    hints = compute_inlay_hints(
        _uri(codegen),
        viewport,
        text,
        cache,
        workspace_index=idx,
    )
    param_hints = [h for h in hints
                   if h["kind"] == INLAY_HINT_KIND_PARAMETER]
    labels_seen = {h["label"] for h in param_hints}
    print(f"  codegen target line={target_line} hints={len(param_hints)} "
          f"labels={labels_seen}")
    assert_("ht:" in labels_seen, "ht: hint present")
    assert_("key:" in labels_seen, "key: hint present")
    assert_("val:" in labels_seen, "val: hint present")


# ---------------------------------------------------------------------------


def main() -> int:
    test_split_param_names_basic()
    test_split_param_names_with_default_and_mut()
    test_walk_args_single_line_two_args()
    test_walk_args_nested_call()
    test_walk_args_multi_line()
    test_walk_args_no_args()
    test_walk_args_string_with_comma()
    test_walk_args_unclosed_returns_none()
    test_has_explicit_name_label()
    test_infer_literal_type_basic()
    test_inlay_hints_basic_two_args()
    test_inlay_hints_nested_call()
    test_inlay_hints_mismatched_arg_count()
    test_inlay_hints_builtin_callee_no_hints()
    test_inlay_hints_literal_args_still_emitted()
    test_inlay_hints_named_arg_skipped()
    test_inlay_hints_range_filter()
    test_inlay_hints_workspace_fallback()
    test_inlay_hints_type_hint_on_let_literal()
    test_inlay_hints_keyword_not_call_site()
    test_inlay_hints_call_in_comment_ignored()
    test_server_inlay_hint_capability_advertised()
    test_server_inlay_hint_wire()
    test_integration_codegen_cg_ht_set_call_site()
    print(f"test_inlay_hints: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
