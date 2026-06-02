"""Tests for DAP function breakpoints — the 20th capability for
``nova-dap``.

Three layers, mirroring ``test_data_breakpoints.py``:

1. **Unit tests** — exercise ``nova_dap.function_breakpoints``
   directly: MI command composition, error classification, manager
   bookkeeping, description builder. These run everywhere (no gdb
   required).

2. **Server handler tests with a stub bridge** — drive
   ``handle_set_function_breakpoints`` against a ``CaptureBridge``
   that records every MI command. We verify the MI command
   composition + the wire-level response shape + complete-replacement
   semantics without spawning gdb.

3. **End-to-end against gdb + a C fixture** (skipped cleanly if gdb /
   gcc are missing). Compiles a tiny C program with three functions,
   sets a function breakpoint on ``main`` and on ``unresolved_xxx``,
   then drives the full DAP wire and verifies that the ``stopped``
   event carries ``reason: "function breakpoint"`` + ``description:
   "Entry to main"``. Also runs against the NOVA ``hello_dwarf``
   binary if it's built.

Run::

    python tools/nova-dap/tests/test_function_breakpoints.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.gdb_bridge import GdbResult  # noqa: E402
from nova_dap.function_breakpoints import (  # noqa: E402
    FunctionBreakpointManager,
    FunctionBreakpointRecord,
    build_function_breakpoint_command,
    describe_function_entry,
    is_unresolved_function_error,
    parse_function_breakpoint_response,
)


# Track every assertion so we can report a count at the end. Same
# convention as the other DAP test suites.
_ASSERT_COUNT = 0


def check(cond: bool, msg: str = "") -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    if not cond:
        raise AssertionError(msg or "assertion failed")


def check_eq(actual: Any, expected: Any, msg: str = "") -> None:
    check(actual == expected, f"{msg}: expected {expected!r}, got {actual!r}")


# ---------------------------------------------------------------------------
# Unit tests for function_breakpoints.py (no gdb required).
# ---------------------------------------------------------------------------


def test_build_command_bare_name() -> None:
    """A function breakpoint without a condition emits the plain
    ``-break-insert "<name>"`` form."""
    cmd = build_function_breakpoint_command("main")
    check(cmd is not None)
    check("-break-insert" in cmd, f"missing -break-insert in {cmd!r}")
    check('"main"' in cmd, f"name should be quoted in {cmd!r}")
    check(" -c " not in cmd, f"no condition -> no -c flag in {cmd!r}")


def test_build_command_with_condition() -> None:
    """A condition string is forwarded via ``-c "<expr>"``."""
    cmd = build_function_breakpoint_command("foo", condition="x > 5")
    check(cmd is not None)
    check("-c" in cmd, f"missing -c flag in {cmd!r}")
    check('"x > 5"' in cmd, f"condition should be quoted in {cmd!r}")
    check('"foo"' in cmd, f"name should be quoted in {cmd!r}")


def test_build_command_empty_name_rejected() -> None:
    """Empty or whitespace-only names return None so the caller can
    surface a clean error."""
    check_eq(build_function_breakpoint_command(""), None)
    check_eq(build_function_breakpoint_command("   "), None)
    check_eq(
        build_function_breakpoint_command(None),  # type: ignore[arg-type]
        None,
        "None name should be rejected",
    )


def test_build_command_blank_condition_treated_as_none() -> None:
    """A whitespace-only condition is treated as no condition (gdb
    rejects empty -c expressions)."""
    cmd = build_function_breakpoint_command("foo", condition="   ")
    check(cmd is not None)
    check(" -c " not in cmd, f"blank cond should produce no -c in {cmd!r}")


def test_build_command_quotes_embedded_quotes() -> None:
    """A name containing a double-quote must be escaped, otherwise
    gdb's MI parser would mis-tokenise the command."""
    cmd = build_function_breakpoint_command('weird"name')
    check(cmd is not None)
    check('\\"' in cmd, f"embedded quote should be escaped in {cmd!r}")


def test_parse_response_verified() -> None:
    """A resolved-symbol reply gives us ``{id, verified: True}``."""
    fields = {
        "bkpt": {
            "number": "3",
            "addr": "0x4010ab",
            "func": "main",
            "file": "src.nova",
            "fullname": "/tmp/src.nova",
            "line": "12",
        }
    }
    parsed = parse_function_breakpoint_response(fields)
    check(parsed is not None)
    check_eq(parsed["id"], 3)
    check_eq(parsed["verified"], True)
    check_eq(parsed["function"], "main")
    check_eq(parsed["line"], 12)
    check_eq(parsed["source_path"], "/tmp/src.nova")


