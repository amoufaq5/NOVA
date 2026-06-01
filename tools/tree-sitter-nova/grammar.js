/**
 * NOVA tree-sitter grammar.
 *
 * Mirrors the surface syntax handled by the NOVA self-hosting compiler
 * (R3-era + a handful of R4 additions: structs, asm blocks, imports,
 * extern decls, string interpolation literals, hex/binary numerics).
 *
 * The grammar intentionally accepts more than the compiler's strict
 * recursive-descent parser — tree-sitter is a tolerant CST builder used
 * by editors for syntax highlighting, structural search, code-folding,
 * etc. Static semantics live in nova-lsp / the compiler itself.
 */

const PREC = {
  // Lower number = lower precedence.
  ternary: 1,
  logical_or: 2,
  logical_and: 3,
  bitwise_or: 4,
  bitwise_xor: 5,
  bitwise_and: 6,
  equality: 7,
  comparison: 8,
  shift: 9,
  additive: 10,
  multiplicative: 11,
  unary: 12,
  call: 13,
  field: 14,
  index: 14,
};

function commaSep(rule) {
  return optional(commaSep1(rule));
}

function commaSep1(rule) {
  return seq(rule, repeat(seq(',', rule)));
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
    // `Identifier(` is both a call expression and (in struct contexts)
    // a struct constructor. Same surface syntax — tree-sitter will pick
    // the shorter parse via dynamic precedence in call_expression.
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
      $._statement,        // includes let, if, while, for, asm, expr
    ),

    // -----------------------------------------------------------------
    // enum (per-task: NOVA has a small enum form used in examples)
    // -----------------------------------------------------------------

    enum_decl: $ => seq(
      'enum',
      field('name', $.identifier),
      '{',
      optional(seq(
        $.identifier,
        repeat(seq(',', $.identifier)),
        optional(','),
      )),
      '}',
    ),

    // -----------------------------------------------------------------
    // Declarations
    // -----------------------------------------------------------------

    import_decl: $ => seq(
      'import',
      field('path', $.string_literal),
      optional(';'),
    ),

    extern_fn_decl: $ => seq(
      'extern',
      'fn',
      field('name', $.identifier),
      '(',
      field('parameters', optional($.parameter_list)),
      ')',
      optional(seq('->', field('return_type', $.type_expression))),
      optional(';'),
    ),

    fn_decl: $ => seq(
      'fn',
      field('name', $.identifier),
      '(',
      field('parameters', optional($.parameter_list)),
      ')',
      // Optional `-> Type` return-type annotation. The compiler doesn't
      // require it today but the task spec and forward-looking syntax
      // (cf. nova-lsp signature help) reserve it.
      optional(seq('->', field('return_type', $.type_expression))),
      field('body', $.block),
    ),

    parameter_list: $ => commaSep1($.parameter),

    parameter: $ => seq(
      field('name', $.identifier),
      // Optional `: Type` annotation, currently unused by the compiler
      // but allowed by the LSP-facing syntax.
      optional(seq(':', field('type', $.type_expression))),
    ),

    type_expression: $ => choice(
      $.identifier,
      // Arrays / lists / generics — accept a permissive form.
      seq($.identifier, '<', commaSep($.type_expression), '>'),
    ),

    struct_decl: $ => seq(
      'struct',
      field('name', $.identifier),
      '{',
      optional($.struct_field_list),
      '}',
    ),

    struct_field_list: $ => seq(
      $.struct_field,
      repeat(seq(',', $.struct_field)),
      optional(','),
    ),

    struct_field: $ => seq(
      field('name', $.identifier),
      optional(seq(':', field('type', $.type_expression))),
    ),

    let_decl: $ => seq(
      'let',
      field('name', $.identifier),
      optional(seq(':', field('type', $.type_expression))),
      '=',
      field('value', $._expression),
      optional(';'),
    ),

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
      $.if_statement,
      $.while_statement,
      $.for_statement,
      $.return_statement,
      $.break_statement,
      $.continue_statement,
      $.asm_block,
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

    while_statement: $ => seq(
      'while',
      field('condition', $._expression),
      field('body', $.block),
    ),

    for_statement: $ => seq(
      'for',
      field('binding', choice(
        $.identifier,
        seq($.identifier, ',', $.identifier),   // `for i, x in list`
      )),
      'in',
      field('iterable', $._expression),
      field('body', $.block),
    ),

    // `return` is right-associative w.r.t. its optional value so that
    // `return (a + b)` parses as `return <expr>` rather than
    // `return ; (a+b)` followed by a stray expression.
    return_statement: $ => prec.right(seq(
      'return',
      optional(field('value', $._expression)),
      optional(';'),
    )),

    break_statement: $ => seq('break', optional(';')),
    continue_statement: $ => seq('continue', optional(';')),

    // Top-level `expr` is a statement. We distinguish assignment from
    // expression so the CST highlights LHS as an l-value.
    assignment_statement: $ => prec.right(seq(
      field('lhs', $._expression),
      field('operator', choice('=', '+=', '-=', '*=', '/=', '%=')),
      field('rhs', $._expression),
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
      $.match_expression,
      $.lambda_expression,
      $.call_expression,
      $.index_expression,
      $.field_expression,
      $.parenthesized_expression,
      $.list_literal,
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
      optional(seq('->', field('return_type', $.type_expression))),
      field('body', $.block),
    ),

    // `match expr { pattern => expr ... }` — both expression and
    // statement form. Whitespace-separated arms are accepted.
    match_expression: $ => seq(
      'match',
      field('scrutinee', $._expression),
      '{',
      repeat($.match_arm),
      '}',
    ),

    match_arm: $ => seq(
      field('pattern', choice($._expression, $.wildcard_pattern)),
      '=>',
      field('value', choice($._expression, $.block)),
      optional(','),
    ),

    wildcard_pattern: $ => '_',

    parenthesized_expression: $ => seq('(', $._expression, ')'),

    binary_expression: $ => {
      const table = [
        ['||', PREC.logical_or],
        ['&&', PREC.logical_and],
        ['|',  PREC.bitwise_or],
        ['^',  PREC.bitwise_xor],
        ['&',  PREC.bitwise_and],
        ['==', PREC.equality],
        ['!=', PREC.equality],
        ['<',  PREC.comparison],
        ['<=', PREC.comparison],
        ['>',  PREC.comparison],
        ['>=', PREC.comparison],
        ['<<', PREC.shift],
        ['>>', PREC.shift],
        ['+',  PREC.additive],
        ['-',  PREC.additive],
        ['*',  PREC.multiplicative],
        ['/',  PREC.multiplicative],
        ['%',  PREC.multiplicative],
      ];

      return choice(...table.map(([op, prec_level]) =>
        prec.left(prec_level, seq(
          field('left', $._expression),
          field('operator', op),
          field('right', $._expression),
        )),
      ));
    },

    unary_expression: $ => prec(PREC.unary, seq(
      field('operator', choice('!', '-', '~')),
      field('operand', $._expression),
    )),

    ternary_expression: $ => prec.right(PREC.ternary, seq(
      field('condition', $._expression),
      '?',
      field('consequence', $._expression),
      ':',
      field('alternative', $._expression),
    )),

    call_expression: $ => prec(PREC.call, seq(
      field('function', choice($.identifier, $.field_expression)),
      '(',
      field('arguments', optional($.argument_list)),
      ')',
    )),

    argument_list: $ => commaSep1($._expression),

    index_expression: $ => prec(PREC.index, seq(
      field('object', $._expression),
      '[',
      field('index', $._expression),
      ']',
    )),

    field_expression: $ => prec(PREC.field, seq(
      field('object', $._expression),
      '.',
      field('field', $.identifier),
    )),

    list_literal: $ => seq(
      '[',
      optional(commaSep1($._expression)),
      ']',
    ),

    // -----------------------------------------------------------------
    // Literals
    // -----------------------------------------------------------------

    string_literal: $ => seq(
      '"',
      repeat(choice(
        $.escape_sequence,
        $.interpolation,
        $._string_content,
      )),
      '"',
    ),

    // String content: anything except `"`, `\`, or the start of an
    // interpolation. We use two patterns so that a bare `$` (e.g. a
    // currency sign in `"$1000"`) doesn't get mis-tokenised as the
    // beginning of an interpolation marker. The trailing `$` before
    // the closing quote (e.g. `"price = $"`) is captured as a
    // standalone `$` chunk. The interpolation `${...}` token (below)
    // has its own higher precedence so it wins the lexer race against
    // a lone `$`.
    _string_content: $ => token.immediate(choice(
      /[^"\\$]+/,
      // `$` followed by any non-{/non-quote/non-backslash char keeps
      // the `$` as plain text; `${` is captured separately as the
      // interpolation token below.
      /\$[^{"\\]/,
      // Bare `$` (when the next char is `"`, `\`, or end of string).
      // The interpolation rule's `token.immediate('${')` has higher
      // intrinsic precedence (longer match) so `${` is not consumed
      // here.
      /\$/,
    )),

    escape_sequence: $ => token.immediate(seq(
      '\\',
      choice(
        /[nrt0\\"']/,
        /x[0-9a-fA-F]{2}/,
      ),
    )),

    // ${ expr } style interpolation as seen in showcase.nova. Higher
    // precedence than `_string_content` so that the lexer picks
    // `${` over a bare `$` when both could match.
    interpolation: $ => seq(
      token.immediate(prec(2, '${')),
      $._expression,
      '}',
    ),

    // Numbers: decimal, hex (0x...), binary (0b...). Underscores allowed.
    number: $ => token(choice(
      /0[xX][0-9a-fA-F_]+/,
      /0[bB][01_]+/,
      /[0-9][0-9_]*/,
    )),

    // Decimal float: at least one digit, dot, at least one digit.
    float: $ => token(/[0-9][0-9_]*\.[0-9][0-9_]*/),

    boolean: $ => choice('true', 'false'),
    none_literal: $ => 'none',

    identifier: $ => /[A-Za-z_][A-Za-z0-9_]*/,

    // -----------------------------------------------------------------
    // Comments — line `//` and block `/* */`
    // -----------------------------------------------------------------

    comment: $ => token(choice(
      seq('//', /[^\n]*/),
      seq(
        '/*',
        /[^*]*\*+([^/*][^*]*\*+)*/,
        '/',
      ),
    )),
  },
});
