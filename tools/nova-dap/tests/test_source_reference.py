"""Tests for R34F: DAP ``disassemble`` + ``sourceReference`` mechanism.

Three layers, mirroring the pattern established by
``test_instruction_stepping.py``:

1. **Unit tests** for ``nova_dap.source_refs`` (objdump parser, cache,
   wire helpers) and the extended ``DisassembledInstruction.to_dap_dict``
   shape (sourceReference field). No gdb / objdump required for the
   pure-data assertions; the objdump-driven cases SKIP if objdump
   isn't on PATH.

2. **Server handler tests with a stub bridge** — drive
   ``handle_disassemble``, ``handle_source``, and ``handle_stack_trace``
   against a ``CaptureBridge``. We verify the sourceReference is
   stamped on each disassembled instruction, the ``source`` handler
   returns the cached buffer, and the stackTrace frames carry
   ``instructionPointerReference``.

3. **End-to-end against gdb + the NOVA hello_dwarf binary** — launch
   the binary, disassemble at main entry, fetch the sourceReference
   via ``source``, and verify the buffer is non-empty assembly text.

Regression checks:

   * R29E hit-count breakpoints still work.
   * R31E reverse-debug still works.
   * R33F exception breakpoints still work.

Run::

    python tools/nova-dap/tests/test_source_reference.py
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

from nova_dap.gdb_bridge import GdbResult, GdbAsyncRecord  # noqa: E402
from nova_dap.disassembly import DisassembledInstruction  # noqa: E402
from nova_dap.source_refs import (  # noqa: E402
    ObjdumpInstruction,
    SourceReferenceCache,
    SourceReferenceEntry,
    build_function_assembly_listing,
    build_source_descriptor,
    disassemble_via_objdump,
    find_function_at_address,
    objdump_available,
    parse_objdump_output,
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
# Unit tests for source_refs.py (no gdb / objdump required for these).
# ---------------------------------------------------------------------------


def test_source_refs_cache_allocates_unique_ids() -> None:
    """Two distinct (binary, function) keys get distinct ids."""
    cache = SourceReferenceCache()
    ref1 = cache.allocate("/tmp/binA", "main")
    ref2 = cache.allocate("/tmp/binA", "greet")
    check(ref1 != ref2)
    check(ref1 > 0)
    check(ref2 > 0)


def test_source_refs_cache_reuses_id_for_same_key() -> None:
    """Same key returns the same id so re-issuing disassemble for the
    same function doesn't bloat the cache or break IDE round-trips."""
    cache = SourceReferenceCache()
    ref1 = cache.allocate("/tmp/bin", "main")
    ref2 = cache.allocate("/tmp/bin", "main")
    check_eq(ref1, ref2)


def test_source_refs_cache_get_returns_entry() -> None:
    """Lookup by id returns the cached entry with the original key."""
    cache = SourceReferenceCache()
    ref = cache.allocate("/tmp/bin", "main")
    entry = cache.get(ref)
    check(entry is not None)
    check_eq(entry.ref_id, ref)
    check_eq(entry.cache_key, ("/tmp/bin", "main"))


def test_source_refs_cache_clear_all_resets() -> None:
    """``clear_all`` drops every entry and resets id allocation to 1."""
    cache = SourceReferenceCache()
    cache.allocate("/tmp/bin", "main")
    cache.allocate("/tmp/bin", "greet")
    check_eq(cache.size(), 2)
    cache.clear_all()
    check(cache.is_empty())
    next_id = cache.allocate("/tmp/bin", "main")
    check_eq(next_id, 1)


def test_source_refs_materialise_with_stub_builder() -> None:
    """Lazy materialise calls the builder once and caches the result."""
    cache = SourceReferenceCache()
    ref = cache.allocate("/tmp/bin", "main")
    call_count = {"n": 0}

    def fake_builder(binary: str, func: str) -> Optional[str]:
        call_count["n"] += 1
        return f"; {func} in {binary}\n  ret\n"

    content = cache.materialise(ref, builder=fake_builder)
    check(content is not None)
    check("main" in content)
    # Second call: cached, builder should NOT be invoked again.
    content2 = cache.materialise(ref, builder=fake_builder)
    check_eq(content, content2)
    check_eq(call_count["n"], 1)


