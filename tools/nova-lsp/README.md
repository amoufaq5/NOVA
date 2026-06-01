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
| `textDocument/references`           | yes (regex scan, open docs + imports) |
| `textDocument/definition`           | future |

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
match in open documents + imported files.

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
```

The three `tests/*_smoke.py` scripts use the bundled `_harness.py`
helper to drive `dispatch()` in-process (no subprocess), open a tiny
workspace, and assert on the response payloads. They run in ~10 ms each.

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
    __main__.py        # python -m nova_lsp
    server.py          # all LSP logic
  tests/
    _harness.py        # in-process LSP client (no subprocess)
    completion_smoke.py
    rename_smoke.py
    references_smoke.py
```
