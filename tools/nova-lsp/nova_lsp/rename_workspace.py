"""Workspace-wide rename (`textDocument/rename` across imports).

The single-file `handle_rename` in `server.py` walks the open documents
plus their transitive import closure and replaces every occurrence of
the cursor's word with `newName`. That is fine for a quick local rename,
but it misses two important cases:

  1. A top-level `fn` / `let` / `const` / `type` defined in file `A.nova`
     and used by `B.nova` which imports it — but `B.nova` is *not* open
     in the editor. The transitive walk starts from open buffers, so a
     reference inside an un-opened sibling file is silently skipped.
  2. A symbol with the same identifier defined privately in an unrelated
     file. The blunt regex replace would rename it too, corrupting the
     sibling file.

This module implements a "proper" workspace rename:

  * Resolve the cursor's symbol to its canonical definition site via
    R5F's `find_definition`.
  * Decide whether the definition is **top-level** (rename should
    propagate across files that import it) or **local** (rename stays
    inside the current file's scope only).
  * For a top-level rename, walk every file in the workspace symbol
    index and check, via the R5F `FileCache`, whether the file
    transitively imports the definition's file. Only files that do are
    scanned for references.
  * Detect name conflicts in any affected file before emitting edits —
    if `newName` is already declared at the same scope in any reference
    file, we return an error instead of silently overwriting it.

The module is intentionally narrow: it returns a Python data structure
that `handle_rename_workspace` in `server.py` converts to an LSP
`WorkspaceEdit` payload. No I/O of its own beyond what R5F's
`FileCache` already does.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, walk_imports, resolve_import
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# A workspace rename targets one of these declaration kinds — anything
# else falls back to the single-file rename in server.py. (`const` and
# `type` are reserved for future NOVA syntax — they're recognised here
# so the dispatcher knows to route the rename through the workspace
# path the moment those keywords land in the language.)
_TOPLEVEL_FN_RE = re.compile(r"^fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_TOPLEVEL_LET_RE = re.compile(r"^let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_TOPLEVEL_CONST_RE = re.compile(r"^const\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_TOPLEVEL_TYPE_RE = re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\b")

_IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')

# Anything indented (even by one space) is "not at column zero" and is
# therefore treated as a local declaration — matches the R8C
# workspace-symbol indexer's convention.
_INDENT_RE = re.compile(r"^\s")


@dataclass
class WorkspaceRenameRequest:
    """A resolved rename request ready to be enacted.

    `references` maps each affected absolute path to the list of LSP
    `Range` objects (the old-name occurrences) within that file. The
    server converts this into a `WorkspaceEdit` payload.

    `conflict_message` is non-None when the rename cannot proceed
    (e.g. the new name already exists at file/global scope in one of
    the touched files). In that case `references` is empty.
    """
    references: Dict[str, List[Dict[str, Any]]]
    conflict_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Symbol classification — top-level fn/let/const/type vs. local binding.
# ---------------------------------------------------------------------------


def classify_symbol(
    name: str,
    def_path: str,
    def_line: int,
    file_cache: FileCache,
    text_overrides: Optional[Dict[str, str]] = None,
) -> str:
    """Return `"toplevel"` if the definition line is a top-level
    `fn` / `let` / `const` / `type` at column 0, `"local"` otherwise
    (function parameter, inner `let`, anything indented).

    The classifier is deliberately strict: only column-0 declarations
    qualify as workspace-renamable. Any indented `fn`/`let` (e.g. an
    inner closure or nested binding) stays a local rename so we don't
    accidentally rename references that share the same identifier
    elsewhere in the workspace.
    """
    text: Optional[str] = None
    if text_overrides:
        text = text_overrides.get(os.path.abspath(def_path))
    if text is None:
        entry = file_cache.get(def_path)
        if entry is None:
            return "local"
        text = entry.text
    lines = text.splitlines()
    if not (0 <= def_line < len(lines)):
        return "local"
    line = lines[def_line]
    if _INDENT_RE.match(line):
        return "local"
    for pat in (_TOPLEVEL_FN_RE, _TOPLEVEL_LET_RE,
                _TOPLEVEL_CONST_RE, _TOPLEVEL_TYPE_RE):
        m = pat.match(line)
        if m and m.group(1) == name:
            return "toplevel"
    return "local"


# ---------------------------------------------------------------------------
# Import-graph reachability — does file B import file A (transitively)?
# ---------------------------------------------------------------------------


def file_imports_target(
    candidate_path: str,
    target_path: str,
    file_cache: FileCache,
    text_overrides: Optional[Dict[str, str]] = None,
) -> bool:
    """True if `candidate_path` transitively imports `target_path`.

    Walks the import graph rooted at `candidate_path` and checks if
    `target_path` appears as a reachable node. The walk uses R5F's
    `FileCache` so repeated calls are cheap. The candidate itself
    counts as "importing" the target when they are the same file —
    that's the definition site itself, which obviously contains the
    declaration we're renaming.
    """
    target_abs = os.path.abspath(target_path)
    candidate_abs = os.path.abspath(candidate_path)
    if candidate_abs == target_abs:
        return True
    visited: Set[str] = set()
    entries = walk_imports(
        candidate_abs,
        file_cache,
        text_overrides=text_overrides,
        visited=visited,
    )
    return any(e.path == target_abs for e in entries)


# ---------------------------------------------------------------------------
# Per-file reference scan — find every `\bname\b` occurrence.
# ---------------------------------------------------------------------------


def _ident_regex(name: str) -> "re.Pattern[str]":
    return re.compile(r"\b" + re.escape(name) + r"\b")


def _scan_file_for_identifier(text: str, name: str) -> List[Dict[str, Any]]:
    """Return LSP `Range[]` for every `\\bname\\b` match in `text`.

    Word-boundary matching means `foo` does NOT match inside `foobar`
    or `myfoo` — case-sensitive, identifier-character boundary on both
    sides. Skips matches inside comments (`// ...`, `# ...`) and string
    literals so renaming a fn doesn't touch documentation that happens
    to mention its name.
    """
    pattern = _ident_regex(name)
    ranges: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines()):
        cleaned = _mask_comments_and_strings(line)
        for m in pattern.finditer(cleaned):
            ranges.append(
                {
                    "start": {"line": lineno, "character": m.start()},
                    "end": {"line": lineno, "character": m.end()},
                }
            )
    return ranges


def _mask_comments_and_strings(line: str) -> str:
    """Replace string-literal and comment content with spaces so a
    regex match against the result reports the original column offsets
    of identifier tokens but never lands inside a quote or comment.

    Conservative: handles `// ...`, `# ...`, and double-quoted strings
    with backslash escapes. Multi-line strings and `/* */` comments
    aren't a concern for NOVA's current grammar.
    """
    out: List[str] = []
    i = 0
    n = len(line)
    in_string = False
    while i < n:
        ch = line[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(" ")
                out.append(" ")
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append('"')
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append('"')
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            out.extend(" " * (n - i))
            break
        if ch == "#":
            out.extend(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Name-conflict detection — would the rename overwrite an existing decl?
# ---------------------------------------------------------------------------


def detect_name_conflict(
    new_name: str,
    affected_files: List[str],
    file_cache: FileCache,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """If any file in `affected_files` already declares `new_name` at
    the top level, return a human-readable conflict message; otherwise
    return None.

    The check is intentionally narrow: we only flag a conflict when the
    NEW name is already a top-level declaration in one of the files
    we'd otherwise rewrite. Local shadowing (an inner `let` with the
    same name) is left alone because the existing scope rules already
    keep the local visible.
    """
    overrides = text_overrides or {}
    for path in affected_files:
        abs_path = os.path.abspath(path)
        text = overrides.get(abs_path)
        if text is None:
            entry = file_cache.get(abs_path)
            if entry is None:
                continue
            text = entry.text
        for line in text.splitlines():
            for pat, kind in (
                (_TOPLEVEL_FN_RE, "fn"),
                (_TOPLEVEL_LET_RE, "let"),
                (_TOPLEVEL_CONST_RE, "const"),
                (_TOPLEVEL_TYPE_RE, "type"),
            ):
                m = pat.match(line)
                if m and m.group(1) == new_name:
                    return (
                        f"`{new_name}` is already defined as a top-level "
                        f"`{kind}` in {abs_path}. Rename aborted to avoid "
                        f"overwriting it."
                    )
    return None


# ---------------------------------------------------------------------------
# Workspace reference walk.
# ---------------------------------------------------------------------------


def find_references_in_workspace(
    symbol_name: str,
    def_path: str,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Find every workspace reference to `symbol_name` rooted at the
    definition in `def_path`.

    A file qualifies for the scan if:
      * it is the definition file itself, OR
      * it transitively imports the definition file.

    Files that are merely indexed by `workspace_index` but never import
    the definition file are skipped — that's the whole point of the
    "respect import scope" requirement. We rely on R5F's transitive
    import walk to determine reachability, with a small short-circuit
    for the definition file itself.

    `extra_paths` may include open-buffer paths that the workspace
    indexer hasn't crawled yet (e.g. the document the user is editing).
    They are unioned with the indexed file list before scanning.

    Returns `{abs_path: [Range, ...]}` keyed by absolute path. Files
    with zero matches are omitted.
    """
    def_abs = os.path.abspath(def_path)
    overrides = text_overrides or {}

    candidates: Set[str] = set()
    for path in workspace_index._by_file.keys():  # noqa: SLF001 (intentional)
        candidates.add(os.path.abspath(path))
    if extra_paths:
        for p in extra_paths:
            candidates.add(os.path.abspath(p))
    candidates.add(def_abs)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(candidates):
        if not file_imports_target(path, def_abs, file_cache, overrides):
            continue
        text = overrides.get(path)
        if text is None:
            entry = file_cache.get(path)
            if entry is None:
                continue
            text = entry.text
        ranges = _scan_file_for_identifier(text, symbol_name)
        if ranges:
            out[path] = ranges
    return out