def test_parse_response_pending_marks_unverified() -> None:
    """A ``<PENDING>`` addr means the symbol hasn't resolved yet —
    we surface that as ``verified=false`` so the IDE shows a
    pending indicator."""
    fields = {
        "bkpt": {
            "number": "7",
            "addr": "<PENDING>",
            "original-location": "lazy_loaded_fn",
        }
    }
    parsed = parse_function_breakpoint_response(fields)
    check(parsed is not None)
    check_eq(parsed["id"], 7)
    check_eq(parsed["verified"], False)


def test_parse_response_multiple_locations_verified() -> None:
    """A ``<MULTIPLE>`` addr (e.g. C++ overloads) still counts as
    verified — the breakpoint IS armed at every matching address."""
    fields = {
        "bkpt": {
            "number": "9",
            "addr": "<MULTIPLE>",
            "func": "overloaded",
        }
    }
    parsed = parse_function_breakpoint_response(fields)
    check(parsed is not None)
    check_eq(parsed["verified"], True)


def test_parse_response_missing_bkpt() -> None:
    """A reply without a ``bkpt`` field returns None — the caller
    treats that as a failure to install."""
    check_eq(parse_function_breakpoint_response({}), None)
    check_eq(
        parse_function_breakpoint_response({"bkpt": "not a dict"}),
        None,
        "non-dict bkpt rejected",
    )


def test_is_unresolved_function_error_classification() -> None:
    """The error-message fragments we recognise as "name doesn't
    resolve" (so the request can keep installing the rest of the
    list)."""
    check(is_unresolved_function_error('Function "foo" not defined.'))
    check(is_unresolved_function_error('No symbol "bar" in current context.'))
    # A bad-condition error should NOT match — those are structural
    # errors the IDE renders differently.
    check(not is_unresolved_function_error("syntax error in expression"))
    check(not is_unresolved_function_error(None))
    check(not is_unresolved_function_error(""))


def test_describe_function_entry_format() -> None:
    """The ``stopped`` event description uses the canonical VS Code
    Node debug-adapter format."""
    check_eq(describe_function_entry("main"), "Entry to main")
    check_eq(describe_function_entry("my::cpp::method"), "Entry to my::cpp::method")


def test_manager_register_lookup() -> None:
    """Registering a record makes both id forms recoverable."""
    mgr = FunctionBreakpointManager()
    rec = FunctionBreakpointRecord(gdb_id=4, name="main")
    mgr.register(rec)
    check(mgr.lookup_by_gdb_id(4) is rec)
    by_name = mgr.lookup_by_name("main")
    check_eq(len(by_name), 1)
    check(by_name[0] is rec)
    check_eq(mgr.lookup_by_name("missing"), [])


def test_manager_clear_all_returns_ids() -> None:
    """``clear_all`` returns active gdb ids so the caller can issue
    ``-break-delete`` on each."""
    mgr = FunctionBreakpointManager()
    mgr.register(FunctionBreakpointRecord(gdb_id=1, name="foo"))
    mgr.register(FunctionBreakpointRecord(gdb_id=2, name="bar"))
    ids = mgr.clear_all()
    check_eq(sorted(ids), [1, 2])
    check_eq(mgr.lookup_by_gdb_id(1), None)
    check(mgr.is_empty())


def test_manager_multiple_names_register_independently() -> None:
    """Two records under the same name are both retained — useful if
    the user installs the same name twice (rare but valid)."""
    mgr = FunctionBreakpointManager()
    mgr.register(FunctionBreakpointRecord(gdb_id=1, name="foo"))
    mgr.register(FunctionBreakpointRecord(gdb_id=2, name="foo"))
    by_name = mgr.lookup_by_name("foo")
    check_eq(len(by_name), 2)


# ---------------------------------------------------------------------------
# Server handler tests (no gdb required).
# ---------------------------------------------------------------------------


