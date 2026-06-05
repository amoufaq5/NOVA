# ADR 0003: Pattern matching evolution -- R17A enums to R33C if-let + guards

## Status
Accepted (R36F) -- documents the rounds-up arc of NOVA's pattern
matching surface and what each step unlocked.

## Context
At R0, NOVA had no pattern matching. Discriminated unions were
expressed via integer tags + manual `if tag == 0 { ... }` chains.
The cost surfaced quickly: CrossEngin's substrate code does heavy
case analysis on signal types, KG entry shapes, and audit decisions.
Every such switch was a maintenance liability -- adding a new variant
forced editing every consuming chain.

The candidate end-states were:

  1. **Tagged-union assistance only** -- syntactic helpers for
     constructing + destructuring tagged unions, but no `match`
     expression.
  2. **Full `match` expression with exhaustiveness checking, pattern
     binding, nested patterns, guards.** ML / Rust shape.
  3. **Something in between, shipped incrementally.** Each
     incremental step is a separate round.

A two-founder team in 18-30 months cannot ship option 2 in one round.
Option 1 leaves CrossEngin's substrate code awkward. Option 3 -- pay
in instalments -- is what NOVA actually did.

## Decision
**Pattern matching shipped incrementally across five rounds.** Each
round was scoped to what unblocked the next CrossEngin track.

### R17A: enums + tag-aware destructure (the foundation)
  - Value-typed enums with tag + payload list.
  - Construction: `Signal::Activation { intensity: 0.7 }`.
  - Destructure at use site via syntactic helpers (no `match` yet).
  - Unlocked: signal-type taxonomy in CrossEngin (existing ADR
    0008).

### R21A: generics + generic enums
  - Type parameters on enum declarations.
  - `Option<T>` and `Result<T, E>` become writable in NOVA.
  - Codegen erases type parameters (consistent with NOVA ADR 0001).
  - Unlocked: KG entry shapes parameterised by domain type.

### R31D: `match` expression
  - First-class `match <expr> { Pat => ..., Pat => ..., _ => ... }`.
  - Each arm is a single expression or block.
  - Pattern surface limited at this round: tag patterns + variable
    binding, no nesting.
  - Codegen lowers to a jump table when feasible, falls back to
    if-else chain.
  - Unlocked: gate routing logic that previously needed nested
    if-else chains.

### R32D: nested patterns + `let` destructure
  - `match Pair { (Some(x), Some(y)) => ..., _ => ... }`.
  - `let (a, b) = pair_value`.
  - Pattern is now a tree, not a flat tag-and-bind.
  - Unlocked: episodic-memory record destructure; concept-pair
    similarity scoring.

### R33C: `if-let` + arm guards
  - `if let Some(x) = expr { use x }`.
  - `match e { Pat if cond => ..., _ => ... }`.
  - Guards run AFTER the pattern matches but BEFORE the arm body.
  - Unlocked: filtered KG lookups; soul-state conditional dispatch.

## Consequences
**Positive.**
  - **Each round delivered a usable improvement.** CrossEngin's
    substrate code at R34 reads dramatically cleaner than at R16.
    The pattern-match surface is now where ML / Rust users would
    expect.
  - **No big-bang refactor.** Each round was 1-2 weeks of work,
    not a 6-month rewrite. The team did not have to halt
    CrossEngin progress while NOVA caught up.
  - **Codegen complexity grew with feature surface.** Each round
    added a manageable codegen extension (R31D: jump table; R32D:
    pattern tree walker; R33C: guard hoist). Code review per
    round was tractable.

**Negative.**
  - **No exhaustiveness checking at compile time.** A `match` that
    forgets a variant produces a runtime "unmatched pattern" error,
    not a compile error. This is a known gap; deferred to R37+.
  - **Pattern syntax differs slightly from Rust / ML idioms.**
    NOVA's pattern operators are aligned with NOVA's parser style,
    not with conformity to any external precedent. New contributors
    with Rust / ML background need a brief on-ramp.
  - **Tree-sitter grammar lags.** R34E shipped tree-sitter coverage
    for the R31D + R32D + R33C surfaces (the "match expr + nested
    patterns + let destructure + if-let + arm guards" commit). IDE
    syntax highlighting works for everything currently in the
    language. Closures (R35C) are the new lag (see ADR 0005).

**Follow-up rounds.**
  - R37+: exhaustiveness checking at compile time.
  - R37+: pattern-match-as-expression-and-statement disambiguation
    (currently match is statement-only in some contexts).
  - R38+: or-patterns (`Pat1 | Pat2 => ...`).

## Alternatives considered
  - **Ship full pattern matching in one round.** Rejected: too
    large for a single round's scope. CrossEngin would have stalled.
  - **Skip pattern matching entirely; tagged-union helpers only.**
    Rejected: CrossEngin's substrate code is heavy in case
    analysis. The cost compounds.
  - **Defer to a "tooling pass" that does pattern matching in the
    LSP rather than the language.** Rejected: pattern matching is
    a language feature, not a UI feature. The LSP can't fix
    "this case-analysis-heavy code is hard to read in the file."
  - **Adopt Rust's exact pattern syntax.** Considered; rejected in
    R31D because NOVA's existing precedence-climbing parser was
    cleaner with NOVA-shape match. Compatible-but-not-identical.

## Why the round-by-round shape matters
Each pattern-matching round is documented in `NEXT_SESSION.md` with
its own "what shipped" + "what's deferred" + "honest caveats"
sections. A contributor wanting to understand "why doesn't NOVA
have or-patterns" can read R31D's deferred-list directly. This is
the round-based development model (CrossEngin's docs/CONTRIBUTING.md)
applied to a language feature.