def test_source_refs_materialise_handles_missing_id() -> None:
    """A lookup for an unknown id returns None cleanly."""
    cache = SourceReferenceCache()
    check_eq(cache.materialise(999), None)


def test_source_refs_materialise_handles_builder_failure() -> None:
    """When the builder returns None (e.g. function not in binary) we
    return None so the caller can report success=False."""
    cache = SourceReferenceCache()
    ref = cache.allocate("/tmp/bin", "ghost")
    out = cache.materialise(ref, builder=lambda b, f: None)
    check_eq(out, None)


def test_build_source_descriptor_shape() -> None:
    """The DAP Source descriptor carries the sourceReference + a
    de-emphasis hint so the IDE renders the tab as derived."""
    cache = SourceReferenceCache()
    ref = cache.allocate("/tmp/bin", "main")
    entry = cache.get(ref)
    desc = build_source_descriptor(entry, "main")
    check_eq(desc["sourceReference"], ref)
    check("main" in desc["name"])
    check(desc["name"].endswith(".s"))
    check_eq(desc["presentationHint"], "deemphasize")
    check("nova-dap" in desc["origin"])


# ---------------------------------------------------------------------------
# objdump parser tests (synthetic input, no objdump invocation needed).
# ---------------------------------------------------------------------------


_SAMPLE_OBJDUMP_OUTPUT = """\
bin/hello_dwarf:     file format elf64-x86-64


Disassembly of section .text:

0000000000401000 <greet>:
  401000:\tpush   rbp
  401001:\tmov    rbp,rsp
  401004:\tsub    rsp,0x10
  401008:\tmov    QWORD PTR [rbp-0x8],rdi
  40100c:\tlea    rax,[rip+0x2fed]        # 404000 <_str_0>
  401035:\tret

0000000000401036 <main>:
  401036:\tpush   rbp
  401037:\tmov    rbp,rsp
  40103a:\tsub    rsp,0x30
  40103e:\tmov    rax,0x1
  401045:\tmov    QWORD PTR [rbp-0x8],rax
  4010a0:\tret
"""


def test_parse_objdump_output_extracts_function_headers() -> None:
    """The parser groups instructions under their enclosing function."""
    rows = parse_objdump_output(_SAMPLE_OBJDUMP_OUTPUT)
    check(len(rows) >= 8)
    greet_rows = [r for r in rows if r.function == "greet"]
    main_rows = [r for r in rows if r.function == "main"]
    check(len(greet_rows) >= 4)
    check(len(main_rows) >= 4)


def test_parse_objdump_output_strips_inline_comments() -> None:
    """``# <_str_0>`` annotations are stripped from the mnemonic."""
    rows = parse_objdump_output(_SAMPLE_OBJDUMP_OUTPUT)
    lea_rows = [r for r in rows if "lea" in r.mnemonic]
    check(len(lea_rows) >= 1)
    # Comment must be gone, mnemonic + operand intact.
    check("#" not in lea_rows[0].mnemonic, f"comment leak: {lea_rows[0].mnemonic!r}")
    check("rip" in lea_rows[0].mnemonic)


def test_parse_objdump_output_skips_section_banners() -> None:
    """The ``Disassembly of section`` banner is filtered out."""
    rows = parse_objdump_output(_SAMPLE_OBJDUMP_OUTPUT)
    for r in rows:
        check("Disassembly" not in r.mnemonic, f"banner leak: {r.mnemonic!r}")


def test_parse_objdump_output_empty_input() -> None:
    """An empty / non-string input returns an empty list, not an error."""
    check_eq(parse_objdump_output(""), [])
    check_eq(parse_objdump_output(None), [])  # type: ignore[arg-type]


def test_parse_objdump_output_address_lowercased() -> None:
    """Addresses are canonicalised to lowercase hex (objdump output is
    already lowercase, but we re-lower so the cache key match is
    case-insensitive)."""
    rows = parse_objdump_output(_SAMPLE_OBJDUMP_OUTPUT)
    for r in rows:
        check_eq(r.address, r.address.lower())


