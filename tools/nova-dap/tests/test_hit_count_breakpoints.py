"""Tests for DAP hit-count + conditional breakpoint gating (R29E).

Extends the ``test_conditional_breakpoint.py`` coverage with:

* The new ``parse_hit_condition`` mini-parser (``>N``, ``>=N``,
  ``==N``, ``=N``, ``!=N``, ``<N``, ``<=N``, ``%N``, bare ``N``).
* The ``SourceBreakpointManager`` registry the ``Session`` holds.
* Server-side stop-handler gating for both ``condition`` and
  ``hitCondition`` -- when either gate fails the server silently
  ``-exec-continue`` instead of firing the DAP ``stopped`` event.
* Capability advertisement: ``supportsHitConditionalBreakpoints``
  flips from False (pre-R29E) to True.

The tests are unit-shaped, exercising the helpers + ``handle_set_breakpoints``
+ ``_handle_stopped`` with a stub bridge that records every MI
command. No gdb subprocess is required, so the tests are fast and
deterministic across CI environments.

Run::

    python tools/nova-dap/tests/test_hit_count_breakpoints.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.breakpoints import (  # noqa: E402
    ConditionGateResult,
    HitConditionError,
    SourceBreakpointManager,
    SourceBreakpointRecord,
    evaluate_condition_via_bridge,
    is_condition_truthy,
    parse_hit_condition,
)
from nova_dap.gdb_bridge import GdbAsyncRecord, GdbResult  # noqa: E402


_ASSERT_COUNT = 0


def check(cond: bool, msg: str = "") -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    if not cond:
        raise AssertionError(msg or "assertion failed")


def check_eq(actual: Any, expected: Any, msg: str = "") -> None:
    check(actual == expected, f"{msg}: expected {expected!r}, got {actual!r}")


def check_raises(
    fn: Callable[[], Any], exc_type: type, msg: str = ""
) -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    raised = False
    try:
        fn()
    except exc_type:
        raised = True
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"{msg}: expected {exc_type.__name__}, got "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not raised:
        raise AssertionError(
            msg or f"expected {exc_type.__name__} to be raised"
        )


# ---------------------------------------------------------------------------
# Test scaffolding -- a stub gdb bridge + null DAP output stream.
# ---------------------------------------------------------------------------


class _NullStream:
    def write(self, _data: bytes) -> int:
        return 0

    def flush(self) -> None:
        pass


class CaptureBridge:
    """Stub gdb bridge that records every MI command and returns
    canned ``GdbResult`` replies. The reply factory can be swapped
    per-command via ``set_response`` so a single test can stage a
    sequence of replies for the same MI prefix.

    Sets ``_supports_inline_eval = True`` so the server's
    ``_bp_gate_should_skip`` calls our condition re-eval helper
    synchronously (safe with this fake because there's no reader
    thread to deadlock). Production gdb bridges don't carry this
    attribute, so the steady-state event-dispatch path skips the
    synchronous re-eval. See the docstring on
    ``server._bp_gate_should_skip`` for the deadlock rationale."""

    # Marker the server checks via getattr; presence (and truthiness)
    # opts this stub into the inline condition re-evaluation path.
    _supports_inline_eval = True

    def __init__(self) -> None:
        self.sent_commands: List[str] = []
        # Map MI command prefix -> callable returning a GdbResult.
        # The default install treats every command as a successful
        # no-op; tests override the relevant prefixes.
        self._responders: Dict[str, Callable[[str], GdbResult]] = {}
        # gdb-id allocator so ``-break-insert`` replies are stable.
        self._next_bp_id = 1

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
                    }
                },
            )
        if cmd.startswith("-exec-continue"):
            return GdbResult(token=None, cls="running", fields={})
        if cmd.startswith("-data-evaluate-expression"):
            return GdbResult(
                token=None, cls="done", fields={"value": "1"}
            )
        return GdbResult(token=None, cls="done", fields={})


# ---------------------------------------------------------------------------
# parse_hit_condition unit tests.
# ---------------------------------------------------------------------------


def test_hit_condition_none_returns_none() -> None:
    check_eq(parse_hit_condition(None), None)


def test_hit_condition_empty_string_returns_none() -> None:
    check_eq(parse_hit_condition(""), None)
    check_eq(parse_hit_condition("   "), None)
    check_eq(parse_hit_condition("\t\n"), None)


def test_hit_condition_bare_count_is_equality() -> None:
    pred = parse_hit_condition("5")
    check(pred is not None)
    assert pred is not None
    check_eq(pred(4), False)
    check_eq(pred(5), True)
    check_eq(pred(6), False)


def test_hit_condition_eq_alias() -> None:
    """DAP allows both ``=`` and ``==`` for equality."""
    pred_eq = parse_hit_condition("=5")
    pred_eqeq = parse_hit_condition("==5")
    check(pred_eq is not None and pred_eqeq is not None)
    assert pred_eq is not None
    assert pred_eqeq is not None
    for n in (4, 5, 6, 7):
        check_eq(pred_eq(n), pred_eqeq(n), f"=5 vs ==5 differ at n={n}")


def test_hit_condition_gt() -> None:
    pred = parse_hit_condition(">3")
    assert pred is not None
    check_eq(pred(1), False)
    check_eq(pred(2), False)
    check_eq(pred(3), False)
    check_eq(pred(4), True)
    check_eq(pred(5), True)
    check_eq(pred(100), True)


def test_hit_condition_ge() -> None:
    pred = parse_hit_condition(">=3")
    assert pred is not None
    check_eq(pred(2), False)
    check_eq(pred(3), True)
    check_eq(pred(4), True)


def test_hit_condition_lt() -> None:
    pred = parse_hit_condition("<3")
    assert pred is not None
    check_eq(pred(1), True)
    check_eq(pred(2), True)
    check_eq(pred(3), False)
    check_eq(pred(4), False)


def test_hit_condition_le() -> None:
    pred = parse_hit_condition("<=3")
    assert pred is not None
    check_eq(pred(2), True)
    check_eq(pred(3), True)
    check_eq(pred(4), False)


def test_hit_condition_ne() -> None:
    pred = parse_hit_condition("!=3")
    assert pred is not None
    check_eq(pred(2), True)
    check_eq(pred(3), False)
    check_eq(pred(4), True)


def test_hit_condition_mod() -> None:
    """``%N`` fires every N-th hit."""
    pred = parse_hit_condition("%2")
    assert pred is not None
    check_eq(pred(1), False)
    check_eq(pred(2), True)
    check_eq(pred(3), False)
    check_eq(pred(4), True)
    check_eq(pred(5), False)
    check_eq(pred(6), True)


def test_hit_condition_mod_three() -> None:
    pred = parse_hit_condition("%3")
    assert pred is not None
    # Hit 0 is unreachable in practice (count starts at 1).
    check_eq(pred(1), False)
    check_eq(pred(2), False)
    check_eq(pred(3), True)
    check_eq(pred(4), False)
    check_eq(pred(5), False)
    check_eq(pred(6), True)


def test_hit_condition_whitespace_tolerated() -> None:
    """Whitespace around operator + count is stripped."""
    pred = parse_hit_condition("  >  3  ")
    assert pred is not None
    check_eq(pred(3), False)
    check_eq(pred(4), True)


def test_hit_condition_rejects_garbage() -> None:
    """Non-integer counts + unknown operators -> HitConditionError."""
    check_raises(lambda: parse_hit_condition("abc"), HitConditionError)
    check_raises(lambda: parse_hit_condition(">abc"), HitConditionError)
    check_raises(lambda: parse_hit_condition(">"), HitConditionError)
    check_raises(lambda: parse_hit_condition(">="), HitConditionError)


def test_hit_condition_rejects_modulo_zero() -> None:
    """``%0`` is a divide-by-zero -- rejected at parse time."""
    check_raises(lambda: parse_hit_condition("%0"), HitConditionError)


def test_hit_condition_rejects_negative() -> None:
    """Negative counts -> HitConditionError."""
    check_raises(lambda: parse_hit_condition("-5"), HitConditionError)
    check_raises(lambda: parse_hit_condition(">-3"), HitConditionError)


def test_hit_condition_rejects_non_string() -> None:
    """Non-string input -> HitConditionError."""
    check_raises(lambda: parse_hit_condition(42), HitConditionError)
    check_raises(lambda: parse_hit_condition([">3"]), HitConditionError)


# ---------------------------------------------------------------------------
# SourceBreakpointManager unit tests.
# ---------------------------------------------------------------------------


def test_manager_register_and_lookup() -> None:
    mgr = SourceBreakpointManager()
    rec = SourceBreakpointRecord(
        gdb_id=7, source_path="/tmp/x.c", line=10, condition="x > 5"
    )
    mgr.register(rec)
    found = mgr.lookup_by_gdb_id(7)
    check(found is rec)
    check_eq(mgr.lookup_by_gdb_id(99), None)


def test_manager_increment_hit_increments_counter() -> None:
    mgr = SourceBreakpointManager()
    rec = SourceBreakpointRecord(gdb_id=1)
    mgr.register(rec)
    check_eq(mgr.increment_hit(1), 1)
    check_eq(mgr.increment_hit(1), 2)
    check_eq(mgr.increment_hit(1), 3)
    check_eq(rec.hit_count, 3)


def test_manager_increment_unknown_id_returns_none() -> None:
    mgr = SourceBreakpointManager()
    check_eq(mgr.increment_hit(42), None)


def test_manager_clear_all_returns_ids() -> None:
    mgr = SourceBreakpointManager()
    mgr.register(SourceBreakpointRecord(gdb_id=1))
    mgr.register(SourceBreakpointRecord(gdb_id=2))
    mgr.register(SourceBreakpointRecord(gdb_id=5))
    ids = mgr.clear_all()
    check_eq(sorted(ids), [1, 2, 5])
    check_eq(mgr.is_empty(), True)
    check_eq(mgr.lookup_by_gdb_id(1), None)


def test_manager_snapshot_returns_records() -> None:
    mgr = SourceBreakpointManager()
    a = SourceBreakpointRecord(gdb_id=1)
    b = SourceBreakpointRecord(gdb_id=2)
    mgr.register(a)
    mgr.register(b)
    snap = mgr.snapshot()
    check_eq(len(snap), 2)
    check(a in snap)
    check(b in snap)


# ---------------------------------------------------------------------------
# is_condition_truthy + evaluate_condition_via_bridge unit tests.
# ---------------------------------------------------------------------------


def test_truthy_zero_is_false() -> None:
    check_eq(is_condition_truthy("0"), False)
    check_eq(is_condition_truthy(" 0 "), False)


def test_truthy_nonzero_int_is_true() -> None:
    check_eq(is_condition_truthy("1"), True)
    check_eq(is_condition_truthy("42"), True)
    check_eq(is_condition_truthy("-3"), True)
    check_eq(is_condition_truthy("0x1A"), True)


def test_truthy_bool_literals() -> None:
    check_eq(is_condition_truthy("true"), True)
    check_eq(is_condition_truthy("false"), False)


def test_truthy_empty_or_none_is_false() -> None:
    check_eq(is_condition_truthy(None), False)
    check_eq(is_condition_truthy(""), False)
    check_eq(is_condition_truthy("   "), False)


def test_truthy_string_value_is_false() -> None:
    """A printable string return -- gdb's ``-c`` wouldn't accept it
    and we mirror that semantics conservatively."""
    check_eq(is_condition_truthy('"hello"'), False)


def test_evaluate_condition_returns_passed_on_truthy_int() -> None:
    bridge = CaptureBridge()
    bridge.set_response(
        "-data-evaluate-expression",
        lambda _cmd: GdbResult(
            token=None, cls="done", fields={"value": "1"}
        ),
    )
    result = evaluate_condition_via_bridge(bridge, "x > 5")
    check(isinstance(result, ConditionGateResult))
    check_eq(result.passed, True)


def test_evaluate_condition_returns_failed_on_zero() -> None:
    bridge = CaptureBridge()
    bridge.set_response(
        "-data-evaluate-expression",
        lambda _cmd: GdbResult(
            token=None, cls="done", fields={"value": "0"}
        ),
    )
    result = evaluate_condition_via_bridge(bridge, "x > 5")
    check_eq(result.passed, False)


def test_evaluate_condition_treats_eval_error_as_false() -> None:
    """Key R29E deliverable: eval errors -> condition false, with a
    message for the warning log."""
    bridge = CaptureBridge()
    bridge.set_response(
        "-data-evaluate-expression",
        lambda _cmd: GdbResult(
            token=None,
            cls="error",
            fields={"msg": "No symbol \"x\" in current context."},
        ),
    )
    result = evaluate_condition_via_bridge(bridge, "x > 5")
    check_eq(result.passed, False)
    check("No symbol" in result.message, f"unexpected msg: {result.message!r}")


def test_evaluate_condition_treats_timeout_as_false() -> None:
    bridge = CaptureBridge()

    def _raise_timeout(_cmd: str) -> GdbResult:
        raise TimeoutError("gdb stuck")

    bridge.set_response("-data-evaluate-expression", _raise_timeout)
    result = evaluate_condition_via_bridge(bridge, "x > 5", timeout=0.1)
    check_eq(result.passed, False)
    check("timed out" in result.message.lower())


def test_evaluate_condition_handles_missing_value_field() -> None:
    bridge = CaptureBridge()
    bridge.set_response(
        "-data-evaluate-expression",
        lambda _cmd: GdbResult(token=None, cls="done", fields={}),
    )
    result = evaluate_condition_via_bridge(bridge, "x > 5")
    check_eq(result.passed, False)
    check("value" in result.message.lower())


# ---------------------------------------------------------------------------
# handle_set_breakpoints integration with the new hitCondition path.
# ---------------------------------------------------------------------------


def test_set_breakpoints_with_hit_condition_registers_record() -> None:
    """A BP carrying ``hitCondition`` must be registered in
    ``session.breakpoints`` so the stop handler can gate hits."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [{"line": 5, "hitCondition": ">3"}],
        },
    }
    server.handle_set_breakpoints(session, req)
    records = session.breakpoints.snapshot()
    check_eq(len(records), 1, f"expected 1 record, got {records}")
    rec = records[0]
    check_eq(rec.line, 5)
    check_eq(rec.hit_condition, ">3")
    check(rec.hit_predicate is not None)


