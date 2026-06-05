"""Data breakpoints (watchpoints) for the NOVA DAP server.

A "data breakpoint" in the DAP spec is a request to stop the program
whenever a particular variable's value changes. gdb already supports
this via the ``-break-watch`` MI command (write), ``-break-watch -r``
(read), and ``-break-watch -a`` (read+write a.k.a. "access").

This module exposes a small, testable wrapper around gdb's MI watch
commands plus the dataId encoding that lets the DAP wire protocol
round-trip a stable identifier between
``dataBreakpointInfo`` and ``setDataBreakpoints``.

DAP wire flow
-------------

1. Client calls ``dataBreakpointInfo({variablesReference, name})``.
   The server returns ``{dataId, description, accessTypes,
   canPersist: false}``. ``dataId`` is opaque to the client — we use
   it as the key the next request will refer to. ``canPersist`` is
   false because watchpoints don't survive a re-launch of the
   inferior; gdb assigns fresh watchpoint numbers each session.

2. Client calls ``setDataBreakpoints({breakpoints: [{dataId,
   accessType}]})``. The server decodes each ``dataId``, issues a
   gdb ``-break-watch`` (with ``-r`` / ``-a`` flags for the access
   type), records the resulting watchpoint number, and returns a
   ``{verified, id, message}`` entry per requested breakpoint.

3. When the watched value changes, gdb emits an async
   ``*stopped,reason="watchpoint-trigger",...`` record (or
   ``read-watchpoint-trigger`` / ``access-watchpoint-trigger`` for
   the read / rw variants). The server's stop handler translates the
   reason to DAP ``"data breakpoint"`` and includes
   ``hitBreakpointIds`` keyed off the watchpoint id we registered.

dataId encoding
---------------

``dataId`` is a base64-encoded JSON object ``{n: name, f: frame_id,
v: varRef}``. The encoder is deliberately reversible so we can
recover the original variable name from a client-supplied id without
keeping per-session state — VS Code sometimes re-issues an old
``setDataBreakpoints`` request after a launch reset, and the dataId
needs to be parseable in isolation.
"""
from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# dataId encoding.
# ---------------------------------------------------------------------------


def encode_data_id(
    name: str,
    frame_id: Optional[int] = None,
    variables_reference: Optional[int] = None,
) -> str:
    """Encode ``(name, frame_id, variables_reference)`` as a stable
    DAP ``dataId`` string.

    The encoded form is ``base64url(json({n, f, v}))`` so it's safe to
    embed in JSON payloads and round-trips through
    ``decode_data_id`` without loss. Both ``frame_id`` and
    ``variables_reference`` are optional — if absent we just omit the
    key, which keeps the encoded string compact for the common case
    of a global / top-frame variable."""
    payload: Dict[str, Any] = {"n": name}
    if frame_id is not None:
        payload["f"] = int(frame_id)
    if variables_reference is not None:
        payload["v"] = int(variables_reference)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_data_id(data_id: str) -> Optional[Dict[str, Any]]:
    """Decode a dataId back to ``{n: name, f?: frame_id, v?: varRef}``.

    Returns ``None`` if the string isn't a valid encoded id. The
    caller should treat a ``None`` return as a client-side bug — the
    DAP spec says the server may reject unknown ids."""
    if not isinstance(data_id, str) or not data_id:
        return None
    try:
        # Re-pad to a multiple of 4 for ``base64.urlsafe_b64decode``,
        # which is strict about padding.
        padding = "=" * (-len(data_id) % 4)
        raw = base64.urlsafe_b64decode(data_id + padding)
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("n")
    if not isinstance(name, str) or not name:
        return None
    out: Dict[str, Any] = {"n": name}
    if "f" in obj:
        try:
            out["f"] = int(obj["f"])
        except (TypeError, ValueError):
            pass
    if "v" in obj:
        try:
            out["v"] = int(obj["v"])
        except (TypeError, ValueError):
            pass
    return out


# ---------------------------------------------------------------------------
# Access-type mapping.
# ---------------------------------------------------------------------------


