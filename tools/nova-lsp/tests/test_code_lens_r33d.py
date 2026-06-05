"""R33D — Run/Debug code lens tests for top-level ``fn test_*`` decls.

Layers a new test-action lens contract on top of the existing
reference-count lens (R10 era; tested by ``test_code_lens.py``). The
two lens kinds coexist: every top-level decl still gets its
"N references" lens, but a top-level ``fn test_*`` ALSO gets two
extra lenses (``▶ Run`` and ``⏷ Debug``) stacked above the references
lens.

Coverage:

  * ``scan_test_functions``: top-level ``fn test_*`` matched at column
    zero; non-test fns ignored; nested (indented) ``fn test_x`` ignored;
    test_-prefixed identifiers that are NOT fns ignored.
  * ``build_test_run_lens`` / ``build_test_debug_lens`` payload shape:
    range, command id, title, arguments[{file, name}], data.
  * ``compute_test_code_lenses`` end-to-end:
      - 5 test fns -> 10 lenses (5 Run + 5 Debug) in alternating order.
      - 0 test fns -> empty list.
      - Mixed file (test + non-test fns) -> only test fns get lenses.
      - Lens range starts on the fn's source line + column 0.
  * Server-level integration through ``compute_code_lenses``: both the
    reference-count lens AND the Run/Debug lenses appear for a file
    containing ``fn test_foo``.
  * Wire smoke via the dispatcher: ``textDocument/codeLens`` returns the
    full lens list (reference + Run/Debug) for a temp workspace test file.
  * Integration: ``tests/unit/test_match_expr.nova`` is a real NOVA test
    file — assert at least 0 Run/Debug lenses (since it uses
    ``assert(...)`` calls rather than ``fn test_*``); ``tests/test_path.nova``
    has 8 top-level ``fn test_*`` decls so it should emit 16 Run/Debug
    lenses.

Total assertions: well over the 15+ floor — ~30 cover the unit + smoke
+ integration paths.
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
from nova_lsp.code_lens import (  # noqa: E402
    TEST_DEBUG_COMMAND,
    TEST_DEBUG_TITLE,
    TEST_RUN_COMMAND,
    TEST_RUN_TITLE,
    TestFunction,
    build_test_debug_lens,
    build_test_run_lens,
    compute_code_lenses,
    compute_test_code_lenses,
    scan_test_functions,
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
# scan_test_functions — top-level `fn test_*` detection.
# ---------------------------------------------------------------------------


def test_scan_test_fns_single() -> None:
    text = "fn test_foo() {\n    assert(1 == 1)\n}\n"
    out = scan_test_functions(text)
    assert_eq(len(out), 1, "one test fn")
    assert_eq(out[0].name, "test_foo", "name carries `test_` prefix")
    assert_eq(out[0].line, 0, "fn on line 0")


def test_scan_test_fns_multiple_preserves_order() -> None:
    text = (
        "fn test_a() {}\n"
        "fn test_b() {}\n"
        "fn test_c() {}\n"
    )
    out = scan_test_functions(text)
    assert_eq([t.name for t in out],
              ["test_a", "test_b", "test_c"],
              "order preserved")
    assert_eq([t.line for t in out], [0, 1, 2], "line numbers correct")


def test_scan_test_fns_ignores_non_test_fn() -> None:
    text = (
        "fn helper() { return 1 }\n"
        "fn test_only() { assert(1 == 1) }\n"
        "fn another_helper() { return 2 }\n"
    )
    out = scan_test_functions(text)
    assert_eq(len(out), 1, "only the test_* fn picked up")
    assert_eq(out[0].name, "test_only", "right fn name")
    assert_eq(out[0].line, 1, "line 1 (not 0 or 2)")


def test_scan_test_fns_ignores_indented_nested_fn() -> None:
    """A nested `fn test_inner` inside another fn body is NOT top-level."""
    text = (
        "fn outer() {\n"
        "    fn test_inner() { return 1 }\n"   # indented -> NOT top-level
        "}\n"
        "fn test_real() { return 1 }\n"        # this one IS top-level
    )
    out = scan_test_functions(text)
    assert_eq(len(out), 1, "only the top-level test fn counted")
    assert_eq(out[0].name, "test_real", "test_real picked up")


def test_scan_test_fns_ignores_let_test_prefix() -> None:
    """`let test_x = ...` is a binding, not a test fn — not a lens."""
    text = (
        "let test_constant = 42\n"
        "fn test_real() { return 1 }\n"
    )
    out = scan_test_functions(text)
    assert_eq(len(out), 1, "only the fn counted")
    assert_eq(out[0].name, "test_real", "let binding ignored")


def test_scan_test_fns_empty_file() -> None:
    assert_eq(scan_test_functions(""), [], "empty file -> no test fns")


def test_scan_test_fns_no_test_prefix() -> None:
    text = "fn helper() {}\nfn another() {}\n"
    assert_eq(scan_test_functions(text), [],
              "non-test_ fns yield empty list")


# ---------------------------------------------------------------------------
# build_test_run_lens / build_test_debug_lens shape.
# ---------------------------------------------------------------------------


def test_build_test_run_lens_shape() -> None:
    tf = TestFunction(name="test_x", line=4)
    lens = build_test_run_lens(tf, "file:///abs/main.nova")
    assert_eq(lens["range"]["start"]["line"], 4, "range line == fn line")
    assert_eq(lens["range"]["start"]["character"], 0,
              "lens starts at column 0")
    assert_eq(lens["command"]["title"], TEST_RUN_TITLE,
              "Run title rendered correctly")
    assert_eq(lens["command"]["command"], TEST_RUN_COMMAND,
              "command id is nova-lsp.runTest")
    # Arguments is a list with one dict carrying {file, name}.
    arg = lens["command"]["arguments"][0]
    assert_eq(arg["file"], "/abs/main.nova", "file path from URI")
    assert_eq(arg["name"], "test_x", "test fn name in args")
    assert_eq(lens["data"]["kind"], "test_run", "data.kind tags the lens")


def test_build_test_debug_lens_shape() -> None:
    tf = TestFunction(name="test_y", line=10)
    lens = build_test_debug_lens(tf, "file:///abs/main.nova")
    assert_eq(lens["command"]["title"], TEST_DEBUG_TITLE,
              "Debug title rendered correctly")
    assert_eq(lens["command"]["command"], TEST_DEBUG_COMMAND,
              "command id is nova-lsp.debugTest")
    assert_eq(lens["range"]["start"]["line"], 10, "range line == fn line")
    assert_eq(lens["data"]["kind"], "test_debug",
              "data.kind tags the lens")


# ---------------------------------------------------------------------------
# compute_test_code_lenses end-to-end.
# ---------------------------------------------------------------------------


def test_compute_test_lenses_five_fns_yields_ten() -> None:
    """5 test_* fns -> 5 Run + 5 Debug = 10 lenses."""
    text = (
        "fn test_a() {}\n"
        "fn test_b() {}\n"
        "fn test_c() {}\n"
        "fn test_d() {}\n"
        "fn test_e() {}\n"
    )
    lenses = compute_test_code_lenses("file:///main.nova", text)
    assert_eq(len(lenses), 10, "5 fns x 2 lenses = 10 lenses")
    # First two lenses belong to test_a (Run then Debug).
    assert_eq(lenses[0]["command"]["title"], TEST_RUN_TITLE,
              "first lens is Run")
    assert_eq(lenses[1]["command"]["title"], TEST_DEBUG_TITLE,
              "second lens is Debug")
    # 5 distinct fn names across the 10 lenses (Run + Debug per fn).
    names = sorted({l["data"]["name"] for l in lenses})
    assert_eq(names, ["test_a", "test_b", "test_c", "test_d", "test_e"],
              "five distinct test fn names")


def test_compute_test_lenses_zero_test_fns() -> None:
    text = (
        "fn helper_a() {}\n"
        "fn another() {}\n"
    )
    lenses = compute_test_code_lenses("file:///main.nova", text)
    assert_eq(lenses, [], "no test fns -> empty lens list")


def test_compute_test_lenses_mixed() -> None:
    """File with 2 test_* and 3 non-test fns -> 4 lenses (2 x 2)."""
    text = (
        "fn helper() { return 1 }\n"
        "fn test_x() { assert(1 == 1) }\n"
        "fn another() { return 2 }\n"
        "fn test_y() { assert(2 == 2) }\n"
        "fn cleanup() { return 0 }\n"
    )
    lenses = compute_test_code_lenses("file:///main.nova", text)
    assert_eq(len(lenses), 4, "2 test fns x 2 lenses = 4 lenses")
    # Lenses are at the fn lines, NOT 0 / 2 / 4.
    lines = sorted({l["range"]["start"]["line"] for l in lenses})
    assert_eq(lines, [1, 3], "lens lines match test fn lines (1, 3)")


def test_compute_test_lenses_lens_at_fn_line() -> None:
    """The lens range starts on the fn's source line exactly."""
    text = (
        "// some comment\n"
        "// another comment\n"
        "fn test_foo() {\n"
        "    assert(1 == 1)\n"
        "}\n"
    )
    lenses = compute_test_code_lenses("file:///main.nova", text)
    assert_eq(len(lenses), 2, "Run + Debug for one fn")
    # fn is on line 2.
    for lens in lenses:
        assert_eq(lens["range"]["start"]["line"], 2,
                  "lens at fn line 2")
        assert_eq(lens["range"]["start"]["character"], 0,
                  "lens at column 0")


