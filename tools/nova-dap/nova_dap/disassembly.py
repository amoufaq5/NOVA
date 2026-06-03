"""Instruction-level disassembly + instruction breakpoints for the
NOVA DAP server.

This module backs the 21st DAP capability: **instruction-level
stepping**. A DAP client can:

  * step a single machine instruction (``stepIn`` / ``next`` /
    ``stepOut`` with ``granularity: "instruction"``),
  * disassemble a range of memory around a program counter
    (``disassemble`` request -> ``DisassembledInstruction[]``),
  * set breakpoints at machine addresses (``setInstructionBreakpoints``
    -> gdb's ``-break-insert *0xADDR``).

All three delegate to gdb-MI commands; no architecture-specific
knowledge lives here. gdb already understands the host's instruction
set (x86-64 / ARM64 / etc.) and emits the disassembled mnemonic +
bytes.

gdb-MI surface used
-------------------

* ``-data-disassemble -s <start> -e <end> -- 0`` — disassemble the
  range ``[start, end)`` mixing source lines with assembly. Result
  shape: ``^done,asm_insns=[{address, func-name, offset, inst}, ...]``
  for pure-asm mode (``-- 0``), or
  ``^done,asm_insns=[src_and_asm_line={line, file, fullname,
  line_asm_insn=[{...}, ...]}, ...]`` for mixed mode (``-- 4``). We
  default to mode 0 (asm only) and surface the source mapping via
  separate gdb queries; mode 4 is harder to flatten back into a
  DAP-shaped flat array.

* ``-exec-step-instruction [--thread N]`` — step exactly one
  instruction, stepping INTO function calls. Same as ``stepi`` in
  CLI gdb.

* ``-exec-next-instruction [--thread N]`` — step one instruction but
  step OVER function calls (i.e. if the current insn is ``call X``,
  run all of X and stop on the next insn after the call returns).
  Same as ``nexti`` in CLI gdb.

* ``-break-insert *0x<ADDR>`` — set a breakpoint at a specific
  machine address. The ``*`` prefix tells gdb's parser this is an
  address, not a symbol.

DAP -> gdb mapping
------------------

| DAP wire                                | gdb-MI command                            |
| --------------------------------------- | ----------------------------------------- |
| ``next  {granularity: "instruction"}``  | ``-exec-next-instruction``                |
| ``stepIn {granularity: "instruction"}`` | ``-exec-step-instruction``                |
| ``stepOut {granularity: "instruction"}``| ``-exec-finish`` (no per-insn stepOut)    |
| ``disassemble {memoryReference, ...}``  | ``-data-disassemble -s START -e END -- 0``|
| ``setInstructionBreakpoints {breakpoints: [{instructionReference: "0xADDR", offset?}]}`` | ``-break-insert *0xADDR`` per entry      |

Module surface
--------------

* :func:`parse_memory_reference` — normalise a DAP ``memoryReference``
  + ``instructionOffset`` into a hex string gdb can consume.
* :func:`build_disassemble_command` — compose the MI command for a
  range request.
* :func:`parse_disassemble_response` — turn gdb's ``asm_insns=[...]``
  reply into a list of LSP ``DisassembledInstruction`` records.
* :func:`build_instruction_breakpoint_command` — compose
  ``-break-insert *0xADDR``.
* :class:`DisassembledInstruction` — typed value for a single
  decoded insn.
* :class:`InstructionBreakpointRecord` /
  :class:`InstructionBreakpointManager` — bookkeeping the
  ``Session`` holds.

No third-party deps; pure stdlib.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Memory reference parsing.
# ---------------------------------------------------------------------------


def _parse_hex_address(text: str) -> Optional[int]:
    """Parse a hex / decimal address string to an int. Accepts ``0xABCD``,
    ``ABCD``, or plain decimal. Returns ``None`` for empty / malformed
    input."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None
    try:
        # ``int(s, 0)`` honours the ``0x`` / ``0o`` / ``0b`` prefixes;
        # if none is present it parses as decimal.
        return int(s, 0)
    except ValueError:
        # Fall back: treat as bare hex if it's all hex digits.
        try:
            return int(s, 16)
        except ValueError:
            return None


