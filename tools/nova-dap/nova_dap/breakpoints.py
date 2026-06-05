"""Source-line breakpoint bookkeeping with conditional + hit-count gating.

The DAP ``setBreakpoints`` request accepts two filter knobs per entry:

* ``condition`` -- a free-form expression evaluated whenever the
  breakpoint is hit. The IDE only stops when the expression is true.
* ``hitCondition`` -- an operator + integer count (e.g. ``">3"``,
  ``"==5"``, ``"%2"``, bare ``"7"``). The IDE only stops based on
  how many times the breakpoint has been crossed.

gdb's ``-break-insert -c "<expr>"`` already filters on the condition
side -- gdb itself evaluates the expression at every hit and only
sends a ``*stopped`` record when it's non-zero. That leaves the
server with two responsibilities:

1. **Hit-count gating.** gdb has no analog of DAP's ``hitCondition``
   shapes (it can do ``-i N`` for "ignore the first N hits", which
   is close to ``">N"`` but doesn't cover ``"%N"`` or ``"==N"``).
   So we maintain a per-breakpoint counter on the server side and,
   when gdb signals a hit, increment it then evaluate the parsed
   predicate. If the predicate says "skip", we silently
   ``-exec-continue`` instead of firing the DAP ``stopped`` event.

2. **Condition fallback.** Even though gdb's ``-c`` already gates
   the stop, we keep a server-side re-evaluation path (via
   ``-data-evaluate-expression``) so that:

   * If a breakpoint install raced the gdb expression compiler (e.g.
     the expression references a symbol not yet in scope) the
     server can still gate the hit using the recorded condition
     string.
   * Eval errors get a consistent "treat as false, log a warning"
     fallback rather than relying on whatever gdb does at
     install-time (which can vary across gdb versions).
   * The behaviour is unit-testable without a real gdb subprocess.

Module surface
--------------

* :func:`parse_hit_condition` -- accept ``">10"``, ``">=N"``,
  ``"==5"``, ``"=5"``, ``"%3"``, bare ``"7"``. Returns a callable
  that maps an integer hit count to ``True`` / ``False``.
* :class:`HitConditionError` -- raised by :func:`parse_hit_condition`
  on malformed input. The server surfaces it as a
  ``verified: false`` entry with a clear message.
* :class:`SourceBreakpointRecord` -- per-BP bookkeeping. Stores the
  condition string, the parsed hit-condition predicate (or
  ``None``), the original hit-condition string (for re-validation
  + tests), and the current hit counter.
* :class:`SourceBreakpointManager` -- thread-safe registry the
  ``Session`` holds. Reset on every ``launch`` (gdb forgets every
  breakpoint when the inferior restarts).
* :func:`evaluate_condition_via_bridge` -- wraps
  ``-data-evaluate-expression`` and decodes the result as a
  truthy boolean. Returns ``(passed, error_message)``; eval errors
  collapse to ``(False, "...")``.

No third-party deps; pure stdlib. Pure functions are
deliberately exported so the test suite can drive them without
spawning gdb.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# hitCondition parser.
# ---------------------------------------------------------------------------


class HitConditionError(ValueError):
    """Raised by :func:`parse_hit_condition` when the supplied
    ``hitCondition`` string can't be parsed into one of the supported
    shapes. The DAP server surfaces this as a ``verified: false``
    entry with a ``message`` describing the problem so the IDE can
    show the user a clear diagnostic instead of silently dropping
    the breakpoint."""


# Recognised operators in order -- longest prefix first so ``>=`` is
# preferred over ``>`` and ``==`` over ``=``.
_OP_PREFIXES: Tuple[Tuple[str, str], ...] = (
    (">=", "ge"),
    ("<=", "le"),
    ("==", "eq"),
    ("!=", "ne"),
    (">", "gt"),
    ("<", "lt"),
    ("=", "eq"),    # DAP uses both "=" and "==" for equality
    ("%", "mod"),   # every N-th hit
)


HitPredicate = Callable[[int], bool]


def parse_hit_condition(raw: Optional[str]) -> Optional[HitPredicate]:
    """Parse a DAP ``hitCondition`` string into a predicate callable.

    Returns ``None`` when ``raw`` is empty / ``None`` / whitespace-only
    (no hit-count gating requested). Raises :class:`HitConditionError`
    when the string has content but doesn't match any of the supported
    shapes:

    =================== ============================================
    Form                Predicate
    =================== ============================================
    ``"5"`` (bare int)  ``hits == 5``
    ``"=5"``            ``hits == 5`` (DAP alias for ``==``)
    ``"==5"``           ``hits == 5``
    ``"!=5"``           ``hits != 5``
    ``">5"``            ``hits > 5`` (skip first 5, fire 6th+)
    ``">=5"``           ``hits >= 5``
    ``"<5"``            ``hits < 5``
    ``"<=5"``           ``hits <= 5``
    ``"%5"``            ``hits % 5 == 0`` (every 5th hit)
    =================== ============================================

    Leading / trailing whitespace inside the expression is tolerated
    (``"  > 5  "`` -> ``hits > 5``). Negative counts and zero modulus
    are rejected at parse time. The integer N must fit in a signed
    Python int (no width limit beyond Python's arbitrary-precision)."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HitConditionError(
            f"hitCondition must be a string, got {type(raw).__name__}"
        )
    text = raw.strip()
    if not text:
        return None
    op, count = _split_op_and_count(text)
    if count < 0:
        raise HitConditionError(
            f"hitCondition count must be non-negative, got {count}"
        )
    if op == "mod" and count == 0:
        raise HitConditionError("hitCondition '%0' is invalid (division by zero)")
    return _build_predicate(op, count)