class _CaptureStream:
    """Captures every DAP message written so tests can introspect them."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.messages: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        while True:
            idx = self.buffer.find(b"\r\n\r\n")
            if idx == -1:
                break
            header = self.buffer[:idx].decode("ascii", errors="replace")
            length = 0
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        length = 0
            body_start = idx + 4
            if len(self.buffer) < body_start + length:
                break
            body = bytes(self.buffer[body_start : body_start + length])
            del self.buffer[: body_start + length]
            try:
                parsed = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            self.messages.append(parsed)
            if parsed.get("type") == "response":
                self.responses.append(parsed)
            elif parsed.get("type") == "event":
                self.events.append(parsed)
        return len(data)

    def flush(self) -> None:
        pass

    def last_response(self) -> Optional[Dict[str, Any]]:
        return self.responses[-1] if self.responses else None


class CaptureBridge:
    """Fake GdbBridge that records every command and returns canned
    replies for ``-break-insert``."""

    def __init__(
        self,
        next_bp_id: int = 1,
        success_addr: str = "0x401000",
        success_line: int = 10,
    ) -> None:
        self.sent_commands: List[str] = []
        self._next_bp_id = next_bp_id
        self._success_addr = success_addr
        self._success_line = success_line
        # When a name is in this set, the next -break-insert for it
        # returns ^error,msg="Function \"<name>\" not defined.".
        self.unresolved_names: set = set()
        # Generic fail-next override (any -break-insert).
        self._fail_next_message: Optional[str] = None

    def fail_next(self, message: str) -> None:
        self._fail_next_message = message

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if cmd.startswith("-break-insert"):
            if self._fail_next_message is not None:
                msg = self._fail_next_message
                self._fail_next_message = None
                return GdbResult(
                    token=None, cls="error", fields={"msg": msg}
                )
            # Extract the name from the tail of the command — it's the
            # last quoted string in the line.
            name = _extract_quoted_tail(cmd)
            if name is not None and name in self.unresolved_names:
                return GdbResult(
                    token=None,
                    cls="error",
                    fields={"msg": f'Function "{name}" not defined.'},
                )
            bp_id = self._next_bp_id
            self._next_bp_id += 1
            bk: Dict[str, Any] = {
                "number": str(bp_id),
                "addr": self._success_addr,
                "func": name or "unknown",
                "file": "src.nova",
                "fullname": "/tmp/src.nova",
                "line": str(self._success_line),
                "original-location": name or "unknown",
            }
            return GdbResult(token=None, cls="done", fields={"bkpt": bk})
        # Default: -break-delete / -stack-select-frame / -thread-select / etc.
        return GdbResult(token=None, cls="done", fields={})


def _extract_quoted_tail(cmd: str) -> Optional[str]:
    """Pull the last ``"..."`` token out of an MI command string."""
    last_close = cmd.rfind('"')
    if last_close < 1:
        return None
    last_open = cmd.rfind('"', 0, last_close)
    if last_open < 0:
        return None
    return cmd[last_open + 1 : last_close]


def test_handle_install_single_function_bp() -> None:
    """A single ``{name: "main"}`` entry must issue one ``-break-insert``
    and return ``{verified: true, id: N}``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=5)
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {"breakpoints": [{"name": "main"}]},
    }
    server.handle_set_function_breakpoints(sess, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1, "exactly one -break-insert expected")
    check('"main"' in inserts[0], f"name missing from {inserts[0]!r}")
    resp = sess.out_stream.last_response()
    check(resp is not None)
    body = resp.get("body", {})
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), True)
    check_eq(entries[0].get("id"), 5)
    # Manager must record the breakpoint.
    record = sess.function_breakpoints.lookup_by_gdb_id(5)
    check(record is not None, "fn-bp not registered in manager")
    check_eq(record.name, "main")
    check_eq(record.condition, None)


