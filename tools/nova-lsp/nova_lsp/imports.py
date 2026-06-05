"""Import-graph walker + per-file definition cache.

Used by `textDocument/definition` and `textDocument/references` to look
up symbols defined in files reachable through `import "..."` statements.

Each cache entry stores `mtime`, plus pre-scanned `fn_defs` and
`let_defs` maps keyed by name -> (line, col_start, col_end). Entries are
invalidated when the file's mtime advances past the cached value.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple


# Re-use the same shapes as server.py — kept here to avoid a circular
# import. The regexes match what `_scan_text` uses but additionally
# expose the column span of the matched name (start/end characters on
# the matching line) so we can return precise `Location` ranges.
_IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')
_FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_LET_DEF_RE = re.compile(r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")


# (line, char_start, char_end) — zero-based, LSP-friendly.
Span = Tuple[int, int, int]


@dataclass
class FileEntry:
    """Cached scan result for one .nova file."""
    path: str
    mtime: float
    text: str
    fn_defs: Dict[str, Span] = field(default_factory=dict)
    let_defs: Dict[str, Span] = field(default_factory=dict)
    imports: List[str] = field(default_factory=list)


def _scan_definitions(text: str) -> Tuple[Dict[str, Span], Dict[str, Span], List[str]]:
    """Walk every line in `text` and collect:
      * `fn name(...) {`   -> name, line, char span of the name.
      * `let name = ...`   -> ditto.
      * `import "..."`     -> the quoted relative/absolute path string.
    First definition wins (matches the rest of the LSP's regex-based behavior)."""
    fn_defs: Dict[str, Span] = {}
    let_defs: Dict[str, Span] = {}
    imports: List[str] = []
    for line_no, line in enumerate(text.splitlines()):
        m_imp = _IMPORT_RE.match(line)
        if m_imp:
            imports.append(m_imp.group(1))
            continue
        m_fn = _FN_DEF_RE.match(line)
        if m_fn:
            name = m_fn.group(1)
            if name not in fn_defs:
                start = m_fn.start(1)
                end = m_fn.end(1)
                fn_defs[name] = (line_no, start, end)
            continue
        m_let = _LET_DEF_RE.match(line)
        if m_let:
            name = m_let.group(1)
            if name not in let_defs:
                start = m_let.start(1)
                end = m_let.end(1)
                let_defs[name] = (line_no, start, end)
    return fn_defs, let_defs, imports


class FileCache:
    """In-memory cache keyed by absolute path with mtime invalidation."""

    def __init__(self) -> None:
        self._entries: Dict[str, FileEntry] = {}

    def invalidate(self, path: str) -> None:
        """Drop the cache entry for `path` if present (used by didChange)."""
        try:
            abs_path = os.path.abspath(path)
        except (OSError, ValueError):
            return
        self._entries.pop(abs_path, None)

    def get(self, path: str) -> Optional[FileEntry]:
        """Return a fresh FileEntry for `path`, rescanning if the on-disk
        mtime has advanced past the cached value. Returns `None` if the
        file cannot be opened."""
        try:
            abs_path = os.path.abspath(path)
        except (OSError, ValueError):
            return None
        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            return None
        cached = self._entries.get(abs_path)
        if cached is not None and cached.mtime >= mtime:
            return cached
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None
        fn_defs, let_defs, imports = _scan_definitions(text)
        entry = FileEntry(
            path=abs_path,
            mtime=mtime,
            text=text,
            fn_defs=fn_defs,
            let_defs=let_defs,
            imports=imports,
        )
        self._entries[abs_path] = entry
        return entry

    def put_from_text(self, path: str, text: str) -> FileEntry:
        """Insert a synthetic FileEntry derived from in-memory `text`.
        Used by callers that want to feed open-buffer text into the same
        scan-and-walk machinery (e.g. the currently-edited document).
        The mtime is set to `+inf` so subsequent on-disk mtime checks
        won't clobber it — `invalidate()` drops it explicitly."""
        try:
            abs_path = os.path.abspath(path)
        except (OSError, ValueError):
            abs_path = path
        fn_defs, let_defs, imports = _scan_definitions(text)
        entry = FileEntry(
            path=abs_path,
            mtime=float("inf"),
            text=text,
            fn_defs=fn_defs,
            let_defs=let_defs,
            imports=imports,
        )
        self._entries[abs_path] = entry
        return entry


def resolve_import(base_dir: str, rel: str) -> Optional[str]:
    """Resolve `import "rel"` relative to `base_dir`. Returns the absolute
    path on disk if it exists, else None. Absolute paths pass through."""
    path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base_dir, rel))
    if os.path.isfile(path):
        return os.path.abspath(path)
    return None


def walk_imports(
    start_path: str,
    cache: FileCache,
    *,
    text_overrides: Optional[Dict[str, str]] = None,
    visited: Optional[Set[str]] = None,
) -> List[FileEntry]:
    """Walk the import graph starting at `start_path` (absolute) and
    return every reachable `FileEntry` in BFS order, including the start
    file itself.

    `text_overrides` maps absolute paths -> live buffer text. For any
    file in this map we use the in-memory text (via `put_from_text`)
    instead of the on-disk scan — so open documents stay authoritative
    even before the user has saved.

    `visited` is the cycle-prevention set. Pass in an externally-managed
    set if you want to combine multiple walks."""
    text_overrides = text_overrides or {}
    visited = visited if visited is not None else set()

    start_abs = os.path.abspath(start_path)
    out: List[FileEntry] = []
    queue: List[str] = [start_abs]

    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)

        # Live override takes precedence (open buffer in the editor).
        override = text_overrides.get(path)
        if override is not None:
            entry = cache.put_from_text(path, override)
        else:
            entry = cache.get(path)
        if entry is None:
            continue
        out.append(entry)

        base_dir = os.path.dirname(path)
        for rel in entry.imports:
            resolved = resolve_import(base_dir, rel)
            if resolved and resolved not in visited:
                queue.append(resolved)

    return out


def find_definition(
    name: str,
    start_path: str,
    cache: FileCache,
    *,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, Span]]:
    """Walk the import graph from `start_path` and return the first file
    that defines `name` as a top-level `fn` or `let`. Returns
    `(abs_path, (line, col_start, col_end))` or `None` if not found.

    Search order: the start file first (so intra-file definitions win),
    then BFS through imports — matches user intuition for "go to
    definition jumps inside the current file when both apply"."""
    for entry in walk_imports(start_path, cache, text_overrides=text_overrides):
        span = entry.fn_defs.get(name) or entry.let_defs.get(name)
        if span is not None:
            return entry.path, span
    return None
