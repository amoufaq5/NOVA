# NOVA Codegen Pointer-Threshold Bug

**Status:** known, worked around with `int_*` builtins (see "Escape hatch" below).
Full type-tag fix tracked under "Future work".

## Symptom

NOVA programs that perform integer arithmetic on values >= **0x100000
(1 MB = 1048576)** crash with a SIGSEGV, often deep inside `str_repeat`,
`_nova_concat`, or `_nova_list_repeat`. The same arithmetic on smaller
values works correctly. Symptoms include:

- `var * literal` in codegen.nova itself, where `literal >= 0x100000`,
  segfaulting the compiler at build time.
- `a * b` where both `a` and `b` exceed `0x100000` jumping into
  string-concatenation code with garbage operands.
- Bit-mask constants of 21+ bits (e.g. `& 2147483647` for a 31-bit LCG)
  silently producing wrong results because the `&` operand exceeds
  the threshold and the wrong branch is taken.

## Reproduction

```nova
fn main() {
    let a = 2097152      // 2 * 0x100000
    let b = 2097153
    print_int(a * b)     // CRASH: dispatches to str_repeat with operands = ints
}
main()
```

Vs. the safe form:

```nova
fn main() {
    let a = 2097152
    let b = 2097153
    print_int(int_mul(a, b))   // prints 4398048608256, correct
}
main()
```

## Root cause

`src/compiler/codegen.nova` defines a "smart-op" runtime dispatch for
the binary operators `+`, `*`, `<`, `>`, `<=`, `>=`, `==`, `!=`. Each
emits a tiny prologue that inspects both operands against a threshold:

```asm
_nova_add:
    cmp rdi, 0x100000         # is rdi a "likely heap pointer"?
    jl  .add_int
    cmp rsi, 0x100000         # is rsi a "likely heap pointer"?
    jl  .add_int
    jmp _nova_concat          # both look like pointers -> string concat
.add_int:
    lea rax, [rdi + rsi]
    ret
```

The threshold lives in one place:

```nova
// src/compiler/codegen.nova, line 8
let PTR_THRESHOLD = "100000"   // 0x100000 = 1048576 (1 MB)
```

The logic is correct for a NaN-boxing-free, single-tag runtime: the
heap allocator places its arena far above 1 MB, so any value above the
threshold is "obviously" a pointer (string, list, or map). It breaks
the moment a legitimate integer crosses the threshold:

| Operator     | Smart helper       | Crash on >= 0x100000 |
| ------------ | ------------------ | -------------------- |
| `a + b`      | `_nova_add`        | jumps to `_nova_concat` |
| `a * b`      | `_nova_mul`        | jumps to `str_repeat` / `_nova_list_repeat` |
| `a < b`      | `_nova_lt`         | jumps to `_nova_strcmp` |
| `a > b`      | `_nova_gt`         | jumps to `_nova_strcmp` |
| `a <= b`     | `_nova_le`         | jumps to `_nova_strcmp` |
| `a >= b`     | `_nova_ge`         | jumps to `_nova_strcmp` |
| `a == b`     | `_nova_eq`         | jumps to `_nova_str_eq` |
| `a != b`     | `_nova_neq`        | jumps to `_nova_str_eq` |

The arithmetic operators `-`, `/`, `%`, the bitwise `&`, `|`, `^`,
`~`, `<<`, `>>` do NOT use smart dispatch — they are already pure
scalar. Only the eight listed above are affected.

## Incidents (worked around in CrossEngin commits)

Six independent agents hit this bug while building CrossEngin (P0..P3),
each finding their own workaround:

| Session   | Module                       | Trigger                                  | Workaround                                          |
| --------- | ---------------------------- | ---------------------------------------- | --------------------------------------------------- |
| **P0.6**  | pacer (slow-mo testing)      | `nanotime() * factor`                    | inline `_raw_imul_add` asm shim                     |
| **P0**    | win32 `_ip_str_to_int`       | `var * 256 * 256 * 256`                  | chained `* 256` against an integer variable         |
| **P1.4**  | http_client port parser      | `port_hi * 256 + port_lo`                | `int_mul` + `int_add`                                |
| **P2.3**  | SIMD bench `dot(a, b)`       | 1 M-element accumulator product          | switched to `int_add` / `int_mul`                   |
| **P2.6**  | Klatt LCG                    | 31-bit textbook seed `& 0x7fffffff`      | dropped to 20-bit mask `& 0xfffff`                  |
| **P3.6**  | DP LCG                       | needed a wide mask but couldn't risk it  | 15-bit mask `& 0x7fff`                              |

In every case the crash signature is the same: SIGSEGV inside a
string/list runtime helper called with integer-shaped garbage operands.

## Escape hatch (this session)

`src/compiler/codegen.nova` exports 10 scalar-only integer builtins.
They emit one or two x86-64 instructions and DO NOT pass through any
smart-op prologue. They are safe for the full int64 range.

