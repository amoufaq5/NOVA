"""Recursive-descent parser for the Nova cognitive architecture language."""

from __future__ import annotations

from .tokens import Token, TokenType
from .ast_nodes import (
    Node,
    Program,
    IntLit,
    FloatLit,
    StrLit,
    BoolLit,
    NoneLit,
    ListLit,
    MapLit,
    Ident,
    BinOp,
    UnaryOp,
    CallExpr,
    IndexExpr,
    DotExpr,
    PipeExpr,
    LambdaExpr,
    IfExpr,
    Param,
    FnDecl,
    LetDecl,
    AssignStmt,
    ReturnStmt,
    IfStmt,
    ForStmt,
    WhileStmt,
    MatchStmt,
    MatchArm,
    BreakStmt,
    ContinueStmt,
    ExprStmt,
    MindDecl,
    MemoryDecl,
    PerceiveDecl,
    ThinkDecl,
    ImagineDecl,
    LearnDecl,
    ActDecl,
    OnEventDecl,
    GoalExpr,
    BelieveExpr,
    RecallExpr,
    StoreExpr,
    SimulateExpr,
    PredictExpr,
    ConsequenceExpr,
    ReflexDecl,
    ForgetExpr,
)


# ═══════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════


class ParseError(Exception):
    """Raised when the parser encounters a syntax error."""

    def __init__(self, message: str, line: int, col: int) -> None:
        self.line = line
        self.col = col
        super().__init__(f"{message} at {line}:{col}")


# ═══════════════════════════════════════════════════════════════════════
# Keyword-to-name helper sets
# ═══════════════════════════════════════════════════════════════════════

# Token types that are keywords but may be used as identifiers in certain
# positions (e.g. variable names, parameter names, field accesses).
_KEYWORD_NAME_TOKENS: set[TokenType] = {
    TokenType.MIND,
    TokenType.MEMORY,
    TokenType.PERCEIVE,
    TokenType.THINK,
    TokenType.IMAGINE,
    TokenType.LEARN,
    TokenType.ACT,
    TokenType.ON,
    TokenType.IDLE,
    TokenType.GOAL,
    TokenType.BELIEVE,
    TokenType.RECALL,
    TokenType.STORE,
    TokenType.FORGET,
    TokenType.SIMULATE,
    TokenType.PREDICT,
    TokenType.CONSEQUENCE,
    TokenType.REFLEX,
    TokenType.FN,
    TokenType.LET,
    TokenType.CONST,
    TokenType.IF,
    TokenType.ELIF,
    TokenType.ELSE,
    TokenType.FOR,
    TokenType.IN,
    TokenType.WHILE,
    TokenType.MATCH,
    TokenType.RETURN,
    TokenType.BREAK,
    TokenType.CONTINUE,
    TokenType.TRUE,
    TokenType.FALSE,
    TokenType.NONE,
    TokenType.AND,
    TokenType.OR,
    TokenType.NOT,
    TokenType.IMPORT,
    TokenType.FROM,
    TokenType.AS,
    TokenType.TYPE,
    TokenType.SELF,
    TokenType.TRY,
    TokenType.PANIC,
    TokenType.MUT,
}

# Cognitive expression keywords: contextual -- only parsed as cognitive
# expressions when followed by '('.
_COGNITIVE_KEYWORDS: set[TokenType] = {
    TokenType.RECALL,
    TokenType.STORE,
    TokenType.BELIEVE,
    TokenType.SIMULATE,
    TokenType.PREDICT,
    TokenType.CONSEQUENCE,
    TokenType.FORGET,
}

# Assignment operators
_ASSIGN_OPS: dict[TokenType, str] = {
    TokenType.EQ: "=",
    TokenType.PLUS_EQ: "+=",
    TokenType.MINUS_EQ: "-=",
    TokenType.STAR_EQ: "*=",
    TokenType.SLASH_EQ: "/=",
}


# ═══════════════════════════════════════════════════════════════════════
# Parser
# ═══════════════════════════════════════════════════════════════════════


