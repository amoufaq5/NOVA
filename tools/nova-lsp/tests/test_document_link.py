"""R33D — `textDocument/documentLink` tests.

Covers the document-link capability that turns ``import "..."`` statements
into clickable hyperlinks:

  * `scan_imports` -- regex parses every `import "..."` line and surfaces
    the path literal + the column span BETWEEN the quotes (so the editor's
    underline doesn't overlap the quote characters).
  * `resolve_import_path` -- relative paths anchor on the source file's
    directory; absolute paths pass through unchanged; empty paths return
    None.
  * `path_to_file_uri` -- absolute path -> `file://` URI with the standard
    `urllib.parse.quote` safe set.
  * `build_document_link` payload shape (range, target, tooltip, data).
  * `compute_document_links` end-to-end:
      - single import -> 1 link with the right URI
      - multiple imports -> N links in source order
      - relative path (`"../foo.nova"`) -> URI rooted at the resolved
        parent dir
      - non-existent file -> link still emitted (dead-link UX is the
        editor's job)
      - string literal NOT preceded by `import` -> NO link
      - leading whitespace before `import` -> handled
      - empty file -> no links
      - import with empty path string -> dropped from the result
  * Server-level wire smoke through `dispatch` for
    `textDocument/documentLink` plus the `documentLinkProvider` capability.
  * Integration: a real test file like `tests/test_system.nova` with 15
    imports -> 15 links emitted.

Total assertions: well over the 10+ floor — ~25 cover the unit + smoke
+ integration paths.
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
from nova_lsp.document_link import (  # noqa: E402
    ImportLink,
    build_document_link,
    compute_document_links,
    path_to_file_uri,
    resolve_import_path,
    scan_imports,
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


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


# ---------------------------------------------------------------------------
# scan_imports.
# ---------------------------------------------------------------------------


def test_scan_imports_single() -> None:
    text = 'import "src/foo.nova"\n'
    links = scan_imports(text)
    assert_eq(len(links), 1, "single import -> 1 link")
    assert_eq(links[0].line, 0, "import on line 0")
    assert_eq(links[0].raw_path, "src/foo.nova", "path text captured")
    # Path is between the quotes. The line starts with `import "` (8 chars
    # including the opening quote) so char_start should be 8.
    assert_eq(links[0].char_start, 8, "path starts after `import \"`")
    assert_eq(links[0].char_end, 8 + len("src/foo.nova"), "char_end at end of path")


def test_scan_imports_multiple_preserves_order() -> None:
    text = (
        'import "a.nova"\n'
        'import "b.nova"\n'
        'import "c.nova"\n'
        "fn main() {}\n"
    )
    links = scan_imports(text)
    assert_eq(len(links), 3, "three imports")
    assert_eq([l.raw_path for l in links],
              ["a.nova", "b.nova", "c.nova"],
              "imports kept in source order")


def test_scan_imports_indented() -> None:
    """Leading whitespace before `import` is accepted."""
    text = '    import "indented.nova"\n'
    links = scan_imports(text)
    assert_eq(len(links), 1, "indented import recognised")
    # `    import "` is 12 chars before the path begins.
    assert_eq(links[0].char_start, 12, "char_start respects indentation")


def test_scan_imports_string_not_preceded_by_import() -> None:
    """A plain string literal must NOT promote to an import link."""
    text = (
        'let path = "src/foo.nova"\n'
        'println("hello.nova")\n'
    )
    links = scan_imports(text)
    assert_eq(links, [], "no imports -> empty list")


def test_scan_imports_empty_file() -> None:
    assert_eq(scan_imports(""), [], "empty file -> no imports")


def test_scan_imports_no_imports_in_source() -> None:
    text = (
        "fn main() {\n"
        '    println("no imports here")\n'
        "}\n"
    )
    assert_eq(scan_imports(text), [], "fn-only file -> no imports")


def test_scan_imports_empty_path() -> None:
    """`import ""` should be captured (the resolver drops it later)."""
    text = 'import ""\n'
    links = scan_imports(text)
    assert_eq(len(links), 1, "empty-path import still captured")
    assert_eq(links[0].raw_path, "", "raw_path is empty string")


# ---------------------------------------------------------------------------
# resolve_import_path.
# ---------------------------------------------------------------------------


def test_resolve_import_path_relative() -> None:
    base = "/tmp/ws/src"
    out = resolve_import_path("../runtime/path.nova", base)
    assert_eq(out, os.path.abspath("/tmp/ws/runtime/path.nova"),
              "relative path resolved + normalised")


def test_resolve_import_path_absolute() -> None:
    out = resolve_import_path("/abs/path/file.nova", "/tmp/ws/src")
    assert_eq(out, "/abs/path/file.nova",
              "absolute path returned as-is")


def test_resolve_import_path_empty_returns_none() -> None:
    out = resolve_import_path("", "/tmp/ws")
    assert_eq(out, None, "empty path -> None")


def test_resolve_import_path_nonexistent_still_resolved() -> None:
    """Existence is NOT checked — the editor handles dead-link UI."""
    out = resolve_import_path("does_not_exist.nova", "/tmp/ws")
    assert_eq(out, "/tmp/ws/does_not_exist.nova",
              "non-existent file still gets a resolved path")


# ---------------------------------------------------------------------------
# path_to_file_uri.
# ---------------------------------------------------------------------------


def test_path_to_file_uri_simple() -> None:
    uri = path_to_file_uri("/tmp/ws/file.nova")
    assert_eq(uri, "file:///tmp/ws/file.nova",
              "plain path -> file:// URI")


def test_path_to_file_uri_preserves_slashes() -> None:
    """`/` is in the safe set so the URI structure stays intact."""
    uri = path_to_file_uri("/a/b/c/d.nova")
    assert_("file:///a/b/c/d.nova" == uri,
            "slashes preserved in URI path")


# ---------------------------------------------------------------------------
# build_document_link payload shape.
# ---------------------------------------------------------------------------


def test_build_document_link_shape() -> None:
    link = ImportLink(line=3, char_start=8, char_end=20, raw_path="src/x.nova")
    out = build_document_link(link, "file:///abs/src/x.nova")
    assert_eq(out["range"]["start"]["line"], 3, "range line == link line")
    assert_eq(out["range"]["start"]["character"], 8, "char_start")
    assert_eq(out["range"]["end"]["character"], 20, "char_end")
    assert_eq(out["target"], "file:///abs/src/x.nova", "target URI populated")
    assert_("tooltip" in out, "tooltip emitted")
    assert_eq(out["data"]["raw_path"], "src/x.nova", "data carries raw_path")


def test_build_document_link_no_target() -> None:
    """An empty target_uri yields a link without `target` / `tooltip`.

    The wire-level `compute_document_links` doesn't currently emit these
    (it drops empty-path links entirely), but the builder accepts None
    so future shapes (e.g. a half-typed path) could still surface a
    no-op link if desired.
    """
    link = ImportLink(line=0, char_start=8, char_end=8, raw_path="")
    out = build_document_link(link, None)
    assert_("target" not in out, "no target when uri is None")
    assert_("tooltip" not in out, "no tooltip without target")


# ---------------------------------------------------------------------------
# compute_document_links end-to-end.
# ---------------------------------------------------------------------------


def test_compute_links_single_import() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = 'import "sibling.nova"\nfn main() {}\n'
        _write(path, text)
        links = compute_document_links(_uri(path), text)
        assert_eq(len(links), 1, "single link emitted")
        # Resolved against the file's directory.
        expected_target = path_to_file_uri(
            os.path.abspath(os.path.join(ws, "sibling.nova"))
        )
        assert_eq(links[0]["target"], expected_target,
                  "target resolved relative to source file")


def test_compute_links_multiple_imports() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = (
            'import "a.nova"\n'
            'import "b.nova"\n'
            'import "c.nova"\n'
            "fn main() {}\n"
        )
        _write(path, text)
        links = compute_document_links(_uri(path), text)
        assert_eq(len(links), 3, "three links for three imports")
        targets = [l["target"] for l in links]
        assert_(targets[0].endswith("/a.nova"), "first target is a.nova")
        assert_(targets[1].endswith("/b.nova"), "second target is b.nova")
        assert_(targets[2].endswith("/c.nova"), "third target is c.nova")


def test_compute_links_relative_path() -> None:
    """`../foo.nova` resolves relative to the file's own directory."""
    with tempfile.TemporaryDirectory() as ws:
        sub = os.path.join(ws, "subdir")
        os.makedirs(sub)
        path = os.path.join(sub, "main.nova")
        text = 'import "../sibling.nova"\n'
        _write(path, text)
        links = compute_document_links(_uri(path), text)
        assert_eq(len(links), 1, "one link")
        # Resolved relative to the file's own directory.
        expected = path_to_file_uri(
            os.path.abspath(os.path.join(ws, "sibling.nova"))
        )
        assert_eq(links[0]["target"], expected,
                  "relative path resolved correctly")


