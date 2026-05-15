"""Nova token types and Token dataclass — revised for full language."""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any


class TokenType(Enum):
    # --- Literals ---
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()
    NONE = auto()

    # --- Identifiers ---
    IDENT = auto()

    # --- Keywords: Core ---
    FN = auto()
    LET = auto()
    OWNED = auto()
    CONST = auto()
    RETURN = auto()
    IF = auto()
    ELIF = auto()
    ELSE = auto()
    FOR = auto()
    IN = auto()
    WHILE = auto()
    MATCH = auto()
    BREAK = auto()
    CONTINUE = auto()
    YIELD = auto()
    TYPE = auto()
    IMPORT = auto()
    FROM = auto()
    AS = auto()
    SELF = auto()
    MUT = auto()
    MOVE = auto()
    TRY = auto()
    PANIC = auto()

    # --- Keywords: AI/ML Domain ---
    MODEL = auto()
    FORWARD = auto()
    TENSOR = auto()
    FRAME = auto()
    AGENT = auto()
    TOOLS = auto()
    MEMORY = auto()
    PLAN = auto()
    ACT = auto()
    GRAD = auto()

    # --- Keywords: Safety ---
    CONSTRAIN = auto()
    SANDBOX = auto()

    # --- Keywords: Concurrency & Distribution ---
    ASYNC = auto()
    AWAIT = auto()
    SPAWN = auto()
    CHANNEL = auto()
    SELECT = auto()
    ON = auto()
    GPU = auto()
    DISTRIBUTE = auto()
    ARENA = auto()
    BUDGET = auto()
    SHARED = auto()

    # --- Keywords: Logical ---
    AND = auto()
    OR = auto()
    NOT = auto()

    # --- Operators: Arithmetic ---
    PLUS = auto()        # +
    MINUS = auto()       # -
    STAR = auto()        # *
    SLASH = auto()       # /
    DSLASH = auto()      # //
    PERCENT = auto()     # %
    DSTAR = auto()       # **
    AT = auto()          # @

    # --- Operators: Pipeline ---
    PIPE_ARROW = auto()  # |>

    # --- Operators: Bitwise ---
    AMP = auto()         # &
    PIPE = auto()        # |
    CARET = auto()       # ^
    TILDE = auto()       # ~
    LSHIFT = auto()      # <<
    RSHIFT = auto()      # >>

    # --- Operators: Comparison ---
    EQ = auto()          # ==
    NEQ = auto()         # !=
    LT = auto()          # <
    GT = auto()          # >
    LTE = auto()         # <=
    GTE = auto()         # >=

    # --- Operators: Assignment ---
    ASSIGN = auto()      # =
    PLUS_EQ = auto()     # +=
    MINUS_EQ = auto()    # -=
    STAR_EQ = auto()     # *=
    SLASH_EQ = auto()    # /=
    PERCENT_EQ = auto()  # %=

    # --- Delimiters ---
    LPAREN = auto()      # (
    RPAREN = auto()      # )
    LBRACKET = auto()    # [
    RBRACKET = auto()    # ]
    LBRACE = auto()      # {
    RBRACE = auto()      # }
    COMMA = auto()       # ,
    DOT = auto()         # .
    COLON = auto()       # :
    ARROW = auto()       # ->
    FAT_ARROW = auto()   # =>
    DOTDOT = auto()      # ..

    # --- Special ---
    EOF = auto()


KEYWORDS = {
    # Core
    "fn": TokenType.FN,
    "let": TokenType.LET,
    "owned": TokenType.OWNED,
    "const": TokenType.CONST,
    "return": TokenType.RETURN,
    "if": TokenType.IF,
    "elif": TokenType.ELIF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "in": TokenType.IN,
    "while": TokenType.WHILE,
    "match": TokenType.MATCH,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,
    "yield": TokenType.YIELD,
    "type": TokenType.TYPE,
    "import": TokenType.IMPORT,
    "from": TokenType.FROM,
    "as": TokenType.AS,
    "self": TokenType.SELF,
    "mut": TokenType.MUT,
    "move": TokenType.MOVE,
    "try": TokenType.TRY,
    "panic": TokenType.PANIC,

    # AI/ML domain
    "model": TokenType.MODEL,
    "forward": TokenType.FORWARD,
    "tensor": TokenType.TENSOR,
    "frame": TokenType.FRAME,
    "agent": TokenType.AGENT,
    "tools": TokenType.TOOLS,
    "memory": TokenType.MEMORY,
    "plan": TokenType.PLAN,
    "act": TokenType.ACT,
    "grad": TokenType.GRAD,

    # Safety
    "constrain": TokenType.CONSTRAIN,
    "sandbox": TokenType.SANDBOX,

    # Concurrency & distribution
    "async": TokenType.ASYNC,
    "await": TokenType.AWAIT,
    "spawn": TokenType.SPAWN,
    "channel": TokenType.CHANNEL,
    "select": TokenType.SELECT,
    "on": TokenType.ON,
    "gpu": TokenType.GPU,
    "distribute": TokenType.DISTRIBUTE,
    "arena": TokenType.ARENA,
    "budget": TokenType.BUDGET,
    "shared": TokenType.SHARED,

    # Logical
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,

    # Literals
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "none": TokenType.NONE,
}


@dataclass
class Token:
    type: TokenType
    value: Any
    line: int
    col: int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:{self.col})"
