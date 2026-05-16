"""Lexer for the Nova cognitive architecture language."""

from __future__ import annotations

from .tokens import KEYWORDS, Token, TokenType


class LexerError(Exception):
    """Raised when the lexer encounters invalid input."""

    def __init__(self, message: str, line: int, col: int) -> None:
        self.line = line
        self.col = col
        super().__init__(f"{message} at {line}:{col}")


class Lexer:
    """Transforms Nova source code into a stream of tokens."""

    def __init__(self, source: str, *, filename: str = "<input>") -> None:
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        # Stack that tracks whether we are inside string interpolation.
        # Each entry is the brace depth for that interpolation level.
        self._interp_depth: list[int] = []

    # ── helpers ───────────────────────────────────────────────────────

    def _cur(self) -> str:
        return self.source[self.pos] if self.pos < len(self.source) else "\0"

    def _peek(self, offset: int = 1) -> str:
        idx = self.pos + offset
        return self.source[idx] if idx < len(self.source) else "\0"

    def _advance(self) -> str:
        ch = self._cur()
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _match(self, expected: str) -> bool:
        if self._cur() == expected:
            self._advance()
            return True
        return False

    def _emit(self, tt: TokenType, value: str, line: int, col: int) -> None:
        self.tokens.append(Token(tt, value, line, col))

    def _error(self, msg: str) -> LexerError:
        return LexerError(msg, self.line, self.col)

    # ── public API ────────────────────────────────────────────────────

    def tokenize(self) -> list[Token]:
        """Lex the entire source and return the token list."""
        while self.pos < len(self.source):
            self._scan_token()
        self._emit(TokenType.EOF, "", self.line, self.col)
        return self.tokens

    # ── main dispatcher ───────────────────────────────────────────────

    def _scan_token(self) -> None:  # noqa: C901 – necessarily large switch
        ch = self._cur()

        # --- whitespace (skip, but track newlines) --------------------
        if ch in (" ", "\t", "\r"):
            self._advance()
            return
        if ch == "\n":
            line, col = self.line, self.col
            self._advance()
            self._emit(TokenType.NEWLINE, "\n", line, col)
            return

        # --- comments -------------------------------------------------
        if ch == "-":
            nxt = self._peek()
            if nxt == "-":
                self._line_comment()
                return
            if nxt == "{":
                self._block_comment()
                return

        # --- numbers --------------------------------------------------
        if ch.isdigit():
            self._number()
            return

        # --- strings --------------------------------------------------
        if ch == '"':
            self._string()
            return

        # --- identifiers / keywords -----------------------------------
        if ch.isalpha() or ch == "_":
            self._identifier()
            return

        # --- operators & delimiters -----------------------------------
        self._operator_or_delimiter()

    # ── comments ──────────────────────────────────────────────────────

    def _line_comment(self) -> None:
        # skip '--' and everything until end of line
        self._advance()  # -
        self._advance()  # -
        while self.pos < len(self.source) and self._cur() != "\n":
            self._advance()

    def _block_comment(self) -> None:
        # -{ ... }- with nesting support
        self._advance()  # -
        self._advance()  # {
        depth = 1
        while self.pos < len(self.source) and depth > 0:
            c = self._cur()
            nxt = self._peek()
            if c == "-" and nxt == "{":
                depth += 1
                self._advance()
                self._advance()
            elif c == "}" and nxt == "-":
                # Note: closing is }-  (right brace then dash)
                # Actually the spec says -{ }- so closing is }-
                depth -= 1
                self._advance()
                self._advance()
            elif c == "-" and nxt == "}":
                # Handle -} as an alternate interpretation – but spec says -{ }-
                # closing sequence is }-
                self._advance()
            else:
                self._advance()
        if depth > 0:
            raise self._error("Unterminated block comment")

    # ── numbers ───────────────────────────────────────────────────────

    def _number(self) -> None:
        line, col = self.line, self.col
        start = self.pos

        if self._cur() == "0":
            nxt = self._peek()
            if nxt in ("x", "X"):
                self._advance()  # 0
                self._advance()  # x
                if not self._is_hex(self._cur()):
                    raise self._error("Expected hex digit after '0x'")
                while self._is_hex(self._cur()) or self._cur() == "_":
                    self._advance()
                self._emit(TokenType.INT, self.source[start : self.pos], line, col)
                return
            if nxt in ("b", "B"):
                self._advance()  # 0
                self._advance()  # b
                if self._cur() not in ("0", "1"):
                    raise self._error("Expected binary digit after '0b'")
                while self._cur() in ("0", "1", "_"):
                    self._advance()
                self._emit(TokenType.INT, self.source[start : self.pos], line, col)
                return
            if nxt in ("o", "O"):
                self._advance()  # 0
                self._advance()  # o
                if not ("0" <= self._cur() <= "7"):
                    raise self._error("Expected octal digit after '0o'")
                while ("0" <= self._cur() <= "7") or self._cur() == "_":
                    self._advance()
                self._emit(TokenType.INT, self.source[start : self.pos], line, col)
                return

        # Decimal integer or float
        self._decimal_digits()
        is_float = False

        # Fractional part – only if '.' is followed by a digit (avoid consuming '..')
        if self._cur() == "." and self._peek().isdigit():
            is_float = True
            self._advance()  # .
            self._decimal_digits()

        # Exponent
        if self._cur() in ("e", "E"):
            is_float = True
            self._advance()
            if self._cur() in ("+", "-"):
                self._advance()
            if not self._cur().isdigit():
                raise self._error("Expected digit in exponent")
            self._decimal_digits()

        tt = TokenType.FLOAT if is_float else TokenType.INT
        self._emit(tt, self.source[start : self.pos], line, col)

    def _decimal_digits(self) -> None:
        if not self._cur().isdigit():
            return
        while self._cur().isdigit() or self._cur() == "_":
            self._advance()

    @staticmethod
    def _is_hex(ch: str) -> bool:
        return ch.isdigit() or ch in "abcdefABCDEF"

    # ── strings ───────────────────────────────────────────────────────

    def _string(self) -> None:
        """Scan a string literal starting at the opening quote."""
        line, col = self.line, self.col
        self._advance()  # opening "
        buf: list[str] = []

        while self.pos < len(self.source):
            ch = self._cur()

            if ch == '"':
                self._advance()  # closing "
                self._emit(TokenType.STRING, "".join(buf), line, col)
                return

            if ch == "\\":
                buf.append(self._escape())
                continue

            if ch == "{":
                # Emit the string part accumulated so far.
                self._emit(TokenType.STRING, "".join(buf), line, col)
                buf = []
                # Emit interpolation start.
                iline, icol = self.line, self.col
                self._advance()  # {
                self._emit(TokenType.INTERP_START, "{", iline, icol)
                self._interp_depth.append(1)
                return  # Control returns to _scan_token for expr tokens.

            buf.append(self._advance())

        raise self._error("Unterminated string literal")

    def _string_continuation(self) -> None:
        """Resume scanning a string after an interpolation closes."""
        line, col = self.line, self.col
        buf: list[str] = []

        while self.pos < len(self.source):
            ch = self._cur()

            if ch == '"':
                self._advance()
                self._emit(TokenType.STRING, "".join(buf), line, col)
                return

            if ch == "\\":
                buf.append(self._escape())
                continue

            if ch == "{":
                self._emit(TokenType.STRING, "".join(buf), line, col)
                buf = []
                iline, icol = self.line, self.col
                self._advance()
                self._emit(TokenType.INTERP_START, "{", iline, icol)
                self._interp_depth.append(1)
                return

            buf.append(self._advance())

        raise self._error("Unterminated string literal")

    _SIMPLE_ESCAPES = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "\\": "\\",
        '"': '"',
        "{": "{",
        "0": "\0",
    }

    def _escape(self) -> str:
        self._advance()  # backslash
        ch = self._cur()
        if ch in self._SIMPLE_ESCAPES:
            self._advance()
            return self._SIMPLE_ESCAPES[ch]
        raise self._error(f"Invalid escape sequence '\\{ch}'")

    # ── identifiers / keywords ────────────────────────────────────────

    def _identifier(self) -> None:
        line, col = self.line, self.col
        start = self.pos
        while self._cur().isalnum() or self._cur() == "_":
            self._advance()
        text = self.source[start : self.pos]
        tt = KEYWORDS.get(text, TokenType.IDENT)
        self._emit(tt, text, line, col)

    # ── operators & delimiters ────────────────────────────────────────

    def _operator_or_delimiter(self) -> None:  # noqa: C901
        line, col = self.line, self.col
        ch = self._advance()

        match ch:
            # --- arithmetic / assignment ---
            case "+":
                if self._match("="):
                    self._emit(TokenType.PLUS_EQ, "+=", line, col)
                else:
                    self._emit(TokenType.PLUS, "+", line, col)
            case "-":
                if self._match(">"):
                    self._emit(TokenType.ARROW, "->", line, col)
                elif self._match("="):
                    self._emit(TokenType.MINUS_EQ, "-=", line, col)
                else:
                    self._emit(TokenType.MINUS, "-", line, col)
            case "*":
                if self._match("*"):
                    self._emit(TokenType.DSTAR, "**", line, col)
                elif self._match("="):
                    self._emit(TokenType.STAR_EQ, "*=", line, col)
                else:
                    self._emit(TokenType.STAR, "*", line, col)
            case "/":
                if self._match("/"):
                    self._emit(TokenType.DSLASH, "//", line, col)
                elif self._match("="):
                    self._emit(TokenType.SLASH_EQ, "/=", line, col)
                else:
                    self._emit(TokenType.SLASH, "/", line, col)
            case "%":
                self._emit(TokenType.PERCENT, "%", line, col)
            case "@":
                self._emit(TokenType.AT, "@", line, col)

            # --- comparison ---
            case "=":
                if self._match("="):
                    self._emit(TokenType.EQ_EQ, "==", line, col)
                elif self._match(">"):
                    self._emit(TokenType.FAT_ARROW, "=>", line, col)
                else:
                    self._emit(TokenType.EQ, "=", line, col)
            case "!":
                if self._match("="):
                    self._emit(TokenType.BANG_EQ, "!=", line, col)
                else:
                    raise self._error(f"Unexpected character '!'")
            case "<":
                if self._match("="):
                    self._emit(TokenType.LT_EQ, "<=", line, col)
                elif self._match("<"):
                    self._emit(TokenType.LSHIFT, "<<", line, col)
                else:
                    self._emit(TokenType.LT, "<", line, col)
            case ">":
                if self._match("="):
                    self._emit(TokenType.GT_EQ, ">=", line, col)
                elif self._match(">"):
                    self._emit(TokenType.RSHIFT, ">>", line, col)
                else:
                    self._emit(TokenType.GT, ">", line, col)

            # --- bitwise ---
            case "&":
                self._emit(TokenType.AMP, "&", line, col)
            case "|":
                if self._match(">"):
                    self._emit(TokenType.PIPE_ARROW, "|>", line, col)
                else:
                    self._emit(TokenType.PIPE, "|", line, col)
            case "^":
                self._emit(TokenType.CARET, "^", line, col)
            case "~":
                self._emit(TokenType.TILDE, "~", line, col)

            # --- delimiters ---
            case "(":
                self._emit(TokenType.LPAREN, "(", line, col)
            case ")":
                self._emit(TokenType.RPAREN, ")", line, col)
            case "[":
                self._emit(TokenType.LBRACKET, "[", line, col)
            case "]":
                self._emit(TokenType.RBRACKET, "]", line, col)
            case "{":
                if self._interp_depth:
                    self._interp_depth[-1] += 1
                self._emit(TokenType.LBRACE, "{", line, col)
            case "}":
                if self._interp_depth:
                    self._interp_depth[-1] -= 1
                    if self._interp_depth[-1] == 0:
                        self._interp_depth.pop()
                        self._emit(TokenType.INTERP_END, "}", line, col)
                        self._string_continuation()
                        return
                self._emit(TokenType.RBRACE, "}", line, col)
            case ",":
                self._emit(TokenType.COMMA, ",", line, col)
            case ".":
                if self._match("."):
                    self._emit(TokenType.DOT_DOT, "..", line, col)
                else:
                    self._emit(TokenType.DOT, ".", line, col)
            case ":":
                self._emit(TokenType.COLON, ":", line, col)

            case _:
                raise self._error(f"Unexpected character {ch!r}")
