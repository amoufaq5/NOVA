# VS Code IDE Setup for NOVA

This guide wires up the full NOVA developer experience in VS Code:
syntax highlighting (tree-sitter), the language server (`nova-lsp`,
Python / pygls), and the debugger (`nova-dap`, Python over gdb's
machine interface). After following this guide you will have hover
docs, go-to-definition, rename, inlay hints, breakpoints, conditional
breakpoints, reverse debug, disassembly view, and a profiler.

> **R37D shortcut:** the recommended install today is the
> [`tools/vscode-nova/`](../tools/vscode-nova/) VS Code extension,
> which bundles the TextMate grammar, an LSP client connected to
> `nova-lsp`, and a DAP client connected to `nova-dap` into a
> single VSIX. Build it once with
> `tools/vscode-nova/scripts/build-vsix.sh` and install via
> `code --install-extension nova-language-0.1.0.vsix`. The
> per-tool sections (2-4) below remain as the **advanced /
> bypass** path for users who want to wire LSP and DAP directly
> without the extension.

**Honest up-front note:** the NOVA VS Code extension is **not in the
Marketplace yet**. Installation today is via the VSIX produced by
R37D's build script (`tools/vscode-nova/scripts/build-vsix.sh`) or
manually (Sections 2-4 below). A Marketplace listing is planned for
a future round and tracked in the
[`tools/vscode-nova/README.md`](../tools/vscode-nova/README.md)
roadmap.

## Section 1 -- Install VS Code

Download VS Code from <https://code.visualstudio.com/>. Open it once
to create the user config directory.

You will need:
  - VS Code 1.60 or later (the extension manifests target this).
  - Python 3.10 or later, with `pip` available, for the LSP and DAP
    servers.
  - `gdb` 9.0 or later on `PATH` (for DAP).
  - `objdump` from binutils (for DAP disassembly view).
  - A working NOVA toolchain (`make` succeeds in the NOVA root and
    `make self-host` passes -- see `docs/GETTING_STARTED.md`).

## Section 2 -- Install tree-sitter-nova

Tree-sitter handles NOVA syntax highlighting independently of the
LSP. Editors that support tree-sitter (VS Code via
`vscode-tree-sitter`, Neovim, Helix, Emacs treesit) consume the same
grammar.

### Build the grammar

```bash
cd $NOVA_ROOT/tools/tree-sitter-nova
npm install                         # install tree-sitter CLI deps
npx tree-sitter generate            # regenerate src/parser.c if needed
npx tree-sitter test                # run grammar test corpus
```

If `npx tree-sitter generate` produces no diff against the
checked-in `src/parser.c`, the parser is already up to date.

### Coverage at R34E
The tree-sitter grammar covers everything currently in the NOVA
language EXCEPT closure literals (`|x| x + 1`) which landed in R35C
after the R34E tree-sitter round. Until the closure grammar update
ships, the IDE will mark `|x| x + 1` as a syntax error even though
the NOVA compiler accepts it. The compiler is the source of truth;
the highlighter is catching up.

### Install in VS Code

Two options:

  - **Marketplace tree-sitter integration**: install the
    "VS Code Tree Sitter" extension by `tree-sitter`. Then point it
    at `$NOVA_ROOT/tools/tree-sitter-nova/` as a custom grammar
    source.
  - **TextMate fallback**: the `tools/vscode-nova/` extension ships a
    TextMate grammar (`syntaxes/nova.tmLanguage.json`) that runs
    without tree-sitter. Lower fidelity (no incremental reparse) but
    works in plain VS Code.

## Section 3 -- Install nova-lsp

The Language Server Protocol implementation lives in
`tools/nova-lsp/`. It is pure Python; no `pip` dependencies beyond
setuptools for the editable install.

### Create a virtualenv

```bash
python3 -m venv $HOME/.local/share/nova-tools-venv
source $HOME/.local/share/nova-tools-venv/bin/activate
```

### Install nova-lsp in editable mode

```bash
cd $NOVA_ROOT/tools/nova-lsp
pip install -e .
which nova-lsp        # should resolve inside the venv
```

### Wire to VS Code

In your workspace `.vscode/settings.json`:

```json
{
  "nova.lsp.serverPath": "/home/<you>/.local/share/nova-tools-venv/bin/nova-lsp",
  "nova.lsp.args": [],
  "nova.compiler.path": "/path/to/NOVA/bin/nova"
}
```

Open a `.nova` file. The LSP should start; the VS Code status bar
will show "Nova" in the lower-right when the language server is
attached.

