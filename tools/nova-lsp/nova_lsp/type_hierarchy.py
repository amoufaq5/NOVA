"""Type hierarchy (`textDocument/prepareTypeHierarchy`,
`typeHierarchy/supertypes`, `typeHierarchy/subtypes`).

The type-hierarchy view lets a reader navigate "subtype" / "supertype"
relationships from the cursor: clicking an ``enum Option`` declaration
expands to a tree of its variants (``Some``, ``None``), while clicking
the right-hand side of a ``type ID = int`` exposes the ``int`` base
as a supertype. Editors typically render this as a two-pane tree
mirroring the call hierarchy panel R15F shipped, but coloured by
type containment rather than caller-callee edges.

LSP requests participating (per the spec's "Type Hierarchy" section):

  1. ``textDocument/prepareTypeHierarchy(uri, position)`` — resolve the
     identifier under the cursor to a ``TypeHierarchyItem`` payload —
     name, kind (Enum / Struct / Class / TypeParameter), uri, full
     decl range, name-token selection range. Returns ``None`` when
     the cursor isn't on a known type / enum declaration or usage.
  2. ``typeHierarchy/supertypes(item)`` — return the parent types the
     hovered type is built on top of. For NOVA's current type system
     this means:
       * ``type T = U``  -> ``[U]`` (U's TypeHierarchyItem)
       * ``enum E { ... }`` -> ``[]`` (enums have no inheritance)
       * a variant ``E::V`` viewed as a subtype -> ``[E]``
  3. ``typeHierarchy/subtypes(item)`` — return the types built on top
     of the hovered type. For NOVA this means:
       * ``enum E { V1, V2 }`` -> ``[V1, V2]`` (each variant as an
         EnumMember TypeHierarchyItem)
       * ``type Base = ...`` -> every other ``type X = Base`` alias
         pointing at it
       * any other type used elsewhere -> empty list

NOVA's current grammar has no formal inheritance / interface system,
so the hierarchy reduces to two clean rules:

  - The supertype of a type alias is the RHS of the alias.
  - The subtypes of an enum are its variant constructors. (R17A added
    these with the ``::`` path syntax — the lens module already counts
    distinct variants used so we share the parsing helpers below.)

Resolution path for ``prepare_type_hierarchy``:

  1. Cursor sits on a column-zero ``enum`` / ``type`` / ``struct``
     declaration line in the current document -> synthesize the item.
  2. Cursor sits on an identifier whose name matches a top-level
     ``enum`` / ``type`` / ``struct`` in the current document -> use
     that span.
  3. Cursor sits on an identifier matching a type declared in the
     transitively imported graph -> resolve via R5F's `walk_imports`.
  4. Cursor sits on an identifier matching a type in the workspace
     symbol index (sibling files outside the import graph).

The graph is built lazily inside ``supertypes`` / ``subtypes`` —
each call walks the relevant indexed files and emits one item per
relationship. There's no global graph object because the workspace
symbol index already keys files by absolute path so on-the-fly scans
are cheap (typically one file for supertypes, the whole workspace
for subtypes of a popular enum).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, walk_imports
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# LSP SymbolKind enum values used for TypeHierarchyItem.kind. The same
# enum is shared between SymbolKind and TypeHierarchyItem (LSP spec
# explicitly re-uses it so editors can pick one icon set).
SYMBOL_KIND_CLASS = 5
SYMBOL_KIND_ENUM = 10
SYMBOL_KIND_INTERFACE = 11
SYMBOL_KIND_FUNCTION = 12
SYMBOL_KIND_STRUCT = 23
SYMBOL_KIND_ENUM_MEMBER = 22
SYMBOL_KIND_TYPE_PARAMETER = 26


# ---------------------------------------------------------------------------
# Declaration regexes.
#
# Top-level (column-zero) only — mirrors `workspace_symbols._FN_DEF_RE`'s
# convention so the picker and the type hierarchy share the same notion
# of "what is a top-level type". `_ENUM_DECL_RE` doubles up to match
# both the legacy C-style `enum Name { N, S, E, W }` and R17A's
# multi-line payload form `enum Shape { Circle(int) Rect(int, int) }`.
# `_TYPE_ALIAS_RE` captures the RHS for the supertype lookup.
# `_STRUCT_DECL_RE` parses struct declarations the same way.
# ---------------------------------------------------------------------------

_ENUM_DECL_RE = re.compile(r"^enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_TYPE_ALIAS_RE = re.compile(
    r"^type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$"
)
_STRUCT_DECL_RE = re.compile(r"^struct\s+([A-Za-z_][A-Za-z0-9_]*)\b")

# Identifier-only — used by `_word_at` to locate the cursor word.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Enum-variant constructor / pattern access — same shape as the lens
# module's `_enum_variant_regex` but parameterised so we can scan any
# enum name. Accepts both R17A's `::` and the pre-R17A `.` form so a
# legacy codebase still navigates cleanly.
_ENUM_VARIANT_HEAD_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*)(?:::|\.)([A-Za-z_][A-Za-z0-9_]*)"
)


# Declaration "kinds" we surface. Stored as plain strings so callers
# can branch on the bucket without coupling to LSP's SymbolKind enum.
# `KIND_ENUM_MEMBER` doesn't appear as a top-level decl — it's emitted
# only as the result of `subtypes(enum)`.
KIND_ENUM = "enum"
KIND_STRUCT = "struct"
KIND_TYPE = "type"
KIND_ENUM_MEMBER = "enum_member"


# ---------------------------------------------------------------------------
# Comment / string masking — copy of the helper used by every other LSP
# module that scans NOVA source. Keeps column offsets stable so regex
# matches against the masked text still report the right Range.
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
# Declaration extraction.
# ---------------------------------------------------------------------------


@dataclass
class TypeDecl:
    """One top-level type declaration parsed out of a NOVA source file.

    ``line`` / ``name_char_start`` / ``name_char_end`` describe the
    column span of the declaration's name token on ``line``
    (zero-based). ``body_end_line`` is the line containing the closing
    ``}`` for enum / struct (or the same as ``line`` for a type alias
    which is single-line). ``alias_rhs`` carries the textual RHS for
    a ``type T = U`` declaration (empty for enum / struct). ``kind``
    is one of ``KIND_ENUM`` / ``KIND_STRUCT`` / ``KIND_TYPE``.
    """
    name: str
    kind: str
    line: int
    body_end_line: int
    name_char_start: int
    name_char_end: int
    alias_rhs: str = ""


def _find_block_end(lines: List[str], start_line: int) -> int:
    """Return the line index containing the closing ``}`` for a brace
    block opened on or after ``start_line``. Falls back to
    ``start_line`` when no brace is found (single-line declaration like
    ``enum E {}`` collapsed onto one line). Brace counting is naive but
    sufficient for well-formed NOVA source.
    """
    n = len(lines)
    open_line = start_line
    while open_line < n and "{" not in lines[open_line]:
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


def scan_type_declarations(text: str) -> List[TypeDecl]:
    """Return every top-level type-like declaration in ``text``, source
    order. Top-level = column zero so the picker stays in sync with the
    workspace symbol index's notion of "navigable global declaration".

    Recognised forms:
      * ``enum Name { ... }``  (single-line or multi-line body)
      * ``struct Name { ... }`` (single-line or multi-line body)
      * ``type Name = RHS``    (single-line; RHS captured for supertype
                                resolution)
    """
    lines = text.splitlines()
    out: List[TypeDecl] = []
    for line_no, line in enumerate(lines):
        m = _ENUM_DECL_RE.match(line)
        if m:
            end_line = _find_block_end(lines, line_no)
            out.append(TypeDecl(
                name=m.group(1),
                kind=KIND_ENUM,
                line=line_no,
                body_end_line=end_line,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
        m = _STRUCT_DECL_RE.match(line)
        if m:
            end_line = _find_block_end(lines, line_no)
            out.append(TypeDecl(
                name=m.group(1),
                kind=KIND_STRUCT,
                line=line_no,
                body_end_line=end_line,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
        m = _TYPE_ALIAS_RE.match(line)
        if m:
            out.append(TypeDecl(
                name=m.group(1),
                kind=KIND_TYPE,
                line=line_no,
                body_end_line=line_no,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
                alias_rhs=m.group(2).strip(),
            ))
            continue
    return out


def type_decl_by_name(decls: List[TypeDecl], name: str) -> Optional[TypeDecl]:
    """First top-level type with matching name (source order). ``None``
    when no top-level type by that name exists in the scanned file."""
    for d in decls:
        if d.name == name:
            return d
    return None


# ---------------------------------------------------------------------------
# Enum variant extraction.
#
# Walks the brace-counted body of ``enum Name { ... }`` and returns each
# variant identifier plus the line/column it was declared on. Shape of
# variants: bare identifier (``None``), payload-bearing
# (``Some(int)``), or comma-separated single-line. R17A's grammar
# accepts both single-line and multi-line forms; we handle both with
# brace-depth tracking and `_mask_comments_and_strings` so a quoted
# ``"None"`` doesn't surface as a variant.
# ---------------------------------------------------------------------------


@dataclass
class EnumVariant:
    """One declared variant inside an ``enum Name { ... }`` body.

    ``line`` is the source line containing the variant identifier;
    ``char_start`` / ``char_end`` are the column span of the identifier
    token. ``arity`` is the number of payload positions inferred from
    the parenthesised arg list (``Some(int)`` -> 1, ``Triangle(int,
    int, int)`` -> 3, bare ``None`` -> 0).
    """
    name: str
    line: int
    char_start: int
    char_end: int
    arity: int = 0


# Match a variant identifier optionally followed by `(...)`. Variant
# names in NOVA are conventionally CamelCase (`Some`, `Ok`, `Circle`)
# but the grammar permits any identifier — keep the regex permissive.
_VARIANT_RE = re.compile(
    r"\b([A-Z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?"
)


def scan_enum_variants(text: str, enum_decl: TypeDecl) -> List[EnumVariant]:
    """Walk the body of ``enum_decl`` and return one ``EnumVariant``
    per declared variant in source order.

    The body is the line range ``[enum_decl.line, enum_decl.body_end_line]``;
    brace depth is tracked across lines so the variants on the
    ``enum {`` opening line and the closing ``}`` line are scanned
    correctly. Variants are extracted via `_VARIANT_RE` so a bare
    ``None`` becomes ``EnumVariant(name="None", arity=0)`` and a
    payload-bearing ``Some(int)`` becomes
    ``EnumVariant(name="Some", arity=1)``.
    """
    out: List[EnumVariant] = []
    lines = text.splitlines()
    # Variants live between the `{` and `}` of the enum body. We track
    # brace depth so a nested `{` inside a generic arg or default
    # doesn't trick us into thinking the enum body has closed.
    in_body = False
    depth = 0
    for line_no in range(enum_decl.line, enum_decl.body_end_line + 1):
        if line_no >= len(lines):
            break
        line = lines[line_no]
        cleaned = _mask_comments_and_strings(line)
        scan_from = 0
        # On the opening line, skip past `enum Name {` so we don't
        # parse the type name itself as a variant.
        if not in_body and line_no == enum_decl.line:
            brace_pos = cleaned.find("{")
            if brace_pos == -1:
                # Multi-line: open brace is on a later line.
                continue
            in_body = True
            depth = 1
            scan_from = brace_pos + 1
        # Walk forward and emit a variant for each identifier at the
        # current brace depth. Naive but works for NOVA: variants are
        # one-per-line in the multi-line form, comma-separated in the
        # single-line form, and neither form uses nested braces inside
        # the variant declaration.
        i = scan_from
        n = len(cleaned)
        # Identify only top-level identifiers (depth == 1) — anything
        # at deeper brace nesting is a payload type signature, not a
        # variant name.
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
            # Try to match a variant at this column.
            m = _VARIANT_RE.match(cleaned, i)
            if m is None:
                i += 1
                continue
            variant_name = m.group(1)
            # The match landed if the identifier ALSO starts here in
            # the unmasked source (so a `(int)` payload after `Some`
            # doesn't get re-scanned). We rely on the mask preserving
            # column offsets.
            args_str = m.group(2) or ""
            arity = 0
            if args_str.strip():
                # Count commas + 1 — naive but matches R17A's
                # `enum_variant_arity` parser.
                arity = len([a for a in args_str.split(",") if a.strip()])
            out.append(EnumVariant(
                name=variant_name,
                line=line_no,
                char_start=m.start(1),
                char_end=m.end(1),
                arity=arity,
            ))
            i = m.end()
    return out


# ---------------------------------------------------------------------------
# TypeHierarchyItem construction.
# ---------------------------------------------------------------------------


def _path_to_uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _kind_to_lsp(kind: str) -> int:
    """Map the internal ``KIND_*`` bucket to the LSP SymbolKind enum
    value used by ``TypeHierarchyItem.kind``. ``KIND_TYPE`` aliases
    surface as ``TypeParameter`` so the editor's icon hints "this is a
    name standing in for something else"."""
    if kind == KIND_ENUM:
        return SYMBOL_KIND_ENUM
    if kind == KIND_STRUCT:
        return SYMBOL_KIND_STRUCT
    if kind == KIND_TYPE:
        return SYMBOL_KIND_TYPE_PARAMETER
    if kind == KIND_ENUM_MEMBER:
        return SYMBOL_KIND_ENUM_MEMBER
    return SYMBOL_KIND_CLASS


