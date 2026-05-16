"""AST node definitions for the Nova cognitive architecture language."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Base
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Node:
    """Base class for every AST node.  Carries source location."""

    line: int = 0
    col: int = 0


# ═══════════════════════════════════════════════════════════════════════
# Program
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Program(Node):
    body: list[Node] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Literals
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class IntLit(Node):
    value: int = 0


@dataclass
class FloatLit(Node):
    value: float = 0.0


@dataclass
class StrLit(Node):
    """String literal.

    *parts* is a list where plain string segments are ``str`` and
    interpolated expressions are arbitrary expression ``Node`` objects.
    For non-interpolated strings *parts* contains a single ``str``.
    """

    parts: list[str | Node] = field(default_factory=list)


@dataclass
class BoolLit(Node):
    value: bool = False


@dataclass
class NoneLit(Node):
    pass


@dataclass
class ListLit(Node):
    elements: list[Node] = field(default_factory=list)


@dataclass
class MapLit(Node):
    """Map / dictionary literal.  Each entry is a (key, value) pair."""

    entries: list[tuple[Node, Node]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TypeAnnotation(Node):
    name: str = ""
    params: list[TypeAnnotation] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Expressions
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Ident(Node):
    name: str = ""


@dataclass
class BinOp(Node):
    op: str = ""
    left: Node | None = None
    right: Node | None = None


@dataclass
class UnaryOp(Node):
    op: str = ""
    operand: Node | None = None


@dataclass
class CallExpr(Node):
    callee: Node | None = None
    args: list[Node] = field(default_factory=list)


@dataclass
class IndexExpr(Node):
    obj: Node | None = None
    index: Node | None = None


@dataclass
class DotExpr(Node):
    obj: Node | None = None
    attr: str = ""


@dataclass
class PipeExpr(Node):
    left: Node | None = None
    right: Node | None = None


@dataclass
class LambdaExpr(Node):
    params: list[Param] = field(default_factory=list)
    body: Node | None = None


@dataclass
class IfExpr(Node):
    """Ternary / inline if expression."""

    condition: Node | None = None
    then_branch: Node | None = None
    else_branch: Node | None = None


# ═══════════════════════════════════════════════════════════════════════
# Declarations
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Param(Node):
    name: str = ""
    type_ann: TypeAnnotation | None = None
    default: Node | None = None


@dataclass
class FnDecl(Node):
    name: str = ""
    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)
    return_type: TypeAnnotation | None = None


@dataclass
class LetDecl(Node):
    name: str = ""
    type_ann: TypeAnnotation | None = None
    value: Node | None = None
    mutable: bool = False


# ═══════════════════════════════════════════════════════════════════════
# Statements
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AssignStmt(Node):
    target: Node | None = None
    op: str = "="
    value: Node | None = None


@dataclass
class ReturnStmt(Node):
    value: Node | None = None


@dataclass
class IfStmt(Node):
    condition: Node | None = None
    then_body: list[Node] = field(default_factory=list)
    elif_clauses: list[tuple[Node, list[Node]]] = field(default_factory=list)
    else_body: list[Node] = field(default_factory=list)


@dataclass
class ForStmt(Node):
    var: str = ""
    iterable: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class WhileStmt(Node):
    condition: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class MatchStmt(Node):
    subject: Node | None = None
    arms: list[MatchArm] = field(default_factory=list)


@dataclass
class MatchArm(Node):
    pattern: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class BreakStmt(Node):
    pass


@dataclass
class ContinueStmt(Node):
    pass


@dataclass
class ExprStmt(Node):
    expr: Node | None = None


# ═══════════════════════════════════════════════════════════════════════
# Cognitive constructs
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MindDecl(Node):
    """Top-level AI agent declaration."""

    name: str = ""
    body: list[Node] = field(default_factory=list)


@dataclass
class MemoryDecl(Node):
    """Memory system declaration.

    *kind* is one of ``"working"``, ``"episodic"``, ``"semantic"``, ``"procedural"``.
    """

    kind: str = ""
    name: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerceiveDecl(Node):
    """Input processing handler."""

    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class ThinkDecl(Node):
    """Reasoning method."""

    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class ImagineDecl(Node):
    """Simulation / imagination method."""

    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class LearnDecl(Node):
    """Learning method."""

    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class ActDecl(Node):
    """Action method."""

    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class OnEventDecl(Node):
    """Reactive event handler (e.g. ``on experience``, ``on idle``)."""

    event_name: str = ""
    params: list[Param] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class GoalExpr(Node):
    """Goal with priority and achievement plan."""

    description: Node | None = None
    priority: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class BelieveExpr(Node):
    """Belief with confidence level."""

    proposition: Node | None = None
    confidence: Node | None = None


@dataclass
class RecallExpr(Node):
    """Memory recall expression."""

    query: Node | None = None
    source: Node | None = None
    limit: Node | None = None


@dataclass
class StoreExpr(Node):
    """Store a value to a memory system."""

    value: Node | None = None
    target: Node | None = None
    metadata: Node | None = None


@dataclass
class SimulateExpr(Node):
    """Imagination simulation expression."""

    scenario: Node | None = None
    steps: Node | None = None


@dataclass
class PredictExpr(Node):
    """Predict consequences of an action."""

    action: Node | None = None


@dataclass
class ConsequenceExpr(Node):
    """Evaluate consequences of an action."""

    action: Node | None = None


@dataclass
class ReflexDecl(Node):
    """Automatic reactive behavior."""

    trigger: Node | None = None
    action: Node | None = None


@dataclass
class ForgetExpr(Node):
    """Remove from memory."""

    query: Node | None = None
    target: Node | None = None


@dataclass
class PatternExpr(Node):
    """Pattern recognition in memory."""

    source: Node | None = None
    query: Node | None = None
