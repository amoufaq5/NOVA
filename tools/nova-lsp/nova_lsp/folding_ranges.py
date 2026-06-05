"""Folding ranges for ``textDocument/foldingRange``.

The folding-range capability lets editors render a collapse/expand
gutter next to every collapsible block — function bodies, ``match``
expressions, ``if`` / ``else`` branches, ``enum`` / ``struct``
declarations, and contiguous ``///`` documentation comment blocks. A
reader can fold an entire function body to a single line, drill into a
specific arm of a long ``match``, or fold every doc-comment block at
once so the file's *signatures* stay visible while the prose collapses
out of view.

LSP wire shape (per the spec's `Folding Range Request
<https://microsoft.github.io/language-server-protocol/specification/#textDocument_foldingRange>`_):

    interface FoldingRange {
        startLine: int;            // zero-based, first folded line
        endLine: int;              // zero-based, last folded line
        startCharacter?: int;      // optional column offset
        endCharacter?: int;        // optional column offset
        kind?: "comment" | "imports" | "region";
    }

A range with ``startLine == endLine`` would fold nothing, so we skip
single-line constructs (single-line ``fn foo() {}``, a one-line ``///``
comment) entirely. The editor draws the fold marker between the
*start* and *end* lines so the line containing the opening token
remains visible after the fold; ``endLine`` should point at the line
with the closing brace (or the last line of the comment block) so the
entire body collapses.

Recognised foldable constructs (NOVA-specific):

  * ``fn name(args) { ... }``         -> block fold spanning the body
  * ``match expr { ... }``            -> block fold for the arms
  * ``if cond { ... }``               -> block fold for the then-branch
  * ``else { ... }``                  -> block fold for the else-branch
  * ``enum Name { ... }``             -> block fold for the variants
  * ``struct Name { ... }``           -> block fold for the fields
  * contiguous ``///`` doc-comment    -> kind=comment fold for the block
    block (at least 2 lines)
  * contiguous ``import "..."``       -> kind=imports fold for the block
    block (at least 2 lines)

Inner blocks are emitted as separate folding ranges so editors can fold
them independently of the surrounding outer fold (e.g. inside a long
``fn``, the user can fold a specific ``match`` arm group while keeping
the outer fn body open). Order of the result list is source order so
clients render the gutter markers consistently.

Single-line block expressions like ``if cond { return 1 }`` (open and
close brace on the same physical line) are not foldable — there's
nothing to collapse. Same goes for single-line ``///`` lines (no
contiguous block of multiple doc comments).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from nova_lsp.imports import FileCache


# ---------------------------------------------------------------------------
# Folding-range kind strings.
#
# Per the LSP spec, ``kind`` is an open enum — the standard values are
# ``"comment"``, ``"imports"``, and ``"region"``. We use ``"comment"``
# for ``///`` doc blocks and ``"imports"`` for contiguous ``import``
# lines; block folds (fn / match / if / enum / struct) omit ``kind`` so
# the editor uses its default block-fold rendering.
# ---------------------------------------------------------------------------


KIND_COMMENT = "comment"
KIND_IMPORTS = "imports"
KIND_REGION = "region"


# ---------------------------------------------------------------------------
# Patterns for declaration headers we recognise as fold starts.
#
# Each pattern matches the *start* of a block opening line — the brace
# itself may live on the same physical line or on a later one (we walk
# forward until the first ``{`` to find the actual open). The capture
# group is only used to validate the construct kind; the brace-walker
# computes the matching close line.
# ---------------------------------------------------------------------------


_FN_HEAD_RE = re.compile(r"^\s*fn\s+[A-Za-z_][A-Za-z0-9_]*\s*\(")
_MATCH_HEAD_RE = re.compile(r"\bmatch\b[^{}/]*\{")
_IF_HEAD_RE = re.compile(r"\bif\b[^{}]*\{")
_ELSE_HEAD_RE = re.compile(r"\belse\b\s*(?:\{|if\b)")
_ENUM_HEAD_RE = re.compile(r"^\s*enum\s+[A-Za-z_][A-Za-z0-9_]*\b")
_STRUCT_HEAD_RE = re.compile(r"^\s*struct\s+[A-Za-z_][A-Za-z0-9_]*\b")

_DOC_COMMENT_RE = re.compile(r"^\s*///")
_IMPORT_RE = re.compile(r'^\s*import\s+"[^"]*"')


# ---------------------------------------------------------------------------
# Comment / string masking — used by the brace walker so a ``{`` inside
# a string literal or a ``//`` comment doesn't perturb the depth count.
# Same shape as the helpers in code_lens / type_hierarchy / inlay_hints.
# ---------------------------------------------------------------------------


def _mask_comments_and_strings(line: str) -> str:
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
# FoldingRange dataclass — internal representation. ``to_lsp`` converts
# to the wire shape, omitting the optional ``kind`` field when not set
# so block folds render with the editor's default styling.
# ---------------------------------------------------------------------------


@dataclass
class FoldingRange:
    """One folding range emitted to the client.

    ``start_line`` / ``end_line`` are zero-based. ``end_line`` is the
    line containing the closing token (closing brace, last doc-comment
    line, last import line). Single-line constructs are filtered out
    upstream — every emitted FoldingRange spans at least 2 lines.
    """
    start_line: int
    end_line: int
    kind: Optional[str] = None

    def to_lsp(self) -> Dict[str, Any]:
        """LSP wire shape for ``FoldingRange``."""
        payload: Dict[str, Any] = {
            "startLine": self.start_line,
            "endLine": self.end_line,
        }
        if self.kind is not None:
            payload["kind"] = self.kind
        return payload


# ---------------------------------------------------------------------------
# Brace-walker: given an ``open_line`` known to contain ``{``, return
# the line index of the matching ``}``. Brace counting uses the masked
# line text so braces inside strings / comments don't perturb the depth.
# Returns ``None`` when no matching close is found (malformed source).
# ---------------------------------------------------------------------------


def _find_matching_close(
    lines: List[str],
    open_line: int,
    open_col: int,
) -> Optional[int]:
    """Return the line index containing the matching ``}`` for the
    ``{`` at ``lines[open_line][open_col]``. ``None`` on mismatch.
    """
    if open_line >= len(lines):
        return None
    n = len(lines)
    depth = 0
    found_open = False
    for j in range(open_line, n):
        cleaned = _mask_comments_and_strings(lines[j])
        start = open_col if j == open_line else 0
        for k in range(start, len(cleaned)):
            ch = cleaned[k]
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return j
    return None


def _find_first_brace(lines: List[str], from_line: int) -> Optional[Tuple[int, int]]:
    """Locate the first ``{`` at or after ``from_line`` (skipping
    strings + comments). Returns ``(line, col)`` or ``None``."""
    n = len(lines)
    for j in range(from_line, n):
        cleaned = _mask_comments_and_strings(lines[j])
        pos = cleaned.find("{")
        if pos != -1:
            return j, pos
    return None


# ---------------------------------------------------------------------------
# Block-fold scanner. Walks every line and emits a FoldingRange for
# every ``fn`` / ``match`` / ``if`` / ``else`` / ``enum`` / ``struct``
# block opener. Inner blocks are also scanned (no early-stop) so
# folding ranges nest correctly — a ``match`` inside a ``fn`` body
# emits two ranges: one for the outer fn, one for the inner match.
# ---------------------------------------------------------------------------


def _scan_block_folds(lines: List[str]) -> List[FoldingRange]:
    """Walk every line; emit one FoldingRange per recognised block."""
    out: List[FoldingRange] = []
    n = len(lines)
    for i in range(n):
        raw = lines[i]
        cleaned = _mask_comments_and_strings(raw)
        # `fn name(args)`: head at column zero (or with leading whitespace
        # for nested fns if they ever appear — NOVA's grammar only allows
        # top-level fns today but the scanner is permissive).
        if _FN_HEAD_RE.match(cleaned):
            brace = _find_first_brace(lines, i)
            if brace is None:
                continue
            open_line, open_col = brace
            close_line = _find_matching_close(lines, open_line, open_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
            continue
        # `enum Name { ... }`: header re; the body may open on the same
        # or a later line.
        if _ENUM_HEAD_RE.match(cleaned):
            brace = _find_first_brace(lines, i)
            if brace is None:
                continue
            open_line, open_col = brace
            close_line = _find_matching_close(lines, open_line, open_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
            continue
        # `struct Name { ... }`: same shape as enum.
        if _STRUCT_HEAD_RE.match(cleaned):
            brace = _find_first_brace(lines, i)
            if brace is None:
                continue
            open_line, open_col = brace
            close_line = _find_matching_close(lines, open_line, open_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
            continue
        # `match` blocks can appear anywhere inside a fn body. We scan
        # the masked line for the keyword followed by `{` on the SAME or
        # a later line; brace-walk to the close.
        for m in _MATCH_HEAD_RE.finditer(cleaned):
            # The match block opens with `{` somewhere on this line (or
            # a future line if the brace falls off the end of the
            # pattern). Find the actual brace position.
            brace_col = cleaned.find("{", m.start())
            if brace_col == -1:
                brace = _find_first_brace(lines, i + 1)
                if brace is None:
                    continue
                open_line, open_col = brace
            else:
                open_line, open_col = i, brace_col
            close_line = _find_matching_close(lines, open_line, open_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
        # `if cond { ... }`. We allow the `if` to appear anywhere in
        # the masked line (so `else if` cases are handled too).
        for m in _IF_HEAD_RE.finditer(cleaned):
            brace_col = cleaned.find("{", m.start())
            if brace_col == -1:
                continue
            close_line = _find_matching_close(lines, i, brace_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
        # `else { ... }` block. The closing brace lives somewhere after
        # the `else` keyword. We exclude `else if` here because the
        # nested `if` branch is already emitted by the `_IF_HEAD_RE`
        # loop above; in `else if`, the trailing `{` is the *if*
        # block's opener, not the else's.
        for m in _ELSE_HEAD_RE.finditer(cleaned):
            tail = cleaned[m.end():]
            if m.group(0).strip().endswith("if"):
                # `else if` — defer to the `if` walker.
                continue
            # Brace is on this line (could be `else {`) or future line.
            brace_col = cleaned.find("{", m.start())
            if brace_col == -1:
                brace = _find_first_brace(lines, i + 1)
                if brace is None:
                    continue
                open_line, open_col = brace
            else:
                open_line, open_col = i, brace_col
            close_line = _find_matching_close(lines, open_line, open_col)
            if close_line is None or close_line <= i:
                continue
            out.append(FoldingRange(start_line=i, end_line=close_line))
    return out


# ---------------------------------------------------------------------------
# Comment-fold scanner. Contiguous runs of ``///`` doc comments are
# folded as one ``kind=comment`` range. A single ``///`` line is not
# foldable. ``//`` plain comments do NOT fold today — they're typically
# used for inline notes, where collapsing would be more confusing than
# helpful (and the LSP spec leaves the policy to the server).
# ---------------------------------------------------------------------------


def _scan_comment_folds(lines: List[str]) -> List[FoldingRange]:
    """Emit one FoldingRange per contiguous block of ``///`` lines."""
    out: List[FoldingRange] = []
    n = len(lines)
    i = 0
    while i < n:
        if not _DOC_COMMENT_RE.match(lines[i]):
            i += 1
            continue
        start = i
        j = i + 1
        while j < n and _DOC_COMMENT_RE.match(lines[j]):
            j += 1
        end = j - 1
        if end > start:
            out.append(FoldingRange(
                start_line=start,
                end_line=end,
                kind=KIND_COMMENT,
            ))
        i = j
    return out


# ---------------------------------------------------------------------------
# Imports-fold scanner. Contiguous runs of ``import "..."`` lines fold
# as one ``kind=imports`` range. Single-import files don't fold.
# ---------------------------------------------------------------------------


def _scan_import_folds(lines: List[str]) -> List[FoldingRange]:
    """Emit one FoldingRange for each run of contiguous import lines."""
    out: List[FoldingRange] = []
    n = len(lines)
    i = 0
    while i < n:
        if not _IMPORT_RE.match(lines[i]):
            i += 1
            continue
        start = i
        j = i + 1
        while j < n and _IMPORT_RE.match(lines[j]):
            j += 1
        end = j - 1
        if end > start:
            out.append(FoldingRange(
                start_line=start,
                end_line=end,
                kind=KIND_IMPORTS,
            ))
        i = j
    return out


# ---------------------------------------------------------------------------
# Top-level API.
# ---------------------------------------------------------------------------


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of ``server.uri_to_path -> abspath``."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))


def compute_folding_ranges(
    uri: str,
    doc_text: str,
    file_cache: Optional[FileCache] = None,
) -> List[Dict[str, Any]]:
    """Compute FoldingRange[] for the document at ``uri``.

    Parameters:
      ``uri``        -- document URI (used for symmetry with the other
                        LSP modules; folding is purely syntactic so the
                        URI itself isn't needed beyond optional
                        validation).
      ``doc_text``   -- current buffer text (authoritative).
      ``file_cache`` -- accepted for signature symmetry with the rest
                        of the LSP module suite but unused; folding is
                        single-file and doesn't traverse imports.

    Returns a list of FoldingRange wire payloads in source order.
    Empty list when ``doc_text`` is empty or contains no foldable
    constructs (a one-line ``fn foo() {}`` returns no ranges).
    """
    if not doc_text:
        return []
    lines = doc_text.splitlines()
    if not lines:
        return []
    ranges: List[FoldingRange] = []
    ranges.extend(_scan_block_folds(lines))
    ranges.extend(_scan_comment_folds(lines))
    ranges.extend(_scan_import_folds(lines))
    # Source order — sort by start, then end (outer folds first when
    # they share a start line). The LSP spec allows any order but
    # editors render the gutter most cleanly when ranges arrive
    # outside-in.
    ranges.sort(key=lambda r: (r.start_line, -r.end_line))
    return [r.to_lsp() for r in ranges]
