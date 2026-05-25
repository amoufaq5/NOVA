# NEXT_SESSION.md — Nova N12–N29 Implementation Status

## Completed

All 18 modules (N12–N29) are fully implemented with:
- Implementation files under `src/cognitive/`, `src/runtime/`, `src/tooling/`
- Unit tests in `tests/` (all passing: 151/157, 0 failures, 6 skipped)
- Example programs in `examples/` (18 new `*_demo.nova` files)
- Documentation in `docs/STDLIB.md`
- Self-hosting verified (`stage2.s == stage3.s`)
- Committed and pushed to `claude/nova-language-design-dZn3q`

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

### Compiler 7th-parameter bug
Functions with 7+ parameters produce incorrect values for the 7th argument
(first stack-passed argument returns 0). Workaround: keep functions to 6
parameters max; push additional values after creation. Applied in
`causal_library.nova` (`causal_pattern_new` and `_causal_add_seed`).

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

## Files Modified in Compiler

- `src/compiler/compiler.nova` — extended `_find_std_file` to search
  `src/core/`, `src/agent/`, `src/cognitive/`, `src/tooling/` directories;
  added dependency entries for all 18 modules in `init_import_tracker()`
- `src/pkg/pkg.nova` — added all 18 packages to STD_PACKAGES list
- `tests/run_tests.sh` — added 18 case blocks for source concatenation

## What's Left (for future sessions)

1. The plan file references Phase 1–4 improvements (tensor perf, cognitive
   LLM, embeddings, import scaling) — those are separate from N12–N29
2. Consider fixing the 7th-parameter compiler bug in codegen.nova
3. Example programs are standalone demos — they don't compile/run without
   library source concatenation (by design, matching existing examples)
