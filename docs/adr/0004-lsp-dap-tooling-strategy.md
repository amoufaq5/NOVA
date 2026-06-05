# ADR 0004: LSP + DAP + tree-sitter tooling strategy

## Status
Accepted (R36F) -- documents the tools/ split that emerged across
R3 (initial LSP), R17F (instruction breakpoints), R28F (profiler),
R29D (semantic tokens), R29E (conditional breakpoints), R30E (inlay
hints), R31E (reverse debug), R32E (rename), R33D (documentLink /
codeLens), R33F (exception breakpoints), R34E (tree-sitter match
patterns), R34F (disassembly), R35E (instruction breakpoint condition
+ hit count), R35F (code actions).

## Context
NOVA needs IDE integration: syntax highlighting, hover docs, go-to-
definition, rename, breakpoints, step-through debugging, profiling.
A new language without IDE integration loses contributor mind-share
fast. The implementation choices for tooling were:

  1. **Write LSP / DAP servers in NOVA.** Pro: dogfooding; everything
     in one language. Con: NOVA's IO + JSON-RPC + LSP-spec
     vocabulary surface would have to be built before any LSP
     functionality could ship; the NOVA test infrastructure for an
     LSP would be substantial.
  2. **Write LSP / DAP servers in Python.** Pro: pygls + python-lsp
     infrastructure exists; rapid iteration; Python's editor /
     terminal tooling is mature. Con: a Python dependency for IDE
     use; two languages in the project.
  3. **Write LSP / DAP servers in Rust.** Pro: tower-lsp; fast cold
     start. Con: another major language in the project; tower-lsp
     is a moving target.
  4. **Write a full IDE fork.** Out of scope.

The substrate language (NOVA) and the tooling language are
independent decisions. The LSP / DAP servers do not run on the
substrate hot path; they run in the developer's editor.

For tree-sitter specifically: tree-sitter has a JavaScript-based
grammar DSL + a C runtime. The grammar is independent of the LSP
language.

## Decision
**LSP + DAP servers are Python (pygls); tree-sitter grammar is the
standard JS-DSL + C-runtime; NOVA toolchain remains NOVA.**

### Tools layout
  - `tools/nova-lsp/`: Python package, pygls-based. Implements the
    full LSP server: hover, goto-def, rename, semantic tokens,
    inlay hints, documentLink, codeLens, code actions.
  - `tools/nova-dap/`: Python package. Implements the Debug Adapter
    Protocol on top of gdb's machine interface (`-mi`). Source-line
    breakpoints, function breakpoints, exception breakpoints,
    instruction breakpoints, conditional / hit-count breakpoints,
    reverse debug, disassembly view, profiler integration.
  - `tools/tree-sitter-nova/`: standard tree-sitter grammar. JS-DSL
    `grammar.js` + generated C parser; bindings for Node, Rust,
    Swift, Python. Powers syntax highlighting in VS Code (via the
    tree-sitter-vscode integration) and any tree-sitter-aware
    editor.
  - `tools/vscode-nova/`: thin VS Code extension wiring up the LSP
    server, the DAP server, and the tree-sitter grammar.

### Why Python for LSP / DAP
  - **pygls** handles the JSON-RPC + LSP-spec plumbing; the LSP
    code is the LSP logic, not the protocol.
  - **gdb -mi** is a stable interface, and Python ecosystem has
    mature subprocess + line-protocol parsing.
  - **Iteration speed.** R29D semantic tokens + R30E inlay hints +
    R32E rename + R33D documentLink / codeLens + R35F code actions
    each shipped in one round because the LSP iteration loop is
    short.
  - **Test infrastructure.** pytest + pygls test helpers exist;
    `tools/nova-lsp/tests/` runs in seconds.

### Why tree-sitter for syntax highlighting
  - **Editor-agnostic.** Tree-sitter grammars run in VS Code, Neovim,
    Emacs (treesit), Helix, Zed -- one grammar, many editors.
  - **Incremental reparse.** Important for large NOVA files (the
    compiler's `parser.nova` is 16k lines).
  - **No NOVA-parser-fork required.** We write the syntax once in
    `grammar.js` rather than maintaining a separate "highlighter
    parser" in NOVA. Stays in lockstep with the real NOVA parser via
    test corpus (each tree-sitter round ships a test corpus that
    asserts shape).

## Consequences
**Positive.**
  - **Tooling iteration is fast.** Python's edit-run loop is
    seconds. A new LSP capability (R30E inlay hints) lands in one
    round.
  - **Editor reach is wide.** Tree-sitter buys us VS Code +
    Neovim + Helix + Zed + Emacs syntax for one grammar
    investment.
  - **DAP is feature-rich.** R17F instruction breakpoints, R28F
    profiler, R29E conditional, R30E inlay hints, R31E reverse
    debug, R33F exception breakpoints, R34F disassembly view, R35E
    instruction breakpoint conditions / hit counts are all in.
  - **LSP feature surface is competitive with mature languages.**
    Hover, goto, rename, codeLens, documentLink, semantic tokens,
    inlay hints, code actions all ship.

**Negative.**
  - **Python dependency for IDE use.** A new contributor needs
    Python 3.10+ to run the LSP / DAP. We document this in
    `docs/IDE_SETUP.md`.
  - **Two languages in the project.** NOVA for the compiler /
    runtime; Python for the LSP / DAP. The split is clean
    (`tools/` is the boundary), but the cognitive cost is real.
  - **Tree-sitter grammar lags the parser.** The R34E tree-sitter
    round shipped match-expression patterns AFTER R31D, R32D, R33C
    had landed in the NOVA parser. New language features land in
    the IDE on a delay. R35C closure literals are the current lag
    (deferred per R35C NEXT_SESSION.md).
  - **No marketplace listing.** `tools/vscode-nova/` is a working
    extension but not in the VS Code Marketplace; install is
    manual today (see `docs/IDE_SETUP.md`).

**Follow-up rounds.**
  - R37+: tree-sitter closure grammar update.
  - R37+: VS Code Marketplace publish flow.
  - R37+: Neovim LSP config docs.

## Alternatives considered
  - **LSP / DAP in Rust** (tower-lsp). Rejected: adds a third major
    language to the project; tower-lsp's API churn would slow us
    down.
  - **LSP / DAP in NOVA itself.** Rejected for tooling-iteration-
    speed reasons. We may revisit when NOVA's IO + JSON surface is
    richer.
  - **Skip tree-sitter; use TextMate grammar.** Rejected: TextMate
    grammars don't do incremental reparse and don't share across
    editors as cleanly.
  - **Skip DAP; tell users to use plain gdb.** Rejected: a modern
    debugging UX is part of being a real language. The DAP +
    gdb-mi shape ended up being a small Python codebase that
    delivers the full IDE-debugger experience.

## The Python-for-tooling pattern beyond NOVA
The same pattern applies to CrossEngin's training-loop scripts and
federation testbench: written in Python, called from shell or used
interactively. The substrate is NOVA; the developer-experience layer
is Python. The split is explicit, documented, and disciplined.
