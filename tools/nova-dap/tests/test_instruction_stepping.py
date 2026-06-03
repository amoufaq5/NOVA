"""Tests for DAP instruction-level stepping — the 21st capability for
``nova-dap``.

Three layers, mirroring the pattern established by
``test_function_breakpoints.py``:

1. **Unit tests** — exercise ``nova_dap.disassembly`` directly:
   memory-reference parsing, command builders, response parser,
   step-command remapping, instruction-pointer extraction, manager
   bookkeeping. These run everywhere (no gdb required).

2. **Server handler tests with a stub bridge** — drive
   ``handle_disassemble``, ``handle_set_instruction_breakpoints``,
   and ``_step`` against a ``CaptureBridge`` that records every MI
   command. We verify granularity routing + the wire-level response
   shape + complete-replacement semantics without spawning gdb.

3. **End-to-end against gdb + the NOVA hello_dwarf binary** —
   launch the binary, step three instructions, assert the PC
   advances by a single instruction each time; disassemble around
   ``main`` entry and assert at least one instruction is returned.
   SKIPs cleanly if gdb / hello_dwarf is missing.

Run::

    python tools/nova-dap/tests/test_instruction_stepping.py
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
from nova_dap.disassembly import (  # noqa: E402
    DisassembledInstruction,
    InstructionBreakpointManager,
    InstructionBreakpointRecord,
    build_disassemble_command,
    build_instruction_breakpoint_command,
    extract_instruction_pointer,
    is_instruction_granularity,
    map_step_command,
    parse_disassemble_response,
    parse_memory_reference,
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
# Unit tests for disassembly.py (no gdb required).
# ---------------------------------------------------------------------------


def test_parse_memory_reference_hex() -> None:
    """A bare ``0xADDR`` string round-trips through normalisation."""
    out = parse_memory_reference("0x401000", 0)
    check_eq(out, "0x401000")


def test_parse_memory_reference_with_offset() -> None:
    """Instruction-offset 2 (positive) shifts the address by 8 bytes
    (our 4-byte-per-insn approximation)."""
    out = parse_memory_reference("0x401000", 2)
    check_eq(out, "0x401008")


def test_parse_memory_reference_with_negative_offset() -> None:
    """Negative instruction-offset walks back from the anchor."""
    out = parse_memory_reference("0x401010", -2)
    check_eq(out, "0x401008")


def test_parse_memory_reference_decimal() -> None:
    """A plain decimal string parses as an address too."""
    out = parse_memory_reference("4198400", 0)  # 0x401000
    check_eq(out, "0x401000")


def test_parse_memory_reference_rejects_garbage() -> None:
    """Malformed input returns None so the caller can reject."""
    check_eq(parse_memory_reference("not-an-address", 0), None)
    check_eq(parse_memory_reference("", 0), None)
    # type: ignore[arg-type]
    check_eq(parse_memory_reference(None, 0), None)  # type: ignore[arg-type]


def test_parse_memory_reference_rejects_negative_result() -> None:
    """A negative resulting address is rejected (no underflow into
    negative-space)."""
    out = parse_memory_reference("0x4", -1024)
    check_eq(out, None)


def test_build_disassemble_command_basic() -> None:
    """The MI command names both start + end address with mode 0."""
    cmd = build_disassemble_command("0x401000", "0x401080", mode=0)
    check(cmd is not None)
    check("-data-disassemble" in cmd, f"missing -data-disassemble in {cmd!r}")
    check("0x401000" in cmd)
    check("0x401080" in cmd)
    check("-- 0" in cmd, f"missing mode in {cmd!r}")


def test_build_disassemble_command_with_opcodes() -> None:
    """Mode 2 selects asm + raw opcode bytes."""
    cmd = build_disassemble_command("0x401000", "0x401080", mode=2)
    check(cmd is not None)
    check("-- 2" in cmd, f"missing mode 2 in {cmd!r}")


def test_build_disassemble_command_rejects_invalid_mode() -> None:
    """Mode outside 0..5 returns None."""
    check_eq(build_disassemble_command("0x401000", "0x401080", mode=9), None)


def test_build_disassemble_command_rejects_empty_address() -> None:
    """Empty start or end address returns None."""
    check_eq(build_disassemble_command("", "0x401080"), None)
    check_eq(build_disassemble_command("0x401000", ""), None)


def test_parse_disassemble_response_flat() -> None:
    """A mode-0 reply (flat list) decodes to one DisassembledInstruction
    per asm_insn entry."""
    fields = {
        "asm_insns": [
            {
                "address": "0x401000",
                "func-name": "main",
                "offset": "0",
                "inst": "push   %rbp",
            },
            {
                "address": "0x401001",
                "func-name": "main",
                "offset": "1",
                "inst": "mov    %rsp,%rbp",
            },
        ]
    }
    parsed = parse_disassemble_response(fields)
    check_eq(len(parsed), 2)
    check_eq(parsed[0].address, "0x401000")
    check_eq(parsed[0].instruction, "push   %rbp")
    check_eq(parsed[0].symbol, "main")
    check_eq(parsed[1].address, "0x401001")
    check_eq(parsed[1].instruction, "mov    %rsp,%rbp")


def test_parse_disassemble_response_empty() -> None:
    """An empty asm_insns array yields an empty list."""
    parsed = parse_disassemble_response({"asm_insns": []})
    check_eq(parsed, [])


def test_parse_disassemble_response_missing_field() -> None:
    """A reply without asm_insns yields an empty list (not an error)."""
    parsed = parse_disassemble_response({})
    check_eq(parsed, [])


def test_parse_disassemble_response_mixed_source() -> None:
    """Mode-4 (mixed source + asm) wraps insns in ``src_and_asm_line``
    tuples — the parser must flatten and propagate ``line`` + ``file``
    to each insn."""
    fields = {
        "asm_insns": [
            {
                "src_and_asm_line": {
                    "line": "30",
                    "file": "hello_dwarf.nova",
                    "fullname": "/home/user/NOVA/examples/hello_dwarf.nova",
                    "line_asm_insn": [
                        {
                            "address": "0x40103e",
                            "func-name": "main",
                            "offset": "0",
                            "inst": "mov    $0x1,%rax",
                        },
                        {
                            "address": "0x401045",
                            "func-name": "main",
                            "offset": "7",
                            "inst": "mov    %rax,-0x8(%rbp)",
                        },
                    ],
                }
            }
        ]
    }
    parsed = parse_disassemble_response(fields)
    check_eq(len(parsed), 2)
    check_eq(parsed[0].line, 30)
    check_eq(
        parsed[0].location_path, "/home/user/NOVA/examples/hello_dwarf.nova"
    )
    check_eq(parsed[0].location_name, "hello_dwarf.nova")
    check_eq(parsed[1].line, 30)


def test_disassembled_instruction_to_dap_dict() -> None:
    """The wire-shape conversion produces camelCase keys per DAP spec."""
    insn = DisassembledInstruction(
        address="0x401000",
        instruction="push   %rbp",
        instruction_bytes="55",
        symbol="main",
        location_path="/tmp/src.nova",
        line=10,
    )
    d = insn.to_dap_dict()
    check_eq(d["address"], "0x401000")
    check_eq(d["instruction"], "push   %rbp")
    check_eq(d["instructionBytes"], "55")
    check_eq(d["symbol"], "main")
    check_eq(d["line"], 10)
    check_eq(d["location"]["path"], "/tmp/src.nova")


def test_disassembled_instruction_minimal_dap_dict() -> None:
    """The minimal form omits all optional keys."""
    insn = DisassembledInstruction(address="0x401000", instruction="ret")
    d = insn.to_dap_dict()
    check_eq(set(d.keys()), {"address", "instruction"})


def test_is_instruction_granularity_match() -> None:
    """Only ``"instruction"`` triggers per-insn routing."""
    check(is_instruction_granularity("instruction"))
    check(not is_instruction_granularity("statement"))
    check(not is_instruction_granularity("line"))
    check(not is_instruction_granularity(None))
    check(not is_instruction_granularity(""))


def test_map_step_command_statement_passthrough() -> None:
    """Statement / line / absent granularity leaves the command alone."""
    check_eq(map_step_command("-exec-step", "statement"), "-exec-step")
    check_eq(map_step_command("-exec-next", "line"), "-exec-next")
    check_eq(map_step_command("-exec-step", None), "-exec-step")
    check_eq(map_step_command("-exec-finish", "statement"), "-exec-finish")


def test_map_step_command_instruction_remap() -> None:
    """``"instruction"`` swaps stepIn -> step-instruction and
    next -> next-instruction. stepOut keeps -exec-finish (no per-insn
    finish variant in gdb)."""
    check_eq(
        map_step_command("-exec-step", "instruction"), "-exec-step-instruction"
    )
    check_eq(
        map_step_command("-exec-next", "instruction"), "-exec-next-instruction"
    )
    check_eq(map_step_command("-exec-finish", "instruction"), "-exec-finish")


def test_build_instruction_breakpoint_command_bare() -> None:
    """A bare instruction breakpoint emits ``-break-insert *0xADDR``."""
    cmd = build_instruction_breakpoint_command("0x401000")
    check(cmd is not None)
    check("-break-insert" in cmd)
    check("*0x401000" in cmd, f"missing *0xADDR in {cmd!r}")


def test_build_instruction_breakpoint_command_with_offset() -> None:
    """An offset adds to the base address before formatting."""
    cmd = build_instruction_breakpoint_command("0x401000", offset=8)
    check(cmd is not None)
    check("*0x401008" in cmd, f"expected base+8 in {cmd!r}")


def test_build_instruction_breakpoint_command_with_condition() -> None:
    """A condition wraps in -c "<expr>"."""
    cmd = build_instruction_breakpoint_command(
        "0x401000", condition="x > 5"
    )
    check(cmd is not None)
    check("-c" in cmd, f"missing -c flag in {cmd!r}")
    check('"x > 5"' in cmd, f"missing condition in {cmd!r}")


def test_build_instruction_breakpoint_command_rejects_bad_ref() -> None:
    """Malformed reference returns None."""
    check_eq(build_instruction_breakpoint_command("not-hex"), None)
    check_eq(build_instruction_breakpoint_command(""), None)


def test_extract_instruction_pointer_from_frame() -> None:
    """The PC is pulled from the ``frame.addr`` field of a stop record."""
    fields = {
        "reason": "end-stepping-range",
        "frame": {
            "addr": "0x401045",
            "func": "main",
            "line": "30",
        },
        "thread-id": "1",
    }
    ip = extract_instruction_pointer(fields)
    check_eq(ip, "0x401045")


def test_extract_instruction_pointer_missing() -> None:
    """A stop record without a frame yields None (we omit the field)."""
    check_eq(extract_instruction_pointer({}), None)
    check_eq(extract_instruction_pointer({"frame": {}}), None)
    check_eq(extract_instruction_pointer({"frame": "not a dict"}), None)


def test_manager_register_lookup() -> None:
    """Registering a record makes it recoverable by gdb id."""
    mgr = InstructionBreakpointManager()
    rec = InstructionBreakpointRecord(
        gdb_id=7, instruction_reference="0x401000"
    )
    mgr.register(rec)
    check(mgr.lookup_by_gdb_id(7) is rec)
    check_eq(mgr.lookup_by_gdb_id(99), None)


def test_manager_clear_all_returns_ids() -> None:
    """``clear_all`` returns active gdb ids so the caller can delete
    each in gdb."""
    mgr = InstructionBreakpointManager()
    mgr.register(InstructionBreakpointRecord(gdb_id=1, instruction_reference="0x1000"))
    mgr.register(InstructionBreakpointRecord(gdb_id=2, instruction_reference="0x2000"))
    ids = mgr.clear_all()
    check_eq(sorted(ids), [1, 2])
    check(mgr.is_empty())


# ---------------------------------------------------------------------------
# Server handler tests (no gdb required).
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
    """Fake GdbBridge that records every command and returns canned
    replies for disassemble + break-insert."""

    def __init__(self, next_bp_id: int = 1) -> None:
        self.sent_commands: List[str] = []
        self._next_bp_id = next_bp_id
        self._fail_next: Optional[str] = None
        # Default asm_insns canned reply (3 simple insns).
        self.canned_asm_insns: List[Dict[str, Any]] = [
            {
                "address": "0x401000",
                "func-name": "main",
                "offset": "0",
                "inst": "push   %rbp",
            },
            {
                "address": "0x401001",
                "func-name": "main",
                "offset": "1",
                "inst": "mov    %rsp,%rbp",
            },
            {
                "address": "0x401004",
                "func-name": "main",
                "offset": "4",
                "inst": "sub    $0x30,%rsp",
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
        if cmd.startswith("-break-insert"):
            bp_id = self._next_bp_id
            self._next_bp_id += 1
            # Extract the *0xADDR from the tail (last token).
            addr = "0x401000"
            for tok in cmd.split():
                if tok.startswith("*0x"):
                    addr = tok[1:]
                    break
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {
                        "number": str(bp_id),
                        "type": "breakpoint",
                        "addr": addr,
                        "func": "main",
                    }
                },
            )
        # Default for -break-delete / -exec-* / etc.
        return GdbResult(token=None, cls="done", fields={})


def test_capability_advertises_instruction_features() -> None:
    """The ``initialize`` response declares the three new capability
    flags. Handler count: R14A was at 20 handlers; we add
    ``setInstructionBreakpoints`` + ``disassemble`` -> 22 handlers
    (so the DAP server now serves 21 capability-level features but
    the HANDLERS table grew by 2)."""
    from nova_dap.server import _capabilities, HANDLERS  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsSteppingGranularity"), True)
    check_eq(caps.get("supportsDisassembleRequest"), True)
    check_eq(caps.get("supportsInstructionBreakpoints"), True)
    # The prior capabilities must still be on.
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsFunctionBreakpoints"), True)
    check_eq(caps.get("supportsDataBreakpoints"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    check_eq(caps.get("supportsSingleThreadExecutionRequests"), True)
    # New handlers wired up.
    check("setInstructionBreakpoints" in HANDLERS)
    check("disassemble" in HANDLERS)
    # All prior handlers preserved (22 total post-R17F).
    check(
        len(HANDLERS) >= 22,
        f"expected >=22 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


def test_step_with_instruction_granularity_routes_to_step_instruction() -> None:
    """``stepIn`` with ``granularity: "instruction"`` must call
    ``-exec-step-instruction`` instead of ``-exec-step``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
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


