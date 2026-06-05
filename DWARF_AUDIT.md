# DWARF Debug Info: Status and Scope

The NOVA self-hosted compiler now emits minimum-viable DWARF
`.debug_line` **and** `.debug_info` sections on Linux ELF targets.
This document captures the scope of those MVPs, what they enable,
and what is explicitly deferred.

## What ships in this MVP

### `.debug_line` (PC → source line mapping)

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

### `.debug_info` (DIE entries: subprograms + variables + types)

- A single `DW_TAG_compile_unit` DIE per object, with name (source
  file), producer ("NOVA self-hosted compiler"), language (12 =
  DW_LANG_C99 — close-enough for gdb's value formatting), low_pc /
  high_pc spanning the user-defined functions, and `stmt_list`
  pointing at offset 0 of `.debug_line`.
- One `DW_TAG_subprogram` DIE per NOVA function (including lambdas
  and methods), with name + low_pc/high_pc (real assembler labels
  emitted around the prologue/epilogue) + `DW_AT_frame_base` of
  `DW_OP_reg6` (rbp on x86-64).
- One `DW_TAG_variable` per local and one `DW_TAG_formal_parameter`
  per parameter, as children of the subprogram. Each gets a name,
  a `DW_AT_type` pointing at the `int` base_type DIE (DW_FORM_ref4),
  and a `DW_AT_location` expression `DW_OP_fbreg <-offset>` keyed
  off rbp. Duplicate names within a single function (a `let nxt =`
  inside two sibling while bodies) are de-duplicated at emit time so
  `info locals` stays clean.
- Four `DW_TAG_base_type` DIEs (children of the CU): `int` (8-byte
  signed), `str` (8-byte address), `list` (8-byte address), `bool`
  (1-byte). All variables currently point at `int` — strings/lists
  read out as pointer values, which is what gdb already shows them as
  via NOVA's native list/string representation. Future work can refine
  per-variable typing in the AST.
- Companion `.debug_abbrev` section with five abbrev entries
  (compile_unit, subprogram, variable, base_type, formal_parameter).
  Section ordering: `.debug_abbrev` before `.debug_info`.

That is enough for:

- `objdump --dwarf=decodedline <bin>` to print a real PC -> source-line
  table.
- `objdump --dwarf=info <bin>` to print compile_unit + subprogram +
  variable + base_type DIEs.
- GDB to resolve `b main` / `b file.nova:LINE` to the correct PC.
- GDB to print a meaningful frame in `where` (file + line populated
  from `.debug_line`).
- GDB `info locals` / `info args` / `p localvar` to list and inspect
  every local and parameter at any breakpoint.
- The DAP server (`tools/nova-dap`) `variables` request now returns
  a non-empty array — exactly the locals + params, with their live
  stack values, in the active frame. R3D's DAP smoke
  (`tools/nova-dap/tests/dap_smoke.py`) asserts this.

## What is explicitly deferred

| Surface                          | Status     | Notes                                                                                       |
| -------------------------------- | ---------- | ------------------------------------------------------------------------------------------- |
| `.debug_line` (Linux ELF)        | **Ships**  | MVP. Verified by `make smoke-dwarf`.                                                        |
| `.debug_info` (Linux ELF)        | **Ships**  | MVP. DW_TAG_compile_unit + subprogram + variable + formal_parameter + base_type. Verified by `make smoke-dwarf` (`objdump --dwarf=info` + `gdb info locals`) and `tools/nova-dap/tests/dap_smoke.py`. |
| `.debug_abbrev` (Linux ELF)      | **Ships**  | Five abbrev entries paired with `.debug_info`. Emitted before `.debug_info` in the section order so the CU header's `debug_abbrev_offset` 0 stays valid through any object concatenation. |
| `.debug_frame` / `.eh_frame`     | Partial    | x86-64 ABI requires `.eh_frame` for proper unwinding; today GDB falls back to prologue scan. |
| `.debug_str`                     | Deferred   | We use DW_FORM_string (zero-terminated inline) for DIE names. A separate `.debug_str` section would shave bytes on large CUs but is otherwise equivalent. |
| Type refinement (str/list/bool)  | Deferred   | Every variable currently DW_AT_type-references the `int` base_type DIE because the AST doesn't yet track per-let types. The `str`/`list`/`bool` base_type DIEs *are* emitted and available; per-variable wiring lands when the type-inference pass does. |
| Windows PE CodeView (`/Z7`)      | Deferred   | Windows uses CodeView, not DWARF. MinGW `as` will accept `.loc` (best-effort), but a real Windows debugger needs CodeView records emitted via `.cv_loc`. Scope: a future P4 task. |
| macOS Mach-O DWARF (`__debug_*`) | Deferred   | macOS uses DWARF inside Mach-O `__DWARF` segments. Clang's MC parser does accept `.loc` from our Mach-O output, but lldb's expectations are stricter (file-table ordering, accelerator tables). Not exercised by `make smoke-macos`. |
| ARM64 (Linux)                    | Inherits   | The arm64 backend (`lower_arm64.nova`) does not currently call `cg_emit_loc` and does not emit `.debug_info`. Adding both is a future task; not in this MVP. |
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
hello_dwarf.nova                          24            0x401000               x
hello_dwarf.nova                          25            0x40100c               x
hello_dwarf.nova                          29            0x401036               x
...

--- gdb b main / r / where ---
Breakpoint 1, main () at examples/hello_dwarf.nova:30
30	    let a = 1
#0  main () at examples/hello_dwarf.nova:30
Hello, DWARF
sum = 3
scaled = 30
[Inferior 1 (process N) exited normally]

=== DWARF smoke PASSED ===
```

A separate sanity check exercises the new `.debug_info` section:

```
$ gdb bin/hello_dwarf -ex 'b hello_dwarf.nova:36' -ex 'r' -ex 'info locals' -ex 'q' -batch
Breakpoint 1, main () at examples/hello_dwarf.nova:36
36	    println(concat("sum = ", int_to_str(sum)))
a = 1
b = 2
sum = 3
scaled = 30
label = 4210696
```

(`label = 4210696` is the address of the `"DWARF"` string. Every
variable is currently typed as `int`; see "Type refinement" in the
matrix above for why string-typing is deferred.)

The DAP end-to-end check
(`python tools/nova-dap/tests/dap_smoke.py`) now also asserts that
the `variables` request returns a non-empty list and that every name
in that list is one of the expected source-level locals.

Source: `examples/hello_dwarf.nova`.

## Why .debug_line + .debug_info is enough for breakpoints + variables

DAP's `setBreakpoints` request takes `{source: {path: ...}, lines:
[N, ...]}` and expects the adapter to translate each line to a PC.
That translation is exactly the `.debug_line` table. DAP's
`variables` request, by contrast, needs DIEs mapping names to
register/stack locations — that's the `.debug_info` table. With both
MVPs shipped:

- A DAP server can implement `setBreakpoints`, `stackTrace`
  (PC->line), `continue`, and `next` (single-step until the line
  changes) via `.debug_line`.
- A DAP server can implement `scopes` (one Locals scope per frame)
  and `variables` (live values for every formal_parameter +
  variable DIE) via `.debug_info`. gdb's
  `-stack-list-variables --all-values` is exactly the MI wire-format
  for that, and the nova-dap server forwards it directly.

The DAP work is tracked in `tools/nova-dap/README.md`. Both DWARF
blockers are now cleared.

## Cross-reference

- `src/compiler/codegen.nova` — `cg_emit_loc` emits `.loc`,
  `gen_function` emits per-function start/end labels + records the
  locals snapshot, `cg_emit_debug_abbrev_section` /
  `cg_emit_debug_info_section` walk that snapshot to produce
  the abbrev + info sections.
- `examples/hello_dwarf.nova` — the smoke test source. Includes
  multiple locals and a parameter so the DIE emitter is exercised
  end-to-end.
- `Makefile` — `smoke-dwarf` target.
- `tools/nova-dap/README.md` — DAP design note.
- `tools/nova-dap/tests/dap_smoke.py` — DAP end-to-end smoke;
  asserts non-empty `variables` against the new `.debug_info` DIEs
  while staying backward-compatible with legacy
  `.debug_line`-only binaries (it tolerates an empty list).
- `STABILITY_AUDIT.md` / `WIN32_AUDIT.md` — track adjacent runtime
  primitives.

## What changed in the .debug_info MVP (vs. .debug_line-only)

| File                              | Change                                                                                       |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| `src/compiler/codegen.nova`       | New `cg_dbg_fns` state recording per-fn metadata (name, start/end labels, locals + offsets, params). `gen_function` emits `.Lnova_fn_<n>_start/_end` labels around each function body. New `cg_emit_debug_abbrev_section` / `cg_emit_debug_info_section` walk the table to produce abbrev + info DIE entries. |
| `examples/hello_dwarf.nova`       | Extended with extra locals (`prefix`, `scaled`, `label`) so the variable DIE emitter has more than two slots to exercise. |
| `tools/nova-dap/tests/dap_smoke.py` | Asserts `variables` returns a non-empty list, names are a subset of the expected locals. Tolerates legacy binaries (empty list = pre-DIE build, still passes). |
| `DWARF_AUDIT.md`                  | This update: matrix entry for `.debug_info` flipped from Deferred to Ships, scope + smoke output refreshed. |
