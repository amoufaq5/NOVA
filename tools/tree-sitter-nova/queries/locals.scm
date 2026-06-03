; tree-sitter locals for NOVA.
;
; Drives "goto-definition (in-scope)", "find references", document-local
; rename, and inline-shadowing highlighting in editors that consume
; tree-sitter locals (Neovim's `nvim-treesitter` locals module, Helix's
; built-in scope tracker, Emacs `treesit`).
;
; Capture vocabulary (standard nvim-treesitter / tree-sitter naming):
;
;   @local.scope        — node introduces a new lexical scope
;   @local.definition   — identifier that defines a binding
;   @local.reference    — identifier that references a binding
;
; A reference resolves to the nearest @local.definition with the same
; text in an enclosing @local.scope.

; -----------------------------------------------------------------
; Scopes
; -----------------------------------------------------------------

; Top-level: the source file is the global scope.
(source_file) @local.scope

; Functions: parameters + locals are visible inside the body.
(fn_decl) @local.scope
(extern_fn_decl) @local.scope
(lambda_expression) @local.scope

; Bodies of structured control flow open a fresh inner scope so that
; `let` introduced in (e.g.) a `while` body is shadowed correctly.
(block) @local.scope
(if_statement) @local.scope
(while_statement) @local.scope
(for_statement) @local.scope
(match_expression) @local.scope
(match_arm) @local.scope

; Type-decl scopes — a `struct` / `enum` introduces a name but does not
; open a scope for value bindings, so they are intentionally not
; tagged here. They show up via @local.definition only.

; impl block introduces a method-bound scope.
(impl_block) @local.scope

; do-while body
(do_while_statement) @local.scope

; -----------------------------------------------------------------
; Definitions
; -----------------------------------------------------------------

; `fn foo(...)` — definition of `foo` in the enclosing scope.
(fn_decl
  name: (identifier) @local.definition.function)

(extern_fn_decl
  name: (identifier) @local.definition.function)

; Parameters of fn / extern fn / lambda — visible inside the body.
(parameter
  name: (identifier) @local.definition.parameter)

; `let name = expr` — binds `name`.
(let_decl
  name: (identifier) @local.definition.var)

; `for i in xs` — binds `i` inside the loop body. The 2-arg form
; `for i, x in xs` binds both `i` and `x`; the grammar models this as a
; `seq(identifier, ',', identifier)` so we have to capture each one.
(for_statement
  binding: (identifier) @local.definition.var)

; Note: the 2-binding form does NOT currently flow through `field('binding')`
; because the grammar uses a flat `choice(identifier, seq(...))`. The
; nested identifiers are still visible as `(for_statement (identifier))`,
; so we add a fallback pattern for them.
(for_statement
  (identifier) @local.definition.var)

; struct / enum names — type-level definitions.
(struct_decl
  name: (identifier) @local.definition.type)

(enum_decl
  name: (identifier) @local.definition.type)

; struct fields — record-scoped definitions.
(struct_field
  name: (identifier) @local.definition.field)

; enum variants — declaration-scoped constructor definitions.
(enum_variant
  name: (identifier) @local.definition.constant)

; const decls — top-level value bindings.
(const_decl
  name: (identifier) @local.definition.constant)

; Type parameters introduced on generic enum / struct / fn decls.
(type_parameter
  name: (identifier) @local.definition.type)

; impl block: the `Type` it impls is a reference, not a definition.
(impl_block
  type: (identifier) @local.reference)

; -----------------------------------------------------------------
; References
; -----------------------------------------------------------------

; Any `identifier` that is not in a definition slot is a reference.
; We list the contexts explicitly rather than `(identifier) @local.reference`
; so we don't double-count definitions.

(call_expression
  function: (identifier) @local.reference)

(call_expression
  function: (field_expression
    object: (identifier) @local.reference))

(field_expression
  object: (identifier) @local.reference)

(index_expression
  object: (identifier) @local.reference)

(binary_expression
  left: (identifier) @local.reference)

(binary_expression
  right: (identifier) @local.reference)

(unary_expression
  operand: (identifier) @local.reference)

(ternary_expression
  condition: (identifier) @local.reference)

(ternary_expression
  consequence: (identifier) @local.reference)

(ternary_expression
  alternative: (identifier) @local.reference)

(assignment_statement
  lhs: (identifier) @local.reference)

(assignment_statement
  rhs: (identifier) @local.reference)

(argument_list
  (identifier) @local.reference)

(parenthesized_expression
  (identifier) @local.reference)

(expression_statement
  (identifier) @local.reference)

(list_literal
  (identifier) @local.reference)

(return_statement
  value: (identifier) @local.reference)

(let_decl
  value: (identifier) @local.reference)

(if_statement
  condition: (identifier) @local.reference)

(while_statement
  condition: (identifier) @local.reference)

(for_statement
  iterable: (identifier) @local.reference)

(match_expression
  scrutinee: (identifier) @local.reference)

(match_arm
  pattern: (identifier) @local.reference)

(match_arm
  value: (identifier) @local.reference)

; Type references inside parameter / let annotations.
(type_expression
  (identifier) @local.reference)
