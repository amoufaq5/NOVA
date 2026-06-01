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

## What works

| DAP request               | Translation                                       |
| ------------------------- | ------------------------------------------------- |
| `initialize`              | Returns the capabilities table (see below).       |
| `launch`                  | Spawns gdb, enables `mi-async` + `non-stop`, `-file-exec-and-symbols <program>`, optional `cwd` + `args`. |
| `setBreakpoints`          | `-break-delete` then `-break-insert <src>:<line>` per breakpoint. |
| `setExceptionBreakpoints` | Accepted, no-op (gdb has none for Nova).          |
| `configurationDone`       | `-exec-run` first time; `-exec-continue --all` thereafter. |
| `threads`                 | Multi-thread: backstop reconcile with `-thread-info`; tracks `=thread-created` / `=thread-exited` for live updates. |
| `stackTrace`              | `-stack-list-frames --thread <threadId>`; frame ids are stable per `(threadId, level)`. |
| `scopes`                  | One `Locals` scope per frame.                     |
| `variables`               | `-thread-select` + `-stack-select-frame` + `-stack-list-variables --all-values` (so per-thread frame chains are isolated). |
| `continue` / `next` / `stepIn` / `stepOut` | `-exec-{continue,next,step,finish}` with `--thread <id>` when DAP carries `singleThread:true`, otherwise `--all`. |
| `pause`                   | `-exec-interrupt --thread <id>` (or `--all`).     |
| `disconnect` / `terminate`| `-gdb-exit` + reap child.                         |

Capabilities advertised:

* `supportsConfigurationDoneRequest: true`
* `supportsStepBack: false`
* `supportsTerminateRequest: true`
* `supportsRestartRequest: false`
* `supportsSingleThreadExecutionRequests: true`
  — every `continue` / `next` / `stepIn` / `stepOut` / `pause`
  honours the DAP `threadId` + `singleThread` arguments. When
  `singleThread:true` the request resumes / stops only the target
  thread; other threads remain in their current state.

Events emitted:

* `initialized`              — after `initialize` succeeds.
* `stopped`                  — translated from gdb's `*stopped`
                                async record. Carries `threadId`
                                (from MI `thread-id`) and
                                `allThreadsStopped` (true iff MI
                                `stopped-threads="all"`).
                                In non-stop mode, a stop on thread A
                                does not affect thread B.
* `continued`                — emitted on `*running,thread-id=...`
                                so DAP clients can show per-thread
                                running state.
* `thread`                   — `started` / `exited` lifecycle events,
                                mapped from `=thread-created` /
                                `=thread-exited`.
* `output`                   — wraps gdb console/log/target streams.
* `exited` + `terminated`    — translated from `*stopped reason=exited*`
                                or `=thread-group-exited`.

## Thread model

The DAP server operates at the gdb-MI layer, so the thread set it
surfaces is whatever gdb reports for the inferior. For a NOVA
program compiled by `bin/nova` today (single OS thread, cooperative
coroutines) `threads` returns one entry (`id=1`, name=`main`) and
all per-thread requests collapse to that thread — the per-thread
wire protocol works, there's just only one thread to address.

When the inferior is linked against pthreads (e.g. a NOVA program
that calls into a C library via FFI, or — for testing — the
`tests/fixtures/multi_thread.c` pthread fixture used by
`tests/dap_multi_thread.py`), gdb reports each LWP as a separate
thread and the adapter exposes them all via the DAP `threads`
request. Each thread can be stepped, paused, and continued
independently.

NOVA-side coroutines are NOT surfaced as DAP threads: they share
a single OS thread, so to gdb they look like ordinary function
calls. A coroutine-aware breakpoint scheme would need NOVA-emitted
DWARF that maps coroutine frames to user-visible thread ids; that
is out of scope for this milestone.

## What does NOT work yet

* **Conditional / hit-count breakpoints.** MI supports them, the
  adapter just doesn't expose them yet.
* **NOVA coroutines as separate DAP threads.** See the thread-model
  note above — coroutines look like ordinary function calls to gdb.
* **`stopOnEntry`.** The adapter runs straight to the first
  breakpoint.
* **`evaluate` (REPL / hover).** Not wired up.
* **Reverse debugging (`stepBack`).** Out of scope.

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
    dap_smoke.py                  end-to-end single-thread smoke test
    dap_multi_thread.py           end-to-end multi-thread coordination test
    fixtures/multi_thread.c       pthread fixture (built on demand by the test)
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
#   breakpoint: line 29 (id=1)
#   top frame:  main at line 30
#   variables:  5 (a, b, sum, scaled, label)
```

The test sets a breakpoint at the `fn main()` line, launches gdb
under the DAP server, waits for the `stopped` event, requests
`stackTrace` + `scopes` + `variables`, then continues to
`terminated`. If `gdb` or `bin/hello_dwarf` is missing, the test
prints a `SKIP` line and exits 0.

For the multi-thread wire protocol there's a second test that
builds a tiny pthread fixture on demand and exercises per-thread
`threads`, `stackTrace`, `variables`, `continue`, `next`, `stepIn`,
`stepOut`, and `pause` with `singleThread:true`:

```sh
python tools/nova-dap/tests/dap_multi_thread.py
# dap_multi_thread: OK
#   fixture:    /tmp/nova_dap_multi_thread
#   source:     /.../tools/nova-dap/tests/fixtures/multi_thread.c
#   threads:    3 reported by `threads` request
#   thread A:   bp at line 53, locals = ['arg', 'i', 'local_a', 'sum_a']
#   thread B:   bp at line 66, locals = ['arg', 'i', 'local_b', 'sum_b']
#   non-stop:   True, singleThread continue ok = True, per-thread step ok = True, per-thread pause ok = True
```

This test SKIPs cleanly if `gcc` (or `pthread`) is unavailable. The
fixture is a small C program because NOVA itself doesn't yet emit
multiple OS threads — see the "Thread model" section above.

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