def build_type_hierarchy_item(
    decl: TypeDecl,
    path: str,
) -> Dict[str, Any]:
    """Construct a ``TypeHierarchyItem`` payload for ``decl`` in ``path``.

    Per LSP spec:
      * ``range`` covers the full ``[decl.line .. decl.body_end_line]``
        span (the whole enum/struct body or the single line of a type
        alias).
      * ``selectionRange`` covers just the name token on ``decl.line``.
      * ``detail`` renders a short tooltip — kind + (for aliases) RHS.
      * ``data`` carries a serializable handle so follow-up
        supertypes / subtypes requests can resolve without re-walking
        from scratch.
    """
    if decl.kind == KIND_TYPE and decl.alias_rhs:
        detail = f"type {decl.name} = {decl.alias_rhs}"
    elif decl.kind == KIND_ENUM:
        detail = f"enum {decl.name}"
    elif decl.kind == KIND_STRUCT:
        detail = f"struct {decl.name}"
    else:
        detail = decl.name
    return {
        "name": decl.name,
        "kind": _kind_to_lsp(decl.kind),
        "detail": detail,
        "uri": _path_to_uri(path),
        "range": {
            "start": {"line": decl.line, "character": 0},
            "end": {"line": decl.body_end_line, "character": 1},
        },
        "selectionRange": {
            "start": {"line": decl.line, "character": decl.name_char_start},
            "end": {"line": decl.line, "character": decl.name_char_end},
        },
        "data": {
            "path": os.path.abspath(path),
            "name": decl.name,
            "kind": decl.kind,
            "line": decl.line,
            "body_end_line": decl.body_end_line,
            "alias_rhs": decl.alias_rhs,
        },
    }


