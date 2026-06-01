# DWARF Debug Info: Status and Scope

The NOVA self-hosted compiler now emits a minimum-viable DWARF
`.debug_line` section on Linux ELF targets. This document captures
the scope of that MVP, what it enables, and what is explicitly
deferred.

## What ships in this MVP

- `.file 1 "<source>.nova"` and `.loc 1 LINE 0` directives are emitted
  for every statement-level AST node by `cg_emit_loc` in
  `src/compiler/codegen.nova`.
- GNU `as` translates these directives into a real `.debug_line`
  section in the produced ELF object file. The linker preserves it
  through `ld -o`.
- The previous 4096-line cap (a parser sanity guard) is raised to
  1,000,000 so the bootstrapped compiler itself (~15k lines combined)
  has full source-line coverage. This is what enables stepping
  through compiler self-hosting builds in GDB.

That is enough for:

- `objdump --dwarf=decodedline <bin>` to print a real PC -> source-line
  table.
- GDB to resolve `b main` / `b file.nova:LINE` to the correct PC.
- GDB to print a meaningful frame in `where` (file + line are
  populated from the .debug_line section).

## What is explicitly deferred

| Surface                          | Status     | Notes                                                                                       |
| -------------------------------- | ---------- | ------------------------------------------------------------------------------------------- |
| `.debug_line` (Linux ELF)        | **Ships**  | This MVP. Verified by `make smoke-dwarf`.                                                   |
| `.debug_info` (variables, types) | Deferred   | Required for `gdb p localvar` to work. See `tools/nova-dap/README.md` for the estimate.     |
| `.debug_frame` / `.eh_frame`     | Partial    | x86-64 ABI requires `.eh_frame` for proper unwinding; today GDB falls back to prologue scan. |
| `.debug_abbrev` / `.debug_str`   | Deferred   | Companion sections that DIEs would reference once `.debug_info` is added.                   |
| Windows PE CodeView (`/Z7`)      | Deferred   | Windows uses CodeView, not DWARF. MinGW `as` will accept `.loc` (best-effort), but a real Windows debugger needs CodeView records emitted via `.cv_loc`. Scope: a future P4 task. |
| macOS Mach-O DWARF (`__debug_*`) | Deferred   | macOS uses DWARF inside Mach-O `__DWARF` segments. Clang's MC parser does accept `.loc` from our Mach-O output, but lldb's expectations are stricter (file-table ordering, accelerator tables). Not exercised by `make smoke-macos`. |
| ARM64 (Linux)                    | Inherits   | The arm64 backend (`lower_arm64.nova`) does not currently call `cg_emit_loc`. Adding the hook is a one-liner; not in this MVP. |
| WASM source maps (`name`/`map`)  | Out of scope | Browser debuggers consume V8/Firefox source maps, not DWARF. Tracked separately in `WASM_AUDIT.md`. |

## Smoke test

`make smoke-dwarf` runs the end-to-end verification:

```
$ make smoke-dwarf
Linux ELF binary: bin/hello_dwarf
bin/hello_dwarf: ELF 64-bit LSB executable, x86-64, ...

--- objdump --dwarf=decodedline bin/hello_dwarf ---

Contents of the .debug_line section:

hello_dwarf.nova:
File name                        Line number    Starting address    View    Stmt
hello_dwarf.nova                          16            0x401000               x
hello_dwarf.nova                          17            0x40100c               x
hello_dwarf.nova                          20            0x40102e               x
...

--- gdb b main / r / where ---
Breakpoint 1, main () at examples/hello_dwarf.nova:20
20	fn main() {
#0  main () at examples/hello_dwarf.nova:20
Hello, DWARF
sum = 3
[Inferior 1 (process N) exited normally]

=== DWARF smoke PASSED ===
```

Source: `examples/hello_dwarf.nova`.

## Why .debug_line is enough for breakpoints

DAP's `setBreakpoints` request takes `{source: {path: ...}, lines:
[N, ...]}` and expects the adapter to translate each line to a PC.
That translation is exactly the `.debug_line` table. So with this
MVP shipped:

- A DAP server (when written) can already implement
  `setBreakpoints`, `stackTrace` (PC->line), `continue`, and `next`
  (single-step until the line changes).
- Without `.debug_info` it cannot yet implement `variables` /
  `evaluate` against live frames — those rely on DIEs mapping names
  to register/stack locations.

The DAP work is still tracked in `tools/nova-dap/README.md`. The
DWARF blocker for `setBreakpoints` is now cleared.

## Cross-reference

- `src/compiler/codegen.nova` — `cg_emit_loc` and `gen_function`
  emit the directives.
- `examples/hello_dwarf.nova` — the smoke test source.
- `Makefile` — `smoke-dwarf` target.
- `tools/nova-dap/README.md` — DAP design note; this update closes
  the `.debug_line` line item.
- `STABILITY_AUDIT.md` / `WIN32_AUDIT.md` — also track the
  `setsid`/`signal` stub-to-syscall work shipping in the same
  commit.

## What changed in this MVP (vs. pre-MVP)

| File                              | Change                                                                                       |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| `src/compiler/codegen.nova`       | Raise the `.loc` line cap from 4096 to 1,000,000. Add docstring. New `_nova_setsid/_nova_getpid/_nova_kill_proc/_nova_raise_sig/_nova_signal_install` runtime helpers (Part 1 of the same commit). |
| `examples/hello_dwarf.nova`       | NEW — smoke test source.                                                                     |
| `Makefile`                        | NEW `smoke-dwarf` target running objdump + gdb verification.                                 |
| `tools/nova-dap/README.md`        | Updated status: `.debug_line` ships.                                                          |
| `DWARF_AUDIT.md`                  | NEW — this file.                                                                              |