# DAP access type -> gdb -break-watch flag suffix.
# - "write"     -> ``-break-watch <expr>``        (default: write watch)
# - "read"      -> ``-break-watch -r <expr>``     (read watch)
# - "readWrite" -> ``-break-watch -a <expr>``     (access watch)
#
# Note: hardware read-only watchpoints are x86-architecture-specific
# and not universally supported, so we conservatively only advertise
# "write" + "readWrite" from ``dataBreakpointInfo``. We still accept
# "read" in ``setDataBreakpoints`` and let gdb decide whether it can
# install one.
_ACCESS_FLAG = {
    "write": "",
    "read": "-r",
    "readWrite": "-a",
}


def access_type_flag(access_type: Optional[str]) -> Optional[str]:
    """Return the ``-break-watch`` flag for a DAP ``accessType``.

    Returns the empty string for ``"write"`` (the default), ``"-r"``
    for ``"read"``, ``"-a"`` for ``"readWrite"``. Returns ``None``
    for unknown access types so the caller can reject the request."""
    if access_type is None:
        return ""
    if not isinstance(access_type, str):
        return None
    return _ACCESS_FLAG.get(access_type)


def default_access_types() -> List[str]:
    """Access types we advertise via ``dataBreakpointInfo``.

    gdb hardware watchpoints reliably support write + access (read+write).
    Pure read watchpoints depend on x86 debug-register semantics that
    aren't universal across gdb's targets, so we don't advertise them
    by default; the wire still accepts ``"read"`` if a client insists,
    and gdb's response surfaces success / failure to the user."""
    return ["write", "readWrite"]


# ---------------------------------------------------------------------------
# Watchpoint manager.
# ---------------------------------------------------------------------------


@dataclass
class WatchpointRecord:
    """Mutable bookkeeping for one active data breakpoint.

    ``gdb_id`` is the integer watchpoint number gdb assigned (parsed
    from the ``^done,wpt={number=...}`` reply). It's what
    ``-break-delete <gdb_id>`` removes, and it's what gdb cites in
    ``*stopped,bkptno=...`` records when the watch fires. ``data_id``
    is the DAP-facing opaque id the client sent in
    ``setDataBreakpoints`` — we keep both so the stop handler can
    map gdb's watchpoint number back to a DAP id for the
    ``hitBreakpointIds`` field."""

    gdb_id: int
    data_id: str
    name: str
    access_type: str  # "write" / "read" / "readWrite"
    description: str = ""


@dataclass
class WatchpointManager:
    """Thread-safe registry of active watchpoints.

    The manager is owned by a DAP ``Session`` and survives across
    multiple ``setDataBreakpoints`` requests within one session. It's
    NOT preserved across ``launch`` — a new launch resets gdb, which
    clears every watchpoint, so we ``clear_all()`` on each
    re-launch."""

    by_gdb_id: Dict[int, WatchpointRecord] = field(default_factory=dict)
    by_data_id: Dict[str, WatchpointRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, record: WatchpointRecord) -> None:
        with self._lock:
            self.by_gdb_id[record.gdb_id] = record
            self.by_data_id[record.data_id] = record

    def remove_by_gdb_id(self, gdb_id: int) -> Optional[WatchpointRecord]:
        with self._lock:
            rec = self.by_gdb_id.pop(gdb_id, None)
            if rec is not None:
                self.by_data_id.pop(rec.data_id, None)
            return rec

    def lookup_by_gdb_id(self, gdb_id: int) -> Optional[WatchpointRecord]:
        with self._lock:
            return self.by_gdb_id.get(gdb_id)

    def lookup_by_data_id(self, data_id: str) -> Optional[WatchpointRecord]:
        with self._lock:
            return self.by_data_id.get(data_id)

    def clear_all(self) -> List[int]:
        """Forget every registered watchpoint. Returns the gdb watchpoint
        ids that were active so the caller can issue ``-break-delete``
        on each. Used at session shutdown + on re-launch."""
        with self._lock:
            ids = list(self.by_gdb_id.keys())
            self.by_gdb_id.clear()
            self.by_data_id.clear()
            return ids

    def snapshot(self) -> List[WatchpointRecord]:
        with self._lock:
            return list(self.by_gdb_id.values())


# ---------------------------------------------------------------------------
# gdb MI command builders.
# ---------------------------------------------------------------------------