def parse_memory_reference(
    memory_reference: str,
    instruction_offset: int = 0,
) -> Optional[str]:
    """Resolve a DAP ``memoryReference`` + ``instructionOffset`` into
    a hex address string gdb's ``-data-disassemble -s ...`` accepts.

    The DAP spec says ``memoryReference`` is opaque to the client —
    it's whatever the server emitted as ``instructionPointerReference``
    in a prior ``stopped`` event. We emit ``"0x..."`` strings, so
    parsing is just hex normalisation.

    ``instruction_offset`` is the offset (in instructions) from
    ``memory_reference``. We can't know the variable instruction
    sizes a priori, so we approximate by 4 bytes per instruction
    (a reasonable lower bound on x86-64 and the fixed size on ARM64).
    Callers that want exact alignment should issue follow-up
    disassemble queries rather than assume our offset arithmetic is
    perfect.

    Returns a string like ``"0x4010ab"`` on success or ``None`` for
    malformed input. The caller embeds the result in the gdb
    ``-s <addr>`` argument."""
    base = _parse_hex_address(memory_reference)
    if base is None:
        return None
    # Treat each "instruction" as 4 bytes. This is exact on ARM64
    # (always 4 bytes) and a conservative lower bound on x86-64
    # (where insns range 1..15 bytes; gdb will round up to the
    # nearest instruction boundary anyway).
    if instruction_offset:
        base = base + (instruction_offset * 4)
    if base < 0:
        return None
    return f"0x{base:x}"


def build_disassemble_command(
    start_address: str,
    end_address: str,
    mode: int = 0,
) -> Optional[str]:
    """Compose ``-data-disassemble -s START -e END -- MODE``.

    ``mode`` is one of gdb's documented modes:

    * 0 -> disassembly only (no source). Result is a flat
      ``asm_insns=[{address, func-name, offset, inst, opcodes?}, ...]``.
    * 1 -> source + disassembly (deprecated, kept for back-compat).
    * 2 -> disassembly with raw opcode bytes.
    * 3 -> source + disassembly with raw opcode bytes.
    * 4 -> mixed source + asm (modern; nested
      ``src_and_asm_line=...`` records).
    * 5 -> mixed source + asm with raw opcode bytes.

    We default to mode 0 for the DAP path because the resulting
    flat array is easier to map to the
    ``DisassembledInstruction[]`` wire shape. The caller may pass
    ``mode=2`` to include opcode bytes (we surface them as
    ``instructionBytes`` in the DAP response).

    Returns ``None`` if either address is empty."""
    if not isinstance(start_address, str) or not start_address.strip():
        return None
    if not isinstance(end_address, str) or not end_address.strip():
        return None
    if mode not in (0, 1, 2, 3, 4, 5):
        return None
    return (
        f"-data-disassemble -s {start_address.strip()} "
        f"-e {end_address.strip()} -- {mode}"
    )


# ---------------------------------------------------------------------------
# Response parsing.
# ---------------------------------------------------------------------------


@dataclass
class DisassembledInstruction:
    """The decoded form of one entry in a gdb disassemble reply.

    Mirrors the DAP ``DisassembledInstruction`` wire shape (modulo
    naming: DAP uses camelCase, this dataclass uses snake_case).
    Conversion happens in :func:`to_dap_dict`."""

    address: str                          # "0x4010ab"
    instruction: str                      # "mov    %rax,-0x8(%rbp)"
    instruction_bytes: Optional[str] = None  # "48 89 45 f8"
    symbol: Optional[str] = None             # "main"
    location_path: Optional[str] = None      # absolute file path
    location_name: Optional[str] = None      # short file name
    line: Optional[int] = None               # 1-based source line

    def to_dap_dict(self) -> Dict[str, Any]:
        """Convert to the DAP wire shape (camelCase).

        Required fields: ``address``, ``instruction``. Optional:
        ``instructionBytes`` (raw bytes as space-separated hex), ``symbol``
        (function name), and ``location`` + ``line`` for source
        mapping. Per the DAP spec, the IDE renders ``address`` in a
        gutter column, ``instruction`` as the mnemonic, and uses
        ``location`` + ``line`` to anchor a source-line jump."""
        out: Dict[str, Any] = {
            "address": self.address,
            "instruction": self.instruction,
        }
        if self.instruction_bytes:
            out["instructionBytes"] = self.instruction_bytes
        if self.symbol:
            out["symbol"] = self.symbol
        if self.location_path:
            loc: Dict[str, Any] = {"path": self.location_path}
            if self.location_name:
                loc["name"] = self.location_name
            else:
                # Best-effort basename so the IDE can render a tab
                # title even if the caller didn't pre-split.
                loc["name"] = self.location_path.rsplit("/", 1)[-1]
            out["location"] = loc
        if self.line is not None:
            out["line"] = self.line
        return out


def _normalise_address(addr: Any) -> Optional[str]:
    """Canonicalise an MI ``address`` field. gdb emits ``"0x...."``
    strings with a leading ``0x`` and variable width zero-padding;
    we just check it's a string and return verbatim."""
    if isinstance(addr, str) and addr.startswith("0x"):
        return addr
    if isinstance(addr, int):
        return f"0x{addr:x}"
    return None


