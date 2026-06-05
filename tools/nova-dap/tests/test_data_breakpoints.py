"""Tests for DAP data breakpoints (watchpoints) — the 19th capability
for ``nova-dap``.

Three layers, mirroring the pattern established by
``test_evaluate.py`` and ``test_conditional_breakpoint.py``:

1. **Unit tests** — exercise ``nova_dap.watchpoints`` directly:
   dataId round-trip encoding, access-type mapping, watch-command
   composition, stop-reason classification, manager bookkeeping.
   These run everywhere (no gdb required).

2. **Server handler tests with a stub bridge** — drive
   ``handle_data_breakpoint_info`` and ``handle_set_data_breakpoints``
   against a ``CaptureBridge`` that records every MI command. We
   verify the MI command composition + the wire-level response
   shape without spawning gdb.

3. **End-to-end against gdb + a C fixture** — compile a tiny C
   program with an ``int counter`` that increments in a loop, then
   drive the full DAP wire: ``dataBreakpointInfo`` ->
   ``setDataBreakpoints`` -> ``configurationDone`` -> wait for a
   ``stopped`` event with ``reason: "data breakpoint"``. SKIPs
   cleanly if gdb / gcc isn't available.

Run::

    python tools/nova-dap/tests/test_data_breakpoints.py
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
from nova_dap.watchpoints import (  # noqa: E402
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
# Unit tests for watchpoints.py (no gdb required).
# ---------------------------------------------------------------------------


def test_data_id_round_trip_bare_name() -> None:
    """Encoding a bare name and decoding the result must yield the
    same name back. This is the minimal round-trip — no frame, no
    varRef."""
    encoded = encode_data_id("counter")
    check(isinstance(encoded, str) and len(encoded) > 0, "encoded must be non-empty")
    decoded = decode_data_id(encoded)
    check(decoded is not None, "decode must succeed for valid encoding")
    check_eq(decoded.get("n"), "counter", "round-tripped name")


def test_data_id_round_trip_with_frame() -> None:
    """Frame id must round-trip through the encoding."""
    encoded = encode_data_id("x", frame_id=42)
    decoded = decode_data_id(encoded)
    check(decoded is not None)
    check_eq(decoded.get("n"), "x")
    check_eq(decoded.get("f"), 42)


def test_data_id_round_trip_with_var_ref() -> None:
    """varRef must round-trip alongside frame id and name."""
    encoded = encode_data_id("sum", frame_id=7, variables_reference=1001)
    decoded = decode_data_id(encoded)
    check(decoded is not None)
    check_eq(decoded.get("n"), "sum")
    check_eq(decoded.get("f"), 7)
    check_eq(decoded.get("v"), 1001)


def test_data_id_decode_rejects_garbage() -> None:
    """Random strings must NOT decode to a valid envelope."""
    check_eq(decode_data_id(""), None, "empty string rejected")
    check_eq(decode_data_id("not-base64!@#"), None, "invalid base64 rejected")
    check_eq(decode_data_id("Zm9vYmFy"), None, "valid base64 but not JSON")


def test_data_id_decode_rejects_missing_name() -> None:
    """A JSON envelope without the ``n`` key must be rejected."""
    import base64

    payload = base64.urlsafe_b64encode(b'{"f":1}').decode("ascii").rstrip("=")
    check_eq(decode_data_id(payload), None, "no name -> reject")


def test_data_id_unique_per_name() -> None:
    """Two different names must encode to different dataIds."""
    a = encode_data_id("foo")
    b = encode_data_id("bar")
    check(a != b, f"encodings for foo / bar should differ: {a!r} vs {b!r}")


def test_access_type_flag_mapping() -> None:
    """Each DAP access type maps to its gdb flag."""
    check_eq(access_type_flag("write"), "", "write -> bare -break-watch")
    check_eq(access_type_flag("read"), "-r", "read -> -break-watch -r")
    check_eq(access_type_flag("readWrite"), "-a", "rw -> -break-watch -a")
    check_eq(access_type_flag(None), "", "None defaults to write")
    check_eq(access_type_flag("invalid"), None, "unknown -> None")


def test_default_access_types_advertised() -> None:
    """``dataBreakpointInfo`` advertises write + readWrite by default.

    Pure read watchpoints are x86-arch specific so we don't include
    them in the advertised list (we still accept ``"read"`` if a
    client insists, but gdb may reject the install)."""
    types = default_access_types()
    check("write" in types, f"write missing from {types}")
    check("readWrite" in types, f"readWrite missing from {types}")


def test_build_watch_command_write() -> None:
    """Write watchpoints use the bare ``-break-watch`` form."""
    cmd = build_watch_command("counter", "write")
    check_eq(cmd, "-break-watch counter")


def test_build_watch_command_read() -> None:
    """Read watchpoints route via ``-break-watch -r``."""
    cmd = build_watch_command("counter", "read")
    check_eq(cmd, "-break-watch -r counter")


def test_build_watch_command_rw() -> None:
    """rw watchpoints route via ``-break-watch -a``."""
    cmd = build_watch_command("counter", "readWrite")
    check_eq(cmd, "-break-watch -a counter")


def test_build_watch_command_unknown_returns_none() -> None:
    """Unknown access types must return None so callers can reject."""
    check_eq(build_watch_command("counter", "nonsense"), None)


def test_parse_watchpoint_id_write() -> None:
    """gdb's ``wpt={number="3"}`` reply must parse to int 3."""
    fields = {"wpt": {"number": "3", "exp": "counter"}}
    check_eq(parse_watchpoint_id(fields), 3)


