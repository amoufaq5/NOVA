# NEXT_SESSION.md — Nova Implementation Status

## R12E — Compiler optimization passes (constant folding + DCE)

NOVA's codegen had grown capabilities for several rounds without
acquiring a real **optimization pass**. R12E added two: explicit
AST-rewriting **constant folding** and **dead-code elimination**
that run between parse and codegen. Previously, codegen contained
embedded per-operator folding (`try_fold` / `is_foldable` helpers
called inline from `gen_expr`), which worked but had to be
re-implemented across each per-target lowering path (x86-64,
ARM64, WASM, etc). The new passes operate **once** on the AST
upstream of any target-specific codegen.

### What the passes do

**Constant folding** (`cg_fold_constants` in `codegen.nova`):

- Walks every expression node and replaces pure-int subtrees with
  a single `AST_INT_LIT` carrying the folded value.
- Operators folded: `+ - * / % & | ^ << >> ** == != < > <= >=`,
  unary `- ! ~`, plus deep nesting (`(2+3)*4` → `20` in one pass).
- Pre-folds operands inside `_cg_fold_eval` so that even when the
  top-level node isn't constant, its inner constants collapse.
- Walks into function-call args, list literals, map literals,
  struct initializers, lambdas, do-expressions, list/map
  comprehensions, ternary, and short-circuit `and`/`or` (these
  recurse into their pieces without folding the whole node when
  the condition can't be evaluated to a constant).
- **Skipped**: function-call results (`foo() + 1`), variable
  reads (`y + 1`), string concat / list ops (operands aren't int
  literals, so they never reach the int fold path), and division
  by literal zero (left as-is so the runtime trap fires as
  documented).
- **Two's-complement wraparound**: integer arithmetic is signed
  i64; folding preserves NOVA runtime semantics including
  wraparound on overflow.

**Dead-code elimination** (`cg_eliminate_dead_code`):

1. **Unreachable-after-terminator**: in any block (`AST_BLOCK`),
   statements following the first `return` / `break` / `continue`
   / `throw` are truncated. The walk descends through nested
   if/while/for/try-catch/match before truncating.
2. **Function-scope unused-let drop**: a top-level
   `let x = pure_expr` whose name is never read OR reassigned in
   the rest of the function body is removed. Purity gate: only
   literals, identifier reads, arithmetic over those, and pure
   field/index access qualify (calls, allocations, throws are
   impure and never folded out). Conservative: only top-level
   statements of each function body are examined, because NOVA
   `collect_locals` hoists every nested `let` to function scope,
   so reasoning about deeper nests is fragile.
3. **Constant-condition `if` / `while`**: NOT collapsed at AST
   level. NOVA allows `if`/`match` to appear in both statement
   and expression position (e.g. `let a = if c { x } else { y }`),
   and the AST doesn't distinguish those contexts. Rewriting in
   place would break the expression form. The existing per-target
   codegen already emits only the chosen branch when it sees a
   literal condition (`gen_stmt`'s `AST_IF_STMT` / `AST_WHILE_STMT`
   shortcuts), so the savings are preserved.

### Pipeline & flag

`compile()` in `compiler.nova` now reads:

```
parse → cg_fold_constants → cg_eliminate_dead_code → cg_init → gen_program
```

A new `--no-opt` flag (set `cg_no_opt = 1`) makes both passes
no-ops, useful when debugging codegen issues against the
unoptimized AST.

### Interaction with R6A's PTR_THRESHOLD root fix

R6A replaced the legacy `cmp rdi, 100000` pointer-vs-int heuristic
with **range-based** classification in `_nova_check_rdi` /
`_nova_check_rsi`: a NOVA value is a pointer iff its address lies
in `[_strlit_start, _strlit_end)` or `[_heap_base, _heap_end)` or
above 16 GiB. Crucially, this means folded integer literals like
`65792` are correctly classified as **integers** regardless of
their magnitude — they don't fall in any pointer range. Folding to
plain integer literals (rather than runtime smart-op values) is
safe; the classifier sees `mov rax, 5` exactly like it sees
`mov rax, X; mov rax, Y; add rax, rdi` for `5 = 2 + 3`.

### Bugs caught while implementing

The first iteration of DCE collapsed `if false { ... } else { ... }`
to a plain `AST_BLOCK` in place. That broke the `test_match_if_expr`
and `test_try_expr` tests because NOVA accepts `if` and `match` as
both statements AND expressions (e.g. `let a = if c { x } else { y }`,
`let b = match v { 1 => "a"; _ => "z" }`), and the parser builds
identical AST nodes in either context. Replacing the if-stmt with a
block would have left a block-typed value on the let's RHS, which
codegen doesn't accept as an expression. The fix was to leave the
collapse to the existing per-target codegen shortcut (which emits
inline code in both contexts correctly).

A second bug surfaced from the line-number suffix that
`parse_stmt` appends to every statement node. AST_TRY_CATCH has
shape `[tag, try, catch, err_var, line]` (5 elts, no `finally`) or
`[tag, try, catch, err_var, finally, line]` (6 elts, with
`finally`). The legacy `collect_locals` correctly uses `len(nd) > 5`
to gate `finally` recursion; the new fold/DCE passes initially used
`> 4`, which made them recurse into the line number (an int!) as
though it were a statement node and segfaulted. Fixed by aligning
with the existing convention.

A third bug: `_cg_dce_expr_uses` initially had no handlers for
`AST_MATCH_STMT` / `AST_IF_STMT` / `AST_TRY_CATCH`. Because those
can appear in expression position (RHS of a `let`), the function
needs to recurse into them via the stmt walker. Without that,
`let val = 3; let c = match val { ... }` would see `val` as unused
in the let-rhs `match val { ... }` and incorrectly drop the `let val`
binding. Fixed by reusing `_cg_dce_stmt_uses` for those tags.

### Changes

- `src/compiler/codegen.nova`:
  - `cg_no_opt` global flag.
  - `_cg_replace_with_int`, `_cg_fold_eval`, `cg_fold_expr`,
    `cg_fold_stmt`, `cg_fold_constants` — folding pass.
  - `_cg_dce_is_terminator`, `_cg_dce_is_pure`,
    `_cg_dce_expr_uses`, `_cg_dce_stmt_uses`,
    `_cg_dce_stmt_assigns`, `_cg_dce_has_assign_to`,
    `_cg_dce_count_reads_anywhere`,
    `_cg_dce_drop_unused_lets_in_fn`, `_cg_dce_block_stmts`,
    `cg_dce_stmt`, `cg_eliminate_dead_code` — DCE pass.
- `src/compiler/compiler.nova`:
  - `--no-opt` CLI flag.
  - `compile()` calls `cg_fold_constants` and
    `cg_eliminate_dead_code` between parse and codegen.
  - Updated `_print_usage` to mention `--no-opt`.
- `tests/test_const_folding.nova` (new): ~29 assertions verifying
  every fold path plus skipped cases (variables, function calls,
  division by literal zero).
- `tests/test_dce.nova` (new): ~15 assertions verifying every DCE
  category (return/break/continue/throw-terminated blocks, unused
  pure-let drop, constant-cond if/while at codegen level, impure
  RHS preservation).

### Verification

- `make test-all` → 157 passed / 0 failed / 6 skipped
  (was 155 / 0 / 6; +2 new test files = `test_const_folding` and
  `test_dce`). Zero regressions in the existing 155.
- `make self-host` → stage2.s bit-identical to stage3.s.
- `make test` → 5/5 pass.
- Cross-target builds verified: `smoke-windows`, `smoke-macos`,
  `smoke-wasm`, `smoke-winarm64`, `smoke-mobile-android`.
- CrossEngin unit tests → 160 / 0 passed (no regressions).
- `bench_fold.nova` microbench (30 stmts, mix of foldable and
  non-foldable): ~4.3% smaller .s with opt on
  (74374 vs 77712 bytes). Compiler self-compile: ~0.036%
  smaller (3280305 vs 3281488 bytes) — most of the compiler
  source already used literal constants where folding would
  apply.

### Future work

- Algebraic identity simplification at the AST level (`x + 0 → x`,
  `x * 1 → x`, `x * 0 → 0`). Subset already done at codegen
  level for pure-int ops; could be lifted to the AST pass.
- Common subexpression elimination (CSE).
- Loop-invariant code motion (LICM).
- Inlining of small leaf functions.
- AST collapse of `if 0 / 1 / true / false` conditions when the
  enclosing context can be statically determined to be statement
  (not expression) — requires the parser to annotate or a
  separate is-expression-context analysis.

---

## R11D — SIMD i32x8 codegen intrinsics

Added five explicit SIMD builtins for 8-lane int32 operations,
lowered directly by the compiler with per-target backends. These
unblock the CrossEngin hot paths called out in `SIMD_AUDIT.md`
(SAD blocks in stereo, autocorrelation, optical flow, ChaCha20
quarter rounds, SHA-256 schedules) without the AoSoA layout
migration that real auto-vectorization would require.

Builtins (all take raw 32-byte int32 buffers, caller-allocated
with `alloc(32)`):

| Builtin                              | Semantics                                          |
| ------------------------------------ | -------------------------------------------------- |
| `simd_add_i32x8(a, b, dst)`          | dst[i] = a[i] + b[i] for i in [0,8)                |
| `simd_sub_i32x8(a, b, dst)`          | dst[i] = a[i] - b[i] for i in [0,8)                |
| `simd_load_i32x8(src, dst)`          | 32-byte copy via YMM/Q register                    |
| `simd_store_i32x8(dst, src)`         | 32-byte copy, arg-swap of load                     |
| `simd_sum_abs_diff(a, b, n) -> int`  | sum(abs(a[i] - b[i]), i in [0,n)) -- SAD reduction |

Per-target lowering:

| Target                | `cg_target` | Lowering                                                  |
| --------------------- | ----------- | --------------------------------------------------------- |
| Linux x86-64          | 0           | AVX2 (`vpaddd`, `vpsubd`, `vpabsd`, `vmovdqu`, `vphaddd`) |
| macOS x86-64          | 1           | scalar 8-iter loop fallback (Rosetta / older Intel)       |
| WebAssembly (WASI)    | 2           | not emitted -- intrinsic call left dangling (see below)   |
| Windows x86-64        | 3           | scalar 8-iter loop fallback                               |
| ARM64 Linux           | 4           | NEON (2x 128-bit `add v0.4s` / `sub v0.4s` / `abs`)       |
| ARM64 Windows         | 5           | NEON (same NEON sequences as Linux ARM64)                 |

### Changes

- `src/compiler/codegen.nova`:
  * `is_builtin_fn` registers the five new builtins.
  * x86-64 ELF/PE/Mach-O runtime emit (after `__intrinsic_dot_i32`):
    each builtin labeled at top level, AVX2 body gated on
    `cg_target == 0`, scalar fallback otherwise. The AVX2 SAD path
    uses `vpabsd` (AVX2 baseline) inside the inner loop and a
    `vphaddd`-based horizontal reduce; the tail uses the standard
    sign-extension-mask abs trick.
  * `arm_gen_call` (ARM64 Linux): five new dispatch arms calling
    `_nova_arm_simd_*` helpers. NEON sequences (`ldr q0` /
    `add v0.4s` / `str q0` / `abs v0.4s`) emitted in
    `arm_emit_runtime`.
  * `warm_gen_call` (ARM64 Windows): same dispatch + helpers, COFF
    section flow.
- `Makefile`:
  * `.PHONY` line adds `bench-simd-sad`.
  * New `bench-simd-sad` target runs `tests/bench_simd.sh`.
- `tests/test_simd_intrinsics.nova` (new): 27 assertions covering
  add, sub, load/store, SAD (headline 9+10+...+16 == 100, zero
  case, mixed signs, 16-element multi-vector path, n=3 tail-only),
  and chained add+sub.
- `tests/bench_simd.sh` (new): generates `examples/bench_simd_sad.nova`
  on each run and times scalar-vs-SIMD SAD on 1024 i32 elements over
  200 trials. Reports averages + speedup ratio.
- `examples/bench_simd_sad.nova` (generated): SAD microbench source.
- `README.md`: new "SIMD i32x8 codegen intrinsics (R11D)" section
  with the lowering table.

### SIMD value model + smart-op classifier interaction

Each "SIMD value" is just a pointer to a 32-byte heap buffer (the
output of `alloc(32)`). This means:

- The smart-op classifier (R6A's `_nova_check_rdi` / `_nova_check_rsi`)
  correctly classifies SIMD buffers as pointers, since they live in
  the `[_heap_base, _heap_end)` range tracked by `_nova_alloc`. No
  new tag is needed.
- The intrinsics are **explicit**: NOVA's `+` / `*` on two SIMD
  pointers would dispatch to `_nova_add` / `_nova_mul` which treat
  pointers as strings or lists. Users invoke the SIMD builtins
  directly by name -- the classifier never reaches those operands.
- 32-byte alignment is not required: x86-64 uses `vmovdqu` (unaligned
  256-bit load/store) and ARM64 uses unaligned `ldr q0` / `str q0`.

### WASM out-of-scope rationale

WebAssembly has its own 128-bit `v128` SIMD intrinsics (`v128.load`,
`i32x4.add`, etc.) and would need a separate WASI / WAT lowering
path. R11D scope is x86-64 AVX2 + NEON; the WASM path falls through
to no body, matching the precedent set by `__intrinsic_dot_i32`
(also absent on the WASM target). Programs targeting `--target=wasm`
should keep using the existing scalar `int_add` / `int_mul` paths
until a follow-up adds the v128 lowering. The compile to WAT
succeeds; only `wat2wasm` validation would fail on a `call
$simd_*` reference, which doesn't impact non-SIMD WASM programs.

### Verification

- `make test-all` -- 155 passed / 0 failed / 6 skipped (was
  154 / 0 / 6; `test_simd_intrinsics` is the new pass).
- `make self-host` -- stage2.s bit-identical to stage3.s.
- `make bench-simd` -- existing `__intrinsic_dot_i32` AVX2 bench
  still reports ~96x speedup, unchanged.
- `make bench-simd-sad` -- new SAD-on-1024 bench reports scalar
  ~85 us avg, SIMD ~0.3 us avg, ~290x speedup vs the pure-NOVA
  scalar reference (which pays for byte-by-byte `load8` reassembly
  + `int_add`/`int_mul` per add).
- `make smoke-macos` -- macOS Mach-O cross-build clean (scalar
  fallback path).
- `make smoke-windows` -- Windows PE32+ cross-build clean (scalar
  fallback path).
- `make smoke-winarm64` -- winARM64 PE32+ cross-build clean
  (NEON helpers emitted; aarch64-windows-gnu assembles).
- ARM64 cross-build (`bin/nova ... --target=arm64`) assembles
  cleanly under `clang -target aarch64-linux-gnu -c`.
- `make smoke-wasm` -- WASM smoke build clean (SIMD builtins not
  referenced by the WASM hello/file-roundtrip examples).

### Measured SAD speedup (Linux x86-64 sandbox)

```
=== R11D SAD benchmark (scalar vs SIMD) ===
  elements: 1024
  scalar result: 43392
  SIMD   result: 43392
  MATCH: scalar and SIMD agree.
  scalar avg (ns): 85807
  SIMD   avg (ns): 293
  speedup: ~291.98x
```

The expected ~4-8x figure in the task description is for compiled-
scalar i32 baselines (which NOVA cannot currently emit -- every
load goes through `_nova_check_rdi` smart-op routing). Against
that hypothetical baseline, the AVX2 SAD inner loop processes 8
int32s per `vpabsd` instruction, so the ceiling is 8x; in practice
~4-7x on Haswell-era CPUs once the horizontal reduction and tail
are amortized. The 290x figure reflects the *current* NOVA scalar
overhead and gives CrossEngin's tick-rate planning a clean lower
bound.

## R11C — DAP data breakpoints (watchpoints)

`tools/nova-dap` now ships its 19th capability: data breakpoints (the
DAP wire name for "stop the program when this variable changes").
The implementation delegates to gdb hardware watchpoints via the
`-break-watch` MI command, with `-r` (read) / `-a` (access) flag
routing for the DAP `accessType` axis. R7D's per-thread plumbing,
R10E's frame-id mapping, and the existing stop-event pipeline all
carry over unchanged; the new code is a small, focused module.

Changes (`tools/nova-dap/nova_dap/watchpoints.py`, NEW):
- `encode_data_id(name, frame_id?, var_ref?)` and `decode_data_id(id)`:
  reversible base64url-encoded JSON envelope `{n, f?, v?}` so the
  client can round-trip a stable identifier between
  `dataBreakpointInfo` and `setDataBreakpoints` without server-side
  state.
- `access_type_flag(access_type)` and `build_watch_command(expr,
  access_type)`: DAP `"write"` / `"read"` / `"readWrite"` ->
  gdb `-break-watch` / `-break-watch -r` / `-break-watch -a`.
- `WatchpointManager` (thread-safe registry of gdb watchpoint ids
  <-> dataIds), `WatchpointRecord` dataclass.
- `parse_watchpoint_id(fields)`: pull gdb's watchpoint number out of
  `wpt={number=...}` / `hw-rwpt={...}` / `hw-awpt={...}` replies.
- `is_watchpoint_stop(reason)`: classify gdb stop reasons that map
  to DAP `"data breakpoint"` (`watchpoint-trigger`,
  `read-watchpoint-trigger`, `access-watchpoint-trigger`,
  `watchpoint-scope`).
- `extract_watch_values(fields)` and `describe_watch_change(...)`:
  build the human-readable `description` string for the DAP `stopped`
  event (`"Variable 'counter' changed (write): 5 -> 6"`).

Changes (`tools/nova-dap/nova_dap/server.py`):
- `Session` now owns a `WatchpointManager` (reset on every `launch`).
- New `handle_data_breakpoint_info(session, req)`: returns `{dataId,
  description, accessTypes: ["write", "readWrite"], canPersist: false}`.
  Best-effort describes the current value of the variable (e.g.
  `"counter = 0"`) by routing through `evaluate_via_bridge` when a
  bridge + frame id are available.
- New `handle_set_data_breakpoints(session, req)`: tears down prior
  watchpoints (per-id `-break-delete` so source breakpoints are
  preserved), then installs gdb watchpoints for each entry.
  Per-result `{verified, id?, message?}` mirrors the response shape
  of `setBreakpoints`. Routes to the right scope via
  `-thread-select` + `-stack-select-frame` when the dataId carries a
  frame id.
- `_handle_stopped` extracts the watchpoint number from `wpt={number}`
  (or `hw-rwpt` / `hw-awpt`) when `bkptno` is absent — gdb only
  populates `bkptno` for source-line breakpoints, not watchpoints —
  and looks up the registered name + access type to compose the
  description string.
- `_capabilities()` declares `supportsDataBreakpoints: true`.
- HANDLERS table grows by two (`dataBreakpointInfo` +
  `setDataBreakpoints`), totalling 20 DAP request handlers.

Tests (`tools/nova-dap/tests/test_data_breakpoints.py`, NEW):
- 131 assertions total (98 unit + 33 end-to-end).
- Unit phase (always runs): dataId round-trip (bare name, with
  frame, with varRef, garbage rejection, missing-name rejection,
  uniqueness), access-type mapping (write/read/rw/unknown/None),
  watch-command composition (all 4 flavours), watchpoint-id parsing
  (wpt / hw-rwpt / hw-awpt / missing), stop-reason classification,
  description builder, value extraction (write shape + read shape +
  missing), manager bookkeeping (register / lookup / clear_all /
  remove_by_gdb_id), and full handler tests against a `CaptureBridge`
  stub.
- E2E phase (skips if gdb / gcc missing): a C fixture with an
  `int counter` that increments three times — driven through
  `dataBreakpointInfo` + `setDataBreakpoints` + `configurationDone`
  on the wire. Asserts 3 distinct `stopped` events with `reason:
  "data breakpoint"`, each carrying a `hitBreakpointIds: [<id>]`
  field matching the watch id we registered and a `description`
  string mentioning "counter".
- NOVA integration: against `bin/hello_dwarf` — set a source-line
  breakpoint at the `sum` write, query `dataBreakpointInfo`, install
  a watchpoint, verify the wire shape end-to-end (no specific
  watchpoint-fire requirement since the program is straight-line).

Verification (this round):
- `python tools/nova-dap/tests/test_data_breakpoints.py` — OK, 131
  assertions; 3 watchpoint stops fired on the counter fixture.
- `python tools/nova-dap/tests/dap_smoke.py` — OK (pre-existing).
- `python tools/nova-dap/tests/dap_multi_thread.py` — OK
  (pre-existing).
- `python tools/nova-dap/tests/test_evaluate.py` — OK, 100
  assertions (pre-existing).
- `python tools/nova-dap/tests/test_conditional_breakpoint.py` —
  OK, 54 assertions (pre-existing).

Capability count: 18 -> 19 DAP capabilities (added data breakpoints);
HANDLERS table 18 -> 20 entries (`dataBreakpointInfo` +
`setDataBreakpoints` are two requests answering one capability).

## R10A: Cross-platform packaging (.deb + .pkg + .msi + Homebrew)

Extended R5's `install.sh` + Homebrew formula to native OS packaging.

- `packaging/debian/` — Debian package metadata (control, changelog,
  copyright, rules, install, postinst, source/format)
- `packaging/macos/` — macOS productbuild distribution.xml +
  welcome/license/conclusion resources + postinstall script
- `packaging/windows/nova.wxs` — WiX 3.x source for the .msi
  installer (ProgramFiles64 layout, PATH env entry, Start menu
  shortcut, MajorUpgrade)
- `packaging/windows/build-msi.bat` — Windows-native cmd.exe builder
- `packaging/man/nova.1` — troff(1) manual page (shipped by .deb + .pkg)
- `packaging/homebrew/` — bump-formula.sh + README documenting the
  tap-update flow
- `scripts/build-deb.sh` — dpkg-deb based builder (sandbox-runnable)
- `scripts/build-pkg.sh` — pkgbuild + productbuild builder (macOS-only;
  emits recipe file on Linux)
- `scripts/build-msi.sh` — WiX 3/4 builder (recipe-fallback on Linux)
- `scripts/sign.sh` — unified signing wrapper for gpg / productsign /
  signtool
- `tools/Formula/nova.rb` — refreshed Homebrew formula with proper
  macOS .o linking, on_intel/on_arm scaffolding, doc + man install,
  full smoke test
- `.github/workflows/release.yml` — extended to a 3-job pipeline:
  build (cross from Ubuntu), package (native runners ubuntu/macos/
  windows), release (publishes everything)
- `Makefile` — new `install`, `package-deb`, `package-pkg`,
  `package-msi`, `package-all` targets (DESTDIR + PREFIX honoured)
- `INSTALL.md` — extended to 7 install paths with per-format layout
  tables

Verification:
- `dpkg-deb --build` produces a valid 137 KB nova_0.1.0_amd64.deb
  (`dpkg-deb --info` clean, `dpkg-deb --contents` clean)
- `bash -n` clean on all four scripts
- `xmllint --noout` clean on nova.wxs
- `ruby -c` clean on tools/Formula/nova.rb
- `yamllint` clean on release.yml
- `actionlint v1.7.7` clean on release.yml
- `make install DESTDIR=...` works end-to-end

Sandbox limits:
- `pkgbuild`/`productbuild` only on macOS — Linux falls through to
  writing `dist/nova-0.1.0.pkg.txt` recipe file
- WiX `candle`/`light` only on Windows / wine — Linux falls through to
  writing `dist/nova-0.1.0.msi.txt` recipe file
- `brew audit` skipped (brew not installed); formula is `ruby -c` clean

## Completed

### N12–N29 Modules (18 modules)
All fully implemented with:
- Implementation files under `src/cognitive/`, `src/runtime/`, `src/tooling/`
- Unit tests in `tests/` (all passing: 154/160, 0 failures, 6 skipped)
- Example programs in `examples/` (18 new `*_demo.nova` files)
- Documentation in `docs/STDLIB.md`
- Self-hosting verified (`stage2.s == stage3.s`)

### Phase 1: Tensor Performance
- SSE2 vectorized `simd_dot_f64` (4-element unrolled mulpd/addpd)
- SSE2 vectorized `simd_scale_f64` and `simd_sum_f64`
- Tiled matmul for matrices >= 64 columns (32x32 tile blocking)
- OpenBLAS FFI wrapper (`src/runtime/blas.nova`)
- Size-based dispatch in `tensor_matmul` (simple → tiled → BLAS)
- Tests: `test_tensor_perf` passes

### Phase 2: Cognitive LLM Pipeline
- `src/agent/cognitive_llm.nova` — LLM generation + reasoning + confidence
- Integrates with reasoning engine, episodic memory, emotion analysis
- Tests: `test_cognitive_llm` passes

### Phase 3: Competitive Embeddings
- `src/runtime/embedding.nova` — unified multi-backend embedding interface
- TF-IDF + character n-grams + BM25 scoring
- Neural embedding via Python bridge (optional)
- Cognitive embedding with emotional/episodic/inferential dimensions
- Tests: `test_embedding` passes

### Phase 4: Import Scaling
- **4a**: O(1) function name lookup via hash table (`cg_fns_ht`)
  - Hash table with 8 buckets, `len(name) % 8` hash function
  - `is_known_function` uses hash table instead of O(n) scan
  - Note: `is_global` stays linear scan due to destructuring edge case
- **4b**: O(1) dependency and package lookups
  - `_dep_get` uses key-value hash table (`_dep_ht`)
  - `is_std_package` uses membership hash table (`_std_pkg_ht`)
- **4c**: Module provenance tracking
  - `cg_fn_modules` hash table maps function names to source files
  - `fn_module(name)` returns the source file that defined a function
  - Tracked for all function declarations, extern functions, methods, lambdas

## Module Summary

| # | Module | Location | Tag |
|---|--------|----------|-----|
| N12 | Associative Memory | src/cognitive/associative.nova | 400 |
| N13 | HDC | src/cognitive/hdc.nova | 410/411 |
| N14 | SDR | src/cognitive/sdr.nova | 420 |
| N15 | Active Inference | src/cognitive/active_inference.nova | 430 |
| N16 | Time Series | src/runtime/timeseries.nova | 440 |
| N17 | Audio Processing | src/runtime/audio.nova | 450 |
| N18 | Node Pool | src/runtime/node_pool.nova | 460 |
| N19 | Resonance Kernel | src/cognitive/resonance.nova | 470 |
| N20 | Atom Lifecycle | src/cognitive/atom_lifecycle.nova | 480/485 |
| N21 | Visualizer | src/tooling/visualizer.nova | 500 |
| N22 | Time Machine | src/tooling/time_machine.nova | 510 |
| N23 | KG Visualizer | src/tooling/kg_visualizer.nova | 520 |
| N24 | Profiler | src/tooling/profiler.nova | 530 |
| N25 | Causal Library | src/cognitive/causal_library.nova | 540/545 |
| N26 | Predictive Coding | src/cognitive/predictive_coding.nova | 550/555 |
| N27 | Skill System | src/cognitive/skill.nova | 560 |
| N28 | Self-Model | src/cognitive/self_model.nova | 570 |
| N29 | Federation | src/runtime/federation.nova | 580/585 |

## Known Issues and Workarounds

### Compiler 7th-parameter bug — FIXED
Previously, functions with 7+ parameters produced incorrect values for the
7th+ arguments (stack-passed args were never copied into local slots, and
the caller didn't clean up extra stack args after the call). Fixed in
codegen.nova: function prologues now copy `[rbp + 16 + n*8]` into local
slots for parameters 7+, callers emit `add rsp` to clean up, and alignment
padding ensures 16-byte stack alignment before `call`. Test:
`tests/test_7th_param.nova` verifies 7, 8, and 9 parameter functions.
Note: `causal_library.nova` still uses the old 6-param workaround but new
code can freely use 7+ parameters.

### is_global hash table incompatibility — ROOT CAUSE FOUND (R9D)
Earlier rounds reported that `is_global` could not be migrated to the hash
table because destructuring patterns (`let [a, b] = ...`) made it
segfault. The actual root cause was different: `parse_stmt` appends a
source-line number as the last element of every statement, and the
`AST_DESTRUCTURE` handlers in `collect_locals` / `gen_stmt` used
`len(nd) > 3` to detect the optional rest-pattern name (`let [a, ...rest]
= xs` stores the rest name at `nd[3]`). After line numbering, a non-rest
node ALSO has `len(nd) == 4`, so the line int was being treated as a
variable name — pushed into `cg_locals`, eventually fed to
`cg_dwarf_sanitize` which calls `len()` on it and crashes.

Fixed in `src/compiler/codegen.nova` by changing both checks to
`len(nd) > 4` (rest node is length 5 after line numbering, non-rest is
length 4). `is_global` could now safely move to the hash table — left
linear for this round so the diff stays focused.

Tests `test_destructure.nova`, `test_rest_pattern.nova`, and
`test_ptr_threshold_fix.nova` (which also hit a separate bootstrap-
runtime issue described below) now pass: 154p / 0f / 6s.

### Forbidden patterns
- `char_at(s, i)` — broken, use `substr(s, i, 1)`
- `map_new()` — 16-slot limit causes infinite loops; use parallel lists
- `soul` — reserved keyword (TOK_SOUL=113), use `soul_ref` as variable name
- `list_insert`/`list_remove` — conflict with compiler builtins when concatenated
- `none` keyword — use `0` instead in runtime modules

### PTR_THRESHOLD integer misclassification — FIXED (R6A)
Previously, the smart-op runtime helpers (`_nova_add`, `_nova_mul`, `_nova_eq`,
`_nova_neq`, `_nova_lt`, `_nova_gt`, `_nova_le`, `_nova_ge`, and several
classifier helpers `_nova_type_of`, `_nova_type_name`, `_nova_debug_print`,
`_nova_hash_key`, `_nova_to_str`, `_nova_flatten`) used a magnitude heuristic
to distinguish pointers from integers: any value `>= 0x100000` (1 MiB) was
treated as a pointer. This silently corrupted any program with integers
above 1 MiB — pacer nanotimes, kg_sync sequence numbers, bignum limbs,
JPEG DCT coefficients, 31-bit LCG masks, etc. — and forced six different
agent sessions to work around it via the `int_*` builtins.

The root cause is now fixed in `src/compiler/codegen.nova`:

1. A new pair of helper subroutines `_nova_check_rdi` / `_nova_check_rsi`
   replace the inline `cmp rdi, 0x100000; jl ...` pattern with a real
   range check. A value is a pointer iff it lies in:
   - the link-time-fixed string literal range
     `[_strlit_start, _strlit_end)` (new labels bracketing all .rodata
     literals), OR
   - the runtime heap range `[_heap_base, _heap_end)`, OR
   - the kernel-area high range `>= 0x400000000` (16 GiB), which covers
     argv/env/stack pointers and mmap'd heaps on macOS/Windows.
   Negative integers (high bit set) short-circuit to integer.

2. Every call site that previously used the magnitude check (22 sites:
   `_nova_add`, `_nova_mul`, the six comparison helpers, `_nova_type_of`,
   `_nova_type_name`, `_nova_debug_print`, `_nova_hash_key`,
   `_nova_to_str`, `_nova_flatten`) now calls one of the new helpers
   and branches on ZF.

The `int_*` builtins remain available as no-op scalar wrappers for
back-compat. They are no longer required for integers up to 16 GiB.

Regression test: `tests/test_ptr_threshold_fix.nova` (23 checks covering
`+`, `*`, `&`, `<<`, `>>`, comparisons, equality, negative integers,
plus string/list smart-op back-compat).

Verification:
- `make self-host` — stage2.s == stage3.s, bit-identical.
- `make test` — all runtime tests pass.
- `make test-all` — 154/160 pass (after R9D fixed the destructure /
  rest-pattern bug; the 6 skipped tests need special setup —
  `test_import*`, `test_fileio`, `test_io_random`, `test_ffi*`).
- `make bench-int-safe` — large-int correctness PASS; smart-op now
  ~2.3-2.8x slower than `int_*` due to the call-vs-inline cost, which
  is still acceptable and the int_* speedup is itself documented.
- CrossEngin `make test` — 150/150 pass; `make integration` — pass.

Proof-of-fix: `crossengin-demo/src/safety/bignum_2048.nova` `bn2048_add`
and `bn2048_sub` were converted from `int_add` / `int_sub` calls to plain
`+` / `-` operators; the bignum test suite continues to pass bit-
identical results (modpow round-trips, Montgomery vs legacy parity).

### bin/nova two-stage build — added (R9D)
`test_ptr_threshold_fix.nova` lexes a 0x500000 literal. The lexer does
`val * 16` (via `_nova_mul`), so the COMPILER's internal smart-op
runtime is exercised. The `boot/nova_boot.s` bootstrap is older than
R6A's PTR_THRESHOLD fix and appends its own pre-fix runtime (`cmp rdi,
0x100000; jge .mul_ptr`) at the bottom of every binary it produces.
Stage-1 (output of boot) thus has the OLD runtime baked in even though
the SOURCE has the new helpers — and trying to lex a 5 MiB hex literal
in stage-1 crashes inside `mul_ptr`.

Fixed by extending the `bin/nova` Makefile rule to do a two-stage build
(boot → stage1 → stage2, then `ld -o bin/nova /tmp/nova_stage2.o`).
Stage-2 is compiled BY stage-1 from the same source, so its runtime is
the new range-check version. Self-host (`make self-host`) still produces
stage2.s == stage3.s bit-identical.

## Files Modified in Compiler (Phase 4)

- `src/compiler/codegen.nova` — added hash table functions (`_cg_ht_new`,
  `_cg_ht_hash`, `_cg_ht_add`, `_cg_ht_has`, `_cg_ht_set`, `_cg_ht_get`),
  function name hash table (`cg_fns_ht`), module tracking (`cg_fn_modules`),
  `fn_module()` query function
- `src/compiler/compiler.nova` — O(1) dependency lookup via `_dep_ht` hash
  table, updated `_dep_add` and `_dep_get`
- `src/pkg/pkg.nova` — O(1) package lookup via `_std_pkg_ht` hash table,
  `_pkg_reg()` helper, updated `is_std_package`

## What's Left (for future sessions)

1. Fix the 7th-parameter compiler bug in codegen.nova
2. Investigate `is_global` hash table incompatibility (may be fixable by
   adding type guards for string pointers in `_cg_ht_hash`)
3. Example programs are standalone demos — they don't compile/run without
   library source concatenation (by design, matching existing examples)
4. Surface NOVA coroutines as DAP threads. Today coroutines share a single
   OS thread and are not visible to gdb as separate LWPs, so the DAP server
   reports them as one thread. A coroutine-aware mapping would need either
   (a) compiler-emitted DWARF that describes coroutine frames as separate
   thread ids, or (b) the DAP server reading NOVA's coroutine table out of
   the inferior's heap via the gdb python API and synthesizing virtual
   threads for it.

## R10E — DAP expression evaluation + conditional breakpoints

`tools/nova-dap` now exposes 18 DAP requests with the addition of the
`evaluate` handler (watch panel / REPL / hover tooltips), and the
`setBreakpoints` handler honours per-breakpoint `condition` strings
end-to-end. Both ride on top of R7D's per-thread frame plumbing and
R4A's `.debug_info` work.

Changes (`tools/nova-dap/nova_dap/evaluator.py`, NEW):
- `decode_value(raw)` classifies a gdb-MI ``value="..."`` string into
  one of `int` / `str` / `char` / `bool` / `ptr` / `raw`. Handles
  decimal / hex / octal int literals, `0x... "text"` pointer-prefixed
  C-strings (with `\\n`, `\\t`, `\\\\`, `\\"` escape decoding), the
  `<int> 'c'` dual-form for chars, and bare-pointer pass-through.
- `build_evaluate_command(expr, thread_id, frame_level)` composes the
  MI command with optional `--thread <id> --frame <level>` routing.
- `evaluate_via_bridge(bridge, expr, thread_id, frame_level)` drives
  the bridge and returns an `EvaluationResult` carrying either a
  `DecodedValue` or an error message.

Changes (`tools/nova-dap/nova_dap/server.py`):
- New `handle_evaluate(session, req)`: resolves the DAP `frameId` back
  to `(threadId, frame_level)` via the existing frame table, calls
  `evaluate_via_bridge` with the right routing, and returns
  `{result, type, variablesReference: 0}`.
- `handle_set_breakpoints` now reads the `condition` field per
  breakpoint and forwards it via `-break-insert -c "<expr>"`. Empty /
  blank conditions are treated as unconditional.
- `_capabilities()` flips two flags from `False` to `True`:
  `supportsConditionalBreakpoints` and `supportsEvaluateForHovers`.
- HANDLERS table grows by one entry (`"evaluate"`), totalling 18 DAP
  requests.

Tests:
- `tools/nova-dap/tests/test_evaluate.py` (NEW) — 100 assertions
  (57 decoder + 43 end-to-end). Covers decoder tier classifications,
  command-builder routing, fake-bridge integration, end-to-end DAP
  wire test against a C fixture, frame-routing verification (frame 0
  vs frame 1 vs unknown frame fallback), `context=hover`/`repl`
  parity with `watch`, and a graceful undefined-variable error path.
- `tools/nova-dap/tests/test_conditional_breakpoint.py` (NEW) — 54
  assertions (15 unit + 39 end-to-end). Covers `-break-insert -c`
  composition, empty-condition fallback, capability registration,
  end-to-end loop where `condition: "x > 5"` correctly skips x=1..5
  and fires at x=6, re-send of an unconditional bp clearing the
  prior condition, and integration against the NOVA `hello_dwarf`
  binary (`condition: "sum == 3"`).
- `dap_smoke.py` + `dap_multi_thread.py` (pre-existing) still pass.

Capability count: 17 → 18 DAP requests (added `evaluate`); 2 new
boolean caps (`supportsConditionalBreakpoints`,
`supportsEvaluateForHovers`).

Verification:
- `python tools/nova-dap/tests/test_evaluate.py` — OK, 100 assertions.
- `python tools/nova-dap/tests/test_conditional_breakpoint.py` — OK,
  54 assertions.
- `python tools/nova-dap/tests/dap_smoke.py` — OK (pre-existing).
- `python tools/nova-dap/tests/dap_multi_thread.py` — OK (pre-existing).
- Integration: `evaluate "1+2"` against the NOVA `hello_dwarf` binary
  returns `{result: "3", type: "int"}`; `evaluate "sum"` returns
  `{result: "3", type: "int"}`; `evaluate "scaled"` returns
  `{result: "30", type: "int"}`. A conditional breakpoint with
  `condition: "sum == 999"` (never true) lets the program run to
  termination with no stop event.

## R7D — Multi-thread DAP coordination

`tools/nova-dap` now exposes 17 DAP capabilities; the multi-thread
coordination layer was added on top of R4A's `.debug_info` work and the
existing single-thread step/breakpoint/stack/variable plumbing.

Changes (`tools/nova-dap/nova_dap/server.py`):
- Enable gdb `mi-async on` + `non-stop on` during launch so each thread
  can be paused / continued independently. Fall back to all-stop mode
  if gdb refuses (e.g. unsupported target).
- Session tracks a `known_threads` table mirroring gdb's thread set,
  updated from `=thread-created` / `=thread-exited` notifications and
  reconciled via `-thread-info` on every DAP `threads` request.
- Stable per-(threadId, level) DAP frame ids; `scopes` and `variables`
  route via `-thread-select` + `-stack-select-frame` so thread A's
  locals never leak into thread B's variables panel.
- `continue` / `next` / `stepIn` / `stepOut` / `pause` accept DAP
  `threadId` + `singleThread`. `singleThread:true` issues
  `-exec-{continue,next,step,finish,interrupt} --thread <id>`;
  otherwise `--all` (or no flag in all-stop mode).
- `*stopped` records produce DAP `stopped` events with `threadId` from
  the MI record and `allThreadsStopped=true` only when MI
  `stopped-threads="all"` (so in non-stop mode a breakpoint hit on
  thread A doesn't claim thread B is stopped).
- New events: `continued` (per-thread resume) and `thread` (lifecycle).
- New capability: `supportsSingleThreadExecutionRequests: true`.

Thread model: real OS threads, surfaced from gdb-MI in non-stop mode.
For a single-threaded NOVA program (no FFI, no pthreads) this collapses
to one thread (`id=1`, name=`main`) — the wire protocol still works
end-to-end, there's just only one thread to address.

Tests:
- `tools/nova-dap/tests/dap_smoke.py` (pre-existing single-thread
  smoke) — still passes.
- `tools/nova-dap/tests/dap_multi_thread.py` (new) — builds a small
  pthread C fixture (`tests/fixtures/multi_thread.c`) on demand and
  exercises `threads`, per-thread `stackTrace` + `scopes` + `variables`
  isolation, per-thread `next`/`stepIn`/`stepOut`, `pause`, and
  `continue` (with and without `singleThread`). SKIPs cleanly if
  `gcc` or `gdb` is unavailable.

## R7A — Windows ARM64 (PE32+ AArch64) backend

NOVA's sixth codegen target. Closes the last gap in the cross-platform
matrix (Linux x86-64 + macOS x86-64 + WASM + Windows x86-64 + ARM64-
Linux/Android + Windows ARM64).

Changes (`src/compiler/codegen.nova`):
- New `winarm64_gen_program` standalone AST-walking backend modeled on
  the Linux ARM64 path (`arm64_gen_program`). Selected via target id
  `5`, exposed as `--target=windows-arm64`.
- Emits GAS-syntax ARM64 instructions with PE section directives
  (`.section .text,"xr"`, `.section .rdata,"dr"`) and IAT-style import
  declarations (`.extern __imp_<API>` for ExitProcess, GetStdHandle,
  WriteFile, BCryptGenRandom).
- Imported APIs are called via the standard PE pattern:
  `adrp x16, __imp_<name>; ldr x16, [x16, :lo12:__imp_<name>]; blr x16`.
  x16 is the AArch64 caller-saved scratch (IP0 in the AAPCS64 spec).
- Win32 entry symbol `mainCRTStartup` (lld-link's default console
  entry when no CRT is present). The entry sets up an ARM64 frame,
  runs top-level statements, then calls `ExitProcess(0)`.
- 16-byte SP alignment maintained at every call boundary; standard
  `stp x29, x30, [sp, #-16]!` prologue / `ldp x29, x30, [sp], #16`
  epilogue; locals laid out below x29 with 8-byte slots.
- Tiny runtime: `_nova_warm_strlen`, `_nova_warm_write_stdout`
  (GetStdHandle(-11) + WriteFile via IAT), `_nova_warm_print`,
  `_nova_warm_println` (CRLF append for Windows console compat),
  printable-error stubs for unsupported builtins (list_new/push/len/
  read_file/write_file).
- `secure_random(buf, n)` lowers to `BCryptGenRandom(NULL, buf, n,
  BCRYPT_USE_SYSTEM_PREFERRED_RNG=2)` via the IAT. NTSTATUS == 0 ->
  returns n_bytes; else -1.

Changes (`src/compiler/compiler.nova`):
- `--target=windows-arm64` is parsed, `cg_target = 5`, banner reads
  "Target: Windows ARM64 (PE32+ AArch64)".

Changes (`Makefile`):
- New `smoke-winarm64` target (alias `cross-winarm64`): generates
  bin/hello_winarm64.exe and bin/secure_random_winarm64.exe. Pipeline:
    1. NOVA --target=windows-arm64 -> ARM64 GAS .s
    2. clang -target aarch64-windows-gnu -c -> Aarch64 COFF .o
    3. llvm-dlltool -m arm64 fabricates ARM64 import libs from .def
       files (KERNEL32.DLL: ExitProcess, GetStdHandle, WriteFile;
       BCRYPT.DLL: BCryptGenRandom). No upstream mingw-w64 aarch64
       import lib package exists on Debian/Ubuntu, so we synthesize
       them at build time.
    4. lld-link /machine:arm64 /subsystem:console
       /entry:mainCRTStartup -> PE32+ executable
  Skips cleanly if clang / lld-link / llvm-dlltool are missing.

Tests (`tests/test_winarm64_emitter.sh`):
- Runs `make smoke-winarm64` then asserts:
  * `file` reports PE32+ executable Aarch64 for MS Windows
  * `llvm-readobj` reports IMAGE_FILE_MACHINE_ARM64 (0xAA64)
  * Raw byte check at PE signature+4 == `64 AA` (little-endian
    0xAA64) on disk
  * KERNEL32.DLL import present in both binaries
  * BCRYPT.DLL import present in secure_random binary
  * IAT call sequence (adrp x16 / ldr x16 / blr x16) appears in
    .text disassembly
  * Standard ARM64 prologue is present
  * Binary sizes within sanity bounds (1024..20480 bytes)

Verification artifacts:
- bin/hello_winarm64.exe — 2048 bytes, KERNEL32.DLL import (loops +
  string concat omitted; standalone path doesn't yet lower them).
- bin/secure_random_winarm64.exe — 3072 bytes, KERNEL32.DLL +
  BCRYPT.DLL imports.
- Both binaries: COFF machine 0xAA64, entry `mainCRTStartup`,
  ImageBase 0x140000000, 2 sections (.text + .rdata), proper IAT.
- Cannot execute on Linux x86-64 host — runtime confirmation
  requires an ARM-Windows tester. Format is verified end-to-end
  on the Linux host via llvm-readobj / llvm-objdump.

Gap status (deferred for future R-rounds):
- The standalone winarm64 path covers the same surface as the
  Linux ARM64 standalone path: integer arithmetic, control flow,
  print/println, exit, secure_random. It does NOT yet wire
  list/map/string-concat/file-IO — those require the full IR-based
  Windows backend (cg_target == 3) which still targets x86-64.
  Lifting the IR path to ARM64 (via lower_arm64.nova's IR-walking
  emitter) is the next milestone.
- DWARF/CodeView debugging info on the winarm64 target is not
  emitted (matches the Linux ARM64 standalone path).

## R9C — LSP workspace rename (`textDocument/rename` across imports)

`tools/nova-lsp` rounds out the editor refactoring story. The previous
single-buffer rename (R5/R5F) handled the open document plus its
on-disk import closure; R9C makes it a true workspace operation: F2 on
a top-level `fn` / `let` / `const` / `type` now rewrites every file in
the workspace that imports the definition site, while keeping
unrelated same-named symbols alone.

Changes (`tools/nova-lsp/nova_lsp/rename_workspace.py`, new module):

- `classify_symbol(name, def_path, def_line, file_cache)` — decides
  whether a declaration is `"toplevel"` (column-0 `fn`/`let`/`const`/
  `type`) or `"local"` (indented binding, fn parameter, anonymous
  helper). Only top-level declarations are eligible for workspace-wide
  rename; locals fall back to the legacy single-buffer path.
- `file_imports_target(candidate, target, file_cache)` — walks R5F's
  transitive import graph rooted at `candidate` and returns true when
  `target` is reachable. Used to filter the candidate set so only
  files that genuinely depend on the definition site are touched.
- `find_references_in_workspace(name, def_path, file_cache, index)` —
  enumerates every file in the workspace symbol index (plus an
  `extra_paths` list of open-doc closures), keeps the ones that
  import the def, and returns `{abs_path: [Range, ...]}` for each
  `\b<name>\b` occurrence. String literals and `//` / `#` comments
  are masked out so docs that mention the name aren't rewritten.
- `detect_name_conflict(new_name, affected_files, file_cache)` —
  if `new_name` is already declared at top level in any file the
  rename would touch, returns a human-readable conflict message;
  the orchestrator then surfaces it as a JSON-RPC `ResponseError`
  (code `-32803` "Request failed") so VS Code renders a popup
  without applying any edits.
- `plan_workspace_rename(...)` — top-level orchestrator returning
  `WorkspaceRenameRequest(references, conflict_message)`.
- `build_workspace_edit(refs, new_name)` — converts the path-keyed
  reference map into the LSP `{"changes": {uri: [TextEdit, ...]}}`
  payload.

Changes (`tools/nova-lsp/nova_lsp/server.py`):

- `handle_rename` now dispatches: top-level fn/let/const/type goes
  through `handle_rename_workspace`, locals fall back to the
  legacy `_handle_rename_legacy` path.
- `handle_rename_workspace` resolves the symbol via R5F's
  `find_definition`, classifies its kind, ensures the workspace index
  has seen the project root (auto-crawling parent dirs of open docs
  when no rootPath was supplied at `initialize` time), seeds
  `extra_paths` from open-buffer import closures, and runs
  `plan_workspace_rename`. The conflict path is encoded as a
  sentinel dict the dispatcher converts to a `ResponseError`.

Tests (`tools/nova-lsp/tests/test_rename_workspace.py`, new):

- 23 test functions / 101 assertions covering:
  * Symbol classification: top-level fn/let are "toplevel", indented
    `let` and fn parameters are "local".
  * Import-graph reachability: direct + transitive imports detected,
    non-importers excluded.
  * Name-conflict detection: clean rename returns None, collision
    surfaces a message mentioning the new name.
  * Three-file cross-file fixture (`A` defines `foo`, `B` imports A
    and uses `foo`, `C` has its own unrelated `foo`): rename touches
    A + B but never C.
  * Word boundaries: `foo` rename does NOT touch `foobar`, `myfoo`,
    `foo_bar`.
  * String + comment masking: mentions of the symbol name inside
    `"..."` strings or `// ...` / `# ...` comments are not edited.
  * End-to-end LSP dispatch for: three-file workspace, word
    boundaries, conflict path returning JSON-RPC error code -32803,
    local `let` rename staying single-file, fn parameter rename
    staying single-file, regression test for the original
    `rename_smoke.py` scenario.
  * Integration: rename `moment_new` in `src/core/moment.nova`
    against the live NOVA codebase — finds 21 occurrences across
    6 files (the def + 5 importers in `examples/`).

Verification:
- `python tests/test_rename_workspace.py` — OK, 101 assertions.
- All 6 prior LSP tests still pass (completion, rename, references,
  code_action, definition_cross_file, workspace_symbols).
- Real-codebase rename of `moment_new` produces a coherent
  `WorkspaceEdit` covering 6 files / 21 ranges, with the
  unrelated `unrelated.nova` file in test fixtures left alone.

## R8C — LSP workspace symbol search (`workspace/symbol`)

`tools/nova-lsp` now exposes the last LSP capability gap. Editors can
hit Cmd+T / Ctrl+T and fuzzy-search every top-level NOVA symbol across
the workspace.

Changes (`tools/nova-lsp/nova_lsp/workspace_symbols.py`, new module):

- `WorkspaceSymbolIndex` — inverted index `name -> [SymbolEntry, ...]`
  with a reverse map `path -> set[name]` for O(symbols_per_file)
  invalidation.
- `index_text(path, text)` — re-scans a file from in-memory text, used
  on `didOpen`/`didChange` so unsaved edits are searchable instantly.
- `index_file(path)` — re-scans from disk, used on `didClose` of a
  still-existing file and during the lazy root crawl.
- `index_workspace_root(root)` — one-time recursive `*.nova` crawl,
  pruning `.git`, `node_modules`, `__pycache__`, `bin`, `build`.
- `fuzzy_match(query, limit=100)` — tiered scoring: exact (0) >
  case-insensitive (1) > prefix (2) > substring (3) > camelCase letter
  match (4) > sequential character match (5). Empty query returns the
  first N symbols in alphabetical order.
- Recognised declarations: `fn name(...)` → `SymbolKind.Function (12)`;
  ALL_CAPS `let NAME = ...` → `SymbolKind.Constant (14)`; other `let`
  → `SymbolKind.Variable (13)`; `out_label("_nova_*")` inside
  `codegen.nova` / `compiler.nova` → `SymbolKind.Function` with
  `containerName="<runtime>"` so the R6A helpers like
  `_nova_check_rdi` / `_nova_check_rsi` are discoverable.

Changes (`tools/nova-lsp/nova_lsp/server.py`):

- `workspaceSymbolProvider: {resolveProvider: false}` registered in
  the `initialize` response.
- `handle_workspace_symbol` lazily crawls `state.root_path` on the
  first query, double-taps the live-buffer refresh for every open doc,
  and returns the top 100 `SymbolInformation` records.
- `didOpen` / `didChange` / `didSave` call
  `_refresh_workspace_symbols_for_doc` so live edits are reflected
  immediately; `didClose` re-reads the file from disk (or invalidates
  if the file is gone). The R5F `FileCache` lifecycle is untouched.

Tests (`tools/nova-lsp/tests/test_workspace_symbols.py`, new):

- 11 test functions / 52 assertions covering: empty workspace, single
  file with 5 fns, three-file cross-file workspace, fuzzy ranking
  (`foB` → `fooBar` before `foo_bar`), tier breakdown for the score,
  empty-query alphabetical first-N, SymbolKind classification (fn
  vs ALL_CAPS let vs lower-case let), file invalidation, didChange
  reindexing, end-to-end LSP wire test through `dispatch`, and an
  integration test that indexes `/home/user/NOVA/src/` (~3665
  symbols) and locates `_nova_check_rdi` at codegen.nova:8569
  alongside its sibling `_nova_check_rsi`.

Verification:
- `python tests/test_workspace_symbols.py` — OK, 52 assertions.
- All 5 prior LSP tests still pass (completion, rename, references,
  code_action, definition_cross_file).
- Indexed 3665 symbols across NOVA's `src/` tree (2097 top-level
  `fn` + 1182 top-level `let` + 386 `out_label("_nova_*")` runtime
  labels).

## R8A — WASI preopens / filesystem (serverless deployment surface)

Closes the WASM serverless deployment gap. Pre-R8A the WASM target
shipped `fd_write`/`fd_read`/`fd_close`/`fd_seek`/`path_open`/
`random_get`/`proc_exit` imports and `read_file`/`write_file`
convenience builtins. R8A extends the surface so a NOVA program can
drive WASI primitives directly -- the building blocks for streaming
I/O on Cloudflare Workers, wasmtime serve, Fastly Compute@Edge, etc.

Changes (`src/compiler/codegen.nova`):

- WASM module header now emits 12 `wasi_snapshot_preview1` imports:
  `fd_write`, `fd_read`, `fd_close`, `fd_seek`, `path_open`,
  `path_filestat_get` (new), `args_sizes_get` (new), `args_get`
  (new), `environ_sizes_get` (new), `environ_get` (new), `random_get`,
  `proc_exit`. All imports are always emitted (vs. on-demand) so the
  module header stays bit-stable for self-host parity.
- Eight new NOVA-callable builtins registered in `is_builtin_fn`:
  `wasi_open(path, flags) -> fd | -1`
  `wasi_read(fd, buf, len) -> bytes_read | -1`
  `wasi_write(fd, buf, len) -> bytes_written | -1`
  `wasi_close(fd) -> 0 | -1`
  `wasi_seek(fd, offset, whence) -> new_offset | -1`
  `wasi_filestat(path) -> [size, mtime_ns, kind] | 0`
  `wasi_args_get() -> list of argv strings`
  `wasi_environ_get() -> list of "KEY=VALUE" strings`
- WASM runtime (`wasm_gen_rt_io`) adds bodies for the eight builtins.
  They wrap the new imports against the existing scratch layout
  (offsets 16..63 for path_open/fd_read/fd_write/fd_seek; offsets
  64..127 for the 64-byte path_filestat_get result; offsets 128..135
  for args/environ size out-pointers). dirfd=3 routes through the
  first preopen — `wasmtime --dir=/tmp` or `wasmer run --mapdir`.
- Linux x86-64 native runtime adds POSIX-equivalent bodies for the
  same builtins so the WASI surface compiles and runs natively too:
  `_nova_wasi_open` -> open(2) with WASI->POSIX flag translation,
  `_nova_wasi_read` -> read(2), `_nova_wasi_write` -> write(2),
  `_nova_wasi_close` -> close(2), `_nova_wasi_seek` -> lseek(2),
  `_nova_wasi_filestat` -> stat(2) with WASI filetype mapping,
  `_nova_wasi_args_get` walks `_nova___arg`, `_nova_wasi_environ_get`
  returns empty list (the WASM path serves the real envp).
- Non-Linux native targets (macOS, Windows, ARM64, winARM64) emit
  stubs that return -1 / empty list. The primary contract is the
  WASM path; native paths exist so the same source compiles
  everywhere.

Changes (`Makefile`):

- New `smoke-wasi-preopens` target. Compiles
  `examples/wasi_file_roundtrip.nova` to WASM, runs under wasmtime
  with `--dir=/tmp`, verifies the 12 wasi_snapshot_preview1 imports
  are present via `wasm-objdump`, and checks the round-tripped file
  contents. Skips cleanly if wat2wasm or wasmtime is missing.

New files:

- `examples/wasi_file_roundtrip.nova` — eight-step program exercising
  every R8A builtin (open(CREAT|TRUNC) -> write -> close -> filestat
  size+kind -> reopen -> read -> close -> bracketed byte verify).
- `tests/test_wasi_preopens.sh` — driver script for the smoke target.

Verification:
- `make smoke-wasi-preopens` -> PASS under wasmtime 45.0.0.
- `wasm-objdump -x bin/wasi_file_roundtrip.wasm | grep wasi_` lists
  all 12 wasi_snapshot_preview1 imports (sig 0..6).
- `make hello-wasm`, `make smoke-wasm`, `make smoke-wasm-file` —
  still PASS; existing behaviour preserved.
- `make test` — runtime tests PASS unchanged.
- `make self-host` — stage2.s == stage3.s bit-identical.
- `make cross-windows`, `make cross-macos`, `make cross-winarm64` —
  all PASS (non-Linux wasi_* stubs are size-stable -1 / empty list).
- Native Linux x86-64 also runs `examples/wasi_file_roundtrip.nova`
  via the syscall-backed `_nova_wasi_*` runtime; the same source
  cross-compiles + executes correctly on both targets.

## Tree-sitter grammar bundle (R9E)

Round-9 extends `tools/tree-sitter-nova/` to a complete editor-ready
bundle for non-VS-Code hosts (Neovim, Helix, Emacs, Zed, ...).

### Changes

- `grammar.js` — added `-> Type` return-type annotation on `fn_decl`,
  `extern_fn_decl`, and `lambda_expression`, plus a precedence fix
  on the string-interpolation lexer rule so `${expr}` inside
  `"..."` is now captured as an `(interpolation ...)` node instead
  of being swallowed by `_string_content`.
- `queries/folds.scm` — NEW. Fold-region patterns for editors that
  consume tree-sitter folding (function bodies, control-flow
  blocks, struct/enum/match bodies, block comments, list literals).
- `queries/locals.scm` — NEW. Lexical-scope + def-ref tracking for
  goto-definition fallback in editors that don't run nova-lsp
  (covers fn/lambda/block/if/while/for/match scopes; definitions
  for fn, extern fn, parameter, let, for-binding, struct/enum
  types, struct fields; references for every identifier slot).
- `queries/highlights.scm` — added `->`, type-annotation captures
  on parameters / let / return-type slots.
- `test/corpus/literals.txt` — NEW. 7 new tests: escape sequences,
  hex escapes, `${...}` interpolation, numeric underscores, empty
  + nested list literals, `none` literal.
- `test/corpus/declarations.txt` — NEW. 7 new tests: type-annotated
  parameter + return type, typed let, single + multi-variant enums
  (with trailing comma), typed struct fields, no-arg extern fn,
  nested module imports.
- `INSTALL_NEOVIM.md` + `README.md` — documented all three query
  files, capability matrix per editor, install snippet for
  fold + locals modules.

### Verification

- `tree-sitter generate` → portable parser.c (379 KiB).
- `tree-sitter test` → **41 / 41 corpus tests pass** (was 27).
- `tree-sitter parse` on representative files:
  - `examples/hello.nova` → 0 ERROR / 0 MISSING nodes.
  - `examples/wasi_file_roundtrip.nova` → 0 ERROR / 0 MISSING nodes.
  - `examples/showcase.nova` → 0 ERROR / 0 MISSING nodes, includes
    two `(interpolation ...)` captures.
  - Sweep over all `examples/*.nova`: **59 / 65 (~91%)** clean,
    same 6 cognitive-DSL files (`soul`/`mind`/`system`) still
    out of scope (documented in `tools/tree-sitter-nova/README.md`).
- `tree-sitter query queries/highlights.scm hello.nova` produces
  expected captures (keyword, function, function.builtin, string,
  punctuation.bracket).
- `tree-sitter query queries/folds.scm` and
  `tree-sitter query queries/locals.scm` both load + match.
