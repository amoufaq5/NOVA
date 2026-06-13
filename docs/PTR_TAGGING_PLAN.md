# NOVA value-tagging rework — implementation plan (bug-#11 root fix)

**Status:** PLAN. Not yet implemented. Supersedes the "Future work — Option 1"
sketch in `NOVA_BUG_THRESHOLD.md`. Authored after confirming the current state
of the codebase (below).

## 0. Current state (verified 2026-06-13)

The original 1 MB magnitude heuristic is **already gone**. `gen_runtime()` in
`src/compiler/codegen.nova` (≈ line 15604) classifies a value as a pointer by
**address range** (`_nova_check_rdi` / `_nova_check_rsi`): a value is a pointer
iff it lies in `[_strlit_start,_strlit_end)` or `[_heap_base,_heap_end)`;
negatives are always integers. This is live in the shipped `bin/nova` (verified:
`2097152 * 2097153` and `x == 0 - 1` both work).

`PTR_THRESHOLD = "100000"` (line 8) is now **dead** (referenced only in a
comment).

**Residual bug:** `_nova_check_*` has a third range — addresses `≥ 0x400000000`
(16 GiB) are assumed pointers (argv/env/stack/mmap-heap on non-brk targets). So
integer arithmetic where **both** operands are `≥ 16 GiB` still segfaults
(verified: `20000000000 + 20000000001` crashes). Ceiling moved 1 MB → 16 GiB
(16,384×) but the bug *class* survives for huge integers (nanotimes, big
products). `int_*` remains the escape hatch for those.

Tagging removes the residual entirely by dispatching on a **tag bit**, not an
address range — no magnitude assumption anywhere.

## 1. Scheme

63-bit low-bit tagging:
- **Integer** `n` → machine word `(n << 1) | 1` (bit 0 = 1). Read back with
  arithmetic shift right (`sar rax, 1`), sign-extending. Range: ±2^62
  (≈ ±4.6e18 — covers nanotimes ~1e18; full int64 is *not* representable, see §6).
- **Pointer** → unchanged. Heap/strlit allocations are already 8-byte aligned,
  so bit 0 is naturally 0. Loads/stores stay byte-exact.
- Operators dispatch on **bit 0** (`test rax, 1`), never on magnitude.

Tag-preserving arithmetic identities (avoid untag/retag where possible):
- `add(a,b)` = `a + b - 1`
- `sub(a,b)` = `a - b + 1`
- `mul(a,b)` = `((a-1) * (b>>1)) + 1`  (one operand untagged, result re-tagged)
- `div/mod` = untag both (`sar 1`), op, re-tag quotient/remainder
- bitwise `& | ^` = operate on tagged words directly **only** with care (the tag
  bit participates); safest is untag→op→retag. `<< >>` = untag shift amount,
  untag value, shift, retag.
- Comparisons `< > <= >= == !=`: tagging is **monotonic and bijective**, so two
  tagged ints compare correctly with a direct signed `cmp` (no untag). The helper
  only needs the bit-0 dispatch: both-int → direct `cmp`; otherwise pointer path
  (strcmp / identity).

## 2. Touch-point inventory (every site, with current line refs)

Monolithic: ALL of these must change together or the first compiled program
(including the compiler itself) faults.