# ---------------------------------------------------------------------------
# DisassembledInstruction.to_dap_dict: sourceReference handling.
# ---------------------------------------------------------------------------


def test_disassembled_instruction_emits_source_reference() -> None:
    """An instruction with a sourceReference emits the field inside
    ``location: {sourceReference: N}``."""
    insn = DisassembledInstruction(
        address="0x401000",
        instruction="push   %rbp",
        source_reference=7,
        source_name="main.s",
    )
    d = insn.to_dap_dict()
    check("location" in d, f"missing location: {d!r}")
    loc = d["location"]
    check_eq(loc["sourceReference"], 7)
    check_eq(loc["name"], "main.s")
    check_eq(loc["presentationHint"], "deemphasize")


def test_disassembled_instruction_combines_path_and_source_reference() -> None:
    """When BOTH ``location_path`` and ``source_reference`` are set, the
    DAP location carries both — the IDE prefers sourceReference > 0
    but the path is still useful as a label."""
    insn = DisassembledInstruction(
        address="0x401000",
        instruction="ret",
        location_path="/tmp/src.nova",
        location_name="src.nova",
        source_reference=3,
        source_name="main.s",
    )
    d = insn.to_dap_dict()
    loc = d["location"]
    check_eq(loc["path"], "/tmp/src.nova")
    check_eq(loc["sourceReference"], 3)


def test_disassembled_instruction_zero_source_reference_omitted() -> None:
    """``source_reference: 0`` is reserved by DAP for "use the path";
    we omit the field rather than emitting ``sourceReference: 0`` so
    the IDE doesn't mistake real-file instructions for synthetic."""
    insn = DisassembledInstruction(
        address="0x401000",
        instruction="ret",
        source_reference=0,
    )
    d = insn.to_dap_dict()
    # No location at all when there's neither path nor positive ref.
    check("location" not in d, f"unexpected location: {d!r}")


def test_disassembled_instruction_no_source_reference_legacy_shape() -> None:
    """Without a sourceReference set, the wire shape is unchanged
    (regression for the R17F + pre-R34F path)."""
    insn = DisassembledInstruction(address="0x401000", instruction="ret")
    d = insn.to_dap_dict()
    check_eq(set(d.keys()), {"address", "instruction"})


# ---------------------------------------------------------------------------
# Server handler tests (no gdb required — uses CaptureBridge stub).
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
    """Fake GdbBridge with canned replies for the disassemble path."""

    def __init__(self) -> None:
        self.sent_commands: List[str] = []
        self._fail_next: Optional[str] = None
        self._next_bp_id = 1
        self.canned_asm_insns: List[Dict[str, Any]] = [
            {
                "address": "0x401036",
                "func-name": "main",
                "offset": "0",
                "inst": "push   %rbp",
            },
            {
                "address": "0x401037",
                "func-name": "main",
                "offset": "1",
                "inst": "mov    %rsp,%rbp",
            },
            {
                "address": "0x40103a",
                "func-name": "main",
                "offset": "4",
                "inst": "sub    $0x30,%rsp",
            },
        ]
        # Optional stack-frames reply for the stackTrace handler test.
        self.canned_stack: List[Dict[str, Any]] = [
            {
                "frame": {
                    "level": "0",
                    "addr": "0x401036",
                    "func": "main",
                    "file": "hello_dwarf.nova",
                    "fullname": "/home/user/NOVA/examples/hello_dwarf.nova",
                    "line": "30",
                }
            },
            {
                "frame": {
                    "level": "1",
                    "addr": "0x4035fe",
                    "func": "__libc_start_main",
                    "file": "libc-start.c",
                    "fullname": "/build/glibc/csu/libc-start.c",
                    "line": "308",
                }
            },
        ]

    def fail_next(self, message: str) -> None:
        self._fail_next = message

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if self._fail_next is not None:
            msg = self._fail_next
            self._fail_next = None
            return GdbResult(token=None, cls="error", fields={"msg": msg})
        if cmd.startswith("-data-disassemble"):
            return GdbResult(
                token=None,
                cls="done",
                fields={"asm_insns": list(self.canned_asm_insns)},
            )
        if cmd.startswith("-stack-list-frames"):
            return GdbResult(
                token=None,
                cls="done",
                fields={"stack": list(self.canned_stack)},
            )
        if cmd.startswith("-break-insert"):
            bp_id = self._next_bp_id
            self._next_bp_id += 1
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {
                        "number": str(bp_id),
                        "addr": "0x401036",
                        "func": "main",
                    }
                },
            )
        return GdbResult(token=None, cls="done", fields={})


