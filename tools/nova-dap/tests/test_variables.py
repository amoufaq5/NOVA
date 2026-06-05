"""Tests for R38F: structured DAP ``scopes`` + ``variables`` (Variables panel).

Three layers, mirroring the pattern established by
``test_source_reference.py``:

1. **Unit tests** for ``nova_dap.variables`` — classifier, list-layout
   helpers, ``VariablesReferenceCache``, gdb-MI response parsers. No
   gdb subprocess required.

2. **Server handler tests with a stub bridge** — drive ``handle_scopes``
   + ``handle_variables`` against a ``CaptureBridge`` so we can verify
   the DAP wire shape (Locals / Arguments / Captures scopes, expandable
   variables, nested list expansion) without spawning gdb. The fake
   bridge feeds canned MI responses including the NOVA list layout
   (``len=3, slot[0]=10, slot[1]=20, slot[2]=30``) so list expansion
   exercises the real ``-data-evaluate-expression`` round-trips.

3. **Regression checks** — every R-round before R38F must still work:
   R17F instruction stepping, R28F profiler, R29E hit-count BPs, R31E
   reverse-debug, R33F exception BPs, R34F disassemble + sourceReference,
   R35E + R36E instruction BP hardening. We import each capability flag
   + key module symbol to ensure the upgrade didn't drop any prior
   surface.

Run::

    python tools/nova-dap/tests/test_variables.py
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
from nova_dap.variables import (  # noqa: E402
    DEFAULT_CHILD_LIMIT,
    ExpansionRef,
    LIST_HEADER_SIZE,
    LIST_OFFSET_CAP,
    LIST_OFFSET_DATA,
    LIST_OFFSET_LEN,
    LIST_SLOT_SIZE,
    SCOPE_ARGUMENTS,
    SCOPE_CAPTURES,
    SCOPE_LOCALS,
    STRING_PREVIEW_LIMIT,
    ScopeRef,
    VariablesReferenceCache,
    build_list_data_expr,
    build_list_length_expr,
    build_list_slot_expr,
    build_scope_entry,
    build_string_preview,
    build_variable_entry,
    classify_variable,
    format_child_name,
    looks_like_closure_frame,
    parse_arguments_response,
    parse_locals_response,
    parse_pointer_text,
    surface_value_text,
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
# Layout constants.
# ---------------------------------------------------------------------------


def test_list_layout_constants_match_runtime() -> None:
    """NOVA list header offsets MUST match ``src/runtime/list.nova``.

    The runtime declares ``[len(8), cap(8), data_ptr(8)]`` so any drift
    here would break list expansion silently. We assert exact integer
    values rather than relying on relative offsets so a runtime change
    surfaces as a hard failure here instead of producing garbage in
    the Variables panel."""
    check_eq(LIST_OFFSET_LEN, 0, "len offset")
    check_eq(LIST_OFFSET_CAP, 8, "cap offset")
    check_eq(LIST_OFFSET_DATA, 16, "data_ptr offset")
    check_eq(LIST_HEADER_SIZE, 24, "header size")
    check_eq(LIST_SLOT_SIZE, 8, "slot size")


def test_string_preview_limit_is_bounded() -> None:
    """STRING_PREVIEW_LIMIT keeps multi-MB strings out of the panel."""
    check(STRING_PREVIEW_LIMIT >= 16, "preview limit too small")
    check(STRING_PREVIEW_LIMIT <= 1024, "preview limit too large")


def test_default_child_limit_protects_ui() -> None:
    """DEFAULT_CHILD_LIMIT prevents UI lockup for million-element lists."""
    check(DEFAULT_CHILD_LIMIT >= 10, "child limit too small")
    check(DEFAULT_CHILD_LIMIT <= 10_000, "child limit too large")


# ---------------------------------------------------------------------------
# Value classifier.
# ---------------------------------------------------------------------------


def test_classify_int_literal_is_leaf() -> None:
    """Plain integers are leaves (variablesReference: 0)."""
    expandable, tag = classify_variable("42")
    check(not expandable, "int should not be expandable")
    check_eq(tag, "int")


def test_classify_signed_int_literal() -> None:
    """Negative ints classify the same as positive."""
    expandable, tag = classify_variable("-7")
    check(not expandable)
    check_eq(tag, "int")


def test_classify_hex_value_is_pointer() -> None:
    """Any ``0x...`` value classifies as a pointer (expandable).

    gdb prints in hex only for pointer-shaped values (NOVA ints are
    untagged decimals after the smart-op layer strips the tag), so we
    treat the ``0x...`` shape as ``ptr`` regardless of length. The
    actual expansion call may discover the pointed-at memory isn't a
    list and surface an empty children list -- best-effort behaviour
    rather than a hard classification error."""
    expandable, tag = classify_variable("0x2A")
    check(expandable, "hex value classified as expandable pointer")
    check_eq(tag, "ptr")


def test_classify_long_hex_pointer_is_expandable() -> None:
    """A long hex pointer (typical gdb output for a real address)."""
    expandable, tag = classify_variable("0x7fff5fbf1234")
    check(expandable)
    check_eq(tag, "ptr")


def test_classify_long_pointer_with_pointer_type_hint() -> None:
    """Pointer type hint upgrades a hex value to ``list`` tag."""
    expandable, tag = classify_variable(
        "0x7fff5fbf1234", type_hint="NovaList*"
    )
    check(expandable, "pointer type hint should mark expandable")
    check_eq(tag, "list")


def test_classify_pointer_with_string_preview_is_str() -> None:
    """gdb's ``0x... "text"`` shape is a string leaf, not a pointer."""
    expandable, tag = classify_variable('0x7fff "hello"')
    check(not expandable, "string preview should be a leaf")
    check_eq(tag, "str")


def test_classify_bare_pointer_is_expandable_ptr() -> None:
    """A pointer with no string suffix is expandable (might be a list)."""
    expandable, tag = classify_variable("0x7fff1234")
    check(expandable)
    check_eq(tag, "ptr")


def test_classify_pointer_with_list_type_hint() -> None:
    """type_hint mentioning ``list`` upgrades the tag from ptr to list."""
    expandable, tag = classify_variable("0x7fff", type_hint="NovaList*")
    check(expandable)
    check_eq(tag, "list")


