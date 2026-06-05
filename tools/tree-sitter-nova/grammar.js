/**
 * NOVA tree-sitter grammar.
 *
 * Mirrors the surface syntax handled by the NOVA self-hosting compiler.
 * Baseline coverage: R3-era + R4 additions (structs, asm blocks, imports,
 * extern decls, string interpolation literals, hex/binary numerics) +
 * R9E (return-type annotations, ${expr} interpolation precedence).
 *
 * R24B extensions:
 *   * R17A — sum-type enums with payloads `Variant(int, str)`,
 *     `Type::Variant` constructor syntax, match-arm destructure.
 *   * R20A — postfix `?` Result-propagation operator, distinct from the
 *     ternary `cond ? a : b`.
 *   * R21A — generic enums `enum Name<T, U> { Variant(T) ... }`.
 *   * R22B — generic fns `fn name<T, U>(p: T) -> U`, function-type
 *     parameters `f: T -> U`.
 *   * R23A — generic structs `struct Name<T> { field: T }` and
 *     semicolon-separated field lists.
 *   * Named-argument call form `f(name: value)`.
 *   * `#` line comments (used throughout the cognitive-DSL examples).
 *
 * R24B also broadens coverage of long-standing NOVA syntax that R9E did
 * not yet model: `not`/`and`/`or` keyword operators, `is`/`in` type and
 * membership operators, `..` / `..=` ranges, `|>` pipe, `??` nullish
 * coalescing, octal literals, `const` declarations, slice expressions,
 * map literals, list comprehensions, multi-binding and destructure
 * `let`, spread `...args`, confidence `@` annotations, statement-
 * separator `;`, `break/continue if`, if-as-expression, match guards,
 * `is T` patterns, and trailing commas everywhere.
 *
 * R26B extensions:
 *   * R25A brace-init struct construction `Point { x: 1, y: 2 }`,
 *     including the shorthand form `Point { x, y }`, nested brace-init
 *     in lists and other brace-inits, and the field-access chain
 *     `Point { x: 1, y: 2 }.x`.
 *   * R25A destructure patterns in `let` bindings (`let Point { x, y }
 *     = p`) and match arms (`match v { Point { x: 0, y: 0 } => ... }`),
 *     including partial destructure with `..` rest, wildcard `_` fields,
 *     literal-valued fields, and binder fields.
 *   * R26A struct update-syntax `Point { x: 1, ..base }` — forward-
 *     compatible grammar support (the compiler's strict parser owns
 *     "at most one base" / "field coverage" enforcement).
 *
 * R34E extensions (this round):
 *   * R31D match expression — the `match_expression` / `match_arm` /
 *     `_pattern` rules were sketched in R17A / R25A; R34E rounds them
 *     out with a dedicated corpus test set and adds the recursive
 *     payload-binder cases (below).
 *   * R32D nested variant patterns `Some(Some(v))`, `Ok(Pair(a, b))` —
 *     `_variant_binder` is now recursive: each binder slot accepts not
 *     only an `identifier` / `_` (R17A) but ALSO another
 *     `variant_pattern` or `struct_pattern`, with no depth limit. The
 *     CST keeps the same anonymous-child shape so R17A / R25A snapshots
 *     stay byte-identical.
 *   * R32D let-destructure with variant pattern — `let Pair(a, b) = e`,
 *     `let Some(Some(v)) = e`. The `let_decl` rule gains a third pattern
 *     branch (variant_pattern), sitting at the same dynamic precedence
 *     as the struct-pattern branch (20) so the LR parser disambiguates
 *     the `let IDENT (` lookahead from a future fn-call expression head.
 *   * R32D bare-ident variant pattern — `variant_pattern` now accepts
 *     either a `path_expression` (`Option::Some`) OR a bare `identifier`
 *     (`Some`, `Ok`, `Pair`) at its `path` field. Same dynamic
 *     precedence (10) — match arms that previously parsed `Some(x)` as
 *     a call_expression now parse it as a variant_pattern. The R17A
 *     path-qualified-only shape stays byte-identical because the
 *     `path_expression` choice is still the first alternative.
 *   * R33C `if let PAT = EXPR { ... } [else ...]` — modelled as both an
 *     `if_let_statement` (statement context) and an `if_let_expression`
 *     (expression context), mirroring the if-stmt / if-expr split. The
 *     `else` branch may chain another if-let, a plain if-stmt, or a
 *     bare block. The pattern slot reuses the `_pattern` rule from
 *     match arms, so every R31D / R32D pattern shape that works at
 *     match-arm head also works at if-let head.
 *   * R33C match arm guards — the `match_arm` rule already accepted
 *     an optional `if EXPR` between the pattern and `=>` from R24B
 *     onward (no grammar change needed for R33C); R34E ships the
 *     dedicated corpus snapshot suite.
 *
 * R37B extensions (this round):
 *   * R35C closure literals `|x| x + 1`, `|x, y| { ... }`, `|| 42` —
 *     a new `closure_expr` rule sitting in `_expression`. Lookahead
 *     disambiguation vs the bitwise OR `|` / logical OR `||` binary
 *     operators relies on tree-sitter's GLR parser exploring both
 *     parses in parallel: `|` / `||` in primary position (the start
 *     of an expression) has no left operand, so `binary_expression`
 *     can't fire and the closure parse wins. After a binary LHS the
 *     `|` / `||` token is consumed by `binary_expression` first. A
 *     dynamic-precedence bump on `closure_expr` ensures the GLR parser
 *     commits to the closure parse whenever it succeeds — i.e. when
 *     a matching second `|` is found before any closing delimiter.
 *     Closure params accept an optional `: Type` annotation (parser
 *     surface only — the compiler ignores it pre-R37). Single-
 *     expression body `|x| x + 1` and block body `|x| { ... }` are
 *     both supported. Zero-param `|| 42` uses the dedicated `||`
 *     token form.
 *   * R36C tuple literals `(a, b)`, tuple types `(int, str)`, tuple
 *     destructure patterns `let (a, b) = pair`, `match v { (0, _) =>
 *     ... }`. Tree-sitter disambiguates the leading `(` between a
 *     parenthesized expression `(x)` and a tuple expression `(x, y)`
 *     via the trailing comma — the `tuple_expr` rule requires
 *     `'(' expr (',' expr)+ ','? ')'` so `(x)` cannot match. Singleton
 *     `(x,)` is out of scope (R36C deferred); the grammar parses it
 *     as a parenthesized_expression with a stray comma, which is
 *     a syntax error — the compiler-side parser also rejects this.
 *     Tuple types live alongside `_simple_type` / `function_type` in
 *     `type_expression`. Tuple patterns live alongside the other
 *     pattern shapes in `_pattern`.
 *
 * The grammar intentionally accepts more than the compiler's strict
 * recursive-descent parser — tree-sitter is a tolerant CST builder used
 * by editors for syntax highlighting, structural search, code-folding,
 * etc. Static semantics live in nova-lsp / the compiler itself.
 */

