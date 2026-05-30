# NOVA WebAssembly Backend Audit

P2.7 sweep on branch `claude/festive-franklin-PP7mW`. Same shape as the
P1.10 macOS and P12 Win32 audits: prove a path end-to-end, document the
chasm between this proof and "CrossEngin in a browser".

## Status: hello-world round-trips through node WASI

`nova examples/hello_wasm.nova --target=wasm -o /tmp/hello_wasm.wat`
emits a 776-line WebAssembly Text module. `wat2wasm` (from `wabt`)
converts it to a 1566-byte `.wasm` binary. Under
`node --experimental-wasi-unstable-preview1`, the binary prints
`hello from NOVA on WASM` and exits 0. `wasmtime` is not present in
this sandbox so it was not exercised, but the WASM is unambiguously a
WASI preview1 module: the only host imports are
`wasi_snapshot_preview1.fd_write` and `wasi_snapshot_preview1.proc_exit`,
both of which are part of every conforming WASI runtime.

`make smoke-wasm` runs the whole pipeline and skips cleanly with a
clear `(skip: wasm toolchain not available)` message if `wat2wasm` is
absent.

## Quick map

- `cg_target == 2` means **WebAssembly (WAT text, WASI preview1)**.
  Numbering: `0=linux, 1=macos, 2=wasm, 3=windows, 4=arm64`. Selected
  via `--target=wasm` (`src/compiler/compiler.nova:619`).
- The WASM path is short-circuited very early in `gen_program`
  (`codegen.nova:3199`): it does NOT share any x86-64 emission code,
  GAS section directives, or peephole passes with the native backends.
  The whole WASM backend lives in one ~1700-line block starting at
  `codegen.nova:4036` (`wasm_*` functions plus `wasm_gen_program`).
- Output is a single `(module ...)` WebAssembly Text file. There is no
  linker -- a WASM module IS the unit, so all of NOVA's runtime helpers
  (`alloc`, `concat`, `list_new`, `push`, `int_to_str`, `print`, etc.)
  are emitted as WASM functions inside the same module.

## Why WASM is structurally harder than Win32 or macOS

The macOS and Windows ports were essentially section-name + syscall-
number translations on top of the same x86-64 register machine NOVA
already targeted. WASM is a fundamentally different machine model:

1. **Stack-based VM, not a register machine.** Every value is pushed
   onto an operand stack; there is no `rax`. NOVA's existing codegen
   walks the AST and emits `push rax / pop rcx / op rax,rcx`. WASM
   would emit `<expr>; <expr>; i64.op`. The two strategies happen to
   align (NOVA already uses stack-based evaluation), but the
   instruction surface is completely disjoint.
2. **No inline assembly.** `__intrinsic_dot_i32` (AVX2, P2.3) and any
   future SIMD path cannot cross the WASM boundary as-is. WASM has its
   own 128-bit SIMD proposal (`v128`) but the mnemonic set is unrelated
   to x86 SSE/AVX.
3. **WASI syscall surface, not POSIX.** WASM has no `syscall`
   instruction; host I/O happens through imported functions in
   namespace `wasi_snapshot_preview1`. For this round, we only import
   `fd_write` (stdout / stderr) and `proc_exit` (clean shutdown).
   Future milestones will need `fd_read`, `fd_close`, `path_open`,
   `clock_time_get`, `fd_prestat_get` (for preopen-based filesystem
   access), and `random_get`.
4. **32-bit linear memory at the host bridge.** WASM's linear memory
   is addressed by `i32`, even on 64-bit hosts. NOVA values are `i64`
   internally (matching the native 64-bit slot size), so every memory
   access in the runtime helpers does `i32.wrap_i64` first. Pointers
   are not transferable between modules.
5. **No raw pointers usable cross-module.** A `.wasm` file is sandboxed
   to its own linear memory. FFI to libc / libdl / a native blas is
   impossible -- those parts of NOVA's runtime (`--link-libc`, BLAS)
   are simply not available in the WASM build. CrossEngin will need a
   capability layer that imports JavaScript / wasmtime helpers for any
   capability the native runtime gets from libc.

## WASI syscall surface used today

Imports declared in the module preamble (`codegen.nova:5694-5696`):