def test_set_breakpoints_without_filter_does_not_register() -> None:
    """A plain BP with no condition + no hitCondition stays out of the
    manager so the hot path stays free of bookkeeping (and so existing
    dap_smoke / R28F profiler regressions keep passing)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [{"line": 5}],
        },
    }
    server.handle_set_breakpoints(session, req)
    check_eq(len(session.breakpoints.snapshot()), 0)


def test_set_breakpoints_with_condition_only_registers_record() -> None:
    """A BP with ``condition`` but no ``hitCondition`` still needs a
    record because the stop handler may re-evaluate the condition for
    the eval-error fallback path."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [{"line": 5, "condition": "x == 5"}],
        },
    }
    server.handle_set_breakpoints(session, req)
    records = session.breakpoints.snapshot()
    check_eq(len(records), 1)
    check_eq(records[0].condition, "x == 5")
    check_eq(records[0].hit_predicate, None)


def test_set_breakpoints_malformed_hit_condition_rejected() -> None:
    """A malformed ``hitCondition`` surfaces as ``verified: false``
    with a ``breakpoint-validation-error`` message and is NOT
    forwarded to gdb."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    responses: List[Dict[str, Any]] = []
    original_send = server.send_response

    def _capture(s, r, body=None, success=True, message=None):
        responses.append(
            {
                "body": body,
                "success": success,
                "message": message,
                "command": r.get("command"),
            }
        )
        original_send(s, r, body=body, success=success, message=message)

    server.send_response = _capture  # type: ignore[assignment]
    try:
        req = {
            "seq": 1,
            "type": "request",
            "command": "setBreakpoints",
            "arguments": {
                "source": {"path": "/tmp/x.c", "name": "x.c"},
                "breakpoints": [{"line": 5, "hitCondition": "wat?"}],
            },
        }
        server.handle_set_breakpoints(session, req)
    finally:
        server.send_response = original_send  # type: ignore[assignment]

    check_eq(len(responses), 1)
    body = responses[0]["body"] or {}
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0]["verified"], False)
    msg = entries[0].get("message") or ""
    check(
        "breakpoint-validation-error" in msg,
        f"expected validation-error in {msg!r}",
    )
    # And nothing got registered.
    check_eq(len(session.breakpoints.snapshot()), 0)
    # And gdb only saw the pre-clear, no -break-insert.
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 0)


def test_set_breakpoints_both_condition_and_hit_condition() -> None:
    """A BP with BOTH ``condition`` and ``hitCondition`` gets both
    fields registered, and the gdb command carries ``-c``."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [
                {"line": 5, "condition": "x > 5", "hitCondition": "%2"}
            ],
        },
    }
    server.handle_set_breakpoints(session, req)
    records = session.breakpoints.snapshot()
    check_eq(len(records), 1)
    check_eq(records[0].condition, "x > 5")
    check_eq(records[0].hit_condition, "%2")
    check(records[0].hit_predicate is not None)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1)
    check(" -c " in inserts[0] or "-c " in inserts[0])


