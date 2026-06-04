# nova-dap

A Debug Adapter Protocol (DAP) server for the Nova programming
language. Translates DAP JSON messages (over stdio, Content-Length
framed) into `gdb --interpreter=mi3` machine-interface commands and
turns gdb's async records back into DAP events.

This is the **MVP** of `nova-dap`: source-level breakpoints (incl.
conditional + data breakpoints / watchpoints + function breakpoints
by name + instruction breakpoints by machine address), step in /
over / out at line OR instruction granularity, continue, stack
traces, evaluate (watch / REPL / hover), disassembly view, and a
single Locals scope per frame. The adapter itself does **no DWARF
parsing** — gdb does. The NOVA compiler already emits a working
`.debug_line` section on Linux ELF (see `DWARF_AUDIT.md` in the
repo root and `make smoke-dwarf`).

## What works

| DAP request               | Translation                                       |
| ------------------------- | ------------------------------------------------- |
| `initialize`              | Returns the capabilities table (see below).       |
| `launch`                  | Spawns gdb, enables `mi-async` + `non-stop`, `-file-exec-and-symbols <program>`, optional `cwd` + `args`. |
| `setBreakpoints`          | `-break-delete` then `-break-insert <src>:<line>` per breakpoint. Conditional breakpoints (`condition: "x > 5"`) are forwarded via `-break-insert -c "<expr>"`. |
| `setFunctionBreakpoints`  | Tears down prior function bps via `-break-delete <id>` (per id, so source-line breakpoints + watchpoints survive) and installs `-break-insert [-c "<expr>"] "<name>"` per entry. Names gdb can't resolve come back `verified: false` with the gdb error in `message`. Hits surface as `stopped` events with `reason: "function breakpoint"` and `description: "Entry to <name>"`. |
| `setExceptionBreakpoints` | Accepted, no-op (gdb has none for Nova).          |
| `configurationDone`       | `-exec-run` first time; `-exec-continue --all` thereafter. |
| `threads`                 | Multi-thread: backstop reconcile with `-thread-info`; tracks `=thread-created` / `=thread-exited` for live updates. |
| `stackTrace`              | `-stack-list-frames --thread <threadId>`; frame ids are stable per `(threadId, level)`. |
| `scopes`                  | One `Locals` scope per frame.                     |
| `variables`               | `-thread-select` + `-stack-select-frame` + `-stack-list-variables --all-values` (so per-thread frame chains are isolated). |
| `evaluate`                | `-data-evaluate-expression --thread <id> --frame <level> "<expr>"`; result string decoded into `{result, type}` where type is `int` / `str` / `char` / `bool` / `ptr` / `raw`. Used for watch panel, REPL, and hover tooltips. |
| `dataBreakpointInfo`      | Returns `{dataId, description, accessTypes: ["write", "readWrite"], canPersist: false}` for a named variable. `dataId` is a base64-encoded JSON envelope `{n, f?, v?}` carrying the variable name + frame id so `setDataBreakpoints` can round-trip it without server-side state. |
| `setDataBreakpoints`      | Tears down prior watchpoints via `-break-delete <id>` (per id, so source breakpoints are preserved) and installs gdb hardware watchpoints via `-break-watch <expr>` (write), `-break-watch -r <expr>` (read), or `-break-watch -a <expr>` (rw). Watchpoint hits surface as `stopped` events with `reason: "data breakpoint"` and a description like `Variable 'counter' changed (write): 5 -> 6`. |
| `setInstructionBreakpoints` | Tears down prior instruction bps via `-break-delete <id>` (per id, so source-line / function / data breakpoints survive) and installs `-break-insert *0xADDR` per entry. Each entry carries `{instructionReference: "0xADDR", offset?, condition?}`; we add `offset` to the parsed address before the gdb call. Hits surface as `stopped` events with `reason: "instruction breakpoint"` and a description like `Stopped at instruction 0x401045`. |
| `disassemble`             | `-data-disassemble -s <start> -e <end> -- 0` — disassembles a range of memory around `memoryReference`, returning `DisassembledInstruction[]` of length exactly `instructionCount` (padded with `??` placeholders if gdb returns fewer). `instructionOffset` and byte `offset` shift the start address (we approximate 4 bytes/insn for instruction offsets). |
| `continue` / `next` / `stepIn` / `stepOut` | `-exec-{continue,next,step,finish}` with `--thread <id>` when DAP carries `singleThread:true`, otherwise `--all`. When `granularity: "instruction"` is supplied, `next` -> `-exec-next-instruction` and `stepIn` -> `-exec-step-instruction` so the IDE's disassembly view can advance the PC by exactly one machine instruction. (`stepOut` keeps `-exec-finish` regardless — gdb has no per-instruction finish variant.) |
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
* `supportsHitConditionalBreakpoints: true`
  — `setBreakpoints` honours the per-breakpoint `hitCondition`
  string. We parse the operator + count form (`">N"`, `">=N"`,
  `"==N"` / `"=N"`, `"!=N"`, `"<N"`, `"<=N"`, `"%N"`, bare `N`)
  and gate the DAP `stopped` event behind a server-side hit
  counter — gdb stops, we increment, and silently `-exec-continue`
  if the predicate says skip. So `hitCondition: ">3"` skips the
  first 3 hits and fires from the 4th onwards; `"%2"` fires every
  other hit. When combined with `condition`, both gates must pass
  (gdb's `-c` filters condition first; the hit counter then
  reflects "the Nth condition-passing hit"). Malformed
  `hitCondition` strings come back `verified: false` with a
  `breakpoint-validation-error` message so the IDE can render a
  diagnostic.
