# Changelog

All notable changes to the `nova-language` VS Code extension are
documented in this file.

The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
extension is versioned independently of the NOVA compiler itself.

## [0.1.0] - 2026-06-05 (R37D)

### Added
- Initial skeleton bundling NOVA editor support into a single
  VS Code extension.
- `package.json` manifest registering the `nova` language, `.nova`
  file extension, TextMate grammar, and the `nova` debugger type.
- `src/extension.ts` glue that:
  - Spawns `python -m nova_lsp` over stdio and attaches a
    `LanguageClient`.
  - Registers a `DebugAdapterDescriptorFactory` for `nova` that
    spawns `python -m nova_dap` on session start.
  - Reads user settings: `nova.python.path`, `nova.lsp.enabled`,
    `nova.lsp.module`, `nova.lsp.args`, `nova.dap.module`,
    `nova.dap.args`, `nova.trace.server`.
  - Probes the configured Python at activation to surface
    install hints early.
  - Reloads the LSP on `nova.*` configuration changes.
  - Exposes commands `nova.restartLanguageServer` and
    `nova.showOutput`.
- TextMate grammar (`syntaxes/nova.tmLanguage.json`) covering
  keywords, builtins, types, literals, comments, inline `asm`,
  and operators for ~80% of NOVA source surface as of R36C.
- Snippets (`snippets/nova.snippets.json`) for common control flow
  scaffolding (`fn`, `let`, `if`, `while`, `for`, `match`, `import`,
  `println`).
- Language configuration (`language-configuration.json`) for
  bracket pairs, auto-closing pairs, comment toggling, and
  indentation rules.
- Build script (`scripts/build-vsix.sh`) that runs
  `npm install` -> `npm run compile` -> `npx vsce package` and
  surfaces the resulting VSIX path.
- Smoke tests (`tests/`) validating the extension manifest, the
  TextMate grammar shape, and the build pipeline.
- `README.md` covering install via VSIX, manual install, the
  Python venv requirement, and the Marketplace-TBD note.

### Known limitations (v0.1.0)
- Tree-sitter WASM parser is NOT bundled. Syntax highlighting
  uses the TextMate grammar fallback. Tree-sitter integration is
  planned for v0.2.
- Marketplace publish path is NOT wired. Distribution is via
  `code --install-extension nova-language-0.1.0.vsix`.
- Python servers (nova-lsp, nova-dap) are NOT bundled. The user
  must install them into a venv and point `nova.python.path` at
  that venv's interpreter (or have `nova-lsp` / `nova-dap`
  resolvable via `python -m`).
- Closure literals (R35C) and tuple syntax (R36C) are partially
  covered by the TextMate grammar but use generic patterns; the
  tree-sitter grammar (when bundled) will give precise tokens.