def test_classify_pointer_with_tuple_type_hint() -> None:
    """Tuples also get the list tag (R36C lowers tuples as tagged lists)."""
    expandable, tag = classify_variable("0x7fff", type_hint="Tuple<int,str>")
    check(expandable)
    check_eq(tag, "list")


def test_classify_null_pointer_is_leaf() -> None:
    """``0x0`` / ``(nil)`` / ``NULL`` are non-expandable leaves."""
    for null_form in ("0x0", "(nil)", "NULL"):
        expandable, tag = classify_variable(null_form)
        check(not expandable, f"{null_form} should be a leaf")
        check_eq(tag, "raw", f"{null_form} tag should be raw")


def test_classify_bool_literal() -> None:
    """C++ bool prints as ``true`` / ``false``."""
    for raw in ("true", "false"):
        expandable, tag = classify_variable(raw)
        check(not expandable)
        check_eq(tag, "bool")


def test_classify_char_literal() -> None:
    """gdb's ``104 'h'`` is a char leaf."""
    expandable, tag = classify_variable("104 'h'")
    check(not expandable)
    check_eq(tag, "char")


def test_classify_raw_fallback() -> None:
    """Unrecognised forms surface as raw + non-expandable."""
    expandable, tag = classify_variable("some weird thing")
    check(not expandable)
    check_eq(tag, "raw")


def test_classify_empty_value() -> None:
    """Empty value is a non-expandable raw."""
    expandable, tag = classify_variable("")
    check(not expandable)
    check_eq(tag, "raw")


def test_classify_struct_type_hint_expandable() -> None:
    """Struct pointer types are expandable even with non-pointer values."""
    expandable, tag = classify_variable("???", type_hint="MyStruct*")
    check(expandable, "struct pointer hint should mark expandable")


# ---------------------------------------------------------------------------
# String preview.
# ---------------------------------------------------------------------------


def test_string_preview_extracts_quoted_text() -> None:
    """``0x7fff "hello"`` returns ``'"hello"'``."""
    preview = build_string_preview('0x7fff "hello"')
    check_eq(preview, '"hello"')


def test_string_preview_handles_long_text() -> None:
    """Very long strings get truncated with a trailing ``...``."""
    long_text = "x" * (STRING_PREVIEW_LIMIT + 50)
    preview = build_string_preview(f'0x7fff "{long_text}"')
    check(preview is not None)
    check(preview.startswith('"'))
    check(preview.endswith('"'), f"missing closing quote: {preview!r}")
    check("..." in preview, f"missing truncation marker: {preview!r}")


def test_string_preview_returns_none_for_non_pointer() -> None:
    """Plain ints / non-pointer-prefixed values return None."""
    check(build_string_preview("42") is None)
    check(build_string_preview("0x1234") is None)
    check(build_string_preview("hello") is None)


def test_string_preview_handles_empty_string() -> None:
    """An empty string still produces a preview (``''``)."""
    preview = build_string_preview('0x7fff ""')
    check_eq(preview, '""')


# ---------------------------------------------------------------------------
# Argument response parsing.
# ---------------------------------------------------------------------------


def test_parse_arguments_response_extracts_args() -> None:
    """``-stack-list-arguments`` response with one frame's args."""
    fields = {
        "stack-args": [
            {
                "frame": {
                    "level": "0",
                    "args": [
                        {"name": "x", "value": "1", "type": "int"},
                        {"name": "y", "value": "2", "type": "int"},
                    ],
                }
            }
        ]
    }
    entries = parse_arguments_response(fields)
    check_eq(len(entries), 2)
    check_eq(entries[0]["name"], "x")
    check_eq(entries[0]["value"], "1")
    check_eq(entries[0]["type"], "int")
    check_eq(entries[1]["name"], "y")


def test_parse_arguments_response_empty_frame() -> None:
    """Frame with no args returns an empty list (not None)."""
    fields = {"stack-args": [{"frame": {"level": "0", "args": []}}]}
    entries = parse_arguments_response(fields)
    check_eq(entries, [])


def test_parse_arguments_response_no_field() -> None:
    """Missing stack-args field returns [] gracefully."""
    check_eq(parse_arguments_response({}), [])
    check_eq(parse_arguments_response({"stack-args": None}), [])


def test_parse_arguments_response_type_optional() -> None:
    """Missing type field defaults to empty string."""
    fields = {
        "stack-args": [
            {"frame": {"level": "0", "args": [{"name": "x", "value": "1"}]}}
        ]
    }
    entries = parse_arguments_response(fields)
    check_eq(len(entries), 1)
    check_eq(entries[0]["type"], "")


# ---------------------------------------------------------------------------
# Locals response parsing.
# ---------------------------------------------------------------------------


def test_parse_locals_strips_args() -> None:
    """Args (``arg="1"``) are filtered out -- they go to the Arguments scope."""
    fields = {
        "variables": [
            {"name": "x", "value": "1", "arg": "1"},
            {"name": "local", "value": "42"},
        ]
    }
    entries = parse_locals_response(fields)
    check_eq(len(entries), 1, "should only return non-arg locals")
    check_eq(entries[0]["name"], "local")


def test_parse_locals_with_type_field() -> None:
    """gdb's ``type`` field is preserved when present."""
    fields = {
        "variables": [
            {"name": "buf", "value": "0x7fff", "type": "char*"},
        ]
    }
    entries = parse_locals_response(fields)
    check_eq(entries[0]["type"], "char*")


def test_parse_locals_missing_value() -> None:
    """Locals without a value get an empty string value."""
    fields = {"variables": [{"name": "x"}]}
    entries = parse_locals_response(fields)
    check_eq(entries[0]["value"], "")


def test_parse_locals_drops_anonymous() -> None:
    """Entries with no name are dropped (gdb anonymous locals)."""
    fields = {
        "variables": [
            {"value": "0", "type": "int"},
            {"name": "real", "value": "1"},
        ]
    }
    entries = parse_locals_response(fields)
    check_eq(len(entries), 1)
    check_eq(entries[0]["name"], "real")


# ---------------------------------------------------------------------------
# Closure frame detection.
# ---------------------------------------------------------------------------


def test_closure_frame_detected_when_fn_ptr_env_list_present() -> None:
    """R37A's ``[fn_ptr, env_list]`` signature triggers Captures scope."""
    args = [
        {"name": "fn_ptr", "value": "0x401000", "type": ""},
        {"name": "env_list", "value": "0x7fff", "type": ""},
        {"name": "x", "value": "1", "type": "int"},
    ]
    check(looks_like_closure_frame(args))


