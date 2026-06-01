# nova-dap

A Debug Adapter Protocol (DAP) server for the Nova programming
language. Translates DAP JSON messages (over stdio, Content-Length
framed) into `gdb --interpreter=mi3` machine-interface commands and
turns gdb's async records back into DAP events.

This is the **MVP** of `nova-dap`: source-level breakpoints (incl.
conditional + data breakpoints / watchpoints), step in / over / out,
continue, stack traces, evaluate (watch / REPL / hover), and a single
Locals scope per frame. The adapter itself does **no DWARF parsing**
— gdb does. The NOVA compiler already emits a working `.debug_line`
section on Linux ELF (see `DWARF_AUDIT.md` in the repo root and
`make smoke-dwarf`).

## What works

| DAP request               | Translation                                       |
| ------------------------- | ------------------------------------------------- |
| `initialize`              | Returns the capabilities table (see below).       |
| `launch`                  | Spawns gdb, enables `mi-async` + `non-stop`, `-file-exec-and-symbols <program>`, optional `cwd` + `args`. |
| `setBreakpoints`          | `-break-delete` then `-break-insert <src>:<line>` per breakpoint. Conditional breakpoints (`condition: "x > 5"`) are forwarded via `-break-insert -c "<expr>"`. |
| `setExceptionBreakpoints` | Accepted, no-op (gdb has none for Nova).          |
| `configurationDone`       | `-exec-run` first time; `-exec-continue --all` thereafter. |
| `threads`                 | Multi-thread: backstop reconcile with `-thread-info`; tracks `=thread-created` / `=thread-exited` for live updates. |
| `stackTrace`              | `-stack-list-frames --thread <threadId>`; frame ids are stable per `(threadId, level)`. |
| `scopes`                  | One `Locals` scope per frame.                     |
| `variables`               | `-thread-select` + `-stack-select-frame` + `-stack-list-variables --all-values` (so per-thread frame chains are isolated). |
| `evaluate`                | `-data-evaluate-expression --thread <id> --frame <level> "<expr>"`; result string decoded into `{result, type}` where type is `int` / `str` / `char` / `bool` / `ptr` / `raw`. Used for watch panel, REPL, and hover tooltips. |
| `dataBreakpointInfo`      | Returns `{dataId, description, accessTypes: ["write", "readWrite"], canPersist: false}` for a named variable. `dataId` is a base64-encoded JSON envelope `{n, f?, v?}` carrying the variable name + frame id so `setDataBreakpoints` can round-trip it without server-side state. |
| `setDataBreakpoints`      | Tears down prior watchpoints via `-break-delete <id>` (per id, so source breakpoints are preserved) and installs gdb hardware watchpoints via `-break-watch <expr>` (write), `-break-watch -r <expr>` (read), or `-break-watch -a <expr>` (rw). Watchpoint hits surface as `stopped` events with `reason: "data breakpoint"` and a description like `Variable 'counter' changed (write): 5 -> 6`. |
| `continue` / `next` / `stepIn` / `stepOut` | `-exec-{continue,next,step,finish}` with `--thread <id>` when DAP carries `singleThread:true`, otherwise `--all`. |
| `pause`                   | `-exec-interrupt --thread <id>` (or `--all`).     |
| `disconnect` / `terminate`| `-gdb-exit` + reap child.                         |

Capabilities advertised:

* `supportsConfigurationDoneRequest: true`
* `supportsStepBack: false`
* `supportsTerminateRequest: true`
* `supportsRestartRequest: false`
* `supportsConditionalBreakpoints: true`
  — `setBreakpoints` honours the per-breakpoint `condition` string,
  forwarded verbatim to gdb's `-break-insert -c "<expr>"`. gdb only
  fires the breakpoint when the expression is non-zero, so a loop
  with `condition: "x > 5"` skips iterations where `x <= 5`.
* `supportsEvaluateForHovers: true`
  — `evaluate` accepts `context: "watch" | "repl" | "hover"`. All
  three route through the same gdb path; hover is safe because
  `-data-evaluate-expression` is side-effect-free for plain reads.
* `supportsSingleThreadExecutionRequests: true`
  — every `continue` / `next` / `stepIn` / `stepOut` / `pause`
  honours the DAP `threadId` + `singleThread` arguments. When
  `singleThread:true` the request resumes / stops only the target
  thread; other threads remain in their current state.