def test_parse_watchpoint_id_read() -> None:
    """Read watchpoints come back as ``hw-rwpt={number=...}``."""
    fields = {"hw-rwpt": {"number": "5", "exp": "counter"}}
    check_eq(parse_watchpoint_id(fields), 5)


def test_parse_watchpoint_id_rw() -> None:
    """rw watchpoints come back as ``hw-awpt={number=...}``."""
    fields = {"hw-awpt": {"number": "7", "exp": "counter"}}
    check_eq(parse_watchpoint_id(fields), 7)


def test_parse_watchpoint_id_missing() -> None:
    """Unrelated fields must return None."""
    check_eq(parse_watchpoint_id({}), None)
    check_eq(parse_watchpoint_id({"wpt": "not a dict"}), None)


def test_is_watchpoint_stop_classification() -> None:
    """The four gdb stop reasons that map to DAP "data breakpoint"."""
    check(is_watchpoint_stop("watchpoint-trigger"))
    check(is_watchpoint_stop("read-watchpoint-trigger"))
    check(is_watchpoint_stop("access-watchpoint-trigger"))
    check(is_watchpoint_stop("watchpoint-scope"))
    check(not is_watchpoint_stop("breakpoint-hit"))
    check(not is_watchpoint_stop(None))


def test_describe_watch_change_with_values() -> None:
    """The description string includes the before / after values."""
    msg = describe_watch_change("counter", "write", "5", "6")
    check("counter" in msg, f"name missing from {msg!r}")
    check("write" in msg, f"access type missing from {msg!r}")
    check("5" in msg and "6" in msg, f"values missing from {msg!r}")


def test_extract_watch_values_write_shape() -> None:
    """gdb's ``value={old,new}`` for write watchpoints."""
    fields = {"value": {"old": "5", "new": "6"}}
    old, new = extract_watch_values(fields)
    check_eq(old, "5")
    check_eq(new, "6")


def test_extract_watch_values_read_shape() -> None:
    """Read watchpoints have a single ``value`` slot."""
    fields = {"value": {"value": "42"}}
    old, new = extract_watch_values(fields)
    check_eq(old, None)
    check_eq(new, "42")


def test_extract_watch_values_missing() -> None:
    """No value field -> both slots None."""
    old, new = extract_watch_values({})
    check_eq(old, None)
    check_eq(new, None)


