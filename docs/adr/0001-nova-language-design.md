# ADR 0001: NOVA language design -- minimalism and what's intentionally absent

## Status
Accepted (R36F) -- restates the rationale for the language shape that
emerged across R0..R35.

## Context
NOVA was designed to be the implementation language for CrossEngin
(see CrossEngin ADR R36F-0001), but the design constraints are
intrinsic to NOVA's own goals:

  - Bootstrap from a handwritten x86-64 assembly seed (stage1) into a
    self-hosting compiler (stage2 = stage3 byte-identical -- see
    ADR 0002).
  - Run on a single Linux x86_64 ELF binary with no libc, no GC, no
    runtime interpreter.
  - Keep the language small enough that two founders can hold the
    entire toolchain (lexer + parser + IR + register allocator +
    x86-64 lowering + codegen) in their heads.

The trade-off space:

  - Smaller language -> easier to self-host, easier to keep stage2 ==
    stage3, faster to onboard a new contributor, harder to express
    sophisticated abstractions.
  - Larger language -> richer expressiveness, more compiler surface
    area to maintain, harder bootstrap path.

## Decision
**NOVA is a small, mostly-imperative language with first-class
substrate primitives but no async, no traits, no implicit dispatch.**
Specifically:

### What NOVA has
  - **Structs + value-typed enums.** Enums carry a small integer tag
    and an optional payload list.
  - **Generics.** Landed R21A; type parameters are erased at codegen.
  - **Pattern matching.** Evolved from R17A (enums) -> R21A (generic
    enums) -> R31D (`match` expressions) -> R32D (nested patterns +
    `let` destructure) -> R33C (`if-let` + arm guards). See ADR 0003.
  - **Closures.** Landed R35C. Single-instance, static-slot capture
    lowering (see ADR 0005). Multi-instance + by-reference capture is
    deferred.
  - **Mind / Soul / System declarations.** Top-level constructs that
    wire up cognitive architectures directly in NOVA syntax.
  - **Flow operators** (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`)
    for signal routing.
  - **Arena allocator.** Default allocation strategy; explicit
    free is rare.
  - **Coroutines** for cooperative multitasking.
  - **Try / catch / finally** for structured error handling.
  - **270+ builtin functions** covering syscalls, string handling,
    list operations, math, IO.
  - **Direct syscalls.** Linux + Windows; no libc.

### What NOVA does NOT have
  - **No `async` / `await`.** Coroutines + the scheduler cover the
    use cases without introducing a separate concurrency model.
  - **No traits / interfaces / type classes.** Polymorphism is
    expressed via generics + tagged unions, not via implicit
    dispatch.
  - **No macros / metaprogramming.** A new builtin is added in
    Nova; the bar is "is this needed by the compiler itself or by
    the cognitive substrate primitives?"
  - **No multi-instance closures (yet).** R35C lowered closures to
    static `_cap_<lname>_<var>` slots. Two instances of the same
    closure literal share slots; "last write wins." Deferred for a
    future round (ADR 0005 covers the migration plan).
  - **No by-reference capture.** Workaround: box mutable state in a
    single-element list and read/write through `[0]`.
  - **No standard library extension via packages.** The package
    manager exists for source-level reuse; there's no equivalent of
    pip / cargo / npm. By design.
  - **No reflection.** Compile-time only.
  - **No garbage collector.** Arena + explicit lifetime management
    via lists / structs.

## Consequences
**Positive.**
  - **Bootstrap is tractable.** 16,467 lines of NOVA implement the
    compiler. The bootstrap-from-assembly stage1 seed is 106,045
    lines of handwritten x86-64. Both are auditable by humans.
  - **Self-host invariant holds** (ADR 0002). The simpler the
    language, the easier it is to keep stage2.s == stage3.s
    byte-identical.
  - **Two-founder mental model.** Every feature in NOVA is one the
    team has seen, used, and tested. There are no "library features"
    we don't understand from the inside.
  - **Substrate primitives are first-class.** Mind / Soul / System /
    flow operators land in the parser as their own AST nodes, not as
    library calls. This is what makes CrossEngin's cognitive
    architecture writable in NOVA syntax.

**Negative.**
  - **No async means I/O is blocking.** Coroutines + scheduler cover
    cooperative cases; blocking I/O on the substrate hot path is a
    footgun if you forget to spawn.
  - **No traits means N similar-but-distinct functions.** E.g.
    `list_map_int`, `list_map_str` -- generics partly mitigate this
    but it's a real verbosity cost.
  - **No multi-instance closures is a real limitation.** Closures
    are useful for cognitive scripts; R35C is a stepping stone, not
    the final design. ADR 0005 plans R37+ migration.
  - **No package ecosystem.** Every NOVA user re-derives common
    utilities. Counter-argument: CrossEngin's `safety/` leaves
    (ADR CrossEngin R36F-0006) are the right shape for this kind of
    canonical-leaf pattern.

**Follow-up rounds.**
  - R37+: closure migration to `[fn_ptr, env_list]` tuple lowering
    (ADR 0005).
  - R37+: pattern matching exhaustiveness checking at compile time
    (currently runtime-only).
  - R38+: tree-sitter closure grammar update (deferred at R35C per
    NEXT_SESSION.md).

## Alternatives considered
  - **Adopt Rust's trait system.** Rejected: implicit dispatch
    complicates self-host + codegen; explicit generics + tagged
    unions cover the substrate's needs.
  - **Adopt Go's interface system.** Same objection.
  - **Adopt full closure semantics (multi-instance + by-ref) in
    R35C.** Rejected: would have required new codegen + new ABI; the
    static-slot path was the minimum-viable shape that unblocks
    closure-using code paths in CrossEngin. Migration is planned
    (ADR 0005).
  - **Add a macro system.** Rejected: macros vastly expand the
    surface area we have to keep bit-identical across stage2 /
    stage3. The few cases where macros would help (struct field
    iteration, e.g.) can be handled by code generation scripts that
    emit NOVA source.
  - **Add a GC.** Rejected outright: a substrate ticking at ~100Hz
    cannot afford unpredictable GC pauses.