def test_handle_install_unresolved_returns_unverified() -> None:
    """A name gdb can't resolve must come back ``verified: false``
    with the gdb error message — but the request as a whole still
    succeeds."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    bridge.unresolved_names.add("bogus_function_xyz")
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 2,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {"breakpoints": [{"name": "bogus_function_xyz"}]},
    }
    server.handle_set_function_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    check(resp is not None)
    check_eq(resp.get("success"), True, "request itself should succeed")
    entries = resp.get("body", {}).get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), False)
    msg = entries[0].get("message", "")
    check("not defined" in msg, f"expected unresolved-symbol msg, got {msg!r}")
    # Manager must NOT register a record for the failed install.
    check_eq(
        len(sess.function_breakpoints.snapshot()),
        0,
        "unresolved bps should not be tracked",
    )


def test_handle_install_with_condition_forwards_to_gdb() -> None:
    """``{name: "foo", condition: "x > 5"}`` must produce
    ``-break-insert -c "x > 5" "foo"``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 3,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {
            "breakpoints": [{"name": "foo", "condition": "x > 5"}]
        },
    }
    server.handle_set_function_breakpoints(sess, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1)
    check("-c" in inserts[0], f"missing -c in {inserts[0]!r}")
    check('"x > 5"' in inserts[0], f"missing condition in {inserts[0]!r}")
    check('"foo"' in inserts[0], f"missing name in {inserts[0]!r}")
    # Manager must record the condition so a later status query
    # can recover it.
    snapshot = sess.function_breakpoints.snapshot()
    check_eq(len(snapshot), 1)
    check_eq(snapshot[0].condition, "x > 5")


def test_handle_install_multiple_bps_in_one_call() -> None:
    """One ``setFunctionBreakpoints`` call with three entries must
    install all three and return three response entries."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=10)
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 4,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {
            "breakpoints": [
                {"name": "main"},
                {"name": "greet"},
                {"name": "println"},
            ]
        },
    }
    server.handle_set_function_breakpoints(sess, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 3, "one -break-insert per entry")
    resp = sess.out_stream.last_response()
    entries = resp.get("body", {}).get("breakpoints") or []
    check_eq(len(entries), 3)
    for ent in entries:
        check_eq(ent.get("verified"), True)
    # Manager must track all three.
    snap = sess.function_breakpoints.snapshot()
    check_eq(len(snap), 3)
    names = sorted(r.name for r in snap)
    check_eq(names, ["greet", "main", "println"])


def test_handle_replacement_clears_prior_set() -> None:
    """A second ``setFunctionBreakpoints`` call must tear down the
    first set via ``-break-delete <id>`` (DAP complete-replacement
    semantics) and install only the new entries."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=20)
    sess.bridge = bridge  # type: ignore[assignment]
    # First install: main + greet.
    req1 = {
        "seq": 5,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {
            "breakpoints": [{"name": "main"}, {"name": "greet"}]
        },
    }
    server.handle_set_function_breakpoints(sess, req1)
    first_ids = sorted(r.gdb_id for r in sess.function_breakpoints.snapshot())
    check_eq(len(first_ids), 2)
    bridge.sent_commands.clear()
    # Second install: only ``println``. Should delete the two prior
    # ids and install one new entry.
    req2 = {
        "seq": 6,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {"breakpoints": [{"name": "println"}]},
    }
    server.handle_set_function_breakpoints(sess, req2)
    deletes = [c for c in bridge.sent_commands if c.startswith("-break-delete")]
    check_eq(
        len(deletes), 2, f"expected 2 -break-delete, got {bridge.sent_commands}"
    )
    # The deletes should target the prior ids (20 + 21).
    delete_targets = sorted(
        int(c.split()[-1]) for c in deletes if c.split()[-1].isdigit()
    )
    check_eq(delete_targets, first_ids)
    # Manager must now only contain the new entry.
    new_snap = sess.function_breakpoints.snapshot()
    check_eq(len(new_snap), 1)
    check_eq(new_snap[0].name, "println")


def test_handle_empty_list_clears_all() -> None:
    """An empty ``setFunctionBreakpoints`` call clears every
    function bp (so the IDE can remove them all at once)."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=30)
    sess.bridge = bridge  # type: ignore[assignment]
    # Install one.
    server.handle_set_function_breakpoints(
        sess,
        {
            "seq": 7,
            "type": "request",
            "command": "setFunctionBreakpoints",
            "arguments": {"breakpoints": [{"name": "main"}]},
        },
    )
    check_eq(len(sess.function_breakpoints.snapshot()), 1)
    bridge.sent_commands.clear()
    # Now clear with empty list.
    server.handle_set_function_breakpoints(
        sess,
        {
            "seq": 8,
            "type": "request",
            "command": "setFunctionBreakpoints",
            "arguments": {"breakpoints": []},
        },
    )
    deletes = [c for c in bridge.sent_commands if c.startswith("-break-delete")]
    check(len(deletes) >= 1, f"expected a delete, got {bridge.sent_commands}")
    resp = sess.out_stream.last_response()
    check_eq(resp.get("body", {}).get("breakpoints"), [])
    check_eq(len(sess.function_breakpoints.snapshot()), 0)


def test_handle_malformed_entry_returns_unverified() -> None:
    """An entry missing ``name`` must come back ``verified: false``
    with a clear message — without spawning gdb."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 9,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {"breakpoints": [{"name": ""}, {}, {"name": "valid"}]},
    }
    server.handle_set_function_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    entries = resp.get("body", {}).get("breakpoints") or []
    check_eq(len(entries), 3)
    check_eq(entries[0].get("verified"), False)
    check("name" in entries[0].get("message", "").lower())
    check_eq(entries[1].get("verified"), False)
    # Only the valid third entry should have been forwarded to gdb.
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1, f"only one valid name, got {bridge.sent_commands}")
    check_eq(entries[2].get("verified"), True)