def _parse_line(raw: Any) -> Optional[int]:
    """Parse a ``line=""`` field to an int, tolerating both string and
    int representations."""
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isdigit():
        v = int(raw)
        return v if v > 0 else None
    return None


def _decode_one_asm_insn(entry: Dict[str, Any]) -> Optional[DisassembledInstruction]:
    """Decode a single ``{address, inst, ...}`` MI tuple."""
    if not isinstance(entry, dict):
        return None
    addr = _normalise_address(entry.get("address"))
    inst = entry.get("inst")
    if not addr or not isinstance(inst, str):
        return None
    out = DisassembledInstruction(address=addr, instruction=inst)
    sym = entry.get("func-name")
    if isinstance(sym, str) and sym:
        out.symbol = sym
    opcodes = entry.get("opcodes")
    if isinstance(opcodes, str) and opcodes:
        out.instruction_bytes = opcodes
    return out


def parse_disassemble_response(
    result_fields: Dict[str, Any],
) -> List[DisassembledInstruction]:
    """Extract a flat ``DisassembledInstruction[]`` from a gdb
    ``-data-disassemble`` reply.

    Handles BOTH mode 0/2 (flat asm-only) and mode 4/5 (mixed
    source + asm) by flattening the nested
    ``src_and_asm_line`` records back to a single linear list.
    For mode 4/5 entries we propagate the source ``line`` /
    ``file`` / ``fullname`` from the wrapping
    ``src_and_asm_line`` so each instruction carries its source
    mapping (matching the DAP ``location`` + ``line`` fields).

    Returns an empty list for an unparseable reply rather than
    raising — the caller treats that as "nothing to show" rather
    than a hard error."""
    raw = result_fields.get("asm_insns")
    if not isinstance(raw, list):
        return []
    out: List[DisassembledInstruction] = []
    for item in raw:
        # Mode 0/2: ``{address, inst, ...}`` directly.
        # Mode 4/5: ``{src_and_asm_line={line, file, fullname,
        # line_asm_insn=[{address, inst, ...}, ...]}}``. The MI list
        # parser wraps each ``name=value`` list element in a single-
        # key dict so we get ``{"src_and_asm_line": {...}}`` here.
        if not isinstance(item, dict):
            continue
        # Flat-insn entry?
        if "inst" in item and "address" in item:
            decoded = _decode_one_asm_insn(item)
            if decoded is not None:
                out.append(decoded)
            continue
        # Mixed wrapper?
        wrapper = item.get("src_and_asm_line")
        if not isinstance(wrapper, dict):
            # Some gdb versions hand us bare ``src_and_asm_line=...``
            # tuples without the wrapping list-element dict; try
            # treating the whole item as the wrapper.
            if "line_asm_insn" in item or "line" in item:
                wrapper = item
            else:
                continue
        line = _parse_line(wrapper.get("line"))
        fullname = wrapper.get("fullname")
        file_name = wrapper.get("file")
        path = fullname if isinstance(fullname, str) and fullname else None
        short = file_name if isinstance(file_name, str) and file_name else None
        nested = wrapper.get("line_asm_insn")
        if not isinstance(nested, list):
            continue
        for sub in nested:
            if not isinstance(sub, dict):
                continue
            decoded = _decode_one_asm_insn(sub)
            if decoded is None:
                continue
            if line is not None:
                decoded.line = line
            if path is not None:
                decoded.location_path = path
            if short is not None:
                decoded.location_name = short
            out.append(decoded)
    return out


# ---------------------------------------------------------------------------
# Instruction breakpoints (set by machine address).
# ---------------------------------------------------------------------------


def build_instruction_breakpoint_command(
    instruction_reference: str,
    offset: int = 0,
    condition: Optional[str] = None,
) -> Optional[str]:
    """Compose ``-break-insert *0xADDR`` (optionally with ``-c``).

    gdb's ``*`` prefix on the location selects "this exact address"
    rather than symbol resolution. ``offset`` is a DAP-side byte
    offset from ``instruction_reference``; the IDE uses it when the
    user clicks a non-anchor instruction in the disassembly view.

    Returns ``None`` for a malformed reference."""
    base = _parse_hex_address(instruction_reference)
    if base is None:
        return None
    final_addr = base + int(offset or 0)
    if final_addr < 0:
        return None
    parts = ["-break-insert"]
    if isinstance(condition, str) and condition.strip():
        # MI cstring escape for the condition.
        escaped = condition.replace("\\", "\\\\").replace('"', '\\"')
        parts.extend(["-c", f'"{escaped}"'])
    parts.append(f"*0x{final_addr:x}")
    return " ".join(parts)


