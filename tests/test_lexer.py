"""Tests for the Nova lexer."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nova_interpreter.lexer.lexer import Lexer
from nova_interpreter.tokens import TokenType


def test_basic_tokens():
    tokens = Lexer("let x = 42").tokenize()
    types = [t.type for t in tokens[:-1]]  # exclude EOF
    assert types == [TokenType.LET, TokenType.IDENT, TokenType.ASSIGN, TokenType.INT]
    assert tokens[3].value == 42
    print("  PASS: basic_tokens")


def test_keywords():
    src = "fn model agent arena sandbox distribute grad spawn async await"
    tokens = Lexer(src).tokenize()
    types = [t.type for t in tokens[:-1]]
    expected = [
        TokenType.FN, TokenType.MODEL, TokenType.AGENT, TokenType.ARENA,
        TokenType.SANDBOX, TokenType.DISTRIBUTE, TokenType.GRAD,
        TokenType.SPAWN, TokenType.ASYNC, TokenType.AWAIT,
    ]
    assert types == expected, f"Got {types}"
    print("  PASS: keywords")


def test_operators():
    src = "+ - * / // ** % @ == != < > <= >= |> -> =>"
    tokens = Lexer(src).tokenize()
    types = [t.type for t in tokens[:-1]]
    expected = [
        TokenType.PLUS, TokenType.MINUS, TokenType.STAR, TokenType.SLASH,
        TokenType.DSLASH, TokenType.DSTAR, TokenType.PERCENT, TokenType.AT,
        TokenType.EQ, TokenType.NEQ, TokenType.LT, TokenType.GT,
        TokenType.LTE, TokenType.GTE, TokenType.PIPE_ARROW,
        TokenType.ARROW, TokenType.FAT_ARROW,
    ]
    assert types == expected, f"Got {types}"
    print("  PASS: operators")


def test_string_literal():
    tokens = Lexer('"hello world"').tokenize()
    assert tokens[0].type == TokenType.STRING
    assert tokens[0].value == "hello world"
    print("  PASS: string_literal")


def test_float_literal():
    tokens = Lexer("3.14 1e10 2.5e-3").tokenize()
    assert tokens[0].type == TokenType.FLOAT
    assert tokens[0].value == 3.14
    assert tokens[1].type == TokenType.FLOAT
    assert tokens[2].type == TokenType.FLOAT
    print("  PASS: float_literal")


def test_comments():
    tokens = Lexer("let x = 1 -- this is a comment\nlet y = 2").tokenize()
    idents = [t.value for t in tokens if t.type == TokenType.IDENT]
    assert idents == ["x", "y"]
    print("  PASS: comments")


def test_block_comments():
    tokens = Lexer("let a = -{ nested -{ comment }- here }- 5").tokenize()
    types = [t.type for t in tokens[:-1]]
    assert types == [TokenType.LET, TokenType.IDENT, TokenType.ASSIGN, TokenType.INT]
    print("  PASS: block_comments")


def test_hex_binary_literals():
    tokens = Lexer("0xFF 0b1010 0o77").tokenize()
    assert tokens[0].value == 255
    assert tokens[1].value == 10
    assert tokens[2].value == 63
    print("  PASS: hex_binary_literals")


if __name__ == "__main__":
    print("Running lexer tests...")
    test_basic_tokens()
    test_keywords()
    test_operators()
    test_string_literal()
    test_float_literal()
    test_comments()
    test_block_comments()
    test_hex_binary_literals()
    print("All lexer tests passed!\n")