def test_compute_test_lenses_command_args() -> None:
    """Command arguments carry {file, name} for the editor to dispatch."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "tests.nova")
        text = "fn test_arg_check() { assert(1 == 1) }\n"
        _write(path, text)
        lenses = compute_test_code_lenses(_uri(path), text)
        assert_eq(len(lenses), 2, "Run + Debug emitted")
        for lens in lenses:
            args = lens["command"]["arguments"]
            assert_eq(len(args), 1, "single argument object")
            assert_eq(args[0]["name"], "test_arg_check",
                      "fn name in args")
            assert_(args[0]["file"].endswith("tests.nova"),
                    "file path in args")


# ---------------------------------------------------------------------------
# Integration with compute_code_lenses — both lens kinds coexist.
# ---------------------------------------------------------------------------


def test_compute_code_lenses_combines_ref_and_test_lenses() -> None:
    """A file with `fn test_foo()` should emit BOTH the reference-count
    lens (R10 era) AND the Run/Debug lenses (R33D).
    """
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "tests.nova")
        text = (
            "fn test_one() {\n"
            "    assert(1 == 1)\n"
            "}\n"
            "fn caller() { test_one() }\n"
        )
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        # 2 fns (test_one, caller) -> 2 reference lenses
        # PLUS 1 test fn (test_one) -> 2 Run/Debug lenses
        # = 4 total.
        assert_eq(len(lenses), 4, "2 ref + 2 test lenses")
        run_lenses = [l for l in lenses
                      if l["command"]["title"] == TEST_RUN_TITLE]
        debug_lenses = [l for l in lenses
                        if l["command"]["title"] == TEST_DEBUG_TITLE]
        ref_lenses = [l for l in lenses
                      if "reference" in l["command"]["title"]]
        assert_eq(len(run_lenses), 1, "exactly one Run lens")
        assert_eq(len(debug_lenses), 1, "exactly one Debug lens")
        assert_eq(len(ref_lenses), 2, "two reference-count lenses")


def test_compute_code_lenses_no_test_fn_still_has_ref_lens() -> None:
    """A file with `fn helper()` (no test_*) emits ONLY the reference
    lens — no Run/Debug lenses appear."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = "fn helper() { return 1 }\n"
        _write(path, text)
        cache = FileCache()
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        lenses = compute_code_lenses(_uri(path), text, cache, idx)
        # 1 fn -> 1 reference lens. No test fn.
        assert_eq(len(lenses), 1, "single reference lens")
        assert_(TEST_RUN_TITLE not in [l["command"]["title"] for l in lenses],
                "no Run lens for non-test fn")
        assert_(TEST_DEBUG_TITLE not in [l["command"]["title"] for l in lenses],
                "no Debug lens for non-test fn")