def test_compute_links_nonexistent_still_emitted() -> None:
    """A path that doesn't exist on disk MUST still produce a link.

    Dead-link UX is the editor's job (red squiggle on click etc); the
    LSP should not gate the link server-side because the file may be
    about to appear in another working copy or branch.
    """
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = 'import "this_does_not_exist.nova"\n'
        _write(path, text)
        links = compute_document_links(_uri(path), text)
        assert_eq(len(links), 1, "dead link still emitted")
        assert_("target" in links[0], "dead link has target URI")
        assert_(links[0]["target"].endswith("/this_does_not_exist.nova"),
                "dead link's URI matches the raw path")


def test_compute_links_string_literal_not_promoted() -> None:
    """`let path = "src/foo.nova"` is NOT a documentLink."""
    text = (
        'let cfg_path = "src/config.nova"\n'
        'fn main() { println("hello.nova") }\n'
    )
    links = compute_document_links("file:///tmp/main.nova", text)
    assert_eq(links, [], "non-import strings yield no links")


def test_compute_links_mixed_imports_and_strings() -> None:
    """Only the import lines become links; the string literals are skipped."""
    text = (
        'import "real.nova"\n'
        'let path = "fake_import.nova"\n'
        'import "another_real.nova"\n'
    )
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        _write(path, text)
        links = compute_document_links(_uri(path), text)
        assert_eq(len(links), 2, "two real imports, fake string excluded")
        assert_(links[0]["target"].endswith("/real.nova"), "first is real.nova")
        assert_(links[1]["target"].endswith("/another_real.nova"),
                "second is another_real.nova")


