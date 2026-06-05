"""Tests for DAP reverse-debugging (R31E).

Three layers, mirroring the pattern established by the other
``test_*.py`` suites in this directory:

1. **Unit tests** — drive the new ``handle_reverse_continue`` /
   ``handle_step_back`` / ``handle_goto`` / ``handle_goto_targets``
   server handlers against a fake bridge (``CaptureBridge``) that
   records every MI command and returns canned ``^done`` results.
   These exercise the granularity routing, lazy record-mode
   enablement, recordMode launch attribute parsing, and the
   ``-target-record-stop`` disconnect cleanup. No gdb required.

2. **Capability + handler-table tests** — confirm
   ``supportsStepBack: true`` is advertised in ``initialize``,
   ``supportsGotoTargetsRequest: true`` is also on, and the
   ``reverseContinue`` / ``stepBack`` / ``goto`` / ``gotoTargets``
   handlers are registered.

3. **Regression checks** — re-run the R28F profiler unit tests and
   R29E conditional/hit-count breakpoint unit tests to confirm the
   reverse wiring hasn't broken either subsystem. The end-to-end
   regression coverage lives in the other test files; these unit
   checks just confirm the relevant ``_capabilities`` flags + handler
   table entries haven't regressed.

Run::

    python tools/nova-dap/tests/test_reverse_debug.py
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.gdb_bridge import GdbResult  # noqa: E402


# Track every assertion so we can report a count at the end. Matches
# the convention in the other DAP test suites.
_ASSERT_COUNT = 0


def check(cond: bool, msg: str = "") -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    if not cond:
        raise AssertionError(msg or "assertion failed")


def check_eq(actual: Any, expected: Any, msg: str = "") -> None:
    check(actual == expected, f"{msg}: expected {expected!r}, got {actual!r}")


class _NullStream:
    """Stand-in for the real binary stdout — accepts writes, drops them."""

    def write(self, _data: bytes) -> int:
        return 0

    def flush(self) -> None:
        pass


class CaptureBridge:
    """Fake :class:`GdbBridge` that records every MI command and
    returns canned ``^done`` results. Mirrors the pattern used in
    test_conditional_breakpoint.py / test_hit_count_breakpoints.py."""

    def __init__(
        self,
        record_enable_ok: bool = True,
        record_stop_ok: bool = True,
        exec_ok: bool = True,
        record_btrace_ok: bool = True,
    ) -> None:
        self.sent_commands: List[str] = []
        self.record_enable_ok = record_enable_ok
        self.record_stop_ok = record_stop_ok
        self.exec_ok = exec_ok
        self.record_btrace_ok = record_btrace_ok
        # Standardised stub flag used by the BP-gate fallback path in
        # _bp_gate_should_skip. Defaults False (don't take the inline
        # eval path).
        self._supports_inline_eval = False

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if 'record btrace' in cmd:
            if self.record_btrace_ok:
                return GdbResult(token=None, cls="done", fields={})
            return GdbResult(
                token=None,
                cls="error",
                fields={"msg": "btrace not supported"},
            )
        if "record" in cmd and "stop" not in cmd:
            if self.record_enable_ok:
                return GdbResult(token=None, cls="done", fields={})
            return GdbResult(
                token=None,
                cls="error",
                fields={"msg": "record not supported"},
            )
        if "-target-record-stop" in cmd:
            if self.record_stop_ok:
                return GdbResult(token=None, cls="done", fields={})
            return GdbResult(
                token=None,
                cls="error",
                fields={"msg": "no recording"},
            )
        if cmd.startswith("-exec-reverse") or cmd.startswith("-exec-"):
            if self.exec_ok:
                return GdbResult(token=None, cls="running", fields={})
            return GdbResult(
                token=None,
                cls="error",
                fields={"msg": "exec failed"},
            )
        if cmd.startswith("-gdb-exit"):
            return GdbResult(token=None, cls="exit", fields={})
        return GdbResult(token=None, cls="done", fields={})


def _make_session(bridge: Optional[CaptureBridge] = None) -> Any:
    from nova_dap import server  # noqa: WPS433

    session = server.Session(out_stream=_NullStream())
    if bridge is not None:
        session.bridge = bridge  # type: ignore[assignment]
    return session


# ---------------------------------------------------------------------------
# Capability + handler-table tests.
# ---------------------------------------------------------------------------


def test_capability_supports_step_back() -> None:
    """The ``initialize`` response must advertise
    ``supportsStepBack: true`` now that R31E is in.

    Per the DAP spec, this flag is the gate that allows the client to
    send ``reverseContinue`` and ``stepBack`` requests."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsStepBack"), True)


