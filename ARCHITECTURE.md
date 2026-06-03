# NOVA Architecture

This document is the layout-and-orientation guide for the NOVA
self-hosting compiler and runtime. It is intentionally complementary
to:

- `README.md` — version status, feature spotlights, builtin index
- `NEXT_SESSION.md` — cumulative session notes for parallel agents
- `docs/LANGUAGE_REFERENCE.md` — formal language reference
- `docs/STDLIB.md` — runtime library reference
- `STABILITY_AUDIT.md` — language + ABI stability inventory
- `COMPAT.md` — breaking-change log
- `INSTALL.md` — packaging + install layout

Where those documents tell you what NOVA does and how to use it, this
document tells you **what lives where** in the implementation so you
can find any pass, any builtin, or any backend in seconds. After
~25 rounds of sprint development, the NOVA compiler is ~27,873 lines
of NOVA across `src/compiler/` and the runtime is ~7,700 lines across
`src/runtime/`. The catalog at the end gives the round in which each
module / feature was introduced.

> Companion document: see
> [`/home/user/Crossengin-demo/ARCHITECTURE.md`](../Crossengin-demo/ARCHITECTURE.md)
> for the layout of CrossEngin, the first non-trivial NOVA consumer.
> Cross-references between this document and CrossEngin's are
> intentional and two-way.

---

## 1. The 30-second view

NOVA is a **self-hosting compiled language** targeting AGI through
"Moment-Signal Computing". The compiler is written in NOVA. It
compiles to native machine code (x86-64 Linux first, then macOS,
Windows-x64, ARM64-Linux, Windows-ARM64, and WebAssembly). There is
no libc, no GC, no interpreter — codegen emits direct syscalls. Self-
hosting is verified by the fixed-point invariant `stage2.s ==
stage3.s` (the binary compiles its own source, and the resulting
output compiles its own source again to byte-identical assembly).

```
                  ┌────────────────────────────────────────┐
                  │           myprogram.nova                │
                  └─────────────────┬──────────────────────┘
                                    │
                  ┌─────────────────┴──────────────────────┐
                  │             src/compiler/                │
                  │                                          │
                  │      lexer ─► parser ─► AST              │
                  │                  │                       │
                  │                  ▼                       │
                  │            type_check                    │
                  │             (R23A + R24A)                │
                  │                  │                       │
                  │                  ▼                       │
                  │                 IR                       │
                  │                  │                       │
                  │                  ▼                       │
                  │             regalloc                     │
                  │                  │                       │
                  │   ┌──────────────┼───────────────┐       │
                  │   ▼              ▼               ▼       │
                  │ codegen      lower_x64       lower_arm64 │
                  └───┬────────────┬───────────────┬─────────┘
                      │            │               │
       ┌──────────────┴──┐    ┌────┴─────┐    ┌────┴──────┐
       │ Linux x86-64    │    │ macOS    │    │ ARM64     │
       │ Win x86-64      │    │ x86-64   │    │ Linux     │
       │ WASM (WASI)     │    │          │    │ Win ARM64 │
       └────┬────────────┘    └────┬─────┘    └────┬──────┘
            │                      │               │
            └──────────────────────┴───────────────┘
                              │
                              ▼
                  ┌────────────────────────────────────────┐
                  │             src/runtime/                 │
                  │  alloc · string · math · simd · tensor   │
                  │  json · http · scheduler · io · syscall  │
                  │  ffi · gpu · llm · embedding · ...       │
                  └────────────────────────────────────────┘
                              │
                              ▼
                          native binary
```

The shape is a classic compiler-then-runtime, but two details are not
classic:

1. **Mind / soul / system declarations are first-class.** The
   compiler parses `mind`, `soul`, and `system` blocks directly. The
   runtime maintains the corresponding cognitive primitives (moments,
   signals, nodes, channels, beliefs, goals, imagination).
2. **The compiler is the runtime.** Both are NOVA. Anything you can
   write at runtime, the compiler also uses.

## 2. Top-level repository layout

```
NOVA/
├── ARCHITECTURE.md          ← this file
├── README.md                ← user-facing language tour
├── NEXT_SESSION.md          ← cumulative session notes
├── VERSION                  ← semver
├── STABILITY_AUDIT.md       ← language + ABI stability inventory
├── COMPAT.md                ← breaking-change log
├── INSTALL.md               ← packaging + install layout
├── NOVA_BUG_THRESHOLD.md    ← PTR_THRESHOLD known-bug docs
├── Makefile                 ← `make / make self-host / make test-all`
├── build_compiler.sh        ← bootstrap script
├── nova                     ← shell wrapper around bin/nova
├── boot/                    ← hand-written assembly bootstrap
│   └── nova_boot.s          ← stage0 (~assembly)
├── bin/                     ← compiled artifacts
│   ├── nova                 ← stage2 + stage3 compiler binaries
│   └── hello_*.{exe,wasm,o} ← per-target hello-world reference outputs
├── src/                     ← all NOVA implementation
├── docs/                    ← language reference + stdlib + use cases
├── examples/                ← 60+ entry-point NOVA programs
├── tools/                   ← IDE tooling (LSP, DAP, tree-sitter)
├── tests/                   ← test suite (178 tests, 172 pass, 6 skip)
├── scripts/                 ← release / packaging helpers
├── packaging/               ← .deb / .pkg / .msi / Homebrew formulas
├── dist/                    ← release artifacts (gitignored)
├── *_AUDIT.md               ← target/feature audits (WIN32, MACOS, WASM,
│                              MOBILE, SIMD, GPU, DWARF, STABILITY)
└── output.s                 ← scratch from last compile
```

## 3. The `src/` tree

```
src/
├── version.nova       Single line of truth for compiler version
├── compiler/          The compiler pipeline (~28k lines NOVA)
│   ├── lexer.nova       880 lines  — UTF-8 tokenizer
│   ├── parser.nova    3,349 lines  — recursive-descent parser
│   ├── ast.nova         633 lines  — AST node definitions
│   ├── ir.nova          486 lines  — intermediate representation
│   ├── codegen.nova  20,064 lines  — x86-64 codegen (the big one)
│   ├── lower_x64.nova   684 lines  — x86-64 instruction lowering
│   ├── lower_arm64.nova 772 lines  — ARM64 instruction lowering
│   ├── regalloc.nova    197 lines  — register allocator
│   └── compiler.nova    808 lines  — top-level driver
├── runtime/           Built-in runtime modules (~7,700 lines NOVA)
│   └── 40 modules (alloc, simd, tensor, json, http, ...)
├── core/              Cognitive primitives (moments, signals, ...)
│   └── 20 modules (moment, signal, node, soul, system, belief, ...)
├── mind/              Whole-mind orchestrations
│   └── 5 modules (academic, experiential, emotion, memory, reasoning)
├── agent/             Agent-level: cognitive LLM, RAG, preprocess
│   └── 4 modules
├── cognitive/         Predictive-coding, active-inference, SDR, HDC
│   └── 10 modules
├── tooling/           Profiler, time machine, visualizer
│   └── 4 modules
└── pkg/               Module/package management
    └── pkg.nova
```