# ---------------------------------------------------------------------------
# WorkspaceEdit shape.
# ---------------------------------------------------------------------------


def build_workspace_edit(
    references: Dict[str, List[Dict[str, Any]]],
    new_name: str,
) -> Dict[str, Any]:
    """Turn a `{path: [range, ...]}` map into an LSP `WorkspaceEdit`
    `{"changes": {uri: [TextEdit, ...]}}` payload.

    Paths are converted to `file://` URIs so the editor can apply them
    without further translation.
    """
    changes: Dict[str, List[Dict[str, Any]]] = {}
    for path, ranges in references.items():
        uri = "file://" + os.path.abspath(path)
        changes[uri] = [
            {"range": r, "newText": new_name} for r in ranges
        ]
    return {"changes": changes}


# ---------------------------------------------------------------------------
# Top-level orchestration — used by server.handle_rename_workspace.
# ---------------------------------------------------------------------------


def plan_workspace_rename(
    symbol_name: str,
    new_name: str,
    def_path: str,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> WorkspaceRenameRequest:
    """Compute the WorkspaceRenameRequest for a top-level symbol.

    Steps:
      1. Find every workspace file that imports the definition site and
         scan it for `\\bsymbol_name\\b` occurrences (skipping strings
         and comments).
      2. Check whether `new_name` would clash with a top-level decl in
         any of those files. If so, surface a conflict message and
         skip the rename.
      3. Otherwise return the `{path: [range, ...]}` map.
    """
    references = find_references_in_workspace(
        symbol_name,
        def_path,
        file_cache,
        workspace_index,
        extra_paths=extra_paths,
        text_overrides=text_overrides,
    )
    if not references:
        return WorkspaceRenameRequest(references={})
    conflict = detect_name_conflict(
        new_name,
        list(references.keys()),
        file_cache,
        text_overrides=text_overrides,
    )
    if conflict:
        return WorkspaceRenameRequest(references={}, conflict_message=conflict)
    return WorkspaceRenameRequest(references=references)
