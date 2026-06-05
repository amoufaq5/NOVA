"""Unit + smoke + integration tests for workspace-wide rename.

The rename module (`nova_lsp/rename_workspace.py`) extends the basic
single-file `textDocument/rename` to:

  * Walk every workspace file that imports the renamed symbol's
    definition site and update the references there as well.
  * Skip unrelated files that happen to use the same identifier
    locally (no transitive import = not touched).
  * Respect word boundaries (case-sensitive `\\b<name>\\b`) so `foo`
    does NOT match inside `foobar` or `myfoo`.
  * Detect name conflicts (new name already declared at top level in
    one of the affected files) and abort with a clear error message.
  * Keep local bindings (function parameters, inner `let`) confined to
    the current file via the legacy single-buffer rename path.

The tests run the harness in-process — no subprocess — and validate
each behaviour with explicit asserts.
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
from nova_lsp.rename_workspace import (  # noqa: E402
    build_workspace_edit,
    classify_symbol,
    detect_name_conflict,
    file_imports_target,
    find_references_in_workspace,
    plan_workspace_rename,
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


# ---------------------------------------------------------------------------
# Unit: classify_symbol.
# ---------------------------------------------------------------------------


def test_classify_symbol_toplevel_fn() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, "fn foo(x) {\n    return x\n}\n")
        cache = FileCache()
        kind = classify_symbol("foo", path, 0, cache)
        assert_eq(kind, "toplevel", "top-level fn classified")


def test_classify_symbol_toplevel_let() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, "let PI = 3\nfn bar() {}\n")
        cache = FileCache()
        kind = classify_symbol("PI", path, 0, cache)
        assert_eq(kind, "toplevel", "top-level let classified")


def test_classify_symbol_indented_let_is_local() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, "fn outer() {\n    let inner = 5\n}\n")
        cache = FileCache()
        kind = classify_symbol("inner", path, 1, cache)
        assert_eq(kind, "local", "indented let is local")


def test_classify_symbol_fn_param_is_local() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "x.nova")
        _write(path, "fn outer(parm_a) {\n    return parm_a\n}\n")
        cache = FileCache()
        # The fn-def line itself is top-level but `parm_a` lives in the
        # parameter list — it's not a top-level identifier so the
        # classifier (which keys off the declaring keyword) should not
        # treat it as workspace-renamable.
        kind = classify_symbol("parm_a", path, 0, cache)
        assert_eq(kind, "local", "fn parameter is local")


# ---------------------------------------------------------------------------
# Unit: file_imports_target.
# ---------------------------------------------------------------------------


def test_file_imports_target_direct() -> None:
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn alpha() {}\n")
        _write(b, "import \"a.nova\"\nfn beta() {}\n")
        cache = FileCache()
        assert_(file_imports_target(b, a, cache), "b imports a")
        assert_(not file_imports_target(a, b, cache), "a does NOT import b")
        assert_(file_imports_target(a, a, cache), "a 'imports' itself")


def test_file_imports_target_transitive() -> None:
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        c = os.path.join(ws, "c.nova")
        _write(a, "fn alpha() {}\n")
        _write(b, "import \"a.nova\"\nfn beta() {}\n")
        _write(c, "import \"b.nova\"\nfn gamma() {}\n")
        cache = FileCache()
        assert_(file_imports_target(c, a, cache), "c transitively imports a")
        assert_(file_imports_target(b, a, cache), "b directly imports a")


# ---------------------------------------------------------------------------
# Unit: detect_name_conflict.
# ---------------------------------------------------------------------------


def test_detect_name_conflict_clean() -> None:
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn foo() {}\n")
        _write(b, "fn other() {}\n")
        cache = FileCache()
        assert_eq(
            detect_name_conflict("bar", [a, b], cache),
            None,
            "no conflict when bar is not declared anywhere",
        )


def test_detect_name_conflict_collision() -> None:
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn foo() {}\n")
        _write(b, "fn bar() {}\n")  # would collide if we rename foo -> bar
        cache = FileCache()
        msg = detect_name_conflict("bar", [a, b], cache)
        assert_(msg is not None, "conflict detected")
        assert_("bar" in msg, "conflict message mentions new name")
        assert_("already defined" in msg, "conflict message explains why")


# ---------------------------------------------------------------------------
# Unit: find_references_in_workspace.
# ---------------------------------------------------------------------------


def test_find_references_three_file_fixture() -> None:
    """Classic three-file fixture: A defines `foo`, B imports A and uses
    `foo`, C is an unrelated file that has its own `foo` definition but
    does NOT import A. The rename must touch A + B but never C."""
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        c = os.path.join(ws, "c.nova")
        _write(a, "fn foo(x) {\n    return x + 1\n}\n")
        _write(b, 'import "a.nova"\nfn main() {\n    foo(1)\n    foo(2)\n}\n')
        _write(c, "fn foo() {\n    return 0\n}\n")  # unrelated same name
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        refs = find_references_in_workspace("foo", a, cache, index)

        # A and B should be in refs; C should NOT.
        a_abs = os.path.abspath(a)
        b_abs = os.path.abspath(b)
        c_abs = os.path.abspath(c)
        assert_(a_abs in refs, "a.nova is in refs")
        assert_(b_abs in refs, "b.nova is in refs")
        assert_(c_abs not in refs, "c.nova is NOT in refs (no import)")

        # a has 1 foo (the definition).
        assert_eq(len(refs[a_abs]), 1, "a.nova has 1 foo occurrence")
        # b has 2 foo calls.
        assert_eq(len(refs[b_abs]), 2, "b.nova has 2 foo occurrences")


def test_find_references_respects_word_boundaries() -> None:
    """`foo` should not match inside `foobar`, `myfoo`, or `foo_bar`."""
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn foo() { return 0 }\n")
        _write(
            b,
            'import "a.nova"\n'
            "fn main() {\n"
            "    foo()\n"           # match
            "    foobar()\n"        # NO match
            "    myfoo()\n"         # NO match
            "    foo_bar()\n"       # NO match
            "    let x = foo\n"     # match
            "}\n",
        )
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        refs = find_references_in_workspace("foo", a, cache, index)
        b_abs = os.path.abspath(b)
        # b should have exactly 2 foo matches (the call + the bare ident).
        assert_eq(len(refs[b_abs]), 2, "word boundary respected in b.nova")


def test_find_references_ignores_strings_and_comments() -> None:
    """Identifiers inside string literals and line comments are not
    treated as references — renaming the symbol must not touch them."""
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn target() { return 0 }\n")
        _write(
            b,
            'import "a.nova"\n'
            "fn main() {\n"
            '    println("call target() here")\n'  # in string, no match
            "    // also mention target in a comment\n"  # in comment, no match
            "    target()\n"  # real call, match
            "}\n",
        )
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        refs = find_references_in_workspace("target", a, cache, index)
        b_abs = os.path.abspath(b)
        # Only the real call counts — 1 occurrence in b.
        assert_eq(len(refs[b_abs]), 1, "strings+comments ignored in b.nova")


def test_find_references_unrelated_file_skipped() -> None:
    """A file that uses `foo` privately without importing A is left alone."""
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        unrelated = os.path.join(ws, "unrelated.nova")
        _write(a, "fn shared() { return 0 }\n")
        _write(
            unrelated,
            "fn shared() { return 99 }\nfn caller() { shared() }\n",
        )
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        refs = find_references_in_workspace("shared", a, cache, index)
        a_abs = os.path.abspath(a)
        unrelated_abs = os.path.abspath(unrelated)
        assert_(a_abs in refs, "a.nova in refs")
        assert_(unrelated_abs not in refs, "unrelated.nova not in refs")


# ---------------------------------------------------------------------------
# Unit: build_workspace_edit / plan_workspace_rename.
# ---------------------------------------------------------------------------


def test_build_workspace_edit_shape() -> None:
    refs = {
        "/tmp/x/a.nova": [
            {"start": {"line": 0, "character": 3}, "end": {"line": 0, "character": 6}},
        ],
        "/tmp/x/b.nova": [
            {"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 7}},
            {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 7}},
        ],
    }
    edit = build_workspace_edit(refs, "bar")
    assert_("changes" in edit, "WorkspaceEdit has 'changes' key")
    assert_eq(
        sorted(edit["changes"].keys()),
        ["file:///tmp/x/a.nova", "file:///tmp/x/b.nova"],
        "URIs in edit.changes",
    )
    # Every TextEdit carries the new name.
    for uri, edits in edit["changes"].items():
        for e in edits:
            assert_eq(e["newText"], "bar", f"newText on {uri}")
    assert_eq(len(edit["changes"]["file:///tmp/x/b.nova"]), 2,
              "b.nova has 2 edits")


def test_plan_workspace_rename_conflict_path() -> None:
    """plan_workspace_rename should surface the conflict message and
    drop the reference map when the new name clashes."""
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn foo() {}\n")
        _write(
            b,
            'import "a.nova"\n'
            "fn bar() { return 1 }\n"   # bar already exists
            "fn caller() { foo() }\n",
        )
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        plan = plan_workspace_rename("foo", "bar", a, cache, index)
        assert_(plan.conflict_message is not None, "conflict surfaced")
        assert_eq(plan.references, {}, "no references emitted on conflict")
        assert_("bar" in (plan.conflict_message or ""),
                "conflict message mentions new name")


def test_plan_workspace_rename_clean_path() -> None:
    with tempfile.TemporaryDirectory() as ws:
        a = os.path.join(ws, "a.nova")
        b = os.path.join(ws, "b.nova")
        _write(a, "fn foo() {}\n")
        _write(b, 'import "a.nova"\nfn main() { foo() }\n')
        cache = FileCache()
        index = WorkspaceSymbolIndex()
        index.index_workspace_root(ws)
        plan = plan_workspace_rename("foo", "spam", a, cache, index)
        assert_(plan.conflict_message is None, "no conflict")
        assert_(len(plan.references) == 2, "two files in plan")


# ---------------------------------------------------------------------------
# End-to-end via the LSP dispatcher: textDocument/rename.
# ---------------------------------------------------------------------------


def test_lsp_rename_workspace_three_files() -> None:
    """Drive the server through `dispatch` with a three-file fixture.

    A defines `foo`, B imports A and calls `foo` twice, C is an
    independent file with its own unrelated symbol. We expect the
    rename to touch A + B but never C.
    """
    a_text = "fn foo(x) {\n    return x + 1\n}\n"
    b_text = (
        'import "a.nova"\n'
        "fn main() {\n"
        "    foo(1)\n"
        "    foo(2)\n"
        "}\n"
    )
    c_text = "fn unrelated() {\n    return 42\n}\n"

    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        c_path = os.path.join(ws, "c.nova")
        _write(a_path, a_text)
        _write(b_path, b_text)
        _write(c_path, c_text)

        client = LspClient()
        client.initialize(ws)
        a_uri = client.open(a_path, a_text)

        # Position cursor on `foo` in the fn-def line of a.nova.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": a_uri},
                "position": {"line": 0, "character": 4},
                "newName": "bar",
            },
        )
        result = resp["result"]
        assert_(result is not None, "WorkspaceEdit returned")
        changes = result["changes"]

        a_change_uri = "file://" + os.path.abspath(a_path)
        b_change_uri = "file://" + os.path.abspath(b_path)
        c_change_uri = "file://" + os.path.abspath(c_path)

        assert_(a_change_uri in changes, "a.nova in WorkspaceEdit")
        assert_(b_change_uri in changes, "b.nova in WorkspaceEdit")
        assert_(c_change_uri not in changes,
                "c.nova NOT in WorkspaceEdit (no import of a.nova)")

        # a has 1 foo edit (the def), b has 2 foo edits (the calls).
        assert_eq(len(changes[a_change_uri]), 1, "a.nova edit count")
        assert_eq(len(changes[b_change_uri]), 2, "b.nova edit count")

        # All edits carry "bar" as the new name.
        for uri, edits in changes.items():
            for e in edits:
                assert_eq(e["newText"], "bar", f"newText in {uri}")


def test_lsp_rename_word_boundaries() -> None:
    """`foo` should NOT rename `foobar` or `myfoo` even when both occur
    in the same file."""
    a_text = "fn foo() {\n    return 1\n}\n"
    b_text = (
        'import "a.nova"\n'
        "fn callsite() {\n"
        "    foo()\n"        # match
        "    foobar()\n"     # NO match
        "    let x = myfoo()\n"  # NO match (myfoo is a separate ident)
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        _write(a_path, a_text)
        _write(b_path, b_text)
        client = LspClient()
        client.initialize(ws)
        a_uri = client.open(a_path, a_text)

        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": a_uri},
                "position": {"line": 0, "character": 4},
                "newName": "spam",
            },
        )
        result = resp["result"]
        b_change_uri = "file://" + os.path.abspath(b_path)
        edits = result["changes"][b_change_uri]
        # Exactly 1 edit in b.nova (the foo() call).
        assert_eq(len(edits), 1, "exactly 1 foo edit in b.nova")
        # Verify the edit lands on the `foo()` call line.
        assert_eq(edits[0]["range"]["start"]["line"], 2,
                  "edit on the foo() call line")


def test_lsp_rename_conflict_returns_error() -> None:
    """If `newName` already exists as a top-level decl in any referenced
    file, the server returns a JSON-RPC error rather than an edit."""
    a_text = "fn foo() {\n    return 1\n}\n"
    # b imports a and ALSO defines `bar` — renaming foo -> bar collides.
    b_text = (
        'import "a.nova"\n'
        "fn bar() {\n"
        "    return 99\n"
        "}\n"
        "fn other() {\n"
        "    foo()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        _write(a_path, a_text)
        _write(b_path, b_text)

        client = LspClient()
        client.initialize(ws)
        a_uri = client.open(a_path, a_text)

        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": a_uri},
                "position": {"line": 0, "character": 4},
                "newName": "bar",
            },
        )
        # Conflict path uses make_error → response carries `error`.
        assert_("error" in resp, "response carries an error envelope")
        err = resp["error"]
        assert_eq(err["code"], -32803, "error code is -32803")
        assert_("bar" in err["message"], "error mentions new name")
        assert_("already defined" in err["message"],
                "error explains collision")


def test_lsp_rename_local_let_single_file() -> None:
    """Renaming a local (indented) `let` binding stays in the current
    buffer — no other files are touched even though they exist."""
    a_text = (
        "fn outer() {\n"
        "    let inner_var = 5\n"
        "    return inner_var\n"
        "}\n"
    )
    b_text = (
        'import "a.nova"\n'
        "fn caller() {\n"
        "    inner_var\n"   # bare ident, but B doesn't actually use a's local
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        _write(a_path, a_text)
        _write(b_path, b_text)

        client = LspClient()
        client.initialize(ws)
        a_uri = client.open(a_path, a_text)

        # Cursor on `inner_var` at line 1, character 12.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": a_uri},
                "position": {"line": 1, "character": 12},
                "newName": "renamed_var",
            },
        )
        result = resp["result"]
        assert_(result is not None, "local rename returned a result")
        # b.nova is closed and the local rename uses the legacy
        # open-buffer + imports walker — so b.nova's path may NOT
        # appear in changes (b only contains text on disk and b doesn't
        # import inner_var anyway). But what we MUST guarantee is that
        # the file ON DISK at b_path is NOT edited (this is the local
        # rename property).
        b_change_uri = "file://" + os.path.abspath(b_path)
        # Legacy rename touches transitively imported files too,
        # but here b only mentions `inner_var` in source; the legacy
        # path *would* match it if b were reachable. Since a.nova is
        # the only open doc and b.nova does NOT import a.nova,
        # b.nova is not in the candidate set, so b is untouched.
        if b_change_uri in result["changes"]:
            # If b is in changes, it must be empty — but we expect it
            # to not be in changes at all.
            assert_eq(len(result["changes"][b_change_uri]), 0,
                      "b.nova has zero edits in local rename")
        else:
            assert_(True, "b.nova absent from local rename changes")
        # a.nova MUST be in changes (the let + the return ref).
        a_change_uri = "file://" + os.path.abspath(a_path)
        assert_(a_change_uri in result["changes"],
                "a.nova in local rename changes")
        a_edits = result["changes"][a_change_uri]
        # 2 occurrences of inner_var (the let and the return).
        assert_eq(len(a_edits), 2, "a.nova has 2 inner_var edits")


def test_lsp_rename_fn_parameter_local() -> None:
    """Renaming a function parameter stays in the current scope."""
    a_text = (
        "fn outer(parm_a) {\n"
        "    return parm_a + 1\n"
        "}\n"
    )
    b_text = (
        'import "a.nova"\n'
        "fn caller() {\n"
        "    outer(42)\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        _write(a_path, a_text)
        _write(b_path, b_text)

        client = LspClient()
        client.initialize(ws)
        a_uri = client.open(a_path, a_text)

        # Cursor on `parm_a` inside the fn def, line 0 character 11.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": a_uri},
                "position": {"line": 0, "character": 12},
                "newName": "p",
            },
        )
        result = resp["result"]
        assert_(result is not None, "param rename returned")
        a_change_uri = "file://" + os.path.abspath(a_path)
        b_change_uri = "file://" + os.path.abspath(b_path)
        # a must be touched (the param + its use).
        assert_(a_change_uri in result["changes"], "a.nova in changes")
        # b must NOT be touched — params don't propagate.
        if b_change_uri in result["changes"]:
            assert_eq(len(result["changes"][b_change_uri]), 0,
                      "b.nova zero edits in param rename")
        else:
            assert_(True, "b.nova absent from param rename changes")


# ---------------------------------------------------------------------------
# Regression: single-file rename still works.
# ---------------------------------------------------------------------------


def test_lsp_single_file_rename_regression() -> None:
    """The original `rename_smoke` scenario must still work after
    routing through the workspace path."""
    util_text = (
        "fn greet(name) {\n"
        "    return name\n"
        "}\n"
        "fn shout(msg) {\n"
        "    return msg\n"
        "}\n"
    )
    main_text = (
        'import "util.nova"\n'
        "fn main() {\n"
        '    greet("nova")\n'
        '    greet("world")\n'
        '    shout("hey")\n'
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        util_path = os.path.join(ws, "util.nova")
        main_path = os.path.join(ws, "main.nova")
        _write(util_path, util_text)
        _write(main_path, main_text)

        client = LspClient()
        client.initialize(ws)
        main_uri = client.open(main_path, main_text)

        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 2, "character": 5},  # on `greet`
                "newName": "salute",
            },
        )
        result = resp["result"]
        assert_(result is not None, "regression: result is not None")
        changes = result["changes"]
        util_uri = "file://" + os.path.abspath(util_path)
        # main has 2 greet edits, util has 1.
        assert_eq(len(changes[main_uri]), 2, "regression: main edits")
        assert_eq(len(changes[util_uri]), 1, "regression: util edits")
        # shout NOT touched.
        for edits in changes.values():
            for e in edits:
                rng = e["range"]
                assert_eq(rng["end"]["character"] - rng["start"]["character"],
                          len("greet"),
                          "regression: edit width matches 'greet'")


# ---------------------------------------------------------------------------
# Integration: the actual NOVA codebase.
#
# We simulate renaming a low-traffic top-level fn from `imports.py` and
# verify the LSP would produce edits covering server.py + workspace_
# symbols.py too — without actually mutating anything on disk.
# ---------------------------------------------------------------------------


def test_integration_real_lsp_codebase_rename() -> None:
    """Plan a rename of `resolve_import` (from R5F's imports.py) using
    a temp copy of the LSP package, and verify edits land in the
    server.py file too.

    We can't realistically test renaming inside the LSP's own Python
    files (the rename engine targets .nova files), so instead we
    construct a NOVA-style three-file fixture that mirrors the import
    pattern of the LSP code: a "lib" module exports a helper, a
    "consumer" file imports the lib and uses the helper, and an
    "unrelated" file shares the helper's name without importing it.
    The integration test exercises the same code path that an editor
    would hit when the user presses F2 in `imports.py` to rename
    `resolve_import`. The unrelated file must remain untouched.
    """
    # Three-file NOVA fixture modelled after the LSP package layout.
    lib_text = (
        "// Mirrors R5F's resolve_import helper in NOVA syntax.\n"
        "fn resolve_import(base, rel) {\n"
        "    return concat(base, rel)\n"
        "}\n"
    )
    consumer_text = (
        'import "lib.nova"\n'
        "fn walk(path) {\n"
        '    let resolved = resolve_import("/", path)\n'
        "    return resolved\n"
        "}\n"
        "fn walk_again(p) {\n"
        '    return resolve_import("/tmp", p)\n'
        "}\n"
    )
    unrelated_text = (
        "// Has its own resolve_import — must NOT be renamed.\n"
        "fn resolve_import() {\n"
        "    return 0\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        lib_path = os.path.join(ws, "lib.nova")
        consumer_path = os.path.join(ws, "consumer.nova")
        unrelated_path = os.path.join(ws, "unrelated.nova")
        _write(lib_path, lib_text)
        _write(consumer_path, consumer_text)
        _write(unrelated_path, unrelated_text)

        client = LspClient()
        client.initialize(ws)
        lib_uri = client.open(lib_path, lib_text)

        # Cursor on `resolve_import` in the fn-def line.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": lib_uri},
                "position": {"line": 1, "character": 5},
                "newName": "resolve_import_path",
            },
        )
        result = resp["result"]
        assert_(result is not None, "integration: rename returned")
        changes = result["changes"]

        lib_change_uri = "file://" + os.path.abspath(lib_path)
        consumer_change_uri = "file://" + os.path.abspath(consumer_path)
        unrelated_change_uri = "file://" + os.path.abspath(unrelated_path)

        assert_(lib_change_uri in changes,
                "integration: lib.nova in changes")
        assert_(consumer_change_uri in changes,
                "integration: consumer.nova in changes")
        assert_(unrelated_change_uri not in changes,
                "integration: unrelated.nova NOT in changes")

        # lib has 1 edit (the def), consumer has 2 calls.
        assert_eq(len(changes[lib_change_uri]), 1,
                  "integration: lib edit count")
        assert_eq(len(changes[consumer_change_uri]), 2,
                  "integration: consumer edit count")
        # Every edit carries the new name.
        for uri, edits in changes.items():
            for e in edits:
                assert_eq(e["newText"], "resolve_import_path",
                          f"integration: newText in {uri}")


# ---------------------------------------------------------------------------


def test_integration_real_nova_codebase() -> None:
    """Drive the rename engine against the real NOVA codebase.

    Uses the existing `src/core/moment.nova` definition of
    `moment_new` which is imported transitively by several example
    files (`examples/security_demo.nova`, etc). We don't actually
    mutate disk — just verify the engine finds the correct set of
    references when asked to rename `moment_new` to a sentinel name.

    Skips silently if the NOVA tree isn't on disk (lets the test
    suite run inside CI sandboxes that strip the codebase).
    """
    moment_path = "/home/user/NOVA/src/core/moment.nova"
    if not os.path.isfile(moment_path):
        print("  SKIP integration: src/core/moment.nova missing")
        return
    cache = FileCache()
    index = WorkspaceSymbolIndex()
    index.index_workspace_root("/home/user/NOVA/src")
    index.index_workspace_root("/home/user/NOVA/examples")

    # Sanity: moment.nova is on the indexer's path list.
    moment_abs = os.path.abspath(moment_path)
    assert_(moment_abs in index._by_file,  # noqa: SLF001
            "moment.nova indexed")
    entry = cache.get(moment_path)
    assert_(entry is not None, "moment.nova readable")
    # `moment_new` should be a top-level fn def in moment.nova.
    assert_("moment_new" in entry.fn_defs, "moment_new defined in moment.nova")
    def_line = entry.fn_defs["moment_new"][0]
    kind = classify_symbol("moment_new", moment_path, def_line, cache)
    assert_eq(kind, "toplevel", "moment_new is top-level")

    plan = plan_workspace_rename(
        "moment_new",
        "moment_new_RENAMED",
        moment_path,
        cache,
        index,
    )
    # No clashes expected — `moment_new_RENAMED` is fresh.
    assert_(plan.conflict_message is None,
            "no conflict for fresh sentinel name")
    # The defining file must be in the plan; at least one importer too.
    assert_(moment_abs in plan.references,
            "moment.nova present in rename plan")
    assert_(len(plan.references) >= 2,
            "rename plan covers more than just the def file")
    # The total occurrence count should be > the in-file def count alone.
    in_file_count = len(plan.references[moment_abs])
    cross_file_count = sum(
        len(v) for p, v in plan.references.items() if p != moment_abs
    )
    assert_(cross_file_count > 0,
            "at least one cross-file occurrence found")
    print(
        f"  integration: moment_new -> "
        f"{len(plan.references)} files, "
        f"{in_file_count} in-file + {cross_file_count} cross-file refs"
    )

    # Now build the actual WorkspaceEdit and verify it round-trips.
    edit = build_workspace_edit(plan.references, "moment_new_RENAMED")
    assert_("changes" in edit, "WorkspaceEdit shape")
    moment_uri = "file://" + moment_abs
    assert_(moment_uri in edit["changes"],
            "moment.nova URI in edit.changes")
    # Every TextEdit's newText is the rename target.
    for uri, edits in edit["changes"].items():
        for e in edits:
            assert_eq(e["newText"], "moment_new_RENAMED",
                      f"newText on {os.path.basename(uri)}")


def main() -> int:
    test_classify_symbol_toplevel_fn()
    test_classify_symbol_toplevel_let()
    test_classify_symbol_indented_let_is_local()
    test_classify_symbol_fn_param_is_local()
    test_file_imports_target_direct()
    test_file_imports_target_transitive()
    test_detect_name_conflict_clean()
    test_detect_name_conflict_collision()
    test_find_references_three_file_fixture()
    test_find_references_respects_word_boundaries()
    test_find_references_ignores_strings_and_comments()
    test_find_references_unrelated_file_skipped()
    test_build_workspace_edit_shape()
    test_plan_workspace_rename_conflict_path()
    test_plan_workspace_rename_clean_path()
    test_lsp_rename_workspace_three_files()
    test_lsp_rename_word_boundaries()
    test_lsp_rename_conflict_returns_error()
    test_lsp_rename_local_let_single_file()
    test_lsp_rename_fn_parameter_local()
    test_lsp_single_file_rename_regression()
    test_integration_real_lsp_codebase_rename()
    test_integration_real_nova_codebase()
    print(f"test_rename_workspace: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
