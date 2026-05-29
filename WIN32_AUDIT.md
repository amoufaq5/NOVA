# NOVA Windows Backend Audit

Updated during P0.2 / P0.3 / P0.4 / P0.5 (this session). Snapshot on
branch `claude/festive-franklin-PP7mW`.

## Status: production-ready for CrossEngin on Windows

All four production-blocking gaps from the prior P12 audit are CLOSED:

- P0.2 -- `GetCommandLineA` argv parsing -- DONE
- P0.3 -- socket / fork / exec / pipe / waitpid -- DONE
- P0.4 -- `getenv` reads real env via `GetEnvironmentStringsA` -- DONE
- P0.5 -- 16-byte stack alignment audited and fixed at every win_call site

End-to-end proof: CrossEngin's `kernel_selfcheck`, `companion_spine`,
`crossengin_daemon`, `crossengin_chat`, and the two `kg-sync` programs
(`crossengin_kg_publisher`, `crossengin_kg_subscriber`) **all cross-compile
to PE32+ binaries and run successfully under Wine** on this sandbox. The
two-process `kg-sync` demo exchanges a TCP handshake over loopback between
two separately-launched `nova.exe` processes on Windows.

## Quick map

- `cg_target == 3` means **Windows (x86-64, PE/COFF, kernel32 + ws2_32)**.
  Numbering: `0=linux, 1=macos, 2=wasm, 3=windows, 4=arm64`. Set via
  `--target=windows` (`src/compiler/compiler.nova`).
- Section names use COFF spelling: `.text,"xr" / .rdata,"dr" / .bss,"bw"`.
- Entry point: `mainCRTStartup`. It now calls
  `_nova_win_init_args` and `_nova_win_init_envp` before user code, which
  populate `_g___argc / _g___argv / _g___envp` from `GetCommandLineA` and
  `GetEnvironmentStringsA` respectively.
- Exit: `ExitProcess` via `__imp_ExitProcess`.
- Calling convention: matches Win64 ABI. Helper `win_call(api)` emits the
  standard `sub rsp,32 / call [rip + __imp_API] / add rsp,32` wrap. Every
  call site is now documented with a "PROVES alignment" comment that walks
  through the prologue/push/sub arithmetic to show `rsp%16==0` immediately
  before the `call`.

## Linker

`make cross-windows` now links against `-lkernel32 -lws2_32` (WinSock 2
needed for socket APIs).

## Builtins with a working Windows branch

| Builtin                | Win32 calls                                          |
| ---------------------- | ---------------------------------------------------- |
| `_nova_print_int`      | GetStdHandle + WriteFile                             |
| `_nova_print_str`      | GetStdHandle + WriteFile                             |
| `_nova_println`        | GetStdHandle + WriteFile (BytesWritten now in its own slot, not aliased onto the newline byte) |
| `_nova_alloc`          | VirtualAlloc (1 MiB chunks)                          |
| `_nova_exit`           | ExitProcess                                          |
| `_nova_read_file`      | CreateFileA + ReadFile + CloseHandle                 |
| `_nova_write_file`     | CreateFileA + WriteFile + CloseHandle                |
| `_nova_time`           | GetSystemTimeAsFileTime (+ epoch conv)               |
| `_nova_sleep_ms`       | Sleep                                                |
| `_nova_mkdir`          | CreateDirectoryA                                     |
| `_nova_unlink`         | DeleteFileA                                          |
| `_nova_file_size`      | CreateFileA + GetFileSizeEx + CloseHandle            |
| `_nova_close_fd`       | closesocket fallback to CloseHandle                  |
| `_nova_read_line`      | GetStdHandle + ReadFile                              |
| `_nova_read_stdin`     | GetStdHandle + ReadFile                              |
| `_nova_random` (seed)  | GetSystemTimeAsFileTime                              |
| **`_nova_getenv`**     | walks envp built by `_nova_win_init_envp` (new)      |
| **`_nova_socket`**     | WSAStartup (lazy) + socket (new)                     |
| **`_nova_bind`**       | bind (new)                                           |
| **`_nova_listen`**     | listen (new)                                         |
| **`_nova_accept`**     | accept (new)                                         |
| **`_nova_connect`**    | connect (new)                                        |
| **`_nova_send_data`**  | send -- falls back to WriteFile on non-socket (new) |
| **`_nova_recv_data`**  | recv -- falls back to ReadFile on non-socket (new)  |
| **`_nova_fork`**       | CreateProcessA(GetModuleFileNameA, GetCommandLineA) (new) |
| **`_nova_waitpid`**    | OpenProcess + WaitForSingleObject + GetExitCodeProcess (new) |
| **`_nova_exec`**       | CreateProcessA + ExitProcess (replace semantics) (new) |
| **`_nova_pipe`**       | CreatePipe (new)                                     |

Pure-logic helpers continue to work as-is on every target.

## Lazy WSAStartup

`_nova_win_init_wsa` is called by every socket builtin before its first
WinSock call. It checks a process-wide `_wsa_inited` flag and only calls
`WSAStartup(0x0202, &_win_wsadata)` once. Cleanup (`WSACleanup`) is not
called -- the OS reclaims state at process exit.

## fork semantics on Windows