const PREC = {
  // Lower number = lower precedence.
  pipe: 0,
  ternary: 1,
  nullish: 1,
  logical_or: 2,
  logical_and: 3,
  bitwise_or: 4,
  bitwise_xor: 5,
  bitwise_and: 6,
  equality: 7,
  comparison: 8,
  membership: 8,    // `in`, `is` — sit alongside comparison.
  range: 8,         // `..`, `..=` — alongside comparison.
  shift: 9,
  additive: 10,
  multiplicative: 11,
  power: 12,
  confidence: 13,   // `expr @ score` — binds tight, treated like a postfix.
  // Postfix `?` propagation must bind tighter than the binary operators
  // (so `a? + b?` parses as `(a?) + (b?)`) and tighter than the ternary
  // (so `expr? : x` is invalid — the `?` is consumed first). It must
  // bind LOOSER than call / index / field so that `f(x)?` parses as
  // `(f(x))?`. We also keep it LOOSER than unary, so that `-x?` parses
  // as `-(x?)` — matching the compiler's `par_q_is_try_propagate`
  // behaviour when the unary operand is itself a single token.
  try_propagate: 14,
  unary: 15,
  spread: 15,
  call: 16,
  field: 17,
  index: 17,
  slice: 17,
  // Type-level `T -> U` function-type — looser than identifier so the
  // single-identifier type `T` still parses, tighter than top-level.
  fn_type: 1,
  // Path-qualified constructor `Type::Variant` — same precedence as a
  // bare identifier in expression position, but the dynamic precedence
  // is bumped over `binary_expression` so `a < Foo::Bar` does not get
  // mis-parsed as a chained comparison candidate.
  path_qualified: 2,
  // R37B: closure literal `|x| x + 1`. Static precedence is 0 (lowest
  // among prefix forms) because a closure body extends as far right
  // as possible — e.g. `|x| x + 1 * 2` parses as `|x| (x + 1 * 2)`,
  // not `(|x| x) + 1 * 2`. The single-expr body is right-associative.
  // Dynamic precedence is bumped on the rule itself so the GLR parser
  // prefers the closure parse over a hypothetical binary continuation.
  closure: 0,
  // R37B: tuple-literal precedence — sits above parenthesized_expression
  // so the GLR parser prefers `tuple_expr` when the second token is a
  // `,` (forcing the multi-element shape).
  tuple: 2,
};

function commaSep(rule) {
  return optional(commaSep1(rule));
}

function commaSep1(rule) {
  return seq(rule, repeat(seq(',', rule)));
}

function commaSepTrail(rule) {
  return seq(rule, repeat(seq(',', rule)), optional(','));
}

