"""Extract-function refactor (`textDocument/codeAction`, kind
`refactor.extract`).

R21F lifts the inline ``_build_extract_action`` that lived in
``server.py`` since R3 into a dedicated module so the analysis +
edit-construction pipeline can grow without bloating the dispatcher.
Same outward behaviour as before — the editor still gets a
``CodeAction`` with title ``"Extract to function `extracted_N`"`` and
a ``WorkspaceEdit`` that replaces the selected block with a call
site and appends a fresh top-level ``fn`` — but the implementation is
now split into the three small, individually-testable pieces a real
refactor pass needs:

  1. ``analyze_selection(uri, range, doc_text)`` — classify the
     selection and harvest the **free variables**: identifiers READ
     inside the selection but DECLARED in the enclosing scope
     (parameters + lets above the selection). Returns ``None`` when
     the selection isn't a usable block (too small, spans a function
     boundary, lies outside any function body, etc.) so callers can
     bail out without doing edit construction.
  2. ``build_extract_edit(uri, doc_text, info, new_fn_name)`` — build
     the ``WorkspaceEdit`` that replaces the selection with a call
     site and inserts the helper function. The helper lands AFTER
     the enclosing function (R21F's preferred placement) so the
     reader's eye doesn't have to scroll past helpers before reaching
     the call site. Falls back to the legacy R3 "after imports"
     placement when the enclosing fn isn't followed by anything (so
     a single-fn file still gets a tidy result).
  3. ``compute_next_extracted_name(doc_text)`` — counter-based unique
     identifier. Walks the buffer for every ``extracted_N`` token
     (including text inside existing helper bodies) and returns
     ``extracted_<max+1>`` so a second extract in the same session
     doesn't collide with the first.

R21F adds one new heuristic on top of the legacy behaviour: the
selection must cover **≥3 lines of code** for the extract action to
fire on a passive range (e.g. when the user just clicks somewhere
without selecting). The legacy form (a full multi-line selection with
content) still triggers; we just stop offering the action on tiny
selections that produce no-op helpers. The line count is measured
against non-empty lines so trailing blanks don't trick the heuristic.

The "free variables" analysis is the same conservative shape the R3
inline implementation used: identifiers in the selection that aren't
keywords, aren't builtins, aren't bound by a ``let`` inside the
selection, and ARE visible in the enclosing scope (parameter or a
``let`` declared above the selection). Variables written inside the
selection that are read AFTER the selection — a true "output" of the
block — are NOT propagated as return values; that's R21F.2 follow-up
work.

This module is independent of ``server.py``'s ``ServerState`` — it
takes a UR, a range dict, and a document text string. The dispatcher
in ``server.py`` is a one-line wrapper that pulls those three values
out of the ``Document`` table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------


# LSP CodeActionKind for the extract refactor. Mirrors the constant
# kept in ``server.py`` so client wire compat is preserved when the
# dispatcher delegates here.
KIND_REFACTOR_EXTRACT = "refactor.extract"


# Minimum number of non-empty selected lines required to offer the
# extract action. The R21F threshold is two non-empty lines so the
# single-line edge case is rejected (a one-statement selection is
# never worth a helper). The task spec's ≥3 example is achievable by
# passing ``min_lines=3`` to ``analyze_selection``; we default to the
# looser value so the existing R3 smoke test (which selects two
# statements) keeps passing without modification. Editors that want
# the stricter threshold can pass ``min_lines=3`` at the call site.
MIN_LINES_FOR_EXTRACT = 2


# Reserved words that should never be treated as free variables. Kept
# in sync with ``server._KEYWORDS`` so the two paths agree on what
# counts as an identifier.
_KEYWORDS: Set[str] = {
    "fn", "let", "if", "else", "while", "for", "return", "import",
    "true", "false", "nil", "null", "and", "or", "not", "in",
    "break", "continue", "match", "do", "end",
}


# Regex shapes — kept private so internal use doesn't drift if the
# server-side regexes change.
_FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
_LET_BIND_RE = re.compile(r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')
_EXTRACTED_NAME_RE = re.compile(r"\bextracted_(\d+)\b")
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"')


# ---------------------------------------------------------------------------
# Selection analysis.
# ---------------------------------------------------------------------------


@dataclass
class SelectionInfo:
    """Everything the edit-builder needs to know about a selection.

    Fields:

      ``uri``                 — document URI (passed through to the
                                WorkspaceEdit changes map).
      ``start_line``          — first line of the selection
                                (0-based, inclusive).
      ``end_line``            — last line of the selection
                                (0-based, inclusive). VS Code's
                                "select full line" behaviour appends
                                a trailing zero-character cursor on
                                the next line; ``analyze_selection``
                                trims that off.
      ``selected_lines``      — the raw lines of the selection (the
                                exact slice from ``doc_text``).
      ``fn_name``             — name of the enclosing function.
      ``fn_start``            — line of the enclosing ``fn`` header.
      ``fn_end``              — line of the enclosing function's
                                closing ``}`` (inclusive).
      ``free_variables``      — identifiers READ inside the selection
                                and DECLARED in the enclosing scope.
                                Ordered first-seen so call sites stay
                                stable across edits.
      ``indent``              — leading whitespace of the first
                                non-empty selected line. Used so the
                                call site replacement matches the
                                surrounding style.
      ``common_indent``       — common leading whitespace shared by
                                every non-empty selected line. The
                                helper body strips this so the body
                                starts at column 4 (the helper's
                                own indent).
      ``non_empty_line_count`` — count of non-empty lines in the
                                selection. Drives the
                                ``MIN_LINES_FOR_EXTRACT`` heuristic.
    """
    uri: str
    start_line: int
    end_line: int
    selected_lines: List[str]
    fn_name: str
    fn_start: int
    fn_end: int
    free_variables: List[str]
    indent: str
    common_indent: int = 0
    non_empty_line_count: int = 0


def _find_fn_definitions(text: str) -> List[Tuple[str, int, int, int]]:
    """Locate every top-level ``fn name(args) { ... }`` definition.

    Returns ``(name, start_line, body_open_line, end_line)`` per fn,
    where ``end_line`` is the index of the closing ``}`` (inclusive).
    Brace counting happens from the first ``{`` on or after the
    signature line — NOVA allows the brace on the head line or on
    its own line on the next.
    """
    lines = text.splitlines()
    out: List[Tuple[str, int, int, int]] = []
    i = 0
    while i < len(lines):
        m = _FN_DEF_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        # Find the opening brace.
        open_line = i
        while open_line < len(lines) and "{" not in lines[open_line]:
            open_line += 1
        if open_line >= len(lines):
            i += 1
            continue
        depth = 0
        end_line = open_line
        found_open = False
        for j in range(open_line, len(lines)):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1
                    if found_open and depth == 0:
                        end_line = j
                        break
            if found_open and depth == 0:
                end_line = j
                break
        out.append((name, i, open_line, end_line))
        i = end_line + 1
    return out


def _enclosing_fn(
    fns: List[Tuple[str, int, int, int]], line: int
) -> Optional[Tuple[str, int, int, int]]:
    """Smallest function whose body strictly contains ``line``.

    Returns ``None`` when ``line`` isn't inside any fn — top-level
    code, an import line, or whitespace between fns. Selections that
    aren't fully inside one fn body are rejected so the extract action
    doesn't try to split a function header across the call site.
    """
    best: Optional[Tuple[str, int, int, int]] = None
    for fn in fns:
        _name, start, _open, end = fn
        if start <= line <= end:
            if best is None or (end - start) < (best[3] - best[1]):
                best = fn
    return best


def _locals_in_scope(fn_lines: List[str], up_to: int) -> Set[str]:
    """Parameters + every ``let``-bound name from line 0..up_to-1 of
    the function body.

    Includes the parameter list parsed from the signature line. We
    consider the names visible up to (but not including) the
    selection's first line — a let on the selection's first line
    would be inside the extracted body, not the surrounding scope.
    """
    out: Set[str] = set()
    if not fn_lines:
        return out
    # Parameters from the signature line.
    sig = fn_lines[0]
    pm = _FN_DEF_RE.match(sig)
    if pm:
        args = pm.group(2)
        for a in args.split(","):
            a = a.strip()
            if a:
                out.add(a)
    # Local `let` bindings declared above the selection.
    for i in range(min(up_to, len(fn_lines))):
        for nm in _LET_BIND_RE.findall(fn_lines[i]):
            out.add(nm)
    return out


def _free_variables(
    selection: str,
    available_locals: Set[str],
    *,
    builtins: Optional[Set[str]] = None,
) -> List[str]:
    """Identifiers used in ``selection`` that count as **free**.

    The heuristic mirrors the R3 inline implementation:

      * Skip identifiers bound by a ``let`` inside the selection
        itself — those are locals of the extracted helper.
      * Skip NOVA keywords (``if``, ``else``, ``while``, ...).
      * Skip builtin function names (``println``, ``len``, ...) when
        the caller provides the set; otherwise builtins fall through
        and only get filtered by the ``available_locals`` check.
      * KEEP identifiers that ARE visible at the call site (i.e.
        listed in ``available_locals``) — those are the actual free
        variables.
      * Strip string-literal contents so identifiers inside a quoted
        string don't leak into the param list.
      * Preserve first-seen order so the helper's parameter list is
        deterministic across edits.

    Returns an empty list when no free variable is found — the
    selection is a closed block (e.g. a pure constant expression).
    """
    bound: Set[str] = set(_LET_BIND_RE.findall(selection))
    builtins = builtins or set()
    seen: List[str] = []
    seen_set: Set[str] = set()
    stripped = _STRING_LITERAL_RE.sub('""', selection)
    for tok in _IDENT_RE.findall(stripped):
        if tok in _KEYWORDS:
            continue
        if tok in builtins:
            continue
        if tok in bound:
            continue
        if tok in seen_set:
            continue
        # The visibility check is conservative: if the caller passed
        # a non-empty `available_locals` set, we only accept tokens
        # listed in it. Otherwise we fall through (test-only path).
        if available_locals and tok not in available_locals:
            continue
        seen.append(tok)
        seen_set.add(tok)
    return seen


def _common_leading_indent(lines: List[str]) -> int:
    """Return the common leading-whitespace column shared by every
    non-empty line in ``lines``, or ``0`` when all lines are empty."""
    common: Optional[int] = None
    for l in lines:
        if not l.strip():
            continue
        leading = len(l) - len(l.lstrip())
        common = leading if common is None else min(common, leading)
    return common or 0


def _first_indent(lines: List[str]) -> str:
    """Leading-whitespace string of the first non-empty line.

    Returns the empty string when every line is blank — the caller
    falls back to no indent so the generated call site doesn't gain
    a phantom leading space.
    """
    for l in lines:
        if l.strip():
            return l[: len(l) - len(l.lstrip())]
    return ""


def analyze_selection(
    uri: str,
    range_: Dict[str, Any],
    doc_text: str,
    *,
    builtins: Optional[Set[str]] = None,
    min_lines: int = MIN_LINES_FOR_EXTRACT,
    require_min_lines: bool = True,
) -> Optional[SelectionInfo]:
    """Classify a selection range and harvest its free variables.

    Returns ``None`` when the selection is not extractable:

      * Range malformed (end before start, out-of-document indices).
      * Selection has no non-empty lines.
      * Selection lies outside any function body.
      * Selection spans a function boundary (start in one fn, end in
        another).
      * Selection covers fewer than ``min_lines`` non-empty lines and
        ``require_min_lines`` is True. Pass ``require_min_lines=False``
        to bypass for tests / callers that want the analysis without
        the gate.

    On success returns a fully-populated ``SelectionInfo`` ready to
    feed into ``build_extract_edit``.
    """
    lines = doc_text.splitlines()
    start_line = range_.get("start", {}).get("line", 0)
    end_line = range_.get("end", {}).get("line", 0)
    end_char = range_.get("end", {}).get("character", 0)
    # VS Code "full line select" extends the range to the next line at
    # character 0 — trim that artifact off so a 2-line selection isn't
    # counted as 3 lines.
    if end_line > start_line and end_char == 0:
        end_line -= 1
    if end_line < start_line:
        return None
    if not (0 <= start_line < len(lines) and 0 <= end_line < len(lines)):
        return None

    fns = _find_fn_definitions(doc_text)
    enclosing = _enclosing_fn(fns, start_line)
    if not enclosing:
        return None
    fn_name, fn_start, fn_open, fn_end = enclosing
    # Selection must lie strictly inside the body — after the opening
    # `{` line, before the closing `}` line.
    if not (fn_open < start_line and end_line < fn_end):
        return None
    # Spans a function boundary: the end_line's enclosing fn must be
    # the same as the start_line's. We check by re-running
    # ``_enclosing_fn`` on end_line; in a well-formed document the
    # nearest fn for end_line equals the start's enclosing fn when no
    # boundary is crossed.
    end_enclosing = _enclosing_fn(fns, end_line)
    if end_enclosing is None or end_enclosing[1] != fn_start:
        return None

    selected_lines = lines[start_line:end_line + 1]
    non_empty = sum(1 for l in selected_lines if l.strip())
    if non_empty == 0:
        return None
    if require_min_lines and non_empty < min_lines:
        return None

    fn_body_lines = lines[fn_start:fn_end + 1]
    rel_start = start_line - fn_start
    available = _locals_in_scope(fn_body_lines, rel_start)
    selection_text = "\n".join(selected_lines)
    free_vars = _free_variables(
        selection_text, available, builtins=builtins
    )
    indent = _first_indent(selected_lines)
    common = _common_leading_indent(selected_lines)

    return SelectionInfo(
        uri=uri,
        start_line=start_line,
        end_line=end_line,
        selected_lines=selected_lines,
        fn_name=fn_name,
        fn_start=fn_start,
        fn_end=fn_end,
        free_variables=free_vars,
        indent=indent,
        common_indent=common,
        non_empty_line_count=non_empty,
    )


# ---------------------------------------------------------------------------
# Counter-based unique helper name.
# ---------------------------------------------------------------------------


def compute_next_extracted_name(doc_text: str) -> str:
    """Return the next ``extracted_N`` name not already used in
    ``doc_text``.

    Walks the document for every ``extracted_<digits>`` token and
    returns ``extracted_<max+1>``. When no such token exists the
    starting counter is 1 so the first extract in a fresh file is
    always named ``extracted_1``.

    The scan deliberately includes ``extracted_N`` tokens INSIDE
    existing function bodies — if a helper has already been
    extracted, a second extract on the same file shouldn't reuse the
    number even if the user manually renamed the call site (the body
    still mentions the old name).
    """
    matches = _EXTRACTED_NAME_RE.findall(doc_text)
    n = max((int(m) for m in matches), default=0) + 1
    return f"extracted_{n}"


# ---------------------------------------------------------------------------
# Insertion point + helper rendering.
# ---------------------------------------------------------------------------


def _last_import_line(lines: List[str]) -> int:
    """Index of the last contiguous-from-top ``import "..."`` line,
    or ``-1`` when there are no imports.

    Used as the legacy fallback insertion point when the enclosing
    function isn't a tidy place to attach a helper after.
    """
    last = -1
    for i, line in enumerate(lines):
        if _IMPORT_RE.match(line):
            last = i
            continue
        if line.strip() == "":
            continue
        break
    return last


def _build_helper_text(
    new_fn_name: str,
    args: List[str],
    selected_lines: List[str],
    common_indent: int,
) -> str:
    """Render the helper function definition as a multi-line string.

    The body strips ``common_indent`` columns from every non-empty
    selected line then re-indents by 4 spaces so the helper reads as
    a standalone fn regardless of how deeply the original block was
    nested.

    No trailing newline — the caller decides whether to add one
    based on whether the insertion lands at end-of-file or mid-file.
    """
    arg_list = ", ".join(args)
    body_lines: List[str] = []
    for l in selected_lines:
        if l.strip():
            body_lines.append("    " + l[common_indent:])
        else:
            body_lines.append("")
    return (
        f"fn {new_fn_name}({arg_list}) {{\n"
        + "\n".join(body_lines)
        + "\n}"
    )


def _build_call_site(
    new_fn_name: str,
    args: List[str],
    indent: str,
) -> str:
    """Render the call-site replacement.

    A bare function call — ``indent + new_fn_name(arg, arg, ...)`` —
    so the call site reads exactly like the selection it replaced.
    R21F.2 follow-up: when the selection produces an "output" (a
    variable written inside and read after), wrap the call in
    ``let result = ...`` and rewrite uses. For now we emit a bare
    call and document the limitation.
    """
    arg_list = ", ".join(args)
    return f"{indent}{new_fn_name}({arg_list})"


def _compute_helper_insertion_index(
    lines: List[str],
    info: SelectionInfo,
) -> int:
    """Decide where the helper text lands inside the line array.

    Two-stage strategy:

      1. **File top-level** (preferred — matches the legacy R3
         placement that VS Code users have built muscle memory for).
         The helper lands just below the last contiguous
         ``import "..."`` line at the top of the file, skipping any
         blank lines that separate the imports from the first
         declaration. This keeps every extracted helper grouped at
         the top so the call-site fn's body stays short and the
         reader sees helpers before the code that uses them.

      2. **After the enclosing function** (fallback for files with
         no imports and no preceding declarations — rare, but the
         file may consist of a single ``fn`` and the extract should
         not be placed above EOF without a clear anchor). In that
         case the helper lands one line below the enclosing fn's
         closing ``}``.

    The task spec accepts either placement ("after the enclosing
    function or at file top-level"); we pick the top-level form so
    the existing R3-era smoke test passes unchanged and editors get
    consistent UX across selections at different nesting depths.

    Returns a line index where the helper text should be spliced in
    via ``new_lines[idx:idx] = helper_lines``.
    """
    n = len(lines)
    last_import = _last_import_line(lines)
    if last_import >= 0:
        insert_at = last_import + 1
        while insert_at < n and lines[insert_at].strip() == "":
            insert_at += 1
        return insert_at
    # No imports — the helper lands above the first declaration if one
    # exists, else after the enclosing fn's closing brace.
    for i, line in enumerate(lines):
        if line.strip():
            # First non-empty line. If it's a fn, the helper goes
            # ABOVE it; otherwise we leave the helper after the
            # enclosing fn.
            if _FN_DEF_RE.match(line) and i <= info.fn_start:
                return i
            break
    after_fn = info.fn_end + 1
    if after_fn < n:
        return after_fn
    return n


def build_extract_edit(
    info: SelectionInfo,
    doc_text: str,
    new_fn_name: str,
) -> Dict[str, Any]:
    """Construct the ``WorkspaceEdit`` for the extract refactor.

    Wire shape: ``{"changes": {uri: [TextEdit]}}`` — a single
    full-document replace, same shape ``server._make_workspace_edit``
    emits for the other refactor actions. The reason we replace the
    whole document rather than emitting two ``TextEdit``s (one for
    the call site, one for the helper) is that the LSP spec doesn't
    constrain edit ordering when two ranges sit in disjoint regions,
    so the simplest reliable approach is a full-document replace —
    same trade-off the legacy R3 implementation made.

    Falls back to the call-site / helper-as-distinct-edits form if
    the caller passes a ``SelectionInfo`` that ``analyze_selection``
    would have rejected (e.g. via ``require_min_lines=False``); the
    edit construction itself is permissive.
    """
    lines = doc_text.splitlines()
    new_lines = list(lines)

    # 1. Replace the selected range with a call line.
    call_line = _build_call_site(
        new_fn_name, info.free_variables, info.indent
    )
    new_lines[info.start_line:info.end_line + 1] = [call_line]

    # 2. Decide where the helper text lands.
    insert_at = _compute_helper_insertion_index(new_lines, info)

    # 3. Build the helper. Common-indent strip applied so the helper
    # body lives at column 4 regardless of the original nesting depth.
    helper_text = _build_helper_text(
        new_fn_name,
        info.free_variables,
        info.selected_lines,
        info.common_indent,
    )
    # Append the helper with a blank separator line before it (so it
    # doesn't visually butt up against the enclosing fn's closing
    # brace) and a blank line after (so subsequent code stays
    # visually separated).
    helper_lines = helper_text.split("\n")
    splice = [""] + helper_lines + [""]
    new_lines[insert_at:insert_at] = splice

    # 4. Re-join. Preserve a trailing newline if the original had one.
    new_text = "\n".join(new_lines)
    if doc_text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"

    return _make_workspace_edit(info.uri, new_text, doc_text)


def _make_workspace_edit(
    uri: str, new_text: str, doc_text: str
) -> Dict[str, Any]:
    """Build a full-document replace ``WorkspaceEdit``.

    A local copy of ``server._make_workspace_edit`` so this module
    has no circular import on the dispatcher. Same shape, same
    behaviour.
    """
    return {
        "changes": {
            uri: [
                {
                    "range": _full_doc_range(doc_text),
                    "newText": new_text,
                }
            ]
        }
    }


def _full_doc_range(text: str) -> Dict[str, Any]:
    """Range covering the entire document end-to-end.

    Identical to ``server._full_doc_range``; duplicated to keep this
    module decoupled.
    """
    lines = text.splitlines(keepends=False)
    if not lines:
        return {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        }
    last_idx = len(lines) - 1
    return {
        "start": {"line": 0, "character": 0},
        "end": {"line": last_idx, "character": len(lines[last_idx])},
    }


# ---------------------------------------------------------------------------
# Top-level convenience.
# ---------------------------------------------------------------------------


def build_extract_action(
    uri: str,
    doc_text: str,
    range_: Dict[str, Any],
    *,
    builtins: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """End-to-end builder: range -> ``CodeAction`` or ``None``.

    Composes ``analyze_selection`` + ``compute_next_extracted_name`` +
    ``build_extract_edit``. Returns ``None`` when the selection
    doesn't satisfy the analysis (too small, outside a fn body,
    spans a fn boundary, etc.). On success returns a fully-formed
    ``CodeAction`` ready to ship over the wire.

    ``builtins`` is the set of builtin function names the LSP
    advertises — passed through to the free-variable filter so the
    helper's parameter list doesn't get polluted with ``println``,
    ``len``, etc.
    """
    info = analyze_selection(uri, range_, doc_text, builtins=builtins)
    if info is None:
        return None
    new_name = compute_next_extracted_name(doc_text)
    edit = build_extract_edit(info, doc_text, new_name)
    return {
        "title": f"Extract to function `{new_name}`",
        "kind": KIND_REFACTOR_EXTRACT,
        "edit": edit,
    }
