"""Function breakpoints for the NOVA DAP server.

A "function breakpoint" in the DAP spec is a breakpoint that fires
on entry to any function whose symbol name matches a user-supplied
string. Unlike source-line breakpoints (``setBreakpoints``) the
client doesn't have to know which file or line a function lives in:
gdb resolves the name to one (or more) addresses via the binary's
symbol table.

Wire flow
---------

1. Client calls ``setFunctionBreakpoints({breakpoints: [{name:
   "foo", condition?: "x > 5"}, ...]})``. Each entry has a free-form
   ``name`` string plus optional ``condition``. Per DAP, the call has
   "complete-replacement" semantics: the new list REPLACES the
   previous one — the server must tear down whichever function
   breakpoints it had installed and reinstall from scratch.

2. The server, for each entry, issues a gdb MI ``-break-insert
   [-c "<cond>"] <name>`` and records the gdb-assigned breakpoint
   number. If gdb can't resolve the symbol (e.g. ``Function "foo"
   not defined.``) the server returns ``{verified: false, message:
   "<gdb msg>"}`` for that entry but keeps installing the rest —
   the DAP spec explicitly allows unverified breakpoints so the IDE
   can show the user a "pending" indicator.

3. When the inferior enters one of these functions, gdb emits the
   usual ``*stopped,reason="breakpoint-hit",bkptno=...`` async
   record. The server's stop handler looks the ``bkptno`` up in the
   function-breakpoint manager; if it matches a registered fn-bp,
   the DAP ``stopped`` event's ``description`` is set to ``"Entry
   to <fn_name>"`` so the IDE call-stack panel can render which
   function the break landed in.

Module surface
--------------

* :func:`build_function_breakpoint_command` — composes the MI string.
* :func:`parse_function_breakpoint_response` — extracts gdb id +
  resolved-vs-pending status from a ``-break-insert`` reply.
* :func:`is_unresolved_function_error` — classifies the
  ``Function "x" not defined.`` family of gdb errors so the server
  can surface them as ``verified: false`` instead of failing the
  whole request.
* :class:`FunctionBreakpointRecord` / :class:`FunctionBreakpointManager`
  — bookkeeping the ``Session`` holds.

No third-party deps; pure stdlib.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# MI command builder.
# ---------------------------------------------------------------------------


def _quote_mi_string(value: str) -> str:
    """Quote ``value`` as an MI C-string literal.

    MI grammar (see GDB/MI Output Syntax) accepts ``"<chars>"`` with
    the usual backslash escapes for ``\\`` and ``"``. This is the
    same encoding ``nova_dap.gdb_bridge.quote_path`` uses; we
    duplicate it here so the function-breakpoint module has no hard
    dependency on the bridge module (keeps unit tests fast)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_function_breakpoint_command(
    name: str,
    condition: Optional[str] = None,
) -> Optional[str]:
    """Compose a ``-break-insert`` MI command for a function breakpoint.

    ``name`` is the function symbol to break on (passed verbatim to
    gdb — gdb's own symbol lookup decides whether the name resolves
    to a single address, multiple overloads, or a pending location).
    ``condition`` is an optional gdb expression; when set, gdb only
    stops if the expression evaluates to non-zero at the entry point.

    Returns ``None`` for an empty / non-string ``name`` so the
    caller can reject the request with a clear error."""
    if not isinstance(name, str) or not name.strip():
        return None
    parts: List[str] = ["-break-insert"]
    # gdb -- ``--function <name>`` is the explicit-location flag for
    # symbol resolution, but plain ``-break-insert <name>`` works
    # identically for the function-name case and matches the existing
    # source-line code path. We use the plain form to keep the MI
    # command body small (and to match what
    # ``test_data_breakpoints.py`` does for source-line bps).
    if isinstance(condition, str) and condition.strip():
        parts.append("-c")
        parts.append(_quote_mi_string(condition))
    parts.append(_quote_mi_string(name))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Response classification.
# ---------------------------------------------------------------------------


