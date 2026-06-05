"""Type-aware completion for `textDocument/completion`.

Earlier rounds shipped a text-based completion list — every builtin +
top-level ``fn`` + top-level ``let`` reachable from the open buffer,
filtered by the editor against the typed prefix. That's strictly more
helpful than nothing but it's noisy: typing ``Result::`` shouldn't
suggest ``println`` or ``main``, it should suggest ``Ok`` and ``Err``.

This module adds a context-sensitive layer that runs BEFORE the
text-based fallback. The completion handler in ``server.py`` calls
``compute_type_aware_completions`` first; if the cursor sits in a
recognised "type-aware" context the returned list of CompletionItems
replaces the generic list. Otherwise the handler falls through to the
existing behaviour and the user sees the full text-based list.

Recognised contexts (all detected by scanning the characters before the
cursor on the same source line — cheap, no AST needed):

  1. ``Name::``  -> suggest the variants of enum ``Name``. Works for
     any enum reachable from the current document's import graph and
     for sibling enums via R8C's workspace symbol index. Examples:

         Option::|        ->  Some, None
         Result::|        ->  Ok, Err
         Shape::|         ->  Circle, Rect, Triangle

  2. ``var.``    -> suggest the fields of the struct ``var`` is bound
     to. ``var``'s type is read from its declaration site — both
     ``let var: Box<int> = ...`` (R23A) and ``let var = Box(...)``
     constructor inference are supported. Example:

         let b: Box<int> = Box(42)
         b.|              ->  value

  3. ``let x: `` / ``let x: Box<`` (annotation site) -> suggest enum +
     struct names from the workspace plus the built-in primitives.
     Triggered by ``: `` after the let name. Examples:

         let x: |         ->  Option, Result, Shape, Box, Pair, int,
                              str, bool, float, list, map
         let r: Box<|     ->  same set (nested type args)

  4. ``fn foo(p: ``      -> suggest type names. Same set as the
     annotation case. Triggers when the cursor lands on a colon-prefixed
     argument position inside a function parameter list.

Trigger detection deliberately uses a single source line — multi-line
contexts (e.g. a parameter list split across rows) would require real
parsing. The character before the cursor is interrogated; if the last
non-space char is a recognised trigger (``::``, ``.``, ``:`` with a
preceding ``let`` / ``,`` / ``(``, or ``<`` following a known type
name) we synthesise the relevant CompletionItem list. Otherwise the
caller falls back to the text-based behaviour.

Public entry point:

    compute_type_aware_completions(
        uri, position, doc_text, file_cache, workspace_index
    ) -> Optional[List[CompletionItem]]

Returns ``None`` when the cursor isn't in a type-aware context — the
caller takes the ``None`` as the signal to fall back. Returns
``List[CompletionItem]`` (possibly empty if e.g. ``Name`` is not a
known enum) when a context IS recognised — in that case the caller
should NOT append the generic list, because the user explicitly asked
for context-specific suggestions and mixing in irrelevant entries
would defeat the point.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.base_spread_completion import (
    compute_base_spread_completions,
)
from nova_lsp.imports import FileCache, walk_imports
from nova_lsp.struct_field_completion import (
    compute_struct_field_completions,
)
from nova_lsp.type_hierarchy import (
    EnumVariant,
    KIND_ENUM,
    KIND_STRUCT,
    TypeDecl,
    scan_enum_variants,
    scan_type_declarations,
    type_decl_by_name,
)
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# ---------------------------------------------------------------------------
# LSP CompletionItemKind enum values (subset we emit).
# https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#completionItemKind
# ---------------------------------------------------------------------------

COMPLETION_KIND_FIELD = 5
COMPLETION_KIND_CLASS = 7          # struct
COMPLETION_KIND_ENUM = 13
COMPLETION_KIND_KEYWORD = 14
COMPLETION_KIND_ENUM_MEMBER = 20
COMPLETION_KIND_STRUCT = 22


# Built-in primitive type names NOVA understands at a type-annotation
# site. Mirrors `type_hierarchy._BUILTIN_TYPES` so the two modules stay
# consistent — but kept here as its own constant so the completion list
# can grow independently (e.g. add `tuple` / `bytes` without disturbing
# the hierarchy view).
_PRIMITIVE_TYPES: List[str] = [
    "any",
    "bool",
    "float",
    "int",
    "list",
    "map",
    "nil",
    "str",
]


# ---------------------------------------------------------------------------
# Trigger detection.
#
# Look at the line text up to the cursor column. We don't need the full
# document — every recognised trigger lives on the same line as the
# cursor. Returning a small enum-like string + the relevant payload
# keeps the downstream completion synthesis simple.
# ---------------------------------------------------------------------------


# Trigger names. Stored as plain strings so callers can branch on them
# without coupling to an Enum class.
TRIGGER_ENUM_VARIANT = "enum_variant"      # `Name::`
TRIGGER_FIELD_ACCESS = "field_access"      # `var.`
TRIGGER_TYPE_ANNOTATION = "type_annotation"  # `let x: ` or fn param `(p: `
TRIGGER_GENERIC_ARG = "generic_arg"        # `Box<` after a known type name
TRIGGER_NONE = "none"


@dataclass
class TriggerInfo:
    """Outcome of trigger detection.

    ``kind`` is one of the ``TRIGGER_*`` constants. ``payload`` carries
    the context the synthesiser needs:

      * TRIGGER_ENUM_VARIANT  -> enum name as a string
      * TRIGGER_FIELD_ACCESS  -> variable name as a string
      * TRIGGER_TYPE_ANNOTATION / TRIGGER_GENERIC_ARG -> empty string
    """
    kind: str
    payload: str = ""


# Identifier regex shared across the helpers. The leading word boundary
# in usage matters more than the regex itself — we always anchor matches
# at the cursor by slicing the prefix string first.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPENAME_RE = re.compile(r"[A-Z][A-Za-z0-9_]*\b\s*$")


def _strip_partial_identifier(prefix: str) -> str:
    """Drop the partial identifier the user is currently typing.

    The editor sends a completion request with the cursor positioned
    AFTER the partial token (e.g. when typing ``Option::No`` the cursor
    sits at column 10 and the line text is ``Option::No``). We strip
    the trailing identifier so the trigger lookup only sees
    ``Option::`` — the matcher doesn't need to know about the partial.
    Returns the prefix unchanged when no trailing identifier exists
    (e.g. the cursor sits immediately after ``::``).
    """
    m = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", prefix)
    if m is None:
        return prefix
    return prefix[:m.start()]


def detect_trigger(line_text: str, character: int) -> TriggerInfo:
    """Inspect ``line_text[:character]`` and classify the trigger context.

    Returns a TriggerInfo with ``kind == TRIGGER_NONE`` when the cursor
    isn't in a recognised type-aware spot."""
    if character < 0 or character > len(line_text):
        return TriggerInfo(TRIGGER_NONE)
    prefix = line_text[:character]
    stripped = _strip_partial_identifier(prefix)

    # 1. `Name::` -> enum variant lookup. The enum name is the
    # identifier IMMEDIATELY before the `::`.
    if stripped.endswith("::"):
        before = stripped[:-2]
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", before)
        if m is not None:
            enum_name = m.group(1)
            # Only honor the trigger if the name looks like a type
            # (leading uppercase). This avoids spurious triggers for
            # `self::` etc. which NOVA doesn't currently use.
            if enum_name[:1].isupper():
                return TriggerInfo(TRIGGER_ENUM_VARIANT, enum_name)

    # 2. `var.` -> struct field lookup.
    if stripped.endswith("."):
        before = stripped[:-1]
        # Skip if the prior char-chain looks like a numeric literal
        # (e.g. `1.5` is a float, not field access). Cheap check.
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", before)
        if m is not None:
            var_name = m.group(1)
            return TriggerInfo(TRIGGER_FIELD_ACCESS, var_name)

    # 3. `let x: ` -> type annotation. Match the FULL line up to here
    # against `let NAME[ : ]` so an arbitrary indent and trailing
    # whitespace after the colon still triggers.
    m = re.search(
        r"\b(?:let|const)\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*$",
        stripped,
    )
    if m is not None:
        return TriggerInfo(TRIGGER_TYPE_ANNOTATION)

    # 4. fn param `(p: ` -> same set of type names. We accept the colon
    # following any identifier inside an open parens — heuristic but
    # sufficient because NOVA fn params live on one line.
    if re.search(r"[(,]\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*$", stripped):
        return TriggerInfo(TRIGGER_TYPE_ANNOTATION)

    # 5. `Box<` -> nested generic argument. Same as the annotation case
    # — we offer type names for the inner slot.
    if stripped.endswith("<"):
        before = stripped[:-1]
        if _TYPENAME_RE.search(before):
            return TriggerInfo(TRIGGER_GENERIC_ARG)

    return TriggerInfo(TRIGGER_NONE)


