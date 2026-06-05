; tree-sitter folds for NOVA.
;
; Each `@fold` capture marks a node whose source-line span is foldable
; in editors that consume tree-sitter folding (Neovim's
; `nvim-treesitter`, Helix, Emacs `treesit-fold`, etc.). The single-line
; nodes are excluded by the editor automatically — only multi-line
; spans become user-visible fold regions.
;
; Convention: cover every node that introduces a `{ ... }` group plus a
; few non-brace spans (match arms with block bodies, top-level imports
; in a run) so the user gets a natural outline view.

; -----------------------------------------------------------------
; Brace-delimited bodies
; -----------------------------------------------------------------

(fn_decl
  body: (block) @fold)

(extern_fn_decl) @fold

(lambda_expression
  body: (block) @fold)

(struct_decl
  (struct_field_list) @fold)

(enum_decl) @fold

(asm_block) @fold

; R24B: impl blocks fold (R12-era method-bundle bodies).
(impl_block) @fold

; R24B: cognitive declarations and their sections fold independently.
(cognitive_decl) @fold
(cognitive_section) @fold

; R24B: do-while body folds with its block.
(do_while_statement
  body: (block) @fold)

; -----------------------------------------------------------------
; Control-flow bodies — fold the inner block, not the whole `if`,
; so the user can collapse the then-arm and else-arm independently.
; -----------------------------------------------------------------

(if_statement
  consequence: (block) @fold)

(if_statement
  alternative: (block) @fold)

; R33C: if-let-statement / if-let-expression bodies fold the same as if/else.
(if_let_statement
  consequence: (block) @fold)

(if_let_statement
  alternative: (block) @fold)

(if_let_expression
  consequence: (block) @fold)

(if_let_expression
  alternative: (block) @fold)

(while_statement
  body: (block) @fold)

(for_statement
  body: (block) @fold)

; -----------------------------------------------------------------
; match { arm => body } — the whole expression folds plus block-bodied
; arms (so an editor can collapse a single arm without collapsing the
; whole match).
; -----------------------------------------------------------------

(match_expression) @fold

(match_arm
  value: (block) @fold)

; -----------------------------------------------------------------
; Long literal collections
; -----------------------------------------------------------------

(list_literal) @fold

; Bare blocks at any nesting level — useful for hand-grouped scopes.
(block) @fold

; -----------------------------------------------------------------
; Block comments — `/* ... */` spans should fold in editors that
; expose comment folding (Helix, Emacs, JetBrains' tree-sitter
; integration). Line comments are intentionally not folded
; individually; runs of `//` lines are merged by the editor.
; -----------------------------------------------------------------

(comment) @fold
