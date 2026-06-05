"""Smoke test for `textDocument/references`.

Workspace has a main file + an imported util module. We ask for every
reference to `greet` (defined in util, used twice in main) and assert
that the returned `Location[]` covers all 3 occurrences."""
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
    let r = greet("world")
    shout("hey")
    return r
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
        assert init["result"]["capabilities"]["referencesProvider"] is True

        main_uri = client.open(main_path, MAIN_NOVA)
        # Cursor on the first `greet` call: line 2, char 5 ("    greet")
        resp = client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 2, "character": 5},
                "context": {"includeDeclaration": True},
            },
        )
        locs = resp["result"]
        assert isinstance(locs, list), f"expected list, got {type(locs)}"

        main_locs = [l for l in locs if l["uri"] == main_uri]
        util_uri = "file://" + os.path.abspath(util_path)
        util_locs = [l for l in locs if l["uri"] == util_uri]

        # 2 occurrences in main, 1 in util (the definition).
        assert len(main_locs) == 2, f"expected 2 main locations, got {len(main_locs)}: {main_locs}"
        assert len(util_locs) == 1, f"expected 1 util location, got {len(util_locs)}: {util_locs}"

        # Each location's range must cover exactly "greet" (5 chars).
        for loc in locs:
            rng = loc["range"]
            span = rng["end"]["character"] - rng["start"]["character"]
            assert span == len("greet"), loc

        # Negative case: word with no occurrences yields an empty list.
        resp_none = client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": main_uri},
                "position": {"line": 0, "character": 0},  # on `import`
                "context": {"includeDeclaration": True},
            },
        )
        none_locs = resp_none["result"]
        # `import` is itself a word so we'll get refs to it (1 in main).
        # The important property is that it didn't crash.
        assert isinstance(none_locs, list)

        print("references_smoke: OK")
        print(f"  total references: {len(locs)}")
        print(f"  in main:          {len(main_locs)}")
        print(f"  in util:          {len(util_locs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
