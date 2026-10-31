# ADR 0009: Module system and symbol mangling for module-private names

## Status

Proposed (2026-10-06). Blocks closure of Crossengin UPSTREAM_NOVA_BUGS §9 + §10
(duplicate module-private symbol collisions on `fn _starts_with` and
`let PC_TAG`).

## Context

NOVA today has **no module system in the compiler's internal model**,
only in the user-facing `import "path/to/file.nova"` syntax. Both the
Makefile build (`cat $(SRC) > /tmp/combined.nova`) and the
`preprocess_imports` path in `src/compiler/compiler.nova` concatenate
all `.nova` sources into a single character stream before the lexer
runs. Downstream of that point, every `fn` and every module-level
`let` is a flat top-level declaration, and `cg_source_file` at
`src/compiler/compiler.nova:774` is set once to the combined-file name.
`src/compiler/codegen.nova:2787-2791` defines a `fn_module(name)`
getter — it is dead code; `cg_fn_modules` is written but never read
during call resolution.

The user-level `_`-prefix convention (`fn _starts_with`, `let _X`) is a
style guideline, not a scope. The lexer, parser, and codegen all treat
`_starts_with` in `src/persistence/snapshot_disk.nova:1789` and
`_starts_with` in `src/io/transducers/kg_sync.nova:410` as the **same
globally-visible symbol**. Codegen emits `.globl _starts_with` for
each; the assembler's final link rejects the duplicate.

This design has worked for the first ~15k LOC of NOVA + stdlib because
every pair of modules has, by convention, used a sufficiently unique
private name (`_gossip_starts_with`, `_sr_starts_with`,
`_att_starts_with`, `_fed_env_str`, `_cd_env_str`, …). As the ecosystem
grows (CrossEngin alone has ~177 `.nova` source files), the
convention-pressure to disambiguate every private name is high and
easy to break. Two concrete collisions have been documented and
worked around by user-side renames
(`docs/UPSTREAM_NOVA_BUGS.md:139-241`); a third class of collision
(module-level `let` globals, bug #10) was discovered the same week.

Phase-1 scoping (`a3c06528587c2f6cf`, `adcdffbf8ca150780`) estimated
the mechanical cost of the fix as **~115 LOC** spanning:

- `preprocess_imports` injects `//#file <path>` sentinels at each
  file boundary (~2 LOC).
- Lexer recognizes `//#file <path>` as a special comment that updates
  a `lex_file` state (~8 LOC).
- Parser threads a `par_cur_file` global onto every `AST_FN_DECL`,
  `AST_LET_STMT`, and `AST_CALL` node (~15 LOC + 10 LOC parser globals).
- Codegen adds a `mangle_private(name, file)` helper that mangles
  `_`-prefixed names with the module path, respecting an allowlist of
  reserved names (`main`, `_start`, `_user_main`, extern fns,
  `_g___argc/__argv/__envp`, compiler-generated runtime labels)
  (~25 LOC + wraps on six emission sites).
- Codegen call-resolution builds a per-file symbol table in a
  two-pass flow (collect decls → resolve calls) and falls back to the
  global `cg_fns_ht` for public (non-`_`-prefixed) names (~40 LOC).
- Debug info (`cg_dbg_fns`) and TCO labels (`cg_tco_label`) must
  consume the mangled name consistently (~15 LOC regression surface).

The 115 LOC spans files that are already in daily rotation and have
no visibility-related tests. Landing the plumbing alone — without a
user-facing `pub`/`mod` design — risks:

- An ABI shift (DWARF names change for every private fn) that
  downstream tooling (`addr2line`, debuggers) will see as a one-time
  break.
- The appearance of a module system without the semantics (no
  per-file imports, no re-exports, no cycle-free dependency DAG
  statement) that would normally accompany it.

## Decision

**NOVA adopts a module system with explicit visibility semantics,
and the Bug #9/#10 symbol-mangling fix lands as part of that system
— not as a standalone compiler patch.**

The decision reasons:

1. **The `_`-prefix convention becomes real syntax.** Any `fn` or
   module-level `let` whose name starts with `_` is PRIVATE to the
   containing `.nova` file. External modules cannot call or reference
   it. Non-`_`-prefixed names are PUBLIC and participate in the
   global symbol namespace. This matches the lint-level convention
   NOVA has used since R0 and avoids introducing a new `pub` keyword.
2. **A file is a module.** The unit of visibility is the `.nova` file.
   No nested `mod { }` scopes, no re-exports, no package granularity
   smaller than a file. This matches the shape of the existing
   `import "path/file.nova"` syntax.
3. **Mangling scheme: full-path.** Private names are mangled with the
   full source file path, converted to a C-identifier-safe form
   (`/` → `__`, `.` → `_`), prefixed with the module tag. Example:
   `fn _starts_with` in `src/persistence/snapshot_disk.nova` emits
   label `_m_src_persistence_snapshot_disk__starts_with`. Module-level
   `let` globals use the same scheme with a `_g_` prefix retained:
   `let PC_TAG = 1` in `src/parts/reasoning/proof_checker.nova`
   emits `_g_m_src_parts_reasoning_proof_checker__PC_TAG`.
4. **Reserved names skip mangling.** `main`, `_start`, `_user_main`,
   `mainCRTStartup`, every `AST_EXTERN_FN` name, `_g___argc`,
   `_g___argv`, `_g___envp`, and compiler-generated runtime labels
   (`_nova_*`, `_str_*`, `_strlit_*`, `_heap_*`, `_fc_*`, `_cap_*`)
   emit at their current flat labels. The mangle function consults
   `cg_externs` and a hard-coded reserved allowlist.
5. **Call resolution matches module scope first, global second.** At
   each `AST_CALL` lowering, the compiler looks up `(cur_file, name)`
   in a per-file symbol table. On miss, falls back to the global
   `cg_fns_ht` for public names. Cross-module calls to private names
   are a compile error (uses the Bug #8 `cg_fail` helper already
   landed in `0f9d3f2`).
6. **Preprocessor injects `//#file <path>` sentinels** at each file
   boundary during `preprocess_imports` and the `cat $(SRC)` build
   step. The lexer recognizes these as a special comment and updates
   `lex_file`. Every token thereafter carries `lex_file`. Every AST
   node derived from a token inherits `par_cur_file`.
7. **Self-host fixpoint is preserved** because every call site in
   NOVA's own compiler + stdlib + runtime currently uses a globally
   unique name (verified by the fact that `make self-host` passes).
   The mangling pass is a bijective rename of private symbols; stage2
   and stage3 both produce the same mangled labels, so `cmp stage2.s
   stage3.s` holds.
8. **Downstream tooling break is accepted as a one-time cost.**
   Debug symbols for private fns gain the module-path prefix. Tools
   consuming NOVA's DWARF/asm output (profilers, debuggers, `nm`
   consumers) will see new names once per rebuild — no runtime
   behavior change, but a cosmetic one.