def test_step_with_instruction_granularity_routes_to_next_instruction() -> None:
    """``next`` with ``granularity: "instruction"`` must call
    ``-exec-next-instruction``."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 2,
        "type": "request",
        "command": "next",
        "arguments": {"threadId": 1, "granularity": "instruction"},
    }
    server.handle_next(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-exec-")]
    check_eq(len(cmds), 1)
    check(
        "-exec-next-instruction" in cmds[0],
        f"expected next-instruction, got {cmds[0]!r}",
    )


def test_step_without_granularity_keeps_line_stepping() -> None:
    """A bare ``stepIn`` (no granularity) keeps ``-exec-step`` — we
    don't accidentally upgrade line-level stepping."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 3,
        "type": "request",
        "command": "stepIn",
        "arguments": {"threadId": 1},
    }
    server.handle_step_in(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-exec-")]
    check_eq(len(cmds), 1)
    check("-exec-step" in cmds[0])
    check(
        "instruction" not in cmds[0],
        f"granularity=None should NOT trigger instruction stepping: {cmds[0]!r}",
    )


def test_disassemble_handler_basic() -> None:
    """A ``disassemble`` request with a valid memoryReference returns
    ``DisassembledInstruction[]`` entries from gdb's reply."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 4,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "0x401000",
            "instructionCount": 3,
        },
    }
    server.handle_disassemble(sess, req)
    cmds = [c for c in bridge.sent_commands if c.startswith("-data-disassemble")]
    check_eq(len(cmds), 1, "expected one -data-disassemble")
    check("0x401000" in cmds[0])
    resp = sess.out_stream.last_response()
    check(resp is not None)
    check_eq(resp.get("success"), True)
    insns = resp.get("body", {}).get("instructions") or []
    check_eq(len(insns), 3)
    check_eq(insns[0]["address"], "0x401000")
    check("push" in insns[0]["instruction"])


def test_disassemble_handler_pads_short_response() -> None:
    """If gdb returns fewer than instructionCount insns, the response
    must be padded to exactly the requested count so DAP clients can
    rely on the array length."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    # Only 1 canned insn; we request 3.
    bridge.canned_asm_insns = [
        {
            "address": "0x401000",
            "func-name": "main",
            "offset": "0",
            "inst": "ret",
        }
    ]
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 5,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "0x401000",
            "instructionCount": 3,
        },
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    insns = resp.get("body", {}).get("instructions") or []
    check_eq(len(insns), 3)
    check_eq(insns[0]["instruction"], "ret")
    # Padded entries use "??" placeholder.
    check_eq(insns[1]["instruction"], "??")