def test_compute_links_empty_file() -> None:
    links = compute_document_links("file:///tmp/empty.nova", "")
    assert_eq(links, [], "empty file -> no links")


def test_compute_links_drops_empty_path() -> None:
    """`import ""` returns no link (no useful target)."""
    text = 'import ""\nfn main() {}\n'
    links = compute_document_links("file:///tmp/main.nova", text)
    assert_eq(len(links), 0, "empty-path import dropped from result")


# ---------------------------------------------------------------------------
# Server-level wire smoke.
# ---------------------------------------------------------------------------


def test_server_document_link_capability_advertised() -> None:
    client = LspClient()
    resp = client.initialize()
    caps = resp["result"]["capabilities"]
    assert_("documentLinkProvider" in caps,
            "documentLinkProvider in capabilities")


def test_server_document_link_wire() -> None:
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        text = (
            'import "alpha.nova"\n'
            'import "beta.nova"\n'
            "fn main() { return 0 }\n"
        )
        _write(path, text)
        client = LspClient()
        client.initialize(root_path=ws)
        uri = client.open(path, text)
        resp = client.request(
            "textDocument/documentLink",
            {"textDocument": {"uri": uri}},
        )
        result = resp["result"]
        assert_(isinstance(result, list), "result is a list")
        assert_eq(len(result), 2, "two links wired through dispatch")
        # First link is alpha.nova, second is beta.nova (source order).
        assert_(result[0]["target"].endswith("/alpha.nova"),
                "first link target is alpha")
        assert_(result[1]["target"].endswith("/beta.nova"),
                "second link target is beta")


