# NEXT_SESSION.md — Nova Implementation Status

## Completed

### N12–N29 Modules (18 modules)
All fully implemented with:
- Implementation files under `src/cognitive/`, `src/runtime/`, `src/tooling/`
- Unit tests in `tests/` (all passing: 151/157, 0 failures, 6 skipped)
- Example programs in `examples/` (18 new `*_demo.nova` files)
- Documentation in `docs/STDLIB.md`
- Self-hosting verified (`stage2.s == stage3.s`)

### Phase 1: Tensor Performance
- SSE2 vectorized `simd_dot_f64` (4-element unrolled mulpd/addpd)
- SSE2 vectorized `simd_scale_f64` and `simd_sum_f64`
- Tiled matmul for matrices >= 64 columns (32x32 tile blocking)
- OpenBLAS FFI wrapper (`src/runtime/blas.nova`)
- Size-based dispatch in `tensor_matmul` (simple → tiled → BLAS)
- Tests: `test_tensor_perf` passes

### Phase 2: Cognitive LLM Pipeline
- `src/agent/cognitive_llm.nova` — LLM generation + reasoning + confidence
- Integrates with reasoning engine, episodic memory, emotion analysis
- Tests: `test_cognitive_llm` passes

### Phase 3: Competitive Embeddings
- `src/runtime/embedding.nova` — unified multi-backend embedding interface
- TF-IDF + character n-grams + BM25 scoring
- Neural embedding via Python bridge (optional)
- Cognitive embedding with emotional/episodic/inferential dimensions
- Tests: `test_embedding` passes

### Phase 4: Import Scaling
- **4a**: O(1) function name lookup via hash table (`cg_fns_ht`)
  - Hash table with 8 buckets, `len(name) % 8` hash function
  - `is_known_function` uses hash table instead of O(n) scan
  - Note: `is_global` stays linear scan due to destructuring edge case
- **4b**: O(1) dependency and package lookups
  - `_dep_get` uses key-value hash table (`_dep_ht`)
  - `is_std_package` uses membership hash table (`_std_pkg_ht`)
- **4c**: Module provenance tracking
  - `cg_fn_modules` hash table maps function names to source files
  - `fn_module(name)` returns the source file that defined a function
  - Tracked for all function declarations, extern functions, methods, lambdas

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