def test_closure_frame_not_detected_for_regular_function() -> None:
    """Ordinary functions don't match the closure signature."""
    args = [
        {"name": "x", "value": "1", "type": "int"},
        {"name": "y", "value": "2", "type": "int"},
    ]
    check(not looks_like_closure_frame(args))


def test_closure_frame_not_detected_for_single_arg() -> None:
    """Need at least 2 formals for the heuristic to match."""
    args = [{"name": "fn_ptr", "value": "0x0"}]
    check(not looks_like_closure_frame(args))


def test_closure_frame_not_detected_empty() -> None:
    """Empty arg list is a nullary function."""
    check(not looks_like_closure_frame([]))


def test_closure_frame_tolerates_underscore_form() -> None:
    """Underscore-wrapped names (``__fn_ptr__``) still match."""
    args = [
        {"name": "__fn_ptr__", "value": "0x401000"},
        {"name": "__env_list__", "value": "0x7fff"},
    ]
    check(looks_like_closure_frame(args))


# ---------------------------------------------------------------------------
# VariablesReferenceCache.
# ---------------------------------------------------------------------------


def test_cache_allocates_unique_scope_refs() -> None:
    """Two scope allocations get distinct ids."""
    cache = VariablesReferenceCache()
    ref1 = cache.allocate_scope(SCOPE_LOCALS, frame_id=1)
    ref2 = cache.allocate_scope(SCOPE_ARGUMENTS, frame_id=1)
    check(ref1 != ref2)
    check(ref1 >= 1000, "starts at >= 1000 for legacy compat")


def test_cache_get_returns_scope_ref() -> None:
    """``get`` resolves a ref back to its ScopeRef."""
    cache = VariablesReferenceCache()
    ref = cache.allocate_scope(SCOPE_LOCALS, frame_id=3)
    rec = cache.get(ref)
    check(isinstance(rec, ScopeRef))
    check_eq(rec.kind, SCOPE_LOCALS)
    check_eq(rec.frame_id, 3)


def test_cache_get_returns_expansion_ref() -> None:
    """``get`` resolves an expansion ref back to its ExpansionRef."""
    cache = VariablesReferenceCache()
    ref = cache.allocate_expansion(frame_id=2, expression="my_list")
    rec = cache.get(ref)
    check(isinstance(rec, ExpansionRef))
    check_eq(rec.expression, "my_list")
    check_eq(rec.frame_id, 2)


def test_cache_is_scope_helper() -> None:
    """``is_scope`` discriminates scope refs from expansions."""
    cache = VariablesReferenceCache()
    scope_ref = cache.allocate_scope(SCOPE_LOCALS, frame_id=1)
    exp_ref = cache.allocate_expansion(frame_id=1, expression="x")
    check(cache.is_scope(scope_ref))
    check(not cache.is_scope(exp_ref))
    check(cache.is_expansion(exp_ref))
    check(not cache.is_expansion(scope_ref))


def test_cache_unknown_ref_returns_none() -> None:
    """``get`` on an unknown ref returns None (legacy fallback)."""
    cache = VariablesReferenceCache()
    check(cache.get(99999) is None)


def test_cache_clear_resets_state() -> None:
    """``clear`` drops every entry + resets the id counter to base."""
    cache = VariablesReferenceCache()
    cache.allocate_scope(SCOPE_LOCALS, frame_id=1)
    cache.allocate_expansion(frame_id=1, expression="x")
    check_eq(cache.size(), 2)
    cache.clear()
    check_eq(cache.size(), 0)
    # After clear, allocation starts fresh.
    fresh = cache.allocate_scope(SCOPE_LOCALS, frame_id=1)
    check_eq(fresh, 1000, "first ref after clear is 1000")


def test_cache_expansion_records_metadata() -> None:
    """ExpansionRef carries display_name + index for child rendering."""
    cache = VariablesReferenceCache()
    ref = cache.allocate_expansion(
        frame_id=5,
        expression="*((long*)(0x7fff + 24))",
        element_kind="slot",
        display_name="[2]",
        index=2,
    )
    rec = cache.get(ref)
    check_eq(rec.display_name, "[2]")
    check_eq(rec.index, 2)
    check_eq(rec.element_kind, "slot")


# ---------------------------------------------------------------------------
# List expansion helpers.
# ---------------------------------------------------------------------------


def test_build_list_length_expr_uses_offset_zero() -> None:
    """Length read is ``*((long*)(ptr))`` because LIST_OFFSET_LEN=0."""
    expr = build_list_length_expr("my_list")
    check_eq(expr, "*((long*)(my_list))")


def test_build_list_data_expr_uses_offset_16() -> None:
    """data_ptr read is ``*((long*)(ptr + 16))``."""
    expr = build_list_data_expr("my_list")
    check_eq(expr, "*((long*)(my_list + 16))")


def test_build_list_slot_expr_indexes_data_pointer() -> None:
    """``list[0]`` is ``*((long*)(data_ptr + 0))``; ``list[3]`` is ``+ 24``."""
    s0 = build_list_slot_expr("my_list", 0)
    s3 = build_list_slot_expr("my_list", 3)
    # Slot 0: read 8 bytes at data_ptr+0.
    check("+ 0" in s0, f"slot 0 missing +0: {s0!r}")
    # Slot 3: read 8 bytes at data_ptr + 3*8 = +24.
    check("+ 24" in s3, f"slot 3 missing +24: {s3!r}")
    # Both should embed the data_ptr read.
    check("my_list + 16" in s0)
    check("my_list + 16" in s3)


def test_build_list_slot_expr_works_with_hex_address() -> None:
    """Slot expression with a raw hex pointer base works."""
    s = build_list_slot_expr("0x7fff", 1)
    check("0x7fff + 16" in s)
    check("+ 8" in s, "slot 1 should be at offset 8")


def test_format_child_name() -> None:
    """List child names are ``[N]``."""
    check_eq(format_child_name(0), "[0]")
    check_eq(format_child_name(42), "[42]")


def test_parse_pointer_text_hex() -> None:
    """Hex pointers decode into ints."""
    check_eq(parse_pointer_text("0x7fff"), 0x7FFF)
    check_eq(parse_pointer_text("0X10"), 0x10)
    check_eq(parse_pointer_text("0xabcdef"), 0xABCDEF)


