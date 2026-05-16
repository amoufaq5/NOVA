"""Nova linter — static analysis warnings for common issues."""

import os
from ..lexer.lexer import Lexer
from ..parser.parser import Parser
from ..ast_nodes.nodes import *


class LintWarning:
    """A single lint warning with location and message."""

    def __init__(self, line: int, col: int, code: str, message: str):
        self.line = line
        self.col = col
        self.code = code
        self.message = message

    def __repr__(self):
        return f"L{self.line}:{self.col} [{self.code}] {self.message}"


# Warning codes:
# W001 - Unused variable
# W002 - Use after move (owned value consumed)
# W003 - Function may be missing a return
# W004 - Unreachable code after return/break/continue


class LintContext:
    """Tracks state during a linting walk."""

    def __init__(self):
        self.warnings: list[LintWarning] = []
        # Stack of scopes, each is a dict of {name: (line, col, used)}
        self._scopes: list[dict[str, tuple[int, int, bool]]] = [{}]
        # Set of variable names that have been moved
        self._moved: set[str] = set()
        # Names that are builtins and should not be flagged
        self._builtins = {
            "print", "len", "range", "type_of", "Ok", "Err",
            "abs", "sum", "min", "max",
            "assert_eq", "assert_true", "assert_false",
            "true", "false", "none",
        }

    def warn(self, line: int, col: int, code: str, message: str):
        self.warnings.append(LintWarning(line, col, code, message))

    def push_scope(self):
        self._scopes.append({})

    def pop_scope(self):
        scope = self._scopes.pop()
        # Check for unused variables in this scope
        for name, (line, col, used) in scope.items():
            if not used and not name.startswith("_"):
                self.warn(line, col, "W001", f"Unused variable '{name}'")

    def define(self, name: str, line: int, col: int):
        self._scopes[-1][name] = (line, col, False)

    def reference(self, name: str):
        # Mark as used in the innermost scope that defines it
        for scope in reversed(self._scopes):
            if name in scope:
                line, col, _ = scope[name]
                scope[name] = (line, col, True)
                return
        # Not found in any scope -- might be a builtin, don't warn

    def mark_moved(self, name: str):
        self._moved.add(name)

    def is_moved(self, name: str) -> bool:
        return name in self._moved


