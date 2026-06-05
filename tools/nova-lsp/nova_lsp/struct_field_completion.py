"""Struct brace-init field completion for `textDocument/completion`.

R25A (commit ``7b74e7e``) shipped struct brace-init syntax::

    let p: Point = Point { x: 10, y: 20 }
    let bg = Box<int> { value: 99 }

R26D extends type-aware completion (R24E) to cover the brace-init
context specifically:

    Point { |              -> suggests [x, y]
    Point { x: 10, |       -> suggests [y]  (x already specified)
    Point { x: 10, y: 20, | -> suggests []  (all specified)

Conceptually similar to R24E's ``box.`` -> struct field completion,
but this one fires INSIDE a brace-init body -- the user is naming
fields to set, not accessing them through a value. Two new pieces vs.
R24E:

  1. Trigger detection needs to scan BACKWARDS from the cursor
     through multi-line text to find the opening ``Name {`` --
     brace-inits often span lines. The R24E triggers all live on the
     same source line so a slice of ``line_text[:character]`` was
     enough; here we have to walk the doc body upwards while masking
     comments and strings.

  2. The candidate list excludes fields already typed in the partial
     init. ``Point { x: 10, |`` shouldn't suggest ``x`` again --
     only the remaining un-specified field names. R24E's struct
     field path had no such notion; every declared field was always
     offered.

Public API (called from
``type_completion.compute_type_aware_completions``)::

    is_brace_init_context(line_no, character, doc_text)
        -> _BraceInitContext | None

    get_already_specified_fields(line_no, character, doc_text,
                                  open_lb_line, open_lb_col)
        -> list[str]

    compute_field_completions(struct_name, already_specified, decls,
                              doc_text="")
        -> list[CompletionItem]

    compute_struct_field_completions(uri, position, doc_text, decls)
        -> list[CompletionItem] | None

Returning ``None`` from ``compute_struct_field_completions`` signals
that this trigger doesn't apply; the caller then falls through to
R24E's existing line-local triggers.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from nova_lsp.type_hierarchy import (
    KIND_STRUCT,
    TypeDecl,
    scan_type_declarations,
)


# ---------------------------------------------------------------------------
# LSP CompletionItemKind enum values we emit (mirror of the values in
# type_completion.py to keep this module standalone).
# ---------------------------------------------------------------------------

COMPLETION_KIND_FIELD = 5
COMPLETION_KIND_KEYWORD = 14


# ---------------------------------------------------------------------------
# Tokenisation helpers -- local copies of the line masker used elsewhere
# so this module stays standalone.
# ---------------------------------------------------------------------------


def _mask_comments_and_strings(line: str) -> str:
    """Replace string and comment text with spaces, preserving column
    offsets. Mirrors the masker used by every other LSP module."""
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


def _mask_full_text(text: str) -> List[str]:
    """Return the document split into masked lines."""
    return [_mask_comments_and_strings(ln) for ln in text.splitlines()]


# ---------------------------------------------------------------------------
# Open-brace finder.
# ---------------------------------------------------------------------------


# Match ``Name<...>`` or just ``Name`` ending at the right edge.
# Allowed inside ``<...>``: anything that isn't ``<``, ``>``, ``{``,
# ``}``, or ``;`` (one-level deep, mirroring the parser's stance --
# NOVA generics don't nest meaningfully in this grammar position).
_STRUCT_NAME_BEFORE_BRACE_RE = re.compile(
    r"([A-Z][A-Za-z0-9_]*)\s*(?:<[^<>{};]*>)?\s*$"
)


@dataclass
class _BraceInitContext:
    """Result of locating the opening brace of a brace-init."""
    struct_name: str
    open_lb_line: int
    open_lb_col: int


def _find_enclosing_brace_init(
    masked_lines: List[str],
    line_no: int,
    character: int,
) -> Optional[_BraceInitContext]:
    """Scan backwards from (line_no, character) for a ``{`` at relative
    depth 0 with an uppercase IDENT directly before it. Returns
    ``None`` if we hit a statement boundary first or never find one."""
    if line_no < 0 or line_no >= len(masked_lines):
        return None
    depth = 0
    paren_depth = 0
    cur_line = line_no
    cur_text = masked_lines[cur_line]
    end_col = min(character, len(cur_text))
    while cur_line >= 0:
        for col in range(end_col - 1, -1, -1):
            ch = cur_text[col]
            if ch == "}":
                depth += 1
                continue
            if ch == "{":
                if depth > 0:
                    depth -= 1
                    continue
                before = cur_text[:col]
                m = _STRUCT_NAME_BEFORE_BRACE_RE.search(before)
                if m is None:
                    return None
                return _BraceInitContext(
                    struct_name=m.group(1),
                    open_lb_line=cur_line,
                    open_lb_col=col,
                )
            if ch == ")":
                paren_depth += 1
                continue
            if ch == "(":
                if paren_depth > 0:
                    paren_depth -= 1
                    continue
                return None
            if ch == "]":
                paren_depth += 1
                continue
            if ch == "[":
                if paren_depth > 0:
                    paren_depth -= 1
                    continue
                return None
            if ch == ";" and paren_depth == 0 and depth == 0:
                return None
        cur_line -= 1
        if cur_line < 0:
            return None
        cur_text = masked_lines[cur_line]
        end_col = len(cur_text)
    return None


# ---------------------------------------------------------------------------
# Already-specified field harvest.
# ---------------------------------------------------------------------------


_INIT_FIELD_RE = re.compile(r"\b([a-z_][A-Za-z0-9_]*)\s*:")


def _collect_specified_fields(
    masked_lines: List[str],
    open_lb_line: int,
    open_lb_col: int,
    cursor_line: int,
    cursor_col: int,
) -> List[str]:
    """Scan the init body forward for ``name:`` tokens at relative
    depth 0, stopping at the cursor. Order is source order."""
    if open_lb_line > cursor_line:
        return []
    out: List[str] = []
    depth = 0
    paren_depth = 0
    for line_no in range(open_lb_line, cursor_line + 1):
        if line_no >= len(masked_lines):
            break
        text = masked_lines[line_no]
        start = 0
        end = len(text)
        if line_no == open_lb_line:
            start = open_lb_col + 1
        if line_no == cursor_line:
            end = min(cursor_col, len(text))
        if start >= end:
            continue
        segment = text[start:end]
        i = 0
        n = len(segment)
        while i < n:
            ch = segment[i]
            if ch == "{":
                depth += 1
                i += 1
                continue
            if ch == "}":
                if depth > 0:
                    depth -= 1
                i += 1
                continue
            if ch == "(" or ch == "[":
                paren_depth += 1
                i += 1
                continue
            if ch == ")" or ch == "]":
                if paren_depth > 0:
                    paren_depth -= 1
                i += 1
                continue
            if depth == 0 and paren_depth == 0:
                if ch.isalpha() or ch == "_":
                    m = _INIT_FIELD_RE.match(segment, i)
                    if m is not None:
                        out.append(m.group(1))
                        i = m.end()
                        continue
            i += 1
    return out


# ---------------------------------------------------------------------------
# Struct field discovery -- local copy of the scanner used elsewhere.
# ---------------------------------------------------------------------------


@dataclass
class _StructField:
    name: str
    type_str: str


_FIELD_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^,;/\n]+?))?\s*(?:[,;]|$)"
)


def scan_struct_fields(text: str, struct_decl: TypeDecl) -> List[_StructField]:
    """Walk ``struct_decl``'s body and return one ``_StructField`` per
    declared field, in source order.

    Handles both multi-line bodies (``struct Point {\\n x: int,\\n
    y: int\\n }``) and single-line bodies (``struct Point { x:
    int, y: int }``) -- the latter is important because the brace-
    init field completion has to work against compact struct decls
    too. The body extent is bounded by ``struct_decl.line`` and
    ``struct_decl.body_end_line``; within that range we track brace
    depth so nested ``{}`` (e.g. a default value with a struct
    literal) doesn't fool the parser into stopping early."""
    out: List[_StructField] = []
    lines = text.splitlines()
    in_body = False
    depth = 0
    for line_no in range(struct_decl.line, struct_decl.body_end_line + 1):
        if line_no >= len(lines):
            break
        cleaned = _mask_comments_and_strings(lines[line_no])
        scan_from = 0
        if not in_body and line_no == struct_decl.line:
            brace_pos = cleaned.find("{")
            if brace_pos == -1:
                continue
            in_body = True
            depth = 1
            scan_from = brace_pos + 1
        segment = (
            cleaned[scan_from:] if line_no == struct_decl.line else cleaned
        )
        # Find the column where this segment's brace depth would hit
        # zero (closing the body). Anything past that point lives
        # OUTSIDE the body and isn't a field.
        local_depth = depth
        body_end_col = len(segment)
        for idx, ch in enumerate(segment):
            if ch == "{":
                local_depth += 1
            elif ch == "}":
                local_depth -= 1
                if local_depth <= 0:
                    body_end_col = idx
                    break
        body_segment = segment[:body_end_col]
        depth = local_depth
        if body_segment.strip():
            for raw_part in re.split(r"[,;]", body_segment):
                if not raw_part.strip():
                    continue
                m = _FIELD_RE.match(raw_part)
                if not m:
                    continue
                out.append(_StructField(
                    name=m.group(1),
                    type_str=(m.group(2) or "").strip(),
                ))
        if depth <= 0:
            return out
    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def is_brace_init_context(
    line_no: int,
    character: int,
    doc_text: str,
) -> Optional[_BraceInitContext]:
    """Return the brace-init context (struct name + opening ``{``
    location) when the cursor at ``(line_no, character)`` sits inside
    an open ``Name { ... }`` body. Returns ``None`` otherwise so the
    caller falls back to R24E's other triggers."""
    masked = _mask_full_text(doc_text)
    return _find_enclosing_brace_init(masked, line_no, character)


def get_already_specified_fields(
    line_no: int,
    character: int,
    doc_text: str,
    open_lb_line: int,
    open_lb_col: int,
) -> List[str]:
    """Return field names the user has ALREADY typed in the brace-init
    body opening at ``(open_lb_line, open_lb_col)`` -- scanning forward
    only as far as the cursor."""
    masked = _mask_full_text(doc_text)
    return _collect_specified_fields(
        masked, open_lb_line, open_lb_col, line_no, character
    )


def _make_field_item(field: _StructField, struct_name: str) -> Dict[str, Any]:
    """CompletionItem for one remaining struct field. The insert text
    includes a trailing ``: `` so the user just types the value next."""
    detail = (
        f"{field.name}: {field.type_str}" if field.type_str else field.name
    )
    return {
        "label": field.name,
        "kind": COMPLETION_KIND_FIELD,
        "detail": detail,
        "insertText": f"{field.name}: ",
        "documentation": {
            "kind": "markdown",
            "value": f"Field of `struct {struct_name}`",
        },
    }


def _make_spread_item(struct_name: str) -> Dict[str, Any]:
    """CompletionItem for the ``..base`` spread placeholder.

    Surfaced as a keyword-kind suggestion when there's enough
    remaining fields that update syntax (a planned R26A follow-up)
    would be useful. The parser doesn't accept ``..`` in brace-init
    bodies today, but the completion is harmless -- the user can
    delete the dots if they meant something else."""
    return {
        "label": "..",
        "kind": COMPLETION_KIND_KEYWORD,
        "detail": f"..base (struct update syntax for {struct_name})",
        "insertText": "..",
        "documentation": {
            "kind": "markdown",
            "value": (
                f"Struct update syntax -- copy remaining fields from a "
                f"base value of `{struct_name}`."
            ),
        },
    }


# Threshold for offering ``..base`` -- only useful when typing all
# remaining fields would be tedious. Three matches the rule of three
# we use in other LSP modules to gate noisy suggestions.
_SPREAD_THRESHOLD = 3


def compute_field_completions(
    struct_name: str,
    already_specified: List[str],
    decls: List[Tuple[str, TypeDecl]],
    *,
    doc_text: str = "",
) -> List[Dict[str, Any]]:
    """Return CompletionItems for every field of ``struct_name`` NOT
    in ``already_specified``. ``decls`` is the same ``(path,
    TypeDecl)`` list R24E's pipeline gathers (in-buffer + imports +
    workspace).

    Returns an empty list when no struct by that name exists in the
    visible set -- matches R24E's convention so the caller doesn't
    fall through to the generic builtin list."""
    target_path: Optional[str] = None
    target_decl: Optional[TypeDecl] = None
    for path, d in decls:
        if d.kind == KIND_STRUCT and d.name == struct_name:
            target_path = path
            target_decl = d
            break
    if target_decl is None or target_path is None:
        return []

    fields = _resolve_fields(target_path, target_decl, doc_text, struct_name)
    already = set(already_specified)
    remaining = [f for f in fields if f.name not in already]
    items = [_make_field_item(f, struct_name) for f in remaining]
    if len(remaining) >= _SPREAD_THRESHOLD:
        items.append(_make_spread_item(struct_name))
    return items


def _resolve_fields(
    target_path: str,
    target_decl: TypeDecl,
    doc_text: str,
    struct_name: str,
) -> List[_StructField]:
    """Walk the source text and return the struct's declared fields.

    Prefers the in-buffer ``doc_text`` when the struct decl actually
    lives in the open buffer (so unsaved edits win), otherwise reads
    from disk via ``target_path``."""
    if doc_text:
        in_buffer_decls = scan_type_declarations(doc_text)
        for d in in_buffer_decls:
            if (
                d.kind == KIND_STRUCT
                and d.name == struct_name
                and d.line == target_decl.line
                and d.body_end_line == target_decl.body_end_line
            ):
                fields = scan_struct_fields(doc_text, d)
                if fields:
                    return fields
    # Disk fallback for cross-file resolution.
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return scan_struct_fields(text, target_decl)


# ---------------------------------------------------------------------------
# Convenience entry point.
# ---------------------------------------------------------------------------


def compute_struct_field_completions(
    uri: str,
    position: Dict[str, int],
    doc_text: str,
    decls: List[Tuple[str, TypeDecl]],
) -> Optional[List[Dict[str, Any]]]:
    """Top-level helper called by the type-completion pipeline.

    Returns ``None`` when the cursor isn't inside a brace-init body --
    the caller falls through to R24E's existing triggers. Returns a
    list of CompletionItems (possibly empty) when the cursor IS
    inside one; the caller treats this as the focused result and
    does NOT fall through to the generic fallback."""
    line_no = int(position.get("line", 0))
    character = int(position.get("character", 0))
    ctx = is_brace_init_context(line_no, character, doc_text)
    if ctx is None:
        return None
    specified = get_already_specified_fields(
        line_no, character, doc_text,
        ctx.open_lb_line, ctx.open_lb_col,
    )
    return compute_field_completions(
        ctx.struct_name, specified, decls, doc_text=doc_text,
    )
