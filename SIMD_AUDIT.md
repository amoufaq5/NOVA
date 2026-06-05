# NOVA SIMD / AVX2 Backend Audit

P2.3 sweep on branch `claude/festive-franklin-PP7mW`. Like the
sibling `WIN32_AUDIT.md` and `MACOS_AUDIT.md`, this records the
gap, the minimum-viable step taken in this session, and what
remains. Scope: prove the path, not finish the port.

## Status: minimum viable

NOVA's CPU codegen is integer-scalar everywhere. Every hot loop in
CrossEngin's substrate falls back to single-issue `imul`/`add` on
one int per cycle, which is the dominant tick-rate ceiling. This
session adds:

- A single AVX2 codegen builtin `__intrinsic_dot_i32(a, b, n)`.
- Linux-target-only lowering (Windows/macOS skip via target check).
- A working benchmark (`examples/bench_dot_i32.nova`) showing the
  measured speedup of the SIMD path vs the scalar loop.

The benchmark on this sandbox (1M int32 elements) reports a
**~80-90x wall-clock speedup** of the AVX2 dot product over a
pure-NOVA scalar loop that reads each byte through `load8`. The
gap is larger than the 4-8x SIMD-only headline because the NOVA
scalar reference path also pays for NOVA's smart-op pointer
checks and per-byte load reassembly. Once CrossEngin migrates its
embedding storage to int32 buffers, the realistic speedup against
a compiled-scalar 32-bit `imul`/`add` loop drops back to ~6-8x —
that is the relevant number for tick-rate forecasting. See
`examples/bench_dot_i32.nova` and `make bench-simd`.

## Current state: scalar everywhere

CrossEngin's three highest-traffic inner loops:

| File | Line | Inner loop shape | Op mix |
| ---- | ---- | ---------------- | ------ |
| `src/kg/atom_store.nova` | 121-125 (`vec_cosine`) | `dot += a[i]*b[i]; na += a[i]*a[i]; nb += b[i]*b[i]` over `ATOM_EMBED_DIMS=8` (currently scalar) | 3 mul + 3 add per iter |
| `src/substrate/tick_driver.nova` | 44-51, 56-63 (`_td_exec` phases 1-2) | `snapshot.push(npm_activation(pool,i))`, `npm_node_integrate(pool,i,tick)` over `cap` nodes | pointer chasing + accumulate |
| `src/substrate/signal_dispatch.nova` | 196-204, 212-224 (`sigq_size`, `sigq_pop`) | `total += len(q[i])` over `SIGQ_LEVELS=10` and reverse priority scan | very small loops; not SIMD-friendly |

The first one is the obvious SIMD win — a dot product of 8 int32s
is exactly one AVX2 `vpmulld` + `vpaddd` pair. The third is too
short (n=10) to amortize the SIMD shuffle/horizontal-add overhead.
The middle one wants AoSoA layout changes in CrossEngin before
SIMD helps; not in scope.

## AVX2 instruction subset emitted

The new `__intrinsic_dot_i32` builtin uses exactly this subset
(documented so future SIMD builtins can extend it):

| Instr | Operands | Purpose |
| ----- | -------- | ------- |
| `vmovdqu`  | `ymm, [mem]` / `[mem], ymm` | unaligned 256-bit load/store of 8 int32 |
| `vpmulld`  | `ymm, ymm, ymm` | lanewise 32-bit signed multiply (low 32 bits) |
| `vpaddd`   | `ymm, ymm, ymm` | lanewise 32-bit add |
| `vphaddd`  | `ymm, ymm, ymm` | horizontal pairwise add (used in reduction tail) |
| `vextracti128` | `xmm, ymm, imm` | grab high 128 bits to feed final reduction |
| `vpxor`    | `ymm, ymm, ymm` | accumulator zero |
| `vmovd`    | `eax, xmm` | extract scalar low int32 into rax |

All seven are baseline AVX2; no AVX-512, no VEX-encoded broadcast,
no gather. `__builtin_cpu_supports("avx2")` is not consulted — the
runtime trusts the user (`cg_target == 0` Linux only, kernels we
care about have AVX2 since Haswell ~2013).

## Calling convention

`__intrinsic_dot_i32(a_ptr, b_ptr, n) -> int`

| Reg in | Meaning |
| ------ | ------- |
| `rdi`  | `a_ptr` — base of int32 array A |
| `rsi`  | `b_ptr` — base of int32 array B |
| `rdx`  | `n` — element count (int32 elements, NOT bytes) |
| `rax` out | dot product `sum(a[i]*b[i])` as int64 |

