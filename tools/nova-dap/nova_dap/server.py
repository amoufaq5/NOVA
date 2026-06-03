"""Nova Debug Adapter Protocol server.

A DAP server that drives ``gdb --interpreter=mi3`` under the hood. VS
Code (or any DAP-speaking client) sends DAP JSON over stdio; we
translate each request into one or more MI commands via
:class:`nova_dap.gdb_bridge.GdbBridge`, then translate gdb's async
records back into DAP events.

Supported requests
------------------

* ``initialize``          — declares capabilities (incl.
                             ``supportsSingleThreadExecutionRequests``).
* ``launch``              — spawns gdb, loads the binary, optionally
                             sets ``cwd`` and ``args`` for the inferior.
                             Enables ``non-stop`` + ``mi-async`` on gdb
                             so each thread can be stepped / continued
                             independently.
* ``setBreakpoints``      — translates each source-line breakpoint via
                             ``-break-insert``. If a breakpoint carries
                             a ``condition`` string, it's forwarded
                             via ``-break-insert -c "<expr>"`` so gdb
                             only stops when the condition is true.
* ``setFunctionBreakpoints`` — installs gdb breakpoints by function
                             name via ``-break-insert [-c "<expr>"]
                             <name>``. Names that don't resolve come
                             back ``verified: false`` (DAP allows
                             pending breakpoints). Hits surface as
                             ``stopped`` events with ``reason:
                             "function breakpoint"`` and a description
                             like ``"Entry to main"``.
* ``setInstructionBreakpoints`` — installs gdb breakpoints by
                             machine address via
                             ``-break-insert *0xADDR``. Used by
                             the IDE's disassembly view. Hits
                             surface as ``stopped`` events with
                             ``reason: "instruction breakpoint"``
                             and a description like
                             ``"Stopped at instruction 0x401045"``.
* ``disassemble``         — ``-data-disassemble -s START -e END
                             -- 0`` for a memory range. Returns
                             ``DisassembledInstruction[]`` of
                             length exactly ``instructionCount``
                             (padded with ``"??"`` placeholders
                             if gdb returns fewer).
* ``configurationDone``   — runs the inferior (``-exec-run``).
* ``threads``             — real multi-thread list from ``-thread-info``.
                             For single-thread programs this still
                             returns ``[{id:1,name:"main"}]``.
* ``stackTrace``          — ``-stack-list-frames`` for the requested
                             thread (``--thread`` argument).
* ``scopes``              — single "Locals" scope per frame (the frame
                             id encodes ``(threadId, level)`` so the
                             ``variables`` request can route back to the
                             right thread).
* ``variables``           — ``-stack-list-variables --all-values`` after
                             selecting the right thread + frame.
* ``evaluate``            — ``-data-evaluate-expression`` routed through
                             the frame-id's (threadId, level). Returns
                             a decoded ``{result, type}`` pair. Used
                             for the watch window, debug-console REPL,
                             and hover tooltips.
* ``dataBreakpointInfo``  — returns ``{dataId, description, accessTypes,
                             canPersist: false}`` for a named variable.
                             ``dataId`` is a base64-encoded JSON blob
                             carrying the variable name + frame id so
                             ``setDataBreakpoints`` can round-trip it.
* ``setDataBreakpoints``  — installs gdb hardware watchpoints via
                             ``-break-watch`` (write) / ``-break-watch
                             -r`` (read) / ``-break-watch -a`` (rw)
                             depending on the DAP ``accessType``.
                             Watchpoint hits surface as ``stopped``
                             events with ``reason: "data breakpoint"``.
* ``continue`` / ``next`` / ``stepIn`` / ``stepOut`` — exec controls,
                             accepting DAP's ``threadId``,
                             ``singleThread``, and ``granularity``
                             arguments. When ``singleThread`` is
                             true we pass ``--thread <id>`` to gdb
                             so only that thread runs; otherwise
                             gdb resumes the whole process. When
                             ``granularity`` is ``"instruction"``,
                             ``next`` / ``stepIn`` reroute to
                             ``-exec-next-instruction`` /
                             ``-exec-step-instruction`` so the PC
                             advances by exactly one machine
                             instruction at a time.
* ``pause``               — interrupt one thread (or all) via
                             ``-exec-interrupt`` with the appropriate
                             ``--thread`` / ``--all`` flag.
* ``disconnect``          — ``-gdb-exit``.

Supported events
----------------

* ``initialized``  — after ``initialize`` succeeds.
* ``stopped``      — emitted on any gdb ``*stopped`` async record. The
                     ``threadId`` field is taken from the MI record,
                     and ``allThreadsStopped`` reflects whether the
                     ``stopped-threads`` field is the literal string
                     ``"all"`` (non-stop mode reports the actual
                     stopped thread ids).
* ``continued``    — DAP ``continued`` event when a thread (or all
                     threads) start running again, carrying the
                     originating ``threadId`` and ``allThreadsContinued``.
* ``thread``       — DAP ``thread`` lifecycle event (``started`` /
                     ``exited``) mapped from gdb ``=thread-created`` /
                     ``=thread-exited`` notifications.
* ``output``       — wraps gdb console / target stream records.
* ``terminated`` + ``exited`` — emitted on ``*stopped reason=exited*``
                                  or ``=thread-group-exited``.

The server is intentionally single-client / single-session. A new gdb
subprocess is launched per ``launch`` request.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nova_dap import __version__
from nova_dap.evaluator import evaluate_via_bridge
from nova_dap.gdb_bridge import (
    GdbAsyncRecord,
    GdbBridge,
    GdbResult,
    gdb_available,
    quote_path,
)
from nova_dap.watchpoints import (
    WatchpointManager,
    WatchpointRecord,
    access_type_flag,
    build_watch_command,
    decode_data_id,
    default_access_types,
    describe_watch_change,
    encode_data_id,
    extract_watch_values,
    is_watchpoint_stop,
    parse_watchpoint_id,
)
from nova_dap.function_breakpoints import (
    FunctionBreakpointManager,
    FunctionBreakpointRecord,
    build_function_breakpoint_command,
    describe_function_entry,
    is_unresolved_function_error,
    parse_function_breakpoint_response,
)
from nova_dap.disassembly import (
    InstructionBreakpointManager,
    InstructionBreakpointRecord,
    build_disassemble_command,
    build_instruction_breakpoint_command,
    extract_instruction_pointer,
    is_instruction_granularity,
    map_step_command,
    parse_disassemble_response,
    parse_memory_reference,
)


LOG_FILE = os.environ.get("NOVA_DAP_LOG")


def _log(msg: str) -> None:
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# DAP JSON-RPC framing (same Content-Length envelope as LSP).
# ---------------------------------------------------------------------------


def read_message(stream) -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if line in ("\r\n", "\n", ""):
            break
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    raw = stream.read(length)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        _log(f"json decode error: {exc}")
        return None


def write_message(stream, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    try:
        stream.write(header + data)
        stream.flush()
    except (BrokenPipeError, OSError) as exc:
        _log(f"write failed: {exc}")


# ---------------------------------------------------------------------------
# Session state.
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """Mutable per-connection state.

    The instance is constructed once per DAP connection; ``launch``
    populates the gdb bridge."""

    out_stream: Any  # binary writable
    out_lock: threading.Lock = field(default_factory=threading.Lock)
    seq: int = 1
    bridge: Optional[GdbBridge] = None
    program: Optional[str] = None
    # Sticky DAP state.
    threadId: int = 1                                  # noqa: N815
    stopped_reported: bool = False
    terminated_reported: bool = False
    last_stop_reason: str = "entry"
    # Multi-thread bookkeeping.
    # `known_threads` is the mirrored view of gdb's thread set — keyed
    # by integer DAP thread id (== gdb's MI thread id). Each value is a
    # dict ``{"name": str, "running": bool}``. The set is updated from
    # ``=thread-created`` / ``=thread-exited`` notifications and (as a
    # backstop) by every ``threads`` request via ``-thread-info``.
    known_threads: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    threads_lock: threading.Lock = field(default_factory=threading.Lock)
    # Set to True after we successfully negotiate ``set non-stop on`` +
    # ``set mi-async on`` with gdb. When False, all step/continue/pause
    # commands are issued without ``--thread`` (all-stop mode fallback).
    non_stop: bool = False
    # Frame id encoding: each (threadId, frame_level) pair gets a
    # stable integer DAP frame id. We assign them lazily and never
    # recycle — DAP clients keep frame ids alive across requests.
    # ``next_frame_id`` is the high-water mark; ``frame_table`` maps
    # frame id -> (threadId, frame_level).
    next_frame_id: int = 1
    frame_table: Dict[int, Tuple[int, int]] = field(default_factory=dict)
    # Reverse lookup so we don't double-issue frame ids for the same
    # (thread, level) pair within a single session.
    frame_lookup: Dict[Tuple[int, int], int] = field(default_factory=dict)
    # variablesReference allocator. Each vRef maps back to a frame id
    # (and the frame id then maps back to a (threadId, frame_level)).
    next_var_ref: int = 1000
    frame_refs: Dict[int, int] = field(default_factory=dict)  # vRef -> frameId
    # Data breakpoint (watchpoint) registry. See
    # ``nova_dap.watchpoints`` for the wire encoding + gdb integration.
    # The manager is reset on every ``launch`` since gdb forgets all
    # watchpoints when the inferior restarts.
    watchpoints: WatchpointManager = field(default_factory=WatchpointManager)
    # Function breakpoint registry. See ``nova_dap.function_breakpoints``
    # for the wire shape + gdb integration. Like the watchpoint
    # registry, reset on every ``launch``; unlike it, populated by
    # ``setFunctionBreakpoints`` (complete-replacement semantics —
    # every call tears down the previous set and reinstalls).
    function_breakpoints: FunctionBreakpointManager = field(
        default_factory=FunctionBreakpointManager
    )
    # Instruction breakpoint registry. See ``nova_dap.disassembly`` for
    # the wire shape + gdb integration. Reset on every ``launch`` for
    # the same reason as the other breakpoint registries (gdb forgets
    # every breakpoint when the inferior restarts; otherwise stale
    # gdb ids could route the wrong description on a subsequent stop).
    instruction_breakpoints: InstructionBreakpointManager = field(
        default_factory=InstructionBreakpointManager
    )

    def alloc_var_ref(self, frame_id: int) -> int:
        ref = self.next_var_ref
        self.next_var_ref += 1
        self.frame_refs[ref] = frame_id
        return ref

    def frame_id_for(self, thread_id: int, level: int) -> int:
        """Return the stable DAP frame id for ``(threadId, level)``,
        allocating one the first time we see it."""
        key = (thread_id, level)
        existing = self.frame_lookup.get(key)
        if existing is not None:
            return existing
        fid = self.next_frame_id
        self.next_frame_id += 1
        self.frame_table[fid] = key
        self.frame_lookup[key] = fid
        return fid

    def frame_lookup_by_id(self, frame_id: int) -> Tuple[int, int]:
        """Inverse of :meth:`frame_id_for`. Returns ``(threadId, level)``;
        falls back to ``(self.threadId, 0)`` for unknown ids so legacy
        clients that pass synthetic frame ids still work."""
        return self.frame_table.get(frame_id, (self.threadId, 0))

    def register_thread(self, thread_id: int, name: str = "", running: bool = True) -> None:
        with self.threads_lock:
            entry = self.known_threads.setdefault(
                thread_id, {"name": name or f"thread-{thread_id}", "running": running}
            )
            if name:
                entry["name"] = name
            entry["running"] = running

    def remove_thread(self, thread_id: int) -> None:
        with self.threads_lock:
            self.known_threads.pop(thread_id, None)

    def mark_thread_running(self, thread_id: int, running: bool) -> None:
        with self.threads_lock:
            entry = self.known_threads.get(thread_id)
            if entry is not None:
                entry["running"] = running

    def thread_snapshot(self) -> List[Dict[str, Any]]:
        with self.threads_lock:
            return [
                {"id": tid, "name": info.get("name") or f"thread-{tid}"}
                for tid, info in sorted(self.known_threads.items())
            ]


# ---------------------------------------------------------------------------
# DAP message helpers.
# ---------------------------------------------------------------------------


def _send(session: Session, payload: Dict[str, Any]) -> None:
    with session.out_lock:
        payload["seq"] = session.seq
        session.seq += 1
        write_message(session.out_stream, payload)


def send_response(
    session: Session,
    request: Dict[str, Any],
    body: Optional[Dict[str, Any]] = None,
    success: bool = True,
    message: Optional[str] = None,
) -> None:
    resp: Dict[str, Any] = {
        "type": "response",
        "request_seq": request.get("seq", 0),
        "success": success,
        "command": request.get("command", ""),
    }
    if body is not None:
        resp["body"] = body
    if message is not None:
        resp["message"] = message
    _send(session, resp)


def send_event(session: Session, event: str, body: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {"type": "event", "event": event}
    if body is not None:
        payload["body"] = body
    _send(session, payload)


# ---------------------------------------------------------------------------
# gdb event translation.
# ---------------------------------------------------------------------------


def _on_gdb_event(session: Session, rec: GdbAsyncRecord) -> None:
    _log(f"gdb-event {rec.kind} {rec.cls} {rec.fields}")
    if rec.cls == "stopped":
        _handle_stopped(session, rec)
    elif rec.cls == "running":
        _handle_running(session, rec)
    elif rec.cls == "thread-group-exited":
        _handle_exited(session, rec)
    elif rec.cls == "thread-created":
        _handle_thread_created(session, rec)
    elif rec.cls == "thread-exited":
        _handle_thread_exited(session, rec)


def _parse_thread_id(raw: Any) -> Optional[int]:
    """Extract an integer thread id from an MI field, tolerating
    decimal strings, raw ints, and the literal ``"all"``."""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _stopped_threads(rec: GdbAsyncRecord) -> Tuple[List[int], bool]:
    """Return ``(thread_ids, all_threads_stopped)`` for a ``*stopped``
    record. gdb reports ``stopped-threads="all"`` in all-stop mode and
    a list of ids in non-stop mode."""
    raw = rec.fields.get("stopped-threads")
    if isinstance(raw, str) and raw == "all":
        return ([], True)
    if isinstance(raw, list):
        ids: List[int] = []
        for entry in raw:
            tid = _parse_thread_id(entry)
            if tid is not None:
                ids.append(tid)
        return (ids, False)
    # Fall back to the single ``thread-id`` field — older gdb / single
    # thread inferiors only emit that.
    tid = _parse_thread_id(rec.fields.get("thread-id"))
    if tid is not None:
        return ([tid], False)
    return ([], False)


def _handle_stopped(session: Session, rec: GdbAsyncRecord) -> None:
    reason = rec.fields.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    # exit reasons translate to terminated, not stopped.
    if reason.startswith("exited"):
        _handle_exited(session, rec)
        return
    dap_reason_map = {
        "breakpoint-hit": "breakpoint",
        "end-stepping-range": "step",
        "function-finished": "step",
        "signal-received": "exception",
        "watchpoint-trigger": "data breakpoint",
        "read-watchpoint-trigger": "data breakpoint",
        "access-watchpoint-trigger": "data breakpoint",
        "watchpoint-scope": "data breakpoint",
    }
    dap_reason = dap_reason_map.get(reason, reason or "pause")
    session.last_stop_reason = dap_reason
    session.stopped_reported = True

    # Which thread (or threads) stopped?
    stopped_ids, all_stopped = _stopped_threads(rec)
    primary_tid = _parse_thread_id(rec.fields.get("thread-id"))
    if primary_tid is None and stopped_ids:
        primary_tid = stopped_ids[0]
    if primary_tid is None:
        primary_tid = session.threadId
    else:
        # Track the most-recently-stopped thread so DAP requests that
        # don't carry a thread id (e.g. ``threads`` -> ``stackTrace``
        # without a ``threadId`` arg in older clients) target the right
        # frame chain.
        session.threadId = primary_tid

    # Mark thread states. In all-stop mode every known thread is
    # stopped; in non-stop mode only the listed ids are.
    if all_stopped:
        with session.threads_lock:
            for tid_entry in session.known_threads.values():
                tid_entry["running"] = False
        # Ensure the primary thread is in the table even if we missed
        # the ``=thread-created`` notification (e.g. single-thread
        # programs where gdb never emits one before the first stop).
        session.register_thread(primary_tid, running=False)
    else:
        # Make sure each stopped id is registered as well.
        for tid in stopped_ids:
            session.register_thread(tid, running=False)
        if primary_tid is not None:
            session.register_thread(primary_tid, running=False)

    body: Dict[str, Any] = {
        "reason": dap_reason,
        "threadId": primary_tid,
        "allThreadsStopped": all_stopped,
        "preserveFocusHint": False,
    }
    # DAP ``instructionPointerReference`` — the PC at the stop site.
    # We surface gdb's ``frame.addr`` so the DAP client can pin its
    # disassembly view (and subsequent ``disassemble`` requests) to
    # the exact instruction the inferior stopped at. Best-effort:
    # some stop records (e.g. ``=thread-group-exited``) don't carry
    # a frame, in which case we just omit the field.
    ip_ref = extract_instruction_pointer(rec.fields)
    if ip_ref is not None:
        body["instructionPointerReference"] = ip_ref
    bkptno = rec.fields.get("bkptno")
    hit_id: Optional[int] = None
    if isinstance(bkptno, str) and bkptno.isdigit():
        hit_id = int(bkptno)
    elif isinstance(bkptno, int):
        hit_id = bkptno
    # Watchpoint stops have a different shape: gdb ships ``wpt={number,
    # exp}`` (or ``hw-rwpt`` / ``hw-awpt``) directly in the *stopped
    # record instead of populating ``bkptno``. Extract the watchpoint
    # number from those fields so the DAP client gets the right
    # ``hitBreakpointIds``.
    if hit_id is None and is_watchpoint_stop(reason):
        watch_id = parse_watchpoint_id(rec.fields)
        if watch_id is not None:
            hit_id = watch_id
    if hit_id is not None:
        body["hitBreakpointIds"] = [hit_id]
    # For watchpoint stops, gdb ships the watched expression's
    # before/after values in a ``value={old,new}`` tuple. We surface
    # that as a human-readable ``description`` so the DAP client can
    # render "Variable 'counter' changed (write): 5 -> 6" in the
    # call-stack panel.
    if is_watchpoint_stop(reason) and hit_id is not None:
        record = session.watchpoints.lookup_by_gdb_id(hit_id)
        if record is not None:
            old_val, new_val = extract_watch_values(rec.fields)
            body["description"] = describe_watch_change(
                record.name, record.access_type, old_val, new_val
            )
    # For function-breakpoint hits, gdb emits the same
    # ``reason="breakpoint-hit"`` record as a source-line bp; we tell
    # them apart by looking the ``bkptno`` up in the function-bp
    # manager. If we find a match, refine the DAP wire shape so the
    # IDE renders "function breakpoint" instead of plain "breakpoint"
    # and the description says which function was entered.
    if (
        reason == "breakpoint-hit"
        and hit_id is not None
        and not is_watchpoint_stop(reason)
    ):
        fn_record = session.function_breakpoints.lookup_by_gdb_id(hit_id)
        if fn_record is not None:
            body["reason"] = "function breakpoint"
            body["description"] = describe_function_entry(fn_record.name)
        else:
            # Instruction breakpoints share the same ``breakpoint-hit``
            # gdb stop reason. If the bkptno matches a record in the
            # instruction-bp manager, refine the DAP wire shape to
            # ``reason: "instruction breakpoint"`` so the IDE can
            # render the disassembly-panel indicator instead of a
            # source-line gutter dot.
            ibp_record = session.instruction_breakpoints.lookup_by_gdb_id(hit_id)
            if ibp_record is not None:
                body["reason"] = "instruction breakpoint"
                body["description"] = (
                    f"Stopped at instruction {ibp_record.instruction_reference}"
                )
    send_event(session, "stopped", body)


def _handle_running(session: Session, rec: GdbAsyncRecord) -> None:
    """Translate ``*running,thread-id=X`` (or ``thread-id="all"``) to a
    DAP ``continued`` event. The DAP spec says clients SHOULD update
    their UI when threads resume so the "running" indicator is
    visible per-thread."""
    raw_tid = rec.fields.get("thread-id")
    if isinstance(raw_tid, str) and raw_tid == "all":
        with session.threads_lock:
            for entry in session.known_threads.values():
                entry["running"] = True
        send_event(
            session,
            "continued",
            {"threadId": session.threadId, "allThreadsContinued": True},
        )
        return
    tid = _parse_thread_id(raw_tid)
    if tid is None:
        return
    session.mark_thread_running(tid, True)
    send_event(
        session,
        "continued",
        {"threadId": tid, "allThreadsContinued": False},
    )


def _handle_thread_created(session: Session, rec: GdbAsyncRecord) -> None:
    tid = _parse_thread_id(rec.fields.get("id"))
    if tid is None:
        return
    name = f"thread-{tid}" if tid != 1 else "main"
    session.register_thread(tid, name=name, running=True)
    send_event(
        session,
        "thread",
        {"reason": "started", "threadId": tid},
    )


def _handle_thread_exited(session: Session, rec: GdbAsyncRecord) -> None:
    tid = _parse_thread_id(rec.fields.get("id"))
    if tid is None:
        return
    session.remove_thread(tid)
    send_event(
        session,
        "thread",
        {"reason": "exited", "threadId": tid},
    )


def _handle_exited(session: Session, rec: GdbAsyncRecord) -> None:
    if session.terminated_reported:
        return
    session.terminated_reported = True
    exit_code = 0
    raw = rec.fields.get("exit-code")
    if isinstance(raw, str):
        try:
            # gdb reports octal (e.g. "0177"); accept hex / decimal too.
            exit_code = int(raw, 0)
        except ValueError:
            try:
                exit_code = int(raw, 8)
            except ValueError:
                exit_code = 0
    send_event(session, "exited", {"exitCode": exit_code})
    send_event(session, "terminated", {})


def _on_gdb_console(session: Session, stream: str, text: str) -> None:
    # Map gdb's stream records to DAP output events. We tag console /
    # log records as ``console`` and target output as ``stdout`` so the
    # IDE shows them in the right pane.
    category_map = {"console": "console", "log": "console", "target": "stdout"}
    category = category_map.get(stream, "console")
    if not text:
        return
    send_event(session, "output", {"category": category, "output": text})


# ---------------------------------------------------------------------------
# Request handlers.
# ---------------------------------------------------------------------------


def _capabilities() -> Dict[str, Any]:
    return {
        "supportsConfigurationDoneRequest": True,
        "supportsStepBack": False,
        "supportsTerminateRequest": True,
        "supportsRestartRequest": False,
        # Conditional breakpoints: ``-break-insert -c "<expr>"`` —
        # gdb evaluates the expression at the breakpoint hit site and
        # only stops when it's non-zero. See ``handle_set_breakpoints``.
        "supportsConditionalBreakpoints": True,
        # Function breakpoints: ``-break-insert <fn_name>`` (optionally
        # with ``-c "<expr>"``). Stops fire on entry to any function
        # whose symbol matches the supplied name; unresolved names
        # come back ``verified: false`` so the IDE can render a
        # pending indicator. See ``handle_set_function_breakpoints``.
        "supportsFunctionBreakpoints": True,
        "supportsHitConditionalBreakpoints": False,
        # Evaluate-for-hovers: hovering an identifier in the editor
        # triggers an ``evaluate`` request with ``context="hover"``.
        # We share the same gdb-MI path with the watch / repl contexts;
        # gdb's evaluator is side-effect-free for plain reads so hover
        # is safe to leave on.
        "supportsEvaluateForHovers": True,
        "supportsSetVariable": False,
        "supportsCompletionsRequest": False,
        "supportsModulesRequest": False,
        "supportsLogPoints": False,
        "supportsExceptionInfoRequest": False,
        "supportsDelayedStackTraceLoading": False,
        "supportsClipboardContext": False,
        # Multi-thread coordination: each step / continue / pause
        # request may carry ``threadId`` + ``singleThread`` so only
        # the target thread is resumed/stopped.
        "supportsSingleThreadExecutionRequests": True,
        # Data breakpoints (watchpoints): ``dataBreakpointInfo`` +
        # ``setDataBreakpoints``. We delegate to gdb's hardware
        # watchpoint support (``-break-watch`` / ``-break-watch -r``
        # / ``-break-watch -a``) so stops fire whenever a watched
        # variable's value changes. See
        # ``handle_data_breakpoint_info`` and
        # ``handle_set_data_breakpoints``.
        "supportsDataBreakpoints": True,
        # Instruction-level stepping: DAP ``next`` / ``stepIn`` /
        # ``stepOut`` requests honour ``granularity: "instruction"``
        # by routing to gdb's ``-exec-step-instruction`` /
        # ``-exec-next-instruction`` MI commands. ``stopped`` events
        # carry ``instructionPointerReference`` (the PC, taken from
        # gdb's ``frame.addr`` field) so the DAP client can pin its
        # disassembly view to the current location.
        "supportsSteppingGranularity": True,
        # Disassembly view: ``disassemble`` request returns
        # ``DisassembledInstruction[]`` for a memory range, driven
        # by gdb's ``-data-disassemble``. The IDE renders these in
        # the disassembly panel with optional source-line mapping.
        "supportsDisassembleRequest": True,
        # Instruction breakpoints: set a breakpoint at a specific
        # machine address via ``setInstructionBreakpoints`` ->
        # gdb's ``-break-insert *0xADDR``. Used by the IDE's
        # disassembly view when the user clicks a breakpoint
        # gutter next to a specific instruction.
        "supportsInstructionBreakpoints": True,
    }


def handle_initialize(session: Session, req: Dict[str, Any]) -> None:
    send_response(session, req, body=_capabilities())
    # initialized must come AFTER the initialize response.
    send_event(session, "initialized")


def handle_launch(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    program = args.get("program")
    if not program:
        send_response(
            session,
            req,
            success=False,
            message="launch requires 'program' argument",
        )
        return
    if not os.path.isfile(program):
        send_response(
            session,
            req,
            success=False,
            message=f"program not found: {program}",
        )
        return
    if not gdb_available():
        send_response(
            session,
            req,
            success=False,
            message="gdb not found; install with `apt install gdb`",
        )
        return

    bridge = GdbBridge(
        on_event=lambda rec: _on_gdb_event(session, rec),
        on_console=lambda s, t: _on_gdb_console(session, s, t),
    )
    bridge.start()
    session.bridge = bridge
    session.program = program
    # Fresh inferior -> no live watchpoints. Drop whatever a previous
    # launch may have left in the registry so dataIds from the old
    # session can't collide with the new gdb-assigned numbers.
    session.watchpoints.clear_all()
    # Same story for function breakpoints — gdb forgets every
    # breakpoint when the inferior restarts, so the manager must be
    # reset or stale gdb ids will route the wrong "Entry to <fn>"
    # description on a subsequent breakpoint-hit stop.
    session.function_breakpoints.clear_all()
    # And the same for instruction breakpoints (``-break-insert
    # *0xADDR`` is just another flavour of breakpoint as far as gdb
    # is concerned, so a relaunch invalidates the manager's gdb_id
    # mappings).
    session.instruction_breakpoints.clear_all()

    # Try to negotiate non-stop + mi-async so individual threads can
    # be paused / continued. If gdb rejects either (e.g. it's running
    # against a target that doesn't support non-stop), fall back to
    # the all-stop model — the DAP wire protocol still works, we just
    # can't honour ``singleThread`` and stops will report
    # ``allThreadsStopped=true``.
    #
    # IMPORTANT: ``mi-async`` MUST be set before ``non-stop`` and both
    # MUST be set before the first ``-exec-run``. Once a target is
    # selected gdb refuses to flip these.
    async_ok = bridge.command("-gdb-set mi-async on").ok
    non_stop_ok = False
    if async_ok:
        non_stop_ok = bridge.command("-gdb-set non-stop on").ok
    session.non_stop = bool(async_ok and non_stop_ok)
    _log(f"thread mode: non_stop={session.non_stop} mi_async={async_ok}")

    cwd = args.get("cwd")
    if cwd:
        bridge.command(f"-environment-cd {quote_path(cwd)}")
    # Inferior arguments — passed through DAP arg list.
    inf_args = args.get("args") or []
    if inf_args:
        # ``-exec-arguments`` takes a free-form string; just space-join.
        joined = " ".join(str(a) for a in inf_args)
        bridge.command(f"-exec-arguments {joined}")

    result = bridge.command(f"-file-exec-and-symbols {quote_path(program)}")
    if not result.ok:
        send_response(
            session,
            req,
            success=False,
            message=f"gdb failed to load program: {result.error_message}",
        )
        return

    # Pre-populate the thread table with thread 1 so a ``threads``
    # request issued before the inferior actually starts doesn't return
    # an empty list. gdb will overwrite this with ``=thread-created``
    # records once the inferior runs.
    session.register_thread(1, name="main", running=True)

    # Don't pause on entry by default; configurationDone runs the program.
    send_response(session, req, body={})


def handle_set_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    source = args.get("source") or {}
    raw_path = source.get("path") or source.get("name") or ""
    breakpoints = args.get("breakpoints") or []
    out: List[Dict[str, Any]] = []
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, body={"breakpoints": [{"verified": False} for _ in breakpoints]})
        return
    # Clear breakpoints for this source first so re-sends don't double up.
    # gdb has no per-source delete, so we delete all and re-create — fine
    # for MVP single-source workflows.
    bridge.command("-break-delete")
    for bp in breakpoints:
        line = bp.get("line")
        if line is None:
            out.append({"verified": False})
            continue
        loc = f"{raw_path}:{line}"
        # DAP carries the condition expression as ``bp["condition"]``
        # (free-form text). gdb's ``-break-insert -c "<expr>"`` only
        # stops when ``<expr>`` evaluates to non-zero. We forward the
        # condition verbatim; if it's malformed gdb returns an
        # ``^error`` which we surface as ``verified=false``.
        condition = bp.get("condition")
        cmd_parts = ["-break-insert"]
        if isinstance(condition, str) and condition.strip():
            cmd_parts.extend(["-c", quote_path(condition)])
        cmd_parts.append(quote_path(loc))
        result = bridge.command(" ".join(cmd_parts))
        if not result.ok:
            out.append(
                {
                    "verified": False,
                    "line": line,
                    "message": result.error_message or "could not set breakpoint",
                }
            )
            continue
        bk = result.fields.get("bkpt")
        verified = False
        actual_line = line
        bp_id: Optional[int] = None
        if isinstance(bk, dict):
            num = bk.get("number")
            if isinstance(num, str) and num.isdigit():
                bp_id = int(num)
            real_line = bk.get("line")
            if isinstance(real_line, str) and real_line.isdigit():
                actual_line = int(real_line)
            addr = bk.get("addr")
            verified = bool(addr) and addr not in ("<PENDING>", "<MULTIPLE>")
        entry: Dict[str, Any] = {"verified": verified, "line": actual_line}
        if bp_id is not None:
            entry["id"] = bp_id
        entry["source"] = source
        out.append(entry)
    send_response(session, req, body={"breakpoints": out})


def handle_set_function_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``setFunctionBreakpoints`` request.

    Replaces the active set of function breakpoints with the supplied
    list. Each entry has ``{name, condition?, hitCondition?}``; we
    forward each to gdb via ``-break-insert [-c "<expr>"] <name>``
    and record the assigned breakpoint number so a later
    ``*stopped,bkptno=...`` can be routed to the right ``"Entry to
    <fn>"`` description.

    Behaviour notes:

    * Like ``setBreakpoints`` + ``setDataBreakpoints``, this is a
      complete-replacement request: every call tears down the
      previously-installed function breakpoints via ``-break-delete
      <id>`` (per id, so source-line breakpoints + watchpoints
      survive) and reinstalls from scratch.
    * Names that gdb can't resolve (``Function "foo" not defined.``)
      come back ``verified: false`` with gdb's message in
      ``message`` — the entry is reported but not active. Other
      gdb errors (e.g. malformed condition expression) get the same
      shape; the DAP client can choose to render them differently
      based on the message.
    * The ``hitCondition`` field is accepted but ignored — we
      declare ``supportsHitConditionalBreakpoints: false`` so well-
      behaved clients won't send it; we tolerate it for robustness.
    """
    args = req.get("arguments", {}) or {}
    bridge = session.bridge
    if bridge is None:
        send_response(
            session, req, success=False, message="not launched"
        )
        return
    # Tear down any previously-installed function breakpoints. We use
    # the per-id delete so source-line breakpoints + watchpoints
    # aren't affected.
    for old_id in session.function_breakpoints.clear_all():
        try:
            bridge.command(f"-break-delete {old_id}", timeout=2.0)
        except (TimeoutError, RuntimeError):
            pass
    raw_breakpoints = args.get("breakpoints") or []
    if not isinstance(raw_breakpoints, list):
        raw_breakpoints = []
    out: List[Dict[str, Any]] = []
    for bp in raw_breakpoints:
        if not isinstance(bp, dict):
            out.append({"verified": False, "message": "malformed breakpoint entry"})
            continue
        name = bp.get("name")
        if not isinstance(name, str) or not name.strip():
            out.append({"verified": False, "message": "missing function name"})
            continue
        condition = bp.get("condition")
        if not isinstance(condition, str):
            condition = None
        elif not condition.strip():
            condition = None
        cmd = build_function_breakpoint_command(name, condition=condition)
        if cmd is None:
            out.append({"verified": False, "message": "could not compose breakpoint"})
            continue
        try:
            result = bridge.command(cmd, timeout=5.0)
        except TimeoutError as exc:
            out.append({"verified": False, "message": f"gdb timed out: {exc}"})
            continue
        except RuntimeError as exc:
            out.append({"verified": False, "message": f"gdb error: {exc}"})
            continue
        if not result.ok:
            err = result.error_message or "could not set function breakpoint"
            # Symbol-resolution failures are soft: the entry stays in
            # the response as ``verified: false`` so the IDE can
            # render a pending indicator instead of treating the
            # whole request as failed. Other errors (e.g. bad
            # condition syntax) also surface as unverified — the
            # message field tells the user what went wrong.
            entry: Dict[str, Any] = {"verified": False, "message": err}
            if is_unresolved_function_error(err):
                # No gdb id assigned in this case (gdb didn't accept
                # the install), so we don't register anything in the
                # manager — just report the entry back to the IDE.
                pass
            out.append(entry)
            continue
        parsed = parse_function_breakpoint_response(result.fields)
        if parsed is None:
            out.append(
                {
                    "verified": False,
                    "message": "gdb returned no breakpoint id",
                }
            )
            continue
        record = FunctionBreakpointRecord(
            gdb_id=parsed["id"],
            name=name,
            condition=condition,
            verified=bool(parsed.get("verified")),
            line=parsed.get("line"),
            source_path=parsed.get("source_path"),
        )
        session.function_breakpoints.register(record)
        entry = {
            "verified": bool(parsed.get("verified")),
            "id": parsed["id"],
        }
        # Include the resolved source location so the IDE can render
        # a gutter indicator at the function entry. Best-effort:
        # symbols without DWARF line info come back without a file /
        # line, which is fine — the IDE just won't show a marker.
        if parsed.get("line") is not None:
            entry["line"] = parsed["line"]
        if parsed.get("source_path"):
            entry["source"] = {
                "path": parsed["source_path"],
                "name": os.path.basename(parsed["source_path"]),
            }
        out.append(entry)
    send_response(session, req, body={"breakpoints": out})


