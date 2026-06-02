# nova-lsp

Language Server Protocol implementation for [Nova](https://github.com/nova-lang/nova).
Pure Python, standard-library only — no `pip` dependencies beyond setuptools
for the install hook.

## Capabilities

| LSP method                          | Status |
| ----------------------------------- | ------ |
| `initialize` / `initialized`        | yes    |
| `shutdown` / `exit`                 | yes    |
| `textDocument/didOpen`              | yes    |
| `textDocument/didChange` (full)     | yes    |
| `textDocument/didSave`              | yes    |
| `textDocument/didClose`             | yes    |
| `textDocument/publishDiagnostics`   | yes (runs `nova --check`) |
| `textDocument/hover`                | yes (signatures from imports + `///` doc comments rendered as markdown) |
| `textDocument/completion`           | yes (builtins + fn/let scan, triggers on `.` and `(`) |
| `textDocument/rename`               | yes (workspace-wide for top-level fn/let/const/type, single-buffer for locals + params) |
| `textDocument/references`           | yes (regex scan, open docs + transitively imported files) |
| `textDocument/codeAction`           | yes (extract function, organize imports, sort fn declarations) |
| `textDocument/definition`           | yes (intra-file + follows `import "..."` transitively) |
| `workspace/symbol`                  | yes (fuzzy name search across every indexed `.nova` file) |
| `textDocument/semanticTokens/full`  | yes (variable / function / type / namespace / keyword / string / number / comment / parameter / constant with declaration / readonly / static modifiers) |
| `textDocument/semanticTokens/range` | yes (same classifier, filtered to the requested line range) |

Hover scans the open document and every `import "..."` it transitively
references for `fn name(args)` and `let X = ...` definitions, plus the
builtin function table mirrored from `src/compiler/codegen.nova`'s
`is_builtin_fn`. The hovered identifier is resolved by reading the word
under the cursor.

When the resolved symbol has a `///` doc-comment block immediately
above its declaration, the hover response includes the doc rendered as
markdown — the signature is shown in a fenced `nova` code block,
separated from the docs by a `---` horizontal rule. For example:

```
/// Returns the n-th Fibonacci number.
/// Time complexity: O(2^n) (naive recursion).
/// Examples:
///   fib(0) == 0
///   fib(10) == 55
fn fib(n) { ... }
```

Hovering over any call site for `fib` renders:

> ```nova
> fn fib(n)
> ```
> ---
> Returns the n-th Fibonacci number.
> Time complexity: O(2^n) (naive recursion).
> Examples:
> &nbsp;&nbsp;fib(0) == 0
> &nbsp;&nbsp;fib(10) == 55

The doc collector (see `nova_lsp/hover_docs.py`) walks **backward** from
the declaration line and gathers every contiguous `///` line. It stops
at the first non-`///` non-blank line, AND **stops at the first blank
line** — doc blocks must be contiguous with the declaration they
document (same convention as `rustdoc`). A `///` with no following
space (`///foo`) is treated the same as `/// foo`; at most one space
after the prefix is consumed so deliberately indented markdown
(bullets, nested lists, code blocks) survives verbatim. Empty `///`
lines become empty markdown lines inside the joined block, enabling
paragraph breaks inside a single doc block. A `////` (four or more
slashes) is treated as a visual divider, not a doc comment.

Cross-file hovers are routed through R5F's `find_definition` so the
docs come from whichever file actually declares the symbol — open
buffers override on-disk content so the user sees their unsaved doc
edits immediately. Builtins (`println`, `len`, ...) have no source
line, so the hover renders only the signature plus the `(builtin)`
marker.

Completion returns the same symbol set (builtins + user `fn`/`let`),
sorted with builtins first; triggered manually or by typing `.` / `(`.
Each item carries a `detail` like `fn foo(a, b)` so VS Code shows the
signature next to the label.

Rename is workspace-wide for top-level declarations. When the cursor
is on a top-level `fn` / `let` / `const` / `type` name, the server
resolves the symbol's canonical definition via R5F's `find_definition`,
classifies the declaration line (top-level vs. indented), and then
walks every file in the workspace symbol index plus open buffers,
keeping only the files that transitively `import` the definition site.
For each kept file the server emits LSP `TextEdit[]` ranges for every
`\b<oldname>\b` occurrence — strings and comments are masked out so
the rename never touches documentation that happens to mention the
name. Unrelated files that use the same identifier locally without
importing the definition site are left alone. If the new name already
exists at top level in any affected file, the server returns a
JSON-RPC `ResponseError` (code `-32803`) with a human-readable
conflict message instead of an edit.

Local renames (function parameters, indented `let`/`const`,
anonymous helpers) stay in the legacy single-buffer-plus-open-imports
path so the user's scope doesn't accidentally bleed across files.

References uses the same regex scan and returns `Location[]` for every
match in open documents + imported files. The scanner walks the transitive
import graph rooted at the current document, then includes any other open
buffers (and their import closures) so multi-root workspaces find
references that aren't reachable from one root.

Go-to-definition (`textDocument/definition`) looks up the identifier under
the cursor against top-level `fn` and `let` definitions, searching the
current document first and then walking `import "..."` statements
transitively until it finds a match. Resolved paths are relative to the
importing file's directory; absolute paths are honoured as-is. Builtins
(`println`, `len`, `map_set`, ...) return an empty location list — they
have no source position, so the editor falls back to hover for the
signature. A per-file scan cache keyed by absolute path with mtime
invalidation keeps repeat lookups O(1) when nothing has changed on disk;
open buffers feed their live text in via a `text_overrides` map so
unsaved edits beat stale on-disk content.

Code actions surface three refactorings via the VS Code lightbulb menu:

* **Extract to function `extracted_N`** (`refactor.extract`) — only
  shown when the selection covers a multi-statement block inside a `fn`
  body. The server identifies free variables in the selection
  (identifiers used but not `let`-bound inside it, minus keywords and
  builtins), inserts a new top-level `fn extracted_N(<free_vars>)` right
  after the last import, and replaces the selection with a call to it.
* **Organize imports** (`source.organizeImports`) — sorts the top-of-file
  `import "..."` block alphabetically and groups it: `std/` first,
  then `../src/`, then `../../tests/`, others last. Blank lines
  separate adjacent groups.
* **Sort top-level functions** (`source.organizeFns`) — sorts every
  top-level `fn` declaration by name, preserving each declaration's
  leading doc-comment block.

All three return `WorkspaceEdit`s in the `{"changes": {uri: TextEdit[]}}`
shape, which VS Code applies in-place without diff reconciliation.

Semantic tokens (`textDocument/semanticTokens/full` + `/range`) drive
rich syntax highlighting beyond TextMate regex scopes. Each identifier
is classified into one of `variable` / `function` / `type` /
`namespace` / `keyword` / `string` / `number` / `comment` /
`parameter` / `constant`, plus a modifier bitmask of `declaration` /
`definition` / `readonly` / `static` / `deprecated`. Editors use this
to colour `let` bindings differently from `mut`, italicise types vs
values, fade out deprecated symbols, and so on.

Classification rules (in priority order):

  * `fn NAME` -> `function + declaration`; the bracketed parameter list
    that follows emits `parameter + declaration` tokens.
  * `let NAME` -> `variable + declaration + readonly` (NOVA `let` is
    immutable); ALL_CAPS names like `TAU` instead emit `constant`.
  * `const NAME` -> `constant + declaration + readonly + static`.
  * `mut NAME` -> writeable `variable + declaration`.
  * `type NAME` / `struct NAME` / `enum NAME` -> `type + declaration`.
  * `module NAME` -> `namespace + declaration`.
  * `import "..."` -> the string literal is classified as `namespace`
    so the editor can colour the path distinctively.
  * Identifier followed by `(` -> `function` (call site).
  * Identifier matching a known function / constant from the workspace
    symbol index (R8C) -> classified accordingly, even when there's no
    local declaration in the same buffer.
  * Numeric literals: decimal, hex `0x`, octal `0o`, binary `0b`, plus
    floats and scientific notation.
  * `//` line and `/* ... */` block comments (block comments crossing
    lines are emitted as one comment slice per line, since semantic
    tokens cannot span line boundaries).
  * Triple-quoted strings (`"""..."""`) are emitted as one string
    slice per line, mirroring the lexer's multiline handling.

The wire format is the standard LSP delta-compressed flat int array:
`[deltaLine, deltaStart, length, tokenType, tokenModifiers]` per token,
sorted by source position. The token-type + modifier legend is
advertised under `semanticTokensProvider.legend` at `initialize` time.
Tokenization of `src/compiler/codegen.nova` (~17000 lines, ~520 kB)
emits about 37000 tokens in roughly 140 ms — fast enough for
interactive recolouring on every keystroke.

Workspace symbols (`workspace/symbol`) drives Cmd+T / Ctrl+T in the
editor. The server keeps an in-memory inverted index of every top-level
`fn`, `let`, and (in `codegen.nova` / `compiler.nova`) every
`out_label("_nova_*")` runtime helper, refreshed on every
`didOpen`/`didChange`/`didSave`/`didClose`. On the first query the
server also crawls `state.root_path` for `*.nova` files so the picker
sees the entire workspace, not just open buffers. Fuzzy matching ranks
candidates by tier: exact > case-insensitive > prefix > substring >
camelCase letter match > sequential character match. An empty query
returns the first 100 symbols in name order. SymbolKind is `Function`
for `fn`, `Constant` for ALL_CAPS `let` (e.g. `TAU`, `MAX_SIZE`), and
`Variable` for everything else.

Diagnostics are produced by writing the buffer to a tempfile and running
`nova --check <tempfile>`. The server falls back to `nova <tempfile> -o
/dev/null` if `--check` is not recognised by the installed compiler.
Stderr is matched against
`file:line:col: (error|warning|note): message`-style records.

## Install

```bash
pip install -e tools/nova-lsp/
```

This exposes a `nova-lsp` console script and a `python -m nova_lsp`
entry point.

## Quick smoke test

```bash
nova-lsp --help
nova-lsp --version
python -m nova_lsp --version
python tools/nova-lsp/tests/completion_smoke.py
python tools/nova-lsp/tests/rename_smoke.py
python tools/nova-lsp/tests/references_smoke.py
python tools/nova-lsp/tests/code_action_smoke.py
python tools/nova-lsp/tests/definition_cross_file_smoke.py
python tools/nova-lsp/tests/test_workspace_symbols.py
python tools/nova-lsp/tests/test_rename_workspace.py
python tools/nova-lsp/tests/test_semantic_tokens.py
python tools/nova-lsp/tests/test_hover_docs.py
```

The nine `tests/*_smoke.py` / `test_*.py` scripts use the bundled
`_harness.py` helper to drive `dispatch()` in-process (no subprocess),
open a tiny workspace, and assert on the response payloads. They run in
~10 ms each (the workspace-symbol + workspace-rename integration legs
also index `/home/user/NOVA/src/` so they take a bit longer when the
tree is present).

## VS Code wiring

The `vscode-nova` extension declares `nova.lsp.serverCommand` and
`nova.compilerPath` settings. A minimal launcher client (`extension.js`)
can be wired up as follows:

```js
// tools/vscode-nova/extension.js (sketch — install vscode-languageclient first)
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;
function activate(context) {
  const cmd = vscode.workspace.getConfiguration("nova").get("lsp.serverCommand", "nova-lsp");
  const compiler = vscode.workspace.getConfiguration("nova").get("compilerPath", "nova");
  const serverOptions = {
    run:   { command: cmd, args: ["--stdio"], transport: TransportKind.stdio },
    debug: { command: cmd, args: ["--stdio"], transport: TransportKind.stdio },
  };
  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "nova" }],
    initializationOptions: { compilerPath: compiler },
  };
  client = new LanguageClient("nova-lsp", "Nova Language Server", serverOptions, clientOptions);
  client.start();
}
function deactivate() { return client && client.stop(); }
module.exports = { activate, deactivate };
```

## Logging

Set `NOVA_LSP_LOG=/tmp/nova-lsp.log` in the environment to capture
internal trace output (helpful for editor integration).

## Layout

```
tools/nova-lsp/
  pyproject.toml
  README.md
  nova_lsp/
    __init__.py
    __main__.py             # python -m nova_lsp
    server.py               # LSP request handlers + dispatcher
    imports.py              # import-graph walker + mtime-invalidated file cache
    workspace_symbols.py    # workspace/symbol index + fuzzy matcher
    rename_workspace.py     # workspace-wide rename engine (top-level decls)
    semantic_tokens.py      # semantic-tokens classifier + delta encoder
    hover_docs.py           # `///` doc-comment extractor for hover
  tests/
    _harness.py             # in-process LSP client (no subprocess)
    completion_smoke.py
    definition_cross_file_smoke.py
    rename_smoke.py
    references_smoke.py
    code_action_smoke.py
    test_workspace_symbols.py
    test_rename_workspace.py
    test_semantic_tokens.py
    test_hover_docs.py
```
