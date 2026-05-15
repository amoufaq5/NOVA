"""Nova AST node definitions — revised for full language with agents, arenas, safety, distribution."""

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass
class ASTNode:
    line: int = 0
    col: int = 0


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

@dataclass
class Program(ASTNode):
    body: list[ASTNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------

@dataclass
class IntLiteral(ASTNode):
    value: int = 0

@dataclass
class FloatLiteral(ASTNode):
    value: float = 0.0

@dataclass
class StringLiteral(ASTNode):
    value: str = ""

@dataclass
class BoolLiteral(ASTNode):
    value: bool = False

@dataclass
class NoneLiteral(ASTNode):
    pass

@dataclass
class ListLiteral(ASTNode):
    elements: list[ASTNode] = field(default_factory=list)

@dataclass
class MapLiteral(ASTNode):
    keys: list[ASTNode] = field(default_factory=list)
    values: list[ASTNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

@dataclass
class Identifier(ASTNode):
    name: str = ""

@dataclass
class SelfExpr(ASTNode):
    pass


# ---------------------------------------------------------------------------
# Parameters & Type Annotations
# ---------------------------------------------------------------------------

@dataclass
class Param(ASTNode):
    name: str = ""
    type_annotation: ASTNode | None = None
    default: ASTNode | None = None
    is_move: bool = False
    is_mut: bool = False

@dataclass
class TypeAnnotation(ASTNode):
    name: str = ""
    params: list[ASTNode] = field(default_factory=list)

@dataclass
class TensorTypeExpr(ASTNode):
    dtype: ASTNode | None = None
    dims: list[ASTNode] = field(default_factory=list)

@dataclass
class ResultTypeExpr(ASTNode):
    ok_type: ASTNode | None = None
    err_type: ASTNode | None = None


# ---------------------------------------------------------------------------
# Function Declarations
# ---------------------------------------------------------------------------

@dataclass
class FnDecl(ASTNode):
    name: str = ""
    params: list[Param] = field(default_factory=list)
    body: list[ASTNode] = field(default_factory=list)
    return_type: ASTNode | None = None
    is_async: bool = False


# ---------------------------------------------------------------------------
# Variable Declarations
# ---------------------------------------------------------------------------

@dataclass
class LetDecl(ASTNode):
    name: str = ""
    type_annotation: ASTNode | None = None
    value: ASTNode | None = None
    is_owned: bool = False

@dataclass
class ConstDecl(ASTNode):
    name: str = ""
    type_annotation: ASTNode | None = None
    value: ASTNode | None = None


# ---------------------------------------------------------------------------
# Model Declarations
# ---------------------------------------------------------------------------

@dataclass
class ModelDecl(ASTNode):
    name: str = ""
    params: list[Param] = field(default_factory=list)
    layers: list['LayerDecl'] = field(default_factory=list)
    forward: 'FnDecl | None' = None
    methods: list[FnDecl] = field(default_factory=list)

@dataclass
class LayerDecl(ASTNode):
    name: str = ""
    value: ASTNode | None = None


# ---------------------------------------------------------------------------
# Agent Declarations (first-class)
# ---------------------------------------------------------------------------

@dataclass
class AgentDecl(ASTNode):
    name: str = ""
    params: list[Param] = field(default_factory=list)
    tools_block: list['LayerDecl'] = field(default_factory=list)
    memory_block: list[LetDecl] = field(default_factory=list)
    plan_fn: FnDecl | None = None
    act_fn: FnDecl | None = None
    methods: list[FnDecl] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Safety Constructs
# ---------------------------------------------------------------------------

@dataclass
class ConstrainExpr(ASTNode):
    config: dict[str, ASTNode] = field(default_factory=dict)
    body: list[ASTNode] = field(default_factory=list)

@dataclass
class SandboxStmt(ASTNode):
    permissions: dict[str, ASTNode] = field(default_factory=dict)
    body: list[ASTNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory: Arena + Ownership
# ---------------------------------------------------------------------------

@dataclass
class ArenaStmt(ASTNode):
    device: str = "gpu"
    config: dict[str, ASTNode] = field(default_factory=dict)
    body: list[ASTNode] = field(default_factory=list)

@dataclass
class MoveExpr(ASTNode):
    name: str = ""

@dataclass
class SharedExpr(ASTNode):
    value: ASTNode | None = None


# ---------------------------------------------------------------------------
# Distribution & GPU
# ---------------------------------------------------------------------------

@dataclass
class OnGpuStmt(ASTNode):
    body: list[ASTNode] = field(default_factory=list)
    config: dict[str, ASTNode] = field(default_factory=dict)

@dataclass
class DistributeStmt(ASTNode):
    strategy: str = ""
    config: dict[str, ASTNode] = field(default_factory=dict)
    body: list[ASTNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

@dataclass
class SpawnExpr(ASTNode):
    body: list[ASTNode] = field(default_factory=list)

@dataclass
class AwaitExpr(ASTNode):
    expr: ASTNode | None = None

@dataclass
class ChannelExpr(ASTNode):
    elem_type: ASTNode | None = None
    buffer_size: ASTNode | None = None

@dataclass
class SelectStmt(ASTNode):
    arms: list[tuple[ASTNode, list[ASTNode]]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Autograd
# ---------------------------------------------------------------------------

@dataclass
class GradExpr(ASTNode):
    func: ASTNode | None = None
    order: ASTNode | None = None


# ---------------------------------------------------------------------------
# Expressions: Binary, Unary, Postfix
# ---------------------------------------------------------------------------

@dataclass
class BinOp(ASTNode):
    op: str = ""
    left: ASTNode | None = None
    right: ASTNode | None = None

@dataclass
class UnaryOp(ASTNode):
    op: str = ""
    operand: ASTNode | None = None

@dataclass
class CallExpr(ASTNode):
    callee: ASTNode | None = None
    args: list[ASTNode] = field(default_factory=list)
    kwargs: dict[str, ASTNode] = field(default_factory=dict)

@dataclass
class IndexExpr(ASTNode):
    obj: ASTNode | None = None
    index: ASTNode | None = None

@dataclass
class SliceExpr(ASTNode):
    obj: ASTNode | None = None
    start: ASTNode | None = None
    stop: ASTNode | None = None
    step: ASTNode | None = None

@dataclass
class DotExpr(ASTNode):
    obj: ASTNode | None = None
    attr: str = ""

@dataclass
class PipeExpr(ASTNode):
    left: ASTNode | None = None
    right: ASTNode | None = None

@dataclass
class LambdaExpr(ASTNode):
    params: list[Param] = field(default_factory=list)
    body: ASTNode | None = None

@dataclass
class TryExpr(ASTNode):
    expr: ASTNode | None = None

@dataclass
class PanicExpr(ASTNode):
    message: ASTNode | None = None


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass
class AssignStmt(ASTNode):
    target: ASTNode | None = None
    op: str = "="
    value: ASTNode | None = None

@dataclass
class ReturnStmt(ASTNode):
    value: ASTNode | None = None

@dataclass
class BreakStmt(ASTNode):
    pass

@dataclass
class ContinueStmt(ASTNode):
    pass

@dataclass
class IfStmt(ASTNode):
    condition: ASTNode | None = None
    body: list[ASTNode] = field(default_factory=list)
    elif_clauses: list[tuple[ASTNode, list[ASTNode]]] = field(default_factory=list)
    else_body: list[ASTNode] = field(default_factory=list)

@dataclass
class ForStmt(ASTNode):
    var: str = ""
    iterable: ASTNode | None = None
    body: list[ASTNode] = field(default_factory=list)

@dataclass
class WhileStmt(ASTNode):
    condition: ASTNode | None = None
    body: list[ASTNode] = field(default_factory=list)

@dataclass
class MatchStmt(ASTNode):
    subject: ASTNode | None = None
    arms: list['MatchArm'] = field(default_factory=list)

@dataclass
class MatchArm(ASTNode):
    pattern: ASTNode | None = None
    body: ASTNode | None = None

@dataclass
class ExprStmt(ASTNode):
    expr: ASTNode | None = None


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

@dataclass
class ImportDecl(ASTNode):
    path: list[str] = field(default_factory=list)
    alias: str | None = None

@dataclass
class FromImportDecl(ASTNode):
    path: list[str] = field(default_factory=list)
    names: list[tuple[str, str | None]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns (for match)
# ---------------------------------------------------------------------------

@dataclass
class WildcardPattern(ASTNode):
    pass

@dataclass
class LiteralPattern(ASTNode):
    value: ASTNode | None = None

@dataclass
class IdentPattern(ASTNode):
    name: str = ""

@dataclass
class ConstructorPattern(ASTNode):
    name: str = ""
    args: list[ASTNode] = field(default_factory=list)
