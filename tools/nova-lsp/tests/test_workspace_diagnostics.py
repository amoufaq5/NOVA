"""Unit + smoke + integration tests for workspace diagnostics.

Covers:

  * ``compute_result_id`` -- deterministic content hash; same text
    produces the same id; different text produces different ids.
  * ``parse_previous_result_ids`` -- wire-shape list -> dict
    conversion, including malformed / missing payloads.
  * ``enumerate_workspace_files`` -- union of indexed files and open
    overrides, sorted, deduplicated.
  * ``build_file_report`` -- ``kind: "unchanged"`` when prev id
    matches, ``kind: "full"`` otherwise; full reports carry resultId,
    uri, version, and items.
  * ``compute_workspace_diagnostics`` end-to-end:
      - empty workspace -> empty items
      - single-file workspace, clean -> 1 item with empty diagnostics
      - single-file with exhaustiveness WARN (R20D pattern) -> 1
        item with 1 diagnostic
      - multi-file (3 files, 2 with diagnostics) -> 3 items; 2 have
        non-empty diagnostics, 1 is empty
      - incremental: re-run with matching previousResultIds -> all
        items return kind="unchanged"
      - incremental: one file changed -> that file returns kind="full"
        with fresh diagnostics, the others stay kind="unchanged"
      - all four severities (error, warning, info, hint) round-trip
  * Open-buffer overrides authoritative over disk content.
  * Server-level wire smoke through ``dispatch`` for
    ``workspace/diagnostic`` plus the enhanced ``diagnosticProvider``
    capability advertisement.
  * Integration against ``src/`` -- walk the real NOVA codebase and
    report the total diagnostic count.

Assertions: ~30 (the ~20 target plus margin).
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
from nova_lsp.workspace_diagnostics import (  # noqa: E402
    KIND_FULL,
    KIND_UNCHANGED,
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    FileDiagnosticContext,
    build_file_report,
    compute_result_id,
    compute_workspace_diagnostics,
    enumerate_workspace_files,
    parse_previous_result_ids,
    path_to_uri,
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
# Deterministic synthetic runners. Production wires the runner to
# `run_compiler_check`; tests inject these so the suite doesn't depend
# on a working `nova` binary.
# ---------------------------------------------------------------------------


def _clean_runner(path: str, text: str):
    """Always returns zero diagnostics -- simulates a clean file."""
    return []


def _make_diag(line: int, severity: int, message: str):
    """Build one LSP Diagnostic dict at column 0."""
    return {
        "range": {
            "start": {"line": line, "character": 0},
            "end": {"line": line, "character": 1},
        },
        "severity": severity,
        "source": "nova",
        "message": message,
    }


def _exhaustiveness_runner(path: str, text: str):
    """Returns the R17A exhaustiveness WARN on any file mentioning `match`."""
    if "match" not in text:
        return []
    return [
        _make_diag(
            line=0,
            severity=SEVERITY_WARNING,
            message="non-exhaustive match on Shape (missing: Rect)",
        )
    ]


def _per_file_runner(diags_by_path):
    """Factory: returns a runner that looks up diagnostics by abs path."""
    def runner(path: str, text: str):
        return diags_by_path.get(os.path.abspath(path), [])
    return runner


# ---------------------------------------------------------------------------
# Unit tests -- compute_result_id.
# ---------------------------------------------------------------------------


def test_result_id_is_deterministic() -> None:
    a = compute_result_id("hello")
    b = compute_result_id("hello")
    assert_eq(a, b, "same text -> same result id")


def test_result_id_differs_for_different_text() -> None:
    a = compute_result_id("hello")
    b = compute_result_id("hello!")
    assert_(a != b, "different text -> different result id")


# ---------------------------------------------------------------------------
# Unit tests -- parse_previous_result_ids.
# ---------------------------------------------------------------------------


def test_parse_previous_result_ids_none() -> None:
    assert_eq(parse_previous_result_ids(None), {}, "None -> empty dict")
    assert_eq(parse_previous_result_ids([]), {}, "empty list -> empty dict")


def test_parse_previous_result_ids_well_formed() -> None:
    raw = [
        {"uri": "file:///a.nova", "value": "abc"},
        {"uri": "file:///b.nova", "value": "def"},
    ]
    out = parse_previous_result_ids(raw)
    assert_eq(out, {"file:///a.nova": "abc", "file:///b.nova": "def"},
              "well-formed list -> {uri: value}")


def test_parse_previous_result_ids_malformed() -> None:
    """Garbage entries are filtered out rather than crashing the server."""
    raw = [
        {"uri": "file:///a.nova", "value": "abc"},
        "not-a-dict",
        {"uri": "file:///c.nova"},          # missing value
        {"value": "xyz"},                   # missing uri
        {"uri": 42, "value": "num"},        # non-string uri
    ]
    out = parse_previous_result_ids(raw)
    assert_eq(out, {"file:///a.nova": "abc"}, "malformed entries dropped")


# ---------------------------------------------------------------------------
# Unit tests -- enumerate_workspace_files.
# ---------------------------------------------------------------------------


def test_enumerate_empty_workspace() -> None:
    idx = WorkspaceSymbolIndex()
    assert_eq(enumerate_workspace_files(idx), [], "empty workspace")


def test_enumerate_indexed_files() -> None:
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        _write(pa, "fn a() {}\n")
        _write(pb, "fn b() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        paths = enumerate_workspace_files(idx)
        assert_eq(sorted(paths),
                  sorted([os.path.abspath(pa), os.path.abspath(pb)]),
                  "indexed files surface")


def test_enumerate_includes_open_overrides() -> None:
    """Open buffers not yet in the index still appear in the candidate set."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        _write(pa, "fn a() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_file(pa)
        # Open buffer that's NOT in the index yet (e.g. an unsaved scratch).
        scratch = os.path.join(ws, "scratch.nova")
        overrides = {path_to_uri(scratch): "fn scratch() {}\n"}
        paths = enumerate_workspace_files(idx, overrides)
        assert_(os.path.abspath(scratch) in paths,
                "open override surfaces in candidate set")
        assert_(os.path.abspath(pa) in paths,
                "indexed file still surfaces")