def build_variant_hierarchy_item(
    variant: EnumVariant,
    enum_decl: TypeDecl,
    path: str,
) -> Dict[str, Any]:
    """Construct a ``TypeHierarchyItem`` payload for an enum variant.

    The variant's ``range`` is the line + column span of the variant
    identifier itself (variants don't have a body — even
    payload-bearing ``Some(int)`` is conceptually a one-line decl).
    ``selectionRange`` mirrors ``range`` so clicking lands precisely
    on the identifier token. ``data.parent_name`` lets the supertypes
    handler walk back up the tree.
    """
    payload = ""
    if variant.arity > 0:
        # Best-effort rendering — we know the count but not the names.
        payload = "(" + ", ".join(["_"] * variant.arity) + ")"
    detail = f"{enum_decl.name}::{variant.name}{payload}"
    return {
        "name": f"{enum_decl.name}::{variant.name}",
        "kind": SYMBOL_KIND_ENUM_MEMBER,
        "detail": detail,
        "uri": _path_to_uri(path),
        "range": {
            "start": {"line": variant.line, "character": variant.char_start},
            "end": {"line": variant.line, "character": variant.char_end},
        },
        "selectionRange": {
            "start": {"line": variant.line, "character": variant.char_start},
            "end": {"line": variant.line, "character": variant.char_end},
        },
        "data": {
            "path": os.path.abspath(path),
            "name": variant.name,
            "kind": KIND_ENUM_MEMBER,
            "line": variant.line,
            "body_end_line": variant.line,
            "parent_name": enum_decl.name,
            "parent_line": enum_decl.line,
            "arity": variant.arity,
        },
    }


