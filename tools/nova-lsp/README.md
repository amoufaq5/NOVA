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
| `textDocument/codeAction`           | yes (extract function via a dedicated `extract_function.py` analysis pipeline, organize imports, sort fn declarations, plus a `quickfix` for R17A's exhaustiveness WARN that auto-adds stub arms for missing variants) |
| `textDocument/definition`           | yes (intra-file + follows `import "..."` transitively) |
| `workspace/symbol`                  | yes (fuzzy name search across every indexed `.nova` file) |
| `textDocument/semanticTokens/full`  | yes (variable / function / type / namespace / keyword / string / number / comment / parameter / constant with declaration / readonly / static modifiers) |
| `textDocument/semanticTokens/range` | yes (same classifier, filtered to the requested line range) |
| `textDocument/prepareCallHierarchy` | yes (resolves cursor to a `CallHierarchyItem` for a top-level `fn`; cross-file via imports + workspace index) |
| `callHierarchy/incomingCalls`       | yes (every `name(` call site across the workspace, grouped by enclosing top-level fn) |
| `callHierarchy/outgoingCalls`       | yes (every top-level fn called inside the source body, resolved via imports + workspace index; builtins elided) |
| `textDocument/inlayHint`            | yes (parameter-name ghost text at call sites + literal-RHS type hints on `let` bindings; viewport-range filtered, callee resolved via imports + workspace index, builtins elided) |
| `textDocument/codeLens`             | yes (annotations above top-level declarations — "N references" on fn / let / const, "N variants used" on enum, with optional "/ tested" marker when a `tests/test_*.nova` mentions the decl; cross-file refs via imports + workspace index) |
| `codeLens/resolve`                  | yes (pass-through — eagerly resolved by `textDocument/codeLens`; schema retained for forward compatibility) |
| `textDocument/prepareTypeHierarchy` | yes (resolves cursor to a `TypeHierarchyItem` for an enum / struct / type alias declaration, an enum variant via `Name::Variant`, or a cross-file use site; falls through imports + workspace index) |
| `typeHierarchy/supertypes`          | yes (enum / struct -> empty; `type T = U` -> the `U` base resolved to its decl or a builtin placeholder; enum variant -> the parent enum) |
| `typeHierarchy/subtypes`            | yes (enum -> every declared variant as an `EnumMember` item; `type Base = ...` -> every other `type X = Base` alias in the workspace; struct + variant -> empty) |

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

Code actions surface four refactorings via the VS Code lightbulb menu:

* **Extract to function `extracted_N`** (`refactor.extract`) — only
  shown when the selection covers a multi-statement block (≥2
  non-empty lines) inside a `fn` body. The R21F implementation
  lives in `nova_lsp/extract_function.py` with three small,
  individually-testable pieces: `analyze_selection(uri, range,
  doc_text)` classifies the selection and harvests the **free
  variables** — identifiers READ inside the selection but DECLARED
  in the enclosing scope (parameters + lets above the selection);
  `compute_next_extracted_name(doc_text)` walks the buffer for
  every `extracted_<N>` token and returns the next free counter
  (so a second extract in the same session doesn't collide with
  the first); `build_extract_edit(info, doc_text, name)` packages
  the call-site replacement and a fresh top-level `fn
  extracted_N(<free_vars>)` helper into a `WorkspaceEdit`. The
  helper lands at file top-level (just below the last `import` line)
  so every extracted helper groups together; falls back to "after
  the enclosing function" when the file has no imports. The action
  is rejected when the selection is empty, single-line, spans a
  function boundary, or sits outside any function body — the
  lightbulb stays clean of no-op extracts. Variables WRITTEN inside
  the selection and read AFTER it are not yet propagated as return
  values (tracked as R21F.2).
* **Organize imports** (`source.organizeImports`) — sorts the top-of-file
  `import "..."` block alphabetically and groups it: `std/` first,
  then `../src/`, then `../../tests/`, others last. Blank lines
  separate adjacent groups.
* **Sort top-level functions** (`source.organizeFns`) — sorts every
  top-level `fn` declaration by name, preserving each declaration's
  leading doc-comment block.
* **Add missing match arms** (`quickfix`) — only shown when the client
  forwards R17A's `non-exhaustive match on E (missing: V1, V2)` WARN
  in `context.diagnostics`. The server resolves the enum's variants
  (same file, transitively imported file, or sibling indexed by the
  workspace symbol index), subtracts the variants already covered by
  existing arms, and inserts a stub arm for each remaining variant
  with `_` placeholders matching the variant's payload arity. The
  insertion lands just above the existing `_` catch-all when one is
  present; otherwise just above the closing `}` of the match. Each
  generated arm body is `/* TODO */` so the user sees an obvious
  spot to fill in. Example: a match on `Shape` that only covers
  `Circle + Triangle` gets `Shape::Rect(_, _) => /* TODO */`
  inserted between the existing arms and the closing brace.

All four return `WorkspaceEdit`s in the `{"changes": {uri: TextEdit[]}}`
shape, which VS Code applies in-place without diff reconciliation. The
quickfix's diagnostic is round-tripped on the `CodeAction.diagnostics`
field so the editor highlights the squiggle as fixable in the gutter.

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

Call hierarchy (`textDocument/prepareCallHierarchy`,
`callHierarchy/incomingCalls`, `callHierarchy/outgoingCalls`) renders
the "Show Call Hierarchy" tree in VS Code (and the equivalent panel in
other editors), letting the user navigate caller / callee chains
without leaving the editor. `prepareCallHierarchy` resolves the
cursor's identifier to a top-level `fn` — same-file declaration first,
then transitively imported files, then the workspace index for
siblings outside the import graph; non-fn identifiers (variables,
parameters, builtins, keywords) return `null` so the editor disables
the action. `incomingCalls` walks every indexed file plus open-buffer
import closures, regex-scans for `name(` call sites with string +
comment masking, and groups results by the enclosing top-level `fn`
(one tree node per caller fn, with `fromRanges` listing every call
site inside it). `outgoingCalls` parses the source fn's body
(brace-counted from the `{` on the signature line), scans for
identifier-followed-by-`(` patterns, elides keywords (`if`, `while`,
`return`, ...) and the declaration token itself, then resolves each
callee through the same import-graph + workspace-index path used by
`prepareCallHierarchy`. Builtins (`println`, `len`, `list_new`, ...)
are omitted from outgoing calls because they have no navigable source
location. Recursion is preserved in both directions — `fib` calling
itself shows up under both incoming and outgoing.

Inlay hints (`textDocument/inlayHint`) render parameter-name ghost
text inline at call sites so a reader can tell which positional
argument maps to which declared parameter without jumping to the
definition. For a call like `process(input, true, 42)` where the
declaration is `fn process(data, verbose, count) {...}`, the editor
draws three dimmed labels — `data:`, `verbose:`, `count:` — at each
argument's starting column. Callee resolution shares R5F's
`find_definition` over the transitive import graph, falling back to
R8C's workspace symbol index for sibling files outside the graph;
builtins (`println`, `len`, ...) and other unresolved names produce
no hints rather than guessing. The argument-position parser tracks
paren / bracket depth so nested calls (`foo(bar(c), d)`) and
multi-line argument lists are handled correctly; commas inside
strings or comments are masked out so a quoted `","` doesn't split
arguments. Variadic / mismatched arg counts cap the hint count at
`min(args, params)` so we never label an argument with the wrong
parameter. Hints are filtered to the requested viewport `range`
(start-inclusive, end-exclusive) so the wire payload stays small on
big files. Explicitly named arguments (`foo(y: 2)`) are skipped to
avoid the redundant double-label. A bonus pass adds Type-kind hints
on `let x = <literal>` bindings — integer / float / string / bool /
nil literals expand to `: int` / `: float` / `: str` / `: bool` /
`: nil` directly after the name token. Anything more complex than a
clean single-literal RHS (arithmetic, function calls, identifier
references) is left unannotated since type-inferring those would
require a real type checker.

Code lenses (`textDocument/codeLens` + `codeLens/resolve`) render
clickable summary annotations on a synthetic line **above** each
top-level declaration — unlike inlay hints, they never shift any
source position so they're safe to enable on dense code. The lens
title encodes the workspace-wide reference count: `"N references"`
on a top-level `fn`, `"N readers"` on a `let` / `const`, and
`"N variants used"` on an `enum` (counting *distinct* declared
variant constructors referenced via `Name::Variant` or
`Name.Variant`). When a `tests/test_*.nova` file mentions the
declaration's name (whole-word, comments + strings masked), the
title gains a `" / tested"` suffix so the IDE doubles as a coverage
hint. Counts are gathered through the same union of files used by
the workspace rename (R9C): the indexed workspace plus every open
buffer's transitive import closure, with comments and string
literals masked so a name in a doc comment never inflates the
number. The declaration line itself is subtracted from the count so
an unused fn shows `"0 references"` rather than `"1 reference"`. The
lens's `command` field is wired to VS Code's
`editor.action.showReferences` action so clicking the lens opens the
references panel for the declaration's position. `codeLens/resolve`
is a no-op pass-through in this implementation (the initial lens
response already carries a fully-populated `command`), but the
`resolveProvider: true` advertisement keeps the wire shape
forward-compatible with a future round that might offload expensive
work to the resolve path.

Type hierarchy (`textDocument/prepareTypeHierarchy`,
`typeHierarchy/supertypes`, `typeHierarchy/subtypes`) renders the
"Show Type Hierarchy" tree in VS Code (and the equivalent panel in
other editors), letting the user navigate sub/supertype edges from
the cursor without leaving the editor. `prepareTypeHierarchy`
resolves the cursor's identifier to a top-level `enum`, `struct`, or
`type` declaration — same-file first, then transitively imported
files, then the workspace symbol index for siblings outside the
import graph. A cursor sitting on a `Name::Variant` use site
resolves to either the enum decl (when on the `Name` token) or to an
`EnumMember`-kind variant item (when on the `Variant` token), so the
hierarchy view can fold open either way around. Non-type identifiers
(function names, let bindings, parameters) return `null` so the
editor disables the action.

`supertypes` follows NOVA's three-rule type model: enums and structs
have no supertypes (NOVA has no formal inheritance) so the response
is an empty list; a `type T = U` alias surfaces `U` as a parent — if
`U` is itself a declared type, the lookup walks the import graph
plus workspace index, otherwise (for primitives like `int` / `str` /
`bool` / `float` / `Option` / `Result`) we emit a synthetic
placeholder item so the editor still renders a node in the tree; an
enum variant item resolves up to its parent enum via the
`data.parent_name` handle recorded at prepare time. `subtypes`
implements the inverse direction: an enum expands to its declared
variants (each emitted as an `EnumMember`-kind TypeHierarchyItem
with the qualified name `Enum::Variant` and the variant's arity
encoded in `detail`); a `type Base = ...` alias enumerates every
other `type X = Base` alias in the workspace so chains like `type
ID = int` / `type UserID = ID` navigate cleanly; struct + variant
items are leaves (empty list). Both directions reuse R5F's
`FileCache` + R8C's `WorkspaceSymbolIndex` for the candidate file
set, with comments and string literals masked so a name inside a
doc comment never inflates the result.

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
python tools/nova-lsp/tests/test_call_hierarchy.py
python tools/nova-lsp/tests/test_inlay_hints.py
python tools/nova-lsp/tests/test_code_lens.py
python tools/nova-lsp/tests/test_type_hierarchy.py
python tools/nova-lsp/tests/test_exhaustiveness_fix.py
```

The fourteen `tests/*_smoke.py` / `test_*.py` scripts use the bundled
`_harness.py` helper to drive `dispatch()` in-process (no subprocess),
open a tiny workspace, and assert on the response payloads. They run in
~10 ms each (the workspace-symbol + workspace-rename + call-hierarchy
integration legs also index `/home/user/NOVA/src/` so they take a bit
longer when the tree is present).

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
    call_hierarchy.py       # prepare / incoming / outgoing call resolver
    inlay_hints.py          # parameter-name + literal-type inlay hints
    code_lens.py            # reference-count annotations above decls
    type_hierarchy.py       # prepare / supertypes / subtypes resolver
    exhaustiveness_fix.py   # quickfix: auto-add missing match arms
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
    test_call_hierarchy.py
    test_inlay_hints.py
    test_code_lens.py
    test_type_hierarchy.py
    test_exhaustiveness_fix.py
```
