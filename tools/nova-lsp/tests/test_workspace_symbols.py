"""Smoke + unit + integration tests for `workspace/symbol`.

Covers:
  * Empty workspace -> empty result.
  * Single-file workspace with 5 fn declarations -> query returns them.
  * Cross-file workspace (3 files, mixed declarations).
  * Fuzzy matching: substring beats camelCase beats sequential.
  * SymbolKind classification for fn / let-Variable / let-Constant.
  * Empty query returns the first N symbols.
  * Invalidation: removing a file's symbols drops them from results.
  * didChange refreshes the index from in-memory text.
  * Integration: index /home/user/NOVA/src/ and find `_nova_check_rdi`.
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
from nova_lsp.workspace_symbols import (
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_VARIABLE,
    WorkspaceSymbolIndex,
    fuzzy_score,
    scan_symbols,
)


# Per-test assertion counter so the final summary can report how many
# checks ran. The harness expects ~20-25.
_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# Unit tests for the bare WorkspaceSymbolIndex.
# ---------------------------------------------------------------------------


def test_empty_index() -> None:
    idx = WorkspaceSymbolIndex()
    assert_eq(idx.fuzzy_match("foo"), [], "empty index, real query")
    assert_eq(idx.fuzzy_match(""), [], "empty index, empty query")
    assert_eq(len(idx), 0, "empty index len")


def test_single_file_five_fns() -> None:
    text = (
        "fn alpha() { return 1 }\n"
        "fn beta() { return 2 }\n"
        "fn gamma() { return 3 }\n"
        "fn delta() { return 4 }\n"
        "fn epsilon() { return 5 }\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "five.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        idx = WorkspaceSymbolIndex()
        n = idx.index_file(path)
        assert_eq(n, 5, "single-file index symbol count")

        # Fetch all symbols via empty query (alphabetical order).
        all_syms = idx.fuzzy_match("", limit=10)
        names = [s.name for s in all_syms]
        assert_eq(names, ["alpha", "beta", "delta", "epsilon", "gamma"],
                  "empty query returns all in name order")

        # Fuzzy: 'al' should top-rank alpha.
        hits = idx.fuzzy_match("al")
        assert_eq(hits[0].name, "alpha", "'al' top-ranks alpha")

        # Each entry carries the absolute path and a sensible range.
        alpha = next(s for s in all_syms if s.name == "alpha")
        assert_eq(alpha.path, os.path.abspath(path), "alpha path")
        assert_eq(alpha.line, 0, "alpha line")
        assert_eq(alpha.kind, SYMBOL_KIND_FUNCTION, "alpha kind=Function")


def test_cross_file_workspace() -> None:
    file_a = "fn a_one() {}\nfn a_two() {}\nlet AVAR = 1\n"
    file_b = "fn b_first() {}\nfn b_second() {}\nfn b_third() {}\nlet bvar = 2\n"
    file_c = "fn c_alpha() {}\nfn c_beta() {}\nlet CFOO = 3\n"
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        pc = os.path.join(ws, "c.nova")
        for path, text in ((pa, file_a), (pb, file_b), (pc, file_c)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        # 7 fns + 3 lets = 10 symbols total.
        assert_eq(len(idx), 10, "cross-file total symbol count")

        # Search for `b_second` — only file b.
        hits = idx.fuzzy_match("b_second")
        assert_eq(len(hits), 1, "b_second hit count")
        assert_eq(hits[0].path, os.path.abspath(pb), "b_second file path")
        assert_eq(hits[0].line, 1, "b_second line")
        assert_eq(hits[0].kind, SYMBOL_KIND_FUNCTION, "b_second kind")


def test_fuzzy_match_ranking() -> None:
    """`foB` query must rank `fooBar` and `foo_bar` above `unrelated_func`."""
    idx = WorkspaceSymbolIndex()
    idx.index_text(
        "test.nova",
        "fn fooBar() {}\n"
        "fn foo_bar() {}\n"
        "fn unrelated_func() {}\n",
    )
    hits = idx.fuzzy_match("foB")
    names = [h.name for h in hits]
    # Both fooBar (camelCase letter match) and foo_bar (case-insensitive
    # substring "fob"? no — foB lower-case 'fob' not in 'foo_bar'.)
    # Actually 'foB' lower → 'fob'; foo_bar contains 'foo' / 'fob'?
    # 'foo_bar' lowercase is 'foo_bar' which doesn't contain 'fob'
    # contiguously. So foo_bar matches via sequential/camelCase only.
    assert_("fooBar" in names, "fooBar in 'foB' results")
    assert_("foo_bar" in names, "foo_bar in 'foB' results")
    # fooBar beats foo_bar (camelCase score is tighter for fooBar).
    fooBar_idx = names.index("fooBar")
    foo_bar_idx = names.index("foo_bar")
    assert_(fooBar_idx < foo_bar_idx, "fooBar beats foo_bar in ranking")
    # unrelated_func should not appear (no 'fob' subsequence).
    assert_("unrelated_func" not in names, "unrelated_func not in 'foB' results")


def test_fuzzy_score_tiers() -> None:
    # Exact match → tier 0.
    score_exact = fuzzy_score("foo", "foo")
    assert_(score_exact is not None and score_exact[0] == 0, "exact tier 0")
    # Case-insensitive match → tier 1.
    score_ci = fuzzy_score("foo", "FOO")
    assert_(score_ci is not None and score_ci[0] == 1, "case-insensitive tier 1")
    # Substring → tier 3.
    score_sub = fuzzy_score("oba", "fooBar")
    assert_(score_sub is not None and score_sub[0] == 3, "substring tier 3")
    # CamelCase → tier 4.
    score_cam = fuzzy_score("fB", "fooBar")
    assert_(score_cam is not None and score_cam[0] == 4, "camelCase tier 4")
    # Sequential → tier 5 (or higher).
    score_seq = fuzzy_score("for", "fooBar")
    assert_(score_seq is not None and score_seq[0] in (3, 5),
            "sequential char match")
    # No match → None.
    assert_(fuzzy_score("xyz", "fooBar") is None, "non-match returns None")


def test_empty_query_returns_first_n() -> None:
    idx = WorkspaceSymbolIndex()
    # Index 5 single-file symbols.
    idx.index_text(
        "test.nova",
        "fn one() {}\nfn two() {}\nfn three() {}\nfn four() {}\nfn five() {}\n",
    )
    hits = idx.fuzzy_match("", limit=3)
    assert_eq(len(hits), 3, "empty query honours limit")
    # Alphabetical: five, four, one ... (sorted)
    assert_eq([h.name for h in hits], ["five", "four", "one"], "empty query sorted")


def test_symbol_kinds() -> None:
    """fn → Function (12); ALL_CAPS let → Constant (14); lower let → Variable (13)."""
    idx = WorkspaceSymbolIndex()
    idx.index_text(
        "kinds.nova",
        "fn func_kind() {}\n"
        "let lowercase_var = 1\n"
        "let ALL_CAPS_CONST = 2\n"
        "let TAU = 6\n"
        "let mixedCase = 3\n",
    )
    by_name = {s.name: s for s in idx.all_symbols()}
    assert_eq(by_name["func_kind"].kind, SYMBOL_KIND_FUNCTION, "fn → Function")
    assert_eq(by_name["lowercase_var"].kind, SYMBOL_KIND_VARIABLE, "lower let → Variable")
    assert_eq(by_name["ALL_CAPS_CONST"].kind, SYMBOL_KIND_CONSTANT, "ALL_CAPS let → Constant")
    assert_eq(by_name["TAU"].kind, SYMBOL_KIND_CONSTANT, "short ALL_CAPS → Constant")
    assert_eq(by_name["mixedCase"].kind, SYMBOL_KIND_VARIABLE, "mixedCase let → Variable")


def test_invalidation() -> None:
    """Calling `invalidate_file` removes that file's contribution."""
    idx = WorkspaceSymbolIndex()
    idx.index_text("/tmp/synthA.nova", "fn alpha() {}\nfn shared() {}\n")
    idx.index_text("/tmp/synthB.nova", "fn beta() {}\nfn shared() {}\n")
    # Both files contribute a `shared` symbol; pre-invalidation we see 2.
    hits = idx.fuzzy_match("shared")
    assert_eq(len(hits), 2, "shared appears twice pre-invalidate")
    # Drop file A.
    idx.invalidate_file("/tmp/synthA.nova")
    hits_post = idx.fuzzy_match("shared")
    assert_eq(len(hits_post), 1, "shared appears once post-invalidate")
    assert_eq(hits_post[0].path, "/tmp/synthB.nova", "remaining shared from B")
    # File A's unique symbol `alpha` is also gone.
    assert_eq(idx.fuzzy_match("alpha"), [], "alpha gone after A invalidate")