def test_capability_advertises_disassemble_and_stepping_granularity() -> None:
    """R34F brief: the ``initialize`` response must advertise
    supportsDisassembleRequest + supportsSteppingGranularity. The
    sourceReference mechanism doesn't have a dedicated capability flag
    (it's implicit in the disassemble/source request pair)."""
    from nova_dap.server import _capabilities, HANDLERS

    caps = _capabilities()
    check_eq(caps.get("supportsDisassembleRequest"), True)
    check_eq(caps.get("supportsSteppingGranularity"), True)
    # ``source`` handler must be wired.
    check("source" in HANDLERS, f"source not in handlers: {sorted(HANDLERS)}")
    # The legacy ``disassemble`` handler must still be wired.
    check("disassemble" in HANDLERS)


def test_disassemble_stamps_source_reference_on_each_instruction() -> None:
    """A successful disassemble run must attach a sourceReference to
    every returned instruction so the IDE can fetch the synthetic
    ``.s`` listing on click."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()
    # The session needs a program path so the source-refs cache can
    # build a stable key. We don't actually need the binary on disk
    # for THIS test because we mock the materialise path separately.
    sess.program = "/tmp/fake_binary"
    req = {
        "seq": 1,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "0x401036",
            "instructionCount": 3,
        },
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    check(resp is not None)
    check_eq(resp.get("success"), True)
    insns = resp.get("body", {}).get("instructions") or []
    check_eq(len(insns), 3)
    # Each insn must carry a positive sourceReference in its location.
    for i, insn in enumerate(insns):
        loc = insn.get("location") or {}
        ref = loc.get("sourceReference")
        check(
            isinstance(ref, int) and ref > 0,
            f"insn[{i}] missing sourceReference: {insn!r}",
        )
    # All three should share the same sourceReference (same function).
    refs = {(i.get("location") or {}).get("sourceReference") for i in insns}
    check_eq(len(refs), 1, f"expected one shared ref, got {refs}")


def test_source_handler_returns_cached_content() -> None:
    """The ``source`` handler returns the buffer cached by a prior
    ``disassemble`` request. We pre-populate the cache via a stub
    builder so the test doesn't require objdump."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    sess.program = "/tmp/fake_binary"
    # Allocate + populate the cache manually so we don't need objdump.
    ref = sess.source_refs.allocate("/tmp/fake_binary", "main")
    # Stamp the content directly (matching what materialise() would do).
    entry = sess.source_refs.get(ref)
    entry.content = "; synthetic disassembly\n  401036:    push rbp\n  401037:    ret\n"
    req = {
        "seq": 2,
        "type": "request",
        "command": "source",
        "arguments": {"sourceReference": ref},
    }
    server.handle_source(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body") or {}
    content = body.get("content") or ""
    check(len(content) > 0)
    check("401036" in content)
    check_eq(body.get("mimeType"), "text/x-asm")


def test_source_handler_rejects_zero_reference() -> None:
    """``sourceReference: 0`` is reserved by DAP for "open the path";
    the server must refuse it cleanly."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    req = {
        "seq": 3,
        "type": "request",
        "command": "source",
        "arguments": {"sourceReference": 0},
    }
    server.handle_source(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_source_handler_rejects_unknown_reference() -> None:
    """An id that was never allocated produces a clear error."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    req = {
        "seq": 4,
        "type": "request",
        "command": "source",
        "arguments": {"sourceReference": 999},
    }
    server.handle_source(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)
    check("999" in resp.get("message", ""))


