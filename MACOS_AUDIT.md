# NOVA macOS Backend Audit

P1.10 sweep on branch `claude/festive-franklin-PP7mW`. Scope: prove
the path, not finish the full port. The equivalent prior write-up for
the Windows backend lives in `WIN32_AUDIT.md`.

## Status: minimum viable

`nova --target=macos hello_macos.nova -o hello.s` followed by
`clang -target x86_64-apple-darwin -c` and `ld64.lld -arch x86_64
-platform_version macos 10.13 10.13 -e _main` produces a `Mach-O
64-bit executable x86_64` from this Linux sandbox. The binary cannot
actually execute here (needs the XNU kernel); format verified with
`file` and `llvm-objdump`. `make smoke-macos` runs the whole pipeline
and skips cleanly if the cross-toolchain is absent.

## Quick map

- `cg_target == 1` means macOS (x86-64, Mach-O, Darwin BSD syscalls).
  Numbering: `0=linux, 1=macos, 2=wasm, 3=windows, 4=arm64`. Selected
  via `--target=macos` (`src/compiler/compiler.nova:619`).
- Section names use Mach-O spelling via `out_section`
  (`codegen.nova:170`): text -> `__TEXT,__text`, rodata ->
  `__TEXT,__const`, bss -> `__DATA,__bss`.
- Entry point: `_main`, called directly by dyld with the standard
  Unix stack layout (`argc` at `[rsp]`, `argv` at `[rsp+8]`). Set in
  `gen_program` at `codegen.nova:3842`.
- Exit: Darwin BSD `exit` syscall (`1 | 0x2000000` = 33554433).

## Darwin syscall numbering

XNU biases BSD-class syscalls by `0x2000000` (Mach traps use other
classes); the `syscall` instruction itself is identical to Linux. The
translation lives in `syscall_num()` at `codegen.nova:207`.

Used by hello-world:

| Linux # | Linux call | Darwin BSD # | Encoded value |
| ------- | ---------- | ------------ | ------------- |
| 1       | write      | 4            | `0x2000004` (33554436) |
| 60      | exit       | 1            | `0x2000001` (33554433) |

Many more mappings (mmap 197, read 3, open 5, close 6, fstat 189,
fork 2, execve 59, socket 97, etc.) are already present for the
broader runtime, but unverified end-to-end this session.

## Toolchain

On a real macOS host: native `as` + `ld` from Xcode CLT.

Cross-link from Linux (this sandbox): `clang -target
x86_64-apple-darwin -c` for assembly (LLVM MC parser accepts the same
`.intel_syntax noprefix` dialect NOVA emits for Linux) plus `ld64.lld`
(`apt install lld llvm clang` or `brew install lld`). No `libSystem`
/ `crt1` -- this is a freestanding executable talking directly to XNU.
A future milestone wanting `printf` or `pthread` will need
`libSystem.tbd` from an SDK and a real macOS host.

## Cross-toolchain fix made this session

LLVM's MC parser rejects `movsx rdi, edi` (requires explicit `movsxd`
for 32->64 sign-extend); GAS accepts both. The two sites in
`_nova_pipe` were switched to `movsxd`. Invisible to Linux/Windows
targets; self-host check still verifies `stage2.s == stage3.s`.

## What works end-to-end (verified here)

- `println` / `_nova_print_str` / `_nova_print_int` -- write syscall
- `_nova_concat` -- `_nova_alloc` -> mmap
- `_nova_alloc` -- Darwin mmap with `MAP_ANON|MAP_PRIVATE` (`0x1002`)
- `_nova_exit` -- exit syscall
- `_nova_int_to_str`, `_nova_str_to_int` -- pure logic, target-agnostic

`make smoke-macos` produces `bin/hello_macos`; `file` reports `Mach-O
64-bit executable x86_64`; `llvm-objdump -h` shows `__text` /
`__const` / `__bss` populated; the encoded syscall bytes for write +
exit are present in `__text`.

## Known gaps for next session

- File I/O on macOS (`_nova_read_file` etc.): Darwin's `stat` layout
  differs from Linux. `file_size` already accounts for it
  (`codegen.nova:7271`); `read_file`/`write_file` paths use only
  fd/buf/len so likely work but unverified.
- Sockets, fork, exec, waitpid: syscall numbers mapped but no
  equivalent of `sock_win32.nova` / `pipe_win32.nova` for macOS yet.
- `_nova_time` (228 -> 232), `_nova_sleep_ms` (Darwin
  `__semwait_signal`): wired in `codegen.nova` but unverified.
- Real Darwin run -- only `file`/`llvm-objdump` validation here, no
  XNU available in sandbox.

## End-to-end verified here

```
make smoke-macos    -> bin/hello_macos: Mach-O 64-bit x86_64 executable
make self-host      -> stage2.s == stage3.s
make test           -> all 5 test programs pass
make cross-windows  -> bin/nova.exe still produced (PE32+)
Crossengin-demo make test -> 100 suites still pass
```
