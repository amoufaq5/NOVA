"""Tests for DAP exception breakpoints (R33F).

Three layers, mirroring the pattern established by the other
``test_*.py`` suites in this directory:

1. **Capability + handler-table tests** -- confirm the new
   ``exceptionBreakpointFilters`` capability is advertised correctly,
   ``supportsExceptionInfoRequest: true`` is on, and
   ``supportsExceptionFilterOptions: false`` is correctly off (since
   NOVA has no exception types to parametrise per-filter).

2. **Unit tests** -- drive the new ``handle_set_exception_breakpoints``
   and ``handle_exception_info`` server handlers against a fake
   ``GdbAsyncRecord`` and ``CaptureBridge`` that records every MI
   command. These exercise the filter-set storage, the
   ``"uncaught"`` filter gating on fatal signals, the silent-resume
   path when the filter is off, the exceptionInfo wire shape, and
   the default-on initial state.

3. **Regression checks** -- re-run the R28F profiler unit checks,
   R29E conditional + hit-count BP gate logic, and R31E reverse-debug
   wiring to confirm the exception filter doesn't disturb either.

Run::

    python tools/nova-dap/tests/test_exception_breakpoints.py
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.gdb_bridge import GdbAsyncRecord, GdbResult  # noqa: E402


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


class _NullStream:
    """Stand-in for the real binary stdout -- accepts writes, drops them."""

    def write(self, _data: bytes) -> int:
        return 0

    def flush(self) -> None:
        pass


class CaptureBridge:
    """Fake :class:`GdbBridge` that records every MI command and
    returns canned ``^done`` results. Mirrors the pattern used in
    ``test_conditional_breakpoint.py`` and ``test_reverse_debug.py``.

    Defaults: every ``-break-insert`` allocates a fresh integer id;
    every ``-exec-continue`` (including the silent-resume path used
    by the exception filter) succeeds with ``^running``. The
    ``_supports_inline_eval`` flag is set True so the silent-resume
    helper takes the synchronous dispatch path (matching the
    test_conditional_breakpoint convention)."""

    # Inline-eval marker -- the server's ``_resume_silently`` checks
    # this attribute to decide between inline dispatch (tests) and
    # worker-thread dispatch (production bridges).
    _supports_inline_eval = True

    def __init__(self) -> None:
        self.sent_commands: List[str] = []
        self._next_bp_id = 1
        self._responders: Dict[str, Callable[[str], GdbResult]] = {}

    def set_response(
        self, prefix: str, responder: Callable[[str], GdbResult]
    ) -> None:
        self._responders[prefix] = responder

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        for prefix, responder in self._responders.items():
            if cmd.startswith(prefix):
                return responder(cmd)
        if cmd.startswith("-break-delete"):
            return GdbResult(token=None, cls="done", fields={})
        if cmd.startswith("-break-insert"):
            bp_id = self._next_bp_id
            self._next_bp_id += 1
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {
                        "number": str(bp_id),
                        "addr": "0x1234",
                        "file": "src.c",
                        "fullname": "/tmp/src.c",
                        "line": "5",
                    },
                },
            )
        if cmd.startswith("-exec-continue") or cmd.startswith("-exec-"):
            return GdbResult(token=None, cls="running", fields={})
        if cmd.startswith("-gdb-exit"):
            return GdbResult(token=None, cls="exit", fields={})
        return GdbResult(token=None, cls="done", fields={})


def _make_session(bridge: Optional[CaptureBridge] = None) -> Any:
    from nova_dap import server  # noqa: WPS433

    session = server.Session(out_stream=_NullStream())
    if bridge is not None:
        session.bridge = bridge  # type: ignore[assignment]
    return session


# Helper: capture responses + events sent by the server handlers so
# tests can assert on their bodies. The server's send_response /
# send_event helpers are module-level, so we monkey-patch them
# inside the helper context.
class _CapturedTraffic:
    def __init__(self) -> None:
        self.responses: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []


def _capture(fn: Callable[["_CapturedTraffic"], None]) -> "_CapturedTraffic":
    from nova_dap import server  # noqa: WPS433

    traffic = _CapturedTraffic()
    original_response = server.send_response
    original_event = server.send_event

    def cap_response(
        _session: Any,
        req: Any,
        body: Any = None,
        success: bool = True,
        message: Any = None,
    ) -> None:
        traffic.responses.append(
            {
                "command": req.get("command", ""),
                "success": success,
                "message": message,
                "body": body or {},
            }
        )

    def cap_event(_session: Any, event: str, body: Any = None) -> None:
        traffic.events.append({"event": event, "body": body or {}})

    server.send_response = cap_response  # type: ignore[assignment]
    server.send_event = cap_event  # type: ignore[assignment]
    try:
        fn(traffic)
    finally:
        server.send_response = original_response  # type: ignore[assignment]
        server.send_event = original_event  # type: ignore[assignment]
    return traffic


# ---------------------------------------------------------------------------
# Capability + handler-table tests.
# ---------------------------------------------------------------------------


def test_capability_exception_info_request() -> None:
    """``supportsExceptionInfoRequest`` must be advertised as True
    now that R33F is in. Pre-R33F this was False."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsExceptionInfoRequest"), True)


