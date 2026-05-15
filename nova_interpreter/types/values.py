"""Nova runtime value types — full language with agents, arenas, result types."""

from typing import Any, Callable
import math


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class NovaValue:
    pass


# ---------------------------------------------------------------------------
# Scalars (copied, not owned)
# ---------------------------------------------------------------------------

class NovaInt(NovaValue):
    __slots__ = ("value",)
    def __init__(self, value: int):
        self.value = value
    def __repr__(self):
        return str(self.value)
    def __eq__(self, other):
        return isinstance(other, NovaInt) and self.value == other.value
    def __hash__(self):
        return hash(self.value)


class NovaFloat(NovaValue):
    __slots__ = ("value",)
    def __init__(self, value: float):
        self.value = value
    def __repr__(self):
        return str(self.value)


class NovaStr(NovaValue):
    __slots__ = ("value",)
    def __init__(self, value: str):
        self.value = value
    def __repr__(self):
        return f'"{self.value}"'
    def __eq__(self, other):
        return isinstance(other, NovaStr) and self.value == other.value
    def __hash__(self):
        return hash(self.value)


class NovaBool(NovaValue):
    __slots__ = ("value",)
    def __init__(self, value: bool):
        self.value = value
    def __repr__(self):
        return "true" if self.value else "false"


class NovaNone(NovaValue):
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self):
        return "none"


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

class NovaList(NovaValue):
    __slots__ = ("elements",)
    def __init__(self, elements: list[NovaValue]):
        self.elements = elements
    def __repr__(self):
        return f"[{', '.join(repr(e) for e in self.elements)}]"


class NovaMap(NovaValue):
    __slots__ = ("entries",)
    def __init__(self, entries: dict):
        self.entries = entries
    def __repr__(self):
        pairs = ", ".join(f"{k!r}: {v!r}" for k, v in self.entries.items())
        return f"{{{pairs}}}"


# ---------------------------------------------------------------------------
# Tensor
# ---------------------------------------------------------------------------

class NovaTensor(NovaValue):
    def __init__(self, data=None, dtype: str = "f32", shape: tuple = ()):
        self.data = data or []
        self.dtype = dtype
        self.shape = shape
        self.requires_grad = False
        self.grad = None
        self.device = "cpu"

    @classmethod
    def zeros(cls, dtype: str, shape: tuple):
        size = math.prod(shape) if shape else 1
        return cls(data=[0.0] * size, dtype=dtype, shape=shape)

    @classmethod
    def ones(cls, dtype: str, shape: tuple):
        size = math.prod(shape) if shape else 1
        return cls(data=[1.0] * size, dtype=dtype, shape=shape)

    @classmethod
    def from_list(cls, data: list, dtype: str = "f64"):
        flat = []
        shape = cls._infer_shape(data, flat)
        return cls(data=flat, dtype=dtype, shape=shape)

    @staticmethod
    def _infer_shape(data, flat: list) -> tuple:
        if not isinstance(data, list):
            flat.append(float(data))
            return ()
        if len(data) == 0:
            return (0,)
        inner_shape = NovaTensor._infer_shape(data[0], flat)
        for item in data[1:]:
            NovaTensor._infer_shape(item, flat)
        return (len(data),) + inner_shape

    @property
    def ndim(self):
        return len(self.shape)

    @property
    def size(self):
        return math.prod(self.shape) if self.shape else 1

    def sum(self):
        return NovaFloat(sum(self.data))

    def __repr__(self):
        dims = ", ".join(str(d) for d in self.shape)
        return f"tensor[{self.dtype}, {dims}]({self.size} elements)"


# ---------------------------------------------------------------------------
# Frame (tabular data)
# ---------------------------------------------------------------------------

class NovaFrame(NovaValue):
    def __init__(self, columns: dict[str, list] | None = None):
        self.columns = columns or {}

    @property
    def shape(self):
        if not self.columns:
            return (0, 0)
        nrows = len(next(iter(self.columns.values())))
        return (nrows, len(self.columns))

    def __repr__(self):
        return f"frame({list(self.columns.keys())}, shape={self.shape})"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

class NovaFunction(NovaValue):
    def __init__(self, name: str, params, body, closure_env=None, is_async=False):
        self.name = name
        self.params = params
        self.body = body
        self.closure_env = closure_env
        self.is_async = is_async

    def __repr__(self):
        return f"<fn {self.name}>"


