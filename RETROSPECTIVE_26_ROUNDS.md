# 26-Round Retrospective -- NOVA side

The cross-repo sprint retrospective is the unified document in CrossEngin:

    /home/user/Crossengin-demo/RETROSPECTIVE_26_ROUNDS.md

Both the CrossEngin and the NOVA stories are interleaved there. This
shorter file is a NOVA-facing pointer + a focused summary of the NOVA
language / toolchain arc.

## NOVA arc summary

Over the 26-round sprint NOVA grew from a stable v0.1.0 self-hosting
compiler with native targets, WASM/WASI, mobile, and IDE tooling into
a language with sum types, pattern exhaustiveness, generics
(enums/fns/structs), the `?` operator, struct brace-init, and a
struct update-syntax field-spread. Tree-sitter, LSP, and DAP tooling
kept exact pace, with the strict rule that tooling rounds land one
round *after* the parser/codegen round they refresh.

Notable NOVA rounds (full SHAs + writeups in the unified doc):

* R11D -- SIMD i32x8 intrinsics primitive (the spark for the multi-
  round CrossEngin SIMD perf chain that ultimately delivered 5.5x on
  stereo SAD, 3.69x on LK, and 137x on the raw `simd_sum_abs_diff`
  microbench).
* R13A -- inline SIMD + int_* builtins at call site -- closed the
  per-call dispatch overhead that R12A in CrossEngin exposed as a
  honest regression.
* R14B -- `simd_sad_u8` raw-byte SAD primitive via AVX2 vpsadbw.
* R15B -- WASM v128 SIMD lowering (closed R11D's last cross-target
  gap).
* R17A -- sum types with payloads + match exhaustiveness.
* R18A -- byte mul-acc SIMD primitives (closed the structural
  mismatch R17C named in CrossEngin).
* R20A -- Result + postfix `?` operator -- first "parser-only zero-
  cost" syntax round.
* R21A -- generic enum payload types (`Result<T, E>` truly
  parametric, runtime-zero).
* R22B -- generic function signatures.
* R23A -- generic structs + lightweight type-check pass.
* R24A -- fn-call + struct-ctor type-check (R23A.2 follow-up that
  gave the type-check pass teeth).
* R25A -- struct brace-init + destructure pattern.
* R26A -- struct update-syntax `Point { x: 10, ..p }`.

Tooling, in lockstep:

* R20D -- LSP quickfix (auto-add missing match arms).
* R21F -- LSP extract-function refactor.
* R22C -- LSP folding ranges + document symbols.
* R23F -- LSP workspace diagnostics aggregation.
* R24B -- tree-sitter R17A-R23A refresh.
* R24E -- type-aware LSP completion.
* R25F -- LSP inline-variable refactor.
* R26B -- tree-sitter R25A brace-init + destructure refresh.
* R26D -- LSP brace-init field completion.

## NOVA-side institutional knowledge in the unified retrospective

Section 6 of the unified doc covers the NOVA language evolution arc.
Section 5 covers how the NOVA codegen story interleaved with the
CrossEngin SIMD story across R11D -> R12A -> R13A -> R14B -> R15A ->
R17C -> R18A -> R18A.2 (an eight-round multi-repo chain). Section 2
covers the cross-agent coordination patterns that worked, including
the parser-tooling serialization rule. Section 4 covers the honest
engineering disclosures (R12A, R17C, R21D, R22F, R26F) -- two of
those (R12A's regression, R17C's 0.80x) directly drove NOVA codegen
rounds (R13A, R14B, R18A) and are joint CE+NOVA stories.

For the full retrospective, read
`/home/user/Crossengin-demo/RETROSPECTIVE_26_ROUNDS.md`.
