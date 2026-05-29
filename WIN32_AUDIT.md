# NOVA Windows Backend Audit

Phase 12, Tier 1 #2. Snapshot as of branch `claude/festive-franklin-PP7mW`.

## Quick map of the existing Windows backend

- `cg_target == 3` means **Windows (x86-64, PE/COFF, kernel32)**. Numbering
  is `0=linux, 1=macos, 2=wasm, 3=windows, 4=arm64` -- set in
  `src/compiler/compiler.nova` lines 619-642 via `--target=windows`.
- Section names use COFF spelling: `.text,"xr" / .rdata,"dr" / .bss,"bw"`
  (codegen.nova line 175-178).
- Entry point: `mainCRTStartup` (codegen.nova line 3847). argc/argv are
  zeroed because we don't yet parse the lpCommandLine (`GetCommandLineA`)
  on Windows. envp is also zeroed (so `getenv` will return null on
  Windows; functional but degraded).
- Exit: `ExitProcess` via `__imp_ExitProcess` (codegen.nova line 3917, 6558).
- Calling convention: matches Win64 ABI on import calls (`win_call(api)`
  helper at line 239 emits `sub rsp,32 / call [rip + __imp_API] / add rsp,32`
  for shadow space). The 16-byte stack alignment at the call site is mostly
  preserved but **not rigorously verified** -- see "Known gaps" below.

## Builtins with a Windows branch already

These all have working `if cg_target == 3 { ... }` paths in `gen_runtime()`:

| Builtin                | Win32 call(s)                    |
| ---------------------- | -------------------------------- |
| `_nova_print_int`      | GetStdHandle(-11) + WriteFile    |
| `_nova_print_str`      | GetStdHandle(-11) + WriteFile    |
| `_nova_println`        | GetStdHandle(-11) + WriteFile    |
| `_nova_alloc`          | VirtualAlloc (1 MiB chunks)      |
| `_nova_exit`           | ExitProcess                      |
| `_nova_read_file`      | CreateFileA + ReadFile + Close   |
| `_nova_write_file`     | CreateFileA + WriteFile + Close  |
| `_nova_time`           | GetSystemTimeAsFileTime (+ conv) |
| `_nova_sleep_ms`       | Sleep                            |
| `_nova_mkdir`          | CreateDirectoryA                 |
| `_nova_unlink`         | DeleteFileA                      |
| `_nova_file_size`      | CreateFileA + GetFileSizeEx      |
| `_nova_close_fd`       | CloseHandle                      |
| `_nova_read_line`      | GetStdHandle(-10) + ReadFile     |
| `_nova_read_stdin`     | GetStdHandle(-10) + ReadFile     |
| `_nova_random` (seed)  | GetSystemTimeAsFileTime          |

Pure-logic helpers that work as-is on any target (no syscall): `_nova_strlen`,
`_nova_concat`, `_nova_int_to_str`, `_nova_str_eq`, `_nova_strcmp`,
`_nova_str_to_int`, `_nova_chr`, `_nova_char_at`, `_nova_substr`,
`_nova_starts_with`, `_nova_ends_with`, `_nova_str_find`, `_nova_abs`,
`_nova_min`, `_nova_max`, `_nova_list_*`, `_nova_map_*`, `_nova_eq`,
`_nova_neq`, `_nova_lt`, `_nova_gt`, `_nova_le`, `_nova_ge`, `_nova_pop`,
`_nova_push`, `_nova_index`, `_nova_len`, `_nova_contains`,
`_nova_hex`, `_nova_type_of`, `_nova_type_name`, `_nova_debug_print`,
`_nova_throw_impl`, `_nova_assert`, coroutines (`_nova_coro_*`).

## Builtins that are stubbed on Windows (return -1 / no-op)

These have a `cg_target == 3 { mov rax, -1 }` branch but no real
implementation: `_nova_socket`, `_nova_bind`, `_nova_listen`,
`_nova_accept`, `_nova_connect`, `_nova_send_data`, `_nova_recv_data`,
`_nova_fork`, `_nova_waitpid`, `_nova_exec`, `_nova_pipe`. Anything
depending on networking or process control will compile and link on
Windows but won't actually do anything.

## Tooling

- `make cross-windows` runs:
  `bin/nova ... --target=windows -o bin/nova_windows.s` then
  `x86_64-w64-mingw32-as` + `x86_64-w64-mingw32-ld -lkernel32`. It
  produces a real **PE32+ executable** (verified: `file bin/nova.exe`).
- mingw-w64 toolchain (`x86_64-w64-mingw32-as`, `-ld`, `-gcc`) is
  installed in this sandbox and works.
- Wine is installed and works on this sandbox **once `XDG_RUNTIME_DIR`
  points at an existing 0700 directory**. Without it wine bails during
  preloader init with `free(): invalid pointer`. The `smoke-windows`
  Makefile target sets `XDG_RUNTIME_DIR` to `/tmp/xdg-runtime` if the
  caller didn't provide one.

## Known gaps / smells

1. **No CLI args on Windows.** mainCRTStartup zeros argc/argv/envp.
   For self-hosted nova.exe to compile a file the CLI parser falls back
   on default behavior. Fix: call `GetCommandLineA` and parse it into
   argv (custom -- mingw's `__getmainargs` would also work but it pulls
   in msvcrt).
