; tree-sitter highlights for NOVA.
;
; Compatible with the standard capture names used by Neovim's
; `nvim-treesitter`, Helix, and the GitHub linguist tree-sitter
; highlighter. Each capture maps to a syntax-highlighting scope.

; -----------------------------------------------------------------
; Keywords
; -----------------------------------------------------------------

[
  "fn"
  "let"
  "const"
  "extern"
  "struct"
  "enum"
  "import"
  "impl"
  "asm"
  "mind"
  "soul"
  "system"
] @keyword

[
  "if"
  "else"
  "while"
  "do"
  "for"
  "in"
  "return"
  "break"
  "continue"
  "match"
] @keyword.control

; Keyword-style operators
[
  "and"
  "or"
  "not"
  "is"
] @keyword.operator

; -----------------------------------------------------------------
; Identifiers & functions
; -----------------------------------------------------------------

(fn_decl name: (identifier) @function)
(extern_fn_decl name: (identifier) @function)
(lambda_expression) @function

; `fn Type.method(...)` — highlight method name as function, type as type.
(fn_decl
  name: (qualified_fn_name
    type: (identifier) @type
    method: (identifier) @function))

(call_expression
  function: (identifier) @function.call)

(call_expression
  function: (field_expression
    field: (identifier) @function.method.call))

; `Type::Variant(...)` — highlight type as type, variant as constructor.
(call_expression
  function: (path_expression
    type: (identifier) @type
    member: (identifier) @function.constructor))

(path_expression
  type: (identifier) @type
  member: (identifier) @constructor)

(parameter (identifier) @variable.parameter)
(parameter (type_expression (identifier) @type))

; Named arguments: highlight name as parameter, value through normal rules.
(named_argument
  name: (identifier) @variable.parameter)

(struct_decl name: (identifier) @type)
(enum_decl name: (identifier) @type)
(enum_variant name: (identifier) @constructor)
(impl_block type: (identifier) @type)
(struct_field name: (identifier) @property)
(field_expression field: (identifier) @property)

; R25A: brace-init struct construction `Point { x: 1, y: 2 }`.
; The leading type is a constructor-style type reference; field names
; highlight as properties (shorthand-binder form drops the value half).
(struct_init_expression type: (identifier) @type)
(field_init name: (identifier) @property)

; R25A: struct destructure pattern `let Point { x, y } = p`,
; `match v { Point { x: 0, y: 0 } => ... }`. Type highlights as type;
; field names as properties; rest-pattern `..` as a punctuation special.
(struct_pattern type: (identifier) @type)
(struct_pattern_field name: (identifier) @property)
(rest_field_pattern) @punctuation.special

; Type parameters
(type_parameter name: (identifier) @type.parameter)

; Generic type references
(generic_type base: (identifier) @type)
(path_qualified_type) @type

; Map entries
(map_entry key: (string_literal) @property)

; Cognitive DSL
(cognitive_decl name: (identifier) @type)
(cognitive_section name: (identifier) @keyword.section)
(cognitive_entry name: (identifier) @property)

; Labels
(label name: (identifier) @label)

; -----------------------------------------------------------------
; Literals
; -----------------------------------------------------------------

(string_literal) @string
(escape_sequence) @string.escape
(interpolation "${" @punctuation.special)
(interpolation "}" @punctuation.special)

(number) @number
(float) @number.float
(boolean) @boolean
(none_literal) @constant.builtin

; -----------------------------------------------------------------
; Comments
; -----------------------------------------------------------------

(comment) @comment

; -----------------------------------------------------------------
; Operators & punctuation
; -----------------------------------------------------------------

[
  "+"
  "-"
  "*"
  "/"
  "%"
  "**"
  "=="
  "!="
  "<"
  "<="
  ">"
  ">="
  "&&"
  "||"
  "!"
  "&"
  "|"
  "^"
  "~"
  "<<"
  ">>"
  "="
  "+="
  "-="
  "*="
  "/="
  "%="
  "?"
  "=>"
  "->"
  "::"
  ".."
  "..="
  "|>"
  "??"
  "@"
  ; Flow operators
  "~>"
  "<~"
  "~~>"
  "<~~"
  "=>>"
  "<<~"
  "<=>"
  "|~>"
] @operator

; Return-type annotations in function signatures.
(fn_decl
  return_type: (type_expression
    (identifier) @type))

(extern_fn_decl
  return_type: (type_expression
    (identifier) @type))

(lambda_expression
  return_type: (type_expression
    (identifier) @type))

; Type annotations on parameters / let bindings.
(let_decl
  type: (type_expression
    (identifier) @type))

(const_decl
  type: (type_expression
    (identifier) @type))

; Function-type `T -> U` parameter annotations.
(function_type
  param: (identifier) @type)
(function_type
  result: (type_expression
    (identifier) @type))

; Enum-variant payload types.
(enum_variant_payload
  (type_expression
    (identifier) @type))

; Struct field types.
(struct_field
  type: (type_expression
    (identifier) @type))

[
  "("
  ")"
  "{"
  "}"
  "["
  "]"
] @punctuation.bracket

[
  ","
  ";"
  ":"
  "."
] @punctuation.delimiter

; -----------------------------------------------------------------
; Builtins — recognised by name pattern
; -----------------------------------------------------------------

((identifier) @function.builtin
 (#match? @function.builtin
  "^(print|println|eprint|eprintln|print_int|read_line|read_file|write_file|append_file|len|substr|char_at|str_eq|str_len|str_concat|str_to_int|int_to_str|str_split|str_index_of|str_contains|list_new|list_len|list_push|push|pop|list_get|list_set|list_pop|map_new|map_set|map_get|map_has|map_keys|map_values|map_len|map_delete|alloc|rt_alloc|rt_realloc|arena_alloc|mem_alloc|mem_alloc_zeroed|mem_free|mem_copy|mem_read_i64|mem_write_i64|to_float|from_float|float_to_str|fsqrt|range|range_step|sum|map_list|filter|reduce|keys|throw|get_error)$"))

((identifier) @type.builtin
 (#match? @type.builtin "^(int|str|list|float|bool|map)$"))

; Match arm pattern wildcards.
(wildcard_pattern) @constant.builtin