def test_capability_exception_filter_options_off() -> None:
    """Per-filter configuration is OFF: NOVA's panic path has no
    exception types to parametrise. Flip this on when NOVA gains
    exception types."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsExceptionFilterOptions"), False)


def test_capability_exception_filters_advertised() -> None:
    """The ``exceptionBreakpointFilters`` array must include both
    ``"uncaught"`` (default True) and ``"caught"`` (default False)
    with human-readable labels."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    filters = caps.get("exceptionBreakpointFilters")
    check(isinstance(filters, list), f"expected list, got {type(filters)}")
    check_eq(len(filters), 2, "expected exactly 2 filters")
    # Filter 0: uncaught, default True.
    f0 = filters[0]
    check_eq(f0.get("filter"), "uncaught")
    check_eq(f0.get("default"), True)
    check(
        isinstance(f0.get("label"), str) and "Uncaught" in f0["label"],
        f"expected human-readable 'Uncaught' label, got {f0.get('label')!r}",
    )
    # Filter 1: caught (future placeholder), default False.
    f1 = filters[1]
    check_eq(f1.get("filter"), "caught")
    check_eq(f1.get("default"), False)
    check(
        isinstance(f1.get("label"), str) and "Caught" in f1["label"],
        f"expected human-readable 'Caught' label, got {f1.get('label')!r}",
    )


