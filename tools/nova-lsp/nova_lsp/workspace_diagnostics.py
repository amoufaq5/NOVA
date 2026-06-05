"""Workspace-wide diagnostics aggregation (`workspace/diagnostic`).

LSP 3.17 introduced the pull-model diagnostic flow with two endpoints:

  * ``textDocument/diagnostic`` -- per-file pull (analogous to the
    older ``publishDiagnostics`` notification, but client-initiated).
  * ``workspace/diagnostic``    -- workspace-wide pull: the server
    walks every known file and returns one report per file.

We implement the workspace-wide variant here. It feeds a single
"problems" panel in the editor (VS Code's PROBLEMS tab, JetBrains'
Problems window, etc.) with ALL diagnostic markers across the
project, regardless of which files happen to be open. Without
``workspace/diagnostic`` the panel can only show diagnostics for
opened files, which is jarring when the user has CI failures in a
file they haven't visited yet.

Wire shape -- ``WorkspaceDiagnosticReport``::

    interface WorkspaceDiagnosticReport {
        items: WorkspaceDocumentDiagnosticReport[];
    }

    type WorkspaceDocumentDiagnosticReport =
        | WorkspaceFullDocumentDiagnosticReport
        | WorkspaceUnchangedDocumentDiagnosticReport;

    interface WorkspaceFullDocumentDiagnosticReport {
        kind: "full";
        resultId?: string;          // server-private cache key
        uri: DocumentUri;
        version: number | null;
        items: Diagnostic[];
    }

    interface WorkspaceUnchangedDocumentDiagnosticReport {
        kind: "unchanged";
        resultId: string;
        uri: DocumentUri;
        version: number | null;
    }

Incremental support uses the ``resultId`` field. A client that has
seen a previous report sends back ``previousResultIds: [{uri,
value}, ...]``; the server compares each entry against its current
content hash and either:

  * returns ``kind: "unchanged"`` (the file's content hash matches
    the previousResultId -> nothing has changed since last time), OR
  * returns ``kind: "full"`` with a fresh ``items`` payload and a
    new ``resultId``.

Hash function:
  * sha1 of the file's text content -- deterministic, content-based.
    Keeps the protocol stateless; the server doesn't need a session
    table of result IDs.

Candidate file enumeration mirrors R8C's ``workspace_symbols``:
  * every file the workspace index has indexed (lives in
    ``WorkspaceSymbolIndex._by_file``); and
  * every open buffer (``document_overrides`` maps URI -> text,
    authoritative over disk so unsaved edits are checked too).

The diagnostic engine itself is pluggable via ``diagnostic_runner``
(a ``Callable[[str, str], List[Dict[str, Any]]]``). Production wires
this to a function that shells out to ``nova --check``; tests pass
in a deterministic synthetic engine so the unit suite doesn't depend
on a working compiler binary.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from nova_lsp.imports import FileCache
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# ---------------------------------------------------------------------------
# Wire-shape constants. Kept as module-level strings so tests can assert
# without hard-coding the literal value at every call site.
# ---------------------------------------------------------------------------

KIND_FULL = "full"
KIND_UNCHANGED = "unchanged"

# LSP diagnostic severity enum -- mirrored from server.parse_compiler_output
# so callers (and tests) can build diagnostics without touching server.
SEVERITY_ERROR = 1
SEVERITY_WARNING = 2
SEVERITY_INFO = 3
SEVERITY_HINT = 4


# A diagnostic runner takes (path, text) and returns LSP Diagnostic[]
# (the wire shape -- each entry has range / severity / message / source).
# Production code wires this to a function that writes `text` to a
# tempfile and runs ``nova --check``; tests pass deterministic stubs.
DiagnosticRunner = Callable[[str, str], List[Dict[str, Any]]]


# ---------------------------------------------------------------------------
# Path / URI helpers. Duplicated from server.py so this module doesn't
# pull in a circular dependency on the dispatcher (server imports us,
# not the other way around -- same pattern as workspace_symbols).
# ---------------------------------------------------------------------------


def path_to_uri(path: str) -> str:
    """Mirror of server.path_to_uri."""
    return "file://" + os.path.abspath(path)


def uri_to_path(uri: str) -> Optional[str]:
    """Mirror of server.uri_to_path."""
    if not uri.startswith("file://"):
        return None
    parsed = urlparse(uri)
    return unquote(parsed.path)


# ---------------------------------------------------------------------------
# Result-ID hashing -- content-based so the protocol stays stateless.
# ---------------------------------------------------------------------------


def compute_result_id(text: str) -> str:
    """Return the canonical ``resultId`` for `text`.

    Uses sha1 -- deterministic, fast, and short enough to comfortably
    fit in a JSON-RPC payload. The hash is over the raw text bytes so
    two files with byte-identical content share the same resultId
    even when they live at different paths.
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Workspace file enumeration.
# ---------------------------------------------------------------------------