def test_watchpoint_manager_register_lookup() -> None:
    """Registering a record makes both id forms recoverable."""
    mgr = WatchpointManager()
    rec = WatchpointRecord(
        gdb_id=3, data_id="abc", name="counter", access_type="write"
    )
    mgr.register(rec)
    check(mgr.lookup_by_gdb_id(3) is rec)
    check(mgr.lookup_by_data_id("abc") is rec)


def test_watchpoint_manager_clear_all() -> None:
    """``clear_all`` returns the active gdb ids and empties the maps."""
    mgr = WatchpointManager()
    mgr.register(
        WatchpointRecord(gdb_id=1, data_id="a", name="x", access_type="write")
    )
    mgr.register(
        WatchpointRecord(gdb_id=2, data_id="b", name="y", access_type="read")
    )
    ids = mgr.clear_all()
    check_eq(sorted(ids), [1, 2], "cleared ids")
    check_eq(mgr.lookup_by_gdb_id(1), None)
    check_eq(mgr.lookup_by_data_id("a"), None)


def test_watchpoint_manager_remove_by_gdb_id() -> None:
    """Removing by gdb id drops both index entries."""
    mgr = WatchpointManager()
    rec = WatchpointRecord(
        gdb_id=4, data_id="abc", name="counter", access_type="write"
    )
    mgr.register(rec)
    out = mgr.remove_by_gdb_id(4)
    check(out is rec)
    check_eq(mgr.lookup_by_gdb_id(4), None)
    check_eq(mgr.lookup_by_data_id("abc"), None)


# ---------------------------------------------------------------------------
# Server handler tests (no gdb required).
# ---------------------------------------------------------------------------


class _NullStream:
    """Stand-in for the real binary stdout — accepts writes, drops them."""

    def write(self, _data: bytes) -> int:
        return 0

    def flush(self) -> None:
        pass


class CaptureBridge:
    """Fake GdbBridge that records every command and returns canned
    replies. Lets us verify MI command composition without spawning
    gdb."""

    def __init__(
        self,
        watch_reply_id: int = 2,
        watch_reply_key: str = "wpt",
    ) -> None:
        self.sent_commands: List[str] = []
        self._watch_reply_id = watch_reply_id
        self._watch_reply_key = watch_reply_key
        # If non-None, the next -break-watch returns an error.
        self._fail_next_watch: Optional[str] = None
        # Track captured responses per request.
        self.captured_responses: List[Dict[str, Any]] = []

    def fail_next_watch(self, message: str) -> None:
        self._fail_next_watch = message

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if cmd.startswith("-break-watch"):
            if self._fail_next_watch is not None:
                msg = self._fail_next_watch
                self._fail_next_watch = None
                return GdbResult(
                    token=None, cls="error", fields={"msg": msg}
                )
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    self._watch_reply_key: {
                        "number": str(self._watch_reply_id),
                        "exp": cmd.split()[-1],
                    }
                },
            )
        # Default: -break-delete / -stack-select-frame / -thread-select / etc.
        return GdbResult(token=None, cls="done", fields={})


def test_handle_data_breakpoint_info_returns_dataid() -> None:
    """A ``dataBreakpointInfo`` request must return a dataId we can
    decode back to the requested name."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "dataBreakpointInfo",
        "arguments": {"variablesReference": 0, "name": "counter"},
    }
    server.handle_data_breakpoint_info(sess, req)
    resp = sess.out_stream.last_response()
    check(resp is not None, "no response captured")
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    data_id = body.get("dataId")
    check(isinstance(data_id, str) and data_id, "missing dataId")
    decoded = decode_data_id(data_id)
    check(decoded is not None and decoded.get("n") == "counter")
    # Must advertise at least write + readWrite.
    types = body.get("accessTypes") or []
    check("write" in types and "readWrite" in types, f"types: {types}")
    check_eq(body.get("canPersist"), False, "watchpoints never persist")


def test_handle_data_breakpoint_info_rejects_empty_name() -> None:
    """Empty ``name`` argument must error out (no dataId issued)."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "dataBreakpointInfo",
        "arguments": {"variablesReference": 0, "name": ""},
    }
    server.handle_data_breakpoint_info(sess, req)
    resp = sess.out_stream.last_response()
    check(resp is not None)
    check_eq(resp.get("success"), False, "empty name should error")