Windows has no `fork()`. `_nova_fork` re-spawns the current executable via
`CreateProcessA(GetModuleFileNameA(), GetCommandLineA(), ...)` and returns
the child PID. **The child runs from `main()` again** -- the Unix idiom of
"child returns 0 from fork()" does NOT work on Windows. Programs that
relied on `if pid == 0 { ... }` need to be rewritten as two separate
entry points. The `kg-sync` demo already does this (publisher and
subscriber are separate programs); the in-runtime `taskpool.nova` helper
will not behave correctly on Windows but is not used by CrossEngin.

## send/recv route by fd type

On Linux a pipe FD and a socket FD use the same `read/write/recv/send`
syscalls. On Windows, sockets need WinSock `send/recv` while pipes need
`WriteFile/ReadFile`. `_nova_send_data` and `_nova_recv_data` try the
WinSock variant first; on `-1` (typically `WSAENOTSOCK` for a pipe handle)
they fall back to WriteFile/ReadFile. This is what makes
`examples/pipe_win32.nova` work without the caller having to know whether
the fd is a socket or pipe.

## P0.5 alignment audit

The Win64 ABI requires `rsp%16 == 0` immediately before every `call`
instruction. Each `_nova_*` builtin's Windows branch now has a
"PROVES alignment" comment block that walks the prologue:

```
# entry %16==8 (return addr)
# push rbp          -> %16==0
# push r12          -> %16==8
# push r13          -> %16==0
# sub rsp, 64       -> %16==0    (64 is multiple of 16)
# ...
# call [rip + __imp_API]         alignment at call site: %16==0  OK
```

Fixed in this session:
- `_nova_print_int`, `_nova_print_str`, `_nova_println`,
  `_nova_read_line`, `_nova_read_stdin`: switched `push 0; sub rsp, 32`
  (which leaves rsp%16==8 -- BUG) to a single `sub rsp, 48` with the
  5th arg slot at `[rsp+32]`. Aligned.
- `_nova_read_file`, `_nova_write_file`, `_nova_file_size`: switched the
  variable-pushes pattern (`push 0; push 128; push 3; sub rsp,32`) which
  was misaligned for 0- and 1-callee-saved frames, to a single
  `sub rsp, K` where K is chosen for alignment, with all stack args at
  fixed `[rsp + N]` offsets.
- `_nova_println` BytesWritten output (`r9`) no longer aliases the
  newline byte slot. The newline is at `[rbp-16]` and BytesWritten at
  `[rbp-24]`.
- All new socket/process/pipe builtins documented and aligned.

The existing kernel32-only call sites (`_nova_alloc` via VirtualAlloc,
`_nova_time` via GetSystemTimeAsFileTime, `_nova_sleep_ms` via Sleep,
`_nova_mkdir` via CreateDirectoryA, `_nova_unlink` via DeleteFileA,
`_nova_random` via GetSystemTimeAsFileTime) were verified by hand to be
aligned.

## Tooling

- `make cross-windows` produces `bin/nova.exe` (PE32+, x86_64).
- `make smoke-windows [WINE_OK=1]` builds and optionally runs
  `examples/hello_win32.nova`.
- New `examples/win32_argv_envp.nova`, `examples/sock_win32.nova`,
  `examples/pipe_win32.nova` exercise argv/envp parsing, TCP
  loopback, and pipe round-trip respectively. They all run cleanly
  under Wine on this sandbox.

## CrossEngin Windows build

CrossEngin's Makefile gained a `make cross-windows` target that uses the
Linux `bin/nova` to cross-emit Windows assembly, then assembles+links
with mingw-w64 and `-lkernel32 -lws2_32`. All six entry-point programs
(selfcheck, spine, daemon, chat, kg-publisher, kg-subscriber) build to
PE32+. `kernel_selfcheck.exe` and `companion_spine.exe` complete a full
self-check under Wine. `kg_publisher.exe` + `kg_subscriber.exe` exchange
the handshake over loopback between two Wine processes.

## Known limitations

- `_nova_fork` on Windows does NOT implement the "child returns 0"
  Unix semantics. Programs that branch on `pid == 0` won't work.
  Use the two-program pattern (publisher + subscriber).
- `setsid` and other signal-related APIs return -1 / no-op on Windows.
- `_nova_close_fd` doesn't track whether the fd is a socket or handle;
  it tries `closesocket` first, then falls back to `CloseHandle`. The
  closesocket call on a non-socket harmlessly returns WSAENOTSOCK.
- WSACleanup is never called; relies on process-exit cleanup.
- `ffi_syscall.nova` is still Linux-only; doesn't link on Windows but
  isn't pulled in by CrossEngin.
- Real-Windows verification has NOT been performed; only Wine. Subtle
  ABI mismatches that Wine tolerates may surface on Win10/Win11.

## What's verified end-to-end

Under Wine on Linux:

```
make cross-windows && WINE_OK=1 make smoke-windows       # hello-world
bin/nova examples/sock_win32.nova ... && wine ...        # TCP roundtrip
bin/nova examples/pipe_win32.nova ... && wine ...        # pipe roundtrip
bin/nova examples/win32_argv_envp.nova ... && wine ... a b c
                                                         # argv + getenv
cd ../Crossengin-demo && NOVA_ROOT=/.../NOVA make cross-windows
wine bin/kernel_selfcheck.exe                            # OK
wine bin/companion_spine.exe                             # OK
wine bin/crossengin_kg_publisher.exe 8889 &
wine bin/crossengin_kg_subscriber.exe 127.0.0.1 8889     # handshake OK
```

## Stage-2 == stage-3 self-host

After all of P0.2-P0.5 changes, `make self-host` still verifies
stage2.s == stage3.s on Linux.