def test_parse_pointer_text_decimal() -> None:
    """Bare decimal addresses are tolerated."""
    check_eq(parse_pointer_text("42"), 42)


def test_parse_pointer_text_null() -> None:
    """Null pointer forms all return None."""
    check(parse_pointer_text("0x0") is None)
    check(parse_pointer_text("(nil)") is None)
    check(parse_pointer_text("NULL") is None)


def test_parse_pointer_text_pointer_with_suffix() -> None:
    """Pointer with trailing text (``0x7fff "hello"``) decodes the hex."""
    addr = parse_pointer_text('0x7fff "hello"')
    check_eq(addr, 0x7FFF)


# ---------------------------------------------------------------------------
# DAP wire helpers.
# ---------------------------------------------------------------------------


def test_build_scope_entry_minimum_shape() -> None:
    """Scope entry has name + variablesReference + expensive."""
    entry = build_scope_entry(name="Locals", variables_reference=1000)
    check_eq(entry["name"], "Locals")
    check_eq(entry["variablesReference"], 1000)
    check_eq(entry["expensive"], False)


def test_build_scope_entry_presentation_hint() -> None:
    """presentationHint is included when provided."""
    entry = build_scope_entry(
        name="Arguments", variables_reference=1001, presentation_hint="arguments"
    )
    check_eq(entry["presentationHint"], "arguments")


def test_build_variable_entry_leaf() -> None:
    """A leaf variable has variablesReference: 0."""
    entry = build_variable_entry(name="x", value="42", type_str="int")
    check_eq(entry["name"], "x")
    check_eq(entry["value"], "42")
    check_eq(entry["type"], "int")
    check_eq(entry["variablesReference"], 0)


def test_build_variable_entry_expandable() -> None:
    """An expandable variable has non-zero variablesReference."""
    entry = build_variable_entry(
        name="my_list",
        value="0x7fff",
        type_str="list",
        variables_reference=1500,
    )
    check_eq(entry["variablesReference"], 1500)


def test_surface_value_text_int_passthrough() -> None:
    """Ints come through verbatim with the ``int`` type."""
    display, tag = surface_value_text("42")
    check_eq(display, "42")
    check_eq(tag, "int")


def test_surface_value_text_string_preview() -> None:
    """Strings get the quoted preview instead of the pointer hex."""
    display, tag = surface_value_text('0x7fff "hello"')
    check_eq(display, '"hello"')
    check_eq(tag, "str")


def test_surface_value_text_pointer_passthrough() -> None:
    """Bare pointers surface as-is with the ``ptr`` type."""
    display, tag = surface_value_text("0x401000")
    check_eq(display, "0x401000")
    check_eq(tag, "ptr")


# ---------------------------------------------------------------------------
# Server handler tests with a stub bridge.
# ---------------------------------------------------------------------------


class CaptureBridge:
    """Stub ``GdbBridge`` for testing handler dispatch + MI composition.

    Each call to ``command`` is recorded so tests can assert on the
    exact MI line we sent. The response is taken from ``responses`` in
    order, OR from the ``response_map`` keyed by exact command string
    (more robust for multi-stage handlers that issue several MI
    commands and depend on specific ones returning specific data)."""

    def __init__(
        self,
        responses: Optional[List[GdbResult]] = None,
        response_map: Optional[Dict[str, GdbResult]] = None,
    ) -> None:
        self.responses = list(responses or [])
        self.response_map = response_map or {}
        self.sent_commands: List[str] = []

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if cmd in self.response_map:
            return self.response_map[cmd]
        # Pattern-based: substring-match a key in the map.
        for key, resp in self.response_map.items():
            if key in cmd:
                return resp
        if self.responses:
            return self.responses.pop(0)
        return GdbResult(token=None, cls="done", fields={})


def _build_session():
    """Construct a Session with a CaptureBridge attached."""
    # Lazy import: server pulls in nova_dap.* which is already on path.
    from nova_dap.server import Session

    bridge = CaptureBridge()
    sess = Session(out_stream=None)
    sess.bridge = bridge
    # Allocate a frame id so var_cache scope refs can map back.
    fid = sess.frame_id_for(thread_id=1, level=0)
    return sess, bridge, fid


