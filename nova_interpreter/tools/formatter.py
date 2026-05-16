"""Nova formatter — parses a .nova file to AST and pretty-prints with consistent style."""

from ..lexer.lexer import Lexer
from ..parser.parser import Parser
from ..ast_nodes.nodes import *

# TODO: Preserve comments. Currently the lexer discards comments during
# tokenization, so they are lost. A future enhancement should store comments
# as tokens or annotations on AST nodes so the formatter can re-emit them.

INDENT = "    "  # 4 spaces


class Formatter:
    """Walk an AST and emit consistently formatted Nova source."""

    def __init__(self):
        self._depth = 0

    def format_source(self, source: str) -> str:
        """Parse source and return formatted output."""
        tokens = Lexer(source).tokenize()
        program = Parser(tokens).parse()
        return self.format_program(program)

    def format_file(self, path: str) -> str:
        """Read, parse, and format a .nova file."""
        with open(path, "r") as f:
            source = f.read()
        return self.format_source(source)

    def format_program(self, program: Program) -> str:
        lines = []
        prev_was_fn = False
        for node in program.body:
            # Add blank line before function/model/agent declarations
            is_decl = isinstance(node, (FnDecl, ModelDecl, AgentDecl))
            if is_decl and lines:
                lines.append("")
            lines.append(self._fmt(node))
            prev_was_fn = is_decl
        # Ensure trailing newline
        text = "\n".join(lines)
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    def _indent(self) -> str:
        return INDENT * self._depth

    def _fmt(self, node: ASTNode) -> str:
        """Dispatch formatting for any AST node."""
        match node:
            case Program():
                return self.format_program(node)
            case FnDecl():
                return self._fmt_fn_decl(node)
            case LetDecl():
                return self._fmt_let_decl(node)
            case ConstDecl():
                return self._fmt_const_decl(node)
            case ModelDecl():
                return self._fmt_model_decl(node)
            case AgentDecl():
                return self._fmt_agent_decl(node)
            case AssignStmt():
                return self._fmt_assign(node)
            case ReturnStmt():
                return self._fmt_return(node)
            case IfStmt():
                return self._fmt_if(node)
            case ForStmt():
                return self._fmt_for(node)
            case WhileStmt():
                return self._fmt_while(node)
            case MatchStmt():
                return self._fmt_match(node)
            case BreakStmt():
                return f"{self._indent()}break"
            case ContinueStmt():
                return f"{self._indent()}continue"
            case ExprStmt():
                return f"{self._indent()}{self._fmt_expr(node.expr)}"
            case ImportDecl():
                return self._fmt_import(node)
            case FromImportDecl():
                return self._fmt_from_import(node)
            case OnGpuStmt():
                return self._fmt_on_gpu(node)
            case ArenaStmt():
                return self._fmt_arena(node)
            case DistributeStmt():
                return self._fmt_distribute(node)
            case SandboxStmt():
                return self._fmt_sandbox(node)
            case SelectStmt():
                return self._fmt_select(node)
            case _:
                # Fallback: treat as expression
                return f"{self._indent()}{self._fmt_expr(node)}"

    def _fmt_fn_decl(self, node: FnDecl) -> str:
        prefix = self._indent()
        async_prefix = "async " if node.is_async else ""
        params = ", ".join(self._fmt_param(p) for p in node.params)
        ret = ""
        if node.return_type:
            ret = f" -> {self._fmt_type(node.return_type)}"
        header = f"{prefix}{async_prefix}fn {node.name}({params}){ret} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_let_decl(self, node: LetDecl) -> str:
        prefix = self._indent()
        keyword = "owned" if node.is_owned else "let"
        type_ann = ""
        if node.type_annotation:
            type_ann = f": {self._fmt_type(node.type_annotation)}"
        val = self._fmt_expr(node.value) if node.value else "none"
        return f"{prefix}{keyword} {node.name}{type_ann} = {val}"

    def _fmt_const_decl(self, node: ConstDecl) -> str:
        prefix = self._indent()
        type_ann = ""
        if node.type_annotation:
            type_ann = f": {self._fmt_type(node.type_annotation)}"
        val = self._fmt_expr(node.value) if node.value else "none"
        return f"{prefix}const {node.name}{type_ann} = {val}"

    def _fmt_model_decl(self, node: ModelDecl) -> str:
        prefix = self._indent()
        params = ", ".join(self._fmt_param(p) for p in node.params)
        header = f"{prefix}model {node.name}({params}) {{"
        parts = [header]
        self._depth += 1
        for layer in node.layers:
            parts.append(f"{self._indent()}{layer.name} = {self._fmt_expr(layer.value)}")
        if node.forward:
            parts.append("")
            fwd_params = ", ".join(self._fmt_param(p) for p in node.forward.params)
            parts.append(f"{self._indent()}forward({fwd_params}) {{")
            body = self._fmt_block_body(node.forward.body)
            parts.append(body)
            parts.append(f"{self._indent()}}}")
        for method in node.methods:
            parts.append("")
            parts.append(self._fmt_fn_decl(method))
        self._depth -= 1
        parts.append(f"{prefix}}}")
        return "\n".join(parts)

    def _fmt_agent_decl(self, node: AgentDecl) -> str:
        prefix = self._indent()
        params = ", ".join(self._fmt_param(p) for p in node.params)
        header = f"{prefix}agent {node.name}({params}) {{"
        parts = [header]
        self._depth += 1
        if node.tools_block:
            parts.append(f"{self._indent()}tools {{")
            self._depth += 1
            for tool in node.tools_block:
                parts.append(f"{self._indent()}{tool.name} = {self._fmt_expr(tool.value)}")
            self._depth -= 1
            parts.append(f"{self._indent()}}}")
        if node.memory_block:
            parts.append("")
            parts.append(f"{self._indent()}memory {{")
            self._depth += 1
            for mem in node.memory_block:
                type_ann = ""
                if mem.type_annotation:
                    type_ann = f": {self._fmt_type(mem.type_annotation)}"
                val = self._fmt_expr(mem.value) if mem.value else "none"
                parts.append(f"{self._indent()}{mem.name}{type_ann} = {val}")
            self._depth -= 1
            parts.append(f"{self._indent()}}}")
        if node.plan_fn:
            parts.append("")
            fparams = ", ".join(self._fmt_param(p) for p in node.plan_fn.params)
            parts.append(f"{self._indent()}plan({fparams}) {{")
            body = self._fmt_block_body(node.plan_fn.body)
            parts.append(body)
            parts.append(f"{self._indent()}}}")
        if node.act_fn:
            parts.append("")
            fparams = ", ".join(self._fmt_param(p) for p in node.act_fn.params)
            parts.append(f"{self._indent()}act({fparams}) {{")
            body = self._fmt_block_body(node.act_fn.body)
            parts.append(body)
            parts.append(f"{self._indent()}}}")
        for method in node.methods:
            parts.append("")
            parts.append(self._fmt_fn_decl(method))
        self._depth -= 1
        parts.append(f"{prefix}}}")
        return "\n".join(parts)

    def _fmt_assign(self, node: AssignStmt) -> str:
        prefix = self._indent()
        target = self._fmt_expr(node.target)
        val = self._fmt_expr(node.value)
        return f"{prefix}{target} {node.op} {val}"

    def _fmt_return(self, node: ReturnStmt) -> str:
        prefix = self._indent()
        if node.value:
            return f"{prefix}return {self._fmt_expr(node.value)}"
        return f"{prefix}return"

    def _fmt_if(self, node: IfStmt) -> str:
        prefix = self._indent()
        parts = []
        cond = self._fmt_expr(node.condition)
        parts.append(f"{prefix}if {cond} {{")
        body = self._fmt_block_body(node.body)
        parts.append(body)
        for elif_cond, elif_body in node.elif_clauses:
            parts.append(f"{prefix}}} elif {self._fmt_expr(elif_cond)} {{")
            parts.append(self._fmt_block_body(elif_body))
        if node.else_body:
            parts.append(f"{prefix}}} else {{")
            parts.append(self._fmt_block_body(node.else_body))
        parts.append(f"{prefix}}}")
        return "\n".join(parts)

    def _fmt_for(self, node: ForStmt) -> str:
        prefix = self._indent()
        iterable = self._fmt_expr(node.iterable)
        header = f"{prefix}for {node.var} in {iterable} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_while(self, node: WhileStmt) -> str:
        prefix = self._indent()
        cond = self._fmt_expr(node.condition)
        header = f"{prefix}while {cond} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_match(self, node: MatchStmt) -> str:
        prefix = self._indent()
        subject = self._fmt_expr(node.subject)
        parts = [f"{prefix}match {subject} {{"]
        self._depth += 1
        for arm in node.arms:
            pattern = self._fmt_pattern(arm.pattern)
            if isinstance(arm.body, list):
                parts.append(f"{self._indent()}{pattern} => {{")
                body = self._fmt_block_body(arm.body)
                parts.append(body)
                parts.append(f"{self._indent()}}},")
            else:
                body_expr = self._fmt_expr(arm.body)
                parts.append(f"{self._indent()}{pattern} => {body_expr},")
        self._depth -= 1
        parts.append(f"{prefix}}}")
        return "\n".join(parts)

    def _fmt_on_gpu(self, node: OnGpuStmt) -> str:
        prefix = self._indent()
        config = self._fmt_kv_config(node.config)
        header = f"{prefix}on gpu{config} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_arena(self, node: ArenaStmt) -> str:
        prefix = self._indent()
        config = self._fmt_kv_config(node.config)
        header = f"{prefix}arena {node.device}{config} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_distribute(self, node: DistributeStmt) -> str:
        prefix = self._indent()
        config_parts = []
        if node.strategy:
            config_parts.append(node.strategy)
        for k, v in node.config.items():
            config_parts.append(f"{k}={self._fmt_expr(v)}")
        config_str = ", ".join(config_parts)
        header = f"{prefix}distribute({config_str}) {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_sandbox(self, node: SandboxStmt) -> str:
        prefix = self._indent()
        config = self._fmt_kv_config(node.permissions)
        header = f"{prefix}sandbox{config} {{"
        body = self._fmt_block_body(node.body)
        return f"{header}\n{body}\n{prefix}}}"

    def _fmt_select(self, node: SelectStmt) -> str:
        prefix = self._indent()
        parts = [f"{prefix}select {{"]
        self._depth += 1
        for cond, body in node.arms:
            parts.append(f"{self._indent()}{self._fmt_expr(cond)} {{")
            inner = self._fmt_block_body(body)
            parts.append(inner)
            parts.append(f"{self._indent()}}}")
        self._depth -= 1
        parts.append(f"{prefix}}}")
        return "\n".join(parts)

    def _fmt_import(self, node: ImportDecl) -> str:
        prefix = self._indent()
        path = ".".join(node.path)
        alias = f" as {node.alias}" if node.alias else ""
        return f"{prefix}import {path}{alias}"

    def _fmt_from_import(self, node: FromImportDecl) -> str:
        prefix = self._indent()
        path = ".".join(node.path)
        names = ", ".join(
            f"{n} as {a}" if a else n
            for n, a in node.names
        )
        return f"{prefix}from {path} import {names}"

    # --- Block body helper ---

    def _fmt_block_body(self, stmts: list[ASTNode]) -> str:
        self._depth += 1
        lines = [self._fmt(stmt) for stmt in stmts]
        self._depth -= 1
        return "\n".join(lines)

    # --- Expressions ---

    def _fmt_expr(self, node: ASTNode) -> str:
        match node:
            case IntLiteral():
                return str(node.value)
            case FloatLiteral():
                return str(node.value)
            case StringLiteral():
                escaped = node.value.replace("\\", "\\\\").replace('"', '\\"')
                return f'"{escaped}"'
            case BoolLiteral():
                return "true" if node.value else "false"
            case NoneLiteral():
                return "none"
            case Identifier():
                return node.name
            case SelfExpr():
                return "self"
            case ListLiteral():
                elems = ", ".join(self._fmt_expr(e) for e in node.elements)
                return f"[{elems}]"
            case MapLiteral():
                pairs = ", ".join(
                    f"{self._fmt_expr(k)}: {self._fmt_expr(v)}"
                    for k, v in zip(node.keys, node.values)
                )
                return f"{{{pairs}}}"
            case BinOp():
                left = self._fmt_expr(node.left)
                right = self._fmt_expr(node.right)
                return f"{left} {node.op} {right}"
            case UnaryOp():
                operand = self._fmt_expr(node.operand)
                if node.op == "not":
                    return f"not {operand}"
                return f"{node.op}{operand}"
            case CallExpr():
                callee = self._fmt_expr(node.callee)
                args = [self._fmt_expr(a) for a in node.args]
                kwargs = [f"{k}={self._fmt_expr(v)}" for k, v in node.kwargs.items()]
                all_args = ", ".join(args + kwargs)
                return f"{callee}({all_args})"
            case IndexExpr():
                return f"{self._fmt_expr(node.obj)}[{self._fmt_expr(node.index)}]"
            case SliceExpr():
                obj = self._fmt_expr(node.obj)
                start = self._fmt_expr(node.start) if node.start else ""
                stop = self._fmt_expr(node.stop) if node.stop else ""
                if node.step:
                    step = self._fmt_expr(node.step)
                    return f"{obj}[{start}:{stop}:{step}]"
                return f"{obj}[{start}:{stop}]"
            case DotExpr():
                return f"{self._fmt_expr(node.obj)}.{node.attr}"
            case PipeExpr():
                return f"{self._fmt_expr(node.left)} |> {self._fmt_expr(node.right)}"
            case LambdaExpr():
                params = ", ".join(self._fmt_param(p) for p in node.params)
                body = self._fmt_expr(node.body)
                return f"|{params}| {body}"
            case FnDecl():
                # Inline anonymous function
                return self._fmt_fn_decl(node)
            case GradExpr():
                func = self._fmt_expr(node.func)
                if node.order:
                    return f"grad({func}, order={self._fmt_expr(node.order)})"
                return f"grad({func})"
            case TryExpr():
                return f"try {self._fmt_expr(node.expr)}"
            case PanicExpr():
                return f"panic({self._fmt_expr(node.message)})"
            case SpawnExpr():
                self._depth += 1
                body_lines = [self._fmt(s) for s in node.body]
                self._depth -= 1
                body_str = "\n".join(body_lines)
                return f"spawn {{\n{body_str}\n{self._indent()}}}"
            case ConstrainExpr():
                config = self._fmt_kv_config(node.config)
                self._depth += 1
                body_lines = [self._fmt(s) for s in node.body]
                self._depth -= 1
                body_str = "\n".join(body_lines)
                return f"constrain{config} {{\n{body_str}\n{self._indent()}}}"
            case SharedExpr():
                return f"shared({self._fmt_expr(node.value)})"
            case ChannelExpr():
                elem = self._fmt_type(node.elem_type) if node.elem_type else ""
                if node.buffer_size:
                    return f"channel[{elem}]({self._fmt_expr(node.buffer_size)})"
                return f"channel[{elem}]"
            case MoveExpr():
                return f"move {node.name}"
            case AwaitExpr():
                return f"await {self._fmt_expr(node.expr)}"
            case TensorTypeExpr():
                return self._fmt_tensor_type(node)
            case ResultTypeExpr():
                ok = self._fmt_type(node.ok_type) if node.ok_type else "?"
                err = self._fmt_type(node.err_type) if node.err_type else "?"
                return f"Result[{ok}, {err}]"
            case TypeAnnotation():
                return self._fmt_type(node)
            case _:
                return f"<unknown:{type(node).__name__}>"

    # --- Patterns ---

    def _fmt_pattern(self, node: ASTNode) -> str:
        match node:
            case WildcardPattern():
                return "_"
            case LiteralPattern():
                return self._fmt_expr(node.value)
            case IdentPattern():
                return node.name
            case ConstructorPattern():
                if node.args:
                    args = ", ".join(self._fmt_pattern(a) for a in node.args)
                    return f"{node.name}({args})"
                return f"{node.name}"
            case _:
                return self._fmt_expr(node)

    # --- Parameters & Types ---

    def _fmt_param(self, p: Param) -> str:
        parts = []
        if p.is_move:
            parts.append("move")
        if p.is_mut:
            parts.append("mut")
        parts.append(p.name)
        s = " ".join(parts)
        if p.type_annotation:
            s += f": {self._fmt_type(p.type_annotation)}"
        if p.default:
            s += f" = {self._fmt_expr(p.default)}"
        return s

    def _fmt_type(self, node: ASTNode) -> str:
        if isinstance(node, TypeAnnotation):
            if node.params:
                params = ", ".join(self._fmt_type(p) for p in node.params)
                if node.name == "list":
                    return f"[{params}]"
                if node.name == "map":
                    return f"{{{params}}}"
                return f"{node.name}[{params}]"
            return node.name
        if isinstance(node, TensorTypeExpr):
            return self._fmt_tensor_type(node)
        if isinstance(node, ResultTypeExpr):
            ok = self._fmt_type(node.ok_type) if node.ok_type else "?"
            err = self._fmt_type(node.err_type) if node.err_type else "?"
            return f"Result[{ok}, {err}]"
        return self._fmt_expr(node)

    def _fmt_tensor_type(self, node: TensorTypeExpr) -> str:
        if node.dtype:
            dtype = self._fmt_type(node.dtype)
            dims = ", ".join(self._fmt_expr(d) for d in node.dims)
            if dims:
                return f"tensor[{dtype}, {dims}]"
            return f"tensor[{dtype}]"
        return "tensor"

    def _fmt_kv_config(self, config: dict[str, ASTNode]) -> str:
        if not config:
            return ""
        pairs = ", ".join(f"{k}={self._fmt_expr(v)}" for k, v in config.items())
        return f"({pairs})"


def format_file(path: str) -> str:
    """Format a .nova file and return the formatted source."""
    return Formatter().format_file(path)


def format_source(source: str) -> str:
    """Format Nova source code and return the formatted version."""
    return Formatter().format_source(source)