def handle_set_instruction_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``setInstructionBreakpoints`` request.

    Replaces the active set of instruction breakpoints with the
    supplied list. Each entry has
    ``{instructionReference, offset?, condition?, hitCondition?}``;
    we forward each to gdb via ``-break-insert *0xADDR`` (with
    ``offset`` added to the parsed address) and record the assigned
    breakpoint number so a later ``*stopped,bkptno=...`` can be
    routed to the right ``"Stopped at instruction <ref>"``
    description.

    Behaviour notes:

    * Complete-replacement semantics — every call tears down the
      previously-installed instruction breakpoints via
      ``-break-delete <id>`` (per id, so source-line breakpoints +
      watchpoints + function breakpoints survive) and reinstalls
      from scratch.
    * References that gdb can't parse (malformed hex, negative
      addresses) come back ``verified: false`` with a clear
      ``message`` — the entry is reported but not active. Other
      gdb errors (e.g. address not in any loaded module yet) get
      the same shape; the DAP client can render them differently
      based on the message.
    * The ``hitCondition`` field is accepted but ignored — we
      declare ``supportsHitConditionalBreakpoints: false`` so well-
      behaved clients won't send it; we tolerate it for robustness.
    """
    args = req.get("arguments", {}) or {}
    bridge = session.bridge
    if bridge is None:
        send_response(
            session, req, success=False, message="not launched"
        )
        return
    # Tear down any previously-installed instruction breakpoints. Use
    # the per-id delete so other breakpoint kinds aren't affected.
    for old_id in session.instruction_breakpoints.clear_all():
        try:
            bridge.command(f"-break-delete {old_id}", timeout=2.0)
        except (TimeoutError, RuntimeError):
            pass
    raw_breakpoints = args.get("breakpoints") or []
    if not isinstance(raw_breakpoints, list):
        raw_breakpoints = []
    out: List[Dict[str, Any]] = []
    for bp in raw_breakpoints:
        if not isinstance(bp, dict):
            out.append({"verified": False, "message": "malformed breakpoint entry"})
            continue
        ref = bp.get("instructionReference")
        if not isinstance(ref, str) or not ref.strip():
            out.append(
                {"verified": False, "message": "missing instructionReference"}
            )
            continue
        offset_raw = bp.get("offset", 0)
        try:
            offset = int(offset_raw) if offset_raw is not None else 0
        except (TypeError, ValueError):
            offset = 0
        condition = bp.get("condition")
        if not isinstance(condition, str):
            condition = None
        elif not condition.strip():
            condition = None
        cmd = build_instruction_breakpoint_command(
            ref, offset=offset, condition=condition
        )
        if cmd is None:
            out.append(
                {
                    "verified": False,
                    "message": f"invalid instructionReference: {ref!r}",
                }
            )
            continue
        try:
            result = bridge.command(cmd, timeout=5.0)
        except TimeoutError as exc:
            out.append({"verified": False, "message": f"gdb timed out: {exc}"})
            continue
        except RuntimeError as exc:
            out.append({"verified": False, "message": f"gdb error: {exc}"})
            continue
        if not result.ok:
            out.append(
                {
                    "verified": False,
                    "message": result.error_message
                    or "could not set instruction breakpoint",
                }
            )
            continue
        bk = result.fields.get("bkpt")
        bp_id: Optional[int] = None
        addr_resolved: Optional[str] = None
        if isinstance(bk, dict):
            num = bk.get("number")
            if isinstance(num, str) and num.isdigit():
                bp_id = int(num)
            elif isinstance(num, int):
                bp_id = num
            addr_resolved = bk.get("addr") if isinstance(bk.get("addr"), str) else None
        if bp_id is None:
            out.append(
                {"verified": False, "message": "gdb returned no breakpoint id"}
            )
            continue
        # gdb's <PENDING> address means the symbol / address hasn't
        # resolved yet (e.g. a dlopen'd module). For machine addresses
        # this is rare but possible if the address points into a not-
        # yet-loaded shared library. We surface verified=true unless
        # gdb specifically returned <PENDING>.
        verified = bool(addr_resolved) and addr_resolved != "<PENDING>"
        record = InstructionBreakpointRecord(
            gdb_id=bp_id,
            instruction_reference=ref,
            offset=offset,
            resolved_address=None,
            condition=condition,
        )
        session.instruction_breakpoints.register(record)
        entry: Dict[str, Any] = {"verified": verified, "id": bp_id}
        if isinstance(addr_resolved, str):
            entry["instructionReference"] = addr_resolved
        out.append(entry)
    send_response(session, req, body={"breakpoints": out})


def handle_disassemble(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``disassemble`` request.

    Args (per DAP spec):
      * ``memoryReference`` (string, required) — opaque address
        reference, typically a previously-issued
        ``instructionPointerReference``. We accept any ``"0xADDR"``
        / ``"ADDR"`` / decimal form.
      * ``offset`` (int, optional) — byte offset from
        ``memoryReference`` to start. Defaults to 0.
      * ``instructionOffset`` (int, optional) — instruction-count
        offset from ``memoryReference`` (negative means "before").
        Defaults to 0.
      * ``instructionCount`` (int, required) — number of instructions
        to return.
      * ``resolveSymbols`` (bool, optional) — whether to resolve
        symbol names. We always return symbols if gdb provides them
        (free at the MI layer).

    Returns ``{instructions: DisassembledInstruction[]}``. If the
    address range can't be disassembled (e.g. unmapped memory), we
    return ``success: false`` with gdb's error message.

    Per DAP, the IDE expects exactly ``instructionCount``
    instructions; if gdb returns fewer (e.g. a short function), we
    pad with ``{address, instruction: "??"}`` placeholders so the
    client's array slicing stays predictable.
    """
    args = req.get("arguments", {}) or {}
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    ref = args.get("memoryReference")
    if not isinstance(ref, str) or not ref.strip():
        send_response(
            session,
            req,
            success=False,
            message="disassemble requires 'memoryReference'",
        )
        return
    # Byte offset (rare; DAP spec lets the client request
    # disassembly at base+offset). We add it to the parsed address.
    byte_offset_raw = args.get("offset", 0)
    try:
        byte_offset = int(byte_offset_raw) if byte_offset_raw is not None else 0
    except (TypeError, ValueError):
        byte_offset = 0
    # Instruction-count offset: how many instructions before/after
    # the anchor. We approximate via 4 bytes/insn (see
    # ``parse_memory_reference``).
    insn_offset_raw = args.get("instructionOffset", 0)
    try:
        insn_offset = int(insn_offset_raw) if insn_offset_raw is not None else 0
    except (TypeError, ValueError):
        insn_offset = 0
    count_raw = args.get("instructionCount", 0)
    try:
        count = int(count_raw) if count_raw is not None else 0
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        send_response(session, req, body={"instructions": []})
        return
    # Compose the start address: parse the memory reference, apply
    # the byte offset, then the (approximate) instruction offset.
    start_addr = parse_memory_reference(ref, insn_offset)
    if start_addr is None:
        send_response(
            session,
            req,
            success=False,
            message=f"invalid memoryReference: {ref!r}",
        )
        return
    # Apply byte offset to the start address.
    start_int = int(start_addr, 16)
    if byte_offset:
        start_int = start_int + byte_offset
    if start_int < 0:
        send_response(
            session,
            req,
            success=False,
            message="resulting address is negative",
        )
        return
    start_hex = f"0x{start_int:x}"
    # End address: pad generously (15 bytes is the max x86-64 insn
    # length, so 15 * count is an upper bound). gdb stops at the
    # first instruction past ``-e END`` so over-padding is harmless.
    end_int = start_int + max(count * 15, 64)
    end_hex = f"0x{end_int:x}"
    cmd = build_disassemble_command(start_hex, end_hex, mode=0)
    if cmd is None:
        send_response(
            session,
            req,
            success=False,
            message="could not compose disassemble command",
        )
        return
    try:
        result = bridge.command(cmd, timeout=5.0)
    except TimeoutError as exc:
        send_response(session, req, success=False, message=f"gdb timed out: {exc}")
        return
    except RuntimeError as exc:
        send_response(session, req, success=False, message=f"gdb error: {exc}")
        return
    if not result.ok:
        send_response(
            session,
            req,
            success=False,
            message=result.error_message or "disassemble failed",
        )
        return
    parsed = parse_disassemble_response(result.fields)
    # Truncate or pad to the requested count so the client's array
    # slicing is predictable. DAP expects EXACTLY ``instructionCount``
    # entries.
    truncated = parsed[:count]
    if len(truncated) < count:
        # Pad with placeholder rows. Address advances by 4 bytes
        # (same approximation as parse_memory_reference) so the IDE
        # still has a monotonic address column.
        last_addr = (
            int(truncated[-1].address, 16) + 4
            if truncated
            else start_int + len(truncated) * 4
        )
        for i in range(count - len(truncated)):
            from nova_dap.disassembly import DisassembledInstruction
            truncated.append(
                DisassembledInstruction(
                    address=f"0x{last_addr + i * 4:x}",
                    instruction="??",
                )
            )
    body = {"instructions": [i.to_dap_dict() for i in truncated]}
    send_response(session, req, body=body)


