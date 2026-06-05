"""Lexical scope index for NOVA source files.

R36D ships a tools-side scope reconstruction so refactors (extract-
function, eventually rename / inline-variable) can ask precise
**which scope declares this name?** questions rather than relying on
file-wide textual heuristics.

R35F's extract-function inherited a textual heuristic for the variable
analysis: ``_assigned_variables`` walked the selection for
``let NAME = ...`` / ``NAME = ...`` patterns, and ``_used_after``
regex-scanned the post-selection lines of the enclosing fn body for
any candidate name. The R35F exit caveat called out the failure mode
as **over-conservative** (extra return values, extra params) rather
than incorrect: a name written inside the selection that's later read
only inside a nested block that shadows it would still be flagged as
live-out. R36D replaces those two helpers with proper scope-aware
lookups so the refactor produces the minimum-correct param + return
sets.

Why a tools-side scope index rather than a parser query? NOVA's
parser doesn't yet emit a stable AST shape nova-lsp can consume
without re-implementing half of it. Until that lands, we reconstruct
scopes by parsing the source textually — brace counting + a small
recogniser for ``fn`` / ``let`` / ``if`` / ``else`` / ``while`` /
``for`` / ``match`` heads. The pattern matches R33D's code-lens
scanner (which also counts braces over the raw text) so the two
tools-side analyses stay consistent.

Scope kinds modelled:

  * ``FILE`` — the implicit top-level scope. Holds every ``fn``
    declared at column zero. Parent of every ``FN`` scope.
  * ``FN`` — a function body. Bindings include the function's
    parameter list + any ``let`` declared directly inside the body
    (not inside a nested block).
  * ``BLOCK`` — a brace-delimited block (``{ ... }``) opened by an
    ``if`` arm, an ``else`` arm, the body of a ``while`` / ``for``,
    a ``match`` arm, or a bare ``{ ... }``. Each block is its own
    scope so ``let`` bindings inside the block don't leak out.

Each scope tracks:

  * its source range (``start_line``, ``start_col``, ``end_line``,
    ``end_col``) — half-open at the end column to match LSP range
    semantics;
  * its parent scope (``None`` for FILE);
  * its declared bindings — a mapping from name -> declaration line.
    The mapping is populated as we walk the source, so a later
    binding inside the same scope overwrites an earlier one (NOVA
    allows ``let`` shadowing inside the same scope; the index
    stores the latest declaration so ``resolve`` finds the most
    recent one at a given position).
  * the list of its child scopes — used by ``scope_at`` to find the
    most-nested scope containing a position.

Top-level public API:

  * :func:`build_scope_index` — entry point.
  * :class:`ScopeIndex.scope_at` — find the most-nested scope
    containing a position.
  * :class:`ScopeIndex.resolve` — look up the declaring scope for a
    name at a position (walks parent chain).
  * :class:`ScopeIndex.live_at_range` — variables READ inside a
    range that are declared OUTSIDE it (these become extract-fn
    params).
  * :class:`ScopeIndex.assigned_used_after` — variables ASSIGNED
    inside a range that are READ AFTER (these become extract-fn
    return values). The "after" cursor stays inside the enclosing
    function scope; reads inside a nested block that has its OWN
    binding of the same name don't count as a use of the outer.

Known limitations (textual reconstruction):

  * String literals containing ``{`` or ``}`` perturb brace
    counting. The masker (``_mask_strings_and_comments``) replaces
    quoted contents with spaces before counting so this case is
    handled.
  * Nested generics ``Foo<Bar<T>>`` could in principle confuse a
    naive ``<`` ``>`` matcher, but the scope index only looks at
    ``{`` / ``}`` so generics don't interact with scope walking.
  * Single-statement bodies without braces (``if cond stmt``) are
    not modelled — NOVA's grammar requires braces. If the parser
    ever permits brace-less bodies the index would silently drop
    the implicit scope.
  * The reconstruction is independent of name resolution as the
    parser performs it; a malformed file with mismatched braces
    yields a partial index but the public API still degrades
    gracefully (``resolve`` returns ``None`` for unknown names).

These limitations are honest design caveats — the index produces
**better** results than R35F's textual heuristic, but doesn't claim
parser-level correctness. A future round can replace the textual
walker with a parser-emitted AST query without changing the public
API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Recogniser regexes.
#
# These mirror the pattern used by `extract_function._FN_DEF_RE` /
# `_LET_BIND_RE` so the scope index and the refactor agree on what
# counts as a declaration. The scope index additionally recognises
# block-introducer keywords (`if`, `else`, `while`, `for`, `match`,
# `do`) so a nested arm becomes its own scope.
# ---------------------------------------------------------------------------


_FN_DEF_RE = re.compile(
    r"^(\s*)fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)
_LET_BIND_RE = re.compile(r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Bare reassignment: ``NAME = expr`` (no ``let``). Excludes ``==`` and
# ``=>`` so equality + match arrows don't pollute the assignment set.
_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=|>)"
)

# Keywords that should never be treated as identifiers. Kept in sync
# with the set in ``extract_function.py``.
_KEYWORDS: Set[str] = {
    "fn", "let", "if", "else", "while", "for", "return", "import",
    "true", "false", "nil", "null", "and", "or", "not", "in",
    "break", "continue", "match", "do", "end",
}


# Scope kinds. Plain strings so the test harness can assert on them
# without importing an enum.
SCOPE_FILE = "file"
SCOPE_FN = "fn"
SCOPE_BLOCK = "block"


# ---------------------------------------------------------------------------
# Helper — mask string literals + line comments.
#
# The brace counter must NOT count ``{`` / ``}`` inside a quoted
# literal or behind a ``//`` comment. We replace masked content with
# spaces so column offsets are preserved (tests / future callers may
# care about exact columns).
# ---------------------------------------------------------------------------


def _mask_strings_and_comments(line: str) -> str:
    """Replace string-literal contents + line-comment trailers with
    spaces.

    Same idea as ``code_lens._mask_comments_and_strings`` but lifted
    into this module so the scope-index analysis has no cross-module
    dependency (keeps it cheap to re-use from refactors).
    """
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
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Scope dataclass + index.
# ---------------------------------------------------------------------------


@dataclass
class Scope:
    """One lexical scope in the scope tree.

    ``kind`` is one of ``SCOPE_FILE`` / ``SCOPE_FN`` / ``SCOPE_BLOCK``.
    ``start_line`` / ``start_col`` mark the OPENING position of the
    scope (the brace's column for ``BLOCK`` / ``FN``, the column-zero
    position for ``FILE``). ``end_line`` / ``end_col`` mark the
    CLOSING brace's position. Both are 0-based; ranges are inclusive
    on both ends.

    ``parent`` is the enclosing scope, or ``None`` for the file scope.
    ``bindings`` maps a declared name to the line where it was bound.
    For shadowing inside the same scope the LATEST binding wins (see
    note in the module docstring).
    ``children`` lists nested scopes in declaration order.
    """

    kind: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    parent: Optional["Scope"] = None
    bindings: Dict[str, int] = field(default_factory=dict)
    children: List["Scope"] = field(default_factory=list)
    # Optional name for ``FN`` scopes — surfaces the function name so
    # tests + the extract refactor can identify the enclosing fn
    # without re-scanning the source. ``None`` for FILE + BLOCK
    # scopes.
    fn_name: Optional[str] = None

    def contains(self, line: int, col: int) -> bool:
        """Return True when ``(line, col)`` falls within the scope's
        source range (inclusive both ends)."""
        if line < self.start_line or line > self.end_line:
            return False
        if line == self.start_line and col < self.start_col:
            return False
        if line == self.end_line and col > self.end_col:
            return False
        return True


@dataclass
class ScopeIndex:
    """Scope tree + name -> declaring-scope index for a single source
    file.

    The class is small + immutable after construction. Public methods
    do not mutate state; they walk the precomputed tree.
    """

    file_scope: Scope
    source: str = ""
    # Cached split lines so multi-method calls don't re-split.
    _lines: List[str] = field(default_factory=list)
    # Cached masked lines (string-lit + comment masked) for
    # identifier-scan APIs. Same length as ``_lines``.
    _masked_lines: List[str] = field(default_factory=list)

    # -- core lookup ---------------------------------------------------------

    def scope_at(self, line: int, col: int = 0) -> Scope:
        """Return the most-nested scope containing ``(line, col)``.

        Falls back to ``file_scope`` when no smaller scope covers the
        position (top-level imports, comments, etc.).
        """
        return self._descend(self.file_scope, line, col)

    def _descend(self, scope: Scope, line: int, col: int) -> Scope:
        """Recursive helper: find the deepest child containing the
        position."""
        for child in scope.children:
            if child.contains(line, col):
                return self._descend(child, line, col)
        return scope

    def resolve(self, name: str, line: int, col: int = 0) -> Optional[Scope]:
        """Look up the declaring scope for ``name`` at ``(line, col)``.

        Walks the parent chain from the smallest containing scope
        outward. Returns the innermost scope that declares ``name``
        with a declaration line at or before ``line`` (so a forward
        reference to a not-yet-declared local doesn't resolve to it).

        For ``FN`` scopes the declaration line check is relaxed:
        parameters are considered declared on the fn's start line,
        and a fn defined later in the same FILE scope still resolves
        for a same-file call. The relaxation lets ``resolve`` find a
        helper called from earlier in the file without re-implementing
        forward-reference rules in the caller.

        Returns ``None`` when no enclosing scope declares ``name``.
        """
        cur: Optional[Scope] = self.scope_at(line, col)
        while cur is not None:
            if name in cur.bindings:
                decl_line = cur.bindings[name]
                # Inside a FN / BLOCK scope a forward reference to a
                # let bound later on the same statement-list is NOT
                # visible. The FILE scope is exempt because top-level
                # fn definitions are hoisted in NOVA's semantics.
                if cur.kind == SCOPE_FILE:
                    return cur
                if decl_line <= line:
                    return cur
            cur = cur.parent
        return None

    # -- range queries -------------------------------------------------------

    def live_at_range(
        self,
        start_line: int,
        end_line: int,
        *,
        builtins: Optional[Set[str]] = None,
    ) -> List[str]:
        """Return identifiers READ inside ``[start_line, end_line]``
        that are declared in an OUTER scope (i.e. declared in a
        scope that is an ancestor of the smallest scope containing
        the range).

        First-seen source order is preserved so the helper's
        parameter list is deterministic across edits. Builtins +
        keywords are filtered out.

        Used by the extract-function refactor to compute the helper's
        free-variable / parameter list.
        """
        builtins = builtins or set()
        if not self._lines:
            return []
        # The most-nested scope that ENCLOSES the entire selection.
        # Identifiers we care about are declared outside this scope.
        enclosing = self._smallest_enclosing_range(start_line, end_line)
        # Names bound INSIDE the selection — these are helper-local.
        local_bound: Set[str] = set()
        for ln in range(start_line, end_line + 1):
            if 0 <= ln < len(self._masked_lines):
                for m in _LET_BIND_RE.finditer(self._masked_lines[ln]):
                    local_bound.add(m.group(1))
        seen: List[str] = []
        seen_set: Set[str] = set()
        for ln in range(start_line, end_line + 1):
            if not (0 <= ln < len(self._masked_lines)):
                continue
            masked = self._masked_lines[ln]
            for m in _IDENT_RE.finditer(masked):
                tok = m.group(0)
                if tok in _KEYWORDS:
                    continue
                if tok in builtins:
                    continue
                if tok in local_bound:
                    continue
                if tok in seen_set:
                    continue
                # Resolve from the token's position. Look up the
                # declaring scope; if it's an ancestor of (or equal
                # to) the enclosing scope, the binding is OUTSIDE
                # the selection -> a free variable.
                col = m.start()
                decl = self.resolve(tok, ln, col)
                if decl is None:
                    continue
                # Exclude top-level fn references (they're callable
                # without being a param). A name declared in the FILE
                # scope is a top-level fn / let — not something we
                # parameterise.
                if decl.kind == SCOPE_FILE:
                    continue
                # The decl scope must be an ANCESTOR of the enclosing
                # scope. If decl is the enclosing scope or contained
                # within it, the binding is part of (or below) the
                # selection -> not a free variable.
                if self._is_ancestor_or_equal(decl, enclosing):
                    seen.append(tok)
                    seen_set.add(tok)
        return seen

    def assigned_used_after(
        self,
        start_line: int,
        end_line: int,
        fn_scope: Optional[Scope] = None,
    ) -> List[str]:
        """Return names ASSIGNED inside ``[start_line, end_line]``
        that are READ AFTER the range (still inside ``fn_scope`` if
        provided, else inside the enclosing FN scope).

        First-seen-assignment order preserved.

        Two cases captured for "assigned":

          * ``let NAME = ...`` declared inside the selection.
          * ``NAME = ...`` (bare reassignment to an already-declared
            name).

        A read counts only when it resolves to the SAME binding (or
        ancestor binding) that the assignment targets — a later block
        that shadows the name with its own ``let`` is NOT counted,
        because the inner reads bind to the shadow rather than the
        outer.

        Returns an empty list when no assignment escapes.
        """
        if not self._lines:
            return []
        # Determine fn_scope: smallest enclosing FN scope of the
        # selection's start.
        if fn_scope is None:
            cur: Optional[Scope] = self.scope_at(start_line, 0)
            while cur is not None and cur.kind != SCOPE_FN:
                cur = cur.parent
            fn_scope = cur
        if fn_scope is None:
            return []
        # 1. Collect assigned names inside the selection + the SCOPE
        # they refer to (the binding's declaring scope).
        assigned: List[Tuple[str, Optional[Scope]]] = []
        assigned_seen: Set[str] = set()
        for ln in range(start_line, end_line + 1):
            if not (0 <= ln < len(self._masked_lines)):
                continue
            masked = self._masked_lines[ln]
            m_let = _LET_BIND_RE.search(masked)
            if m_let:
                name = m_let.group(1)
                if name not in assigned_seen:
                    # The let bound a fresh binding; the declaring
                    # scope is the scope containing this line.
                    decl_scope = self.scope_at(ln, m_let.start(1))
                    assigned.append((name, decl_scope))
                    assigned_seen.add(name)
                continue
            m_bare = _ASSIGN_RE.match(masked)
            if m_bare:
                name = m_bare.group(1)
                if name in _KEYWORDS:
                    continue
                if name in assigned_seen:
                    continue
                # The bare assignment refers to an EXISTING binding —
                # resolve it from the assignment's position to find
                # the declaring scope.
                col = m_bare.start(1)
                decl_scope = self.resolve(name, ln, col)
                assigned.append((name, decl_scope))
                assigned_seen.add(name)
        if not assigned:
            return []
        # 2. Walk lines AFTER the selection but inside fn_scope; for
        # each candidate name look for a read that resolves to its
        # declaring scope (or an ancestor — same binding).
        used: List[str] = []
        used_set: Set[str] = set()
        candidates_left: Dict[str, Optional[Scope]] = dict(assigned)
        for ln in range(end_line + 1, fn_scope.end_line + 1):
            if not (0 <= ln < len(self._masked_lines)):
                continue
            masked = self._masked_lines[ln]
            for m in _IDENT_RE.finditer(masked):
                tok = m.group(0)
                if tok not in candidates_left:
                    continue
                if tok in used_set:
                    continue
                target_scope = candidates_left[tok]
                col = m.start()
                decl = self.resolve(tok, ln, col)
                # Same-binding match: the resolution returns either
                # the original declaring scope or — for a let bound
                # inside the selection itself — None (the binding is
                # no longer visible after the selection ends).
                #
                # The R36D rule:
                #   * If the assignment was a ``let`` inside the
                #     selection AND the target_scope EQUALS the
                #     enclosing fn / block, the let escapes only if a
                #     post-selection read resolves to the SAME scope's
                #     binding. A shadow inside a nested block doesn't
                #     count because the inner read resolves to the
                #     shadow scope (not target_scope).
                #   * If the assignment was a bare ``NAME = expr``,
                #     target_scope is the original declaring scope of
                #     ``NAME`` (which is OUTSIDE the selection). A
                #     later read should resolve to that same scope to
                #     count as a use.
                if decl is target_scope:
                    # Order-preserving accumulation.
                    used.append(tok)
                    used_set.add(tok)
        # 3. Order results by first-seen-assignment.
        return [n for n, _ in assigned if n in used_set]

    # -- internal helpers ----------------------------------------------------

    def _smallest_enclosing_range(
        self, start_line: int, end_line: int
    ) -> Scope:
        """Smallest scope that fully contains
        ``[start_line, end_line]``."""
        cur = self.scope_at(start_line, 0)
        # Walk up until cur's end_line covers end_line.
        while cur.end_line < end_line and cur.parent is not None:
            cur = cur.parent
        return cur

    def _is_ancestor_or_equal(self, ancestor: Scope, descendant: Scope) -> bool:
        """Return True when ``ancestor`` is the same scope as
        ``descendant`` or appears in its parent chain.

        Used by ``live_at_range`` to decide whether a binding's
        declaring scope is OUTSIDE (an ancestor of) the selection's
        enclosing scope.
        """
        cur: Optional[Scope] = descendant
        while cur is not None:
            if cur is ancestor:
                return True
            cur = cur.parent
        return False


# ---------------------------------------------------------------------------
# Scope-tree construction.
# ---------------------------------------------------------------------------


def build_scope_index(source: str) -> ScopeIndex:
    """Parse ``source`` into a scope tree + return the index.

    Two-pass strategy:

      1. Mask strings + comments so brace counting isn't confused by
         a literal ``"{"`` or ``// {``.
      2. Single-pass walk recognising:
           * ``fn NAME(args)`` — opens a new FN scope at the first
             ``{``. Parameters land in the FN scope's bindings on
             the fn's start line.
           * ``{`` outside an fn-head — opens a BLOCK scope on the
             current line (parent = current).
           * ``}`` — closes the topmost open scope.
           * ``let NAME = ...`` — adds a binding to the current
             scope.
         The walker keeps a stack of (scope, depth_when_opened); when
         the line-end's brace depth drops back below a stacked entry,
         that scope is closed.

    Returns a ScopeIndex backed by the new tree.
    """
    lines = source.splitlines()
    masked_lines = [_mask_strings_and_comments(l) for l in lines]
    # File scope covers the whole document.
    last_line = max(len(lines) - 1, 0)
    last_col = len(lines[-1]) if lines else 0
    file_scope = Scope(
        kind=SCOPE_FILE,
        start_line=0,
        start_col=0,
        end_line=last_line,
        end_col=last_col,
    )

    # Open-scope stack. Each entry: (scope, opened_at_depth_after_open).
    # ``opened_at_depth_after_open`` is the brace depth AT (and
    # including) the brace that opened the scope. When the depth drops
    # back BELOW that level the scope closes.
    open_stack: List[Tuple[Scope, int]] = [(file_scope, 0)]
    depth = 0  # running brace depth

    # When we encounter an ``fn`` head we may not see its opening
    # brace on the same line. We defer FN-scope creation until the
    # first ``{`` after the head; ``pending_fn`` holds the head info.
    pending_fn: Optional[Tuple[str, List[str], int, int]] = None
    # The tuple is (fn_name, params, head_line, head_col).

    for line_no, raw in enumerate(lines):
        masked = masked_lines[line_no]

        # Record let-bindings on this line (in the current top-of-
        # stack scope). We do this BEFORE walking braces so a let
        # on a fn-head line lands in the right scope. NB the fn-head
        # itself doesn't carry a `let`.
        cur_scope = open_stack[-1][0]
        # Skip let-binding capture INSIDE the fn-head line itself —
        # the head doesn't have a body yet.
        if pending_fn is None:
            for m in _LET_BIND_RE.finditer(masked):
                name = m.group(1)
                # Bindings in the FILE scope are top-level `let`
                # declarations. Bindings inside an FN / BLOCK scope
                # land in that scope.
                cur_scope.bindings[name] = line_no

        # Detect an fn head on this line.
        fn_match = _FN_DEF_RE.match(masked)
        if fn_match and open_stack[-1][0].kind == SCOPE_FILE:
            fn_name = fn_match.group(2)
            args_text = fn_match.group(3)
            params = [
                a.strip() for a in args_text.split(",") if a.strip()
            ]
            pending_fn = (fn_name, params, line_no, fn_match.start(2))
            # Register the fn in the FILE scope bindings (fn name ->
            # declaration line). Forward references are allowed by
            # NOVA's hoisting semantics; ``resolve`` returns FILE-
            # scope bindings irrespective of the declaration line.
            file_scope.bindings[fn_name] = line_no

        # Walk the masked line character-by-character to track
        # braces. We open new scopes at ``{`` and close the top
        # scope at ``}``.
        for col, ch in enumerate(masked):
            if ch == "{":
                depth += 1
                if pending_fn is not None:
                    fn_name, params, _hl, _hc = pending_fn
                    fn_scope = Scope(
                        kind=SCOPE_FN,
                        start_line=line_no,
                        start_col=col,
                        end_line=last_line,
                        end_col=last_col,
                        parent=open_stack[-1][0],
                        fn_name=fn_name,
                    )
                    # Parameters land in the fn scope's bindings on the
                    # fn's start line.
                    for p in params:
                        # Strip optional type annotation
                        # (``name: type`` form). Default to the bare
                        # name token before whitespace / ``:``.
                        pname = re.split(r"[:\s]", p, 1)[0]
                        if pname and _IDENT_RE.fullmatch(pname):
                            fn_scope.bindings[pname] = line_no
                    open_stack[-1][0].children.append(fn_scope)
                    open_stack.append((fn_scope, depth))
                    pending_fn = None
                else:
                    # Plain BLOCK scope. Parent is the current top.
                    block_scope = Scope(
                        kind=SCOPE_BLOCK,
                        start_line=line_no,
                        start_col=col,
                        end_line=last_line,
                        end_col=last_col,
                        parent=open_stack[-1][0],
                    )
                    open_stack[-1][0].children.append(block_scope)
                    open_stack.append((block_scope, depth))
            elif ch == "}":
                # Close every scope whose opening depth equals the
                # current depth — there's typically just one but
                # nested ``{ { } }`` close the inner first.
                while (
                    len(open_stack) > 1
                    and open_stack[-1][1] == depth
                ):
                    closing, _ = open_stack.pop()
                    closing.end_line = line_no
                    closing.end_col = col
                depth -= 1
                if depth < 0:
                    depth = 0  # malformed source — degrade gracefully

    # Any scope still open at EOF stays open (its end_line / end_col
    # were initialised to last_line / last_col already — degrade
    # gracefully on malformed input).
    idx = ScopeIndex(
        file_scope=file_scope,
        source=source,
        _lines=lines,
        _masked_lines=masked_lines,
    )
    return idx
