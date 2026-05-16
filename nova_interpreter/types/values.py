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
# Causal Values (v2)
# ---------------------------------------------------------------------------

class NovaCausalGraph:
    """Runtime representation of a causal DAG."""
    def __init__(self, name, nodes=None, edges=None, confounders=None, invariances=None, mechanisms=None):
        self.name = name
        self.nodes = nodes or {}       # {name: node_type}
        self.edges = edges or []       # [(source, target, annotation)]
        self.confounders = confounders or []  # [(var, (a, b))]
        self.invariances = invariances or []  # [(var, indep_of, given)]
        self.mechanisms = mechanisms or {}    # {name: callable}
        self._adjacency = {}  # computed lazily
        self._parents = {}
        self._build_adjacency()

    def _build_adjacency(self):
        self._adjacency = {n: [] for n in self.nodes}
        self._parents = {n: [] for n in self.nodes}
        for src, tgt, _ in self.edges:
            if src in self._adjacency:
                self._adjacency[src].append(tgt)
            if tgt in self._parents:
                self._parents[tgt].append(src)

    def children(self, node):
        return self._adjacency.get(node, [])

    def parents(self, node):
        return self._parents.get(node, [])

    def ancestors(self, node, visited=None):
        if visited is None:
            visited = set()
        for p in self.parents(node):
            if p not in visited:
                visited.add(p)
                self.ancestors(p, visited)
        return visited

    def descendants(self, node, visited=None):
        if visited is None:
            visited = set()
        for c in self.children(node):
            if c not in visited:
                visited.add(c)
                self.descendants(c, visited)
        return visited

    def topological_sort(self):
        visited = set()
        order = []
        def dfs(n):
            if n in visited:
                return
            visited.add(n)
            for child in self.children(n):
                dfs(child)
            order.append(n)
        for node in self.nodes:
            dfs(node)
        return list(reversed(order))

    def is_d_separated(self, x, y, given):
        """Check if x and y are d-separated given a set of observed variables."""
        given_set = set(given)
        # Simple implementation: check if all paths are blocked
        # A path is blocked if it goes through a non-collider in given
        # or through a collider NOT in given
        return not self._has_active_path(x, y, given_set)

    def _has_active_path(self, start, end, given):
        """BFS to find active (d-connected) path."""
        from collections import deque
        # Use the Bayes-Ball algorithm (simplified)
        visited = set()
        queue = deque([(start, "up")])  # (node, direction)
        while queue:
            node, direction = queue.popleft()
            if (node, direction) in visited:
                continue
            visited.add((node, direction))
            if node == end:
                return True
            if direction == "up":  # going to parents
                if node not in given:
                    # Pass through (not observed) — can go to parents and children
                    for parent in self.parents(node):
                        queue.append((parent, "up"))
                    for child in self.children(node):
                        queue.append((child, "down"))
            elif direction == "down":  # going to children
                if node not in given:
                    for child in self.children(node):
                        queue.append((child, "down"))
                if node in given:
                    # Collider is observed — can go to parents
                    for parent in self.parents(node):
                        queue.append((parent, "up"))
        return False

    def do(self, interventions):
        """Apply do-calculus: return a mutilated graph with incoming edges removed for intervened variables."""
        new_edges = [(s, t, a) for s, t, a in self.edges if t not in interventions]
        return NovaCausalGraph(
            name=self.name + "_mutilated",
            nodes=dict(self.nodes),
            edges=new_edges,
            confounders=self.confounders,
            invariances=self.invariances,
            mechanisms=dict(self.mechanisms),
        )

    def adjustment_set(self, treatment, outcome):
        """Find a valid adjustment set (backdoor criterion)."""
        # Find all backdoor paths: paths from treatment to outcome through parents of treatment
        # Block them by conditioning on parents of treatment (that aren't descendants of treatment)
        treatment_descendants = self.descendants(treatment)
        treatment_parents = set(self.parents(treatment))
        # Valid adjustment: parents of treatment that aren't descendants of treatment
        adjust = treatment_parents - treatment_descendants
        return adjust

    def independent_mechanisms(self):
        """Find sets of mechanisms that can be trained independently."""
        # Mechanisms with no shared edges between them
        roots = [n for n in self.nodes if not self.parents(n)]
        # Group nodes by their root ancestor
        groups = []
        assigned = set()
        for root in roots:
            group = {root} | self.descendants(root)
            group -= assigned
            if group:
                groups.append(group)
                assigned |= group
        return groups

    def causal_chains(self):
        """Get all maximal causal chains in the graph."""
        order = self.topological_sort()
        chains = []
        visited = set()
        for node in order:
            if node not in visited and not self.parents(node):
                chain = []
                current = node
                while current:
                    chain.append(current)
                    visited.add(current)
                    children = [c for c in self.children(current) if c not in visited]
                    current = children[0] if children else None
                if len(chain) > 1:
                    chains.append(chain)
        return chains

    def __repr__(self):
        return f"CausalGraph({self.name}, {len(self.nodes)} nodes, {len(self.edges)} edges)"