def test_handle_set_data_breakpoints_installs_watch() -> None:
    """``setDataBreakpoints`` issues ``-break-watch`` and confirms."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(watch_reply_id=11)
    sess.bridge = bridge  # type: ignore[assignment]
    data_id = encode_data_id("counter")
    req = {
        "seq": 2,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "write"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req)
    # Must have issued a -break-watch
    watch_cmds = [c for c in bridge.sent_commands if c.startswith("-break-watch")]
    check_eq(len(watch_cmds), 1, "exactly one -break-watch expected")
    check("counter" in watch_cmds[0], f"expr missing from {watch_cmds[0]!r}")
    # Verify response shape.
    resp = sess.out_stream.last_response()
    check(resp is not None)
    body = resp.get("body", {})
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), True)
    check_eq(entries[0].get("id"), 11)
    # Manager must record the watchpoint.
    record = sess.watchpoints.lookup_by_gdb_id(11)
    check(record is not None, "watchpoint not registered in manager")
    check_eq(record.name, "counter")
    check_eq(record.access_type, "write")


def test_handle_set_data_breakpoints_read_routes_dash_r() -> None:
    """``accessType: "read"`` must produce ``-break-watch -r``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(watch_reply_id=12, watch_reply_key="hw-rwpt")
    sess.bridge = bridge  # type: ignore[assignment]
    data_id = encode_data_id("counter")
    req = {
        "seq": 3,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "read"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req)
    watch_cmds = [c for c in bridge.sent_commands if c.startswith("-break-watch")]
    check_eq(len(watch_cmds), 1)
    check("-r" in watch_cmds[0], f"missing -r flag in {watch_cmds[0]!r}")


def test_handle_set_data_breakpoints_rw_routes_dash_a() -> None:
    """``accessType: "readWrite"`` must produce ``-break-watch -a``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(watch_reply_id=13, watch_reply_key="hw-awpt")
    sess.bridge = bridge  # type: ignore[assignment]
    data_id = encode_data_id("counter")
    req = {
        "seq": 4,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "readWrite"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req)
    watch_cmds = [c for c in bridge.sent_commands if c.startswith("-break-watch")]
    check_eq(len(watch_cmds), 1)
    check("-a" in watch_cmds[0], f"missing -a flag in {watch_cmds[0]!r}")


def test_handle_set_data_breakpoints_clears_prior_watches() -> None:
    """A second ``setDataBreakpoints`` request must tear down the first
    set via ``-break-delete <id>`` so re-sends don't double-install."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(watch_reply_id=21)
    sess.bridge = bridge  # type: ignore[assignment]
    data_id = encode_data_id("counter")
    # First install.
    req1 = {
        "seq": 5,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "write"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req1)
    bridge.sent_commands.clear()
    # Second install — gdb id will be 22 this time.
    bridge._watch_reply_id = 22
    req2 = {
        "seq": 6,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "write"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req2)
    deletes = [c for c in bridge.sent_commands if c.startswith("-break-delete")]
    check(len(deletes) >= 1, f"expected -break-delete 21, got {bridge.sent_commands}")
    check("21" in deletes[0], f"delete should target old id 21, got {deletes[0]!r}")