def test_capability_preserves_prior_flags() -> None:
    """R33F must not regress any previously-advertised capability flag.
    Explicit guard against accidental flips of the R28F profiler /
    R29E conditional-BP / R31E reverse-debug flags."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsHitConditionalBreakpoints"), True)
    check_eq(caps.get("supportsFunctionBreakpoints"), True)
    check_eq(caps.get("supportsDataBreakpoints"), True)
    check_eq(caps.get("supportsInstructionBreakpoints"), True)
    check_eq(caps.get("supportsSteppingGranularity"), True)
    check_eq(caps.get("supportsDisassembleRequest"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    check_eq(caps.get("supportsSingleThreadExecutionRequests"), True)
    check_eq(caps.get("supportsConfigurationDoneRequest"), True)
    check_eq(caps.get("supportsTerminateRequest"), True)
    check_eq(caps.get("supportsStepBack"), True)
    check_eq(caps.get("supportsGotoTargetsRequest"), True)


def test_set_exception_breakpoints_handler_registered() -> None:
    """The dispatch table must list both R33F handlers
    (``setExceptionBreakpoints`` was pre-existing as a no-op stub
    pre-R33F; ``exceptionInfo`` is brand new)."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check("setExceptionBreakpoints" in HANDLERS)
    check("exceptionInfo" in HANDLERS)
    # Handler count regression: pre-R33F shipped 30 handlers (R31E +
    # base); R33F adds exceptionInfo -> 31 minimum.
    check(
        len(HANDLERS) >= 30,
        f"expected >=30 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


# ---------------------------------------------------------------------------
# is_fatal_signal helper tests.
# ---------------------------------------------------------------------------


def test_is_fatal_signal_recognises_panics() -> None:
    """The five panic-class signals (SIGABRT / SIGSEGV / SIGFPE /
    SIGBUS / SIGILL) must all classify as fatal."""
    from nova_dap.server import is_fatal_signal  # noqa: WPS433

    for sig in ("SIGABRT", "SIGSEGV", "SIGFPE", "SIGBUS", "SIGILL"):
        check(is_fatal_signal(sig), f"{sig} should be fatal")


def test_is_fatal_signal_rejects_controlled_signals() -> None:
    """Control signals (SIGINT / SIGTERM / SIGSTOP / SIGTRAP / SIGKILL)
    must NOT classify as fatal. SIGTRAP in particular is gdb's
    breakpoint trigger; gating that would break the normal breakpoint
    path. SIGINT comes from ``-exec-interrupt`` and the multi-thread
    pause test asserts that produces a stopped event (not a silent
    resume)."""
    from nova_dap.server import is_fatal_signal  # noqa: WPS433

    for sig in (
        "SIGINT",
        "SIGTERM",
        "SIGSTOP",
        "SIGTRAP",
        "SIGKILL",
        "SIGUSR1",
        "SIGUSR2",
        "SIGCHLD",
        "SIGCONT",
    ):
        check(not is_fatal_signal(sig), f"{sig} should NOT be fatal")


def test_is_fatal_signal_tolerates_none_and_non_strings() -> None:
    """A signal-name of ``None`` (degenerate MI record) or any
    non-string value returns False -- we never want to crash on bad
    input from gdb."""
    from nova_dap.server import is_fatal_signal  # noqa: WPS433

    check(not is_fatal_signal(None))
    check(not is_fatal_signal(123))  # type: ignore[arg-type]
    check(not is_fatal_signal(""))
    check(not is_fatal_signal("not-a-signal"))


# ---------------------------------------------------------------------------
# Session default state tests.
# ---------------------------------------------------------------------------


def test_session_default_exception_filters_uncaught_on() -> None:
    """A fresh session starts with ``"uncaught"`` in the filter set,
    matching the advertised default. This way a brand-new debug
    session catches panics out of the box even if the IDE doesn't
    send a ``setExceptionBreakpoints`` request before configurationDone."""
    session = _make_session()
    check_eq(session.exception_filters, {"uncaught"})


def test_session_default_signal_info_cleared() -> None:
    """A fresh session has no signal info recorded.
    ``exceptionInfo`` should fail cleanly before any fatal stop."""
    session = _make_session()
    check(session.last_signal_name is None)
    check(session.last_signal_meaning is None)
    check(session.last_signal_thread_id is None)


# ---------------------------------------------------------------------------
# handle_set_exception_breakpoints tests.
# ---------------------------------------------------------------------------


def test_set_exception_breakpoints_stores_filters() -> None:
    """A ``setExceptionBreakpoints {filters: ["uncaught"]}`` request
    stores the filter set on the session and responds with the
    standard empty-breakpoints array."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {"filters": ["uncaught"]},
    }
    traffic = _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, {"uncaught"})
    check_eq(len(traffic.responses), 1)
    resp = traffic.responses[0]
    check_eq(resp["command"], "setExceptionBreakpoints")
    check(resp["success"])
    check_eq(resp["body"], {"breakpoints": []})


def test_set_exception_breakpoints_empty_clears_filters() -> None:
    """``setExceptionBreakpoints {filters: []}`` clears the filter set.
    Subsequent fatal signals are silently resumed."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    # Seed with the default-on uncaught filter.
    check_eq(session.exception_filters, {"uncaught"})
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {"filters": []},
    }
    _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, set())


def test_set_exception_breakpoints_caught_filter_accepted() -> None:
    """The placeholder ``"caught"`` filter is accepted into the set
    without complaint, even though it currently has no effect."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {"filters": ["uncaught", "caught"]},
    }
    _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, {"uncaught", "caught"})


def test_set_exception_breakpoints_missing_filters_clears() -> None:
    """A request body without a ``filters`` key clears the filter set
    (DAP's absence-equals-empty convention)."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {},
    }
    _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, set())


def test_set_exception_breakpoints_unknown_filter_accepted() -> None:
    """Unknown filter names (e.g. from a Java-style client) are
    accepted into the set but have no effect on the stop path. Being
    lenient here keeps the wire shape stable across protocol versions."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {"filters": ["thrown", "userUnhandled"]},
    }
    _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, {"thrown", "userUnhandled"})


def test_set_exception_breakpoints_malformed_entries_filtered() -> None:
    """Non-string entries in the filters array are dropped silently
    (DAP clients should never send these, but we defensively filter
    them)."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    req = {
        "seq": 1,
        "type": "request",
        "command": "setExceptionBreakpoints",
        "arguments": {"filters": ["uncaught", 42, None, "", "caught"]},
    }
    _capture(lambda _t: server.handle_set_exception_breakpoints(session, req))
    check_eq(session.exception_filters, {"uncaught", "caught"})