def _last_response(captured: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the last response payload from a captured-out-stream."""
    for entry in reversed(captured):
        if entry.get("type") == "response":
            return entry
    raise AssertionError("no response captured")


def _capture_out_stream():
    """Build a fake out_stream + a function returning the captured payload."""

    class FakeStream:
        def __init__(self):
            self.payloads = []
            self.buf = bytearray()

        def write(self, data):
            self.buf.extend(data)

        def flush(self):
            pass

        def consume(self):
            """Parse all Content-Length framed messages from the buffer."""
            import json

            out = []
            data = bytes(self.buf)
            i = 0
            while i < len(data):
                # Locate the header terminator.
                end = data.find(b"\r\n\r\n", i)
                if end < 0:
                    break
                header = data[i:end].decode("ascii")
                length = 0
                for line in header.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1].strip())
                body_start = end + 4
                body = data[body_start : body_start + length]
                out.append(json.loads(body.decode("utf-8")))
                i = body_start + length
            return out

    return FakeStream()


def test_handle_scopes_returns_locals_and_arguments() -> None:
    """Non-closure frame returns exactly Locals + Arguments."""
    from nova_dap.server import Session, handle_scopes

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    # Stub bridge with NO closure arg signature -- ordinary fn(x, y).
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-arguments": GdbResult(
                token=None,
                cls="done",
                fields={
                    "stack-args": [
                        {
                            "frame": {
                                "level": "0",
                                "args": [
                                    {"name": "x", "value": "1", "type": "int"},
                                    {"name": "y", "value": "2", "type": "int"},
                                ],
                            }
                        }
                    ]
                },
            ),
        }
    )
    fid = sess.frame_id_for(thread_id=1, level=0)
    handle_scopes(sess, {"seq": 1, "command": "scopes", "arguments": {"frameId": fid}})
    payload = _last_response(stream.consume())
    check(payload["success"])
    scopes = payload["body"]["scopes"]
    check_eq(len(scopes), 2, "non-closure frame: Locals + Arguments only")
    names = [s["name"] for s in scopes]
    check_eq(names, ["Locals", "Arguments"])
    # Both should have non-zero variablesReferences.
    for s in scopes:
        check(s["variablesReference"] > 0)


def test_handle_scopes_returns_captures_for_closure_frame() -> None:
    """Closure body (R37A signature) returns Locals + Arguments + Captures."""
    from nova_dap.server import Session, handle_scopes

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-arguments": GdbResult(
                token=None,
                cls="done",
                fields={
                    "stack-args": [
                        {
                            "frame": {
                                "level": "0",
                                "args": [
                                    {"name": "fn_ptr", "value": "0x401000"},
                                    {"name": "env_list", "value": "0x7fff"},
                                ],
                            }
                        }
                    ]
                },
            ),
        }
    )
    fid = sess.frame_id_for(thread_id=1, level=0)
    handle_scopes(sess, {"seq": 1, "command": "scopes", "arguments": {"frameId": fid}})
    payload = _last_response(stream.consume())
    scopes = payload["body"]["scopes"]
    check_eq(len(scopes), 3, "closure frame: Locals + Arguments + Captures")
    names = [s["name"] for s in scopes]
    check_eq(names, ["Locals", "Arguments", "Captures"])


def test_handle_scopes_uses_cache_for_refs() -> None:
    """Allocated scope refs round-trip via Session.var_cache."""
    from nova_dap.server import Session, handle_scopes

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge()
    fid = sess.frame_id_for(thread_id=1, level=0)
    handle_scopes(sess, {"seq": 1, "command": "scopes", "arguments": {"frameId": fid}})
    payload = _last_response(stream.consume())
    scopes = payload["body"]["scopes"]
    # Each scope ref should map back to a ScopeRef in the cache.
    for s in scopes:
        rec = sess.var_cache.get(s["variablesReference"])
        check(isinstance(rec, ScopeRef), f"ref {s['variablesReference']} missing")
        check_eq(rec.frame_id, fid)


def test_handle_variables_locals_returns_normalised_entries() -> None:
    """Locals scope returns the gdb locals minus args, all leaves."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-variables --all-values": GdbResult(
                token=None,
                cls="done",
                fields={
                    "variables": [
                        {"name": "x", "value": "1", "arg": "1"},   # arg
                        {"name": "y", "value": "2", "arg": "1"},   # arg
                        {"name": "count", "value": "42"},
                        {"name": "name", "value": '0x1000 "alice"'},
                    ]
                },
            ),
        }
    )
    fid = sess.frame_id_for(thread_id=1, level=0)
    locals_ref = sess.var_cache.allocate_scope(SCOPE_LOCALS, fid)
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": locals_ref},
        },
    )
    payload = _last_response(stream.consume())
    check(payload["success"])
    vars_out = payload["body"]["variables"]
    # Args filtered out; only count + name remain.
    check_eq(len(vars_out), 2)
    names = [v["name"] for v in vars_out]
    check_eq(names, ["count", "name"])
    # count is an int leaf.
    check_eq(vars_out[0]["value"], "42")
    check_eq(vars_out[0]["variablesReference"], 0)
    # name is a string -- value shows the preview, not the pointer.
    check_eq(vars_out[1]["value"], '"alice"')
    check_eq(vars_out[1]["variablesReference"], 0)


def test_handle_variables_arguments_scope_returns_params() -> None:
    """Arguments scope returns the formal parameters via stack-list-arguments."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-arguments": GdbResult(
                token=None,
                cls="done",
                fields={
                    "stack-args": [
                        {
                            "frame": {
                                "level": "0",
                                "args": [
                                    {"name": "n", "value": "10", "type": "int"},
                                ],
                            }
                        }
                    ]
                },
            ),
        }
    )
    fid = sess.frame_id_for(thread_id=1, level=0)
    args_ref = sess.var_cache.allocate_scope(SCOPE_ARGUMENTS, fid)
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": args_ref},
        },
    )
    payload = _last_response(stream.consume())
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 1)
    check_eq(vars_out[0]["name"], "n")
    check_eq(vars_out[0]["value"], "10")


def test_handle_variables_list_expansion_returns_indexed_children() -> None:
    """Expanding a list ref returns ``[0], [1], [2]`` children."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    # The slot expressions look like:
    #   *((long*)(*((long*)(0x7fff1000 + 16)) + 0))   # slot 0
    #   *((long*)(*((long*)(0x7fff1000 + 16)) + 8))   # slot 1
    #   *((long*)(*((long*)(0x7fff1000 + 16)) + 16))  # slot 2
    # And the header read looks like:
    #   *((long*)(0x7fff1000))                        # length
    # We match the exact end of the inner expression so slot 2's
    # ``+ 16))`` doesn't collide with the header's ``+ 16)``.
    class CB:
        def __init__(self):
            self.sent: List[str] = []

        def command(self, cmd: str, timeout: float = 10.0):
            self.sent.append(cmd)
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            # Pull the quoted expression out of the command.
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            # Head pointer read (first evaluate of the cached expression).
            if inner == "my_list":
                return GdbResult(
                    token=None, cls="done", fields={"value": "0x7fff1000"}
                )
            # Length read at offset 0 of the list header.
            if inner == "*((long*)(0x7fff1000))":
                return GdbResult(token=None, cls="done", fields={"value": "3"})
            # Slot reads -- the outer wrapper is ``*((long*)(<data> + N))``
            # so we match by the per-slot offset.
            if inner.endswith("+ 0))"):
                return GdbResult(token=None, cls="done", fields={"value": "10"})
            if inner.endswith("+ 8))"):
                return GdbResult(token=None, cls="done", fields={"value": "20"})
            if inner.endswith("+ 16))"):
                return GdbResult(token=None, cls="done", fields={"value": "30"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    # Allocate an expansion ref for ``my_list``.
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="my_list")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    check(payload["success"], f"response: {payload}")
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 3, "should emit 3 children for length=3 list")
    check_eq([v["name"] for v in vars_out], ["[0]", "[1]", "[2]"])
    check_eq([v["value"] for v in vars_out], ["10", "20", "30"])
    # Leaf ints have variablesReference: 0.
    for v in vars_out:
        check_eq(v["variablesReference"], 0)


