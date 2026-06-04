"""R32E — `textDocument/prepareRename` + cross-file `textDocument/rename` tests.

This suite layers the R32E rename guarantees on top of the existing
workspace-rename tests:

  * `prepareRename` returns the identifier's range + placeholder for
    renameable positions and `null` for keywords / comments / strings
    / whitespace.
  * `rename` validates the new name against the NOVA keyword table and
    the `[A-Za-z_][A-Za-z0-9_]*` shape, returning -32602 InvalidParams
    on failure.
  * Local-variable / parameter renames stay confined to the enclosing
    fn's brace-counted scope — sibling fns are NOT touched, callsites
    of the fn are NOT touched.
  * Same-scope shadowing emits the edit but precedes it with a
    `window/showMessage` warning notification.
  * Top-level fn rename propagates across every workspace file that
    imports the definition site.
  * Comment-internal + string-literal occurrences of the same word are
    NEVER rewritten.
  * Capability advertisement: `renameProvider.prepareProvider == true`.

We drive the server through the in-process harness (no subprocess),
matching the rest of the LSP test suite for fast (~10 ms) iteration.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

# Make the lsp package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from _harness import LspClient  # noqa: E402
from nova_lsp.prepare_rename import (  # noqa: E402
    NOVA_KEYWORDS,
    detect_same_scope_shadow,
    enclosing_fn_range,
    identifier_range_at,
    is_keyword,
    is_valid_identifier,
    mask_comments_and_strings,
    scope_constrained_occurrences,
    validate_new_name,
)


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


# ---------------------------------------------------------------------------
# Unit: is_valid_identifier / is_keyword / validate_new_name.
# ---------------------------------------------------------------------------


def test_unit_identifier_shape_check() -> None:
    assert_(is_valid_identifier("foo"), "foo is identifier")
    assert_(is_valid_identifier("_underscore"), "_underscore is identifier")
    assert_(is_valid_identifier("camelCase"), "camelCase is identifier")
    assert_(is_valid_identifier("snake_case_42"), "snake_case_42 is identifier")
    assert_(not is_valid_identifier("123foo"), "123foo is NOT identifier")
    assert_(not is_valid_identifier("foo bar"), "foo bar is NOT identifier")
    assert_(not is_valid_identifier(""), "empty is NOT identifier")
    assert_(not is_valid_identifier("foo-bar"), "foo-bar is NOT identifier")


def test_unit_keyword_table() -> None:
    # Every keyword called out in the R32E spec must be reserved.
    for kw in ("fn", "let", "match", "enum", "struct", "if", "else",
               "while", "for", "return", "import", "mut", "const",
               "type", "true", "false"):
        assert_(is_keyword(kw), f"{kw} is a NOVA keyword")
    # Identifiers that LOOK like keywords but aren't are not flagged.
    assert_(not is_keyword("fnord"), "fnord is NOT a keyword")
    assert_(not is_keyword("matcher"), "matcher is NOT a keyword")
    assert_(not is_keyword("foo"), "foo is NOT a keyword")


def test_unit_validate_new_name() -> None:
    assert_eq(validate_new_name("renamed"), None, "renamed accepted")
    assert_eq(validate_new_name("Renamed_42"), None, "mixed-case accepted")
    msg = validate_new_name("123bad")
    assert_(msg is not None and "valid NOVA identifier" in msg,
            "leading digit rejected")
    msg = validate_new_name("if")
    assert_(msg is not None and "keyword" in msg, "if keyword rejected")
    msg = validate_new_name("fn")
    assert_(msg is not None and "keyword" in msg, "fn keyword rejected")
    msg = validate_new_name("")
    assert_(msg is not None, "empty new name rejected")


# ---------------------------------------------------------------------------
# Unit: identifier_range_at — the prepareRename engine.
# ---------------------------------------------------------------------------


def test_unit_identifier_range_basic() -> None:
    text = "fn foo(bar) {\n    return bar\n}\n"
    # Cursor on "foo".
    hit = identifier_range_at(text, 0, 4)
    assert_(hit is not None, "cursor on foo found")
    name, rng = hit
    assert_eq(name, "foo", "name is foo")
    assert_eq(rng["start"]["character"], 3, "foo start char")
    assert_eq(rng["end"]["character"], 6, "foo end char")
    # Cursor on "bar" inside fn body.
    hit2 = identifier_range_at(text, 1, 12)
    assert_(hit2 is not None, "cursor on bar (return) found")
    n2, _r2 = hit2
    assert_eq(n2, "bar", "second hit is bar")


def test_unit_identifier_range_on_keyword_returns_none() -> None:
    # Cursor on the `fn` keyword itself — prepareRename refuses.
    text = "fn foo(bar) {\n    return bar\n}\n"
    hit = identifier_range_at(text, 0, 1)
    assert_eq(hit, None, "cursor on `fn` keyword -> None")
    # Cursor on `return` keyword.
    hit2 = identifier_range_at(text, 1, 7)
    assert_eq(hit2, None, "cursor on `return` -> None")


def test_unit_identifier_range_on_whitespace_returns_none() -> None:
    text = "fn foo(bar) {\n    return bar\n}\n"
    # Cursor in the leading-whitespace gap of line 1.
    hit = identifier_range_at(text, 1, 2)
    assert_eq(hit, None, "cursor on whitespace -> None")


def test_unit_identifier_range_in_comment_returns_none() -> None:
    text = "// foo is a comment word\nfn bar() {}\n"
    # Cursor on `foo` inside the comment.
    hit = identifier_range_at(text, 0, 4)
    assert_eq(hit, None, "cursor inside // comment -> None")
    # Cursor on `bar` (legit identifier) still works.
    hit2 = identifier_range_at(text, 1, 4)
    assert_(hit2 is not None, "cursor on bar still renameable")


def test_unit_identifier_range_in_string_returns_none() -> None:
    text = 'fn main() {\n    println("foo bar baz")\n}\n'
    # Cursor on `foo` inside the string literal.
    hit = identifier_range_at(text, 1, 14)
    assert_eq(hit, None, "cursor inside string -> None")
    # Cursor on `main` (real ident) works.
    hit2 = identifier_range_at(text, 0, 4)
    assert_(hit2 is not None, "main is renameable")


def test_unit_identifier_range_on_numeric_literal_returns_none() -> None:
    text = "let x = 42\n"
    # Cursor on `42`. The boundary walk could land on it.
    hit = identifier_range_at(text, 0, 8)
    assert_eq(hit, None, "cursor on 42 -> None (not a valid ident)")


# ---------------------------------------------------------------------------
# Unit: enclosing_fn_range + scope_constrained_occurrences.
# ---------------------------------------------------------------------------


def test_unit_enclosing_fn_simple() -> None:
    text = (
        "fn outer(x) {\n"          # line 0
        "    let y = x + 1\n"      # line 1
        "    return y\n"           # line 2
        "}\n"                      # line 3
        "fn other(z) {\n"          # line 4
        "    return z\n"           # line 5
        "}\n"                      # line 6
    )
    assert_eq(enclosing_fn_range(text, 1), (0, 3), "line 1 -> outer")
    assert_eq(enclosing_fn_range(text, 5), (4, 6), "line 5 -> other")
    # Line at file scope outside any fn.
    assert_eq(enclosing_fn_range(text, 7), None, "line 7 not in any fn")


def test_unit_scope_constrained_occurrences() -> None:
    text = (
        "fn outer(x) {\n"
        "    let z = x + x\n"
        "    return z\n"
        "}\n"
        "fn other(x) {\n"           # SAME name in sibling fn, must NOT be touched
        "    return x * 2\n"
        "}\n"
    )
    scope = enclosing_fn_range(text, 1)
    assert_(scope is not None, "outer scope found")
    ranges = scope_constrained_occurrences(text, "x", scope)
    # Three x occurrences in outer: param `x`, `x + x` (twice). NOT the
    # other fn's x.
    assert_eq(len(ranges), 3, "3 x occurrences in outer scope")
    # Every occurrence's line is inside [0, 3].
    for r in ranges:
        ln = r["start"]["line"]
        assert_(0 <= ln <= 3, f"line {ln} in outer scope")


def test_unit_scope_skips_comments_and_strings() -> None:
    text = (
        "fn outer(parm) {\n"
        '    // mentions parm in a comment\n'
        '    println("parm in a string")\n'
        "    return parm\n"
        "}\n"
    )
    scope = enclosing_fn_range(text, 1)
    ranges = scope_constrained_occurrences(text, "parm", scope)
    # Only 2 real occurrences: the param decl + the return.
    assert_eq(len(ranges), 2, "comments+strings skipped in scope scan")


# ---------------------------------------------------------------------------
# Unit: mask_comments_and_strings + detect_same_scope_shadow.
# ---------------------------------------------------------------------------


def test_unit_mask_comments_and_strings() -> None:
    line = 'foo("bar baz") // qux'
    masked = mask_comments_and_strings(line)
    # `foo` survives, the string + comment body are blanked.
    assert_("foo" in masked, "foo unchanged")
    assert_("bar" not in masked, "string body blanked")
    assert_("qux" not in masked, "comment body blanked")


def test_unit_detect_shadow_let() -> None:
    text = (
        "fn outer(p) {\n"
        "    let existing = 1\n"
        "    return p\n"
        "}\n"
    )
    scope = enclosing_fn_range(text, 1)
    # Renaming `p` -> `existing` shadows the let.
    msg = detect_same_scope_shadow(text, "existing", scope)
    assert_(msg is not None, "shadow detected for `existing`")
    assert_("shadow" in msg, "warning mentions shadow")
    # Renaming `p` -> `fresh` does NOT shadow.
    msg2 = detect_same_scope_shadow(text, "fresh", scope)
    assert_eq(msg2, None, "no shadow for fresh name")


def test_unit_detect_shadow_param() -> None:
    text = (
        "fn outer(parm_a, parm_b) {\n"
        "    return parm_a\n"
        "}\n"
    )
    scope = enclosing_fn_range(text, 1)
    # Renaming `parm_a` -> `parm_b` shadows the existing param.
    msg = detect_same_scope_shadow(text, "parm_b", scope)
    assert_(msg is not None, "shadow detected for sibling param")


# ---------------------------------------------------------------------------
# End-to-end: prepareRename via dispatcher.
# ---------------------------------------------------------------------------


def test_lsp_prepare_rename_on_identifier() -> None:
    text = "fn greet(name) {\n    return name\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        init = client.initialize(ws)
        # R32E capability shape.
        rp = init["result"]["capabilities"]["renameProvider"]
        assert_(isinstance(rp, dict) and rp.get("prepareProvider") is True,
                "renameProvider.prepareProvider advertised")

        uri = client.open(path, text)
        resp = client.request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 4},  # on `greet`
            },
        )
        result = resp["result"]
        assert_(result is not None, "prepareRename returns a payload")
        assert_eq(result["placeholder"], "greet", "placeholder is greet")
        assert_eq(result["range"]["start"]["character"], 3, "range start col")
        assert_eq(result["range"]["end"]["character"], 8, "range end col")


def test_lsp_prepare_rename_on_keyword_returns_null() -> None:
    text = "fn greet(name) {\n    return name\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        # Cursor on `return` keyword.
        resp = client.request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 7},
            },
        )
        assert_eq(resp["result"], None, "prepareRename on keyword -> null")


def test_lsp_prepare_rename_on_whitespace_returns_null() -> None:
    text = "fn greet(name) {\n    return name\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        # Cursor in the leading whitespace of the body.
        resp = client.request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 2},
            },
        )
        assert_eq(resp["result"], None, "prepareRename on whitespace -> null")


def test_lsp_prepare_rename_in_string_returns_null() -> None:
    text = 'fn main() {\n    println("greet world")\n}\n'
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        # Cursor inside the string literal.
        resp = client.request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 14},
            },
        )
        assert_eq(resp["result"], None, "prepareRename in string -> null")


# ---------------------------------------------------------------------------
# End-to-end: rename validation rejects bad names.
# ---------------------------------------------------------------------------


def test_lsp_rename_invalid_identifier_returns_error() -> None:
    text = "fn foo() {\n    return 1\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        for bad in ("123foo", "foo bar", "foo-bar"):
            resp = client.request(
                "textDocument/rename",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": 0, "character": 4},
                    "newName": bad,
                },
            )
            assert_("error" in resp, f"`{bad}` returns an error")
            assert_eq(resp["error"]["code"], -32602,
                      f"`{bad}` -> -32602 InvalidParams")


def test_lsp_rename_to_keyword_returns_error() -> None:
    text = "fn foo() {\n    return 1\n}\n"
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        for kw in ("if", "fn", "match", "let"):
            resp = client.request(
                "textDocument/rename",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": 0, "character": 4},
                    "newName": kw,
                },
            )
            assert_("error" in resp, f"`{kw}` returns an error")
            assert_eq(resp["error"]["code"], -32602,
                      f"`{kw}` -> -32602 InvalidParams")
            assert_("keyword" in resp["error"]["message"],
                    "error message mentions keyword")


# ---------------------------------------------------------------------------
# End-to-end: local var rename — 5 occurrences in same fn all updated.
# ---------------------------------------------------------------------------


def test_lsp_rename_local_var_five_occurrences() -> None:
    text = (
        "fn add5(x) {\n"
        "    let acc = x\n"      # acc occurrence #1
        "    acc = acc + 1\n"    # #2, #3
        "    acc = acc + 1\n"    # #4, #5
        "    acc = acc + 1\n"    # #6, #7
        "    return acc\n"       # #8
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        # Cursor on `acc` at the let.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 9},
                "newName": "accumulator",
            },
        )
        result = resp["result"]
        assert_(result is not None, "local var rename returned")
        edits = result["changes"][uri]
        # 8 acc occurrences expected.
        assert_eq(len(edits), 8, "all 8 acc occurrences renamed")
        for e in edits:
            assert_eq(e["newText"], "accumulator", "every edit has new name")


# ---------------------------------------------------------------------------
# End-to-end: param rename stays inside fn body (sibling fn untouched).
# ---------------------------------------------------------------------------


def test_lsp_rename_param_does_not_touch_sibling_fn() -> None:
    text = (
        "fn outer(x) {\n"          # line 0 — `x` is the renaming target
        "    return x + x\n"       # line 1
        "}\n"                      # line 2
        "fn other(x) {\n"          # line 3 — sibling fn, must NOT be touched
        "    return x * 2\n"       # line 4
        "}\n"                      # line 5
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        # Cursor on `x` in outer's param list.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 9},
                "newName": "renamed_x",
            },
        )
        result = resp["result"]
        assert_(result is not None, "param rename returned")
        edits = result["changes"][uri]
        # Exactly 3 outer occurrences (param + 2 uses), NONE in sibling.
        assert_eq(len(edits), 3, "3 x edits in outer scope")
        for e in edits:
            ln = e["range"]["start"]["line"]
            assert_(0 <= ln <= 2, f"edit line {ln} in outer scope")


def test_lsp_rename_param_does_not_touch_callsites() -> None:
    """A function PARAMETER rename doesn't touch callsites of the fn —
    those are still passing positional / named args, not the param name."""
    a_text = (
        "fn helper(parm_a) {\n"
        "    return parm_a + 1\n"
        "}\n"
    )
    b_text = (
        'import "a.nova"\n'
        "fn main() {\n"
        "    helper(42)\n"          # callsite — parm_a is NOT here
        "    let parm_a = 5\n"      # SAME name, separate binding
        "    helper(parm_a)\n"      # callsite passing parm_a as positional arg
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
        # Cursor on `parm_a` inside helper's param list.
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
        # b.nova MUST NOT be touched (the local-rename engine confines
        # itself to a.nova's fn scope).
        b_uri = "file://" + os.path.abspath(b_path)
        if b_uri in result["changes"]:
            assert_eq(len(result["changes"][b_uri]), 0,
                      "b.nova has zero edits")
        else:
            assert_(True, "b.nova absent from changes")
        # a.nova must have exactly 2 edits — the param + its use.
        a_uri_changes = a_uri
        assert_(a_uri_changes in result["changes"], "a.nova in changes")
        assert_eq(len(result["changes"][a_uri_changes]), 2,
                  "exactly 2 parm_a edits in a.nova")


# ---------------------------------------------------------------------------
# End-to-end: top-level fn rename across multiple files.
# ---------------------------------------------------------------------------


def test_lsp_rename_toplevel_fn_across_workspace() -> None:
    """Renaming a top-level fn defined in lib.nova rewrites every
    callsite in every workspace file that imports lib.nova."""
    lib_text = "fn shared(x) {\n    return x + 1\n}\n"
    a_text = (
        'import "lib.nova"\n'
        "fn caller_a() {\n"
        "    shared(1)\n"
        "    shared(2)\n"
        "}\n"
    )
    b_text = (
        'import "lib.nova"\n'
        "fn caller_b() {\n"
        "    shared(99)\n"
        "}\n"
    )
    unrelated = "fn shared() { return 0 }\nfn other() { shared() }\n"
    with tempfile.TemporaryDirectory() as ws:
        lib_path = os.path.join(ws, "lib.nova")
        a_path = os.path.join(ws, "a.nova")
        b_path = os.path.join(ws, "b.nova")
        u_path = os.path.join(ws, "unrelated.nova")
        _write(lib_path, lib_text)
        _write(a_path, a_text)
        _write(b_path, b_text)
        _write(u_path, unrelated)

        client = LspClient()
        client.initialize(ws)
        lib_uri = client.open(lib_path, lib_text)
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": lib_uri},
                "position": {"line": 0, "character": 4},
                "newName": "shared_v2",
            },
        )
        result = resp["result"]
        assert_(result is not None, "workspace fn rename returned")
        changes = result["changes"]
        lib_change = "file://" + os.path.abspath(lib_path)
        a_change = "file://" + os.path.abspath(a_path)
        b_change = "file://" + os.path.abspath(b_path)
        u_change = "file://" + os.path.abspath(u_path)
        assert_(lib_change in changes, "lib.nova in changes")
        assert_(a_change in changes, "a.nova in changes")
        assert_(b_change in changes, "b.nova in changes")
        assert_(u_change not in changes,
                "unrelated.nova NOT in changes (no import)")
        # lib has 1 (the def). a has 2 calls. b has 1 call.
        assert_eq(len(changes[lib_change]), 1, "lib.nova edit count")
        assert_eq(len(changes[a_change]), 2, "a.nova edit count")
        assert_eq(len(changes[b_change]), 1, "b.nova edit count")


# ---------------------------------------------------------------------------
# End-to-end: comments + strings stay untouched (regression for both
# the workspace and local paths).
# ---------------------------------------------------------------------------


def test_lsp_rename_skips_comments() -> None:
    """A comment that mentions the symbol's identifier is text — the
    rename must NOT rewrite it."""
    text = (
        "fn target() {\n"
        "    return 1\n"
        "}\n"
        "// target is mentioned here in a comment\n"
        "fn caller() {\n"
        "    target()\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 4},
                "newName": "renamed_target",
            },
        )
        result = resp["result"]
        assert_(result is not None, "rename returned")
        changes = result["changes"]
        change_uri = "file://" + os.path.abspath(path)
        edits = changes[change_uri]
        # Only 2 occurrences: the def + the call. Comment-internal use
        # is NOT touched.
        assert_eq(len(edits), 2, "2 target edits — comment skipped")


def test_lsp_rename_skips_string_literal() -> None:
    """A string literal that contains the symbol's name as text is NOT
    rewritten by the rename engine."""
    text = (
        "fn target() {\n"
        "    return 1\n"
        "}\n"
        "fn caller() {\n"
        '    println("target name in string")\n'  # NO rename
        "    target()\n"                          # YES rename
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 4},
                "newName": "renamed",
            },
        )
        result = resp["result"]
        change_uri = "file://" + os.path.abspath(path)
        edits = result["changes"][change_uri]
        # 2 edits: the def + the real call. String NOT touched.
        assert_eq(len(edits), 2, "2 target edits — string skipped")


# ---------------------------------------------------------------------------
# End-to-end: same-scope shadow returns a warning (not an error).
# ---------------------------------------------------------------------------


def test_lsp_rename_same_scope_shadow_emits_edit() -> None:
    """Renaming a local var to a name already declared by `let` in the
    same enclosing fn still emits the edit but precedes it with a
    `window/showMessage` warning notification."""
    text = (
        "fn outer() {\n"
        "    let already_here = 1\n"
        "    let target = 2\n"
        "    return target\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": 9},  # on `target`
                "newName": "already_here",
            },
        )
        # The edit MUST be present (no error envelope).
        assert_("error" not in resp,
                "shadow does not block the rename")
        result = resp["result"]
        assert_(result is not None, "edit returned despite shadow")
        # The harness records pending notifications too — verify the
        # warning was fired.
        warn_notifs = [
            n for n in client.notifications
            if n.get("method") == "window/showMessage"
            and n.get("params", {}).get("type") == 2
        ]
        assert_(len(warn_notifs) >= 1, "warning notification emitted")
        assert_("already_here" in warn_notifs[-1]["params"]["message"],
                "warning mentions new name")


# ---------------------------------------------------------------------------
# Regression: prepareRename + rename round-trip on parser.nova.
# ---------------------------------------------------------------------------


def test_lsp_smoke_parser_nova_symbol() -> None:
    """Real-codebase smoke: pick a top-level fn from parser.nova and
    verify prepareRename + rename agree on the symbol's identity.

    Skipped if parser.nova isn't on disk so the test can still run in
    sandboxes that strip the codebase.
    """
    parser_path = "/home/user/NOVA/src/compiler/parser.nova"
    if not os.path.isfile(parser_path):
        print("  SKIP parser.nova smoke — file missing")
        return
    with open(parser_path, "r", encoding="utf-8") as f:
        parser_text = f.read()
    # Pick `tc_arm_expr_type` (R31D-shipped helper, unlikely to clash).
    sym = "tc_arm_expr_type"
    # Find the fn-def line for sym.
    def_line = None
    def_col = None
    for i, line in enumerate(parser_text.splitlines()):
        m = re.match(r"^fn\s+(" + re.escape(sym) + r")\s*\(", line)
        if m:
            def_line = i
            def_col = m.start(1)
            break
    if def_line is None:
        print(f"  SKIP parser.nova smoke — fn {sym} not found")
        return

    client = LspClient()
    client.initialize("/home/user/NOVA/src/compiler")
    uri = client.open(parser_path, parser_text)

    # prepareRename should succeed on the def position.
    presp = client.request(
        "textDocument/prepareRename",
        {
            "textDocument": {"uri": uri},
            "position": {"line": def_line, "character": def_col + 1},
        },
    )
    presult = presp["result"]
    assert_(presult is not None, "parser.nova prepareRename succeeds")
    assert_eq(presult["placeholder"], sym,
              "parser.nova placeholder matches symbol name")

    # Rename to a sentinel name; verify the edit count matches the
    # grep count of the identifier-token occurrences (subtract those
    # inside comments/strings).
    rresp = client.request(
        "textDocument/rename",
        {
            "textDocument": {"uri": uri},
            "position": {"line": def_line, "character": def_col + 1},
            "newName": sym + "_RENAMED",
        },
    )
    rresult = rresp["result"]
    assert_(rresult is not None, "parser.nova rename returned an edit")

    # Manually count `\bsym\b` outside comments+strings.
    pattern = re.compile(r"\b" + re.escape(sym) + r"\b")
    expected = 0
    for line in parser_text.splitlines():
        cleaned = mask_comments_and_strings(line)
        expected += len(pattern.findall(cleaned))
    # The engine's count for parser.nova must match the cleaned grep.
    parser_uri = "file://" + os.path.abspath(parser_path)
    if parser_uri not in rresult["changes"]:
        # Some path-normalisation paths case the URI differently — fall
        # back to a substring match.
        candidates = [u for u in rresult["changes"]
                      if u.endswith("parser.nova")]
        assert_(candidates, "parser.nova present in rename changes")
        parser_uri = candidates[0]
    parser_edits = rresult["changes"][parser_uri]
    assert_eq(len(parser_edits), expected,
              f"parser.nova edit count == cleaned grep ({expected})")
    print(
        f"  parser.nova smoke: {sym} -> {expected} edits, "
        f"matches identifier-token grep"
    )


# ---------------------------------------------------------------------------
# Regression: rename across files where definition has multiple usages.
# ---------------------------------------------------------------------------


def test_lsp_rename_struct_like_fn_field_resolution() -> None:
    """NOVA's grammar doesn't yet have `struct.field` access in a typed
    sense; the rename engine treats every `\\bfield\\b` token as a
    candidate. The test below pins the *current* behaviour: a free
    identifier called `value` inside any importing file's body is
    renamed alongside the top-level `value` declaration. When NOVA's
    typechecker exposes field types, this will need refinement.
    """
    a_text = (
        "let value = 42\n"
        "fn read_value() {\n"
        "    return value\n"
        "}\n"
    )
    b_text = (
        'import "a.nova"\n'
        "fn use() {\n"
        "    return value + 1\n"
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
                "position": {"line": 0, "character": 5},
                "newName": "renamed_value",
            },
        )
        result = resp["result"]
        assert_(result is not None, "let-decl workspace rename returned")
        a_change = "file://" + os.path.abspath(a_path)
        b_change = "file://" + os.path.abspath(b_path)
        # a.nova has 2 `value` occurrences (def + return). b.nova has 1.
        assert_eq(len(result["changes"][a_change]), 2, "a edits")
        assert_eq(len(result["changes"][b_change]), 1, "b edits")


def test_lsp_rename_word_boundaries_local() -> None:
    """The local-rename engine must respect word boundaries too —
    `foo` should NOT rewrite `foobar` or `myfoo`."""
    text = (
        "fn outer() {\n"
        "    let foo = 1\n"          # match
        "    let foobar = 2\n"       # NO match
        "    let myfoo = 3\n"        # NO match (myfoo is its own ident)
        "    return foo + foobar + myfoo\n"  # match foo, NOT the others
        "}\n"
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "a.nova")
        _write(path, text)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 9},  # on first foo
                "newName": "spam",
            },
        )
        result = resp["result"]
        edits = result["changes"][uri]
        # 2 foo occurrences: the let + the return ref.
        assert_eq(len(edits), 2, "exactly 2 foo edits (word boundary)")


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    test_unit_identifier_shape_check()
    test_unit_keyword_table()
    test_unit_validate_new_name()
    test_unit_identifier_range_basic()
    test_unit_identifier_range_on_keyword_returns_none()
    test_unit_identifier_range_on_whitespace_returns_none()
    test_unit_identifier_range_in_comment_returns_none()
    test_unit_identifier_range_in_string_returns_none()
    test_unit_identifier_range_on_numeric_literal_returns_none()
    test_unit_enclosing_fn_simple()
    test_unit_scope_constrained_occurrences()
    test_unit_scope_skips_comments_and_strings()
    test_unit_mask_comments_and_strings()
    test_unit_detect_shadow_let()
    test_unit_detect_shadow_param()
    test_lsp_prepare_rename_on_identifier()
    test_lsp_prepare_rename_on_keyword_returns_null()
    test_lsp_prepare_rename_on_whitespace_returns_null()
    test_lsp_prepare_rename_in_string_returns_null()
    test_lsp_rename_invalid_identifier_returns_error()
    test_lsp_rename_to_keyword_returns_error()
    test_lsp_rename_local_var_five_occurrences()
    test_lsp_rename_param_does_not_touch_sibling_fn()
    test_lsp_rename_param_does_not_touch_callsites()
    test_lsp_rename_toplevel_fn_across_workspace()
    test_lsp_rename_skips_comments()
    test_lsp_rename_skips_string_literal()
    test_lsp_rename_same_scope_shadow_emits_edit()
    test_lsp_smoke_parser_nova_symbol()
    test_lsp_rename_struct_like_fn_field_resolution()
    test_lsp_rename_word_boundaries_local()
    print(f"test_prepare_rename_r32e: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
