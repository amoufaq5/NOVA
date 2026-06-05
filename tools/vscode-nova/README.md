# NOVA Language — VS Code extension

`nova-language` is the official VS Code extension for the
[NOVA programming language](https://github.com/amoufaq5/NOVA). It
bundles three previously-separate pieces of tooling into one
installable extension:

  - **Syntax highlighting** for `.nova` files (TextMate grammar in
    `syntaxes/nova.tmLanguage.json`, ~80% surface coverage as of
    R36C).
  - **Language Server Protocol client** that talks to
    [`nova-lsp`](../nova-lsp/) — hover, goto, rename, inlay hints,
    code actions, semantic tokens, code lens. 27+ LSP capabilities
    as of R36D.
  - **Debug Adapter Protocol client** that talks to
    [`nova-dap`](../nova-dap/) — source-line / function /
    exception / instruction breakpoints, conditional + hit-count
    breakpoints, reverse debug, disassembly view, sampler
    profiler. 28+ DAP capabilities as of R36E.

This is the v0.1.0 release — see the [Roadmap](#roadmap) for what's
not yet bundled.

## Requirements

  - VS Code **1.85** or later.
  - Python **3.10** or later, on a path the extension can find. The
    two Python servers (`nova-lsp`, `nova-dap`) are NOT bundled in
    the VSIX; you install them once into a venv and point the
    extension at it (see [Server install](#server-install)).
  - For debugging only: `gdb` 9.0+ and `objdump` (GNU binutils) on
    `PATH`.

## Install

### Option A — install the prebuilt VSIX (recommended)

1. Download `nova-language-0.1.0.vsix` from the latest NOVA
   release, or build it locally:
   ```bash
   cd tools/vscode-nova
   ./scripts/build-vsix.sh    # writes nova-language-0.1.0.vsix here
   ```
2. Install into VS Code:
   ```bash
   code --install-extension nova-language-0.1.0.vsix
   ```
3. Open a `.nova` file. Syntax highlighting starts immediately.
   LSP + DAP wire up after [Server install](#server-install).

### Option B — development / symlink mode

```bash
ln -s "$(pwd)/tools/vscode-nova" \
    "$HOME/.vscode/extensions/crossengin.nova-language-0.1.0"
# then reload VS Code
```

## Server install

The extension's TypeScript glue runs `python -m nova_lsp` and
`python -m nova_dap` on activation / debug start. To make those
modules resolvable, install the servers in editable mode inside a
venv:

```bash
python3 -m venv $HOME/.local/share/nova-tools-venv
source $HOME/.local/share/nova-tools-venv/bin/activate
pip install -e tools/nova-lsp tools/nova-dap
which python3       # remember this path
```

Then in your VS Code workspace `.vscode/settings.json`:

```jsonc
{
  // Point at the venv's python interpreter so `python -m nova_lsp`
  // resolves the editable install.
  "nova.python.path": "/home/<you>/.local/share/nova-tools-venv/bin/python",

  // Optional overrides (defaults shown).
  "nova.lsp.enabled": true,
  "nova.lsp.module": "nova_lsp",
  "nova.lsp.args": [],
  "nova.dap.module": "nova_dap",
  "nova.dap.args": [],

  // Trace LSP traffic into the "NOVA Language Server" output channel.
  "nova.trace.server": "off"
}
```

Open a `.nova` file. The status bar shows `NOVA Language Server`
when the LSP is attached. The output channel of the same name has
the Python traceback if start fails.

## Debugging

Add a `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "nova",
      "request": "launch",
      "name": "Launch NOVA program",
      "program": "${workspaceFolder}/build/myprogram",
      "args": [],
      "cwd": "${workspaceFolder}",
      "stopOnEntry": false,
      "console": "integratedTerminal"
    }
  ]
}
```

Compile the program first (`nova compile src/main.nova -o
build/myprogram`), then F5 to launch. The DAP factory in
`src/extension.ts` spawns `python -m nova_dap` on each session.

## Commands

| Command ID                    | Title                           |
| ----------------------------- | ------------------------------- |
| `nova.restartLanguageServer`  | NOVA: Restart Language Server   |
| `nova.showOutput`             | NOVA: Show Language Server Log  |

Bring up the Command Palette (`Ctrl+Shift+P`) and search for
`NOVA:` to find them.

## Settings reference

All settings live under the `nova.*` namespace. See `package.json`
for the authoritative list; the main ones:

| Setting               | Default     | Purpose                                                            |
| --------------------- | ----------- | ------------------------------------------------------------------ |
| `nova.python.path`    | `"python3"` | Python interpreter that hosts both servers.                        |
| `nova.lsp.enabled`    | `true`      | Master switch for the LSP client.                                  |
| `nova.lsp.module`     | `"nova_lsp"`| Module name passed to `python -m`.                                 |
| `nova.lsp.args`       | `[]`        | Extra argv tail for the LSP.                                       |
| `nova.dap.module`     | `"nova_dap"`| Module name passed to `python -m` on debug start.                  |
| `nova.dap.args`       | `[]`        | Extra argv tail for the DAP server.                                |
| `nova.compiler.path`  | `"nova"`    | NOVA compiler binary (used by the LSP for `--check` diagnostics).  |
| `nova.trace.server`   | `"off"`     | `off` / `messages` / `verbose` — LSP wire trace.                   |

## Build from source

Prerequisites: Node.js 18+ and `npm`.

```bash
cd tools/vscode-nova
./scripts/build-vsix.sh
```

The script runs `npm install`, `npm run compile` (TypeScript ->
`out/`), and `npx @vscode/vsce package --no-dependencies`. On
success the last line of stdout is the VSIX absolute path. Common
failure modes:

  - **`npm: command not found`** — install Node.js 18+ and re-run.
  - **`Missing publisher name`** from vsce — `package.json` already
    sets `publisher: "crossengin"` as a placeholder. For a real
    Marketplace publish you need a registered publisher namespace +
    a Personal Access Token; that's deferred (see Roadmap).
  - **`Make sure to edit the README.md file before you package or
    publish your extension`** — vsce thinks the README is the
    default stub. Pre-publishing for real you'd want to expand
    this README; for local install it's a warning, not an error.

## Tests

```bash
cd tools/vscode-nova
python -m unittest discover tests
```

Three test modules:

  - `tests/test_extension_manifest.py` — `package.json` invariants.
  - `tests/test_textmate_grammar.py`  — TextMate grammar invariants.
  - `tests/test_build.py`             — runs `scripts/build-vsix.sh`
    when `npm` is available (auto-skipped otherwise; also skipped
    when `NOVA_VSCODE_OFFLINE=1`).

## Architecture

```
.nova file open
      │
      └─→ VS Code activates `crossengin.nova-language`
            │
            ├─→ out/extension.js
            │     ├── readConfig() → nova.python.path, modules, args
            │     ├── spawn python -m nova_lsp  (stdio)   ──► LanguageClient
            │     └── register DebugAdapterDescriptorFactory("nova")
            │
            ├── TextMate grammar → syntax highlighting (no LSP needed)
            │
            └── On F5 (debug launch):
                  └── factory.createDebugAdapterDescriptor()
                        └── spawn python -m nova_dap  (stdio)
```

## Roadmap

What this v0.1.0 extension explicitly does NOT do, and what's planned:

  - **Tree-sitter WASM grammar.** The
    [`tree-sitter-nova`](../tree-sitter-nova/) grammar is shipped
    separately; bundling its compiled WASM into the VSIX is
    deferred to v0.2. v0.1 syntax highlighting uses the TextMate
    fallback.
  - **Marketplace publish.** v0.1 distributes via the VSIX file
    + `code --install-extension`. A real Marketplace listing
    needs a registered publisher namespace + a `vsce` PAT —
    deferred.
  - **Auto-install the Python servers.** v0.1 documents the
    `pip install -e` step; a future round may bundle a postinstall
    hook that prompts the user.
  - **Closure-literal (R35C) and tuple (R36C) grammar precision.**
    The TextMate grammar covers them with generic patterns; the
    tree-sitter grammar will give precise tokens once bundled.

## See also

  - [`docs/IDE_SETUP.md`](../../docs/IDE_SETUP.md) — full IDE setup
    guide (this extension + venv + gdb).
  - [`tools/nova-lsp/README.md`](../nova-lsp/README.md) — LSP
    capability matrix per round.
  - [`tools/nova-dap/README.md`](../nova-dap/README.md) — DAP
    request matrix.
  - [`tools/tree-sitter-nova/README.md`](../tree-sitter-nova/README.md)
    — grammar coverage.

## License

MIT. See the root `LICENSE` file in the NOVA repository.
