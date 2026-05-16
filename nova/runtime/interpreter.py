"""Nova Interpreter — connects parsed AST to the cognitive runtime.

Executes Nova code by walking the AST and dispatching to:
- Standard operations (arithmetic, functions, control flow)
- Cognitive operations (memory, reasoning, imagination, learning)
"""

import math
import sys

from ..ast_nodes import *
from .mind import Mind
from .memory import WorkingMemory, EpisodicMemory, SemanticMemory, ProceduralMemory


class Environment:
    def __init__(self, parent=None):
        self.vars: dict[str, any] = {}
        self.parent = parent

    def define(self, name, value):
        self.vars[name] = value

    def get(self, name):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"Undefined variable: {name!r}")

    def set(self, name, value):
        if name in self.vars:
            self.vars[name] = value
            return
        if self.parent:
            self.parent.set(name, value)
            return
        raise NameError(f"Undefined variable: {name!r}")

    def has(self, name):
        if name in self.vars:
            return True
        if self.parent:
            return self.parent.has(name)
        return False

    def child(self):
        return Environment(parent=self)


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value

class BreakSignal(Exception):
    pass

class ContinueSignal(Exception):
    pass

class NovaPanic(Exception):
    pass


class NovaValue:
    pass

class NovaInt(NovaValue):
    def __init__(self, v): self.value = v
    def __repr__(self): return str(self.value)

class NovaFloat(NovaValue):
    def __init__(self, v): self.value = v
    def __repr__(self): return str(self.value)

class NovaStr(NovaValue):
    def __init__(self, v): self.value = v
    def __repr__(self): return self.value

class NovaBool(NovaValue):
    def __init__(self, v): self.value = v
    def __repr__(self): return "true" if self.value else "false"

class NovaNone(NovaValue):
    def __repr__(self): return "none"

class NovaList(NovaValue):
    def __init__(self, elements=None): self.elements = elements or []
    def __repr__(self):
        return "[" + ", ".join(repr(e) for e in self.elements) + "]"

class NovaMap(NovaValue):
    def __init__(self, entries=None): self.entries = entries or {}
    def __repr__(self):
        pairs = [f"{k}: {v}" for k, v in self.entries.items()]
        return "{" + ", ".join(pairs) + "}"

class NovaFunction(NovaValue):
    def __init__(self, name, params, body, closure_env):
        self.name = name
        self.params = params
        self.body = body
        self.closure_env = closure_env
    def __repr__(self): return f"<fn {self.name}>"

class NovaBuiltin(NovaValue):
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn
    def __repr__(self): return f"<builtin {self.name}>"

class NovaMind(NovaValue):
    def __init__(self, mind: Mind):
        self.mind = mind
    def __repr__(self): return repr(self.mind)

class NovaResult(NovaValue):
    def __init__(self, value=None, error=None, is_ok=True):
        self.value = value
        self.error = error
        self.is_ok = is_ok
    @classmethod
    def ok(cls, v): return cls(value=v, is_ok=True)
    @classmethod
    def err(cls, e): return cls(error=e, is_ok=False)
    def unwrap(self):
        if self.is_ok: return self.value
        raise NovaPanic(f"unwrap on Err: {self.error}")
    def __repr__(self):
        return f"Ok({self.value})" if self.is_ok else f"Err({self.error})"