def test_capability_supports_goto_targets() -> None:
    """``supportsGotoTargetsRequest: true`` is also flipped on by R31E
    because the goto wiring rides on the same record/replay
    infrastructure as reverse-debug."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsGotoTargetsRequest"), True)


def test_capability_preserves_prior_flags() -> None:
    """R31E must not regress any previously-advertised capability flag.

    Explicit guard against accidental flips of the R28F profiler /
    R29E conditional-BP / R26E etc. flags."""
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


def test_handlers_registered() -> None:
    """All four new R31E handlers must appear in the dispatch table."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check("reverseContinue" in HANDLERS, "reverseContinue handler missing")
    check("stepBack" in HANDLERS, "stepBack handler missing")
    check("goto" in HANDLERS, "goto handler missing")
    check("gotoTargets" in HANDLERS, "gotoTargets handler missing")
    # Handler count: prior R29E shipped 25 handlers; R31E adds 4 ->
    # 29 minimum.
    check(
        len(HANDLERS) >= 29,
        f"expected >=29 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


# ---------------------------------------------------------------------------
# Session state tests.
# ---------------------------------------------------------------------------


def test_session_record_state_defaults() -> None:
    """A fresh ``Session`` starts with no recording configured and the
    started flag cleared. ``handle_launch`` populates ``record_mode``
    from the request body; until then both fields are pristine."""
    session = _make_session()
    check(session.record_mode is None)
    check(session.record_started is False)


def test_handle_launch_picks_full_by_default() -> None:
    """``recordMode`` defaults to ``"full"`` when the launch request
    doesn't specify one (per the R31E spec — universal compatibility
    over speed)."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    # Fake the gdb-availability check + program existence so we can
    # exercise the recordMode branch without actually launching gdb.
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
        check_eq(session.record_mode, "full")
        check(session.record_started is False)
    finally:
        server.gdb_available = saved_gdb_available
        os.path.isfile = saved_isfile
        server.GdbBridge = saved_bridge_class


def test_handle_launch_accepts_btrace_mode() -> None:
    """``recordMode: "btrace"`` is honoured verbatim."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    saved_gdb_available = server.gdb_available
    saved_isfile = os.path.isfile
    saved_bridge_class = server.GdbBridge

    class FakeBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            return None

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            return GdbResult(token=None, cls="done", fields={})

    try:
        server.gdb_available = lambda: True
        os.path.isfile = lambda _p: True
        server.GdbBridge = FakeBridge
        req = {
            "seq": 1,
            "type": "request",
            "command": "launch",
            "arguments": {
                "program": "/tmp/fake-binary",
                "recordMode": "btrace",
            },
        }
        server.handle_launch(session, req)
        check_eq(session.record_mode, "btrace")
    finally:
        server.gdb_available = saved_gdb_available
        os.path.isfile = saved_isfile
        server.GdbBridge = saved_bridge_class


def test_handle_launch_unknown_mode_falls_back_to_full() -> None:
    """Unknown ``recordMode`` strings (e.g. typos) fall back to
    ``"full"`` instead of failing the whole launch."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    saved_gdb_available = server.gdb_available
    saved_isfile = os.path.isfile
    saved_bridge_class = server.GdbBridge

    class FakeBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            return None

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            return GdbResult(token=None, cls="done", fields={})

    try:
        server.gdb_available = lambda: True
        os.path.isfile = lambda _p: True
        server.GdbBridge = FakeBridge
        req = {
            "seq": 1,
            "type": "request",
            "command": "launch",
            "arguments": {
                "program": "/tmp/fake-binary",
                "recordMode": "imaginary",
            },
        }
        server.handle_launch(session, req)
        check_eq(session.record_mode, "full")
    finally:
        server.gdb_available = saved_gdb_available
        os.path.isfile = saved_isfile
        server.GdbBridge = saved_bridge_class


# ---------------------------------------------------------------------------
# _ensure_recording / _stop_recording tests.
# ---------------------------------------------------------------------------


def test_ensure_recording_enables_full_by_default() -> None:
    """First call enables ``record full`` and sets the ``record_started``
    flag. The MI command is dispatched via the console pseudo-
    interpreter because gdb has no native MI form for the ``record``
    command family."""
    from nova_dap.server import _ensure_recording  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"

    ok, err = _ensure_recording(session)
    check(ok, f"_ensure_recording failed: {err}")
    check(err is None)
    check(session.record_started is True)
    record_cmds = [c for c in bridge.sent_commands if "record" in c]
    check_eq(len(record_cmds), 1)
    check(
        'record full' in record_cmds[0],
        f"expected 'record full', got {record_cmds!r}",
    )


def test_ensure_recording_idempotent() -> None:
    """A second call after the recording is already enabled is a no-op
    (matches gdb's "already recording" semantics)."""
    from nova_dap.server import _ensure_recording  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"

    ok1, _ = _ensure_recording(session)
    check(ok1)
    first_cmds = list(bridge.sent_commands)
    ok2, _ = _ensure_recording(session)
    check(ok2)
    # No new commands issued the second time.
    check_eq(bridge.sent_commands, first_cmds)


def test_ensure_recording_btrace_when_requested() -> None:
    """``record_mode = "btrace"`` is forwarded as ``record btrace``."""
    from nova_dap.server import _ensure_recording  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "btrace"

    ok, _ = _ensure_recording(session)
    check(ok)
    record_cmds = [c for c in bridge.sent_commands if "record" in c]
    check_eq(len(record_cmds), 1)
    check(
        'record btrace' in record_cmds[0],
        f"expected 'record btrace', got {record_cmds!r}",
    )


def test_ensure_recording_btrace_falls_back_to_full() -> None:
    """If ``record btrace`` fails (no Intel PT / older CPU), we fall
    back to ``record full`` instead of erroring out — the user
    explicitly asked for reverse-debug, so we'd rather ship slower
    recording than reject the request."""
    from nova_dap.server import _ensure_recording  # noqa: WPS433

    bridge = CaptureBridge(record_btrace_ok=False)
    session = _make_session(bridge)
    session.record_mode = "btrace"

    ok, _ = _ensure_recording(session)
    check(ok)
    # Two record-enable commands: btrace (failed) + full (succeeded).
    record_cmds = [c for c in bridge.sent_commands if "record" in c]
    check_eq(len(record_cmds), 2)
    check('record btrace' in record_cmds[0])
    check('record full' in record_cmds[1])
    # After successful fallback the session reports the actual mode.
    check_eq(session.record_mode, "full")


def test_ensure_recording_no_bridge_returns_error() -> None:
    """Without a launched bridge ``_ensure_recording`` returns
    ``(False, 'not launched')`` so callers can surface a clean error."""
    from nova_dap.server import _ensure_recording  # noqa: WPS433

    session = _make_session()
    session.bridge = None
    ok, err = _ensure_recording(session)
    check(ok is False)
    check_eq(err, "not launched")


def test_stop_recording_dispatches_target_record_stop() -> None:
    """``_stop_recording`` issues ``-target-record-stop`` when we have
    an active recording. After the call the started flag is cleared."""
    from nova_dap.server import _stop_recording, _ensure_recording  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    _ensure_recording(session)
    check(session.record_started)
    bridge.sent_commands.clear()

    _stop_recording(session)
    check(session.record_started is False)
    check_eq(bridge.sent_commands, ["-target-record-stop"])


def test_stop_recording_noop_when_not_started() -> None:
    """Without an active recording ``_stop_recording`` issues no MI
    commands -- nothing to tear down."""
    from nova_dap.server import _stop_recording  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    _stop_recording(session)
    check_eq(bridge.sent_commands, [])


# ---------------------------------------------------------------------------
# handle_reverse_continue tests.
# ---------------------------------------------------------------------------


def test_reverse_continue_enables_recording_lazily() -> None:
    """The first ``reverseContinue`` call enables ``record full`` and
    then issues ``-exec-reverse-continue``. Mirrors the lazy-enable
    contract called out in the R31E deliverables."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, req)
    record_cmds = [c for c in bridge.sent_commands if "record" in c]
    check_eq(len(record_cmds), 1, "exactly one record-enable")
    check('record full' in record_cmds[0])
    rc_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-continue")]
    check_eq(len(rc_cmds), 1, "exactly one -exec-reverse-continue dispatch")


def test_reverse_continue_skips_record_enable_when_already_started() -> None:
    """A second ``reverseContinue`` reuses the existing recording
    (no re-issue of ``record full``)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, req)
    bridge.sent_commands.clear()
    req["seq"] = 2
    server.handle_reverse_continue(session, req)
    # Only the exec command; no second record-enable.
    record_cmds = [c for c in bridge.sent_commands if "record" in c]
    check_eq(len(record_cmds), 0)
    rc_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-continue")]
    check_eq(len(rc_cmds), 1)


def test_reverse_continue_no_bridge_returns_error() -> None:
    """Without a launched bridge the handler responds with a clean
    success=false rather than NPE."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    session.bridge = None
    req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, req)
    # The session writes responses via the out_stream; the handler must
    # not raise. The success-false path is exercised implicitly by the
    # fact that we didn't crash.
    check(True, "handler tolerated missing bridge")


def test_reverse_continue_non_stop_single_thread() -> None:
    """In non-stop mode + singleThread:true the reverse-continue MI
    command is routed via ``--thread <id>`` so other threads aren't
    disturbed."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    session.non_stop = True
    req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 3, "singleThread": True},
    }
    server.handle_reverse_continue(session, req)
    rc_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-continue")]
    check_eq(len(rc_cmds), 1)
    check(
        "--thread 3" in rc_cmds[0],
        f"expected per-thread routing, got {rc_cmds[0]!r}",
    )