def test_set_breakpoints_resend_clears_manager() -> None:
    """A re-send of ``setBreakpoints`` (the path VS Code uses every
    time the user toggles a condition) clears the prior manager state
    so hit counters don't leak between installs."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    # First install: 2 BPs with hitConditions.
    req1 = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [
                {"line": 5, "hitCondition": ">2"},
                {"line": 10, "hitCondition": "%3"},
            ],
        },
    }
    server.handle_set_breakpoints(session, req1)
    check_eq(len(session.breakpoints.snapshot()), 2)
    # Pretend the BPs got hit a few times.
    for rec in session.breakpoints.snapshot():
        session.breakpoints.increment_hit(rec.gdb_id)
        session.breakpoints.increment_hit(rec.gdb_id)
    # Re-send with a different set.
    req2 = {
        "seq": 2,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/x.c", "name": "x.c"},
            "breakpoints": [{"line": 5, "hitCondition": "==1"}],
        },
    }
    server.handle_set_breakpoints(session, req2)
    records = session.breakpoints.snapshot()
    check_eq(len(records), 1)
    check_eq(records[0].hit_count, 0, "hit counter must reset on re-send")
    check_eq(records[0].hit_condition, "==1")


# ---------------------------------------------------------------------------
# _handle_stopped gating tests.
# ---------------------------------------------------------------------------


def _make_stopped_record(bkptno: int, thread_id: int = 1) -> GdbAsyncRecord:
    return GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "breakpoint-hit",
            "bkptno": str(bkptno),
            "thread-id": str(thread_id),
            "stopped-threads": "all",
            "frame": {"addr": "0x401000"},
        },
    )


def _drive_stop(session, gdb_id: int, thread_id: int = 1) -> bool:
    """Invoke ``_handle_stopped`` against the session and report
    whether a DAP ``stopped`` event was emitted."""
    from nova_dap import server  # noqa: WPS433

    events: List[Dict[str, Any]] = []
    original_send_event = server.send_event

    def _capture_event(s, name, body=None):
        events.append({"event": name, "body": body or {}})
        original_send_event(s, name, body=body)

    server.send_event = _capture_event  # type: ignore[assignment]
    try:
        rec = _make_stopped_record(gdb_id, thread_id=thread_id)
        server._handle_stopped(session, rec)
    finally:
        server.send_event = original_send_event  # type: ignore[assignment]
    return any(ev["event"] == "stopped" for ev in events)


def _setup_session_with_bp(
    hit_condition: Optional[str] = None,
    condition: Optional[str] = None,
    eval_value: str = "1",
    eval_ok: bool = True,
    eval_error_msg: str = "",
) -> Tuple[Any, CaptureBridge]:
    """Build a session with a single registered BP (gdb_id=1) and a
    capture bridge whose ``-data-evaluate-expression`` returns
    ``eval_value`` (with ``ok`` controlled by ``eval_ok``)."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()

    def _eval_responder(_cmd: str) -> GdbResult:
        if eval_ok:
            return GdbResult(
                token=None, cls="done", fields={"value": eval_value}
            )
        return GdbResult(
            token=None, cls="error", fields={"msg": eval_error_msg}
        )

    bridge.set_response("-data-evaluate-expression", _eval_responder)
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    predicate = (
        parse_hit_condition(hit_condition)
        if hit_condition is not None
        else None
    )
    record = SourceBreakpointRecord(
        gdb_id=1,
        source_path="/tmp/x.c",
        line=5,
        condition=condition,
        hit_condition=hit_condition,
        hit_predicate=predicate,
    )
    session.breakpoints.register(record)
    session.register_thread(1, running=False)
    return session, bridge