## 4. The compiler pipeline

```
   .nova source
        │
        ▼
  ┌─────────┐    Tokens                ┌────────────┐
  │ lexer   │ ──────────────────────►  │  parser    │
  │ (880    │                          │  (3,349    │
  │  lines) │                          │   lines)   │
  └─────────┘                          └──────┬─────┘
                                              │ AST
                                              ▼
                                       ┌────────────┐
                                       │ type_check │
                                       │ R23A+R24A  │
                                       │ (in parser)│
                                       └──────┬─────┘
                                              │ typed AST
                                              ▼
                                       ┌────────────┐
                                       │   ir.nova  │
                                       │ optional   │
                                       │ IR form    │
                                       └──────┬─────┘
                                              │
                                              ▼
                                       ┌────────────┐
                                       │  regalloc  │
                                       │ (197 lines)│
                                       └──────┬─────┘
                                              │
                                              ▼
                  ┌───────────────────────────┴───────────────────────────┐
                  │                                                       │
                  ▼                                                       ▼
           ┌────────────┐                                          ┌────────────┐
           │ codegen.nova│                                          │lower_x64 / │
           │ AST-walker  │  ───────────────► .s asm ───────────►   │lower_arm64 │
           │ (20k lines) │                                          │            │
           └────────────┘                                          └────────────┘
                  │
                  ▼
            stage2.s == stage3.s ?  (self-host check)
```

### 4.1 `lexer.nova` (~880 lines)

UTF-8 source → token stream. Token types include:

- Literals: int (`0xff`, `0o755`, `0b101`, decimal), float (IEEE 754),
  string (with escapes + interpolation), char, bool.
- Keywords: `let`, `fn`, `struct`, `enum`, `impl`, `match`, `if`,
  `else`, `while`, `for`, `in`, `break`, `continue`, `return`, `do`,
  `unless`, `try`, `catch`, `finally`, `mind`, `soul`, `system`,
  `const`, `import`.
- Word-form operators (R24B): `not`, `and`, `or`, `is`, `in`,
  `not in`.
- Punctuation: ASCII + `..`, `..=`, `?`, `??`, `??=`, `|>`, `=>`,
  `->`, `~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`.
- Comments: `//`, `--`, `#` (R24B).

### 4.2 `parser.nova` (~3,349 lines)

Recursive-descent parser. Lands the same AST shape that `ast.nova`
defines. Major features by round:

- **R17A (commit `41f0332`)** — sum-type enums with payloads
  (`Variant(int, str)`), `Type::Variant(args)` constructor syntax,
  `match Option::Some(v) => v` destructure with `_` wildcards.
- **R20A (commit `c49ebf8`)** — postfix `?` Result-propagation
  operator. Disambiguated from the classic ternary via dynamic
  precedence.
- **R21A (commit `c7b6b33`)** — generic enums. `enum Result<T, E> {
  Ok(T) Err(E) }` parses; nested generic type annotations
  (`Result<Result<int, str>, str>`) work.
- **R22B (commit `3fd1a6a`)** — generic function signatures.
  `fn name<T, U>(p: T) -> U`. Plus the `T -> U` function-type
  spelling inside parameter annotations.
- **R23A (commit `ef159ec`)** — generic structs (`struct Box<T> {
  value: T }`) and semicolon-separated field lists. Includes a
  lightweight type-check pass.
- **R24A (commit `fda288c`)** — fn-call + struct-ctor type-check
  follow-up to R23A.

The parser is also where the **mind / soul / system** declaration
syntax is recognized — these are first-class language constructs, not
macros.

### 4.3 `ast.nova` (~633 lines)

The AST node definitions. Every node has a kind tag, a span (start +
end byte offset), and per-kind payload fields. Parent kinds include:
program, function, struct, enum, impl, mind, soul, system, let,
const, expression statements, control flow (if / while / for / do /
match), patterns (literal / variable / variant / list / map / rest),
and the full expression algebra.

### 4.4 `ir.nova` (~486 lines)

A light intermediate representation. Codegen is primarily an AST
walker, but IR is used as a staging form for specific passes (constant
folding, dead-code elimination — see commit `022586d`).

### 4.5 `regalloc.nova` (~197 lines)

The register allocator. Linear-scan-flavoured; tracks live ranges
across basic blocks, spills to stack frame slots, supports cross-call
register saves per target ABI.

### 4.6 `codegen.nova` (~20,064 lines)

The big one. AST → x86-64 assembly. Includes:

- Per-AST-kind emit functions (every node type has a `cg_emit_*`
  handler).
- The 152-builtin dispatch table (see `STABILITY_AUDIT.md` for the
  full list; the 152nd is `secure_random` from commit `906fd14`).
- Inline SIMD intrinsics (commit `11ae0d0`).
- AST-level constant folding + dead-code elimination (`022586d`).
- Hash-accelerated O(1) function lookup (`8609cc2`).
- Match-as-expression block-body locals + frame allocation
  (`047ace1`).
- The `PTR_THRESHOLD` heuristic that decides whether a 64-bit value is
  an integer or a pointer (see `NOVA_BUG_THRESHOLD.md`).
- DWARF debug-info emission (`6bd32e6` for `.debug_info` DIE entries
  on locals + params, `3d5393a` for `.debug_line` MVP) — see
  [`DWARF_AUDIT.md`](./DWARF_AUDIT.md).

### 4.7 `lower_x64.nova` (~684 lines) and `lower_arm64.nova` (~772 lines)

Per-target instruction lowering. These are not full backends — they
share IR/regalloc with `codegen.nova` but emit per-target instruction
encodings:

- `lower_x64.nova` — SIB byte addressing, REX prefix, AVX2 encoding
  for SIMD intrinsics, x86-64 calling convention (SysV + Win64).
- `lower_arm64.nova` (commit `a108b79`) — AArch64 NEON SIMD encoding,
  PCS (Procedure Call Standard) calling convention, syscall translation
  for Linux ARM64 + iOS-style.

### 4.8 `compiler.nova` (~808 lines)

The top-level driver. Reads CLI args, opens the source file, drives
lex → parse → check → codegen, writes the `.s` file. Also drives the
`--check` mode (parse + type-check, no codegen) and the multi-file
import path canonicalization (commit `d0e2f44`).

## 5. Cross-target backends

NOVA targets **six** native targets:

```
                          ┌─────────────────────────┐
                          │     codegen.nova         │
                          │   + per-target lowering  │
                          └──┬────┬────┬────┬───┬───┘
                             │    │    │    │   │
            ┌────────────────┘    │    │    │   │
            ▼                     │    │    │   │
  ┌──────────────────┐            │    │    │   │
  │ Linux x86-64     │            │    │    │   │
  │   (default)      │            │    │    │   │
  │ syscalls direct  │            │    │    │   │
  │ ELF64 output     │            │    │    │   │
  └──────────────────┘            │    │    │   │
                                  ▼    │    │   │
                       ┌──────────────────┐ │   │
                       │ Windows x86-64   │ │   │
                       │ (cross-compile)  │ │   │
                       │ Win64 ABI        │ │   │
                       │ PE32+ output     │ │   │
                       │ commit 530ef76   │ │   │
                       └──────────────────┘ │   │
                                            ▼   │
                                ┌─────────────────────┐
                                │ macOS x86-64        │
                                │ Mach-O output       │
                                │ Mach-O syscall ABI  │
                                │ commit 72e929d      │
                                └─────────────────────┘
                                                ▼   │
                                    ┌─────────────────────┐
                                    │ WebAssembly (WASI)  │
                                    │ v128 SIMD (R15B)    │
                                    │ commit e8d27a5      │
                                    │ + commit 7d9b3e6    │
                                    │ (WASI preopens)     │
                                    └─────────────────────┘
                                                    ▼
                                        ┌─────────────────────┐
                                        │ ARM64-Linux         │
                                        │ commit a108b79      │
                                        │ + iOS / Android     │
                                        │ smoke targets       │
                                        │ commit a84f0bf      │
                                        ├─────────────────────┤
                                        │ Windows ARM64       │
                                        │ (PE32+ AArch64)     │
                                        │ commit af94bd2      │
                                        └─────────────────────┘
```

### 5.1 The six targets and their audits

| Target | First commit | Audit | Reference output |
|---|---|---|---|
| Linux x86-64 | original bootstrap | (default; no audit) | `bin/nova` |
| Windows x86-64 | `530ef76` + `0a6c2ef` (audit) + `0a97f24` (argv/envp/sockets/fork) | [`WIN32_AUDIT.md`](./WIN32_AUDIT.md) | `bin/hello_win32.exe`, `bin/nova.exe` |
| macOS x86-64 | `72e929d` (audit + hello-world) | [`MACOS_AUDIT.md`](./MACOS_AUDIT.md) | `bin/hello_macos`, `bin/nova_macos.s` |
| ARM64-Linux | `a108b79` (Nova v4.2: ARM64 codegen) + `a84f0bf` (mobile audit) | [`MOBILE_AUDIT.md`](./MOBILE_AUDIT.md) | `bin/hello_android.o` |
| Windows ARM64 | `af94bd2` (close last target gap) | [`MOBILE_AUDIT.md`](./MOBILE_AUDIT.md) | `bin/hello_winarm64.exe`, `bin/secure_random_winarm64.exe` |
| WebAssembly (WASI) | `e8d27a5` (initial), `7d9b3e6` (WASI preopens), `7e50f26` (R15B v128 SIMD) | [`WASM_AUDIT.md`](./WASM_AUDIT.md) | `bin/hello.wasm`, `bin/file_wasm.wasm`, `bin/wasi_file_roundtrip.wasm` |

### 5.2 Sample lowering — a single integer add

| Target | Emission |
|---|---|
| Linux x86-64 (SysV) | `mov rax, [rbp-8]` → `add rax, [rbp-16]` → `mov [rbp-24], rax` |
| Windows x86-64 (Win64) | Same encoding; only differs at the call boundary (RCX/RDX/R8/R9 first 4 args; shadow space) |
| macOS x86-64 | Same SysV-style ABI; underscore-prefixed symbol names |
| ARM64 (NEON) | `ldr x0, [x29, #-8]` → `ldr x1, [x29, #-16]` → `add x0, x0, x1` → `str x0, [x29, #-24]` |
| Windows ARM64 | Same AArch64 encoding; PE32+ wrapping + Win64-style call frame |
| WASM | `local.get $a` → `local.get $b` → `i32.add` → `local.set $sum` |

### 5.3 GPU compute (`src/runtime/gpu.nova`)

The GPU backend is the seventh target lineage, but it is **runtime-
dispatched** rather than a separate codegen path. The
`src/runtime/gpu.nova` runtime ships `wgpu_dispatch` invocation
machinery and a WGSL shader for `gpu_vector_add`. The audit document
is [`GPU_AUDIT.md`](./GPU_AUDIT.md); the working example is
`examples/gpu_vector_add.nova`.

## 6. Language features by round

The big-ticket language additions, in chronological order:

### 6.1 Foundational era (pre-R-tags)