2. **Alignment is best-effort.** Several Win32 paths in `gen_runtime`
   do `push rdx / push rsi / win_call(GetStdHandle) / pop rsi / pop rdx`
   patterns. Most are arithmetically aligned. None have been audited
   against the Win64 ABI requirement that `rsp%16 == 0` at the `call`
   instruction. Real Windows tolerates this in kernel32 stubs; Wine may
   not. Worth doing a sweep.
3. **`_nova_println` aliases the WriteFile source buffer with the
   `BytesWritten` output pointer** (`lea r9, [rbp - 16]` -- same byte
   that holds the `\n`). Works by accident because WriteFile reads
   source before writing the count. Cosmetic bug, not blocking.
4. **Sockets / fork / exec / waitpid / pipe / exec** all stubbed to
   `-1`. Anything CrossEngin-like that uses `tcp_echo_server` or
   subprocess support will not work yet.
5. **Network FFI (`ffi_syscall.nova`)** is hard-coded to Linux syscall
   numbers (asm "syscall" instruction). No Windows analog. Will not
   link if exercised on Windows, but isn't pulled in by hello-world.
6. **Object format pinning.** Codegen emits GAS Intel-syntax assembly
   regardless of target -- the differentiation is only in section
   directives and the import shape. PE-vs-ELF is decided entirely by
   which assembler/linker you invoke afterwards.

## Bottleneck for hello-world

**None remaining.** `examples/hello_win32.nova` compiles, assembles, links,
and **runs cleanly under wine on Linux**. The smoke target prints the
expected output and exits 0:

```
hello from NOVA on Windows
hello, world!
tick 0
tick 1
tick 2
read back: nova roundtrip ok
done
```

End-to-end command (also wrapped as `make smoke-windows`, or
`WINE_OK=1 make smoke-windows` to also run it):

```
bin/nova examples/hello_win32.nova --target=windows -o /tmp/h.s
x86_64-w64-mingw32-as -o /tmp/h.o /tmp/h.s
x86_64-w64-mingw32-ld -o bin/hello_win32.exe /tmp/h.o \
    -L/usr/x86_64-w64-mingw32/lib -lkernel32
XDG_RUNTIME_DIR=/tmp/xdg-runtime wine bin/hello_win32.exe
```

That demonstrates the working pipeline: GetStdHandle + WriteFile (println),
VirtualAlloc (_nova_alloc for concat), CreateFileA + WriteFile + ReadFile +
CloseHandle (write_file / read_file), ExitProcess (clean exit).

The trivial `examples/hello.nova` also now runs end-to-end on Windows:

```
bin/nova examples/hello.nova --target=windows -o /tmp/hello.s
x86_64-w64-mingw32-as -o /tmp/hello.o /tmp/hello.s
x86_64-w64-mingw32-ld -o /tmp/hello.exe /tmp/hello.o \
    -L/usr/x86_64-w64-mingw32/lib -lkernel32
XDG_RUNTIME_DIR=/run/user/0 wine /tmp/hello.exe
# Hello, World!
# This was compiled by the Nova self-hosting compiler.
```

The self-hosted `bin/nova.exe` itself also reaches user code (it prints the
usage banner when invoked without args) -- proving the full mainCRTStartup
-> println -> ExitProcess path. It still page-faults if asked to compile a
file because `argv` is zeroed (see "Known gaps" #1).

## Recommended next steps

1. **Argv parsing** via `GetCommandLineA` + a small in-runtime tokenizer.
   That alone unlocks `nova.exe input.nova -o output.s` working natively.
   Without it, the self-hosted compiler can start but cannot compile
   anything on Windows.
2. **Audit and fix 16-byte stack alignment** systematically at each Win32
   `win_call` site. Add a small assertion-debug toggle that emits
   `test rsp, 15; jnz .die` before each kernel32 call so we can verify
   with a debugger.
3. **Real sockets** via `__imp_WSAStartup / socket / bind / listen /
   accept / send / recv / WSACleanup`. Required before CrossEngin's TCP
   path can run.
4. **`getenv`** by walking the result of `GetEnvironmentStringsA`. Needs
   an `__imp_GetEnvironmentStringsA / __imp_FreeEnvironmentStringsA` pair.
5. **Fix the `_nova_println` r9 alias** (gap #3) -- give the BytesWritten
   slot its own stack slot instead of overlapping the newline buffer.
6. **Verify on real Windows** (not just Wine) before declaring victory.
   The audit doesn't catch the kind of subtle ABI mismatches that
   surface on real kernel32 but get tolerated by Wine.

## Completed in this session

- `examples/hello_win32.nova` (smoke test exercising println, concat,
  int_to_str, loop, write_file/read_file roundtrip).
- `make smoke-windows` (build + optionally `WINE_OK=1` run).
- Wine compatibility unlocked (XDG_RUNTIME_DIR fix).
- Confirmed: both `hello_win32.exe` and `hello.exe` print expected output
  and exit 0 under wine.
- Confirmed: the existing self-hosted `nova.exe` reaches user code
  (prints help banner) when run under wine -- proving the entry-point
  and stdout path are end-to-end functional.