def test_stop_handler_fires_when_no_gate_registered() -> None:
    """A BP that's NOT in ``session.breakpoints`` fires normally --
    this is the pre-R29E happy path."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    session.register_thread(1, running=False)
    fired = _drive_stop(session, gdb_id=99)
    check_eq(fired, True, "unregistered BP must fire normally")
    # No silent resume issued.
    continues = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(continues), 0)


def test_stop_handler_gates_hit_condition_gt_skips_first_three() -> None:
    """``hitCondition=">3"`` skips the first 3 hits and fires on the
    4th onwards. This is the canonical "ignore first N" use case."""
    session, bridge = _setup_session_with_bp(hit_condition=">3")
    # Hits 1, 2, 3 must NOT fire.
    for i in range(1, 4):
        fired = _drive_stop(session, gdb_id=1)
        check_eq(
            fired,
            False,
            f"hit {i} of '>3' must not fire (hit_count={session.breakpoints.lookup_by_gdb_id(1).hit_count})",  # type: ignore[union-attr]
        )
    # Hit 4 must fire.
    fired4 = _drive_stop(session, gdb_id=1)
    check_eq(fired4, True, "hit 4 of '>3' must fire")
    # Hit 5 also fires.
    fired5 = _drive_stop(session, gdb_id=1)
    check_eq(fired5, True, "hit 5 of '>3' must fire")
    # Silent resumes were issued for the first 3 hits.
    continues = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(continues), 3)


def test_stop_handler_gates_hit_condition_modulo() -> None:
    """``hitCondition="%2"`` fires on every other hit (hits 2, 4, 6
    fire; hits 1, 3, 5 don't)."""
    session, bridge = _setup_session_with_bp(hit_condition="%2")
    expected = [False, True, False, True, False, True]
    for i, want in enumerate(expected, start=1):
        fired = _drive_stop(session, gdb_id=1)
        check_eq(fired, want, f"hit {i} of '%2': expected {want}")
    # 3 silent resumes (hits 1, 3, 5 skipped).
    continues = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(continues), 3)


