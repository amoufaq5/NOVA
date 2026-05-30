# nova-lsp

Language Server Protocol implementation for [Nova](https://github.com/nova-lang/nova).
Pure Python, standard-library only — no `pip` dependencies beyond setuptools
for the install hook.

## Capabilities (MVP)

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
| `textDocument/completion`           | future |
| `textDocument/definition`           | future |

Hover scans the open document and every `import "..."` it transitively
references for `fn name(args)` definitions and surfaces the signature in
a Markdown popover. The hovered identifier is resolved by reading the
word under the cursor.

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
```

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
```