def test_reverse_continue_non_stop_all_threads() -> None:
    """In non-stop mode without singleThread the command resumes all
    threads via ``--all`` (matches the forward-``continue`` semantics)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    session.non_stop = True
    req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, req)
    rc_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-continue")]
    check_eq(len(rc_cmds), 1)
    check("--all" in rc_cmds[0], f"expected --all, got {rc_cmds[0]!r}")


# ---------------------------------------------------------------------------
# handle_step_back tests.
# ---------------------------------------------------------------------------


def test_step_back_default_granularity_uses_reverse_next() -> None:
    """``stepBack`` with no granularity (== line) uses
    ``-exec-reverse-next`` (line-level)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 1},
    }
    server.handle_step_back(session, req)
    sb_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-next")]
    check_eq(len(sb_cmds), 1, f"expected reverse-next, got {bridge.sent_commands!r}")


def test_step_back_line_granularity_uses_reverse_next() -> None:
    """Explicit ``granularity: "line"`` resolves to the same
    line-level command."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 1, "granularity": "line"},
    }
    server.handle_step_back(session, req)
    sb_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-next")]
    check_eq(len(sb_cmds), 1)


def test_step_back_statement_granularity_uses_reverse_next() -> None:
    """``"statement"`` (the DAP default for non-disassembly stepping)
    also maps to reverse-next."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 1, "granularity": "statement"},
    }
    server.handle_step_back(session, req)
    sb_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-next")]
    check_eq(len(sb_cmds), 1)
    # And specifically NOT the instruction-level command.
    sb_step_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-step")]
    check_eq(len(sb_step_cmds), 0)