# ---------------------------------------------------------------------------
# Unit tests -- build_file_report.
# ---------------------------------------------------------------------------


def test_build_file_report_full_when_no_prev_id() -> None:
    ctx = FileDiagnosticContext(
        uri="file:///x.nova",
        path="/x.nova",
        text="fn x() {}\n",
        version=3,
    )
    report = build_file_report(ctx, _clean_runner)
    assert_eq(report["kind"], KIND_FULL, "no prev id -> full")
    assert_eq(report["uri"], "file:///x.nova", "uri echoed")
    assert_eq(report["version"], 3, "version echoed")
    assert_eq(report["items"], [], "clean runner -> empty items")
    assert_("resultId" in report, "full report carries resultId")


def test_build_file_report_unchanged_when_prev_id_matches() -> None:
    text = "fn x() {}\n"
    rid = compute_result_id(text)
    ctx = FileDiagnosticContext(
        uri="file:///x.nova",
        path="/x.nova",
        text=text,
        version=3,
        previous_result_id=rid,
    )
    # Use a runner that would crash if called -- to confirm the
    # unchanged path short-circuits before invoking the engine.
    def boom(_p, _t):
        raise AssertionError("runner must not be invoked for unchanged report")
    report = build_file_report(ctx, boom)
    assert_eq(report["kind"], KIND_UNCHANGED, "matching prev id -> unchanged")
    assert_eq(report["resultId"], rid, "unchanged report carries the same id")
    assert_("items" not in report, "unchanged report has no items field")


def test_build_file_report_full_when_prev_id_stale() -> None:
    """Stale previousResultId (content changed) -> full report with new id."""
    ctx = FileDiagnosticContext(
        uri="file:///x.nova",
        path="/x.nova",
        text="fn new() {}\n",
        previous_result_id=compute_result_id("fn old() {}\n"),
    )
    report = build_file_report(ctx, _clean_runner)
    assert_eq(report["kind"], KIND_FULL, "stale prev id -> full")
    assert_(report["resultId"] != ctx.previous_result_id,
            "new resultId differs from stale previousResultId")


# ---------------------------------------------------------------------------
# compute_workspace_diagnostics -- empty workspace.
# ---------------------------------------------------------------------------


def test_empty_workspace_empty_report() -> None:
    idx = WorkspaceSymbolIndex()
    cache = FileCache()
    rep = compute_workspace_diagnostics(cache, idx, _clean_runner)
    assert_eq(rep, {"items": []}, "empty workspace -> empty report")