def test_didchange_refreshes_index() -> None:
    """Re-indexing the same path with new text replaces the old entries."""
    idx = WorkspaceSymbolIndex()
    idx.index_text("/tmp/synthC.nova", "fn original() {}\nfn keeper() {}\n")
    assert_(len(idx.fuzzy_match("original")) == 1, "original present pre-edit")
    # Simulate `didChange` — the file's text is now different.
    idx.index_text("/tmp/synthC.nova", "fn renamed() {}\nfn keeper() {}\n")
    assert_eq(idx.fuzzy_match("original"), [], "original gone after re-index")
    assert_(len(idx.fuzzy_match("renamed")) == 1, "renamed appears after re-index")
    assert_(len(idx.fuzzy_match("keeper")) == 1, "keeper still single after re-index")


# ---------------------------------------------------------------------------
# Server-level smoke test.
# ---------------------------------------------------------------------------


def test_server_workspace_symbol() -> None:
    """End-to-end: drive the LSP through `dispatch` and check the wire."""
    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "mod_a.nova")
        b_path = os.path.join(ws, "mod_b.nova")
        with open(a_path, "w", encoding="utf-8") as f:
            f.write("fn compute_total() {}\nfn helper() {}\nlet PI = 3\n")
        with open(b_path, "w", encoding="utf-8") as f:
            f.write("fn render_view() {}\nfn helper() {}\nlet greeting = \"hi\"\n")

        client = LspClient()
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        assert_(caps.get("workspaceSymbolProvider") is not None,
                "workspaceSymbolProvider in capabilities")

        # Open one of the files so didOpen also warms the index.
        a_uri = client.open(a_path, open(a_path, "r", encoding="utf-8").read())

        # Empty query → first 100 symbols across the workspace.
        resp = client.request("workspace/symbol", {"query": ""})
        result = resp["result"]
        assert_(isinstance(result, list), "result is a list")
        names = sorted({r["name"] for r in result})
        # Should include both fns from both files (5 unique + 1 shared = 6 ish).
        assert_("compute_total" in names, "compute_total in empty-query result")
        assert_("render_view" in names, "render_view in empty-query result")

        # Substring query.
        resp2 = client.request("workspace/symbol", {"query": "render"})
        names2 = [r["name"] for r in resp2["result"]]
        assert_(names2 and names2[0] == "render_view", "render_view top hit for 'render'")

        # `helper` appears in both files → 2 hits.
        resp3 = client.request("workspace/symbol", {"query": "helper"})
        helper_hits = [r for r in resp3["result"] if r["name"] == "helper"]
        assert_eq(len(helper_hits), 2, "helper hits across both files")

        # SymbolInformation shape: name, kind, location.uri, location.range.
        sample = resp2["result"][0]
        assert_(set(["name", "kind", "location"]).issubset(sample.keys()),
                "SymbolInformation shape")
        assert_("uri" in sample["location"] and "range" in sample["location"],
                "location has uri + range")