module.exports = grammar({
  name: 'nova',

  // Whitespace and comments are ignored between tokens.
  extras: $ => [/\s+/, $.comment],

  // The semicolon is optional in NOVA — newlines act as statement
  // terminators. To avoid LR(1) ambiguity for tree-sitter we keep
  // statements terminator-less and let the GLR parser disambiguate.
  word: $ => $.identifier,

  conflicts: $ => [
    // `a ? b : c` (ternary) vs. `a?` (try_propagate). GLR keeps both
    // parses alive until the `:` token reveals the intent.
    [$.ternary_expression, $.try_propagate_expression],
    // Inside a match arm `Option::Some(v) => ...`, the pattern can be
    // either a (call_expression Option::Some (argument_list v)) or a
    // (variant_pattern (path) v). Dynamic precedence picks variant_pattern.
    [$._expression, $._variant_binder],
    // `if cond { a } else { b }` is both an `if_statement` (statement
    // context) and an `if_expression` (expression context). Same surface
    // syntax; GLR picks based on whether the result is being consumed.
    [$.if_statement, $.if_expression],
    // R33C: `if let PAT = EXPR { ... } [else ...]` is both a statement
    // and an expression in NOVA — mirrors the if-stmt / if-expr split.
    // GLR keeps both alive until the surrounding context (used in a
    // value position vs. on its own line) disambiguates.
    [$.if_let_statement, $.if_let_expression],
    // R25A: inside a match arm, `IDENT { x, y }` could be either a
    // struct_pattern (shorthand-binder destructure) or a struct_init
    // expression that happens to contain shorthand fields. The arm's
    // `=>` token disambiguates; GLR keeps both alive until it sees it.
    [$.struct_pattern_field, $.field_init],
    // R25A: in `for x in xs { ... }` and `while cond { ... }`, the
    // identifier+brace pair could be a struct_init (xs{...}) or a
    // separate identifier + body block. We declare the conflict so
    // GLR keeps both parses alive — for_statement / while_statement
    // need the block, so struct_init falls away naturally.
    [$._expression, $.struct_init_expression],
    [$.struct_pattern, $.struct_init_expression],
  ],

  rules: {
    source_file: $ => repeat($._top_level),

    // Top-level prefers declaration constructs; we lift `prec` on the
    // declaration variants so the parser disambiguates `let` at module
    // scope from `let` inside a block.
    _top_level: $ => choice(
      prec(1, $.fn_decl),
      prec(1, $.extern_fn_decl),
      prec(1, $.import_decl),
      prec(1, $.struct_decl),
      prec(1, $.enum_decl),
      prec(1, $.const_decl),
      prec(1, $.cognitive_decl),    // mind / soul / system
      $._statement,        // includes let, if, while, for, asm, expr
    ),

    // Cognitive-DSL `mind`/`soul`/`system` declarations. R10-era
    // experimental syntax used by examples/*_demo.nova files.
    // We keep the grammar permissive — these are user-defined
    // declarative records with named sections; the compiler lowers
    // them into a series of runtime calls via a macro pass.
    cognitive_decl: $ => seq(
      field('kind', choice('mind', 'soul', 'system')),
      field('name', $.identifier),
      '{',
      repeat($.cognitive_section),
      '}',
    ),

    cognitive_section: $ => choice(
      // `nodes { Vision: perceiver, Audio: perceiver }`,
      // `channels { perception: Sense ~> Think }`,
      // `bridges { see_to_think: sensory.Vision ~> reasoning.Analyzer }`,
      // `identity { purpose: "..." }`,
      // `values { truth: "..." }`,
      // `drives { curiosity: 90 }`,
      // `feelings { focus: 80 }`,
      // `minds { sensory: Sensory }`,
      seq(
        field('name', $.identifier),
        '{',
        repeat($.cognitive_entry),
        '}',
      ),
      // `soul: Nova` — single-field section (used inside `system`).
      seq(
        field('name', $.identifier),
        ':',
        field('value', $._expression),
        optional(','),
      ),
    ),

    // Each line inside a cognitive section is one of:
    //   * `name: expr` (a tagged value),
    //   * `name { ... }` (a nested phase / sub-section with its own
    //     `name: expr` entries; used by `phases { awakening { ... } }`
    //     in examples/soul_demo.nova).
    cognitive_entry: $ => choice(
      seq(
        field('name', $.identifier),
        ':',
        field('value', $._expression),
        // Allow a trailing comma-separated continuation for entries
        // like `boosts: curiosity by 20, alertness by 30`.
        optional(seq(
          repeat(seq(',', $._expression)),
        )),
      ),
      seq(
        field('name', $.identifier),
        '{',
        repeat($.cognitive_entry),
        '}',
      ),
    ),

    // -----------------------------------------------------------------
    // enum (R17A: variants may carry payloads; R21A: type parameters)
    // -----------------------------------------------------------------

    enum_decl: $ => seq(
      'enum',
      field('name', $.identifier),
      optional(field('type_parameters', $.type_parameters)),
      '{',
      optional($.enum_variant_list),
      '}',
    ),

    enum_variant_list: $ => seq(
      $.enum_variant,
      // Variants may be separated by `,` or by nothing (newline). The
      // R17A reference test_sum_types.nova writes them on consecutive
      // lines without a trailing comma.
      repeat(seq(optional(','), $.enum_variant)),
      optional(','),
    ),

    enum_variant: $ => seq(
      field('name', $.identifier),
      optional(field('payload', $.enum_variant_payload)),
    ),

    enum_variant_payload: $ => seq(
      '(',
      commaSep1($.type_expression),
      ')',
    ),

    // R21A / R22B / R23A: shared `<T, U>` type-parameter syntax for
    // enum decls, struct decls, and fn decls.
    type_parameters: $ => seq(
      '<',
      commaSep1($.type_parameter),
      '>',
    ),

    type_parameter: $ => field('name', $.identifier),

    // -----------------------------------------------------------------
    // Declarations
    // -----------------------------------------------------------------

    import_decl: $ => seq(
      'import',
      field('path', $.string_literal),
      optional(';'),
    ),

    // `const NAME = value` — a compile-time constant. Permitted at both
    // top-level and inside fn bodies.
    const_decl: $ => seq(
      'const',
      field('name', $.identifier),
      optional(seq(':', field('type', $.type_expression))),
      '=',
      field('value', $._expression),
      optional(';'),
    ),

    extern_fn_decl: $ => seq(
      'extern',
      'fn',
      field('name', $.identifier),
      optional(field('type_parameters', $.type_parameters)),
      '(',
      field('parameters', optional($.parameter_list)),
      ')',
      optional(seq(choice('->', ':'), field('return_type', $.type_expression))),
      optional(';'),
    ),

    fn_decl: $ => seq(
      'fn',
      // Method-style decl `fn Type.method(self, ...)` — captured by
      // letting the name be a single identifier OR a `Type.method`
      // chain. The compiler folds this into a `Type.method` builtin.
      field('name', choice($.identifier, $.qualified_fn_name)),
      // R22B: optional `<T, U>` type-parameter list.
      optional(field('type_parameters', $.type_parameters)),
      '(',
      field('parameters', optional($.parameter_list)),
      ')',
      // Optional `-> Type` return-type annotation (R9E). NOVA also
      // accepts the legacy `: Type` spelling for forward compatibility
      // with older sources (cf. R22B test_generic_fn `legacy_add`).
      optional(seq(choice('->', ':'), field('return_type', $.type_expression))),
      field('body', $.block),
    ),

    // `fn Type.method(self, ...)` method-binding head — R12-era feature
    // still used widely in tests/examples (test_struct_methods.nova).
    qualified_fn_name: $ => seq(
      field('type', $.identifier),
      '.',
      field('method', $.identifier),
    ),

    parameter_list: $ => commaSepTrail($.parameter),

    parameter: $ => seq(
      field('name', $.identifier),
      // Optional `: Type` annotation, currently unused by the compiler
      // but allowed by the LSP-facing syntax.
      optional(seq(':', field('type', $.type_expression))),
      // Optional default-value: `fn greet(name, times = 1)`.
      optional(seq('=', field('default', $._expression))),
    ),

    // -----------------------------------------------------------------
    // Type expressions — bare ident, generic `T<U, V>`, qualified
    // `Type::Variant`, or function type `T -> U`. R22B extends this
    // with `T -> U` so `f: T -> U` parses inside a parameter list.
    // -----------------------------------------------------------------
    type_expression: $ => choice(
      $.function_type,
      // R37B / R36C: tuple type `(int, str)`, `(int, str, bool)`.
      // Sits at the same level as function_type so it composes inside
      // a fn return-type slot, a parameter annotation, or a let-type
      // annotation. Two-or-more elements; trailing comma accepted.
      $.tuple_type,
      $._simple_type,
    ),

    _simple_type: $ => choice(
      // Plain `T`, `int`, `MyType`...
      $.identifier,
      // Generic: `Result<int, str>`, `Box<T>`, `Result<Result<int, str>, str>`.
      $.generic_type,
      // Path: `Result::Ok`, `Option::None`.
      $.path_qualified_type,
    ),

    // R37B / R36C: tuple type `(T, U)`, `(T, U, V)`. Two-or-more
    // elements; `(T)` is intentionally NOT a tuple type — type
    // annotations like `(int)` collapse to a parenthesized type form
    // that is not separately modelled (the LR parser falls through
    // to `_simple_type` via the identifier branch). Trailing comma
    // allowed for codegen-tool convenience.
    tuple_type: $ => prec(PREC.tuple, seq(
      '(',
      $.type_expression,
      repeat1(seq(',', $.type_expression)),
      optional(','),
      ')',
    )),

    generic_type: $ => prec(1, seq(
      field('base', $.identifier),
      '<',
      commaSep1($.type_expression),
      '>',
    )),

    path_qualified_type: $ => seq(
      $.identifier,
      '::',
      $.identifier,
    ),

    // R22B: `T -> U` function-type syntax. Right-associative so
    // `T -> U -> V` parses as `T -> (U -> V)`.
    function_type: $ => prec.right(PREC.fn_type, seq(
      field('param', $._simple_type),
      '->',
      field('result', $.type_expression),
    )),

    struct_decl: $ => seq(
      'struct',
      field('name', $.identifier),
      optional(field('type_parameters', $.type_parameters)),
      '{',
      optional($.struct_field_list),
      '}',
    ),

    struct_field_list: $ => seq(
      $.struct_field,
      // R23A also accepts `;` or nothing (newline) as a field separator
      // alongside `,`. test_struct_methods.nova has each field on its
      // own line with no punctuation.
      repeat(seq(optional(choice(',', ';')), $.struct_field)),
      optional(choice(',', ';')),
    ),

    struct_field: $ => seq(
      field('name', $.identifier),
      optional(seq(':', field('type', $.type_expression))),
    ),

    // R14-era multi-binding let: `let a, b = expr, expr`. Also
    // covers single-binding let, optionally with a type annotation,
    // and the R13 destructure form `let [a, b, ...rest] = expr`.
    // Single-binding `let x = expr` keeps the legacy CST shape:
    //   (let_decl name: (identifier) value: (...))
    // Multi-binding adds extra `(identifier)` siblings under the
    // `name` field. Destructure swaps `name` for `pattern`.
    let_decl: $ => seq(
      'let',
      choice(
        seq(
          field('name', $.identifier),
          optional(seq(':', field('type', $.type_expression))),
          repeat(seq(',', field('name', $.identifier))),
          '=',
          field('value', choice($._expression, $._expression_list)),
        ),
        seq(
          field('pattern', $.list_pattern),
          '=',
          field('value', $._expression),
        ),
        // R25A: `let Point { x: px, y: py } = p` — struct destructure
        // pattern at let head. Dynamic precedence picks this over the
        // single-name reading when the identifier is followed by a
        // `{` and field-init-like contents.
        prec.dynamic(20, seq(
          field('pattern', $.struct_pattern),
          '=',
          field('value', $._expression),
        )),
        // R32D: `let Pair(a, b) = pair`, `let Some(Some(v)) = nested`,
        // `let Ok(Point { x, y }) = res`. The variant-pattern branch
        // sits at the same dynamic-precedence (20) as struct_pattern
        // so the parser commits to it over the fall-through call-
        // expression reading. Path-qualified variants
        // (`Result::Ok(v)`) and bare-identifier variants (`Some(v)`)
        // share this branch via `variant_pattern`'s own grammar.
        prec.dynamic(20, seq(
          field('pattern', $.variant_pattern),
          '=',
          field('value', $._expression),
        )),
        // R37B / R36C: `let (a, b) = pair`, `let (a, b, c) = triple`.
        // Tuple-destructure pattern at let head. Same dynamic
        // precedence as struct_pattern / variant_pattern (20) so the
        // parser commits to the tuple_pattern reading over the
        // fall-through parenthesized/tuple-expression reading.
        prec.dynamic(20, seq(
          field('pattern', $.tuple_pattern),
          '=',
          field('value', $._expression),
        )),
      ),
      optional(';'),
    ),

    list_pattern: $ => seq(
      '[',
      optional(commaSep1(choice(
        $.identifier,
        $.wildcard_pattern,
        $.rest_pattern,
      ))),
      ']',
    ),

    rest_pattern: $ => seq(
      '...',
      optional(field('name', $.identifier)),
    ),

    // R25A: struct destructure pattern `Point { x: px, y: py }` or
    // shorthand `Point { x, y }`. Used at let head, match arm head, or
    // nested inside other patterns. A trailing `..` denotes "rest of
    // fields ignored" (partial destructure). The leading type is a bare
    // identifier — generic forms are out of scope for the same reason
    // as struct_init_expression (lexer can't disambiguate `<` cheaply).
    struct_pattern: $ => prec.dynamic(20, seq(
      field('type', $.identifier),
      '{',
      optional($.struct_pattern_field_list),
      '}',
    )),

    struct_pattern_field_list: $ => seq(
      choice($.struct_pattern_field, $.rest_field_pattern),
      repeat(seq(',', choice($.struct_pattern_field, $.rest_field_pattern))),
      optional(','),
    ),

    // Each pattern-field is either:
    //   * `name: pattern`  — explicit binder (pattern can be a literal
    //     constant for match arms, or an identifier binder).
    //   * `name`           — shorthand: binds field to a local of the
    //     same name.
    struct_pattern_field: $ => choice(
      seq(
        field('name', $.identifier),
        ':',
        field('pattern', choice($._expression, $.wildcard_pattern)),
      ),
      field('name', $.identifier),
    ),

    // `..` rest pattern: matches and discards the remaining fields.
    // Distinct from list_pattern's `...rest` because struct destructure
    // doesn't bind a name to the leftovers.
    rest_field_pattern: $ => '..',

    // Comma-separated list of expressions, used as the RHS of multi-
    // binding let and multi-assign.
    _expression_list: $ => prec.right(seq(
      $._expression,
      repeat1(seq(',', $._expression)),
    )),

    // -----------------------------------------------------------------
    // asm { "..." "..." }
    // -----------------------------------------------------------------

    asm_block: $ => seq(
      'asm',
      '{',
      repeat($.string_literal),
      '}',
    ),

    // -----------------------------------------------------------------
    // Statements
    // -----------------------------------------------------------------

    block: $ => seq('{', repeat($._statement), '}'),

    _statement: $ => choice(
      $.let_decl,                  // `let` may appear inside a block
      $.const_decl,
      $.if_statement,
      $.if_let_statement,          // R33C: `if let PAT = expr { ... }`
      $.while_statement,
      $.do_while_statement,
      $.for_statement,
      $.return_statement,
      $.break_statement,
      $.continue_statement,
      $.asm_block,
      $.impl_block,
      $.labeled_statement,         // `@label while ...`
      $.assignment_statement,
      $.expression_statement,
      $.block,                     // bare block is a valid statement
    ),

    if_statement: $ => prec.right(seq(
      'if',
      field('condition', $._expression),
      field('consequence', $.block),
      optional(seq(
        'else',
        field('alternative', choice($.block, $.if_statement)),
      )),
    )),

    // R33C: `if let PAT = expr { ... } [else { ... } | else if ... | else if let ...]`.
    // The pattern slot reuses `_pattern` from match arms, so every
    // R31D / R32D pattern shape that works at match-arm head also
    // works at if-let head: wildcards, variant patterns (incl. nested
    // payload binders), struct patterns, and bare literal / identifier
    // patterns. `prec.right` resolves the dangling-else chain in favour
    // of the closest preceding `if` / `if let`, matching the compiler's
    // recursive-descent parser. The else branch may chain to another
    // if-let, a plain if-stmt, or a bare block.
    if_let_statement: $ => prec.right(seq(
      'if',
      'let',
      field('pattern', $._pattern),
      '=',
      field('value', $._expression),
      field('consequence', $.block),
      optional(seq(
        'else',
        field('alternative', choice($.block, $.if_let_statement, $.if_statement)),
      )),
    )),

    while_statement: $ => seq(
      'while',
      field('condition', $._expression),
      field('body', $.block),
      // R15-era `else` clause on while that runs when the loop exits
      // normally (no `break`). Present in test_while_else.nova.
      optional(seq('else', field('else_branch', $.block))),
    ),

    // `do { ... } while cond` — at-least-once loop
    // (test_ternary_dowhile.nova).
    do_while_statement: $ => seq(
      'do',
      field('body', $.block),
      'while',
      field('condition', $._expression),
      optional(';'),
    ),

    // `impl Type { fn method(self) { ... } ... }` — R12-era method
    // bundle. Lowers to a series of `Type.method` definitions.
    impl_block: $ => seq(
      'impl',
      field('type', $.identifier),
      '{',
      repeat($.fn_decl),
      '}',
    ),

    // `@label while ...`, `@label for ...` — labeled loops. The
    // matching `break @label` / `continue @label` uses the same
    // label syntax (already handled in break/continue statements
    // via the optional `@<ident>` token below).
    labeled_statement: $ => seq(
      field('label', $.label),
      field('statement', choice($.while_statement, $.for_statement, $.do_while_statement)),
    ),

    label: $ => seq('@', field('name', $.identifier)),

    for_statement: $ => seq(
      'for',
      field('binding', choice(
        $.identifier,
        seq($.identifier, ',', $.identifier),   // `for i, x in list`
      )),
      'in',
      field('iterable', $._expression),
      field('body', $.block),
      optional(seq('else', field('else_branch', $.block))),
    ),

    // `return` is right-associative w.r.t. its optional value so that
    // `return (a + b)` parses as `return <expr>` rather than
    // `return ; (a+b)` followed by a stray expression.
    return_statement: $ => prec.right(seq(
      'return',
      optional(field('value', $._expression)),
      optional(';'),
    )),

    // `break` / `continue` may take:
    //   * an optional `@label` target (`break @outer`),
    //   * an optional `if cond` guard (`break if i > 5`,
    //     `break @outer if inner > 2`).
    // Combined form `break @label if cond` is used in
    // test_break_continue_if.nova.
    break_statement: $ => prec.right(seq(
      'break',
      optional(field('target', $.label)),
      optional(seq('if', field('condition', $._expression))),
      optional(';'),
    )),
    continue_statement: $ => prec.right(seq(
      'continue',
      optional(field('target', $.label)),
      optional(seq('if', field('condition', $._expression))),
      optional(';'),
    )),

    // Top-level `expr` is a statement. We distinguish assignment from
    // expression so the CST highlights LHS as an l-value. The LHS can
    // also be a comma-separated list for multi-assign (`a, b = b, a`).
    assignment_statement: $ => prec.right(seq(
      field('lhs', choice($._expression, $._expression_list)),
      field('operator', choice(
        // Basic assignment + arithmetic combos.
        '=', '+=', '-=', '*=', '/=', '%=',
        // Bitwise compounds — token wrappers needed because the lexer
        // would otherwise prefer the binary `<<` / `>>` / etc. for the
        // first two chars.
        token(prec(2, '**=')),
        token(prec(2, '&=')),
        token(prec(2, '|=')),
        token(prec(2, '^=')),
        token(prec(2, '<<=')),
        token(prec(2, '>>=')),
        // Logical/nullish compound — same lexer trick.
        token(prec(2, '??=')),
        token(prec(2, '||=')),
        token(prec(2, '&&=')),
      )),
      field('rhs', choice($._expression, $._expression_list)),
      optional(';'),
    )),

    expression_statement: $ => seq(
      $._expression,
      optional(';'),
    ),

    // -----------------------------------------------------------------
    // Expressions
    // -----------------------------------------------------------------

    _expression: $ => choice(
      $.binary_expression,
      $.unary_expression,
      $.ternary_expression,
      // R20A: postfix `?` Result-propagation.
      $.try_propagate_expression,
      // `expr @ score` confidence annotation.
      $.confidence_expression,
      // `...iter` spread, used in calls and list literals.
      $.spread_expression,
      // R15-era `if cond { a } else { b }` as expression.
      $.if_expression,
      // R33C: `if let PAT = expr { ... } else { ... }` as expression.
      $.if_let_expression,
      $.match_expression,
      $.lambda_expression,
      // R37B / R35C: closure literal `|x| x + 1`, `|x, y| { ... }`,
      // `|| 42`. Listed before call_expression so the GLR parser
      // attempts the closure parse first when the leading token is
      // `|` or `||` in primary position.
      $.closure_expr,
      $.call_expression,
      // R25A: `Point { x: 1, y: 2 }` brace-init. Sits at call-level
      // precedence so `Point { x: 1 }.x` field-access chains work.
      $.struct_init_expression,
      $.index_expression,
      $.slice_expression,
      $.field_expression,
      $.parenthesized_expression,
      // R37B / R36C: tuple literal `(a, b)`. Listed after
      // parenthesized_expression so single-element `(x)` falls through
      // to parenthesized; the GLR parser keeps both parses alive until
      // the second `,` token forces tuple_expr.
      $.tuple_expr,
      $.path_expression,        // R17A: `Type::Variant` constructor base
      $.list_literal,
      $.map_literal,
      $.map_comprehension,
      $.list_comprehension,
      $.number,
      $.float,
      $.string_literal,
      $.boolean,
      $.none_literal,
      $.identifier,
    ),

    // Anonymous function: `fn(x, y) { ... }`
    lambda_expression: $ => seq(
      'fn',
      '(',
      field('parameters', optional($.parameter_list)),
      ')',
      optional(seq(choice('->', ':'), field('return_type', $.type_expression))),
      field('body', $.block),
    ),

    // R37B / R35C: closure literal.
    //
    //   closure_expr := '|' closure_params? '|' (block | _expression)
    //                |  '||' (block | _expression)
    //
    // Lookahead disambiguation vs the bitwise OR `|` / logical OR `||`
    // operators relies on tree-sitter's GLR parser exploring both
    // parses in parallel: in primary position (no LHS), `binary_expression`
    // cannot fire, so the closure parse wins. After a binary LHS the
    // `|` / `||` token is consumed by `binary_expression` first. The
    // dynamic-precedence bump on closure_expr nudges the GLR parser
    // toward the closure reading when both succeed.
    //
    // Body is right-associative so `|x| x + 1` parses as `|x| (x + 1)`,
    // not `(|x| x) + 1`. Block bodies (`|x| { ... }`) are unambiguous —
    // the `{` token unambiguously starts a block.
    //
    // Zero-param `|| 42` uses the `||` token directly (same lexeme as
    // logical-OR); in primary position the parser disambiguates by
    // absence of a LHS.
    closure_expr: $ => prec.dynamic(30, prec.right(PREC.closure, choice(
      // Standard form: `| params | body`.
      seq(
        '|',
        field('parameters', optional($.closure_params)),
        '|',
        field('body', choice($.block, $._expression)),
      ),
      // Zero-param shorthand: `|| body`.
      seq(
        '||',
        field('body', choice($.block, $._expression)),
      ),
    ))),

    closure_params: $ => seq(
      $.closure_param,
      repeat(seq(',', $.closure_param)),
      optional(','),
    ),

    closure_param: $ => seq(
      field('name', $.identifier),
      // Optional `: Type` annotation — parser surface only; the
      // compiler's R35C lowering ignores the annotation.
      optional(seq(':', field('type', $.type_expression))),
    ),

    // R37B / R36C: tuple literal `(a, b)`. The `repeat1` of `(',' expr)`
    // enforces a minimum of TWO elements, so `(x)` cannot match
    // tuple_expr and falls through to parenthesized_expression.
    // Trailing comma allowed.
    tuple_expr: $ => prec(PREC.tuple, seq(
      '(',
      $._expression,
      repeat1(seq(',', $._expression)),
      optional(','),
      ')',
    )),

    // R15-era `if cond { ... } else { ... }` used in expression
    // position. We model it explicitly so the CST can distinguish
    // an if-as-statement from an if-as-expression at editor level.
    if_expression: $ => prec.right(seq(
      'if',
      field('condition', $._expression),
      field('consequence', $.block),
      'else',
      field('alternative', choice($.block, $.if_expression)),
    )),

    // R33C: `if let PAT = expr { ... } else { ... }` in expression
    // position. The `else` branch is REQUIRED for the expression form
    // (so the result is total) — the if-let-statement form above allows
    // omitting the else. The else alternative may chain another
    // if-let-expression or a plain if-expression (`else if cond` /
    // `else if let pat = ...`). prec.right resolves the dangling-else
    // chain to the closest preceding `if`.
    if_let_expression: $ => prec.right(seq(
      'if',
      'let',
      field('pattern', $._pattern),
      '=',
      field('value', $._expression),
      field('consequence', $.block),
      'else',
      field('alternative', choice($.block, $.if_let_expression, $.if_expression)),
    )),

    // R17A: `match expr { pattern => expr ... }` — both expression and
    // statement form. Whitespace-separated arms are accepted. Patterns
    // may now also destructure enum variants: `Option::Some(v)`.
    match_expression: $ => seq(
      'match',
      field('scrutinee', $._expression),
      '{',
      repeat($.match_arm),
      '}',
    ),

    match_arm: $ => prec.dynamic(100, seq(
      field('pattern', $._pattern),
      // Optional `if cond` guard: `_ if x > 0 => ...`.
      optional(seq('if', field('guard', $._expression))),
      '=>',
      field('value', choice($._expression, $.block)),
      optional(','),
    )),

    _pattern: $ => choice(
      $.wildcard_pattern,
      // `is int` / `is str` type patterns. Higher dynamic precedence
      // so the parser prefers it over starting a new binary `value is T`.
      $.is_pattern,
      // R17A destructure pattern: `Option::Some(v)`, `Shape::Rect(w, h)`,
      // `Tree::Leaf(x)`.
      $.variant_pattern,
      // R25A: `Point { x: 0, y: 0 }`, `Point { x, y }`,
      // `Point { x: 7, y: _ }`. Sits at higher dynamic precedence than
      // the generic `_expression` fallback so a `Type { ... }` arm
      // resolves to struct_pattern, not struct_init_expression.
      $.struct_pattern,
      // R37B / R36C: tuple destructure pattern `(a, b)`, `((a, b), c)`
      // nested. Sub-patterns reuse the full `_pattern` rule.
      //
      // CAVEAT: In match-arm head, `(0, _) =>` parses as `(tuple_expr
      // (number) (identifier))` rather than `(tuple_pattern (number)
      // (wildcard_pattern))` because the LR parser commits to the
      // `_pattern → _expression` reading at the leading `(` token
      // before the inner `_` token is seen. Editor-side highlighting
      // and structural search still work — the inner positions are
      // captured as expressions rather than patterns. For at-let-head
      // contexts (`let (a, b) = pair`), the let_decl rule has a
      // dedicated tuple_pattern branch with prec.dynamic(20) that
      // wins, so destructure-let works as expected.
      $.tuple_pattern,
      // Other patterns: bare literal / identifier / path constructor
      // with no binders. We re-use the expression rule so number/string
      // literals and `Option::None` (no parens) all parse as patterns.
      $._expression,
    ),

    is_pattern: $ => prec.dynamic(50, seq(
      'is',
      field('type', $.identifier),
    )),

    // R17A: `Type::Variant(binders, ...)` destructure pattern. Dynamic
    // precedence is bumped over the generic call_expression so that
    // match arm `Option::Some(v)` is recognised as a destructure
    // pattern rather than a regular constructor call.
    // R17A path-qualified form `Type::Variant(...)` keeps the same
    // CST shape (path: (path_expression ...)). R32D bare-identifier
    // form `Some(...)` / `Ok(...)` / `Pair(...)` also accepted at the
    // same dynamic precedence; the path field carries the identifier
    // directly so consumers branch on the inner node type.
    variant_pattern: $ => prec.dynamic(10, seq(
      field('path', choice($.path_expression, $.identifier)),
      '(',
      optional(commaSep1($._variant_binder)),
      ')',
    )),

    // Each binder is either a name (`v`, `w`), the wildcard `_`, or
    // — R32D — another nested pattern. The recursive shapes allow
    // `Some(Some(v))`, `Ok(Pair(a, b))`, `Result::Ok(Point { x, y })`
    // at any depth. Identifier / `_` binders keep the same anonymous-
    // child position they had in R17A, so the R17A / R25A corpus
    // snapshots remain byte-identical.
    _variant_binder: $ => choice(
      $.variant_pattern,
      $.struct_pattern,
      $.identifier,
      $.wildcard_pattern,
    ),

    wildcard_pattern: $ => '_',

    // R37B / R36C: tuple destructure pattern. Two-or-more elements,
    // trailing comma accepted. Sub-patterns reuse the full `_pattern`
    // rule, so nested tuple / variant / struct patterns compose.
    // Singleton `(x,)` is out of scope (R36C deferred) — the rule
    // demands at least one comma followed by a second pattern.
    //
    // Used by:
    //   * let_decl's tuple_pattern branch with prec.dynamic(20) —
    //     `let (a, b) = pair` resolves cleanly to tuple_pattern.
    //   * _pattern's choice — `match v { (a, b) => ... }`. See the
    //     CAVEAT on the _pattern rule above: when the inner positions
    //     contain a wildcard `_`, the LR parser commits to the
    //     `_pattern → _expression → tuple_expr` reading first; the
    //     CST captures inner positions as expressions rather than
    //     patterns. Editor-side highlighting and structural search
    //     still work; semantic checking is the LSP / compiler's job.
    tuple_pattern: $ => prec.dynamic(20, seq(
      '(',
      $._pattern,
      repeat1(seq(',', $._pattern)),
      optional(','),
      ')',
    )),

    parenthesized_expression: $ => seq('(', $._expression, ')'),

    binary_expression: $ => {
      const table = [
        ['|>', PREC.pipe],
        ['??', PREC.nullish],
        ['||', PREC.logical_or],
        ['or', PREC.logical_or],
        ['&&', PREC.logical_and],
        ['and', PREC.logical_and],
        ['|',  PREC.bitwise_or],
        ['^',  PREC.bitwise_xor],
        ['&',  PREC.bitwise_and],
        ['==', PREC.equality],
        ['!=', PREC.equality],
        ['<',  PREC.comparison],
        ['<=', PREC.comparison],
        ['>',  PREC.comparison],
        ['>=', PREC.comparison],
        ['in', PREC.membership],
        ['is', PREC.membership],
        ['..', PREC.range],
        ['..=', PREC.range],
        ['<<', PREC.shift],
        ['>>', PREC.shift],
        ['+',  PREC.additive],
        ['-',  PREC.additive],
        ['*',  PREC.multiplicative],
        ['/',  PREC.multiplicative],
        ['%',  PREC.multiplicative],
        ['**', PREC.power],
        // R10-era cognitive-DSL flow operators. They share precedence
        // with the pipe so chains like `sig ~> a ~> b` parse left-to-
        // right just like `5 |> f |> g`.
        ['~>',  PREC.pipe],   // forward flow
        ['<~',  PREC.pipe],   // backward flow / reflection
        ['~~>', PREC.pipe],   // tentative flow
        ['<~~', PREC.pipe],
        ['=>>', PREC.pipe],   // broadcast
        ['<<=', PREC.pipe],   // collect
        ['<<~', PREC.pipe],
        ['<=>', PREC.pipe],   // bidirectional
        ['|~>', PREC.pipe],   // filtered flow
      ];

      const standard = table.map(([op, prec_level]) =>
        prec.left(prec_level, seq(
          field('left', $._expression),
          field('operator', op),
          field('right', $._expression),
        )),
      );

      // Two-token `not in` membership operator (test_not_in.nova). We
      // accept the keywords as a sequence — tree-sitter can't easily
      // fuse non-adjacent words into one token without an external
      // scanner. The CST captures them as two adjacent terminals under
      // the operator field via the `not_in_operator` wrapper node.
      const not_in_form = prec.left(PREC.membership, seq(
        field('left', $._expression),
        field('operator', alias(seq('not', 'in'), $.not_in_operator)),
        field('right', $._expression),
      ));

      return choice(...standard, not_in_form);
    },

    not_in_operator: $ => seq('not', 'in'),

    // R5-era `not` keyword unary, alongside `!`, `-`, `~`. Also `+x`
    // (unary plus) for completeness.
    unary_expression: $ => prec(PREC.unary, seq(
      field('operator', choice('!', '-', '~', '+', 'not')),
      field('operand', $._expression),
    )),

    // Spread `...expr` — used as a call argument or list-literal item.
    spread_expression: $ => prec(PREC.spread, seq(
      '...',
      field('value', $._expression),
    )),

    // Confidence annotation `expr @ score` (test_confidence.nova). The
    // compiler lowers this to a `[value, score]` tuple at codegen time.
    confidence_expression: $ => prec.left(PREC.confidence, seq(
      field('value', $._expression),
      '@',
      field('score', $._expression),
    )),

    // Classic ternary `cond ? then : alt`. Sits at the same static
    // precedence as the binary postfix `?` (try_propagate) so the GLR
    // parser keeps both alternatives alive until the `:` token
    // (ternary) or end-of-expression (try_propagate) disambiguates.
    // Dynamic precedence 10 > 1 picks the ternary when both succeed.
    ternary_expression: $ => prec.dynamic(10, prec.right(PREC.ternary, seq(
      field('condition', $._expression),
      '?',
      field('consequence', $._expression),
      ':',
      field('alternative', $._expression),
    ))),

    // R20A: postfix `?` Result-propagation operator.
    // `expr?` is shorthand for `match expr { Ok(v) => v, Err(e) => return Err(e) }`.
    try_propagate_expression: $ => prec.dynamic(1, prec(PREC.ternary, seq(
      field('value', $._expression),
      '?',
    ))),

    call_expression: $ => prec(PREC.call, seq(
      field('function', choice(
        $.identifier,
        $.field_expression,
        $.path_expression,        // R17A: `Option::Some(42)`
      )),
      '(',
      field('arguments', optional($.argument_list)),
      ')',
    )),

    // R5-era named-argument call form: `greet(name: "Alice", greeting: "Hello")`.
    // Names are documentation-only — the compiler binds positionally.
    // Trailing comma allowed (test_trailing_comma.nova).
    argument_list: $ => seq(
      choice($.named_argument, $._expression),
      repeat(seq(',', choice($.named_argument, $._expression))),
      optional(','),
    ),

    named_argument: $ => prec(2, seq(
      field('name', $.identifier),
      ':',
      field('value', $._expression),
    )),

    // R25A: brace-init struct construction `Foo { field: val, ... }`.
    // The type prefix is a bare identifier (`Point`, `Box`). Tree-sitter
    // cannot disambiguate the generic form `Box<int> { value: 42 }`
    // from the binary-comparison expression `a < b` without an external
    // scanner — the `<` token would force LR conflict resolution. The
    // compiler's strict parser handles the generic case; editor-side
    // syntax highlighting on the generic form will see two adjacent
    // exprs (a binary_expression `Box < int` and a map_literal
    // `{ value: 42 }`) which still highlight reasonably.
    //
    // We use *dynamic* precedence rather than static prec(PREC.call),
    // so the parser doesn't commit to struct_init too early. In contexts
    // like `for x in xs { ... }`, both parses (xs as identifier + block,
    // or xs as struct_init prefix) run in parallel; whichever completes
    // a valid statement parse wins.
    struct_init_expression: $ => prec.dynamic(5, seq(
      field('type', $.identifier),
      '{',
      optional($.field_init_list),
      '}',
    )),

    field_init_list: $ => seq(
      choice($.field_init, $.struct_update_base),
      repeat(seq(',', choice($.field_init, $.struct_update_base))),
      optional(','),
    ),

    field_init: $ => choice(
      seq(
        field('name', $.identifier),
        ':',
        field('value', $._expression),
      ),
      // R25A shorthand: `Point { x, y }` is sugar for `Point { x: x, y: y }`.
      // We capture the same `name` field so editor highlights and the LSP
      // see consistent CST shape.
      field('name', $.identifier),
    ),

    // R26A: struct update-syntax base — `Foo { f: 1, ..base }`. The
    // remaining fields are taken from `base`. Grammar-side support is
    // forward-compatible with R25A brace-init; the compiler's strict
    // parser enforces "at most one base" and the field-coverage rules.
    struct_update_base: $ => seq(
      '..',
      field('base', $._expression),
    ),

    index_expression: $ => prec(PREC.index, seq(
      field('object', $._expression),
      '[',
      field('index', $._expression),
      ']',
    )),

    // R12-era slice `xs[start:end]` — extends index syntax.
    slice_expression: $ => prec(PREC.slice, seq(
      field('object', $._expression),
      '[',
      optional(field('start', $._expression)),
      ':',
      optional(field('end', $._expression)),
      optional(seq(':', optional(field('step', $._expression)))),
      ']',
    )),

    field_expression: $ => prec(PREC.field, seq(
      field('object', $._expression),
      '.',
      field('field', $.identifier),
    )),

    // R17A: `Type::Variant` qualified constructor / path expression.
    path_expression: $ => prec(PREC.path_qualified, seq(
      field('type', $.identifier),
      '::',
      field('member', $.identifier),
    )),

    list_literal: $ => seq(
      '[',
      optional(seq(
        $._expression,
        repeat(seq(',', $._expression)),
        optional(','),
      )),
      ']',
    ),

    // R14-era list comprehension `[expr for x in iter [if cond]]`.
    list_comprehension: $ => seq(
      '[',
      field('expression', $._expression),
      'for',
      field('binding', choice(
        $.identifier,
        seq($.identifier, ',', $.identifier),
      )),
      'in',
      field('iterable', $._expression),
      optional(seq('if', field('condition', $._expression))),
      ']',
    ),

    // R13-era map literal `{key: value, ...}`. Empty `{}` is also a
    // map (the compiler distinguishes empty-map vs. empty-block by
    // context); we accept both. The tree-sitter parse defaults to
    // map_literal since the inner shape gives it away.
    map_literal: $ => prec(2, seq(
      '{',
      optional(seq(
        $.map_entry,
        repeat(seq(',', $.map_entry)),
        optional(','),
      )),
      '}',
    )),

    map_entry: $ => seq(
      field('key', $._expression),
      ':',
      field('value', $._expression),
    ),

    // R14-era map comprehension `{k: v for x in iter [if cond]}`
    // (test_map_comp.nova).
    map_comprehension: $ => prec(3, seq(
      '{',
      field('key', $._expression),
      ':',
      field('value', $._expression),
      'for',
      field('binding', choice(
        $.identifier,
        seq($.identifier, ',', $.identifier),
      )),
      'in',
      field('iterable', $._expression),
      optional(seq('if', field('condition', $._expression))),
      '}',
    )),

    // -----------------------------------------------------------------
    // Literals
    // -----------------------------------------------------------------

    // String literal. We support two shapes:
    //   * A plain string (no `${...}`) is matched as a single
    //     atomic token, so the lexer NEVER consults the `extras`
    //     (whitespace + comments) inside a quoted body. That
    //     protects strings like `"#"` and `print("# done")` from
    //     having their inner `#` mis-tokenised as a `#` line
    //     comment.
    //   * A string with `${expr}` interpolation is modelled as
    //     `"` + interleaved segments + `"`, so the inner
    //     expression can use the full NOVA grammar.
    string_literal: $ => choice(
      // Interpolated form has higher precedence so any `"..."` with
      // an internal `${expr}` or `\n` escape goes through the
      // structured rule below.
      prec(2, $._interpolated_string),
      $._plain_string,
    ),

    // Plain string (no escapes, no interpolation). Matched as a
    // single atomic token so the lexer never consults `extras`
    // inside the body — that protects strings like `"#"` from
    // having their inner `#` mis-tokenised as a `#` comment. We
    // explicitly disallow `\` so any escape-bearing string falls
    // through to `_interpolated_string`. For `$`, plain strings
    // accept `$` as a body character only when it's NOT followed
    // by `{` (which would start an interpolation). The trailing
    // `$` before the closing `"` is also accepted via the
    // single-char `$` alternative.
    _plain_string: $ => token(seq(
      '"',
      repeat(choice(
        /[^"\\$]+/,
        // `$<char>` for any non-`{`, non-`"` char.
        /\$[^{"]/,
      )),
      // Optional trailing `$` (no follow char) before the closing quote.
      optional('$'),
      '"',
    )),

    _interpolated_string: $ => seq(
      '"',
      repeat1(choice(
        $.escape_sequence,
        $.interpolation,
        $._string_content,
      )),
      token.immediate('"'),
    ),

    // String content: anything except `"`, `\`, or the start of an
    // interpolation.
    _string_content: $ => token.immediate(choice(
      /[^"\\$]+/,
      /\$[^{"\\]/,
      /\$/,
    )),

    escape_sequence: $ => token.immediate(seq(
      '\\',
      choice(
        /[nrt0\\"']/,
        /x[0-9a-fA-F]{2}/,
      ),
    )),

    // ${ expr } style interpolation as seen in showcase.nova. The
    // body `expr` IS parsed against the full grammar (so extras
    // apply inside `${...}`), but the surrounding `${` and `}`
    // tokens are immediate so they can't be split by whitespace.
    interpolation: $ => seq(
      token.immediate(prec(2, '${')),
      $._expression,
      '}',
    ),

    // Numbers: decimal, hex (0x...), octal (0o...), binary (0b...).
    // Underscores allowed inside any base.
    number: $ => token(choice(
      /0[xX][0-9a-fA-F_]+/,
      /0[oO][0-7_]+/,
      /0[bB][01_]+/,
      /[0-9][0-9_]*/,
    )),

    // Decimal float: at least one digit, dot, at least one digit.
    float: $ => token(/[0-9][0-9_]*\.[0-9][0-9_]*/),

    boolean: $ => choice('true', 'false'),
    none_literal: $ => choice('none', 'nil', 'null'),

    identifier: $ => /[A-Za-z_][A-Za-z0-9_]*/,

    // -----------------------------------------------------------------
    // Comments — line `//`, line `#` (used in cognitive-DSL files),
    // and block `/* */`.
    // -----------------------------------------------------------------

    comment: $ => token(choice(
      seq('//', /[^\n]*/),
      // `#` line comment — supports the shell-style comments used in
      // many tests. Tree-sitter cannot easily make `#` context-
      // sensitive (rejected inside strings); however the
      // `_plain_string` token rule is matched as a whole, so a `#`
      // inside a quoted string is captured as part of the string
      // token first.
      seq('#',  /[^\n]*/),
      // `--` line comment used in some cognitive-DSL examples
      // (examples/basic_mind.nova).
      seq('--', /[^\n]*/),
      seq(
        '/*',
        /[^*]*\*+([^/*][^*]*\*+)*/,
        '/',
      ),
    )),
  },
});
