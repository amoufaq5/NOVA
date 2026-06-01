"""Smoke test for `textDocument/rename`.

Sets up a tiny workspace (main file + an imported util file), asks the
server to rename a function defined in the imported file, and asserts
that edits are emitted for both files."""
from __future__ import annotations

import os
import sys
import tempfile

from _harness import LspClient


UTIL_NOVA = """\
fn greet(name) {
    return name
}
fn shout(msg) {
    return msg
}
"""

MAIN_NOVA = """\
import "util.nova"
fn main() {
    greet("nova")
    greet("world")
    shout("hey")
}
"""


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
        assert init["result"]["capabilities"]["renameProvider"] is True

        main_uri = client.open(main_path, MAIN_NOVA)
        # Position cursor on the first `greet` call: line 2, col 4.
        resp = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 2, "character": 5},
                "newName": "salute",
            },
        )
        result = resp["result"]
        assert result is not None, "expected WorkspaceEdit, got null"
        changes = result["changes"]
        assert main_uri in changes, f"main file edits missing; got {list(changes)}"

        # main.nova should have 2 `greet` edits.
        main_edits = changes[main_uri]
        assert len(main_edits) == 2, f"expected 2 main edits, got {len(main_edits)}"
        for e in main_edits:
            assert e["newText"] == "salute", e

        # util.nova (closed file, on disk) should have 1 edit for the fn def.
        util_uri = "file://" + os.path.abspath(util_path)
        assert util_uri in changes, f"util file edits missing; got {list(changes)}"
        util_edits = changes[util_uri]
        assert len(util_edits) == 1, f"expected 1 util edit, got {len(util_edits)}"
        assert util_edits[0]["newText"] == "salute"

        # `shout` must NOT be renamed.
        for edits in changes.values():
            for e in edits:
                # Edit covers a 5-char span (length of "greet").
                rng = e["range"]
                start = rng["start"]
                end = rng["end"]
                assert end["character"] - start["character"] == len("greet"), e

        total = sum(len(v) for v in changes.values())
        print("rename_smoke: OK")
        print(f"  files touched:    {len(changes)}")
        print(f"  total edits:      {total}")
        print(f"  main edits:       {len(main_edits)}")
        print(f"  util edits:       {len(util_edits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