# ---------------------------------------------------------------------------
# Integration test: real NOVA codebase.
# ---------------------------------------------------------------------------


def test_integration_nova_src() -> None:
    """Index NOVA's src/ tree and assert `_nova_check_rdi` shows up."""
    src_root = "/home/user/NOVA/src"
    if not os.path.isdir(src_root):
        print(f"  SKIP integration: {src_root} missing")
        return
    idx = WorkspaceSymbolIndex()
    n = idx.index_workspace_root(src_root)
    print(f"  indexed {len(idx)} symbols across {n} declarations under {src_root}")

    # Fuzzy lookup for the runtime helper.
    hits = idx.fuzzy_match("_nova_check_rdi")
    assert_(len(hits) >= 1, "_nova_check_rdi resolves to at least one symbol")
    top = hits[0]
    assert_eq(top.name, "_nova_check_rdi", "top hit name is _nova_check_rdi")
    assert_(top.path.endswith("codegen.nova"), "_nova_check_rdi defined in codegen.nova")

    # And its sibling helper.
    hits_rsi = idx.fuzzy_match("_nova_check_rsi")
    assert_(len(hits_rsi) >= 1, "_nova_check_rsi resolves to at least one symbol")

    # Sanity: there are many top-level `fn`s under src/.
    assert_(len(idx) > 100, "NOVA src/ contributes >100 symbols")

    print(f"  _nova_check_rdi -> {top.path}:{top.line}")
    print(f"    range chars {top.char_start}-{top.char_end}")
    print(f"  _nova_check_rsi -> {hits_rsi[0].path}:{hits_rsi[0].line}")


# ---------------------------------------------------------------------------


def main() -> int:
    test_empty_index()
    test_single_file_five_fns()
    test_cross_file_workspace()
    test_fuzzy_match_ranking()
    test_fuzzy_score_tiers()
    test_empty_query_returns_first_n()
    test_symbol_kinds()
    test_invalidation()
    test_didchange_refreshes_index()
    test_server_workspace_symbol()
    test_integration_nova_src()
    print(f"test_workspace_symbols: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
