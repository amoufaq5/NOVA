"""Token types for the Nova cognitive architecture language."""

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    # ── Literals ──────────────────────────────────────────────────────
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    IDENT = auto()

    # ── Core cognitive keywords ───────────────────────────────────────
    MIND = auto()
    MEMORY = auto()
    PERCEIVE = auto()
    THINK = auto()
    IMAGINE = auto()
    LEARN = auto()
    ACT = auto()
    ON = auto()
    IDLE = auto()
    GOAL = auto()
    BELIEVE = auto()
    RECALL = auto()
    STORE = auto()
    FORGET = auto()
    SIMULATE = auto()
    PREDICT = auto()
    CONSEQUENCE = auto()
    REFLEX = auto()

    # ── Standard keywords ─────────────────────────────────────────────
    FN = auto()
    LET = auto()
    CONST = auto()
    IF = auto()
    ELIF = auto()
    ELSE = auto()
    FOR = auto()
    IN = auto()
    WHILE = auto()
    MATCH = auto()
    RETURN = auto()
    BREAK = auto()
    CONTINUE = auto()
    TRUE = auto()
    FALSE = auto()
    NONE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    IMPORT = auto()
    FROM = auto()
    AS = auto()
    TYPE = auto()
    SELF = auto()
    TRY = auto()
    PANIC = auto()
    MUT = auto()

    # ── Operators ─────────────────────────────────────────────────────
    PLUS = auto()          # +
    MINUS = auto()         # -
    STAR = auto()          # *
    SLASH = auto()         # /
    DSLASH = auto()        # //
    PERCENT = auto()       # %
    DSTAR = auto()         # **
    AT = auto()            # @
    PIPE_ARROW = auto()    # |>
    FAT_ARROW = auto()     # =>
    ARROW = auto()         # ->
    EQ_EQ = auto()         # ==
    BANG_EQ = auto()       # !=
    LT = auto()            # <
    GT = auto()            # >
    LT_EQ = auto()         # <=
    GT_EQ = auto()         # >=
    EQ = auto()            # =
    PLUS_EQ = auto()       # +=
    MINUS_EQ = auto()      # -=
    STAR_EQ = auto()       # *=
    SLASH_EQ = auto()      # /=
    AMP = auto()           # &
    PIPE = auto()          # |
    CARET = auto()         # ^
    TILDE = auto()         # ~
    LSHIFT = auto()        # <<
    RSHIFT = auto()        # >>

    # ── Delimiters ────────────────────────────────────────────────────
    LPAREN = auto()        # (
    RPAREN = auto()        # )
    LBRACKET = auto()      # [
    RBRACKET = auto()      # ]
    LBRACE = auto()        # {
    RBRACE = auto()        # }
    COMMA = auto()         # ,
    DOT = auto()           # .
    COLON = auto()         # :
    DOT_DOT = auto()       # ..

    # ── String interpolation ──────────────────────────────────────────
    INTERP_START = auto()  # { inside a string (beginning of interpolation)
    INTERP_END = auto()    # } closing interpolation inside a string

    # ── Special ───────────────────────────────────────────────────────
    EOF = auto()
    NEWLINE = auto()


# ── Keyword lookup table ──────────────────────────────────────────────

KEYWORDS: dict[str, TokenType] = {
    # Cognitive
    "mind": TokenType.MIND,
    "memory": TokenType.MEMORY,
    "perceive": TokenType.PERCEIVE,
    "think": TokenType.THINK,
    "imagine": TokenType.IMAGINE,
    "learn": TokenType.LEARN,
    "act": TokenType.ACT,
    "on": TokenType.ON,
    "idle": TokenType.IDLE,
    "goal": TokenType.GOAL,
    "believe": TokenType.BELIEVE,
    "recall": TokenType.RECALL,
    "store": TokenType.STORE,
    "forget": TokenType.FORGET,
    "simulate": TokenType.SIMULATE,
    "predict": TokenType.PREDICT,
    "consequence": TokenType.CONSEQUENCE,
    "reflex": TokenType.REFLEX,
    # Standard
    "fn": TokenType.FN,
    "let": TokenType.LET,
    "const": TokenType.CONST,
    "if": TokenType.IF,
    "elif": TokenType.ELIF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "in": TokenType.IN,
    "while": TokenType.WHILE,
    "match": TokenType.MATCH,
    "return": TokenType.RETURN,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "none": TokenType.NONE,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
    "import": TokenType.IMPORT,
    "from": TokenType.FROM,
    "as": TokenType.AS,
    "type": TokenType.TYPE,
    "self": TokenType.SELF,
    "try": TokenType.TRY,
    "panic": TokenType.PANIC,
    "mut": TokenType.MUT,
}


@dataclass(frozen=True, slots=True)
class Token:
    """A single lexical token."""

    type: TokenType
    value: str
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"
