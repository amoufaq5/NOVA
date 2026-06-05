"""Workspace symbol index for `workspace/symbol`.

Maintains an in-memory map `symbol_name -> [SymbolInformation, ...]` plus
a reverse index `file_path -> [symbol_name, ...]` so individual files can
be invalidated cheaply when they change on disk or in the editor buffer.

The indexer is incremental — files are indexed on `didOpen`/`didChange`
and on the first `workspace/symbol` query (lazy crawl of the workspace
root). This gives instant interactive queries after warm-up without
slowing down server startup.

Indexed declaration kinds (NOVA-specific):

  * `fn name(args)`                          -> SymbolKind.Function (12)
  * `let NAME = ...`     (all-caps)          -> SymbolKind.Constant (14)
  * `let name = ...`     (mixed/lower-case)  -> SymbolKind.Variable (13)
  * `out_label("_nova_X")` inside codegen    -> SymbolKind.Function (12)
    (runtime helper labels emitted by the
    compiler — recognising these lets users
    Cmd+T to e.g. `_nova_check_rdi` even
    though it isn't a top-level `fn`.)

Fuzzy matching ranks candidates by three signals (lower score = better):

  1. Exact / case-insensitive substring match — best score (0 / 1).
  2. CamelCase letter match — query letters appear in order at
     word-segment boundaries (`foB` -> `fooBar`, `foo_bar`).
  3. Sequential character match — query letters appear in order anywhere
     in the symbol name; score scales with skip distance.

A query of `""` returns the first `limit` symbols in name order.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# LSP SymbolKind enum values we surface.
SYMBOL_KIND_FUNCTION = 12
SYMBOL_KIND_VARIABLE = 13
SYMBOL_KIND_CONSTANT = 14


# Top-level declaration patterns. Stricter than imports._scan_definitions
# (which uses `^\s*` and so picks up `let` bindings inside function
# bodies). For workspace symbols we only want navigable top-level
# declarations — those at column zero — so a Cmd+T pick lands on a
# globally-meaningful name rather than an inner local.
_FN_DEF_RE = re.compile(r"^fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_LET_DEF_RE = re.compile(r"^let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
# Compiler runtime label declarations, e.g. `out_label("_nova_check_rdi")`.
# These surface as Function-kind symbols so editor symbol search reaches
# them. The label name lives in a string literal so the regex captures the
# quoted contents.
_OUT_LABEL_RE = re.compile(r'\bout_label\(\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)')
_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def path_to_uri(path: str) -> str:
    """Mirror of server.path_to_uri — duplicated to avoid the circular
    import (server imports this module, not the other way around)."""
    return "file://" + os.path.abspath(path)


@dataclass
class SymbolEntry:
    """One row in the workspace symbol index.

    `range` is zero-based (line, char_start, char_end) — the span of the
    symbol's name token, suitable for hand-back as a Location.range pair.
    """
    name: str
    kind: int                       # LSP SymbolKind
    path: str                       # absolute path
    line: int                       # zero-based
    char_start: int                 # zero-based
    char_end: int                   # zero-based
    container_name: Optional[str] = None

    def to_symbol_information(self) -> Dict[str, Any]:
        """LSP SymbolInformation payload (the deprecated-but-universal
        shape; clients fall back to this when `WorkspaceSymbol` isn't
        supported)."""
        info: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "location": {
                "uri": path_to_uri(self.path),
                "range": {
                    "start": {"line": self.line, "character": self.char_start},
                    "end": {"line": self.line, "character": self.char_end},
                },
            },
        }
        if self.container_name:
            info["containerName"] = self.container_name
        return info


def _classify_let(name: str) -> int:
    """All-caps `let` names (e.g. `TAU`, `MAX_SIZE`) are conventionally
    constants in NOVA; everything else is a runtime variable."""
    return SYMBOL_KIND_CONSTANT if _ALL_CAPS_RE.match(name) else SYMBOL_KIND_VARIABLE


def scan_symbols(path: str, text: str) -> List[SymbolEntry]:
    """Walk `text` and emit one SymbolEntry per top-level declaration.

    Order is source order. A name that appears more than once at the top
    level still emits multiple entries (the index keeps them all so
    workspace/symbol surfaces every definition site)."""
    abs_path = os.path.abspath(path)
    out: List[SymbolEntry] = []
    is_compiler_file = abs_path.endswith(("codegen.nova", "compiler.nova"))
    for line_no, line in enumerate(text.splitlines()):
        m_fn = _FN_DEF_RE.match(line)
        if m_fn:
            out.append(SymbolEntry(
                name=m_fn.group(1),
                kind=SYMBOL_KIND_FUNCTION,
                path=abs_path,
                line=line_no,
                char_start=m_fn.start(1),
                char_end=m_fn.end(1),
            ))
            continue
        m_let = _LET_DEF_RE.match(line)
        if m_let:
            name = m_let.group(1)
            out.append(SymbolEntry(
                name=name,
                kind=_classify_let(name),
                path=abs_path,
                line=line_no,
                char_start=m_let.start(1),
                char_end=m_let.end(1),
            ))
            continue
        # `out_label("...")` runtime helper symbols — only scanned in
        # codegen.nova / compiler.nova so we don't accidentally pick up
        # `out_label` argument strings from unrelated tooling.
        if is_compiler_file:
            for m_label in _OUT_LABEL_RE.finditer(line):
                name = m_label.group(1)
                out.append(SymbolEntry(
                    name=name,
                    kind=SYMBOL_KIND_FUNCTION,
                    path=abs_path,
                    line=line_no,
                    char_start=m_label.start(1),
                    char_end=m_label.end(1),
                    container_name="<runtime>",
                ))
    return out


# ---------------------------------------------------------------------------
# Fuzzy matcher.
# ---------------------------------------------------------------------------


def _word_segments(name: str) -> List[Tuple[int, str]]:
    """Split `name` into camelCase / snake_case word starts.

    Returns `[(index_in_name, first_letter), ...]` — used by the CamelCase
    matcher to test whether query letters land on segment boundaries."""
    segs: List[Tuple[int, str]] = []
    if not name:
        return segs
    segs.append((0, name[0]))
    for i in range(1, len(name)):
        ch = name[i]
        prev = name[i - 1]
        if ch == "_" or prev == "_":
            continue
        if ch.isupper() and prev.islower():
            segs.append((i, ch))
    # Also segment positions immediately AFTER underscores.
    for i in range(1, len(name)):
        if name[i - 1] == "_" and name[i] != "_":
            segs.append((i, name[i]))
    # De-dup and re-sort.
    seen: Set[int] = set()
    uniq: List[Tuple[int, str]] = []
    for idx, ch in sorted(segs):
        if idx not in seen:
            seen.add(idx)
            uniq.append((idx, ch))
    return uniq


def _camel_score(query: str, name: str) -> Optional[int]:
    """If every query letter (case-insensitive) lands on a word boundary
    in `name` in order, return the index of the last matched letter.
    Returns None if the camelCase pattern doesn't match."""
    if not query:
        return 0
    segs = _word_segments(name)
    qi = 0
    last = 0
    q_lower = query.lower()
    for idx, ch in segs:
        if qi >= len(q_lower):
            break
        if ch.lower() == q_lower[qi]:
            last = idx
            qi += 1
    if qi == len(q_lower):
        return last
    return None