### Compiler 7th-parameter bug — FIXED
Previously, functions with 7+ parameters produced incorrect values for the
7th+ arguments (stack-passed args were never copied into local slots, and
the caller didn't clean up extra stack args after the call). Fixed in
codegen.nova: function prologues now copy `[rbp + 16 + n*8]` into local
slots for parameters 7+, callers emit `add rsp` to clean up, and alignment
padding ensures 16-byte stack alignment before `call`. Test:
`tests/test_7th_param.nova` verifies 7, 8, and 9 parameter functions.
Note: `causal_library.nova` still uses the old 6-param workaround but new
code can freely use 7+ parameters.

### is_global hash table incompatibility
The hash table optimization for `is_global` causes segfaults when compiling
destructuring patterns (`let [a, b] = ...`). Root cause: values passed to
`is_global` during `collect_locals` may not always be valid string pointers.
The hash table works for `is_known_function` because function names are
always string literals from the AST. `is_global` remains O(n) linear scan.

### Forbidden patterns
- `char_at(s, i)` — broken, use `substr(s, i, 1)`
- `map_new()` — 16-slot limit causes infinite loops; use parallel lists
- `soul` — reserved keyword (TOK_SOUL=113), use `soul_ref` as variable name
- `list_insert`/`list_remove` — conflict with compiler builtins when concatenated
- `none` keyword — use `0` instead in runtime modules

### PTR_THRESHOLD integer misclassification — FIXED (R6A)
Previously, the smart-op runtime helpers (`_nova_add`, `_nova_mul`, `_nova_eq`,
`_nova_neq`, `_nova_lt`, `_nova_gt`, `_nova_le`, `_nova_ge`, and several
classifier helpers `_nova_type_of`, `_nova_type_name`, `_nova_debug_print`,
`_nova_hash_key`, `_nova_to_str`, `_nova_flatten`) used a magnitude heuristic
to distinguish pointers from integers: any value `>= 0x100000` (1 MiB) was
treated as a pointer. This silently corrupted any program with integers
above 1 MiB — pacer nanotimes, kg_sync sequence numbers, bignum limbs,
JPEG DCT coefficients, 31-bit LCG masks, etc. — and forced six different
agent sessions to work around it via the `int_*` builtins.

The root cause is now fixed in `src/compiler/codegen.nova`:

1. A new pair of helper subroutines `_nova_check_rdi` / `_nova_check_rsi`
   replace the inline `cmp rdi, 0x100000; jl ...` pattern with a real
   range check. A value is a pointer iff it lies in:
   - the link-time-fixed string literal range
     `[_strlit_start, _strlit_end)` (new labels bracketing all .rodata
     literals), OR
   - the runtime heap range `[_heap_base, _heap_end)`, OR
   - the kernel-area high range `>= 0x400000000` (16 GiB), which covers
     argv/env/stack pointers and mmap'd heaps on macOS/Windows.
   Negative integers (high bit set) short-circuit to integer.

2. Every call site that previously used the magnitude check (22 sites:
   `_nova_add`, `_nova_mul`, the six comparison helpers, `_nova_type_of`,
   `_nova_type_name`, `_nova_debug_print`, `_nova_hash_key`,
   `_nova_to_str`, `_nova_flatten`) now calls one of the new helpers
   and branches on ZF.

The `int_*` builtins remain available as no-op scalar wrappers for
back-compat. They are no longer required for integers up to 16 GiB.

Regression test: `tests/test_ptr_threshold_fix.nova` (23 checks covering
`+`, `*`, `&`, `<<`, `>>`, comparisons, equality, negative integers,
plus string/list smart-op back-compat).

Verification:
- `make self-host` — stage2.s == stage3.s, bit-identical.
- `make test` — all runtime tests pass.
- `make test-all` — 152/160 pass (the 2 pre-existing destructure
  failures are unchanged; +1 net pass from the new regression test).
- `make bench-int-safe` — large-int correctness PASS; smart-op now
  ~2.3-2.8x slower than `int_*` due to the call-vs-inline cost, which
  is still acceptable and the int_* speedup is itself documented.
- CrossEngin `make test` — 142/142 pass.

Proof-of-fix: `crossengin-demo/src/safety/bignum_2048.nova` `bn2048_add`
and `bn2048_sub` were converted from `int_add` / `int_sub` calls to plain
`+` / `-` operators; the bignum test suite continues to pass bit-
identical results (modpow round-trips, Montgomery vs legacy parity).

## Files Modified in Compiler (Phase 4)

- `src/compiler/codegen.nova` — added hash table functions (`_cg_ht_new`,
  `_cg_ht_hash`, `_cg_ht_add`, `_cg_ht_has`, `_cg_ht_set`, `_cg_ht_get`),
  function name hash table (`cg_fns_ht`), module tracking (`cg_fn_modules`),
  `fn_module()` query function
- `src/compiler/compiler.nova` — O(1) dependency lookup via `_dep_ht` hash
  table, updated `_dep_add` and `_dep_get`
- `src/pkg/pkg.nova` — O(1) package lookup via `_std_pkg_ht` hash table,
  `_pkg_reg()` helper, updated `is_std_package`

## What's Left (for future sessions)

1. Fix the 7th-parameter compiler bug in codegen.nova
2. Investigate `is_global` hash table incompatibility (may be fixable by
   adding type guards for string pointers in `_cg_ht_hash`)
3. Example programs are standalone demos — they don't compile/run without
   library source concatenation (by design, matching existing examples)
4. Surface NOVA coroutines as DAP threads. Today coroutines share a single
   OS thread and are not visible to gdb as separate LWPs, so the DAP server
   reports them as one thread. A coroutine-aware mapping would need either
   (a) compiler-emitted DWARF that describes coroutine frames as separate
   thread ids, or (b) the DAP server reading NOVA's coroutine table out of
   the inferior's heap via the gdb python API and synthesizing virtual
   threads for it.

## R7D — Multi-thread DAP coordination

`tools/nova-dap` now exposes 17 DAP capabilities; the multi-thread
coordination layer was added on top of R4A's `.debug_info` work and the
existing single-thread step/breakpoint/stack/variable plumbing.

Changes (`tools/nova-dap/nova_dap/server.py`):
- Enable gdb `mi-async on` + `non-stop on` during launch so each thread
  can be paused / continued independently. Fall back to all-stop mode
  if gdb refuses (e.g. unsupported target).
- Session tracks a `known_threads` table mirroring gdb's thread set,
  updated from `=thread-created` / `=thread-exited` notifications and
  reconciled via `-thread-info` on every DAP `threads` request.
- Stable per-(threadId, level) DAP frame ids; `scopes` and `variables`
  route via `-thread-select` + `-stack-select-frame` so thread A's
  locals never leak into thread B's variables panel.
- `continue` / `next` / `stepIn` / `stepOut` / `pause` accept DAP
  `threadId` + `singleThread`. `singleThread:true` issues
  `-exec-{continue,next,step,finish,interrupt} --thread <id>`;
  otherwise `--all` (or no flag in all-stop mode).
- `*stopped` records produce DAP `stopped` events with `threadId` from
  the MI record and `allThreadsStopped=true` only when MI
  `stopped-threads="all"` (so in non-stop mode a breakpoint hit on
  thread A doesn't claim thread B is stopped).
- New events: `continued` (per-thread resume) and `thread` (lifecycle).
- New capability: `supportsSingleThreadExecutionRequests: true`.

Thread model: real OS threads, surfaced from gdb-MI in non-stop mode.
For a single-threaded NOVA program (no FFI, no pthreads) this collapses
to one thread (`id=1`, name=`main`) — the wire protocol still works
end-to-end, there's just only one thread to address.

Tests:
- `tools/nova-dap/tests/dap_smoke.py` (pre-existing single-thread
  smoke) — still passes.
- `tools/nova-dap/tests/dap_multi_thread.py` (new) — builds a small
  pthread C fixture (`tests/fixtures/multi_thread.c`) on demand and
  exercises `threads`, per-thread `stackTrace` + `scopes` + `variables`
  isolation, per-thread `next`/`stepIn`/`stepOut`, `pause`, and
  `continue` (with and without `singleThread`). SKIPs cleanly if
  `gcc` or `gdb` is unavailable.

## R7A — Windows ARM64 (PE32+ AArch64) backend

NOVA's sixth codegen target. Closes the last gap in the cross-platform
matrix (Linux x86-64 + macOS x86-64 + WASM + Windows x86-64 + ARM64-
Linux/Android + Windows ARM64).

Changes (`src/compiler/codegen.nova`):
- New `winarm64_gen_program` standalone AST-walking backend modeled on
  the Linux ARM64 path (`arm64_gen_program`). Selected via target id
  `5`, exposed as `--target=windows-arm64`.
- Emits GAS-syntax ARM64 instructions with PE section directives
  (`.section .text,"xr"`, `.section .rdata,"dr"`) and IAT-style import
  declarations (`.extern __imp_<API>` for ExitProcess, GetStdHandle,
  WriteFile, BCryptGenRandom).
- Imported APIs are called via the standard PE pattern:
  `adrp x16, __imp_<name>; ldr x16, [x16, :lo12:__imp_<name>]; blr x16`.
  x16 is the AArch64 caller-saved scratch (IP0 in the AAPCS64 spec).
- Win32 entry symbol `mainCRTStartup` (lld-link's default console
  entry when no CRT is present). The entry sets up an ARM64 frame,
  runs top-level statements, then calls `ExitProcess(0)`.
- 16-byte SP alignment maintained at every call boundary; standard
  `stp x29, x30, [sp, #-16]!` prologue / `ldp x29, x30, [sp], #16`
  epilogue; locals laid out below x29 with 8-byte slots.
- Tiny runtime: `_nova_warm_strlen`, `_nova_warm_write_stdout`
  (GetStdHandle(-11) + WriteFile via IAT), `_nova_warm_print`,
  `_nova_warm_println` (CRLF append for Windows console compat),
  printable-error stubs for unsupported builtins (list_new/push/len/
  read_file/write_file).
- `secure_random(buf, n)` lowers to `BCryptGenRandom(NULL, buf, n,
  BCRYPT_USE_SYSTEM_PREFERRED_RNG=2)` via the IAT. NTSTATUS == 0 ->
  returns n_bytes; else -1.

Changes (`src/compiler/compiler.nova`):
- `--target=windows-arm64` is parsed, `cg_target = 5`, banner reads
  "Target: Windows ARM64 (PE32+ AArch64)".

Changes (`Makefile`):
- New `smoke-winarm64` target (alias `cross-winarm64`): generates
  bin/hello_winarm64.exe and bin/secure_random_winarm64.exe. Pipeline:
    1. NOVA --target=windows-arm64 -> ARM64 GAS .s
    2. clang -target aarch64-windows-gnu -c -> Aarch64 COFF .o
    3. llvm-dlltool -m arm64 fabricates ARM64 import libs from .def
       files (KERNEL32.DLL: ExitProcess, GetStdHandle, WriteFile;
       BCRYPT.DLL: BCryptGenRandom). No upstream mingw-w64 aarch64
       import lib package exists on Debian/Ubuntu, so we synthesize
       them at build time.
    4. lld-link /machine:arm64 /subsystem:console
       /entry:mainCRTStartup -> PE32+ executable
  Skips cleanly if clang / lld-link / llvm-dlltool are missing.

Tests (`tests/test_winarm64_emitter.sh`):
- Runs `make smoke-winarm64` then asserts:
  * `file` reports PE32+ executable Aarch64 for MS Windows
  * `llvm-readobj` reports IMAGE_FILE_MACHINE_ARM64 (0xAA64)
  * Raw byte check at PE signature+4 == `64 AA` (little-endian
    0xAA64) on disk
  * KERNEL32.DLL import present in both binaries
  * BCRYPT.DLL import present in secure_random binary
  * IAT call sequence (adrp x16 / ldr x16 / blr x16) appears in
    .text disassembly
  * Standard ARM64 prologue is present
  * Binary sizes within sanity bounds (1024..20480 bytes)

Verification artifacts:
- bin/hello_winarm64.exe — 2048 bytes, KERNEL32.DLL import (loops +
  string concat omitted; standalone path doesn't yet lower them).
- bin/secure_random_winarm64.exe — 3072 bytes, KERNEL32.DLL +
  BCRYPT.DLL imports.
- Both binaries: COFF machine 0xAA64, entry `mainCRTStartup`,
  ImageBase 0x140000000, 2 sections (.text + .rdata), proper IAT.
- Cannot execute on Linux x86-64 host — runtime confirmation
  requires an ARM-Windows tester. Format is verified end-to-end
  on the Linux host via llvm-readobj / llvm-objdump.

Gap status (deferred for future R-rounds):
- The standalone winarm64 path covers the same surface as the
  Linux ARM64 standalone path: integer arithmetic, control flow,
  print/println, exit, secure_random. It does NOT yet wire
  list/map/string-concat/file-IO — those require the full IR-based
  Windows backend (cg_target == 3) which still targets x86-64.
  Lifting the IR path to ARM64 (via lower_arm64.nova's IR-walking
  emitter) is the next milestone.
- DWARF/CodeView debugging info on the winarm64 target is not
  emitted (matches the Linux ARM64 standalone path).

## R8C — LSP workspace symbol search (`workspace/symbol`)

`tools/nova-lsp` now exposes the last LSP capability gap. Editors can
hit Cmd+T / Ctrl+T and fuzzy-search every top-level NOVA symbol across
the workspace.

Changes (`tools/nova-lsp/nova_lsp/workspace_symbols.py`, new module):

- `WorkspaceSymbolIndex` — inverted index `name -> [SymbolEntry, ...]`
  with a reverse map `path -> set[name]` for O(symbols_per_file)
  invalidation.
- `index_text(path, text)` — re-scans a file from in-memory text, used
  on `didOpen`/`didChange` so unsaved edits are searchable instantly.
- `index_file(path)` — re-scans from disk, used on `didClose` of a
  still-existing file and during the lazy root crawl.
- `index_workspace_root(root)` — one-time recursive `*.nova` crawl,
  pruning `.git`, `node_modules`, `__pycache__`, `bin`, `build`.
- `fuzzy_match(query, limit=100)` — tiered scoring: exact (0) >
  case-insensitive (1) > prefix (2) > substring (3) > camelCase letter
  match (4) > sequential character match (5). Empty query returns the
  first N symbols in alphabetical order.
- Recognised declarations: `fn name(...)` → `SymbolKind.Function (12)`;
  ALL_CAPS `let NAME = ...` → `SymbolKind.Constant (14)`; other `let`
  → `SymbolKind.Variable (13)`; `out_label("_nova_*")` inside
  `codegen.nova` / `compiler.nova` → `SymbolKind.Function` with
  `containerName="<runtime>"` so the R6A helpers like
  `_nova_check_rdi` / `_nova_check_rsi` are discoverable.

Changes (`tools/nova-lsp/nova_lsp/server.py`):

- `workspaceSymbolProvider: {resolveProvider: false}` registered in
  the `initialize` response.
- `handle_workspace_symbol` lazily crawls `state.root_path` on the
  first query, double-taps the live-buffer refresh for every open doc,
  and returns the top 100 `SymbolInformation` records.
- `didOpen` / `didChange` / `didSave` call
  `_refresh_workspace_symbols_for_doc` so live edits are reflected
  immediately; `didClose` re-reads the file from disk (or invalidates
  if the file is gone). The R5F `FileCache` lifecycle is untouched.

Tests (`tools/nova-lsp/tests/test_workspace_symbols.py`, new):

- 11 test functions / 52 assertions covering: empty workspace, single
  file with 5 fns, three-file cross-file workspace, fuzzy ranking
  (`foB` → `fooBar` before `foo_bar`), tier breakdown for the score,
  empty-query alphabetical first-N, SymbolKind classification (fn
  vs ALL_CAPS let vs lower-case let), file invalidation, didChange
  reindexing, end-to-end LSP wire test through `dispatch`, and an
  integration test that indexes `/home/user/NOVA/src/` (~3665
  symbols) and locates `_nova_check_rdi` at codegen.nova:8569
  alongside its sibling `_nova_check_rsi`.

Verification:
- `python tests/test_workspace_symbols.py` — OK, 52 assertions.
- All 5 prior LSP tests still pass (completion, rename, references,
  code_action, definition_cross_file).
- Indexed 3665 symbols across NOVA's `src/` tree (2097 top-level
  `fn` + 1182 top-level `let` + 386 `out_label("_nova_*")` runtime
  labels).

## R8A — WASI preopens / filesystem (serverless deployment surface)

Closes the WASM serverless deployment gap. Pre-R8A the WASM target
shipped `fd_write`/`fd_read`/`fd_close`/`fd_seek`/`path_open`/
`random_get`/`proc_exit` imports and `read_file`/`write_file`
convenience builtins. R8A extends the surface so a NOVA program can
drive WASI primitives directly -- the building blocks for streaming
I/O on Cloudflare Workers, wasmtime serve, Fastly Compute@Edge, etc.

Changes (`src/compiler/codegen.nova`):

- WASM module header now emits 12 `wasi_snapshot_preview1` imports:
  `fd_write`, `fd_read`, `fd_close`, `fd_seek`, `path_open`,
  `path_filestat_get` (new), `args_sizes_get` (new), `args_get`
  (new), `environ_sizes_get` (new), `environ_get` (new), `random_get`,
  `proc_exit`. All imports are always emitted (vs. on-demand) so the
  module header stays bit-stable for self-host parity.
- Eight new NOVA-callable builtins registered in `is_builtin_fn`:
  `wasi_open(path, flags) -> fd | -1`
  `wasi_read(fd, buf, len) -> bytes_read | -1`
  `wasi_write(fd, buf, len) -> bytes_written | -1`
  `wasi_close(fd) -> 0 | -1`
  `wasi_seek(fd, offset, whence) -> new_offset | -1`
  `wasi_filestat(path) -> [size, mtime_ns, kind] | 0`
  `wasi_args_get() -> list of argv strings`
  `wasi_environ_get() -> list of "KEY=VALUE" strings`
- WASM runtime (`wasm_gen_rt_io`) adds bodies for the eight builtins.
  They wrap the new imports against the existing scratch layout
  (offsets 16..63 for path_open/fd_read/fd_write/fd_seek; offsets
  64..127 for the 64-byte path_filestat_get result; offsets 128..135
  for args/environ size out-pointers). dirfd=3 routes through the
  first preopen — `wasmtime --dir=/tmp` or `wasmer run --mapdir`.
- Linux x86-64 native runtime adds POSIX-equivalent bodies for the
  same builtins so the WASI surface compiles and runs natively too:
  `_nova_wasi_open` -> open(2) with WASI->POSIX flag translation,
  `_nova_wasi_read` -> read(2), `_nova_wasi_write` -> write(2),
  `_nova_wasi_close` -> close(2), `_nova_wasi_seek` -> lseek(2),
  `_nova_wasi_filestat` -> stat(2) with WASI filetype mapping,
  `_nova_wasi_args_get` walks `_nova___arg`, `_nova_wasi_environ_get`
  returns empty list (the WASM path serves the real envp).
- Non-Linux native targets (macOS, Windows, ARM64, winARM64) emit
  stubs that return -1 / empty list. The primary contract is the
  WASM path; native paths exist so the same source compiles
  everywhere.

Changes (`Makefile`):

- New `smoke-wasi-preopens` target. Compiles
  `examples/wasi_file_roundtrip.nova` to WASM, runs under wasmtime
  with `--dir=/tmp`, verifies the 12 wasi_snapshot_preview1 imports
  are present via `wasm-objdump`, and checks the round-tripped file
  contents. Skips cleanly if wat2wasm or wasmtime is missing.

New files:

- `examples/wasi_file_roundtrip.nova` — eight-step program exercising
  every R8A builtin (open(CREAT|TRUNC) -> write -> close -> filestat
  size+kind -> reopen -> read -> close -> bracketed byte verify).
- `tests/test_wasi_preopens.sh` — driver script for the smoke target.

Verification:
- `make smoke-wasi-preopens` -> PASS under wasmtime 45.0.0.
- `wasm-objdump -x bin/wasi_file_roundtrip.wasm | grep wasi_` lists
  all 12 wasi_snapshot_preview1 imports (sig 0..6).
- `make hello-wasm`, `make smoke-wasm`, `make smoke-wasm-file` —
  still PASS; existing behaviour preserved.
- `make test` — runtime tests PASS unchanged.
- `make self-host` — stage2.s == stage3.s bit-identical.
- `make cross-windows`, `make cross-macos`, `make cross-winarm64` —
  all PASS (non-Linux wasi_* stubs are size-stable -1 / empty list).
- Native Linux x86-64 also runs `examples/wasi_file_roundtrip.nova`
  via the syscall-backed `_nova_wasi_*` runtime; the same source
  cross-compiles + executes correctly on both targets.