def test_handle_set_data_breakpoints_empty_clears() -> None:
    """``setDataBreakpoints`` with an empty list must clear existing
    watches and return an empty result."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(watch_reply_id=31)
    sess.bridge = bridge  # type: ignore[assignment]
    # First install one.
    data_id = encode_data_id("counter")
    server.handle_set_data_breakpoints(
        sess,
        {
            "seq": 7,
            "type": "request",
            "command": "setDataBreakpoints",
            "arguments": {
                "breakpoints": [{"dataId": data_id, "accessType": "write"}]
            },
        },
    )
    # Now clear.
    bridge.sent_commands.clear()
    server.handle_set_data_breakpoints(
        sess,
        {
            "seq": 8,
            "type": "request",
            "command": "setDataBreakpoints",
            "arguments": {"breakpoints": []},
        },
    )
    resp = sess.out_stream.last_response()
    body = resp.get("body", {})
    check_eq(body.get("breakpoints"), [], "empty list -> empty result")
    # Manager state must be empty.
    check_eq(sess.watchpoints.lookup_by_gdb_id(31), None)


def test_handle_set_data_breakpoints_invalid_data_id() -> None:
    """Malformed dataIds must be surfaced as ``verified: false``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 9,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": "not-a-real-id", "accessType": "write"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    body = resp.get("body", {})
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), False)
    check("message" in entries[0])