## Consequences

### Positive

- Bugs #9 and #10 are mechanically closed. No further user-side
  renames of private colliding helpers; the compiler handles it.
- The user-side workarounds for the two known collisions
  (`_snap_starts_with` in `snapshot_disk.nova`, `PROOF_CHECKER_TAG` in
  `proof_checker.nova`) can be reverted once the system lands.
- Cross-module calls to private names fail at compile time with a
  clean error (via `cg_fail`), catching a class of accidental
  dependency that today SEGVs or silently uses a colliding symbol.
- NOVA stops being a flat global namespace. The `_`-prefix becomes a
  load-bearing part of the language, matching Go's lowercase-is-private
  and Rust's `pub`-is-public conventions.

### Negative / risks

- **One-time ABI shift**: DWARF names for private fns change. Any
  cached profiler data referring to old names becomes stale.
- **Build system coupling**: the preprocessor must inject correct
  `//#file` sentinels. A mismatch between the Makefile's `cat $(SRC)`
  ordering and the `preprocess_imports` ordering would produce a
  mangled-name mismatch between stage2 and stage3 — self-host would
  fail. Fix is to centralize the injection in `preprocess_imports` and
  remove the Makefile's bare `cat`.
- **Debug step-through friction**: `step` in a debugger that lands on
  `_m_src_parts_reasoning_proof_checker__PC_TAG` needs tooling that
  understands the prefix. One-line unmangle in debugger config
  (`demangle: strip _m_ + replace __ with /`) is adequate.
- **No re-exports means cross-file private sharing is impossible**.
  If two files legitimately need to share a `_private` helper, the
  design forces one of them to be the owner and the other to call
  the public name. This is a stylistic constraint, not a semantic
  limitation, and matches NOVA's existing single-file-is-the-unit
  convention.

### Explicit non-goals

- NOVA does NOT grow a `pub` keyword. The underscore convention IS
  the keyword.
- NOVA does NOT grow nested `mod { }` scopes.
- NOVA does NOT grow re-exports (`pub use`).
- NOVA does NOT grow a package manager or a dependency DAG checker.
  Modules form whatever graph the user `import`s; cycles are detected
  (or not) by the existing import handling.
- The mangling scheme does NOT need to be stable across NOVA
  versions. If a future round changes `_m_` to `_nmod_`, that is a
  rebuild-everyone one-time break, acceptable per the ABI-shift
  policy.

### Implementation plan (reference, not binding on this ADR)

A single landing round sized at ~115 LOC + regression tests:

1. `preprocess_imports` (`src/compiler/compiler.nova:549-604`) emits
   `//#file <path>\n` before each imported file's content.
2. Lexer (`src/compiler/lexer.nova:211-219`) recognizes `//#file ` at
   column 0 and updates a global `lex_file` variable.
3. Tokens carry `lex_file`; parser threads it to AST nodes.
4. Codegen `mangle_private(name, file)` helper + 6 emission-site
   wraps + per-file symbol table + two-pass call resolution + extern
   allowlist.
5. Debug info + TCO labels consume mangled names.
6. `tests/test_module_private_collisions.nova` — two separate files
   with `fn _starts_with` + `let X`, link in one `main` → must
   compile without duplicate-symbol errors. New harness convention
   if needed (multi-file test).

The round should ship on `claude/confident-fermi-op241b` or a fresh
branch after this ADR is Accepted. Phase-1's 115-LOC estimate stands.

### Related

- Crossengin UPSTREAM_NOVA_BUGS §9 (`_starts_with`) and §10 (`PC_TAG`).
- Crossengin workaround commits: `01a843f` (snapshot_disk rename),
  `be4130b` (proof_checker rename), `85667a7` (dead-constant removal).
- NOVA Bug #8 fix (`0f9d3f2`) landed the `cg_fail` helper that this
  design will reuse for cross-module-private-call errors.
- NOVA ADR-0002 (self-hosting). This design must not break stage2 ==
  stage3 fixpoint.