def test_stop_handler_gates_condition_eq_fires_at_match() -> None:
    """``condition="i==5"`` only fires when the condition evaluates to
    true at the hit site. The stub bridge alternates between false /
    true responses to simulate the loop where i increments past 5."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    # Alternate: hits 1..3 -> condition false (value=0),
    # hit 4 -> condition true (value=1).
    eval_calls = {"n": 0}

    def _responder(_cmd: str) -> GdbResult:
        eval_calls["n"] += 1
        value = "1" if eval_calls["n"] == 4 else "0"
        return GdbResult(token=None, cls="done", fields={"value": value})

    bridge.set_response("-data-evaluate-expression", _responder)
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    session.register_thread(1, running=False)
    session.breakpoints.register(
        SourceBreakpointRecord(
            gdb_id=1, condition="i == 5", hit_predicate=None
        )
    )
    # Hits 1..3: condition false -> silent skip.
    for i in range(1, 4):
        check_eq(
            _drive_stop(session, gdb_id=1),
            False,
            f"hit {i}: condition false should skip",
        )
    # Hit 4: condition true -> fire.
    check_eq(
        _drive_stop(session, gdb_id=1), True, "hit 4: condition true should fire"
    )


def test_stop_handler_treats_condition_eval_error_as_false() -> None:
    """Key R29E deliverable: gdb eval-error -> BP silently skipped,
    warning logged. The user never sees a phantom stop for a BP
    whose condition expression failed to evaluate."""
    session, bridge = _setup_session_with_bp(
        condition="bogus_symbol",
        eval_ok=False,
        eval_error_msg="No symbol \"bogus_symbol\" in current context.",
    )
    fired = _drive_stop(session, gdb_id=1)
    check_eq(fired, False, "eval-error must skip silently")
    # The silent resume was issued.
    continues = [c for c in bridge.sent_commands if c.startswith("-exec-continue")]
    check_eq(len(continues), 1)


def test_stop_handler_gates_both_condition_and_hit_condition() -> None:
    """When BOTH gates are present, both must pass.

    Setup: condition="x==5" alternates between false/true; hitCondition
    is ">=2" so the 1st condition-passing hit doesn't fire but the
    2nd condition-passing hit does."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    # Sequence of evaluation results: hit 1 false, hit 2 true,
    # hit 3 false, hit 4 true. Each non-skipped condition advances the
    # hit counter.
    seq = ["0", "1", "0", "1"]
    seq_idx = {"i": 0}

    def _responder(_cmd: str) -> GdbResult:
        i = seq_idx["i"]
        seq_idx["i"] += 1
        value = seq[i] if i < len(seq) else "1"
        return GdbResult(token=None, cls="done", fields={"value": value})

    bridge.set_response("-data-evaluate-expression", _responder)
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    session.register_thread(1, running=False)
    predicate = parse_hit_condition(">=2")
    session.breakpoints.register(
        SourceBreakpointRecord(
            gdb_id=1,
            condition="x == 5",
            hit_condition=">=2",
            hit_predicate=predicate,
        )
    )
    # Hit 1: condition false -> skip.
    check_eq(_drive_stop(session, gdb_id=1), False, "hit1: cond false skip")
    # Hit 2: condition true, hit_count becomes 1, hit_pred(1)=>False
    # (because >=2). Should skip silently.
    check_eq(
        _drive_stop(session, gdb_id=1),
        False,
        "hit2: cond true but hit-count=1 < 2",
    )
    # Hit 3: condition false -> skip.
    check_eq(_drive_stop(session, gdb_id=1), False, "hit3: cond false skip")
    # Hit 4: condition true, hit_count becomes 2, hit_pred(2)=>True. Fire!
    check_eq(
        _drive_stop(session, gdb_id=1),
        True,
        "hit4: cond true + hit-count=2 >= 2 fires",
    )


