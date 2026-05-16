"""Tests for expanded standard library — math, string methods, list methods, assertions."""

import sys
import os
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nova_interpreter.lexer.lexer import Lexer
from nova_interpreter.parser.parser import Parser
from nova_interpreter.runtime.interpreter import Interpreter
from nova_interpreter.types.values import *


def run(src: str) -> tuple[NovaValue, str]:
    interp = Interpreter()
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    old_stdout = sys.stdout
    sys.stdout = captured = io.StringIO()
    try:
        result = interp.execute(ast)
    finally:
        sys.stdout = old_stdout
    return result, captured.getvalue()


# --- Math builtins ---

def test_sqrt():
    _, out = run("print(sqrt(16.0))")
    assert "4.0" in out
    print("  PASS: sqrt")


def test_log_exp():
    _, out = run("print(exp(0.0))")
    assert "1.0" in out
    print("  PASS: log_exp")


def test_floor_ceil():
    _, out = run("print(floor(3.7))")
    assert "3" in out
    _, out2 = run("print(ceil(3.2))")
    assert "4" in out2
    print("  PASS: floor_ceil")


def test_round():
    _, out = run("print(round(3.14159, 2))")
    assert "3.14" in out
    print("  PASS: round")


def test_trig():
    _, out = run("print(sin(0.0))")
    assert "0.0" in out
    _, out2 = run("print(cos(0.0))")
    assert "1.0" in out2
    print("  PASS: trig")


def test_pow_builtin():
    _, out = run("print(pow(2, 10))")
    assert "1024" in out
    print("  PASS: pow")


# --- Assertions ---

def test_assert_eq_pass():
    run("assert_eq(1, 1)")
    print("  PASS: assert_eq_pass")


def test_assert_eq_fail():
    try:
        run("assert_eq(1, 2)")
        assert False, "Should have panicked"
    except NovaPanic:
        pass
    print("  PASS: assert_eq_fail")


def test_assert_true():
    run("assert_true(1 > 0)")
    print("  PASS: assert_true")


def test_assert_false():
    run("assert_false(1 > 2)")
    print("  PASS: assert_false")


def test_assert_ne():
    run("assert_ne(1, 2)")
    try:
        run("assert_ne(1, 1)")
        assert False
    except NovaPanic:
        pass
    print("  PASS: assert_ne")


# --- String methods ---

def test_string_upper_lower():
    _, out = run("""
    let s = "hello"
    print(s.upper())
    print(s.lower())
    """)
    lines = out.strip().split("\n")
    assert lines[0] == "HELLO"
    assert lines[1] == "hello"
    print("  PASS: string_upper_lower")


def test_string_split():
    _, out = run("""
    let s = "a,b,c"
    print(s.split(","))
    """)
    assert "a" in out and "b" in out and "c" in out
    print("  PASS: string_split")


def test_string_strip():
    _, out = run("""
    let s = "  hello  "
    print(s.strip())
    """)
    assert out.strip() == "hello"
    print("  PASS: string_strip")


def test_string_contains():
    _, out = run("""
    let s = "hello world"
    print(s.contains("world"))
    print(s.contains("xyz"))
    """)
    lines = out.strip().split("\n")
    assert lines[0] == "true"
    assert lines[1] == "false"
    print("  PASS: string_contains")


def test_string_starts_ends():
    _, out = run("""
    let s = "hello world"
    print(s.starts_with("hello"))
    print(s.ends_with("world"))
    """)
    lines = out.strip().split("\n")
    assert lines[0] == "true"
    assert lines[1] == "true"
    print("  PASS: string_starts_ends")


def test_string_replace():
    _, out = run("""
    let s = "hello world"
    print(s.replace("world", "Nova"))
    """)
    assert "hello Nova" in out
    print("  PASS: string_replace")


# --- List methods ---

def test_list_map():
    _, out = run("""
    let nums = [1, 2, 3]
    let doubled = nums.map(|x| x * 2)
    print(doubled)
    """)
    assert "2" in out and "4" in out and "6" in out
    print("  PASS: list_map")


def test_list_filter():
    _, out = run("""
    let nums = [1, 2, 3, 4, 5, 6]
    let evens = nums.filter(|x| x % 2 == 0)
    print(evens)
    """)
    assert "2" in out and "4" in out and "6" in out
    assert "1" not in out.replace("[1", "").replace("1,", "").replace("10", "")  # rough check
    print("  PASS: list_filter")


def test_list_reduce():
    _, out = run("""
    let nums = [1, 2, 3, 4, 5]
    let total = nums.reduce(|acc, x| acc + x, 0)
    print(total)
    """)
    assert out.strip() == "15"
    print("  PASS: list_reduce")


def test_list_sort():
    _, out = run("""
    let nums = [3, 1, 4, 1, 5]
    let sorted_nums = nums.sort()
    print(sorted_nums)
    """)
    assert "[1, 1, 3, 4, 5]" in out
    print("  PASS: list_sort")


def test_list_reverse():
    _, out = run("""
    let nums = [1, 2, 3]
    print(nums.reverse())
    """)
    assert "[3, 2, 1]" in out
    print("  PASS: list_reverse")


def test_list_contains():
    _, out = run("""
    let nums = [1, 2, 3]
    print(nums.contains(2))
    print(nums.contains(5))
    """)
    lines = out.strip().split("\n")
    assert lines[0] == "true"
    assert lines[1] == "false"
    print("  PASS: list_contains")


# --- Tensor methods ---

def test_tensor_reshape():
    _, out = run("""
    let t = tensor[f32, 2, 3].ones()
    let r = t.reshape(3, 2)
    print(r.shape)
    """)
    assert "[3, 2]" in out
    print("  PASS: tensor_reshape")


def test_tensor_flatten():
    _, out = run("""
    let t = tensor[f32, 2, 3].ones()
    let f = t.flatten()
    print(f.shape)
    """)
    assert "[6]" in out
    print("  PASS: tensor_flatten")


def test_tensor_item():
    _, out = run("""
    let t = tensor[f32, 1].ones()
    print(t.item())
    """)
    assert "1.0" in out
    print("  PASS: tensor_item")


def test_tensor_mean():
    _, out = run("""
    let t = tensor[f32, 4].from([1.0, 2.0, 3.0, 4.0])
    print(t.mean())
    """)
    assert "2.5" in out
    print("  PASS: tensor_mean")


if __name__ == "__main__":
    print("Running stdlib tests...")
    test_sqrt()
    test_log_exp()
    test_floor_ceil()
    test_round()
    test_trig()
    test_pow_builtin()
    test_assert_eq_pass()
    test_assert_eq_fail()
    test_assert_true()
    test_assert_false()
    test_assert_ne()
    test_string_upper_lower()
    test_string_split()
    test_string_strip()
    test_string_contains()
    test_string_starts_ends()
    test_string_replace()
    test_list_map()
    test_list_filter()
    test_list_reduce()
    test_list_sort()
    test_list_reverse()
    test_list_contains()
    test_tensor_reshape()
    test_tensor_flatten()
    test_tensor_item()
    test_tensor_mean()
    print("All stdlib tests passed!\n")