def parse_function_breakpoint_response(
    result_fields: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract ``{id, verified, line?}`` from a ``-break-insert`` reply.

    gdb returns ``^done,bkpt={number,addr,func,...}`` for a
    successful install. We parse:

    * ``number`` -> integer breakpoint id (the value we'll cite in
      ``-break-delete`` + ``hitBreakpointIds`` on stop).
    * ``addr``   -> resolved address. ``"<PENDING>"`` means gdb
      couldn't resolve the symbol yet (will fire when a matching
      shared library loads); we surface that as ``verified=false``
      so the IDE shows a "pending" indicator. ``"<MULTIPLE>"`` means
      gdb resolved to multiple addresses (e.g. C++ overloads) — we
      still report ``verified=true`` because the breakpoint IS
      armed.
    * ``func``, ``file``, ``fullname``, ``line`` -> optional
      metadata; we surface ``line`` so the DAP client can render a
      gutter indicator at the function's entry line.

    Returns ``None`` if the reply doesn't carry a recognisable
    ``bkpt`` tuple — the caller treats that as a failure."""
    bk = result_fields.get("bkpt")
    if not isinstance(bk, dict):
        return None
    num_raw = bk.get("number")
    bp_id: Optional[int] = None
    if isinstance(num_raw, str) and num_raw.isdigit():
        bp_id = int(num_raw)
    elif isinstance(num_raw, int):
        bp_id = num_raw
    if bp_id is None:
        return None
    addr = bk.get("addr")
    # ``<PENDING>`` -> unresolved (e.g. dlopen'd symbol). Everything
    # else (including ``<MULTIPLE>``) counts as verified — the
    # breakpoint is armed and will fire when the matching address
    # is executed.
    verified = bool(addr) and addr != "<PENDING>"
    out: Dict[str, Any] = {"id": bp_id, "verified": verified}
    func_name = bk.get("func")
    if isinstance(func_name, str) and func_name:
        out["function"] = func_name
    line_raw = bk.get("line")
    if isinstance(line_raw, str) and line_raw.isdigit():
        out["line"] = int(line_raw)
    elif isinstance(line_raw, int):
        out["line"] = line_raw
    fullname = bk.get("fullname") or bk.get("file")
    if isinstance(fullname, str) and fullname:
        out["source_path"] = fullname
    return out


# gdb error messages that mean "the symbol you asked for doesn't exist".
# We treat these as soft failures: report ``verified=false`` for the
# entry but keep processing the rest of the list. The DAP spec
# explicitly allows unverified breakpoints so the IDE can show a
# pending indicator.
_UNRESOLVED_FRAGMENTS = (
    "not defined",          # "Function "foo" not defined."
    "no symbol",            # "No symbol "foo" in current context."
    "no source file",       # "No source file named foo."
    "no line",              # "No line ... in the current file."
)


def is_unresolved_function_error(message: Optional[str]) -> bool:
    """Return True if the gdb error message indicates the function
    name simply doesn't resolve (vs. a structural error like a bad
    condition expression). Used so the server can surface unresolved
    fn-bps as ``verified: false`` without aborting the whole
    setFunctionBreakpoints request."""
    if not isinstance(message, str) or not message:
        return False
    low = message.lower()
    return any(frag in low for frag in _UNRESOLVED_FRAGMENTS)


# ---------------------------------------------------------------------------
# Manager bookkeeping.
# ---------------------------------------------------------------------------


@dataclass
class FunctionBreakpointRecord:
    """Mutable bookkeeping for one active function breakpoint.

    ``gdb_id`` is the integer breakpoint number gdb assigned (from
    the ``^done,bkpt={number=...}`` reply). ``name`` is the
    user-supplied function-name string — we keep it so the stop
    handler can build a ``"Entry to <name>"`` description. ``condition``
    is the optional condition expression we forwarded to gdb (None
    when unconditional). ``verified`` mirrors what the server
    returned to the DAP client so a follow-up status query doesn't
    have to re-derive it."""

    gdb_id: int
    name: str
    condition: Optional[str] = None
    verified: bool = True
    line: Optional[int] = None
    source_path: Optional[str] = None


@dataclass
class FunctionBreakpointManager:
    """Thread-safe registry of active function breakpoints.

    Owned by a DAP ``Session``; survives across multiple
    ``setFunctionBreakpoints`` requests within one session. Cleared
    on every ``launch`` (gdb forgets every breakpoint when the
    inferior restarts)."""

    by_gdb_id: Dict[int, FunctionBreakpointRecord] = field(default_factory=dict)
    by_name: Dict[str, List[FunctionBreakpointRecord]] = field(
        default_factory=dict
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, record: FunctionBreakpointRecord) -> None:
        with self._lock:
            self.by_gdb_id[record.gdb_id] = record
            self.by_name.setdefault(record.name, []).append(record)

    def lookup_by_gdb_id(
        self, gdb_id: int
    ) -> Optional[FunctionBreakpointRecord]:
        with self._lock:
            return self.by_gdb_id.get(gdb_id)

    def lookup_by_name(self, name: str) -> List[FunctionBreakpointRecord]:
        """Return every record registered under ``name`` (multiple
        installs of the same name pile up in order — useful for
        diagnostics, but the active install is always the last one
        because we tear down + re-install on every
        ``setFunctionBreakpoints`` request)."""
        with self._lock:
            return list(self.by_name.get(name, ()))

    def clear_all(self) -> List[int]:
        """Forget every registered function breakpoint and return the
        gdb breakpoint ids that were active. The caller is expected
        to issue ``-break-delete <id>`` on each so gdb's view stays
        in sync with the manager's. Used both on re-send
        (``setFunctionBreakpoints`` replaces the previous list) and
        on session shutdown."""
        with self._lock:
            ids = list(self.by_gdb_id.keys())
            self.by_gdb_id.clear()
            self.by_name.clear()
            return ids

    def snapshot(self) -> List[FunctionBreakpointRecord]:
        with self._lock:
            return list(self.by_gdb_id.values())

    def is_empty(self) -> bool:
        with self._lock:
            return not self.by_gdb_id


# ---------------------------------------------------------------------------
# Stop-event description builder.
# ---------------------------------------------------------------------------


def describe_function_entry(name: str) -> str:
    """Build the DAP ``stopped`` event description for a function
    breakpoint hit. Format: ``"Entry to <name>"`` — matches the
    convention used by the VS Code Node debug adapter so the
    call-stack panel renders identically."""
    return f"Entry to {name}"
