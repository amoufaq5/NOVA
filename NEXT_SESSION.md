# NEXT_SESSION.md — Nova Implementation Status

## Completed

### N12–N29 Modules (18 modules)
All fully implemented with:
- Implementation files under `src/cognitive/`, `src/runtime/`, `src/tooling/`
- Unit tests in `tests/` (all passing: 151/157, 0 failures, 6 skipped)
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

### is_global hash table incompatibility
The hash table optimization for `is_global` causes segfaults when compiling
destructuring patterns (`let [a, b] = ...`). Root cause: values passed to
`is_global` during `collect_locals` may not always be valid string pointers.
The hash table works for `is_known_function` because function names are
always string literals from the AST. `is_global` remains O(n) linear scan.

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
- `make test-all` — 152/160 pass (the 2 pre-existing destructure
  failures are unchanged; +1 net pass from the new regression test).
- `make bench-int-safe` — large-int correctness PASS; smart-op now
  ~2.3-2.8x slower than `int_*` due to the call-vs-inline cost, which
  is still acceptable and the int_* speedup is itself documented.
- CrossEngin `make test` — 142/142 pass.

Proof-of-fix: `crossengin-demo/src/safety/bignum_2048.nova` `bn2048_add`
and `bn2048_sub` were converted from `int_add` / `int_sub` calls to plain
`+` / `-` operators; the bignum test suite continues to pass bit-
identical results (modpow round-trips, Montgomery vs legacy parity).

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