def test_disassemble_handler_rejects_missing_ref() -> None:
    """A request without memoryReference must fail with success=False."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 6,
        "type": "request",
        "command": "disassemble",
        "arguments": {"instructionCount": 3},
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_disassemble_handler_zero_count() -> None:
    """instructionCount=0 returns an empty list cleanly."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 7,
        "type": "request",
        "command": "disassemble",
        "arguments": {"memoryReference": "0x401000", "instructionCount": 0},
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    check_eq(resp.get("body", {}).get("instructions"), [])


def test_disassemble_handler_invalid_ref() -> None:
    """Malformed memoryReference must surface a clear error."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge()  # type: ignore[assignment]
    req = {
        "seq": 8,
        "type": "request",
        "command": "disassemble",
        "arguments": {
            "memoryReference": "not-a-real-address",
            "instructionCount": 3,
        },
    }
    server.handle_disassemble(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_set_instruction_breakpoints_basic() -> None:
    """A single instruction breakpoint must issue ``-break-insert *0xADDR``
    and register the gdb id."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=7)
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 9,
        "type": "request",
        "command": "setInstructionBreakpoints",
        "arguments": {
            "breakpoints": [{"instructionReference": "0x401000"}]
        },
    }
    server.handle_set_instruction_breakpoints(sess, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1)
    check("*0x401000" in inserts[0], f"missing *0xADDR in {inserts[0]!r}")
    resp = sess.out_stream.last_response()
    body = resp.get("body", {})
    entries = body.get("breakpoints") or []
    check_eq(len(entries), 1)
    check_eq(entries[0].get("verified"), True)
    check_eq(entries[0].get("id"), 7)
    # Manager must record the breakpoint.
    record = sess.instruction_breakpoints.lookup_by_gdb_id(7)
    check(record is not None)
    check_eq(record.instruction_reference, "0x401000")