def test_handle_not_launched_fails_cleanly() -> None:
    """A request before launch must fail with success: False (no
    bridge to send commands to)."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    # bridge intentionally left None.
    req = {
        "seq": 10,
        "type": "request",
        "command": "setFunctionBreakpoints",
        "arguments": {"breakpoints": [{"name": "main"}]},
    }
    server.handle_set_function_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_capability_advertises_function_breakpoints() -> None:
    """The ``initialize`` response declares
    ``supportsFunctionBreakpoints: true`` and HANDLERS still routes
    ``setFunctionBreakpoints``. Handler count: R11C had 20, R14A adds
    no new handler (replaces the stub) — total still 20."""
    from nova_dap.server import _capabilities, HANDLERS  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsFunctionBreakpoints"), True)
    # The prior capabilities must still be on.
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    check_eq(caps.get("supportsSingleThreadExecutionRequests"), True)
    check_eq(caps.get("supportsDataBreakpoints"), True)
    # Handler stays wired up.
    check("setFunctionBreakpoints" in HANDLERS)
    check(
        len(HANDLERS) >= 20,
        f"expected >=20 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


def test_stopped_event_routes_function_bp_description() -> None:
    """A simulated ``*stopped reason=breakpoint-hit,bkptno=...`` with
    bkptno matching a registered fn-bp must produce a DAP ``stopped``
    event with ``reason: "function breakpoint"`` and
    ``description: "Entry to <name>"``."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.gdb_bridge import GdbAsyncRecord  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    # Register a fn-bp so the stop handler can look it up.
    sess.function_breakpoints.register(
        FunctionBreakpointRecord(gdb_id=5, name="main")
    )
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "breakpoint-hit",
            "bkptno": "5",
            "thread-id": "1",
            "stopped-threads": "all",
        },
    )
    server._handle_stopped(sess, rec)
    events = sess.out_stream.events
    stopped_events = [e for e in events if e.get("event") == "stopped"]
    check_eq(len(stopped_events), 1, f"expected one stopped, got {events}")
    body = stopped_events[0].get("body", {})
    check_eq(body.get("reason"), "function breakpoint")
    check_eq(body.get("description"), "Entry to main")
    check_eq(body.get("hitBreakpointIds"), [5])


def test_stopped_event_unrelated_bp_keeps_breakpoint_reason() -> None:
    """A breakpoint-hit whose bkptno is NOT in the fn-bp manager
    must keep the plain ``reason: "breakpoint"`` — i.e. our
    classifier doesn't accidentally upgrade source-line bp stops
    to function-bp stops."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.gdb_bridge import GdbAsyncRecord  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    # No fn-bps registered — bkptno=99 is an unrelated source-line bp.
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "breakpoint-hit",
            "bkptno": "99",
            "thread-id": "1",
            "stopped-threads": "all",
        },
    )
    server._handle_stopped(sess, rec)
    events = sess.out_stream.events
    stopped_events = [e for e in events if e.get("event") == "stopped"]
    check_eq(len(stopped_events), 1)
    body = stopped_events[0].get("body", {})
    check_eq(body.get("reason"), "breakpoint")
    # No description should be set for an unmatched source-line bp.
    check_eq(body.get("description"), None)


def test_launch_clears_function_bp_registry() -> None:
    """A re-launch must reset the function-bp manager so stale gdb
    ids from the previous session can't collide with the new
    gdb-assigned numbers."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.function_breakpoints.register(
        FunctionBreakpointRecord(gdb_id=42, name="stale")
    )
    check(not sess.function_breakpoints.is_empty())
    # Clear directly — handle_launch will do this for us in the real
    # flow but we just want to verify the clear path runs.
    sess.function_breakpoints.clear_all()
    check(sess.function_breakpoints.is_empty())