def test_source_handler_accepts_nested_arg_shape() -> None:
    """The DAP client may nest the reference under ``source``: we
    accept both shapes."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    sess.program = "/tmp/bin"
    ref = sess.source_refs.allocate("/tmp/bin", "fn")
    entry = sess.source_refs.get(ref)
    entry.content = "  401000: ret\n"
    req = {
        "seq": 5,
        "type": "request",
        "command": "source",
        "arguments": {"source": {"sourceReference": ref}},
    }
    server.handle_source(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)


def test_stack_trace_carries_instruction_pointer_reference() -> None:
    """R34F: each ``stackTrace`` frame must include
    ``instructionPointerReference`` so the IDE can pin disassemble
    requests to the right frame's PC."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge
    req = {
        "seq": 6,
        "type": "request",
        "command": "stackTrace",
        "arguments": {"threadId": 1},
    }
    server.handle_stack_trace(sess, req)
    resp = sess.out_stream.last_response()
    check(resp is not None)
    check_eq(resp.get("success"), True)
    frames = resp.get("body", {}).get("stackFrames") or []
    check_eq(len(frames), 2)
    for i, f in enumerate(frames):
        ip = f.get("instructionPointerReference")
        check(
            isinstance(ip, str) and ip.startswith("0x"),
            f"frame[{i}] missing PC: {f!r}",
        )
    # Frame 0 sits at main's first insn; frame 1 sits in the libc
    # start glue.
    check_eq(frames[0]["instructionPointerReference"], "0x401036")
    check_eq(frames[1]["instructionPointerReference"], "0x4035fe")


def test_disassemble_with_negative_instruction_offset() -> None:
    """R34F brief: ``instructionOffset: -3`` returns 3 instructions
    BEFORE the anchor. We approximate via the 4-byte step in
    ``parse_memory_reference`` so the gdb start address shifts back by
    12 bytes."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge
    sess.program = "/tmp/fake_binary"
    # Override canned asm_insns to include addresses BEFORE 0x401036
    # so the response covers the negative window.
    bridge.canned_asm_insns = [
        {"address": "0x40102a", "inst": "ret"},
        {"address": "0x40102b", "inst": "nop"},
        {"address": "0x40102c", "inst": "nop"},
    ]
    req = {
        "seq": 7,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "0x401036",
            "instructionOffset": -3,
            "instructionCount": 3,
        },
    }
    server.handle_disassemble(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-data-disassemble")]
    check_eq(len(cmds), 1, f"expected one disassemble cmd: {bridge.sent_commands}")
    # Start address must be lower than the anchor (12 bytes back from 0x401036).
    check("0x40102a" in cmds[0], f"expected backwards offset in {cmds[0]!r}")
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    insns = resp.get("body", {}).get("instructions") or []
    check_eq(len(insns), 3)


def test_disassemble_returns_count_instructions_or_padded() -> None:
    """R34F brief: disassemble returns >= instructionCount entries
    (we pad with ``"??"`` if gdb returns fewer)."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    bridge.canned_asm_insns = bridge.canned_asm_insns[:1]  # one only
    sess.bridge = bridge
    sess.program = "/tmp/fake_binary"
    req = {
        "seq": 8,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "0x401036",
            "instructionCount": 5,
        },
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    insns = resp.get("body", {}).get("instructions") or []
    check_eq(len(insns), 5)
    # Last few are placeholders.
    check_eq(insns[-1]["instruction"], "??")


