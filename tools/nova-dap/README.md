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
| `setExceptionBreakpoints` | Stores the active filter set on the session. Filters: `"uncaught"` (default ON, gates fatal-signal stops — SIGABRT / SIGSEGV / SIGFPE / SIGBUS / SIGILL — so NOVA panics surface as `stopped(reason=exception)` events) and `"caught"` (default OFF, placeholder for future NOVA exception-catching constructs). When `"uncaught"` is NOT in the filter set, fatal signals are silently resumed (the inferior crashes; the IDE sees `terminated`). |
| `exceptionInfo`           | Returns `{exceptionId, description, breakMode: "unhandled", details: {typeName, message}}` for the most recent fatal signal stop. Reads `signal-name` + `signal-meaning` from the stashed gdb info. |
| `configurationDone`       | `-exec-run` first time; `-exec-continue --all` thereafter. |
| `threads`                 | Multi-thread: backstop reconcile with `-thread-info`; tracks `=thread-created` / `=thread-exited` for live updates. |
| `stackTrace`              | `-stack-list-frames --thread <threadId>`; frame ids are stable per `(threadId, level)`. |
| `scopes`                  | One `Locals` scope per frame.                     |
| `variables`               | `-thread-select` + `-stack-select-frame` + `-stack-list-variables --all-values` (so per-thread frame chains are isolated). |
| `evaluate`                | `-data-evaluate-expression --thread <id> --frame <level> "<expr>"`; result string decoded into `{result, type}` where type is `int` / `str` / `char` / `bool` / `ptr` / `raw`. Used for watch panel, REPL, and hover tooltips. |
| `dataBreakpointInfo`      | Returns `{dataId, description, accessTypes: ["write", "readWrite"], canPersist: false}` for a named variable. `dataId` is a base64-encoded JSON envelope `{n, f?, v?}` carrying the variable name + frame id so `setDataBreakpoints` can round-trip it without server-side state. |
| `setDataBreakpoints`      | Tears down prior watchpoints via `-break-delete <id>` (per id, so source breakpoints are preserved) and installs gdb hardware watchpoints via `-break-watch <expr>` (write), `-break-watch -r <expr>` (read), or `-break-watch -a <expr>` (rw). Watchpoint hits surface as `stopped` events with `reason: "data breakpoint"` and a description like `Variable 'counter' changed (write): 5 -> 6`. |
| `setInstructionBreakpoints` | Tears down prior instruction bps via `-break-delete <id>` (per id, so source-line / function / data breakpoints survive) and installs `-break-insert *0xADDR` per entry. Each entry carries `{instructionReference: "0xADDR", offset?, condition?}`; we add `offset` to the parsed address before the gdb call. Hits surface as `stopped` events with `reason: "instruction breakpoint"` and a description like `Stopped at instruction 0x401045`. |
| `disassemble`             | `-data-disassemble -s <start> -e <end> -- 0` — disassembles a range of memory around `memoryReference`, returning `DisassembledInstruction[]` of length exactly `instructionCount` (padded with `??` placeholders if gdb returns fewer). `instructionOffset` and byte `offset` shift the start address (we approximate 4 bytes/insn for instruction offsets). Each instruction's `location` carries `sourceReference: N` (R34F) pointing at a per-session cache of the enclosing function's `.s` listing. If gdb's MI form returns empty, we fall back to a static `objdump -d` parse. |
| `source`                  | (R34F) Returns the cached `.s` listing for a `sourceReference` previously emitted by `disassemble`. Body: `{content, mimeType: "text/x-asm"}`. Content is built via `objdump --disassemble=<fn>` on first access then cached for the rest of the session. |
| `continue` / `next` / `stepIn` / `stepOut` | `-exec-{continue,next,step,finish}` with `--thread <id>` when DAP carries `singleThread:true`, otherwise `--all`. When `granularity: "instruction"` is supplied, `next` -> `-exec-next-instruction` and `stepIn` -> `-exec-step-instruction` so the IDE's disassembly view can advance the PC by exactly one machine instruction. (`stepOut` keeps `-exec-finish` regardless — gdb has no per-instruction finish variant.) |
| `pause`                   | `-exec-interrupt --thread <id>` (or `--all`).     |
| `reverseContinue`         | `-exec-reverse-continue`, after lazily enabling gdb's process-record mode on the first call. Honours `threadId` + `singleThread` the same way forward `continue` does. The follow-up `*stopped` surfaces as a normal DAP `stopped` event with `reason: "step"` (DAP has no distinct reverse-stop reason). |
| `stepBack`                | `-exec-reverse-next` (granularity `line` / `statement` / default) or `-exec-reverse-step` (granularity `instruction`). Mirrors the forward `next` / `stepIn` granularity split. Also lazily enables process-record on first use. |
| `gotoTargets` / `goto`    | `gotoTargets` round-trips `{source.path, line}` as a single target id; `goto` issues `-interpreter-exec console "jump <file>:<line>"`. Useful for replaying a hot loop after a source edit without re-launching. |
| `disconnect` / `terminate`| `-target-record-stop` (if a recording was started) + `-gdb-exit` + reap child. The record-stop is symmetric with the lazy enable in `reverseContinue` / `stepBack` so the recording buffer is drained explicitly before gdb tears down. |

Capabilities advertised:

* `supportsConfigurationDoneRequest: true`
* `supportsStepBack: true`
  — `reverseContinue` + `stepBack` are wired to gdb's process-record
  mode. The `recordMode` launch attribute picks the backend: `"full"`
  (default — software recording, works on every gdb; ~50-1000x slower
  on the recorded segment) or `"btrace"` (hardware Intel PT, much
  faster, requires Skylake+ + Linux; falls back to `"full"` if the
  CPU / kernel can't support it). Recording is enabled lazily on the
  first reverse request so sessions that never reverse pay no cost;
  `disconnect` runs `-target-record-stop` to drain the buffer.
* `supportsGotoTargetsRequest: true`
  — `gotoTargets` + `goto` translate to gdb's `jump` command,
  relocating the PC to an arbitrary source line. The goto wiring
  rides alongside reverse-debug because both extend the exec-control
  surface; goto itself does not require recording.
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
* **`source` request + `sourceReference` mechanism (R34F).** The DAP
  client invokes `source({sourceReference: N})` to fetch the buffer
  content for a synthetic source descriptor — one whose
  `sourceReference != 0`. nova-dap allocates these ids inside the
  `disassemble` handler: each `DisassembledInstruction`'s `location`
  carries `sourceReference: N` pointing at the enclosing function's
  cached `.s` listing. The `source` handler then returns
  `{content, mimeType: "text/x-asm"}` (so VS Code applies its asm
  syntax theme). Cache key: `(binary_path, function_name)`. The cache
  is per-session, allocated lazily on the first `disassemble`, and
  cleared on every `launch` (the new binary may have moved functions,
  so stale ids would mislead). When `objdump` is on PATH the listing
  is built via `objdump -d --no-show-raw-insn --insn-width=8 -M intel
  --disassemble=<fn>` (with a full-dump fallback for older binutils
  that don't recognise the `--disassemble=NAME` form). When `objdump`
  isn't installed the `source` handler returns `success: false` with
  a clear message so the IDE renders its default placeholder.
  `disassemble` itself also has an `objdump` fallback path for the
  rare case gdb's `-data-disassemble` returns nothing (stripped section,
  no DWARF, etc.) — gdb-MI is still tried first.
* **`stackTrace` carries `instructionPointerReference` (R34F).** Every
  frame returned by `stackTrace` now includes the PC (`gdb` reports it
  in the `addr=` field of each `-stack-list-frames` entry). The IDE
  uses this to pin its disassembly view to each frame and to anchor
  follow-up `disassemble` requests; prior to R34F only `stopped`
  events carried the PC. The frame chain still includes the per-frame
  `source` + `line` + `column` for the source-level path.
* `supportsExceptionInfoRequest: true` + `exceptionBreakpointFilters`
  — `setExceptionBreakpoints {filters: ["uncaught"]}` activates a
  server-side gate that converts gdb's `*stopped reason=signal-received`
  records (for the fatal-signal set: `SIGABRT` / `SIGSEGV` / `SIGFPE`
  / `SIGBUS` / `SIGILL`) into DAP `stopped` events with
  `reason: "exception"` and a `description` carrying the signal name
  + meaning (e.g. `SIGABRT (Aborted)`). The `"uncaught"` filter is
  default-ON so a brand-new session catches panics out of the box;
  when removed from the filter set the gate silently `-exec-continue`s
  past fatal signals and the IDE eventually sees `terminated`. A
  follow-up `exceptionInfo` request returns
  `{exceptionId, description, breakMode: "unhandled", details}`. The
  `"caught"` filter is advertised default-OFF as a placeholder for
  future NOVA exception-catching constructs (e.g. a `?` operator) and
  currently has no effect. `supportsExceptionFilterOptions: false`
  because NOVA panics don't yet have per-exception-type
  configuration.

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
* **Reverse-debug performance with `record full`.** The default
  `recordMode: "full"` slows the recorded segment 50-1000x because
  gdb instruments every basic block. Long debug sessions can chew
  through gigabytes of memory for the recording buffer. Users with
  modern Intel CPUs should pass `recordMode: "btrace"` in their
  `launch.json` to get hardware Intel PT recording (much faster,
  much smaller buffer). The adapter falls back to `"full"` if
  `btrace` isn't supported on the target.

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
    test_reverse_debug.py                reverse-debug (reverseContinue / stepBack / goto / record lifecycle)
    test_exception_breakpoints.py        exception breakpoints (setExceptionBreakpoints + exceptionInfo, R33F)
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

For reverse debugging (R31E):

```sh
python tools/nova-dap/tests/test_reverse_debug.py
# test_reverse_debug: OK
#   unit tests:       38
#   total assertions: 100
```

The reverse-debug test is pure unit (no gdb required); it drives
each handler against a fake bridge and verifies the MI command
shape + record-mode lifecycle. The end-to-end reverse-debug flow
is covered by the existing `dap_smoke` regression (which now
checks `supportsStepBack: true`) plus manual VS Code interaction.

For exception breakpoints (R33F):

```sh
python tools/nova-dap/tests/test_exception_breakpoints.py
# test_exception_breakpoints: OK
#   unit tests:       38
#   total assertions: 141
```

The exception-breakpoint test is pure unit (no gdb required); it
drives `handle_set_exception_breakpoints` + `handle_exception_info`
+ `_handle_stopped`'s signal-filter gate against synthesised
`GdbAsyncRecord` fixtures matching what gdb emits on real panics
(SIGABRT / SIGSEGV / SIGFPE / SIGBUS / SIGILL). The R28F profiler /
R29E conditional+hit-count BP / R31E reverse-debug regression
guards live in the same file so a single test run confirms the
exception filter doesn't disturb any prior subsystem.

All nine tests have a pure-Python phase that runs anywhere (no gdb
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
      "stopOnEntry": false,
      // R31E: enable reverse-debug recording. `full` (default) works
      // everywhere but slows the recorded segment 50-1000x; `btrace`
      // is much faster on modern Intel CPUs but needs Skylake+ + Linux.
      // If `btrace` isn't supported the adapter silently falls back.
      "recordMode": "full"
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
