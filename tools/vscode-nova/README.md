# Nova VS Code Extension

Syntax highlighting and editor configuration for the [Nova](https://github.com/nova-lang/nova) programming language.

## Features

- Syntax highlighting for `.nova` files (keywords, builtins, strings, numbers, comments).
- Auto-closing pairs and bracket matching.
- Comment toggling (`Ctrl+/`).
- Indentation rules.
- Configuration hooks for the `nova-lsp` language server (diagnostics + hover).

## Install (from VSIX)

The extension is published as a `.vsix` bundle. To build and install locally:

```bash
cd tools/vscode-nova
npm install -g @vscode/vsce
vsce package
code --install-extension nova-syntax-0.1.0.vsix
```

(The `vsce package` step produces the `.vsix` file in this directory.)

## Install (development mode)

Symlink the extension into VS Code's extensions directory:

```bash
ln -s "$(pwd)/tools/vscode-nova" ~/.vscode/extensions/nova-syntax-0.1.0
```

Reload VS Code; `.nova` files now highlight.

## LSP wiring

The extension declares the configuration keys `nova.lsp.enabled`,
`nova.lsp.serverCommand`, and `nova.compilerPath`. To wire up diagnostics
and hover support, install the LSP server from `tools/nova-lsp/`:

```bash
pip install -e tools/nova-lsp/
```

Then add to your `settings.json`:

```json
{
  "nova.lsp.enabled": true,
  "nova.lsp.serverCommand": "nova-lsp",
  "nova.compilerPath": "/path/to/bin/nova"
}
```

A small VS Code client snippet that launches the LSP server over stdio is
included in `tools/nova-lsp/README.md`.

## Grammar coverage

The TextMate grammar (`syntaxes/nova.tmLanguage.json`) highlights:

- **Keywords**: `fn`, `let`, `if`, `else`, `while`, `for`, `in`, `return`,
  `break`, `continue`, `import`, `extern`, `struct`, `asm`.
- **Builtins**: `print`, `println`, `eprint`, `print_int`, `alloc`, `len`,
  `substr`, `char_at`, `str_eq`, `int_to_str`, `str_to_int`, `list_new`,
  `push`, `pop`, `map_new`, `map_get`, `map_set`, the `__intrinsic_*`
  family, the `int_shl/shr/and/or/xor` ops, and the runtime memory
  helpers.
- **Types**: `int`, `str`, `list`, `float`, `bool`, `map`.
- **Literals**: decimal / hex / binary integers, double-quoted strings
  with escapes, booleans, `none`.
- **Comments**: line comments (`// ...`).
- **Inline assembly**: `asm { "..." }` blocks are highlighted as an
  embedded language region.