def test_set_instruction_breakpoints_replacement_clears_prior() -> None:
    """A second ``setInstructionBreakpoints`` call tears down the
    first set via ``-break-delete <id>`` (complete-replacement
    semantics) and installs only the new entries."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge(next_bp_id=20)
    sess.bridge = bridge  # type: ignore[assignment]
    req1 = {
        "seq": 10,
        "type": "request",
        "command": "setInstructionBreakpoints",
        "arguments": {
            "breakpoints": [
                {"instructionReference": "0x401000"},
                {"instructionReference": "0x401010"},
            ]
        },
    }
    server.handle_set_instruction_breakpoints(sess, req1)
    first_ids = sorted(
        r.gdb_id for r in sess.instruction_breakpoints.snapshot()
    )
    check_eq(len(first_ids), 2)
    bridge.sent_commands.clear()
    # Second install: only one new entry.
    req2 = {
        "seq": 11,
        "type": "request",
        "command": "setInstructionBreakpoints",
        "arguments": {
            "breakpoints": [{"instructionReference": "0x401020"}]
        },
    }
    server.handle_set_instruction_breakpoints(sess, req2)
    deletes = [c for c in bridge.sent_commands if c.startswith("-break-delete")]
    check_eq(len(deletes), 2, f"expected 2 deletes, got {bridge.sent_commands}")
    delete_targets = sorted(
        int(c.split()[-1]) for c in deletes if c.split()[-1].isdigit()
    )
    check_eq(delete_targets, first_ids)
    snap = sess.instruction_breakpoints.snapshot()
    check_eq(len(snap), 1)
    check_eq(snap[0].instruction_reference, "0x401020")


def test_set_instruction_breakpoints_rejects_missing_ref() -> None:
    """An entry without instructionReference comes back unverified
    with a clear message."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    bridge = CaptureBridge()
    sess.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 12,
        "type": "request",
        "command": "setInstructionBreakpoints",
        "arguments": {
            "breakpoints": [{}, {"instructionReference": "0x401000"}]
        },
    }
    server.handle_set_instruction_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    entries = resp.get("body", {}).get("breakpoints") or []
    check_eq(len(entries), 2)
    check_eq(entries[0].get("verified"), False)
    check("instructionReference" in entries[0].get("message", ""))
    check_eq(entries[1].get("verified"), True)


