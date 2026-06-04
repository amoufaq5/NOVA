"""`textDocument/prepareRename` + new-name validation for R32E.

The LSP `prepareRename` request lets the server validate the cursor
position BEFORE the editor pops up the rename input box. We return
either:

  * `{ "range": <ident-range>, "placeholder": <current-name> }` when
    the cursor sits on a renameable identifier, OR
  * `null` when the position is not renameable (e.g. cursor inside a
    keyword, comment, string literal, or whitespace).

The `rename` request then has its own pre-flight validation:

  * the new name must be a syntactically valid NOVA identifier, and
  * the new name must not collide with a reserved NOVA keyword.

A clash with an already-declared symbol at the same scope is a
*warning* (per LSP convention: the server still returns the edit so
the user can decide), not a hard refusal — the only hard refusal is
"new name is not a valid identifier" and "new name is a keyword",
both of which return JSON-RPC `-32602 Invalid Params`.

This module is intentionally side-effect free: it operates on raw text
strings + LSP position objects and returns plain Python dicts the
server's dispatcher converts into LSP response messages.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# NOVA reserved keywords — every word that the lexer turns into a
# token-type other than `TOK_IDENT`. Keeping this list explicit (and in
# sync with the lexer) means rename refuses to rewrite a symbol into a
# string the parser would no longer accept as an identifier.
#
# Source of truth: `src/compiler/lexer.nova` keyword table. The
# pessimistic superset below covers every keyword that currently lives
# in the language PLUS several reserved-for-future-use words so the
# server doesn't have to be re-shipped when those land.
NOVA_KEYWORDS: frozenset = frozenset({
    # Declarations.
    "fn", "let", "const", "type", "struct", "enum", "import",
    # Control flow.
    "if", "else", "while", "for", "in", "match", "return", "break",
    "continue",
    # Modifiers / type qualifiers.
    "mut", "pub", "static", "as",
    # Reserved literals.
    "true", "false", "null", "nil", "None",
    # Reserved for future use (so rename doesn't paint us into a
    # corner when the keyword arrives).
    "trait", "impl", "where", "self", "Self", "super", "use",
    "extern", "unsafe", "async", "await", "yield", "move", "ref",
    "box", "dyn",
})


# Valid NOVA identifier: ASCII letter / underscore, followed by letters
# / digits / underscores. Matches `[A-Za-z_][A-Za-z0-9_]*` end-to-end.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_valid_identifier(name: str) -> bool:
    """True iff `name` is a syntactically valid NOVA identifier.

    Leading digit, empty string, embedded whitespace / punctuation all
    return False. Does NOT check the keyword reservation list — that's a
    separate concern (a keyword IS a valid identifier shape; it's just
    semantically reserved by the lexer).
    """
    return bool(name) and bool(_IDENTIFIER_RE.match(name))


def is_keyword(name: str) -> bool:
    """True iff `name` is reserved by the NOVA lexer."""
    return name in NOVA_KEYWORDS


def validate_new_name(name: str) -> Optional[str]:
    """Return None if `name` is an acceptable rename target, otherwise
    a human-readable error message explaining why not.

    The validation is intentionally narrow: shape + keyword check only.
    Same-scope collision detection happens in the rename planner and is
    surfaced as a *warning* in the WorkspaceEdit's wrapper, not as a
    hard reject — the user may legitimately want to shadow a name.
    """
    if not name:
        return "new name is empty"
    if not is_valid_identifier(name):
        return (
            f"`{name}` is not a valid NOVA identifier "
            f"(must match [A-Za-z_][A-Za-z0-9_]*)"
        )
    if is_keyword(name):
        return f"`{name}` is a reserved NOVA keyword"
    return None


# ---------------------------------------------------------------------------
# Identifier-at-position scan.
# ---------------------------------------------------------------------------


def identifier_range_at(
    text: str, line: int, character: int
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return `(name, lsp_range)` for the identifier under (`line`,
    `character`), or `None` if the cursor doesn't sit on a renameable
    identifier.

    Renameable means:
      * The cursor is on (or immediately adjacent to) an identifier
        token — `[A-Za-z_][A-Za-z0-9_]*`.
      * The identifier is NOT inside a comment (`// ...`, `# ...`) or
        a string literal (`"..."`).
      * The identifier itself is NOT a NOVA keyword. Renaming a `fn`
        keyword makes no sense, so prepareRename returns null and the
        editor won't even pop the rename dialog.

    Returns the LSP `Range` (zero-based line + UTF-16 character offset
    — we use UTF-16 code units == Python characters for ASCII-only
    NOVA source, which is what the rest of the LSP already assumes).
    """
    lines = text.splitlines()
    if not (0 <= line < len(lines)):
        return None
    src = lines[line]
    if not (0 <= character <= len(src)):
        return None

    # If the cursor is in a comment or string region of this line,
    # there's nothing to rename.
    if _position_is_in_comment_or_string(src, character):
        return None

    # Walk left + right from the cursor to find identifier boundaries.
    start = character
    while start > 0 and _is_ident_char(src[start - 1]):
        start -= 1
    end = character
    while end < len(src) and _is_ident_char(src[end]):
        end += 1
    if start == end:
        # Cursor is on whitespace / punctuation, not on an identifier.
        return None

    name = src[start:end]
    # Identifiers can't start with a digit; if the boundary walk landed
    # us on a numeric literal (e.g. cursor on `42`), bail out.
    if not is_valid_identifier(name):
        return None
    if is_keyword(name):
        return None

    rng: Dict[str, Any] = {
        "start": {"line": line, "character": start},
        "end": {"line": line, "character": end},
    }
    return name, rng


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _position_is_in_comment_or_string(src: str, character: int) -> bool:
    """Return True if `character` falls inside a `// ...` / `# ...`
    line comment or a double-quoted string literal on `src`.

    Conservative scanner — walks left-to-right tracking string state and
    line-comment markers. Doesn't handle `/* ... */` block comments
    because NOVA's grammar doesn't have them.
    """
    n = len(src)
    if character >= n:
        # Past the last character — treat as in-comment if the line is
        # entirely a comment (cursor parked at the end of a `// foo`
        # line shouldn't fire prepareRename on the empty position).
        # Otherwise we still consider it "not in comment".
        for i, ch in enumerate(src):
            if ch == "/" and i + 1 < n and src[i + 1] == "/":
                if character > i:
                    return True
            if ch == "#":
                if character > i:
                    return True
        return False

    in_string = False
    i = 0
    while i < n:
        ch = src[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                if character == i or character == i + 1:
                    return True
                i += 2
                continue
            if ch == '"':
                if character == i:
                    # Cursor on the closing quote — treat as boundary,
                    # not "inside the string".
                    return False
                in_string = False
                i += 1
                continue
            if character == i:
                return True
            i += 1
            continue
        # Not in a string.
        if ch == '"':
            if character == i:
                return False  # On the opening quote: boundary, not inside.
            in_string = True
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            if character >= i:
                return True
            return False
        if ch == "#":
            if character >= i:
                return True
            return False
        i += 1
    return False


# ---------------------------------------------------------------------------
# Mask comments + strings for whole-line identifier scans.
# ---------------------------------------------------------------------------


def mask_comments_and_strings(line: str) -> str:
    """Replace string-literal and line-comment content with spaces so a
    regex match against the result reports original column offsets but
    never lands inside a quote or comment.

    Mirrors the helper inside `rename_workspace._mask_comments_and_strings`
    but is exposed publicly so other consumers (prepareRename, struct
    field rename, the smoke test suite) can share one implementation.
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
        if ch == "#":
            out.extend(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Function-scope walker — used by local-binding rename to confine edits
# to the enclosing fn body rather than blanket-replacing the whole file.
# ---------------------------------------------------------------------------


_FN_OPEN_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)")


def enclosing_fn_range(text: str, line: int) -> Optional[Tuple[int, int]]:
    """Return `(start_line, end_line)` of the top-level fn that
    contains `line`, or None if `line` sits at file scope.

    Walks the source brace-counting from each `fn NAME(...)` declaration
    so nested blocks (match / if / while / nested fn-likes) are skipped
    correctly. The returned end_line is inclusive — the line containing
    the closing `}` of the fn body.
    """
    lines = text.splitlines()
    # Find every top-level fn def. We define "top-level" as `fn` at
    # column 0 with `(` on the same line — matches the rest of the LSP.
    for i, src in enumerate(lines):
        m = _FN_OPEN_RE.match(src)
        if not m:
            continue
        # Find the opening brace — same line or later.
        open_line = i
        while open_line < len(lines) and "{" not in lines[open_line]:
            open_line += 1
        if open_line >= len(lines):
            continue
        depth = 0
        found_open = False
        end_line = open_line
        for j in range(open_line, len(lines)):
            cleaned = mask_comments_and_strings(lines[j])
            for ch in cleaned:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1
                    if found_open and depth == 0:
                        end_line = j
                        break
            if found_open and depth == 0:
                break
        if i <= line <= end_line:
            return i, end_line
    return None


def scope_constrained_occurrences(
    text: str, name: str, scope: Tuple[int, int]
) -> List[Dict[str, Any]]:
    """Return LSP `Range[]` for every `\\bname\\b` occurrence inside
    the inclusive [start_line, end_line] window, skipping comments +
    strings.

    Used by the local-variable / parameter rename path so renaming
    `parm_a` only touches the parameter binding + its uses in the same
    fn body, never an unrelated `parm_a` in a sibling fn.
    """
    start_line, end_line = scope
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    ranges: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for lineno in range(start_line, min(end_line + 1, len(lines))):
        cleaned = mask_comments_and_strings(lines[lineno])
        for m in pattern.finditer(cleaned):
            ranges.append({
                "start": {"line": lineno, "character": m.start()},
                "end": {"line": lineno, "character": m.end()},
            })
    return ranges


# ---------------------------------------------------------------------------
# Same-scope name-shadowing detector — *warns* without refusing the rename.
# ---------------------------------------------------------------------------


_LET_DECL_RE = re.compile(r"^\s*let\s+(mut\s+)?([A-Za-z_][A-Za-z0-9_]*)\b")
_FN_PARAM_HEAD_RE = re.compile(r"^\s*fn\s+[A-Za-z_][A-Za-z0-9_]*\s*\(([^)]*)\)")


def detect_same_scope_shadow(
    text: str, new_name: str, scope: Tuple[int, int]
) -> Optional[str]:
    """If `new_name` is already declared by a `let` or fn-parameter in
    the same enclosing scope, return a human-readable warning string,
    otherwise None.

    Per LSP convention, the server still emits the edit — the editor
    surfaces the warning in the rename dialog. This lets the user
    rename `let x = ...` to `y` even when a sibling `let y` already
    exists; they presumably know what they're doing.
    """
    start_line, end_line = scope
    lines = text.splitlines()
    head = lines[start_line] if 0 <= start_line < len(lines) else ""
    pm = _FN_PARAM_HEAD_RE.match(head)
    if pm:
        params = pm.group(1)
        for tok in _IDENT_TOKEN_RE.findall(params):
            if tok == new_name:
                return (
                    f"`{new_name}` is already a parameter of the enclosing fn; "
                    f"the rename will shadow it."
                )
    for i in range(start_line, min(end_line + 1, len(lines))):
        cleaned = mask_comments_and_strings(lines[i])
        lm = _LET_DECL_RE.match(cleaned)
        if lm and lm.group(2) == new_name:
            return (
                f"`{new_name}` is already declared via `let` in the same "
                f"scope; the rename will shadow it."
            )
    return None