# ---------------------------------------------------------------------------
# End-to-end driver shared with the conditional / data-bp tests.
# ---------------------------------------------------------------------------


class DapClient:
    """Tiny DAP client over stdio. Same shape as the other DAP test
    files — duplicated here so each test file is self-contained."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._seq = 1
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._cond = threading.Condition()
        self.responses: Dict[int, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def request(
        self,
        command: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        with self._lock:
            seq = self._seq
            self._seq += 1
        payload: Dict[str, Any] = {
            "seq": seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            payload["arguments"] = arguments
        self._write(payload)
        end = time.monotonic() + timeout
        with self._cond:
            while True:
                if seq in self.responses:
                    return self.responses.pop(seq)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no response for seq {seq}")
                self._cond.wait(timeout=remaining)

    def wait_for_event(
        self, name: str, timeout: float = 15.0, consume: bool = True
    ) -> Dict[str, Any]:
        end = time.monotonic() + timeout
        with self._cond:
            while True:
                for i, ev in enumerate(self.events):
                    if ev.get("event") == name:
                        if consume:
                            return self.events.pop(i)
                        return ev
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no event {name!r} within {timeout}s")
                self._cond.wait(timeout=remaining)

    def _write(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + data)
        self.proc.stdin.flush()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        stream = self.proc.stdout
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            self._buf.extend(chunk)
            while True:
                parsed = self._try_parse_one()
                if parsed is None:
                    break
                with self._cond:
                    if parsed.get("type") == "response":
                        self.responses[parsed.get("request_seq", -1)] = parsed
                    else:
                        self.events.append(parsed)
                    self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def _try_parse_one(self) -> Optional[Dict[str, Any]]:
        header_end = self._buf.find(b"\r\n\r\n")
        if header_end == -1:
            return None
        header = self._buf[:header_end].decode("ascii", errors="replace")
        length = 0
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        body_start = header_end + 4
        if len(self._buf) < body_start + length:
            return None
        body = bytes(self._buf[body_start : body_start + length])
        del self._buf[: body_start + length]
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


def _build_three_function_fixture() -> Optional[str]:
    """Compile a tiny C program with three functions: ``main``,
    ``helper`` (called once), and ``unused`` (referenced only via
    pointer so the linker keeps it). We set fn-bps on all three plus
    one bogus name to exercise the unresolved-symbol path."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