class NovaWorldModel:
    """Runtime representation of a world model with causal structure."""
    def __init__(self, name, graph=None, state=None, mechanisms=None, transitions=None):
        self.name = name
        self.graph = graph  # NovaCausalGraph
        self.state = state or {}  # {var_name: value}
        self.mechanisms = mechanisms or {}  # {name: callable}
        self.transitions = transitions or {}  # {var_name: callable}
        self.history = []

    def step(self, env=None):
        """Advance one timestep using transition rules."""
        new_state = dict(self.state)
        for var, fn in self.transitions.items():
            new_state[var] = fn(self.state, env)
        self.history.append(dict(self.state))
        self.state = new_state
        return new_state

    def predict(self, steps, env=None):
        """Predict future states."""
        states = []
        saved = dict(self.state)
        for _ in range(steps):
            self.step(env)
            states.append(dict(self.state))
        self.state = saved
        return states

    def intervene(self, interventions, steps=1, env=None):
        """Simulate with interventions (do-calculus)."""
        saved = dict(self.state)
        # Apply interventions
        for var, val in interventions.items():
            self.state[var] = val
        # Remove mechanisms for intervened variables
        saved_mechanisms = dict(self.transitions)
        for var in interventions:
            if var in self.transitions:
                del self.transitions[var]
        # Simulate
        states = self.predict(steps, env)
        # Restore
        self.state = saved
        self.transitions = saved_mechanisms
        return states

    def counterfactual(self, observed, interventions, steps=1, env=None):
        """Compute counterfactual: given observed, what if interventions?"""
        # Step 1: Abduction — infer latent state from observations
        saved = dict(self.state)
        self.state.update(observed)
        # Step 2: Intervention — apply graph surgery
        # Step 3: Prediction — forward simulate
        result = self.intervene(interventions, steps, env)
        self.state = saved
        return result

    def __repr__(self):
        return f"WorldModel({self.name}, state={list(self.state.keys())})"


class NovaObjective:
    """Runtime representation of a typed objective."""
    def __init__(self, name, clauses=None):
        self.name = name
        self.clauses = clauses or []  # [(kind, check_fn)]
        self.satisfied = {}

    def check(self, model, data=None):
        """Check if all objective clauses are satisfied."""
        results = {}
        for kind, check_fn in self.clauses:
            try:
                results[kind] = check_fn(model, data)
            except Exception as e:
                results[kind] = False
        self.satisfied = results
        return all(results.values())

    def __repr__(self):
        return f"Objective({self.name}, {len(self.clauses)} clauses)"


class NovaCurriculum:
    """Runtime representation of a training curriculum."""
    def __init__(self, name, stages=None):
        self.name = name
        self.stages = stages or []  # [(name, depends_on, fn)]
        self.completed = set()

    def ready_stages(self):
        """Get stages whose dependencies are all satisfied."""
        ready = []
        for name, deps, fn in self.stages:
            if name not in self.completed and all(d in self.completed for d in deps):
                ready.append((name, fn))
        return ready

    def mark_complete(self, stage_name):
        self.completed.add(stage_name)

    def all_complete(self):
        return len(self.completed) == len(self.stages)

    def __repr__(self):
        return f"Curriculum({self.name}, {len(self.completed)}/{len(self.stages)} stages done)"


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
