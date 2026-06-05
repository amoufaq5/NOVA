"""Structured DAP ``variables`` + ``scopes`` support (R38F).

Pre-R38F the DAP server returned a single flat ``"Locals"`` scope per
frame, with every entry a leaf (``variablesReference: 0``). That made
the VS Code Variables panel show "x = 5" but reduced any NOVA list /
tuple / closure to its bare pointer string -- the user couldn't drill
into the value.

R38F upgrades the scopes + variables handlers so:

1. ``scopes`` returns one DAP scope per *category* per frame:
   ``Locals`` (current frame's stack variables minus the formal
   parameters), ``Arguments`` (the formal parameters), and ``Captures``
   (the closure environment, surfaced when the frame looks like a
   closure body — best-effort, depends on R37A's ``env_list`` lowering
   being visible to gdb's symbol table).

2. ``variables`` returns the children of a previously-allocated
   ``variablesReference``. Each child is either a leaf
   (``variablesReference: 0``) or expandable (``variablesReference:
   <fresh_id>`` pointing into the same cache).

3. Lists / tuples / strings / pointers-to-structs are *expandable*.
   Lists follow the NOVA runtime layout from ``src/runtime/list.nova``::

       [length (8B)][cap (8B)][data_ptr (8B)]
       data_ptr -> [slot_0 (8B)][slot_1 (8B)]...

   Tuples after R36C are lists with the slots in declaration order.
   Closures after R37A are 2-tuples ``[fn_ptr, env_list]``.

   For each expandable variable we cache an :class:`ExpansionDescriptor`
   so a follow-up ``variables`` request can replay the same gdb-MI
   walk against the right ``(thread_id, frame_level, expression)``
   triple.

Module surface
--------------

* :class:`VariablesReferenceCache` — per-session cache mapping the
  integer ``variablesReference`` ids on the wire back to a typed
  descriptor of what to do when the IDE expands them.
* :class:`ScopeRef` — a "top-level" scope descriptor; the IDE expands
  this to get the Locals/Arguments/Captures view for one frame.
* :class:`ExpansionRef` — a sub-tree descriptor; the IDE expands this
  to drill into a list/tuple element. Carries the gdb-MI expression
  to re-evaluate against the right thread + frame.
* :func:`classify_variable` — given a gdb-MI ``value="..."`` string +
  the variable's ``type`` field (if present), decide whether the
  variable should be advertised as expandable.
* :func:`build_string_preview` — surface a quoted preview for
  pointer-with-string values (e.g. ``0x7fff "hello"`` becomes
  ``"hello"``) so the Variables panel shows the string content
  instead of the raw pointer hex.
* :func:`parse_locals_response` / :func:`parse_arguments_response` —
  pull the ``variables`` / ``stack-args`` lists out of a gdb-MI
  result and normalise them to ``{name, value, type}`` dicts.
* :func:`looks_like_closure_frame` — best-effort heuristic that
  inspects the formal-arg list for a frame and reports whether it
  carries the R37A ``[fn_ptr, env_list]`` shape. The Captures scope
  is only emitted when this returns True so non-closure frames get
  the simpler two-scope view.

Why a separate module
---------------------

``server.py`` is already 3.7k lines covering 28 DAP capabilities.
Keeping the variable-tree machinery in its own file lets the unit
test suite (``tests/test_variables.py``) exercise classification,
list-layout parsing, and cache invariants without needing to spin
up a gdb subprocess. The classifier in particular has enough edge
cases (signed int, pointer-prefixed string, bare hex pointer,
typed-struct deref) that it earns its own module.

No third-party deps; pure stdlib + ``nova_dap.evaluator``.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# NOVA runtime list / string layout (mirror of ``src/runtime/list.nova``).
# ---------------------------------------------------------------------------

# List header byte offsets — see ``src/runtime/list.nova`` for the
# canonical declaration. We replicate them here as constants so the DAP
# server can read NOVA list / tuple values via ``-data-evaluate-expression``
# without depending on the runtime source. If the runtime layout ever
# changes these constants must move in lockstep.
LIST_OFFSET_LEN = 0
LIST_OFFSET_CAP = 8
LIST_OFFSET_DATA = 16
LIST_HEADER_SIZE = 24
# Each element slot is exactly 8 bytes (pointer-sized value).
LIST_SLOT_SIZE = 8

# Default cap on how many string bytes we surface in the preview. The
# DAP wire protocol has no upper bound but the Variables panel renders
# the ``value`` field inline; an unbounded preview can make the panel
# unreadable for multi-MB string buffers.
STRING_PREVIEW_LIMIT = 80
# Cap on the number of children we emit when expanding a list. The IDE
# can request more via DAP's ``start`` / ``count`` paging arguments but
# we bias the default towards bounded UI rendering (a million-element
# list shouldn't lock up VS Code).
DEFAULT_CHILD_LIMIT = 100


# ---------------------------------------------------------------------------
# Value classification (used to decide ``variablesReference: 0 | <id>``).
# ---------------------------------------------------------------------------


# A bare hex pointer with no string preview attached. gdb prints these
# when the inferior holds an opaque pointer to a heap-allocated NOVA
# value (list, tuple, struct). For R38F we treat any such pointer as
# *potentially* expandable; the actual expansion call may discover the
# memory at the address isn't a list and degrade gracefully.
_HEX_PTR_ONLY_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
# Pointer-with-string preview: gdb prints e.g. ``0x7fff "hello"`` for a
# ``char*`` (NOVA's string layout collapses to this in gdb's view since
# strings are null-terminated). Capturing the leading hex lets us
# treat the variable as a string leaf instead of an opaque pointer.
_HEX_PTR_WITH_STRING_RE = re.compile(
    r"^0[xX][0-9a-fA-F]+\s+\"(?P<text>.*)\"\s*$"
)
# Decimal integer literal (with optional sign) plus the octal form gdb
# occasionally emits. Hex is NOT included here -- ``0x...`` always
# means "pointer" for the Variables panel's purposes, because gdb only
# prints in hex for pointer-shaped values. NOVA integers come from the
# inferior as untagged decimals (the smart-op layer strips the tag
# before passing through ``-data-evaluate-expression``).
_INT_LITERAL_RE = re.compile(r"^-?(?:0[0-7]*|[1-9][0-9]*)$")
# Char display: ``104 'h'``.
_CHAR_LITERAL_RE = re.compile(r"^-?\d+\s+'.+'$")


def _is_null_pointer(text: str) -> bool:
    """Return True for the various ways gdb prints a null pointer.

    gdb sometimes prints ``0x0`` (preferred), other times ``(nil)`` or
    ``NULL``; we treat all of them as a non-expandable leaf so the
    Variables panel shows ``null`` and the IDE doesn't request an
    expansion that would inevitably fail."""
    t = text.strip()
    return t in ("0x0", "(nil)", "NULL", "0")


def build_string_preview(text: str, limit: int = STRING_PREVIEW_LIMIT) -> Optional[str]:
    """Extract a quoted-string preview from gdb's value text.

    Returns the surfaced display text (without surrounding quotes) when
    ``text`` matches a pointer-with-string shape; returns ``None`` when
    no string preview is available so callers can fall back to the raw
    pointer text.

    Examples::

        build_string_preview('0x7fff "hello"')         -> '"hello"'
        build_string_preview('0x7fff "very long..."')  -> '"very long..."'
        build_string_preview('0x1234')                 -> None
        build_string_preview('42')                     -> None

    The output is wrapped in double quotes so the Variables panel
    visually distinguishes a string from a numeric literal. Long
    strings are truncated at ``limit`` characters with a trailing
    ``"..."`` indicator (the actual ``"`` closer is preserved).
    """
    m = _HEX_PTR_WITH_STRING_RE.match(text.strip())
    if not m:
        return None
    inner = m.group("text")
    if len(inner) > limit:
        inner = inner[:limit] + "..."
    return f'"{inner}"'


def classify_variable(value: str, type_hint: Optional[str] = None) -> Tuple[bool, str]:
    """Classify a gdb-MI variable value as ``(expandable, type_tag)``.

    Returns a ``(bool, str)`` pair where the first element is True when
    the variable should be advertised with a non-zero
    ``variablesReference`` (so the IDE renders the expansion chevron)
    and the second is the DAP ``type`` field — one of
    ``"int" | "str" | "char" | "ptr" | "list" | "raw"``.

    The classifier is intentionally tolerant: anything we can't pin
    down falls through to ``("raw", False)`` so the panel still shows
    *something* even for exotic gdb output. The ``type_hint`` argument
    (from gdb's per-variable ``type`` field) is consulted as a tiebreaker
    when the value alone is ambiguous — e.g. ``int*`` carrying a bare
    hex pointer should expand into a one-element view, while ``int``
    holding the same hex digit shouldn't.

    The shape of the ``type_hint`` field is gdb's own pretty-printed
    type name; we treat anything containing ``*`` or ``[`` as a
    pointer / array (expandable) and everything else as a value
    (non-expandable)."""
    text = value.strip()
    if not text or _is_null_pointer(text):
        return (False, "raw")

    # Plain int -> leaf.
    if _INT_LITERAL_RE.match(text):
        return (False, "int")

    # ``104 'h'`` -> char leaf.
    if _CHAR_LITERAL_RE.match(text):
        return (False, "char")

    # ``true`` / ``false`` -> bool leaf.
    if text in ("true", "false"):
        return (False, "bool")

    # Pointer-with-string preview -> string leaf. The user sees the
    # quoted text; no expansion needed.
    if _HEX_PTR_WITH_STRING_RE.match(text):
        return (False, "str")

    # Bare hex pointer -> expandable. We don't know yet whether the
    # memory at the address is a list, tuple, or struct; the expansion
    # handler does the actual probe.
    if _HEX_PTR_ONLY_RE.match(text):
        # ``type_hint`` lets us refine the label. ``list`` if the
        # NOVA type system says so; ``ptr`` otherwise.
        if type_hint:
            lower = type_hint.lower()
            if "list" in lower or "tuple" in lower:
                return (True, "list")
        return (True, "ptr")

    # Anything else: surface verbatim. We tentatively mark struct-like
    # types as expandable when the type_hint says so.
    if type_hint and ("*" in type_hint or "[" in type_hint):
        return (True, "raw")

    return (False, "raw")


# ---------------------------------------------------------------------------
# gdb-MI response parsers.
# ---------------------------------------------------------------------------


def parse_locals_response(fields: Dict[str, Any]) -> List[Dict[str, str]]:
    """Normalise the ``variables=[...]`` list from
    ``-stack-list-variables --all-values``.

    gdb returns a list of ``{name, value, type?, arg?}`` dicts where
    ``arg="1"`` marks formal arguments. The DAP handler routes args to
    the ``Arguments`` scope, so the *locals* parse strips them. Each
    returned dict has the keys ``{name, value, type}`` with the type
    field defaulted to ``""`` when gdb didn't include it (older MI
    versions sometimes omit it)."""
    return _normalise_variable_entries(fields.get("variables"), keep_args=False)


def parse_arguments_response(fields: Dict[str, Any]) -> List[Dict[str, str]]:
    """Normalise the ``stack-args=[...]`` list from
    ``-stack-list-arguments --simple-values <frame>``.

    The MI response shape differs slightly from ``-stack-list-locals``:
    the outer wrapping is ``stack-args=[frame={level=N, args=[...]}]``.
    We unwrap one frame's args and surface them with the same
    ``{name, value, type}`` triple as ``parse_locals_response`` for
    consumer symmetry."""
    stack_args = fields.get("stack-args")
    if not isinstance(stack_args, list):
        return []
    # Pull the first frame (we always select one frame at a time).
    args_list: Optional[List[Any]] = None
    for entry in stack_args:
        if isinstance(entry, dict):
            # gdb wraps each frame as {"frame": {...}}; unwrap.
            frame = entry.get("frame")
            if isinstance(frame, dict):
                candidate = frame.get("args")
                if isinstance(candidate, list):
                    args_list = candidate
                    break
            # Older MI versions sometimes inline ``args`` at the same
            # level as ``level``; tolerate that too.
            candidate2 = entry.get("args")
            if isinstance(candidate2, list):
                args_list = candidate2
                break
    if args_list is None:
        return []
    return _normalise_variable_entries(args_list, keep_args=True)


def _normalise_variable_entries(
    entries: Any, keep_args: bool
) -> List[Dict[str, str]]:
    """Filter + rename helper shared by the locals + arguments parsers.

    ``keep_args=True`` returns every entry; ``keep_args=False`` drops
    entries with ``arg="1"`` since those are the formal parameters
    (surfaced in the Arguments scope)."""
    out: List[Dict[str, str]] = []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        # ``arg="1"`` flags formal parameters in gdb's combined locals
        # response (`-stack-list-variables --all-values` lists args +
        # locals together). The Locals scope filters them out so they
        # don't appear twice (Arguments + Locals).
        is_arg = entry.get("arg") == "1"
        if not keep_args and is_arg:
            continue
        value = entry.get("value")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        type_str = entry.get("type") or ""
        if not isinstance(type_str, str):
            type_str = str(type_str)
        out.append({"name": name, "value": value, "type": type_str})
    return out


# ---------------------------------------------------------------------------
# Closure-frame detection (best-effort, depends on R37A).
# ---------------------------------------------------------------------------


# Names the R37A lowering uses for the closure header slots. Treated
# case-insensitively; the matcher is best-effort -- if NOVA's closure
# convention later changes (e.g. env_list -> captures_arr) the test
# suite will surface the mismatch via the Captures-not-present assertion.
_CLOSURE_HEADER_NAMES = ("fn_ptr", "env_list", "__fn_ptr__", "__env_list__")


def looks_like_closure_frame(arg_entries: List[Dict[str, str]]) -> bool:
    """Return True when the frame's formal-argument list looks like the
    body of a R37A-lowered closure.

    Heuristic: the first two formals are named ``fn_ptr`` + ``env_list``
    (in declaration order). The names come from NOVA's R37A closure
    lowering convention; we tolerate a leading underscore-wrapped form
    so future renamings don't break detection. If R37A's lowering
    output isn't visible to gdb's symbol table (DWARF gap), this
    function returns False and the Captures scope is silently skipped.

    Returning False is the safe degraded mode -- the user still gets
    Locals + Arguments and can manually expand the ``env_list`` local
    via the watch window."""
    if not arg_entries or len(arg_entries) < 2:
        return False
    first = arg_entries[0].get("name", "").strip()
    second = arg_entries[1].get("name", "").strip()
    return (
        first.lower() in (n.lower() for n in _CLOSURE_HEADER_NAMES)
        and second.lower() in (n.lower() for n in _CLOSURE_HEADER_NAMES)
    )


# ---------------------------------------------------------------------------
# variablesReference cache.
# ---------------------------------------------------------------------------


# Sentinel scope kind values. Tests reach for these constants instead of
# the bare strings so a rename surfaces as a single-line update.
SCOPE_LOCALS = "locals"
SCOPE_ARGUMENTS = "arguments"
SCOPE_CAPTURES = "captures"


@dataclass
class ScopeRef:
    """A top-level scope reference (Locals / Arguments / Captures).

    The IDE issues a ``variables`` request with this ref to fetch the
    children of one scope for one frame. The handler dispatches to the
    appropriate gdb-MI command per ``kind``:

    * ``SCOPE_LOCALS`` -> ``-stack-list-variables --all-values`` (locals only)
    * ``SCOPE_ARGUMENTS`` -> ``-stack-list-arguments --simple-values <level>``
    * ``SCOPE_CAPTURES`` -> expand ``env_list`` as a list

    ``frame_id`` is the stable DAP frame id the scope was emitted for
    (see ``Session.frame_lookup_by_id`` in ``server.py``); the handler
    resolves it back to a ``(thread_id, frame_level)`` pair at expansion
    time."""

    kind: str
    frame_id: int


@dataclass
class ExpansionRef:
    """A nested-variable reference (list element, tuple slot, etc.).

    Carries enough state for ``handle_variables`` to re-run the
    gdb-MI expansion against the originating thread + frame without
    relying on gdb's current "selected" state. The ``expression`` field
    is a gdb-evaluable string -- typically a pointer arithmetic
    expression like ``*((long*)(0x7fff5fbf + 16))`` that reads one slot
    of a NOVA list.

    ``element_type`` is a hint for further nesting: when set to
    ``"list"`` the expansion handler treats the result as another NOVA
    list (so its slots get child refs too); when set to ``"slot"`` the
    handler classifies the result via :func:`classify_variable` and
    chooses leaf vs expandable based on the gdb-printed value."""

    frame_id: int
    expression: str
    # ``"list"`` or ``"slot"`` — see docstring above.
    element_kind: str = "slot"
    # Optional override for the display name (e.g. ``[0]`` for an
    # indexed list child). Falls back to the gdb-printed expression
    # when None.
    display_name: Optional[str] = None
    # Optional declared element index (for ``"[N]"`` style children).
    # Carried separately so the test suite can assert "the 3rd child is
    # element [2]" without parsing display_name.
    index: Optional[int] = None


class VariablesReferenceCache:
    """Per-session ``variablesReference`` allocator + lookup.

    DAP requires server-allocated ``variablesReference`` ids to be
    stable for the lifetime of the session: the IDE may issue a
    ``variables`` request with an id the server emitted hours ago when
    the user scrolls back to an old stack frame. We allocate fresh ids
    monotonically starting at ``base`` (default 1000 — same starting
    point the legacy ``Session.alloc_var_ref`` used so existing tests
    that hard-code ``1000`` still pass).

    Thread-safe: ``handle_scopes`` may run in parallel with a
    background ``stopped`` handler that emits scope refs for a new
    frame. The lock protects both the id counter and the descriptor
    dict."""

    def __init__(self, base: int = 1000) -> None:
        self._lock = threading.Lock()
        self._next_id = base
        # ref_id -> ScopeRef | ExpansionRef
        self._records: Dict[int, Any] = {}

    def allocate_scope(self, kind: str, frame_id: int) -> int:
        """Allocate a new scope ref for ``(kind, frame_id)``.

        Scope refs are NOT memoised by ``(kind, frame_id)`` -- DAP
        clients call ``scopes`` afresh on every stop and we don't want
        them to retain a stale ref across re-stops. The cache simply
        grows monotonically; ``clear`` resets it on relaunch."""
        with self._lock:
            ref = self._next_id
            self._next_id += 1
            self._records[ref] = ScopeRef(kind=kind, frame_id=frame_id)
            return ref

    def allocate_expansion(
        self,
        frame_id: int,
        expression: str,
        element_kind: str = "slot",
        display_name: Optional[str] = None,
        index: Optional[int] = None,
    ) -> int:
        """Allocate a fresh ref for a nested expansion."""
        with self._lock:
            ref = self._next_id
            self._next_id += 1
            self._records[ref] = ExpansionRef(
                frame_id=frame_id,
                expression=expression,
                element_kind=element_kind,
                display_name=display_name,
                index=index,
            )
            return ref

    def get(self, ref: int) -> Optional[Any]:
        """Return the cached descriptor for ``ref`` or None if unknown."""
        with self._lock:
            return self._records.get(ref)

    def is_scope(self, ref: int) -> bool:
        with self._lock:
            return isinstance(self._records.get(ref), ScopeRef)

    def is_expansion(self, ref: int) -> bool:
        with self._lock:
            return isinstance(self._records.get(ref), ExpansionRef)

    def clear(self) -> None:
        """Drop every descriptor + reset the id counter.

        Called from ``handle_launch`` so a relaunch doesn't reuse refs
        from the previous inferior (whose stack frames are long gone)."""
        with self._lock:
            self._next_id = 1000
            self._records.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._records)


# ---------------------------------------------------------------------------
# List / tuple expansion (NOVA runtime layout walker).
# ---------------------------------------------------------------------------


def build_list_length_expr(list_ptr_expr: str) -> str:
    """Build a gdb expression that reads the length of a NOVA list.

    The list layout from ``src/runtime/list.nova`` is
    ``[len(8B), cap(8B), data_ptr(8B)]`` so ``len = *((long*)(ptr + 0))``.

    ``list_ptr_expr`` is whatever expression evaluates to the list
    pointer in the current frame -- typically a local name (``my_list``)
    or a slot read (``*((long*)(0x7fff + 16))``). We wrap it in parens
    so operator precedence doesn't bite us when the caller passes a
    complex expression."""
    return f"*((long*)({list_ptr_expr}))"


def build_list_data_expr(list_ptr_expr: str) -> str:
    """Build a gdb expression that reads the ``data_ptr`` field of a
    NOVA list -- offset +16 from the header."""
    return f"*((long*)({list_ptr_expr} + {LIST_OFFSET_DATA}))"


def build_list_slot_expr(list_ptr_expr: str, index: int) -> str:
    """Build a gdb expression that reads ``list[index]`` as a long.

    Composes the two header reads above plus the per-slot offset:
    ``*((long*)(data_ptr + index * 8))``. The data_ptr lookup is
    inlined into the expression so this round-trips through gdb in a
    single ``-data-evaluate-expression``."""
    data_expr = build_list_data_expr(list_ptr_expr)
    return f"*((long*)({data_expr} + {index * LIST_SLOT_SIZE}))"


@dataclass
class ListExpansion:
    """Result of an expansion request that resolved into a list-like
    value.

    ``length`` is the value read from the NOVA list header; ``children``
    is the per-slot ``{name, value, type, variablesReference}`` records
    ready to be wrapped in the DAP response. ``truncated`` is True when
    we capped the child count at :data:`DEFAULT_CHILD_LIMIT`."""

    length: int
    children: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


def parse_pointer_text(text: str) -> Optional[int]:
    """Parse the integer address out of a gdb-printed pointer.

    Returns the int value for ``0x7fff``, ``0X7FFF``, or a raw hex
    string; returns ``None`` for the various null / non-pointer forms
    so the caller can surface a degraded result."""
    if not text:
        return None
    t = text.strip()
    if _is_null_pointer(t):
        return None
    # ``0x...`` -- the common case.
    m = re.match(r"^0[xX]([0-9a-fA-F]+)", t)
    if m:
        try:
            return int(m.group(1), 16)
        except ValueError:
            return None
    # Bare decimal address -- rare but tolerated.
    if t.lstrip("-").isdigit():
        try:
            return int(t)
        except ValueError:
            return None
    return None


def format_child_name(index: int) -> str:
    """Format a list child's display name. ``index=2`` -> ``"[2]"``."""
    return f"[{index}]"


# ---------------------------------------------------------------------------
# DAP wire-shape helpers.
# ---------------------------------------------------------------------------


def build_scope_entry(
    name: str,
    variables_reference: int,
    expensive: bool = False,
    presentation_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble one DAP ``Scope`` body.

    DAP requires ``name`` + ``variablesReference`` + ``expensive``; we
    add ``presentationHint`` when present (``"locals"`` /
    ``"arguments"`` / ``"registers"`` are the standard values; the IDE
    uses it to render a context-appropriate icon)."""
    entry: Dict[str, Any] = {
        "name": name,
        "variablesReference": variables_reference,
        "namedVariables": 0,
        "indexedVariables": 0,
        "expensive": expensive,
    }
    if presentation_hint:
        entry["presentationHint"] = presentation_hint
    return entry


def build_variable_entry(
    name: str,
    value: str,
    type_str: str = "",
    variables_reference: int = 0,
    presentation_hint: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Assemble one DAP ``Variable`` body.

    ``variables_reference: 0`` marks the entry as a leaf; any positive
    integer makes the IDE render an expansion chevron."""
    entry: Dict[str, Any] = {
        "name": name,
        "value": value,
        "type": type_str,
        "variablesReference": variables_reference,
    }
    if presentation_hint:
        entry["presentationHint"] = presentation_hint
    return entry


def surface_value_text(raw_value: str) -> Tuple[str, str]:
    """Decide what text to show in the Variables panel's ``value`` cell.

    Returns ``(display, type_tag)`` where ``display`` is the rendered
    cell content and ``type_tag`` is the classification (``int`` /
    ``str`` / ``ptr`` / ``raw`` / ``bool`` / ``char``). For strings the
    display is the quoted preview rather than the raw pointer hex; for
    other types we surface the value verbatim."""
    preview = build_string_preview(raw_value)
    if preview is not None:
        return (preview, "str")
    expandable, tag = classify_variable(raw_value)
    return (raw_value.strip(), tag)