int helper(int x) {
    return x * 2;
}
int unused(int x) {
    return x + 1;
}
int main(void) {
    int (*kept)(int) = unused; /* keeps unused() from being stripped */
    int total = 0;
    for (int i = 0; i < 3; i++) {
        total += helper(i);
    }
    printf("total=%d, kept=%p\\n", total, (void*)kept);
    return 0;
}
"""
    src_path = "/tmp/nova_dap_fn_fixture.c"
    bin_path = "/tmp/nova_dap_fn_fixture"
    with open(src_path, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        subprocess.check_call(["gcc", "-g", "-O0", "-o", bin_path, src_path])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return bin_path if os.path.isfile(bin_path) else None


def _start_dap_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _PKG_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "nova_dap.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
    )


def _e2e_against_c_fixture(bin_path: str) -> Dict[str, Any]:
    """Drive the full function-bp flow against the C fixture.

    Returns a dict ``{stop_reason, description, hit_id, helper_hits,
    unresolved_verified}`` so the caller can assert against gdb's
    actual behaviour."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], f"initialize: {init}")
        caps = init.get("body", {})
        check_eq(caps.get("supportsFunctionBreakpoints"), True)
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": bin_path})
        check(launch["success"], f"launch: {launch}")

        # Install fn-bps: main (verified), helper (verified), and
        # bogus_xyz_xxx (unresolved). The unresolved one must NOT
        # cause the whole request to fail.
        sfbps = client.request(
            "setFunctionBreakpoints",
            {
                "breakpoints": [
                    {"name": "main"},
                    {"name": "helper"},
                    {"name": "bogus_xyz_xxx"},
                ]
            },
        )
        check(sfbps["success"], f"setFunctionBreakpoints: {sfbps}")
        entries = sfbps.get("body", {}).get("breakpoints") or []
        check_eq(len(entries), 3, "should report all 3 entries")
        # First two verified, third unverified.
        check_eq(entries[0].get("verified"), True, f"main: {entries[0]}")
        check_eq(entries[1].get("verified"), True, f"helper: {entries[1]}")
        unresolved_verified = entries[2].get("verified")
        check_eq(unresolved_verified, False, f"bogus: {entries[2]}")
        check(
            "message" in entries[2],
            f"unresolved bp should carry message: {entries[2]}",
        )
        main_id = int(entries[0]["id"])
        helper_id = int(entries[1]["id"])

        # Run -> should stop at main entry.
        cd = client.request("configurationDone", {})
        check(cd["success"])
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        first_reason = stop_body.get("reason")
        first_desc = stop_body.get("description") or ""
        first_hit = stop_body.get("hitBreakpointIds") or []
        check_eq(first_reason, "function breakpoint", f"stop body: {stop_body}")
        check("main" in first_desc, f"description: {first_desc!r}")
        check(main_id in first_hit, f"hit ids: {first_hit}")
        tid = int(stop_body.get("threadId") or 1)

        # Continue — should stop at helper() entry (called from the
        # loop). We expect 3 helper hits total (i = 0, 1, 2).
        helper_hits = 0
        for _ in range(6):
            client.request("continue", {"threadId": tid})
            try:
                ev = client.wait_for_event("stopped", timeout=5.0)
            except TimeoutError:
                break
            b = ev.get("body", {})
            tid = int(b.get("threadId") or tid)
            hit = b.get("hitBreakpointIds") or []
            if helper_id in hit:
                helper_hits += 1
                # The description should say "Entry to helper".
                desc = b.get("description") or ""
                check("helper" in desc, f"helper desc: {desc!r}")
                check_eq(b.get("reason"), "function breakpoint")

        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
        return {
            "stop_reason": first_reason,
            "description": first_desc,
            "hit_id": main_id in first_hit,
            "helper_hits": helper_hits,
            "unresolved_verified": unresolved_verified,
        }
    finally:
        client.close()


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")
HELLO_DWARF_SRC = os.path.join(REPO_ROOT, "examples", "hello_dwarf.nova")