def _sequential_score(query: str, name: str) -> Optional[int]:
    """If every query letter (case-insensitive) appears in order anywhere
    in `name`, return the total skip distance (lower = closer match).
    Returns None if the letters don't appear in order."""
    if not query:
        return 0
    q_lower = query.lower()
    n_lower = name.lower()
    qi = 0
    skips = 0
    last_pos = -1
    for ci, ch in enumerate(n_lower):
        if qi >= len(q_lower):
            break
        if ch == q_lower[qi]:
            if last_pos != -1:
                skips += (ci - last_pos - 1)
            last_pos = ci
            qi += 1
    if qi == len(q_lower):
        return skips
    return None


def fuzzy_score(query: str, name: str) -> Optional[Tuple[int, int, int]]:
    """Score `name` against `query`. Lower is better.

    Returns `(tier, secondary, tertiary)` or `None` if no match. The
    primary `tier` groups matches into clear buckets:

      0 — exact match (case-sensitive)
      1 — exact case-insensitive match
      2 — name starts with query (case-insensitive)
      3 — case-insensitive substring
      4 — camelCase letter match
      5 — sequential character match

    The secondary score breaks ties within a tier; the tertiary is the
    name length so shorter names sort first.
    """
    if not query:
        # No query → every symbol is a match with the same primary score.
        # Use name length as the tertiary so short names sort first when
        # callers want the "first N symbols" behaviour.
        return (0, 0, len(name))

    if name == query:
        return (0, 0, len(name))
    n_lower = name.lower()
    q_lower = query.lower()
    if n_lower == q_lower:
        return (1, 0, len(name))
    if n_lower.startswith(q_lower):
        return (2, 0, len(name))
    sub_idx = n_lower.find(q_lower)
    if sub_idx != -1:
        return (3, sub_idx, len(name))
    cam = _camel_score(query, name)
    if cam is not None:
        return (4, cam, len(name))
    seq = _sequential_score(query, name)
    if seq is not None:
        return (5, seq, len(name))
    return None