def test_server_document_link_no_doc() -> None:
    """A request for a URI we haven't opened returns an empty list."""
    client = LspClient()
    client.initialize()
    resp = client.request(
        "textDocument/documentLink",
        {"textDocument": {"uri": "file:///nonexistent/x.nova"}},
    )
    assert_eq(resp["result"], [], "unopened doc -> empty list")


# ---------------------------------------------------------------------------
# Integration -- real file with many imports.
# ---------------------------------------------------------------------------


def test_integration_test_system_imports() -> None:
    """`tests/test_system.nova` imports 15 sibling modules — every one
    should surface as a clickable link.
    """
    src = "/home/user/NOVA/tests/test_system.nova"
    if not os.path.isfile(src):
        print("  SKIP integration: tests/test_system.nova missing")
        return
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    links = compute_document_links(_uri(src), text)
    # Count the raw imports for ground truth (one per `import "..."` line).
    expected = sum(
        1 for line in text.splitlines()
        if re.match(r'^\s*import\s+"', line)
    )
    print(f"  test_system.nova: {len(links)} links emitted (grep={expected})")
    assert_(len(links) >= 10, "at least 10 imports found")
    assert_eq(len(links), expected, "link count matches grep count")
    # Every link points at a URI that resolves under src/.
    for l in links:
        assert_(l["target"].startswith("file://"),
                "every target is a file:// URI")


def test_integration_parser_imports_zero() -> None:
    """`src/compiler/parser.nova` has NO imports — link count must be 0.

    The bootstrapping compiler files are self-contained (no `import`
    statements at all), so this is the canonical "zero links" test
    against a real-world file.
    """
    src = "/home/user/NOVA/src/compiler/parser.nova"
    if not os.path.isfile(src):
        print("  SKIP integration: parser.nova missing")
        return
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    links = compute_document_links(_uri(src), text)
    print(f"  parser.nova: {len(links)} links")
    assert_eq(links, [], "parser.nova has zero imports -> zero links")


# ---------------------------------------------------------------------------


def main() -> int:
    test_scan_imports_single()
    test_scan_imports_multiple_preserves_order()
    test_scan_imports_indented()
    test_scan_imports_string_not_preceded_by_import()
    test_scan_imports_empty_file()
    test_scan_imports_no_imports_in_source()
    test_scan_imports_empty_path()
    test_resolve_import_path_relative()
    test_resolve_import_path_absolute()
    test_resolve_import_path_empty_returns_none()
    test_resolve_import_path_nonexistent_still_resolved()
    test_path_to_file_uri_simple()
    test_path_to_file_uri_preserves_slashes()
    test_build_document_link_shape()
    test_build_document_link_no_target()
    test_compute_links_single_import()
    test_compute_links_multiple_imports()
    test_compute_links_relative_path()
    test_compute_links_nonexistent_still_emitted()
    test_compute_links_string_literal_not_promoted()
    test_compute_links_mixed_imports_and_strings()
    test_compute_links_empty_file()
    test_compute_links_drops_empty_path()
    test_server_document_link_capability_advertised()
    test_server_document_link_wire()
    test_server_document_link_no_doc()
    test_integration_test_system_imports()
    test_integration_parser_imports_zero()
    print(f"test_document_link: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
