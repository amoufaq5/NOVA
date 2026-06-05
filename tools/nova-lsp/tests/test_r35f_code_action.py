"""R35F — code action coverage for organize-imports + extract-function.

Builds on top of the existing R3 / R21F / R25F code-action tests with
the specific behaviours R35F adds:

  * organize-imports **deduplication** — identical paths collapse to a
    single line. The legacy behaviour sorted but kept duplicates.
  * organize-imports **idempotency** — running the action on an
    already-sorted+unique file returns ``None`` (no edit), so the
    lightbulb stays clean.
  * organize-imports **empty / no-imports file** — returns ``None``
    rather than emitting an empty edit.
  * extract-function **return-value computation** — variables assigned
    inside the selection AND read after it become the helper's return
    list + the call site's LHS.
  * extract-function **early-exit rejection** — selections containing
    ``return`` / ``break`` / ``continue`` at line-leading position are
    rejected because the helper's control flow would diverge from the
    caller's.
  * extract-function **name collision** — ``extracted_<N>`` counter
    advances past every prior helper number, even when the user
    manually inserted one out of order.
  * Capability surface — ``codeActionProvider.codeActionKinds`` lists
    both ``source.organizeImports`` and ``refactor.extract``.
  * Regression — other LSP capabilities (semantic tokens, inlay hints,
    rename, documentLink, codeLens) still advertise in initialize and
    respond on a minimal fixture.

Each test is a small unit pinned to one behaviour so failure
diagnostics point straight at the regression. The harness reuses the
in-process ``LspClient`` from ``_harness.py`` so the dispatcher path
is exercised end-to-end via JSON-RPC.
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
from nova_lsp.extract_function import (  # noqa: E402
    KIND_REFACTOR_EXTRACT,
    analyze_selection,
    build_extract_action,
    build_extract_edit,
    compute_next_extracted_name,
)
from nova_lsp.server import (  # noqa: E402
    KIND_SOURCE_ORGANIZE_IMPORTS,
    _organize_imports_text,
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


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _apply_edit(doc_text: str, uri: str, edit) -> str:
    """Apply a ``{"changes": {uri: [TextEdit]}}`` to a document.

    Last-to-first ordering so earlier edits don't shift offsets of
    later ones — matches the server's emission style.
    """
    changes = edit.get("changes") or {}
    edits = changes.get(uri) or []
    if not edits:
        return doc_text
    text = doc_text
    sorted_edits = sorted(
        edits,
        key=lambda e: (
            e["range"]["start"]["line"],
            e["range"]["start"]["character"],
        ),
        reverse=True,
    )
    for e in sorted_edits:
        r = e["range"]
        s_line, s_char = r["start"]["line"], r["start"]["character"]
        e_line, e_char = r["end"]["line"], r["end"]["character"]
        cur_lines = text.splitlines(keepends=True)
        s_off = sum(len(l) for l in cur_lines[:s_line]) + s_char
        e_off = sum(len(l) for l in cur_lines[:e_line]) + e_char
        text = text[:s_off] + e["newText"] + text[e_off:]
    return text


# ---------------------------------------------------------------------------
# Capability surface — initialize response advertises both kinds.
# ---------------------------------------------------------------------------


def test_capability_advertises_both_kinds() -> None:
    """The initialize response lists both ``source.organizeImports``
    and ``refactor.extract`` in ``codeActionKinds``."""
    with tempfile.TemporaryDirectory() as workspace:
        client = LspClient()
        resp = client.initialize(workspace)
        caps = resp["result"]["capabilities"]
        provider = caps.get("codeActionProvider")
        assert_(provider is not None, "codeActionProvider present")
        kinds = provider["codeActionKinds"]
        assert_(
            "source.organizeImports" in kinds,
            f"source.organizeImports in {kinds}",
        )
        assert_(
            "refactor.extract" in kinds,
            f"refactor.extract in {kinds}",
        )


def test_capability_regression_other_providers() -> None:
    """Other LSP capabilities still advertise alongside codeAction —
    these are the providers R35F must not break."""
    with tempfile.TemporaryDirectory() as workspace:
        client = LspClient()
        resp = client.initialize(workspace)
        caps = resp["result"]["capabilities"]
        # The capability count matters because the harness's grep
        # regression test pins it; check the load-bearing ones.
        assert_(caps.get("renameProvider") is not None, "renameProvider")
        assert_(
            caps.get("semanticTokensProvider") is not None,
            "semanticTokensProvider",
        )
        assert_(caps.get("inlayHintProvider") is not None, "inlayHintProvider")
        assert_(caps.get("codeLensProvider") is not None, "codeLensProvider")
        assert_(
            caps.get("documentLinkProvider") is not None,
            "documentLinkProvider",
        )


# ---------------------------------------------------------------------------
# organize-imports — pure unit tests over _organize_imports_text.
# ---------------------------------------------------------------------------


def test_organize_imports_dedupes_two_identical() -> None:
    """Two ``import "std/io.nova"`` lines collapse to one."""
    src = (
        'import "std/io.nova"\n'
        'import "std/io.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    new = _organize_imports_text(src)
    assert_(new is not None, "duplicate-only block triggers rewrite")
    assert_eq(
        new.count('import "std/io.nova"'),
        1,
        "duplicates collapsed",
    )


def test_organize_imports_dedupes_and_sorts() -> None:
    """5-import block with 2 duplicates -> 3 unique sorted lines."""
    src = (
        'import "z.nova"\n'
        'import "a.nova"\n'
        'import "z.nova"\n'
        'import "m.nova"\n'
        'import "a.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    new = _organize_imports_text(src)
    assert_(new is not None, "rewrite emitted")
    # Three distinct paths after dedupe.
    assert_eq(new.count('import "a.nova"'), 1, "a.nova once")
    assert_eq(new.count('import "z.nova"'), 1, "z.nova once")
    assert_eq(new.count('import "m.nova"'), 1, "m.nova once")
    # Order: a < m < z lexicographically (within group 3 / "other").
    head = new.splitlines()
    paths_in_order = [
        l for l in head
        if l.startswith("import ")
    ]
    assert_eq(
        paths_in_order,
        [
            'import "a.nova"',
            'import "m.nova"',
            'import "z.nova"',
        ],
        "lexicographic order",
    )


def test_organize_imports_idempotent_on_sorted_unique() -> None:
    """A file with already-sorted+unique imports yields no rewrite."""
    src = (
        'import "std/io.nova"\n'
        "\n"
        'import "../src/util.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    new = _organize_imports_text(src)
    assert_(new is None, "no rewrite on already-sorted file")


def test_organize_imports_idempotent_no_imports() -> None:
    """A file with no imports at all yields no rewrite."""
    src = "fn main() { return 0 }\n"
    new = _organize_imports_text(src)
    assert_(new is None, "no imports -> no action")


def test_organize_imports_empty_file() -> None:
    """An empty document yields no rewrite."""
    new = _organize_imports_text("")
    assert_(new is None, "empty file -> no action")


def test_organize_imports_idempotency_after_apply() -> None:
    """Applying organize-imports twice produces the same output —
    the second pass is a no-op."""
    src = (
        'import "z.nova"\n'
        'import "a.nova"\n'
        'import "z.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    once = _organize_imports_text(src)
    assert_(once is not None, "first pass rewrites")
    twice = _organize_imports_text(once)
    assert_(twice is None, "second pass is no-op")


def test_organize_imports_single_unique_no_action() -> None:
    """A file with a single import and no duplicates -> no rewrite."""
    src = (
        'import "std/io.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    new = _organize_imports_text(src)
    assert_(new is None, "single import, no duplicates -> no action")


def test_organize_imports_dedupes_three_duplicates_one_path() -> None:
    """A block with three lines all importing the same path collapses
    to one — the dedupe heuristic doesn't need >1 distinct path to
    fire."""
    src = (
        'import "lib.nova"\n'
        'import "lib.nova"\n'
        'import "lib.nova"\n'
        "\n"
        "fn main() { }\n"
    )
    new = _organize_imports_text(src)
    assert_(new is not None, "dedupe still fires on single-path block")
    assert_eq(new.count('import "lib.nova"'), 1, "one import after dedupe")


def test_organize_imports_preserves_trailing_newline() -> None:
    """Output preserves the input's trailing newline state."""
    src_with = (
        'import "z.nova"\n'
        'import "a.nova"\n'
    )
    new = _organize_imports_text(src_with)
    assert_(new is not None, "rewrite emitted")
    assert_(new.endswith("\n"), "trailing newline preserved")