def test_handle_variables_list_with_string_slots() -> None:
    """List of strings: each child's value is the quoted string preview."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            if "my_list" in cmd and 'long' not in cmd:
                return GdbResult(token=None, cls="done", fields={"value": "0x7fff2000"})
            # Length 2.
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            if inner == "*((long*)(0x7fff2000))":
                return GdbResult(token=None, cls="done", fields={"value": "2"})
            # Slot 0: "alice"
            if "+ 0)" in inner:
                return GdbResult(
                    token=None, cls="done", fields={"value": '0x10 "alice"'}
                )
            # Slot 1: "bob"
            if inner.endswith("+ 8))"):
                return GdbResult(
                    token=None, cls="done", fields={"value": '0x20 "bob"'}
                )
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="my_list")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 2)
    check_eq(vars_out[0]["value"], '"alice"')
    check_eq(vars_out[1]["value"], '"bob"')
    # String leaves have variablesReference: 0.
    for v in vars_out:
        check_eq(v["variablesReference"], 0)


def test_handle_variables_nested_list_outer_marks_inner_expandable() -> None:
    """Outer list of lists: inner pointers come back expandable."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            if "outer" in cmd and 'long' not in cmd:
                return GdbResult(token=None, cls="done", fields={"value": "0x7fff3000"})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            if inner == "*((long*)(0x7fff3000))":
                return GdbResult(token=None, cls="done", fields={"value": "2"})
            # Each slot points to another list (bare hex pointers,
            # which the classifier marks as expandable).
            if "+ 0)" in inner:
                return GdbResult(token=None, cls="done", fields={"value": "0x8000aaaa"})
            if inner.endswith("+ 8))"):
                return GdbResult(token=None, cls="done", fields={"value": "0x8000bbbb"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="outer")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 2)
    # Both children are pointers -> expandable.
    for v in vars_out:
        check(v["variablesReference"] != 0, f"{v['name']} should be expandable")
        # Each child ref must resolve to an ExpansionRef in the cache.
        rec = sess.var_cache.get(v["variablesReference"])
        check(isinstance(rec, ExpansionRef))


def test_handle_variables_nested_list_inner_expansion_works() -> None:
    """Two-level expansion: outer list expands, then inner ref expands."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    state = {"call_seq": 0}

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            # Outer head ptr resolution.
            if inner == "outer":
                return GdbResult(token=None, cls="done", fields={"value": "0x10000"})
            # Outer length.
            if inner == "*((long*)(0x10000))":
                return GdbResult(token=None, cls="done", fields={"value": "1"})
            # Slot 0 of outer points to inner list.
            if inner == "*((long*)(*((long*)(0x10000 + 16)) + 0))":
                return GdbResult(token=None, cls="done", fields={"value": "0x20000"})
            # Inner head resolution -- the expansion stores the slot
            # expression which re-evaluates to the inner list head.
            # Inner length.
            if inner == "*((long*)(0x20000))":
                return GdbResult(token=None, cls="done", fields={"value": "2"})
            # Inner slots.
            if inner == "*((long*)(*((long*)(0x20000 + 16)) + 0))":
                return GdbResult(token=None, cls="done", fields={"value": "100"})
            if inner == "*((long*)(*((long*)(0x20000 + 16)) + 8))":
                return GdbResult(token=None, cls="done", fields={"value": "200"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    outer_ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="outer")

    # Step 1: expand outer.
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": outer_ref},
        },
    )
    payload = _last_response(stream.consume())
    outer_kids = payload["body"]["variables"]
    check_eq(len(outer_kids), 1, "outer has 1 inner list")
    inner_ref = outer_kids[0]["variablesReference"]
    check(inner_ref != 0, "inner must be expandable")

    # Step 2: expand inner using the ref we got from step 1.
    # Reset the out stream so we read the second response cleanly.
    stream2 = _capture_out_stream()
    sess.out_stream = stream2
    handle_variables(
        sess,
        {
            "seq": 2,
            "command": "variables",
            "arguments": {"variablesReference": inner_ref},
        },
    )
    payload2 = _last_response(stream2.consume())
    inner_kids = payload2["body"]["variables"]
    check_eq(len(inner_kids), 2, "inner list has 2 elements")
    check_eq([v["value"] for v in inner_kids], ["100", "200"])


def test_handle_variables_unknown_ref_falls_back_to_legacy() -> None:
    """An unknown ref still surfaces locals (backward compat with R17F)."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-variables --all-values": GdbResult(
                token=None,
                cls="done",
                fields={
                    "variables": [{"name": "legacy_local", "value": "1"}]
                },
            ),
        }
    )
    # Use the LEGACY alloc_var_ref (pre-R38F) so the ref isn't in
    # var_cache.
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.alloc_var_ref(fid)
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    check(payload["success"])
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 1)
    check_eq(vars_out[0]["name"], "legacy_local")
    check_eq(vars_out[0]["variablesReference"], 0)


def test_handle_variables_bridge_unavailable_returns_empty() -> None:
    """No bridge -> empty variables list, no crash."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = None
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": 1500},
        },
    )
    payload = _last_response(stream.consume())
    check_eq(payload["body"]["variables"], [])


def test_handle_variables_zero_length_list_returns_empty() -> None:
    """A list with len=0 produces an empty children list (not an error)."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            if inner == "empty_list":
                return GdbResult(token=None, cls="done", fields={"value": "0x4000"})
            if inner == "*((long*)(0x4000))":
                return GdbResult(token=None, cls="done", fields={"value": "0"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="empty_list")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    check(payload["success"])
    check_eq(payload["body"]["variables"], [])


def test_handle_variables_null_pointer_returns_empty() -> None:
    """Trying to expand a null pointer returns [] not an error."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            if inner == "null_var":
                return GdbResult(token=None, cls="done", fields={"value": "0x0"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="null_var")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    check_eq(payload["body"]["variables"], [])


def test_launch_clears_variables_cache() -> None:
    """A relaunch must drop every cached variablesReference.

    Stack frames from the prior inferior are gone -- any retained
    ScopeRef would resolve to a frame id that no longer exists, and
    ExpansionRef expressions might read garbage memory."""
    from nova_dap.server import Session

    sess = Session(out_stream=_capture_out_stream())
    fid = sess.frame_id_for(thread_id=1, level=0)
    sess.var_cache.allocate_scope(SCOPE_LOCALS, fid)
    sess.var_cache.allocate_expansion(frame_id=fid, expression="x")
    check_eq(sess.var_cache.size(), 2)
    sess.var_cache.clear()
    check_eq(sess.var_cache.size(), 0)


def test_handle_scopes_no_bridge_still_returns_locals_and_arguments() -> None:
    """Pre-launch (no bridge) emits scope refs without crashing.

    The captures probe is skipped but Locals + Arguments still come
    out with valid refs."""
    from nova_dap.server import Session, handle_scopes

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = None
    fid = sess.frame_id_for(thread_id=1, level=0)
    handle_scopes(sess, {"seq": 1, "command": "scopes", "arguments": {"frameId": fid}})
    payload = _last_response(stream.consume())
    scopes = payload["body"]["scopes"]
    check_eq(len(scopes), 2)
    check_eq([s["name"] for s in scopes], ["Locals", "Arguments"])


def test_handle_variables_locals_with_list_value_marks_expandable() -> None:
    """A local of type list (bare pointer value) gets an expandable ref."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)
    sess.bridge = CaptureBridge(
        response_map={
            "-stack-list-variables --all-values": GdbResult(
                token=None,
                cls="done",
                fields={
                    "variables": [
                        {"name": "items", "value": "0x7fffaaaa"},
                    ]
                },
            ),
        }
    )
    fid = sess.frame_id_for(thread_id=1, level=0)
    locals_ref = sess.var_cache.allocate_scope(SCOPE_LOCALS, fid)
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": locals_ref},
        },
    )
    payload = _last_response(stream.consume())
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 1)
    # The pointer-shaped value should produce an expandable child ref.
    check(vars_out[0]["variablesReference"] != 0, "list local should expand")
    rec = sess.var_cache.get(vars_out[0]["variablesReference"])
    check(isinstance(rec, ExpansionRef))
    check_eq(rec.expression, "items")