@dataclass
class InstructionBreakpointRecord:
    """Bookkeeping for one active instruction breakpoint.

    ``gdb_id`` is the integer breakpoint number gdb assigned.
    ``instruction_reference`` is the original DAP-side address string
    (we keep it so a follow-up ``setInstructionBreakpoints`` call
    can match by address and re-use the gdb id where possible).
    ``offset`` is the DAP-side byte offset; ``resolved_address`` is
    the actual ``base + offset`` we passed to gdb."""

    gdb_id: int
    instruction_reference: str
    offset: int = 0
    resolved_address: Optional[int] = None
    condition: Optional[str] = None


@dataclass
class InstructionBreakpointManager:
    """Thread-safe registry of active instruction breakpoints.

    Owned by a DAP ``Session``; survives across multiple
    ``setInstructionBreakpoints`` requests within one session.
    Cleared on every ``launch`` (gdb forgets every breakpoint when
    the inferior restarts)."""

    by_gdb_id: Dict[int, InstructionBreakpointRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, record: InstructionBreakpointRecord) -> None:
        with self._lock:
            self.by_gdb_id[record.gdb_id] = record

    def lookup_by_gdb_id(
        self, gdb_id: int
    ) -> Optional[InstructionBreakpointRecord]:
        with self._lock:
            return self.by_gdb_id.get(gdb_id)

    def clear_all(self) -> List[int]:
        """Forget every registered instruction breakpoint and return
        the gdb ids that were active. The caller is expected to
        ``-break-delete <id>`` each so gdb's view stays in sync."""
        with self._lock:
            ids = list(self.by_gdb_id.keys())
            self.by_gdb_id.clear()
            return ids

    def snapshot(self) -> List[InstructionBreakpointRecord]:
        with self._lock:
            return list(self.by_gdb_id.values())

    def is_empty(self) -> bool:
        with self._lock:
            return not self.by_gdb_id


# ---------------------------------------------------------------------------
# Stepping granularity helpers.
# ---------------------------------------------------------------------------


# DAP ``granularity`` values that mean "step a single machine
# instruction" vs the default "step one statement / line".
_INSTRUCTION_GRANULARITY = "instruction"


def is_instruction_granularity(granularity: Optional[str]) -> bool:
    """Return True if the DAP request's ``granularity`` argument
    selects instruction-level stepping. ``"statement"`` (default)
    and ``"line"`` both return False; only ``"instruction"`` flips
    the routing to ``-exec-step-instruction`` / ``-exec-next-instruction``."""
    return isinstance(granularity, str) and granularity == _INSTRUCTION_GRANULARITY


def map_step_command(base_command: str, granularity: Optional[str]) -> str:
    """Translate the base DAP step command into its instruction-level
    equivalent when ``granularity == "instruction"``.

    ``base_command`` is the gdb-MI command used for line-level
    stepping (``-exec-step`` / ``-exec-next`` / ``-exec-finish``).
    Returns the instruction-level variant where one exists, otherwise
    the original command unchanged.

    gdb's stepOut equivalent (``-exec-finish``) has no per-instruction
    variant — you can't "finish out of" a single instruction the way
    you can finish out of a function — so we keep ``-exec-finish``
    for stepOut even under instruction granularity. The DAP spec
    permits this: ``granularity`` is a hint, not a hard requirement."""
    if not is_instruction_granularity(granularity):
        return base_command
    mapping = {
        "-exec-step": "-exec-step-instruction",
        "-exec-next": "-exec-next-instruction",
        # ``-exec-finish`` has no per-insn variant; leave it alone.
        "-exec-finish": "-exec-finish",
    }
    return mapping.get(base_command, base_command)


# ---------------------------------------------------------------------------
# instructionPointerReference helper for stopped events.
# ---------------------------------------------------------------------------


def extract_instruction_pointer(fields: Dict[str, Any]) -> Optional[str]:
    """Pull the program counter out of a gdb ``*stopped`` async record.

    gdb ships the PC in ``frame={addr="0x...", ...}``. We return the
    address string verbatim so it can be embedded in the DAP
    ``instructionPointerReference`` field of the ``stopped`` event.
    The DAP client then uses that reference as the
    ``memoryReference`` for follow-up ``disassemble`` requests."""
    frame = fields.get("frame")
    if isinstance(frame, dict):
        addr = frame.get("addr")
        if isinstance(addr, str) and addr.startswith("0x"):
            return addr
    return None