### Features now available
After step 3 is complete you get:
  - Hover docs (R3+).
  - Go-to-definition (R3+).
  - Workspace symbol search.
  - Rename (R32E) -- workspace-wide for top-level fn / let / const /
    type; single-buffer for locals + params.
  - Code lens for imports + test functions (R33D).
  - Document links (R33D).
  - Semantic tokens (R29D) -- variable / function / type / namespace
    / keyword / string / number / comment / parameter / constant /
    class / property, with declaration / readonly / static /
    deprecated modifiers.
  - Inlay hints (R30E) -- type inlay for `let x = ...` and parameter-
    name inlay for call sites.
  - Code actions (R35F) -- organize imports (dedup + sort) and
    extract function (with R36D scope-aware variable analysis if
    R36D has landed in your tree).

## Section 4 -- Install nova-dap

The Debug Adapter Protocol server lives in `tools/nova-dap/`. It
talks gdb's machine interface (`-mi3`) and translates DAP requests
into gdb commands.

### Install in the same venv

```bash
source $HOME/.local/share/nova-tools-venv/bin/activate
cd $NOVA_ROOT/tools/nova-dap
pip install -e .
which nova-dap
```

### Verify gdb + objdump

```bash
gdb --version           # 9.0 or later
objdump --version       # GNU binutils
```

### `.vscode/launch.json` -- one-shot launch

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

`program` must be the path to a compiled NOVA binary. Compile it
first with `nova compile path/to/main.nova -o build/myprogram` (or
the equivalent `make` target).

### Features now available
After step 4 is complete you get:
  - **Source-line breakpoints** -- click in the gutter.
  - **Function breakpoints** (R3+).
  - **Exception breakpoints** (R33F) -- breaks on uncaught panics
    (SIGABRT / SIGSEGV / SIGFPE / SIGBUS / SIGILL).
  - **Instruction breakpoints** (R17F + R35E) -- break on a specific
    machine address; supports condition + hit count + DAP-spec offset.
  - **Conditional + hit-count breakpoints** (R29E) -- `condition:
    "x > 10"` and `hitCondition: "%5"` (every 5th hit).
  - **Reverse debug** (R31E) -- step backward through execution.
    Requires gdb with reverse-debugging support.
  - **Profiler** (R28F) -- sample-based; surfaces as a profile view
    in the IDE.
  - **Disassembly view** (R34F) -- objdump-driven; shows machine
    code at the current instruction pointer with source-line
    annotations.
  - **Step in / over / out** at line OR instruction granularity.
  - **Watch expressions** + **Locals** + **stack trace** in the
    sidebar.

## Section 5 -- Install the VS Code extension (recommended)

R37D ships `tools/vscode-nova/` as a single installable VSIX. It
bundles the TextMate grammar, an LSP client wired to `nova-lsp`,
and a DAP client wired to `nova-dap`. After Sections 1 + 3 + 4
above have set up VS Code and the Python venv, the extension
replaces the manual VS Code-side wiring of Sections 2 (extension
directory symlink) and Section 5's old `.vscode/settings.json`
boilerplate.

### Build + install the VSIX

```bash
cd $NOVA_ROOT/tools/vscode-nova
./scripts/build-vsix.sh
# Build output: ./nova-language-0.1.0.vsix
code --install-extension nova-language-0.1.0.vsix
```

If you don't have `npm` available, you can also load the extension
in dev mode by symlinking:

```bash
ln -s "$NOVA_ROOT/tools/vscode-nova" \
    "$HOME/.vscode/extensions/crossengin.nova-language-0.1.0"
```

### Point the extension at your venv

In your workspace `.vscode/settings.json`:

```jsonc
{
  // Path to the venv interpreter where `pip install -e tools/nova-lsp
  // tools/nova-dap` was run. The extension spawns
  // `python -m nova_lsp` and `python -m nova_dap` from this.
  "nova.python.path":
    "/home/<you>/.local/share/nova-tools-venv/bin/python",
  "nova.compiler.path": "/path/to/NOVA/bin/nova"
}
```

You do NOT need to set `nova.lsp.serverPath` -- the extension uses
`python -m nova_lsp` rather than a standalone `nova-lsp` binary.

### Bring it all together

In your NOVA project workspace:

```
my-project/
├── .vscode/
│   ├── settings.json     # nova.python.path + nova.compiler.path
│   └── launch.json       # nova-dap launch config
├── src/
│   └── main.nova
├── tests/
└── build/                # compile output here
```

Open the workspace in VS Code. Open `src/main.nova` -- the LSP
attaches; hover, goto, rename, inlay all work. Compile via the
integrated terminal:

```bash
nova compile src/main.nova -o build/main
```

Then F5 to launch under the debugger. Set a breakpoint by clicking
the gutter; right-click the breakpoint for the conditional-breakpoint
dialog. The R34F disassembly view is on the View menu -> Open View
-> "Disassembly View".

### Marketplace listing (TBD)

R37D produces a local-install VSIX, not a Marketplace listing.
Marketplace publish requires a registered publisher namespace and
a Personal Access Token; that step is tracked in
[`tools/vscode-nova/README.md`](../tools/vscode-nova/README.md)
under "Roadmap" and is deferred to a future round. Until then,
distribute the VSIX by hand (or attach it to a GitHub release).

## Section 5b -- Advanced: bypass the extension

If you want to wire `nova-lsp` and `nova-dap` directly without
installing the VSIX (for example, you're using a different editor,
or developing the servers themselves), Sections 2-4 above are
self-contained: the tree-sitter grammar, `nova-lsp` venv install,
and `nova-dap` venv install all work without the VS Code extension.

For VS Code specifically, the legacy wiring uses
`nova.lsp.serverPath` (pointing at the `nova-lsp` console script)
plus a `launch.json` that omits the bundled extension's debugger
type registration. The R37D extension supersedes that path; both
approaches read the same Python venv and produce identical
diagnostics + debug behavior.

## Section 6 -- Troubleshooting

### LSP doesn't start / "Nova" not in status bar
  - Open the Output panel and select "Nova Language Server" from the
    dropdown. The Python traceback will be there if pygls or our
    LSP fails to start.
  - Check `nova.lsp.serverPath` in `.vscode/settings.json` -- the
    venv path is workspace-specific.
  - Confirm `python3 -c "import pygls"` succeeds inside the venv.

### DAP can't find binary
  - Confirm `nova compile` produced a binary at the path you wrote
    in `launch.json`'s `program` field.
  - Confirm `gdb path/to/binary` opens it without complaining about
    missing debug info. NOVA emits `.debug_line` on Linux ELF (see
    `DWARF_AUDIT.md` in this repo).
  - If gdb says "no such file," it's almost always a `cwd`
    mismatch -- the binary path in `program` is relative to `cwd`.

### Tree-sitter doesn't highlight
  - Confirm `tree-sitter test` passes in
    `tools/tree-sitter-nova/`.
  - In VS Code: Output panel -> "Tree Sitter" channel for parser
    errors.
  - Fall back to the TextMate grammar bundled in
    `tools/vscode-nova/syntaxes/`.

### Reverse debug doesn't work
  - Requires gdb with reverse-debugging support compiled in.
    `gdb --configuration` should mention `--enable-record-btrace`
    or similar. Most distro gdb packages have this; some minimal
    builds don't.
  - The first reverse-step from any point requires the recording to
    have been turned on (DAP handles this automatically when the
    user requests reverse).

### Inlay hints don't appear
  - VS Code's inlay-hint feature must be enabled globally:
    `editor.inlayHints.enabled: "on"`.
  - LSP must be running (check the status bar).

### Code actions don't show the lightbulb
  - VS Code's code-action feature must be enabled. Check
    `editor.lightbulb.enabled`.
  - Some R35F actions only fire when the selection / cursor is in a
    position where the action applies (e.g. organize-imports only
    fires when there are imports to organize).

## Section 7 -- Where to go next

  - `docs/GETTING_STARTED.md` -- get NOVA itself running first.
  - `docs/LANGUAGE_REFERENCE.md` -- the NOVA language reference.
  - `docs/adr/0004-lsp-dap-tooling-strategy.md` -- why we picked
    Python + tree-sitter (and what's deferred).
  - `tools/nova-lsp/README.md` -- LSP capability matrix per round.
  - `tools/nova-dap/README.md` -- DAP request matrix.
  - `tools/tree-sitter-nova/README.md` -- grammar coverage.
  - `tools/vscode-nova/README.md` -- extension structure.

## Section 8 -- Honest limitations summary

  - **No Marketplace listing yet.** R37D ships a local-install
    VSIX (`tools/vscode-nova/scripts/build-vsix.sh`) but the
    Marketplace publish step (registered publisher + PAT + verified
    namespace) is deferred. Distribute the VSIX by hand or attach
    it to a GitHub release until then.
  - **Tree-sitter WASM not bundled in the VSIX.** The R37D extension
    uses the TextMate grammar (`syntaxes/nova.tmLanguage.json`,
    ~80% surface coverage) for highlighting. Bundling the
    tree-sitter WASM parser is deferred to v0.2 (planned R38+).
  - **Tree-sitter lags closure grammar.** R35C closure literals
    show as syntax error in the IDE until the grammar update lands
    (planned R37+).
  - **LSP runs on a Python dependency.** A new contributor needs
    Python 3.10+ for IDE features. CLI compile + run does NOT need
    Python.
  - **DAP profiler is sample-based.** Not a flame graph in the
    style of perf+FlameGraph; if you want production profiling,
    use `perf` directly against the NOVA binary.
  - **Windows / macOS IDE setup follows the same pattern but with
    platform-specific gdb installation.** See
    `docs/GETTING_STARTED.md` for the platform setup; the LSP / DAP
    venv + VS Code wiring is identical across platforms.