# ---------------------------------------------------------------------------
# compute_workspace_diagnostics -- single-file workspace.
# ---------------------------------------------------------------------------


def test_single_file_clean_one_item_empty_diagnostics() -> None:
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        _write(pa, "fn a() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        rep = compute_workspace_diagnostics(FileCache(), idx, _clean_runner)
        assert_eq(len(rep["items"]), 1, "single-file workspace: 1 item")
        item = rep["items"][0]
        assert_eq(item["kind"], KIND_FULL, "first report is full")
        assert_eq(item["uri"], path_to_uri(pa), "item uri matches file")
        assert_eq(item["items"], [], "clean file -> empty diagnostics list")


def test_single_file_with_exhaustiveness_warn() -> None:
    """R20D pattern -- a file containing a `match` triggers the WARN."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "shape.nova")
        _write(pa, "fn main() {\n  match s { _ => 0 }\n}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        rep = compute_workspace_diagnostics(
            FileCache(), idx, _exhaustiveness_runner,
        )
        assert_eq(len(rep["items"]), 1, "single-file: 1 item")
        diags = rep["items"][0]["items"]
        assert_eq(len(diags), 1, "one exhaustiveness warning")
        assert_eq(diags[0]["severity"], SEVERITY_WARNING, "severity=warning")
        assert_("non-exhaustive" in diags[0]["message"],
                "diagnostic message preserved")


# ---------------------------------------------------------------------------
# compute_workspace_diagnostics -- multi-file workspace.
# ---------------------------------------------------------------------------


def test_multi_file_mixed_diagnostics() -> None:
    """3 files: 2 with diagnostics, 1 clean -- 3 items, 2 non-empty."""
    with tempfile.TemporaryDirectory() as ws:
        dirty1 = os.path.join(ws, "dirty1.nova")
        dirty2 = os.path.join(ws, "dirty2.nova")
        clean = os.path.join(ws, "clean.nova")
        _write(dirty1, "fn d1() {}\n")
        _write(dirty2, "fn d2() {}\n")
        _write(clean, "fn c() {}\n")
        diags_by_path = {
            os.path.abspath(dirty1): [
                _make_diag(0, SEVERITY_ERROR, "missing semicolon"),
            ],
            os.path.abspath(dirty2): [
                _make_diag(0, SEVERITY_WARNING, "unused let"),
                _make_diag(2, SEVERITY_HINT, "consider renaming"),
            ],
        }
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        rep = compute_workspace_diagnostics(
            FileCache(), idx, _per_file_runner(diags_by_path),
        )
        assert_eq(len(rep["items"]), 3, "3 files indexed -> 3 items")
        by_uri = {it["uri"]: it for it in rep["items"]}
        d1_diags = by_uri[path_to_uri(dirty1)]["items"]
        d2_diags = by_uri[path_to_uri(dirty2)]["items"]
        c_diags = by_uri[path_to_uri(clean)]["items"]
        assert_eq(len(d1_diags), 1, "dirty1: 1 diagnostic")
        assert_eq(len(d2_diags), 2, "dirty2: 2 diagnostics")
        assert_eq(len(c_diags), 0, "clean: 0 diagnostics")
        total = sum(len(it.get("items", [])) for it in rep["items"])
        assert_eq(total, 3, "total diagnostics across workspace: 3")


# ---------------------------------------------------------------------------
# compute_workspace_diagnostics -- incremental support.
# ---------------------------------------------------------------------------


def test_incremental_unchanged_when_prev_ids_match() -> None:
    """Round-trip: previous report's resultIds -> all unchanged on re-run."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        _write(pa, "fn a() {}\n")
        _write(pb, "fn b() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        cache = FileCache()
        # First run -- full reports for both.
        first = compute_workspace_diagnostics(cache, idx, _clean_runner)
        for item in first["items"]:
            assert_eq(item["kind"], KIND_FULL, "first run: full")
        # Build prev ids from the first run; second run should return
        # unchanged for every file (none changed on disk).
        prev = {it["uri"]: it["resultId"] for it in first["items"]}
        second = compute_workspace_diagnostics(
            cache, idx, _clean_runner, previous_result_ids=prev,
        )
        assert_eq(len(second["items"]), 2, "second run still has 2 items")
        for item in second["items"]:
            assert_eq(item["kind"], KIND_UNCHANGED,
                      f"second run: {item['uri']} unchanged")
            assert_("items" not in item, "unchanged carries no items")


def test_incremental_changed_file_goes_full_others_unchanged() -> None:
    """File changed -> kind="full" for it; others stay kind="unchanged"."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        pb = os.path.join(ws, "b.nova")
        _write(pa, "fn a() {}\n")
        _write(pb, "fn b() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        cache = FileCache()
        first = compute_workspace_diagnostics(cache, idx, _clean_runner)
        prev = {it["uri"]: it["resultId"] for it in first["items"]}
        # Mutate file `a` so its hash no longer matches.
        new_text_a = "fn a() { return 42 }\n"
        # Override via document_overrides so we don't have to worry
        # about mtime/cache invalidation -- mirrors what the LSP does
        # when a buffer is open + edited.
        overrides = {path_to_uri(pa): new_text_a}
        # Inject a fresh diagnostic on file a only -- proves the
        # changed file actually re-ran through the engine.
        diags = {
            os.path.abspath(pa): [
                _make_diag(0, SEVERITY_INFO, "freshly computed"),
            ],
        }
        second = compute_workspace_diagnostics(
            cache, idx, _per_file_runner(diags),
            document_overrides=overrides,
            previous_result_ids=prev,
        )
        by_uri = {it["uri"]: it for it in second["items"]}
        a_item = by_uri[path_to_uri(pa)]
        b_item = by_uri[path_to_uri(pb)]
        assert_eq(a_item["kind"], KIND_FULL, "changed file -> full")
        assert_eq(len(a_item["items"]), 1, "changed file: fresh diagnostic")
        assert_eq(a_item["items"][0]["severity"], SEVERITY_INFO,
                  "fresh diagnostic severity")
        assert_eq(b_item["kind"], KIND_UNCHANGED,
                  "unchanged file -> unchanged")


def test_all_severities_preserved() -> None:
    """error / warning / info / hint all round-trip through the report."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        _write(pa, "fn a() {}\n")
        diags = {
            os.path.abspath(pa): [
                _make_diag(0, SEVERITY_ERROR, "err"),
                _make_diag(1, SEVERITY_WARNING, "warn"),
                _make_diag(2, SEVERITY_INFO, "info"),
                _make_diag(3, SEVERITY_HINT, "hint"),
            ],
        }
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        rep = compute_workspace_diagnostics(
            FileCache(), idx, _per_file_runner(diags),
        )
        items = rep["items"][0]["items"]
        sevs = [d["severity"] for d in items]
        assert_eq(sevs,
                  [SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_HINT],
                  "all four severities preserved in order")


# ---------------------------------------------------------------------------
# Open-buffer overrides authoritative over disk content.
# ---------------------------------------------------------------------------


def test_open_buffer_overrides_disk() -> None:
    """When a URI is in `document_overrides`, the runner sees buffer text."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "a.nova")
        _write(pa, "fn old() {}\n")
        idx = WorkspaceSymbolIndex()
        idx.index_workspace_root(ws)
        seen_text = []

        def runner(path: str, text: str):
            seen_text.append(text)
            return []

        overrides = {path_to_uri(pa): "fn buffered() {}\n"}
        compute_workspace_diagnostics(
            FileCache(), idx, runner, document_overrides=overrides,
        )
        assert_eq(seen_text, ["fn buffered() {}\n"],
                  "runner saw buffer text, not disk text")


# ---------------------------------------------------------------------------
# Server-level wire smoke -- dispatch through the LSP harness.
# ---------------------------------------------------------------------------


def test_server_diagnostic_capability_advertised() -> None:
    """`workspaceDiagnostics: True` shows up in the initialize response."""
    client = LspClient()
    init = client.initialize()
    caps = init["result"]["capabilities"]
    diag = caps.get("diagnosticProvider")
    assert_(diag is not None, "diagnosticProvider exists")
    assert_eq(diag.get("workspaceDiagnostics"), True,
              "workspaceDiagnostics flipped to True")


def test_server_workspace_diagnostic_wire() -> None:
    """End-to-end: open a file, request workspace/diagnostic, get a report."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "main.nova")
        _write(pa, "fn main() {}\n")
        client = LspClient()
        client.initialize(ws)
        client.open(pa, "fn main() {}\n")
        resp = client.request("workspace/diagnostic", {})
        result = resp["result"]
        assert_("items" in result, "wire response has items field")
        # We may get 1 or more items depending on whether the lazy
        # workspace crawl picks up the file as well as the open buffer.
        # Either way the file should appear exactly once with our URI.
        uris = [it["uri"] for it in result["items"]]
        assert_(path_to_uri(pa) in uris, "wire response includes our file")


def test_server_workspace_diagnostic_with_previous_ids() -> None:
    """previousResultIds flow through dispatch -> handler -> module."""
    with tempfile.TemporaryDirectory() as ws:
        pa = os.path.join(ws, "main.nova")
        _write(pa, "fn main() {}\n")
        client = LspClient()
        client.initialize(ws)
        client.open(pa, "fn main() {}\n")
        first = client.request("workspace/diagnostic", {})
        prev = [
            {"uri": it["uri"], "value": it["resultId"]}
            for it in first["result"]["items"]
            if "resultId" in it
        ]
        second = client.request(
            "workspace/diagnostic",
            {"previousResultIds": prev},
        )
        # At least one file should report unchanged on the second pass.
        kinds = [it["kind"] for it in second["result"]["items"]]
        assert_(KIND_UNCHANGED in kinds,
                "second wire call surfaces at least one unchanged")


# ---------------------------------------------------------------------------
# Integration against the real NOVA codebase.
# ---------------------------------------------------------------------------


def test_integration_walks_src_directory() -> None:
    """Walk `src/` (NOVA's real source tree) and report total diagnostic
    counts. Uses the clean runner so we don't depend on `nova --check`
    being available in the sandbox -- we're really checking the
    workspace walk picks up the files."""
    src_root = "/home/user/NOVA/src"
    if not os.path.isdir(src_root):
        print("  SKIP integration: src/ missing")
        return
    idx = WorkspaceSymbolIndex()
    n_indexed = idx.index_workspace_root(src_root)
    file_count = sum(1 for _ in idx._by_file.keys())  # noqa: SLF001
    rep = compute_workspace_diagnostics(FileCache(), idx, _clean_runner)
    print(f"  src/ indexed: {n_indexed} symbols across {file_count} files")
    print(f"  src/ workspace report: {len(rep['items'])} items")
    assert_(len(rep["items"]) == file_count,
            "every indexed file appears in the workspace report")
    # The clean runner reports zero per file; total diagnostics = 0.
    total = sum(len(it.get("items", [])) for it in rep["items"])
    print(f"  src/ total diagnostics (clean runner): {total}")
    assert_eq(total, 0, "clean runner -> zero total diagnostics")
    # With a synthetic runner that flags every file once, the count
    # should match the file count -- exercises the per-file fan-out.
    def one_diag_runner(_p, _t):
        return [_make_diag(0, SEVERITY_INFO, "synthetic")]
    rep2 = compute_workspace_diagnostics(FileCache(), idx, one_diag_runner)
    total2 = sum(len(it.get("items", [])) for it in rep2["items"])
    print(f"  src/ total diagnostics (one-per-file runner): {total2}")
    assert_eq(total2, file_count,
              "one-per-file runner -> exactly file_count diagnostics")


# ---------------------------------------------------------------------------


def main() -> int:
    test_result_id_is_deterministic()
    test_result_id_differs_for_different_text()
    test_parse_previous_result_ids_none()
    test_parse_previous_result_ids_well_formed()
    test_parse_previous_result_ids_malformed()
    test_enumerate_empty_workspace()
    test_enumerate_indexed_files()
    test_enumerate_includes_open_overrides()
    test_build_file_report_full_when_no_prev_id()
    test_build_file_report_unchanged_when_prev_id_matches()
    test_build_file_report_full_when_prev_id_stale()
    test_empty_workspace_empty_report()
    test_single_file_clean_one_item_empty_diagnostics()
    test_single_file_with_exhaustiveness_warn()
    test_multi_file_mixed_diagnostics()
    test_incremental_unchanged_when_prev_ids_match()
    test_incremental_changed_file_goes_full_others_unchanged()
    test_all_severities_preserved()
    test_open_buffer_overrides_disk()
    test_server_diagnostic_capability_advertised()
    test_server_workspace_diagnostic_wire()
    test_server_workspace_diagnostic_with_previous_ids()
    test_integration_walks_src_directory()
    print(f"test_workspace_diagnostics: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
