"""Nova recursive-descent parser — full language with agents, arenas, safety, distribution."""

from ..tokens import Token, TokenType
from ..ast_nodes.nodes import *


class ParseError(Exception):
    def __init__(self, message: str, token: Token):
        super().__init__(f"ParseError at L{token.line}:{token.col}: {message}")
        self.token = token


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # --- Utilities ---

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _check(self, *types: TokenType) -> bool:
        return self._peek().type in types

    def _match(self, *types: TokenType) -> Token | None:
        if self._peek().type in types:
            return self._advance()
        return None

    def _expect(self, ttype: TokenType, msg: str = "") -> Token:
        tok = self._advance()
        if tok.type != ttype:
            raise ParseError(
                msg or f"Expected {ttype.name}, got {tok.type.name} ({tok.value!r})", tok
            )
        return tok

    def _at_end(self) -> bool:
        return self._peek().type == TokenType.EOF

    def _error(self, msg: str) -> ParseError:
        return ParseError(msg, self._peek())

    # -----------------------------------------------------------------------
    # Program
    # -----------------------------------------------------------------------

    def parse(self) -> Program:
        prog = Program(body=[], line=1, col=1)
        while not self._at_end():
            prog.body.append(self._top_level_decl())
        return prog

    def _top_level_decl(self) -> ASTNode:
        tok = self._peek()
        match tok.type:
            case TokenType.FN | TokenType.ASYNC:
                return self._fn_decl()
            case TokenType.MODEL:
                return self._model_decl()
            case TokenType.AGENT:
                return self._agent_decl()
            case TokenType.IMPORT:
                return self._import_decl()
            case TokenType.FROM:
                return self._from_import_decl()
            case TokenType.CONST:
                return self._const_decl()
            case TokenType.TYPE:
                return self._type_alias()
            case _:
                return self._statement()

    # -----------------------------------------------------------------------
    # Function Declarations
    # -----------------------------------------------------------------------

    def _fn_decl(self) -> FnDecl:
        tok = self._peek()
        is_async = bool(self._match(TokenType.ASYNC))
        self._expect(TokenType.FN, "Expected 'fn'")
        name_tok = self._expect(TokenType.IDENT, "Expected function name")
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._type_expr()
        body = self._block()
        return FnDecl(
            name=name_tok.value, params=params, body=body,
            return_type=ret_type, is_async=is_async,
            line=tok.line, col=tok.col,
        )

    def _param_list(self) -> list[Param]:
        params = []
        if self._check(TokenType.RPAREN):
            return params
        params.append(self._param())
        while self._match(TokenType.COMMA):
            if self._check(TokenType.RPAREN):
                break
            params.append(self._param())
        return params

    def _param(self) -> Param:
        is_move = bool(self._match(TokenType.MOVE))
        is_mut = bool(self._match(TokenType.MUT))
        tok = self._expect(TokenType.IDENT, "Expected parameter name")
        type_ann = None
        default = None
        if self._match(TokenType.COLON):
            type_ann = self._type_expr()
        if self._match(TokenType.ASSIGN):
            default = self._expression()
        return Param(
            name=tok.value, type_annotation=type_ann,
            default=default, is_move=is_move, is_mut=is_mut,
            line=tok.line, col=tok.col,
        )

    # -----------------------------------------------------------------------
    # Model Declarations
    # -----------------------------------------------------------------------

    def _model_decl(self) -> ModelDecl:
        tok = self._advance()  # model
        name_tok = self._expect(TokenType.IDENT, "Expected model name")
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.LBRACE)

        layers = []
        forward = None
        methods = []

        while not self._check(TokenType.RBRACE) and not self._at_end():
            if self._check(TokenType.FORWARD):
                forward = self._forward_decl()
            elif self._check(TokenType.FN):
                methods.append(self._fn_decl())
            else:
                layers.append(self._layer_decl())

        self._expect(TokenType.RBRACE)
        return ModelDecl(
            name=name_tok.value, params=params, layers=layers,
            forward=forward, methods=methods,
            line=tok.line, col=tok.col,
        )

    def _layer_decl(self) -> LayerDecl:
        tok = self._expect(TokenType.IDENT, "Expected layer name")
        self._expect(TokenType.ASSIGN, "Expected '='")
        value = self._expression()
        return LayerDecl(name=tok.value, value=value, line=tok.line, col=tok.col)

    def _forward_decl(self) -> FnDecl:
        tok = self._advance()  # forward
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._type_expr()
        body = self._block()
        return FnDecl(
            name="forward", params=params, body=body,
            return_type=ret_type, line=tok.line, col=tok.col,
        )

    # -----------------------------------------------------------------------
    # Agent Declarations (first-class)
    # -----------------------------------------------------------------------

    def _agent_decl(self) -> AgentDecl:
        tok = self._advance()  # agent
        name_tok = self._expect(TokenType.IDENT, "Expected agent name")
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.LBRACE)

        tools_block = []
        memory_block = []
        plan_fn = None
        act_fn = None
        methods = []

        while not self._check(TokenType.RBRACE) and not self._at_end():
            if self._check(TokenType.TOOLS):
                tools_block = self._agent_tools_block()
            elif self._check(TokenType.MEMORY):
                memory_block = self._agent_memory_block()
            elif self._check(TokenType.PLAN):
                plan_fn = self._agent_plan_decl()
            elif self._check(TokenType.ACT):
                act_fn = self._agent_act_decl()
            elif self._check(TokenType.FN):
                methods.append(self._fn_decl())
            else:
                raise self._error(
                    f"Unexpected token in agent body: {self._peek().type.name}"
                )

        self._expect(TokenType.RBRACE)
        return AgentDecl(
            name=name_tok.value, params=params,
            tools_block=tools_block, memory_block=memory_block,
            plan_fn=plan_fn, act_fn=act_fn, methods=methods,
            line=tok.line, col=tok.col,
        )

    def _agent_tools_block(self) -> list[LayerDecl]:
        self._advance()  # tools
        self._expect(TokenType.LBRACE)
        tools = []
        while not self._check(TokenType.RBRACE) and not self._at_end():
            tools.append(self._layer_decl())
        self._expect(TokenType.RBRACE)
        return tools

    def _agent_memory_block(self) -> list[LetDecl]:
        self._advance()  # memory
        self._expect(TokenType.LBRACE)
        decls = []
        while not self._check(TokenType.RBRACE) and not self._at_end():
            tok = self._peek()
            name_tok = self._expect(TokenType.IDENT, "Expected memory field name")
            type_ann = None
            if self._match(TokenType.COLON):
                type_ann = self._type_expr()
            self._expect(TokenType.ASSIGN, "Expected '='")
            value = self._expression()
            decls.append(LetDecl(
                name=name_tok.value, type_annotation=type_ann,
                value=value, line=tok.line, col=tok.col,
            ))
        self._expect(TokenType.RBRACE)
        return decls

    def _agent_plan_decl(self) -> FnDecl:
        tok = self._advance()  # plan
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._type_expr()
        body = self._block()
        return FnDecl(
            name="plan", params=params, body=body,
            return_type=ret_type, line=tok.line, col=tok.col,
        )

    def _agent_act_decl(self) -> FnDecl:
        tok = self._advance()  # act
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._type_expr()
        body = self._block()
        return FnDecl(
            name="act", params=params, body=body,
            return_type=ret_type, line=tok.line, col=tok.col,
        )

    # -----------------------------------------------------------------------
    # Imports
    # -----------------------------------------------------------------------

    def _import_decl(self) -> ImportDecl:
        tok = self._advance()  # import
        path = [self._expect(TokenType.IDENT).value]
        while self._match(TokenType.DOT):
            path.append(self._expect(TokenType.IDENT).value)
        alias = None
        if self._match(TokenType.AS):
            alias = self._expect(TokenType.IDENT).value
        return ImportDecl(path=path, alias=alias, line=tok.line, col=tok.col)

    def _from_import_decl(self) -> FromImportDecl:
        tok = self._advance()  # from
        path = [self._expect(TokenType.IDENT).value]
        while self._match(TokenType.DOT):
            path.append(self._expect(TokenType.IDENT).value)
        self._expect(TokenType.IMPORT, "Expected 'import'")
        names = []
        name_tok = self._expect(TokenType.IDENT)
        alias = self._expect(TokenType.IDENT).value if self._match(TokenType.AS) else None
        names.append((name_tok.value, alias))
        while self._match(TokenType.COMMA):
            name_tok = self._expect(TokenType.IDENT)
            alias = self._expect(TokenType.IDENT).value if self._match(TokenType.AS) else None
            names.append((name_tok.value, alias))
        return FromImportDecl(path=path, names=names, line=tok.line, col=tok.col)

    # -----------------------------------------------------------------------
    # Constants & Type Aliases
    # -----------------------------------------------------------------------

    def _const_decl(self) -> ConstDecl:
        tok = self._advance()  # const
        name_tok = self._expect(TokenType.IDENT)
        type_ann = None
        if self._match(TokenType.COLON):
            type_ann = self._type_expr()
        self._expect(TokenType.ASSIGN)
        value = self._expression()
        return ConstDecl(
            name=name_tok.value, type_annotation=type_ann,
            value=value, line=tok.line, col=tok.col,
        )

    def _type_alias(self) -> ASTNode:
        tok = self._advance()  # type
        name_tok = self._expect(TokenType.IDENT)
        self._expect(TokenType.ASSIGN)
        type_val = self._type_expr()
        return ConstDecl(
            name=name_tok.value, value=type_val,
            line=tok.line, col=tok.col,
        )

    # -----------------------------------------------------------------------
    # Blocks
    # -----------------------------------------------------------------------

    def _block(self) -> list[ASTNode]:
        self._expect(TokenType.LBRACE)
        stmts = []
        while not self._check(TokenType.RBRACE) and not self._at_end():
            stmts.append(self._statement())
        self._expect(TokenType.RBRACE)
        return stmts

    # -----------------------------------------------------------------------
    # Statements
    # -----------------------------------------------------------------------

    def _statement(self) -> ASTNode:
        tok = self._peek()
        match tok.type:
            case TokenType.LET:
                return self._let_decl(is_owned=False)
            case TokenType.OWNED:
                return self._let_decl(is_owned=True)
            case TokenType.RETURN:
                return self._return_stmt()
            case TokenType.IF:
                return self._if_stmt()
            case TokenType.FOR:
                return self._for_stmt()
            case TokenType.WHILE:
                return self._while_stmt()
            case TokenType.MATCH:
                return self._match_stmt()
            case TokenType.ON:
                return self._on_gpu_stmt()
            case TokenType.ARENA:
                return self._arena_stmt()
            case TokenType.DISTRIBUTE:
                return self._distribute_stmt()
            case TokenType.SANDBOX:
                return self._sandbox_stmt()
            case TokenType.SPAWN:
                return self._spawn_stmt()
            case TokenType.SELECT:
                return self._select_stmt()
            case TokenType.BREAK:
                self._advance()
                return BreakStmt(line=tok.line, col=tok.col)
            case TokenType.CONTINUE:
                self._advance()
                return ContinueStmt(line=tok.line, col=tok.col)
            case TokenType.FN | TokenType.ASYNC:
                return self._fn_decl()
            case _:
                return self._assign_or_expr_stmt()

    def _let_decl(self, is_owned: bool) -> LetDecl:
        tok = self._advance()  # let or owned
        name_tok = self._expect(TokenType.IDENT)
        type_ann = None
        if self._match(TokenType.COLON):
            type_ann = self._type_expr()
        self._expect(TokenType.ASSIGN, "Expected '='")
        value = self._expression()
        return LetDecl(
            name=name_tok.value, type_annotation=type_ann,
            value=value, is_owned=is_owned,
            line=tok.line, col=tok.col,
        )

    def _return_stmt(self) -> ReturnStmt:
        tok = self._advance()  # return
        value = None
        if not self._check(TokenType.RBRACE) and not self._at_end():
            value = self._expression()
        return ReturnStmt(value=value, line=tok.line, col=tok.col)

    def _if_stmt(self) -> IfStmt:
        tok = self._advance()  # if
        cond = self._expression()
        body = self._block()
        elif_clauses = []
        else_body = []
        while self._match(TokenType.ELIF):
            elif_cond = self._expression()
            elif_body = self._block()
            elif_clauses.append((elif_cond, elif_body))
        if self._match(TokenType.ELSE):
            else_body = self._block()
        return IfStmt(
            condition=cond, body=body,
            elif_clauses=elif_clauses, else_body=else_body,
            line=tok.line, col=tok.col,
        )

    def _for_stmt(self) -> ForStmt:
        tok = self._advance()  # for
        var_tok = self._expect(TokenType.IDENT)
        self._expect(TokenType.IN, "Expected 'in'")
        iterable = self._expression()
        body = self._block()
        return ForStmt(
            var=var_tok.value, iterable=iterable,
            body=body, line=tok.line, col=tok.col,
        )

    def _while_stmt(self) -> WhileStmt:
        tok = self._advance()  # while
        cond = self._expression()
        body = self._block()
        return WhileStmt(condition=cond, body=body, line=tok.line, col=tok.col)

    def _match_stmt(self) -> MatchStmt:
        tok = self._advance()  # match
        subject = self._expression()
        self._expect(TokenType.LBRACE)
        arms = []
        while not self._check(TokenType.RBRACE) and not self._at_end():
            arms.append(self._match_arm())
        self._expect(TokenType.RBRACE)
        return MatchStmt(subject=subject, arms=arms, line=tok.line, col=tok.col)

    def _match_arm(self) -> MatchArm:
        pattern = self._pattern()
        self._expect(TokenType.FAT_ARROW, "Expected '=>'")
        if self._check(TokenType.LBRACE):
            body_stmts = self._block()
            body = body_stmts[-1] if body_stmts else NoneLiteral()
        else:
            body = self._expression()
        self._match(TokenType.COMMA)
        return MatchArm(pattern=pattern, body=body)

    def _pattern(self) -> ASTNode:
        tok = self._peek()
        if tok.type == TokenType.IDENT and tok.value == "_":
            self._advance()
            return WildcardPattern(line=tok.line, col=tok.col)
        if tok.type in (TokenType.INT, TokenType.FLOAT, TokenType.STRING,
                        TokenType.TRUE, TokenType.FALSE, TokenType.NONE):
            return LiteralPattern(value=self._primary(), line=tok.line, col=tok.col)
        if tok.type == TokenType.IDENT:
            name = self._advance()
            if self._match(TokenType.LPAREN):
                args = []
                if not self._check(TokenType.RPAREN):
                    args.append(self._pattern())
                    while self._match(TokenType.COMMA):
                        args.append(self._pattern())
                self._expect(TokenType.RPAREN)
                return ConstructorPattern(
                    name=name.value, args=args,
                    line=tok.line, col=tok.col,
                )
            return IdentPattern(name=name.value, line=tok.line, col=tok.col)
        raise self._error(f"Unexpected token in pattern: {tok.type.name}")

    # --- Domain-specific statement forms ---

    def _on_gpu_stmt(self) -> OnGpuStmt:
        tok = self._advance()  # on
        self._expect(TokenType.GPU, "Expected 'gpu'")
        config = self._optional_kv_parens()
        body = self._block()
        return OnGpuStmt(body=body, config=config, line=tok.line, col=tok.col)

    def _arena_stmt(self) -> ArenaStmt:
        tok = self._advance()  # arena
        device = "gpu"
        if self._check(TokenType.GPU):
            self._advance()
            device = "gpu"
        elif self._check(TokenType.IDENT):
            device = self._advance().value
        config = self._optional_kv_parens()
        body = self._block()
        return ArenaStmt(device=device, config=config, body=body,
                         line=tok.line, col=tok.col)

    def _distribute_stmt(self) -> DistributeStmt:
        tok = self._advance()  # distribute
        self._expect(TokenType.LPAREN)
        strategy = ""
        config = {}
        if self._check(TokenType.IDENT):
            strategy = self._advance().value
        while self._match(TokenType.COMMA):
            key = self._expect(TokenType.IDENT).value
            self._expect(TokenType.ASSIGN)
            val = self._expression()
            config[key] = val
        self._expect(TokenType.RPAREN)
        body = self._block()
        return DistributeStmt(
            strategy=strategy, config=config, body=body,
            line=tok.line, col=tok.col,
        )

    def _sandbox_stmt(self) -> SandboxStmt:
        tok = self._advance()  # sandbox
        permissions = self._optional_kv_parens()
        body = self._block()
        return SandboxStmt(permissions=permissions, body=body,
                           line=tok.line, col=tok.col)

    def _spawn_stmt(self) -> ExprStmt:
        tok = self._peek()
        expr = self._expression()  # spawn will be parsed as primary
        return ExprStmt(expr=expr, line=tok.line, col=tok.col)

    def _select_stmt(self) -> SelectStmt:
        tok = self._advance()  # select
        self._expect(TokenType.LBRACE)
        arms = []
        while not self._check(TokenType.RBRACE) and not self._at_end():
            cond = self._expression()
            body = self._block()
            arms.append((cond, body))
        self._expect(TokenType.RBRACE)
        return SelectStmt(arms=arms, line=tok.line, col=tok.col)

    def _assign_or_expr_stmt(self) -> ASTNode:
        expr = self._expression()
        assign_ops = {
            TokenType.ASSIGN: "=", TokenType.PLUS_EQ: "+=",
            TokenType.MINUS_EQ: "-=", TokenType.STAR_EQ: "*=",
            TokenType.SLASH_EQ: "/=", TokenType.PERCENT_EQ: "%=",
        }
        tok = self._peek()
        if tok.type in assign_ops:
            op = assign_ops[self._advance().type]
            value = self._expression()
            return AssignStmt(target=expr, op=op, value=value,
                              line=tok.line, col=tok.col)
        return ExprStmt(expr=expr, line=expr.line, col=expr.col)

    # -----------------------------------------------------------------------
    # Helper: optional (key=val, ...) config
    # -----------------------------------------------------------------------

    def _optional_kv_parens(self) -> dict[str, ASTNode]:
        config = {}
        if self._match(TokenType.LPAREN):
            while not self._check(TokenType.RPAREN) and not self._at_end():
                # Accept identifiers or keywords as config keys
                tok = self._advance()
                key = tok.value
                self._expect(TokenType.ASSIGN)
                val = self._expression()
                config[key] = val
                self._match(TokenType.COMMA)
            self._expect(TokenType.RPAREN)
        return config

    # -----------------------------------------------------------------------
    # Type Expressions
    # -----------------------------------------------------------------------

    def _type_expr(self) -> ASTNode:
        tok = self._peek()
        if tok.type == TokenType.TENSOR:
            return self._tensor_type()
        if tok.type == TokenType.FRAME:
            self._advance()
            return TypeAnnotation(name="frame", line=tok.line, col=tok.col)
        # [T] — list type
        if tok.type == TokenType.LBRACKET:
            self._advance()
            inner = self._type_expr()
            self._expect(TokenType.RBRACKET)
            return TypeAnnotation(name="list", params=[inner], line=tok.line, col=tok.col)
        # {K: V} — map type, {T} — set type
        if tok.type == TokenType.LBRACE:
            self._advance()
            first = self._type_expr()
            if self._match(TokenType.COLON):
                val = self._type_expr()
                self._expect(TokenType.RBRACE)
                return TypeAnnotation(name="map", params=[first, val], line=tok.line, col=tok.col)
            self._expect(TokenType.RBRACE)
            return TypeAnnotation(name="set", params=[first], line=tok.line, col=tok.col)
        # Result[T, E]
        if tok.type == TokenType.IDENT and tok.value == "Result":
            return self._result_type()
        if tok.type in (TokenType.IDENT, TokenType.FN):
            name = self._advance()
            return TypeAnnotation(name=name.value, line=tok.line, col=tok.col)
        name = self._advance()
        return TypeAnnotation(name=name.value, line=tok.line, col=tok.col)

    def _tensor_type(self) -> TensorTypeExpr:
        tok = self._advance()  # tensor
        if not self._match(TokenType.LBRACKET):
            return TensorTypeExpr(line=tok.line, col=tok.col)
        dtype = self._type_expr()
        dims = []
        while self._match(TokenType.COMMA):
            if self._check(TokenType.IDENT) and self._peek().value == "_":
                self._advance()
                dims.append(Identifier(name="_", line=self._peek().line, col=self._peek().col))
            else:
                dims.append(self._expression())
        self._expect(TokenType.RBRACKET)
        return TensorTypeExpr(dtype=dtype, dims=dims, line=tok.line, col=tok.col)

    def _result_type(self) -> ResultTypeExpr:
        tok = self._advance()  # Result
        self._expect(TokenType.LBRACKET)
        ok_type = self._type_expr()
        self._expect(TokenType.COMMA)
        err_type = self._type_expr()
        self._expect(TokenType.RBRACKET)
        return ResultTypeExpr(ok_type=ok_type, err_type=err_type,
                              line=tok.line, col=tok.col)

    # -----------------------------------------------------------------------
    # Expressions — Precedence Climbing
    # -----------------------------------------------------------------------

    def _expression(self) -> ASTNode:
        return self._or_expr()

    def _or_expr(self) -> ASTNode:
        left = self._and_expr()
        while self._match(TokenType.OR):
            right = self._and_expr()
            left = BinOp(op="or", left=left, right=right, line=left.line, col=left.col)
        return left

    def _and_expr(self) -> ASTNode:
        left = self._not_expr()
        while self._match(TokenType.AND):
            right = self._not_expr()
            left = BinOp(op="and", left=left, right=right, line=left.line, col=left.col)
        return left

    def _not_expr(self) -> ASTNode:
        if tok := self._match(TokenType.NOT):
            operand = self._not_expr()
            return UnaryOp(op="not", operand=operand, line=tok.line, col=tok.col)
        return self._comparison()

    def _comparison(self) -> ASTNode:
        left = self._pipe_expr()
        comp_ops = {
            TokenType.EQ: "==", TokenType.NEQ: "!=",
            TokenType.LT: "<", TokenType.GT: ">",
            TokenType.LTE: "<=", TokenType.GTE: ">=",
        }
        while self._peek().type in comp_ops:
            op = comp_ops[self._advance().type]
            right = self._pipe_expr()
            left = BinOp(op=op, left=left, right=right, line=left.line, col=left.col)
        if self._check(TokenType.IN):
            self._advance()
            right = self._pipe_expr()
            left = BinOp(op="in", left=left, right=right, line=left.line, col=left.col)
        return left

    def _pipe_expr(self) -> ASTNode:
        left = self._bitor_expr()
        while self._match(TokenType.PIPE_ARROW):
            right = self._bitor_expr()
            left = PipeExpr(left=left, right=right, line=left.line, col=left.col)
        return left

    def _bitor_expr(self) -> ASTNode:
        left = self._bitxor_expr()
        while self._match(TokenType.PIPE):
            right = self._bitxor_expr()
            left = BinOp(op="|", left=left, right=right, line=left.line, col=left.col)
        return left

    def _bitxor_expr(self) -> ASTNode:
        left = self._bitand_expr()
        while self._match(TokenType.CARET):
            right = self._bitand_expr()
            left = BinOp(op="^", left=left, right=right, line=left.line, col=left.col)
        return left

    def _bitand_expr(self) -> ASTNode:
        left = self._shift_expr()
        while self._match(TokenType.AMP):
            right = self._shift_expr()
            left = BinOp(op="&", left=left, right=right, line=left.line, col=left.col)
        return left

    def _shift_expr(self) -> ASTNode:
        left = self._add_expr()
        while self._check(TokenType.LSHIFT, TokenType.RSHIFT):
            op = ">>" if self._advance().type == TokenType.RSHIFT else "<<"
            right = self._add_expr()
            left = BinOp(op=op, left=left, right=right, line=left.line, col=left.col)
        return left

    def _add_expr(self) -> ASTNode:
        left = self._mul_expr()
        while self._check(TokenType.PLUS, TokenType.MINUS):
            op = "+" if self._advance().type == TokenType.PLUS else "-"
            right = self._mul_expr()
            left = BinOp(op=op, left=left, right=right, line=left.line, col=left.col)
        return left

    def _mul_expr(self) -> ASTNode:
        left = self._unary_expr()
        mul_ops = {
            TokenType.STAR: "*", TokenType.SLASH: "/",
            TokenType.DSLASH: "//", TokenType.PERCENT: "%",
            TokenType.AT: "@",
        }
        while self._peek().type in mul_ops:
            op = mul_ops[self._advance().type]
            right = self._unary_expr()
            left = BinOp(op=op, left=left, right=right, line=left.line, col=left.col)
        return left

    def _unary_expr(self) -> ASTNode:
        if tok := self._match(TokenType.MINUS):
            operand = self._unary_expr()
            return UnaryOp(op="-", operand=operand, line=tok.line, col=tok.col)
        if tok := self._match(TokenType.TILDE):
            operand = self._unary_expr()
            return UnaryOp(op="~", operand=operand, line=tok.line, col=tok.col)
        return self._power_expr()

    def _power_expr(self) -> ASTNode:
        base = self._await_expr()
        if self._match(TokenType.DSTAR):
            exp = self._unary_expr()  # right-associative
            return BinOp(op="**", left=base, right=exp, line=base.line, col=base.col)
        return base

    def _await_expr(self) -> ASTNode:
        if tok := self._match(TokenType.AWAIT):
            expr = self._postfix_expr()
            return AwaitExpr(expr=expr, line=tok.line, col=tok.col)
        return self._postfix_expr()

    def _postfix_expr(self) -> ASTNode:
        expr = self._primary()
        while True:
            if self._match(TokenType.LPAREN):
                expr = self._finish_call(expr)
            elif self._match(TokenType.LBRACKET):
                expr = self._finish_index(expr)
            elif self._match(TokenType.DOT):
                # Allow keywords as attribute names (e.g., model.forward, agent.plan)
                attr = self._advance()
                expr = DotExpr(obj=expr, attr=attr.value, line=expr.line, col=expr.col)
            else:
                break
        return expr

    def _finish_call(self, callee: ASTNode) -> CallExpr:
        args = []
        kwargs = {}
        if not self._check(TokenType.RPAREN):
            self._parse_arg(args, kwargs)
            while self._match(TokenType.COMMA):
                if self._check(TokenType.RPAREN):
                    break
                self._parse_arg(args, kwargs)
        self._expect(TokenType.RPAREN)
        return CallExpr(
            callee=callee, args=args, kwargs=kwargs,
            line=callee.line, col=callee.col,
        )

    def _parse_arg(self, args: list, kwargs: dict):
        if (self._check(TokenType.IDENT) and
                self.pos + 1 < len(self.tokens) and
                self.tokens[self.pos + 1].type == TokenType.ASSIGN):
            name = self._advance().value
            self._advance()  # =
            kwargs[name] = self._expression()
        elif self._check(TokenType.MOVE):
            tok = self._advance()
            name = self._expect(TokenType.IDENT).value
            args.append(MoveExpr(name=name, line=tok.line, col=tok.col))
        else:
            args.append(self._expression())

    def _finish_index(self, obj: ASTNode) -> ASTNode:
        if self._match(TokenType.COLON):
            stop = None if self._check(TokenType.RBRACKET, TokenType.COLON) else self._expression()
            step = None
            if self._match(TokenType.COLON):
                step = None if self._check(TokenType.RBRACKET) else self._expression()
            self._expect(TokenType.RBRACKET)
            return SliceExpr(obj=obj, start=None, stop=stop, step=step,
                             line=obj.line, col=obj.col)
        start = self._expression()
        if self._match(TokenType.COLON):
            stop = None if self._check(TokenType.RBRACKET, TokenType.COLON) else self._expression()
            step = None
            if self._match(TokenType.COLON):
                step = None if self._check(TokenType.RBRACKET) else self._expression()
            self._expect(TokenType.RBRACKET)
            return SliceExpr(obj=obj, start=start, stop=stop, step=step,
                             line=obj.line, col=obj.col)
        self._expect(TokenType.RBRACKET)
        return IndexExpr(obj=obj, index=start, line=obj.line, col=obj.col)

    # -----------------------------------------------------------------------
    # Primary Expressions
    # -----------------------------------------------------------------------

    def _primary(self) -> ASTNode:
        tok = self._peek()

        match tok.type:
            case TokenType.INT:
                self._advance()
                return IntLiteral(value=tok.value, line=tok.line, col=tok.col)
            case TokenType.FLOAT:
                self._advance()
                return FloatLiteral(value=tok.value, line=tok.line, col=tok.col)
            case TokenType.STRING:
                self._advance()
                return StringLiteral(value=tok.value, line=tok.line, col=tok.col)
            case TokenType.TRUE:
                self._advance()
                return BoolLiteral(value=True, line=tok.line, col=tok.col)
            case TokenType.FALSE:
                self._advance()
                return BoolLiteral(value=False, line=tok.line, col=tok.col)
            case TokenType.NONE:
                self._advance()
                return NoneLiteral(line=tok.line, col=tok.col)
            case TokenType.SELF:
                self._advance()
                return SelfExpr(line=tok.line, col=tok.col)
            case TokenType.IDENT:
                self._advance()
                return Identifier(name=tok.value, line=tok.line, col=tok.col)
            case TokenType.TENSOR:
                return self._tensor_primary()
            case TokenType.FRAME:
                self._advance()
                return Identifier(name="frame", line=tok.line, col=tok.col)
            case TokenType.GRAD:
                return self._grad_expr()
            case TokenType.TRY:
                return self._try_expr()
            case TokenType.PANIC:
                return self._panic_expr()
            case TokenType.SPAWN:
                return self._spawn_expr()
            case TokenType.CONSTRAIN:
                return self._constrain_expr()
            case TokenType.SHARED:
                return self._shared_expr()
            case TokenType.CHANNEL:
                return self._channel_expr()
            case TokenType.LPAREN:
                self._advance()
                expr = self._expression()
                self._expect(TokenType.RPAREN)
                return expr
            case TokenType.LBRACKET:
                return self._list_literal()
            case TokenType.LBRACE:
                return self._map_literal()
            case TokenType.PIPE:
                return self._lambda_expr()
            case TokenType.FN:
                return self._anonymous_fn()
            case _:
                raise self._error(f"Unexpected token: {tok.type.name} ({tok.value!r})")

    def _tensor_primary(self) -> ASTNode:
        tok = self._advance()  # tensor
        if self._check(TokenType.LBRACKET):
            self._advance()
            dtype = self._type_expr()
            dims = []
            while self._match(TokenType.COMMA):
                if self._check(TokenType.IDENT) and self._peek().value == "_":
                    dims.append(Identifier(name="_", line=self._peek().line, col=self._peek().col))
                    self._advance()
                else:
                    dims.append(self._expression())
            self._expect(TokenType.RBRACKET)
            return TensorTypeExpr(dtype=dtype, dims=dims, line=tok.line, col=tok.col)
        return Identifier(name="tensor", line=tok.line, col=tok.col)

    def _grad_expr(self) -> GradExpr:
        tok = self._advance()  # grad
        self._expect(TokenType.LPAREN)
        func = self._expression()
        order = None
        if self._match(TokenType.COMMA):
            if self._check(TokenType.IDENT) and self._peek().value == "order":
                self._advance()
                self._expect(TokenType.ASSIGN)
            order = self._expression()
        self._expect(TokenType.RPAREN)
        return GradExpr(func=func, order=order, line=tok.line, col=tok.col)

    def _try_expr(self) -> TryExpr:
        tok = self._advance()  # try
        expr = self._expression()
        return TryExpr(expr=expr, line=tok.line, col=tok.col)

    def _panic_expr(self) -> PanicExpr:
        tok = self._advance()  # panic
        self._expect(TokenType.LPAREN)
        msg = self._expression()
        self._expect(TokenType.RPAREN)
        return PanicExpr(message=msg, line=tok.line, col=tok.col)

    def _spawn_expr(self) -> SpawnExpr:
        tok = self._advance()  # spawn
        body = self._block()
        return SpawnExpr(body=body, line=tok.line, col=tok.col)

    def _constrain_expr(self) -> ConstrainExpr:
        tok = self._advance()  # constrain
        config = self._optional_kv_parens()
        body = self._block()
        return ConstrainExpr(config=config, body=body, line=tok.line, col=tok.col)

    def _shared_expr(self) -> SharedExpr:
        tok = self._advance()  # shared
        self._expect(TokenType.LPAREN)
        value = self._expression()
        self._expect(TokenType.RPAREN)
        return SharedExpr(value=value, line=tok.line, col=tok.col)

    def _channel_expr(self) -> ChannelExpr:
        tok = self._advance()  # channel
        self._expect(TokenType.LBRACKET)
        elem_type = self._type_expr()
        self._expect(TokenType.RBRACKET)
        buf_size = None
        if self._match(TokenType.LPAREN):
            buf_size = self._expression()
            self._expect(TokenType.RPAREN)
        return ChannelExpr(elem_type=elem_type, buffer_size=buf_size,
                           line=tok.line, col=tok.col)

    def _list_literal(self) -> ListLiteral:
        tok = self._advance()  # [
        elements = []
        if not self._check(TokenType.RBRACKET):
            elements.append(self._expression())
            while self._match(TokenType.COMMA):
                if self._check(TokenType.RBRACKET):
                    break
                elements.append(self._expression())
        self._expect(TokenType.RBRACKET)
        return ListLiteral(elements=elements, line=tok.line, col=tok.col)

    def _map_literal(self) -> MapLiteral:
        tok = self._advance()  # {
        keys = []
        values = []
        if not self._check(TokenType.RBRACE):
            k = self._expression()
            self._expect(TokenType.COLON)
            v = self._expression()
            keys.append(k)
            values.append(v)
            while self._match(TokenType.COMMA):
                if self._check(TokenType.RBRACE):
                    break
                k = self._expression()
                self._expect(TokenType.COLON)
                v = self._expression()
                keys.append(k)
                values.append(v)
        self._expect(TokenType.RBRACE)
        return MapLiteral(keys=keys, values=values, line=tok.line, col=tok.col)

    def _lambda_expr(self) -> LambdaExpr:
        tok = self._advance()  # |
        params = []
        if not self._check(TokenType.PIPE):
            params = self._param_list()
        self._expect(TokenType.PIPE)
        if self._check(TokenType.LBRACE):
            body_stmts = self._block()
            body = body_stmts[-1] if body_stmts else NoneLiteral()
        else:
            body = self._expression()
        return LambdaExpr(params=params, body=body, line=tok.line, col=tok.col)

    def _anonymous_fn(self) -> FnDecl:
        tok = self._advance()  # fn
        self._expect(TokenType.LPAREN)
        params = self._param_list()
        self._expect(TokenType.RPAREN)
        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._type_expr()
        body = self._block()
        return FnDecl(
            name="<anon>", params=params, body=body,
            return_type=ret_type, line=tok.line, col=tok.col,
        )
