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

Imports declared in the module preamble (`codegen.nova:wasm_gen_program`):

| Import | WASI function | Used by |
| ------ | ------------- | ------- |
| `wasi_snapshot_preview1.fd_write` | write iovec to fd | `print`, `println`, `assert`, `write_file` |
| `wasi_snapshot_preview1.fd_read` | read iovec from fd | `read_file` |
| `wasi_snapshot_preview1.fd_close` | close fd | `read_file`, `write_file` |
| `wasi_snapshot_preview1.fd_seek` | seek on fd | reserved (P4+ random-access I/O) |
| `wasi_snapshot_preview1.path_open` | open file relative to dirfd | `read_file`, `write_file` |
| `wasi_snapshot_preview1.proc_exit` | exit with code | `exit`, `assert`-on-failure |

The hello-world flows `println -> print -> rt_strlen -> fd_write(1)`.
The file-I/O round-trip (`examples/file_wasm.nova`) flows
`write_file -> path_open(dirfd=3) -> fd_write -> fd_close` and the
mirror for `read_file`. On the way out, `_start -> main -> i64.const 0`
falls through to the module-level epilogue and the runtime exits
cleanly. There is no explicit `proc_exit` call for normal completion
-- WASI hosts treat `_start` returning as exit 0.

Mapping the rest of NOVA's `sys_*` wrappers is mechanical but unbuilt:
`nanotime -> clock_time_get(MONOTONIC)`, `sys_fsync -> fd_sync`,
`sys_rename -> path_rename`. Process-level calls (`fork`, `exec`,
`waitpid`, sockets) have no WASI equivalent in preview1; preview2 adds
a sockets API but is not in this sandbox.

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
make smoke-wasm      -> bin/hello.wat + bin/hello.wasm
                        node --experimental-wasi-unstable-preview1 prints
                        "hello from NOVA on WASM"
make smoke-wasm-file -> bin/file_wasm.wat + bin/file_wasm.wasm
                        node WASI w/ preopens={'/tmp':'/tmp'} writes
                        "nova" to /tmp/out.txt, reads it back, asserts
                        round-trip. Prints "OK: wasm file round-trip
                        succeeded".
make self-host      -> stage2.s == stage3.s
make test           -> all 5 test programs pass
make cross-windows  -> bin/nova.exe still produced
```

## Shipped this session (file I/O)

- `path_open`, `fd_read`, `fd_close`, `fd_seek` added to the WASI
  preamble. `_nova_wasi_path_offset` (codegen.nova helper) strips the
  leading `/<preopen-dir>/` prefix (e.g. `/tmp/out.txt -> out.txt`) so
  the same source compiles unchanged across native (which uses absolute
  paths via syscalls) and WASM (which routes through a preopen dirfd).
- `$read_file` and `$write_file` runtime functions implemented in
  `wasm_gen_rt_extra`. Layout:
  - `path_open(dirfd=3, dirflags=0, path_ptr, path_len, oflags,
     fs_rights_base=0x4DFFFFF, fs_rights_inheriting=0x4DFFFFF,
     fdflags=0, opened_fd_out=&scratch[16])`
  - Linear-memory scratch at offsets 16..63 carries `opened_fd_out`,
    iovec, and the in-out byte-count slot. Offsets 0..15 belong to
    `print`, so file I/O never clobbers print's iovec.
  - Buffer alloc reuses the existing bump allocator: 524288 bytes per
    `read_file` call (matches the native `_nova_read_file` budget).
- `str_eq` added to the WASM runtime as a thin wrapper around the
  existing `$rt_streq` (was previously only available to native
  targets; needed by `examples/file_wasm.nova` to assert the round-trip).
- The rights mask `0x4DFFFFF` covers everything the dirfd grants for a
  regular file: `FD_DATASYNC|READ|SEEK|FDSTAT_SET_FLAGS|SYNC|TELL|
  WRITE|ADVISE|ALLOCATE|FILESTAT_GET|FILESTAT_SET_SIZE|
  FILESTAT_SET_TIMES|POLL_FD_READWRITE`. We arrived at this via
  empirical bisection -- the broader `ALL_RIGHTS=0x1FFFFFFF` mask trips
  `ENOTCAPABLE (76)` on node WASI because node's preopen grants do not
  cover the SOCK_* bits.

## Known gaps for next session

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
  `make smoke-wasm-file` always runs node (since the assertion IS the
  whole point of the target) but skips cleanly if node or wat2wasm is
  missing.

## Estimated wall-clock to full CrossEngin-on-WASM

4-6 weeks remaining (was 6-8 before file I/O landed):

- ~~Week 1-2: file I/O via WASI `path_open` + preopen directory
  mounting.~~ **DONE** -- see "Shipped this session" above.
- Week 1-2: time / random / clock builtins via WASI clock + `random_get`.
- Week 2-4: replace bump allocator with `memory.grow`-aware
  arena, add explicit free for short-lived strings.
- Week 4-5: validate full CrossEngin demo (`kernel_selfcheck`,
  `companion_spine`, `crossengin_daemon`) under wasmtime + browser.
- Week 5-6: WASM SIMD lowering for the `__intrinsic_dot_i32` path,
  socket emulation via a JS bridge if needed, browser deployment doc.

## Minimum scope shipped this session (P2.7 + P2.7.1 follow-up)

Per the priority-list ask: hello-world that prints `hello from NOVA on
WASM` and exits cleanly. **Done.** Plus the P2.7.1 follow-up: a
WASI-backed `read_file` / `write_file` round-trip running under node
`--experimental-wasi-unstable-preview1` with `preopens=/tmp=/tmp`.
**Done.** Anything beyond that lives in the gaps section above.
