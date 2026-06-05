"""R38E completion enrichment: keywords + snippets + scope-aware identifiers.

The previous text-based completion handler in ``server.py`` returns
every builtin + top-level ``fn`` / ``let`` reachable from the open
buffer. That's strictly more helpful than nothing but doesn't match
modern-IDE expectations:

  * No keyword suggestions (``fn``, ``let``, ``if`` — the bedrock of
    the language is invisible until the user starts typing).
  * No snippets (one-key insertion of a ``match`` skeleton with
    placeholder cursors is table stakes for any LSP).
  * No scope awareness — the "in-scope identifiers at this cursor"
    set is the full file's union of fns + lets, not the bindings
    actually visible at the position.
  * No literal-friendly handling for built-in primitive types
    (``int`` / ``str`` / ``bool`` / ``list`` / ``float``) at the
    snippet level.

This module layers ALL of those on top of R24E's type-aware completion
layer + the legacy text-based fallback. The wiring (see
``server.handle_text_document_completion``) calls
``compute_r38e_completions`` AFTER the type-aware layer has had a
chance to preempt (so ``Name::``, ``var.``, ``let x: `` still produce
focused lists) but BEFORE the generic builtin-flood is appended (so
keywords + snippets + scope-aware identifiers are mixed into the
returned list).

What's covered:

  * **Keywords** — the 13 NOVA control-flow + decl keywords. Always
    included; the editor filters against the typed prefix.
  * **Built-in primitive types** — ``int`` / ``str`` / ``bool`` /
    ``list`` / ``float`` / ``map`` / ``any`` / ``nil`` (R38A adds
    float; the rest predate this round).
  * **Snippets** — 8 multi-placeholder skeletons (``fn``, ``let``,
    ``if``, ``match``, ``while``, ``import``, ``enum``, ``closure``).
    Each ships with VS-Code-style ``${N:placeholder}`` markers so the
    editor can tab through the slots.
  * **In-scope identifiers** — R36D's ``ScopeIndex.visible_at(line,
    col)`` returns the bindings actually visible at the cursor. We
    enrich each with the matching signature from the document's
    ``fn`` / ``let`` scan so the completion item carries a useful
    ``detail`` (``"fn greet(name)"`` rather than just ``"greet"``).
  * **Imported-module fn names** — every ``import "path/file.nova"``
    directive resolves to a sibling file; we scan that file's top-
    level ``fn`` declarations and surface them as Function items.
    The doc comment above the declaration (``///`` block, R3's hover
    convention) populates the ``documentation`` field when present.
  * **Stdlib functions** — the R37E list combinators
    (``list_map`` / ``list_filter`` / ...) hardcoded so completion
    works even when the user hasn't explicitly imported
    ``src/stdlib/list.nova``.

What's *deferred*:

  * **Member access ``.field``** — the type-aware layer in R24E
    already handles ``var.`` for known-struct variables. R38E
    deliberately does NOT extend this to "guess the receiver's type
    from arbitrary expressions" — that needs a type inferencer the
    LSP doesn't currently own. Returns the existing struct-field
    list from R24E when the trigger fires; otherwise empty for
    member access.
  * **Smart fuzzy ranking** — completion items are returned in a
    stable lexicographic order. The editor does the fuzzy filtering
    against the user's prefix.

Public entry point:

  * :func:`compute_r38e_completions` — given the document text + the
    cursor position + the import-resolution callback, return the
    list of CompletionItem dicts to layer onto the existing
    fallback.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from nova_lsp.hover_docs import extract_doc_comment_from_text
from nova_lsp.scope_index import (
    SCOPE_BLOCK,
    SCOPE_FILE,
    SCOPE_FN,
    build_scope_index,
)


# ---------------------------------------------------------------------------
# LSP CompletionItemKind enum values (subset emitted by R38E).
# https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#completionItemKind
# ---------------------------------------------------------------------------

KIND_FUNCTION = 3
KIND_FIELD = 5
KIND_VARIABLE = 6
KIND_CLASS = 7         # struct
KIND_MODULE = 9
KIND_PROPERTY = 10
KIND_KEYWORD = 14
KIND_SNIPPET = 15

# CompletionItem.insertTextFormat: 1 = PlainText, 2 = Snippet.
# Snippets use the LSP standard ${N:placeholder} syntax.
INSERT_TEXT_PLAIN = 1
INSERT_TEXT_SNIPPET = 2


# ---------------------------------------------------------------------------
# Keywords + built-in primitive type names.
# ---------------------------------------------------------------------------


# Reserved NOVA keywords. Order is the order surfaced in the
# completion list — control-flow first, then declarations, then
# expression-keywords. The editor filters by typed prefix so order
# matters only for tiebreaks.
NOVA_KEYWORDS: List[str] = [
    "fn",
    "let",
    "if",
    "else",
    "while",
    "for",
    "match",
    "return",
    "import",
    "enum",
    "struct",
    "true",
    "false",
    "break",
    "continue",
    "nil",
    "and",
    "or",
    "not",
    "in",
]


# Primitive type names recognised at annotation sites + as standalone
# identifiers in expression position. Mirrors
# ``type_completion._PRIMITIVE_TYPES`` so the two stay aligned.
NOVA_PRIMITIVE_TYPES: List[str] = [
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
# Stdlib functions — R37E list combinators.
#
# Hardcoded so the completion list includes them even when the user
# hasn't imported `src/stdlib/list.nova` yet. The signatures mirror
# the actual fn declarations in that file.
# ---------------------------------------------------------------------------


NOVA_STDLIB_FUNCTIONS: Dict[str, str] = {
    "list_map":       "fn list_map(lst, f)",
    "list_filter":    "fn list_filter(lst, pred)",
    "list_fold":      "fn list_fold(lst, init, f)",
    "list_take":      "fn list_take(lst, n)",
    "list_drop":      "fn list_drop(lst, n)",
    "list_concat":    "fn list_concat(a, b)",
    "list_zip":       "fn list_zip(a, b)",
    "list_enumerate": "fn list_enumerate(lst)",
    "list_find":      "fn list_find(lst, pred)",
    "list_any":       "fn list_any(lst, pred)",
    "list_all":       "fn list_all(lst, pred)",
    "list_reverse":   "fn list_reverse(lst)",
    "list_sum":       "fn list_sum(lst)",
}


# ---------------------------------------------------------------------------
# Snippets — multi-placeholder skeletons.
#
# Each snippet emits a single CompletionItem with `insertTextFormat: 2`
# (Snippet) so the editor processes the ${N:placeholder} markers as
# tab stops. The ``label`` matches the keyword the user is most likely
# to type so prefix-matching surfaces the snippet next to the bare
# keyword item.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snippet:
    """A snippet skeleton for a NOVA construct."""
    label: str
    insert_text: str
    detail: str
    documentation: str = ""


NOVA_SNIPPETS: List[Snippet] = [
    Snippet(
        label="fn",
        insert_text="fn ${1:name}(${2:params}) {\n\t${3:body}\n}",
        detail="snippet: fn declaration",
        documentation="Insert a function declaration with body placeholder.",
    ),
    Snippet(
        label="let",
        insert_text="let ${1:name} = ${2:value}",
        detail="snippet: let binding",
        documentation="Insert a let binding.",
    ),
    Snippet(
        label="if",
        insert_text="if ${1:cond} {\n\t${2:body}\n}",
        detail="snippet: if block",
        documentation="Insert an if block.",
    ),
    Snippet(
        label="match",
        insert_text=(
            "match ${1:expr} {\n"
            "\t${2:pat} => ${3:result},\n"
            "\t_ => ${4:default}\n"
            "}"
        ),
        detail="snippet: match expression",
        documentation="Insert a match expression with two arms and wildcard.",
    ),
    Snippet(
        label="while",
        insert_text="while ${1:cond} {\n\t${2:body}\n}",
        detail="snippet: while loop",
        documentation="Insert a while loop.",
    ),
    Snippet(
        label="import",
        insert_text='import "${1:path}"',
        detail="snippet: import directive",
        documentation="Insert an import directive.",
    ),
    Snippet(
        label="enum",
        insert_text=(
            "enum ${1:Name} {\n"
            "\t${2:Variant1},\n"
            "\t${3:Variant2}\n"
            "}"
        ),
        detail="snippet: enum declaration",
        documentation="Insert an enum declaration with two variants.",
    ),
    Snippet(
        label="closure",
        insert_text="|${1:x}| ${2:expr}",
        detail="snippet: closure literal",
        documentation="Insert a closure literal.",
    ),
]


# ---------------------------------------------------------------------------
# Top-level construct keywords vs. expression-context keywords.
#
# At the start of a line / after `;` / after `\n` (top-level position)
# only declaration-shape keywords + import make sense. Inside an
# expression (after `let X = `, function args, etc.) the literal
# keywords (`true`, `false`, `nil`, `not`) + control-flow heads make
# sense. We use this to mildly rank suggestions — both categories are
# always returned, just with the top-level set first when the cursor
# looks like it's at top level.
# ---------------------------------------------------------------------------


_TOP_LEVEL_KEYWORDS: Set[str] = {
    "fn", "let", "import", "enum", "struct",
}


_EXPRESSION_KEYWORDS: Set[str] = {
    "true", "false", "nil", "not", "and", "or", "in",
    "if", "match", "while", "for", "return", "break", "continue",
}


# ---------------------------------------------------------------------------
# Context detection.
# ---------------------------------------------------------------------------


# Regex describing a "line is empty / starts with whitespace then a
# top-level construct keyword" — used to decide whether the cursor is
# at a top-level position.
_LINE_TOP_LEVEL_RE = re.compile(r"^\s*$")


@dataclass
class CompletionContext:
    """Lightweight context for the cursor position.

    ``at_top_level`` — line is blank or only whitespace before the
        cursor AND the cursor's enclosing scope is the FILE scope.
    ``after_let_rhs`` — line text up to the cursor matches
        ``let NAME = `` so we expect an expression to follow.
    ``after_member_access`` — the character immediately before the
        cursor is ``.`` (struct-field / list-method position).
    ``in_match_arm`` — preceding lines contain an open ``match`` block
        whose closing brace hasn't been seen yet.
    ``in_import_path`` — the cursor sits between quotes after the
        ``import`` keyword (path-suggestion territory; we don't try
        to enumerate filesystem paths but flag the context so the
        caller can decide what to surface).
    """
    line_text: str
    character: int
    at_top_level: bool = False
    after_let_rhs: bool = False
    after_member_access: bool = False
    in_match_arm: bool = False
    in_import_path: bool = False


def detect_context(
    doc_text: str, line: int, character: int
) -> CompletionContext:
    """Build a CompletionContext for the cursor in ``doc_text``.

    Pure text analysis — uses the source line up to the cursor + the
    handful of preceding lines to decide each context flag. None of
    the heuristics requires the scope index, so callers can use this
    function standalone (the tests do).
    """
    lines = doc_text.splitlines()
    if line < 0:
        return CompletionContext(line_text="", character=character)
    if line >= len(lines):
        # Empty document or cursor on a trailing-newline-only line — the
        # cursor is at top level with no preceding line text.
        ctx = CompletionContext(line_text="", character=character)
        ctx.at_top_level = True
        return ctx
    line_text = lines[line]
    if character < 0:
        character = 0
    if character > len(line_text):
        character = len(line_text)
    prefix = line_text[:character]
    ctx = CompletionContext(line_text=line_text, character=character)

    # at_top_level: line is empty + cursor in FILE scope. The scope-
    # scope check is the caller's job; here we just signal "the line
    # itself doesn't have anything before the cursor". The caller
    # ANDs this with the scope-index lookup.
    if _LINE_TOP_LEVEL_RE.match(prefix):
        ctx.at_top_level = True

    # after_let_rhs: ``let NAME = `` pattern. The trailing space is
    # captured by the regex (``\s+`` after the ``=``) to require the
    # cursor to be in expression position.
    if re.search(r"\blet\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*", prefix):
        # ``let X = foo`` — the cursor is past the ``=`` boundary so
        # we're in expression position even if there's already a
        # partial identifier.
        ctx.after_let_rhs = True

    # after_member_access: previous non-whitespace char is ``.``. We
    # strip the trailing identifier (if any) so ``var.fi`` still
    # registers as member-access on ``var``.
    stripped = re.sub(r"[A-Za-z_][A-Za-z0-9_]*$", "", prefix)
    if stripped.endswith("."):
        ctx.after_member_access = True

    # in_import_path: ``import "..."`` pattern. The cursor sits
    # between the quotes.
    if re.search(r'^\s*import\s+"[^"]*$', prefix):
        ctx.in_import_path = True

    # in_match_arm: walk backwards from the current line looking for
    # an open ``match ... {`` whose closing brace hasn't been seen.
    # Cheap heuristic — counts braces ignoring strings + comments.
    depth = 0
    in_match = False
    for prev_line in reversed(lines[: line + 1]):
        # Track brace balance from the cursor's line going backward.
        for ch in reversed(prev_line):
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    # The brace that opened the current statement.
                    # Check if its head contains "match ".
                    if "match " in prev_line:
                        in_match = True
                    break
                depth -= 1
        if in_match:
            break
        # Stop walking if we hit an unbalanced ``}`` (we exited the
        # outer scope before finding a match).
        if depth < 0:
            break
    ctx.in_match_arm = in_match

    return ctx


# ---------------------------------------------------------------------------
# Document scan — extract fn / let / enum / struct declarations.
#
# We need the SIGNATURE for each top-level fn so completion items
# carry useful ``detail`` text. Cheap regex scan over the document
# text (and imported-file text).
# ---------------------------------------------------------------------------


# Reuses the convention from server.py's symbol scan.
_FN_DECL_RE = re.compile(
    r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)
_LET_DECL_RE = re.compile(
    r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.+))?$"
)
_ENUM_DECL_RE = re.compile(r"^\s*enum\s+([A-Za-z_][A-Za-z0-9_]*)")
_STRUCT_DECL_RE = re.compile(r"^\s*struct\s+([A-Za-z_][A-Za-z0-9_]*)")
_IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')


@dataclass
class FnDecl:
    """One top-level fn declaration in a NOVA source file."""
    name: str
    params: str           # raw param list, e.g. "x, y, z"
    line: int             # zero-based decl line
    signature: str        # "fn name(params)"


@dataclass
class LetDecl:
    """One top-level let declaration in a NOVA source file."""
    name: str
    rhs: str              # raw rhs after `=`; empty if no initializer
    line: int
    signature: str        # "let name" or "let name = rhs"


@dataclass
class EnumDecl:
    """One top-level enum declaration."""
    name: str
    line: int


@dataclass
class StructDecl:
    """One top-level struct declaration."""
    name: str
    line: int


@dataclass
class DocScan:
    """Result of scanning a NOVA document for declarations."""
    fns: List[FnDecl] = field(default_factory=list)
    lets: List[LetDecl] = field(default_factory=list)
    enums: List[EnumDecl] = field(default_factory=list)
    structs: List[StructDecl] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)


def scan_document(text: str) -> DocScan:
    """Cheap regex scan of ``text`` for top-level declarations.

    Returns the declarations grouped by kind so the completion
    handler can attach the right ``CompletionItemKind`` to each.
    Comments + strings aren't masked here because we only match on
    line-prefix patterns (``^\\s*fn\\s+...``) which don't fire from
    inside a multi-line string literal that begins on a previous
    line. False positives are acceptable for completion — the worst
    case is an extra item the editor filters away.
    """
    scan = DocScan()
    for lineno, line in enumerate(text.splitlines()):
        m_fn = _FN_DECL_RE.match(line)
        if m_fn:
            name = m_fn.group(1)
            params = m_fn.group(2).strip()
            sig = f"fn {name}({params})"
            scan.fns.append(FnDecl(name=name, params=params, line=lineno, signature=sig))
            continue
        m_let = _LET_DECL_RE.match(line)
        if m_let:
            name = m_let.group(1)
            rhs = (m_let.group(2) or "").strip()
            sig = f"let {name}" + (f" = {rhs}" if rhs else "")
            scan.lets.append(LetDecl(name=name, rhs=rhs, line=lineno, signature=sig.rstrip()))
            continue
        m_enum = _ENUM_DECL_RE.match(line)
        if m_enum:
            scan.enums.append(EnumDecl(name=m_enum.group(1), line=lineno))
            continue
        m_struct = _STRUCT_DECL_RE.match(line)
        if m_struct:
            scan.structs.append(StructDecl(name=m_struct.group(1), line=lineno))
            continue
        m_imp = _IMPORT_RE.match(line)
        if m_imp:
            scan.imports.append(m_imp.group(1))
            continue
    return scan


# ---------------------------------------------------------------------------
# Imported-module fn enumeration.
# ---------------------------------------------------------------------------


def collect_imported_fns(
    doc_text: str,
    doc_path: Optional[str],
    base_dir: Optional[str] = None,
) -> List[Tuple[FnDecl, str, str]]:
    """For every ``import "..."`` in ``doc_text``, scan the imported
    file's top-level ``fn`` declarations and return them.

    Returns ``(FnDecl, source_path, doc_comment)`` triples. The doc
    comment is the ``///`` block immediately above the declaration
    (empty when absent) and is surfaced in the CompletionItem's
    ``documentation`` field.

    Files that can't be read (missing / permission error) are
    silently skipped. We never raise from completion.
    """
    out: List[Tuple[FnDecl, str, str]] = []
    if doc_path is None and base_dir is None:
        return out
    if base_dir is None:
        assert doc_path is not None
        base_dir = os.path.dirname(doc_path)
    seen: Set[str] = set()
    for raw_line in doc_text.splitlines():
        m = _IMPORT_RE.match(raw_line)
        if not m:
            continue
        rel = m.group(1)
        path = rel if os.path.isabs(rel) else os.path.normpath(
            os.path.join(base_dir or ".", rel)
        )
        try:
            abs_path = os.path.abspath(path)
        except (OSError, ValueError):
            continue
        if abs_path in seen:
            continue
        seen.add(abs_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        sub_scan = scan_document(text)
        for fn in sub_scan.fns:
            docs = extract_doc_comment_from_text(text, fn.line)
            out.append((fn, abs_path, docs))
    return out


# ---------------------------------------------------------------------------
# Item construction.
# ---------------------------------------------------------------------------


def _item_keyword(label: str) -> Dict[str, Any]:
    """Build a CompletionItem for a NOVA keyword."""
    return {
        "label": label,
        "kind": KIND_KEYWORD,
        "detail": "keyword",
        "insertText": label,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_primitive_type(label: str) -> Dict[str, Any]:
    """Build a CompletionItem for a built-in primitive type name."""
    return {
        "label": label,
        "kind": KIND_CLASS,
        "detail": "primitive type",
        "insertText": label,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_snippet(snip: Snippet) -> Dict[str, Any]:
    """Build a CompletionItem from a Snippet skeleton."""
    return {
        "label": snip.label,
        "kind": KIND_SNIPPET,
        "detail": snip.detail,
        "documentation": {"kind": "markdown", "value": snip.documentation},
        "insertText": snip.insert_text,
        "insertTextFormat": INSERT_TEXT_SNIPPET,
    }


def _item_scope_fn(name: str, signature: str) -> Dict[str, Any]:
    """Build a CompletionItem for an in-scope fn declaration."""
    return {
        "label": name,
        "kind": KIND_FUNCTION,
        "detail": signature,
        "insertText": name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_scope_var(name: str, signature: str) -> Dict[str, Any]:
    """Build a CompletionItem for an in-scope let / parameter."""
    return {
        "label": name,
        "kind": KIND_VARIABLE,
        "detail": signature,
        "insertText": name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_imported_fn(
    fn: FnDecl, source_path: str, docs: str
) -> Dict[str, Any]:
    """Build a CompletionItem for a fn imported from another file."""
    rel = os.path.basename(source_path)
    item: Dict[str, Any] = {
        "label": fn.name,
        "kind": KIND_FUNCTION,
        "detail": f"{fn.signature}  (from {rel})",
        "insertText": fn.name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }
    if docs:
        item["documentation"] = {"kind": "markdown", "value": docs}
    return item


def _item_stdlib_fn(name: str, signature: str) -> Dict[str, Any]:
    """Build a CompletionItem for a R37E stdlib combinator."""
    return {
        "label": name,
        "kind": KIND_FUNCTION,
        "detail": f"{signature}  (stdlib)",
        "documentation": {
            "kind": "markdown",
            "value": f"NOVA stdlib (R37E): `{signature}`",
        },
        "insertText": name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_enum(name: str) -> Dict[str, Any]:
    """Build a CompletionItem for a user-declared enum."""
    return {
        "label": name,
        "kind": 13,  # CompletionItemKind.Enum
        "detail": f"enum {name}",
        "insertText": name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


def _item_struct(name: str) -> Dict[str, Any]:
    """Build a CompletionItem for a user-declared struct."""
    return {
        "label": name,
        "kind": KIND_CLASS,
        "detail": f"struct {name}",
        "insertText": name,
        "insertTextFormat": INSERT_TEXT_PLAIN,
    }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def compute_r38e_completions(
    *,
    uri: str,
    doc_text: str,
    doc_path: Optional[str],
    position: Dict[str, int],
    context: Optional[CompletionContext] = None,
) -> List[Dict[str, Any]]:
    """Build the R38E completion item list for the cursor at
    ``position``.

    ``uri`` carries the document URI for diagnostics.
    ``doc_text`` is the open buffer's current text.
    ``doc_path`` is the abs filesystem path of the buffer (used to
    resolve ``import`` directives against the filesystem). When the
    buffer isn't yet saved (`untitled:` scheme etc.) pass ``None``.
    ``position`` is the LSP position dict ``{"line": ..., "character": ...}``.
    ``context`` is an optional pre-computed CompletionContext —
    callers can build their own (the test harness does) or let this
    function build one from the document.

    Returns a list of CompletionItem dicts. The list is NOT
    de-duplicated against the legacy completion list — the caller is
    responsible for merging.
    """
    line = int(position.get("line", 0) or 0)
    character = int(position.get("character", 0) or 0)

    if context is None:
        context = detect_context(doc_text, line, character)

    items: List[Dict[str, Any]] = []

    # 1. Keywords + primitive types — always surfaced. Editor filters
    # by prefix, but the list is deterministic so item order is
    # stable across edits (helps the completion-rank tests).
    for kw in NOVA_KEYWORDS:
        items.append(_item_keyword(kw))
    for ty in NOVA_PRIMITIVE_TYPES:
        items.append(_item_primitive_type(ty))

    # 2. Snippets — 8 multi-placeholder skeletons. Each snippet has a
    # bare-keyword counterpart but the editor distinguishes them via
    # the `kind` field (Snippet vs Keyword) so both can coexist.
    for snip in NOVA_SNIPPETS:
        items.append(_item_snippet(snip))

    # 3. Stdlib combinators (R37E). Always offered; the user may not
    # have imported `src/stdlib/list.nova` yet but the suggestion
    # alone surfaces the available API.
    for name in sorted(NOVA_STDLIB_FUNCTIONS):
        items.append(_item_stdlib_fn(name, NOVA_STDLIB_FUNCTIONS[name]))

    # 4. In-scope identifiers from the current document.
    # We build the scope index over the buffer text + use the
    # extended `visible_at` API to enumerate every binding visible
    # at the cursor. Each binding is enriched with the matching
    # signature from `scan_document` so the completion items carry
    # the full "fn foo(a, b)" / "let X = 42" detail.
    scan = scan_document(doc_text)
    fn_by_name: Dict[str, FnDecl] = {f.name: f for f in scan.fns}
    let_by_name: Dict[str, LetDecl] = {l.name: l for l in scan.lets}
    enum_by_name: Dict[str, EnumDecl] = {e.name: e for e in scan.enums}
    struct_by_name: Dict[str, StructDecl] = {s.name: s for s in scan.structs}

    try:
        index = build_scope_index(doc_text)
        visible = index.visible_at(line, character)
    except Exception:
        visible = []

    in_scope_names: Set[str] = set()
    for name, scope_kind, _decl_line in visible:
        if name in in_scope_names:
            continue
        in_scope_names.add(name)
        if name in fn_by_name:
            items.append(_item_scope_fn(name, fn_by_name[name].signature))
        elif name in let_by_name:
            items.append(_item_scope_var(name, let_by_name[name].signature))
        else:
            # Fn parameter or block-local let — no top-level signature.
            label_detail = "parameter" if scope_kind == SCOPE_FN else "local"
            items.append(
                {
                    "label": name,
                    "kind": KIND_VARIABLE,
                    "detail": label_detail,
                    "insertText": name,
                    "insertTextFormat": INSERT_TEXT_PLAIN,
                }
            )

    # 5. Top-level enum + struct declarations from the current file.
    for enum in scan.enums:
        items.append(_item_enum(enum.name))
    for struct in scan.structs:
        items.append(_item_struct(struct.name))

    # 6. Imported-module fns. We resolve each `import "path"`
    # directive against the current file's directory + scan the
    # imported file's top-level `fn` declarations.
    base_dir = os.path.dirname(doc_path) if doc_path else None
    for fn, source_path, docs in collect_imported_fns(
        doc_text, doc_path, base_dir
    ):
        if fn.name in in_scope_names:
            # Already surfaced via the current-file scan; skip to
            # avoid duplicate entries.
            continue
        items.append(_item_imported_fn(fn, source_path, docs))

    return items


# ---------------------------------------------------------------------------
# Helpers exposed for tests.
# ---------------------------------------------------------------------------


def fuzzy_match(prefix: str, label: str) -> bool:
    """Case-insensitive subsequence match — used by the test harness
    to simulate the editor's filtering behaviour.

    Returns True when every character of ``prefix`` appears in
    ``label`` in order. Empty prefix matches everything.
    """
    if not prefix:
        return True
    p = prefix.lower()
    l = label.lower()
    i = 0
    for ch in l:
        if i < len(p) and ch == p[i]:
            i += 1
        if i == len(p):
            return True
    return i == len(p)


def prefix_match(prefix: str, label: str) -> bool:
    """Case-insensitive prefix match — stricter than ``fuzzy_match``.

    Used in tests to assert the higher-priority "starts with" rank.
    """
    if not prefix:
        return True
    return label.lower().startswith(prefix.lower())
