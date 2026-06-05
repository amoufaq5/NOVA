"""Smoke test for cross-file `textDocument/definition`.

Workspace: `main.nova` imports `util.nova`. `util.nova` defines
`fn helper(x) { return x * 2 }` and `let GREETING = "hi"`. `main.nova`
calls `helper(...)` and also defines its own local `fn main()`.

We verify:
  * Cursor on `helper` in `main.nova` resolves to the `fn helper(...)`
    definition in `util.nova` (line/character of the name token).
  * Cursor on `helper` in `util.nova` itself still resolves to the same
    span (intra-file lookup keeps working).
  * Cursor on `main` in `main.nova` resolves to its local definition.
  * Cursor on a builtin (`println`) returns `[]` — builtins have no
    source location.
  * Cursor on an unknown identifier returns `[]`.
  * Modifying the import target on disk and re-requesting picks up the
    change (mtime invalidation works).

Also exercises cross-file `textDocument/references` by checking that
calling `helper` from main and the def in util both surface."""
from __future__ import annotations

import os
import sys
import tempfile
import time

from _harness import LspClient


UTIL_NOVA = """\
let GREETING = "hi"
fn helper(x) {
    return x * 2
}
fn shout(msg) {
    return msg
}
"""

MAIN_NOVA = """\
import "util.nova"
fn main() {
    println("start")
    let r = helper(21)
    let s = helper(2)
    return r + s
}
"""