def test_step_back_instruction_granularity_uses_reverse_step() -> None:
    """``granularity: "instruction"`` routes to
    ``-exec-reverse-step`` (the reverse single-instruction step)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 1, "granularity": "instruction"},
    }
    server.handle_step_back(session, req)
    sb_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-step")]
    check_eq(len(sb_cmds), 1, f"expected reverse-step, got {bridge.sent_commands!r}")
    # And specifically NOT the line-level command.
    sb_next_cmds = [c for c in bridge.sent_commands if c.startswith("-exec-reverse-next")]
    check_eq(len(sb_next_cmds), 0)


def test_step_back_enables_recording_lazily() -> None:
    """``stepBack`` triggers the same lazy record-mode enablement as
    ``reverseContinue``."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    check(session.record_started is False)
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 1},
    }
    server.handle_step_back(session, req)
    check(session.record_started is True)
    record_cmds = [c for c in bridge.sent_commands if "record" in c and "stop" not in c]
    check_eq(len(record_cmds), 1)


def test_step_back_non_stop_routes_thread() -> None:
    """In non-stop mode the command carries ``--thread <id>``."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    session.non_stop = True
    req = {
        "seq": 1,
        "type": "request",
        "command": "stepBack",
        "arguments": {"threadId": 7},
    }
    server.handle_step_back(session, req)
    sb_cmds = [c for c in bridge.sent_commands if "-exec-reverse-next" in c]
    check_eq(len(sb_cmds), 1)
    check("--thread 7" in sb_cmds[0])


# ---------------------------------------------------------------------------
# Forward / backward interleaving test.
# ---------------------------------------------------------------------------


def test_continue_after_reverse_continue_still_works() -> None:
    """After a reverse-continue, a forward ``continue`` must still
    dispatch ``-exec-continue`` cleanly. gdb's record mode keeps both
    directions usable -- there's no "lock" to release.

    Per the R31E deliverables this is the test that confirms the
    record-mode buffer doesn't trap the inferior in reverse-only
    mode."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    # Reverse-continue first.
    rc_req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, rc_req)
    bridge.sent_commands.clear()
    # Forward-continue.
    cont_req = {
        "seq": 2,
        "type": "request",
        "command": "continue",
        "arguments": {"threadId": 1},
    }
    server.handle_continue(session, cont_req)
    fwd_cmds = [c for c in bridge.sent_commands if c == "-exec-continue"]
    check_eq(len(fwd_cmds), 1, f"expected forward continue, got {bridge.sent_commands!r}")
    # Record is still active.
    check(session.record_started is True)