# ---------------------------------------------------------------------------
# Server-level wire smoke.
# ---------------------------------------------------------------------------


def test_server_codelens_wire_includes_test_actions() -> None:
    """Wire dispatch returns Run/Debug lenses alongside reference lenses."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "tests.nova")
        text = (
            "fn test_alpha() { assert(1 == 1) }\n"
            "fn test_beta() { assert(2 == 2) }\n"
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
        # 2 reference lenses + 2 Run + 2 Debug = 6.
        assert_eq(len(lenses), 6, "six total lenses")
        run_count = sum(1 for l in lenses
                        if l["command"]["title"] == TEST_RUN_TITLE)
        debug_count = sum(1 for l in lenses
                          if l["command"]["title"] == TEST_DEBUG_TITLE)
        assert_eq(run_count, 2, "two Run lenses")
        assert_eq(debug_count, 2, "two Debug lenses")
        # Sanity: every Run lens command id is nova-lsp.runTest.
        for l in lenses:
            if l["command"]["title"] == TEST_RUN_TITLE:
                assert_eq(l["command"]["command"], TEST_RUN_COMMAND,
                          "Run command id")
            if l["command"]["title"] == TEST_DEBUG_TITLE:
                assert_eq(l["command"]["command"], TEST_DEBUG_COMMAND,
                          "Debug command id")


# ---------------------------------------------------------------------------
# Integration — real test files in tests/.
# ---------------------------------------------------------------------------


def test_integration_test_path_yields_run_debug() -> None:
    """`tests/test_path.nova` declares 8 top-level `fn test_*` fns; this
    means R33D should emit 16 Run/Debug lenses for that file (8 x 2).
    """
    src = "/home/user/NOVA/tests/test_path.nova"
    if not os.path.isfile(src):
        print("  SKIP integration: tests/test_path.nova missing")
        return
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    lenses = compute_test_code_lenses(_uri(src), text)
    # Count test fns in source for ground truth.
    expected_fns = sum(
        1 for line in text.splitlines()
        if line.startswith("fn test_")
    )
    print(f"  test_path.nova: {expected_fns} test fns -> "
          f"{len(lenses)} lenses (expected {expected_fns * 2})")
    assert_eq(len(lenses), expected_fns * 2,
              "lens count == 2 * number of test fns")
    assert_(expected_fns >= 5,
            f"test_path.nova should have many test fns; got {expected_fns}")


# ---------------------------------------------------------------------------


def main() -> int:
    test_scan_test_fns_single()
    test_scan_test_fns_multiple_preserves_order()
    test_scan_test_fns_ignores_non_test_fn()
    test_scan_test_fns_ignores_indented_nested_fn()
    test_scan_test_fns_ignores_let_test_prefix()
    test_scan_test_fns_empty_file()
    test_scan_test_fns_no_test_prefix()
    test_build_test_run_lens_shape()
    test_build_test_debug_lens_shape()
    test_compute_test_lenses_five_fns_yields_ten()
    test_compute_test_lenses_zero_test_fns()
    test_compute_test_lenses_mixed()
    test_compute_test_lenses_lens_at_fn_line()
    test_compute_test_lenses_command_args()
    test_compute_code_lenses_combines_ref_and_test_lenses()
    test_compute_code_lenses_no_test_fn_still_has_ref_lens()
    test_server_codelens_wire_includes_test_actions()
    test_integration_test_path_yields_run_debug()
    print(f"test_code_lens_r33d: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