# ---------------------------------------------------------------------------
# Index walker — collect every type declaration the cursor sees.
#
# We walk three sources (in priority order so duplicates de-dup):
#
#   1. The current document's text (in-buffer; unsaved edits beat disk).
#   2. The transitive import graph (R5F's `walk_imports`).
#   3. The workspace symbol index (R8C) — used for sibling .nova files
#      that aren't reachable from the current document's imports.
#
# Each entry returned by `collect_type_decls` is `(path, TypeDecl)` so
# the caller knows where the decl lives (for cross-file completion the
# `path` is the file containing the enum, not the file with the cursor).
# ---------------------------------------------------------------------------


def _collect_in_text(path: str, text: str) -> List[Tuple[str, TypeDecl]]:
    """Wrap `scan_type_declarations` in the (path, decl) shape."""
    return [(path, d) for d in scan_type_declarations(text)]


def collect_type_decls(
    uri: str,
    doc_text: str,
    file_cache: FileCache,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, TypeDecl]]:
    """Return every type declaration visible from the document at ``uri``.

    Sources walked in priority order:

      1. ``doc_text`` itself (the open buffer — wins over disk).
      2. Files reachable via ``walk_imports`` from the current document.
      3. Files from the workspace symbol index that aren't otherwise
         walked. (Used so a user typing ``: `` sees enums declared in
         sibling files even when no `import` statement has wired them
         together yet — matches the editor's expectation that the type
         picker spans the project.)

    Duplicates by ``(path, name)`` are dropped — the priority above
    ensures the in-buffer version wins if multiple sources see the same
    file."""
    path = _uri_to_path(uri)
    seen_files: Set[str] = set()
    out: List[Tuple[str, TypeDecl]] = []

    if path:
        abs_path = os.path.abspath(path)
        # In-buffer text first so unsaved edits win.
        for entry in _collect_in_text(abs_path, doc_text):
            out.append(entry)
        seen_files.add(abs_path)

        # Walk the import graph from the current file.
        overrides = dict(text_overrides or {})
        overrides.setdefault(abs_path, doc_text)
        try:
            walked = walk_imports(
                abs_path, file_cache, text_overrides=overrides
            )
        except Exception:
            walked = []
        for fe in walked:
            if fe.path == abs_path or fe.path in seen_files:
                continue
            seen_files.add(fe.path)
            out.extend(_collect_in_text(fe.path, fe.text))

    # Layer 3: workspace symbol index — files NOT yet seen. The
    # workspace index only tracks files containing fn/let symbols
    # (R8C's scope), so files with ONLY enum / struct / type
    # declarations won't surface via `all_symbols()`. We also walk
    # every crawled workspace root for sibling `.nova` files so a
    # type-only header (`types.nova` with no executable code) is
    # still reachable from any open buffer.
    if workspace_index is not None:
        candidate_paths: Set[str] = set()
        for entry in workspace_index.all_symbols():
            candidate_paths.add(entry.path)
        # Also walk crawled roots so type-only files surface.
        for root in workspace_index._crawled_roots:  # noqa: SLF001
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # Mirror workspace_symbols's prune list.
                dirnames[:] = [
                    d for d in dirnames
                    if d not in (
                        "node_modules", ".git", "__pycache__", "bin", "build"
                    )
                ]
                for fname in filenames:
                    if fname.endswith(".nova"):
                        candidate_paths.add(
                            os.path.abspath(os.path.join(dirpath, fname))
                        )
        for p in sorted(candidate_paths):
            if p in seen_files:
                continue
            seen_files.add(p)
            fe = file_cache.get(p)
            if fe is None:
                continue
            out.extend(_collect_in_text(fe.path, fe.text))

    return out


