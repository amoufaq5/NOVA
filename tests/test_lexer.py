"""Tests for the Nova lexer."""
import pytest
from nova.lexer import Lexer
from nova.tokens import TokenType


def lex(source):
    return Lexer(source).tokenize()


def types(source):
    return [t.type for t in lex(source) if t.type != TokenType.EOF]


class TestBasicTokens:
    def test_integers(self):
        assert types("42") == [TokenType.INT]
        assert types("0xFF") == [TokenType.INT]
        assert types("0b1010") == [TokenType.INT]
        assert types("1_000_000") == [TokenType.INT]

    def test_floats(self):
        assert types("3.14") == [TokenType.FLOAT]
        assert types("1e10") == [TokenType.FLOAT]
        assert types("2.5e-3") == [TokenType.FLOAT]

    def test_strings(self):
        toks = lex('"hello"')
        assert toks[0].type == TokenType.STRING
        assert toks[0].value == "hello"

    def test_string_escapes(self):
        toks = lex(r'"line\nbreak"')
        assert toks[0].value == "line\nbreak"

    def test_booleans(self):
        assert types("true false") == [TokenType.TRUE, TokenType.FALSE]

    def test_none(self):
        assert types("none") == [TokenType.NONE]

    def test_identifiers(self):
        assert types("foo bar_baz x1") == [TokenType.IDENT] * 3


class TestOperators:
    def test_arithmetic(self):
        assert types("+ - * / // % **") == [
            TokenType.PLUS, TokenType.MINUS, TokenType.STAR,
            TokenType.SLASH, TokenType.DSLASH, TokenType.PERCENT,
            TokenType.DSTAR,
        ]

    def test_comparison(self):
        assert types("== != < > <= >=") == [
            TokenType.EQ_EQ, TokenType.BANG_EQ, TokenType.LT, TokenType.GT,
            TokenType.LT_EQ, TokenType.GT_EQ,
        ]

    def test_pipe(self):
        assert types("|>") == [TokenType.PIPE_ARROW]

    def test_arrow(self):
        assert types("->") == [TokenType.ARROW]

    def test_fat_arrow(self):
        assert types("=>") == [TokenType.FAT_ARROW]


class TestKeywords:
    def test_cognitive_keywords(self):
        assert types("mind") == [TokenType.MIND]
        assert types("memory") == [TokenType.MEMORY]
        assert types("perceive") == [TokenType.PERCEIVE]
        assert types("think") == [TokenType.THINK]
        assert types("imagine") == [TokenType.IMAGINE]
        assert types("learn") == [TokenType.LEARN]
        assert types("recall") == [TokenType.RECALL]
        assert types("store") == [TokenType.STORE]
        assert types("believe") == [TokenType.BELIEVE]
        assert types("simulate") == [TokenType.SIMULATE]
        assert types("predict") == [TokenType.PREDICT]

    def test_control_flow(self):
        assert types("if elif else for while match") == [
            TokenType.IF, TokenType.ELIF, TokenType.ELSE,
            TokenType.FOR, TokenType.WHILE, TokenType.MATCH,
        ]


class TestComments:
    def test_line_comment(self):
        assert types("42 -- this is a comment\n10") == [TokenType.INT, TokenType.NEWLINE, TokenType.INT]

    def test_block_comment(self):
        assert types("42 -{ block comment }- 10") == [TokenType.INT, TokenType.INT]

    def test_nested_block_comment(self):
        assert types("1 -{ outer -{ inner }- still comment }- 2") == [
            TokenType.INT, TokenType.INT
        ]


class TestDelimiters:
    def test_braces(self):
        assert types("{ }") == [TokenType.LBRACE, TokenType.RBRACE]

    def test_parens(self):
        assert types("( )") == [TokenType.LPAREN, TokenType.RPAREN]

    def test_brackets(self):
        assert types("[ ]") == [TokenType.LBRACKET, TokenType.RBRACKET]