class Parser:
    """Recursive-descent parser for Nova source code.

    Accepts a list of ``Token`` objects (as produced by the lexer) and
    returns a ``Program`` AST node.
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    # ── Token helpers ─────────────────────────────────────────────────

    def _cur(self) -> Token:
        """Return the current token (never past EOF)."""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        # Return last token (should be EOF) to avoid index errors.
        return self.tokens[-1]

    def _peek(self, offset: int = 1) -> Token:
        """Look ahead *offset* tokens from the current position."""
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]

    def _at(self, *types: TokenType) -> bool:
        """Check if the current token is one of *types*."""
        return self._cur().type in types

    def _peek_is(self, *types: TokenType, offset: int = 1) -> bool:
        """Check if the token at *offset* is one of *types*."""
        return self._peek(offset).type in types

    def _advance(self) -> Token:
        """Consume and return the current token."""
        tok = self._cur()
        self.pos += 1
        return tok

    def _expect(self, tt: TokenType, msg: str | None = None) -> Token:
        """Consume a token of type *tt* or raise ``ParseError``."""
        tok = self._cur()
        if tok.type != tt:
            text = msg or f"Expected {tt.name}, got {tok.type.name} ({tok.value!r})"
            raise ParseError(text, tok.line, tok.col)
        return self._advance()

    def _match(self, *types: TokenType) -> Token | None:
        """If current token matches one of *types*, consume and return it."""
        if self._cur().type in types:
            return self._advance()
        return None

    def _skip_newlines(self) -> None:
        """Skip over any NEWLINE tokens."""
        while self._at(TokenType.NEWLINE):
            self._advance()

    def _peek_non_newline(self) -> Token:
        """Return the next token that isn't a NEWLINE, without consuming."""
        i = self.pos
        while i < len(self.tokens) and self.tokens[i].type == TokenType.NEWLINE:
            i += 1
        if i < len(self.tokens):
            return self.tokens[i]
        return self.tokens[-1]

    def _error(self, msg: str) -> ParseError:
        tok = self._cur()
        return ParseError(msg, tok.line, tok.col)

    # ── Name helper ───────────────────────────────────────────────────

    def _consume_name(self) -> tuple[str, int, int]:
        """Consume a name token.  Accepts IDENT or any keyword token so
        that ``let store = 5`` and similar constructs work.
        """
        tok = self._cur()
        if tok.type == TokenType.IDENT or tok.type in _KEYWORD_NAME_TOKENS:
            self._advance()
            return tok.value, tok.line, tok.col
        raise ParseError(
            f"Expected identifier, got {tok.type.name} ({tok.value!r})",
            tok.line,
            tok.col,
        )

    # ── Public API ────────────────────────────────────────────────────

    def parse(self) -> Program:
        """Parse the token stream and return a ``Program``."""
        self._skip_newlines()
        tok = self._cur()
        prog = Program(line=tok.line, col=tok.col, body=[])
        while not self._at(TokenType.EOF):
            node = self._parse_top_level()
            if node is not None:
                prog.body.append(node)
            self._skip_newlines()
        return prog

    # ── Top-level declarations ────────────────────────────────────────

    def _parse_top_level(self) -> Node | None:
        self._skip_newlines()
        tok = self._cur()

        if tok.type == TokenType.MIND:
            return self._parse_mind_decl()
        if tok.type == TokenType.FN:
            return self._parse_fn_decl()
        if tok.type in (TokenType.LET, TokenType.CONST):
            return self._parse_let_or_const()
        # Fallback: statement
        return self._parse_statement()

    # ── mind declaration ──────────────────────────────────────────────

    def _parse_mind_decl(self) -> MindDecl:
        tok = self._expect(TokenType.MIND)
        name, _, _ = self._consume_name()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_mind_body()
        self._expect(TokenType.RBRACE)
        return MindDecl(line=tok.line, col=tok.col, name=name, body=body)

    def _parse_mind_body(self) -> list[Node]:
        body: list[Node] = []
        self._skip_newlines()
        while not self._at(TokenType.RBRACE, TokenType.EOF):
            node = self._parse_mind_member()
            if node is not None:
                body.append(node)
            self._skip_newlines()
        return body

    def _parse_mind_member(self) -> Node | None:
        self._skip_newlines()
        tok = self._cur()

        if tok.type == TokenType.MEMORY:
            return self._parse_memory_decl()
        if tok.type == TokenType.PERCEIVE:
            return self._parse_cognitive_method(PerceiveDecl, TokenType.PERCEIVE)
        if tok.type == TokenType.THINK:
            return self._parse_cognitive_method(ThinkDecl, TokenType.THINK)
        if tok.type == TokenType.IMAGINE:
            return self._parse_cognitive_method(ImagineDecl, TokenType.IMAGINE)
        if tok.type == TokenType.LEARN:
            return self._parse_cognitive_method(LearnDecl, TokenType.LEARN)
        if tok.type == TokenType.ACT:
            return self._parse_cognitive_method(ActDecl, TokenType.ACT)
        if tok.type == TokenType.ON:
            return self._parse_on_event()
        if tok.type == TokenType.REFLEX:
            return self._parse_reflex()
        if tok.type == TokenType.GOAL:
            return self._parse_goal_decl()
        if tok.type == TokenType.FN:
            return self._parse_fn_decl()
        if tok.type in (TokenType.LET, TokenType.CONST):
            return self._parse_let_or_const()
        # Fallback: statement
        return self._parse_statement()

    # ── memory declaration ────────────────────────────────────────────

    def _parse_memory_decl(self) -> MemoryDecl:
        tok = self._expect(TokenType.MEMORY)
        kind_name, _, _ = self._consume_name()
        self._expect(TokenType.LPAREN)
        config = self._parse_config_pairs()
        self._expect(TokenType.RPAREN)
        return MemoryDecl(
            line=tok.line,
            col=tok.col,
            kind=kind_name,
            name=kind_name,
            config=config,
        )

    def _parse_config_pairs(self) -> dict[str, object]:
        """Parse ``key: value, key: value`` inside parentheses."""
        config: dict[str, object] = {}
        while not self._at(TokenType.RPAREN, TokenType.EOF):
            self._skip_newlines()
            key, _, _ = self._consume_name()
            self._expect(TokenType.COLON)
            self._skip_newlines()
            # Parse the value as a primary expression and extract a Python
            # literal where possible.
            val_node = self._parse_expression()
            config[key] = self._node_to_config_value(val_node)
            self._match(TokenType.COMMA)
            self._skip_newlines()
        return config

    @staticmethod
    def _node_to_config_value(node: Node) -> object:
        """Convert simple AST nodes to plain Python values for config."""
        if isinstance(node, IntLit):
            return node.value
        if isinstance(node, FloatLit):
            return node.value
        if isinstance(node, StrLit):
            if len(node.parts) == 1 and isinstance(node.parts[0], str):
                return node.parts[0]
            return node
        if isinstance(node, BoolLit):
            return node.value
        if isinstance(node, Ident):
            return node.name
        return node

    # ── cognitive methods (perceive, think, imagine, learn, act) ──────

    def _parse_cognitive_method(self, cls: type, keyword: TokenType) -> Node:
        tok = self._expect(keyword)
        self._expect(TokenType.LPAREN)
        params = self._parse_param_list()
        self._expect(TokenType.RPAREN)
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return cls(line=tok.line, col=tok.col, params=params, body=body)

    # ── on event ──────────────────────────────────────────────────────

    def _parse_on_event(self) -> OnEventDecl:
        tok = self._expect(TokenType.ON)
        event_name, _, _ = self._consume_name()
        self._expect(TokenType.LPAREN)
        params = self._parse_param_list()
        self._expect(TokenType.RPAREN)
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return OnEventDecl(
            line=tok.line,
            col=tok.col,
            event_name=event_name,
            params=params,
            body=body,
        )

    # ── reflex ────────────────────────────────────────────────────────

    def _parse_reflex(self) -> ReflexDecl:
        tok = self._expect(TokenType.REFLEX)
        trigger = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        # Body is stored as a list; wrap in a Program-like node or use
        # the first expression as the action.  Per the AST, ``action`` is
        # a single Node, so we can wrap in a block-like ExprStmt list.
        # Actually, let's re-read the AST: action is a single Node | None.
        # We'll wrap in a synthetic block if there are multiple statements.
        if len(body) == 1:
            action = body[0]
        else:
            # Use a Program node as a block wrapper.
            action = Program(line=tok.line, col=tok.col, body=body)
        return ReflexDecl(line=tok.line, col=tok.col, trigger=trigger, action=action)

    # ── goal declaration (inside mind) ────────────────────────────────

    def _parse_goal_decl(self) -> GoalExpr:
        tok = self._expect(TokenType.GOAL)
        description = self._parse_primary()  # typically a string literal
        priority: Node | None = None
        self._skip_newlines()
        # optional: priority: N
        if self._at(TokenType.IDENT) and self._cur().value == "priority":
            self._advance()
            self._expect(TokenType.COLON)
            priority = self._parse_expression()
            self._skip_newlines()
        elif self._at(TokenType.COMMA):
            # Also accept comma-separated style: goal "text", priority: N
            self._advance()
            self._skip_newlines()
            if self._at(TokenType.IDENT) and self._cur().value == "priority":
                self._advance()
                self._expect(TokenType.COLON)
                priority = self._parse_expression()
                self._skip_newlines()
        body: list[Node] = []
        if self._at(TokenType.LBRACE):
            self._expect(TokenType.LBRACE)
            body = self._parse_block()
            self._expect(TokenType.RBRACE)
        return GoalExpr(
            line=tok.line,
            col=tok.col,
            description=description,
            priority=priority,
            body=body,
        )

    # ── fn declaration ────────────────────────────────────────────────

    def _parse_fn_decl(self) -> FnDecl:
        tok = self._expect(TokenType.FN)
        name, _, _ = self._consume_name()
        self._expect(TokenType.LPAREN)
        params = self._parse_param_list()
        self._expect(TokenType.RPAREN)
        return_type = None
        if self._match(TokenType.ARROW):
            return_type = self._parse_type_annotation()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return FnDecl(
            line=tok.line,
            col=tok.col,
            name=name,
            params=params,
            body=body,
            return_type=return_type,
        )

    def _parse_param_list(self) -> list[Param]:
        """Parse a comma-separated parameter list (without surrounding parens)."""
        params: list[Param] = []
        self._skip_newlines()
        while not self._at(TokenType.RPAREN, TokenType.EOF):
            params.append(self._parse_param())
            self._skip_newlines()
            if not self._match(TokenType.COMMA):
                break
            self._skip_newlines()
        return params

    def _parse_param(self) -> Param:
        name, line, col = self._consume_name()
        type_ann = None
        default = None
        if self._match(TokenType.COLON):
            type_ann = self._parse_type_annotation()
        if self._match(TokenType.EQ):
            default = self._parse_expression()
        return Param(line=line, col=col, name=name, type_ann=type_ann, default=default)

    def _parse_type_annotation(self) -> "TypeAnnotation":
        from .ast_nodes import TypeAnnotation

        tok = self._cur()
        name, line, col = self._consume_name()
        params: list[TypeAnnotation] = []
        if self._match(TokenType.LT):
            while not self._at(TokenType.GT, TokenType.EOF):
                params.append(self._parse_type_annotation())
                if not self._match(TokenType.COMMA):
                    break
            self._expect(TokenType.GT)
        return TypeAnnotation(line=line, col=col, name=name, params=params)

    # ── let / const ───────────────────────────────────────────────────

    def _parse_let_or_const(self) -> LetDecl:
        tok = self._advance()  # LET or CONST
        is_const = tok.type == TokenType.CONST
        mutable = False
        if tok.type == TokenType.LET and self._at(TokenType.MUT):
            self._advance()
            mutable = True
        name, _, _ = self._consume_name()
        type_ann = None
        if self._match(TokenType.COLON):
            type_ann = self._parse_type_annotation()
        value: Node | None = None
        if self._match(TokenType.EQ):
            value = self._parse_expression()
        return LetDecl(
            line=tok.line,
            col=tok.col,
            name=name,
            type_ann=type_ann,
            value=value,
            mutable=mutable and not is_const,
        )

    # ── block ─────────────────────────────────────────────────────────

    def _parse_block(self) -> list[Node]:
        """Parse statements inside braces (braces already consumed/expected
        by the caller).  Returns when RBRACE or EOF is reached.
        """
        stmts: list[Node] = []
        self._skip_newlines()
        while not self._at(TokenType.RBRACE, TokenType.EOF):
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
            self._skip_newlines()
        return stmts

    # ── statement ─────────────────────────────────────────────────────

    def _parse_statement(self) -> Node | None:
        self._skip_newlines()
        tok = self._cur()

        if tok.type in (TokenType.LET, TokenType.CONST):
            return self._parse_let_or_const()
        if tok.type == TokenType.IF:
            return self._parse_if_stmt()
        if tok.type == TokenType.FOR:
            return self._parse_for_stmt()
        if tok.type == TokenType.WHILE:
            return self._parse_while_stmt()
        if tok.type == TokenType.MATCH:
            return self._parse_match_stmt()
        if tok.type == TokenType.RETURN:
            return self._parse_return_stmt()
        if tok.type == TokenType.BREAK:
            self._advance()
            return BreakStmt(line=tok.line, col=tok.col)
        if tok.type == TokenType.CONTINUE:
            self._advance()
            return ContinueStmt(line=tok.line, col=tok.col)
        if tok.type == TokenType.FN:
            return self._parse_fn_decl()

        # Expression statement (may be upgraded to assignment)
        return self._parse_expr_stmt()

    # ── if statement ──────────────────────────────────────────────────

    def _parse_if_stmt(self) -> IfStmt:
        tok = self._expect(TokenType.IF)
        condition = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        then_body = self._parse_block()
        self._expect(TokenType.RBRACE)

        elif_clauses: list[tuple[Node, list[Node]]] = []
        else_body: list[Node] = []

        self._skip_newlines()
        while self._at(TokenType.ELIF):
            self._advance()
            elif_cond = self._parse_expression()
            self._skip_newlines()
            self._expect(TokenType.LBRACE)
            elif_body = self._parse_block()
            self._expect(TokenType.RBRACE)
            elif_clauses.append((elif_cond, elif_body))
            self._skip_newlines()

        if self._match(TokenType.ELSE):
            self._skip_newlines()
            self._expect(TokenType.LBRACE)
            else_body = self._parse_block()
            self._expect(TokenType.RBRACE)

        return IfStmt(
            line=tok.line,
            col=tok.col,
            condition=condition,
            then_body=then_body,
            elif_clauses=elif_clauses,
            else_body=else_body,
        )

    # ── for statement ─────────────────────────────────────────────────

    def _parse_for_stmt(self) -> ForStmt:
        tok = self._expect(TokenType.FOR)
        var_name, _, _ = self._consume_name()
        self._expect(TokenType.IN)
        iterable = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return ForStmt(
            line=tok.line, col=tok.col, var=var_name, iterable=iterable, body=body
        )

    # ── while statement ───────────────────────────────────────────────

    def _parse_while_stmt(self) -> WhileStmt:
        tok = self._expect(TokenType.WHILE)
        condition = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return WhileStmt(
            line=tok.line, col=tok.col, condition=condition, body=body
        )

    # ── match statement ───────────────────────────────────────────────

    def _parse_match_stmt(self) -> MatchStmt:
        tok = self._expect(TokenType.MATCH)
        subject = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        arms = self._parse_match_arms()
        self._expect(TokenType.RBRACE)
        return MatchStmt(line=tok.line, col=tok.col, subject=subject, arms=arms)

    def _parse_match_arms(self) -> list[MatchArm]:
        arms: list[MatchArm] = []
        self._skip_newlines()
        while not self._at(TokenType.RBRACE, TokenType.EOF):
            arm = self._parse_match_arm()
            arms.append(arm)
            # Consume optional comma or newline between arms
            self._skip_newlines()
            self._match(TokenType.COMMA)
            self._skip_newlines()
        return arms

    def _parse_match_arm(self) -> MatchArm:
        tok = self._cur()
        pattern = self._parse_pattern()
        self._expect(TokenType.FAT_ARROW)
        self._skip_newlines()
        # Body can be a brace-block or a single expression
        if self._at(TokenType.LBRACE):
            self._advance()
            body = self._parse_block()
            self._expect(TokenType.RBRACE)
        else:
            expr = self._parse_expression()
            body = [ExprStmt(line=expr.line, col=expr.col, expr=expr)]
        return MatchArm(line=tok.line, col=tok.col, pattern=pattern, body=body)

    def _parse_pattern(self) -> Node:
        """Parse a match pattern.

        Supports:
        - ``_`` (wildcard, returned as Ident("_"))
        - Literals (int, float, string, bool, none)
        - ``Name(inner)`` (constructor pattern, returned as CallExpr)
        - Bare identifiers
        """
        tok = self._cur()

        # Literal patterns
        if tok.type == TokenType.INT:
            self._advance()
            return IntLit(line=tok.line, col=tok.col, value=int(tok.value))
        if tok.type == TokenType.FLOAT:
            self._advance()
            return FloatLit(line=tok.line, col=tok.col, value=float(tok.value))
        if tok.type == TokenType.STRING:
            return self._parse_string_lit()
        if tok.type == TokenType.TRUE:
            self._advance()
            return BoolLit(line=tok.line, col=tok.col, value=True)
        if tok.type == TokenType.FALSE:
            self._advance()
            return BoolLit(line=tok.line, col=tok.col, value=False)
        if tok.type == TokenType.NONE:
            self._advance()
            return NoneLit(line=tok.line, col=tok.col)

        # Identifier or constructor pattern
        if tok.type == TokenType.IDENT or tok.type in _KEYWORD_NAME_TOKENS:
            name, line, col = self._consume_name()
            ident = Ident(line=line, col=col, name=name)
            # Constructor pattern: Name(inner_patterns...)
            if self._at(TokenType.LPAREN):
                self._advance()
                args: list[Node] = []
                self._skip_newlines()
                while not self._at(TokenType.RPAREN, TokenType.EOF):
                    args.append(self._parse_pattern())
                    self._skip_newlines()
                    if not self._match(TokenType.COMMA):
                        break
                    self._skip_newlines()
                self._expect(TokenType.RPAREN)
                return CallExpr(line=line, col=col, callee=ident, args=args)
            return ident

        raise self._error(
            f"Expected pattern, got {tok.type.name} ({tok.value!r})"
        )

    # ── return statement ──────────────────────────────────────────────

    def _parse_return_stmt(self) -> ReturnStmt:
        tok = self._expect(TokenType.RETURN)
        value: Node | None = None
        # Return value if not followed by newline/rbrace/eof
        if not self._at(TokenType.NEWLINE, TokenType.RBRACE, TokenType.EOF):
            value = self._parse_expression()
        return ReturnStmt(line=tok.line, col=tok.col, value=value)

    # ── expression statement (possibly assignment) ────────────────────

    def _parse_expr_stmt(self) -> Node:
        expr = self._parse_expression()
        # Check for assignment operators
        if self._cur().type in _ASSIGN_OPS:
            op_tok = self._advance()
            value = self._parse_expression()
            return AssignStmt(
                line=expr.line,
                col=expr.col,
                target=expr,
                op=_ASSIGN_OPS[op_tok.type],
                value=value,
            )
        return ExprStmt(line=expr.line, col=expr.col, expr=expr)

    # ═══════════════════════════════════════════════════════════════════
    # Expression parsing (precedence climbing)
    # ═══════════════════════════════════════════════════════════════════

    def _parse_expression(self) -> Node:
        """Top-level expression entry point (lowest precedence)."""
        return self._parse_pipeline()

    # 1. Pipeline: expr |> expr (left-assoc)
    def _parse_pipeline(self) -> Node:
        left = self._parse_or()
        while self._at(TokenType.PIPE_ARROW):
            tok = self._advance()
            right = self._parse_or()
            left = PipeExpr(line=tok.line, col=tok.col, left=left, right=right)
        return left

    # 2a. Logical or (left-assoc)
    def _parse_or(self) -> Node:
        left = self._parse_and()
        while self._at(TokenType.OR):
            tok = self._advance()
            right = self._parse_and()
            left = BinOp(
                line=tok.line, col=tok.col, op="or", left=left, right=right
            )
        return left

    # 2b. Logical and (left-assoc)
    def _parse_and(self) -> Node:
        left = self._parse_not()
        while self._at(TokenType.AND):
            tok = self._advance()
            right = self._parse_not()
            left = BinOp(
                line=tok.line, col=tok.col, op="and", left=left, right=right
            )
        return left

    # 2c. Logical not (prefix, unary)
    def _parse_not(self) -> Node:
        if self._at(TokenType.NOT):
            tok = self._advance()
            operand = self._parse_not()
            return UnaryOp(line=tok.line, col=tok.col, op="not", operand=operand)
        return self._parse_comparison()

    # 3. Comparison: ==, !=, <, >, <=, >= (non-associative -- single)
    _COMPARISON_OPS: dict[TokenType, str] = {
        TokenType.EQ_EQ: "==",
        TokenType.BANG_EQ: "!=",
        TokenType.LT: "<",
        TokenType.GT: ">",
        TokenType.LT_EQ: "<=",
        TokenType.GT_EQ: ">=",
    }

    def _parse_comparison(self) -> Node:
        left = self._parse_bitor()
        if self._cur().type in self._COMPARISON_OPS:
            tok = self._advance()
            op = self._COMPARISON_OPS[tok.type]
            right = self._parse_bitor()
            left = BinOp(
                line=tok.line, col=tok.col, op=op, left=left, right=right
            )
        return left

    # 4a. Bitwise or (left-assoc)
    def _parse_bitor(self) -> Node:
        left = self._parse_bitxor()
        while self._at(TokenType.PIPE):
            tok = self._advance()
            right = self._parse_bitxor()
            left = BinOp(
                line=tok.line, col=tok.col, op="|", left=left, right=right
            )
        return left

    # 4b. Bitwise xor (left-assoc)
    def _parse_bitxor(self) -> Node:
        left = self._parse_bitand()
        while self._at(TokenType.CARET):
            tok = self._advance()
            right = self._parse_bitand()
            left = BinOp(
                line=tok.line, col=tok.col, op="^", left=left, right=right
            )
        return left

    # 4c. Bitwise and (left-assoc)
    def _parse_bitand(self) -> Node:
        left = self._parse_shift()
        while self._at(TokenType.AMP):
            tok = self._advance()
            right = self._parse_shift()
            left = BinOp(
                line=tok.line, col=tok.col, op="&", left=left, right=right
            )
        return left

    # 5. Shift: <<, >> (left-assoc)
    def _parse_shift(self) -> Node:
        left = self._parse_addition()
        while self._at(TokenType.LSHIFT, TokenType.RSHIFT):
            tok = self._advance()
            op = "<<" if tok.type == TokenType.LSHIFT else ">>"
            right = self._parse_addition()
            left = BinOp(
                line=tok.line, col=tok.col, op=op, left=left, right=right
            )
        return left

    # 6. Addition: +, - (left-assoc)
    def _parse_addition(self) -> Node:
        left = self._parse_multiplication()
        while self._at(TokenType.PLUS, TokenType.MINUS):
            tok = self._advance()
            op = "+" if tok.type == TokenType.PLUS else "-"
            right = self._parse_multiplication()
            left = BinOp(
                line=tok.line, col=tok.col, op=op, left=left, right=right
            )
        return left

    # 7. Multiplication: *, /, //, %, @ (left-assoc)
    _MUL_OPS: dict[TokenType, str] = {
        TokenType.STAR: "*",
        TokenType.SLASH: "/",
        TokenType.DSLASH: "//",
        TokenType.PERCENT: "%",
        TokenType.AT: "@",
    }

    def _parse_multiplication(self) -> Node:
        left = self._parse_unary()
        while self._cur().type in self._MUL_OPS:
            tok = self._advance()
            op = self._MUL_OPS[tok.type]
            right = self._parse_unary()
            left = BinOp(
                line=tok.line, col=tok.col, op=op, left=left, right=right
            )
        return left

    # 8. Unary: -, ~, not (already handled in _parse_not for logical not)
    def _parse_unary(self) -> Node:
        if self._at(TokenType.MINUS):
            tok = self._advance()
            operand = self._parse_unary()
            return UnaryOp(line=tok.line, col=tok.col, op="-", operand=operand)
        if self._at(TokenType.TILDE):
            tok = self._advance()
            operand = self._parse_unary()
            return UnaryOp(line=tok.line, col=tok.col, op="~", operand=operand)
        return self._parse_power()

    # 9. Power: ** (right-assoc)
    def _parse_power(self) -> Node:
        base = self._parse_postfix()
        if self._at(TokenType.DSTAR):
            tok = self._advance()
            # Right-associative: recurse into _parse_unary (which includes power)
            exp = self._parse_unary()
            return BinOp(
                line=tok.line, col=tok.col, op="**", left=base, right=exp
            )
        return base

    # 10. Postfix: calls (), indexing [], dot .
    def _parse_postfix(self) -> Node:
        node = self._parse_primary()
        while True:
            if self._at(TokenType.LPAREN):
                node = self._parse_call(node)
            elif self._at(TokenType.LBRACKET):
                node = self._parse_index(node)
            elif self._at(TokenType.DOT):
                tok = self._advance()
                attr_name, _, _ = self._consume_name()
                node = DotExpr(
                    line=tok.line, col=tok.col, obj=node, attr=attr_name
                )
            else:
                break
        return node

    def _parse_call(self, callee: Node) -> CallExpr:
        tok = self._expect(TokenType.LPAREN)
        args = self._parse_arg_list()
        self._expect(TokenType.RPAREN)
        return CallExpr(line=tok.line, col=tok.col, callee=callee, args=args)

    def _parse_arg_list(self) -> list[Node]:
        """Parse a comma-separated argument list.

        Handles both positional and keyword arguments (``key: value``).
        Keyword arguments are represented as ``BinOp(op=":", left=Ident, right=expr)``
        to preserve the information without needing a dedicated KeywordArg node.
        """
        args: list[Node] = []
        self._skip_newlines()
        while not self._at(TokenType.RPAREN, TokenType.EOF):
            # Check for keyword argument: name COLON expr
            if (
                (self._cur().type == TokenType.IDENT or self._cur().type in _KEYWORD_NAME_TOKENS)
                and self._peek_is(TokenType.COLON)
            ):
                key_tok = self._advance()
                colon_tok = self._advance()  # consume ':'
                self._skip_newlines()
                value = self._parse_expression()
                key_node = Ident(line=key_tok.line, col=key_tok.col, name=key_tok.value)
                arg = BinOp(
                    line=colon_tok.line,
                    col=colon_tok.col,
                    op=":",
                    left=key_node,
                    right=value,
                )
                args.append(arg)
            else:
                args.append(self._parse_expression())
            self._skip_newlines()
            if not self._match(TokenType.COMMA):
                break
            self._skip_newlines()
        return args

    def _parse_index(self, obj: Node) -> IndexExpr:
        tok = self._expect(TokenType.LBRACKET)
        index = self._parse_expression()
        self._expect(TokenType.RBRACKET)
        return IndexExpr(line=tok.line, col=tok.col, obj=obj, index=index)

    # ── 11. Primary ───────────────────────────────────────────────────

    def _parse_primary(self) -> Node:  # noqa: C901
        tok = self._cur()

        # ── Cognitive keyword expressions (contextual) ────────────────
        if tok.type in _COGNITIVE_KEYWORDS and self._peek_is(TokenType.LPAREN):
            return self._parse_cognitive_expr()

        # ── goal expression (inline) ──────────────────────────────────
        # Only parse as a goal expression when followed by a string literal;
        # otherwise treat as an identifier (e.g. ``think(goal)``).
        if tok.type == TokenType.GOAL and self._peek_is(TokenType.STRING):
            return self._parse_goal_expr()

        # ── Literals ──────────────────────────────────────────────────
        if tok.type == TokenType.INT:
            self._advance()
            raw = tok.value.replace("_", "")
            if raw.startswith(("0x", "0X")):
                val = int(raw, 16)
            elif raw.startswith(("0b", "0B")):
                val = int(raw, 2)
            elif raw.startswith(("0o", "0O")):
                val = int(raw, 8)
            else:
                val = int(raw)
            return IntLit(line=tok.line, col=tok.col, value=val)

        if tok.type == TokenType.FLOAT:
            self._advance()
            return FloatLit(
                line=tok.line, col=tok.col, value=float(tok.value.replace("_", ""))
            )

        if tok.type == TokenType.STRING:
            return self._parse_string_lit()

        if tok.type == TokenType.TRUE:
            self._advance()
            return BoolLit(line=tok.line, col=tok.col, value=True)

        if tok.type == TokenType.FALSE:
            self._advance()
            return BoolLit(line=tok.line, col=tok.col, value=False)

        if tok.type == TokenType.NONE:
            self._advance()
            return NoneLit(line=tok.line, col=tok.col)

        # ── Identifier ────────────────────────────────────────────────
        if tok.type == TokenType.IDENT:
            self._advance()
            return Ident(line=tok.line, col=tok.col, name=tok.value)

        # ── self ──────────────────────────────────────────────────────
        if tok.type == TokenType.SELF:
            self._advance()
            return Ident(line=tok.line, col=tok.col, name="self")

        # ── Keywords used as identifiers in expression position ───────
        # Any keyword token that wasn't consumed by a special rule above
        # can be used as an identifier (e.g. ``perceive(input)`` inside
        # a function body, ``let store = 5``, etc.).
        # FN is excluded here so ``fn(x) { ... }`` parses as an anonymous function.
        if (tok.type in _KEYWORD_NAME_TOKENS or tok.type == TokenType.GOAL) and tok.type != TokenType.FN:
            self._advance()
            return Ident(line=tok.line, col=tok.col, name=tok.value)

        # ── Parenthesized expression ──────────────────────────────────
        if tok.type == TokenType.LPAREN:
            self._advance()
            self._skip_newlines()
            expr = self._parse_expression()
            self._skip_newlines()
            self._expect(TokenType.RPAREN)
            return expr

        # ── List literal [a, b, c] ────────────────────────────────────
        if tok.type == TokenType.LBRACKET:
            return self._parse_list_lit()

        # ── Anonymous function: fn(params) { body } ─────────────────
        if tok.type == TokenType.FN:
            return self._parse_fn_expr()

        # ── Map literal {k: v, ...} ──────────────────────────────────
        if tok.type == TokenType.LBRACE:
            return self._parse_map_lit()

        # ── Lambda: |params| expr or |params| { block } ──────────────
        if tok.type == TokenType.PIPE:
            return self._parse_lambda()

        # ── Match expression (used as expression) ─────────────────────
        if tok.type == TokenType.MATCH:
            return self._parse_match_expr()

        # ── If expression ─────────────────────────────────────────────
        if tok.type == TokenType.IF:
            return self._parse_if_expr()

        raise self._error(
            f"Unexpected token {tok.type.name} ({tok.value!r}) in expression"
        )

    # ── String literal (with interpolation support) ───────────────────

    def _parse_string_lit(self) -> StrLit:
        """Parse a string literal, handling interpolation sequences."""
        tok = self._cur()
        if tok.type != TokenType.STRING:
            raise self._error(f"Expected STRING, got {tok.type.name}")
        self._advance()
        parts: list[str | Node] = []
        if tok.value:
            parts.append(tok.value)

        # Check for interpolation continuation
        while self._at(TokenType.INTERP_START):
            self._advance()  # consume INTERP_START
            self._skip_newlines()
            expr = self._parse_expression()
            self._skip_newlines()
            self._expect(TokenType.INTERP_END)
            parts.append(expr)
            # After INTERP_END the lexer emits a STRING for the rest
            if self._at(TokenType.STRING):
                seg = self._advance()
                if seg.value:
                    parts.append(seg.value)

        return StrLit(line=tok.line, col=tok.col, parts=parts if parts else [""])

    # ── Cognitive expressions ─────────────────────────────────────────

    def _parse_cognitive_expr(self) -> Node:
        tok = self._advance()  # keyword token
        self._expect(TokenType.LPAREN)
        args = self._parse_arg_list()
        self._expect(TokenType.RPAREN)

        # Build the appropriate keyword-specific arguments from the arg
        # list.  Positional args are plain nodes; keyword args are
        # BinOp(op=":").
        positional: list[Node] = []
        named: dict[str, Node] = {}
        for a in args:
            if isinstance(a, BinOp) and a.op == ":" and isinstance(a.left, Ident):
                named[a.left.name] = a.right  # type: ignore[assignment]
            else:
                positional.append(a)

        line, col = tok.line, tok.col

        if tok.type == TokenType.RECALL:
            return RecallExpr(
                line=line,
                col=col,
                query=positional[0] if positional else None,
                source=named.get("from"),
                limit=named.get("limit"),
            )
        if tok.type == TokenType.STORE:
            return StoreExpr(
                line=line,
                col=col,
                value=positional[0] if positional else None,
                target=named.get("in"),
                metadata=named.get("metadata"),
            )
        if tok.type == TokenType.BELIEVE:
            return BelieveExpr(
                line=line,
                col=col,
                proposition=positional[0] if positional else None,
                confidence=named.get("confidence"),
            )
        if tok.type == TokenType.SIMULATE:
            return SimulateExpr(
                line=line,
                col=col,
                scenario=positional[0] if positional else None,
                steps=named.get("steps"),
            )
        if tok.type == TokenType.PREDICT:
            return PredictExpr(
                line=line,
                col=col,
                action=positional[0] if positional else None,
            )
        if tok.type == TokenType.CONSEQUENCE:
            return ConsequenceExpr(
                line=line,
                col=col,
                action=positional[0] if positional else None,
            )
        if tok.type == TokenType.FORGET:
            return ForgetExpr(
                line=line,
                col=col,
                query=positional[0] if positional else None,
                target=named.get("from"),
            )

        # Fallback (shouldn't reach here)
        callee = Ident(line=line, col=col, name=tok.value)
        return CallExpr(line=line, col=col, callee=callee, args=args)

    # ── goal expression (inline) ──────────────────────────────────────

    def _parse_goal_expr(self) -> GoalExpr:
        tok = self._expect(TokenType.GOAL)
        description = self._parse_primary()
        priority: Node | None = None
        body: list[Node] = []
        self._skip_newlines()
        # Check for priority keyword
        if self._at(TokenType.IDENT) and self._cur().value == "priority":
            self._advance()
            self._expect(TokenType.COLON)
            priority = self._parse_expression()
            self._skip_newlines()
        if self._at(TokenType.LBRACE):
            self._advance()
            body = self._parse_block()
            self._expect(TokenType.RBRACE)
        return GoalExpr(
            line=tok.line,
            col=tok.col,
            description=description,
            priority=priority,
            body=body,
        )

    # ── List literal ──────────────────────────────────────────────────

    def _parse_list_lit(self) -> ListLit:
        tok = self._expect(TokenType.LBRACKET)
        elements: list[Node] = []
        self._skip_newlines()
        while not self._at(TokenType.RBRACKET, TokenType.EOF):
            elements.append(self._parse_expression())
            self._skip_newlines()
            if not self._match(TokenType.COMMA):
                break
            self._skip_newlines()
        self._expect(TokenType.RBRACKET)
        return ListLit(line=tok.line, col=tok.col, elements=elements)

    # ── Map literal ───────────────────────────────────────────────────

    def _parse_map_lit(self) -> MapLit:
        tok = self._expect(TokenType.LBRACE)
        entries: list[tuple[Node, Node]] = []
        self._skip_newlines()
        while not self._at(TokenType.RBRACE, TokenType.EOF):
            key = self._parse_expression()
            self._expect(TokenType.COLON)
            self._skip_newlines()
            value = self._parse_expression()
            entries.append((key, value))
            self._skip_newlines()
            if not self._match(TokenType.COMMA):
                break
            self._skip_newlines()
        self._expect(TokenType.RBRACE)
        return MapLit(line=tok.line, col=tok.col, entries=entries)

    # ── Anonymous function expression ────────────────────────────────

    def _parse_fn_expr(self) -> FnDecl:
        tok = self._expect(TokenType.FN)
        name = ""
        if self._at(TokenType.IDENT):
            name, _, _ = self._consume_name()
        self._expect(TokenType.LPAREN)
        params = self._parse_param_list()
        self._expect(TokenType.RPAREN)
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        body = self._parse_block()
        self._expect(TokenType.RBRACE)
        return FnDecl(
            line=tok.line, col=tok.col, name=name or "<anon>",
            params=params, body=body,
        )

    # ── Lambda expression ─────────────────────────────────────────────

    def _parse_lambda(self) -> LambdaExpr:
        tok = self._expect(TokenType.PIPE)
        params: list[Param] = []
        while not self._at(TokenType.PIPE, TokenType.EOF):
            params.append(self._parse_param())
            if not self._match(TokenType.COMMA):
                break
        self._expect(TokenType.PIPE)
        self._skip_newlines()
        if self._at(TokenType.LBRACE):
            self._advance()
            body_stmts = self._parse_block()
            self._expect(TokenType.RBRACE)
            body: Node = Program(line=tok.line, col=tok.col, body=body_stmts)
        else:
            body = self._parse_expression()
        return LambdaExpr(
            line=tok.line, col=tok.col, params=params, body=body
        )

    # ── Match expression (reuse match logic) ──────────────────────────

    def _parse_match_expr(self) -> MatchStmt:
        """Match can appear in expression position; we reuse MatchStmt."""
        return self._parse_match_stmt()

    # ── If expression ─────────────────────────────────────────────────

    def _parse_if_expr(self) -> IfExpr:
        """Parse ``if cond { expr } else { expr }`` as an expression."""
        tok = self._expect(TokenType.IF)
        condition = self._parse_expression()
        self._skip_newlines()
        self._expect(TokenType.LBRACE)
        then_body = self._parse_block()
        self._expect(TokenType.RBRACE)
        # Wrap the then-block in a single node
        if len(then_body) == 1 and isinstance(then_body[0], ExprStmt):
            then_branch = then_body[0].expr
        elif then_body:
            then_branch = Program(line=tok.line, col=tok.col, body=then_body)
        else:
            then_branch = NoneLit(line=tok.line, col=tok.col)
        self._skip_newlines()
        else_branch: Node | None = None
        if self._match(TokenType.ELSE):
            self._skip_newlines()
            self._expect(TokenType.LBRACE)
            else_body = self._parse_block()
            self._expect(TokenType.RBRACE)
            if len(else_body) == 1 and isinstance(else_body[0], ExprStmt):
                else_branch = else_body[0].expr
            elif else_body:
                else_branch = Program(line=tok.line, col=tok.col, body=else_body)
            else:
                else_branch = NoneLit(line=tok.line, col=tok.col)
        return IfExpr(
            line=tok.line,
            col=tok.col,
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
        )


# ═══════════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════════


def parse(tokens: list[Token]) -> Program:
    """Parse a token list into a ``Program`` AST."""
    return Parser(tokens).parse()