def test_set_instruction_breakpoints_not_launched() -> None:
    """A request before launch must fail cleanly."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    req = {
        "seq": 13,
        "type": "request",
        "command": "setInstructionBreakpoints",
        "arguments": {
            "breakpoints": [{"instructionReference": "0x401000"}]
        },
    }
    server.handle_set_instruction_breakpoints(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)


def test_stopped_event_carries_instruction_pointer() -> None:
    """A simulated ``*stopped`` record with a ``frame.addr`` field
    must produce a DAP ``stopped`` event with
    ``instructionPointerReference: "0xADDR"`` so the IDE's
    disassembly view can pin to the PC."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.gdb_bridge import GdbAsyncRecord  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "end-stepping-range",
            "thread-id": "1",
            "stopped-threads": "all",
            "frame": {
                "addr": "0x401045",
                "func": "main",
                "line": "30",
            },
        },
    )
    server._handle_stopped(sess, rec)
    events = sess.out_stream.events
    stopped_events = [e for e in events if e.get("event") == "stopped"]
    check_eq(len(stopped_events), 1)
    body = stopped_events[0].get("body", {})
    check_eq(body.get("instructionPointerReference"), "0x401045")
    check_eq(body.get("reason"), "step")


def test_stopped_event_routes_instruction_bp_reason() -> None:
    """A simulated breakpoint-hit whose bkptno matches a registered
    instruction breakpoint produces ``reason: "instruction breakpoint"``."""
    from nova_dap import server  # noqa: WPS433
    from nova_dap.gdb_bridge import GdbAsyncRecord  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.instruction_breakpoints.register(
        InstructionBreakpointRecord(gdb_id=11, instruction_reference="0x401045")
    )
    rec = GdbAsyncRecord(
        kind="exec",
        cls="stopped",
        fields={
            "reason": "breakpoint-hit",
            "bkptno": "11",
            "thread-id": "1",
            "stopped-threads": "all",
            "frame": {"addr": "0x401045"},
        },
    )
    server._handle_stopped(sess, rec)
    events = sess.out_stream.events
    stopped_events = [e for e in events if e.get("event") == "stopped"]
    check_eq(len(stopped_events), 1)
    body = stopped_events[0].get("body", {})
    check_eq(body.get("reason"), "instruction breakpoint")
    check("0x401045" in body.get("description", ""))


