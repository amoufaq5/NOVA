"""Auto-fix for non-exhaustive ``match`` expressions.

The companion to R17A's exhaustiveness check. R17A added a WARN-level
diagnostic ``warning: non-exhaustive match on E (missing: V1, V2)``
whenever a ``match`` arm covers some-but-not-all variants of an enum
``E``. This module turns that diagnostic into a one-click code action
in the LSP — the editor shows a lightbulb at the match site; clicking
"Add missing match arms" inserts a stub arm for each missing variant.

Wire shape (per LSP spec):

  * ``textDocument/codeAction({uri, range, context: {diagnostics}})``
    -> ``[CodeAction]``.
  * Each CodeAction has ``{title, kind, edit, diagnostics}`` where
    ``edit`` is a ``WorkspaceEdit`` in the ``{"changes": {uri:
    TextEdit[]}}`` form the rest of the LSP already uses (mirrors
    ``server._make_workspace_edit``).
  * ``kind`` is ``"quickfix"`` — VS Code surfaces these in the
    lightbulb dropdown above the squiggle (the same UX as Rust's
    "fill match arms" hint).

Heuristic in three steps:

  1. **Locate the match.** Given the diagnostic's range, walk the
     buffer from the diagnostic line down to find the nearest
     ``match <discriminant> {`` block. The block's body extends from
     the opening ``{`` to the matching ``}``; we count brace depth
     across lines so payload-bearing arms with nested expressions
     don't trick the parser.

  2. **Find the enum's variant set.** R17A's exhaustiveness WARN
     names the enum: ``non-exhaustive match on Shape (missing:
     Rect)``. We parse the enum name out of the diagnostic message,
     then resolve its declaration via R5F's ``find_definition`` over
     the import graph + R8C's workspace symbol index (so an enum
     declared in ``shapes.nova`` is found from a match in
     ``main.nova``). The variants are extracted using R15F's
     ``scan_enum_variants`` so payload arity is correct.

  3. **Subtract covered variants.** Walk the existing arms inside the
     match body and pull out every ``Enum::Variant`` token (mirrors
     R8C's ``_enum_variant_regex``). The set difference with the
     declared variants gives us the variants to generate. We also
     detect the ``_`` catch-all so the insertion lands BEFORE it.

  4. **Generate insertion text.** Each new arm is a single line:
     ``    Enum::Variant(_, _) => /* TODO */`` where the ``_``
     placeholders match the variant's declared arity (zero for
     nullary variants -> bare ``Enum::Variant``). The leading
     indentation copies the indent of an existing arm so the new
     arms align cleanly with the surrounding style.

  5. **Build the WorkspaceEdit.** A single ``TextEdit`` that inserts
     the generated arm block on its own line just above the closing
     ``}`` (or just above the catch-all arm when present). The edit
     uses a zero-width range so the editor doesn't drop existing
     text — purely insertion.

The non-trivial bit is that R17A's exhaustiveness WARN doesn't carry
the variant list directly in a structured form; it only embeds it in
the human-readable message. Parsing the message back out keeps the
LSP loosely coupled to R17A's WARN format — when the format changes
(e.g. localized) we can fall back to recomputing the missing-set
from the AST scan.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, walk_imports
from nova_lsp.type_hierarchy import (
    EnumVariant,
    KIND_ENUM,
    TypeDecl,
    scan_enum_variants,
    scan_type_declarations,
    type_decl_by_name,
)
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------


# LSP CodeActionKind for our fix. ``quickfix`` is the standard kind for
# diagnostic-driven repairs — VS Code shows them in the lightbulb menu
# attached to the squiggle. Other editors (neovim, helix) match on the
# string prefix the same way.
KIND_QUICKFIX = "quickfix"

# The TODO placeholder we drop into each generated arm body. Kept as a
# constant so tests can match it precisely.
TODO_PLACEHOLDER = "/* TODO */"

# R17A's exhaustiveness WARN format. ``"non-exhaustive match on E
# (missing: V1, V2)"`` — we capture the enum name and the missing
# variant list. The leading ``warning:`` prefix is optional because
# the LSP's diagnostic parser strips it before forwarding.
_EXHAUSTIVENESS_RE = re.compile(
    r"non-exhaustive\s+match\s+on\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(missing:\s*([^)]+)\)"
)


# ---------------------------------------------------------------------------
# String / comment masking.
#
# Same shape as `type_hierarchy._mask_comments_and_strings`. We keep a
# private copy so this module doesn't reach into another module's
# underscore-prefixed helper.
# ---------------------------------------------------------------------------


def _mask_comments_and_strings(line: str) -> str:
    """Replace string contents and comment regions with spaces so a
    later regex scan doesn't misinterpret literal text as code.
    Preserves column offsets so reported character positions stay
    accurate."""
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
# Match expression locator.
# ---------------------------------------------------------------------------


@dataclass
class MatchExprInfo:
    """One ``match`` block parsed out of a NOVA source file.

    Fields:
      ``open_line``       — the line containing ``match <expr> {``.
      ``close_line``      — the line containing the matching ``}``.
      ``body_start_line`` — first line inside the body (open_line + 1
                            for a multi-line match where the brace
                            sits at end-of-line; same as open_line
                            when the body and brace share a line).
      ``arms``            — list of (start_line, end_line, arm_text)
                            covering each arm. Used by the missing-set
                            computation and the catch-all detector.
      ``catch_all_line``  — line of the ``_ => ...`` arm if any, else
                            ``None``. Insertion lands BEFORE this line
                            when present.
      ``base_indent``     — string of whitespace used for arm
                            indentation, inferred from the first
                            non-empty body line. New arms reuse this
                            indent so they line up with siblings.
    """
    open_line: int = 0
    close_line: int = 0
    body_start_line: int = 0
    arms: List[Tuple[int, int, str]] = field(default_factory=list)
    catch_all_line: Optional[int] = None
    base_indent: str = "    "


_MATCH_HEAD_RE = re.compile(r"\bmatch\b")
# Used to identify catch-all arms (``_`` pattern). The ``=>`` is a
# defensive guard so a bare underscore in the discriminant expression
# of the match itself isn't misread as a catch-all.
_CATCH_ALL_RE = re.compile(r"^\s*_\s*=>")
# Identifier-ish at the start of an arm — used to harvest covered
# variants from the existing arms. Matches both R17A's ``Name::Variant``
# and the legacy ``Name.Variant`` form so we don't drift if a fixture
# uses the older syntax.
_ARM_VARIANT_RE = re.compile(
    r"^\s*([A-Z][A-Za-z0-9_]*)(?:::|\.)([A-Za-z_][A-Za-z0-9_]*)"
)


def find_match_at(
    doc_text: str,
    line: int,
) -> Optional[MatchExprInfo]:
    """Locate the smallest ``match { ... }`` block whose head sits
    at or near ``line``.

    Strategy:
      1. Scan the document for every ``match ... {`` head and its
         matching ``}``.
      2. Pick the block whose head line is closest to ``line``
         (preferring the one that actually contains ``line``).

    Returns ``None`` when the buffer has no match block, or when
    ``line`` is far enough away from any match head that we can't
    confidently bind a code action to it.
    """
    blocks = _all_matches(doc_text)
    if not blocks:
        return None
    # First: the block that strictly contains `line`. Among nested
    # matches we want the innermost; sort by (containment, length).
    containing = [
        b for b in blocks
        if b.open_line <= line <= b.close_line
    ]
    if containing:
        # innermost = smallest body span.
        containing.sort(key=lambda b: b.close_line - b.open_line)
        return containing[0]
    # Fall back to the nearest match head — diagnostic ranges are
    # sometimes one-character zero-width markers anchored on the
    # match keyword's first column, but ranges from other compilers
    # might land just before/after the head. We accept anything within
    # 2 lines of a head as "near".
    nearby = [
        b for b in blocks
        if abs(b.open_line - line) <= 2
    ]
    if not nearby:
        return None
    nearby.sort(key=lambda b: abs(b.open_line - line))
    return nearby[0]


def _all_matches(doc_text: str) -> List[MatchExprInfo]:
    """Walk the document and return one ``MatchExprInfo`` per
    ``match ... {`` block. Nested matches are emitted in source
    order; the caller picks the right one via positional containment.
    """
    lines = doc_text.splitlines()
    out: List[MatchExprInfo] = []
    n = len(lines)
    i = 0
    while i < n:
        cleaned = _mask_comments_and_strings(lines[i])
        for m in _MATCH_HEAD_RE.finditer(cleaned):
            # Confirm this is a `match` keyword, not e.g. `dispatch`.
            start = m.start()
            # Identifier-boundary check.
            if start > 0 and (cleaned[start - 1].isalnum() or cleaned[start - 1] == "_"):
                continue
            block = _parse_match_block(lines, i, start)
            if block is not None:
                out.append(block)
                # Skip past the block's close so we don't re-parse a
                # match nested inside this one (we'll re-enter via the
                # body scan below — yes, this is a deliberate redundancy
                # so nested matches still get their own MatchExprInfo
                # entries).
                # Note: we don't break out of the for-loop; multiple
                # match-heads on the same line is unusual but legal.
        i += 1
    return out


def _parse_match_block(
    lines: List[str],
    head_line: int,
    head_col: int,
) -> Optional[MatchExprInfo]:
    """Given the line/column of a ``match`` keyword, parse the matching
    ``{ ... }`` body and the arms inside it. Returns ``None`` if the
    opening brace can't be located (malformed source)."""
    n = len(lines)
    if head_line >= n:
        return None
    # Find the opening brace. It may be on the head line or a later
    # line (NOVA permits `match x\n{` formatting though rare).
    open_line = head_line
    open_col = -1
    while open_line < n:
        cleaned = _mask_comments_and_strings(lines[open_line])
        # When we're on the head line, start scanning AFTER the head
        # column so we don't pick up an unrelated brace earlier on the
        # line (defensive — head_col is the start of `match`, no brace
        # should appear before it anyway).
        scan_from = head_col if open_line == head_line else 0
        brace_pos = cleaned.find("{", scan_from)
        if brace_pos != -1:
            open_col = brace_pos
            break
        open_line += 1
    if open_col < 0:
        return None
    # Walk forward counting braces to find the matching `}`.
    depth = 1
    close_line = open_line
    close_col = -1
    # On the opening line, start AFTER the `{`.
    started = True
    j = open_line
    col = open_col + 1
    while j < n and depth > 0:
        cleaned = _mask_comments_and_strings(lines[j])
        m = len(cleaned)
        k = col if started else 0
        while k < m:
            ch = cleaned[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_line = j
                    close_col = k
                    break
            k += 1
        if depth == 0:
            break
        j += 1
        col = 0
        started = False
    if depth != 0:
        return None
    # Parse arms inside the body. NOVA's normal style puts the open
    # brace at the end of the head line so we start scanning at the
    # next line; if the match is a one-liner (open + close on the
    # same line) we fall back to the open line itself.
    if open_line + 1 <= close_line:
        body_start = open_line + 1
    else:
        body_start = open_line
    info = MatchExprInfo(
        open_line=open_line,
        close_line=close_line,
        body_start_line=body_start,
    )
    info.arms, info.catch_all_line, info.base_indent = _parse_arms(
        lines, body_start, close_line
    )
    return info


def _parse_arms(
    lines: List[str],
    body_start: int,
    close_line: int,
) -> Tuple[List[Tuple[int, int, str]], Optional[int], str]:
    """Walk lines ``[body_start, close_line)`` and group them into
    arms. Each arm spans from its head line (where ``=>`` appears)
    through the next arm's head line minus one.

    Returns ``(arms, catch_all_line, base_indent)`` where ``arms`` is
    a list of (start_line, end_line, text) tuples, ``catch_all_line``
    is the line of the ``_ =>`` arm or ``None``, and ``base_indent``
    is the indent string of the first arm (used so generated arms
    align with siblings).
    """
    arms: List[Tuple[int, int, str]] = []
    catch_all_line: Optional[int] = None
    base_indent = "    "
    # Collect arm head lines first — the line where ``=>`` first
    # appears at the body's brace depth. We track a simple per-line
    # depth so a payload-bearing arm with a nested expression doesn't
    # confuse us.
    depth = 0
    head_lines: List[int] = []
    for i in range(body_start, close_line):
        cleaned = _mask_comments_and_strings(lines[i])
        if depth == 0 and "=>" in cleaned:
            head_lines.append(i)
            if _CATCH_ALL_RE.match(cleaned):
                catch_all_line = i
            # Establish base_indent from the first arm.
            if base_indent == "    " and not head_lines[:-1]:
                stripped = lines[i].lstrip()
                indent_len = len(lines[i]) - len(stripped)
                if indent_len > 0:
                    base_indent = lines[i][:indent_len]
        # Update depth for the next line.
        for ch in cleaned:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    # Build arm ranges: each arm extends from its head to the next
    # arm's head minus 1 (or close_line - 1 for the last arm).
    for idx, hl in enumerate(head_lines):
        end = head_lines[idx + 1] - 1 if idx + 1 < len(head_lines) else close_line - 1
        text = "\n".join(lines[hl:end + 1])
        arms.append((hl, end, text))
    return arms, catch_all_line, base_indent


# ---------------------------------------------------------------------------
# Diagnostic parsing.
# ---------------------------------------------------------------------------


def parse_exhaustiveness_diagnostic(
    message: str,
) -> Optional[Tuple[str, List[str]]]:
    """Parse R17A's exhaustiveness WARN message.

    The message looks like ``warning: non-exhaustive match on Shape
    (missing: Rect, Triangle)`` after the LSP's diagnostic line parser
    has stripped the ``file:line:col:`` prefix. We strip the optional
    ``warning:`` prefix here too so callers don't have to.

    Returns ``(enum_name, [missing_variants])`` on a hit, ``None``
    when the message doesn't match the format.
    """
    if not message:
        return None
    # The LSP DIAG_LINE_RE strips the leading "warning:" so the message
    # starts with "non-exhaustive...", but be defensive — some clients
    # forward the raw stderr line.
    if message.startswith("warning:"):
        message = message[len("warning:"):].lstrip()
    m = _EXHAUSTIVENESS_RE.search(message)
    if m is None:
        return None
    enum_name = m.group(1)
    missing_str = m.group(2).strip()
    if not missing_str:
        return None
    missing = [v.strip() for v in missing_str.split(",") if v.strip()]
    if not missing:
        return None
    return enum_name, missing


def is_exhaustiveness_diagnostic(diagnostic: Dict[str, Any]) -> bool:
    """Cheap probe used by the dispatcher to filter diagnostics
    before doing the expensive enum resolution."""
    msg = diagnostic.get("message", "")
    return parse_exhaustiveness_diagnostic(msg) is not None


# ---------------------------------------------------------------------------
# Enum resolution.
# ---------------------------------------------------------------------------


def resolve_enum_decl(
    enum_name: str,
    start_path: Optional[str],
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[TypeDecl, str, List[EnumVariant]]]:
    """Locate ``enum_name``'s declaration and return its variant list.

    Search order mirrors R5F's import-graph walk + R8C's workspace
    symbol index fallback:

      1. The current file (the buffer containing the warning).
      2. Every file reachable through ``import "..."`` from it.
      3. Every other file the workspace index has cataloged (so an
         enum declared in a sibling file outside the import graph is
         still found).

    Returns ``(decl, path, variants)`` on success, ``None`` when the
    enum can't be located. The variants are returned in source order
    (declaration order matches R17A's variant tag indices) so the
    generated arms follow the user's preferred ordering.
    """
    overrides = text_overrides or {}
    # 1+2. Import graph.
    if start_path:
        for entry in walk_imports(
            os.path.abspath(start_path), file_cache, text_overrides=overrides
        ):
            decls = scan_type_declarations(entry.text)
            hit = type_decl_by_name(decls, enum_name)
            if hit is not None and hit.kind == KIND_ENUM:
                variants = scan_enum_variants(entry.text, hit)
                return hit, entry.path, variants
    # 3. Workspace index.
    if workspace_index is not None:
        seen: Set[str] = set()
        # noinspection PyProtectedMember
        for path in list(workspace_index._by_file.keys()):  # noqa: SLF001
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
            hit = type_decl_by_name(decls, enum_name)
            if hit is not None and hit.kind == KIND_ENUM:
                variants = scan_enum_variants(text, hit)
                return hit, abs_path, variants
    return None


# ---------------------------------------------------------------------------
# Missing-variant computation.
# ---------------------------------------------------------------------------


def collect_covered_variants(
    match_info: MatchExprInfo,
    enum_name: str,
) -> Set[str]:
    """Walk the arms of ``match_info`` and return the set of variant
    names already covered.

    We look for arms whose head starts with ``EnumName::Variant`` (or
    the legacy ``EnumName.Variant``). Arms with other heads (e.g. a
    catch-all ``_``, a literal, a destructured tuple) don't contribute
    to the covered set — the catch-all is tracked separately by
    ``match_info.catch_all_line``.
    """
    covered: Set[str] = set()
    for (_start, _end, text) in match_info.arms:
        # We use the first line of the arm to identify the pattern.
        first_line = text.splitlines()[0] if text else ""
        cleaned = _mask_comments_and_strings(first_line)
        m = _ARM_VARIANT_RE.match(cleaned)
        if not m:
            continue
        head_enum = m.group(1)
        head_variant = m.group(2)
        if head_enum == enum_name:
            covered.add(head_variant)
    return covered


def infer_missing_variants(
    match_info: MatchExprInfo,
    enum_name: str,
    declared_variants: List[EnumVariant],
    *,
    fallback_missing: Optional[List[str]] = None,
) -> List[EnumVariant]:
    """Compute the variants that should be added to ``match_info``.

    ``declared_variants`` is the enum's full variant list (in source
    order). ``fallback_missing`` lets the caller pass through the
    list R17A's WARN message embedded; we use it as a tiebreaker
    when the AST scan can't fully verify the covered set (e.g. when
    an arm's head uses an alias the LSP doesn't understand).

    Returns the variants to insert, in declaration order so the
    generated arms follow R17A's variant tag ordering.
    """
    covered = collect_covered_variants(match_info, enum_name)
    # Variants declared but not covered.
    missing_from_ast = [
        v for v in declared_variants if v.name not in covered
    ]
    if not fallback_missing:
        return missing_from_ast
    # Use the WARN list as a safety net: only emit variants both the
    # AST scan AND R17A's WARN agree are missing. This avoids
    # double-adding an arm whose head was unparseable but actually
    # covers the variant. When the AST scan returns an empty set
    # (rare — e.g. a malformed match body), fall back to the WARN
    # list resolved against the declared variants.
    warn_set: Set[str] = set(fallback_missing)
    if missing_from_ast:
        return [v for v in missing_from_ast if v.name in warn_set]
    return [v for v in declared_variants if v.name in warn_set]


# ---------------------------------------------------------------------------
# Arm generation.
# ---------------------------------------------------------------------------


def build_arm_text(
    enum_name: str,
    variant: EnumVariant,
    indent: str,
) -> str:
    """Render one stub arm for ``variant`` of ``enum_name``.

    Forms:
      * ``Enum::None => /* TODO */`` for a nullary variant.
      * ``Enum::Some(_) => /* TODO */`` for a 1-arity variant.
      * ``Enum::Rect(_, _) => /* TODO */`` for a 2-arity variant.
      * ``Enum::Triangle(_, _, _) => /* TODO */`` for 3-arity, etc.

    The body uses the ``/* TODO */`` placeholder so the editor
    highlights an obvious spot to fill in. Each call site decides
    whether to wrap the arm in braces (block body) or keep it on one
    line (expression body) — we always emit a single-line arm to keep
    the diff small. NOVA's parser accepts both forms.
    """
    if variant.arity <= 0:
        head = f"{enum_name}::{variant.name}"
    else:
        head = f"{enum_name}::{variant.name}(" + ", ".join(["_"] * variant.arity) + ")"
    return f"{indent}{head} => {TODO_PLACEHOLDER}"


def build_arms_block(
    enum_name: str,
    variants: List[EnumVariant],
    indent: str,
) -> str:
    """Render the full insertion block — one arm per missing variant,
    newline-separated. No trailing newline; the caller decides
    whether to append one based on where the insertion lands.
    """
    return "\n".join(build_arm_text(enum_name, v, indent) for v in variants)


# ---------------------------------------------------------------------------
# Insertion point + WorkspaceEdit construction.
# ---------------------------------------------------------------------------


def compute_insertion_point(
    match_info: MatchExprInfo,
    doc_text: str,
) -> Tuple[int, int]:
    """Decide where the generated arms land.

    Returns ``(line, character)`` for a zero-width insertion point.
    Two cases:

      * **Catch-all present** — the new arms go on a fresh line
        immediately above the catch-all arm, so the catch-all stays
        the last arm and continues to match anything we missed.
      * **No catch-all** — the new arms go on a fresh line
        immediately above the closing ``}``.

    Both cases use ``(line, 0)`` and emit a trailing newline in the
    arm text so existing lines are pushed down rather than overwritten.
    """
    lines = doc_text.splitlines()
    if match_info.catch_all_line is not None:
        return match_info.catch_all_line, 0
    # Insert just above the closing `}`.
    return match_info.close_line, 0


def build_workspace_edit(
    uri: str,
    doc_text: str,
    match_info: MatchExprInfo,
    enum_name: str,
    missing_variants: List[EnumVariant],
) -> Dict[str, Any]:
    """Construct a ``WorkspaceEdit`` that inserts the generated arms
    at the right place inside ``match_info``.

    Wire shape: ``{"changes": {uri: [TextEdit]}}`` — same form as
    ``server._make_workspace_edit`` so the same WorkspaceEdit
    application code (in tests + clients) handles it.
    """
    line, character = compute_insertion_point(match_info, doc_text)
    block = build_arms_block(
        enum_name, missing_variants, match_info.base_indent
    )
    # Append a newline so the line we're inserting BEFORE stays on its
    # own line. Without this, the existing close-brace line would end
    # up appended to the last new arm.
    new_text = block + "\n"
    return {
        "changes": {
            uri: [
                {
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": line, "character": character},
                    },
                    "newText": new_text,
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Top-level entry point used by the LSP dispatcher.
# ---------------------------------------------------------------------------


def build_exhaustiveness_code_actions(
    uri: str,
    doc_text: str,
    diagnostics: List[Dict[str, Any]],
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
    start_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Walk ``diagnostics`` and return one code action per
    exhaustiveness WARN that we can repair.

    Each returned action carries the originating diagnostic in its
    ``diagnostics`` field so VS Code can highlight the squiggle as
    "fixable" in the gutter. The ``edit`` is a single-TextEdit
    WorkspaceEdit ready to be applied verbatim.
    """
    actions: List[Dict[str, Any]] = []
    seen_matches: Set[Tuple[int, int]] = set()  # dedupe identical fixes
    for diag in diagnostics:
        message = diag.get("message", "")
        parsed = parse_exhaustiveness_diagnostic(message)
        if parsed is None:
            continue
        enum_name, warn_missing = parsed
        # Resolve the match block from the diagnostic range. R17A
        # anchors the WARN at the match's first line.
        diag_range = diag.get("range") or {}
        diag_line = diag_range.get("start", {}).get("line", 0)
        match_info = find_match_at(doc_text, diag_line)
        if match_info is None:
            continue
        # Dedupe: a single match block should produce one fix even if
        # several diagnostics overlap it.
        key = (match_info.open_line, match_info.close_line)
        if key in seen_matches:
            continue
        # Resolve the enum.
        resolved = resolve_enum_decl(
            enum_name,
            start_path,
            file_cache,
            workspace_index=workspace_index,
            text_overrides=text_overrides,
        )
        if resolved is None:
            continue
        _decl, _path, declared_variants = resolved
        if not declared_variants:
            continue
        # Compute the missing set.
        missing = infer_missing_variants(
            match_info,
            enum_name,
            declared_variants,
            fallback_missing=warn_missing,
        )
        if not missing:
            continue
        edit = build_workspace_edit(
            uri, doc_text, match_info, enum_name, missing
        )
        actions.append({
            "title": "Add missing match arms",
            "kind": KIND_QUICKFIX,
            "diagnostics": [diag],
            "edit": edit,
        })
        seen_matches.add(key)
    return actions