# ---------------------------------------------------------------------------
# WorkspaceSymbolIndex.
# ---------------------------------------------------------------------------


class WorkspaceSymbolIndex:
    """In-memory inverted index of every indexed file's symbols.

    The index is keyed by symbol name with a reverse map from absolute
    file path -> set of symbol names so individual files can be removed
    in O(symbols_in_file) without re-scanning the entire workspace.
    """

    def __init__(self) -> None:
        # name -> list of entries (multiple files may define the same name)
        self._by_name: Dict[str, List[SymbolEntry]] = {}
        # path (absolute) -> set of names declared in that file
        self._by_file: Dict[str, Set[str]] = {}
        # Track which workspace roots we've crawled so repeated calls to
        # `index_workspace_root` are no-ops.
        self._crawled_roots: Set[str] = set()

    # --- mutation -----------------------------------------------------------

    def index_text(self, path: str, text: str) -> int:
        """Reindex `path` from in-memory `text`. Returns the number of
        symbols newly registered for the file. Existing entries for the
        same path are dropped first so re-indexing is idempotent."""
        abs_path = os.path.abspath(path)
        self.invalidate_file(abs_path)
        entries = scan_symbols(abs_path, text)
        names: Set[str] = set()
        for e in entries:
            self._by_name.setdefault(e.name, []).append(e)
            names.add(e.name)
        if names:
            self._by_file[abs_path] = names
        return len(entries)

    def index_file(self, path: str) -> int:
        """Reindex `path` from disk. Returns the symbol count, or 0 if
        the file cannot be read."""
        abs_path = os.path.abspath(path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return 0
        return self.index_text(abs_path, text)

    def invalidate_file(self, path: str) -> None:
        """Drop every symbol contributed by `path` from the index."""
        abs_path = os.path.abspath(path)
        names = self._by_file.pop(abs_path, None)
        if not names:
            return
        for name in names:
            bucket = self._by_name.get(name)
            if not bucket:
                continue
            survivors = [e for e in bucket if e.path != abs_path]
            if survivors:
                self._by_name[name] = survivors
            else:
                self._by_name.pop(name, None)

    def index_workspace_root(self, root: str) -> int:
        """Crawl `root` for `*.nova` files and index each one from disk.

        Idempotent — a root is crawled only the first time it's seen, so
        repeated calls (e.g. one per `workspace/symbol` query when the
        client doesn't proactively warm the index) are cheap."""
        abs_root = os.path.abspath(root)
        if abs_root in self._crawled_roots:
            return 0
        if not os.path.isdir(abs_root):
            return 0
        self._crawled_roots.add(abs_root)
        total = 0
        for dirpath, dirnames, filenames in os.walk(abs_root):
            # Prune common build/dependency directories so we don't
            # index megabytes of vendored output.
            dirnames[:] = [
                d for d in dirnames
                if d not in ("node_modules", ".git", "__pycache__", "bin", "build")
            ]
            for fname in filenames:
                if not fname.endswith(".nova"):
                    continue
                total += self.index_file(os.path.join(dirpath, fname))
        return total

    # --- query --------------------------------------------------------------

    def all_symbols(self) -> Iterable[SymbolEntry]:
        for bucket in self._by_name.values():
            yield from bucket

    def __len__(self) -> int:
        return sum(len(b) for b in self._by_name.values())

    def fuzzy_match(self, query: str, limit: int = 100) -> List[SymbolEntry]:
        """Return the top `limit` symbols matching `query` (best first).

        An empty query returns the first `limit` symbols in name order —
        useful for clients that pre-populate the picker with "recent"
        symbols when the user opens it without typing."""
        scored: List[Tuple[Tuple[int, int, int], SymbolEntry]] = []
        if not query:
            # All symbols match equally — sort by name, take first N.
            collected: List[SymbolEntry] = []
            for name in sorted(self._by_name.keys()):
                collected.extend(self._by_name[name])
            return collected[:limit]
        for name, bucket in self._by_name.items():
            score = fuzzy_score(query, name)
            if score is None:
                continue
            for entry in bucket:
                scored.append((score, entry))
        # Stable sort by score, then by name for deterministic order
        # within a tier (e.g. all "tier 3" substring matches tie-break by
        # name so users get stable results across runs).
        scored.sort(key=lambda x: (x[0], x[1].name, x[1].path, x[1].line))
        return [entry for _score, entry in scored[:limit]]