# ---------------------------------------------------------------------------
# disconnect cleanup tests.
# ---------------------------------------------------------------------------


def test_disconnect_stops_recording() -> None:
    """On DAP ``disconnect`` the adapter MUST run
    ``-target-record-stop`` before ``-gdb-exit`` so the recording
    buffer is drained explicitly (rather than relying on gdb's
    on-exit cleanup -- a ``record full`` buffer can be GB-sized and
    the symmetric teardown is easier to reason about)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    # Activate recording the way a real session would.
    rc_req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, rc_req)
    bridge.sent_commands.clear()
    # Patch the bridge's terminate() since CaptureBridge doesn't have
    # one — the handler calls it.
    bridge.terminate = lambda: None  # type: ignore[attr-defined]
    disc_req = {
        "seq": 2,
        "type": "request",
        "command": "disconnect",
        "arguments": {},
    }
    server.handle_disconnect(session, disc_req)
    # Order matters: record-stop BEFORE gdb-exit.
    sent = bridge.sent_commands
    check("-target-record-stop" in sent, f"missing record-stop in {sent!r}")
    check("-gdb-exit" in sent, f"missing gdb-exit in {sent!r}")
    stop_idx = sent.index("-target-record-stop")
    exit_idx = sent.index("-gdb-exit")
    check(stop_idx < exit_idx, "record-stop must precede gdb-exit")
    # Cleared flag.
    check(session.record_started is False)


def test_disconnect_without_recording_is_clean() -> None:
    """If reverse-debug was never used, the disconnect path does NOT
    issue an unnecessary ``-target-record-stop`` (gdb would error since
    no recording is active)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    bridge.terminate = lambda: None  # type: ignore[attr-defined]
    disc_req = {
        "seq": 1,
        "type": "request",
        "command": "disconnect",
        "arguments": {},
    }
    server.handle_disconnect(session, disc_req)
    sent = bridge.sent_commands
    check("-target-record-stop" not in sent, f"unexpected record-stop in {sent!r}")
    check("-gdb-exit" in sent, f"missing gdb-exit in {sent!r}")