def build_synthetic_base_item(
    base_name: str,
    uri: str,
) -> Dict[str, Any]:
    """Construct a placeholder TypeHierarchyItem for a base type that
    isn't itself declared in the workspace (e.g. ``int`` / ``str`` /
    ``bool`` — built-in primitives).

    The placeholder uses a zero-width range at the file's origin so
    the editor still renders a node in the tree; the editor disables
    "go to definition" because no source location is provided.
    """
    return {
        "name": base_name,
        "kind": SYMBOL_KIND_TYPE_PARAMETER,
        "detail": f"(builtin) {base_name}",
        "uri": uri,
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        },
        "selectionRange": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        },
        "data": {
            "path": "",
            "name": base_name,
            "kind": "builtin",
            "line": 0,
            "body_end_line": 0,
            "alias_rhs": "",
        },
    }


# ---------------------------------------------------------------------------
# prepareTypeHierarchy.
# ---------------------------------------------------------------------------


def _word_at(text: str, line: int, character: int) -> Optional[str]:
    """Identifier under the cursor (mirror of `server.word_at`)."""
    lines = text.splitlines()
    if not (0 <= line < len(lines)):
        return None
    src = lines[line]
    if not (0 <= character <= len(src)):
        return None
    start = character
    while start > 0 and (src[start - 1].isalnum() or src[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(src) and (src[end].isalnum() or src[end] == "_"):
        end += 1
    if start == end:
        return None
    return src[start:end]


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of `server.uri_to_path -> abspath`."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))


# Built-in primitive types NOVA understands. Cursor on the RHS of a
# type alias that references one of these resolves to a synthetic
# base item rather than failing the prepare step.
_BUILTIN_TYPES: Set[str] = {
    "int", "str", "bool", "float", "nil", "list", "map", "any",
    "Option", "Result",  # R17A's standard library sum types
}


def prepare_type_hierarchy(
    uri: str,
    line: int,
    character: int,
    doc_text: str,
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Resolve the cursor's symbol to a ``TypeHierarchyItem[]``.

    Per LSP spec the response is ``TypeHierarchyItem[] | null``. We
    return either:
      * ``None`` when the cursor isn't on a recognised type / enum
        identifier (e.g. on whitespace, a function name, or a let
        binding).
      * A single-element list with the TypeHierarchyItem.

    Resolution strategy (in priority order):
      1. Cursor on a column-zero ``enum`` / ``type`` / ``struct``
         declaration line in the current document.
      2. Cursor on an identifier whose name matches a top-level type
         declaration in the current document.
      3. Cursor on an enum-variant identifier (``Name::Variant``) —
         emit a variant-flavoured TypeHierarchyItem so the editor
         can navigate up to its parent enum via ``supertypes``.
      4. Cursor on an identifier matching a type in the transitively
         imported graph.
      5. Cursor on an identifier matching a type in the workspace
         symbol index (sibling files outside the import graph).
    """
    name = _word_at(doc_text, line, character)
    if not name:
        return None
    decls = scan_type_declarations(doc_text)
    # Step 1+2: same-doc resolution.
    same_doc = type_decl_by_name(decls, name)
    if same_doc is not None:
        path = _uri_to_abs(uri)
        if path:
            return [build_type_hierarchy_item(same_doc, path)]
    # Step 3: maybe the cursor is on a variant identifier inside a
    # `Name::Variant` expression. Walk the line for `Name::Variant`
    # patterns and emit the variant item if the cursor sits on either
    # the enum name OR the variant name.
    lines = doc_text.splitlines()
    if 0 <= line < len(lines):
        cleaned = _mask_comments_and_strings(lines[line])
        for m in _ENUM_VARIANT_HEAD_RE.finditer(cleaned):
            enum_name = m.group(1)
            variant_name = m.group(2)
            enum_start, enum_end = m.start(1), m.end(1)
            variant_start, variant_end = m.start(2), m.end(2)
            on_enum_name = enum_start <= character <= enum_end and name == enum_name
            on_variant = variant_start <= character <= variant_end and name == variant_name
            if not (on_enum_name or on_variant):
                continue
            # Resolve the parent enum's decl — could be in this doc, in
            # an imported file, or in a sibling workspace file.
            enum_decl = type_decl_by_name(decls, enum_name)
            if enum_decl is not None and on_enum_name:
                path = _uri_to_abs(uri)
                if path:
                    return [build_type_hierarchy_item(enum_decl, path)]
            resolved = _resolve_type_decl(
                enum_name,
                _uri_to_abs(uri) or "",
                file_cache,
                workspace_index=workspace_index,
                text_overrides=text_overrides,
            )
            if resolved is None:
                continue
            enum_decl_remote, enum_path = resolved
            if enum_decl_remote.kind != KIND_ENUM:
                continue
            if on_enum_name:
                return [build_type_hierarchy_item(enum_decl_remote, enum_path)]
            # Find the variant inside the enum body.
            enum_text = _read_text(enum_path, file_cache, text_overrides or {})
            if enum_text is None:
                continue
            variants = scan_enum_variants(enum_text, enum_decl_remote)
            for v in variants:
                if v.name == variant_name:
                    return [build_variant_hierarchy_item(v, enum_decl_remote, enum_path)]
            # Variant identifier known but not declared on the enum —
            # still surface a synthetic item so the editor can render
            # a node (e.g. legacy `Name.foo` with a stray field).
            synthetic = EnumVariant(
                name=variant_name,
                line=line,
                char_start=variant_start,
                char_end=variant_end,
                arity=0,
            )
            return [build_variant_hierarchy_item(synthetic, enum_decl_remote, enum_path)]
    # Step 4+5: cross-file resolution.
    resolved = _resolve_type_decl(
        name,
        _uri_to_abs(uri) or "",
        file_cache,
        workspace_index=workspace_index,
        text_overrides=text_overrides,
    )
    if resolved is None:
        return None
    decl, path = resolved
    return [build_type_hierarchy_item(decl, path)]


def _read_text(
    path: str,
    file_cache: FileCache,
    text_overrides: Dict[str, str],
) -> Optional[str]:
    """Live buffer text for ``path`` if open, else cached on-disk
    content. ``None`` when the file can't be read at all."""
    override = text_overrides.get(os.path.abspath(path))
    if override is not None:
        return override
    entry = file_cache.get(path)
    if entry is None:
        return None
    return entry.text


def _resolve_type_decl(
    name: str,
    start_path: str,
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[TypeDecl, str]]:
    """Look up ``name``'s ``(TypeDecl, path)`` in the import graph
    rooted at ``start_path``, falling back to the workspace symbol
    index for sibling files outside the graph. Returns ``None`` when
    ``name`` isn't a known top-level type."""
    overrides = text_overrides or {}
    # 1. Import graph.
    if start_path:
        for entry in walk_imports(
            os.path.abspath(start_path), file_cache, text_overrides=overrides
        ):
            decls = scan_type_declarations(entry.text)
            hit = type_decl_by_name(decls, name)
            if hit is not None:
                return hit, entry.path
    # 2. Workspace index — walk every file the index knows about and
    # scan it for a matching top-level type. The index doesn't track
    # types as a distinct symbol kind (R8C only surfaces fn/let), so
    # we open each file once. Cheap thanks to the FileCache.
    if workspace_index is not None:
        seen: Set[str] = set()
        # noinspection PyProtectedMember
        for path in workspace_index._by_file.keys():  # noqa: SLF001
            abs_path = os.path.abspath(path)
            if abs_path in seen:
                continue
            seen.add(abs_path)
            text = overrides.get(abs_path)
            if text is None:
                entry = file_cache.get(abs_path)
                if entry is None:
                    continue
                text = entry.text
            decls = scan_type_declarations(text)
            hit = type_decl_by_name(decls, name)
            if hit is not None:
                return hit, abs_path
    return None


# ---------------------------------------------------------------------------
# supertypes.
# ---------------------------------------------------------------------------


def supertypes(
    item: Dict[str, Any],
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Return the parent types of ``item``.

    Per LSP spec the response is ``TypeHierarchyItem[]``. NOVA's
    current type system has three rules:

      * ``enum``  -> ``[]`` (no inheritance).
      * ``struct`` -> ``[]`` (no inheritance).
      * ``type T = U`` -> ``[U]``; U is resolved to its own decl when
        possible, otherwise we emit a synthetic primitive base item
        so the editor still renders a node.
      * enum-variant member -> ``[parent_enum]`` (the enum that
        declares this variant).
    """
    data = item.get("data") or {}
    kind = data.get("kind")
    overrides = text_overrides or {}
    if kind == KIND_ENUM or kind == KIND_STRUCT:
        return []
    if kind == KIND_ENUM_MEMBER:
        # The parent enum is recorded in `data.parent_name`.
        parent_name = data.get("parent_name")
        if not parent_name:
            return []
        start_path = data.get("path") or ""
        resolved = _resolve_type_decl(
            parent_name,
            start_path,
            file_cache,
            workspace_index=workspace_index,
            text_overrides=overrides,
        )
        if resolved is None:
            return []
        parent_decl, parent_path = resolved
        return [build_type_hierarchy_item(parent_decl, parent_path)]
    if kind == KIND_TYPE:
        # The supertype is the RHS of the alias. The RHS might be a
        # single identifier (`type ID = int`) or a more complex
        # expression (`type Result = Option<int>`). We extract every
        # identifier; for each, we attempt to resolve it to a type
        # decl. Identifiers that resolve to a builtin or fail to
        # resolve are emitted as synthetic placeholder items.
        rhs = data.get("alias_rhs") or item.get("detail", "")
        # If we got the full `type X = RHS` detail, peel off the prefix.
        if "=" in rhs:
            rhs = rhs.split("=", 1)[1]
        rhs = rhs.strip()
        if not rhs:
            return []
        # Extract identifier tokens only — drop punctuation / numbers.
        idents = _IDENT_RE.findall(rhs)
        if not idents:
            return []
        start_path = data.get("path") or ""
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        item_uri = item.get("uri", "")
        for ident in idents:
            if ident in seen:
                continue
            seen.add(ident)
            resolved = _resolve_type_decl(
                ident,
                start_path,
                file_cache,
                workspace_index=workspace_index,
                text_overrides=overrides,
            )
            if resolved is not None:
                parent_decl, parent_path = resolved
                out.append(build_type_hierarchy_item(parent_decl, parent_path))
                continue
            # Treat as a builtin / synthetic base.
            if ident in _BUILTIN_TYPES:
                out.append(build_synthetic_base_item(ident, item_uri))
        return out
    return []


# ---------------------------------------------------------------------------
# subtypes.
# ---------------------------------------------------------------------------


def subtypes(
    item: Dict[str, Any],
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Return the children of ``item`` in the type hierarchy.

    Per LSP spec the response is ``TypeHierarchyItem[]``. NOVA's
    rules:

      * ``enum`` -> every declared variant as an
        ``EnumMember``-kind TypeHierarchyItem.
      * ``struct`` -> ``[]`` (no inheritance / no per-field navigation).
      * ``type T = U`` -> every other ``type X = T`` alias in the
        workspace pointing back at this name.
      * enum-variant member -> ``[]`` (variants are leaves in the
        type tree).
    """
    data = item.get("data") or {}
    kind = data.get("kind")
    overrides = text_overrides or {}
    path = data.get("path") or ""
    if kind == KIND_ENUM:
        # Read the enum body and emit one variant item per declared
        # variant. The enum decl might be in another file — `data.path`
        # records the absolute path so we don't lose it across the
        # round trip.
        text = _read_text(path, file_cache, overrides)
        if text is None:
            return []
        decls = scan_type_declarations(text)
        enum_decl = type_decl_by_name(decls, data.get("name", ""))
        if enum_decl is None or enum_decl.kind != KIND_ENUM:
            return []
        variants = scan_enum_variants(text, enum_decl)
        return [
            build_variant_hierarchy_item(v, enum_decl, path)
            for v in variants
        ]
    if kind == KIND_TYPE:
        # Find every other `type X = <name>` alias in the workspace
        # that references this type as its supertype.
        target_name = data.get("name", "")
        if not target_name:
            return []
        candidates: Set[str] = set()
        # noinspection PyProtectedMember
        for p in workspace_index._by_file.keys():  # noqa: SLF001
            candidates.add(os.path.abspath(p))
        if extra_paths:
            for p in extra_paths:
                candidates.add(os.path.abspath(p))
        candidates.add(os.path.abspath(path))
        out: List[Dict[str, Any]] = []
        for p in sorted(candidates):
            text = _read_text(p, file_cache, overrides)
            if text is None:
                continue
            decls = scan_type_declarations(text)
            for d in decls:
                if d.kind != KIND_TYPE:
                    continue
                # Quick whole-word containment check on the RHS — the
                # supertype resolver handles complex RHS (generic
                # forms), but for the subtype walk we just look for
                # the target name as a whole-word identifier.
                if not d.alias_rhs:
                    continue
                if target_name in _IDENT_RE.findall(d.alias_rhs):
                    out.append(build_type_hierarchy_item(d, p))
        return out
    if kind == KIND_STRUCT:
        return []
    if kind == KIND_ENUM_MEMBER:
        return []
    return []
