"""Document symbols for ``textDocument/documentSymbol``.

The document-symbol provider drives the editor's *outline* panel — the
hierarchical tree view of every top-level declaration in the file with
nested children where appropriate. VS Code, JetBrains, Helix, and most
other LSP-aware editors render this as the breadcrumb bar, the
"Outline" sidebar, and the "Go to symbol in file..." picker
(Cmd+Shift+O).

LSP wire shape (per the spec's `Document Symbol Request
<https://microsoft.github.io/language-server-protocol/specification/#textDocument_documentSymbol>`_):

    interface DocumentSymbol {
        name: string;
        detail?: string;
        kind: SymbolKind;
        tags?: SymbolTag[];
        deprecated?: boolean;
        range: Range;             // entire declaration through `}`
        selectionRange: Range;    // just the name token
        children?: DocumentSymbol[];
    }

The server returns ``DocumentSymbol[]`` (the modern hierarchical
shape; ``SymbolInformation[]`` is the older flat shape clients fall
back to when the hierarchical form isn't supported). Editors prefer
``DocumentSymbol[]`` because it preserves containment, so an enum's
variants nest under their enum and a struct's fields nest under their
struct.

Recognised top-level declarations (NOVA-specific):

  * ``fn name(args) { ... }``        -> SymbolKind.Function (12)
  * ``let NAME = ...``  (ALL_CAPS)   -> SymbolKind.Constant (14)
  * ``let name = ...``  (mixed-case) -> SymbolKind.Variable (13)
  * ``const NAME = ...``             -> SymbolKind.Constant (14)
  * ``type Name = ...``              -> SymbolKind.TypeParameter (26)
  * ``enum Name { V1, V2 }``         -> SymbolKind.Enum (10)
    + each declared variant as       -> SymbolKind.EnumMember (22)
      a child symbol
  * ``struct Name { f1, f2 }``       -> SymbolKind.Struct (23)
    + each declared field as a       -> SymbolKind.Field (8)
      child symbol

Why we surface variants + fields as children: editors render the
outline tree so a user can fold an enum to see only the variant names,
or fold a struct to see only the field names. This matches the
"breadcrumb" UX of VS Code (Type::Variant -> select to navigate).
Conventional choice across language servers (rust-analyzer,
typescript-language-server, pyright all do this).

The ``range`` covers the full declaration ``[decl_line, body_end_line]``
so the editor can scroll the entire block into view when navigating;
``selectionRange`` covers only the name token so clicking lands
precisely on the identifier.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nova_lsp.imports import FileCache


# ---------------------------------------------------------------------------
# LSP SymbolKind enum values referenced by document symbols. The spec
# reserves these as opaque integers; we mirror the same numbering used
# in workspace_symbols.py / type_hierarchy.py so the editor renders
# consistent icons across capabilities.
# ---------------------------------------------------------------------------


SYMBOL_KIND_FIELD = 8
SYMBOL_KIND_ENUM = 10
SYMBOL_KIND_FUNCTION = 12
SYMBOL_KIND_VARIABLE = 13
SYMBOL_KIND_CONSTANT = 14
SYMBOL_KIND_ENUM_MEMBER = 22
SYMBOL_KIND_STRUCT = 23
SYMBOL_KIND_TYPE_PARAMETER = 26


# ---------------------------------------------------------------------------
# Declaration patterns. Identical to the workspace_symbols / code_lens
# regexes so the outline picker and the workspace symbol picker stay
# in lock-step ("if it's a top-level fn, both surfaces see it").
# ---------------------------------------------------------------------------


_FN_DEF_RE = re.compile(r"^fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_LET_DEF_RE = re.compile(r"^let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_CONST_DEF_RE = re.compile(r"^const\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_ENUM_DEF_RE = re.compile(r"^enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_STRUCT_DEF_RE = re.compile(r"^struct\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_TYPE_DEF_RE = re.compile(r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")

_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# Variant pattern inside an enum body: an identifier (typically
# CamelCase) optionally followed by a parenthesised payload list. Same
# pattern type_hierarchy uses; duplicated here to keep modules
# independent so this one compiles even if type_hierarchy isn't loaded.
_VARIANT_RE = re.compile(
    r"\b([A-Z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?"
)


# Field pattern inside a struct body. NOVA's struct syntax accepts
# ``name: type`` rows (R17A's notation). We capture both the field
# identifier and the optional type annotation so the outline detail
# can render ``f1: int`` rather than a bare ``f1``.
_FIELD_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^,/\n]+?))?\s*(?:,|$)"
)


# ---------------------------------------------------------------------------
# Comment + string masking — shared idea with code_lens / type_hierarchy.
# Replaces commented / string-literal text with spaces so column offsets
# stay stable but regex matches never land inside a quoted token.
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
# Internal symbol dataclass — converted to LSP wire form by ``to_lsp``.
# Children carry their own list of nested ``DocumentSymbol`` for
# variants / fields.
# ---------------------------------------------------------------------------


@dataclass
class DocumentSymbol:
    """One outline entry — name + kind + ranges + children.

    All line / character offsets are zero-based. ``end_line`` /
    ``end_char`` describe the position just past the last character of
    the declaration (LSP ranges are half-open: end exclusive). For a
    multi-line block the convention is ``end_line = body_end_line``,
    ``end_char = 1`` (past the closing ``}``).
    """
    name: str
    kind: int
    detail: str
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    selection_start_line: int
    selection_end_line: int
    selection_start_char: int
    selection_end_char: int
    children: List["DocumentSymbol"] = field(default_factory=list)

    def to_lsp(self) -> Dict[str, Any]:
        """LSP wire shape for DocumentSymbol."""
        payload: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "range": {
                "start": {"line": self.start_line, "character": self.start_char},
                "end": {"line": self.end_line, "character": self.end_char},
            },
            "selectionRange": {
                "start": {
                    "line": self.selection_start_line,
                    "character": self.selection_start_char,
                },
                "end": {
                    "line": self.selection_end_line,
                    "character": self.selection_end_char,
                },
            },
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.children:
            payload["children"] = [c.to_lsp() for c in self.children]
        return payload


# ---------------------------------------------------------------------------
# Brace-walker — given a line known (or expected) to contain ``{``,
# return the line index containing the matching ``}``. Mirrors the
# code_lens / type_hierarchy `_find_block_end` helper but exposes both
# the *opening* line and the *close* line so callers can position
# ranges precisely.
# ---------------------------------------------------------------------------


def _find_block_end(lines: List[str], start_line: int) -> int:
    """Return the line index containing the closing ``}`` for a brace
    block opened on or after ``start_line``. Falls back to ``start_line``
    when no brace is found (single-line declaration like ``enum E {}``
    collapsed onto one line)."""
    n = len(lines)
    open_line = start_line
    while open_line < n and "{" not in _mask_comments_and_strings(lines[open_line]):
        open_line += 1
    if open_line >= n:
        return start_line
    depth = 0
    found_open = False
    for j in range(open_line, n):
        cleaned = _mask_comments_and_strings(lines[j])
        for ch in cleaned:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return j
    return n - 1


# ---------------------------------------------------------------------------
# Variant scanner — adapted from type_hierarchy.scan_enum_variants but
# keyed on ``(start_line, end_line)`` so it doesn't need a separate
# type-decl object. Walks the body of an enum, emitting one entry per
# declared variant with the line/column of the identifier.
# ---------------------------------------------------------------------------


@dataclass
class _Variant:
    name: str
    line: int
    char_start: int
    char_end: int
    arity: int


def _scan_enum_variants(
    lines: List[str],
    decl_line: int,
    body_end_line: int,
) -> List[_Variant]:
    """Walk the enum body and emit one ``_Variant`` per declaration."""
    out: List[_Variant] = []
    in_body = False
    depth = 0
    for line_no in range(decl_line, body_end_line + 1):
        if line_no >= len(lines):
            break
        cleaned = _mask_comments_and_strings(lines[line_no])
        scan_from = 0
        if not in_body and line_no == decl_line:
            brace_pos = cleaned.find("{")
            if brace_pos == -1:
                continue
            in_body = True
            depth = 1
            scan_from = brace_pos + 1
        i = scan_from
        n = len(cleaned)
        while i < n:
            ch = cleaned[i]
            if ch == "{":
                depth += 1
                i += 1
                continue
            if ch == "}":
                depth -= 1
                i += 1
                if depth <= 0:
                    return out
                continue
            if depth != 1:
                i += 1
                continue
            m = _VARIANT_RE.match(cleaned, i)
            if m is None:
                i += 1
                continue
            args_str = m.group(2) or ""
            arity = 0
            if args_str.strip():
                arity = len([a for a in args_str.split(",") if a.strip()])
            out.append(_Variant(
                name=m.group(1),
                line=line_no,
                char_start=m.start(1),
                char_end=m.end(1),
                arity=arity,
            ))
            i = m.end()
    return out


# ---------------------------------------------------------------------------
# Field scanner — walks the body of a struct decl and emits one entry
# per field row. Field rows are typically one per line in NOVA's
# multi-line struct form, but we also accept comma-separated
# single-line forms.
# ---------------------------------------------------------------------------


@dataclass
class _Field:
    name: str
    type_str: str
    line: int
    char_start: int
    char_end: int


def _scan_struct_fields(
    lines: List[str],
    decl_line: int,
    body_end_line: int,
) -> List[_Field]:
    """Walk the struct body and emit one ``_Field`` per row."""
    out: List[_Field] = []
    in_body = False
    depth = 0
    for line_no in range(decl_line, body_end_line + 1):
        if line_no >= len(lines):
            break
        cleaned = _mask_comments_and_strings(lines[line_no])
        scan_from = 0
        if not in_body and line_no == decl_line:
            brace_pos = cleaned.find("{")
            if brace_pos == -1:
                continue
            in_body = True
            depth = 1
            scan_from = brace_pos + 1
        # We process each line segment (after `{` on the open line).
        segment = cleaned[scan_from:] if line_no == decl_line else cleaned
        seg_offset = scan_from if line_no == decl_line else 0
        # Count braces in the segment so we know when the body closes.
        for k, ch in enumerate(segment):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth <= 0:
                    return out
        if depth >= 1 and segment.strip():
            # Try to match `name [: type] [,]`. We allow multiple
            # fields per line (single-line struct form).
            for raw_part in segment.split(","):
                part = raw_part
                if not part.strip():
                    continue
                m = _FIELD_RE.match(part)
                if not m:
                    continue
                # Compute absolute column for the name token.
                name_off = part.find(m.group(1))
                # Locate part position within segment.
                start_in_seg = segment.find(raw_part)
                if start_in_seg == -1:
                    start_in_seg = 0
                name_start = seg_offset + start_in_seg + name_off
                out.append(_Field(
                    name=m.group(1),
                    type_str=(m.group(2) or "").strip(),
                    line=line_no,
                    char_start=name_start,
                    char_end=name_start + len(m.group(1)),
                ))
    return out


# ---------------------------------------------------------------------------
# Top-level walker — emit a DocumentSymbol per top-level decl in source
# order. Brace-counted bodies for enum/struct produce nested children.
# ---------------------------------------------------------------------------


def _classify_let(name: str) -> int:
    """ALL_CAPS lets are constants by convention; everything else is a
    variable. Mirrors workspace_symbols._classify_let so the outline
    picker and the workspace picker classify the same name the same
    way."""
    return SYMBOL_KIND_CONSTANT if _ALL_CAPS_RE.match(name) else SYMBOL_KIND_VARIABLE


def _fn_body_end(lines: List[str], decl_line: int) -> int:
    """Return the line index of the matching ``}`` for a ``fn`` body
    opened on or after ``decl_line``. Mirrors ``_find_block_end`` but
    returns ``decl_line`` for a single-line ``fn foo() { return 1 }``.
    """
    return _find_block_end(lines, decl_line)


def _signature_detail(line: str) -> str:
    """Render the signature portion of an ``fn`` declaration as the
    outline ``detail`` field. For ``fn foo(a, b) {`` returns
    ``fn foo(a, b)`` (drops the open brace and trailing whitespace).
    """
    cleaned = _mask_comments_and_strings(line)
    brace = cleaned.find("{")
    if brace == -1:
        return cleaned.rstrip()
    return cleaned[:brace].rstrip()


def _build_fn_symbol(
    lines: List[str],
    line_no: int,
    name: str,
    name_start: int,
    name_end: int,
) -> DocumentSymbol:
    """Construct a fn DocumentSymbol covering the full body."""
    body_end = _fn_body_end(lines, line_no)
    detail = _signature_detail(lines[line_no])
    return DocumentSymbol(
        name=name,
        kind=SYMBOL_KIND_FUNCTION,
        detail=detail,
        start_line=line_no,
        end_line=body_end,
        start_char=0,
        end_char=len(lines[body_end]) if body_end < len(lines) else 0,
        selection_start_line=line_no,
        selection_end_line=line_no,
        selection_start_char=name_start,
        selection_end_char=name_end,
    )


def _build_enum_symbol(
    lines: List[str],
    line_no: int,
    name: str,
    name_start: int,
    name_end: int,
) -> DocumentSymbol:
    """Construct an enum DocumentSymbol + nested variant children."""
    body_end = _find_block_end(lines, line_no)
    variants = _scan_enum_variants(lines, line_no, body_end)
    children: List[DocumentSymbol] = []
    for v in variants:
        payload = ""
        if v.arity > 0:
            payload = "(" + ", ".join(["_"] * v.arity) + ")"
        variant_detail = f"{name}::{v.name}{payload}"
        children.append(DocumentSymbol(
            name=v.name,
            kind=SYMBOL_KIND_ENUM_MEMBER,
            detail=variant_detail,
            start_line=v.line,
            end_line=v.line,
            start_char=v.char_start,
            end_char=v.char_end,
            selection_start_line=v.line,
            selection_end_line=v.line,
            selection_start_char=v.char_start,
            selection_end_char=v.char_end,
        ))
    return DocumentSymbol(
        name=name,
        kind=SYMBOL_KIND_ENUM,
        detail=f"enum {name}",
        start_line=line_no,
        end_line=body_end,
        start_char=0,
        end_char=len(lines[body_end]) if body_end < len(lines) else 0,
        selection_start_line=line_no,
        selection_end_line=line_no,
        selection_start_char=name_start,
        selection_end_char=name_end,
        children=children,
    )


def _build_struct_symbol(
    lines: List[str],
    line_no: int,
    name: str,
    name_start: int,
    name_end: int,
) -> DocumentSymbol:
    """Construct a struct DocumentSymbol + nested field children."""
    body_end = _find_block_end(lines, line_no)
    fields = _scan_struct_fields(lines, line_no, body_end)
    children: List[DocumentSymbol] = []
    for f in fields:
        field_detail = f"{f.name}: {f.type_str}" if f.type_str else f.name
        children.append(DocumentSymbol(
            name=f.name,
            kind=SYMBOL_KIND_FIELD,
            detail=field_detail,
            start_line=f.line,
            end_line=f.line,
            start_char=f.char_start,
            end_char=f.char_end,
            selection_start_line=f.line,
            selection_end_line=f.line,
            selection_start_char=f.char_start,
            selection_end_char=f.char_end,
        ))
    return DocumentSymbol(
        name=name,
        kind=SYMBOL_KIND_STRUCT,
        detail=f"struct {name}",
        start_line=line_no,
        end_line=body_end,
        start_char=0,
        end_char=len(lines[body_end]) if body_end < len(lines) else 0,
        selection_start_line=line_no,
        selection_end_line=line_no,
        selection_start_char=name_start,
        selection_end_char=name_end,
        children=children,
    )


def _build_value_symbol(
    line_no: int,
    line: str,
    name: str,
    name_start: int,
    name_end: int,
    kind: int,
    keyword: str,
) -> DocumentSymbol:
    """Construct a single-line value (``let`` / ``const`` / ``type``)
    DocumentSymbol covering the entire declaration line.
    """
    cleaned = _mask_comments_and_strings(line).rstrip()
    return DocumentSymbol(
        name=name,
        kind=kind,
        detail=cleaned if len(cleaned) < 80 else f"{keyword} {name}",
        start_line=line_no,
        end_line=line_no,
        start_char=0,
        end_char=len(line),
        selection_start_line=line_no,
        selection_end_line=line_no,
        selection_start_char=name_start,
        selection_end_char=name_end,
    )


def scan_document_symbols(text: str) -> List[DocumentSymbol]:
    """Walk ``text`` and emit one DocumentSymbol per top-level
    declaration in source order. Enums and structs gain a ``children``
    list of variant / field sub-symbols.
    """
    lines = text.splitlines()
    out: List[DocumentSymbol] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = _FN_DEF_RE.match(line)
        if m:
            out.append(_build_fn_symbol(
                lines,
                i,
                m.group(1),
                m.start(1),
                m.end(1),
            ))
            # Skip past the body so a nested `let` doesn't surface as a
            # spurious top-level symbol.
            i = _fn_body_end(lines, i) + 1
            continue
        m = _ENUM_DEF_RE.match(line)
        if m:
            sym = _build_enum_symbol(
                lines,
                i,
                m.group(1),
                m.start(1),
                m.end(1),
            )
            out.append(sym)
            i = sym.end_line + 1
            continue
        m = _STRUCT_DEF_RE.match(line)
        if m:
            sym = _build_struct_symbol(
                lines,
                i,
                m.group(1),
                m.start(1),
                m.end(1),
            )
            out.append(sym)
            i = sym.end_line + 1
            continue
        m = _CONST_DEF_RE.match(line)
        if m:
            out.append(_build_value_symbol(
                i, line,
                m.group(1), m.start(1), m.end(1),
                SYMBOL_KIND_CONSTANT,
                "const",
            ))
            i += 1
            continue
        m = _LET_DEF_RE.match(line)
        if m:
            name = m.group(1)
            out.append(_build_value_symbol(
                i, line,
                name, m.start(1), m.end(1),
                _classify_let(name),
                "let",
            ))
            i += 1
            continue
        m = _TYPE_DEF_RE.match(line)
        if m:
            out.append(_build_value_symbol(
                i, line,
                m.group(1), m.start(1), m.end(1),
                SYMBOL_KIND_TYPE_PARAMETER,
                "type",
            ))
            i += 1
            continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# Top-level entry point used by ``server.handle_document_symbol``.
# Returns the LSP wire shape (list of dicts), suitable for direct return
# from the JSON-RPC dispatcher.
# ---------------------------------------------------------------------------


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of ``server.uri_to_path -> abspath`` — kept here so
    this module is independently importable without the server import
    cycle."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))


def compute_document_symbols(
    uri: str,
    doc_text: str,
    file_cache: Optional[FileCache] = None,
) -> List[Dict[str, Any]]:
    """Compute DocumentSymbol[] for the document at ``uri``.

    Parameters:
      ``uri``        -- document URI (used for signature symmetry; not
                        otherwise needed because document symbols are
                        intra-file).
      ``doc_text``   -- current buffer text (authoritative over disk).
      ``file_cache`` -- accepted for symmetry with the rest of the LSP
                        module suite but unused; document symbols are
                        single-file and don't traverse imports.

    Returns a list of LSP wire-shape DocumentSymbol entries in source
    order. Empty list for an empty file or a file with no declarations.
    """
    if not doc_text:
        return []
    symbols = scan_document_symbols(doc_text)
    return [s.to_lsp() for s in symbols]