| Import | WASI function | Used by |
| ------ | ------------- | ------- |
| `wasi_snapshot_preview1.fd_write` | write iovec to fd | `print`, `println`, `assert` |
| `wasi_snapshot_preview1.proc_exit` | exit with code | `exit`, `assert`-on-failure |

The hello-world flows `println -> print -> rt_strlen -> fd_write(1)`.
On the way out, `_start -> main -> i64.const 0` falls through to the
module-level epilogue and the runtime exits cleanly. There is no
explicit `proc_exit` call for normal completion -- WASI hosts treat
`_start` returning as exit 0.

Mapping the rest of NOVA's `sys_*` wrappers is mechanical but unbuilt:
`sys_read -> fd_read`, `sys_open -> path_open`, `sys_close -> fd_close`,
`sys_lseek -> fd_seek`, `nanotime -> clock_time_get(MONOTONIC)`,
`sys_fsync -> fd_sync`, `sys_rename -> path_rename`. Process-level
calls (`fork`, `exec`, `waitpid`, sockets) have no WASI equivalent in
preview1; preview2 adds a sockets API but is not in this sandbox.

## Memory model

`(memory (export "memory") 256)` declares 256 64KB pages = 16 MiB.
`heap_ptr` starts at offset 65536 (skipping the first page which holds
the data section + iovec scratch at offsets 0-4). The runtime helpers
`alloc(size)` are a pure bump allocator -- no free, no GC. For
hello-world this is fine; CrossEngin will need either `memory.grow`
hooks for arenas or a real allocator to handle multi-megabyte mind
states. `memory.grow` plus a free-list bump is the planned next step.

## What works end-to-end (verified here)

```
make smoke-wasm    -> bin/hello.wat (776 lines) + bin/hello.wasm (1566 bytes)
                      node --experimental-wasi-unstable-preview1 prints
                      "hello from NOVA on WASM"
make self-host    -> stage2.s == stage3.s
make test         -> all 5 test programs pass
make cross-windows-> bin/nova.exe still produced
```

## Known gaps for next session

- **File I/O on WASI** (`fd_read`, `path_open`, etc.): runtime
  emission is stubbed at `i64.const 0`, so any program calling
  `read_file` / `write_file` under `--target=wasm` will silently no-op.
- **`int_to_str` printf integration**: works, but only used through
  `print_int`. Float-to-string is approximate (multiplied by 1000).
- **Concat / string ops**: emitted but exercised only at the runtime-
  helper level. Larger programs (anything beyond hello-world) need a
  real round-trip test under wasmtime.
- **No FFI**: `--link-libc` plus `--target=wasm` should produce a clear
  error. Today it silently still attempts to emit ELF-shaped output.
- **No SIMD / intrinsics**: `__intrinsic_dot_i32` on WASM falls through
  to the scalar path (the AVX2 codegen guard at `codegen.nova:10885`
  requires `cg_target == 0`). WASM v128 lowering is a P4+ task.
- **`wasmtime` not in this sandbox**: only `node`'s WASI was used.
  Adding wasmtime to the smoke target would confirm the binary really
  is portable across hosts.
- **`make smoke-wasm` only runs node if `WASM_OK=1`** -- by default it
  produces the .wat / .wasm and stops; this matches the pattern of
  `smoke-windows` (which only runs wine on `WINE_OK=1`).

## Estimated wall-clock to full CrossEngin-on-WASM

6-8 weeks, consistent with the P2.7 priority-list entry. Breakdown:

- Week 1-2: file I/O via WASI `path_open` + preopen directory mounting.
- Week 2-3: time / random / clock builtins via WASI clock + `random_get`.
- Week 3-5: replace bump allocator with `memory.grow`-aware
  arena, add explicit free for short-lived strings.
- Week 5-6: validate full CrossEngin demo (`kernel_selfcheck`,
  `companion_spine`, `crossengin_daemon`) under wasmtime + browser.
- Week 6-8: WASM SIMD lowering for the `__intrinsic_dot_i32` path,
  socket emulation via a JS bridge if needed, browser deployment doc.

## Minimum scope shipped this session (P2.7)

Per the priority-list ask: hello-world that prints `hello from NOVA on
WASM` and exits cleanly. **Done.** Anything beyond that lives in the
gaps section above.