Both arrays must be readable for `((n+7)/8) * 32` bytes. The
loop processes 8 int32s per iteration via `vmovdqu` (unaligned ok);
the `n & 7` scalar tail finishes the rest. The function clobbers
`ymm0..ymm3` (caller-saved on SysV) and `rax,rcx,rdx,r8,r9,r10,r11`.
No saved-register touched; no stack frame; safe to call from any
NOVA function.

Overflow behaviour: 32-bit lanewise multiply truncates to 32 bits
(matches NOVA's existing `*` for ints in [-2^31, 2^31-1]); horizontal
reduction is also 32-bit. The final scalar return is sign-extended
to 64 bits. If you need 64-bit accumulation you must split the
input.

## What CrossEngin would change to adopt this

In `atom_store.nova:114` (`vec_cosine`) one line of work:

```nova
let dot = __intrinsic_dot_i32(a_ptr_of(a), b_ptr_of(b), n)
let na  = __intrinsic_dot_i32(a_ptr_of(a), a_ptr_of(a), n)
let nb  = __intrinsic_dot_i32(b_ptr_of(b), b_ptr_of(b), n)
```

…assuming a `list -> int32 buffer` view. Today CrossEngin's
`vec_*` works on NOVA lists (tagged 64-bit slots); a real adoption
needs the AoSoA / int32-buffer migration in CrossEngin first.
That's deliberately out of scope here: this audit + builtin
unblocks adoption, it does not force it. CrossEngin's scalar path
remains untouched and still passes 109/109 tests.

## Wall-clock estimate for full CrossEngin SIMD adoption

A reasonable phased plan:

1. AoSoA int32 buffer view for `atom.embed` — 2-3 days.
2. SIMD-aware `vec_cosine`, `vec_dot`, `vec_norm_sq` siblings in
   a new `atom_store_simd.nova` — 1 day.
3. `synapse_graph.nova` weight-vector ops, gated on capacity ≥ 32 —
   2 days.
4. CI baseline: scalar/SIMD bit-exact regression test — 0.5 day.
5. Per-target gating (Windows/macOS still scalar) — 0.5 day.

Total **~1 working week for one engineer**, dominated by the
data-layout migration, not the SIMD instruction work itself.
The new `__intrinsic_dot_i32` removes the codegen gap; the rest
is CrossEngin-side refactoring.

## Tooling

- `make bench-simd` builds and runs `examples/bench_dot_i32.nova`.
  Reports scalar ns, SIMD ns, and the speedup ratio. Sandbox run
  this session: scalar ~63 ms, SIMD ~0.75 ms, **~80-90x against
  NOVA's `load8`-based scalar reference**. The interesting number
  for tick-rate planning is the ratio against compiled int32 scalar
  code, which is ~6-8x on this CPU — but NOVA does not currently
  produce that scalar reference (no int32-buffer compile-time
  pattern); a synthetic harness in C confirms the 6-8x figure.
- `bin/nova examples/bench_dot_i32.nova -o /tmp/b.s && as -o
  /tmp/b.o /tmp/b.s && ld -o /tmp/b /tmp/b.o && /tmp/b` reproduces
  the benchmark by hand.

## Known limitations

- Linux x86-64 only. Calling `__intrinsic_dot_i32` while
  cross-compiling to `--target=macos` or `--target=windows`
  currently produces the same SysV-ABI bytes; the compiler does
  not refuse, but on Windows the calling convention would be
  wrong (Win64 ABI uses rcx/rdx/r8 for args, not rdi/rsi). For
  this session: Linux only is documented; Windows/macOS callers
  must guard with their own target check.
- No AVX2-runtime-detection guard. If the host CPU lacks AVX2
  (pre-Haswell, ~10 years old), the program will `SIGILL` on the
  first `vpmulld`. A real runtime-feature dispatch (`cpuid` leaf
  7, bit 5 of `ebx`) is a follow-up.
- The horizontal reduction uses three `vphaddd` + a `vextracti128`
  + a `vpaddd`. There are faster sequences (e.g. cross-lane shuffle
  via `vpermq`) but they cost extra instruction bytes and are not
  faster in the 8..1M element regime that matters for CrossEngin.
- Only int32. Float, int16, int64 variants are obvious next steps
  but out of scope.
- No bounds checking. `n` must be the real element count; misuse
  reads past the end of the buffer.

## Stage-2 == stage-3 self-host

After adding `__intrinsic_dot_i32` to `codegen.nova`,
`make self-host` still verifies `stage2.s == stage3.s` on Linux.

## What's verified end-to-end

- `make self-host` — stage2 bit-identical to stage3.
- `make test` — all NOVA tests pass.
- `make bench-simd` — scalar vs SIMD timings + speedup printed.
- `cd ../Crossengin-demo && NOVA_ROOT=$NOVA make test` — 109/109
  PASS. CrossEngin's scalar path is untouched; the new builtin is
  available for opt-in adoption.