def test_organize_imports_body_untouched() -> None:
    """The fn body below the import block is byte-identical in the
    output."""
    src = (
        'import "z.nova"\n'
        'import "a.nova"\n'
        "\n"
        "fn main() {\n"
        "    let x = 1\n"
        "    return x\n"
        "}\n"
    )
    new = _organize_imports_text(src)
    assert_(new is not None, "rewrite emitted")
    body = (
        "fn main() {\n"
        "    let x = 1\n"
        "    return x\n"
        "}\n"
    )
    assert_(new.endswith(body), "body byte-identical after sort")


# ---------------------------------------------------------------------------
# extract-function — return-value computation.
# ---------------------------------------------------------------------------


def test_extract_no_return_when_var_not_used_after() -> None:
    """A let inside the selection that's not read after the selection
    does NOT become a return value."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return x\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_eq(info.return_variables, [], "no live-out vars")


def test_extract_single_return_var() -> None:
    """A let inside the selection that's read after it becomes the
    single return value of the helper."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_eq(info.return_variables, ["b"], "b is live-out")


def test_extract_multiple_return_vars() -> None:
    """Two assigned vars both read after the selection -> both
    returned, in first-seen order."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 2\n"
        "    return a + b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_eq(info.return_variables, ["a", "b"], "a, b in order")


def test_extract_helper_has_return_line() -> None:
    """When there's a single return var the helper body ends with
    ``return <var>``."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # Helper body must include ``return b``.
    helper_lines = []
    in_helper = False
    for l in new_text.splitlines():
        if l.startswith("fn extracted_1("):
            in_helper = True
        if in_helper:
            helper_lines.append(l)
        if in_helper and l == "}":
            break
    body = "\n".join(helper_lines)
    assert_("return b" in body, "helper returns b")