def enumerate_workspace_files(
    workspace_index: WorkspaceSymbolIndex,
    document_overrides: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Return absolute paths of every file we should check.

    Union of:

      * every file the workspace symbol index has indexed (the lazy
        crawl on first workspace/symbol query populates this); and
      * every open document URI (so the user's unsaved buffers feed
        into the panel without waiting for didSave to fire).

    Sorted for deterministic output -- the editor sorts by URI in the
    panel anyway, but a stable server-side order makes the tests
    easier to write and the protocol traces easier to read.
    """
    paths: Set[str] = set()
    # noinspection PyProtectedMember
    for p in workspace_index._by_file.keys():  # noqa: SLF001
        paths.add(os.path.abspath(p))
    if document_overrides:
        for uri in document_overrides.keys():
            path = uri_to_path(uri)
            if path:
                paths.add(os.path.abspath(path))
    return sorted(paths)


# ---------------------------------------------------------------------------
# Text resolution -- open buffer wins over on-disk content, exactly as
# code_lens._read_text. Returning None means "skip this file".
# ---------------------------------------------------------------------------


def _read_text(
    path: str,
    file_cache: FileCache,
    document_overrides: Dict[str, str],
) -> Optional[str]:
    """Pull the live text for `path`.

    Lookup order:
      1. ``document_overrides[path_to_uri(path)]`` -- the open buffer
         text. Authoritative when present.
      2. ``file_cache.get(path).text`` -- the mtime-keyed on-disk copy.
      3. Direct read from disk (fallback when the cache is cold).

    Returns ``None`` when none of the above produce a result; the
    caller then skips this file entirely.
    """
    abs_path = os.path.abspath(path)
    uri = path_to_uri(abs_path)
    if uri in document_overrides:
        return document_overrides[uri]
    entry = file_cache.get(abs_path)
    if entry is not None:
        return entry.text
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Single-file report construction.
# ---------------------------------------------------------------------------


@dataclass
class FileDiagnosticContext:
    """All inputs needed to build one WorkspaceDocumentDiagnosticReport.

    Bundled into a dataclass so test helpers can populate / mutate
    individual fields without long parameter lists.
    """
    uri: str
    path: str
    text: str
    version: Optional[int] = None
    previous_result_id: Optional[str] = None


def build_file_report(
    ctx: FileDiagnosticContext,
    diagnostic_runner: DiagnosticRunner,
) -> Dict[str, Any]:
    """Return one WorkspaceDocumentDiagnosticReport for `ctx`.

    If `ctx.previous_result_id` matches the current content hash, we
    return ``{"kind": "unchanged", "resultId": ..., "uri": ..., "version": ...}``
    -- a tiny payload that tells the client "no change since last
    time, keep the cached items". Otherwise we run the diagnostic
    engine and return a full report with fresh items + a fresh
    ``resultId``.
    """
    new_result_id = compute_result_id(ctx.text)
    if (
        ctx.previous_result_id is not None
        and ctx.previous_result_id == new_result_id
    ):
        return {
            "kind": KIND_UNCHANGED,
            "resultId": new_result_id,
            "uri": ctx.uri,
            "version": ctx.version,
        }
    items = diagnostic_runner(ctx.path, ctx.text)
    return {
        "kind": KIND_FULL,
        "resultId": new_result_id,
        "uri": ctx.uri,
        "version": ctx.version,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Top-level entry point.
# ---------------------------------------------------------------------------


def compute_workspace_diagnostics(
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    diagnostic_runner: DiagnosticRunner,
    *,
    document_overrides: Optional[Dict[str, str]] = None,
    document_versions: Optional[Dict[str, int]] = None,
    previous_result_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Return the LSP ``WorkspaceDiagnosticReport`` payload.

    Parameters:

      * ``file_cache`` -- R5F's mtime-keyed file cache, shared with
        the rest of the LSP so on-disk reads don't double-up.
      * ``workspace_index`` -- R8C's symbol index. Its
        ``_by_file`` keys give us the candidate file set.
      * ``diagnostic_runner`` -- callable ``(path, text) -> Diagnostic[]``
        that produces the diagnostics for one file. Production wires
        this to a function that shells out to ``nova --check``; tests
        inject deterministic stubs.
      * ``document_overrides`` -- ``{uri: text}`` map of open buffers.
        Text wins over disk; URIs not already in the workspace index
        are added to the candidate set so unsaved buffers feed the
        panel too.
      * ``document_versions`` -- ``{uri: version}`` map of open buffer
        versions. Echoed back in each per-file report so the client
        can correlate the diagnostics with the buffer they were
        computed against.
      * ``previous_result_ids`` -- ``{uri: result_id}`` map carried
        forward from the previous workspace/diagnostic response. Files
        whose current content hash matches the previousResultId return
        ``kind: "unchanged"``; the rest get a fresh ``kind: "full"``
        report.

    The returned dict matches the LSP ``WorkspaceDiagnosticReport``
    wire shape exactly so the dispatcher can hand it back as a JSON-RPC
    result without further massaging.
    """
    overrides = dict(document_overrides or {})
    versions = dict(document_versions or {})
    prev_ids = dict(previous_result_ids or {})

    items: List[Dict[str, Any]] = []
    paths = enumerate_workspace_files(workspace_index, overrides)
    for path in paths:
        text = _read_text(path, file_cache, overrides)
        if text is None:
            continue
        uri = path_to_uri(path)
        ctx = FileDiagnosticContext(
            uri=uri,
            path=path,
            text=text,
            version=versions.get(uri),
            previous_result_id=prev_ids.get(uri),
        )
        items.append(build_file_report(ctx, diagnostic_runner))
    return {"items": items}


# ---------------------------------------------------------------------------
# Tiny convenience: parse the ``previousResultIds`` array shape into a
# ``{uri: resultId}`` dict. The LSP spec defines it as::
#
#     previousResultIds: { uri: DocumentUri, value: string }[]
#
# so callers (the dispatcher) need a one-step unpacker.
# ---------------------------------------------------------------------------


def parse_previous_result_ids(
    raw: Optional[List[Dict[str, Any]]],
) -> Dict[str, str]:
    """Convert the wire-shape ``previousResultIds`` list into a dict.

    The LSP spec sends ``[{uri, value}, ...]`` because not every JSON
    consumer can key a map by an arbitrary URI string; we want the
    dict form internally for O(1) lookup. Returns ``{}`` when ``raw``
    is None/empty/malformed so the caller never has to special-case
    a missing previous-IDs payload.
    """
    if not raw or not isinstance(raw, list):
        return {}
    out: Dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri")
        value = entry.get("value")
        if isinstance(uri, str) and isinstance(value, str):
            out[uri] = value
    return out
