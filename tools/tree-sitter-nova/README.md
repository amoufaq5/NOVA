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

This grammar handles the everyday NOVA constructs covered by the
existing TextMate grammar plus a few more that the regex-based one
could not express precisely:

| Construct                       | Status          |
| ------------------------------- | --------------- |
| `fn` declaration / call         | full            |
| `let` binding (top-level + block) | full          |
| `if` / `else` (incl. `else if`) | full            |
| `while` loop                    | full            |
| `for` loop (incl. `for i, x in xs`) | full        |
| `return` / `break` / `continue` | full            |
| `extern fn`                     | full            |
| `-> Type` return-type annotation | full           |
| `name: Type` type annotations (params + let) | full |
| `import "path.nova"`            | full            |
| `struct` declaration            | full            |
| `enum` declaration              | full            |
| `asm { "..." }` blocks          | full            |
| Anonymous `fn(...) { }` lambdas | full            |
| `match … { p => e }` expression | full            |
| Line `//` and block `/* */` comments | full       |
| Number literals: dec, hex, binary, float | full   |
| String literals + `${expr}` interpolation | full  |
| Operators with C-like precedence | full           |
| Cognitive DSL (`soul`/`mind`/`system`/`~>`) | not yet — see [Out of scope](#out-of-scope) |

Empirically, the grammar parses **59 / 65 (~91%)** of the canonical
sample programs under `examples/` cleanly, including the WASI
round-trip (`wasi_file_roundtrip.nova`), `concurrency.nova`,
`game_of_life.nova`, `brainfuck.nova`, `showcase.nova` (with
`${expr}` interpolation), and every R8/R9-era example. The 6 that
still produce `(ERROR …)` nodes all use the experimental
cognitive-system DSL (`soul`/`mind`/`system`/`~>` and friends),
which neither the TextMate grammar nor the current self-hosting
compiler treats as first-class — they are recognised lexically as
identifiers and let through to be lowered by a separate macro pass.

### Out of scope

The bespoke "AGI surface" syntax (`soul`, `mind`, `system`, flow
operators `~>` / `~~>` / `<~`, `pattern_match`, `try`/`catch`/`throw`)
is intentionally left to a follow-up grammar revision. These keywords
are not load-bearing for the day-to-day editing experience and they
require a token-level redesign (flow operators conflict with the
existing `~` bitwise-not unary operator on tree-sitter's lexer).

## Build

```bash
cd tools/tree-sitter-nova
npm install                           # installs tree-sitter-cli
npx tree-sitter generate              # writes src/parser.c
npx tree-sitter test                  # 41 corpus tests should pass
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
