"""Nova tree-walking interpreter — Phase 0 bootstrap."""

import builtins as _py_builtins
import math
import random

from ..ast_nodes.nodes import *
from ..types.values import *
from ..types.ownership import OwnershipManager
from .environment import Environment


def _parse_budget(val: str) -> int:
    """Parse a budget string like '24gb' into bytes."""
    val = val.lower().strip()
    multipliers = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if val.endswith(suffix):
            return int(float(val[:-len(suffix)]) * mult)
    return int(val)


class Interpreter:
    def __init__(self):
        self.global_env = Environment()
        self.ownership = OwnershipManager()
        self._arena_stack: list[NovaArena] = []
        self._setup_builtins()

    # -------------------------------------------------------------------
    # Built-in functions
    # -------------------------------------------------------------------

    def _setup_builtins(self):
        self.global_env.define("print", NovaBuiltin("print", self._builtin_print))
        self.global_env.define("len", NovaBuiltin("len", self._builtin_len))
        self.global_env.define("range", NovaBuiltin("range", self._builtin_range))
        self.global_env.define("type_of", NovaBuiltin("type_of", self._builtin_type_of))
        self.global_env.define("Ok", NovaBuiltin("Ok", self._builtin_ok))
        self.global_env.define("Err", NovaBuiltin("Err", self._builtin_err))
        self.global_env.define("abs", NovaBuiltin("abs", self._builtin_abs))
        self.global_env.define("sum", NovaBuiltin("sum", self._builtin_sum))
        self.global_env.define("min", NovaBuiltin("min", self._builtin_min))
        self.global_env.define("max", NovaBuiltin("max", self._builtin_max))

        # Math builtins
        self.global_env.define("sqrt", NovaBuiltin("sqrt", self._builtin_sqrt))
        self.global_env.define("log", NovaBuiltin("log", self._builtin_log))
        self.global_env.define("exp", NovaBuiltin("exp", self._builtin_exp))
        self.global_env.define("pow", NovaBuiltin("pow", self._builtin_pow))
        self.global_env.define("floor", NovaBuiltin("floor", self._builtin_floor))
        self.global_env.define("ceil", NovaBuiltin("ceil", self._builtin_ceil))
        self.global_env.define("round", NovaBuiltin("round", self._builtin_round))
        self.global_env.define("sin", NovaBuiltin("sin", self._builtin_sin))
        self.global_env.define("cos", NovaBuiltin("cos", self._builtin_cos))
        self.global_env.define("tan", NovaBuiltin("tan", self._builtin_tan))

        # Assert builtins (for test runner)
        self.global_env.define("assert_eq", NovaBuiltin("assert_eq", self._builtin_assert_eq))
        self.global_env.define("assert_ne", NovaBuiltin("assert_ne", self._builtin_assert_ne))
        self.global_env.define("assert_true", NovaBuiltin("assert_true", self._builtin_assert_true))
        self.global_env.define("assert_false", NovaBuiltin("assert_false", self._builtin_assert_false))

    @staticmethod
    def _builtin_print(args, kwargs):
        parts = []
        for a in args:
            if isinstance(a, NovaStr):
                parts.append(a.value)
            else:
                parts.append(repr(a))
        print(" ".join(parts))
        return NovaNone()

    @staticmethod
    def _builtin_len(args, kwargs):
        obj = args[0]
        if isinstance(obj, NovaList):
            return NovaInt(len(obj.elements))
        if isinstance(obj, NovaStr):
            return NovaInt(len(obj.value))
        if isinstance(obj, NovaMap):
            return NovaInt(len(obj.entries))
        if isinstance(obj, NovaTensor):
            return NovaInt(obj.shape[0] if obj.shape else 0)
        raise NovaError(f"len() not supported for {type(obj).__name__}")

    @staticmethod
    def _builtin_range(args, kwargs):
        if len(args) == 1:
            n = args[0].value
            return NovaList([NovaInt(i) for i in range(n)])
        if len(args) == 2:
            return NovaList([NovaInt(i) for i in range(args[0].value, args[1].value)])
        if len(args) == 3:
            return NovaList([NovaInt(i) for i in range(args[0].value, args[1].value, args[2].value)])
        raise NovaError("range() takes 1-3 arguments")

    @staticmethod
    def _builtin_type_of(args, kwargs):
        v = args[0]
        type_names = {
            NovaInt: "int", NovaFloat: "float", NovaStr: "str",
            NovaBool: "bool", NovaNone: "none", NovaList: "list",
            NovaMap: "map", NovaTensor: "tensor", NovaFrame: "frame",
            NovaFunction: "fn", NovaBuiltin: "fn",
            NovaModel: "model", NovaAgent: "agent",
            NovaResult: "result", NovaChannel: "channel",
        }
        return NovaStr(type_names.get(type(v), "unknown"))

    @staticmethod
    def _builtin_ok(args, kwargs):
        return NovaResult.ok(args[0])

    @staticmethod
    def _builtin_err(args, kwargs):
        return NovaResult.err(args[0])

    @staticmethod
    def _builtin_abs(args, kwargs):
        v = args[0]
        if isinstance(v, NovaInt):
            return NovaInt(abs(v.value))
        if isinstance(v, NovaFloat):
            return NovaFloat(abs(v.value))
        raise NovaError("abs() requires int or float")

    @staticmethod
    def _builtin_sum(args, kwargs):
        lst = args[0]
        if isinstance(lst, NovaList):
            total = 0
            for e in lst.elements:
                total += e.value
            return NovaFloat(total) if any(isinstance(e, NovaFloat) for e in lst.elements) else NovaInt(total)
        raise NovaError("sum() requires a list")

    @staticmethod
    def _builtin_min(args, kwargs):
        if len(args) == 1 and isinstance(args[0], NovaList):
            vals = [e.value for e in args[0].elements]
            m = min(vals)
        else:
            vals = [a.value for a in args]
            m = min(vals)
        return NovaFloat(m) if isinstance(m, float) else NovaInt(m)

    @staticmethod
    def _builtin_max(args, kwargs):
        if len(args) == 1 and isinstance(args[0], NovaList):
            vals = [e.value for e in args[0].elements]
            m = max(vals)
        else:
            vals = [a.value for a in args]
            m = max(vals)
        return NovaFloat(m) if isinstance(m, float) else NovaInt(m)

    # --- Math builtins ---

    @staticmethod
    def _builtin_sqrt(args, kwargs):
        v = args[0]
        return NovaFloat(math.sqrt(v.value))

    @staticmethod
    def _builtin_log(args, kwargs):
        v = args[0]
        return NovaFloat(math.log(v.value))

    @staticmethod
    def _builtin_exp(args, kwargs):
        v = args[0]
        return NovaFloat(math.exp(v.value))

    @staticmethod
    def _builtin_pow(args, kwargs):
        base = args[0].value
        exp = args[1].value
        result = base ** exp
        if isinstance(result, float) or isinstance(args[0], NovaFloat) or isinstance(args[1], NovaFloat):
            return NovaFloat(float(result))
        return NovaInt(int(result))

    @staticmethod
    def _builtin_floor(args, kwargs):
        v = args[0]
        return NovaInt(math.floor(v.value))

    @staticmethod
    def _builtin_ceil(args, kwargs):
        v = args[0]
        return NovaInt(math.ceil(v.value))

    @staticmethod
    def _builtin_round(args, kwargs):
        v = args[0]
        decimals = 0
        if len(args) > 1:
            decimals = args[1].value
        elif "decimals" in kwargs:
            decimals = kwargs["decimals"].value
        result = round(v.value, decimals)
        if decimals == 0:
            return NovaInt(int(result))
        return NovaFloat(result)

    @staticmethod
    def _builtin_sin(args, kwargs):
        return NovaFloat(math.sin(args[0].value))

    @staticmethod
    def _builtin_cos(args, kwargs):
        return NovaFloat(math.cos(args[0].value))

    @staticmethod
    def _builtin_tan(args, kwargs):
        return NovaFloat(math.tan(args[0].value))

    # --- Assert builtins ---

    @staticmethod
    def _builtin_assert_eq(args, kwargs):
        a, b = args[0], args[1]
        a_val = a.value if hasattr(a, 'value') else a
        b_val = b.value if hasattr(b, 'value') else b
        if a_val != b_val:
            raise NovaPanic(f"assert_eq failed: {a!r} != {b!r}")
        return NovaNone()

    @staticmethod
    def _builtin_assert_ne(args, kwargs):
        a, b = args[0], args[1]
        a_val = a.value if hasattr(a, 'value') else a
        b_val = b.value if hasattr(b, 'value') else b
        if a_val == b_val:
            raise NovaPanic(f"assert_ne failed: {a!r} == {b!r}")
        return NovaNone()

    @staticmethod
    def _builtin_assert_true(args, kwargs):
        v = args[0]
        truthy = False
        if isinstance(v, NovaBool):
            truthy = v.value
        elif isinstance(v, NovaNone):
            truthy = False
        elif isinstance(v, NovaInt):
            truthy = v.value != 0
        elif isinstance(v, NovaFloat):
            truthy = v.value != 0.0
        elif isinstance(v, NovaStr):
            truthy = len(v.value) > 0
        elif isinstance(v, NovaList):
            truthy = len(v.elements) > 0
        else:
            truthy = True
        if not truthy:
            raise NovaPanic(f"assert_true failed: {v!r} is not truthy")
        return NovaNone()

    @staticmethod
    def _builtin_assert_false(args, kwargs):
        v = args[0]
        truthy = False
        if isinstance(v, NovaBool):
            truthy = v.value
        elif isinstance(v, NovaNone):
            truthy = False
        elif isinstance(v, NovaInt):
            truthy = v.value != 0
        elif isinstance(v, NovaFloat):
            truthy = v.value != 0.0
        elif isinstance(v, NovaStr):
            truthy = len(v.value) > 0
        elif isinstance(v, NovaList):
            truthy = len(v.elements) > 0
        else:
            truthy = True
        if truthy:
            raise NovaPanic(f"assert_false failed: {v!r} is truthy")
        return NovaNone()

    # -------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------

    def execute(self, program: Program) -> NovaValue:
        result = NovaNone()
        self.ownership.enter_scope()
        for node in program.body:
            result = self._exec(node, self.global_env)
        self.ownership.exit_scope()
        return result

    # -------------------------------------------------------------------
    # Statement dispatch
    # -------------------------------------------------------------------

    def _exec(self, node: ASTNode, env: Environment) -> NovaValue:
        match node:
            case FnDecl():
                return self._exec_fn_decl(node, env)
            case LetDecl():
                return self._exec_let_decl(node, env)
            case ConstDecl():
                return self._exec_const_decl(node, env)
            case ModelDecl():
                return self._exec_model_decl(node, env)
            case AgentDecl():
                return self._exec_agent_decl(node, env)
            case AssignStmt():
                return self._exec_assign(node, env)
            case ReturnStmt():
                val = self._eval(node.value, env) if node.value else NovaNone()
                raise ReturnSignal(val)
            case IfStmt():
                return self._exec_if(node, env)
            case ForStmt():
                return self._exec_for(node, env)
            case WhileStmt():
                return self._exec_while(node, env)
            case MatchStmt():
                return self._exec_match(node, env)
            case OnGpuStmt():
                return self._exec_on_gpu(node, env)
            case ArenaStmt():
                return self._exec_arena(node, env)
            case DistributeStmt():
                return self._exec_distribute(node, env)
            case SandboxStmt():
                return self._exec_sandbox(node, env)
            case SelectStmt():
                return self._exec_select(node, env)
            case BreakStmt():
                raise BreakSignal()
            case ContinueStmt():
                raise ContinueSignal()
            case ExprStmt():
                return self._eval(node.expr, env)
            case ImportDecl() | FromImportDecl():
                return NovaNone()  # imports are no-ops in bootstrap
            case _:
                return self._eval(node, env)

    # -------------------------------------------------------------------
    # Declarations
    # -------------------------------------------------------------------

    def _exec_fn_decl(self, node: FnDecl, env: Environment) -> NovaValue:
        fn = NovaFunction(
            name=node.name, params=node.params, body=node.body,
            closure_env=env, is_async=node.is_async,
        )
        env.define(node.name, fn)
        return fn

    def _exec_let_decl(self, node: LetDecl, env: Environment) -> NovaValue:
        val = self._eval(node.value, env)
        env.define(node.name, val)
        if node.is_owned or not self._is_scalar(val):
            self.ownership.register(node.name, val)
        return val

    def _exec_const_decl(self, node: ConstDecl, env: Environment) -> NovaValue:
        val = self._eval(node.value, env) if node.value else NovaNone()
        env.define(node.name, val)
        return val

    def _exec_model_decl(self, node: ModelDecl, env: Environment) -> NovaValue:
        model_class = NovaModelClass(
            name=node.name,
            init_params=node.params,
            layer_defs=node.layers,
            forward_ast=node.forward,
            method_asts=node.methods,
        )
        env.define(node.name, model_class)
        return model_class

    def _exec_agent_decl(self, node: AgentDecl, env: Environment) -> NovaValue:
        agent_class = NovaAgentClass(
            name=node.name,
            init_params=node.params,
            tools_defs=node.tools_block,
            memory_defs=node.memory_block,
            plan_ast=node.plan_fn,
            act_ast=node.act_fn,
            method_asts=node.methods,
        )
        env.define(node.name, agent_class)
        return agent_class

    # -------------------------------------------------------------------
    # Statements
    # -------------------------------------------------------------------

    def _exec_assign(self, node: AssignStmt, env: Environment) -> NovaValue:
        val = self._eval(node.value, env)
        if node.op != "=":
            old = self._eval(node.target, env)
            val = self._apply_binop(node.op[0], old, val)
        if isinstance(node.target, Identifier):
            env.set(node.target.name, val)
        elif isinstance(node.target, DotExpr):
            obj = self._eval(node.target.obj, env)
            if isinstance(obj, NovaModel):
                obj.layers[node.target.attr] = val
            elif isinstance(obj, NovaAgent):
                obj.memory[node.target.attr] = val
        elif isinstance(node.target, IndexExpr):
            obj = self._eval(node.target.obj, env)
            idx = self._eval(node.target.index, env)
            if isinstance(obj, NovaList):
                obj.elements[idx.value] = val
            elif isinstance(obj, NovaMap):
                obj.entries[idx] = val
        return val

    def _exec_if(self, node: IfStmt, env: Environment) -> NovaValue:
        if self._truthy(self._eval(node.condition, env)):
            return self._exec_block(node.body, env)
        for cond, body in node.elif_clauses:
            if self._truthy(self._eval(cond, env)):
                return self._exec_block(body, env)
        if node.else_body:
            return self._exec_block(node.else_body, env)
        return NovaNone()

    def _exec_for(self, node: ForStmt, env: Environment) -> NovaValue:
        iterable = self._eval(node.iterable, env)
        items = self._to_iterable(iterable)
        result = NovaNone()
        for item in items:
            child_env = env.child()
            child_env.define(node.var, item)
            self.ownership.enter_scope()
            try:
                result = self._exec_block_raw(node.body, child_env)
            except BreakSignal:
                self.ownership.exit_scope()
                break
            except ContinueSignal:
                pass
            self.ownership.exit_scope()
        return result

    def _exec_while(self, node: WhileStmt, env: Environment) -> NovaValue:
        result = NovaNone()
        while self._truthy(self._eval(node.condition, env)):
            self.ownership.enter_scope()
            try:
                result = self._exec_block_raw(node.body, env)
            except BreakSignal:
                self.ownership.exit_scope()
                break
            except ContinueSignal:
                pass
            self.ownership.exit_scope()
        return result

    def _exec_match(self, node: MatchStmt, env: Environment) -> NovaValue:
        subject = self._eval(node.subject, env)
        for arm in node.arms:
            bindings = {}
            if self._match_pattern(arm.pattern, subject, bindings):
                child_env = env.child()
                for k, v in bindings.items():
                    child_env.define(k, v)
                return self._eval(arm.body, child_env) if not isinstance(arm.body, list) else self._exec_block(arm.body, child_env)
        return NovaNone()

    def _match_pattern(self, pattern: ASTNode, value: NovaValue, bindings: dict) -> bool:
        match pattern:
            case WildcardPattern():
                return True
            case LiteralPattern():
                lit_val = self._literal_to_value(pattern.value)
                return self._values_equal(lit_val, value)
            case IdentPattern():
                bindings[pattern.name] = value
                return True
            case ConstructorPattern():
                if isinstance(value, NovaResult):
                    if pattern.name == "Ok" and value.is_ok:
                        if pattern.args:
                            return self._match_pattern(pattern.args[0], value.value, bindings)
                        return True
                    if pattern.name == "Err" and not value.is_ok:
                        if pattern.args:
                            return self._match_pattern(pattern.args[0], value.error, bindings)
                        return True
                return False
        return False

    def _exec_on_gpu(self, node: OnGpuStmt, env: Environment) -> NovaValue:
        # In bootstrap, GPU blocks just execute on CPU
        return self._exec_block(node.body, env)

    def _exec_arena(self, node: ArenaStmt, env: Environment) -> NovaValue:
        budget = 0
        for key, val_node in node.config.items():
            val = self._eval(val_node, env)
            if key == "budget":
                if isinstance(val, NovaStr):
                    budget = _parse_budget(val.value)
                elif isinstance(val, NovaInt):
                    budget = val.value
        arena = NovaArena(device=node.device, budget_bytes=budget)
        self._arena_stack.append(arena)
        self.ownership.enter_scope()
        result = self._exec_block_raw(node.body, env)
        self.ownership.exit_scope()
        arena.free_all()
        self._arena_stack.pop()
        return result

    def _exec_distribute(self, node: DistributeStmt, env: Environment) -> NovaValue:
        # In bootstrap, distribute blocks just execute locally
        return self._exec_block(node.body, env)

    def _exec_sandbox(self, node: SandboxStmt, env: Environment) -> NovaValue:
        # In bootstrap, sandbox blocks execute normally but we record intent
        return self._exec_block(node.body, env)

    def _exec_select(self, node: SelectStmt, env: Environment) -> NovaValue:
        for cond_expr, body in node.arms:
            cond = self._eval(cond_expr, env)
            if self._truthy(cond):
                return self._exec_block(body, env)
        return NovaNone()

    # -------------------------------------------------------------------
    # Block execution
    # -------------------------------------------------------------------

    def _exec_block(self, stmts: list[ASTNode], env: Environment) -> NovaValue:
        child_env = env.child()
        self.ownership.enter_scope()
        result = NovaNone()
        try:
            for stmt in stmts:
                result = self._exec(stmt, child_env)
        finally:
            self.ownership.exit_scope()
        return result

    def _exec_block_raw(self, stmts: list[ASTNode], env: Environment) -> NovaValue:
        """Execute block without creating new scope (caller manages scope)."""
        result = NovaNone()
        for stmt in stmts:
            result = self._exec(stmt, env)
        return result

    # -------------------------------------------------------------------
    # Expression evaluation
    # -------------------------------------------------------------------

    def _eval(self, node: ASTNode, env: Environment) -> NovaValue:
        match node:
            case IntLiteral():
                return NovaInt(node.value)
            case FloatLiteral():
                return NovaFloat(node.value)
            case StringLiteral():
                return NovaStr(self._interpolate_string(node.value, env))
            case BoolLiteral():
                return NovaBool(node.value)
            case NoneLiteral():
                return NovaNone()
            case Identifier():
                return self._eval_ident(node, env)
            case SelfExpr():
                return env.get("self")
            case ListLiteral():
                return NovaList([self._eval(e, env) for e in node.elements])
            case MapLiteral():
                keys = [self._eval(k, env) for k in node.keys]
                vals = [self._eval(v, env) for v in node.values]
                return NovaMap(dict(zip(keys, vals)))
            case BinOp():
                return self._eval_binop(node, env)
            case UnaryOp():
                return self._eval_unary(node, env)
            case CallExpr():
                return self._eval_call(node, env)
            case IndexExpr():
                return self._eval_index(node, env)
            case SliceExpr():
                return self._eval_slice(node, env)
            case DotExpr():
                return self._eval_dot(node, env)
            case PipeExpr():
                left = self._eval(node.left, env)
                return self._call_value(self._eval(node.right, env), [left], {}, env)
            case LambdaExpr():
                return NovaFunction(
                    name="<lambda>", params=node.params,
                    body=[node.body] if not isinstance(node.body, list) else node.body,
                    closure_env=env,
                )
            case FnDecl():
                fn = NovaFunction(
                    name=node.name, params=node.params, body=node.body,
                    closure_env=env, is_async=node.is_async,
                )
                if node.name != "<anon>":
                    env.define(node.name, fn)
                return fn
            case GradExpr():
                return self._eval_grad(node, env)
            case TryExpr():
                return self._eval_try(node, env)
            case PanicExpr():
                msg = self._eval(node.message, env)
                raise NovaPanic(msg.value if isinstance(msg, NovaStr) else repr(msg))
            case SpawnExpr():
                return self._exec_block(node.body, env)
            case ConstrainExpr():
                return self._exec_block(node.body, env)
            case SharedExpr():
                return self._eval(node.value, env)
            case ChannelExpr():
                buf = 0
                if node.buffer_size:
                    buf = self._eval(node.buffer_size, env).value
                return NovaChannel(buffer_size=buf)
            case MoveExpr():
                return NovaValue()  # placeholder
            case TensorTypeExpr():
                return self._eval_tensor_type(node, env)
            case AwaitExpr():
                return self._eval(node.expr, env)
            case _:
                raise NovaError(f"Cannot evaluate node: {type(node).__name__}")

    def _eval_ident(self, node: Identifier, env: Environment) -> NovaValue:
        self.ownership.access(node.name)
        return env.get(node.name)

    def _eval_binop(self, node: BinOp, env: Environment) -> NovaValue:
        if node.op == "and":
            left = self._eval(node.left, env)
            return left if not self._truthy(left) else self._eval(node.right, env)
        if node.op == "or":
            left = self._eval(node.left, env)
            return left if self._truthy(left) else self._eval(node.right, env)

        left = self._eval(node.left, env)
        right = self._eval(node.right, env)
        return self._apply_binop(node.op, left, right)

    def _apply_binop(self, op: str, left: NovaValue, right: NovaValue) -> NovaValue:
        # Numeric operations
        if isinstance(left, (NovaInt, NovaFloat)) and isinstance(right, (NovaInt, NovaFloat)):
            lv, rv = left.value, right.value
            use_float = isinstance(left, NovaFloat) or isinstance(right, NovaFloat)
            wrap = NovaFloat if use_float else NovaInt
            match op:
                case "+":  return wrap(lv + rv)
                case "-":  return wrap(lv - rv)
                case "*":  return wrap(lv * rv)
                case "/":  return NovaFloat(lv / rv)
                case "//": return wrap(lv // rv)
                case "%":  return wrap(lv % rv)
                case "**": return wrap(lv ** rv)
                case "==": return NovaBool(lv == rv)
                case "!=": return NovaBool(lv != rv)
                case "<":  return NovaBool(lv < rv)
                case ">":  return NovaBool(lv > rv)
                case "<=": return NovaBool(lv <= rv)
                case ">=": return NovaBool(lv >= rv)
                case "&":  return NovaInt(int(lv) & int(rv))
                case "|":  return NovaInt(int(lv) | int(rv))
                case "^":  return NovaInt(int(lv) ^ int(rv))
                case "<<": return NovaInt(int(lv) << int(rv))
                case ">>": return NovaInt(int(lv) >> int(rv))

        # String concatenation (auto-coerce non-strings with +)
        if isinstance(left, NovaStr) and op == "+":
            if isinstance(right, NovaStr):
                return NovaStr(left.value + right.value)
            return NovaStr(left.value + self._to_string(right))
        if isinstance(right, NovaStr) and op == "+" and isinstance(left, NovaStr):
            return NovaStr(left.value + right.value)

        if isinstance(left, NovaStr) and isinstance(right, NovaStr):
            if op == "==":
                return NovaBool(left.value == right.value)
            if op == "!=":
                return NovaBool(left.value != right.value)

        # String * int
        if isinstance(left, NovaStr) and isinstance(right, NovaInt) and op == "*":
            return NovaStr(left.value * right.value)

        # List concatenation
        if isinstance(left, NovaList) and isinstance(right, NovaList) and op == "+":
            return NovaList(left.elements + right.elements)

        # 'in' operator
        if op == "in":
            if isinstance(right, NovaList):
                return NovaBool(any(self._values_equal(left, e) for e in right.elements))
            if isinstance(right, NovaMap):
                return NovaBool(any(self._values_equal(left, k) for k in right.entries.keys()))
            if isinstance(right, NovaStr) and isinstance(left, NovaStr):
                return NovaBool(left.value in right.value)

        # Tensor matmul
        if isinstance(left, NovaTensor) and isinstance(right, NovaTensor) and op == "@":
            return self._tensor_matmul(left, right)

        # Tensor arithmetic
        if isinstance(left, NovaTensor) or isinstance(right, NovaTensor):
            return self._tensor_arith(op, left, right)

        # Equality fallback
        if op == "==":
            return NovaBool(self._values_equal(left, right))
        if op == "!=":
            return NovaBool(not self._values_equal(left, right))

        raise NovaError(f"Unsupported operation: {type(left).__name__} {op} {type(right).__name__}")

    def _eval_unary(self, node: UnaryOp, env: Environment) -> NovaValue:
        val = self._eval(node.operand, env)
        match node.op:
            case "-":
                if isinstance(val, NovaInt):
                    return NovaInt(-val.value)
                if isinstance(val, NovaFloat):
                    return NovaFloat(-val.value)
            case "~":
                if isinstance(val, NovaInt):
                    return NovaInt(~val.value)
            case "not":
                return NovaBool(not self._truthy(val))
        raise NovaError(f"Unsupported unary op: {node.op} on {type(val).__name__}")

    def _eval_call(self, node: CallExpr, env: Environment) -> NovaValue:
        callee = self._eval(node.callee, env)
        args = [self._eval(a, env) for a in node.args]
        kwargs = {k: self._eval(v, env) for k, v in node.kwargs.items()}
        return self._call_value(callee, args, kwargs, env)

    def _call_value(self, callee: NovaValue, args: list, kwargs: dict,
                    env: Environment) -> NovaValue:
        if isinstance(callee, NovaBuiltin):
            return callee.fn(args, kwargs)

        if isinstance(callee, NovaFunction):
            return self._call_function(callee, args, kwargs)

        if isinstance(callee, NovaModelClass):
            return self._instantiate_model(callee, args, kwargs, env)

        if isinstance(callee, NovaAgentClass):
            return self._instantiate_agent(callee, args, kwargs, env)

        if isinstance(callee, NovaModel):
            if callee.forward_fn:
                return self._call_function(callee.forward_fn, args, kwargs)
            raise NovaError(f"Model {callee.name} has no forward() method")

        raise NovaError(f"Value of type {type(callee).__name__} is not callable")

    def _call_function(self, fn: NovaFunction, args: list, kwargs: dict) -> NovaValue:
        call_env = (fn.closure_env or self.global_env).child()

        for i, param in enumerate(fn.params):
            if i < len(args):
                call_env.define(param.name, args[i])
            elif param.name in kwargs:
                call_env.define(param.name, kwargs[param.name])
            elif param.default is not None:
                call_env.define(param.name, self._eval(param.default, call_env))
            else:
                call_env.define(param.name, NovaNone())

        self.ownership.enter_scope()
        last = NovaNone()
        try:
            for stmt in fn.body:
                last = self._exec(stmt, call_env)
        except ReturnSignal as ret:
            self.ownership.exit_scope()
            return ret.value
        self.ownership.exit_scope()
        return last

    def _instantiate_model(self, cls: NovaModelClass, args: list, kwargs: dict,
                           env: Environment) -> NovaValue:
        init_env = env.child()
        for i, param in enumerate(cls.init_params):
            if i < len(args):
                init_env.define(param.name, args[i])
            elif param.name in kwargs:
                init_env.define(param.name, kwargs[param.name])
            elif param.default is not None:
                init_env.define(param.name, self._eval(param.default, init_env))

        layers = {}
        for layer_def in cls.layer_defs:
            layers[layer_def.name] = self._eval(layer_def.value, init_env)

        model_env = init_env.child()
        for name, val in layers.items():
            model_env.define(name, val)

        forward_fn = None
        if cls.forward_ast:
            forward_fn = NovaFunction(
                name="forward", params=cls.forward_ast.params,
                body=cls.forward_ast.body, closure_env=model_env,
            )

        methods = {}
        for m in cls.method_asts:
            methods[m.name] = NovaFunction(
                name=m.name, params=m.params,
                body=m.body, closure_env=model_env,
            )

        return NovaModel(
            name=cls.name, layers=layers, forward_fn=forward_fn,
            methods=methods,
        )

    def _instantiate_agent(self, cls: NovaAgentClass, args: list, kwargs: dict,
                           env: Environment) -> NovaValue:
        init_env = env.child()
        for i, param in enumerate(cls.init_params):
            if i < len(args):
                init_env.define(param.name, args[i])
            elif param.name in kwargs:
                init_env.define(param.name, kwargs[param.name])
            elif param.default is not None:
                init_env.define(param.name, self._eval(param.default, init_env))

        tools = {}
        for tool_def in cls.tools_defs:
            tools[tool_def.name] = self._eval(tool_def.value, init_env)

        memory = {}
        for mem_def in cls.memory_defs:
            memory[mem_def.name] = self._eval(mem_def.value, init_env) if mem_def.value else NovaNone()

        agent_env = init_env.child()
        for name, val in tools.items():
            agent_env.define(name, val)
        for name, val in memory.items():
            agent_env.define(name, val)

        plan_fn = None
        if cls.plan_ast:
            plan_fn = NovaFunction(
                name="plan", params=cls.plan_ast.params,
                body=cls.plan_ast.body, closure_env=agent_env,
            )

        act_fn = None
        if cls.act_ast:
            act_fn = NovaFunction(
                name="act", params=cls.act_ast.params,
                body=cls.act_ast.body, closure_env=agent_env,
            )

        methods = {}
        for m in cls.method_asts:
            methods[m.name] = NovaFunction(
                name=m.name, params=m.params,
                body=m.body, closure_env=agent_env,
            )

        return NovaAgent(
            name=cls.name, tools=tools, memory=memory,
            plan_fn=plan_fn, act_fn=act_fn, methods=methods,
        )

    # --- Postfix: index, slice, dot ---

    def _eval_index(self, node: IndexExpr, env: Environment) -> NovaValue:
        obj = self._eval(node.obj, env)
        idx = self._eval(node.index, env)
        if isinstance(obj, NovaList):
            return obj.elements[idx.value]
        if isinstance(obj, NovaMap):
            return obj.entries.get(idx, NovaNone())
        if isinstance(obj, NovaStr):
            return NovaStr(obj.value[idx.value])
        raise NovaError(f"Cannot index into {type(obj).__name__}")

    def _eval_slice(self, node: SliceExpr, env: Environment) -> NovaValue:
        obj = self._eval(node.obj, env)
        start = self._eval(node.start, env).value if node.start else None
        stop = self._eval(node.stop, env).value if node.stop else None
        step = self._eval(node.step, env).value if node.step else None
        if isinstance(obj, NovaList):
            return NovaList(obj.elements[start:stop:step])
        if isinstance(obj, NovaStr):
            return NovaStr(obj.value[start:stop:step])
        raise NovaError(f"Cannot slice {type(obj).__name__}")

    def _eval_dot(self, node: DotExpr, env: Environment) -> NovaValue:
        obj = self._eval(node.obj, env)

        if isinstance(obj, NovaModel):
            if node.attr in obj.layers:
                return obj.layers[node.attr]
            if node.attr in obj.methods:
                return obj.methods[node.attr]
            if node.attr == "parameters":
                return NovaBuiltin("parameters", lambda a, k: obj.parameters())
            if node.attr == "forward":
                return obj.forward_fn
            if node.attr == "name":
                return NovaStr(obj.name)

        if isinstance(obj, NovaAgent):
            if node.attr in obj.tools:
                return obj.tools[node.attr]
            if node.attr in obj.memory:
                return obj.memory[node.attr]
            if node.attr in obj.methods:
                return obj.methods[node.attr]
            if node.attr == "plan" and obj.plan_fn:
                return obj.plan_fn
            if node.attr == "act" and obj.act_fn:
                return obj.act_fn

        if isinstance(obj, NovaTensor):
            match node.attr:
                case "shape":
                    return NovaList([NovaInt(d) for d in obj.shape])
                case "dtype":
                    return NovaStr(obj.dtype)
                case "ndim":
                    return NovaInt(obj.ndim)
                case "size":
                    return NovaInt(obj.size)
                case "device":
                    return NovaStr(obj.device)
                case "grad":
                    return obj.grad if obj.grad else NovaNone()
                case "requires_grad":
                    return NovaBool(obj.requires_grad)
                case "sum":
                    return NovaBuiltin("sum", lambda a, k: obj.sum())
                case "zeros":
                    return NovaBuiltin("zeros", lambda a, k: NovaTensor.zeros(obj.dtype, obj.shape))
                case "ones":
                    return NovaBuiltin("ones", lambda a, k: NovaTensor.ones(obj.dtype, obj.shape))
                case "to":
                    def _to(args, kwargs):
                        t = NovaTensor(data=obj.data[:], dtype=obj.dtype, shape=obj.shape)
                        t.device = args[0].value if args else "cpu"
                        return t
                    return NovaBuiltin("to", _to)
                case "clone":
                    return NovaBuiltin("clone", lambda a, k: NovaTensor(
                        data=obj.data[:], dtype=obj.dtype, shape=obj.shape))
                case "reshape":
                    def _reshape(args, kwargs):
                        if len(args) == 1 and isinstance(args[0], NovaList):
                            new_shape = tuple(e.value for e in args[0].elements)
                        else:
                            new_shape = tuple(a.value for a in args)
                        if math.prod(new_shape) != math.prod(obj.shape):
                            raise NovaError(f"Cannot reshape {obj.shape} to {new_shape}: size mismatch")
                        return NovaTensor(data=obj.data[:], dtype=obj.dtype, shape=new_shape)
                    return NovaBuiltin("reshape", _reshape)
                case "flatten":
                    return NovaBuiltin("flatten", lambda a, k: NovaTensor(
                        data=obj.data[:], dtype=obj.dtype, shape=(len(obj.data),)))
                case "transpose":
                    def _transpose(args, kwargs):
                        if obj.ndim != 2:
                            raise NovaError("transpose() requires a 2D tensor")
                        rows, cols = obj.shape
                        new_data = [0.0] * len(obj.data)
                        for r in range(rows):
                            for c in range(cols):
                                new_data[c * rows + r] = obj.data[r * cols + c]
                        return NovaTensor(data=new_data, dtype=obj.dtype, shape=(cols, rows))
                    return NovaBuiltin("transpose", _transpose)
                case "item":
                    def _item(args, kwargs):
                        if len(obj.data) != 1:
                            raise NovaError(f"item() requires a scalar tensor, got {obj.shape}")
                        v = obj.data[0]
                        if obj.dtype.startswith("f"):
                            return NovaFloat(v)
                        return NovaInt(int(v))
                    return NovaBuiltin("item", _item)
                case "mean":
                    def _mean(args, kwargs):
                        dim = None
                        if args:
                            dim = args[0].value
                        elif "dim" in kwargs:
                            dim = kwargs["dim"].value
                        if dim is None:
                            return NovaFloat(sum(obj.data) / len(obj.data))
                        # Compute mean along a dimension for nD tensors
                        if obj.ndim != 2:
                            return NovaFloat(sum(obj.data) / len(obj.data))
                        rows, cols = obj.shape
                        if dim == 0:
                            result = [sum(obj.data[r * cols + c] for r in range(rows)) / rows for c in range(cols)]
                            return NovaTensor(data=result, dtype=obj.dtype, shape=(cols,))
                        elif dim == 1:
                            result = [sum(obj.data[r * cols + c] for c in range(cols)) / cols for r in range(rows)]
                            return NovaTensor(data=result, dtype=obj.dtype, shape=(rows,))
                        return NovaFloat(sum(obj.data) / len(obj.data))
                    return NovaBuiltin("mean", _mean)
                case "max":
                    return NovaBuiltin("max", lambda a, k: NovaFloat(max(obj.data)) if obj.data else NovaFloat(0.0))
                case "min":
                    return NovaBuiltin("min", lambda a, k: NovaFloat(min(obj.data)) if obj.data else NovaFloat(0.0))
                case "abs":
                    return NovaBuiltin("abs", lambda a, k: NovaTensor(
                        data=[_py_builtins.abs(x) for x in obj.data], dtype=obj.dtype, shape=obj.shape))
                case "backward":
                    def _backward(args, kwargs):
                        print("autograd backward pass (bootstrap stub)")
                        return NovaNone()
                    return NovaBuiltin("backward", _backward)

        if isinstance(obj, NovaFrame):
            if node.attr in obj.columns:
                return NovaList([NovaStr(v) if isinstance(v, str) else NovaFloat(v) for v in obj.columns[node.attr]])
            if node.attr == "shape":
                return NovaList([NovaInt(obj.shape[0]), NovaInt(obj.shape[1])])

        if isinstance(obj, NovaList):
            match node.attr:
                case "length":
                    return NovaInt(len(obj.elements))
                case "push":
                    return NovaBuiltin("push", lambda a, k: (obj.elements.append(a[0]), NovaNone())[-1])
                case "pop":
                    return NovaBuiltin("pop", lambda a, k: obj.elements.pop() if obj.elements else NovaNone())
                case "map":
                    def _list_map(a, k):
                        fn = a[0]
                        return NovaList([self._call_value(fn, [e], {}, env) for e in obj.elements])
                    return NovaBuiltin("map", _list_map)
                case "filter":
                    def _list_filter(a, k):
                        fn = a[0]
                        return NovaList([e for e in obj.elements if self._truthy(self._call_value(fn, [e], {}, env))])
                    return NovaBuiltin("filter", _list_filter)
                case "reduce":
                    def _list_reduce(a, k):
                        fn = a[0]
                        acc = a[1] if len(a) > 1 else obj.elements[0]
                        start = 0 if len(a) > 1 else 1
                        for e in obj.elements[start:]:
                            acc = self._call_value(fn, [acc, e], {}, env)
                        return acc
                    return NovaBuiltin("reduce", _list_reduce)
                case "sort":
                    def _list_sort(a, k):
                        return NovaList(sorted(obj.elements, key=lambda e: e.value))
                    return NovaBuiltin("sort", _list_sort)
                case "reverse":
                    return NovaBuiltin("reverse", lambda a, k: NovaList(list(reversed(obj.elements))))
                case "contains":
                    def _list_contains(a, k):
                        target = a[0]
                        return NovaBool(any(self._values_equal(target, e) for e in obj.elements))
                    return NovaBuiltin("contains", _list_contains)
                case "index":
                    def _list_index(a, k):
                        target = a[0]
                        for i, e in enumerate(obj.elements):
                            if self._values_equal(target, e):
                                return NovaInt(i)
                        return NovaInt(-1)
                    return NovaBuiltin("index", _list_index)
                case "join":
                    def _list_join(a, k):
                        sep = a[0].value if a else ""
                        parts = [self._to_string(e) for e in obj.elements]
                        return NovaStr(sep.join(parts))
                    return NovaBuiltin("join", _list_join)

        if isinstance(obj, NovaStr):
            match node.attr:
                case "length":
                    return NovaInt(len(obj.value))
                case "upper":
                    return NovaBuiltin("upper", lambda a, k: NovaStr(obj.value.upper()))
                case "lower":
                    return NovaBuiltin("lower", lambda a, k: NovaStr(obj.value.lower()))
                case "split":
                    return NovaBuiltin("split", lambda a, k: NovaList(
                        [NovaStr(s) for s in obj.value.split(a[0].value if a else " ")]))
                case "strip":
                    return NovaBuiltin("strip", lambda a, k: NovaStr(obj.value.strip()))
                case "contains":
                    return NovaBuiltin("contains", lambda a, k: NovaBool(a[0].value in obj.value))
                case "starts_with":
                    return NovaBuiltin("starts_with", lambda a, k: NovaBool(obj.value.startswith(a[0].value)))
                case "ends_with":
                    return NovaBuiltin("ends_with", lambda a, k: NovaBool(obj.value.endswith(a[0].value)))
                case "replace":
                    return NovaBuiltin("replace", lambda a, k: NovaStr(obj.value.replace(a[0].value, a[1].value)))
                case "join":
                    def _str_join(a, k):
                        if not a or not isinstance(a[0], NovaList):
                            raise NovaError("join() requires a list argument")
                        parts = [self._to_string(e) for e in a[0].elements]
                        return NovaStr(obj.value.join(parts))
                    return NovaBuiltin("join", _str_join)
                case "chars":
                    return NovaBuiltin("chars", lambda a, k: NovaList([NovaStr(c) for c in obj.value]))
                case "format":
                    def _str_format(a, k):
                        s = obj.value
                        for i, arg in enumerate(a):
                            s = s.replace(f"{{{i}}}", self._to_string(arg))
                        return NovaStr(s)
                    return NovaBuiltin("format", _str_format)

        if isinstance(obj, NovaResult):
            match node.attr:
                case "is_ok":
                    return NovaBool(obj.is_ok)
                case "is_err":
                    return NovaBool(not obj.is_ok)
                case "unwrap":
                    return NovaBuiltin("unwrap", lambda a, k: obj.unwrap())
                case "value":
                    return obj.value if obj.is_ok else NovaNone()
                case "error":
                    return obj.error if not obj.is_ok else NovaNone()

        if isinstance(obj, NovaChannel):
            match node.attr:
                case "send":
                    return NovaBuiltin("send", lambda a, k: (obj.send(a[0]), NovaNone())[-1])
                case "recv":
                    return NovaBuiltin("recv", lambda a, k: obj.recv())

        if isinstance(obj, TensorTypeExpr):
            dtype_name = obj.dtype.name if hasattr(obj.dtype, 'name') else "f32"
            dims = tuple(d.value for d in obj.dims if hasattr(d, 'value'))
            match node.attr:
                case "zeros":
                    return NovaBuiltin("zeros", lambda a, k: NovaTensor.zeros(dtype_name, dims))
                case "ones":
                    return NovaBuiltin("ones", lambda a, k: NovaTensor.ones(dtype_name, dims))
                case "randn":
                    def _randn(a, k):
                        size = math.prod(dims) if dims else 1
                        data = [random.gauss(0, 1) for _ in range(size)]
                        return NovaTensor(data=data, dtype=dtype_name, shape=dims)
                    return NovaBuiltin("randn", _randn)
                case "empty":
                    return NovaBuiltin("empty", lambda a, k: NovaTensor(
                        data=[0.0] * (math.prod(dims) if dims else 1),
                        dtype=dtype_name, shape=dims))
                case "rand":
                    def _rand(a, k, _dims=dims, _dtype=dtype_name):
                        size = math.prod(_dims) if _dims else 1
                        data = [random.random() for _ in range(size)]
                        return NovaTensor(data=data, dtype=_dtype, shape=_dims)
                    return NovaBuiltin("rand", _rand)
                case "full":
                    def _full(a, k, _dims=dims, _dtype=dtype_name):
                        fill_val = 0.0
                        if a:
                            fill_val = a[0].value
                        elif "value" in k:
                            fill_val = k["value"].value
                        size = math.prod(_dims) if _dims else 1
                        data = [float(fill_val)] * size
                        return NovaTensor(data=data, dtype=_dtype, shape=_dims)
                    return NovaBuiltin("full", _full)
                case "eye":
                    def _eye(a, k, _dims=dims, _dtype=dtype_name):
                        if len(_dims) != 2 or _dims[0] != _dims[1]:
                            raise NovaError(f"eye() requires square 2D tensor, got {_dims}")
                        n = _dims[0]
                        data = [0.0] * (n * n)
                        for i in range(n):
                            data[i * n + i] = 1.0
                        return NovaTensor(data=data, dtype=_dtype, shape=_dims)
                    return NovaBuiltin("eye", _eye)
                case "from":
                    def _from(a, k, _dtype=dtype_name):
                        if not a or not isinstance(a[0], NovaList):
                            raise NovaError("from() requires a list argument")
                        def _flatten_list(lst):
                            result = []
                            for e in lst.elements:
                                if isinstance(e, NovaList):
                                    result.extend(_flatten_list(e))
                                else:
                                    result.append(float(e.value))
                            return result
                        def _infer_shape(lst):
                            if not isinstance(lst, NovaList):
                                return ()
                            if not lst.elements:
                                return (0,)
                            inner = _infer_shape(lst.elements[0])
                            return (len(lst.elements),) + inner
                        flat = _flatten_list(a[0])
                        shape = _infer_shape(a[0])
                        return NovaTensor(data=flat, dtype=_dtype, shape=shape)
                    return NovaBuiltin("from", _from)

        raise NovaError(f"Attribute '{node.attr}' not found on {type(obj).__name__}")

    # --- Autograd ---

    def _eval_grad(self, node: GradExpr, env: Environment) -> NovaValue:
        func = self._eval(node.func, env)
        order = 1
        if node.order:
            order = self._eval(node.order, env).value

        def grad_fn(args, kwargs):
            """Numerical gradient via finite differences (bootstrap only)."""
            if not args:
                raise NovaError("grad function requires at least one argument")
            x = args[0]
            if not isinstance(x, NovaTensor):
                raise NovaError("grad() currently supports tensor arguments only")

            eps = 1e-5
            grad_data = []
            for i in range(len(x.data)):
                x_plus = NovaTensor(data=x.data[:], dtype=x.dtype, shape=x.shape)
                x_minus = NovaTensor(data=x.data[:], dtype=x.dtype, shape=x.shape)
                x_plus.data[i] += eps
                x_minus.data[i] -= eps
                f_plus = self._call_value(func, [x_plus], {}, env)
                f_minus = self._call_value(func, [x_minus], {}, env)
                vp = f_plus.value if isinstance(f_plus, (NovaInt, NovaFloat)) else f_plus.data[0]
                vm = f_minus.value if isinstance(f_minus, (NovaInt, NovaFloat)) else f_minus.data[0]
                grad_data.append((vp - vm) / (2 * eps))

            return NovaTensor(data=grad_data, dtype=x.dtype, shape=x.shape)

        return NovaBuiltin(f"grad({func.name if hasattr(func, 'name') else '?'})", grad_fn)

    # --- Try (Result unwrap/propagation) ---

    def _eval_try(self, node: TryExpr, env: Environment) -> NovaValue:
        val = self._eval(node.expr, env)
        if isinstance(val, NovaResult):
            if val.is_ok:
                return val.value
            raise ReturnSignal(val)  # propagate Err
        return val

    # --- Tensor helpers ---

    def _eval_tensor_type(self, node: TensorTypeExpr, env: Environment) -> NovaValue:
        return node  # Return as-is; .zeros()/.ones() handle via dot eval

    def _tensor_matmul(self, a: NovaTensor, b: NovaTensor) -> NovaTensor:
        if a.ndim != 2 or b.ndim != 2:
            raise NovaError("@ operator requires 2D tensors")
        m, k1 = a.shape
        k2, n = b.shape
        if k1 != k2:
            raise NovaError(f"Matmul shape mismatch: [{m},{k1}] @ [{k2},{n}]")
        result = [0.0] * (m * n)
        for i in range(m):
            for j in range(n):
                s = 0.0
                for p in range(k1):
                    s += a.data[i * k1 + p] * b.data[p * n + j]
                result[i * n + j] = s
        return NovaTensor(data=result, dtype=a.dtype, shape=(m, n))

    def _tensor_arith(self, op: str, left: NovaValue, right: NovaValue) -> NovaTensor:
        if isinstance(left, NovaTensor) and isinstance(right, (NovaInt, NovaFloat)):
            rv = right.value
            ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
                   "*": lambda a, b: a * b, "/": lambda a, b: a / b,
                   "**": lambda a, b: a ** b}
            if op in ops:
                data = [ops[op](x, rv) for x in left.data]
                return NovaTensor(data=data, dtype=left.dtype, shape=left.shape)
        if isinstance(left, NovaTensor) and isinstance(right, NovaTensor):
            if left.shape != right.shape:
                raise NovaError(f"Tensor shape mismatch: {left.shape} vs {right.shape}")
            ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
                   "*": lambda a, b: a * b, "/": lambda a, b: a / b}
            if op in ops:
                data = [ops[op](a, b) for a, b in zip(left.data, right.data)]
                return NovaTensor(data=data, dtype=left.dtype, shape=left.shape)
        raise NovaError(f"Unsupported tensor operation: {op}")

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _interpolate_string(self, s: str, env: Environment) -> str:
        from ..lexer.lexer import Lexer
        from ..parser.parser import Parser

        result = []
        i = 0
        while i < len(s):
            if s[i] == "{" and i + 1 < len(s) and s[i + 1] != "{":
                # Find matching closing brace (handle nested braces)
                depth = 1
                j = i + 1
                while j < len(s) and depth > 0:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                expr_str = s[i + 1:j - 1]
                try:
                    # Parse and evaluate the expression
                    tokens = Lexer(expr_str).tokenize()
                    parser = Parser(tokens)
                    expr_ast = parser._expression()
                    val = self._eval(expr_ast, env)
                    if isinstance(val, NovaStr):
                        result.append(val.value)
                    else:
                        result.append(self._to_string(val))
                except Exception:
                    result.append(s[i:j])
                i = j
            else:
                result.append(s[i])
                i += 1
        return "".join(result)

    @staticmethod
    def _to_string(val: NovaValue) -> str:
        if isinstance(val, NovaStr):
            return val.value
        if isinstance(val, NovaInt):
            return str(val.value)
        if isinstance(val, NovaFloat):
            return str(val.value)
        if isinstance(val, NovaBool):
            return "true" if val.value else "false"
        if isinstance(val, NovaNone):
            return "none"
        return repr(val)

    @staticmethod
    def _is_scalar(val: NovaValue) -> bool:
        return isinstance(val, (NovaInt, NovaFloat, NovaBool, NovaNone))

    @staticmethod
    def _truthy(val: NovaValue) -> bool:
        if isinstance(val, NovaBool):
            return val.value
        if isinstance(val, NovaNone):
            return False
        if isinstance(val, NovaInt):
            return val.value != 0
        if isinstance(val, NovaFloat):
            return val.value != 0.0
        if isinstance(val, NovaStr):
            return len(val.value) > 0
        if isinstance(val, NovaList):
            return len(val.elements) > 0
        return True

    @staticmethod
    def _values_equal(a: NovaValue, b: NovaValue) -> bool:
        if type(a) != type(b):
            return False
        if isinstance(a, (NovaInt, NovaFloat, NovaStr, NovaBool)):
            return a.value == b.value
        if isinstance(a, NovaNone):
            return True
        return a is b

    def _literal_to_value(self, node: ASTNode) -> NovaValue:
        match node:
            case IntLiteral():
                return NovaInt(node.value)
            case FloatLiteral():
                return NovaFloat(node.value)
            case StringLiteral():
                return NovaStr(node.value)
            case BoolLiteral():
                return NovaBool(node.value)
            case NoneLiteral():
                return NovaNone()
        return NovaNone()

    @staticmethod
    def _to_iterable(val: NovaValue) -> list:
        if isinstance(val, NovaList):
            return val.elements
        if isinstance(val, NovaStr):
            return [NovaStr(c) for c in val.value]
        if isinstance(val, NovaMap):
            return list(val.entries.keys())
        raise NovaError(f"Value of type {type(val).__name__} is not iterable")