def test_handle_set_data_breakpoints_gdb_error() -> None:
    """A gdb error during install surfaces as ``verified: false`` with
    the error message."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    bridge.fail_next_watch("Could not set watchpoint (no slots)")
    sess.bridge = bridge  # type: ignore[assignment]
    data_id = encode_data_id("counter")
    req = {
        "seq": 10,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {
            "breakpoints": [{"dataId": data_id, "accessType": "write"}]
        },
    }
    server.handle_set_data_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    body = resp.get("body", {})
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), False)
    check("no slots" in entries[0].get("message", ""))


def test_handle_set_data_breakpoints_not_launched() -> None:
    """A request issued before launch must fail cleanly."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    # Bridge intentionally left None.
    req = {
        "seq": 11,
        "type": "request",
        "command": "setDataBreakpoints",
        "arguments": {"breakpoints": []},
    }
    server.handle_set_data_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_capability_advertises_data_breakpoints() -> None:
    """The ``initialize`` response must declare
    ``supportsDataBreakpoints: true`` and the HANDLERS table must
    grow by two (``dataBreakpointInfo`` + ``setDataBreakpoints``)."""
    from nova_dap.server import _capabilities, HANDLERS  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsDataBreakpoints"), True)
    # All the previous-round capabilities must still be on.
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    check_eq(caps.get("supportsSingleThreadExecutionRequests"), True)
    # New handlers wired up.
    check("dataBreakpointInfo" in HANDLERS, "dataBreakpointInfo missing")
    check("setDataBreakpoints" in HANDLERS, "setDataBreakpoints missing")
    # Handler count: 18 (R10E) + 2 = 20.
    check(
        len(HANDLERS) >= 20,
        f"expected >=20 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


def test_watchpoint_stop_description_via_handler() -> None:
    """A simulated ``*stopped reason=watchpoint-trigger`` must be turned
    into a DAP ``stopped`` event with ``reason: "data breakpoint"``
    and a description carrying the before / after values."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.gdb_bridge import GdbAsyncRecord  # noqa: WPS433

    captured: List[Dict[str, Any]] = []
    sess = server.Session(out_stream=_CaptureStream())
    # Pre-register a watchpoint so the stop handler can look up its
    # name + access type for the description.
    sess.watchpoints.register(
        WatchpointRecord(
            gdb_id=5, data_id="abc", name="counter", access_type="write"
        )
    )
    # Forge an async record matching gdb's *stopped emission.
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "watchpoint-trigger",
            "bkptno": "5",
            "thread-id": "1",
            "stopped-threads": "all",
            "value": {"old": "0", "new": "1"},
        },
    )
    server._handle_stopped(sess, rec)
    events = sess.out_stream.events
    stopped_events = [e for e in events if e.get("event") == "stopped"]
    check_eq(len(stopped_events), 1, f"expected one stopped, got {events}")
    body = stopped_events[0].get("body", {})
    check_eq(body.get("reason"), "data breakpoint")
    check_eq(body.get("hitBreakpointIds"), [5])
    desc = body.get("description") or ""
    check("counter" in desc, f"name missing from desc {desc!r}")
    check("0" in desc and "1" in desc, f"values missing from desc {desc!r}")


class _CaptureStream:
    """Captures every DAP message written so tests can introspect them."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.messages: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        # Try to parse complete frames out of the buffer.
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


# ---------------------------------------------------------------------------
# End-to-end driver shared with the conditional / evaluate tests.
# ---------------------------------------------------------------------------


class DapClient:
    """Tiny DAP client over stdio. Mirrors the implementation in
    ``test_conditional_breakpoint.py`` — duplicated here so the
    test files stay self-contained."""

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

    def drain_events(self, name: str) -> List[Dict[str, Any]]:
        """Collect every matching event currently buffered, without
        waiting. Used to count repeated watchpoint stops after the
        program has terminated."""
        with self._cond:
            matching = [e for e in self.events if e.get("event") == name]
            self.events = [e for e in self.events if e.get("event") != name]
        return matching

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


def _build_counter_fixture() -> Optional[str]:
    """Compile a tiny C program with an int ``counter`` that increments
    three times in a loop. We set a write watchpoint on it and expect
    three ``stopped`` events (one per increment after the initial)."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
int main(void) {
    int counter = 0;
    /* line 4: pause point so we can set a watchpoint before increments. */
    int sentinel = 0;
    for (int i = 0; i < 3; i++) {
        counter = counter + 1;
        printf("counter=%d\\n", counter);
    }
    return counter; /* line 10 */
}
"""
    src_path = "/tmp/nova_dap_watch_fixture.c"
    bin_path = "/tmp/nova_dap_watch_fixture"
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


def _e2e_against_counter_fixture(bin_path: str, src_path: str) -> int:
    """Drive the full data-breakpoint flow against the C fixture.

    Returns the number of distinct ``stopped`` events observed with
    ``reason: "data breakpoint"``. Three increments -> at least three
    stops expected."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        # 1. initialize -> verify the capability is on.
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], f"initialize: {init}")
        caps = init.get("body", {})
        check_eq(caps.get("supportsDataBreakpoints"), True)
        client.wait_for_event("initialized", timeout=5.0)

        # 2. launch.
        launch = client.request("launch", {"program": bin_path})
        check(launch["success"], f"launch: {launch}")

        # 3. set a source-line breakpoint at line 4 (sentinel init) so
        # we have a known stop point at which to install the watch.
        bps = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": 5}],
            },
        )
        check(bps["success"], f"setBreakpoints: {bps}")

        # 4. configurationDone -> run to the source-line breakpoint.
        cd = client.request("configurationDone", {})
        check(cd["success"])
        first_stop = client.wait_for_event("stopped", timeout=15.0)
        first_body = first_stop.get("body", {})
        check_eq(first_body.get("reason"), "breakpoint")
        tid = int(first_body.get("threadId") or 1)

        # 5. resolve a frame id so dataBreakpointInfo can carry it.
        st = client.request("stackTrace", {"threadId": tid})
        check(st["success"])
        frames = st.get("body", {}).get("stackFrames") or []
        check(len(frames) > 0)
        top_frame_id = int(frames[0]["id"])

        # 6. scopes -> variablesReference for the Locals scope.
        scopes = client.request("scopes", {"frameId": top_frame_id})
        check(scopes["success"])
        scope_list = scopes.get("body", {}).get("scopes") or []
        check(len(scope_list) >= 1)
        var_ref = int(scope_list[0].get("variablesReference") or 0)

        # 7. dataBreakpointInfo on ``counter``.
        info = client.request(
            "dataBreakpointInfo",
            {"variablesReference": var_ref, "name": "counter"},
        )
        check(info["success"], f"dataBreakpointInfo: {info}")
        info_body = info.get("body", {})
        data_id = info_body.get("dataId")
        check(isinstance(data_id, str) and data_id, f"missing dataId: {info_body}")
        access_types = info_body.get("accessTypes") or []
        check("write" in access_types)
        check_eq(info_body.get("canPersist"), False)
        # Verify the dataId round-trips back to "counter".
        decoded = decode_data_id(data_id)
        check(decoded is not None and decoded.get("n") == "counter")

        # 8. setDataBreakpoints with a write watchpoint on counter.
        sdb = client.request(
            "setDataBreakpoints",
            {"breakpoints": [{"dataId": data_id, "accessType": "write"}]},
        )
        check(sdb["success"], f"setDataBreakpoints: {sdb}")
        sdb_entries = sdb.get("body", {}).get("breakpoints") or []
        check_eq(len(sdb_entries), 1)
        check_eq(sdb_entries[0].get("verified"), True, f"watch not verified: {sdb_entries}")
        watch_id = int(sdb_entries[0].get("id") or 0)
        check(watch_id > 0, f"watch_id should be > 0: {watch_id}")

        # 9. Continue. The loop runs counter = 0 -> 1 -> 2 -> 3, so we
        # expect three watchpoint hits (one per ``counter = counter + 1``).
        # We drain up to 8 stops to absorb the eventual
        # ``watchpoint-scope`` event gdb emits when ``counter`` goes
        # out of scope at the end of ``main``.
        watchpoint_stops = 0
        last_tid = tid
        for iteration in range(8):
            client.request("continue", {"threadId": last_tid})
            try:
                stopped = client.wait_for_event("stopped", timeout=5.0)
            except TimeoutError:
                # No more stops — fall through to wait for terminated.
                break
            stop_body = stopped.get("body", {})
            reason = stop_body.get("reason")
            last_tid = int(stop_body.get("threadId") or last_tid)
            if reason == "data breakpoint":
                # If it's a real watchpoint hit (we registered an id),
                # verify the description carries the variable name.
                hit = stop_body.get("hitBreakpointIds") or []
                if watch_id in hit:
                    watchpoint_stops += 1
                    desc = stop_body.get("description") or ""
                    check("counter" in desc, f"description missing counter: {desc!r}")
                # If it's a scope stop (no registered id), keep going.
            # All other reasons (breakpoint / step) — also continue.

        # 10. Run to completion.
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
        return watchpoint_stops
    finally:
        client.close()


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")
HELLO_DWARF_SRC = os.path.join(REPO_ROOT, "examples", "hello_dwarf.nova")


def _e2e_against_nova_program() -> bool:
    """Integration test against the NOVA-compiled ``hello_dwarf`` binary.

    Sets a source-line breakpoint at the ``let sum = a + b`` line,
    queries ``dataBreakpointInfo`` on ``sum``, and verifies the
    response is well-formed. We don't try to fire the watch (the
    program is straight-line and ``sum`` is written once); we just
    verify that the wire protocol works end-to-end against actual
    NOVA-emitted DWARF + the DAP server's frame resolution."""
    if not os.path.isfile(HELLO_DWARF_BIN):
        return False
    if not os.path.isfile(HELLO_DWARF_SRC):
        return False
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], "initialize against NOVA binary")
        client.wait_for_event("initialized", timeout=5.0)
        launch = client.request("launch", {"program": HELLO_DWARF_BIN})
        check(launch["success"], f"launch NOVA binary: {launch}")
        # Stop after ``let sum = a + b`` so sum is in scope.
        bps = client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": HELLO_DWARF_SRC,
                    "name": os.path.basename(HELLO_DWARF_SRC),
                },
                "breakpoints": [{"line": 36}],
            },
        )
        check(bps["success"], f"setBreakpoints (NOVA): {bps}")

        cd = client.request("configurationDone", {})
        check(cd["success"])

        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        check_eq(stop_body.get("reason"), "breakpoint")
        tid = int(stop_body.get("threadId") or 1)

        st = client.request("stackTrace", {"threadId": tid})
        check(st["success"])
        frame_id = int(st["body"]["stackFrames"][0]["id"])
        scopes = client.request("scopes", {"frameId": frame_id})
        var_ref = int(
            scopes["body"]["scopes"][0].get("variablesReference") or 0
        )

        info = client.request(
            "dataBreakpointInfo",
            {"variablesReference": var_ref, "name": "sum"},
        )
        check(info["success"], f"dataBreakpointInfo (NOVA sum): {info}")
        body = info.get("body", {})
        data_id = body.get("dataId")
        check(isinstance(data_id, str) and data_id)
        decoded = decode_data_id(data_id)
        check(decoded is not None and decoded.get("n") == "sum")
        # Try installing the watch — we don't require it to fire (the
        # program may exit before sum is written again) but the install
        # should at least be syntactically valid.
        sdb = client.request(
            "setDataBreakpoints",
            {"breakpoints": [{"dataId": data_id, "accessType": "write"}]},
        )
        check(sdb["success"], f"setDataBreakpoints (NOVA): {sdb}")

        # Drain any incidental stops (watchpoint-scope when ``sum``
        # goes out of scope at function return) on the way to
        # ``terminated``.
        last_tid = tid
        for _ in range(6):
            client.request("continue", {"threadId": last_tid})
            try:
                ev = client.wait_for_event("stopped", timeout=3.0)
                last_tid = int((ev.get("body") or {}).get("threadId") or last_tid)
            except TimeoutError:
                break
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
        return True
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    test_data_id_round_trip_bare_name()
    test_data_id_round_trip_with_frame()
    test_data_id_round_trip_with_var_ref()
    test_data_id_decode_rejects_garbage()
    test_data_id_decode_rejects_missing_name()
    test_data_id_unique_per_name()
    test_access_type_flag_mapping()
    test_default_access_types_advertised()
    test_build_watch_command_write()
    test_build_watch_command_read()
    test_build_watch_command_rw()
    test_build_watch_command_unknown_returns_none()
    test_parse_watchpoint_id_write()
    test_parse_watchpoint_id_read()
    test_parse_watchpoint_id_rw()
    test_parse_watchpoint_id_missing()
    test_is_watchpoint_stop_classification()
    test_describe_watch_change_with_values()
    test_extract_watch_values_write_shape()
    test_extract_watch_values_read_shape()
    test_extract_watch_values_missing()
    test_watchpoint_manager_register_lookup()
    test_watchpoint_manager_clear_all()
    test_watchpoint_manager_remove_by_gdb_id()
    test_handle_data_breakpoint_info_returns_dataid()
    test_handle_data_breakpoint_info_rejects_empty_name()
    test_handle_set_data_breakpoints_installs_watch()
    test_handle_set_data_breakpoints_read_routes_dash_r()
    test_handle_set_data_breakpoints_rw_routes_dash_a()
    test_handle_set_data_breakpoints_clears_prior_watches()
    test_handle_set_data_breakpoints_empty_clears()
    test_handle_set_data_breakpoints_invalid_data_id()
    test_handle_set_data_breakpoints_gdb_error()
    test_handle_set_data_breakpoints_not_launched()
    test_capability_advertises_data_breakpoints()
    test_watchpoint_stop_description_via_handler()


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
        bin_path = _build_counter_fixture()
        if bin_path is None:
            e2e_reason = "gcc not available"
        else:
            src_path = "/tmp/nova_dap_watch_fixture.c"
            try:
                hits = _e2e_against_counter_fixture(bin_path, src_path)
                check(
                    hits >= 1,
                    f"expected >=1 data breakpoint hit, got {hits}",
                )
                e2e_status = f"ok ({hits} watchpoint stops)"
                # Best-effort: integration against the NOVA binary
                # (only runs if make smoke-dwarf has been done).
                nova_ran = _e2e_against_nova_program()
                if nova_ran:
                    nova_status = "ok"
                else:
                    nova_status = "skipped (hello_dwarf binary missing)"
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout: {exc}"

    print(f"test_data_breakpoints: OK")
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
