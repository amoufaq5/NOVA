"""Nova Debug Adapter Protocol server.

A minimum-viable DAP server that drives ``gdb --interpreter=mi3`` under
the hood. VS Code (or any DAP-speaking client) sends DAP JSON over
stdio; we translate each request into one or more MI commands via
:class:`nova_dap.gdb_bridge.GdbBridge`, then translate gdb's async
records back into DAP events.

Supported requests
------------------

* ``initialize``          — declares MVP capabilities.
* ``launch``              — spawns gdb, loads the binary, optionally
                             sets ``cwd`` and ``args`` for the inferior.
* ``setBreakpoints``      — translates each source-line breakpoint via
                             ``-break-insert``.
* ``configurationDone``   — runs the inferior (``-exec-run``).
* ``threads``             — single-threaded stub.
* ``stackTrace``          — ``-stack-list-frames``.
* ``scopes``              — single "Locals" scope per frame.
* ``variables``           — ``-stack-list-variables --all-values``.
* ``continue`` / ``next`` / ``stepIn`` / ``stepOut`` — exec controls.
* ``disconnect``          — ``-gdb-exit``.

Supported events
----------------

* ``initialized``  — after ``initialize`` succeeds.
* ``stopped``      — emitted on any gdb ``*stopped`` async record.
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
from nova_dap.gdb_bridge import (
    GdbAsyncRecord,
    GdbBridge,
    GdbResult,
    gdb_available,
    quote_path,
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
    # variablesReference allocator.
    next_var_ref: int = 1000
    frame_refs: Dict[int, int] = field(default_factory=dict)  # vRef -> frameId

    def alloc_var_ref(self, frame_id: int) -> int:
        ref = self.next_var_ref
        self.next_var_ref += 1
        self.frame_refs[ref] = frame_id
        return ref


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
    elif rec.cls == "thread-group-exited":
        _handle_exited(session, rec)
    elif rec.cls == "thread-created":
        # Single-thread model for MVP: we always report id=1, so no-op.
        pass
    elif rec.cls == "thread-exited":
        pass


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
    }
    dap_reason = dap_reason_map.get(reason, reason or "pause")
    session.last_stop_reason = dap_reason
    session.stopped_reported = True
    body: Dict[str, Any] = {
        "reason": dap_reason,
        "threadId": session.threadId,
        "allThreadsStopped": True,
    }
    bkptno = rec.fields.get("bkptno")
    if isinstance(bkptno, str) and bkptno.isdigit():
        body["hitBreakpointIds"] = [int(bkptno)]
    send_event(session, "stopped", body)


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
        "supportsConditionalBreakpoints": False,
        "supportsFunctionBreakpoints": False,
        "supportsHitConditionalBreakpoints": False,
        "supportsEvaluateForHovers": False,
        "supportsSetVariable": False,
        "supportsCompletionsRequest": False,
        "supportsModulesRequest": False,
        "supportsLogPoints": False,
        "supportsExceptionInfoRequest": False,
        "supportsDelayedStackTraceLoading": False,
        # We never need separate ``threads`` to be requested explicitly,
        # but VS Code asks anyway after the first stopped event.
        "supportsClipboardContext": False,
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
        result = bridge.command(f"-break-insert {quote_path(loc)}")
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
    # MVP: stub — we declared no support but VS Code may still ask.
    send_response(session, req, body={"breakpoints": []})


def handle_set_exception_breakpoints(session: Session, req: Dict[str, Any]) -> None:
    send_response(session, req, body={})


def handle_configuration_done(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    # If the inferior has never run, -exec-run; otherwise -exec-continue.
    cmd = "-exec-continue" if session.stopped_reported else "-exec-run"
    result = bridge.command(cmd)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    send_response(session, req, body={})


def handle_threads(session: Session, req: Dict[str, Any]) -> None:
    send_response(
        session,
        req,
        body={"threads": [{"id": session.threadId, "name": "main"}]},
    )


def handle_stack_trace(session: Session, req: Dict[str, Any]) -> None:
    args = req.get("arguments", {}) or {}
    start = int(args.get("startFrame") or 0)
    levels_arg = args.get("levels")
    levels = int(levels_arg) if levels_arg else 0  # 0 means "all"
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, body={"stackFrames": [], "totalFrames": 0})
        return
    result = bridge.command("-stack-list-frames")
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
                "id": level + 1,  # DAP frame ids are arbitrary nonzero ints
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
    var_ref = session.alloc_var_ref(frame_id - 1)  # back-translate to MI level
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
    frame_level = session.frame_refs.get(var_ref, 0)
    # Select the frame, then list its locals + arguments.
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


def handle_continue(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    result = bridge.command("-exec-continue")
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    send_response(session, req, body={"allThreadsContinued": True})


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
    result = bridge.command(mi)
    if not result.ok:
        send_response(session, req, success=False, message=result.error_message)
        return
    send_response(session, req, body={})


def handle_pause(session: Session, req: Dict[str, Any]) -> None:
    bridge = session.bridge
    if bridge is None:
        send_response(session, req, success=False, message="not launched")
        return
    bridge.command("-exec-interrupt")
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
    "setExceptionBreakpoints": handle_set_exception_breakpoints,
    "configurationDone": handle_configuration_done,
    "threads": handle_threads,
    "stackTrace": handle_stack_trace,
    "scopes": handle_scopes,
    "variables": handle_variables,
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