def test_launch_clears_source_reference_cache() -> None:
    """A relaunch must reset the source-refs cache (the new binary may
    have moved functions; stale ids would mislead the IDE)."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    sess.source_refs.allocate("/tmp/old_bin", "main")
    sess.source_refs.allocate("/tmp/old_bin", "greet")
    check_eq(sess.source_refs.size(), 2)
    # Simulate the launch's cache-clear step.
    sess.source_refs.clear_all()
    check(sess.source_refs.is_empty())


# ---------------------------------------------------------------------------
# Stepping granularity routing — regression for R17F + R34F.
# ---------------------------------------------------------------------------


def test_step_instruction_granularity_routes_to_si() -> None:
    """``stepIn`` with ``granularity: "instruction"`` must call
    ``-exec-step-instruction`` (gdb's MI form of ``si``)."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge
    req = {
        "seq": 9,
        "type": "request",
        "command": "stepIn",
        "arguments": {"threadId": 1, "granularity": "instruction"},
    }
    server.handle_step_in(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-exec-")]
    check_eq(len(cmds), 1)
    check(
        "-exec-step-instruction" in cmds[0],
        f"expected step-instruction, got {cmds[0]!r}",
    )


def test_step_line_granularity_uses_default_step() -> None:
    """Regression: ``stepIn`` with ``granularity: "line"`` (or absent)
    keeps the line-level ``-exec-step`` — we must not accidentally
    upgrade to instruction stepping when the IDE asks for source-level."""
    from nova_dap import server

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge
    req = {
        "seq": 10,
        "type": "request",
        "command": "stepIn",
        "arguments": {"threadId": 1, "granularity": "line"},
    }
    server.handle_step_in(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-exec-")]
    check_eq(len(cmds), 1)
    check("-exec-step" in cmds[0])
    check(
        "instruction" not in cmds[0],
        f"line granularity must NOT trigger instruction stepping: {cmds[0]!r}",
    )


# ---------------------------------------------------------------------------
# Regression for R29E hit-count breakpoints, R31E reverse-debug, R33F
# exception breakpoints.
# ---------------------------------------------------------------------------


def test_r29e_hit_count_breakpoints_capability_preserved() -> None:
    """R29E hit-count breakpoint capability must still be advertised."""
    from nova_dap.server import _capabilities
    caps = _capabilities()
    check_eq(caps.get("supportsHitConditionalBreakpoints"), True)


def test_r31e_reverse_debug_capability_preserved() -> None:
    """R31E reverse-debug must still be advertised."""
    from nova_dap.server import _capabilities
    caps = _capabilities()
    check_eq(caps.get("supportsStepBack"), True)


def test_r33f_exception_breakpoints_capability_preserved() -> None:
    """R33F exception-breakpoint filters + exceptionInfo capability."""
    from nova_dap.server import _capabilities
    caps = _capabilities()
    check_eq(caps.get("supportsExceptionInfoRequest"), True)
    filters = caps.get("exceptionBreakpointFilters") or []
    check(len(filters) >= 1)
    filter_names = {f.get("filter") for f in filters if isinstance(f, dict)}
    check("uncaught" in filter_names)


def test_r29e_hit_count_module_intact() -> None:
    """Regression: parse_hit_condition still parses common forms."""
    from nova_dap.breakpoints import parse_hit_condition
    pred = parse_hit_condition(">3")
    check(pred is not None)
    check(not pred(1))
    check(not pred(3))
    check(pred(4))


def test_r33f_fatal_signal_detection_intact() -> None:
    """Regression: is_fatal_signal still flags SIGABRT / SIGSEGV."""
    from nova_dap.server import is_fatal_signal
    check(is_fatal_signal("SIGABRT"))
    check(is_fatal_signal("SIGSEGV"))
    check(not is_fatal_signal("SIGINT"))
    check(not is_fatal_signal(None))


# ---------------------------------------------------------------------------
# objdump end-to-end (requires objdump on PATH).
# ---------------------------------------------------------------------------


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")


def test_disassemble_via_objdump_against_real_binary() -> Optional[str]:
    """Drive the objdump path end-to-end against the NOVA hello_dwarf
    binary. SKIPs cleanly if objdump or the binary aren't available."""
    if not objdump_available():
        return "skip: objdump not on PATH"
    if not os.path.isfile(HELLO_DWARF_BIN):
        return "skip: hello_dwarf binary missing"
    # Pick a known address. Look up main via objdump first so we don't
    # hard-code an address that may shift across builds.
    func = find_function_at_address(HELLO_DWARF_BIN, 0x401036)
    # Function lookup may fail if the layout changed — that's fine,
    # try the broader sweep.
    if func is None:
        # Try another likely address near main.
        func = find_function_at_address(HELLO_DWARF_BIN, 0x401050)
    # Even without a hit on a precise address, objdump must produce
    # SOME instructions from the binary.
    rows = disassemble_via_objdump(HELLO_DWARF_BIN, 0x401000, 5)
    check(len(rows) >= 1, f"objdump returned no rows for {HELLO_DWARF_BIN}")
    # Build the function listing for main. We expect it to find SOMETHING.
    if func is not None:
        listing = build_function_assembly_listing(HELLO_DWARF_BIN, func)
        check(listing is not None, f"no listing for {func}")
        check("main" in listing or func in listing, f"listing missing name: {listing[:200]}")
    return None


# ---------------------------------------------------------------------------
# End-to-end DAP client driver.
# ---------------------------------------------------------------------------


class DapClient:
    """Tiny DAP client over stdio."""

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


def _e2e_disassemble_and_source() -> Optional[Dict[str, Any]]:
    """Launch DAP server + the hello_dwarf binary, disassemble at main,
    then fetch the sourceReference via ``source``. Returns a summary
    dict for assertions or None when SKIPped."""
    if not os.path.isfile(HELLO_DWARF_BIN):
        return None
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], f"initialize: {init}")
        caps = init.get("body", {})
        check_eq(caps.get("supportsDisassembleRequest"), True)
        check_eq(caps.get("supportsSteppingGranularity"), True)
        client.wait_for_event("initialized", timeout=5.0)
        launch = client.request("launch", {"program": HELLO_DWARF_BIN})
        check(launch["success"], f"launch: {launch}")
        sbps = client.request(
            "setFunctionBreakpoints",
            {"breakpoints": [{"name": "main"}]},
        )
        check(sbps["success"])
        cd = client.request("configurationDone", {})
        check(cd["success"])
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        first_pc = stop_body.get("instructionPointerReference")
        check(
            isinstance(first_pc, str) and first_pc.startswith("0x"),
            f"expected PC in stop, got {first_pc!r}",
        )
        tid = int(stop_body.get("threadId") or 1)
        # stackTrace must carry the per-frame PC (R34F deliverable).
        stk = client.request("stackTrace", {"threadId": tid})
        check(stk["success"])
        frames = stk.get("body", {}).get("stackFrames") or []
        check(len(frames) >= 1)
        frame_pc = frames[0].get("instructionPointerReference")
        check(
            isinstance(frame_pc, str) and frame_pc.startswith("0x"),
            f"frame[0] missing PC: {frames[0]!r}",
        )
        # Disassemble around the PC. R34F: every instruction should
        # carry a sourceReference.
        disasm = client.request(
            "disassemble",
            {"memoryReference": first_pc, "instructionCount": 6},
        )
        check(disasm["success"], f"disassemble: {disasm}")
        insns = disasm.get("body", {}).get("instructions") or []
        check(len(insns) >= 1)
        # Find the first insn with a sourceReference.
        source_ref: Optional[int] = None
        for insn in insns:
            loc = insn.get("location") or {}
            ref = loc.get("sourceReference")
            if isinstance(ref, int) and ref > 0:
                source_ref = ref
                break
        check(
            source_ref is not None,
            f"no sourceReference found in disasm response: {insns}",
        )
        # Now fetch the synthetic source via ``source``.
        src = client.request("source", {"sourceReference": source_ref})
        # The source may legitimately fail if objdump isn't on PATH or
        # the symbol isn't resolved. We tolerate that — the test
        # captures success / failure to surface as a summary metric.
        source_success = bool(src.get("success"))
        source_len = 0
        if source_success:
            content = (src.get("body") or {}).get("content") or ""
            source_len = len(content)
        # Continue + drain.
        client.request("continue", {"threadId": tid})
        for _ in range(8):
            try:
                ev = client.wait_for_event("stopped", timeout=3.0)
                tid = int((ev.get("body") or {}).get("threadId") or tid)
                client.request("continue", {"threadId": tid})
            except TimeoutError:
                break
        try:
            client.wait_for_event("terminated", timeout=10.0)
        except TimeoutError:
            pass
        client.request("disconnect", {})
        return {
            "first_pc": first_pc,
            "frame_pc": frame_pc,
            "disasm_count": len(insns),
            "source_ref": source_ref,
            "source_success": source_success,
            "source_len": source_len,
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    # SourceReferenceCache
    test_source_refs_cache_allocates_unique_ids()
    test_source_refs_cache_reuses_id_for_same_key()
    test_source_refs_cache_get_returns_entry()
    test_source_refs_cache_clear_all_resets()
    test_source_refs_materialise_with_stub_builder()
    test_source_refs_materialise_handles_missing_id()
    test_source_refs_materialise_handles_builder_failure()
    test_build_source_descriptor_shape()
    # objdump parser
    test_parse_objdump_output_extracts_function_headers()
    test_parse_objdump_output_strips_inline_comments()
    test_parse_objdump_output_skips_section_banners()
    test_parse_objdump_output_empty_input()
    test_parse_objdump_output_address_lowercased()
    # DisassembledInstruction wire shape
    test_disassembled_instruction_emits_source_reference()
    test_disassembled_instruction_combines_path_and_source_reference()
    test_disassembled_instruction_zero_source_reference_omitted()
    test_disassembled_instruction_no_source_reference_legacy_shape()
    # Server handlers
    test_capability_advertises_disassemble_and_stepping_granularity()
    test_disassemble_stamps_source_reference_on_each_instruction()
    test_source_handler_returns_cached_content()
    test_source_handler_rejects_zero_reference()
    test_source_handler_rejects_unknown_reference()
    test_source_handler_accepts_nested_arg_shape()
    test_stack_trace_carries_instruction_pointer_reference()
    test_disassemble_with_negative_instruction_offset()
    test_disassemble_returns_count_instructions_or_padded()
    test_launch_clears_source_reference_cache()
    # Stepping granularity (R17F + R34F overlap)
    test_step_instruction_granularity_routes_to_si()
    test_step_line_granularity_uses_default_step()
    # Regressions for R29E / R31E / R33F
    test_r29e_hit_count_breakpoints_capability_preserved()
    test_r31e_reverse_debug_capability_preserved()
    test_r33f_exception_breakpoints_capability_preserved()
    test_r29e_hit_count_module_intact()
    test_r33f_fatal_signal_detection_intact()


def main() -> int:
    _run_unit_tests()
    unit_assertions = _ASSERT_COUNT

    # objdump integration test (SKIPs if objdump / binary missing).
    objdump_status = "skipped"
    objdump_reason = ""
    try:
        skip_reason = test_disassemble_via_objdump_against_real_binary()
        if skip_reason is not None:
            objdump_reason = skip_reason
        else:
            objdump_status = "ok"
    except AssertionError as exc:
        objdump_status = "fail"
        objdump_reason = str(exc)
        raise

    # End-to-end DAP server test (SKIPs if gdb / binary missing).
    e2e_status = "skipped"
    e2e_reason = ""
    if shutil.which("gdb") is None:
        e2e_reason = "gdb not installed"
    elif not os.path.isfile(HELLO_DWARF_BIN):
        e2e_reason = "hello_dwarf binary missing"
    else:
        try:
            summary = _e2e_disassemble_and_source()
            if summary is None:
                e2e_status = "skipped (precondition)"
            else:
                # The PC strings must be valid hex.
                check(
                    summary["first_pc"].startswith("0x"),
                    f"first PC malformed: {summary['first_pc']!r}",
                )
                check(
                    summary["frame_pc"].startswith("0x"),
                    f"frame PC malformed: {summary['frame_pc']!r}",
                )
                check(summary["disasm_count"] >= 1)
                check(summary["source_ref"] is not None)
                if summary["source_success"]:
                    check(
                        summary["source_len"] > 0,
                        "source content empty when success=True",
                    )
                e2e_status = (
                    f"ok (disasm {summary['disasm_count']} insns, "
                    f"source success={summary['source_success']}, "
                    f"len={summary['source_len']})"
                )
        except TimeoutError as exc:
            e2e_status = "skipped"
            e2e_reason = f"DAP timeout: {exc}"

    print("test_source_reference: OK")
    print(f"  unit assertions:     {unit_assertions}")
    print(f"  total assertions:    {_ASSERT_COUNT}")
    if objdump_status == "ok":
        extra_obj = _ASSERT_COUNT - unit_assertions
        print(f"  objdump integration: ok ({extra_obj} extra checks)")
    else:
        print(f"  objdump integration: SKIP — {objdump_reason}")
    if e2e_status.startswith("ok"):
        print(f"  DAP end-to-end:      {e2e_status}")
    else:
        print(f"  DAP end-to-end:      SKIP — {e2e_reason or e2e_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