def _uri_to_path(uri: str) -> Optional[str]:
    """Local copy — type_completion is loaded by server.py so we can't
    import the helper back from there without a cycle."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return unquote(parsed.path)


# ---------------------------------------------------------------------------
# Struct field discovery.
#
# Struct fields live between the `{` and `}` of the struct body. We
# parse the same way as `document_symbols._scan_struct_fields` — but
# don't import it because that module pulls in heavier outline-
# rendering logic we don't need here. Instead we walk the body text
# directly with brace counting.
# ---------------------------------------------------------------------------


@dataclass
class _StructField:
    name: str
    type_str: str


_FIELD_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^,;/\n]+?))?\s*(?:[,;]|$)"
)


def _mask_comments_and_strings(line: str) -> str:
    """Copy of the masker used by every other LSP module. Keeps column
    offsets stable; commented / quoted text becomes spaces."""
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


def scan_struct_fields(text: str, struct_decl: TypeDecl) -> List[_StructField]:
    """Walk ``struct_decl``'s body and return one ``_StructField`` per row.

    Accepts both comma and semicolon separators (R23A allowed both)."""
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
        segment = cleaned[scan_from:] if line_no == struct_decl.line else cleaned
        # Count braces in the segment so we know when the body closes.
        for ch in segment:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth <= 0:
                    return out
        if depth >= 1 and segment.strip():
            # Allow multiple comma/semicolon-separated fields per line.
            for raw_part in re.split(r"[,;]", segment):
                if not raw_part.strip():
                    continue
                m = _FIELD_RE.match(raw_part)
                if not m:
                    continue
                out.append(_StructField(
                    name=m.group(1),
                    type_str=(m.group(2) or "").strip(),
                ))
    return out


# ---------------------------------------------------------------------------
# Variable -> type-name lookup.
#
# Given a variable identifier in scope at the cursor, walk back through
# the current document text to find its declaration line. Recognises:
#
#   * `let var: Type = ...`        (R23A annotation form)
#   * `let var: Type<...> = ...`   (generic annotation)
#   * `let var = TypeName(...)`    (constructor inference)
#   * `fn foo(var: Type, ...)`     (parameter annotation)
#
# Returns the type-name string (without generics) or ``None`` when no
# annotation / constructor hint is visible.
# ---------------------------------------------------------------------------


_LET_ANNOT_RE = re.compile(
    r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Za-z0-9_]*)"
)
_LET_INFER_RE = re.compile(
    r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Z][A-Za-z0-9_]*)\s*\("
)
_FN_PARAM_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Za-z0-9_]*)"
)


def variable_type_name(text: str, var_name: str) -> Optional[str]:
    """Return the declared / inferred type-name for ``var_name`` in
    ``text``, or ``None`` if no annotation / constructor hint is found.

    The lookup is purely textual — we walk every line, score the matches
    by precedence (annotation > constructor > param), and return the
    first hit. Names with type-name suffixes like ``Box<int>`` resolve
    to the base ``Box`` so downstream lookup against the struct
    declaration table works regardless of generic arity."""
    annotation_hit: Optional[str] = None
    inference_hit: Optional[str] = None
    param_hit: Optional[str] = None
    for line in text.splitlines():
        masked = _mask_comments_and_strings(line)
        # Annotation form first — best signal.
        for m in _LET_ANNOT_RE.finditer(masked):
            if m.group(1) == var_name and annotation_hit is None:
                annotation_hit = m.group(2)
        # Constructor inference next.
        for m in _LET_INFER_RE.finditer(masked):
            if m.group(1) == var_name and inference_hit is None:
                inference_hit = m.group(2)
        # Param annotation — has to look like an fn arg position. The
        # regex would also match a struct field row, so we require the
        # match to live INSIDE parens on the same line.
        if "(" in masked and param_hit is None:
            inside = masked[masked.find("(") + 1:]
            for m in _FN_PARAM_RE.finditer(inside):
                if m.group(1) == var_name:
                    param_hit = m.group(2)
                    break

    return annotation_hit or inference_hit or param_hit


# ---------------------------------------------------------------------------
# Completion item synthesis.
# ---------------------------------------------------------------------------


def _make_variant_item(
    variant: EnumVariant, enum_name: str
) -> Dict[str, Any]:
    """CompletionItem for one enum variant."""
    payload = ""
    if variant.arity > 0:
        payload = "(" + ", ".join(["_"] * variant.arity) + ")"
    detail = f"{enum_name}::{variant.name}{payload}"
    return {
        "label": variant.name,
        "kind": COMPLETION_KIND_ENUM_MEMBER,
        "detail": detail,
        "documentation": {
            "kind": "markdown",
            "value": f"Variant of `enum {enum_name}`",
        },
    }


def _make_field_item(field: _StructField, struct_name: str) -> Dict[str, Any]:
    """CompletionItem for one struct field."""
    detail = f"{field.name}: {field.type_str}" if field.type_str else field.name
    return {
        "label": field.name,
        "kind": COMPLETION_KIND_FIELD,
        "detail": detail,
        "documentation": {
            "kind": "markdown",
            "value": f"Field of `struct {struct_name}`",
        },
    }


def _make_type_item(decl: TypeDecl) -> Dict[str, Any]:
    """CompletionItem for an enum / struct / type alias name."""
    if decl.kind == KIND_ENUM:
        kind_label = "enum"
        kind_num = COMPLETION_KIND_ENUM
    elif decl.kind == KIND_STRUCT:
        kind_label = "struct"
        kind_num = COMPLETION_KIND_STRUCT
    else:
        kind_label = "type"
        kind_num = COMPLETION_KIND_CLASS
    detail = f"{kind_label} {decl.name}"
    if decl.kind == "type" and decl.alias_rhs:
        detail = f"type {decl.name} = {decl.alias_rhs}"
    return {
        "label": decl.name,
        "kind": kind_num,
        "detail": detail,
        "documentation": {
            "kind": "markdown",
            "value": f"`{detail}`",
        },
    }


def _make_primitive_item(name: str) -> Dict[str, Any]:
    """CompletionItem for a builtin primitive type."""
    return {
        "label": name,
        "kind": COMPLETION_KIND_KEYWORD,
        "detail": f"(builtin) {name}",
        "documentation": {
            "kind": "markdown",
            "value": f"NOVA built-in primitive type `{name}`",
        },
    }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def compute_type_aware_completions(
    uri: str,
    position: Dict[str, int],
    doc_text: str,
    file_cache: FileCache,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Return a context-specific completion list for the cursor at
    ``position``, or ``None`` when no type-aware context applies.

    A return value of ``None`` is the explicit signal to fall back to
    the generic text-based completion list. An empty list is returned
    when the trigger WAS recognised but no candidates exist (e.g.
    ``Foo::`` where ``Foo`` isn't a known enum — we don't pollute the
    result with arbitrary symbols)."""
    line_no = int(position.get("line", 0))
    character = int(position.get("character", 0))
    lines = doc_text.splitlines()
    # ``len(lines)`` is a legal cursor line for an empty trailing line.
    if not (0 <= line_no <= len(lines)):
        return None

    decls = collect_type_decls(
        uri, doc_text, file_cache,
        workspace_index=workspace_index,
        text_overrides=text_overrides,
    )

    # R26A.2: base-spread completion runs FIRST inside the brace-init
    # dispatch since the cursor after ``Point { ..|`` would ALSO match
    # R26D's `is_brace_init_context` (which only checks for being inside
    # a brace body). The spread position needs the in-scope value list,
    # not the remaining field list.
    base_spread = compute_base_spread_completions(
        uri, position, doc_text, decls
    )
    if base_spread is not None:
        return base_spread

    # R26D: brace-init field completion runs next since `Point { x:
    # 10, |` is multi-line capable -- the regular line-local triggers
    # (`Name::`, `var.`, etc.) wouldn't see anything to fire on after
    # the user presses Enter inside the brace body.
    brace_init = compute_struct_field_completions(
        uri, position, doc_text, decls
    )
    if brace_init is not None:
        return brace_init

    # Line-local triggers operate on the current line text.
    if line_no >= len(lines):
        return None
    line_text = lines[line_no]
    info = detect_trigger(line_text, character)
    if info.kind == TRIGGER_NONE:
        return None

    if info.kind == TRIGGER_ENUM_VARIANT:
        return _variant_completions(info.payload, decls)
    if info.kind == TRIGGER_FIELD_ACCESS:
        return _field_completions(info.payload, doc_text, decls)
    if info.kind in (TRIGGER_TYPE_ANNOTATION, TRIGGER_GENERIC_ARG):
        return _type_name_completions(decls)
    return None