| Builtin              | Emitted x86-64                  | Notes                          |
| -------------------- | ------------------------------- | ------------------------------ |
| `int_add(a, b)`      | `lea rax, [rdi + rsi]; ret`     |                                |
| `int_sub(a, b)`      | `mov rax, rdi; sub rax, rsi`    |                                |
| `int_mul(a, b)`      | `mov rax, rdi; imul rax, rsi`   |                                |
| `int_div(a, b)`      | `mov rax, rdi; cqo; idiv rsi`   | signed                         |
| `int_mod(a, b)`      | `... idiv rsi; mov rax, rdx`    | signed                         |
| `int_shl(a, k)`      | `mov rax, rdi; mov rcx, rsi; shl rax, cl` | shift count via cl   |
| `int_shr(a, k)`      | `mov rax, rdi; mov rcx, rsi; sar rax, cl` | arithmetic shift     |
| `int_and(a, b)`      | `mov rax, rdi; and rax, rsi`    | full 64-bit                    |
| `int_or(a, b)`       | `mov rax, rdi; or  rax, rsi`    | full 64-bit                    |
| `int_xor(a, b)`      | `mov rax, rdi; xor rax, rsi`    | full 64-bit                    |

These are now BLESSED (not workarounds). Use them whenever:

1. either operand can exceed `0x100000`, OR
2. the call is in a hot loop and you want to avoid the two-`cmp` branch
   overhead (the bench `examples/bench_int_safe.nova` measures ~1.05x
   to 1.10x speedup on a small-value loop on x86-64 Linux).

The smart `+` and `*` operators remain for ergonomic
string-concatenation and list-repeat:

```nova
let greeting = "hello, " + name
let row      = "-" * 80
let buf      = [0] * 256
```

These are still the right form for that syntax. They are NOT safe for
large-integer arithmetic; use `int_*` there.

## Why we did NOT raise PTR_THRESHOLD this session

The original task asked whether raising `PTR_THRESHOLD` from `0x100000`
(1 MB) to `0x40000000` (1 GB) would be a safe pragmatic patch. The
reasoning was: heap pointers on Linux are usually high, integer values
rarely reach 1 GB.

We measured the actual heap layout of NOVA binaries on this Linux host
(kernel 6.18, full ASLR enabled, `randomize_va_space=2`). The NOVA
allocator is a `brk()`-based bump allocator, so the heap starts at the
program break. Across 500 runs of a trivial `alloc(8)` program, the
minimum observed heap start address was **4,599,808 (~4.5 MB)**, only
~4x above the current threshold. The mean was around 500 MB, the max
about 1 GB. So:

- Raising `PTR_THRESHOLD` to **0x40000000 (1 GB)** would misclassify
  every heap pointer (which is < 1 GB in ~50% of runs) as an integer,
  silently breaking string `+` / list `*` / `==` for all programs.
- Raising it to even **0x300000 (3 MB)** would crash some runs:
  heap-start landed below 5 MB in multiple runs out of 500.
- Raising it to a marginal **0x200000 (2 MB)** would give a 2x headroom
  for integers but still has occasional risk under fresh-process ASLR.

The right long-term fix is Option 1 below (a real tag bit), not a
threshold raise. We have therefore left `PTR_THRESHOLD = "100000"`
unchanged and rely on the `int_*` escape hatch instead.

If the allocator is ever switched from `brk()` to an `mmap()` arena
placed at a known-high fixed address (e.g. 0x7000_0000_0000), the
threshold can be safely raised and this bug class effectively retires.

## Future work — Option 1 (tagged values)

The correct long-term fix is a low-bit pointer tag. Sketch:

- All NOVA values become 63-bit tagged.
- Bit 0 = 1 for integers (shifted left by 1, sign extended).
- Bit 0 = 0 for pointers (heap-aligned to 8 bytes already, so the low
  bit is naturally 0).
- `+`, `*`, `==` etc. dispatch on bit 0, not on magnitude.
- Heap allocator stays unchanged.
- Codegen changes: every integer literal must shift-and-set bit 0;
  every integer arithmetic op must shift-out and shift-back; pointer
  loads / stores stay byte-exact.

Estimated effort: 2-3 weeks of compiler work + bootstrap regen + full
test sweep. Out of scope for this session.

## Verification done in this session

- `make self-host` — stage2.s == stage3.s after adding the new builtins.
- `make test` — all in-tree tests pass.
- `examples/bench_int_safe.nova` exercises:
  - `int_mul(2097152, 2097153) == 4398048608256` (correctness above
    threshold; smart `*` would crash here).
  - `int_add(2097152, 3145728) == 5242880` (correctness above
    threshold).
  - `int_and/int_shr/int_shl/int_or` round-trip of `0xFFFFFFFF`
    through a 20-bit split (correctness of all four bitops).
  - Throughput comparison `acc = (acc + j*(j+1)) % 65536` for 1.024 M
    iterations, smart vs scalar. On my run: smart 17.1 ms,
    scalar 15.8 ms (~1.08x speedup, consistent with one branch
    saved per arithmetic op).

## File map

| File                                     | Purpose                                       |
| ---------------------------------------- | --------------------------------------------- |
| `src/compiler/codegen.nova` (line 8)     | `PTR_THRESHOLD = "100000"` (unchanged)        |
| `src/compiler/codegen.nova` (~5879)      | `_nova_add` smart dispatch                    |
| `src/compiler/codegen.nova` (~5891)      | `_nova_mul` smart dispatch                    |
| `src/compiler/codegen.nova` (~10769-10814) | `int_*` builtin codegen                     |
| `src/compiler/codegen.nova` (~991-1000)  | `int_*` registration in `is_builtin_fn`       |
| `examples/bench_int_safe.nova`           | correctness + speed bench                     |
| `NOVA_BUG_THRESHOLD.md` (this file)      | bug documentation                             |