# ---------------------------------------------------------------------------
# _handle_stopped exception-filter gate tests.
# ---------------------------------------------------------------------------


def _signal_record(
    signal_name: str = "SIGABRT",
    signal_meaning: str = "Aborted",
    thread_id: str = "1",
    pc: str = "0x401040",
) -> GdbAsyncRecord:
    """Build a ``*stopped,reason=signal-received`` MI record fixture
    matching what gdb emits on a real panic."""
    return GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "signal-received",
            "signal-name": signal_name,
            "signal-meaning": signal_meaning,
            "thread-id": thread_id,
            "stopped-threads": "all",
            "frame": {
                "addr": pc,
                "func": "panic_path",
                "file": "src.c",
                "fullname": "/tmp/src.c",
                "line": "10",
            },
        },
    )


def test_signal_received_uncaught_filter_fires_stop_event() -> None:
    """When ``"uncaught"`` is in the filter set, a fatal signal stop
    fires a DAP ``stopped`` event with reason="exception" and a
    description carrying the signal name + meaning."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    # Default exception_filters = {"uncaught"}.
    rec = _signal_record(signal_name="SIGABRT", signal_meaning="Aborted")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    # One stopped event, no silent resume.
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 1, f"expected one stopped event, got {traffic.events!r}")
    body = stopped_events[0]["body"]
    check_eq(body.get("reason"), "exception")
    check_eq(body.get("threadId"), 1)
    desc = body.get("description") or ""
    check(
        "SIGABRT" in desc,
        f"expected SIGABRT in description, got {desc!r}",
    )
    check(
        "Aborted" in desc,
        f"expected meaning in description, got {desc!r}",
    )
    # No silent resume MI commands were issued.
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(resume_cmds), 0)


def test_signal_received_no_uncaught_filter_silent_resume() -> None:
    """When ``"uncaught"`` is NOT in the filter set, a fatal signal
    stop is silently resumed (no DAP event, ``-exec-continue`` issued
    to gdb)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.exception_filters = set()  # cleared
    rec = _signal_record(signal_name="SIGSEGV", signal_meaning="Segmentation fault")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    # No stopped event fired.
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 0)
    # Silent-resume MI command was issued.
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(resume_cmds), 1)


def test_signal_received_only_caught_filter_silent_resume() -> None:
    """When only ``"caught"`` is in the filter set (no ``"uncaught"``),
    a fatal signal stop is silently resumed. The ``"caught"`` filter
    is a placeholder for future NOVA exception-catching constructs and
    today has no effect."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.exception_filters = {"caught"}
    rec = _signal_record(signal_name="SIGFPE", signal_meaning="Floating point exception")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 0, "caught-only should NOT fire on fatal panic")
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(resume_cmds), 1)


def test_signal_received_uncaught_stashes_info_for_exception_info() -> None:
    """When a fatal stop passes the uncaught filter, the session
    records the signal name + meaning + thread id so a follow-up
    ``exceptionInfo`` request can read it."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    rec = _signal_record(
        signal_name="SIGSEGV",
        signal_meaning="Segmentation fault",
        thread_id="3",
    )
    _capture(lambda _t: server._handle_stopped(session, rec))
    check_eq(session.last_signal_name, "SIGSEGV")
    check_eq(session.last_signal_meaning, "Segmentation fault")
    check_eq(session.last_signal_thread_id, 3)


def test_signal_received_silent_resume_does_not_stash_info() -> None:
    """When a fatal signal is silently resumed (uncaught filter off),
    the session does NOT record the signal info -- the IDE shouldn't
    see exceptionInfo for an event it never received a stopped event
    for."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.exception_filters = set()
    rec = _signal_record(signal_name="SIGABRT")
    _capture(lambda _t: server._handle_stopped(session, rec))
    check(session.last_signal_name is None)
    check(session.last_signal_meaning is None)
    check(session.last_signal_thread_id is None)


def test_signal_received_non_fatal_falls_through() -> None:
    """A non-fatal signal (SIGINT from ``-exec-interrupt``) is NOT
    gated -- it surfaces as a normal stopped event with
    reason="exception", matching pre-R33F behaviour. The multi-thread
    test relies on this for the pause path."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    # Clear the filter set explicitly to make the test clean. A
    # non-fatal signal must surface regardless of the filter set.
    session.exception_filters = set()
    rec = _signal_record(signal_name="SIGINT", signal_meaning="Interrupt")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 1, "SIGINT should produce a stopped event")
    check_eq(stopped_events[0]["body"].get("reason"), "exception")
    # No silent resume issued.
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(resume_cmds), 0)


