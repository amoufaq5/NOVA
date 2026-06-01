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
| `textDocument/hover`                | yes (function signatures from imports) |
| `textDocument/completion`           | yes (builtins + fn/let scan, triggers on `.` and `(`) |
| `textDocument/rename`               | yes (regex `\b<old>\b` across open docs + imports) |
| `textDocument/references`           | yes (regex scan, open docs + transitively imported files) |
| `textDocument/codeAction`           | yes (extract function, organize imports, sort fn declarations) |
| `textDocument/definition`           | yes (intra-file + follows `import "..."` transitively) |
| `workspace/symbol`                  | yes (fuzzy name search across every indexed `.nova` file) |

Hover scans the open document and every `import "..."` it transitively
references for `fn name(args)` and `let X = ...` definitions, plus the
builtin function table mirrored from `src/compiler/codegen.nova`'s
`is_builtin_fn`. The hovered identifier is resolved by reading the word
under the cursor.

Completion returns the same symbol set (builtins + user `fn`/`let`),
sorted with builtins first; triggered manually or by typing `.` / `(`.
Each item carries a `detail` like `fn foo(a, b)` so VS Code shows the
signature next to the label.

Rename walks every open document plus the union of files they import
(transitively) and emits a `WorkspaceEdit` with per-file `TextEdit[]`
arrays for each `\b<oldname>\b` occurrence. It is a textual rename — it
won't respect shadowing, but it handles the 80 % case.

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
```

The six `tests/*_smoke.py` / `test_*.py` scripts use the bundled
`_harness.py` helper to drive `dispatch()` in-process (no subprocess),
open a tiny workspace, and assert on the response payloads. They run in
~10 ms each (the workspace-symbol integration leg also indexes
`/home/user/NOVA/src/` so it takes a bit longer when the tree is
present).

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
  tests/
    _harness.py             # in-process LSP client (no subprocess)
    completion_smoke.py
    definition_cross_file_smoke.py
    rename_smoke.py
    references_smoke.py
    code_action_smoke.py
    test_workspace_symbols.py
```