* `supportsFunctionBreakpoints: true`
  — `setFunctionBreakpoints` accepts a list of `{name, condition?}`
  entries and installs gdb breakpoints by symbol name. Useful when
  you don't know which file or line a function lives in, or when
  you want to catch all overloads. Stops fire on entry to any
  function whose symbol resolves; the `stopped` event carries
  `reason: "function breakpoint"`, `hitBreakpointIds: [<id>]`,
  and `description: "Entry to <name>"`. Names that don't resolve
  (e.g. typos, dynamically-loaded symbols) come back
  `verified: false` so the IDE can render a pending indicator
  without aborting the whole request.
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
* `supportsSteppingGranularity: true`
  — `next` / `stepIn` requests honour the DAP `granularity`
  argument. `"statement"` (default) and `"line"` keep the existing
  line-level stepping; `"instruction"` reroutes to gdb's
  `-exec-step-instruction` / `-exec-next-instruction` so the PC
  advances by exactly one machine instruction at a time. Each
  `stopped` event carries `instructionPointerReference` (the PC,
  taken from gdb's `frame.addr`) so the IDE can pin its
  disassembly view to the current location.
* `supportsDisassembleRequest: true`
  — `disassemble({memoryReference, instructionOffset?,
  instructionCount, offset?})` returns a `DisassembledInstruction[]`
  array of length exactly `instructionCount`, driven by gdb's
  `-data-disassemble`. Each instruction carries `address` +
  `instruction` (mnemonic + operands) plus optional `symbol`
  (function name) and `instructionBytes` (raw opcode hex). When
  source-line info is available (mode 4/5), `location` + `line`
  are populated so the IDE can anchor a source-line jump from the
  disassembly view.
* `supportsInstructionBreakpoints: true`
  — `setInstructionBreakpoints` accepts a list of
  `{instructionReference, offset?, condition?}` entries and
  installs gdb breakpoints by machine address via
  `-break-insert *0xADDR`. Used by the IDE's disassembly view when
  the user clicks the breakpoint gutter next to a specific
  instruction. Hits surface as `stopped` events with `reason:
  "instruction breakpoint"`, `hitBreakpointIds: [<id>]`, and a
  description like `Stopped at instruction 0x401045`.

Custom DAP requests (extensions under the standard request channel):

* `nova/profile/start({frequency_hz})` — start sample-based
  profiling. Installs a background sampler that periodically pauses
  the inferior (`-exec-interrupt`), captures the current call stack
  (`-stack-list-frames`), and resumes (`-exec-continue --all`) at
  the given frequency (default 100 Hz; clamped to [1, 1000]). The
  resulting `stopped` / `continued` events generated by the
  pause-sample-resume cycle are suppressed at the server level so
  the DAP client doesn't see profile-internal traffic. Sample-based
  profiling is less invasive than instrumentation (no code
  rewriting, no per-call overhead) and ideal for finding perf
  bottlenecks in production-shape binaries. Frame deduplication
  (function + file + line) keeps the wire shape compact.
* `nova/profile/stop()` — halt sampling and return the aggregated
  flame-graph data: `{samples, frames, total_samples, duration_s,
  frequency_hz, drop_count}`. The `samples` array is the raw
  innermost-first stack list per sample; the `frames` table maps
  each unique `(function, file, line)` triple to a stable frame id
  so the wire shape stays compact even for long profiles.
  Idempotent — a second stop() returns the empty aggregate
  without erroring.
* `nova/profile/report({format})` — re-format the captured samples
  in `"text"` (human-readable top-10 outline), `"folded"` (Brendan
  Gregg flamegraph.pl input — one `frame;frame;... count` per
  stack), or `"json"` (full samples + frames dump). Doesn't stop
  sampling; can be called mid-profile to peek at progress.

Events emitted:

* `initialized`              — after `initialize` succeeds.
* `stopped`                  — translated from gdb's `*stopped`
                                async record. Carries `threadId`
                                (from MI `thread-id`),
                                `allThreadsStopped` (true iff MI
                                `stopped-threads="all"`), and
                                `instructionPointerReference` (the
                                PC, taken from gdb's `frame.addr`,
                                used by the IDE's disassembly view
                                as the `memoryReference` anchor for
                                follow-up `disassemble` requests).
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
    function_breakpoints.py              Function breakpoints by name: gdb
                                          `-break-insert <fn_name>` command
                                          builder, response parser (verified
                                          vs pending), `FunctionBreakpointManager`
                                          + `describe_function_entry` description
                                          builder.
    disassembly.py                       Instruction-level stepping +
                                          disassembly view + instruction
                                          breakpoints: memoryReference parser,
                                          `-data-disassemble` command builder
                                          + response parser, step-granularity
                                          remapper (`-exec-step` ->
                                          `-exec-step-instruction`),
                                          `-break-insert *0xADDR` builder,
                                          `InstructionBreakpointManager`,
                                          `extract_instruction_pointer` PC
                                          extractor.
    profiler.py                          Sample-based profiler: `Profiler`
                                          sampling state, `Frame` / `Sample`
                                          dataclasses, frame-deduping
                                          aggregator, formatters (text /
                                          folded / JSON), gdb-MI adapter
                                          `pausing_stack_fn` that pauses
                                          the inferior to sample then
                                          resumes (the only way gdb can
                                          walk a live thread's stack).
    breakpoints.py                       Source-line BP gating (R29E):
                                          `parse_hit_condition` mini-parser
                                          (>N / >=N / ==N / =N / !=N / <N
                                          / <=N / %N / bare N),
                                          `SourceBreakpointRecord`
                                          (per-BP condition + hit-count
                                          bookkeeping),
                                          `SourceBreakpointManager`
                                          (thread-safe registry the
                                          `Session` holds),
                                          `evaluate_condition_via_bridge`
                                          (server-side re-eval helper
                                          with eval-error -> false
                                          fallback for the
                                          worker-thread / install-time
                                          path).
  tests/
    dap_smoke.py                         end-to-end single-thread smoke test
    dap_multi_thread.py                  end-to-end multi-thread coordination test
    test_evaluate.py                     evaluate request: decoder + REPL/watch/hover
    test_conditional_breakpoint.py       conditional `setBreakpoints` with `condition`
    test_hit_count_breakpoints.py        hit-count + condition gating (R29E)
    test_data_breakpoints.py             data breakpoints / watchpoints (dataBreakpointInfo + setDataBreakpoints)
    test_function_breakpoints.py         function breakpoints by name (setFunctionBreakpoints)
    test_instruction_stepping.py         instruction-level stepping + disassemble + setInstructionBreakpoints
    test_profiler.py                     sample-based profiler (nova/profile/{start,stop,report})
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

For function breakpoints (by name):

```sh
python tools/nova-dap/tests/test_function_breakpoints.py
# test_function_breakpoints: OK
#   unit assertions:    103
#   total assertions:   138
#   end-to-end:         ok (helper hits=3) (35 extra checks)
#   NOVA integration:   ok (main main_id=1, greet_hit=True)
```

For instruction-level stepping + disassembly + instruction
breakpoints:

```sh
python tools/nova-dap/tests/test_instruction_stepping.py
# test_instruction_stepping: OK
#   unit assertions:    125
#   total assertions:   149
#   NOVA integration:   ok (PC advance 18 bytes, disasm 8 insns) (24 extra checks)
```

For sample-based profiling:

```sh
python tools/nova-dap/tests/test_profiler.py
# test_profiler: OK
#   unit assertions:    101
#   total assertions:   120
#   end-to-end:         ok (73 samples, 96% in loop_body, 4 folded stacks)
```

All seven tests have a pure-Python phase that runs anywhere (no gdb
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