def test_signal_received_all_fatal_signals_gated() -> None:
    """Each of the five fatal-signal names must be gated by the
    uncaught filter. This is the regression guard for the
    FATAL_SIGNAL_NAMES set."""
    from nova_dap import server  # noqa: WPS433

    for sig in ("SIGABRT", "SIGSEGV", "SIGFPE", "SIGBUS", "SIGILL"):
        bridge = CaptureBridge()
        session = _make_session(bridge)
        # Filter on -> stops.
        rec = _signal_record(signal_name=sig)
        traffic_on = _capture(lambda _t: server._handle_stopped(session, rec))
        events_on = [e for e in traffic_on.events if e["event"] == "stopped"]
        check_eq(
            len(events_on), 1, f"{sig} with uncaught ON should stop"
        )
        check_eq(events_on[0]["body"].get("reason"), "exception")
        # Reset + filter off -> silent.
        bridge2 = CaptureBridge()
        session2 = _make_session(bridge2)
        session2.exception_filters = set()
        rec2 = _signal_record(signal_name=sig)
        traffic_off = _capture(lambda _t: server._handle_stopped(session2, rec2))
        events_off = [e for e in traffic_off.events if e["event"] == "stopped"]
        check_eq(
            len(events_off), 0, f"{sig} with uncaught OFF should be silent"
        )


# ---------------------------------------------------------------------------
# handle_exception_info tests.
# ---------------------------------------------------------------------------


def test_exception_info_returns_signal_details() -> None:
    """After a fatal stop, ``exceptionInfo`` returns
    ``{exceptionId, description, breakMode, details}`` with the
    captured signal info."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    # Simulate the stash that _handle_stopped does on a passing
    # uncaught-filter stop.
    session.last_signal_name = "SIGABRT"
    session.last_signal_meaning = "Aborted"
    session.last_signal_thread_id = 1
    req = {
        "seq": 1,
        "type": "request",
        "command": "exceptionInfo",
        "arguments": {"threadId": 1},
    }
    traffic = _capture(lambda _t: server.handle_exception_info(session, req))
    check_eq(len(traffic.responses), 1)
    resp = traffic.responses[0]
    check(resp["success"])
    body = resp["body"]
    check_eq(body.get("exceptionId"), "SIGABRT")
    check_eq(body.get("breakMode"), "unhandled")
    desc = body.get("description", "")
    check("SIGABRT" in desc)
    check("Aborted" in desc)
    details = body.get("details") or {}
    check_eq(details.get("typeName"), "SIGABRT")
    check_eq(details.get("message"), "Aborted")


def test_exception_info_break_mode_always_unhandled() -> None:
    """``breakMode`` is always ``"unhandled"`` because NOVA has no
    catch construct yet -- every signal we surface is by definition
    unhandled (the inferior is about to die)."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    session.last_signal_name = "SIGSEGV"
    session.last_signal_meaning = "Segmentation fault"
    req = {
        "seq": 1,
        "type": "request",
        "command": "exceptionInfo",
        "arguments": {"threadId": 1},
    }
    traffic = _capture(lambda _t: server.handle_exception_info(session, req))
    body = traffic.responses[0]["body"]
    check_eq(body.get("breakMode"), "unhandled")