def _split_op_and_count(text: str) -> Tuple[str, int]:
    """Split a stripped hitCondition string into ``(op_tag, count)``.

    A bare integer (no operator prefix) is treated as equality --
    ``"5"`` means "fire on the 5th hit", matching VS Code's behaviour
    when the user types just a number in the breakpoint UI."""
    for prefix, op_tag in _OP_PREFIXES:
        if text.startswith(prefix):
            tail = text[len(prefix):].strip()
            count = _parse_count(tail, prefix)
            return (op_tag, count)
    # No operator prefix -- DAP says treat as ``==``.
    count = _parse_count(text, "<bare>")
    return ("eq", count)


def _parse_count(tail: str, prefix: str) -> int:
    if not tail:
        raise HitConditionError(
            f"hitCondition operator {prefix!r} missing a count"
        )
    try:
        return int(tail, 10)
    except ValueError as exc:
        raise HitConditionError(
            f"hitCondition count is not an integer: {tail!r}"
        ) from exc


def _build_predicate(op: str, count: int) -> HitPredicate:
    if op == "gt":
        return lambda hits, _c=count: hits > _c
    if op == "ge":
        return lambda hits, _c=count: hits >= _c
    if op == "lt":
        return lambda hits, _c=count: hits < _c
    if op == "le":
        return lambda hits, _c=count: hits <= _c
    if op == "eq":
        return lambda hits, _c=count: hits == _c
    if op == "ne":
        return lambda hits, _c=count: hits != _c
    if op == "mod":
        return lambda hits, _c=count: hits > 0 and (hits % _c) == 0
    # Unreachable -- _split_op_and_count only returns the tags above.
    raise HitConditionError(f"unknown operator tag: {op!r}")


# ---------------------------------------------------------------------------
# Per-breakpoint record + manager.
# ---------------------------------------------------------------------------


@dataclass
class SourceBreakpointRecord:
    """Mutable bookkeeping for one active source-line breakpoint.

    Created by ``handle_set_breakpoints`` whenever the BP has either a
    ``condition`` or a ``hitCondition`` (unconditional BPs don't need a
    record -- gdb stops, the server fires ``stopped``, done). The record
    lives in :class:`SourceBreakpointManager` until the next
    ``setBreakpoints`` re-send or a ``launch`` restart clears it.

    Fields:

    * ``gdb_id`` -- the integer breakpoint number gdb assigned. The
      manager keys records by this so a ``*stopped,bkptno=...`` can be
      routed back to the originating DAP entry.
    * ``source_path`` / ``line`` -- the originating DAP location.
      Used for log messages + diagnostics; not for routing.
    * ``condition`` -- the original DAP ``condition`` string (None when
      unconditional). Stored verbatim so the server can re-evaluate it
      on hit if needed (e.g. for the eval-error fallback path) and so
      tests can introspect what was installed.
    * ``hit_condition`` -- the original DAP ``hitCondition`` string
      (None when the BP has no hit-count gate). Kept for diagnostics
      / round-trip; the parsed predicate is stored separately.
    * ``hit_predicate`` -- callable produced by
      :func:`parse_hit_condition`. ``None`` when no hit-count gate is
      installed; otherwise a ``hits -> bool`` predicate the stop
      handler invokes to decide stop vs. silent-continue.
    * ``hit_count`` -- number of times this BP has been hit (and
      condition-passed) since it was installed. Incremented at every
      ``*stopped`` for this gdb_id before the predicate is evaluated.
      Per DAP convention: hits are counted starting from 1 (so a
      ``hitCondition: "==1"`` fires on the first hit)."""

    gdb_id: int
    source_path: str = ""
    line: int = 0
    condition: Optional[str] = None
    hit_condition: Optional[str] = None
    hit_predicate: Optional[HitPredicate] = None
    hit_count: int = 0