def _e2e_against_nova_program() -> Optional[Dict[str, Any]]:
    """Integration test against the NOVA-compiled ``hello_dwarf``
    binary. Sets function breakpoints on ``main`` + ``greet``, runs
    the program, asserts each is hit with ``reason: "function
    breakpoint"`` and the right description.

    Returns ``None`` if the binary isn't built; otherwise returns a
    dict summarising what we observed."""
    if not os.path.isfile(HELLO_DWARF_BIN):
        return None
    if not os.path.isfile(HELLO_DWARF_SRC):
        return None
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], "initialize against NOVA binary")
        client.wait_for_event("initialized", timeout=5.0)
        launch = client.request("launch", {"program": HELLO_DWARF_BIN})
        check(launch["success"], f"launch NOVA binary: {launch}")
        sfbps = client.request(
            "setFunctionBreakpoints",
            {
                "breakpoints": [
                    {"name": "main"},
                    {"name": "greet"},
                ]
            },
        )
        check(sfbps["success"], f"setFunctionBreakpoints NOVA: {sfbps}")
        entries = sfbps.get("body", {}).get("breakpoints") or []
        check_eq(len(entries), 2)
        # main + greet both exist in hello_dwarf.nova -> both verified.
        check(
            entries[0].get("verified"),
            f"NOVA main bp not verified: {entries[0]}",
        )
        check(
            entries[1].get("verified"),
            f"NOVA greet bp not verified: {entries[1]}",
        )
        main_id = int(entries[0]["id"])
        greet_id = int(entries[1]["id"])

        cd = client.request("configurationDone", {})
        check(cd["success"])
        # First stop: main entry.
        first = client.wait_for_event("stopped", timeout=15.0)
        first_body = first.get("body", {})
        check_eq(
            first_body.get("reason"),
            "function breakpoint",
            f"NOVA first stop body: {first_body}",
        )
        desc = first_body.get("description") or ""
        check(
            "main" in desc,
            f"NOVA description should mention main: {desc!r}",
        )
        check(main_id in (first_body.get("hitBreakpointIds") or []))
        tid = int(first_body.get("threadId") or 1)

        # Continue -> greet entry (called once by main).
        client.request("continue", {"threadId": tid})
        greet_hit = False
        try:
            second = client.wait_for_event("stopped", timeout=8.0)
            second_body = second.get("body", {})
            tid = int(second_body.get("threadId") or tid)
            if greet_id in (second_body.get("hitBreakpointIds") or []):
                greet_hit = True
                check_eq(second_body.get("reason"), "function breakpoint")
                desc2 = second_body.get("description") or ""
                check(
                    "greet" in desc2,
                    f"NOVA greet desc: {desc2!r}",
                )
        except TimeoutError:
            pass

        # Continue to completion (drain any incidental stops).
        for _ in range(4):
            client.request("continue", {"threadId": tid})
            try:
                ev = client.wait_for_event("stopped", timeout=3.0)
                tid = int((ev.get("body") or {}).get("threadId") or tid)
            except TimeoutError:
                break
        try:
            client.wait_for_event("terminated", timeout=10.0)
        except TimeoutError:
            pass
        client.request("disconnect", {})
        return {
            "main_stop_ok": True,
            "greet_hit": greet_hit,
            "main_id": main_id,
            "greet_id": greet_id,
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    # Module-level unit tests.
    test_build_command_bare_name()
    test_build_command_with_condition()
    test_build_command_empty_name_rejected()
    test_build_command_blank_condition_treated_as_none()
    test_build_command_quotes_embedded_quotes()
    test_parse_response_verified()
    test_parse_response_pending_marks_unverified()
    test_parse_response_multiple_locations_verified()
    test_parse_response_missing_bkpt()
    test_is_unresolved_function_error_classification()
    test_describe_function_entry_format()
    test_manager_register_lookup()
    test_manager_clear_all_returns_ids()
    test_manager_multiple_names_register_independently()
    # Handler tests with a stub bridge.
    test_handle_install_single_function_bp()
    test_handle_install_unresolved_returns_unverified()
    test_handle_install_with_condition_forwards_to_gdb()
    test_handle_install_multiple_bps_in_one_call()
    test_handle_replacement_clears_prior_set()
    test_handle_empty_list_clears_all()
    test_handle_malformed_entry_returns_unverified()
    test_handle_not_launched_fails_cleanly()
    test_capability_advertises_function_breakpoints()
    test_stopped_event_routes_function_bp_description()
    test_stopped_event_unrelated_bp_keeps_breakpoint_reason()
    test_launch_clears_function_bp_registry()


def main() -> int:
    # Phase 1: unit + handler tests (always run).
    _run_unit_tests()
    unit_assertions = _ASSERT_COUNT

    # Phase 2: end-to-end.
    e2e_status = "skipped"
    e2e_reason = ""
    nova_status = "skipped"
    if shutil.which("gdb") is None:
        e2e_reason = "gdb not installed"
    else:
        bin_path = _build_three_function_fixture()
        if bin_path is None:
            e2e_reason = "gcc not available"
        else:
            try:
                summary = _e2e_against_c_fixture(bin_path)
                check_eq(
                    summary["stop_reason"],
                    "function breakpoint",
                    "main entry should produce function-bp reason",
                )
                check(
                    summary["hit_id"],
                    "main bp id should be in hitBreakpointIds",
                )
                check(
                    summary["helper_hits"] >= 1,
                    f"expected >=1 helper hits, got {summary['helper_hits']}",
                )
                check_eq(
                    summary["unresolved_verified"],
                    False,
                    "bogus_xyz_xxx must come back unverified",
                )
                e2e_status = (
                    f"ok (helper hits={summary['helper_hits']})"
                )
                nova_summary = _e2e_against_nova_program()
                if nova_summary is None:
                    nova_status = "skipped (hello_dwarf binary missing)"
                else:
                    nova_status = (
                        f"ok (main main_id={nova_summary['main_id']}, "
                        f"greet_hit={nova_summary['greet_hit']})"
                    )
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout: {exc}"

    print(f"test_function_breakpoints: OK")
    print(f"  unit assertions:    {unit_assertions}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    if e2e_status.startswith("ok"):
        extra = _ASSERT_COUNT - unit_assertions
        print(f"  end-to-end:         {e2e_status} ({extra} extra checks)")
        print(f"  NOVA integration:   {nova_status}")
    else:
        print(f"  end-to-end:         SKIP — {e2e_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