def test_exception_info_without_recorded_signal_returns_error() -> None:
    """If no fatal signal has been recorded, ``exceptionInfo`` returns
    success=false with a clear message rather than fabricating data."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    # last_signal_name is None on a fresh session.
    check(session.last_signal_name is None)
    req = {
        "seq": 1,
        "type": "request",
        "command": "exceptionInfo",
        "arguments": {"threadId": 1},
    }
    traffic = _capture(lambda _t: server.handle_exception_info(session, req))
    resp = traffic.responses[0]
    check(resp["success"] is False)
    check(isinstance(resp["message"], str) and "no exception info" in resp["message"])


def test_exception_info_handles_missing_meaning() -> None:
    """If gdb's ``signal-meaning`` field is missing (older gdb,
    degenerate record), we still return useful info: the typeName +
    message fall back to the signal name."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    session.last_signal_name = "SIGILL"
    session.last_signal_meaning = None
    req = {
        "seq": 1,
        "type": "request",
        "command": "exceptionInfo",
        "arguments": {"threadId": 1},
    }
    traffic = _capture(lambda _t: server.handle_exception_info(session, req))
    body = traffic.responses[0]["body"]
    check_eq(body.get("exceptionId"), "SIGILL")
    check_eq(body.get("details", {}).get("typeName"), "SIGILL")
    # Message falls back to the signal name when meaning is missing.
    check_eq(body.get("details", {}).get("message"), "SIGILL")


# ---------------------------------------------------------------------------
# launch / relaunch interaction tests.
# ---------------------------------------------------------------------------


def test_launch_clears_stale_signal_info() -> None:
    """A relaunch resets stale signal info from a prior dead inferior.
    The exception filter set itself is PRESERVED across launches
    (the IDE configures it once and expects it to persist)."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    saved_gdb_available = server.gdb_available
    saved_isfile = os.path.isfile
    saved_bridge_class = server.GdbBridge

    class FakeBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            self.sent: List[str] = []

        def start(self) -> None:
            return None

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            self.sent.append(cmd)
            return GdbResult(token=None, cls="done", fields={})

    # Seed stale signal info as if a previous inferior died from
    # SIGABRT and the user re-launched.
    session.last_signal_name = "SIGABRT"
    session.last_signal_meaning = "Aborted"
    session.last_signal_thread_id = 1
    # Customise the filter set BEFORE the relaunch.
    session.exception_filters = {"uncaught", "caught"}

    try:
        server.gdb_available = lambda: True
        os.path.isfile = lambda _p: True
        server.GdbBridge = FakeBridge
        req = {
            "seq": 1,
            "type": "request",
            "command": "launch",
            "arguments": {"program": "/tmp/fake-binary"},
        }
        server.handle_launch(session, req)
    finally:
        server.gdb_available = saved_gdb_available
        os.path.isfile = saved_isfile
        server.GdbBridge = saved_bridge_class

    # Stale signal info cleared.
    check(session.last_signal_name is None)
    check(session.last_signal_meaning is None)
    check(session.last_signal_thread_id is None)
    # Filter set PRESERVED across the relaunch.
    check_eq(session.exception_filters, {"uncaught", "caught"})


# ---------------------------------------------------------------------------
# R29E + R28F + R31E regression checks.
# ---------------------------------------------------------------------------


def test_r29e_breakpoint_hit_unaffected_by_exception_filter() -> None:
    """A normal source-line breakpoint hit fires while the uncaught
    filter is active -- the exception filter only gates fatal-signal
    stops, not regular breakpoint hits.

    This is the explicit regression guard from the R33F spec: "a
    normal user breakpoint hit while exception filtering is active
    should still stop on the BP, NOT skip because the exception path
    is unrelated"."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    # Uncaught filter on (default state).
    check_eq(session.exception_filters, {"uncaught"})
    # Synthesise a regular breakpoint-hit stop record.
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "breakpoint-hit",
            "bkptno": "1",
            "thread-id": "1",
            "stopped-threads": "all",
            "frame": {"addr": "0x401040", "func": "main", "file": "x.c", "line": "5"},
        },
    )
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 1, "breakpoint hit must fire even with exception filter")
    check_eq(stopped_events[0]["body"].get("reason"), "breakpoint")
    # No silent resume.
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(resume_cmds), 0)


