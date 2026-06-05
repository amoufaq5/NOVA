"""Base-spread completion for `textDocument/completion`.

R26A (commit ``1f399a8``) shipped struct update-syntax::

    let p = Point { x: 1, y: 2 }
    let p2 = Point { x: 10, ..p }     // ``..p`` copies y from p

R26D (commit ``7932060``) added field completion when the cursor sits
INSIDE a brace-init body at a field position (``Point { |`` ->
``[x, y]``). The R26A.2 follow-up handled here covers the COMPLEMENTARY
position: cursor immediately after ``..`` inside a brace-init body.

    Point { ..|                       -> suggest every in-scope value
                                         whose type is ``Point``
    Point { x: 10, ..|                -> same set; the override-list
                                         is irrelevant to the base
                                         candidate set

Conceptually similar to R24E's ``var.`` field completion but in
reverse: there we know a value name and look up its type, here we
know a type name and look up every value of that type currently in
scope at the cursor.

Trigger detection:

  1. Walk backwards from the cursor through the masked document text
     to find a ``Name {`` at relative depth zero (re-using R26D's
     ``is_brace_init_context`` machinery).
  2. Confirm the cursor sits IMMEDIATELY after a ``..`` token at the
     outermost (depth-zero) level of the brace body — not inside a
     nested ``Inner { ..q }``, not after a ``..`` that's part of a
     range literal in some future grammar extension.

Scope walk:

  1. Find the enclosing function declaration line for the cursor
     position (the most recent ``fn NAME(..)`` whose body brace is
     still open at the cursor). If the cursor isn't inside an fn body
     we fall back to top-level declarations — a top-level
     ``let p = Point { ... }`` is just as accessible as a per-fn one
     when the spread happens at top level.
  2. Walk every line of the fn body (or the top-level scope) and
     match either of:
       * ``let NAME : TYPE = ...``      (explicit annotation)
       * ``let NAME = TYPE(...)``       (constructor-style ctor call)
       * ``let NAME = TYPE { ... }``    (brace-init form)
       * fn parameter ``(NAME: TYPE, ...)`` if NAME's type is TYPE
  3. Strip generic suffixes (``Point<int>`` -> ``Point``) so a
     ``let pi: Point<int> = ...`` is offered for ``Point { ..|``.

Public API (called from
``type_completion.compute_type_aware_completions``)::

    is_base_spread_context(uri, position, doc_text)
        -> _BaseSpreadContext | None

    find_in_scope_struct_values(uri, position, struct_name,
                                 doc_text, decls)
        -> list[CompletionItem]

    compute_base_spread_completions(uri, position, doc_text, decls)
        -> list[CompletionItem] | None

Returning ``None`` signals the trigger doesn't apply; the caller then
falls through to R26D's field completion (which itself may fall through
to R24E's line-local triggers).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from nova_lsp.struct_field_completion import (
    _find_enclosing_brace_init,
    _mask_full_text,
)
from nova_lsp.type_hierarchy import (
    KIND_STRUCT,
    TypeDecl,
)


# ---------------------------------------------------------------------------
# LSP CompletionItemKind enum values we emit.
# ---------------------------------------------------------------------------

COMPLETION_KIND_VARIABLE = 6


# ---------------------------------------------------------------------------
# Trigger detection.
# ---------------------------------------------------------------------------


@dataclass
class _BaseSpreadContext:
    """Result of locating a ``..`` spread position inside a brace-init."""
    struct_name: str
    open_lb_line: int
    open_lb_col: int


def _cursor_immediately_after_double_dot(
    masked_lines: List[str],
    line_no: int,
    character: int,
    open_lb_line: int,
    open_lb_col: int,
) -> bool:
    """Return ``True`` when the cursor at ``(line_no, character)`` sits
    directly after a ``..`` token at depth-zero relative to the brace-
    init body opening at ``(open_lb_line, open_lb_col)``.

    "Directly after" allows trailing whitespace between the ``..`` and
    the cursor (so ``Point { ..  |`` still triggers) but disallows any
    intervening non-space character. Allowing whitespace matches the
    R26D field-completion semantics where ``Point { |`` and
    ``Point { x: 10,    |`` both fire."""
    if line_no < 0 or line_no >= len(masked_lines):
        return False
    cur_text = masked_lines[line_no]
    end_col = min(character, len(cur_text))
    # Walk back over trailing whitespace on the cursor line.
    col = end_col - 1
    cur_line = line_no
    while True:
        while col >= 0:
            ch = cur_text[col]
            if ch == " " or ch == "\t":
                col -= 1
                continue
            # Found the last non-space char before the cursor.
            # Need exactly two dots: text[col] == '.' AND text[col-1] == '.'.
            if ch != ".":
                return False
            if col - 1 < 0:
                # Need to look at the previous line for the second dot.
                # But a ``.`` at column 0 followed by EOL means the prev
                # line's content; we don't bother — multi-line ``..`` is
                # not a NOVA convention.
                return False
            if cur_text[col - 1] != ".":
                return False
            # Confirm the ``..`` sits at outer-level depth 0 relative
            # to the brace-init body. Scan from the opening ``{`` to
            # this column and count braces / parens.
            return _is_at_outer_depth(
                masked_lines,
                open_lb_line, open_lb_col,
                cur_line, col - 1,
            )
        # Cursor was on a line with only whitespace — move up.
        cur_line -= 1
        if cur_line < open_lb_line:
            return False
        cur_text = masked_lines[cur_line]
        col = len(cur_text) - 1


def _is_at_outer_depth(
    masked_lines: List[str],
    open_lb_line: int,
    open_lb_col: int,
    target_line: int,
    target_col: int,
) -> bool:
    """Return ``True`` when the character at ``(target_line, target_col)``
    lives at brace-depth 0 relative to the body opening at
    ``(open_lb_line, open_lb_col)``. Used to verify the ``..`` token is
    a top-level spread rather than nested inside ``Inner { ..q }``."""
    depth = 0
    paren_depth = 0
    for line_no in range(open_lb_line, target_line + 1):
        if line_no >= len(masked_lines):
            return False
        text = masked_lines[line_no]
        start = 0
        end = len(text)
        if line_no == open_lb_line:
            start = open_lb_col + 1
        if line_no == target_line:
            end = min(target_col, len(text))
        if start > end:
            continue
        for i in range(start, end):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
            elif ch == "(" or ch == "[":
                paren_depth += 1
            elif ch == ")" or ch == "]":
                if paren_depth > 0:
                    paren_depth -= 1
    return depth == 0 and paren_depth == 0


def is_base_spread_context(
    uri: str,
    position: Dict[str, int],
    doc_text: str,
) -> Optional[_BaseSpreadContext]:
    """Return the base-spread context (struct name + opening ``{``
    location) when the cursor sits immediately after a depth-zero
    ``..`` inside a brace-init body. Returns ``None`` otherwise."""
    line_no = int(position.get("line", 0))
    character = int(position.get("character", 0))
    masked = _mask_full_text(doc_text)
    ctx = _find_enclosing_brace_init(masked, line_no, character)
    if ctx is None:
        return None
    if not _cursor_immediately_after_double_dot(
        masked, line_no, character, ctx.open_lb_line, ctx.open_lb_col,
    ):
        return None
    return _BaseSpreadContext(
        struct_name=ctx.struct_name,
        open_lb_line=ctx.open_lb_line,
        open_lb_col=ctx.open_lb_col,
    )


# ---------------------------------------------------------------------------
# Enclosing-fn discovery.
# ---------------------------------------------------------------------------


_FN_DECL_RE = re.compile(
    r"^\s*fn\s+(?:[A-Z][A-Za-z0-9_]*\s*\.\s*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def _find_enclosing_fn(
    masked_lines: List[str], cursor_line: int
) -> Optional[Tuple[int, int]]:
    """Return ``(fn_decl_line, fn_close_brace_line)`` for the function
    whose body contains ``cursor_line``, or ``None`` when the cursor
    isn't inside any fn body.

    The detection is conservative: we look for the most recent
    ``fn NAME(`` line at or before ``cursor_line`` whose opening body
    brace ``{`` is still unclosed at ``cursor_line``."""
    # Walk lines top-down and track depth so we know which fn we're in.
    depth = 0
    fn_stack: List[Tuple[int, int]] = []   # (fn_line, depth_at_open)
    for line_no, line in enumerate(masked_lines):
        if line_no > cursor_line:
            break
        # Detect an ``fn`` declaration line at the START of the line
        # (top-level). The brace may be on this same line or on a
        # later one.
        if _FN_DECL_RE.match(line) and depth == 0:
            # Record the fn decl. The body open brace may appear on
            # this line — scan for it.
            fn_stack.append((line_no, depth))
        # Update brace depth based on this line's masked content.
        for ch in line:
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                # When depth drops back to the fn's opening depth, the
                # current fn body closes. (Pure heuristic — works for
                # well-formed source.)
                if fn_stack and depth <= fn_stack[-1][1]:
                    fn_stack.pop()
    if not fn_stack:
        return None
    fn_line, _ = fn_stack[-1]
    # Find the closing brace by re-walking from fn_line forward.
    close_line = _find_fn_body_end(masked_lines, fn_line)
    return (fn_line, close_line)


def _find_fn_body_end(masked_lines: List[str], fn_line: int) -> int:
    """Return the line containing the closing ``}`` for the fn body
    opening at or after ``fn_line``. Falls back to the last line of
    the document when no close is found."""
    n = len(masked_lines)
    depth = 0
    open_found = False
    for line_no in range(fn_line, n):
        line = masked_lines[line_no]
        for ch in line:
            if ch == "{":
                depth += 1
                open_found = True
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                if open_found and depth == 0:
                    return line_no
    return n - 1


# ---------------------------------------------------------------------------
# Let-binding harvest within a line range.
# ---------------------------------------------------------------------------


_LET_ANNOT_RE = re.compile(
    r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Za-z0-9_]*)"
)
_LET_CTOR_RE = re.compile(
    r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Z][A-Za-z0-9_]*)\s*\("
)
_LET_BRACE_RE = re.compile(
    r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Z][A-Za-z0-9_]*)"
    r"(?:\s*<[^<>{};]*>)?\s*\{"
)
_FN_PARAM_TYPED_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Z][A-Za-z0-9_]*)"
)


@dataclass
class _ScopeBinding:
    """One let-binding (or parameter) discovered inside the scope."""
    name: str
    type_name: str
    # The decl_line is informative — used for tie-breaking when two
    # bindings share a name (later wins).
    decl_line: int


def _collect_bindings_in_range(
    masked_lines: List[str],
    start_line: int,
    end_line: int,
    cursor_line: int,
    cursor_char: int,
) -> List[_ScopeBinding]:
    """Walk ``masked_lines[start_line..end_line]`` and harvest every
    typed binding (``let``-annotation, ``let``-ctor, ``let``-brace, fn
    parameter row).

    Bindings declared AT or AFTER the cursor line don't count — they're
    not yet in scope. Self-referential decls (``let p = Point { ..p }``)
    are filtered out because ``p`` isn't bound until the RHS finishes
    evaluating; we approximate by skipping any binding on the cursor
    line. This matches the user's mental model: completing
    ``Point { ..|`` on line 5 doesn't suggest ``p`` if ``p`` is itself
    being defined on line 5."""
    out: List[_ScopeBinding] = []
    end_line = min(end_line, len(masked_lines) - 1)
    for line_no in range(start_line, end_line + 1):
        if line_no >= cursor_line:
            # Don't include the cursor's own line: avoids suggesting
            # the variable being assigned to.
            break
        line = masked_lines[line_no]
        # Annotation form first — highest precedence.
        for m in _LET_ANNOT_RE.finditer(line):
            out.append(_ScopeBinding(
                name=m.group(1),
                type_name=m.group(2),
                decl_line=line_no,
            ))
        # Constructor form: ``let p = Point(...)``.
        for m in _LET_CTOR_RE.finditer(line):
            out.append(_ScopeBinding(
                name=m.group(1),
                type_name=m.group(2),
                decl_line=line_no,
            ))
        # Brace-init form: ``let p = Point { ... }``.
        for m in _LET_BRACE_RE.finditer(line):
            out.append(_ScopeBinding(
                name=m.group(1),
                type_name=m.group(2),
                decl_line=line_no,
            ))
        # Fn parameter row: only when the ``fn`` keyword appears on
        # this line, to avoid matching struct field declarations
        # (``x: int,``) inside a struct body.
        if re.search(r"\bfn\b", line) and "(" in line:
            inside = line[line.find("(") + 1:]
            for m in _FN_PARAM_TYPED_RE.finditer(inside):
                out.append(_ScopeBinding(
                    name=m.group(1),
                    type_name=m.group(2),
                    decl_line=line_no,
                ))
    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def find_in_scope_struct_values(
    uri: str,
    position: Dict[str, int],
    struct_name: str,
    doc_text: str,
    decls: List[Tuple[str, TypeDecl]],
) -> List[Dict[str, Any]]:
    """Return CompletionItems (one per in-scope variable whose type is
    ``struct_name``).

    The walk:
      1. Determine the enclosing fn (or fall back to top-level scope).
      2. Harvest every typed binding inside that scope.
      3. Filter by ``type_name == struct_name`` (after stripping any
         generic suffix on the binding's annotation -- ``let p:
         Point<int>`` matches ``Point { ..|``).
      4. Sort by declaration order and de-duplicate by name (later wins,
         so a let-shadow returns the most recently visible binding).
    """
    line_no = int(position.get("line", 0))
    character = int(position.get("character", 0))
    masked = _mask_full_text(doc_text)
    enclosing = _find_enclosing_fn(masked, line_no)
    if enclosing is not None:
        start_line, end_line = enclosing
    else:
        # Top-level scope: harvest the whole document.
        start_line, end_line = 0, len(masked) - 1
    bindings = _collect_bindings_in_range(
        masked, start_line, end_line, line_no, character,
    )
    # Filter by type. Strip any trailing generic args from the binding's
    # type name (the regexes already drop ``<...>`` for the annotation
    # form, but constructor / brace forms preserve the base name).
    matches: List[_ScopeBinding] = []
    seen: set = set()
    for b in reversed(bindings):
        if b.name in seen:
            continue
        if b.type_name != struct_name:
            continue
        seen.add(b.name)
        matches.append(b)
    # Source order = reverse of the de-duped collection.
    matches.reverse()
    return [_make_value_item(b, struct_name) for b in matches]


def _make_value_item(
    binding: _ScopeBinding, struct_name: str
) -> Dict[str, Any]:
    """CompletionItem for one in-scope struct-typed binding.

    The label is the variable name; ``kind`` is Variable (6); ``detail``
    is ``var: StructName`` so the editor's hover surface shows the
    type. Insertion is plain — the user has already typed the leading
    ``..``."""
    return {
        "label": binding.name,
        "kind": COMPLETION_KIND_VARIABLE,
        "detail": f"{binding.name}: {struct_name}",
        "insertText": binding.name,
        "documentation": {
            "kind": "markdown",
            "value": (
                f"In-scope value of type `{struct_name}` "
                f"(usable as a base for struct update-syntax)."
            ),
        },
    }


def compute_base_spread_completions(
    uri: str,
    position: Dict[str, int],
    doc_text: str,
    decls: List[Tuple[str, TypeDecl]],
) -> Optional[List[Dict[str, Any]]]:
    """Top-level helper called by the type-completion pipeline.

    Returns ``None`` when the cursor isn't immediately after a ``..``
    inside a brace-init body — the caller falls through to R26D's
    field-position completion. Returns a list (possibly empty) when
    the trigger applies; an empty list signals "no in-scope values of
    that type" so the caller does NOT mix in the generic fallback (an
    arbitrary ``main`` symbol isn't a sensible spread base).

    The list MAY be empty when:
      * ``struct_name`` isn't declared anywhere visible (so no in-scope
        binding can possibly have that type).
      * No ``let`` / param of that type is in scope.
    Both empty-result cases return ``[]`` so the editor shows nothing,
    matching R26D's "trigger recognised but no candidates" behaviour."""
    ctx = is_base_spread_context(uri, position, doc_text)
    if ctx is None:
        return None
    # Sanity check: only offer values when the struct is actually
    # declared somewhere visible (don't suggest values for a typo'd
    # struct name -- they probably want a different completion mode).
    if not _struct_known(ctx.struct_name, decls):
        return []
    return find_in_scope_struct_values(
        uri, position, ctx.struct_name, doc_text, decls,
    )


def _struct_known(name: str, decls: List[Tuple[str, TypeDecl]]) -> bool:
    """Return ``True`` when a struct declaration named ``name`` exists
    in ``decls``."""
    for _path, d in decls:
        if d.kind == KIND_STRUCT and d.name == name:
            return True
    return False