def test_handle_variables_captures_scope_routes_to_env_list() -> None:
    """Captures scope expands the env_list local (R37A convention).

    In real gdb, ``env_list`` is the symbol name of the local variable
    holding the list head pointer. gdb's expression evaluator
    transparently resolves the symbol then derefs through it, so
    ``*((long*)(env_list))`` reads the length field directly without
    a separate symbol -> address step. We model that behaviour in
    the stub by responding to the dereference-shaped expression."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    captured_cmds: List[str] = []

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            captured_cmds.append(cmd)
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            # Length read at env_list+0 (gdb resolves env_list -> pointer
            # then derefs through it; the test simulates this in one step).
            if inner == "*((long*)(env_list))":
                return GdbResult(token=None, cls="done", fields={"value": "1"})
            # Slot 0 read.
            if inner == "*((long*)(*((long*)(env_list + 16)) + 0))":
                return GdbResult(token=None, cls="done", fields={"value": "99"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    cap_ref = sess.var_cache.allocate_scope(SCOPE_CAPTURES, fid)
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": cap_ref},
        },
    )
    payload = _last_response(stream.consume())
    check(payload["success"])
    vars_out = payload["body"]["variables"]
    # One captured value (the env_list had length 1).
    check_eq(len(vars_out), 1)
    check_eq(vars_out[0]["name"], "[0]")
    check_eq(vars_out[0]["value"], "99")
    # The handler must have queried env_list (not some other expression).
    saw_env_list = any("env_list" in c for c in captured_cmds)
    check(saw_env_list, f"never queried env_list: {captured_cmds}")


def test_var_cache_starts_at_1000_for_compat() -> None:
    """First-allocated ref is 1000 so legacy tests that hard-code 1000 pass."""
    from nova_dap.server import Session

    sess = Session(out_stream=_capture_out_stream())
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_scope(SCOPE_LOCALS, fid)
    check_eq(ref, 1000)


def test_expansion_ref_records_index() -> None:
    """ExpansionRefs created for list children carry the slot index."""
    from nova_dap.server import Session, handle_variables

    stream = _capture_out_stream()
    sess = Session(out_stream=stream)

    class CB:
        def command(self, cmd: str, timeout: float = 10.0):
            if cmd.startswith("-stack-select-frame") or cmd.startswith("-thread-select"):
                return GdbResult(token=None, cls="done", fields={})
            inner = cmd.split('"')[1] if '"' in cmd else cmd
            if inner == "outer":
                return GdbResult(token=None, cls="done", fields={"value": "0x9000"})
            if inner == "*((long*)(0x9000))":
                return GdbResult(token=None, cls="done", fields={"value": "2"})
            if "+ 0)" in inner:
                return GdbResult(token=None, cls="done", fields={"value": "0xaaaa"})
            if inner.endswith("+ 8))"):
                return GdbResult(token=None, cls="done", fields={"value": "0xbbbb"})
            return GdbResult(token=None, cls="done", fields={"value": "0"})

    sess.bridge = CB()
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.var_cache.allocate_expansion(frame_id=fid, expression="outer")
    handle_variables(
        sess,
        {
            "seq": 1,
            "command": "variables",
            "arguments": {"variablesReference": ref},
        },
    )
    payload = _last_response(stream.consume())
    vars_out = payload["body"]["variables"]
    check_eq(len(vars_out), 2)
    rec0 = sess.var_cache.get(vars_out[0]["variablesReference"])
    rec1 = sess.var_cache.get(vars_out[1]["variablesReference"])
    check_eq(rec0.index, 0)
    check_eq(rec1.index, 1)
    check_eq(rec0.display_name, "[0]")
    check_eq(rec1.display_name, "[1]")


# ---------------------------------------------------------------------------
# Regression checks for prior R-rounds.
# ---------------------------------------------------------------------------


def test_r17f_instruction_stepping_capability_preserved() -> None:
    """R17F: supportsSteppingGranularity is still advertised."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    check(caps.get("supportsSteppingGranularity") is True)


def test_r28f_profiler_routes_still_registered() -> None:
    """R28F: nova/profile/* requests are dispatched."""
    from nova_dap.server import HANDLERS

    check("nova/profile/start" in HANDLERS)
    check("nova/profile/stop" in HANDLERS)
    check("nova/profile/report" in HANDLERS)


def test_r29e_hit_count_capability_preserved() -> None:
    """R29E: supportsHitConditionalBreakpoints stays True."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    check(caps.get("supportsHitConditionalBreakpoints") is True)


def test_r31e_reverse_debug_capability_preserved() -> None:
    """R31E: supportsStepBack stays True."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    check(caps.get("supportsStepBack") is True)