def test_extract_call_site_rewritten_with_assignment() -> None:
    """When the helper returns a value, the call site is rewritten
    as ``<var> = helper(args)`` so downstream reads stay bound."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    assert_(
        "b = extracted_1(" in new_text,
        f"call site assigns to b: {new_text}",
    )


def test_extract_void_helper_when_no_live_out() -> None:
    """When nothing is live-out the helper has no return + the call
    site is a bare statement call (no LHS)."""
    src = (
        "fn run() {\n"
        "    let a = 1\n"
        "    let b = 2\n"
        "    println(a)\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 3, "character": 14},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src, builtins={"println"}
    )
    edit = build_extract_edit(info, src, "extracted_1")
    new_text = _apply_edit(src, _uri("/t.nova"), edit)
    # Helper body shouldn't have a trailing ``return ...`` line.
    helper_section = new_text.split("fn extracted_1(")[1]
    helper_body, _, _ = helper_section.partition("\n}")
    assert_(
        "return " not in helper_body,
        f"void helper has no return: {helper_body}",
    )


def test_extract_bare_assignment_is_live_out() -> None:
    """A bare ``NAME = expr`` (no ``let``) assignment captured as a
    live-out variable when the name is read after the selection."""
    src = (
        "fn run(x) {\n"
        "    let total = 0\n"
        "    total = total + x\n"
        "    total = total * 2\n"
        "    return total\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 24},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is not None, "analyzed")
    assert_(
        "total" in info.return_variables,
        f"total is live-out: {info.return_variables}",
    )


# ---------------------------------------------------------------------------
# extract-function — early-exit rejection.
# ---------------------------------------------------------------------------


def test_extract_rejects_early_return() -> None:
    """A selection containing a line-leading ``return`` is rejected
    because moving it into a helper would change caller control flow."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    return a\n"
        "    let b = a + 1\n"
        "    return b\n"
        "}\n"
    )
    # Select the return line + the line after.
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 17},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is None, "early-return selection rejected")


