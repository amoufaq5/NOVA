"""Tests for the Nova parser."""
import pytest
from nova.lexer import Lexer
from nova.parser import Parser, ParseError
from nova.ast_nodes import *


def parse(source):
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()


class TestLetDeclarations:
    def test_let_int(self):
        ast = parse("let x = 42")
        assert len(ast.body) == 1
        node = ast.body[0]
        assert isinstance(node, LetDecl)
        assert node.name == "x"
        assert isinstance(node.value, IntLit)
        assert node.value.value == 42

    def test_let_string(self):
        ast = parse('let name = "Nova"')
        node = ast.body[0]
        assert isinstance(node, LetDecl)
        assert node.name == "name"

    def test_let_expression(self):
        ast = parse("let y = 2 + 3")
        node = ast.body[0]
        assert isinstance(node.value, BinOp)
        assert node.value.op == "+"


class TestFunctions:
    def test_simple_fn(self):
        ast = parse("fn add(a, b) { return a + b }")
        node = ast.body[0]
        assert isinstance(node, FnDecl)
        assert node.name == "add"
        assert len(node.params) == 2

    def test_recursive_fn(self):
        ast = parse("""
fn factorial(n) {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
""")
        node = ast.body[0]
        assert isinstance(node, FnDecl)
        assert node.name == "factorial"

    def test_anonymous_fn(self):
        ast = parse("let f = fn(x) { return x * 2 }")
        node = ast.body[0]
        assert isinstance(node, LetDecl)
        assert isinstance(node.value, FnDecl)
        assert node.value.name == "<anon>"


class TestControlFlow:
    def test_if_stmt(self):
        ast = parse("if x > 0 { print(x) }")
        node = ast.body[0]
        assert isinstance(node, IfStmt)
        assert isinstance(node.condition, BinOp)
        assert len(node.then_body) == 1

    def test_if_else(self):
        ast = parse("if x > 0 { print(x) } else { print(0) }")
        node = ast.body[0]
        assert isinstance(node, IfStmt)
        assert len(node.else_body) == 1

    def test_for_loop(self):
        ast = parse("for i in range(10) { print(i) }")
        node = ast.body[0]
        assert isinstance(node, ForStmt)
        assert node.var == "i"

    def test_while_loop(self):
        ast = parse("while x > 0 { let x = x - 1 }")
        node = ast.body[0]
        assert isinstance(node, WhileStmt)

    def test_match(self):
        ast = parse("""
match value {
    1 => print("one")
    2 => print("two")
    _ => print("other")
}
""")
        node = ast.body[0]
        assert isinstance(node, MatchStmt)
        assert len(node.arms) == 3


class TestMindDeclaration:
    def test_basic_mind(self):
        ast = parse("""
mind Agent {
    memory working(capacity: 7)
    memory episodic(max_episodes: 1000)

    perceive(input) {
        return input
    }

    think(goal) {
        return goal
    }
}
""")
        node = ast.body[0]
        assert isinstance(node, MindDecl)
        assert node.name == "Agent"
        mem_nodes = [n for n in node.body if isinstance(n, MemoryDecl)]
        assert len(mem_nodes) == 2
        assert mem_nodes[0].kind == "working"
        assert mem_nodes[1].kind == "episodic"

    def test_mind_with_handlers(self):
        ast = parse("""
mind Bot {
    on idle() { let x = 1 }
    on experience(event) { store(event, in: "episodic") }
}
""")
        node = ast.body[0]
        events = [n for n in node.body if isinstance(n, OnEventDecl)]
        assert len(events) == 2
        assert events[0].event_name == "idle"
        assert events[1].event_name == "experience"


class TestCognitiveExpressions:
    def test_recall(self):
        ast = parse('recall("test", from: "semantic", limit: 5)')
        node = ast.body[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, RecallExpr)

    def test_store(self):
        ast = parse('store("data", in: "working")')
        node = ast.body[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, StoreExpr)

    def test_believe(self):
        ast = parse('believe("sky is blue", 0.9)')
        node = ast.body[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, BelieveExpr)

    def test_simulate(self):
        ast = parse('simulate("scenario", steps: 10)')
        node = ast.body[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, SimulateExpr)

    def test_predict(self):
        ast = parse('predict("action")')
        node = ast.body[0]
        assert isinstance(node, ExprStmt)
        assert isinstance(node.expr, PredictExpr)

    def test_cognitive_keyword_as_variable(self):
        ast = parse("let store = 5")
        node = ast.body[0]
        assert isinstance(node, LetDecl)
        assert node.name == "store"


class TestExpressions:
    def test_list_literal(self):
        ast = parse("[1, 2, 3]")
        node = ast.body[0].expr
        assert isinstance(node, ListLit)
        assert len(node.elements) == 3

    def test_dot_access(self):
        ast = parse("obj.field")
        node = ast.body[0].expr
        assert isinstance(node, DotExpr)
        assert node.attr == "field"

    def test_index_access(self):
        ast = parse("arr[0]")
        node = ast.body[0].expr
        assert isinstance(node, IndexExpr)

    def test_pipe(self):
        ast = parse("x |> print")
        node = ast.body[0].expr
        assert isinstance(node, PipeExpr)

    def test_lambda(self):
        ast = parse("|x| x * 2")
        node = ast.body[0].expr
        assert isinstance(node, LambdaExpr)
        assert len(node.params) == 1