* `supportsDataBreakpoints: true`
  — `dataBreakpointInfo` + `setDataBreakpoints` are wired up to
  gdb's `-break-watch` (write), `-break-watch -r` (read), and
  `-break-watch -a` (read+write) MI commands. The server
  advertises `write` + `readWrite` as the supported access types
  (pure read watchpoints depend on x86 debug-register semantics
  that aren't universal). A watchpoint hit emits a `stopped` event
  with `reason: "data breakpoint"`, `hitBreakpointIds: [<id>]`,
  and a `description` carrying the before / after values
  (`Variable 'counter' changed (write): 5 -> 6`).

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

* **Hit-count breakpoints.** Conditional breakpoints are supported
  (gdb's `-break-insert -c`), hit-count (`-break-insert -i N` /
  `ignore N`) is not yet plumbed.
* **NOVA coroutines as separate DAP threads.** See the thread-model
  note above — coroutines look like ordinary function calls to gdb.
* **`stopOnEntry`.** The adapter runs straight to the first
  breakpoint.
* **Structured `evaluate` results.** Today `evaluate` returns a flat
  `{result, type}` pair with `variablesReference: 0`. NOVA list /
  struct / map values come back as bare pointer strings; expanding
  them inline in the watch panel needs a debugger-side reader of
  the smart-op runtime headers — deferred.
* **Reverse debugging (`stepBack`).** Out of scope.

## Layout

```
tools/nova-dap/
  pyproject.toml
  README.md                              (this file)
  nova_dap/
    __init__.py
    __main__.py                          `python -m nova_dap` entry point
    server.py                            DAP request handlers + stdio loop
    gdb_bridge.py                        GdbBridge subprocess wrapper + MI parser
    evaluator.py                         `-data-evaluate-expression` wrapper +
                                          gdb-output decoder (int/str/char/ptr).
    watchpoints.py                       Data breakpoints (watchpoints): dataId
                                          encoding, access-type mapping, gdb
                                          `-break-watch` command builder,
                                          `WatchpointManager` + stop classifier.
  tests/
    dap_smoke.py                         end-to-end single-thread smoke test
    dap_multi_thread.py                  end-to-end multi-thread coordination test
    test_evaluate.py                     evaluate request: decoder + REPL/watch/hover
    test_conditional_breakpoint.py       conditional `setBreakpoints` with `condition`
    test_data_breakpoints.py             data breakpoints / watchpoints (dataBreakpointInfo + setDataBreakpoints)
    fixtures/multi_thread.c              pthread fixture (built on demand by the test)
```

The `nova_dap.watchpoints` module is a small (~250 line) helper that
covers four concerns:

| Helper                       | What it does                                              |
| ---------------------------- | --------------------------------------------------------- |
| `encode_data_id` / `decode_data_id` | base64url-encoded JSON envelope `{n, f?, v?}` so a DAP `dataId` round-trips name + frame id + varRef without server-side state. |
| `access_type_flag`           | DAP `accessType` (`"write"` / `"read"` / `"readWrite"`) -> gdb `-break-watch` flag (`""` / `"-r"` / `"-a"`). |
| `build_watch_command`        | Composes the full MI command string for the watch insert. |
| `WatchpointManager`          | Thread-safe registry of active watchpoints; the `Session` holds one and resets it on each `launch`. |
| `is_watchpoint_stop`, `extract_watch_values`, `describe_watch_change`, `parse_watchpoint_id` | gdb `*stopped` record classification + value extraction + DAP description builder. |

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

For the watch / REPL / hover evaluate path:

```sh
python tools/nova-dap/tests/test_evaluate.py
# test_evaluate: OK
#   decoder assertions: 57
#   total assertions:   100
#   end-to-end:        ok (43 extra checks)
```

For conditional breakpoints:

```sh
python tools/nova-dap/tests/test_conditional_breakpoint.py
# test_conditional_breakpoint: OK
#   unit assertions:    15
#   total assertions:   54
#   end-to-end:         ok (39 extra checks)
```

For data breakpoints (watchpoints):

```sh
python tools/nova-dap/tests/test_data_breakpoints.py
# test_data_breakpoints: OK
#   unit assertions:    98
#   total assertions:   131
#   end-to-end:         ok (3 watchpoint stops) (33 extra checks)
#   NOVA integration:   ok
```

All four tests have a pure-Python phase that runs anywhere (no gdb
required) plus an end-to-end phase that SKIPs cleanly when `gdb` /
`gcc` are missing.

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