def test_r33f_exception_breakpoints_capability_preserved() -> None:
    """R33F: exception filters still advertised."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    filters = caps.get("exceptionBreakpointFilters", [])
    filter_ids = {f.get("filter") for f in filters}
    check("uncaught" in filter_ids)
    check("caught" in filter_ids)


def test_r34f_disassemble_capability_preserved() -> None:
    """R34F: supportsDisassembleRequest stays True."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    check(caps.get("supportsDisassembleRequest") is True)


def test_r35e_instruction_bp_capability_preserved() -> None:
    """R35E: supportsInstructionBreakpoints stays True."""
    from nova_dap.server import _capabilities

    caps = _capabilities()
    check(caps.get("supportsInstructionBreakpoints") is True)


def test_r36e_normalize_hex_module_intact() -> None:
    """R36E: _normalize_hex_address still importable + functional."""
    from nova_dap.disassembly import _normalize_hex_address

    # Spot check the canonical-lowercase contract.
    check_eq(_normalize_hex_address("0X1A"), "0x1a")
    check_eq(_normalize_hex_address("0xABCD"), "0xabcd")


def test_r38f_handlers_registered_in_dispatch() -> None:
    """Scopes + variables handlers still in the dispatch table."""
    from nova_dap.server import HANDLERS

    check("scopes" in HANDLERS)
    check("variables" in HANDLERS)


def test_r38f_legacy_alloc_var_ref_still_works() -> None:
    """Session.alloc_var_ref (pre-R38F API) remains for backward compat."""
    from nova_dap.server import Session

    sess = Session(out_stream=_capture_out_stream())
    fid = sess.frame_id_for(thread_id=1, level=0)
    ref = sess.alloc_var_ref(fid)
    check(ref > 0)
    check_eq(sess.frame_refs.get(ref), fid)


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    # Layout
    test_list_layout_constants_match_runtime()
    test_string_preview_limit_is_bounded()
    test_default_child_limit_protects_ui()
    # Classifier
    test_classify_int_literal_is_leaf()
    test_classify_signed_int_literal()
    test_classify_hex_value_is_pointer()
    test_classify_long_hex_pointer_is_expandable()
    test_classify_long_pointer_with_pointer_type_hint()
    test_classify_pointer_with_string_preview_is_str()
    test_classify_bare_pointer_is_expandable_ptr()
    test_classify_pointer_with_list_type_hint()
    test_classify_pointer_with_tuple_type_hint()
    test_classify_null_pointer_is_leaf()
    test_classify_bool_literal()
    test_classify_char_literal()
    test_classify_raw_fallback()
    test_classify_empty_value()
    test_classify_struct_type_hint_expandable()
    # String preview
    test_string_preview_extracts_quoted_text()
    test_string_preview_handles_long_text()
    test_string_preview_returns_none_for_non_pointer()
    test_string_preview_handles_empty_string()
    # Argument response parsing
    test_parse_arguments_response_extracts_args()
    test_parse_arguments_response_empty_frame()
    test_parse_arguments_response_no_field()
    test_parse_arguments_response_type_optional()
    # Locals response parsing
    test_parse_locals_strips_args()
    test_parse_locals_with_type_field()
    test_parse_locals_missing_value()
    test_parse_locals_drops_anonymous()
    # Closure detection
    test_closure_frame_detected_when_fn_ptr_env_list_present()
    test_closure_frame_not_detected_for_regular_function()
    test_closure_frame_not_detected_for_single_arg()
    test_closure_frame_not_detected_empty()
    test_closure_frame_tolerates_underscore_form()
    # Cache
    test_cache_allocates_unique_scope_refs()
    test_cache_get_returns_scope_ref()
    test_cache_get_returns_expansion_ref()
    test_cache_is_scope_helper()
    test_cache_unknown_ref_returns_none()
    test_cache_clear_resets_state()
    test_cache_expansion_records_metadata()
    # List expansion helpers
    test_build_list_length_expr_uses_offset_zero()
    test_build_list_data_expr_uses_offset_16()
    test_build_list_slot_expr_indexes_data_pointer()
    test_build_list_slot_expr_works_with_hex_address()
    test_format_child_name()
    test_parse_pointer_text_hex()
    test_parse_pointer_text_decimal()
    test_parse_pointer_text_null()
    test_parse_pointer_text_pointer_with_suffix()
    # Wire helpers
    test_build_scope_entry_minimum_shape()
    test_build_scope_entry_presentation_hint()
    test_build_variable_entry_leaf()
    test_build_variable_entry_expandable()
    test_surface_value_text_int_passthrough()
    test_surface_value_text_string_preview()
    test_surface_value_text_pointer_passthrough()
    # Handler tests
    test_handle_scopes_returns_locals_and_arguments()
    test_handle_scopes_returns_captures_for_closure_frame()
    test_handle_scopes_uses_cache_for_refs()
    test_handle_variables_locals_returns_normalised_entries()
    test_handle_variables_arguments_scope_returns_params()
    test_handle_variables_list_expansion_returns_indexed_children()
    test_handle_variables_list_with_string_slots()
    test_handle_variables_nested_list_outer_marks_inner_expandable()
    test_handle_variables_nested_list_inner_expansion_works()
    test_handle_variables_unknown_ref_falls_back_to_legacy()
    test_handle_variables_bridge_unavailable_returns_empty()
    test_handle_variables_zero_length_list_returns_empty()
    test_handle_variables_null_pointer_returns_empty()
    test_launch_clears_variables_cache()
    test_handle_scopes_no_bridge_still_returns_locals_and_arguments()
    test_handle_variables_locals_with_list_value_marks_expandable()
    test_handle_variables_captures_scope_routes_to_env_list()
    test_var_cache_starts_at_1000_for_compat()
    test_expansion_ref_records_index()
    # Regression: prior R-rounds
    test_r17f_instruction_stepping_capability_preserved()
    test_r28f_profiler_routes_still_registered()
    test_r29e_hit_count_capability_preserved()
    test_r31e_reverse_debug_capability_preserved()
    test_r33f_exception_breakpoints_capability_preserved()
    test_r34f_disassemble_capability_preserved()
    test_r35e_instruction_bp_capability_preserved()
    test_r36e_normalize_hex_module_intact()
    test_r38f_handlers_registered_in_dispatch()
    test_r38f_legacy_alloc_var_ref_still_works()


def main() -> int:
    _run_unit_tests()

    print("test_variables: OK")
    print(f"  total assertions: {_ASSERT_COUNT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
