"""Tests for the Nova interpreter."""
import pytest
from nova.lexer import Lexer
from nova.parser import Parser
from nova.runtime.interpreter import Interpreter, NovaPanic, NovaNone, NovaInt, NovaStr, NovaBool, NovaFloat, NovaList


def run(source):
    tokens = Lexer(source).tokenize()
    ast = Parser(tokens).parse()
    interp = Interpreter()
    result = interp.execute(ast)
    return interp, result


def eval_expr(source):
    interp, result = run(source)
    return result


class TestArithmetic:
    def test_addition(self):
        _, r = run("let x = 2 + 3")
        assert isinstance(r, NovaInt) and r.value == 5

    def test_multiplication(self):
        _, r = run("let x = 4 * 5")
        assert r.value == 20

    def test_division(self):
        _, r = run("let x = 10 / 3")
        assert isinstance(r, NovaFloat)

    def test_integer_division(self):
        _, r = run("let x = 10 // 3")
        assert isinstance(r, NovaInt) and r.value == 3

    def test_modulo(self):
        _, r = run("let x = 10 % 3")
        assert r.value == 1

    def test_power(self):
        _, r = run("let x = 2 ** 10")
        assert r.value == 1024

    def test_string_concat(self):
        _, r = run('let x = "hello" + " " + "world"')
        assert isinstance(r, NovaStr) and r.value == "hello world"


class TestVariables:
    def test_let(self):
        interp, _ = run("let x = 42")
        assert interp.global_env.get("x").value == 42

    def test_reassignment(self):
        interp, _ = run("let x = 1\nx = 2")
        assert interp.global_env.get("x").value == 2


class TestFunctions:
    def test_basic_fn(self):
        _, r = run("""
fn double(x) { return x * 2 }
let result = double(5)
""")
        assert r.value == 10

    def test_recursive_fn(self):
        _, r = run("""
fn factorial(n) {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
let result = factorial(6)
""")
        assert r.value == 720

    def test_closure(self):
        _, r = run("""
fn make_adder(n) {
    return fn(x) { return x + n }
}
let add5 = make_adder(5)
let result = add5(10)
""")
        assert r.value == 15


class TestControlFlow:
    def test_if_true(self, capsys):
        run('if true { print("yes") }')
        assert capsys.readouterr().out.strip() == "yes"

    def test_if_false(self, capsys):
        run('if false { print("yes") } else { print("no") }')
        assert capsys.readouterr().out.strip() == "no"

    def test_for_loop(self, capsys):
        run("""
for i in range(3) {
    print(i)
}
""")
        assert capsys.readouterr().out.strip() == "0\n1\n2"

    def test_while_loop(self, capsys):
        run("""
let count = 0
while count < 3 {
    print(count)
    count = count + 1
}
""")
        assert capsys.readouterr().out.strip() == "0\n1\n2"

    def test_match(self, capsys):
        run("""
let x = 2
match x {
    1 => print("one")
    2 => print("two")
    _ => print("other")
}
""")
        assert capsys.readouterr().out.strip() == "two"


class TestBuiltins:
    def test_print(self, capsys):
        run('print("hello")')
        assert capsys.readouterr().out.strip() == "hello"

    def test_len(self):
        _, r = run('let x = len("hello")')
        assert r.value == 5

    def test_range(self):
        _, r = run("let x = range(5)")
        assert isinstance(r, NovaList) and len(r.elements) == 5

    def test_type_of(self):
        _, r = run('let x = type_of(42)')
        assert r.value == "int"

    def test_abs(self):
        _, r = run("let x = abs(-5)")
        assert r.value == 5

    def test_sqrt(self):
        _, r = run("let x = sqrt(16)")
        assert r.value == 4.0


class TestCollections:
    def test_list(self):
        _, r = run("let x = [1, 2, 3]")
        assert isinstance(r, NovaList) and len(r.elements) == 3

    def test_list_index(self):
        _, r = run("let x = [10, 20, 30]\nlet y = x[1]")
        assert r.value == 20

    def test_list_map(self):
        _, r = run("let x = [1, 2, 3].map(fn(n) { return n * 2 })")
        assert [e.value for e in r.elements] == [2, 4, 6]

    def test_list_filter(self):
        _, r = run("let x = [1, 2, 3, 4].filter(fn(n) { return n > 2 })")
        assert [e.value for e in r.elements] == [3, 4]

    def test_string_methods(self):
        _, r = run('let x = "hello".upper()')
        assert r.value == "HELLO"


class TestResult:
    def test_ok(self):
        _, r = run("let x = Ok(42)")
        assert r.is_ok and r.value.value == 42

    def test_err(self):
        _, r = run('let x = Err("oops")')
        assert not r.is_ok

    def test_match_result(self, capsys):
        run("""
let x = Ok(42)
match x {
    Ok(n) => print(n)
    Err(e) => print(e)
}
""")
        assert capsys.readouterr().out.strip() == "42"


class TestAssertions:
    def test_assert_eq_pass(self):
        run("assert_eq(1, 1)")

    def test_assert_eq_fail(self):
        with pytest.raises(NovaPanic):
            run("assert_eq(1, 2)")

    def test_assert_true(self):
        run("assert_true(true)")

    def test_assert_false(self):
        run("assert_false(false)")


class TestMind:
    def test_mind_creation(self):
        interp, _ = run("""
mind Agent {
    memory working(capacity: 7)
    memory episodic(max_episodes: 1000)
}
""")
        assert "Agent" in interp.minds
        mind = interp.minds["Agent"]
        assert mind.name == "Agent"
        assert mind.working.capacity == 7

    def test_mind_perceive(self, capsys):
        run("""
mind Bot {
    perceive(input) {
        print(input)
        return input
    }
}
Bot.perceive("hello")
""")
        assert capsys.readouterr().out.strip() == "hello"

    def test_mind_think(self):
        interp, _ = run("""
mind Bot {
    think(goal) {
        return "thinking about: " + goal
    }
}
let result = Bot.think("life")
""")
        result = interp.global_env.get("result")
        assert isinstance(result, NovaStr)
        assert "thinking about: life" in result.value

    def test_mind_believe(self):
        interp, _ = run("""
mind Bot {}
Bot.believe("sky is blue", 0.9)
""")
        mind = interp.minds["Bot"]
        belief = mind.get_belief("sky is blue")
        assert belief is not None
        assert belief["confidence"] >= 0.9

    def test_mind_know(self):
        interp, _ = run("""
mind Bot {}
Bot.know("Earth", "orbits", "Sun")
""")
        mind = interp.minds["Bot"]
        results = mind.recall("Earth", source="semantic")
        assert results["semantic"]

    def test_mind_add_goal(self):
        interp, _ = run("""
mind Bot {}
Bot.add_goal("learn", 0.8)
""")
        mind = interp.minds["Bot"]
        assert len(mind.goals) == 1
        assert mind.goals[0]["description"] == "learn"

    def test_mind_status(self):
        interp, _ = run("""
mind Bot {
    memory working(capacity: 5)
}
let s = Bot.status()
""")
        s = interp.global_env.get("s")
        assert s is not None

    def test_mind_name(self):
        interp, _ = run("""
mind MyAgent {}
let n = MyAgent.name
""")
        assert interp.global_env.get("n").value == "MyAgent"

    def test_cognitive_store_recall(self):
        interp, _ = run("""
mind Bot {
    perceive(input) {
        store(input, in: "working")
        return input
    }
}
Bot.perceive("important data")
""")
        mind = interp.minds["Bot"]
        assert len(mind.working) > 0
