# nova-dap (design note, MVP DWARF shipping)

This directory is a placeholder for a future Debug Adapter Protocol
(DAP) implementation for Nova. No DAP server is shipped here yet —
this README documents what would be required and the current state.

**Update — DWARF `.debug_line` MVP is now shipping.** The compiler now
emits a working `.debug_line` section on Linux ELF; see
`/home/user/NOVA/DWARF_AUDIT.md`. GDB can already set source-level
breakpoints on NOVA-compiled binaries today (`make smoke-dwarf`
verifies this). The remaining DAP work (variable inspection, DAP
adapter binary) is still tracked here.

## What DAP would need

A useful Nova DAP adapter would need each of the following in turn:

1. **DWARF debug info in the codegen.** The compiler currently emits
   GAS comments noting source line numbers
   (`# src/foo.nova:42`) but does not emit a `.debug_line` /
   `.debug_info` section. A debugger cannot map machine-code addresses
   back to source positions without DWARF (or an equivalent custom
   sidecar table). This is the single biggest piece of work.
2. **Single-step + breakpoint hooks.** The compiler must reserve a
   well-known interrupt or `int3` slot at each source-line boundary so
   a `SIGTRAP`-based debugger (or `ptrace(2)` consumer) can trap and
   resume. Today the codegen aggressively inlines and reuses registers
   across statements, which means there is no per-line boundary to
   stop at without explicit codegen support.
3. **Variable inspection.** Each frame needs a debug-info entry
   mapping Nova names to stack offsets / register live ranges. This is
   already tracked transiently in `regalloc.nova` but is not preserved
   into the final binary.
4. **Watchpoints.** Hardware watchpoints are best-effort on Linux
   (`DR0..DR3` via `ptrace`), so this is mostly a host-side feature
   once (1) and (3) land.
5. **Adapter binary.** A DAP server (JSON over stdio, similar to the
   LSP server in `tools/nova-lsp/`) translating DAP requests
   (`launch`, `setBreakpoints`, `stackTrace`, `variables`, `evaluate`,
   `continue`, `stepIn`, `stepOver`, `stepOut`) into `ptrace` / `lldb`
   calls. The DAP protocol itself is well-specified; the hard part is
   the underlying debug info, not the adapter.

## Current state

- `nova` codegen: emits GAS `.file 1 ...` + `.loc 1 LINE 0` directives
  on every statement boundary. GAS turns these into a real
  `.debug_line` section on Linux ELF. `make smoke-dwarf` builds
  `bin/hello_dwarf` and verifies `objdump --dwarf=decodedline` and
  `gdb b main / r / where` both work end-to-end.
- `.debug_info` (DWARF DIE entries for variables, parameters, types)
  is NOT yet emitted. Source-level breakpoints work; variable
  inspection on a stopped frame does not.
- `nova-lsp`: ships in this same release with diagnostics + hover.
- `nova-dap`: **DAP server still not implemented** — print-debugging
  + gdb breakpoints are the documented path for this release.

## Estimated effort to MVP DAP

A minimal-but-real DAP (set a breakpoint, step over a line, print a
local variable on a stopped frame) is roughly **1-2 calendar months**
for one engineer:

| Sub-task                                                  | Estimate |
| --------------------------------------------------------- | -------- |
| DWARF `.debug_line` emission in `codegen.nova` (DONE)     | -        |
| DWARF `.debug_info` (function / parameter / locals)       | 2 wk     |
| Per-line breakpoint anchors + `int3` patching             | 1 wk     |
| Variable-location tracking through regalloc               | 2 wk     |
| DAP adapter binary (Python, similar shape to `nova-lsp`)  | 1 wk     |
| End-to-end testing against VS Code's built-in DAP client  | 1 wk     |

That ordering is also the dependency order — there is no point
writing the adapter before DWARF lands, because it would have nothing
to translate.

## Today: print-debugging is the path

Until DAP lands, the supported workflow is:

```nova
println("foo at line 42: x=" + int_to_str(x) + ", y=" + int_to_str(y))
```

The compiler's GAS line comments make it possible to read a
disassembly and find the originating Nova line; that is the closest
thing to a source-level debugger we have today.

See `INSTALL.md` (repo root) for the install paths covered by this
release: the IDE story is **Layer 1 (syntax) + Layer 2 (LSP)**, with
DAP tracked here as future work.
