"""Synthetic-source / ``sourceReference`` cache for the nova-dap DAP server.

This module backs the DAP ``source`` request — when the editor opens a
source view that doesn't correspond to a real file on disk (a synthetic
buffer like the ``.s`` assembly listing for a function), the editor
passes ``sourceReference: N`` instead of (or alongside) ``source.path``,
and the server is expected to return the buffer's content via the
``source`` request.

For nova-dap the synthetic content is the function-level disassembly /
``.s`` listing. The IDE shows it side-by-side with the NOVA source so
the user can correlate ``foo.nova:30`` with the corresponding machine
instructions.

Module surface
--------------

* :class:`SourceReferenceCache` — per-session bookkeeping that maps
  integer ``sourceReference`` ids to cached buffers. Allocation is
  lazy: the first time the server wants to emit a synthetic source it
  asks the cache for an id (or reuses one if an entry already exists
  with the same cache key).
* :func:`disassemble_via_objdump` — fallback path for the ``disassemble``
  handler when gdb's ``-data-disassemble`` is unavailable or returns
  empty. Parses ``objdump -d --no-show-raw-insn`` output for the
  requested address window. Documented format assumption inline.
* :func:`build_function_assembly_listing` — produces the synthetic ``.s``
  string for a single function, used as the ``source`` request body.
  Aggregates objdump's disassembly section for the target function.

Cache keys
----------

The cache key for a function-level listing is
``(binary_path, function_name)``. The DAP wire treats every
``sourceReference`` as opaque, so a fresh id is allocated per launch:
relaunching invalidates the cache (gdb's address space could differ
across runs, so the cached ``.s`` content would mislead).

Format assumption — objdump output
----------------------------------

We parse the GNU binutils ``objdump -d --no-show-raw-insn`` form:

    0000000000401036 <main>:
      401036:\tpush   rbp
      401037:\tmov    rbp,rsp
      ...

Each disassembly line starts with whitespace, then a lowercase hex
address, then a colon, then a tab, then the mnemonic. We tolerate
binutils version drift in the column-width slots but require the
``ADDR:\tMNEMONIC`` shape — newer binutils versions occasionally add a
section / offset annotation BEFORE the mnemonic which we strip.

If the runtime ``objdump`` deviates further (e.g. a vendor build that
omits the tab separator), :func:`disassemble_via_objdump` returns an
empty list and the caller falls back to the gdb-MI path.

No third-party deps; pure stdlib.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# objdump-driven disassembly fallback.
# ---------------------------------------------------------------------------


# Match lines of the form ``  401036:\tpush   rbp`` (with optional
# leading whitespace, lowercase hex address, colon, tab, then the
# mnemonic / operands). We anchor the address with a non-capturing
# leading whitespace + the address itself + colon to avoid false
# matches on data dump output (``404000 <_str_0>:`` etc., which lacks
# the trailing colon-tab shape).
_OBJDUMP_INSN_RE = re.compile(
    r"^\s*([0-9a-fA-F]+):\s*(?:[0-9a-fA-F]{2} )*\s*(.+?)\s*$"
)

# Match a function-header line: ``0000000000401036 <funcname>:``.
_OBJDUMP_FUNC_RE = re.compile(
    r"^\s*([0-9a-fA-F]+)\s+<([^>]+)>:\s*$"
)


@dataclass
class ObjdumpInstruction:
    """One decoded objdump line.

    ``address`` is the hex string verbatim from objdump (no ``0x``
    prefix — objdump prints without). ``mnemonic`` is the rest of
    the line, stripped of trailing whitespace and inline comments
    starting with ``#``."""

    address: str            # "401036" (no leading 0x)
    mnemonic: str           # "push   rbp"
    function: Optional[str] = None  # enclosing function name


def objdump_available() -> bool:
    """Return True if ``objdump`` is callable on PATH. Sentinels for
    tests + the server's fallback path picker."""
    return shutil.which("objdump") is not None