def _variant_completions(
    enum_name: str,
    decls: List[Tuple[str, TypeDecl]],
) -> List[Dict[str, Any]]:
    """Look up enum ``enum_name`` in the gathered decls and return its
    variants. Returns an empty list if no enum by that name exists —
    the caller treats this as "trigger was recognised but list is
    empty" so the generic suggestions are NOT mixed in."""
    # The same enum name may be declared in multiple files (e.g. test
    # fixtures shadowing each other). We collect every match and
    # deduplicate variants by name — first occurrence wins.
    matching: List[Tuple[str, TypeDecl]] = []
    for path, d in decls:
        if d.kind == KIND_ENUM and d.name == enum_name:
            matching.append((path, d))
    if not matching:
        return []
    seen_names: Set[str] = set()
    items: List[Dict[str, Any]] = []
    for path, decl in matching:
        # We need the file text to scan variants. Try the file_cache,
        # but fall back to reading from disk if needed.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        variants = scan_enum_variants(text, decl) if text else []
        for v in variants:
            if v.name in seen_names:
                continue
            seen_names.add(v.name)
            items.append(_make_variant_item(v, enum_name))
    return items


def _field_completions(
    var_name: str,
    doc_text: str,
    decls: List[Tuple[str, TypeDecl]],
) -> List[Dict[str, Any]]:
    """Resolve ``var_name`` to its type via the textual annotation /
    constructor lookup, then return the struct's fields."""
    type_name = variable_type_name(doc_text, var_name)
    if not type_name:
        return []
    # Find a struct decl matching that type name.
    for path, decl in decls:
        if decl.kind == KIND_STRUCT and decl.name == type_name:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                text = doc_text
            fields = scan_struct_fields(text, decl)
            return [_make_field_item(f, type_name) for f in fields]
    return []


def _type_name_completions(
    decls: List[Tuple[str, TypeDecl]],
) -> List[Dict[str, Any]]:
    """Return enum + struct + type-alias names plus the built-in
    primitives. Sorted by kind (user-declared first, builtins last) and
    deduplicated by name."""
    seen: Set[str] = set()
    items: List[Dict[str, Any]] = []
    for _path, d in decls:
        if d.name in seen:
            continue
        seen.add(d.name)
        items.append(_make_type_item(d))
    # Sort the user-declared entries by name so the list is stable
    # across runs (helps test assertions, helps user muscle memory).
    items.sort(key=lambda it: it["label"])
    # Append primitives last — they're always available, listed
    # alphabetically. Skip any name that collides with a user decl.
    for prim in _PRIMITIVE_TYPES:
        if prim in seen:
            continue
        seen.add(prim)
        items.append(_make_primitive_item(prim))
    return items
