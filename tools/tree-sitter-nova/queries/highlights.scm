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
  "extern"
  "struct"
  "enum"
  "import"
  "asm"
] @keyword

[
  "if"
  "else"
  "while"
  "for"
  "in"
  "return"
  "break"
  "continue"
  "match"
] @keyword.control

; -----------------------------------------------------------------
; Identifiers & functions
; -----------------------------------------------------------------

(fn_decl name: (identifier) @function)
(extern_fn_decl name: (identifier) @function)
(lambda_expression) @function

(call_expression
  function: (identifier) @function.call)

(call_expression
  function: (field_expression
    field: (identifier) @function.method.call))

(parameter (identifier) @variable.parameter)

(struct_decl name: (identifier) @type)
(enum_decl name: (identifier) @type)
(struct_field name: (identifier) @property)
(field_expression field: (identifier) @property)

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
] @operator

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