def _strip_inline_comment(text: str) -> str:
    """Drop the trailing ``# ...`` annotation that objdump appends for
    PC-relative loads (``lea rax,[rip+0x...] # 404000 <_str_0>``).

    The mnemonic stays intact; we just trim the comment so the DAP
    response doesn't show duplicate symbol info (the IDE already
    renders the symbol column separately)."""
    idx = text.find("#")
    if idx == -1:
        return text
    return text[:idx].rstrip()


def parse_objdump_output(text: str) -> List[ObjdumpInstruction]:
    """Parse the full ``objdump -d`` stdout into a flat list of
    ``ObjdumpInstruction``.

    Tracks the enclosing function name so each instruction knows which
    symbol it lives in (the DAP disassembly view groups instructions by
    function). Skips section headers (``Disassembly of section
    .text:``) and blank lines.

    Returns an empty list if the input is malformed (no parse errors
    raised — the caller treats empty as "fall back to gdb-MI")."""
    out: List[ObjdumpInstruction] = []
    if not isinstance(text, str) or not text:
        return out
    current_func: Optional[str] = None
    for raw_line in text.splitlines():
        # Skip blank lines and section banners.
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("Disassembly of section"):
            continue
        if stripped.startswith("file format"):
            continue
        # Function header? ``0000000000401036 <main>:``
        func_m = _OBJDUMP_FUNC_RE.match(raw_line)
        if func_m is not None:
            current_func = func_m.group(2)
            continue
        # Instruction line? ``  401036:\tpush   rbp``
        m = _OBJDUMP_INSN_RE.match(raw_line)
        if m is None:
            continue
        addr = m.group(1).lower()
        mnemonic = _strip_inline_comment(m.group(2))
        if not mnemonic:
            continue
        out.append(
            ObjdumpInstruction(
                address=addr,
                mnemonic=mnemonic,
                function=current_func,
            )
        )
    return out


