"""Nova lexer — converts source text into a stream of tokens."""

from ..tokens import Token, TokenType, KEYWORDS


class LexerError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"LexerError at L{line}:{col}: {message}")
        self.line = line
        self.col = col


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        if i < len(self.source):
            return self.source[i]
        return "\0"

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _match(self, expected: str) -> bool:
        if self.pos < len(self.source) and self.source[self.pos] == expected:
            self._advance()
            return True
        return False

    def _error(self, msg: str):
        raise LexerError(msg, self.line, self.col)

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.source):
            self._skip_whitespace_and_comments()
            if self.pos >= len(self.source):
                break

            line, col = self.line, self.col
            ch = self._peek()

            if ch.isdigit():
                self.tokens.append(self._read_number(line, col))
            elif ch.isalpha() or ch == "_":
                self.tokens.append(self._read_ident(line, col))
            elif ch in ('"', "'"):
                self.tokens.append(self._read_string(line, col))
            else:
                self.tokens.append(self._read_symbol(line, col))

        self.tokens.append(Token(TokenType.EOF, None, self.line, self.col))
        return self.tokens

    def _skip_whitespace_and_comments(self):
        while self.pos < len(self.source):
            ch = self._peek()
            if ch in (" ", "\t", "\r", "\n"):
                self._advance()
            elif ch == "-" and self._peek(1) == "-":
                if self._peek(2) == "{":
                    self._skip_block_comment()
                else:
                    while self.pos < len(self.source) and self._peek() != "\n":
                        self._advance()
            elif ch == "-" and self._peek(1) == "{":
                self._skip_block_comment()
            else:
                break

    def _skip_block_comment(self):
        self._advance()  # -
        self._advance()  # {
        depth = 1
        while self.pos < len(self.source) and depth > 0:
            if self._peek() == "-" and self._peek(1) == "{":
                depth += 1
                self._advance()
                self._advance()
            elif self._peek() == "}" and self._peek(1) == "-":
                depth -= 1
                self._advance()
                self._advance()
            else:
                self._advance()
        if depth != 0:
            self._error("Unterminated block comment")

    def _read_number(self, line: int, col: int) -> Token:
        start = self.pos
        if self._peek() == "0" and self._peek(1) in ("x", "X", "b", "B", "o", "O"):
            self._advance()
            self._advance()
            while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
                self._advance()
            text = self.source[start:self.pos].replace("_", "")
            return Token(TokenType.INT, int(text, 0), line, col)

        is_float = False
        while self.pos < len(self.source) and (self._peek().isdigit() or self._peek() == "_"):
            self._advance()

        if self._peek() == "." and self._peek(1) != ".":
            is_float = True
            self._advance()
            while self.pos < len(self.source) and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()

        if self._peek() in ("e", "E"):
            is_float = True
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            while self.pos < len(self.source) and self._peek().isdigit():
                self._advance()

        text = self.source[start:self.pos].replace("_", "")
        if is_float:
            return Token(TokenType.FLOAT, float(text), line, col)
        return Token(TokenType.INT, int(text), line, col)

    def _read_ident(self, line: int, col: int) -> Token:
        start = self.pos
        while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.source[start:self.pos]
        ttype = KEYWORDS.get(text, TokenType.IDENT)
        return Token(ttype, text, line, col)

    def _read_string(self, line: int, col: int) -> Token:
        quote = self._advance()
        if self._peek() == quote and self._peek(1) == quote:
            self._advance()
            self._advance()
            return self._read_triple_string(quote, line, col)

        chars: list[str] = []
        while self.pos < len(self.source) and self._peek() != quote:
            if self._peek() == "\n":
                self._error("Unterminated string literal")
            if self._peek() == "\\":
                self._advance()
                chars.append(self._read_escape())
            else:
                chars.append(self._advance())
        if self.pos >= len(self.source):
            self._error("Unterminated string literal")
        self._advance()
        return Token(TokenType.STRING, "".join(chars), line, col)

    def _read_triple_string(self, quote: str, line: int, col: int) -> Token:
        chars: list[str] = []
        while self.pos < len(self.source):
            if self._peek() == quote and self._peek(1) == quote and self._peek(2) == quote:
                self._advance()
                self._advance()
                self._advance()
                return Token(TokenType.STRING, "".join(chars), line, col)
            chars.append(self._advance())
        self._error("Unterminated triple-quoted string")

    def _read_escape(self) -> str:
        ch = self._advance()
        escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}
        if ch in escapes:
            return escapes[ch]
        if ch == "x":
            h = self._advance() + self._advance()
            return chr(int(h, 16))
        self._error(f"Unknown escape sequence: \\{ch}")

    def _read_symbol(self, line: int, col: int) -> Token:
        ch = self._advance()
        match ch:
            case "+":
                if self._match("="):
                    return Token(TokenType.PLUS_EQ, "+=", line, col)
                return Token(TokenType.PLUS, "+", line, col)
            case "-":
                if self._match(">"):
                    return Token(TokenType.ARROW, "->", line, col)
                if self._match("="):
                    return Token(TokenType.MINUS_EQ, "-=", line, col)
                return Token(TokenType.MINUS, "-", line, col)
            case "*":
                if self._match("*"):
                    return Token(TokenType.DSTAR, "**", line, col)
                if self._match("="):
                    return Token(TokenType.STAR_EQ, "*=", line, col)
                return Token(TokenType.STAR, "*", line, col)
            case "/":
                if self._match("/"):
                    return Token(TokenType.DSLASH, "//", line, col)
                if self._match("="):
                    return Token(TokenType.SLASH_EQ, "/=", line, col)
                return Token(TokenType.SLASH, "/", line, col)
            case "%":
                if self._match("="):
                    return Token(TokenType.PERCENT_EQ, "%=", line, col)
                return Token(TokenType.PERCENT, "%", line, col)
            case "@":
                return Token(TokenType.AT, "@", line, col)
            case "=":
                if self._match("="):
                    return Token(TokenType.EQ, "==", line, col)
                if self._match(">"):
                    return Token(TokenType.FAT_ARROW, "=>", line, col)
                return Token(TokenType.ASSIGN, "=", line, col)
            case "!":
                if self._match("="):
                    return Token(TokenType.NEQ, "!=", line, col)
                self._error("Unexpected character: !")
            case "<":
                if self._match("<"):
                    return Token(TokenType.LSHIFT, "<<", line, col)
                if self._match("="):
                    return Token(TokenType.LTE, "<=", line, col)
                return Token(TokenType.LT, "<", line, col)
            case ">":
                if self._match(">"):
                    return Token(TokenType.RSHIFT, ">>", line, col)
                if self._match("="):
                    return Token(TokenType.GTE, ">=", line, col)
                return Token(TokenType.GT, ">", line, col)
            case "&":
                return Token(TokenType.AMP, "&", line, col)
            case "|":
                if self._match(">"):
                    return Token(TokenType.PIPE_ARROW, "|>", line, col)
                return Token(TokenType.PIPE, "|", line, col)
            case "^":
                return Token(TokenType.CARET, "^", line, col)
            case "~":
                return Token(TokenType.TILDE, "~", line, col)
            case "(":
                return Token(TokenType.LPAREN, "(", line, col)
            case ")":
                return Token(TokenType.RPAREN, ")", line, col)
            case "[":
                return Token(TokenType.LBRACKET, "[", line, col)
            case "]":
                return Token(TokenType.RBRACKET, "]", line, col)
            case "{":
                return Token(TokenType.LBRACE, "{", line, col)
            case "}":
                return Token(TokenType.RBRACE, "}", line, col)
            case ",":
                return Token(TokenType.COMMA, ",", line, col)
            case ".":
                if self._match("."):
                    return Token(TokenType.DOTDOT, "..", line, col)
                return Token(TokenType.DOT, ".", line, col)
            case ":":
                return Token(TokenType.COLON, ":", line, col)
            case _:
                self._error(f"Unexpected character: {ch!r}")
