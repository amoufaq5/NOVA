"""Tests for the Nova parser."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nova_interpreter.lexer.lexer import Lexer
from nova_interpreter.parser.parser import Parser
from nova_interpreter.ast_nodes.nodes import *


def parse(src: str) -> Program:
    tokens = Lexer(src).tokenize()
    return Parser(tokens).parse()


def test_let_declaration():
    prog = parse("let x = 42")
    assert len(prog.body) == 1
    assert isinstance(prog.body[0], LetDecl)
    assert prog.body[0].name == "x"
    print("  PASS: let_declaration")


def test_function_declaration():
    prog = parse("fn add(a, b) { return a + b }")
    assert isinstance(prog.body[0], FnDecl)
    assert prog.body[0].name == "add"
    assert len(prog.body[0].params) == 2
    print("  PASS: function_declaration")


def test_model_declaration():
    src = """
    model Net(dim: int) {
        w = tensor[f32, 4, 4].zeros()
        forward(x) {
            return x
        }
    }
    """
    prog = parse(src)
    assert isinstance(prog.body[0], ModelDecl)
    assert prog.body[0].name == "Net"
    assert len(prog.body[0].layers) == 1
    assert prog.body[0].forward is not None
    print("  PASS: model_declaration")


def test_agent_declaration():
    src = """
    agent Bot(name: str) {
        tools {
            search = WebSearch()
        }
        memory {
            history: [str] = []
        }
        plan(goal: str) {
            return goal
        }
        act(step: str) {
            return Ok(step)
        }
    }
    """
    prog = parse(src)
    assert isinstance(prog.body[0], AgentDecl)
    assert prog.body[0].name == "Bot"
    assert len(prog.body[0].tools_block) == 1
    assert len(prog.body[0].memory_block) == 1
    assert prog.body[0].plan_fn is not None
    assert prog.body[0].act_fn is not None
    print("  PASS: agent_declaration")


def test_arena_statement():
    src = 'arena gpu(budget=1073741824) { let x = 1 }'
    prog = parse(src)
    assert isinstance(prog.body[0], ArenaStmt)
    assert prog.body[0].device == "gpu"
    print("  PASS: arena_statement")


def test_sandbox_statement():
    src = 'sandbox(net=false, fs="readonly") { let x = 1 }'
    prog = parse(src)
    assert isinstance(prog.body[0], SandboxStmt)
    assert "net" in prog.body[0].permissions
    print("  PASS: sandbox_statement")


def test_distribute_statement():
    src = "distribute(data_parallel, devices=8) { let x = 1 }"
    prog = parse(src)
    assert isinstance(prog.body[0], DistributeStmt)
    assert prog.body[0].strategy == "data_parallel"
    print("  PASS: distribute_statement")


def test_if_elif_else():
    src = """
    if x > 0 {
        let a = 1
    } elif x == 0 {
        let a = 0
    } else {
        let a = -1
    }
    """
    prog = parse(src)
    assert isinstance(prog.body[0], IfStmt)
    assert len(prog.body[0].elif_clauses) == 1
    assert len(prog.body[0].else_body) == 1
    print("  PASS: if_elif_else")


def test_for_loop():
    src = "for i in range(10) { print(i) }"
    prog = parse(src)
    assert isinstance(prog.body[0], ForStmt)
    assert prog.body[0].var == "i"
    print("  PASS: for_loop")


def test_match_statement():
    src = """
    match result {
        Ok(val) => val,
        Err(msg) => msg,
        _ => none,
    }
    """
    prog = parse(src)
    assert isinstance(prog.body[0], MatchStmt)
    assert len(prog.body[0].arms) == 3
    print("  PASS: match_statement")


def test_pipe_operator():
    src = "let y = x |> double |> add_one"
    prog = parse(src)
    assert isinstance(prog.body[0], LetDecl)
    # The value should be a nested PipeExpr
    val = prog.body[0].value
    assert isinstance(val, PipeExpr)
    print("  PASS: pipe_operator")


def test_grad_expression():
    src = "let df = grad(f)"
    prog = parse(src)
    val = prog.body[0].value
    assert isinstance(val, GradExpr)
    print("  PASS: grad_expression")


def test_grad_with_order():
    src = "let d2f = grad(f, order=2)"
    prog = parse(src)
    val = prog.body[0].value
    assert isinstance(val, GradExpr)
    assert val.order is not None
    print("  PASS: grad_with_order")


def test_async_function():
    src = "async fn fetch(url: str) { return await get(url) }"
    prog = parse(src)
    assert isinstance(prog.body[0], FnDecl)
    assert prog.body[0].is_async
    print("  PASS: async_function")


def test_tensor_type_expression():
    src = "let t = tensor[f32, 3, 3].zeros()"
    prog = parse(src)
    assert isinstance(prog.body[0], LetDecl)
    print("  PASS: tensor_type_expression")


def test_lambda():
    src = "let sq = |x| x * x"
    prog = parse(src)
    assert isinstance(prog.body[0].value, LambdaExpr)
    print("  PASS: lambda")


def test_operator_precedence():
    prog = parse("let x = 1 + 2 * 3")
    val = prog.body[0].value
    assert isinstance(val, BinOp) and val.op == "+"
    assert isinstance(val.right, BinOp) and val.right.op == "*"
    print("  PASS: operator_precedence")


if __name__ == "__main__":
    print("Running parser tests...")
    test_let_declaration()
    test_function_declaration()
    test_model_declaration()
    test_agent_declaration()
    test_arena_statement()
    test_sandbox_statement()
    test_distribute_statement()
    test_if_elif_else()
    test_for_loop()
    test_match_statement()
    test_pipe_operator()
    test_grad_expression()
    test_grad_with_order()
    test_async_function()
    test_tensor_type_expression()
    test_lambda()
    test_operator_precedence()
    print("All parser tests passed!\n")