def assert_eq(actual, expected, label):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def main() -> int:
    with tempfile.TemporaryDirectory() as workspace:
        util_path = os.path.join(workspace, "util.nova")
        main_path = os.path.join(workspace, "main.nova")
        with open(util_path, "w", encoding="utf-8") as f:
            f.write(UTIL_NOVA)
        with open(main_path, "w", encoding="utf-8") as f:
            f.write(MAIN_NOVA)

        client = LspClient()
        init = client.initialize(workspace)
        caps = init["result"]["capabilities"]
        assert caps.get("definitionProvider") is True, \
            f"definitionProvider missing from capabilities: {caps}"

        main_uri = client.open(main_path, MAIN_NOVA)
        util_uri = "file://" + os.path.abspath(util_path)

        # --- 1. Cursor on `helper` in main.nova → defn in util.nova -----
        # MAIN_NOVA line 3 (0-based) is "    let r = helper(21)".
        # `helper` starts at character 16.
        resp = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 3, "character": 17},  # mid-"helper"
            },
        )
        result = resp["result"]
        assert isinstance(result, list), f"expected list, got {type(result)}"
        assert len(result) == 1, f"expected 1 location, got {len(result)}: {result}"
        loc = result[0]
        assert_eq(loc["uri"], util_uri, "uri")
        # util.nova line 1: "fn helper(x) {", "helper" starts at char 3.
        assert_eq(loc["range"]["start"]["line"], 1, "line")
        assert_eq(loc["range"]["start"]["character"], 3, "char")
        assert_eq(loc["range"]["end"]["character"], 9, "end char")

        # --- 2. Open util.nova too and request defn from inside it ------
        client.open(util_path, UTIL_NOVA)
        # In util.nova, line 1 char 3 = on the `helper` name token itself.
        # Cursor on the helper *name* should resolve to itself.
        resp2 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": util_uri},
                "position": {"line": 1, "character": 5},
            },
        )
        result2 = resp2["result"]
        assert len(result2) == 1, f"expected 1 location, got {result2}"
        loc2 = result2[0]
        assert_eq(loc2["uri"], util_uri, "intra-file uri")
        assert_eq(loc2["range"]["start"]["line"], 1, "intra line")
        assert_eq(loc2["range"]["start"]["character"], 3, "intra char")

        # --- 3. Cursor on local `fn main` body call → main defn in main.nova
        # line 2 char 4: "    println(...)", cursor inside "println" (builtin).
        resp3 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 2, "character": 6},
            },
        )
        builtin_result = resp3["result"]
        assert builtin_result == [], \
            f"expected [] for builtin println, got {builtin_result}"

        # --- 4. Cursor on `main` name in `fn main()` → returns its own loc
        # MAIN_NOVA line 1: "fn main() {", `main` starts at char 3.
        resp4 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 1, "character": 5},
            },
        )
        main_loc = resp4["result"]
        assert len(main_loc) == 1, f"expected 1, got {main_loc}"
        assert_eq(main_loc[0]["uri"], main_uri, "main fn uri")
        assert_eq(main_loc[0]["range"]["start"]["line"], 1, "main fn line")
        assert_eq(main_loc[0]["range"]["start"]["character"], 3, "main fn char")

        # --- 5. Cursor on unknown identifier → empty -------------------
        resp5 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 0, "character": 0},  # on `import` keyword
            },
        )
        # `import` is a keyword that the cursor selects as a word, but
        # there's no fn or let with that name, so we expect [].
        kw_loc = resp5["result"]
        assert kw_loc == [], f"expected [] for keyword `import`, got {kw_loc}"

        # --- 6. Cursor on a let var defined in util ---------------------
        # In main.nova, add a use of GREETING. Edit the open buffer.
        new_main = MAIN_NOVA + 'let g = GREETING\n'
        client.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": main_uri, "version": 2},
                "contentChanges": [{"text": new_main}],
            },
        )
        # The new `GREETING` reference is on the last line (0-based 7).
        last_line = new_main.rstrip("\n").splitlines().index("let g = GREETING")
        resp6 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": last_line, "character": 10},
            },
        )
        let_loc = resp6["result"]
        assert len(let_loc) == 1, f"expected 1, got {let_loc}"
        assert_eq(let_loc[0]["uri"], util_uri, "let uri")
        # util.nova line 0: `let GREETING = "hi"`, name starts at char 4.
        assert_eq(let_loc[0]["range"]["start"]["line"], 0, "let line")
        assert_eq(let_loc[0]["range"]["start"]["character"], 4, "let char")

        # --- 7. References for `helper` across both files ---------------
        ref_resp = client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 3, "character": 17},
                "context": {"includeDeclaration": True},
            },
        )
        ref_locs = ref_resp["result"]
        # 2 calls in main + 1 defn in util = 3 (util is also open, so
        # only the open-buffer pass counts it once).
        main_refs = [l for l in ref_locs if l["uri"] == main_uri]
        util_refs = [l for l in ref_locs if l["uri"] == util_uri]
        assert len(main_refs) == 2, f"expected 2 main helper refs, got {main_refs}"
        assert len(util_refs) == 1, f"expected 1 util helper ref, got {util_refs}"

        # --- 8. Mtime invalidation: change util.nova on disk -----------
        # Move helper one line down by prepending a comment, then close
        # the util.nova buffer so the cache must reread from disk.
        # Close util buffer:
        client.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": util_uri}},
        )
        # Bump mtime: write new content with a leading comment.
        new_util = "// new comment\n" + UTIL_NOVA
        # ensure mtime advances
        os.utime(util_path, None)
        time.sleep(0.01)
        with open(util_path, "w", encoding="utf-8") as f:
            f.write(new_util)
        # Force mtime to be strictly greater than what the cache saw.
        future = time.time() + 5
        os.utime(util_path, (future, future))

        resp8 = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 3, "character": 17},
            },
        )
        moved = resp8["result"]
        assert len(moved) == 1, f"expected 1, got {moved}"
        # `helper` shifted down by 1 line (the comment we prepended).
        assert_eq(moved[0]["range"]["start"]["line"], 2, "post-edit line")
        assert_eq(moved[0]["range"]["start"]["character"], 3, "post-edit char")

        print("definition_cross_file_smoke: OK")
        print(f"  main helper -> util loc: {result[0]['range']['start']}")
        print(f"  intra-file helper:       {loc2['range']['start']}")
        print(f"  builtin println empty:   {builtin_result == []}")
        print(f"  unknown ident empty:     {kw_loc == []}")
        print(f"  let var GREETING:        {let_loc[0]['range']['start']}")
        print(f"  helper references total: {len(ref_locs)}")
        print(f"    in main: {len(main_refs)}, in util: {len(util_refs)}")
        print(f"  post-mtime helper line:  {moved[0]['range']['start']['line']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
