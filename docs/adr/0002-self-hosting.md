# ADR 0002: Self-hosting bootstrap -- stage1 -> stage2 -> stage3 invariant

## Status
Accepted (R36F) -- decision originally made pre-R0; this ADR documents
the rationale and the invariant.

## Context
NOVA needs to be implemented in some language. The choices for the
implementation language are:

  1. **A larger existing language** (Rust, Go, C, OCaml). Pro:
     immediate access to a mature toolchain. Con: NOVA's runtime
     properties (no libc, direct syscalls, deterministic memory) are
     not naturally expressed in any of these; we'd be writing a NOVA
     interpreter rather than a NOVA compiler that produces NOVA-shaped
     binaries.
  2. **An assembly seed + self-hosting.** The compiler is
     handwritten in assembly to a minimum viable level, then the
     rest of the compiler is written in NOVA and compiled by that
     seed. Once stage2 (NOVA-compiled NOVA) can compile NOVA, we have
     a self-host fixed point.

The second path has historical precedent (Lisp, Smalltalk, OCaml, Go,
Rust) but requires more upfront effort. The first path is easier to
get going but produces a compiler that is structurally beholden to
its host language.

## Decision
**NOVA bootstraps via a three-stage path.**

  - **stage1**: 106,045 lines of handwritten x86-64 assembly.
    Implements the minimum lexer + parser + codegen needed to
    compile a useful subset of NOVA.
  - **stage2**: stage1 compiles `compiler/main.nova` (the full NOVA
    compiler written in NOVA). The output is `stage2.s`. stage2
    knows the full language; stage1 only knew enough to bootstrap.
  - **stage3**: stage2 compiles `compiler/main.nova` (same source as
    stage2's input). The output is `stage3.s`.

**The invariant: `stage2.s == stage3.s` byte-identical.**

If stage2 and stage3 diverge, there is a compiler bug. The divergence
is the smallest possible repro -- the diff between the two assembly
outputs is the exact set of bytes that the compiler is emitting
differently.

This invariant is verified by `make self-host`. It is a pre-merge
CI gate for any change to:
  - `src/compiler/parser.nova`
  - `src/compiler/codegen.nova`
  - `src/compiler/register_allocator.nova`
  - `src/compiler/x86_64_lowering.nova`
  - `src/compiler/ast.nova`
  - `src/compiler/main.nova`

## Consequences
**Positive.**
  - **Regression detection cost = 30 seconds.** A change that breaks
    the invariant is detected by `make self-host` immediately. The
    diff localises the bug. No need to debug runtime behaviour to
    find a compiler bug.
  - **Determinism is forced.** Codegen cannot depend on hashmap
    iteration order, timestamps, random numbers, or any other
    non-determinic source. The invariant fails if it does.
  - **Refactor confidence.** Major compiler refactors (R8, R17, R21,
    R26) all preserved the invariant. We trust the test signal.
  - **The seed shrinks over time.** Anything moved out of stage1
    assembly into stage2 NOVA stays in NOVA. Future maintenance is
    in the higher-level language.

**Negative.**
  - **Non-determinism is forbidden in codegen.** Every hashmap that
    influences output must iterate deterministically. Every
    backtracking algorithm in codegen must produce stable output.
    We accept this constraint.
  - **Compiler refactors land slower.** A new optimisation pass
    requires a stage2 -> stage3 reconvergence cycle: stage2 produces
    a new stage2.s, then stage3 must match. Forces the contributor
    to think about the fixed point.
  - **CrossEngin (downstream) inherits the discipline.** A bug in
    NOVA codegen can break CrossEngin's federation wire tests
    silently. CrossEngin ADR R36F-0005 documents this; the
    self-host invariant is CrossEngin's first line of defence.

**Follow-up rounds.**
  - R26F: regression hunt round added CI for `make self-host` on
    every PR.
  - R37+: extend invariant to ARM64 (NOVA's MACOS_AUDIT.md tracks
    macOS ARM64 separately).
  - R37+: extend invariant to Windows PE32+ output (WIN32_AUDIT.md).

## Alternatives considered
  - **Implement NOVA in Rust.** Rejected: NOVA's runtime shape
    (no libc, direct syscalls, arena allocator) is intrinsic and
    would force a Rust implementation to ship two ABIs.
  - **Implement NOVA in C.** Rejected: same issue, plus C's bug
    surface is exactly what NOVA exists to escape.
  - **Use an existing compiler-construction toolkit** (LLVM, Cranelift).
    Rejected: LLVM is too large to audit in two-founder mode;
    Cranelift is closer in spirit but still adds a dependency that
    breaks bit-identical self-host.
  - **Skip stage3.** Rejected: without stage3, we don't have the
    "compiler compiles itself byte-identically" property; a stage2
    bug could go unnoticed indefinitely.
  - **Add stage4.** Considered; rejected as redundant -- if stage2
    == stage3, then stage3 compiling itself produces stage4 == stage3
    by induction.

## The bit-identical property as documentation
A new contributor reading NOVA's `compiler/` for the first time can:
  1. Read `parser.nova` and form a mental model of what it does.
  2. Make a small change.
  3. Run `make self-host`.
  4. If the invariant fails, the contributor knows precisely what
     changed. Their mental model was wrong somewhere specific.

This makes the codebase self-documenting in a way test coverage
alone cannot. The compiler IS its own most thorough test case.