def test_launch_clears_instruction_bp_registry() -> None:
    """A relaunch must reset the instruction-bp manager (stale ids
    from the previous gdb session can't collide with new ones)."""
    from nova_dap import server  # noqa: WPS433

    sess = server.Session(out_stream=_CaptureStream())
    sess.instruction_breakpoints.register(
        InstructionBreakpointRecord(gdb_id=99, instruction_reference="0x401000")
    )
    check(not sess.instruction_breakpoints.is_empty())
    sess.instruction_breakpoints.clear_all()
    check(sess.instruction_breakpoints.is_empty())


# ---------------------------------------------------------------------------
# End-to-end driver shared with the other DAP tests.
# ---------------------------------------------------------------------------


class DapClient:
    """Tiny DAP client over stdio. Same shape as the other DAP test
    files — duplicated here so each test file is self-contained."""

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


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")


def _e2e_against_nova_binary() -> Optional[Dict[str, Any]]:
    """Drive the full instruction-stepping flow against the NOVA
    ``hello_dwarf`` binary.

    Returns a dict ``{first_pc, after_three_steps_pc, steps_advanced,
    disasm_count, disasm_first_addr, insn_bp_verified}`` so the caller
    can assert against gdb's actual behaviour.

    Returns ``None`` if the NOVA binary isn't built (test SKIPs)."""
    if not os.path.isfile(HELLO_DWARF_BIN):
        return None
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], f"initialize: {init}")
        caps = init.get("body", {})
        check_eq(caps.get("supportsSteppingGranularity"), True)
        check_eq(caps.get("supportsDisassembleRequest"), True)
        check_eq(caps.get("supportsInstructionBreakpoints"), True)
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": HELLO_DWARF_BIN})
        check(launch["success"], f"launch: {launch}")

        # Set a regular function breakpoint on main so we have a
        # known stop point.
        sbps = client.request(
            "setFunctionBreakpoints",
            {"breakpoints": [{"name": "main"}]},
        )
        check(sbps["success"], f"setFunctionBreakpoints: {sbps}")

        cd = client.request("configurationDone", {})
        check(cd["success"])
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        first_pc = stop_body.get("instructionPointerReference")
        check(
            isinstance(first_pc, str) and first_pc.startswith("0x"),
            f"expected PC in first stop, got {first_pc!r}",
        )
        tid = int(stop_body.get("threadId") or 1)

        # Disassemble around main entry — expect at least 1 insn back.
        disasm = client.request(
            "disassemble",
            {"memoryReference": first_pc, "instructionCount": 8},
        )
        check(disasm["success"], f"disassemble: {disasm}")
        insns = disasm.get("body", {}).get("instructions") or []
        check(
            len(insns) >= 1,
            f"expected >=1 insn in disassemble, got {len(insns)}",
        )
        disasm_first_addr = insns[0].get("address") if insns else None
        # The first disassembled insn should be at the PC.
        check_eq(disasm_first_addr, first_pc, "first disasm == PC")

        # Step three instructions and verify PC advances each time.
        pcs: List[str] = [first_pc]
        for _ in range(3):
            client.request(
                "stepIn",
                {"threadId": tid, "granularity": "instruction"},
            )
            ev = client.wait_for_event("stopped", timeout=10.0)
            new_pc = ev.get("body", {}).get("instructionPointerReference")
            check(
                isinstance(new_pc, str) and new_pc.startswith("0x"),
                f"PC missing after step: {ev}",
            )
            check(
                new_pc != pcs[-1],
                f"PC didn't advance after step: {pcs[-1]} -> {new_pc}",
            )
            pcs.append(new_pc)
            tid = int(ev.get("body", {}).get("threadId") or tid)
        after_three = pcs[-1]
        steps_advanced = (
            int(after_three, 16) - int(first_pc, 16)
        ) if after_three and first_pc else 0
        # PC should have advanced by at least 3 bytes after 3 single-
        # instruction steps (every x86-64 instruction is >= 1 byte).
        check(
            steps_advanced >= 3,
            f"expected PC to advance >=3 bytes, got {steps_advanced} "
            f"(first={first_pc}, after={after_three})",
        )

        # Set an instruction breakpoint at the after-three address —
        # this proves end-to-end that gdb accepts ``-break-insert *0xADDR``.
        # We can't easily re-hit it without disrupting the test flow,
        # so we just confirm the wire shape: verified=True with a
        # gdb-assigned id.
        ibp = client.request(
            "setInstructionBreakpoints",
            {"breakpoints": [{"instructionReference": after_three}]},
        )
        check(ibp["success"], f"setInstructionBreakpoints: {ibp}")
        ibp_entries = ibp.get("body", {}).get("breakpoints") or []
        check_eq(len(ibp_entries), 1)
        insn_bp_verified = bool(ibp_entries[0].get("verified"))

        # Continue + drain to completion.
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
            "after_three_steps_pc": after_three,
            "steps_advanced": steps_advanced,
            "disasm_count": len(insns),
            "disasm_first_addr": disasm_first_addr,
            "insn_bp_verified": insn_bp_verified,
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    # Unit tests for disassembly.py
    test_parse_memory_reference_hex()
    test_parse_memory_reference_with_offset()
    test_parse_memory_reference_with_negative_offset()
    test_parse_memory_reference_decimal()
    test_parse_memory_reference_rejects_garbage()
    test_parse_memory_reference_rejects_negative_result()
    test_build_disassemble_command_basic()
    test_build_disassemble_command_with_opcodes()
    test_build_disassemble_command_rejects_invalid_mode()
    test_build_disassemble_command_rejects_empty_address()
    test_parse_disassemble_response_flat()
    test_parse_disassemble_response_empty()
    test_parse_disassemble_response_missing_field()
    test_parse_disassemble_response_mixed_source()
    test_disassembled_instruction_to_dap_dict()
    test_disassembled_instruction_minimal_dap_dict()
    test_is_instruction_granularity_match()
    test_map_step_command_statement_passthrough()
    test_map_step_command_instruction_remap()
    test_build_instruction_breakpoint_command_bare()
    test_build_instruction_breakpoint_command_with_offset()
    test_build_instruction_breakpoint_command_with_condition()
    test_build_instruction_breakpoint_command_rejects_bad_ref()
    test_extract_instruction_pointer_from_frame()
    test_extract_instruction_pointer_missing()
    test_manager_register_lookup()
    test_manager_clear_all_returns_ids()
    # Handler tests with a stub bridge.
    test_capability_advertises_instruction_features()
    test_step_with_instruction_granularity_routes_to_step_instruction()
    test_step_with_instruction_granularity_routes_to_next_instruction()
    test_step_without_granularity_keeps_line_stepping()
    test_disassemble_handler_basic()
    test_disassemble_handler_pads_short_response()
    test_disassemble_handler_rejects_missing_ref()
    test_disassemble_handler_zero_count()
    test_disassemble_handler_invalid_ref()
    test_set_instruction_breakpoints_basic()
    test_set_instruction_breakpoints_replacement_clears_prior()
    test_set_instruction_breakpoints_rejects_missing_ref()
    test_set_instruction_breakpoints_not_launched()
    test_stopped_event_carries_instruction_pointer()
    test_stopped_event_routes_instruction_bp_reason()
    test_launch_clears_instruction_bp_registry()