def build_watch_command(
    expression: str,
    access_type: Optional[str] = "write",
) -> Optional[str]:
    """Compose a ``-break-watch`` MI command for ``expression``.

    ``expression`` is the variable name (or arbitrary expression) gdb
    should watch; the caller is responsible for selecting the right
    frame first. ``access_type`` selects the watchpoint flavour;
    unknown access types return ``None`` so the caller can reject
    the request with a clear error."""
    flag = access_type_flag(access_type)
    if flag is None:
        return None
    # Use a plain unquoted expression so gdb parses it as a normal
    # C/NOVA identifier. Quoting via cstring would force gdb's
    # symbol lookup to use the literal string verbatim, which works
    # for simple names but breaks for compound expressions like
    # ``counter[2]``.
    parts = ["-break-watch"]
    if flag:
        parts.append(flag)
    parts.append(expression)
    return " ".join(parts)


def parse_watchpoint_id(result_fields: Dict[str, Any]) -> Optional[int]:
    """Extract gdb's watchpoint number from a ``-break-watch`` reply.

    gdb returns one of three keys depending on the watchpoint flavour:
    ``wpt`` (plain write), ``hw-rwpt`` (read), or ``hw-awpt`` (access).
    All carry a ``number`` field with the integer id. We probe each
    in turn so the caller doesn't have to remember the variant."""
    for key in ("wpt", "hw-rwpt", "hw-awpt"):
        entry = result_fields.get(key)
        if isinstance(entry, dict):
            num = entry.get("number")
            if isinstance(num, str) and num.isdigit():
                return int(num)
            if isinstance(num, int):
                return num
    # Fallback: gdb occasionally inlines ``bkpt={number=...}`` for
    # watchpoints on older versions.
    bk = result_fields.get("bkpt")
    if isinstance(bk, dict):
        num = bk.get("number")
        if isinstance(num, str) and num.isdigit():
            return int(num)
    return None


# ---------------------------------------------------------------------------
# Stop-record classification.
# ---------------------------------------------------------------------------


# gdb's *stopped reason strings that map to a DAP "data breakpoint" stop.
WATCHPOINT_STOP_REASONS = frozenset(
    {
        "watchpoint-trigger",
        "read-watchpoint-trigger",
        "access-watchpoint-trigger",
        # gdb sometimes uses ``watchpoint-scope`` when the watched
        # variable goes out of scope; we surface it as a data
        # breakpoint stop so the user knows the watch is gone.
        "watchpoint-scope",
    }
)


def is_watchpoint_stop(reason: Optional[str]) -> bool:
    """Return True if the gdb ``*stopped`` reason indicates a
    watchpoint event we should surface as a DAP data breakpoint."""
    if not isinstance(reason, str):
        return False
    return reason in WATCHPOINT_STOP_REASONS


def describe_watch_change(
    name: str,
    access_type: str,
    old_value: Optional[str],
    new_value: Optional[str],
) -> str:
    """Build a human-readable ``stopped`` event description.

    DAP clients render this string in the call-stack panel next to the
    stop reason. Format: ``Variable 'x' changed (write): 5 -> 10``.
    ``old_value`` / ``new_value`` may be missing (gdb only ships them
    for true write hits; read watchpoints just say "accessed")."""
    if old_value is not None and new_value is not None:
        return f"Variable {name!r} changed ({access_type}): {old_value} -> {new_value}"
    if new_value is not None:
        return f"Variable {name!r} ({access_type}): now {new_value}"
    return f"Variable {name!r} ({access_type})"


def extract_watch_values(
    fields: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    """Pull ``(old_value, new_value)`` out of a gdb ``*stopped`` record.

    For a write watchpoint hit, gdb emits a ``value={old="...",
    new="..."}`` tuple. For a read hit, ``value={value="..."}``
    (single field). We tolerate either shape and return ``None`` for
    missing slots."""
    val = fields.get("value")
    if not isinstance(val, dict):
        return (None, None)
    old = val.get("old")
    new = val.get("new")
    if old is None and new is None:
        # Read-watch shape — single ``value`` slot.
        cur = val.get("value")
        if isinstance(cur, str):
            return (None, cur)
    if isinstance(old, str) or isinstance(new, str):
        return (
            old if isinstance(old, str) else None,
            new if isinstance(new, str) else None,
        )
    return (None, None)
