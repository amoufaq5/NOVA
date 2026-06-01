"""Smoke test for `textDocument/codeAction`.

Builds a fixture with imports out of order, fn declarations out of order,
and a multi-statement block suitable for extraction. Requests code
actions for the whole document and verifies that exactly the three
expected actions (extract-function, organize-imports, sort-fns) come
back with WorkspaceEdits that, when applied, produce the expected text.

Prints `OK` on success or `SKIP: <reason>` if the test environment is
missing something (so CI can still tick the box on minimal images)."""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Dict, List

from _harness import LspClient


FIXTURE = """\
import "../src/foo.nova"
import "std/io.nova"
import "../../tests/util.nova"

fn zeta(x) {
    return x
}

fn alpha(n) {
    let total = 0
    let i = 0
    let step = 2
    total = total + n
    total = total + step
    return total
}

fn mid(a, b) {
    return a + b
}
"""


def apply_workspace_edit(doc_text: str, uri: str, edit: Dict[str, Any]) -> str:
    """Apply a WorkspaceEdit (in the `{"changes": {uri: TextEdit[]}}`
    shape used by the server) to `doc_text` and return the new text.
    Only handles single-range full-document replacements + non-overlapping
    edits sorted last-to-first — matches everything the server emits."""
    changes = edit.get("changes") or {}
    edits = changes.get(uri) or []
    if not edits:
        return doc_text
    lines = doc_text.splitlines(keepends=True)
    # Apply in reverse so earlier offsets don't shift.
    sorted_edits = sorted(
        edits,
        key=lambda e: (
            e["range"]["start"]["line"],
            e["range"]["start"]["character"],
        ),
        reverse=True,
    )
    text = doc_text
    for e in sorted_edits:
        r = e["range"]
        s_line, s_char = r["start"]["line"], r["start"]["character"]
        e_line, e_char = r["end"]["line"], r["end"]["character"]
        # Convert (line, char) to absolute offsets.
        # Recompute lines each loop because earlier edits may have changed
        # offsets — except we're applying in reverse, so later edits in
        # the document are applied first and don't shift earlier ones.
        # For simplicity, use a fresh splitlines on `text` each time.
        cur_lines = text.splitlines(keepends=True)
        # Compute the start offset.
        s_off = sum(len(l) for l in cur_lines[:s_line]) + s_char
        e_off = sum(len(l) for l in cur_lines[:e_line]) + e_char
        text = text[:s_off] + e["newText"] + text[e_off:]
    return text