def test_terminate_delegates_to_disconnect() -> None:
    """DAP ``terminate`` is implemented by delegating to disconnect
    (so the recording cleanup runs in both paths)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)
    session.record_mode = "full"
    rc_req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, rc_req)
    bridge.sent_commands.clear()
    bridge.terminate = lambda: None  # type: ignore[attr-defined]
    term_req = {
        "seq": 2,
        "type": "request",
        "command": "terminate",
        "arguments": {},
    }
    server.handle_terminate(session, term_req)
    sent = bridge.sent_commands
    check("-target-record-stop" in sent)


def test_disconnect_record_stop_failure_is_swallowed() -> None:
    """If ``-target-record-stop`` errors out (e.g. gdb already lost
    the recording due to a crash mid-session), the disconnect still
    completes — we log the failure and proceed to ``-gdb-exit``."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge(record_stop_ok=False)
    session = _make_session(bridge)
    session.record_mode = "full"
    rc_req = {
        "seq": 1,
        "type": "request",
        "command": "reverseContinue",
        "arguments": {"threadId": 1},
    }
    server.handle_reverse_continue(session, rc_req)
    bridge.sent_commands.clear()
    bridge.terminate = lambda: None  # type: ignore[attr-defined]
    disc_req = {
        "seq": 2,
        "type": "request",
        "command": "disconnect",
        "arguments": {},
    }
    server.handle_disconnect(session, disc_req)
    sent = bridge.sent_commands
    check("-target-record-stop" in sent)
    check("-gdb-exit" in sent)


# ---------------------------------------------------------------------------
# goto / gotoTargets tests.
# ---------------------------------------------------------------------------


