"""Smoke test for `textDocument/completion`.

Opens a small Nova doc that defines a `fn` and a `let`, plus references a
builtin, then asks the server for completions and checks the response."""
from __future__ import annotations

import os
import sys
import tempfile

from _harness import LspClient


SAMPLE = """\
let TAU = 6
fn greet(name) {
    println(name)
    return name
}
fn main() {
    greet("nova")
}
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "hello.nova")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)

        client = LspClient()
        init = client.initialize(workspace)
        caps = init["result"]["capabilities"]
        assert caps.get("completionProvider"), "no completionProvider in capabilities"
        assert "." in caps["completionProvider"]["triggerCharacters"]
        assert "(" in caps["completionProvider"]["triggerCharacters"]

        uri = client.open(path, SAMPLE)
        resp = client.request(
            "textDocument/completion",
            {"textDocument": {"uri": uri}, "position": {"line": 5, "character": 10}},
        )
        result = resp["result"]
        items = result["items"]
        labels = {it["label"] for it in items}

        # Builtins must surface.
        for builtin in ("println", "len", "list_new", "concat", "str_eq"):
            assert builtin in labels, f"builtin {builtin!r} missing from completion list"

        # User-defined symbols must surface.
        assert "greet" in labels, "user fn 'greet' missing from completion list"
        assert "main" in labels, "user fn 'main' missing from completion list"
        assert "TAU" in labels, "user let 'TAU' missing from completion list"

        # `detail` carries the signature for builtins.
        println_item = next(it for it in items if it["label"] == "println")
        assert println_item["detail"].startswith("fn println("), println_item

        # `detail` carries the signature for user-defined fns.
        greet_item = next(it for it in items if it["label"] == "greet")
        assert greet_item["detail"] == "fn greet(name)", greet_item

        # `kind` distinguishes functions from variables.
        tau_item = next(it for it in items if it["label"] == "TAU")
        assert tau_item["kind"] == 6, tau_item  # CompletionItemKind.Variable
        assert println_item["kind"] == 3, println_item  # CompletionItemKind.Function

        print("completion_smoke: OK")
        print(f"  total items: {len(items)}")
        print(f"  println detail: {println_item['detail']}")
        print(f"  greet detail:   {greet_item['detail']}")
        print(f"  TAU detail:     {tau_item['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