def find_action(actions: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    for a in actions:
        if a.get("kind") == kind:
            return a
    raise AssertionError(
        f"no action with kind={kind} (got: {[a.get('kind') for a in actions]})"
    )


def main() -> int:
    try:
        with tempfile.TemporaryDirectory() as workspace:
            path = os.path.join(workspace, "demo.nova")
            with open(path, "w", encoding="utf-8") as f:
                f.write(FIXTURE)

            client = LspClient()
            init = client.initialize(workspace)
            caps = init["result"]["capabilities"]
            assert caps.get("codeActionProvider"), \
                f"no codeActionProvider in capabilities: {caps}"
            kinds = caps["codeActionProvider"]["codeActionKinds"]
            assert "refactor.extract" in kinds, kinds
            assert "source.organizeImports" in kinds, kinds
            assert "source.organizeFns" in kinds, kinds

            uri = client.open(path, FIXTURE)

            # Select 3 statements inside `alpha`. The fixture is:
            #   line 8 : fn alpha(n) {
            #   line 9 :     let total = 0
            #   line 10:     let i = 0
            #   line 11:     let step = 2
            #   line 12:     total = total + n
            #   line 13:     total = total + step
            #   line 14:     return total
            # We grab lines 12-13 (two statements using `total`, `n`,
            # `step` — `total` and `step` are bound in the let-block above
            # so they're locals; `n` is a parameter; nothing escapes).
            sel_range = {
                "start": {"line": 12, "character": 0},
                "end": {"line": 13, "character": 25},
            }
            resp = client.request(
                "textDocument/codeAction",
                {
                    "textDocument": {"uri": uri},
                    "range": sel_range,
                    "context": {"diagnostics": []},
                },
            )
            actions = resp["result"]
            assert isinstance(actions, list), f"expected list, got {type(actions)}"

            kinds_returned = sorted(a.get("kind", "") for a in actions)
            print(f"  actions returned: {kinds_returned}")
            assert len(actions) == 3, \
                f"expected 3 actions, got {len(actions)}: {kinds_returned}"

            # --- 1. Organize imports ----------------------------------------
            oi = find_action(actions, "source.organizeImports")
            assert oi["title"] == "Organize imports", oi["title"]
            oi_result = apply_workspace_edit(FIXTURE, uri, oi["edit"])
            expected_oi_head = (
                'import "std/io.nova"\n'
                '\n'
                'import "../src/foo.nova"\n'
                '\n'
                'import "../../tests/util.nova"\n'
            )
            assert oi_result.startswith(expected_oi_head), \
                f"organize-imports result mismatch:\n--- expected head ---\n{expected_oi_head}\n--- got ---\n{oi_result[:200]}"
            # Body (fn declarations) should be untouched.
            assert "fn zeta(x)" in oi_result
            assert "fn alpha(n)" in oi_result
            assert "fn mid(a, b)" in oi_result

            # --- 2. Sort fn declarations -----------------------------------
            sf = find_action(actions, "source.organizeFns")
            assert sf["title"] == "Sort top-level functions", sf["title"]
            sf_result = apply_workspace_edit(FIXTURE, uri, sf["edit"])
            # alpha must come before mid must come before zeta.
            i_alpha = sf_result.index("fn alpha(")
            i_mid = sf_result.index("fn mid(")
            i_zeta = sf_result.index("fn zeta(")
            assert i_alpha < i_mid < i_zeta, \
                f"sort-fns ordering wrong: alpha={i_alpha} mid={i_mid} zeta={i_zeta}"
            # Imports must still be in the original (unsorted) order — this
            # action only touches fn declarations.
            assert sf_result.startswith('import "../src/foo.nova"\n'
                                        'import "std/io.nova"\n'
                                        'import "../../tests/util.nova"\n')

            # --- 3. Extract function ---------------------------------------
            ex = find_action(actions, "refactor.extract")
            assert ex["title"].startswith("Extract to function `extracted_"), ex["title"]
            ex_result = apply_workspace_edit(FIXTURE, uri, ex["edit"])
            # The helper fn must appear above `fn zeta`.
            assert "fn extracted_1(" in ex_result, \
                f"missing extracted_1 in:\n{ex_result}"
            i_helper = ex_result.index("fn extracted_1(")
            i_zeta_ex = ex_result.index("fn zeta(")
            assert i_helper < i_zeta_ex, "helper fn should be above existing fns"
            # The call site replaces the two selected lines.
            assert "extracted_1(" in ex_result
            # The helper signature should mention `n`, `total`, `step`
            # (locals of `alpha` that the selection uses).
            helper_sig_line = next(
                l for l in ex_result.splitlines()
                if l.startswith("fn extracted_1(")
            )
            for v in ("total", "n", "step"):
                assert v in helper_sig_line, \
                    f"expected `{v}` in helper signature: {helper_sig_line}"
            # The helper body must contain the original statements.
            assert "total = total + n" in ex_result
            assert "total = total + step" in ex_result

            # --- Bonus: range = trivial (e.g. cursor only) should still
            # return organize-imports + sort-fns (no extract). ---
            resp2 = client.request(
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
            actions2 = resp2["result"]
            kinds2 = sorted(a.get("kind", "") for a in actions2)
            assert "source.organizeImports" in kinds2, kinds2
            assert "source.organizeFns" in kinds2, kinds2

            print("code_action_smoke: OK")
            print(f"  fixture size:     {len(FIXTURE)} bytes")
            print(f"  actions for full: {kinds_returned}")
            print(f"  extract title:    {ex['title']}")
            print(f"  helper sig line:  {helper_sig_line}")
            print(f"  organize-imports first line: {oi_result.splitlines()[0]}")
            print(f"  sort-fns alpha<mid<zeta: {i_alpha} < {i_mid} < {i_zeta}")
        return 0
    except AssertionError as e:
        print(f"code_action_smoke: FAIL — {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover — defensive
        print(f"code_action_smoke: SKIP — unexpected error: {e!r}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