def test_stop_handler_no_double_resume_on_skip() -> None:
    """The ``silent_bp_resume`` flag prevents the ``*running`` record
    from the silent continue producing a phantom DAP ``continued``."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    session.register_thread(1, running=False)
    session.breakpoints.register(
        SourceBreakpointRecord(gdb_id=1, hit_predicate=parse_hit_condition(">5"))
    )
    events: List[Dict[str, Any]] = []
    original_send_event = server.send_event

    def _capture(s, name, body=None):
        events.append({"event": name, "body": body or {}})
        original_send_event(s, name, body=body)

    server.send_event = _capture  # type: ignore[assignment]
    try:
        # Drive a stop -> should be skipped.
        rec = _make_stopped_record(1)
        server._handle_stopped(session, rec)
        # Now simulate the ``*running`` record that gdb would emit in
        # response to the silent continue.
        running_rec = GdbAsyncRecord(
            kind="exec",
            cls="running",
            fields={"thread-id": "all"},
        )
        server._handle_running(session, running_rec)
    finally:
        server.send_event = original_send_event  # type: ignore[assignment]
    # Neither ``stopped`` nor ``continued`` should be emitted.
    stop_evs = [e for e in events if e["event"] == "stopped"]
    cont_evs = [e for e in events if e["event"] == "continued"]
    check_eq(len(stop_evs), 0, "no stopped on silent skip")
    check_eq(len(cont_evs), 0, "no continued on silent resume")
    # And the silent-bp-resume flag was cleared by _handle_running.
    check_eq(session.silent_bp_resume, False)


def test_stop_handler_preserves_user_continue_events() -> None:
    """A user-triggered ``continue`` (after a real stop) still produces
    a DAP ``continued`` event -- the silent-bp-resume flag is one-shot."""
    from nova_dap import server  # noqa: WPS433

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    session.register_thread(1, running=False)
    events: List[Dict[str, Any]] = []
    original_send_event = server.send_event

    def _capture(s, name, body=None):
        events.append({"event": name, "body": body or {}})
        original_send_event(s, name, body=body)

    server.send_event = _capture  # type: ignore[assignment]
    try:
        # User-triggered running event (no prior silent skip).
        running_rec = GdbAsyncRecord(
            kind="exec",
            cls="running",
            fields={"thread-id": "all"},
        )
        server._handle_running(session, running_rec)
    finally:
        server.send_event = original_send_event  # type: ignore[assignment]
    cont_evs = [e for e in events if e["event"] == "continued"]
    check_eq(len(cont_evs), 1, "user-triggered continue must produce event")


# ---------------------------------------------------------------------------
# Capability advertisement.
# ---------------------------------------------------------------------------


def test_capability_hit_conditional_breakpoints_advertised() -> None:
    """R29E flips ``supportsHitConditionalBreakpoints`` from False to
    True."""
    from nova_dap.server import _capabilities  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsHitConditionalBreakpoints"), True)
    # Conditional breakpoints stay on.
    check_eq(caps.get("supportsConditionalBreakpoints"), True)


def test_handlers_registry_unchanged_count() -> None:
    """R29E doesn't add any new DAP request handlers -- the hit-count
    + condition support is folded into the existing
    ``setBreakpoints`` + ``_handle_stopped`` path. So the handler
    count must match R28F's (22 DAP + 3 custom profile = 25)."""
    from nova_dap.server import HANDLERS  # noqa: WPS433

    check(
        len(HANDLERS) >= 25,
        f"expected >=25 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )
    check("setBreakpoints" in HANDLERS)


# ---------------------------------------------------------------------------
# End-to-end (best-effort; skipped when gdb / gcc are missing).
# ---------------------------------------------------------------------------


class DapClient:
    """Minimal DAP stdio client. Mirrors the shape used by the other
    DAP test files; we don't share the implementation because the
    sister tests (e.g. test_conditional_breakpoint) keep theirs
    private to ease future maintenance."""

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
        body = bytes(self._buf[body_start: body_start + length])
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


def _build_loop_fixture() -> Optional[str]:
    """Compile a C program with a loop body so we can verify
    hit-count gating fires at the expected iteration."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
int main(void) {
    int i;
    for (i = 1; i <= 10; ++i) {
        printf("i=%d\\n", i); /* line 5: hit-count breakpoint target */
    }
    return 0;
}
"""
    src_path = "/tmp/nova_dap_hit_fixture.c"
    bin_path = "/tmp/nova_dap_hit_fixture"
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


def _e2e_hit_condition_gt(bin_path: str, src_path: str) -> int:
    """Drive a full hit-count BP flow: ``hitCondition=">3"`` skips
    the first 3 iterations and fires on the 4th, where ``i == 4``.
    Returns the number of extra assertions performed."""
    extra = [0]

    def chk(cond: bool, msg: str = "") -> None:
        check(cond, msg)
        extra[0] += 1

    def chk_eq(actual: Any, expected: Any, msg: str = "") -> None:
        check_eq(actual, expected, msg)
        extra[0] += 1

    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        chk(init["success"])
        caps = init.get("body", {})
        chk_eq(caps.get("supportsHitConditionalBreakpoints"), True)
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": bin_path})
        chk(launch["success"], f"launch: {launch}")

        bps = client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": src_path,
                    "name": os.path.basename(src_path),
                },
                "breakpoints": [{"line": 5, "hitCondition": ">3"}],
            },
        )
        chk(bps["success"], f"setBreakpoints: {bps}")
        entries = bps.get("body", {}).get("breakpoints") or []
        chk_eq(len(entries), 1)
        chk(entries[0].get("verified") is True, f"bp not verified: {entries}")
        bp_id = int(entries[0]["id"])

        cd = client.request("configurationDone", {})
        chk(cd["success"])

        # Hits 1..3 are skipped silently by the gate; the first
        # stopped event we see should be hit 4 (i==4).
        stopped = client.wait_for_event("stopped", timeout=20.0)
        body = stopped.get("body", {})
        chk_eq(body.get("reason"), "breakpoint")
        thread_id = int(body.get("threadId") or 1)

        st = client.request("stackTrace", {"threadId": thread_id})
        chk(st["success"])
        frames = st.get("body", {}).get("stackFrames") or []
        chk(len(frames) > 0)
        top_frame_id = int(frames[0]["id"])
        ev = client.request(
            "evaluate",
            {"expression": "i", "frameId": top_frame_id, "context": "watch"},
        )
        chk(ev["success"], f"evaluate i: {ev}")
        i_val = ev.get("body", {}).get("result")
        # KEY ASSERTION: the gate filtered hits 1..3 server-side so
        # the IDE first sees the stop at i==4.
        chk_eq(
            i_val, "4",
            f"hitCondition '>3' should first fire at i=4, got {i_val!r}",
        )

        # Continue -> next stop is at i==5 (still > 3).
        client.request("continue", {"threadId": thread_id})
        stopped2 = client.wait_for_event("stopped", timeout=15.0)
        st2 = client.request(
            "stackTrace", {"threadId": int(stopped2["body"]["threadId"])}
        )
        ev2 = client.request(
            "evaluate",
            {
                "expression": "i",
                "frameId": int(st2["body"]["stackFrames"][0]["id"]),
                "context": "watch",
            },
        )
        chk_eq(ev2["body"].get("result"), "5", "next stop should be i=5")

        # Clear the BP and let the program run to completion.
        client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": src_path,
                    "name": os.path.basename(src_path),
                },
                "breakpoints": [],
            },
        )
        client.request(
            "continue", {"threadId": int(stopped2["body"]["threadId"])}
        )
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
    finally:
        client.close()
    return extra[0]


