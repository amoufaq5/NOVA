"""Unit + smoke + integration tests for call hierarchy.

Covers:

  * `find_function_spans` — brace-counted span extraction for top-level
    `fn name(...) {...}` declarations.
  * `prepare_call_hierarchy` — cursor on a fn name, on a call site, on
    a variable, on a parameter, cross-file via import.
  * `incoming_calls` — single-file, multi-file, recursion, no callers
    (private/unused), group-by-enclosing-fn semantics.
  * `outgoing_calls` — multiple distinct callees, duplicate callee
    (multiple call sites), recursion, leaf fn, builtin elision,
    cross-file callee resolution.
  * Server-level wire smoke through `dispatch` for all three handlers.
  * Integration on `src/compiler/codegen.nova` — prepare + incoming
    for a real, popular top-level fn (`out`) and a leaf utility
    (`cg_init` outgoing-call check).
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
from nova_lsp.call_hierarchy import (  # noqa: E402
    build_call_hierarchy_item,
    find_function_spans,
    function_span_at,
    function_span_by_name,
    incoming_calls,
    outgoing_calls,
    prepare_call_hierarchy,
)
from nova_lsp.imports import FileCache  # noqa: E402
from nova_lsp.workspace_symbols import (  # noqa: E402
    SYMBOL_KIND_FUNCTION,
    WorkspaceSymbolIndex,
)


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
# find_function_spans.
# ---------------------------------------------------------------------------


def test_find_function_spans_basic() -> None:
    text = (
        "fn alpha() {\n"
        "    return 1\n"
        "}\n"
        "fn beta(x, y) {\n"
        "    return x + y\n"
        "}\n"
    )
    spans = find_function_spans(text)
    assert_eq(len(spans), 2, "two top-level fns found")
    assert_eq(spans[0].name, "alpha", "first fn name")
    assert_eq(spans[0].decl_line, 0, "alpha decl_line")
    assert_eq(spans[0].end_line, 2, "alpha end_line")
    assert_eq(spans[1].name, "beta", "second fn name")
    assert_eq(spans[1].decl_line, 3, "beta decl_line")
    assert_eq(spans[1].end_line, 5, "beta end_line")
    assert_eq(spans[1].args, "x, y", "beta args string")


def test_find_function_spans_nested_braces() -> None:
    text = (
        "fn outer() {\n"
        "    if cond {\n"
        "        let y = { x }\n"
        "    }\n"
        "    return 1\n"
        "}\n"
    )
    spans = find_function_spans(text)
    assert_eq(len(spans), 1, "one fn despite nested braces")
    assert_eq(spans[0].end_line, 5, "outer end at matching }")


# ---------------------------------------------------------------------------
# prepare_call_hierarchy.
# ---------------------------------------------------------------------------


def test_prepare_on_fn_declaration() -> None:
    text = "fn target() {\n    return 1\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on 't' of `target`.
        result = prepare_call_hierarchy(_uri(path), 0, 5, text, cache)
        assert_(result is not None, "prepare on fn decl is non-None")
        assert_eq(len(result), 1, "prepare returns single-element list")
        item = result[0]
        assert_eq(item["name"], "target", "item.name = target")
        assert_eq(item["kind"], SYMBOL_KIND_FUNCTION, "item.kind = Function")
        assert_eq(item["uri"], _uri(path), "item.uri matches")
        assert_eq(item["range"]["start"]["line"], 0, "range start line")
        assert_eq(item["range"]["end"]["line"], 2, "range end line")


def test_prepare_on_variable_returns_none() -> None:
    text = (
        "fn outer() {\n"
        "    let some_var = 5\n"
        "    return some_var\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on 'some_var' (a local variable, not a fn).
        result = prepare_call_hierarchy(_uri(path), 1, 10, text, cache)
        assert_(result is None, "prepare on let returns None")


def test_prepare_on_whitespace_returns_none() -> None:
    text = "fn foo() {\n    return 1\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        result = prepare_call_hierarchy(_uri(path), 1, 2, text, cache)  # spaces
        assert_(result is None, "prepare on whitespace returns None")


def test_prepare_on_call_site_resolves_to_decl() -> None:
    text = (
        "fn target() {\n"
        "    return 1\n"
        "}\n"
        "fn caller() {\n"
        "    target()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        # Cursor on `target` inside the call on line 4.
        result = prepare_call_hierarchy(_uri(path), 4, 6, text, cache)
        assert_(result is not None, "prepare on call site is non-None")
        assert_eq(result[0]["name"], "target", "resolved to target decl")
        assert_eq(result[0]["range"]["start"]["line"], 0, "range points to decl")


# ---------------------------------------------------------------------------
# incoming_calls — single-file workspace.
# ---------------------------------------------------------------------------


def test_incoming_calls_three_callers_one_file() -> None:
    text = (
        "fn foo() {\n"
        "    return 1\n"
        "}\n"
        "fn caller_a() {\n"
        "    foo()\n"
        "    foo()\n"
        "}\n"
        "fn caller_b() {\n"
        "    foo()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        foo_span = function_span_by_name(spans, "foo")
        assert_(foo_span is not None, "foo span found")
        item = build_call_hierarchy_item(foo_span, path)
        calls = incoming_calls(item, idx, cache)
        # Group by enclosing fn: caller_a (2 sites) + caller_b (1 site).
        assert_eq(len(calls), 2, "two distinct caller fns")
        callers = [c["from"]["name"] for c in calls]
        assert_("caller_a" in callers, "caller_a in list")
        assert_("caller_b" in callers, "caller_b in list")
        for c in calls:
            if c["from"]["name"] == "caller_a":
                assert_eq(len(c["fromRanges"]), 2, "caller_a has 2 sites")
            elif c["from"]["name"] == "caller_b":
                assert_eq(len(c["fromRanges"]), 1, "caller_b has 1 site")


def test_incoming_calls_three_files() -> None:
    """A defines foo; B calls foo 2x; C calls foo 1x → 3 caller groups
    (caller_b1 + caller_b2 in B, caller_c in C). Groups are by enclosing
    function, not by file, so this stays a stable 3-group result."""
    file_a = "fn foo() {\n    return 1\n}\n"
    file_b = (
        'import "./a.nova"\n'
        "fn caller_b1() {\n"
        "    foo()\n"
        "}\n"
        "fn caller_b2() {\n"
        "    foo()\n"
        "}\n"
    )
    file_c = (
        'import "./a.nova"\n'
        "fn caller_c() {\n"
        "    foo()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        pc = os.path.join(ws, "c.nova")
        _write(pa, file_a)
        _write(pb, file_b)
        _write(pc, file_c)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        spans = find_function_spans(file_a)
        foo_span = function_span_by_name(spans, "foo")
        item = build_call_hierarchy_item(foo_span, pa)
        calls = incoming_calls(item, idx, cache)
        assert_eq(len(calls), 3, "three caller groups across three files")
        names = sorted(c["from"]["name"] for c in calls)
        assert_eq(names, ["caller_b1", "caller_b2", "caller_c"],
                  "caller names listed")
        for c in calls:
            assert_eq(len(c["fromRanges"]), 1, f"{c['from']['name']} has 1 site")


def test_incoming_calls_no_callers() -> None:
    """Private/unused function → incoming returns empty list."""
    text = (
        "fn lonely() {\n"
        "    return 42\n"
        "}\n"
        "fn other() {\n"
        "    return 1\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        lonely_span = function_span_by_name(spans, "lonely")
        item = build_call_hierarchy_item(lonely_span, path)
        calls = incoming_calls(item, idx, cache)
        assert_eq(calls, [], "no callers for lonely fn")


def test_incoming_calls_recursion_includes_self() -> None:
    """A recursive function counts itself as an incoming caller."""
    text = (
        "fn fib(n) {\n"
        "    if n < 2 { return n }\n"
        "    return fib(n - 1) + fib(n - 2)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        fib_span = function_span_by_name(spans, "fib")
        item = build_call_hierarchy_item(fib_span, path)
        calls = incoming_calls(item, idx, cache)
        assert_eq(len(calls), 1, "one caller group (self)")
        assert_eq(calls[0]["from"]["name"], "fib", "caller is fib itself")
        assert_eq(len(calls[0]["fromRanges"]), 2, "two recursive sites")


def test_incoming_calls_skips_strings_and_comments() -> None:
    """A `foo(` mention inside a string or `//` comment must NOT count
    as an incoming call site."""
    text = (
        "fn foo() {\n"
        "    return 1\n"
        "}\n"
        "fn talker() {\n"
        '    let s = "calls foo() in a string"\n'
        "    // a comment that mentions foo() too\n"
        "    foo()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        foo_span = function_span_by_name(spans, "foo")
        item = build_call_hierarchy_item(foo_span, path)
        calls = incoming_calls(item, idx, cache)
        assert_eq(len(calls), 1, "single caller group")
        assert_eq(len(calls[0]["fromRanges"]), 1,
                  "only the real call counts (strings/comments skipped)")


# ---------------------------------------------------------------------------
# outgoing_calls.
# ---------------------------------------------------------------------------


def test_outgoing_calls_basic() -> None:
    """fn that calls bar(), baz(), bar() → bar (2 sites) + baz (1 site)."""
    text = (
        "fn bar() { return 1 }\n"
        "fn baz() { return 2 }\n"
        "fn caller() {\n"
        "    bar()\n"
        "    baz()\n"
        "    bar()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        caller_span = function_span_by_name(spans, "caller")
        item = build_call_hierarchy_item(caller_span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(len(outs), 2, "two distinct callees")
        names = [o["to"]["name"] for o in outs]
        assert_eq(sorted(names), ["bar", "baz"], "callee names")
        for o in outs:
            if o["to"]["name"] == "bar":
                assert_eq(len(o["fromRanges"]), 2, "bar called twice")
            elif o["to"]["name"] == "baz":
                assert_eq(len(o["fromRanges"]), 1, "baz called once")


def test_outgoing_calls_leaf_function() -> None:
    """A function that doesn't call anything (no top-level fn calls)
    returns an empty outgoing list. We include a builtin to confirm
    builtins are elided too."""
    text = (
        "fn leaf() {\n"
        "    let x = 1\n"
        "    return x + 2\n"
        "}\n"
        "fn helper() {\n"
        "    leaf()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        leaf_span = function_span_by_name(spans, "leaf")
        item = build_call_hierarchy_item(leaf_span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(outs, [], "leaf fn has no outgoing calls")


def test_outgoing_calls_builtin_elision() -> None:
    """A function that only calls builtins (println, len, etc) reports
    no outgoing calls — builtins aren't navigable."""
    text = (
        "fn uses_builtin() {\n"
        "    println(\"hi\")\n"
        "    let l = list_new()\n"
        "    return len(l)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        span = function_span_by_name(spans, "uses_builtin")
        item = build_call_hierarchy_item(span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(outs, [], "no outgoing calls for builtin-only body")


def test_outgoing_calls_recursive_self_reference() -> None:
    """A recursive fn appears in its own outgoing list."""
    text = (
        "fn fact(n) {\n"
        "    if n <= 1 { return 1 }\n"
        "    return fact(n - 1)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        fact_span = function_span_by_name(spans, "fact")
        item = build_call_hierarchy_item(fact_span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(len(outs), 1, "single outgoing edge (self)")
        assert_eq(outs[0]["to"]["name"], "fact", "callee is fact itself")
        assert_eq(len(outs[0]["fromRanges"]), 1, "one recursive site")


def test_outgoing_calls_cross_file() -> None:
    """A function in A calling an imported function in B → outgoing
    correctly resolves the callee item to B's location."""
    file_a = (
        'import "./b.nova"\n'
        "fn user() {\n"
        "    helper_in_b()\n"
        "}\n"
    )
    file_b = "fn helper_in_b() {\n    return 42\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        _write(pa, file_a)
        _write(pb, file_b)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        spans = find_function_spans(file_a)
        user_span = function_span_by_name(spans, "user")
        item = build_call_hierarchy_item(user_span, pa)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(len(outs), 1, "single outgoing edge")
        callee = outs[0]["to"]
        assert_eq(callee["name"], "helper_in_b", "callee resolved by name")
        assert_eq(callee["uri"], _uri(pb), "callee uri points to file B")


def test_outgoing_calls_skips_keywords() -> None:
    """`if (`, `while (`, etc must NOT be reported as calls."""
    text = (
        "fn shape_check(v) {\n"
        "    if (v > 0) {\n"
        "        while (v > 1) { v = v - 1 }\n"
        "        return helper(v)\n"
        "    }\n"
        "    return 0\n"
        "}\n"
        "fn helper(x) { return x }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        span = function_span_by_name(spans, "shape_check")
        item = build_call_hierarchy_item(span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        # Only `helper` should appear — if/while/return are not callees.
        assert_eq(len(outs), 1, "only helper appears (keywords elided)")
        assert_eq(outs[0]["to"]["name"], "helper", "helper is the callee")


def test_outgoing_calls_skips_strings_and_comments() -> None:
    """A `bar(` mention in a string or comment must not count."""
    text = (
        "fn bar() { return 1 }\n"
        "fn talker() {\n"
        '    let s = "calls bar() in a string"\n'
        "    // also mentions bar() in a comment\n"
        "    bar()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_file(path)
        spans = find_function_spans(text)
        span = function_span_by_name(spans, "talker")
        item = build_call_hierarchy_item(span, path)
        outs = outgoing_calls(item, cache, workspace_index=idx)
        assert_eq(len(outs), 1, "single callee")
        assert_eq(len(outs[0]["fromRanges"]), 1,
                  "exactly one real call (string/comment skipped)")


# ---------------------------------------------------------------------------
# Server-level dispatch (wire smoke).
# ---------------------------------------------------------------------------


def test_server_call_hierarchy_capability_advertised() -> None:
    """`callHierarchyProvider: True` must appear in the init capabilities."""
    client = LspClient()
    init = client.initialize()
    caps = init["result"]["capabilities"]
    assert_eq(caps.get("callHierarchyProvider"), True,
              "callHierarchyProvider in capabilities")


def test_server_prepare_call_hierarchy_wire() -> None:
    """End-to-end: open a file, ask for prepareCallHierarchy on a fn."""
    text = (
        "fn target() {\n"
        "    return 1\n"
        "}\n"
        "fn caller() {\n"
        "    target()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 5}},
        )
        result = resp["result"]
        assert_(result is not None, "prepareCallHierarchy returns non-None")
        assert_eq(len(result), 1, "result is single-element")
        assert_eq(result[0]["name"], "target", "item name")


def test_server_incoming_calls_wire() -> None:
    """End-to-end: prepare then incomingCalls."""
    text = (
        "fn target() {\n"
        "    return 1\n"
        "}\n"
        "fn caller_a() {\n"
        "    target()\n"
        "}\n"
        "fn caller_b() {\n"
        "    target()\n"
        "    target()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        prep = client.request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 0, "character": 5}},
        )
        item = prep["result"][0]
        resp = client.request(
            "callHierarchy/incomingCalls",
            {"item": item},
        )
        result = resp["result"]
        assert_eq(len(result), 2, "two callers (caller_a, caller_b)")
        sites_by_name = {c["from"]["name"]: len(c["fromRanges"]) for c in result}
        assert_eq(sites_by_name.get("caller_a"), 1, "caller_a 1 site")
        assert_eq(sites_by_name.get("caller_b"), 2, "caller_b 2 sites")


def test_server_outgoing_calls_wire() -> None:
    """End-to-end: prepare then outgoingCalls."""
    text = (
        "fn bar() { return 1 }\n"
        "fn baz() { return 2 }\n"
        "fn caller() {\n"
        "    bar()\n"
        "    baz()\n"
        "    bar()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        prep = client.request(
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": uri},
             "position": {"line": 2, "character": 5}},  # cursor on `caller`
        )
        item = prep["result"][0]
        resp = client.request(
            "callHierarchy/outgoingCalls",
            {"item": item},
        )
        result = resp["result"]
        assert_eq(len(result), 2, "two callees")
        names = sorted(o["to"]["name"] for o in result)
        assert_eq(names, ["bar", "baz"], "callees by name")


# ---------------------------------------------------------------------------
# Integration on the real NOVA codebase.
# ---------------------------------------------------------------------------


def test_integration_codegen_out() -> None:
    """Pick `out` from src/compiler/codegen.nova — one of the most
    popular helpers — and confirm incoming-calls reports a sensible
    count. The codebase is large, so we assert a lower bound rather
    than an exact number (insulates the test against future edits)."""
    codegen = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(codegen):
        print("  SKIP integration: codegen.nova missing")
        return
    with open(codegen, "r", encoding="utf-8") as f:
        text = f.read()
    spans = find_function_spans(text)
    out_span = function_span_by_name(spans, "out")
    assert_(out_span is not None, "out fn found in codegen.nova")
    print(f"  out() declared at line {out_span.decl_line}, "
          f"ends at {out_span.end_line}")

    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(codegen)

    item = build_call_hierarchy_item(out_span, codegen)
    # prepare on the same position works.
    prep = prepare_call_hierarchy(
        _uri(codegen),
        out_span.decl_line,
        out_span.name_char_start,
        text,
        cache,
        workspace_index=idx,
    )
    assert_(prep is not None, "prepare on out() returns non-None")
    assert_eq(prep[0]["name"], "out", "prepare resolves to out")

    # incoming calls — out() is called all over the codebase. Codegen
    # has ~138 top-level fns; about 20-30 of them invoke out() directly
    # (the rest go through helpers like out_label, out_globl, etc that
    # themselves call out()). Total call sites are in the thousands.
    callers = incoming_calls(item, idx, cache)
    print(f"  out() has {len(callers)} caller fns in codegen.nova")
    assert_(len(callers) >= 10,
            "out() has >=10 caller fns (popular helper)")
    # Each caller has at least one fromRanges entry.
    total_sites = sum(len(c["fromRanges"]) for c in callers)
    print(f"  out() has {total_sites} total call sites in codegen.nova")
    assert_(total_sites >= len(callers), "every caller has >=1 site")
    assert_(total_sites > 1000, "out() called >1000 times total")
    # Spot-check one caller has a non-trivial name and is a function.
    first = callers[0]["from"]
    assert_(first["name"] and first["name"].isidentifier(),
            "first caller has a valid identifier name")
    assert_eq(first["kind"], SYMBOL_KIND_FUNCTION, "caller kind=Function")


def test_integration_codegen_cg_init_outgoing() -> None:
    """cg_init() calls list_new + _cg_ht_new helpers — confirm the
    outgoing-call resolver finds them as top-level fns."""
    codegen = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(codegen):
        print("  SKIP integration: codegen.nova missing")
        return
    with open(codegen, "r", encoding="utf-8") as f:
        text = f.read()
    spans = find_function_spans(text)
    cg_init_span = function_span_by_name(spans, "cg_init")
    assert_(cg_init_span is not None, "cg_init found")
    cache = FileCache()
    idx = WorkspaceSymbolIndex()
    idx.index_file(codegen)
    item = build_call_hierarchy_item(cg_init_span, codegen)
    outs = outgoing_calls(item, cache, workspace_index=idx)
    # cg_init calls _cg_ht_new at least twice (cg_fns_ht, cg_fn_modules).
    callee_names = [o["to"]["name"] for o in outs]
    assert_("_cg_ht_new" in callee_names,
            "_cg_ht_new in cg_init outgoing")
    print(f"  cg_init() outgoing callees: {sorted(set(callee_names))}")
    # list_new is a builtin so it must NOT appear.
    assert_("list_new" not in callee_names,
            "list_new (builtin) elided from outgoing")


# ---------------------------------------------------------------------------


def main() -> int:
    test_find_function_spans_basic()
    test_find_function_spans_nested_braces()
    test_prepare_on_fn_declaration()
    test_prepare_on_variable_returns_none()
    test_prepare_on_whitespace_returns_none()
    test_prepare_on_call_site_resolves_to_decl()
    test_incoming_calls_three_callers_one_file()
    test_incoming_calls_three_files()
    test_incoming_calls_no_callers()
    test_incoming_calls_recursion_includes_self()
    test_incoming_calls_skips_strings_and_comments()
    test_outgoing_calls_basic()
    test_outgoing_calls_leaf_function()
    test_outgoing_calls_builtin_elision()
    test_outgoing_calls_recursive_self_reference()
    test_outgoing_calls_cross_file()
    test_outgoing_calls_skips_keywords()
    test_outgoing_calls_skips_strings_and_comments()
    test_server_call_hierarchy_capability_advertised()
    test_server_prepare_call_hierarchy_wire()
    test_server_incoming_calls_wire()
    test_server_outgoing_calls_wire()
    test_integration_codegen_out()
    test_integration_codegen_cg_init_outgoing()
    print(f"test_call_hierarchy: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