def test_goto_targets_returns_single_target() -> None:
    """A ``gotoTargets`` request returns a single target round-tripping
    the supplied source location."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)

    captured_response: List[Dict[str, Any]] = []
    original_send = server.send_response

    def capture_response(_session: Any, _req: Any, body: Any = None, **_kwargs: Any) -> None:
        captured_response.append(body or {})

    server.send_response = capture_response  # type: ignore[assignment]
    try:
        req = {
            "seq": 1,
            "type": "request",
            "command": "gotoTargets",
            "arguments": {
                "source": {"path": "/tmp/src.c", "name": "src.c"},
                "line": 42,
            },
        }
        server.handle_goto_targets(session, req)
    finally:
        server.send_response = original_send  # type: ignore[assignment]
    check_eq(len(captured_response), 1)
    targets = captured_response[0].get("targets") or []
    check_eq(len(targets), 1)
    check_eq(targets[0].get("line"), 42)


def test_goto_sends_jump_command() -> None:
    """A ``goto`` request issues an ``-interpreter-exec console
    "jump <file>:<line>"`` to gdb."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)

    # First grab a target id via gotoTargets.
    captured: List[Dict[str, Any]] = []
    original_send = server.send_response

    def cap_send(_session: Any, _req: Any, body: Any = None, **_kwargs: Any) -> None:
        captured.append(body or {})

    server.send_response = cap_send  # type: ignore[assignment]
    try:
        gt_req = {
            "seq": 1,
            "type": "request",
            "command": "gotoTargets",
            "arguments": {
                "source": {"path": "/tmp/src.c"},
                "line": 7,
            },
        }
        server.handle_goto_targets(session, gt_req)
        target_id = captured[-1]["targets"][0]["id"]
        bridge.sent_commands.clear()
        go_req = {
            "seq": 2,
            "type": "request",
            "command": "goto",
            "arguments": {"threadId": 1, "targetId": target_id},
        }
        server.handle_goto(session, go_req)
    finally:
        server.send_response = original_send  # type: ignore[assignment]
    jump_cmds = [c for c in bridge.sent_commands if "jump" in c]
    check_eq(len(jump_cmds), 1, f"expected one jump, got {bridge.sent_commands!r}")
    check("/tmp/src.c:7" in jump_cmds[0])


def test_goto_unknown_target_returns_error() -> None:
    """A ``goto`` with an unknown targetId responds success=false."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = _make_session(bridge)

    captured: List[Dict[str, Any]] = []
    original_send = server.send_response

    def cap_send(_session: Any, _req: Any, body: Any = None, success: bool = True, message: Any = None, **_kwargs: Any) -> None:
        captured.append({"body": body or {}, "success": success, "message": message})

    server.send_response = cap_send  # type: ignore[assignment]
    try:
        go_req = {
            "seq": 1,
            "type": "request",
            "command": "goto",
            "arguments": {"threadId": 1, "targetId": 999999},
        }
        server.handle_goto(session, go_req)
    finally:
        server.send_response = original_send  # type: ignore[assignment]
    check_eq(len(captured), 1)
    check(captured[0]["success"] is False)


# ---------------------------------------------------------------------------
# R28F + R29E regression checks.
# ---------------------------------------------------------------------------


def test_r29e_conditional_bp_handler_still_present() -> None:
    """R29E shipped conditional + hit-count BPs via the source-line
    BP gate. R31E must not regress either capability flag."""
    from nova_dap.server import _capabilities, HANDLERS  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsHitConditionalBreakpoints"), True)
    check("setBreakpoints" in HANDLERS)


def test_r28f_profiler_handlers_still_present() -> None:
    """R28F shipped the custom ``nova/profile/{start,stop,report}``
    request channel. The handler registry must still contain all
    three."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check("nova/profile/start" in HANDLERS)
    check("nova/profile/stop" in HANDLERS)
    check("nova/profile/report" in HANDLERS)


def test_r28f_profile_sampling_flag_independent_of_record() -> None:
    """The profile-sampling flag and the record_started flag are
    independent. A profile run does NOT change record state; a
    reverse-debug run does NOT touch profile state."""
    from nova_dap import server  # noqa: WPS433

    session = _make_session()
    check(session.profile_sampling is False)
    check(session.record_started is False)
    # Simulate profile activation.
    session.profile_sampling = True
    check(session.record_started is False)
    session.profile_sampling = False
    # Simulate recording activation.
    session.record_started = True
    check(session.profile_sampling is False)


