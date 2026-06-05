# ADR 0005: Static-slot closure lowering (R35C) -- choice + the "last write wins" caveat

## Status
Accepted (R36F) -- documents R35C's design choice for closure capture
and the planned R37+ migration path.

## Context
At R34F, NOVA did not have closures. Lambdas existed via the
`fn(params) { body }` form, which compiled to a top-level function
and produced a function pointer; this works for "pass a function as
an argument" but does not capture surrounding state. CrossEngin's
substrate scripts (gate logic, decision rules, plasticity callbacks)
needed real closures: lambdas that capture local variables from the
enclosing scope.

Two end-states for closure capture in a NOVA-shaped language:

  1. **Static-slot capture.** Each closure literal in source code
     gets a fixed set of `.bss` slots, one per captured variable.
     The closure body reads `[rip + _cap_<lname>_<var>]` for each
     capture. Pro: zero ABI changes; closures remain plain function
     pointers; codegen is small. Con: two instances of the same
     closure literal share slots, so "last write wins" is the
     observable semantics.
  2. **Tuple lowering: `[fn_ptr, env_list]`.** Each closure value
     is a 2-element list. Indirect-call ABI is extended: the
     caller prepends `env_list` to the argument vector before
     calling `fn_ptr`. Pro: correct semantics for multi-instance
     closures + by-reference capture is straightforward. Con:
     ABI change (every indirect call site must adjust); codegen
     is larger; runtime list allocation per closure value.

R35C had a hard scope budget. The team wanted closure literals
shipped in one round to unblock the substrate code that wanted
them. Option 2 is the right end-state but too large for R35C.
Option 1 is the right stepping stone.

## Decision
**R35C lowers closures using static-slot capture.** Specifically:

  - Closure literal syntax `|x, y| x + y * z` (R35C grammar).
  - Each closure literal becomes a synthetic top-level function
    `_lambda_N` whose `AST_FN_DECL` is appended to `par_lambdas`.
  - `compute_captures` walks the body, subtracting closure params +
    body-bound `let` names + builtins + globals from the identifier
    set; the residue is the capture set.
  - Each captured variable gets a `_cap_<lname>_<var>` slot in
    `.bss` via the existing `cg_capture_slots` registry.
  - The closure expression site emits per-capture loads + stores
    into the static slot, then `lea rax, [rip + _lambda_N]` to
    produce the function pointer.
  - The closure body reads captures via
    `mov rax, [rip + _cap_<lname>_<var>]`.
  - Indirect-call ABI is unchanged (SysV / NOVA standard): the
    function pointer is naked; captures are read via static slots
    inside the body, not threaded through the call.

### Capture semantics shipped
  - **Globals**: live-binding. The closure reads the global slot
    directly. Mutations to the global are visible inside the
    closure.
  - **Locals**: by-value at closure-creation time. The capture
    write snapshots the local's current value into the static
    slot. Subsequent mutations of the source local do NOT update
    the closure's view.

### The "last write wins" caveat (load-bearing)
Because the capture slots are static (one set per source-position
lambda), two instances of the same closure literal share slots:

```nova
fn make_adder(k: int) -> Closure {
  return |x| x + k    // captures k into _cap__lambda_0_k
}
let add5 = make_adder(5)   // stores 5 in _cap__lambda_0_k
let add7 = make_adder(7)   // overwrites with 7
add5(3)                    // returns 10 (3 + 7), NOT 8
```

`tests/unit/test_closures.nova` includes an EXPLICITLY VALIDATED
test for this limitation -- the failure mode is documented as
intentional in R35C, not a bug.

### Other R35C limitations
  - **No by-reference capture.** Workaround: box mutable state in a
    single-element list; both parties read/write through `[0]`.
  - **No closure literals in tree-sitter grammar (yet).** R34E
    shipped match-expression grammar; closure literal grammar is a
    follow-on. IDE syntax highlighting will treat `|x| x + 1` as a
    syntax error until the tree-sitter pass lands.

## Consequences
**Positive.**
  - **Closures shipped in one round** without ABI changes. Existing
    indirect-call code paths continue to work unchanged.
  - **Codegen footprint is small.** R35C added ~600 lines across
    `parser.nova` and `codegen.nova`; no register-allocator or
    x86_64-lowering changes were required.
  - **Bit-identical self-host invariant** (ADR 0002) was preserved:
    stage2.s == stage3.s after R35C.
  - **The single-instance case works correctly.** Closures used as
    callbacks (gate decision functions, plasticity update rules)
    that are created once and called many times are correct.

**Negative.**
  - **Multi-instance is wrong.** `make_adder(5)` + `make_adder(7)`
    clobber. Documented in test; documented in NEXT_SESSION.md;
    documented in this ADR. The fix is the R37+ migration below.
  - **No by-reference capture** for now. Workaround is real but
    awkward.
  - **Tree-sitter lag** means IDE highlighting is missing for
    closure literals until R37+.

**Follow-up rounds.**
  - **R37+ closure migration.** Lower closures to `[fn_ptr,
    env_list]` 2-tuples. The indirect-call ABI extension: the
    caller prepends `env_list` to the argument vector; the callee
    treats the first argument as the env handle and reads captures
    via env indexing. Estimated scope: ~1500 lines across parser
    (closure value construction), codegen (indirect-call ABI shim),
    and runtime (env_list lifecycle). Tests in
    `test_closures.nova` switch from "last write wins documented"
    to "per-instance correct."
  - **R37+ tree-sitter closure grammar.** Add closure-expression
    rule to `grammar.js`, regenerate parser, ship VS Code
    highlighting.

## Alternatives considered
  - **Ship tuple lowering in R35C.** Rejected: scope budget. Two
    things would have been wrong: (a) the round wouldn't have
    closed in one round, and (b) the ABI change risk on the
    indirect-call surface would have been too high to land
    alongside the closure grammar.
  - **Skip closures; recommend `fn() { ... }` lambdas + manual
    state-passing.** Rejected: CrossEngin's substrate code wanted
    closures, and the cost of the workaround (everything is
    `fn(state_handle, ...args)`) compounds across many call sites.
  - **Lower closures via stack-allocated environments.** Rejected:
    closures can outlive their creating scope (e.g. stored in a
    list, returned from a function); stack allocation breaks.

## Why document this as an ADR and not just a NEXT_SESSION.md entry
Future contributors will encounter the "last write wins" caveat
when their multi-instance closure misbehaves. An ADR is the right
place for "yes, we know; here's the migration plan." The R35C
NEXT_SESSION.md entry has the implementation detail; this ADR has
the design rationale.