class Interpreter:
    def __init__(self):
        self.global_env = Environment()
        self.minds: dict[str, Mind] = {}
        self.active_mind: Mind | None = None
        self._register_builtins()

    def _register_builtins(self):
        env = self.global_env
        env.define("print", NovaBuiltin("print", self._builtin_print))
        env.define("len", NovaBuiltin("len", self._builtin_len))
        env.define("range", NovaBuiltin("range", self._builtin_range))
        env.define("type_of", NovaBuiltin("type_of", self._builtin_type_of))
        env.define("abs", NovaBuiltin("abs", self._builtin_abs))
        env.define("min", NovaBuiltin("min", self._builtin_min))
        env.define("max", NovaBuiltin("max", self._builtin_max))
        env.define("sum", NovaBuiltin("sum", self._builtin_sum))
        env.define("str", NovaBuiltin("str", self._builtin_str))
        env.define("int", NovaBuiltin("int", self._builtin_int))
        env.define("float", NovaBuiltin("float", self._builtin_float))
        env.define("Ok", NovaBuiltin("Ok", lambda a, k: NovaResult.ok(a[0])))
        env.define("Err", NovaBuiltin("Err", lambda a, k: NovaResult.err(a[0])))
        env.define("sqrt", NovaBuiltin("sqrt", lambda a, k: NovaFloat(math.sqrt(self._num(a[0])))))
        env.define("assert_eq", NovaBuiltin("assert_eq", self._builtin_assert_eq))
        env.define("assert_true", NovaBuiltin("assert_true", self._builtin_assert_true))
        env.define("assert_false", NovaBuiltin("assert_false", self._builtin_assert_false))

    # -- Builtins --

    def _builtin_print(self, args, kwargs):
        parts = [self._to_string(a) for a in args]
        print(" ".join(parts))
        return NovaNone()

    @staticmethod
    def _builtin_len(args, kwargs):
        v = args[0]
        if isinstance(v, NovaList): return NovaInt(len(v.elements))
        if isinstance(v, NovaStr): return NovaInt(len(v.value))
        if isinstance(v, NovaMap): return NovaInt(len(v.entries))
        return NovaInt(0)

    @staticmethod
    def _builtin_range(args, kwargs):
        if len(args) == 1:
            return NovaList([NovaInt(i) for i in range(int(args[0].value))])
        elif len(args) == 2:
            return NovaList([NovaInt(i) for i in range(int(args[0].value), int(args[1].value))])
        return NovaList([NovaInt(i) for i in range(int(args[0].value), int(args[1].value), int(args[2].value))])

    @staticmethod
    def _builtin_type_of(args, kwargs):
        v = args[0]
        names = {
            NovaInt: "int", NovaFloat: "float", NovaStr: "str",
            NovaBool: "bool", NovaNone: "none", NovaList: "list",
            NovaMap: "map", NovaFunction: "fn", NovaBuiltin: "fn",
            NovaMind: "mind", NovaResult: "result",
        }
        return NovaStr(names.get(type(v), "unknown"))

    @staticmethod
    def _builtin_abs(args, kwargs):
        v = args[0]
        if isinstance(v, NovaInt): return NovaInt(abs(v.value))
        if isinstance(v, NovaFloat): return NovaFloat(abs(v.value))
        return v

    @staticmethod
    def _builtin_min(args, kwargs):
        if len(args) == 1 and isinstance(args[0], NovaList):
            vals = [e.value for e in args[0].elements]
        else:
            vals = [a.value for a in args]
        m = min(vals)
        return NovaFloat(m) if isinstance(m, float) else NovaInt(m)

    @staticmethod
    def _builtin_max(args, kwargs):
        if len(args) == 1 and isinstance(args[0], NovaList):
            vals = [e.value for e in args[0].elements]
        else:
            vals = [a.value for a in args]
        m = max(vals)
        return NovaFloat(m) if isinstance(m, float) else NovaInt(m)

    @staticmethod
    def _builtin_sum(args, kwargs):
        if isinstance(args[0], NovaList):
            vals = [e.value for e in args[0].elements]
        else:
            vals = [a.value for a in args]
        return NovaInt(sum(vals)) if all(isinstance(v, int) for v in vals) else NovaFloat(sum(vals))

    @staticmethod
    def _builtin_str(args, kwargs):
        return NovaStr(str(args[0].value if hasattr(args[0], 'value') else args[0]))

    @staticmethod
    def _builtin_int(args, kwargs):
        return NovaInt(int(args[0].value))

    @staticmethod
    def _builtin_float(args, kwargs):
        return NovaFloat(float(args[0].value))

    @staticmethod
    def _builtin_assert_eq(args, kwargs):
        a, b = args[0], args[1]
        av = a.value if hasattr(a, 'value') else a
        bv = b.value if hasattr(b, 'value') else b
        if av != bv:
            raise NovaPanic(f"assert_eq failed: {a!r} != {b!r}")
        return NovaNone()

    @staticmethod
    def _builtin_assert_true(args, kwargs):
        v = args[0]
        if isinstance(v, NovaBool) and not v.value:
            raise NovaPanic(f"assert_true failed")
        if isinstance(v, NovaNone):
            raise NovaPanic(f"assert_true failed: none")
        return NovaNone()

    @staticmethod
    def _builtin_assert_false(args, kwargs):
        v = args[0]
        if isinstance(v, NovaBool) and v.value:
            raise NovaPanic(f"assert_false failed")
        return NovaNone()

    # -- Entry point --

    def execute(self, program: Program):
        result = NovaNone()
        for node in program.body:
            result = self._exec(node, self.global_env)
        return result

    # -- Statement dispatch --

    def _exec(self, node: Node, env: Environment):
        if isinstance(node, MindDecl):
            return self._exec_mind(node, env)
        if isinstance(node, FnDecl):
            return self._exec_fn_decl(node, env)
        if isinstance(node, LetDecl):
            return self._exec_let(node, env)
        if isinstance(node, AssignStmt):
            return self._exec_assign(node, env)
        if isinstance(node, ReturnStmt):
            val = self._eval(node.value, env) if node.value else NovaNone()
            raise ReturnSignal(val)
        if isinstance(node, IfStmt):
            return self._exec_if(node, env)
        if isinstance(node, ForStmt):
            return self._exec_for(node, env)
        if isinstance(node, WhileStmt):
            return self._exec_while(node, env)
        if isinstance(node, MatchStmt):
            return self._exec_match(node, env)
        if isinstance(node, BreakStmt):
            raise BreakSignal()
        if isinstance(node, ContinueStmt):
            raise ContinueSignal()
        if isinstance(node, ExprStmt):
            return self._eval(node.expr, env)
        return self._eval(node, env)

    # -- Mind execution --

    def _exec_mind(self, node: MindDecl, env: Environment):
        config = {}
        for item in node.body:
            if isinstance(item, MemoryDecl):
                cfg = {}
                for k, v in item.config.items():
                    cfg[k] = self._eval(v, env) if isinstance(v, Node) else v
                if item.kind == "working":
                    cap = cfg.get("capacity")
                    config["working_capacity"] = cap.value if isinstance(cap, NovaInt) else 7
                elif item.kind == "episodic":
                    mx = cfg.get("max_episodes")
                    config["max_episodes"] = mx.value if isinstance(mx, NovaInt) else 100000

        mind = Mind(name=node.name, config=config)
        self.minds[node.name] = mind
        self.active_mind = mind

        mind_val = NovaMind(mind)
        env.define(node.name, mind_val)

        mind_env = env.child()
        mind_env.define("self", mind_val)
        mind_env.define("working", self._wrap_memory(mind.working))
        mind_env.define("episodic", self._wrap_memory(mind.episodic))
        mind_env.define("semantic", self._wrap_memory(mind.semantic))
        mind_env.define("procedural", self._wrap_memory(mind.procedural))

        for item in node.body:
            if isinstance(item, MemoryDecl):
                continue
            elif isinstance(item, PerceiveDecl):
                fn = NovaFunction("perceive", item.params, item.body, mind_env)
                mind.perceive_fn = fn
            elif isinstance(item, ThinkDecl):
                fn = NovaFunction("think", item.params, item.body, mind_env)
                mind.think_fn = fn
            elif isinstance(item, ImagineDecl):
                fn = NovaFunction("imagine", item.params, item.body, mind_env)
                mind.imagine_fn = fn
            elif isinstance(item, LearnDecl):
                fn = NovaFunction("learn", item.params, item.body, mind_env)
                mind.learn_fn = fn
            elif isinstance(item, ActDecl):
                fn = NovaFunction("act", item.params, item.body, mind_env)
                mind.act_fn = fn
            elif isinstance(item, OnEventDecl):
                fn = NovaFunction(f"on_{item.event_name}", item.params, item.body, mind_env)
                if item.event_name == "idle":
                    mind.idle_fn = fn
                else:
                    mind.on(item.event_name, lambda data, f=fn: self._call_fn(f, [self._wrap(data)], {}, mind_env))
            elif isinstance(item, ReflexDecl):
                mind.procedural.add_reflex(item.trigger, item.action)
            elif isinstance(item, FnDecl):
                fn = NovaFunction(item.name, item.params, item.body, mind_env)
                mind_env.define(item.name, fn)
            elif isinstance(item, GoalExpr):
                desc = self._eval(item.description, mind_env) if item.description else NovaNone()
                priority = self._eval(item.priority, mind_env).value if item.priority else 0.5
                mind.add_goal(self._to_string(desc), priority)

        return mind_val

    def _wrap_memory(self, mem):
        if isinstance(mem, WorkingMemory):
            return NovaMap({
                NovaStr("type"): NovaStr("working"),
                NovaStr("capacity"): NovaInt(mem.capacity),
                NovaStr("size"): NovaInt(len(mem)),
            })
        if isinstance(mem, EpisodicMemory):
            return NovaMap({
                NovaStr("type"): NovaStr("episodic"),
                NovaStr("size"): NovaInt(len(mem)),
            })
        if isinstance(mem, SemanticMemory):
            return NovaMap({
                NovaStr("type"): NovaStr("semantic"),
                NovaStr("concepts"): NovaInt(mem.concepts_count()),
            })
        if isinstance(mem, ProceduralMemory):
            return NovaMap({
                NovaStr("type"): NovaStr("procedural"),
                NovaStr("procedures"): NovaInt(len(mem.procedures)),
            })
        return NovaNone()

    # -- Declarations --

    def _exec_fn_decl(self, node: FnDecl, env: Environment):
        fn = NovaFunction(node.name, node.params, node.body, env)
        env.define(node.name, fn)
        return fn

    def _exec_let(self, node: LetDecl, env: Environment):
        val = self._eval(node.value, env) if node.value else NovaNone()
        env.define(node.name, val)
        return val

    def _exec_assign(self, node: AssignStmt, env: Environment):
        val = self._eval(node.value, env)
        if node.op != "=":
            old = self._eval(node.target, env)
            val = self._apply_binop(node.op[0], old, val)
        if isinstance(node.target, Ident):
            env.set(node.target.name, val)
        elif isinstance(node.target, DotExpr):
            obj = self._eval(node.target.obj, env)
            if isinstance(obj, NovaMap):
                obj.entries[NovaStr(node.target.attr)] = val
        elif isinstance(node.target, IndexExpr):
            obj = self._eval(node.target.obj, env)
            idx = self._eval(node.target.index, env)
            if isinstance(obj, NovaList):
                obj.elements[idx.value] = val
        return val

    # -- Control flow --

    def _exec_if(self, node: IfStmt, env: Environment):
        if self._truthy(self._eval(node.condition, env)):
            return self._exec_block(node.then_body, env)
        for cond, body in (node.elif_clauses or []):
            if self._truthy(self._eval(cond, env)):
                return self._exec_block(body, env)
        if node.else_body:
            return self._exec_block(node.else_body, env)
        return NovaNone()

    def _exec_for(self, node: ForStmt, env: Environment):
        iterable = self._eval(node.iterable, env)
        items = self._to_iterable(iterable)
        result = NovaNone()
        for item in items:
            child = env.child()
            child.define(node.var, item)
            try:
                result = self._exec_block(node.body, child)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        return result

    def _exec_while(self, node: WhileStmt, env: Environment):
        result = NovaNone()
        while self._truthy(self._eval(node.condition, env)):
            try:
                result = self._exec_block(node.body, env)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        return result

    def _exec_match(self, node: MatchStmt, env: Environment):
        subject = self._eval(node.subject, env)
        for arm in node.arms:
            bindings = self._match_pattern(arm.pattern, subject)
            if bindings is not None:
                child = env.child()
                for k, v in bindings.items():
                    child.define(k, v)
                if isinstance(arm.body, list):
                    return self._exec_block(arm.body, child)
                return self._eval(arm.body, child)
        return NovaNone()

    def _match_pattern(self, pattern, value):
        if isinstance(pattern, Ident) and pattern.name == "_":
            return {}
        if isinstance(pattern, Ident):
            return {pattern.name: value}
        if isinstance(pattern, IntLit):
            if isinstance(value, NovaInt) and value.value == pattern.value:
                return {}
        if isinstance(pattern, StrLit):
            parts = pattern.parts if hasattr(pattern, 'parts') else [pattern.value]
            s = parts[0] if parts and isinstance(parts[0], str) else ""
            if isinstance(value, NovaStr) and value.value == s:
                return {}
        if isinstance(pattern, BoolLit):
            if isinstance(value, NovaBool) and value.value == pattern.value:
                return {}
        if isinstance(pattern, CallExpr):
            name = pattern.callee.name if isinstance(pattern.callee, Ident) else ""
            if name == "Ok" and isinstance(value, NovaResult) and value.is_ok:
                if pattern.args:
                    return self._match_pattern(pattern.args[0], value.value)
                return {}
            if name == "Err" and isinstance(value, NovaResult) and not value.is_ok:
                if pattern.args:
                    return self._match_pattern(pattern.args[0], value.error)
                return {}
        return None

    def _exec_block(self, stmts, env):
        child = env.child()
        result = NovaNone()
        for stmt in stmts:
            result = self._exec(stmt, child)
        return result

    # -- Expression evaluation --

    def _eval(self, node: Node, env: Environment):
        if node is None:
            return NovaNone()
        if isinstance(node, IntLit):
            return NovaInt(node.value)
        if isinstance(node, FloatLit):
            return NovaFloat(node.value)
        if isinstance(node, StrLit):
            return self._eval_string(node, env)
        if isinstance(node, BoolLit):
            return NovaBool(node.value)
        if isinstance(node, NoneLit):
            return NovaNone()
        if isinstance(node, Ident):
            return env.get(node.name)
        if isinstance(node, ListLit):
            return NovaList([self._eval(e, env) for e in node.elements])
        if isinstance(node, MapLit):
            entries = {}
            for k, v in zip(node.keys, node.values):
                entries[self._eval(k, env)] = self._eval(v, env)
            return NovaMap(entries)
        if isinstance(node, BinOp):
            return self._eval_binop(node, env)
        if isinstance(node, UnaryOp):
            return self._eval_unary(node, env)
        if isinstance(node, CallExpr):
            return self._eval_call(node, env)
        if isinstance(node, IndexExpr):
            obj = self._eval(node.obj, env)
            idx = self._eval(node.index, env)
            if isinstance(obj, NovaList):
                return obj.elements[idx.value]
            if isinstance(obj, NovaMap):
                return obj.entries.get(idx, NovaNone())
            if isinstance(obj, NovaStr):
                return NovaStr(obj.value[idx.value])
            return NovaNone()
        if isinstance(node, DotExpr):
            return self._eval_dot(node, env)
        if isinstance(node, PipeExpr):
            left = self._eval(node.left, env)
            fn = self._eval(node.right, env)
            return self._call_fn(fn, [left], {}, env)
        if isinstance(node, LambdaExpr):
            body = node.body if isinstance(node.body, list) else [node.body]
            return NovaFunction("<lambda>", node.params, body, env)
        if isinstance(node, FnDecl):
            fn = NovaFunction(node.name, node.params, node.body, env)
            if node.name and node.name != "<anon>":
                env.define(node.name, fn)
            return fn
        if isinstance(node, RecallExpr):
            return self._eval_recall(node, env)
        if isinstance(node, StoreExpr):
            return self._eval_store(node, env)
        if isinstance(node, BelieveExpr):
            return self._eval_believe(node, env)
        if isinstance(node, SimulateExpr):
            return self._eval_simulate(node, env)
        if isinstance(node, PredictExpr):
            return self._eval_predict(node, env)
        if isinstance(node, ConsequenceExpr):
            return self._eval_predict(node, env)
        if isinstance(node, IfExpr):
            if self._truthy(self._eval(node.condition, env)):
                return self._eval(node.then_expr, env)
            return self._eval(node.else_expr, env) if node.else_expr else NovaNone()
        return NovaNone()

    def _eval_string(self, node: StrLit, env: Environment):
        parts = node.parts if hasattr(node, 'parts') else [node.value if hasattr(node, 'value') else ""]
        result = []
        for part in parts:
            if isinstance(part, str):
                result.append(part)
            elif isinstance(part, Node):
                val = self._eval(part, env)
                result.append(self._to_string(val))
        return NovaStr("".join(result))

    def _eval_binop(self, node: BinOp, env: Environment):
        left = self._eval(node.left, env)
        if node.op == "and":
            return left if not self._truthy(left) else self._eval(node.right, env)
        if node.op == "or":
            return left if self._truthy(left) else self._eval(node.right, env)
        right = self._eval(node.right, env)
        return self._apply_binop(node.op, left, right)

    def _apply_binop(self, op, left, right):
        if isinstance(left, NovaStr) and op == "+":
            return NovaStr(left.value + self._to_string(right))
        if isinstance(left, NovaList) and op == "+":
            if isinstance(right, NovaList):
                return NovaList(left.elements + right.elements)
        lv = left.value if hasattr(left, 'value') else 0
        rv = right.value if hasattr(right, 'value') else 0
        use_float = isinstance(left, NovaFloat) or isinstance(right, NovaFloat)
        match op:
            case "+": r = lv + rv
            case "-": r = lv - rv
            case "*":
                if isinstance(left, NovaStr):
                    return NovaStr(left.value * int(rv))
                r = lv * rv
            case "/": r = lv / rv; use_float = True
            case "//": r = lv // rv
            case "%": r = lv % rv
            case "**": r = lv ** rv
            case "==": return NovaBool(lv == rv)
            case "!=": return NovaBool(lv != rv)
            case "<": return NovaBool(lv < rv)
            case ">": return NovaBool(lv > rv)
            case "<=": return NovaBool(lv <= rv)
            case ">=": return NovaBool(lv >= rv)
            case "&": r = lv & rv
            case "|": r = lv | rv
            case "^": r = lv ^ rv
            case "<<": r = lv << rv
            case ">>": r = lv >> rv
            case _: r = 0
        return NovaFloat(float(r)) if use_float else NovaInt(int(r))

    def _eval_unary(self, node: UnaryOp, env: Environment):
        val = self._eval(node.operand, env)
        if node.op == "-":
            if isinstance(val, NovaInt): return NovaInt(-val.value)
            if isinstance(val, NovaFloat): return NovaFloat(-val.value)
        if node.op == "not":
            return NovaBool(not self._truthy(val))
        if node.op == "~":
            if isinstance(val, NovaInt): return NovaInt(~val.value)
        return val

    def _eval_call(self, node: CallExpr, env: Environment):
        callee = self._eval(node.callee, env)
        args = [self._eval(a, env) for a in node.args]
        kwargs = {}
        if hasattr(node, 'kwargs') and node.kwargs:
            for k, v in node.kwargs.items():
                kwargs[k] = self._eval(v, env)
        return self._call_fn(callee, args, kwargs, env)

    def _call_fn(self, callee, args, kwargs, env):
        if isinstance(callee, NovaBuiltin):
            return callee.fn(args, kwargs)
        if isinstance(callee, NovaFunction):
            child = callee.closure_env.child()
            for i, param in enumerate(callee.params):
                name = param.name if isinstance(param, Param) else param
                if i < len(args):
                    child.define(name, args[i])
                elif name in kwargs:
                    child.define(name, kwargs[name])
                elif isinstance(param, Param) and param.default:
                    child.define(name, self._eval(param.default, callee.closure_env))
                else:
                    child.define(name, NovaNone())
            try:
                last = NovaNone()
                for stmt in callee.body:
                    last = self._exec(stmt, child)
                return last
            except ReturnSignal as ret:
                return ret.value
        return NovaNone()

    def _eval_dot(self, node: DotExpr, env: Environment):
        obj = self._eval(node.obj, env)
        attr = node.attr

        if isinstance(obj, NovaMind):
            mind = obj.mind
            match attr:
                case "perceive":
                    return NovaBuiltin("perceive", lambda a, k: self._mind_perceive(mind, a, k, env))
                case "think":
                    return NovaBuiltin("think", lambda a, k: self._mind_think(mind, a, k, env))
                case "imagine":
                    return NovaBuiltin("imagine", lambda a, k: self._mind_imagine(mind, a, k, env))
                case "learn_from":
                    return NovaBuiltin("learn_from", lambda a, k: self._mind_learn(mind, a, k))
                case "act":
                    return NovaBuiltin("act", lambda a, k: self._mind_act(mind, a, k, env))
                case "believe":
                    return NovaBuiltin("believe", lambda a, k: self._wrap(
                        mind.believe(self._to_string(a[0]),
                                     a[1].value if len(a) > 1 else 0.5)))
                case "know":
                    return NovaBuiltin("know", lambda a, k: self._wrap(
                        mind.know(self._to_string(a[0]), self._to_string(a[1]),
                                  self._to_string(a[2]))))
                case "recall":
                    return NovaBuiltin("recall", lambda a, k: self._wrap(
                        mind.recall(self._to_string(a[0]),
                                    limit=k.get("limit", NovaInt(10)).value)))
                case "status":
                    return NovaBuiltin("status", lambda a, k: self._wrap(mind.status()))
                case "goals":
                    return NovaList([self._wrap(g) for g in mind.goals])
                case "idle_cycle":
                    return NovaBuiltin("idle_cycle", lambda a, k: self._wrap(mind.idle_cycle()))
                case "add_goal":
                    return NovaBuiltin("add_goal", lambda a, k: self._wrap(
                        mind.add_goal(self._to_string(a[0]),
                                      a[1].value if len(a) > 1 else 0.5)))
                case "name":
                    return NovaStr(mind.name)
                case "state":
                    return NovaStr(mind.state)

        if isinstance(obj, NovaMap):
            key = NovaStr(attr)
            if key in obj.entries:
                return obj.entries[key]
            for k, v in obj.entries.items():
                if isinstance(k, NovaStr) and k.value == attr:
                    return v
            return NovaNone()

        if isinstance(obj, NovaList):
            match attr:
                case "length": return NovaInt(len(obj.elements))
                case "push": return NovaBuiltin("push", lambda a, k: (obj.elements.append(a[0]), NovaNone())[-1])
                case "pop": return NovaBuiltin("pop", lambda a, k: obj.elements.pop() if obj.elements else NovaNone())
                case "map": return NovaBuiltin("map", lambda a, k: NovaList([self._call_fn(a[0], [e], {}, env) for e in obj.elements]))
                case "filter": return NovaBuiltin("filter", lambda a, k: NovaList([e for e in obj.elements if self._truthy(self._call_fn(a[0], [e], {}, env))]))
                case "contains": return NovaBuiltin("contains", lambda a, k: NovaBool(any(self._eq(a[0], e) for e in obj.elements)))
                case "sort": return NovaBuiltin("sort", lambda a, k: NovaList(sorted(obj.elements, key=lambda e: e.value)))

        if isinstance(obj, NovaStr):
            match attr:
                case "length": return NovaInt(len(obj.value))
                case "upper": return NovaBuiltin("upper", lambda a, k: NovaStr(obj.value.upper()))
                case "lower": return NovaBuiltin("lower", lambda a, k: NovaStr(obj.value.lower()))
                case "split": return NovaBuiltin("split", lambda a, k: NovaList([NovaStr(s) for s in obj.value.split(a[0].value if a else " ")]))
                case "contains": return NovaBuiltin("contains", lambda a, k: NovaBool(a[0].value in obj.value))
                case "replace": return NovaBuiltin("replace", lambda a, k: NovaStr(obj.value.replace(a[0].value, a[1].value)))
                case "strip": return NovaBuiltin("strip", lambda a, k: NovaStr(obj.value.strip()))

        if isinstance(obj, NovaResult):
            match attr:
                case "is_ok": return NovaBool(obj.is_ok)
                case "is_err": return NovaBool(not obj.is_ok)
                case "unwrap": return NovaBuiltin("unwrap", lambda a, k: obj.unwrap())
                case "value": return obj.value if obj.is_ok else NovaNone()
                case "error": return obj.error if not obj.is_ok else NovaNone()

        return NovaNone()

    # -- Cognitive expressions --

    def _eval_recall(self, node: RecallExpr, env: Environment):
        if not self.active_mind:
            return NovaNone()
        query = self._to_string(self._eval(node.query, env)) if node.query else ""
        source = self._to_string(self._eval(node.source, env)) if node.source else "all"
        limit = self._eval(node.limit, env).value if node.limit else 10
        results = self.active_mind.recall(query, source=source, limit=limit)
        return self._wrap(results)

    def _eval_store(self, node: StoreExpr, env: Environment):
        if not self.active_mind:
            return NovaNone()
        value = self._eval(node.value, env)
        target = self._to_string(self._eval(node.target, env)) if node.target else "episodic"
        content = self._to_string(value)
        if target == "working":
            self.active_mind.working.hold(content)
        elif target == "episodic":
            self.active_mind.episodic.store(content)
        elif target == "semantic":
            self.active_mind.semantic.store_fact(content, "is", content)
        return NovaNone()

    def _eval_believe(self, node: BelieveExpr, env: Environment):
        if not self.active_mind:
            return NovaNone()
        prop = self._to_string(self._eval(node.proposition, env)) if node.proposition else ""
        conf = self._eval(node.confidence, env).value if node.confidence else 0.5
        result = self.active_mind.believe(prop, conf)
        return self._wrap(result)

    def _eval_simulate(self, node: SimulateExpr, env: Environment):
        if not self.active_mind:
            return NovaNone()
        scenario = self._to_string(self._eval(node.scenario, env)) if node.scenario else ""
        steps = self._eval(node.steps, env).value if node.steps else 5
        result = self.active_mind.imagine(scenario, steps)
        return self._wrap(result)

    def _eval_predict(self, node, env: Environment):
        if not self.active_mind:
            return NovaNone()
        action = self._to_string(self._eval(node.action, env)) if node.action else ""
        result = self.active_mind.reasoning.predict_consequence(action)
        return self._wrap(result)

    # -- Mind method dispatch --

    def _mind_perceive(self, mind, args, kwargs, env):
        input_val = self._to_string(args[0]) if args else ""
        if mind.perceive_fn:
            return self._call_fn(mind.perceive_fn, args, kwargs, env)
        result = mind.perceive(input_val)
        return self._wrap(result)

    def _mind_think(self, mind, args, kwargs, env):
        goal = self._to_string(args[0]) if args else ""
        if mind.think_fn:
            return self._call_fn(mind.think_fn, args, kwargs, env)
        result = mind.think(goal)
        return self._wrap(result)

    def _mind_imagine(self, mind, args, kwargs, env):
        scenario = self._to_string(args[0]) if args else ""
        steps = kwargs.get("steps", NovaInt(5)).value if kwargs else 5
        if mind.imagine_fn:
            return self._call_fn(mind.imagine_fn, args, kwargs, env)
        result = mind.imagine(scenario, steps)
        return self._wrap(result)

    def _mind_learn(self, mind, args, kwargs):
        if args:
            event = self._to_native(args[0])
            result = mind.learn_from(event)
            return self._wrap(result)
        return NovaNone()

    def _mind_act(self, mind, args, kwargs, env):
        action = self._to_string(args[0]) if args else ""
        if mind.act_fn:
            return self._call_fn(mind.act_fn, args, kwargs, env)
        result = mind.act(action)
        return self._wrap(result)

    # -- Helpers --

    def _wrap(self, value):
        if value is None:
            return NovaNone()
        if isinstance(value, NovaValue):
            return value
        if isinstance(value, bool):
            return NovaBool(value)
        if isinstance(value, int):
            return NovaInt(value)
        if isinstance(value, float):
            return NovaFloat(value)
        if isinstance(value, str):
            return NovaStr(value)
        if isinstance(value, list):
            return NovaList([self._wrap(v) for v in value])
        if isinstance(value, dict):
            entries = {}
            for k, v in value.items():
                entries[self._wrap(k)] = self._wrap(v)
            return NovaMap(entries)
        return NovaStr(str(value))

    def _to_native(self, val):
        if isinstance(val, NovaInt): return val.value
        if isinstance(val, NovaFloat): return val.value
        if isinstance(val, NovaStr): return val.value
        if isinstance(val, NovaBool): return val.value
        if isinstance(val, NovaNone): return None
        if isinstance(val, NovaList): return [self._to_native(e) for e in val.elements]
        if isinstance(val, NovaMap):
            return {self._to_native(k): self._to_native(v) for k, v in val.entries.items()}
        return str(val)

    @staticmethod
    def _num(val):
        if isinstance(val, (NovaInt, NovaFloat)): return val.value
        return 0

    def _to_string(self, val):
        if isinstance(val, NovaStr): return val.value
        if isinstance(val, NovaInt): return str(val.value)
        if isinstance(val, NovaFloat): return str(val.value)
        if isinstance(val, NovaBool): return "true" if val.value else "false"
        if isinstance(val, NovaNone): return "none"
        return repr(val)

    def _truthy(self, val):
        if isinstance(val, NovaBool): return val.value
        if isinstance(val, NovaNone): return False
        if isinstance(val, NovaInt): return val.value != 0
        if isinstance(val, NovaFloat): return val.value != 0.0
        if isinstance(val, NovaStr): return len(val.value) > 0
        if isinstance(val, NovaList): return len(val.elements) > 0
        return True

    def _eq(self, a, b):
        if type(a) != type(b): return False
        if isinstance(a, (NovaInt, NovaFloat, NovaStr, NovaBool)):
            return a.value == b.value
        return a is b

    @staticmethod
    def _to_iterable(val):
        if isinstance(val, NovaList): return val.elements
        if isinstance(val, NovaStr): return [NovaStr(c) for c in val.value]
        return []