@dataclass
class SourceBreakpointManager:
    """Thread-safe registry of active source-line breakpoints.

    Like :class:`function_breakpoints.FunctionBreakpointManager`, owned
    by a DAP ``Session`` and reset on every ``launch`` (gdb forgets
    every breakpoint when the inferior restarts, so stale gdb ids
    would route the wrong stop).

    Only BPs with at least one of ``condition`` / ``hitCondition`` need
    a record -- the manager's API tolerates lookups for unregistered
    ids (``lookup_by_gdb_id`` returns ``None``)."""

    by_gdb_id: Dict[int, SourceBreakpointRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, record: SourceBreakpointRecord) -> None:
        with self._lock:
            self.by_gdb_id[record.gdb_id] = record

    def lookup_by_gdb_id(
        self, gdb_id: int
    ) -> Optional[SourceBreakpointRecord]:
        with self._lock:
            return self.by_gdb_id.get(gdb_id)

    def increment_hit(self, gdb_id: int) -> Optional[int]:
        """Bump the hit counter for ``gdb_id``. Returns the new count
        or ``None`` if no record is registered for that id."""
        with self._lock:
            record = self.by_gdb_id.get(gdb_id)
            if record is None:
                return None
            record.hit_count += 1
            return record.hit_count

    def clear_all(self) -> List[int]:
        """Forget every registered breakpoint and return the gdb ids
        that were active. The caller is responsible for telling gdb to
        delete them -- the manager just tracks bookkeeping, not the
        wire commands.

        Returning the id list lets the server delete by-id (preserving
        watchpoints / function breakpoints / instruction breakpoints
        in other managers) instead of calling ``-break-delete`` with
        no args (which nukes everything)."""
        with self._lock:
            ids = list(self.by_gdb_id.keys())
            self.by_gdb_id.clear()
            return ids

    def snapshot(self) -> List[SourceBreakpointRecord]:
        with self._lock:
            return list(self.by_gdb_id.values())

    def is_empty(self) -> bool:
        with self._lock:
            return not self.by_gdb_id


# ---------------------------------------------------------------------------
# Condition evaluation against a gdb bridge.
# ---------------------------------------------------------------------------


# gdb's ``-data-evaluate-expression`` returns ``^done,value="<text>"``;
# we treat the value as truthy when it's a non-zero integer (the
# common case for C / NOVA boolean expressions). Strings / pointers
# come out as quoted text and are NOT considered truthy because gdb's
# ``-c`` install would already have rejected them -- we mirror that
# semantic.
_INT_VALUE_RE = re.compile(r"^-?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*|0)$")


def is_condition_truthy(raw_value: Optional[str]) -> bool:
    """Decode a gdb-MI ``value`` string from
    ``-data-evaluate-expression`` and return ``True`` if it represents
    a true condition.

    gdb prints ``"1"`` for true, ``"0"`` for false in the common case
    (C boolean expressions). C++ bools come out as the literals
    ``"true"`` / ``"false"``. Anything else (strings, pointers,
    complex structs) we conservatively treat as false -- gdb's own
    ``-c`` filter would also reject those at install time."""
    if raw_value is None:
        return False
    text = raw_value.strip()
    if not text:
        return False
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT_VALUE_RE.match(text):
        try:
            return int(text, 0) != 0
        except ValueError:
            return False
    # Non-integer, non-bool result -- treat as false. The DAP wire
    # contract is "only stop when the condition is true", and a
    # printable string / pointer doesn't have a clear truthiness
    # meaning at the gdb evaluator level.
    return False


@dataclass
class ConditionGateResult:
    """Outcome of a server-side condition re-eval.

    * ``passed`` -- True if the condition evaluated to a truthy value.
    * ``message`` -- when ``passed`` is False and an eval error
      happened (vs. a clean false result), carries gdb's error text
      for the warning log."""

    passed: bool
    message: str = ""


def evaluate_condition_via_bridge(
    bridge: Any,
    expression: str,
    thread_id: Optional[int] = None,
    frame_level: Optional[int] = None,
    timeout: float = 2.0,
) -> ConditionGateResult:
    """Evaluate a condition expression against a live gdb bridge.

    The bridge is duck-typed: any object with a ``.command(cmd,
    timeout=...)`` method returning a ``GdbResult``-shaped value
    (``.ok``, ``.fields``, ``.error_message``) works. Tests pass a
    fake bridge so this function is exercised without spawning gdb.

    On any failure (gdb timeout, parse error, malformed expression,
    symbol-out-of-scope) the result is ``passed=False`` with the
    failure message -- the calling stop handler treats that as
    "condition false, skip silently, log a warning". This matches
    the contract spelled out in the deliverables."""
    from nova_dap.evaluator import build_evaluate_command  # noqa: WPS433

    cmd = build_evaluate_command(
        expression, thread_id=thread_id, frame_level=frame_level
    )
    try:
        result = bridge.command(cmd, timeout=timeout)
    except TimeoutError as exc:
        return ConditionGateResult(
            passed=False, message=f"gdb timed out evaluating condition: {exc}"
        )
    except RuntimeError as exc:
        return ConditionGateResult(
            passed=False, message=f"gdb error evaluating condition: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - defensive bridge-failure path
        return ConditionGateResult(
            passed=False,
            message=f"condition evaluation raised {type(exc).__name__}: {exc}",
        )
    if not result.ok:
        return ConditionGateResult(
            passed=False,
            message=result.error_message or "condition evaluation failed",
        )
    raw_value = result.fields.get("value")
    if not isinstance(raw_value, str):
        return ConditionGateResult(
            passed=False,
            message=f"gdb returned no value field for condition {expression!r}",
        )
    passed = is_condition_truthy(raw_value)
    return ConditionGateResult(passed=passed, message="" if passed else "")