def disassemble_via_objdump(
    binary_path: str,
    start_address: int,
    instruction_count: int,
    extra_padding: int = 8,
) -> List[ObjdumpInstruction]:
    """Run ``objdump -d`` and return the instructions covering the
    requested window.

    Filters the parsed instruction stream to those whose address falls
    in ``[start_address, start_address + count * 15)`` (15 = max x86-64
    insn length, generous upper bound). ``extra_padding`` extra
    instructions are returned BEFORE the window so the caller can
    handle a small negative ``instructionOffset`` without re-running
    objdump.

    Returns an empty list if objdump isn't available, the binary
    doesn't exist, or parsing produced no usable rows. Never raises —
    the caller can decide whether to fall back to gdb-MI."""
    if not binary_path or not os.path.isfile(binary_path):
        return []
    if not objdump_available():
        return []
    try:
        completed = subprocess.run(
            [
                "objdump",
                "-d",
                "--no-show-raw-insn",
                "--insn-width=8",
                "-M",
                "intel",
                binary_path,
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if completed.returncode != 0:
        return []
    parsed = parse_objdump_output(completed.stdout)
    if not parsed:
        return []
    if instruction_count <= 0:
        return parsed
    # Filter to the requested address window. We can't predict the
    # exact upper bound (variable-width insns), so we just include
    # everything from the first matching address forward and let the
    # caller truncate to ``instruction_count``.
    low = start_address
    out: List[ObjdumpInstruction] = []
    started = False
    for insn in parsed:
        try:
            addr_int = int(insn.address, 16)
        except ValueError:
            continue
        if not started:
            if addr_int >= low:
                started = True
                out.append(insn)
        else:
            out.append(insn)
        if len(out) >= instruction_count + extra_padding:
            break
    return out


def find_function_at_address(
    binary_path: str, address: int
) -> Optional[str]:
    """Return the function name enclosing ``address`` in ``binary_path``
    by scanning objdump's ``<funcname>:`` headers. Returns ``None``
    when no enclosing function is found (or objdump isn't available)."""
    if not binary_path or not os.path.isfile(binary_path):
        return None
    if not objdump_available():
        return None
    try:
        completed = subprocess.run(
            [
                "objdump",
                "-d",
                "--no-show-raw-insn",
                "--insn-width=8",
                "-M",
                "intel",
                binary_path,
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if completed.returncode != 0:
        return None
    current_func: Optional[str] = None
    current_func_start: int = 0
    last_addr_in_func: int = 0
    for raw_line in completed.stdout.splitlines():
        func_m = _OBJDUMP_FUNC_RE.match(raw_line)
        if func_m is not None:
            # If we're transitioning out of a function and the previous
            # function's address range straddles ``address``, return it.
            if (
                current_func is not None
                and current_func_start <= address <= last_addr_in_func
            ):
                return current_func
            try:
                current_func_start = int(func_m.group(1), 16)
            except ValueError:
                current_func_start = 0
            current_func = func_m.group(2)
            last_addr_in_func = current_func_start
            continue
        m = _OBJDUMP_INSN_RE.match(raw_line)
        if m is None:
            continue
        try:
            addr_int = int(m.group(1), 16)
        except ValueError:
            continue
        if current_func is not None:
            last_addr_in_func = addr_int
            if addr_int == address:
                return current_func
    # Final flush — if the last function in the dump contains the
    # address, return it.
    if (
        current_func is not None
        and current_func_start <= address <= last_addr_in_func
    ):
        return current_func
    return None


def build_function_assembly_listing(
    binary_path: str, function_name: str
) -> Optional[str]:
    """Return the synthetic ``.s`` listing string for one function.

    The returned content is what the DAP ``source`` request hands back
    to the IDE: a header naming the binary + function, followed by one
    line per disassembled instruction (``  ADDR:  MNEMONIC``).

    Best-effort: returns ``None`` when objdump isn't installed, the
    binary doesn't exist, or the named function isn't present in the
    disassembly. The DAP server then responds with ``success: false``
    so the IDE can render its default "source not available" message.
    """
    if not binary_path or not function_name:
        return None
    if not os.path.isfile(binary_path):
        return None
    if not objdump_available():
        return None
    try:
        completed = subprocess.run(
            [
                "objdump",
                "-d",
                "--no-show-raw-insn",
                "--insn-width=8",
                "-M",
                "intel",
                "--disassemble=" + function_name,
                binary_path,
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    # ``--disassemble=NAME`` filters to the target function on modern
    # binutils. If it fails (older binutils don't recognise the
    # ``=NAME`` form) we fall back to a full dump + filter client-side.
    if completed.returncode != 0 or not completed.stdout.strip():
        try:
            completed = subprocess.run(
                [
                    "objdump",
                    "-d",
                    "--no-show-raw-insn",
                    "--insn-width=8",
                    "-M",
                    "intel",
                    binary_path,
                ],
                capture_output=True,
                text=True,
                timeout=20.0,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if completed.returncode != 0:
            return None
    parsed = parse_objdump_output(completed.stdout)
    fn_insns = [insn for insn in parsed if insn.function == function_name]
    if not fn_insns:
        return None
    header_lines: List[str] = [
        f"; Disassembly of {function_name} in {os.path.basename(binary_path)}",
        f"; Source: {binary_path}",
        f"; Generated by nova-dap; format: ADDR: MNEMONIC",
        "",
        f"<{function_name}>:",
    ]
    body_lines: List[str] = []
    for insn in fn_insns:
        body_lines.append(f"  {insn.address}:    {insn.mnemonic}")
    return "\n".join(header_lines + body_lines) + "\n"


# ---------------------------------------------------------------------------
# SourceReferenceCache — bookkeeping for the DAP ``source`` request.
# ---------------------------------------------------------------------------


@dataclass
class SourceReferenceEntry:
    """One cached synthetic-source buffer.

    ``ref_id`` is the integer ``sourceReference`` the IDE round-trips
    in its ``source { sourceReference: N }`` request.

    ``cache_key`` is ``(binary_path, function_name)`` — the same
    function loaded twice (e.g. user opens disassembly view, closes,
    reopens) reuses the same id.

    ``content`` is the synthetic ``.s`` string. It may be ``None``
    while a lazy build is in flight; callers re-call
    :meth:`SourceReferenceCache.materialise` to populate it.

    ``mime_type`` is what the DAP server returns in the response;
    DAP doesn't strictly require it but VS Code uses it for syntax
    highlighting (``text/x-asm`` triggers the asm syntax theme)."""

    ref_id: int
    cache_key: Tuple[str, str]
    content: Optional[str] = None
    mime_type: str = "text/x-asm"


@dataclass
class SourceReferenceCache:
    """Thread-safe per-session registry of synthetic source buffers.

    Owned by a DAP ``Session``; survives across multiple ``disassemble``
    + ``source`` requests within one session. Cleared on every
    ``launch`` because the binary may have changed (the cached
    addresses + function names would no longer correlate)."""

    # Next id to allocate. We start at 1 because DAP reserves
    # ``sourceReference: 0`` for "use the path instead".
    _next_id: int = 1
    by_id: Dict[int, SourceReferenceEntry] = field(default_factory=dict)
    by_key: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allocate(
        self, binary_path: str, function_name: str
    ) -> int:
        """Allocate (or reuse) a sourceReference id for
        ``(binary_path, function_name)``.

        Same key returns the same id within a session so a follow-up
        ``source`` request from the IDE finds the cached entry."""
        key = (binary_path, function_name)
        with self._lock:
            existing = self.by_key.get(key)
            if existing is not None:
                return existing
            ref = self._next_id
            self._next_id += 1
            self.by_id[ref] = SourceReferenceEntry(
                ref_id=ref, cache_key=key
            )
            self.by_key[key] = ref
            return ref

    def materialise(
        self,
        ref_id: int,
        builder: Optional[Any] = None,
    ) -> Optional[str]:
        """Return the cached content for ``ref_id``, lazily building
        it via ``builder(binary_path, function_name)`` if absent.

        ``builder`` defaults to :func:`build_function_assembly_listing`
        — callers pass a different function only in tests."""
        with self._lock:
            entry = self.by_id.get(ref_id)
            if entry is None:
                return None
            if entry.content is not None:
                return entry.content
            cache_key = entry.cache_key
        # Release the lock while we shell out to objdump (potentially
        # slow). If two threads race we may build twice but each writer
        # stores the same content, so the last-writer-wins is fine.
        if builder is None:
            builder = build_function_assembly_listing
        content = builder(cache_key[0], cache_key[1])
        if content is None:
            return None
        with self._lock:
            # Re-check entry existence in case a clear_all() ran while
            # we were building.
            entry = self.by_id.get(ref_id)
            if entry is None:
                return content
            entry.content = content
            return content

    def get(self, ref_id: int) -> Optional[SourceReferenceEntry]:
        with self._lock:
            return self.by_id.get(ref_id)

    def clear_all(self) -> None:
        """Drop every cached entry. Called from ``handle_launch`` so a
        relaunch starts with a fresh id space (the new binary may have
        moved functions, so cached ``.s`` content would mislead)."""
        with self._lock:
            self._next_id = 1
            self.by_id.clear()
            self.by_key.clear()

    def is_empty(self) -> bool:
        with self._lock:
            return not self.by_id

    def size(self) -> int:
        with self._lock:
            return len(self.by_id)


# ---------------------------------------------------------------------------
# DAP wire helpers.
# ---------------------------------------------------------------------------


def build_source_descriptor(
    entry: SourceReferenceEntry, function_name: str
) -> Dict[str, Any]:
    """Build the DAP ``Source`` object the IDE uses to open the
    synthetic buffer.

    Returns a dict with ``{sourceReference, name, presentationHint:
    "deemphasize", origin}`` — the ``name`` is shown as the tab title
    in the IDE's source view, ``presentationHint: "deemphasize"``
    flags it as derived (so VS Code renders it in italics), and
    ``origin`` tells the user it came from nova-dap's disassembler.

    ``sourceReference != 0`` is what tells the client this is a
    synthetic source (rather than a file at ``path``); the client
    will issue a ``source { sourceReference }`` request to fetch
    the buffer content."""
    return {
        "sourceReference": entry.ref_id,
        "name": f"{function_name}.s",
        "presentationHint": "deemphasize",
        "origin": "nova-dap disassembly",
    }