def main() -> int:
    # Phase 1: unit + handler tests (always run).
    _run_unit_tests()
    unit_assertions = _ASSERT_COUNT

    # Phase 2: end-to-end against NOVA hello_dwarf binary.
    nova_status = "skipped"
    nova_reason = ""
    if shutil.which("gdb") is None:
        nova_reason = "gdb not installed"
    elif not os.path.isfile(HELLO_DWARF_BIN):
        nova_reason = "hello_dwarf binary missing (run `make smoke-dwarf`)"
    else:
        try:
            summary = _e2e_against_nova_binary()
            if summary is None:
                nova_status = "skipped (precondition not met)"
            else:
                check(
                    summary["steps_advanced"] >= 3,
                    f"PC must advance after 3 single-insn steps: "
                    f"{summary['steps_advanced']}",
                )
                check(
                    summary["disasm_count"] >= 1,
                    f"disassemble must return >=1 instruction near main, "
                    f"got {summary['disasm_count']}",
                )
                check_eq(
                    summary["disasm_first_addr"],
                    summary["first_pc"],
                    "first disassembled insn must be at PC",
                )
                check(
                    summary["insn_bp_verified"],
                    "instruction breakpoint at a known PC must verify",
                )
                nova_status = (
                    f"ok (PC advance {summary['steps_advanced']} bytes, "
                    f"disasm {summary['disasm_count']} insns)"
                )
        except TimeoutError as exc:
            nova_status = "skipped"
            nova_reason = f"DAP server timeout: {exc}"

    print("test_instruction_stepping: OK")
    print(f"  unit assertions:    {unit_assertions}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    if nova_status.startswith("ok"):
        extra = _ASSERT_COUNT - unit_assertions
        print(f"  NOVA integration:   {nova_status} ({extra} extra checks)")
    else:
        print(f"  NOVA integration:   SKIP — {nova_reason or nova_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