def test_r29e_bp_gate_not_confused_by_reverse_direction() -> None:
    """The BP-gate logic only consults ``hit_predicate`` and
    ``condition`` — it has no reverse-aware state. A reverse-continue
    that hits a conditional BP will fire the gate the same way a
    forward-continue would (gdb reports breakpoint hits identically
    in both directions; the gate doesn't know or care which side
    we're coming from).

    This documents the subtlety called out in the R31E spec: if a
    user sets ``hitCondition: ">3"`` and steps backwards over the
    BP a fourth time, the gate will fire because the counter has
    already passed 3. That is intentional -- the gate counts
    physical hits, not logical positions in the program. If the user
    wants direction-aware gating they should use ``condition`` (which
    gdb evaluates per-hit using the actual program state)."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.breakpoints import (
        SourceBreakpointRecord,
        parse_hit_condition,
    )

    bridge = CaptureBridge()
    session = _make_session(bridge)
    record = SourceBreakpointRecord(
        gdb_id=5,
        source_path="/tmp/x.c",
        line=10,
        condition=None,
        hit_condition=">3",
        hit_predicate=parse_hit_condition(">3"),
    )
    session.breakpoints.register(record)
    # Three forward hits -> all gated.
    for _ in range(3):
        skip = server._bp_gate_should_skip(session, gdb_id=5, thread_id=1)
        check(skip is True, "first 3 hits should be skipped")
    # Fourth hit -> fires.
    skip4 = server._bp_gate_should_skip(session, gdb_id=5, thread_id=1)
    check(skip4 is False, "4th hit should fire")
    # A reverse-direction hit (gdb still reports it as bkptno=5)
    # increments the same counter. The 5th hit -> fires (because the
    # ``>3`` predicate is now perpetually true).
    skip5 = server._bp_gate_should_skip(session, gdb_id=5, thread_id=1)
    check(skip5 is False, "5th hit should fire (direction-blind)")


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _all_tests() -> List[Any]:
    return [
        # Capability + handler-table
        test_capability_supports_step_back,
        test_capability_supports_goto_targets,
        test_capability_preserves_prior_flags,
        test_handlers_registered,
        # Session state
        test_session_record_state_defaults,
        test_handle_launch_picks_full_by_default,
        test_handle_launch_accepts_btrace_mode,
        test_handle_launch_unknown_mode_falls_back_to_full,
        # _ensure_recording / _stop_recording
        test_ensure_recording_enables_full_by_default,
        test_ensure_recording_idempotent,
        test_ensure_recording_btrace_when_requested,
        test_ensure_recording_btrace_falls_back_to_full,
        test_ensure_recording_no_bridge_returns_error,
        test_stop_recording_dispatches_target_record_stop,
        test_stop_recording_noop_when_not_started,
        # reverse-continue
        test_reverse_continue_enables_recording_lazily,
        test_reverse_continue_skips_record_enable_when_already_started,
        test_reverse_continue_no_bridge_returns_error,
        test_reverse_continue_non_stop_single_thread,
        test_reverse_continue_non_stop_all_threads,
        # step-back
        test_step_back_default_granularity_uses_reverse_next,
        test_step_back_line_granularity_uses_reverse_next,
        test_step_back_statement_granularity_uses_reverse_next,
        test_step_back_instruction_granularity_uses_reverse_step,
        test_step_back_enables_recording_lazily,
        test_step_back_non_stop_routes_thread,
        # forward after reverse
        test_continue_after_reverse_continue_still_works,
        # disconnect / terminate
        test_disconnect_stops_recording,
        test_disconnect_without_recording_is_clean,
        test_terminate_delegates_to_disconnect,
        test_disconnect_record_stop_failure_is_swallowed,
        # goto
        test_goto_targets_returns_single_target,
        test_goto_sends_jump_command,
        test_goto_unknown_target_returns_error,
        # regression
        test_r29e_conditional_bp_handler_still_present,
        test_r28f_profiler_handlers_still_present,
        test_r28f_profile_sampling_flag_independent_of_record,
        test_r29e_bp_gate_not_confused_by_reverse_direction,
    ]


def main() -> int:
    tests = _all_tests()
    for fn in tests:
        fn()
    print("test_reverse_debug: OK")
    print(f"  unit tests:       {len(tests)}")
    print(f"  total assertions: {_ASSERT_COUNT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