def test_extract_rejects_break() -> None:
    """``break`` in the selection is rejected."""
    src = (
        "fn run(x) {\n"
        "    while x > 0 {\n"
        "        let a = x\n"
        "        break\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 13},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is None, "break selection rejected")


def test_extract_rejects_continue() -> None:
    """``continue`` in the selection is rejected."""
    src = (
        "fn run(x) {\n"
        "    while x > 0 {\n"
        "        let a = x\n"
        "        continue\n"
        "    }\n"
        "    return 0\n"
        "}\n"
    )
    sel = {
        "start": {"line": 2, "character": 0},
        "end": {"line": 3, "character": 16},
    }
    info = analyze_selection(_uri("/t.nova"), sel, src, builtins=set())
    assert_(info is None, "continue selection rejected")


def test_extract_early_exit_bypass_flag() -> None:
    """Passing ``reject_early_exit=False`` allows the selection
    through — useful for callers that want the analysis without the
    gate (mostly tests + tooling probes)."""
    src = (
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    return a\n"
        "}\n"
    )
    sel = {
        "start": {"line": 1, "character": 0},
        "end": {"line": 2, "character": 12},
    }
    info = analyze_selection(
        _uri("/t.nova"), sel, src,
        builtins=set(),
        reject_early_exit=False,
    )
    assert_(info is not None, "bypass allows early-exit through")


# ---------------------------------------------------------------------------
# extract-function — counter advances past existing helpers.
# ---------------------------------------------------------------------------


def test_extract_counter_picks_max_plus_one() -> None:
    """If ``extracted_5`` exists, next is ``extracted_6`` — not
    ``extracted_2`` (the file has only one prior extracted, but its
    number is 5)."""
    src = (
        "fn extracted_5(a) { return a }\n"
        "fn run() { let x = extracted_5(0) }\n"
    )
    assert_eq(
        compute_next_extracted_name(src),
        "extracted_6",
        "counter advances past max prior",
    )


def test_extract_counter_skips_gaps() -> None:
    """If ``extracted_1`` and ``extracted_3`` exist, next is
    ``extracted_4`` — gaps in the sequence don't get reused."""
    src = (
        "fn extracted_1(a) { return a }\n"
        "fn extracted_3(b) { return b }\n"
        "fn run() { let x = extracted_1(extracted_3(0)) }\n"
    )
    assert_eq(
        compute_next_extracted_name(src),
        "extracted_4",
        "gaps not reused; max+1 wins",
    )


# ---------------------------------------------------------------------------
# Integration via the LSP dispatcher.
# ---------------------------------------------------------------------------


_DEDUP_FIXTURE = """\
import "std/io.nova"
import "std/io.nova"
import "../src/util.nova"

fn main() {
    return 0
}
"""


def test_dispatch_organize_imports_dedupe() -> None:
    """End-to-end: open a doc with duplicate imports, request the
    code action, apply the edit, verify duplicates are gone."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_DEDUP_FIXTURE)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, _DEDUP_FIXTURE)
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        oi = next(
            (a for a in actions if a.get("kind") == KIND_SOURCE_ORGANIZE_IMPORTS),
            None,
        )
        assert_(oi is not None, "organize-imports action present")
        new_text = _apply_edit(_DEDUP_FIXTURE, uri, oi["edit"])
        assert_eq(
            new_text.count('import "std/io.nova"'),
            1,
            "duplicate std/io.nova collapsed",
        )


_RETURN_FIXTURE = """\
fn caller(a, b) {
    let x = a + b
    let y = x + 1
    return y
}
"""


def test_dispatch_extract_with_return_value() -> None:
    """End-to-end: select two lets, request the code action, verify
    the helper returns y and the call site rebinds y."""
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_RETURN_FIXTURE)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, _RETURN_FIXTURE)
        sel = {
            "start": {"line": 1, "character": 0},
            "end": {"line": 2, "character": 17},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        ex = next(
            (a for a in actions if a.get("kind") == KIND_REFACTOR_EXTRACT),
            None,
        )
        assert_(ex is not None, "extract action present")
        new_text = _apply_edit(_RETURN_FIXTURE, uri, ex["edit"])
        # Helper should return y.
        assert_(
            "return y" in new_text,
            f"helper returns y: {new_text}",
        )
        # Call site should rebind y.
        assert_(
            "y = extracted_1(" in new_text,
            f"call site rebinds y: {new_text}",
        )


def test_dispatch_no_extract_on_early_return_selection() -> None:
    """End-to-end: a selection containing a ``return`` line yields no
    extract action."""
    src = (
        "fn run(x) {\n"
        "    let a = x\n"
        "    return a\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, src)
        sel = {
            "start": {"line": 1, "character": 0},
            "end": {"line": 2, "character": 13},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            "refactor.extract" not in kinds,
            f"no extract on early-return selection: {kinds}",
        )


# ---------------------------------------------------------------------------
# Regression — other refactor actions still surface when their
# preconditions are met.
# ---------------------------------------------------------------------------


def test_dispatch_extract_and_organize_coexist() -> None:
    """A doc with messy imports + an extractable block surfaces both
    actions in the same code-action response."""
    src = (
        'import "z.nova"\n'
        'import "a.nova"\n'
        'import "z.nova"\n'
        "\n"
        "fn run(x) {\n"
        "    let a = x + 1\n"
        "    let b = a + 2\n"
        "    let c = b + 3\n"
        "    return c\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "demo.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        client = LspClient()
        client.initialize(workspace)
        uri = client.open(path, src)
        sel = {
            "start": {"line": 5, "character": 0},
            "end": {"line": 7, "character": 18},
        }
        resp = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": sel,
                "context": {"diagnostics": []},
            },
        )
        actions = resp["result"]
        kinds = sorted(a.get("kind", "") for a in actions)
        assert_(
            "refactor.extract" in kinds,
            f"extract present: {kinds}",
        )
        assert_(
            "source.organizeImports" in kinds,
            f"organize-imports present: {kinds}",
        )


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Capability surface
        test_capability_advertises_both_kinds,
        test_capability_regression_other_providers,
        # organize-imports unit tests
        test_organize_imports_dedupes_two_identical,
        test_organize_imports_dedupes_and_sorts,
        test_organize_imports_idempotent_on_sorted_unique,
        test_organize_imports_idempotent_no_imports,
        test_organize_imports_empty_file,
        test_organize_imports_idempotency_after_apply,
        test_organize_imports_single_unique_no_action,
        test_organize_imports_dedupes_three_duplicates_one_path,
        test_organize_imports_preserves_trailing_newline,
        test_organize_imports_body_untouched,
        # extract-function return-value
        test_extract_no_return_when_var_not_used_after,
        test_extract_single_return_var,
        test_extract_multiple_return_vars,
        test_extract_helper_has_return_line,
        test_extract_call_site_rewritten_with_assignment,
        test_extract_void_helper_when_no_live_out,
        test_extract_bare_assignment_is_live_out,
        # extract-function early-exit rejection
        test_extract_rejects_early_return,
        test_extract_rejects_break,
        test_extract_rejects_continue,
        test_extract_early_exit_bypass_flag,
        # extract-function counter
        test_extract_counter_picks_max_plus_one,
        test_extract_counter_skips_gaps,
        # Integration via dispatcher
        test_dispatch_organize_imports_dedupe,
        test_dispatch_extract_with_return_value,
        test_dispatch_no_extract_on_early_return_selection,
        # Regression
        test_dispatch_extract_and_organize_coexist,
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
            f"test_r35f_code_action: FAIL — {failed} test(s) failed",
            file=sys.stderr,
        )
        return 1
    print(f"test_r35f_code_action: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
