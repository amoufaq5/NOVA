# nova-dap

A Debug Adapter Protocol (DAP) server for the Nova programming
language. Translates DAP JSON messages (over stdio, Content-Length
framed) into `gdb --interpreter=mi3` machine-interface commands and
turns gdb's async records back into DAP events.

This is the **MVP** of `nova-dap`: source-level breakpoints, step
in / over / out, continue, stack traces, and a single Locals scope per
frame. The adapter itself does **no DWARF parsing** — gdb does. The
NOVA compiler already emits a working `.debug_line` section on Linux
ELF (see `DWARF_AUDIT.md` in the repo root and `make smoke-dwarf`).

## What works in this MVP

| DAP request               | Translation                                       |
| ------------------------- | ------------------------------------------------- |
| `initialize`              | Returns the capabilities table (see below).       |
| `launch`                  | Spawns gdb, `-file-exec-and-symbols <program>`, optional `cwd` + `args`. |
| `setBreakpoints`          | `-break-delete` then `-break-insert <src>:<line>` per breakpoint. |
| `setExceptionBreakpoints` | Accepted, no-op (gdb has none for Nova).          |
| `configurationDone`       | `-exec-run` first time; `-exec-continue` thereafter. |
| `threads`                 | Single-thread stub (`id=1`, name=`main`).         |
| `stackTrace`              | `-stack-list-frames`.                             |
| `scopes`                  | One `Locals` scope per frame.                     |
| `variables`               | `-stack-select-frame` then `-stack-list-variables --all-values`. |
| `continue` / `next` / `stepIn` / `stepOut` | `-exec-continue` / `-exec-next` / `-exec-step` / `-exec-finish`. |
| `pause`                   | `-exec-interrupt`.                                |
| `disconnect` / `terminate`| `-gdb-exit` + reap child.                         |

Capabilities advertised:

* `supportsConfigurationDoneRequest: true`
* `supportsStepBack: false`
* `supportsTerminateRequest: true`
* `supportsRestartRequest: false`

Events emitted:

* `initialized`              — after `initialize` succeeds.
* `stopped`                  — translated from gdb's `*stopped`
                                async record (reason mapped to DAP
                                vocabulary: `breakpoint`, `step`,
                                `exception`, ...).
* `output`                   — wraps gdb console/log/target streams.
* `exited` + `terminated`    — translated from `*stopped reason=exited*`
                                or `=thread-group-exited`.

## What does NOT work yet

* **Variable values for Nova locals.** The compiler emits
  `.debug_line` but not `.debug_info` DIE entries for locals, so
  `variables` returns an empty list at NOVA breakpoints today.
  (The request itself succeeds; the list just has no entries.) This
  is a compiler-side gap tracked in `DWARF_AUDIT.md`, not a DAP one.
* **Conditional / hit-count breakpoints.** MI supports them, the
  adapter just doesn't expose them yet.
* **Multi-threaded NOVA.** The DAP server reports a single thread
  (`id=1`); coroutines are not surfaced.
* **`stopOnEntry`.** The MVP runs straight to the first breakpoint.
* **`evaluate` (REPL / hover).** Not wired up.
* **Reverse debugging (`stepBack`).** Out of scope for MVP.

## Layout

```
tools/nova-dap/
  pyproject.toml
  README.md                       (this file)
  nova_dap/
    __init__.py
    __main__.py                   `python -m nova_dap` entry point
    server.py                     DAP request handlers + stdio loop
    gdb_bridge.py                 GdbBridge subprocess wrapper + MI parser
  tests/
    dap_smoke.py                  end-to-end smoke test
```

The implementation is pure stdlib — no third-party Python deps.

## Running the server

Install editable (optional):

```sh
pip install -e tools/nova-dap
nova-dap                      # entry-point installed by pyproject.toml
```

Without install:

```sh
PYTHONPATH=tools/nova-dap python -m nova_dap.server
```

Environment knobs:

| Env var          | Effect                                                |
| ---------------- | ----------------------------------------------------- |
| `NOVA_DAP_LOG`   | If set, append per-message debug logs to this file.   |
| `NOVA_DAP_GDB`   | Override the path to the `gdb` binary (default: `which gdb`). |

## Quick test

After `make smoke-dwarf` (which builds `bin/hello_dwarf` with DWARF
line info), run the end-to-end smoke test:

```sh
python tools/nova-dap/tests/dap_smoke.py
# dap_smoke: OK
#   binary:     /.../NOVA/bin/hello_dwarf
#   source:     /.../NOVA/examples/hello_dwarf.nova
#   breakpoint: line 20 (id=1)
#   top frame:  main at line 20
```

The test sets a breakpoint at `hello_dwarf.nova:20` (the `fn main()`
line), launches gdb under the DAP server, waits for the `stopped`
event, requests `stackTrace` + `scopes` + `variables`, then
continues to `terminated`. If `gdb` or `bin/hello_dwarf` is missing,
the test prints a `SKIP` line and exits 0.

## VS Code integration

The `tools/vscode-nova` extension declares a `nova` debugger type
that points at the `nova-dap` command. A minimal `launch.json`:

```jsonc
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "nova",
      "request": "launch",
      "name": "Debug Nova program",
      "program": "${workspaceFolder}/bin/hello_dwarf",
      "cwd": "${workspaceFolder}",
      "stopOnEntry": false
    }
  ]
}
```

Drop that in `.vscode/launch.json`, hit F5, and set a breakpoint in
any `.nova` source file that was compiled with `make smoke-dwarf`-
style line info. VS Code's breakpoint gutter is enabled for the
`nova` language id via the extension's `contributes.breakpoints`
entry.

## Architecture in one paragraph

`server.py` runs the DAP message loop on stdin/stdout. Each DAP
request is translated to one or more MI commands via
`GdbBridge.command(...)`, which assigns a monotonically-increasing
integer token, writes `<token>-command\n` to gdb's stdin, and blocks
on a `Queue` until the matching `<token>^done,...` (or `^error,...`)
record arrives on stdout. A background reader thread inside
`GdbBridge` parses every MI record using a tiny recursive-descent
parser for the value grammar (`cstring`, `{tuple}`, `[list]`), and
dispatches each parsed record either to its waiting `Queue`
(synchronous result) or to the session's event callback (async
record: `*stopped`, `=thread-group-exited`, etc.). The DAP server
maps the resulting `GdbAsyncRecord` objects to DAP `stopped` /
`exited` / `terminated` events.
