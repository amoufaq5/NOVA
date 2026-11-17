"""Diagnostics from the real NOVA compiler (audit F-27).

The compiler prints parse errors as ``error[line N]: msg``, semantic errors
as ``ERROR: msg`` / ``Error: msg`` and warnings as ``warning: msg``. The old
regex only knew ``file:line:col: level: msg`` and matched none of them.

The check is syntax-only on a copy with ``import`` lines blanked: the compiler
expands imports textually, so its line numbers would count the merged program,
and a full compile flags valid library modules (built by concatenation) with
"undeclared callee".

Unit checks run everywhere; the end-to-end checks run ``bin/nova`` from the
repository root and are skipped when it has not been built.
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_PKG_ROOT))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_lsp.server import (  # noqa: E402
    Document,
    ServerState,
    parse_compiler_output,
    path_to_uri,
    run_compiler_check,
)

_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def _doc(text="", path="/tmp/x.nova"):
    return Document(uri=path_to_uri(path), text=text)


def test_parse_line_error() -> None:
    out = "=== Nova Compiler Starting ===\nerror[line 3]: unexpected }\n  | }\n  |  ^\n"
    d = parse_compiler_output(out, _doc())
    assert_(len(d) == 1, "one diagnostic from error[line N]")
    assert_(d[0]["range"]["start"]["line"] == 2, "line N -> 0-based N-1")
    assert_(d[0]["severity"] == 1, "error severity")
    assert_(d[0]["message"] == "unexpected }", "message text")


def test_parse_semantic_error_and_warning() -> None:
    out = ("ERROR: undeclared callee: foo\n"
           "warning: non-exhaustive match on Color (missing: Blue)\n"
           "Error: cannot read file: nope.nova\n"
           "OK: syntax check passed\nCompiled: a -> b\n")
    d = parse_compiler_output(out, _doc())
    assert_(len(d) == 3, "ERROR, warning and Error lines parsed; OK/Compiled ignored")
    assert_(d[0]["severity"] == 1 and d[0]["range"]["start"]["line"] == 0, "ERROR at top")
    assert_(d[1]["severity"] == 2, "warning severity")
    assert_("undeclared callee" in d[0]["message"], "ERROR message")


def test_caret_block_is_not_parsed() -> None:
    # After error[line N] the compiler echoes the rest of the source; numeric
    # source lines must not become diagnostics.
    out = "error[line 3]: unexpected }\n  | }\n10,\n20\nfoo: 7\n"
    d = parse_compiler_output(out, _doc())
    assert_(len(d) == 1, "only the error line, not the echoed source")


def test_parse_generic_format_still_works() -> None:
    d = parse_compiler_output("x.nova:5:7: warning: thing", _doc())
    assert_(len(d) == 1 and d[0]["range"]["start"]["line"] == 4, "file:line:col form")
    assert_(d[0]["range"]["start"]["character"] == 6, "column")


def _compiler():
    p = os.path.join(_REPO, "bin", "nova")
    return p if os.path.exists(p) and os.access(p, os.X_OK) else None


def test_real_compiler() -> None:
    nova = _compiler()
    if nova is None:
        print("  (skipping real-compiler checks: bin/nova not built)")
        return
    st = ServerState(nova_compiler=nova)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "t.nova")
        bad_syntax = "fn main() {\n    let x = \n}\n"
        d = run_compiler_check(st, Document(uri=path_to_uri(path), text=bad_syntax))
        assert_(len(d) == 1, "syntax error reported once")
        assert_(d[0]["range"]["start"]["line"] == 2, "syntax error on line 3")
        # A file that imports a 7-line library: the error must land on the
        # importing file's own line, not the merged-program line.
        with open(os.path.join(td, "lib.nova"), "w") as f:
            f.write("fn _lib_a() {\n    return 1\n}\n\nfn _lib_b() {\n    return 2\n}\n")
        imported = 'import "lib.nova"\nfn main() {\n    let x = \n}\nmain()\n'
        d = run_compiler_check(st, Document(uri=path_to_uri(path), text=imported))
        assert_(len(d) == 1 and d[0]["range"]["start"]["line"] == 3, "line number not shifted by imports")
        # A broken imported file is not reported in the importer.
        with open(os.path.join(td, "broken.nova"), "w") as f:
            f.write("fn {\n")
        importer = 'import "broken.nova"\nfn main() {\n    println("ok")\n}\nmain()\n'
        d = run_compiler_check(st, Document(uri=path_to_uri(path), text=importer))
        assert_(d == [], "errors in an imported file are not blamed on the importer")
        clean = 'import "std/string"\nfn main() {\n    println("hi")\n}\nmain()\n'
        d = run_compiler_check(st, Document(uri=path_to_uri(path), text=clean))
        assert_(d == [], "clean program (with a std import) has no diagnostics")
        assert_([f for f in os.listdir(td) if f.startswith(".nova-lsp-")] == [], "nothing written next to the file")
    # A library module built by concatenation (calls siblings it does not
    # import) is valid and must not be flagged.
    cg = os.path.join(_REPO, "src", "compiler", "codegen.nova")
    if os.path.exists(cg):
        with open(cg, encoding="utf-8") as f:
            d = run_compiler_check(st, Document(uri=path_to_uri(cg), text=f.read()))
        assert_(d == [], "src/compiler/codegen.nova has no diagnostics")


def main() -> int:
    test_parse_line_error()
    test_parse_semantic_error_and_warning()
    test_caret_block_is_not_parsed()
    test_parse_generic_format_still_works()
    test_real_compiler()
    print(f"test_compiler_diagnostics: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
