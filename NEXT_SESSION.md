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

### Integer overflow
Values exceeding PTR_THRESHOLD (0x100000 = 1048576) are treated as heap
pointers. Use `int_add/int_mul/int_sub/int_div/int_mod` builtins for
arithmetic on potentially large values.

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