| Feature | Commit | Notes |
|---|---|---|
| Soul declarations | `2042d44` | First-class identity/feelings/initiative. |
| Mind declarations | `1956280` | Declarative cognitive architecture. |
| System declarations | `2b68f34` | Multi-mind composition. |
| Flow operators (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`) | `5a3918e` | Signal routing as concise as arithmetic. |
| Streaming signals + node pools + dynamic scaling | `5ced087` | Phase 2. |
| Knowledge persistence + embeddings + KG | `96265ed` | Phase 3. |
| Security primitives (SHA-256, validate, secure_mem, rate limit) | `d97150e` | Phase 4. |
| Multi-mind system composition | `2b68f34` | Phase 5. |
| Windows x86-64 cross-compile | `530ef76` | Phase 6. |
| IEEE 754 floats + FFI + GPU + SIMD | `52f339e` | Phases 7-9. |

### 6.2 NOVA v3.0 - v4.2 era

| Feature | Commit | Notes |
|---|---|---|
| Range step + inclusive range | `978a22d` | `0..10..2`, `1..=5`. |
| `do` block-scoped value expressions | `342118d` | `do { ... } yields value`. |
| Map merge / sum / product / min_list / max_list / flatten / unique builtins | `c0de38c`, `75aaee5` | List-algebra builtins. |
| Match as expression, if as expression | `5346cfa` | Value-producing control flow. |
| Rest patterns + map comprehension + enumerate + unless-expr | `9ee83ba` | Destructure improvements. |
| Try-as-expression + pad_left + pad_right + str_count | `e74c663` | Error-handling improvements. |
| Cross-function exception via catch stack | `b8ab0a1` | Try/catch across call frames. |
| Type-based match patterns (`is int`, `is str`) | `1c9bf76` | Tagged-union friendly. |
| M9 full-system self-host verification | `a19f840` | First fixed-point checkpoint. |
| NOVA v3.0 — FFI + productivity libs + cognitive | `d52470d` | First v3 baseline. |
| NOVA v4.0 — SSE2 SIMD + tiled matmul + OpenBLAS + cognitive LLM | `438e798` | First v4 baseline. |
| Concept layer (hierarchy, schemas, multi-vector) | `46f03a1` | v4.1 lead-in. |
| NOVA v4.1 — Bayesian belief + goal engine + safety + imagination | `e521870` | Cognitive overhaul. |
| NOVA v4.2 — Float utils + syscall FFI + ARM64 codegen + persistent alloc + O(1) function lookup | `a108b79` | The biggest single bump. |

### 6.3 Round-tagged era (R1 ... R24)

| Round | Feature | Commit |
|---|---|---|
| R11D | SIMD i32x8 intrinsics (AVX2 / NEON / scalar) | `844d54b` |
| R13A | Inline SIMD + `int_*` builtins at call site | `11ae0d0` |
| R14B | `simd_sad_u8` raw-byte SAD via AVX2 vpsadbw | `698f8e9` |
| R15B | WASM v128 SIMD lowering — close R11D's last gap | `7e50f26` |
| R17A | Sum types with payloads + match exhaustiveness | `41f0332` |
| R18A | Byte mul-acc SIMD primitives (close R17C LK ceiling) | `db34532` |
| R19B | Cross-target enum codegen — R17A.2 follow-up | `618635d` |
| R20A | Result + postfix `?` propagation operator | `c49ebf8` |
| R21A | Generic enum payloads — `Result<T, E>` truly parametric | `c7b6b33` |
| R22B | Generic function signatures — extend R21A from enums to fns | `3fd1a6a` |
| R23A | Generic structs + lightweight type-check pass | `ef159ec` |
| R24A | Fn-call + struct-ctor type-check (R23A.2 follow-up) | `fda288c` |
| also | AST-level constant folding + DCE | `022586d` |
| also | Match-as-expression block-body locals + frame allocation | `047ace1` |
| also | Hash-accelerated O(1) function lookup in codegen | `8609cc2` |
| also | Compiler import-path canonicalization (dedup re-imports) | `d0e2f44` |
| also | Two-stage `bin/nova` build (fix destructure / rest_pattern segfault) | `16f029f` |
| also | int_shl/shr/and/or/xor builtins, doc PTR_THRESHOLD | `ac692f7` |

## 7. SIMD primitives — cross-target lowering matrix

NOVA has three generations of SIMD primitives, each landing in a
specific round:

| Primitive | Round | x86-64 lowering | ARM64 lowering | WASM lowering | Where used in CrossEngin |
|---|---|---|---|---|---|
| `simd_dot_i32x8` (and friends) | R11D (`844d54b`) | AVX2 `vpmaddwd` + `vpaddd` | NEON `smlal` + `addv` | `i32x4.add` × 2 (R15B `7e50f26`) | `image_optical_flow.nova` accumulators (R12A) |
| `simd_sad_u8` (8/16/32/64-byte) | R14B (`698f8e9`) | AVX2 `vpsadbw` + `vpaddq` | NEON `uabd` + `uaddlv` | Scalar fallback initially; v128 lowering in R15B | `image_stereo.nova` (R15A, 5.5× absolute) + `image_optical_flow.nova` (R17C, 5.09× vs i32) |
| `simd_byte_mac` byte mul-acc | R18A (`db34532`) | AVX2 `vpmaddubsw` + `vpmaddwd` + `vpaddd` | NEON `smlal2` + `addv` | Scalar fallback | `image_optical_flow.nova` LK ceiling close (R18A.2, 3.69× absolute) + `image_hog.nova` integral histogram (R22A) |

The R15B WASM lowering (`7e50f26`) is the round that closed the last
target gap: every SIMD primitive that R11D / R14B / R18A introduced
on x86-64 now has a v128 lowering on WASM.

Audit: [`SIMD_AUDIT.md`](./SIMD_AUDIT.md).

## 8. Runtime (`src/runtime/`)

40 NOVA modules; ~7,700 lines total. Roughly grouped:

### 8.1 Memory + syscalls + scheduler

| Module | Role |
|---|---|
| `alloc.nova` | Arena allocator + bump pointer. |
| `mem.nova` | Low-level memory ops (memcpy, memset, ...). |
| `persistent_alloc.nova` | File-backed allocator (`mmap MAP_SHARED`, checksummed header). Survives arena resets. |
| `secure_mem.nova` | Locked/wiped memory regions. |
| `syscall.nova` | Direct Linux/macOS/Win32 syscall layer (no libc). |
| `node_pool.nova` | Cognitive-node pool allocator. |
| `coroutine.nova` | Cooperative coroutine stacks + scheduler. |
| `scheduler.nova` | Task/coroutine scheduler. |
| `taskpool.nova` | Worker-pool runtime for parallel work. |
| `chan.nova` | Buffered/unbuffered channels (CSP-style). |
| `effects.nova` | Effect-tracking primitives. |
| `confidence.nova` | Confidence annotation type. |

### 8.2 Strings + IO + paths

| Module | Role |
|---|---|
| `string.nova` | NUL-terminated str type + utf-8 helpers. |
| `io.nova` | File + stdio + stream IO. |
| `path.nova` | Path joining + canonicalization. |

### 8.3 Math + numeric + SIMD + tensor

| Module | Role |
|---|---|
| `math.nova` | Trig + log + sqrt + common math. |
| `float.nova` | IEEE 754 double-precision utils (R-NOVA v4.2). |
| `simd.nova` | SIMD primitives wrapper. |
| `tensor.nova` | Tensor math + tiled matmul. |
| `blas.nova` | BLAS-equivalent operations + OpenBLAS FFI dispatch. |

### 8.4 Data structures

| Module | Role |
|---|---|
| `list.nova` | Growable list + iteration. |
| `map.nova` | Hash map (keys + values). |
| `set.nova` | Hash set. |
| `timeseries.nova` | Time-stamped series helpers. |

### 8.5 Cognitive

| Module | Role |
|---|---|
| `embedding.nova` | Unified embedding interface (BM25 + cognitive dims). |
| `embed.nova` | Embedding storage helpers. |
| `llm.nova` | The LLM-bridge integration point (calls `llm_bridge.c`). |
| `llm_bridge.c` | The thin C bridge for llama.cpp. |

### 8.6 IO + parsing + network

| Module | Role |
|---|---|
| `json.nova` | JSON parser + serializer. |
| `csv.nova` | CSV parser + serializer. |
| `http.nova` | HTTP client. |
| `db.nova` | DB connection seam. |
| `validate.nova` | Input validation helpers. |
| `audio.nova` | Audio buffer + format helpers. |

### 8.7 FFI + foreign integration

| Module | Role |
|---|---|
| `ffi.nova` | The generic FFI layer (.so / .dylib). |
| `ffi_syscall.nova` | Syscall-based FFI without libc (ELF parser + SYSV hash lookup). |
| `python.nova` | Bidirectional Python bridge. |
| `crypto.nova` | The crypto-runtime entry points. |
| `gpu.nova` | The GPU compute layer (wgpu/WGSL dispatch). |
| `federation.nova` | Federation primitives at runtime. |

## 9. Cognitive layer (`src/core/`, `src/mind/`, `src/cognitive/`)

This is the layer that distinguishes NOVA from a generic systems
language. It composes from three levels.

### 9.1 Core primitives (`src/core/`)

| Module | Role |
|---|---|
| `moment.nova` | A structured moment — what happened, who, what was felt, consequences. |
| `signal.nova` | A typed signal carrying a moment between nodes. |
| `signal_flow.nova` | The flow-operator runtime. |
| `node.nova` | A cognitive processing node (perceiver / knower / rememberer / reasoner / feeler / actor). |
| `channel.nova` | The channel between nodes. |
| `path.nova` | Cognitive-path encoding. |
| `parts.nova` | Sub-node "parts" (the substrate building block). |
| `gates.nova` | Gate types for the substrate router. |
| `reader.nova` | The reader-pipeline primitives. |
| `concept.nova` | Concept hierarchy + property inheritance + multi-vector embedding (v4.1, commit `46f03a1`). |
| `knowledge.nova` | KG primitives. |
| `similarity.nova` | Similarity scoring (Jaccard structural + cosine semantic). |
| `belief.nova` | Bayesian belief — Beta-distribution evidence accumulation (v4.1, `e521870`). |
| `goal.nova` | Goal engine — priority-sorted goals + four drive generators (v4.1, `e521870`). |
| `safety.nova` | Safety/audit — three permission tiers + reversibility classification + circular-buffer decision log (v4.1, `e521870`). |
| `imagination.nova` | World model + forward simulation + counterfactual + dream recombination + scenario planning (v4.1, `e521870`). |
| `soul.nova` | The soul declaration runtime (purpose, values, drives, feelings) (`2042d44`). |
| `system.nova` | The system declaration runtime — composes multiple minds + bridges + a soul (`2b68f34`). |
| `synapse.nova` | Synapse-graph primitives. |

### 9.2 Whole-mind systems (`src/mind/`)

| Module | Role |
|---|---|
| `academic.nova` | The "academic" mind — knows things. |
| `experiential.nova` | The "experiential" mind — remembers things. |
| `emotion.nova` | The "emotion" mind — feels things. |
| `memory.nova` | The "memory" mind — recalls things. |
| `reasoning.nova` | The "reasoning" mind — deduces things. |

### 9.3 Cognitive computational primitives (`src/cognitive/`)

| Module | Role |
|---|---|
| `active_inference.nova` | Active-inference / free-energy minimization. |
| `associative.nova` | Associative-recall primitives. |
| `atom_lifecycle.nova` | Atom birth/death tracking. |
| `causal_library.nova` | Causal-relation library. |
| `hdc.nova` | Hyperdimensional computing primitives. |
| `predictive_coding.nova` | Predictive-coding error generation. |
| `resonance.nova` | Co-activation resonance tracking. |
| `sdr.nova` | Sparse Distributed Representations. |
| `self_model.nova` | Self-modelling primitives. |
| `skill.nova` | Skill / competence primitives. |

### 9.4 Agent layer (`src/agent/`)

| Module | Role |
|---|---|
| `agent.nova` | The agent shell. |
| `cognitive_llm.nova` | LLM output routed through confidence + episodic memory + symbolic reasoning. |
| `preprocess.nova` | Input preprocessing. |
| `rag.nova` | Retrieval-augmented generation (BM25 + n-grams + cognitive dimensions). |

### 9.5 Tooling layer (`src/tooling/`)

| Module | Role |
|---|---|
| `profiler.nova` | The NOVA profiler. |
| `time_machine.nova` | Time-machine debugger. |
| `visualizer.nova` | KG / signal visualizer. |
| `kg_visualizer.nova` | KG-specific visualizer. |

## 10. IDE tooling (`tools/`)

### 10.1 LSP server — `tools/nova-lsp/` (17+ capabilities)

NOVA ships an LSP server written in Python (it's the IDE half of the
tooling; the compiler itself stays pure NOVA). Capabilities shipped to
date, in roughly chronological order:

| # | Capability | Round | Commit |
|---|---|---|---|
| 1 | `textDocument/completion` | initial | `154a001` |
| 2 | `textDocument/rename` | initial | `154a001` |
| 3 | `textDocument/references` | initial | `154a001` |
| 4 | `textDocument/codeAction` (extract fn, organize imports, sort fns) | code-action round | `9de1a78` |
| 5 | `textDocument/definition` (follows imports across files) | def round | `deac648` |
| 6 | `workspace/symbol` (fuzzy search across all .nova files) | wkspc round | `c18a014` |
| 7 | `textDocument/rename` (workspace rename across imports) | wkspc-rename round | `e52314f` |
| 8 | `textDocument/semanticTokens` (per-token classification beyond TextMate) | semantic round | `85fe64a` |
| 9 | `textDocument/hover` (surfaces `///` doc-comments as markdown) | hover round | `a852a39` |
| 10 | DAP MVP (parallel stream of debug capabilities — see 10.2) | — | `233758a` |
| 11 | `textDocument/callHierarchy` (12th LSP capability) | call-hierarchy round | `5a57beb` |
| 12 | `textDocument/inlayHint` (parameter names at call sites; 13th capability) | inlay round | `c530629` |
| 13 | `textDocument/codeLens` (N-references annotations; 14th capability) | code-lens round | `3a6e217` |
| 14 | `textDocument/typeHierarchy` (15th capability) | type-hier round | `a32e95c` |
| 15 | `textDocument/codeAction` — quickfix auto-add missing match arms (R20D) | R20D | `04f1378` |
| 16 | extract-function refactor (R21F) | R21F | `ce4f878` |
| 17 | `textDocument/foldingRange` + `textDocument/documentSymbol` (R22C, 16th + 17th capabilities) | R22C | `47769af` |
| 18 | `workspace/diagnostic` aggregation (R23F) | R23F | `bb61c6f` |
| 19 | type-aware completion — variants, fields, type names (R24E) | R24E | `940c4f6` |
| 20 | inline-variable refactor (R25F) | R25F | `e2a87a9` |

Implementation files (under `tools/nova-lsp/nova_lsp/`):
`server.py`, `call_hierarchy.py`, `code_lens.py`,
`document_symbols.py`, `exhaustiveness_fix.py`,
`extract_function.py`, `folding_ranges.py`, `hover_docs.py`,
`imports.py`, `inlay_hints.py`, `inline_variable.py`,
`rename_workspace.py`, `semantic_tokens.py`, `type_completion.py`,
`type_hierarchy.py`, `workspace_diagnostics.py`,
`workspace_symbols.py`.

### 10.2 DAP server — `tools/nova-dap/` (21 capabilities)

The debug-adapter-protocol server is the debugger half. It bridges
`gdb` (and where applicable lldb) MI3 to the DAP wire format.

| # | Capability | Commit |
|---|---|---|
| 1-15 | MVP DAP server (gdb MI3 bridge): launch, attach, source breakpoints, step (in/over/out), continue, pause, threads, stack trace, scopes, variables, stopped events, terminated events, output events, configuration done | `233758a` |
| 16 | Multi-thread coordination (threads, per-thread step/pause/continue) | `17f176e` |
| 17 | (DAP / LSP cross-feature work) | — |
| 18 | Evaluate request + conditional breakpoints (18th DAP capability) | `2b58d5a` |
| 19 | Data breakpoints / watchpoints (19th DAP capability) | `8cb0610` |
| 20 | Function breakpoints by name (20th DAP capability) | `3691954` |
| 21 | Instruction-level stepping (21st DAP capability) | `38619aa` |

Implementation files (under `tools/nova-dap/nova_dap/`):
`server.py`, `gdb_bridge.py`, `disassembly.py`, `evaluator.py`,
`function_breakpoints.py`, `watchpoints.py`.

### 10.3 Tree-sitter grammar — `tools/tree-sitter-nova/`

Tree-sitter grammar for editor highlighting and structural editing.

| Round | Addition | Commit |
|---|---|---|
| initial | NOVA grammar + highlights + corpus tests | `6f503ec` |
| folds round | folds + locals queries, return-type syntax, 14 corpus tests | `09d7299` |
| R24B | R17A-R23A syntax refresh: enums, `?`, generic enums, generic fns, generic structs, plus long-standing NOVA syntax R9E missed; 89 / 89 corpus tests, 240 / 244 (98.4%) `tests/*.nova` + `examples/*.nova` clean | `72db976` |

### 10.4 VSCode extension — `tools/vscode-nova/`

VSCode extension that wires up the LSP server + tree-sitter
highlighting + DAP debugging into a single install.

## 11. Self-hosting (`boot/` + `bin/`)

```
   ┌──────────────────┐
   │ boot/nova_boot.s │     stage0 — hand-written assembly
   │ (~assembly)      │
   └────────┬─────────┘
            │ as + ld
            ▼
   ┌──────────────────┐
   │  boot/nova_boot  │     stage0.5 — minimal NOVA compiler executable
   │  (executable)    │     can compile src/compiler/*.nova
   └────────┬─────────┘
            │ ./nova_boot src/compiler/compiler.nova -o stage1.s
            ▼
   ┌──────────────────┐
   │      stage1.s    │     stage1 — assembly emitted by stage0.5
   └────────┬─────────┘
            │ as + ld
            ▼
   ┌──────────────────┐
   │     stage1       │     stage1 binary — same source, real compile
   └────────┬─────────┘
            │ stage1 src/compiler/compiler.nova -o stage2.s
            ▼
   ┌──────────────────┐
   │      stage2.s    │     stage2 — emitted by stage1
   └────────┬─────────┘
            │ as + ld
            ▼
   ┌──────────────────┐
   │     stage2       │     stage2 binary — should match stage1 behaviour
   └────────┬─────────┘
            │ stage2 src/compiler/compiler.nova -o stage3.s
            ▼
   ┌──────────────────┐
   │      stage3.s    │     stage3 — emitted by stage2
   └────────┬─────────┘
            │
            ▼
   stage2.s == stage3.s ?  (fixed-point check)
```

The fixed-point invariant `stage2.s == stage3.s` is verified by
`make self-host`. M9 (commit `a19f840`) was the first M-numbered
fixed-point checkpoint. The current line is verified through
`STABILITY_AUDIT.md` and every release rebuilds the chain.

## 12. Tests + benchmarks

```
tests/
├── 178 test files total (172 pass, 6 skip)
├── test_*.nova                  — per-feature smoke tests
├── test_simd_dot_i32_bench.nova — R11D microbench
├── test_simd_sad_bench.nova     — R14B SAD microbench
├── test_wasm_simd_*.nova        — R15B WASM SIMD correctness + perf
├── test_generic_enum.nova       — R21A
├── test_generic_fn.nova         — R22B
├── test_generic_struct.nova     — R23A
├── test_result.nova             — R20A
├── test_sum_types.nova          — R17A
└── ...

examples/
├── 60+ example programs
├── hello.nova                   — minimal example
├── full_mind.nova               — full cognitive architecture demo
├── kg_visualizer_demo.nova      — KG visualization
├── tcp_echo_server.nova         — networking
├── concurrency.nova             — coroutines
├── gpu_vector_add.nova          — GPU compute
└── ...
```

Per-target reference outputs live in `bin/`:
`bin/hello_macos`, `bin/hello_win32.exe`, `bin/hello_winarm64.exe`,
`bin/hello_android.o`, `bin/hello.wasm`, `bin/file_wasm.wasm`,
`bin/secure_random.exe`, `bin/secure_random_macos`,
`bin/secure_random_winarm64.exe`, `bin/wasi_file_roundtrip.wasm`,
`bin/hello_secure_random.wasm`, `bin/hello_dwarf` (DWARF debug-info
smoke target).

## 13. Cross-reference: CrossEngin consumes which NOVA features?

NOVA's primary external consumer is CrossEngin. Many NOVA features
landed specifically to unblock a CrossEngin module:

| NOVA round | NOVA feature | CrossEngin module it unblocks |
|---|---|---|
| R11D (`844d54b`) | SIMD i32x8 intrinsics | `image_optical_flow.nova` LK accumulators (CE R12A) |
| R14B (`698f8e9`) | `simd_sad_u8` raw-byte SAD | `image_stereo.nova` (CE R15A, 5.5× absolute) |
| R15B (`7e50f26`) | WASM v128 SIMD lowering | (forward-looking — closes the last target for CE SIMD modules) |
| R17A (`41f0332`) | Sum types + match exhaustiveness | `kg/query.nova` (CE R15D etc.) — Result/Optional shapes |
| R18A (`db34532`) | Byte mul-acc SIMD | `image_optical_flow.nova` LK ceiling close (CE R18A.2, 3.69×) + `image_hog.nova` integral histogram (CE R22A) |
| R20A (`c49ebf8`) | Result + postfix `?` operator | `kg/rule_inference.nova` (CE R20B), all federation modules |
| R21A (`c7b6b33`) | Generic enum payloads | `federation/gossip.nova` (CE R18E+) — `Result<T, E>` wire types |
| R22B (`3fd1a6a`) | Generic function signatures | every CE module that ships a `..._cmd` dispatcher |
| R23A (`ef159ec`) | Generic structs + type-check pass | every CE module that ships typed records |
| R24A (`fda288c`) | Fn-call + struct-ctor type-check | catches bugs in new CE modules before runtime |
| also: ARM64 (`a108b79`) | ARM64 codegen | unblocks CE on Apple Silicon dev hardware + Linux ARM64 servers |
| also: persistent_alloc (`a108b79`) | File-backed allocator | unblocks `snapshot_disk.nova` for memory-mapped snapshots |
| also: secure_random (`906fd14`) | 152nd builtin | CE crypto modules (random nonces, ephemeral DH keys, etc.) |

> See [`/home/user/Crossengin-demo/ARCHITECTURE.md`](../Crossengin-demo/ARCHITECTURE.md)
> §20 for the CrossEngin-side cross-reference table.

## 14. Per-feature audit documents

This document is the **index**. The deep-dives are:

| Audit doc | Scope |
|---|---|
| [`STABILITY_AUDIT.md`](./STABILITY_AUDIT.md) | Language syntax, the 152-function builtin set, the six codegen targets, the 167-symbol `_nova_*` runtime ABI, stdlib, PTR_THRESHOLD bug class, and a ranked roadmap to 1.0. |
| [`COMPAT.md`](./COMPAT.md) | Breaking-change log. Only one breaking change in the past 100 commits (`str_new` format reconciliation in `56322bb`). |
| [`NOVA_BUG_THRESHOLD.md`](./NOVA_BUG_THRESHOLD.md) | PTR_THRESHOLD details + `int_*` escape hatch. |
| [`WIN32_AUDIT.md`](./WIN32_AUDIT.md) | Windows x86-64 target — argv/envp, sockets, fork/exec, alignment. |
| [`MACOS_AUDIT.md`](./MACOS_AUDIT.md) | macOS x86-64 target — Mach-O wrapping + syscall ABI. |
| [`WASM_AUDIT.md`](./WASM_AUDIT.md) | WASM target — WASI file IO, v128 SIMD, secure_random. |
| [`MOBILE_AUDIT.md`](./MOBILE_AUDIT.md) | ARM64-Linux + Windows-ARM64 + iOS / Android references. |
| [`SIMD_AUDIT.md`](./SIMD_AUDIT.md) | SIMD primitive lineage (R11D, R14B, R18A) + cross-target lowering. |
| [`GPU_AUDIT.md`](./GPU_AUDIT.md) | GPU compute backend (wgpu / WGSL). |
| [`DWARF_AUDIT.md`](./DWARF_AUDIT.md) | DWARF debug-info emission (`.debug_info` + `.debug_line`). |
| [`INSTALL.md`](./INSTALL.md) | Packaging — `.deb` / `.pkg` / `.msi` / Homebrew. |

---

## 15. Full module catalog

> Path is relative to repository root.
> "Round" / "Phase" is the first round (or v-tag) the module appeared.
> Commit SHA is the short hash of the introducing commit
> (verifiable via `git log --diff-filter=A -- <path>`).

### 15.1 Compiler

| File | Round | SHA |
|---|---|---|
| `src/compiler/lexer.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/parser.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/ast.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/ir.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/codegen.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/lower_x64.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/lower_arm64.nova` | NOVA v4.2 | `a108b79` |
| `src/compiler/regalloc.nova` | original bootstrap | `91cf5b9` |
| `src/compiler/compiler.nova` | original bootstrap | `91cf5b9` |

### 15.2 Runtime

| File | Round | SHA |
|---|---|---|
| `src/runtime/alloc.nova` | foundational | original |
| `src/runtime/mem.nova` | foundational | original |
| `src/runtime/syscall.nova` | foundational | original |
| `src/runtime/string.nova` | foundational | original (refactored `56322bb`) |
| `src/runtime/io.nova` | foundational | original |
| `src/runtime/path.nova` | foundational | original |
| `src/runtime/math.nova` | foundational | original |
| `src/runtime/list.nova` | foundational | original |
| `src/runtime/map.nova` | `91cf5b9` map iteration | `91cf5b9` |
| `src/runtime/set.nova` | foundational | original |
| `src/runtime/scheduler.nova` | foundational | original |
| `src/runtime/coroutine.nova` | foundational | original |
| `src/runtime/chan.nova` | foundational | original |
| `src/runtime/taskpool.nova` | foundational | original |
| `src/runtime/secure_mem.nova` | Phase 4 | `d97150e` |
| `src/runtime/crypto.nova` | foundational | original |
| `src/runtime/json.nova` | `91cf5b9` JSON | `91cf5b9` |
| `src/runtime/csv.nova` | NOVA v3.0 productivity | `d52470d` |
| `src/runtime/http.nova` | foundational | original |
| `src/runtime/db.nova` | NOVA v3.0 productivity | `d52470d` |
| `src/runtime/validate.nova` | Phase 4 | `d97150e` |
| `src/runtime/audio.nova` | foundational | original |
| `src/runtime/simd.nova` | Phases 7-9 | `52f339e` |
| `src/runtime/tensor.nova` | NOVA v3.0 | `d52470d` |
| `src/runtime/blas.nova` | NOVA v4.0 | `438e798` |
| `src/runtime/embedding.nova` | NOVA v4.0 | `438e798` |
| `src/runtime/embed.nova` | NOVA v4.0 | `438e798` |
| `src/runtime/llm.nova` | NOVA v3.0 | `d52470d` |
| `src/runtime/llm_bridge.c` | NOVA v4.0 | `438e798` |
| `src/runtime/ffi.nova` | Phases 7-9 | `52f339e` |
| `src/runtime/ffi_syscall.nova` | NOVA v4.2 | `a108b79` |
| `src/runtime/gpu.nova` | Phases 7-9 | `52f339e` |
| `src/runtime/python.nova` | Phases 7-9 | `52f339e` |
| `src/runtime/float.nova` | NOVA v4.2 | `a108b79` |
| `src/runtime/persistent_alloc.nova` | NOVA v4.2 | `a108b79` |
| `src/runtime/node_pool.nova` | Phase 2 (CE-relevant) | `5ced087` |
| `src/runtime/timeseries.nova` | foundational | original |
| `src/runtime/effects.nova` | foundational | original |
| `src/runtime/confidence.nova` | foundational | original |
| `src/runtime/federation.nova` | foundational | original |

### 15.3 Core cognitive primitives

| File | Round | SHA |
|---|---|---|
| `src/core/moment.nova` | foundational | original |
| `src/core/signal.nova` | foundational | original |
| `src/core/signal_flow.nova` | foundational | original |
| `src/core/node.nova` | foundational | original |
| `src/core/channel.nova` | foundational | original |
| `src/core/path.nova` | foundational | original |
| `src/core/parts.nova` | foundational | original |
| `src/core/gates.nova` | foundational | original |
| `src/core/reader.nova` | foundational | original |
| `src/core/concept.nova` | v4.1 lead-in | `46f03a1` |
| `src/core/knowledge.nova` | Phase 3 | `96265ed` |
| `src/core/similarity.nova` | foundational | original |
| `src/core/belief.nova` | NOVA v4.1 | `e521870` |
| `src/core/goal.nova` | NOVA v4.1 | `e521870` |
| `src/core/safety.nova` | NOVA v4.1 | `e521870` |
| `src/core/imagination.nova` | NOVA v4.1 | `e521870` |
| `src/core/soul.nova` | `2042d44` | `2042d44` |
| `src/core/system.nova` | `2b68f34` | `2b68f34` |
| `src/core/synapse.nova` | foundational | original |

### 15.4 Mind systems

| File | Round | SHA |
|---|---|---|
| `src/mind/academic.nova` | foundational | original |
| `src/mind/experiential.nova` | foundational | original |
| `src/mind/emotion.nova` | foundational | original |
| `src/mind/memory.nova` | foundational | original |
| `src/mind/reasoning.nova` | foundational | original |

### 15.5 Cognitive computational primitives

| File | Round | SHA |
|---|---|---|
| `src/cognitive/active_inference.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/associative.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/atom_lifecycle.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/causal_library.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/hdc.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/predictive_coding.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/resonance.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/sdr.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/self_model.nova` | N1-N11 batch | `73795eb` |
| `src/cognitive/skill.nova` | N1-N11 batch | `73795eb` |

### 15.6 Agent layer

| File | Round | SHA |
|---|---|---|
| `src/agent/agent.nova` | NOVA v4.1 | `e521870` |
| `src/agent/cognitive_llm.nova` | NOVA v4.0 | `438e798` |
| `src/agent/preprocess.nova` | NOVA v4.0 | `438e798` |
| `src/agent/rag.nova` | NOVA v4.0 | `438e798` |

### 15.7 Tooling layer

| File | Round | SHA |
|---|---|---|
| `src/tooling/profiler.nova` | N12-N29 batch | `accad2f` |
| `src/tooling/time_machine.nova` | N12-N29 batch | `accad2f` |
| `src/tooling/visualizer.nova` | N12-N29 batch | `accad2f` |
| `src/tooling/kg_visualizer.nova` | N12-N29 batch | `accad2f` |

### 15.8 IDE tooling (Python implementations)

| File | Round | SHA |
|---|---|---|
| `tools/nova-lsp/nova_lsp/server.py` | LSP MVP | `154a001` |
| `tools/nova-lsp/nova_lsp/imports.py` | LSP definition follow | `deac648` |
| `tools/nova-lsp/nova_lsp/workspace_symbols.py` | LSP workspace symbol | `c18a014` |
| `tools/nova-lsp/nova_lsp/rename_workspace.py` | LSP workspace rename | `e52314f` |
| `tools/nova-lsp/nova_lsp/semantic_tokens.py` | semantic tokens | `85fe64a` |
| `tools/nova-lsp/nova_lsp/hover_docs.py` | hover docs | `a852a39` |
| `tools/nova-lsp/nova_lsp/call_hierarchy.py` | call hierarchy | `5a57beb` |
| `tools/nova-lsp/nova_lsp/inlay_hints.py` | inlay hints | `c530629` |
| `tools/nova-lsp/nova_lsp/code_lens.py` | code lens | `3a6e217` |
| `tools/nova-lsp/nova_lsp/type_hierarchy.py` | type hierarchy | `a32e95c` |
| `tools/nova-lsp/nova_lsp/exhaustiveness_fix.py` | R20D quickfix | `04f1378` |
| `tools/nova-lsp/nova_lsp/extract_function.py` | R21F refactor | `ce4f878` |
| `tools/nova-lsp/nova_lsp/folding_ranges.py` | R22C folds | `47769af` |
| `tools/nova-lsp/nova_lsp/document_symbols.py` | R22C symbols | `47769af` |
| `tools/nova-lsp/nova_lsp/workspace_diagnostics.py` | R23F | `bb61c6f` |
| `tools/nova-lsp/nova_lsp/type_completion.py` | R24E | `940c4f6` |
| `tools/nova-lsp/nova_lsp/inline_variable.py` | R25F | `e2a87a9` |
| `tools/nova-dap/nova_dap/server.py` | DAP MVP | `233758a` |
| `tools/nova-dap/nova_dap/gdb_bridge.py` | DAP MVP | `233758a` |
| `tools/nova-dap/nova_dap/evaluator.py` | DAP evaluate | `2b58d5a` |
| `tools/nova-dap/nova_dap/watchpoints.py` | DAP watchpoints | `8cb0610` |
| `tools/nova-dap/nova_dap/function_breakpoints.py` | DAP fn bps | `3691954` |
| `tools/nova-dap/nova_dap/disassembly.py` | DAP instruction step | `38619aa` |
| `tools/tree-sitter-nova/grammar.js` | tree-sitter MVP | `6f503ec` (extended `09d7299`, R24B `72db976`) |

---

## 16. Round-to-feature index (rough chronology)

```
v1.0        Phases 1-6: M-numbered fixed-point checkpoints,
            mind + soul + signal + reader primitives, M9 self-host
v2.0        Cross-windows backend, security primitives,
            streaming signals + node pools
v3.0        FFI scaling, productivity libs, cognitive upgrades,
            tensors, real LLM bridge, novel syntax
v4.0        SSE2 SIMD vectorization, tiled matmul, OpenBLAS,
            cognitive LLM pipeline, embeddings
v4.1        Bayesian belief + goal engine + safety + imagination +
            multi-loop agent + concept hierarchy
v4.2        IEEE 754 floats + syscall FFI + ARM64 codegen +
            persistent allocator + hash-accelerated codegen
R11D        SIMD i32x8 intrinsics
R13A        Inline SIMD + int_* at call site
R14B        simd_sad_u8 (AVX2 vpsadbw)
R15B        WASM v128 SIMD lowering (close R11D's last gap)
R17A        Sum types with payloads + match exhaustiveness
R18A        Byte mul-acc SIMD primitives
R19B        Cross-target enum codegen
R20A        Result + postfix ? operator
R21A        Generic enum payloads
R22B        Generic function signatures
R23A        Generic structs + lightweight type-check pass
R24A        Fn-call + struct-ctor type-check
R24B        Tree-sitter grammar refresh (R17A-R23A)
R24E        LSP type-aware completion
R23F        LSP workspace diagnostics aggregation
R22C        LSP folding ranges + document symbols (16th + 17th)
R21F        LSP extract-function refactor
R20D        LSP quickfix auto-add missing match arms
R25F        LSP inline-variable refactor (third IDE refactor)
        +   Per-DAP rounds: evaluate/conditional bps, watchpoints,
            function bps, multi-thread, instruction stepping
Stability   VERSION 0.1.0 + STABILITY_AUDIT.md + COMPAT.md
            (commit a83cf50)
Distrib     v0.1.0 release pipeline activation (commit d543ec6)
```

---

## 17. How to navigate this codebase

1. **Start at the language reference, not the compiler.** Read
   `docs/LANGUAGE_REFERENCE.md` first for the surface syntax, then
   `docs/STDLIB.md` for the runtime surface, then this document for the
   "where does this live" map.
2. **The compiler entry point is `src/compiler/compiler.nova`
   (~808 lines).** Read it top-to-bottom — it's the cleanest
   walkthrough of the whole pipeline.
3. **Codegen is big (`~20k lines`) but uniform.** Every AST node has a
   `cg_emit_*` function. Search for the kind tag and you'll find the
   handler.
4. **The PTR_THRESHOLD heuristic is the single most common pitfall.**
   See `NOVA_BUG_THRESHOLD.md`. If a builtin returns a 64-bit value
   that the compiler decides is a pointer when it's actually an
   integer (or vice versa), that's PTR_THRESHOLD. The `int_*` family
   of builtins is the escape hatch.
5. **Every target has both an audit doc and a reference output in
   `bin/`.** If you're working on a target, start with the audit
   doc, then run the reference output to confirm the toolchain is
   working before you start editing.
6. **Self-host is the only invariant that matters.** `make self-host`
   must pass. If your change breaks it, the change is wrong (or the
   bootstrap needs an update — but you must say so explicitly).

---

This document is intentionally a long index. The audits go deeper;
the README narrates per-version; `STABILITY_AUDIT.md` enumerates the
ABI; `docs/LANGUAGE_REFERENCE.md` defines the language. Together they
cover the whole compiler.