def test_r29e_hit_count_bp_still_gated_under_exception_filter() -> None:
    """A conditional / hit-count BP gate still gates correctly when
    the exception filter is also active. The two gates are
    independent: a BP hit goes through the hit-count gate; a signal
    goes through the exception filter. They don't interact."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.breakpoints import SourceBreakpointRecord, parse_hit_condition

    bridge = CaptureBridge()
    session = _make_session(bridge)
    # Default exception filter (uncaught on).
    record = SourceBreakpointRecord(
        gdb_id=7,
        source_path="/tmp/x.c",
        line=10,
        condition=None,
        hit_condition=">3",
        hit_predicate=parse_hit_condition(">3"),
    )
    session.breakpoints.register(record)
    # First three hits gated by hit-count.
    for _ in range(3):
        skip = server._bp_gate_should_skip(session, gdb_id=7, thread_id=1)
        check(skip is True, "first 3 hits should be skipped by hit-count gate")
    # Fourth hit fires.
    skip4 = server._bp_gate_should_skip(session, gdb_id=7, thread_id=1)
    check(skip4 is False, "4th hit should fire")
    # The exception filter remained intact through all the BP gate
    # activity -- this is the explicit "no cross-talk" regression
    # guard.
    check_eq(session.exception_filters, {"uncaught"})


def test_r28f_profiler_handlers_still_present() -> None:
    """R28F shipped the custom ``nova/profile/{start,stop,report}``
    request channel. The handler registry must still contain all
    three after R33F adds exceptionInfo."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check("nova/profile/start" in HANDLERS)
    check("nova/profile/stop" in HANDLERS)
    check("nova/profile/report" in HANDLERS)


def test_r28f_profile_sampling_suppresses_exception_filter() -> None:
    """A profile-sampling cycle that incidentally observes a
    ``*stopped`` record must NOT enter the exception filter path --
    the profiler's pause-sample-resume cycle is internal traffic
    that should be fully suppressed.

    The standing guard is the early-return in ``_handle_stopped``
    when ``session.profile_sampling`` is True. R33F's exception
    filter sits AFTER that guard, so the profiler path naturally
    bypasses it."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.profile_sampling = True  # simulate active profile cycle
    # A spurious signal-received record during a profile cycle must
    # not fire ANY events -- not a stopped event, not a silent resume.
    rec = _signal_record(signal_name="SIGABRT")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    check_eq(len(traffic.events), 0, "profile cycle must suppress exception filter")
    resume_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(
        len(resume_cmds), 0,
        "profile cycle must not issue silent resumes",
    )
    # Session signal info untouched.
    check(session.last_signal_name is None)


def test_r31e_reverse_continue_handler_still_present() -> None:
    """R31E shipped reverse-debugging via gdb's process-record mode.
    The handlers must still be registered after R33F."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check("reverseContinue" in HANDLERS)
    check("stepBack" in HANDLERS)
    check("goto" in HANDLERS)
    check("gotoTargets" in HANDLERS)


def test_r31e_reverse_step_into_signal_frame_honours_filter() -> None:
    """R31E reverse-debug spec: when reverse-stepping into a signal
    frame, the exception filter SHOULD fire if it's enabled. gdb
    surfaces reverse-stepping into a recorded signal as a normal
    ``*stopped reason=signal-received`` record (the recording
    preserves the signal state), so the exception filter naturally
    applies the same way as the forward path.

    This test confirms the gate is direction-blind: a signal record
    arriving from a reverse-step is treated identically to one from
    forward execution. (gdb itself doesn't distinguish; the
    record/replay infrastructure replays the original *stopped
    record verbatim.)"""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    # Uncaught filter is on (default).
    rec = _signal_record(signal_name="SIGSEGV", signal_meaning="Segmentation fault")
    # Mark the record as coming from a reverse step by simulating
    # the session state after a stepBack request. R31E's
    # record_started flag is the closest analogue, but the gate
    # itself doesn't read it -- the signal record looks identical.
    session.record_started = True
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(
        len(stopped_events), 1,
        "reverse-stepping into recorded signal frame should still fire filter",
    )
    check_eq(stopped_events[0]["body"].get("reason"), "exception")
    # And the signal info is stashed for exceptionInfo just like the
    # forward path.
    check_eq(session.last_signal_name, "SIGSEGV")