class NovaBuiltin(NovaValue):
    def __init__(self, name: str, fn: Callable):
        self.name = name
        self.fn = fn

    def __repr__(self):
        return f"<builtin {self.name}>"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class NovaModelClass(NovaValue):
    def __init__(self, name: str, init_params, layer_defs, forward_ast, method_asts):
        self.name = name
        self.init_params = init_params
        self.layer_defs = layer_defs
        self.forward_ast = forward_ast
        self.method_asts = method_asts

    def __repr__(self):
        return f"<model class {self.name}>"


class NovaModel(NovaValue):
    def __init__(self, name: str, layers: dict, forward_fn: NovaFunction,
                 methods: dict | None = None, init_params: dict | None = None):
        self.name = name
        self.layers = layers
        self.forward_fn = forward_fn
        self.methods = methods or {}
        self.init_params = init_params or {}

    def parameters(self) -> NovaList:
        params = []
        for v in self.layers.values():
            if isinstance(v, NovaTensor):
                params.append(v)
            elif isinstance(v, NovaModel):
                params.extend(v.parameters().elements)
        return NovaList(params)

    def __repr__(self):
        return f"<model {self.name}>"


# ---------------------------------------------------------------------------
# Agent (first-class)
# ---------------------------------------------------------------------------

class NovaAgentClass(NovaValue):
    def __init__(self, name: str, init_params, tools_defs, memory_defs,
                 plan_ast, act_ast, method_asts):
        self.name = name
        self.init_params = init_params
        self.tools_defs = tools_defs
        self.memory_defs = memory_defs
        self.plan_ast = plan_ast
        self.act_ast = act_ast
        self.method_asts = method_asts

    def __repr__(self):
        return f"<agent class {self.name}>"


class NovaAgent(NovaValue):
    def __init__(self, name: str, tools: dict, memory: dict,
                 plan_fn: NovaFunction | None, act_fn: NovaFunction | None,
                 methods: dict | None = None):
        self.name = name
        self.tools = tools
        self.memory = memory
        self.plan_fn = plan_fn
        self.act_fn = act_fn
        self.methods = methods or {}

    def __repr__(self):
        return f"<agent {self.name}>"


# ---------------------------------------------------------------------------
# Result[T, E]
# ---------------------------------------------------------------------------

class NovaResult(NovaValue):
    __slots__ = ("is_ok", "value", "error")

    def __init__(self, is_ok: bool, value: NovaValue | None = None,
                 error: NovaValue | None = None):
        self.is_ok = is_ok
        self.value = value
        self.error = error

    @classmethod
    def ok(cls, value: NovaValue):
        return cls(is_ok=True, value=value)

    @classmethod
    def err(cls, error: NovaValue):
        return cls(is_ok=False, error=error)

    def unwrap(self):
        if self.is_ok:
            return self.value
        raise NovaPanic(f"Called unwrap() on Err: {self.error}")

    def __repr__(self):
        if self.is_ok:
            return f"Ok({self.value!r})"
        return f"Err({self.error!r})"


# ---------------------------------------------------------------------------
# Arena (runtime memory region)
# ---------------------------------------------------------------------------

class NovaArena(NovaValue):
    def __init__(self, device: str, budget_bytes: int):
        self.device = device
        self.budget_bytes = budget_bytes
        self.used_bytes = 0
        self.allocations: list[NovaValue] = []

    def allocate(self, nbytes: int, value: NovaValue):
        if self.used_bytes + nbytes > self.budget_bytes:
            raise NovaPanic(
                f"Arena OOM: need {nbytes} bytes, "
                f"only {self.budget_bytes - self.used_bytes} remaining "
                f"(budget={self.budget_bytes})"
            )
        self.used_bytes += nbytes
        self.allocations.append(value)

    def free_all(self):
        self.allocations.clear()
        self.used_bytes = 0

    def __repr__(self):
        return f"<arena {self.device} {self.used_bytes}/{self.budget_bytes}>"


# ---------------------------------------------------------------------------
# Channel (typed message passing)
# ---------------------------------------------------------------------------

class NovaChannel(NovaValue):
    def __init__(self, buffer_size: int = 0):
        self.buffer: list[NovaValue] = []
        self.buffer_size = buffer_size

    def send(self, value: NovaValue):
        self.buffer.append(value)

    def recv(self) -> NovaValue:
        if self.buffer:
            return self.buffer.pop(0)
        return NovaNone()

    def __repr__(self):
        return f"<channel ({len(self.buffer)} buffered)>"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NovaPanic(Exception):
    def __init__(self, message: str):
        super().__init__(f"PANIC: {message}")


class NovaError(Exception):
    pass


class ReturnSignal(Exception):
    def __init__(self, value: NovaValue):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass
