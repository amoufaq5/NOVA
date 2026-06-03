# tree-sitter-nova

A [tree-sitter](https://tree-sitter.github.io/tree-sitter/) grammar for
the **NOVA** programming language.

The grammar produces a concrete syntax tree (CST) directly usable by:

- **Neovim** via `nvim-treesitter` (see `INSTALL_NEOVIM.md`)
- **Helix** (built-in tree-sitter support)
- **Emacs** via `tree-sitter` package
- **GitHub** web UI (when accepted into `linguist`)
- **VS Code** via the `vscode-tree-sitter` extension (the bundled
  `tools/vscode-nova` extension still ships a TextMate grammar as the
  fallback for hosts that lack tree-sitter integration)

## Coverage

This grammar handles every everyday NOVA construct plus the
declarative cognitive-DSL surface used by `mind`/`soul`/`system`
examples. The R26B revision extends R24B's R17A–R23A coverage with
the R25A brace-init struct construction and destructure patterns:

| Construct                       | Status          |
| ------------------------------- | --------------- |
| `fn` declaration / call         | full            |
| `let` binding (top-level + block) | full          |
| `let [a, b, ...rest] = expr`    | full (R13)      |
| `let a, b = x, y`               | full (R14)      |
| `const NAME = value`            | full            |
| `if` / `else` (incl. `else if`, if-expression) | full |
| `while` loop (with `else` clause) | full          |
| `do { ... } while cond`         | full            |
| `for` loop (incl. `for i, x in xs`, `for ... else`) | full |
| `return` / `break` / `continue` | full            |
| `break if cond` / `continue if cond` | full       |
| `@label while`, `break @label`  | full            |
| `extern fn`                     | full            |
| `-> Type` return-type annotation | full           |
| `: Type` legacy return-type     | full            |
| `T -> U` function-type syntax   | full (R22B)     |
| `name: Type` type annotations (params + let + const) | full |
| `import "path.nova"`            | full            |
| `struct Name<T, U> { f: T; g: U }` | full (R23A)  |
| `Foo { field: val, ... }` brace-init | full (R25A) |
| `Foo { field }` brace-init shorthand | full (R25A) |
| `let Foo { f: a, g: b } = expr` destructure | full (R25A) |
| `let Foo { f, g } = expr` destructure shorthand | full (R25A) |
| `let Foo { f, .. } = expr` partial destructure | full (R25A) |
| `match v { Foo { f: 0, g: _ } => ... }` struct-pattern arm | full (R25A) |
| `Foo { f: a, ..base }` struct update-syntax | full (R26A, grammar-only) |
| `enum Name<T, U> { Variant(T) }` | full (R17A + R21A) |
| `Type::Variant(payload)` constructor / match destructure | full (R17A) |
| `expr?` Result-propagation operator | full (R20A) |
| `impl Type { fn ... }`          | full            |
| `fn Type.method(self, ...)`     | full            |
| Match guards `_ if cond =>`     | full            |
| `match … { p => e }` expression | full            |
| `is T` / `in xs` / `not in xs`  | full            |
| `..` / `..=` range operators    | full            |
| `\|>` pipe operator             | full            |
| `??` nullish coalescing, `??=` assignment | full   |
| `not` / `and` / `or` keyword operators | full      |
| `**` power, `**=` assignment    | full            |
| `expr @ score` confidence annotation | full       |
| `asm { "..." }` blocks          | full            |
| Anonymous `fn(...) { }` lambdas (with `: T` / `-> T` return) | full |
| Slice `xs[start:end]` / `xs[s:e:step]` | full      |
| List literal `[1, 2, 3]`, list comprehension `[x*2 for x in xs]` | full |
| Map literal `{"k": v}`, map comprehension `{k: v for x in xs}` | full |
| Spread `...args`                | full            |
| Named-arg call `f(name: value)` | full            |
| Line `//`, `#`, `--` and block `/* */` comments | full |
| Number literals: dec, hex, octal, binary, float | full |
| String literals + `${expr}` interpolation | full  |
| Operators with C-like precedence | full           |
| Flow operators `~> <~ =>> <<~ ~~> <=> \|~>` | full (cognitive DSL) |
| `mind`/`soul`/`system { sections... }` declarations | partial |

Empirically, the grammar parses **242 / 247 (~98.0%)** of the canonical
NOVA sample programs (`tests/*.nova` + `examples/*.nova`) with **0
ERROR / 0 MISSING** nodes — including all R17A enum sum-type tests,
R20A `?` propagation tests, R21A generic enum tests, R22B generic fn
tests, R23A generic struct tests, R25A brace-init + destructure tests
(`test_struct_brace_init.nova`, `test_struct_destructure.nova`), every
`_demo.nova` cognitive example (`active_inference_demo`,
`causal_library_demo`, `predictive_coding`, `hdc_demo`, `sdr_demo`,
`soul_demo`, `system_syntax_demo`), the WASI round-trip,
`concurrency.nova`, `game_of_life.nova`, `brainfuck.nova`, and
`showcase.nova` (with `${expr}` interpolation).

### Out of scope

The remaining five files use surface syntax we intentionally defer to
a follow-up revision:

- `examples/basic_mind.nova` — uses a freeform `mind { memory NAME(args)
  ... fn-like-bindings ... }` body with implicit `fn` declarations
  (a separate macro-style DSL).
- `examples/moment_signal.nova` — uses the bespoke `moment`, `node :
  reasoner`, `when X arrives as sig`, `reason_by`, `pattern_match
  ... against ...` syntax.
- `examples/soul_demo.nova` — uses additional `phases { name { when:
  ..., behavior: ..., boosts: A by N, ... } }` nesting with `by` /
  `when` as soft keywords.
- `tests/test_type_match.nova` — uses `is T` patterns at match-arm
  head with no left-hand operand; tree-sitter cannot disambiguate
  the case where the previous arm value is a single literal from
  `value is T` as a binary type-check expression without an external
  scanner. Workaround: write the match arm body as a block
  (`{ "integer" }`) to make the arm terminus explicit.
- `tests/test_struct_update_syntax.nova` (R26A) — uses the generic
  brace-init form `Box<int> { ..bi }`. The `<` token forces an LR
  conflict with `a < b` binary comparison that GLR cannot resolve
  without an external scanner. The bare-identifier brace-init form
  (`Box { value: 42 }`, `Box { ..bi }`) parses cleanly; only the
  explicit `<TypeArgs>` annotation is OOS. Tree-sitter sees the
  generic prefix as a binary chain `Box < int > { ... }` (map literal),
  which still highlights reasonably even if not as a struct_init node.

These five files require either an external scanner or a token-level
redesign that the editor experience does not need today.

## Build

```bash
cd tools/tree-sitter-nova
npm install                           # installs tree-sitter-cli
npx tree-sitter generate              # writes src/parser.c
npx tree-sitter test                  # 108 corpus tests should pass
npx tree-sitter parse path/to.nova    # print the CST for a file
```

If `npm` is unavailable on your machine, the grammar itself is just a
JavaScript file (`grammar.js`). You can validate its syntax with the
host node binary:

```bash
node --check grammar.js
```

…and then run `tree-sitter generate` from a machine that has the CLI
installed. The generated `src/parser.c` is portable C and only needs a
C compiler downstream — neither node nor npm.

## Layout

```
tree-sitter-nova/
├── grammar.js              ← the grammar DSL
├── package.json            ← npm metadata + scripts
├── tree-sitter.json        ← tree-sitter 0.22+ project descriptor
├── binding.gyp             ← node-gyp native binding build
├── Cargo.toml              ← Rust binding (optional, for Helix/embedded)
├── queries/
│   ├── highlights.scm      ← editor highlight scopes
│   ├── folds.scm           ← fold-region patterns (functions, blocks, …)
│   └── locals.scm          ← lexical-scope / def-ref tracking
├── test/
│   └── corpus/
│       └── basics.txt      ← round-trip CST snapshots
└── bindings/               ← auto-generated language bindings
```

## Editor integration

See `INSTALL_NEOVIM.md` for a worked Neovim example. Other editors
share the same install shape: build the parser as a shared library,
drop the `queries/*.scm` files into the editor's tree-sitter runtime
path, register the file extension.

### Query files

The grammar ships three editor query files under `queries/`:

| File              | Purpose                                                      |
| ----------------- | ------------------------------------------------------------ |
| `highlights.scm`  | Token → highlight-scope mapping (keywords, calls, strings…). |
| `folds.scm`       | Foldable regions (function bodies, control-flow blocks…).    |
| `locals.scm`      | Lexical-scope tracking for goto-def / rename in editors that lack a full LSP. |

Editor capability matrix:

| Editor   | highlights | folds | locals |
| -------- | :--------: | :---: | :----: |
| Neovim   | yes        | yes   | yes    |
| Helix    | yes        | yes   | yes    |
| Emacs    | yes        | yes   | yes    |
| Zed      | yes        | yes   | n/a    |
| VS Code* | yes        | n/a   | n/a    |

\* VS Code consumes `highlights.scm` only when paired with a tree-sitter
host extension; the bundled `tools/vscode-nova` TextMate grammar is the
default. Folding and locals in VS Code come from `nova-lsp`.

## Versioning

This grammar is versioned independently of the NOVA compiler. The
contract is *forward-compatible*: a CST shape that parses today will
still parse with future grammar versions — fields may be *added* but
will not be renamed or removed without a major version bump.
