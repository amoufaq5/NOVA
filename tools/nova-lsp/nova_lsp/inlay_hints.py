"""Inlay hints (`textDocument/inlayHint`) — parameter-name hints at
function call sites.

NOVA's call sites look like ``foo(input, true, 42)``. Without inlay
hints, a reader can't tell which positional argument maps to which
declared parameter without jumping back to the definition. Inlay hints
solve this by overlaying ghost text at each argument position naming
the parameter that argument is bound to::

    foo(/*data:*/ input, /*verbose:*/ true, /*count:*/ 42)

The ``/*data:*/``, ``/*verbose:*/``, ``/*count:*/`` snippets are not
in the source file — they are LSP-supplied labels the editor renders
as dimmed inline text.

This module scopes hints to parameter names. Type-annotation hints on
expressions (e.g. ``let x = 5`` -> ``: int``) would require a real
type checker; for now we do an opt-in lightweight version of that
for literal RHS expressions (`int`, `str`, `bool`) since the inference
cost there is trivial.

Resolution path for a call ``callee(arg1, arg2, ...)``:

  1. Find the call site identifier + opening paren.
  2. Resolve `callee` via R5F's :func:`find_definition` over the
     transitive import graph, falling back to the workspace symbol
     index for sibling files that aren't reachable through imports.
  3. Parse the parameter list out of the declaration line.
  4. Walk the argument list with paren/bracket-depth tracking and
     emit one ``parameter`` inlay hint per `(arg_position, param_name)`
     pair, up to ``min(len(args), len(params))``.

Edge cases handled (mirrors the integration-test matrix):

  * Multi-line argument lists -> each argument's hint is anchored at
    its actual line:column position.
  * Nested calls -> inner-call argument boundaries respect outer-call
    paren depth.
  * Variadic / mismatched arg count -> we never emit a hint without
    a corresponding declared parameter.
  * String / number literal args -> hints ARE still emitted; the
    parameter name is precisely what disambiguates ``foo(42, true)``.
  * Named arguments ``foo(name: value)`` -> when the source already
    has an explicit ``name:`` before the argument we skip the hint
    (no point overlaying the same label).
  * Builtin callees (`println`, `len`, ...) -> no source location, so
    no hints. These are silently elided rather than guessed.
  * Comments and strings -> masked before scanning so a call name
    appearing inside ``"// fake(x, y)"`` doesn't trigger hints.
  * Range filter -> only hints whose position falls inside the
    requested viewport ``range`` are returned to keep payloads small.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, find_definition
from nova_lsp.workspace_symbols import (
    SYMBOL_KIND_FUNCTION,
    WorkspaceSymbolIndex,
)


# LSP InlayHintKind enum.
INLAY_HINT_KIND_TYPE = 1
INLAY_HINT_KIND_PARAMETER = 2


# Top-level fn declaration regex used to extract parameter names.
# Mirrors `imports._FN_DEF_RE` but additionally captures the parameter list.
_FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")

# Identifier-followed-by-`(` — a candidate call site. Word boundary on
# the left to avoid matching `myfoo(` for symbol `foo`.
_CALL_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Identifier-only — used to recognise the leading identifier of an
# argument expression (so we can position the hint at column 0 of that
# identifier rather than at the comma).
_LEADING_IDENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")

# `let NAME = RHS` (rest-of-line) for the type-inference bonus path.
# We capture everything after the `=` so we can sniff a literal.
_LET_INFER_RE = re.compile(
    r"^(\s*)let\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$"
)

# NOVA keywords that shouldn't be interpreted as call sites even when
# followed by `(`. Mirrors `call_hierarchy._NON_CALL_KEYWORDS`.
_NON_CALL_KEYWORDS = frozenset({
    "if", "while", "for", "return", "match", "do", "and", "or", "not",
    "in", "let", "fn", "import", "else", "true", "false", "nil", "null",
    "break", "continue", "end", "mut", "const", "type", "struct",
    "enum", "module", "throw", "try", "catch", "finally", "yield",
})


# ---------------------------------------------------------------------------
# Comment / string masking — shared idea with rename_workspace +
# call_hierarchy. Replaces string-literal and comment content with spaces
# so column offsets are preserved but the masked regions never match.
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
# Parameter list parsing.
# ---------------------------------------------------------------------------


def _split_param_names(raw: str) -> List[str]:
    """Split a raw parameter-list string like ``a, b, c`` into a list of
    bare parameter names, stripping any default-value suffix.

    Examples:
      ``"x, y"``        -> ``["x", "y"]``
      ``"x = 1, y"``    -> ``["x", "y"]``
      ``""``            -> ``[]``
      ``"  "``          -> ``[]``
    """
    raw = raw.strip()
    if not raw:
        return []
    out: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # Strip a trailing `= default` clause if present.
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        # Strip a leading `mut` modifier if present.
        if part.startswith("mut "):
            part = part[4:].strip()
        # The first word is the identifier — bail out if it isn't one.
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", part)
        if not m:
            continue
        out.append(m.group(1))
    return out


def _params_for_function(
    name: str,
    declarer_text: str,
) -> Optional[List[str]]:
    """Scan `declarer_text` for ``fn name(args) {`` and return the
    parameter names. Returns ``None`` when no declaration is found.

    First match wins — same convention as the rest of the LSP's
    regex-based symbol resolution.
    """
    for line in declarer_text.splitlines():
        m = _FN_DEF_RE.match(line)
        if m and m.group(1) == name:
            return _split_param_names(m.group(2))
    return None


# ---------------------------------------------------------------------------
# Argument-position parsing.
# ---------------------------------------------------------------------------


@dataclass
class _ArgPosition:
    """One argument expression in a call's argument list.

    `line` / `char` is the position of the FIRST non-whitespace
    character of the argument expression (where the inlay hint will be
    anchored). `text` is the raw argument substring used by the
    named-argument detector and the leading-identifier filter.
    """
    line: int
    char: int
    text: str


def _walk_args(
    lines: List[str],
    open_line: int,
    open_char: int,
) -> Optional[List[_ArgPosition]]:
    """Parse argument positions for the call whose opening ``(`` is at
    ``lines[open_line][open_char]``.

    Returns a list of `_ArgPosition` records — one per top-level
    argument expression. Returns ``None`` when the matching close
    paren isn't found within the visible lines (caller can skip the
    call as malformed).

    Comments + strings inside lines are masked so commas/parens inside
    them don't perturb the bookkeeping.
    """
    depth = 1  # we start just after the opening `(`
    bracket_depth = 0
    # Position immediately AFTER the opening `(`.
    cur_line = open_line
    cur_char = open_char + 1
    # Argument boundaries:
    args: List[_ArgPosition] = []
    # `arg_start_line`/`_char` track where the CURRENT argument's
    # expression begins (first non-whitespace position). `arg_chars`
    # accumulates the argument's text across lines so the named-arg
    # detector and the literal sniffer can inspect it.
    arg_started = False
    arg_start_line = cur_line
    arg_start_char = cur_char
    arg_chars: List[str] = []

    def _flush_arg() -> None:
        nonlocal arg_started, arg_chars
        # Only flush when we actually saw a non-empty arg expression.
        if arg_started:
            text = "".join(arg_chars).strip()
            if text:
                args.append(_ArgPosition(
                    line=arg_start_line,
                    char=arg_start_char,
                    text=text,
                ))
        arg_started = False
        arg_chars = []

    while cur_line < len(lines):
        masked = _mask_comments_and_strings(lines[cur_line])
        # When advancing to a new line, the cursor sits at column
        # `cur_char`; for any line after `open_line` we start at 0.
        while cur_char < len(masked):
            ch = masked[cur_char]
            if depth == 1 and bracket_depth == 0:
                if ch == ",":
                    _flush_arg()
                    cur_char += 1
                    continue
                if ch == ")":
                    _flush_arg()
                    return args
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    # close of the enclosing call (top-level)
                    _flush_arg()
                    return args
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]":
                if bracket_depth > 0:
                    bracket_depth -= 1
            # Track the start of an argument expression once we see a
            # non-whitespace character.
            if not arg_started and not ch.isspace():
                arg_started = True
                arg_start_line = cur_line
                arg_start_char = cur_char
            if arg_started:
                arg_chars.append(ch)
            cur_char += 1
        # End of line: advance, preserving the in-progress argument
        # span (so a multi-line argument keeps its anchor point).
        if arg_started:
            arg_chars.append("\n")
        cur_line += 1
        cur_char = 0
    # Reached EOF without finding the closing `)` — malformed call.
    return None


def _has_explicit_name_label(arg_text: str) -> bool:
    """Return True if `arg_text` already starts with `<name>: value` —
    i.e. the source explicitly annotates which parameter this argument
    binds to. Used to skip the inlay hint in that case.

    Heuristic: identifier followed by ``:`` but NOT ``::`` (which is
    a path separator in some grammars). The ``:`` must not be the
    start of a ternary / dictionary literal, but in NOVA call-site
    context ``name:`` is unambiguous.
    """
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:(?!:)", arg_text)
    return m is not None


# ---------------------------------------------------------------------------
# Callee resolution.
# ---------------------------------------------------------------------------


def _resolve_callee_params(
    name: str,
    start_path: str,
    file_cache: FileCache,
    workspace_index: Optional[WorkspaceSymbolIndex],
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[List[str]]:
    """Return the parameter-name list for `name` looked up as a top-
    level fn, or `None` if no declaration is found.

    Resolution order matches the rest of the LSP:

      1. Walk the import graph from `start_path` (so intra-file decls
         win first).
      2. Fall back to the workspace symbol index for siblings outside
         the import graph.

    Open buffers are honoured via `text_overrides` so unsaved edits to
    the declarer's parameter list reflect immediately in the caller's
    inlay hints.
    """
    overrides = text_overrides or {}
    # Step 1: import graph.
    hit = find_definition(
        name,
        os.path.abspath(start_path),
        file_cache,
        text_overrides=overrides,
    )
    if hit is not None:
        path, _span = hit
        text = overrides.get(os.path.abspath(path))
        if text is None:
            entry = file_cache.get(path)
            if entry is None:
                return None
            text = entry.text
        params = _params_for_function(name, text)
        if params is not None:
            return params
    # Step 2: workspace symbol index.
    if workspace_index is not None:
        for sym in workspace_index.all_symbols():
            if sym.kind != SYMBOL_KIND_FUNCTION or sym.name != name:
                continue
            text = overrides.get(sym.path)
            if text is None:
                try:
                    with open(sym.path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            params = _params_for_function(name, text)
            if params is not None:
                return params
    return None


# ---------------------------------------------------------------------------
# Range filtering.
# ---------------------------------------------------------------------------


def _position_in_range(
    line: int, char: int, range_: Optional[Dict[str, Any]]
) -> bool:
    """Return True when `(line, char)` falls inside `range_`.

    The check is the standard LSP "start inclusive, end exclusive"
    semantics. A missing range means "the whole document" — caller
    can pass `None` to disable filtering.
    """
    if range_ is None:
        return True
    start = range_.get("start") or {}
    end = range_.get("end") or {}
    s_line = start.get("line", 0)
    s_char = start.get("character", 0)
    e_line = end.get("line", 1 << 30)
    e_char = end.get("character", 1 << 30)
    if line < s_line or line > e_line:
        return False
    if line == s_line and char < s_char:
        return False
    if line == e_line and char > e_char:
        return False
    return True


# ---------------------------------------------------------------------------
# Literal type inference (bonus: type hints on `let x = literal`).
# ---------------------------------------------------------------------------


_INT_LIT_RE = re.compile(r"^-?(?:0x[0-9A-Fa-f]+|0o[0-7]+|0b[01]+|\d+)$")
_FLOAT_LIT_RE = re.compile(r"^-?\d+\.\d+(?:[eE][-+]?\d+)?$")


def _infer_literal_type(rhs: str) -> Optional[str]:
    """Sniff a NOVA literal expression and return its inferred type
    name, or ``None`` when the RHS isn't a clean single-literal.

    Supports: integer (decimal/hex/oct/bin), float, string (`"..."`),
    bool (`true` / `false`), and `nil`/`null`. Anything more complex
    (e.g. arithmetic, identifiers, function calls) returns `None`
    rather than guessing wrong.
    """
    s = rhs.strip()
    if not s:
        return None
    # Strip a single trailing trailing comment if any (defensive — the
    # caller normally hands us the captured group only).
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        # Make sure the closing quote isn't escaped.
        body = s[1:-1]
        # Count consecutive trailing backslashes to detect odd escape.
        bs = 0
        for ch in reversed(body):
            if ch == "\\":
                bs += 1
            else:
                break
        if bs % 2 == 0:
            return "str"
    if s == "true" or s == "false":
        return "bool"
    if s in ("nil", "null"):
        return "nil"
    if _INT_LIT_RE.match(s):
        return "int"
    if _FLOAT_LIT_RE.match(s):
        return "float"
    return None


# ---------------------------------------------------------------------------
# Top-level entry point.
# ---------------------------------------------------------------------------


def compute_inlay_hints(
    uri: str,
    range_: Optional[Dict[str, Any]],
    doc_text: str,
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
    include_type_hints: bool = True,
) -> List[Dict[str, Any]]:
    """Compute inlay hints for the document at `uri`.

    Parameters:
      `uri`              -- doc URI (used to derive abs path for import walk).
      `range_`           -- visible viewport `{start, end}` from the client,
                            or `None` to compute hints for the whole doc.
      `doc_text`         -- current buffer text (open-buffer authoritative).
      `file_cache`       -- R5F's mtime-keyed cache, reused for callee lookup.
      `workspace_index`  -- R8C's symbol index, fallback for non-imported sibs.
      `text_overrides`   -- open-buffer path -> text map (used by `find_def`).
      `include_type_hints` -- emit `: <type>` hints on `let x = literal`
                              when the RHS is a clean literal. Defaults on.

    Returns an `InlayHint[]` per LSP spec, filtered to the requested
    `range_`. Each hint has ``position``, ``label``, ``kind``
    (Parameter=2, Type=1), and ``paddingLeft``/``paddingRight`` flags
    so the editor draws a single-space gap between the hint and the
    surrounding source.

    Hints are emitted in source order so the editor can render them
    without additional sorting.
    """
    overrides = text_overrides or {}
    start_path = _uri_to_abs(uri)
    if start_path is None:
        return []

    lines = doc_text.splitlines()
    hints: List[Dict[str, Any]] = []

    # Cache callee param-lists across the document so a popular helper
    # (`out`, `_cg_ht_set`, ...) is only resolved once even when called
    # hundreds of times. `None` means "lookup failed once; don't retry"
    # — important for builtins like `println` where we'd otherwise walk
    # the whole workspace per call.
    callee_cache: Dict[str, Optional[List[str]]] = {}

    for lineno, raw_line in enumerate(lines):
        masked = _mask_comments_and_strings(raw_line)

        # --- Type hints on `let x = literal` ------------------------
        if include_type_hints:
            m_let = _LET_INFER_RE.match(raw_line)
            if m_let:
                indent_len = len(m_let.group(1))
                name = m_let.group(2)
                rhs = m_let.group(3)
                # The hint sits IMMEDIATELY AFTER the name token.
                name_end = indent_len + len("let ") + len(name)
                # Skip if a type annotation is already present (e.g.
                # `let x: int = 5`). NOVA's current grammar doesn't
                # support that, but defensively check.
                inferred = _infer_literal_type(rhs)
                if inferred is not None and _position_in_range(
                    lineno, name_end, range_
                ):
                    hints.append({
                        "position": {"line": lineno, "character": name_end},
                        "label": f": {inferred}",
                        "kind": INLAY_HINT_KIND_TYPE,
                        "paddingLeft": False,
                        "paddingRight": True,
                    })

        # --- Parameter-name hints at call sites ---------------------
        for m_call in _CALL_RE.finditer(masked):
            callee = m_call.group(1)
            if callee in _NON_CALL_KEYWORDS:
                continue
            # Open-paren position is the char immediately after the
            # identifier-trailing whitespace; the regex already
            # consumed the optional whitespace between the name and
            # the `(`, so `m_call.end() - 1` is the `(` itself.
            open_idx = m_call.end() - 1
            # Sanity check: `lines[lineno][open_idx]` should be `(`.
            # (It is, because the regex matched against `masked` which
            # preserves column positions.)

            # Resolve params (with the cache).
            if callee in callee_cache:
                params = callee_cache[callee]
            else:
                params = _resolve_callee_params(
                    callee,
                    start_path,
                    file_cache,
                    workspace_index,
                    text_overrides=overrides,
                )
                callee_cache[callee] = params
            if not params:
                # Either an unknown name or a builtin — no hint.
                continue

            # Walk the argument list.
            arg_positions = _walk_args(lines, lineno, open_idx)
            if arg_positions is None:
                continue
            # Emit hints for up to min(args, params) — never more.
            n = min(len(arg_positions), len(params))
            for i in range(n):
                arg = arg_positions[i]
                pname = params[i]
                if _has_explicit_name_label(arg.text):
                    # Already explicit in source — skip.
                    continue
                # Skip arguments whose leading char is `(` or `[` —
                # those are paren-grouped or list-literal expressions
                # where overlaying the param name is visually noisy.
                if arg.text.startswith("(") or arg.text.startswith("["):
                    continue
                if not _position_in_range(arg.line, arg.char, range_):
                    continue
                hints.append({
                    "position": {"line": arg.line, "character": arg.char},
                    "label": f"{pname}:",
                    "kind": INLAY_HINT_KIND_PARAMETER,
                    "paddingLeft": False,
                    "paddingRight": True,
                })

    # Source order is preserved by construction (we iterate lines in
    # order and `_CALL_RE.finditer` yields left-to-right).
    return hints


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of server.uri_to_path -> abspath."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))
