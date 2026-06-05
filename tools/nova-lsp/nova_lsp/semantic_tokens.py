"""Semantic tokens for `textDocument/semanticTokens/full`.

LSP semantic tokens go beyond TextMate regex highlighting by classifying
each identifier with a *token type* (variable, function, type, namespace,
keyword, string, number, comment, operator, parameter, constant) and a
set of *modifiers* (declaration, definition, readonly, static, deprecated).
Editors use this to colour mutable vs immutable bindings differently,
italicise types vs values, fade out deprecated names, etc — strictly more
expressive than TextMate's per-pattern scopes.

The wire format is a delta-compressed flat integer array. For every token
the server emits five ints:

    deltaLine     -- lines since previous token (0 for same line)
    deltaStart    -- start char delta within line OR absolute char on a
                     new line (when deltaLine > 0)
    length        -- token length in characters
    tokenType     -- index into TOKEN_TYPES
    modifierBits  -- bitmask over TOKEN_MODIFIERS

For example, two tokens on lines 2 and 5 both starting at col 4 with type
indices 0 and 1 and no modifiers emit::

    [2, 4, 3, 0, 0,   # first token: jump 2 lines, col 4, len 3, type 0
     3, 4, 3, 1, 0]   # second token: jump 3 more lines, col 4, len 3, type 1

The tokenizer is a single-pass scanner that walks `text` character by
character (NOT a regex). It tracks line/column position, handles
double-quoted strings (including triple-quoted multi-line variants),
`//` line and `/* */` block comments, numbers (decimal, hex `0x`,
octal `0o`, binary `0b`), identifiers, and operators. It then classifies each identifier by looking at the
surrounding tokens (`fn NAME`, `let NAME`, `NAME (` for calls, etc) and
optionally consulting a `WorkspaceSymbolIndex` so cross-file fn / type
references resolve correctly.

Tokens are emitted in source order. The same identifier appearing twice
emits two tokens; modifiers vary (`declaration` on the first, no
modifiers on call sites).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set


# ---------------------------------------------------------------------------
# Legend: token types + modifiers reported to the client at `initialize`
# time. The client uses these arrays to convert the integer indices in the
# wire format back into semantic categories.
# ---------------------------------------------------------------------------


TOKEN_TYPES: List[str] = [
    "variable",     # 0  - non-readonly bindings (let in some langs, mut, params, etc)
    "function",     # 1  - fn declarations + call sites
    "type",         # 2  - type declarations + uses
    "namespace",    # 3  - import paths, module names
    "keyword",      # 4  - fn / let / if / while / ...
    "string",       # 5  - "..." / """..."""
    "number",       # 6  - 42 / 3.14 / 0xff
    "comment",      # 7  - // ... / /* ... */
    "operator",     # 8  - + - * / == etc (currently not emitted, reserved)
    "parameter",    # 9  - function parameters in their declaration position
    "constant",     # 10 - const X = ... + ALL_CAPS lets
    "class",        # 11 - `struct` names + struct-literal head `Foo { ... }`
    "property",     # 12 - field access after `.`, e.g. `obj.field`
]

TOKEN_MODIFIERS: List[str] = [
    "declaration",  # 0 - bit 0 (1) - the definition site of a symbol
    "definition",   # 1 - bit 1 (2) - alias for declaration (some clients prefer this)
    "readonly",     # 2 - bit 2 (4) - `let` / `const` (NOVA `let` is immutable)
    "static",       # 3 - bit 3 (8) - `const` declarations
    "deprecated",   # 4 - bit 4 (16)- @deprecated marker (reserved for future use)
]


# Convenience type-index constants — referenced from tests.
TYPE_VARIABLE = 0
TYPE_FUNCTION = 1
TYPE_TYPE = 2
TYPE_NAMESPACE = 3
TYPE_KEYWORD = 4
TYPE_STRING = 5
TYPE_NUMBER = 6
TYPE_COMMENT = 7
TYPE_OPERATOR = 8
TYPE_PARAMETER = 9
TYPE_CONSTANT = 10
TYPE_CLASS = 11
TYPE_PROPERTY = 12

MOD_DECLARATION = 1 << 0
MOD_DEFINITION = 1 << 1
MOD_READONLY = 1 << 2
MOD_STATIC = 1 << 3
MOD_DEPRECATED = 1 << 4


# NOVA keywords. Source: src/compiler/lexer.nova + parser.nova + our
# existing code-action `_KEYWORDS` set. Keeping these in one place so the
# classifier's "is this an identifier or a keyword?" check is consistent.
KEYWORDS: Set[str] = {
    "fn", "let", "const", "type", "mut",
    "if", "else", "while", "for", "do", "end",
    "match", "case",
    "return", "break", "continue", "throw", "try", "catch", "finally", "defer",
    "import", "module", "extern", "as",
    "true", "false", "nil", "null",
    "and", "or", "not", "in", "is",
    "struct", "enum", "lambda",
    "asm",
}

# Keywords that introduce a top-level declaration where the NEXT identifier
# is the bound name (so we tag it `declaration`).
DECL_KEYWORDS: Set[str] = {"fn", "let", "const", "type", "struct", "enum", "module", "mut"}


# ---------------------------------------------------------------------------
# Tokens.
# ---------------------------------------------------------------------------


@dataclass
class SemanticToken:
    """One semantic token. Coordinates are zero-based, character offsets
    are UTF-16 code units per LSP spec (we approximate with Python str
    length — fine for ASCII; an LSP client that needs strict UTF-16 can
    re-measure on its side)."""
    line: int
    start_char: int
    length: int
    token_type: int
    modifier_bits: int = 0

    def with_modifiers(self, bits: int) -> "SemanticToken":
        return SemanticToken(
            line=self.line,
            start_char=self.start_char,
            length=self.length,
            token_type=self.token_type,
            modifier_bits=self.modifier_bits | bits,
        )


# ---------------------------------------------------------------------------
# Tokenizer.
# ---------------------------------------------------------------------------


@dataclass
class _RawToken:
    """Internal pre-classification token. We classify in a second pass so
    we have lookahead and lookbehind across the whole document."""
    kind: str          # "ident", "string", "number", "comment", "punct", "newline"
    text: str
    line: int
    col: int

    @property
    def length(self) -> int:
        return len(self.text)


class SemanticTokenizer:
    """Walks a NOVA source file and emits classified semantic tokens.

    Pass `known_functions` / `known_types` to classify identifiers that
    were declared in *other* files (typically from R8C's workspace symbol
    index). Without those hints we still classify call-site identifiers
    via the syntactic rule `NAME(` -> function, but plain
    references-by-name fall back to `variable`.
    """

    def __init__(
        self,
        text: str,
        *,
        known_functions: Optional[Set[str]] = None,
        known_types: Optional[Set[str]] = None,
        known_constants: Optional[Set[str]] = None,
    ) -> None:
        self.text = text
        self.known_functions: Set[str] = set(known_functions or ())
        self.known_types: Set[str] = set(known_types or ())
        self.known_constants: Set[str] = set(known_constants or ())

    # ------------------------------------------------------------------
    # Raw scan: walk the source byte-by-byte producing _RawToken entries.
    # ------------------------------------------------------------------

    def _scan_raw(self) -> List[_RawToken]:
        out: List[_RawToken] = []
        text = self.text
        n = len(text)
        i = 0
        line = 0
        col = 0

        def at(off: int) -> str:
            j = i + off
            return text[j] if 0 <= j < n else ""

        while i < n:
            ch = text[i]

            # Newline
            if ch == "\n":
                i += 1
                line += 1
                col = 0
                continue
            # Carriage return — treat \r\n as a single newline
            if ch == "\r":
                i += 1
                if at(0) == "\n":
                    i += 1
                line += 1
                col = 0
                continue

            # Whitespace (spaces, tabs, etc.)
            if ch.isspace():
                i += 1
                col += 1
                continue

            # Line comment: // ...
            if ch == "/" and at(1) == "/":
                start_col = col
                start_i = i
                while i < n and text[i] != "\n":
                    i += 1
                    col += 1
                out.append(_RawToken("comment", text[start_i:i], line, start_col))
                continue

            # Block comment: /* ... */ (may span multiple lines — we emit
            # one comment token per line slice so the wire format stays
            # tractable; semantic-tokens spec doesn't allow a single token
            # to cross line boundaries).
            if ch == "/" and at(1) == "*":
                # Emit slices line by line.
                start_col = col
                start_i = i
                i += 2
                col += 2
                while i < n:
                    if text[i] == "*" and at(1) == "/":
                        i += 2
                        col += 2
                        # final line slice
                        out.append(_RawToken("comment", text[start_i:i], line, start_col))
                        break
                    if text[i] == "\n":
                        # close the current-line slice, advance line
                        out.append(_RawToken("comment", text[start_i:i], line, start_col))
                        i += 1
                        line += 1
                        col = 0
                        start_i = i
                        start_col = 0
                        continue
                    i += 1
                    col += 1
                else:
                    # EOF inside /* ... */ — emit what we have.
                    out.append(_RawToken("comment", text[start_i:i], line, start_col))
                continue

            # String literal: "..." or """..."""
            if ch == '"':
                triple = at(1) == '"' and at(2) == '"'
                start_col = col
                start_i = i
                if triple:
                    i += 3
                    col += 3
                    while i < n:
                        if text[i] == '"' and at(1) == '"' and at(2) == '"':
                            i += 3
                            col += 3
                            out.append(_RawToken(
                                "string", text[start_i:i], line, start_col
                            ))
                            break
                        if text[i] == "\n":
                            # close current-line slice and start a fresh
                            # one on the next line (same handling as
                            # block comments).
                            out.append(_RawToken(
                                "string", text[start_i:i], line, start_col
                            ))
                            i += 1
                            line += 1
                            col = 0
                            start_i = i
                            start_col = 0
                            continue
                        i += 1
                        col += 1
                    else:
                        out.append(_RawToken(
                            "string", text[start_i:i], line, start_col
                        ))
                else:
                    i += 1
                    col += 1
                    while i < n and text[i] != '"':
                        if text[i] == "\\" and i + 1 < n:
                            # escape sequence (e.g. \n, \"); consume both
                            i += 2
                            col += 2
                            continue
                        if text[i] == "\n":
                            # unterminated string — close it at end of line
                            break
                        i += 1
                        col += 1
                    if i < n and text[i] == '"':
                        i += 1
                        col += 1
                    out.append(_RawToken("string", text[start_i:i], line, start_col))
                continue

            # Number literal: 0x... / 0o... / 0b... / decimal / float
            if ch.isdigit():
                start_col = col
                start_i = i
                if ch == "0" and at(1) in ("x", "X", "o", "O", "b", "B"):
                    i += 2
                    col += 2
                    while i < n and (text[i].isalnum() or text[i] == "_"):
                        i += 1
                        col += 1
                else:
                    while i < n and (text[i].isdigit() or text[i] == "_"):
                        i += 1
                        col += 1
                    # Floating point: 3.14 (but not 3..7 ranges if NOVA
                    # had them; safe because we require a digit after `.`).
                    if i < n and text[i] == "." and i + 1 < n and text[i + 1].isdigit():
                        i += 1
                        col += 1
                        while i < n and (text[i].isdigit() or text[i] == "_"):
                            i += 1
                            col += 1
                    # Scientific: 1e5 / 2.5e-3
                    if i < n and text[i] in ("e", "E"):
                        i += 1
                        col += 1
                        if i < n and text[i] in ("+", "-"):
                            i += 1
                            col += 1
                        while i < n and text[i].isdigit():
                            i += 1
                            col += 1
                out.append(_RawToken("number", text[start_i:i], line, start_col))
                continue

            # Identifier or keyword: [A-Za-z_][A-Za-z0-9_]*
            if ch.isalpha() or ch == "_":
                start_col = col
                start_i = i
                while i < n and (text[i].isalnum() or text[i] == "_"):
                    i += 1
                    col += 1
                out.append(_RawToken("ident", text[start_i:i], line, start_col))
                continue

            # Anything else — single-char punctuation. Multi-char
            # operators (==, !=, ~>) emit multiple punct tokens; the
            # classifier collapses them when relevant. We don't emit
            # semantic tokens for operators in the default pass.
            out.append(_RawToken("punct", ch, line, col))
            i += 1
            col += 1

        return out

    # ------------------------------------------------------------------
    # Classification pass.
    # ------------------------------------------------------------------

    def tokenize(self) -> List[SemanticToken]:
        raws = self._scan_raw()
        out: List[SemanticToken] = []

        def is_word(name: str) -> bool:
            return bool(name) and (name[0].isalpha() or name[0] == "_")

        i = 0
        n = len(raws)

        # Per-buffer sets so subsequent uses of a declared symbol get the
        # right type even without a workspace index. E.g. once we see
        # `fn foo()` we add "foo" to `local_functions`.
        local_functions: Set[str] = set()
        local_types: Set[str] = set()
        local_constants: Set[str] = set()
        local_params: Set[str] = set()
        local_variables: Set[str] = set()

        # Raw-token indices whose semantic token was already emitted out
        # of band (e.g. fn-parameter identifiers handled by the
        # `_annotate_fn_params` helper while processing `fn NAME`). The
        # main loop skips these to avoid duplicate emissions.
        consumed_raw: Set[int] = set()

        # Identifiers used in `import "X"` form a separate namespace bucket
        # but the string IS the namespace — we still emit the string as
        # `namespace + string` so the editor can colour it distinctively.

        while i < n:
            if i in consumed_raw:
                i += 1
                continue
            tok = raws[i]

            # ---------------- comments --------------------------------
            if tok.kind == "comment":
                out.append(SemanticToken(
                    line=tok.line,
                    start_char=tok.col,
                    length=tok.length,
                    token_type=TYPE_COMMENT,
                ))
                i += 1
                continue

            # ---------------- numbers ---------------------------------
            if tok.kind == "number":
                out.append(SemanticToken(
                    line=tok.line,
                    start_char=tok.col,
                    length=tok.length,
                    token_type=TYPE_NUMBER,
                ))
                i += 1
                continue

            # ---------------- strings ---------------------------------
            if tok.kind == "string":
                # Was this string the argument of an `import` keyword?
                # If yes we mark it as `namespace` (still a string token
                # type — clients can pick either). We choose `namespace`
                # because it's semantically more useful for navigation.
                is_namespace = (
                    out
                    and out[-1].token_type == TYPE_KEYWORD
                    and i > 0
                    and raws[i - 1].kind == "ident"
                    and raws[i - 1].text == "import"
                )
                ttype = TYPE_NAMESPACE if is_namespace else TYPE_STRING
                out.append(SemanticToken(
                    line=tok.line,
                    start_char=tok.col,
                    length=tok.length,
                    token_type=ttype,
                ))
                i += 1
                continue

            # ---------------- identifiers + keywords ------------------
            if tok.kind == "ident":
                name = tok.text

                # Keywords classify trivially.
                if name in KEYWORDS:
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_KEYWORD,
                    ))
                    i += 1
                    continue

                # Decide what this identifier IS:
                #   * Preceded by a decl keyword -> declaration
                #   * Inside a fn(...) param list -> parameter (declaration)
                #   * Followed by `(` (call site) -> function
                #   * In `known_functions` / `known_types` / `known_constants`
                #     -> classified accordingly
                #   * Otherwise -> variable

                prev_ident = _previous_ident(raws, i)
                next_punct = _next_punct(raws, i)
                prev_punct = _previous_punct(raws, i)
                kind_kw = prev_ident.text if prev_ident else None

                # Special-case `mut x` introduces a writeable variable
                # declaration with no readonly modifier.
                if kind_kw == "fn":
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_FUNCTION,
                        modifier_bits=MOD_DECLARATION | MOD_DEFINITION,
                    ))
                    local_functions.add(name)
                    # Following `(`...`)` — emit parameters too. The
                    # helper marks each consumed raw-token index so the
                    # main loop below won't re-emit them.
                    _annotate_fn_params(raws, i, out, local_params, consumed_raw)
                    i += 1
                    continue

                if kind_kw == "let":
                    bits = MOD_DECLARATION | MOD_DEFINITION | MOD_READONLY
                    is_all_caps = bool(name) and name == name.upper() and any(
                        c.isalpha() for c in name
                    )
                    if is_all_caps:
                        token_type = TYPE_CONSTANT
                        local_constants.add(name)
                    else:
                        token_type = TYPE_VARIABLE
                        local_variables.add(name)
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=token_type,
                        modifier_bits=bits,
                    ))
                    i += 1
                    continue

                if kind_kw == "const":
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_CONSTANT,
                        modifier_bits=(
                            MOD_DECLARATION | MOD_DEFINITION
                            | MOD_READONLY | MOD_STATIC
                        ),
                    ))
                    local_constants.add(name)
                    i += 1
                    continue

                if kind_kw in ("type", "struct", "enum"):
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_TYPE,
                        modifier_bits=MOD_DECLARATION | MOD_DEFINITION,
                    ))
                    local_types.add(name)
                    i += 1
                    continue

                if kind_kw == "module":
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_NAMESPACE,
                        modifier_bits=MOD_DECLARATION | MOD_DEFINITION,
                    ))
                    i += 1
                    continue

                if kind_kw == "mut":
                    # `mut x = ...` — writeable variable declaration.
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_VARIABLE,
                        modifier_bits=MOD_DECLARATION | MOD_DEFINITION,
                    ))
                    local_variables.add(name)
                    i += 1
                    continue

                # Field access: `obj.field` -> classify `field` as property.
                # We check the immediately-preceding punctuation token; if
                # it's a `.` we treat this ident as a property reference.
                # This must run BEFORE the type / function / variable
                # branches so member names don't accidentally land in
                # `known_functions` or `known_types` (they share namespaces
                # with top-level symbols in NOVA's flat module layout).
                if prev_punct == ".":
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_PROPERTY,
                    ))
                    i += 1
                    continue

                # Type annotation slot: `let x: Foo = ...`, `fn f(x: Foo)`,
                # `Box<Foo>`. Heuristic: a capitalised identifier (first
                # char uppercase letter) preceded by `:` or `<` is most
                # likely a type reference. We only apply the rule when the
                # name actually looks like a type to limit false positives
                # in dict-literal-ish contexts (`{key: value}`).
                if prev_punct in (":", "<") and name and name[0].isupper():
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_TYPE,
                    ))
                    i += 1
                    continue

                # Struct-literal head: `Foo { ... }` — a capitalised
                # identifier immediately followed by `{` is the type being
                # constructed. We emit it as TYPE_TYPE so editors colour
                # the constructor consistently with type annotations.
                if next_punct == "{" and name and name[0].isupper():
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_TYPE,
                    ))
                    i += 1
                    continue

                # USE site (not a declaration).
                # 1) Followed by `(` -> function call.
                # 2) Match against known buckets.
                if next_punct == "(":
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_FUNCTION,
                    ))
                    i += 1
                    continue

                if name in local_functions or name in self.known_functions:
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_FUNCTION,
                    ))
                    i += 1
                    continue

                if name in local_types or name in self.known_types:
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_TYPE,
                    ))
                    i += 1
                    continue

                if name in local_constants or name in self.known_constants:
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_CONSTANT,
                        modifier_bits=MOD_READONLY,
                    ))
                    i += 1
                    continue

                if name in local_params:
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_PARAMETER,
                    ))
                    i += 1
                    continue

                # All-caps identifiers that we haven't seen declared are
                # almost always constants (e.g. exported sibling-module
                # consts). Mark them as constants without `declaration`.
                if name == name.upper() and any(c.isalpha() for c in name):
                    out.append(SemanticToken(
                        line=tok.line,
                        start_char=tok.col,
                        length=tok.length,
                        token_type=TYPE_CONSTANT,
                        modifier_bits=MOD_READONLY,
                    ))
                    i += 1
                    continue

                # Default: variable reference.
                out.append(SemanticToken(
                    line=tok.line,
                    start_char=tok.col,
                    length=tok.length,
                    token_type=TYPE_VARIABLE,
                ))
                i += 1
                continue

            # ---------------- punctuation (skipped) -------------------
            # We don't currently emit operator tokens — keeping the token
            # stream small makes the wire payload cheaper. The OPERATOR
            # type index is reserved so we can opt-in later.
            i += 1

        return out


# ---------------------------------------------------------------------------
# Wire-format helpers.
# ---------------------------------------------------------------------------


def tokens_to_lsp_array(tokens: Iterable[SemanticToken]) -> List[int]:
    """Delta-compress `tokens` into the flat int array LSP expects.

    Per spec each token contributes 5 ints in source order:

        [deltaLine, deltaStart, length, tokenType, tokenModifiers]

    `deltaLine` is the gap since the previous token's line; `deltaStart`
    is the gap within the same line (`deltaLine == 0`) or the absolute
    char on a new line (`deltaLine > 0`). The first token's deltas are
    measured from `(0, 0)`.

    Tokens must be sorted by (line, start_char); we sort defensively so
    callers don't have to.
    """
    sorted_tokens = sorted(tokens, key=lambda t: (t.line, t.start_char))
    data: List[int] = []
    prev_line = 0
    prev_start = 0
    for t in sorted_tokens:
        delta_line = t.line - prev_line
        if delta_line == 0:
            delta_start = t.start_char - prev_start
        else:
            delta_start = t.start_char
        # Defensive: deltas must be non-negative.
        if delta_line < 0 or delta_start < 0:
            continue
        data.extend([delta_line, delta_start, t.length, t.token_type, t.modifier_bits])
        prev_line = t.line
        prev_start = t.start_char
    return data


def semantic_tokens_legend() -> dict:
    """Return the legend payload for `semanticTokensProvider` registration."""
    return {
        "tokenTypes": list(TOKEN_TYPES),
        "tokenModifiers": list(TOKEN_MODIFIERS),
    }


# ---------------------------------------------------------------------------
# Helpers for the classification pass — kept module-level so unit tests
# can poke at them directly.
# ---------------------------------------------------------------------------


def _previous_ident(raws: List[_RawToken], idx: int) -> Optional[_RawToken]:
    """Return the identifier token immediately preceding `raws[idx]`,
    skipping punctuation and whitespace. None if there is none on the
    same statement (we stop at newlines unconditionally — most NOVA
    statements are line-terminated)."""
    j = idx - 1
    while j >= 0:
        t = raws[j]
        if t.kind == "ident":
            return t
        # Stop early on a newline or semicolon-equivalent — declarations
        # don't span statements.
        if t.kind == "comment":
            j -= 1
            continue
        if t.kind == "punct" and t.text in (";", "{", "}"):
            return None
        j -= 1
    return None


def _next_punct(raws: List[_RawToken], idx: int) -> Optional[str]:
    """Return the next punctuation character after `raws[idx]`, skipping
    whitespace and comments. None if EOF or a newline-terminated
    statement gives no punctuation before the next ident."""
    j = idx + 1
    while j < len(raws):
        t = raws[j]
        if t.kind == "punct":
            return t.text
        if t.kind == "comment":
            j += 1
            continue
        # Hitting another token kind first means the punct check fails.
        return None
    return None


def _previous_punct(raws: List[_RawToken], idx: int) -> Optional[str]:
    """Return the punctuation character immediately preceding `raws[idx]`.

    Unlike `_previous_ident`, this looks at the raw-token *right before*
    the current one (skipping only comments), so `obj.field` -> `.` and
    `let x: Foo` -> `:` for the `Foo` ident. We stop at any other ident
    or non-punct token because we only care about the *immediate*
    predecessor — `.x` and ` .x` both classify, but `.x + y` does not
    classify `y` as a property.
    """
    j = idx - 1
    while j >= 0:
        t = raws[j]
        if t.kind == "punct":
            return t.text
        if t.kind == "comment":
            j -= 1
            continue
        # Any other token kind interrupts the immediate-predecessor chain.
        return None
    return None


def _annotate_fn_params(
    raws: List[_RawToken],
    name_idx: int,
    out: List[SemanticToken],
    local_params: Set[str],
    consumed_raw: Set[int],
) -> None:
    """After `fn NAME` was emitted at `out[-1]`, scan forward through the
    `(arg, arg, ...)` list and emit a TYPE_PARAMETER token for each
    parameter identifier. Updates `local_params` so the function body's
    references to those names classify as parameters. Raw-token indices
    consumed here are added to `consumed_raw` so the main loop's
    per-ident pass doesn't re-emit them as a default `variable`."""
    j = name_idx + 1
    n = len(raws)
    # Find the opening `(`.
    while j < n and not (raws[j].kind == "punct" and raws[j].text == "("):
        # If we run into another ident before the paren, there's no
        # parameter list — bail.
        if raws[j].kind == "ident":
            return
        j += 1
    if j >= n:
        return
    j += 1
    depth = 1
    while j < n and depth > 0:
        t = raws[j]
        if t.kind == "punct":
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
                if depth == 0:
                    return
            j += 1
            continue
        if t.kind == "ident":
            # First ident after `(` or `,` is a parameter name. Identifiers
            # following `:` (type annotations) are TYPE references — we
            # don't currently emit them as parameters.
            prev_punct = None
            k = j - 1
            while k >= 0:
                p = raws[k]
                if p.kind == "punct":
                    prev_punct = p.text
                    break
                if p.kind == "ident":
                    prev_punct = None
                    break
                k -= 1
            if prev_punct in ("(", ","):
                out.append(SemanticToken(
                    line=t.line,
                    start_char=t.col,
                    length=t.length,
                    token_type=TYPE_PARAMETER,
                    modifier_bits=MOD_DECLARATION | MOD_DEFINITION,
                ))
                local_params.add(t.text)
                consumed_raw.add(j)
            elif prev_punct == ":":
                # Type annotation. Emit as TYPE reference.
                out.append(SemanticToken(
                    line=t.line,
                    start_char=t.col,
                    length=t.length,
                    token_type=TYPE_TYPE,
                ))
                consumed_raw.add(j)
        j += 1