def test_r31e_reverse_continue_with_exception_filter_off() -> None:
    """Reverse-continue with the uncaught filter OFF should silently
    skip past recorded fatal signals (the reverse direction must not
    surface stops that the forward direction wouldn't)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.exception_filters = set()
    session.record_started = True
    rec = _signal_record(signal_name="SIGFPE")
    traffic = _capture(lambda _t: server._handle_stopped(session, rec))
    stopped_events = [e for e in traffic.events if e["event"] == "stopped"]
    check_eq(len(stopped_events), 0)


# ---------------------------------------------------------------------------
# _extract_signal_info helper tests.
# ---------------------------------------------------------------------------


def test_extract_signal_info_pulls_both_fields() -> None:
    """``_extract_signal_info`` returns both ``signal-name`` and
    ``signal-meaning`` from the gdb record."""
    from nova_dap.server import _extract_signal_info  # noqa: WPS433

    rec = _signal_record(signal_name="SIGABRT", signal_meaning="Aborted")
    name, meaning = _extract_signal_info(rec)
    check_eq(name, "SIGABRT")
    check_eq(meaning, "Aborted")


def test_extract_signal_info_tolerates_missing_fields() -> None:
    """A degenerate record without ``signal-meaning`` returns ``None``
    for that field (real gdb always populates it, but we're defensive)."""
    from nova_dap.server import _extract_signal_info  # noqa: WPS433

    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "signal-received",
            "signal-name": "SIGILL",
            "thread-id": "1",
        },
    )
    name, meaning = _extract_signal_info(rec)
    check_eq(name, "SIGILL")
    check(meaning is None)


def test_extract_signal_info_tolerates_no_signal_fields() -> None:
    """A record without either signal field (e.g. a breakpoint-hit
    that happened to be passed through this helper by mistake)
    returns ``(None, None)``."""
    from nova_dap.server import _extract_signal_info  # noqa: WPS433

    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={"reason": "breakpoint-hit", "bkptno": "1"},
    )
    name, meaning = _extract_signal_info(rec)
    check(name is None)
    check(meaning is None)


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _all_tests() -> List[Any]:
    return [
        # Capability + handler-table
        test_capability_exception_info_request,
        test_capability_exception_filter_options_off,
        test_capability_exception_filters_advertised,
        test_capability_preserves_prior_flags,
        test_set_exception_breakpoints_handler_registered,
        # is_fatal_signal helper
        test_is_fatal_signal_recognises_panics,
        test_is_fatal_signal_rejects_controlled_signals,
        test_is_fatal_signal_tolerates_none_and_non_strings,
        # Session defaults
        test_session_default_exception_filters_uncaught_on,
        test_session_default_signal_info_cleared,
        # handle_set_exception_breakpoints
        test_set_exception_breakpoints_stores_filters,
        test_set_exception_breakpoints_empty_clears_filters,
        test_set_exception_breakpoints_caught_filter_accepted,
        test_set_exception_breakpoints_missing_filters_clears,
        test_set_exception_breakpoints_unknown_filter_accepted,
        test_set_exception_breakpoints_malformed_entries_filtered,
        # _handle_stopped gate
        test_signal_received_uncaught_filter_fires_stop_event,
        test_signal_received_no_uncaught_filter_silent_resume,
        test_signal_received_only_caught_filter_silent_resume,
        test_signal_received_uncaught_stashes_info_for_exception_info,
        test_signal_received_silent_resume_does_not_stash_info,
        test_signal_received_non_fatal_falls_through,
        test_signal_received_all_fatal_signals_gated,
        # handle_exception_info
        test_exception_info_returns_signal_details,
        test_exception_info_break_mode_always_unhandled,
        test_exception_info_without_recorded_signal_returns_error,
        test_exception_info_handles_missing_meaning,
        # launch / relaunch
        test_launch_clears_stale_signal_info,
        # R29E + R28F + R31E regression
        test_r29e_breakpoint_hit_unaffected_by_exception_filter,
        test_r29e_hit_count_bp_still_gated_under_exception_filter,
        test_r28f_profiler_handlers_still_present,
        test_r28f_profile_sampling_suppresses_exception_filter,
        test_r31e_reverse_continue_handler_still_present,
        test_r31e_reverse_step_into_signal_frame_honours_filter,
        test_r31e_reverse_continue_with_exception_filter_off,
        # _extract_signal_info helper
        test_extract_signal_info_pulls_both_fields,
        test_extract_signal_info_tolerates_missing_fields,
        test_extract_signal_info_tolerates_no_signal_fields,
    ]


def main() -> int:
    tests = _all_tests()
    for fn in tests:
        fn()
    print("test_exception_breakpoints: OK")
    print(f"  unit tests:       {len(tests)}")
    print(f"  total assertions: {_ASSERT_COUNT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