def handle_set_exception_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    send_response(session, req, body={})


def handle_configuration_done(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    # If the inferior has never run, -exec-run; otherwise -exec-continue.
    # In non-stop mode -exec-continue defaults to the current thread
    # only — we want all threads to start running, so add ``--all``.
    if session.stopped_reported:
        cmd = "-exec-continue --all" if session.non_stop else "-exec-continue"
    else:
        cmd = "-exec-run"
    result = bridge.command(cmd)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    send_response(session, req, body={})


def _refresh_threads_from_gdb(session: Session) -> None:
    """Issue ``-thread-info`` and reconcile ``session.known_threads``
    with gdb's actual thread list. Used as a backstop in case we
    missed an ``=thread-created`` notification (e.g. when threads
    spawn between MI commands)."""
    bridge = session.bridge
    if bridge is None:
        return
    try:
        result = bridge.command("-thread-info", timeout=2.0)
    except (TimeoutError, RuntimeError):
        return
    if not result.ok:
        return
    threads = result.fields.get("threads") or []
    if not isinstance(threads, list):
        return
    seen: List[int] = []
    for entry in threads:
        if not isinstance(entry, dict):
            continue
        tid = _parse_thread_id(entry.get("id"))
        if tid is None:
            continue
        seen.append(tid)
        name = entry.get("target-id")
        if not isinstance(name, str):
            name = ""
        # Prefer a short, human-readable name; gdb's ``target-id`` is
        # like "Thread 0x...  (LWP 12345)". Stash the more useful
        # ``name`` field if present (per-thread name set by
        # pthread_setname_np); otherwise fall back to "thread-N".
        explicit_name = entry.get("name")
        if isinstance(explicit_name, str) and explicit_name:
            display = explicit_name
        elif tid == 1:
            display = "main"
        else:
            display = f"thread-{tid}"
        state = entry.get("state")
        running = isinstance(state, str) and state == "running"
        session.register_thread(tid, name=display, running=running)
    # Drop threads gdb no longer knows about.
    with session.threads_lock:
        stale = [tid for tid in session.known_threads if tid not in seen]
        for tid in stale:
            session.known_threads.pop(tid, None)


def handle_threads(session: Session, req: Dict[str, Any]) -> None:
    # Reconcile with gdb. If the bridge isn't up yet (pre-launch) or
    # the inferior hasn't started, return whatever we have cached
    # (always at least thread 1 from launch).
    _refresh_threads_from_gdb(session)
    threads = session.thread_snapshot()
    if not threads:
        threads = [{"id": session.threadId, "name": "main"}]
    send_response(session, req, body={"threads": threads})


def handle_stack_trace(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    start = int(args.get("startFrame") or 0)
    levels_arg = args.get("levels")
    levels = int(levels_arg) if levels_arg else 0  # 0 means "all"
    thread_id = _parse_thread_id(args.get("threadId")) or session.threadId
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, body={"stackFrames": [], "totalFrames": 0})
        return
    # ``-stack-list-frames`` is implicitly per-thread; in non-stop mode
    # we explicitly route via ``--thread`` so a request that targets a
    # stopped thread always sees that thread's frames even if gdb's
    # ``current-thread-id`` has drifted.
    cmd = "-stack-list-frames"
    if session.non_stop:
        cmd = f"-stack-list-frames --thread {thread_id}"
    result = bridge.command(cmd)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    raw_stack = result.fields.get("stack") or []
    frames: List[Dict[str, Any]] = []
    if isinstance(raw_stack, list):
        for entry in raw_stack:
            # Entries are ``{"frame": {...}}`` for MI list-of-results.
            if isinstance(entry, dict) and "frame" in entry:
                fr = entry["frame"]
            elif isinstance(entry, dict):
                fr = entry
            else:
                continue
            if not isinstance(fr, dict):
                continue
            level_raw = fr.get("level", "0")
            try:
                level = int(level_raw) if isinstance(level_raw, str) else 0
            except ValueError:
                level = 0
            name = fr.get("func") or "<unknown>"
            line_raw = fr.get("line", "0")
            try:
                line = int(line_raw) if isinstance(line_raw, str) else 0
            except ValueError:
                line = 0
            fullname = fr.get("fullname") or fr.get("file") or ""
            short = fr.get("file") or os.path.basename(fullname) if fullname else ""
            frame: Dict[str, Any] = {
                # Frame ids are stable per (threadId, level) so a
                # ``variables`` request for thread A's frame doesn't
                # accidentally select thread B's frame at the same
                # level.
                "id": session.frame_id_for(thread_id, level),
                "name": name if isinstance(name, str) else "<unknown>",
                "line": line,
                "column": 1,
            }
            if fullname:
                frame["source"] = {"path": fullname, "name": short or os.path.basename(fullname)}
            frames.append(frame)
    total = len(frames)
    sliced = frames[start : start + levels] if levels else frames[start:]
    send_response(session, req, body={"stackFrames": sliced, "totalFrames": total})


def handle_scopes(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    frame_id = int(args.get("frameId") or 1)
    var_ref = session.alloc_var_ref(frame_id)
    send_response(
        session,
        req,
        body={
            "scopes": [
                {
                    "name": "Locals",
                    "variablesReference": var_ref,
                    "namedVariables": 0,
                    "indexedVariables": 0,
                    "expensive": False,
                }
            ]
        },
    )


def handle_variables(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    var_ref = int(args.get("variablesReference") or 0)
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, body={"variables": []})
        return
    frame_id = session.frame_refs.get(var_ref, 0)
    thread_id, frame_level = session.frame_lookup_by_id(frame_id)
    # Select the right thread first (no-op in all-stop mode where
    # there's only one stopped thread), then the right frame, then
    # list locals + arguments. Routing via ``--thread`` on every MI
    # command is also fine but adds noise; selecting once is enough.
    if session.non_stop:
        bridge.command(f"-thread-select {thread_id}")
    bridge.command(f"-stack-select-frame {frame_level}")
    result = bridge.command("-stack-list-variables --all-values")
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    raw_vars = result.fields.get("variables") or []
    out: List[Dict[str, Any]] = []
    if isinstance(raw_vars, list):
        for entry in raw_vars:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or ""
            value = entry.get("value")
            if value is None:
                value = ""
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            out.append(
                {
                    "name": name,
                    "value": value,
                    "variablesReference": 0,
                    "type": "",
                }
            )
    send_response(session, req, body={"variables": out})


def handle_evaluate(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``evaluate`` request.

    The client sends ``{expression, frameId, context}`` where
    ``context`` is one of ``"watch"`` / ``"repl"`` / ``"hover"`` /
    ``"clipboard"``. We route the expression through gdb's
    ``-data-evaluate-expression`` MI command with ``--thread`` /
    ``--frame`` resolved from the DAP frame id table (the same
    mapping ``variables`` uses).

    Response shape::

        {
          "result": "<display text>",
          "type":   "int" | "str" | "char" | "ptr" | "bool" | "raw",
          "variablesReference": 0
        }

    ``variablesReference: 0`` means the result is a leaf — DAP clients
    treat non-zero refs as expandable structured values, which we
    don't synthesise yet. (List / struct introspection is the next
    R-round; for now the user sees ``"0x7fff..."`` for opaque
    pointers, which still matches what ``print`` shows in CLI gdb.)
    """
    args = req.get("arguments", {}) or {}
    expression = args.get("expression")
    if not isinstance(expression, str) or not expression:
        send_response(
            session,
            req,
            success=False,
            message="evaluate requires non-empty 'expression' argument",
        )
        return
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    # Resolve the frame id back to (threadId, frame_level). When the
    # client doesn't supply a frame id (e.g. an ``evaluate`` without
    # a current frame — happens for ``context=repl`` before the first
    # stop) we fall back to the session's current thread + frame 0.
    frame_id_raw = args.get("frameId")
    if frame_id_raw is not None:
        try:
            frame_id = int(frame_id_raw)
        except (TypeError, ValueError):
            frame_id = 0
        thread_id, frame_level = session.frame_lookup_by_id(frame_id)
    else:
        thread_id, frame_level = (session.threadId, 0)

    # In all-stop mode we don't pass ``--thread`` because gdb has one
    # selected thread and the routing is implicit. In non-stop mode
    # we MUST pass ``--thread`` so the right scope is in view.
    eff_thread: Optional[int] = thread_id if session.non_stop else None
    eff_frame: Optional[int] = frame_level if session.non_stop else None
    if not session.non_stop:
        # All-stop: select the frame so the implicit scope matches.
        # ``-stack-select-frame`` is a no-op when the level matches
        # gdb's current frame, which is the common case for
        # single-thread programs.
        bridge.command(f"-stack-select-frame {frame_level}")

    eval_result = evaluate_via_bridge(
        bridge,
        expression,
        thread_id=eff_thread,
        frame_level=eff_frame,
        timeout=5.0,
    )
    if not eval_result.ok or eval_result.decoded is None:
        send_response(
            session,
            req,
            success=False,
            message=eval_result.message or "evaluate failed",
        )
        return
    decoded = eval_result.decoded
    body: Dict[str, Any] = {
        "result": decoded.display,
        "type": decoded.type,
        "variablesReference": 0,
    }
    send_response(session, req, body=body)


def handle_data_breakpoint_info(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``dataBreakpointInfo`` request.

    The client asks: "could I set a data breakpoint on this variable?"
    For a named variable in a scope (``variablesReference`` >= 1, plus
    a ``name`` string) we return a freshly-minted ``dataId`` the
    client will pass back in ``setDataBreakpoints``, a human-readable
    description, the list of access types we support, and
    ``canPersist: false`` (watchpoints don't survive a relaunch).

    The DAP spec also permits the client to query "is the expression
    on the right-hand side of an assignment data-breakpointable?" via
    ``{variablesReference: 0, name: <expression>}``. We support that
    too — the expression is opaque to us; we just round-trip it as
    the ``dataId``'s ``name`` field. gdb will then try to set a
    watchpoint on it; if the expression isn't a watchable lvalue gdb
    returns an error which we surface as ``verified: false`` in
    ``setDataBreakpoints``."""
    args = req.get("arguments", {}) or {}
    name = args.get("name")
    if not isinstance(name, str) or not name:
        send_response(
            session,
            req,
            success=False,
            message="dataBreakpointInfo requires non-empty 'name' argument",
        )
        return
    # ``variablesReference`` is optional — 0 means "evaluate the name
    # as a free-form expression in the current scope".
    var_ref_raw = args.get("variablesReference")
    var_ref: Optional[int] = None
    if var_ref_raw is not None:
        try:
            var_ref = int(var_ref_raw)
        except (TypeError, ValueError):
            var_ref = None
    # If the client gave us a varRef, we can recover the originating
    # frame id. That frame id ends up in the encoded dataId so a later
    # ``setDataBreakpoints`` request knows which scope to install the
    # watchpoint in. Without a frame id we still produce a dataId — it
    # just falls back to the session's current frame at watch time.
    frame_id: Optional[int] = None
    if var_ref is not None and var_ref > 0:
        frame_id = session.frame_refs.get(var_ref)
    data_id = encode_data_id(
        name, frame_id=frame_id, variables_reference=var_ref
    )
    description = name
    # If we have a bridge + frame_id, append the current value to the
    # description so the IDE shows e.g. "counter = 0" in its "add data
    # breakpoint" dialog. Best-effort — silently fall back to the bare
    # name if evaluation fails.
    bridge = session.bridge
    if bridge is not None and frame_id is not None:
        try:
            thread_id, frame_level = session.frame_lookup_by_id(frame_id)
            ev = evaluate_via_bridge(
                bridge,
                name,
                thread_id=thread_id if session.non_stop else None,
                frame_level=frame_level if session.non_stop else None,
                timeout=2.0,
            )
            if ev.ok and ev.decoded is not None:
                description = f"{name} = {ev.decoded.display}"
        except (TimeoutError, RuntimeError):
            pass
    body: Dict[str, Any] = {
        "dataId": data_id,
        "description": description,
        "accessTypes": default_access_types(),
        "canPersist": False,
    }
    send_response(session, req, body=body)


def handle_set_data_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    """DAP ``setDataBreakpoints`` request.

    Replaces the active set of watchpoints with the supplied list.
    Each entry has ``{dataId, accessType?}``; we decode the dataId,
    select the right frame (so the watch lands in the right scope),
    and issue a gdb ``-break-watch [-r|-a] <expr>``.

    Behaviour notes:

    * Like ``setBreakpoints``, we tear down the existing watchpoint
      set first so re-sends don't double-install. The teardown uses
      ``-break-delete <id>`` per watchpoint so source-line breakpoints
      survive (``-break-delete`` without args nukes everything).
    * Each result entry mirrors the request shape: ``{verified, id?,
      message?}``. ``verified=true`` iff gdb confirmed the install.
    * Watchpoints that fail to install (e.g. variable out of scope,
      no hardware watchpoint slots left) come back ``verified=false``
      with gdb's error message in ``message`` so the IDE can render
      a useful diagnostic."""
    args = req.get("arguments", {}) or {}
    bridge = session.bridge
    if bridge is None:
        send_response(
            session,
            req,
            success=False,
            message="not launched",
        )
        return
    # Tear down any previously-installed watchpoints. We use the
    # per-id delete so source-line breakpoints set via
    # ``setBreakpoints`` aren't affected.
    for old_id in session.watchpoints.clear_all():
        try:
            bridge.command(f"-break-delete {old_id}", timeout=2.0)
        except (TimeoutError, RuntimeError):
            pass
    raw_breakpoints = args.get("breakpoints") or []
    if not isinstance(raw_breakpoints, list):
        raw_breakpoints = []
    out: List[Dict[str, Any]] = []
    for bp in raw_breakpoints:
        if not isinstance(bp, dict):
            out.append({"verified": False, "message": "malformed breakpoint entry"})
            continue
        data_id = bp.get("dataId")
        if not isinstance(data_id, str) or not data_id:
            out.append({"verified": False, "message": "missing dataId"})
            continue
        decoded = decode_data_id(data_id)
        if decoded is None:
            out.append({"verified": False, "message": "unrecognised dataId"})
            continue
        name = decoded["n"]
        access_type = bp.get("accessType") or "write"
        if access_type_flag(access_type) is None:
            out.append(
                {"verified": False, "message": f"unsupported accessType: {access_type!r}"}
            )
            continue
        # Route to the right scope so the watch lives in the right
        # frame's variable. In non-stop mode we ``-thread-select``;
        # in all-stop mode the current thread is implicit.
        frame_id = decoded.get("f")
        if isinstance(frame_id, int):
            thread_id, frame_level = session.frame_lookup_by_id(frame_id)
            if session.non_stop:
                try:
                    bridge.command(f"-thread-select {thread_id}", timeout=2.0)
                except (TimeoutError, RuntimeError):
                    pass
            try:
                bridge.command(f"-stack-select-frame {frame_level}", timeout=2.0)
            except (TimeoutError, RuntimeError):
                pass
        cmd = build_watch_command(name, access_type)
        if cmd is None:
            out.append({"verified": False, "message": "could not compose watch"})
            continue
        try:
            result = bridge.command(cmd, timeout=5.0)
        except TimeoutError as exc:
            out.append({"verified": False, "message": f"gdb timed out: {exc}"})
            continue
        except RuntimeError as exc:
            out.append({"verified": False, "message": f"gdb error: {exc}"})
            continue
        if not result.ok:
            out.append(
                {
                    "verified": False,
                    "message": result.error_message or "could not set watchpoint",
                }
            )
            continue
        gdb_id = parse_watchpoint_id(result.fields)
        if gdb_id is None:
            out.append({"verified": False, "message": "gdb returned no watchpoint id"})
            continue
        record = WatchpointRecord(
            gdb_id=gdb_id,
            data_id=data_id,
            name=name,
            access_type=access_type,
            description=f"watch {name} ({access_type})",
        )
        session.watchpoints.register(record)
        out.append({"verified": True, "id": gdb_id})
    send_response(session, req, body={"breakpoints": out})


def _thread_args(session: Session, req_args: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    """Extract ``(threadId, singleThread)`` from a DAP request body.

    Per the DAP spec, ``singleThread`` is optional and defaults to
    false; clients that don't set it expect the whole process to be
    resumed/stepped. ``threadId`` is required by the spec for all
    step / continue requests."""
    tid = _parse_thread_id(req_args.get("threadId"))
    single = bool(req_args.get("singleThread", False))
    return tid, single


def _exec_with_thread(
    session: Session, base_cmd: str, thread_id: Optional[int], single: bool
) -> str:
    """Compose a gdb MI exec command that targets either one thread
    (``singleThread=true``) or all threads (default). In all-stop mode
    we always issue the command without ``--thread`` because gdb
    resumes every stopped thread on ``-exec-continue`` regardless."""
    if not session.non_stop:
        return base_cmd
    if single and thread_id is not None:
        return f"{base_cmd} --thread {thread_id}"
    # ``--all`` resumes every stopped thread in non-stop mode. Without
    # it gdb only resumes the currently-selected thread, which is the
    # opposite of what a DAP ``continue`` without ``singleThread``
    # asks for.
    return f"{base_cmd} --all"


def handle_continue(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    args = req.get("arguments", {}) or {}
    tid, single = _thread_args(session, args)
    cmd = _exec_with_thread(session, "-exec-continue", tid, single)
    result = bridge.command(cmd)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    all_continued = not (single and session.non_stop and tid is not None)
    # Mark thread state in our mirror so a follow-up ``threads`` query
    # reflects the resume immediately (gdb's ``*running`` async record
    # will also do this but it's racy w.r.t. the response).
    if single and tid is not None:
        session.mark_thread_running(tid, True)
    else:
        with session.threads_lock:
            for entry in session.known_threads.values():
                entry["running"] = True
    send_response(session, req, body={"allThreadsContinued": all_continued})


def handle_next(session: Session, req: Dict[str, Any]) -> None:
    _step(session, req, "-exec-next")


def handle_step_in(session: Session, req: Dict[str, Any]) -> None:
    _step(session, req, "-exec-step")


def handle_step_out(session: Session, req: Dict[str, Any]) -> None:
    _step(session, req, "-exec-finish")


def _step(session: Session, req: Dict[str, Any], mi: str) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    args = req.get("arguments", {}) or {}
    tid, single = _thread_args(session, args)
    # ``granularity`` (per DAP spec 1.51) controls how far the step
    # advances. ``"statement"`` (default) and ``"line"`` map to gdb's
    # source-level stepping; ``"instruction"`` reroutes to the
    # ``-exec-{next,step}-instruction`` MI commands so the IDE's
    # disassembly view can advance the PC by exactly one machine
    # instruction at a time. See ``nova_dap.disassembly.map_step_command``.
    granularity = args.get("granularity")
    base_mi = map_step_command(mi, granularity)
    # Step commands are intrinsically per-thread in DAP — they target a
    # specific thread id. In non-stop mode we route via ``--thread`` so
    # other threads keep running (or stay stopped) independently. In
    # all-stop mode gdb steps the currently-selected thread and
    # resumes others to do it; that matches DAP's expectation when
    # ``singleThread`` is false. If ``singleThread`` is true in
    # all-stop mode we fall back to a regular step (gdb has no way to
    # step a single thread while keeping others paused without
    # non-stop).
    if session.non_stop and tid is not None:
        cmd = f"{base_mi} --thread {tid}"
    else:
        cmd = base_mi
    if tid is not None:
        session.mark_thread_running(tid, True)
    result = bridge.command(cmd)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    send_response(session, req, body={})


def handle_pause(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    args = req.get("arguments", {}) or {}
    tid, _single = _thread_args(session, args)
    # ``pause`` always targets one thread in DAP — clients that want
    # to pause everything send one ``pause`` per thread. We honour
    # that by routing via ``--thread``. If the client sends ``pause``
    # without a thread id we interrupt everything (``--all``).
    if session.non_stop:
        cmd = f"-exec-interrupt --thread {tid}" if tid is not None else "-exec-interrupt --all"
    else:
        cmd = "-exec-interrupt"
    bridge.command(cmd)
    send_response(session, req, body={})


def handle_disconnect(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is not None:
        try:
            bridge.command("-gdb-exit", timeout=2.0)
        except (TimeoutError, RuntimeError):
            pass
        bridge.terminate()
        session.bridge = None
    send_response(session, req, body={})


def handle_terminate(session: Session, req: Dict[str, Any]) -> None:
    handle_disconnect(session, req)


# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------


HANDLERS = {
    "initialize": handle_initialize,
    "launch": handle_launch,
    "setBreakpoints": handle_set_breakpoints,
    "setFunctionBreakpoints": handle_set_function_breakpoints,
    "setInstructionBreakpoints": handle_set_instruction_breakpoints,
    "setExceptionBreakpoints": handle_set_exception_breakpoints,
    "configurationDone": handle_configuration_done,
    "threads": handle_threads,
    "stackTrace": handle_stack_trace,
    "scopes": handle_scopes,
    "variables": handle_variables,
    "evaluate": handle_evaluate,
    "dataBreakpointInfo": handle_data_breakpoint_info,
    "setDataBreakpoints": handle_set_data_breakpoints,
    "disassemble": handle_disassemble,
    "continue": handle_continue,
    "next": handle_next,
    "stepIn": handle_step_in,
    "stepOut": handle_step_out,
    "pause": handle_pause,
    "disconnect": handle_disconnect,
    "terminate": handle_terminate,
}


def dispatch(session: Session, message: Dict[str, Any]) -> None:
    if message.get("type") != "request":
        return
    command = message.get("command", "")
    handler = HANDLERS.get(command)
    if handler is None:
        send_response(
            session,
            message,
            success=False,
            message=f"unsupported command: {command}",
        )
        return
    try:
        handler(session, message)
    except Exception as exc:
        _log(f"handler {command} raised: {exc!r}")
        send_response(
            session,
            message,
            success=False,
            message=f"internal error in {command}: {exc}",
        )


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def serve(in_stream, out_stream) -> int:
    session = Session(out_stream=out_stream)
    while True:
        msg = read_message(in_stream)
        if msg is None:
            break
        dispatch(session, msg)
        if msg.get("command") == "disconnect":
            break
    if session.bridge is not None:
        try:
            session.bridge.terminate()
        except Exception:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nova-dap",
        description="Nova Debug Adapter Protocol server (DAP <-> gdb MI3 bridge).",
    )
    parser.add_argument(
        "--version", action="version", version=f"nova-dap {__version__}"
    )
    parser.parse_args(argv if argv is not None else sys.argv[1:])
    in_stream = sys.stdin.buffer
    out_stream = sys.stdout.buffer
    return serve(in_stream, out_stream)


if __name__ == "__main__":
    sys.exit(main())
