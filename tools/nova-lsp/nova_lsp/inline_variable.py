"""Inline-variable refactor (`textDocument/codeAction`, kind
`refactor.inline`).

The classic third IDE refactor (after rename + extract). Given a
``let x = <expr>`` binding under the cursor, replace every use of
``x`` in the binding's scope with ``<expr>`` and delete the let.

  // Before (cursor on `let x = ...`)
  let x = compute() + 1
  println(x)
  return x * 2

  // After "Inline variable"
  println(compute() + 1)
  return (compute() + 1) * 2

The implementation is broken into four small, individually-testable
pieces:

  1. ``find_let_at(uri, position, doc_text)`` — locate the ``let``
     binding the cursor sits on (or near — VS Code's "Cmd+." shortcut
     puts the cursor anywhere on the let line). Returns ``None`` when
     the cursor isn't on a let line or the let's RHS is empty.
  2. ``analyze_scope(let_info, doc_text)`` — find the binding's
     scope (its enclosing fn body, ending at the closing ``}``), then
     scan for use sites + disqualifying patterns:
       * reassignment (``x = ...``) -> refuse the inline
       * cross-fn use (no fn body is the enclosing scope) -> refuse
       * captured by inner closure -> NOVA has no formal closures so
         this is currently always false, but the scaffolding is in
         place for forward compatibility
     Returns ``None`` when the inline is refused; otherwise returns a
     populated ``InlineInfo``.
  3. ``detect_side_effects(expr_text)`` — conservative heuristic:
     when the RHS contains a balanced ``(...)`` call-shape we mark
     the inline as side-effecting so the action's title can warn the
     user about duplicate evaluation. String literals + parenthesised
     sub-expressions are stripped before the check so a pure
     expression like ``(a + b) * c`` doesn't trip the heuristic.
  4. ``build_inline_edit(let_info, scope_info, edit_each_use)`` —
     build a ``WorkspaceEdit`` that:
       * replaces every use site with the parenthesised RHS,
       * removes the let line itself.
     The wrapped form ``(<expr>)`` preserves precedence even when
     the user inlines into an arithmetic context where the bare
     ``<expr>`` would re-associate.

This module is independent of ``server.py``'s ``ServerState`` — it
takes a URI, a position dict, and a document text string. The
dispatcher in ``server.py`` is a one-line wrapper that pulls those
three values out of the ``Document`` table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------


# LSP CodeActionKind for the inline refactor. ``refactor.inline`` is the
# standard string in the LSP CodeActionKind hierarchy (VS Code uses it
# for "Inline variable" / "Inline function" actions). Editors that filter
# on the prefix accept any ``refactor.inline.*`` sub-kind too.
KIND_REFACTOR_INLINE = "refactor.inline"


# Reserved words that should never be treated as use sites. Kept in
# sync with ``extract_function._KEYWORDS`` so the two paths agree on
# what counts as an identifier.
_KEYWORDS: Set[str] = {
    "fn", "let", "if", "else", "while", "for", "return", "import",
    "true", "false", "nil", "null", "and", "or", "not", "in",
    "break", "continue", "match", "do", "end", "const", "mut",
    "type", "enum", "struct",
}


# Regex shapes.
_FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
_LET_BIND_RE = re.compile(
    r"^(\s*)let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[^=]+)?\s*=\s*(.+?)\s*$"
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_LINE_COMMENT_RE = re.compile(r"//.*$|#.*$|--.*$")


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass
class LetBinding:
    """A located ``let NAME = RHS`` binding.

    Fields:

      ``uri``         — document URI (passed through to the edit).
      ``line``        — 0-based line where the let lives.
      ``indent``      — leading whitespace of the let line (so the
                        line removal is clean — we delete the whole
                        line, not just the keyword).
      ``name``        — bound identifier (``x`` in ``let x = ...``).
      ``rhs``         — the RHS expression text, trimmed.
      ``name_start``  — column where the name begins on the line.
      ``name_end``    — column just past the end of the name.
    """
    uri: str
    line: int
    indent: str
    name: str
    rhs: str
    name_start: int
    name_end: int


@dataclass
class UseSite:
    """A located use of the bound variable.

    Fields:

      ``line``  — 0-based line index.
      ``start`` — 0-based start column.
      ``end``   — 0-based end column (exclusive).
    """
    line: int
    start: int
    end: int


@dataclass
class InlineInfo:
    """Everything the edit-builder needs after analysis.

    Fields:

      ``binding``      — the located ``let`` binding.
      ``fn_start``     — line where the enclosing fn header begins.
      ``fn_end``       — line where the enclosing fn's closing ``}``
                         lives (inclusive).
      ``uses``         — list of use sites inside the scope, in
                         document order.
      ``has_side_effects`` — True when ``binding.rhs`` looks like it
                         contains a function call (caller surfaces a
                         warning suffix on the action title).
    """
    binding: LetBinding
    fn_start: int
    fn_end: int
    uses: List[UseSite]
    has_side_effects: bool = False


# ---------------------------------------------------------------------------
# Function-scope detection (mirrors extract_function).
# ---------------------------------------------------------------------------


def _find_fn_definitions(text: str) -> List[Tuple[str, int, int, int]]:
    """Locate every top-level ``fn name(args) { ... }`` definition.

    Returns ``(name, start_line, body_open_line, end_line)`` per fn,
    where ``end_line`` is the index of the closing ``}`` (inclusive).
    Brace-count from the first ``{`` on or after the signature line.

    Mirrors ``extract_function._find_fn_definitions`` exactly — kept
    local so this module has no cross-module coupling on internal
    helpers that might change.
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
    """Smallest fn body containing ``line``, or None.

    Mirrors ``extract_function._enclosing_fn``.
    """
    best: Optional[Tuple[str, int, int, int]] = None
    for fn in fns:
        _name, start, _open, end = fn
        if start <= line <= end:
            if best is None or (end - start) < (best[3] - best[1]):
                best = fn
    return best


# ---------------------------------------------------------------------------
# Step 1: find_let_at
# ---------------------------------------------------------------------------


def find_let_at(
    uri: str,
    position: Dict[str, Any],
    doc_text: str,
) -> Optional[LetBinding]:
    """Locate the ``let`` binding the cursor sits on.

    The cursor is considered "on" a let when its line number matches
    a line whose content is a ``let NAME = RHS`` form. Position
    character is not used to constrain horizontally — VS Code's
    "Cmd+." popup positions the cursor anywhere on the let line, so
    matching by line index keeps the UX consistent with the other
    refactors.

    Returns ``None`` when:
      * the position is out of bounds for the document
      * the line isn't a let binding (no ``let NAME = expr`` shape)
      * the RHS is empty (``let x =``)
      * the let is destructure-style (``let [a, b] = ...``) — those
        aren't single-variable bindings and the inline semantics
        get hairy fast; refuse cleanly
    """
    lines = doc_text.splitlines()
    line_no = position.get("line", -1)
    if not (0 <= line_no < len(lines)):
        return None
    line = lines[line_no]
    # Strip a trailing line comment from the line before regex-matching
    # so `let x = 1 // a comment` still parses with rhs = "1".
    body, _, _trailing = line.partition("//")
    # Repartition for `#` and `--` comments — preferred over chained
    # partitions because `--` could appear in arithmetic. Only treat as
    # a comment if it's preceded by whitespace.
    stripped = _strip_trailing_comment(line)
    m = _LET_BIND_RE.match(stripped)
    if not m:
        return None
    indent = m.group(1)
    name = m.group(2)
    rhs = m.group(3).rstrip()
    if not rhs:
        return None
    # Compute name column on the original line so the LSP range later
    # reflects the source layout exactly.
    let_kw_pos = line.find("let", len(indent))
    if let_kw_pos < 0:
        return None
    after_kw = let_kw_pos + len("let")
    # Skip whitespace between "let" and the name.
    name_start = after_kw
    while name_start < len(line) and line[name_start].isspace():
        name_start += 1
    name_end = name_start + len(name)
    return LetBinding(
        uri=uri,
        line=line_no,
        indent=indent,
        name=name,
        rhs=rhs,
        name_start=name_start,
        name_end=name_end,
    )


def _strip_trailing_comment(line: str) -> str:
    """Strip a trailing ``//``, ``#``, or ``--`` comment from ``line``.

    Conservative: we look at the part of the line OUTSIDE string
    literals so an inlined ``"//"`` doesn't get mistaken for a comment
    delimiter. The leftmost comment marker (after string-stripping)
    cuts the line.
    """
    masked = _STRING_LITERAL_RE.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', line)
    cut: int = len(line)
    for marker in ("//", "#", "--"):
        idx = masked.find(marker)
        if idx >= 0 and idx < cut:
            # Only treat `--` as a comment when it's preceded by
            # whitespace (otherwise `x--y` would falsely match — though
            # NOVA doesn't have `--` arithmetic, defensive is cheap).
            if marker == "--" and idx > 0 and not masked[idx - 1].isspace():
                continue
            cut = idx
    return line[:cut]


# ---------------------------------------------------------------------------
# Step 2: scope analysis + use scanning.
# ---------------------------------------------------------------------------


def _ident_positions(line: str, name: str) -> List[Tuple[int, int]]:
    """Return ``(start, end)`` for every ``\\bname\\b`` use in ``line``
    that isn't inside a string literal or comment.

    String contents and the comment tail are masked to spaces so the
    regex doesn't match identifiers that happen to live inside quotes.
    """
    masked = _STRING_LITERAL_RE.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', line)
    masked = _LINE_COMMENT_RE.sub(lambda m: " " * len(m.group(0)), masked)
    out: List[Tuple[int, int]] = []
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    for m in pattern.finditer(masked):
        out.append((m.start(), m.end()))
    return out


def _is_reassignment(line: str, name: str, start_col: int) -> bool:
    """Detect ``name = expr`` at ``start_col`` on ``line``.

    NOVA spells assignment as ``x = expr`` (not ``x := expr``); the
    use site is a reassignment when the identifier is followed by
    optional whitespace and a single ``=`` that isn't part of ``==``,
    ``!=``, ``<=``, ``>=``, ``=>``, or a let header.

    Conservative: the ``let`` keyword at column N means a *fresh*
    binding (potentially shadowing the outer one); we treat that as a
    new declaration, not a reassignment.
    """
    end_col = start_col + len(name)
    if end_col >= len(line):
        return False
    # Walk past whitespace.
    i = end_col
    while i < len(line) and line[i].isspace():
        i += 1
    if i >= len(line) or line[i] != "=":
        return False
    # Distinguish single `=` from `==` / `!=` / `=>` / `<=` / `>=`.
    if i + 1 < len(line) and line[i + 1] == "=":
        return False  # `==`
    if i > 0 and line[i - 1] in "!<>=":
        return False  # `!=`, `<=`, `>=`, `==`
    # `=>` (match arm) — accept the right-hand `=>` as not an assignment.
    if i + 1 < len(line) and line[i + 1] == ">":
        return False
    # Check this isn't a `let` line (the use site is the let itself).
    stripped = line[:start_col].rstrip()
    if stripped.endswith("let") or " let" in (" " + stripped + " "):
        return False
    return True


def _captured_by_closure(
    lines: List[str], use_line: int, name: str, fn_start: int, fn_end: int
) -> bool:
    """Detect whether the use at ``use_line`` lives inside an inner
    closure (a nested fn) defined inside the same enclosing fn.

    Walks the lines from ``fn_start..fn_end`` tracking nested ``fn``
    declarations. If ``use_line`` falls inside such a nested fn body,
    the use is "captured". NOVA's first-class function values aren't
    formal closures, but a nested ``fn helper() { ... use x ... }``
    inside a parent fn is the closest analogue and we refuse the
    inline because the bound variable's lifetime crosses the helper's
    body boundary.

    Conservative: only counts inner fn declarations. Match-arm
    expressions, while-bodies, and if-bodies don't count even though
    they nest — those execute in the parent's scope.
    """
    # Find inner fn ranges by depth-tracking, skipping the first
    # ``fn`` (which is the enclosing fn itself).
    depth = 0
    seen_outer_brace = False
    inner_ranges: List[Tuple[int, int]] = []
    nested_open: List[int] = []  # stack of nested fn open lines

    # Walk every line in the enclosing fn looking for nested fn decls.
    for j in range(fn_start + 1, fn_end + 1):
        line = lines[j]
        # Top-level fn signature inside the body = nested fn.
        if _FN_DEF_RE.match(line):
            # Locate the open brace.
            open_line = j
            while open_line <= fn_end and "{" not in lines[open_line]:
                open_line += 1
            if open_line > fn_end:
                continue
            # Brace-count to find this nested fn's close.
            d = 0
            close_line = open_line
            found_open = False
            for k in range(open_line, fn_end + 1):
                for ch in lines[k]:
                    if ch == "{":
                        d += 1
                        found_open = True
                    elif ch == "}":
                        d -= 1
                        if found_open and d == 0:
                            close_line = k
                            break
                if found_open and d == 0:
                    close_line = k
                    break
            inner_ranges.append((j, close_line))

    for inner_start, inner_end in inner_ranges:
        if inner_start <= use_line <= inner_end:
            return True
    return False


def analyze_scope(
    binding: LetBinding,
    doc_text: str,
) -> Optional[InlineInfo]:
    """Find the binding's scope, scan for uses, refuse on disqualifying
    patterns.

    Returns ``None`` (i.e. refuse the inline) when:

      * the binding isn't inside any fn body (top-level let) — out of
        scope for this refactor's safe transformation
      * the binding is reassigned anywhere in the scope after the let
        (``x = ...`` on a later line)
      * the binding is captured by an inner closure (currently:
        nested ``fn`` declaration that uses ``name``)

    Returns a populated ``InlineInfo`` otherwise. The ``uses`` list
    is the set of read-only references — what we'll replace with the
    RHS. The let line itself is NOT included in ``uses``.
    """
    lines = doc_text.splitlines()
    fns = _find_fn_definitions(doc_text)
    enclosing = _enclosing_fn(fns, binding.line)
    if enclosing is None:
        return None
    _fn_name, fn_start, fn_open, fn_end = enclosing
    # The binding must lie strictly inside the fn body (after the open
    # brace, before the close).
    if not (fn_open < binding.line < fn_end):
        return None

    # Walk the body from the binding line onward looking for uses.
    uses: List[UseSite] = []
    for j in range(binding.line + 1, fn_end):
        line = lines[j]
        positions = _ident_positions(line, binding.name)
        if not positions:
            continue
        for (s, e) in positions:
            # Reassignment? Refuse.
            if _is_reassignment(line, binding.name, s):
                return None
            # Closure capture? Refuse.
            if _captured_by_closure(lines, j, binding.name, fn_start, fn_end):
                return None
            uses.append(UseSite(line=j, start=s, end=e))

    # Cross-fn check: any *other* fn that uses the same name would be
    # using a different binding (NOVA's `let` is fn-local) and we
    # should NOT replace it. The scope walk above already restricts to
    # the enclosing fn body, so uses in other fns are naturally
    # excluded; this is just a sanity assertion for tests.

    has_se = detect_side_effects(binding.rhs)
    return InlineInfo(
        binding=binding,
        fn_start=fn_start,
        fn_end=fn_end,
        uses=uses,
        has_side_effects=has_se,
    )


# ---------------------------------------------------------------------------
# Step 3: side-effect heuristic.
# ---------------------------------------------------------------------------


def detect_side_effects(expr_text: str) -> bool:
    """Conservative side-effect detector for an RHS expression.

    Heuristic: when the stripped expression contains a balanced
    ``(...)`` pair that looks like a function call (an identifier
    immediately followed by ``(``), we flag it. Pure parenthesised
    sub-expressions like ``(a + b) * c`` are stripped first by
    checking that the ``(`` is preceded by an identifier character.

    Examples:

      ``compute() + 1``    -> True  (compute followed by `(` -> call)
      ``foo(1, 2)``        -> True  (foo followed by `(` -> call)
      ``a + b``            -> False
      ``(a + b) * c``      -> False  (`(` preceded by space, not ident)
      ``x.foo()``          -> True  (method call)
      ``[1, 2, 3]``        -> False
      ``"hello"``          -> False  (string literal stripped)
      ``"foo()"``          -> False  (call inside string is masked)

    Conservative bias: when we're unsure, we say "yes side effects"
    so the user gets the duplicate-evaluation warning. The action is
    still offered — they can decline.
    """
    if not expr_text:
        return False
    # Mask out string literals so calls embedded in strings don't trip
    # the heuristic.
    masked = _STRING_LITERAL_RE.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', expr_text)
    # Now look for `ident(` patterns. We need the character preceding
    # the `(` to be either an identifier char or `]` (e.g. arr[0]() ),
    # both of which are call-shape.
    for i, ch in enumerate(masked):
        if ch != "(":
            continue
        if i == 0:
            continue
        prev = masked[i - 1]
        if prev.isalnum() or prev == "_" or prev == "]" or prev == ")":
            # Make sure the identifier isn't a NOVA keyword like `if`,
            # `while` — those parens are control-flow not call.
            # Walk backwards from i-1 to grab the preceding identifier.
            j = i - 1
            while j >= 0 and (masked[j].isalnum() or masked[j] == "_"):
                j -= 1
            tok = masked[j + 1 : i]
            if tok in _KEYWORDS:
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Step 4: build_inline_edit
# ---------------------------------------------------------------------------


def _wrap_rhs(rhs: str) -> str:
    """Return ``rhs`` wrapped in parens to preserve precedence.

    Already-parenthesised expressions don't get double-wrapped: when
    ``rhs`` is a single balanced ``(...)`` form we leave it alone.
    Pure literals (a single number / string / identifier) don't need
    parens, but wrapping them is harmless and uniform — we still
    wrap for consistency so downstream readers know the substitution
    point.
    """
    s = rhs.strip()
    if not s:
        return s
    # Skip wrapping when the entire RHS is already one paren pair.
    if s.startswith("(") and s.endswith(")"):
        # Verify the leading `(` actually closes at the trailing `)`
        # (vs e.g. `(a) + (b)` which has two paren pairs).
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    # The first `(` doesn't pair with the last `)`.
                    return f"({s})"
        return s
    # Single identifier or number literal — wrapping is uniform but
    # not strictly needed. Wrap for consistency.
    return f"({s})"


def build_inline_edit(
    info: InlineInfo,
    doc_text: str,
    *,
    edit_each_use: bool = True,
) -> Dict[str, Any]:
    """Construct the ``WorkspaceEdit`` for the inline refactor.

    Wire shape: ``{"changes": {uri: [TextEdit]}}`` — a list of
    non-overlapping ranged edits sorted later-first so VS Code can
    apply them without offset recomputation.

    Each use site becomes a TextEdit replacing ``[start, end)`` with
    the parenthesised RHS. The let line itself is replaced by an
    empty range covering the whole line including its trailing
    newline (so the file doesn't gain a blank line where the let
    was).

    ``edit_each_use`` is wired through for future use (e.g. an
    "inline at cursor" variant that only replaces the use under the
    cursor); for now it just gates whether to emit use-site edits at
    all. Always passed True in normal operation.
    """
    lines = doc_text.splitlines(keepends=True)
    binding = info.binding
    edits: List[Dict[str, Any]] = []

    # 1. Use-site replacements — every read of `binding.name` in the
    # scope becomes the wrapped RHS.
    wrapped = _wrap_rhs(binding.rhs)
    if edit_each_use:
        for use in info.uses:
            edits.append({
                "range": {
                    "start": {"line": use.line, "character": use.start},
                    "end": {"line": use.line, "character": use.end},
                },
                "newText": wrapped,
            })

    # 2. Let-line removal — delete the whole line including its
    # trailing newline. We do this by replacing the line span with the
    # empty string. If the let line is the last line of the file (no
    # trailing newline), we still want to remove just the let content
    # without leaving a phantom blank line.
    if 0 <= binding.line < len(lines):
        line_end_col = len(lines[binding.line])
        # Compute end position: start of next line (so the newline is
        # consumed too).
        if binding.line + 1 < len(lines):
            end_line = binding.line + 1
            end_char = 0
        else:
            end_line = binding.line
            end_char = line_end_col
        edits.append({
            "range": {
                "start": {"line": binding.line, "character": 0},
                "end": {"line": end_line, "character": end_char},
            },
            "newText": "",
        })

    return {"changes": {binding.uri: edits}}


# ---------------------------------------------------------------------------
# Top-level convenience.
# ---------------------------------------------------------------------------


def build_inline_action(
    uri: str,
    doc_text: str,
    range_: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """End-to-end builder: range -> ``CodeAction`` or ``None``.

    Composes ``find_let_at`` + ``analyze_scope`` +
    ``build_inline_edit``. Returns ``None`` when the cursor isn't on
    a let binding or the analysis refuses (reassigned, captured,
    cross-fn). On success returns a fully-formed ``CodeAction`` ready
    to ship over the wire.

    Side-effect warning is surfaced in the action title:
    when the RHS looks like a call (``compute()``) and there are
    more than one use, the title becomes
    ``Inline variable `x` (warning: duplicates N call expressions)``.
    Otherwise it's just ``Inline variable `x```.

    The ``range_`` is interpreted as the cursor position via its
    ``start`` field — VS Code passes the cursor line+character there
    even for non-empty selections. We accept either the start or end
    if start doesn't land on a let (so a "select the whole let line"
    drag also works).
    """
    start = range_.get("start") or {}
    end = range_.get("end") or {}
    # Try start first, then end. The cursor is typically at start
    # (range collapse on a click) but VS Code's full-line select
    # extends to (next_line, 0); fall back to that.
    binding = find_let_at(uri, start, doc_text)
    if binding is None and end and end != start:
        # Try the previous line (full-line selects end at next_line, 0).
        if end.get("character", 0) == 0:
            adj_end = {"line": end.get("line", 0) - 1, "character": 0}
            binding = find_let_at(uri, adj_end, doc_text)
        if binding is None:
            binding = find_let_at(uri, end, doc_text)
    if binding is None:
        return None

    info = analyze_scope(binding, doc_text)
    if info is None:
        return None

    edit = build_inline_edit(info, doc_text)
    title = _format_title(info)
    return {
        "title": title,
        "kind": KIND_REFACTOR_INLINE,
        "edit": edit,
    }


def _format_title(info: InlineInfo) -> str:
    """Build the action title, including the side-effect warning
    suffix when the inline would duplicate a call across multiple
    uses.
    """
    name = info.binding.name
    n_uses = len(info.uses)
    if info.has_side_effects and n_uses > 1:
        return (
            f"Inline variable `{name}` "
            f"(warning: duplicates side-effecting expression across "
            f"{n_uses} uses)"
        )
    if info.has_side_effects and n_uses == 1:
        # Single use: warning still fires because the user should
        # understand the call happens "at the use site" instead of
        # the binding site.
        return (
            f"Inline variable `{name}` "
            f"(warning: side-effecting expression)"
        )
    if n_uses == 0:
        # Edge case: let with no uses — inlining just removes the
        # binding. Still offer the action; user might want the
        # cleanup.
        return f"Inline variable `{name}` (unused)"
    return f"Inline variable `{name}`"