| # | Site | File:line (current) | Change |
|---|------|--------------------|--------|
| 1 | Integer literal emission | codegen literal path (NUM token → `mov`) | emit `(n<<1)|1` |
| 2 | Smart binop dispatch | `_nova_check_rdi/rsi` 15604; `_nova_add` 15688, `_nova_mul` 15700, `_nova_eq` 15784, `lt/gt/le/ge/neq` | replace range-classify with `test ...,1`; apply §1 identities |
| 3 | `int_*` builtins | `is_builtin_fn` 2162+/2388+, codegen 2679+ | become tag-aware (untag in / retag out) or retire (tagging makes `+`/`*` safe to 63-bit) |
| 4 | List/string index | gen_index path; element-addr = base + (idx_untagged * 8) | `sar 1` the index before scaling |
| 5 | `_nova_alloc` | 16105 | size arg is tagged → `sar 1`; returns a pointer (untagged, already aligned) |
| 6 | `_nova_len` | 16379 | returns a length → re-tag `(len<<1)|1` |
| 7 | `_nova_int_to_str` | 16244 | arg tagged → `sar 1` before formatting |
| 8 | `_nova_str_to_int` | 16673 | returns int → re-tag |
| 9 | `_nova_concat`/`str_repeat`/`list_repeat`/`strcmp`/`str_eq` | 16203/20752/15740/15911/16300 | length/count args tagged → untag; returned lengths re-tagged; char/byte loads raw |
| 10 | **Syscall boundary** | `emit_syscall` 573, all stdlib syscalls | EACH integer arg (fd, size, offset, flags) → `sar 1`; pointer args stay raw; integer return → re-tag. **Highest-risk: a missed untag here = silent kernel corruption, not a clean crash.** |
| 11 | FFI / addr-as-int | any `mmap`/buffer-address exposed as a NOVA int | tag on the way out, untag on the way in |
| 12 | Float64 (R38A) | float boxing path | floats are heap-boxed → bit 0 = 0 already; verify no raw-double-as-int path |
| 13 | `print_int` / debug | uses `int_to_str` | covered by #7 |

Compile-time `int_to_str` calls in DWARF/`.loc` emission operate on the
*compiler's own* ints — those become tagged uniformly in stage3 and need no
special handling (the compiler is just another tagged program).

## 3. Bootstrap procedure (the only safe path)

`boot/nova_boot` is a **stable static ELF interpreter** (untagged) — the anchor.

1. Edit `codegen.nova` (this branch) so it EMITS tagged code everywhere (§2).
2. `boot/nova_boot` interprets the combined source (untagged semantics — it only
   computes numbers and emits asm strings) → **stage1.s** = tagged code →
   assemble/link → **stage1** (a tagged binary that emits tagged code).
3. stage1 compiles combined → **stage2.s**; stage2 compiles combined →
   **stage3.s**.
4. **Fixpoint gate:** `stage2.s == stage3.s` byte-identical. (stage1.s will
   differ from stage2.s — representation transition — which is expected; the
   fixpoint is stage2≡stage3.)
5. Install to `bin/nova` **only after** the fixpoint AND all tests pass. Build
   every intermediate to `/tmp` scratch paths; **never overwrite `bin/nova`
   until verified** (CrossEngin's `/home/user/NOVA/nova` uses it live).

## 4. Verification gates (all must pass before install)

1. `20000000000 + 20000000001` via smart `+` → correct (no crash) — the residual
   is gone.
2. Full 63-bit round-trips: `(1<<62)-1`, negatives, bit ops.
3. NOVA `make test` (in-tree suite) green.
4. Self-host fixpoint (§3.4).
5. CrossEngin full suite (273/273) green against the rebuilt `bin/nova`.
6. `examples/bench_int_safe.nova` still correct.

## 5. Rollback

All work on branch `claude/adoring-wozniak-gdnye9`; `bin/nova` untouched until
§4 passes. If a stage miscompiles, `git checkout` the branch and the shipped
`bin/nova` is still the known-good range-classified compiler.

## 6. Known consequence & follow-ups

- **63-bit integer ceiling.** Tagging spends one bit, so ints are ±2^62, not
  full int64. This is the standard tagged-runtime tradeoff and covers all
  practical values. If true 64-bit ints are ever needed, they must be heap-boxed
  (like floats) — out of scope.
- **`int_*` builtins** can be retired once tagging lands (smart `+`/`*` are then
  safe to 63 bits), or kept as raw-64 escape for the boxed-int case.
- **CrossEngin `int_safety_lint`** becomes unnecessary for the negative-literal
  and `<16 GiB` cases (already fixed) and for everything once tagging lands;
  retire it then, or keep the `>2^62` guard.

## 7. Effort

Consistent with the doc's original estimate: ~2-3 weeks. The change is
**monolithic** (no runnable partial — §2), so it is implemented on a branch with
the scratch-build/fixpoint loop and landed atomically once §4 is green.