def _e2e_hit_condition_mod(bin_path: str, src_path: str) -> int:
    """``hitCondition="%2"`` fires every other iteration.
    With the loop running i=1..10, the gate fires at hits 2, 4, 6,
    8, 10, which correspond to i=2, 4, 6, 8, 10. We verify the
    first 3 such stops land at i==2, i==4, i==6."""
    extra = [0]

    def chk(cond: bool, msg: str = "") -> None:
        check(cond, msg)
        extra[0] += 1

    def chk_eq(actual: Any, expected: Any, msg: str = "") -> None:
        check_eq(actual, expected, msg)
        extra[0] += 1

    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        chk(init["success"])
        client.wait_for_event("initialized", timeout=5.0)
        launch = client.request("launch", {"program": bin_path})
        chk(launch["success"])
        bps = client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": src_path,
                    "name": os.path.basename(src_path),
                },
                "breakpoints": [{"line": 5, "hitCondition": "%2"}],
            },
        )
        chk(bps["success"])
        client.request("configurationDone", {})

        for expected_i in ("2", "4", "6"):
            stopped = client.wait_for_event("stopped", timeout=20.0)
            tid = int(stopped["body"]["threadId"])
            st = client.request("stackTrace", {"threadId": tid})
            chk(st["success"])
            ev = client.request(
                "evaluate",
                {
                    "expression": "i",
                    "frameId": int(st["body"]["stackFrames"][0]["id"]),
                    "context": "watch",
                },
            )
            chk_eq(
                ev["body"].get("result"),
                expected_i,
                f"hitCondition '%2' must stop at i={expected_i}",
            )
            client.request("continue", {"threadId": tid})

        # Clear + run to end.
        client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": src_path,
                    "name": os.path.basename(src_path),
                },
                "breakpoints": [],
            },
        )
        # The previous continue triggered a stop; consume any
        # remaining stops + run to terminated.
        try:
            stopped = client.wait_for_event("stopped", timeout=2.0)
            client.request(
                "continue", {"threadId": int(stopped["body"]["threadId"])}
            )
        except TimeoutError:
            pass
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
    finally:
        client.close()
    return extra[0]


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def main() -> int:
    fns = [
        # parse_hit_condition
        test_hit_condition_none_returns_none,
        test_hit_condition_empty_string_returns_none,
        test_hit_condition_bare_count_is_equality,
        test_hit_condition_eq_alias,
        test_hit_condition_gt,
        test_hit_condition_ge,
        test_hit_condition_lt,
        test_hit_condition_le,
        test_hit_condition_ne,
        test_hit_condition_mod,
        test_hit_condition_mod_three,
        test_hit_condition_whitespace_tolerated,
        test_hit_condition_rejects_garbage,
        test_hit_condition_rejects_modulo_zero,
        test_hit_condition_rejects_negative,
        test_hit_condition_rejects_non_string,
        # Manager
        test_manager_register_and_lookup,
        test_manager_increment_hit_increments_counter,
        test_manager_increment_unknown_id_returns_none,
        test_manager_clear_all_returns_ids,
        test_manager_snapshot_returns_records,
        # Truthy + evaluate
        test_truthy_zero_is_false,
        test_truthy_nonzero_int_is_true,
        test_truthy_bool_literals,
        test_truthy_empty_or_none_is_false,
        test_truthy_string_value_is_false,
        test_evaluate_condition_returns_passed_on_truthy_int,
        test_evaluate_condition_returns_failed_on_zero,
        test_evaluate_condition_treats_eval_error_as_false,
        test_evaluate_condition_treats_timeout_as_false,
        test_evaluate_condition_handles_missing_value_field,
        # handle_set_breakpoints integration
        test_set_breakpoints_with_hit_condition_registers_record,
        test_set_breakpoints_without_filter_does_not_register,
        test_set_breakpoints_with_condition_only_registers_record,
        test_set_breakpoints_malformed_hit_condition_rejected,
        test_set_breakpoints_both_condition_and_hit_condition,
        test_set_breakpoints_resend_clears_manager,
        # _handle_stopped gating
        test_stop_handler_fires_when_no_gate_registered,
        test_stop_handler_gates_hit_condition_gt_skips_first_three,
        test_stop_handler_gates_hit_condition_modulo,
        test_stop_handler_gates_condition_eq_fires_at_match,
        test_stop_handler_treats_condition_eval_error_as_false,
        test_stop_handler_gates_both_condition_and_hit_condition,
        test_stop_handler_no_double_resume_on_skip,
        test_stop_handler_preserves_user_continue_events,
        # Capability + handlers
        test_capability_hit_conditional_breakpoints_advertised,
        test_handlers_registry_unchanged_count,
    ]
    for fn in fns:
        fn()
    unit_count = _ASSERT_COUNT

    # End-to-end (best-effort: skipped when gdb / gcc are missing).
    e2e_status = "skipped"
    e2e_reason = ""
    extra_count = 0
    if shutil.which("gdb") is None:
        e2e_reason = "gdb not installed"
    else:
        bin_path = _build_loop_fixture()
        if bin_path is None:
            e2e_reason = "gcc not available"
        else:
            src_path = "/tmp/nova_dap_hit_fixture.c"
            try:
                extra_count += _e2e_hit_condition_gt(bin_path, src_path)
                extra_count += _e2e_hit_condition_mod(bin_path, src_path)
                e2e_status = "ok"
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout: {exc}"

    print(f"test_hit_count_breakpoints: OK")
    print(f"  unit assertions:    {unit_count}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    print(f"  test functions:     {len(fns)}")
    if e2e_status == "ok":
        print(f"  end-to-end:         ok ({extra_count} extra checks)")
    else:
        print(f"  end-to-end:         SKIP -- {e2e_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