class Linter:
    """Walk the AST and collect lint warnings."""

    def lint_source(self, source: str) -> list[LintWarning]:
        tokens = Lexer(source).tokenize()
        program = Parser(tokens).parse()
        return self.lint_program(program)

    def lint_file(self, path: str) -> list[LintWarning]:
        with open(path, "r") as f:
            source = f.read()
        return self.lint_source(source)

    def lint_program(self, program: Program) -> list[LintWarning]:
        ctx = LintContext()
        for node in program.body:
            self._visit(node, ctx)
        # Pop the global scope to check for unused globals
        # (but don't warn about functions, models, agents defined at top level)
        return ctx.warnings

    def _visit(self, node: ASTNode, ctx: LintContext):
        """Dispatch visitor for each node type."""
        match node:
            case FnDecl():
                self._visit_fn_decl(node, ctx)
            case LetDecl():
                self._visit_let_decl(node, ctx)
            case ConstDecl():
                self._visit_const_decl(node, ctx)
            case ModelDecl():
                self._visit_model_decl(node, ctx)
            case AgentDecl():
                self._visit_agent_decl(node, ctx)
            case AssignStmt():
                self._visit_assign(node, ctx)
            case ReturnStmt():
                self._visit_return(node, ctx)
            case IfStmt():
                self._visit_if(node, ctx)
            case ForStmt():
                self._visit_for(node, ctx)
            case WhileStmt():
                self._visit_while(node, ctx)
            case MatchStmt():
                self._visit_match(node, ctx)
            case ExprStmt():
                self._visit_expr(node.expr, ctx)
            case BreakStmt() | ContinueStmt():
                pass
            case ImportDecl() | FromImportDecl():
                pass
            case OnGpuStmt():
                self._visit_block(node.body, ctx)
            case ArenaStmt():
                self._visit_block(node.body, ctx)
            case DistributeStmt():
                self._visit_block(node.body, ctx)
            case SandboxStmt():
                self._visit_block(node.body, ctx)
            case SelectStmt():
                for cond, body in node.arms:
                    self._visit_expr(cond, ctx)
                    self._visit_block(body, ctx)
            case _:
                # Expression node at statement level
                self._visit_expr(node, ctx)

    def _visit_fn_decl(self, node: FnDecl, ctx: LintContext):
        # Define the function name in current scope
        if node.name and node.name not in ("<anon>", "<lambda>"):
            ctx.define(node.name, node.line, node.col)
            # Immediately mark as used (function declarations are rarely "unused")
            ctx.reference(node.name)

        ctx.push_scope()

        # Define parameters
        for p in node.params:
            ctx.define(p.name, p.line, p.col)
            # Parameters are considered "used" (caller provides them)
            ctx.reference(p.name)

        # Check for unreachable code and missing returns
        self._visit_body_with_reachability(node.body, ctx)

        # Check for missing return (heuristic: if function has > 1 statement
        # and has branches or non-trivial logic, it probably should return something)
        if node.return_type and node.body:
            if not self._body_always_returns(node.body):
                ctx.warn(node.line, node.col, "W003",
                         f"Function '{node.name}' declares a return type "
                         f"but may not always return a value")

        ctx.pop_scope()

    def _visit_let_decl(self, node: LetDecl, ctx: LintContext):
        if node.value:
            self._visit_expr(node.value, ctx)
        ctx.define(node.name, node.line, node.col)
        if node.is_owned:
            # Track that this is an owned value
            pass

    def _visit_const_decl(self, node: ConstDecl, ctx: LintContext):
        if node.value:
            self._visit_expr(node.value, ctx)
        ctx.define(node.name, node.line, node.col)

    def _visit_model_decl(self, node: ModelDecl, ctx: LintContext):
        ctx.define(node.name, node.line, node.col)
        ctx.reference(node.name)  # Models are considered used by definition
        ctx.push_scope()
        for p in node.params:
            ctx.define(p.name, p.line, p.col)
            ctx.reference(p.name)
        for layer in node.layers:
            ctx.define(layer.name, layer.line, layer.col)
            ctx.reference(layer.name)  # Layers are used by forward
            if layer.value:
                self._visit_expr(layer.value, ctx)
        if node.forward:
            self._visit_fn_decl(node.forward, ctx)
        for method in node.methods:
            self._visit_fn_decl(method, ctx)
        ctx.pop_scope()

    def _visit_agent_decl(self, node: AgentDecl, ctx: LintContext):
        ctx.define(node.name, node.line, node.col)
        ctx.reference(node.name)
        ctx.push_scope()
        for p in node.params:
            ctx.define(p.name, p.line, p.col)
            ctx.reference(p.name)
        for tool in node.tools_block:
            ctx.define(tool.name, tool.line, tool.col)
            ctx.reference(tool.name)
            if tool.value:
                self._visit_expr(tool.value, ctx)
        for mem in node.memory_block:
            ctx.define(mem.name, mem.line, mem.col)
            ctx.reference(mem.name)
            if mem.value:
                self._visit_expr(mem.value, ctx)
        if node.plan_fn:
            self._visit_fn_decl(node.plan_fn, ctx)
        if node.act_fn:
            self._visit_fn_decl(node.act_fn, ctx)
        for method in node.methods:
            self._visit_fn_decl(method, ctx)
        ctx.pop_scope()

    def _visit_assign(self, node: AssignStmt, ctx: LintContext):
        self._visit_expr(node.value, ctx)
        # The target is a reference (reading for compound assignments)
        if node.op != "=":
            self._visit_expr(node.target, ctx)
        elif isinstance(node.target, Identifier):
            # Simple assignment -- just mark the target as referenced
            ctx.reference(node.target.name)
        else:
            self._visit_expr(node.target, ctx)

    def _visit_return(self, node: ReturnStmt, ctx: LintContext):
        if node.value:
            self._visit_expr(node.value, ctx)

    def _visit_if(self, node: IfStmt, ctx: LintContext):
        self._visit_expr(node.condition, ctx)
        self._visit_block(node.body, ctx)
        for cond, body in node.elif_clauses:
            self._visit_expr(cond, ctx)
            self._visit_block(body, ctx)
        if node.else_body:
            self._visit_block(node.else_body, ctx)

    def _visit_for(self, node: ForStmt, ctx: LintContext):
        self._visit_expr(node.iterable, ctx)
        ctx.push_scope()
        ctx.define(node.var, node.line, node.col)
        ctx.reference(node.var)  # Loop variable is always considered used
        for stmt in node.body:
            self._visit(stmt, ctx)
        ctx.pop_scope()

    def _visit_while(self, node: WhileStmt, ctx: LintContext):
        self._visit_expr(node.condition, ctx)
        self._visit_block(node.body, ctx)

    def _visit_match(self, node: MatchStmt, ctx: LintContext):
        self._visit_expr(node.subject, ctx)
        for arm in node.arms:
            ctx.push_scope()
            self._visit_pattern(arm.pattern, ctx)
            if isinstance(arm.body, list):
                for stmt in arm.body:
                    self._visit(stmt, ctx)
            else:
                self._visit_expr(arm.body, ctx)
            ctx.pop_scope()

    def _visit_pattern(self, pattern: ASTNode, ctx: LintContext):
        match pattern:
            case IdentPattern():
                ctx.define(pattern.name, pattern.line, pattern.col)
                ctx.reference(pattern.name)  # Pattern bindings are used
            case ConstructorPattern():
                for arg in pattern.args:
                    self._visit_pattern(arg, ctx)
            case _:
                pass

    def _visit_block(self, stmts: list[ASTNode], ctx: LintContext):
        ctx.push_scope()
        self._visit_body_with_reachability(stmts, ctx)
        ctx.pop_scope()

    def _visit_body_with_reachability(self, stmts: list[ASTNode], ctx: LintContext):
        """Visit statements and check for unreachable code."""
        hit_terminator = False
        for i, stmt in enumerate(stmts):
            if hit_terminator:
                ctx.warn(stmt.line, stmt.col, "W004",
                         "Unreachable code after return/break/continue")
                break
            self._visit(stmt, ctx)
            if isinstance(stmt, (ReturnStmt, BreakStmt, ContinueStmt)):
                hit_terminator = True

    # --- Expression visitors ---

    def _visit_expr(self, node: ASTNode, ctx: LintContext):
        if node is None:
            return
        match node:
            case Identifier():
                ctx.reference(node.name)
                if ctx.is_moved(node.name):
                    ctx.warn(node.line, node.col, "W002",
                             f"Use of moved value '{node.name}'")
            case MoveExpr():
                ctx.mark_moved(node.name)
                ctx.reference(node.name)
            case BinOp():
                self._visit_expr(node.left, ctx)
                self._visit_expr(node.right, ctx)
            case UnaryOp():
                self._visit_expr(node.operand, ctx)
            case CallExpr():
                self._visit_expr(node.callee, ctx)
                for arg in node.args:
                    self._visit_expr(arg, ctx)
                for v in node.kwargs.values():
                    self._visit_expr(v, ctx)
            case IndexExpr():
                self._visit_expr(node.obj, ctx)
                self._visit_expr(node.index, ctx)
            case SliceExpr():
                self._visit_expr(node.obj, ctx)
                if node.start:
                    self._visit_expr(node.start, ctx)
                if node.stop:
                    self._visit_expr(node.stop, ctx)
                if node.step:
                    self._visit_expr(node.step, ctx)
            case DotExpr():
                self._visit_expr(node.obj, ctx)
            case PipeExpr():
                self._visit_expr(node.left, ctx)
                self._visit_expr(node.right, ctx)
            case LambdaExpr():
                ctx.push_scope()
                for p in node.params:
                    ctx.define(p.name, p.line, p.col)
                    ctx.reference(p.name)
                self._visit_expr(node.body, ctx)
                ctx.pop_scope()
            case GradExpr():
                self._visit_expr(node.func, ctx)
                if node.order:
                    self._visit_expr(node.order, ctx)
            case TryExpr():
                self._visit_expr(node.expr, ctx)
            case PanicExpr():
                self._visit_expr(node.message, ctx)
            case SpawnExpr():
                for stmt in node.body:
                    self._visit(stmt, ctx)
            case ConstrainExpr():
                for v in node.config.values():
                    self._visit_expr(v, ctx)
                for stmt in node.body:
                    self._visit(stmt, ctx)
            case SharedExpr():
                self._visit_expr(node.value, ctx)
            case ChannelExpr():
                if node.buffer_size:
                    self._visit_expr(node.buffer_size, ctx)
            case AwaitExpr():
                self._visit_expr(node.expr, ctx)
            case ListLiteral():
                for e in node.elements:
                    self._visit_expr(e, ctx)
            case MapLiteral():
                for k, v in zip(node.keys, node.values):
                    self._visit_expr(k, ctx)
                    self._visit_expr(v, ctx)
            case FnDecl():
                self._visit_fn_decl(node, ctx)
            case TensorTypeExpr():
                for d in node.dims:
                    self._visit_expr(d, ctx)
            case _:
                # Literals and other leaf nodes -- nothing to visit
                pass

    # --- Helpers ---

    def _body_always_returns(self, stmts: list[ASTNode]) -> bool:
        """Check if a list of statements always reaches a return."""
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, ReturnStmt):
            return True
        if isinstance(last, IfStmt):
            # All branches must return
            if_returns = self._body_always_returns(last.body)
            elif_returns = all(
                self._body_always_returns(body)
                for _, body in last.elif_clauses
            )
            else_returns = self._body_always_returns(last.else_body) if last.else_body else False
            return if_returns and elif_returns and else_returns
        return False


def lint_file(path: str) -> list[LintWarning]:
    """Lint a .nova file and return a list of warnings."""
    return Linter().lint_file(path)


def lint_source(source: str) -> list[LintWarning]:
    """Lint Nova source code and return a list of warnings."""
    return Linter().lint_source(source)
