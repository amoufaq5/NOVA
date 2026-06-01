"""Expression evaluation for the NOVA DAP server.

Wraps gdb's ``-data-evaluate-expression`` MI command and decodes the
returned ``value="..."`` string into a (display_text, type) pair the
DAP wire protocol can carry verbatim.

gdb's ``-data-evaluate-expression`` is a thin wrapper around the
internal print routine, so the ``value`` field is exactly what
``print expr`` would produce in CLI mode. The decoder below recognises
the four shapes that matter for NOVA debugging:

* ``int``: a bare decimal / hex / octal literal (``42``, ``-3``,
  ``0x1A``). Pointer-tagged NOVA integers go through this path because
  gdb itself doesn't see the smart-op tag — the inferior already
  untagged the value before passing it to gdb's expression evaluator.
* ``str``: a quoted C-string, optionally prefixed with the pointer
  address (``0x7fff... "hello"``). NOVA strings are stored as
  null-terminated arrays in memory, so ``(char*)nova_str`` and bare
  ``char*`` locals both come back in this shape.
* ``char``: ``104 'h'`` — gdb's dual-form for a single char. We
  surface the printable form.
* ``list``: NOVA-runtime lists from R6A's smart-op infrastructure are
  pointer values; the decoder catches a leading ``0x`` with no string
  after it and labels the result ``ptr``. Full list traversal would
  require a debugger-side reader of the list header, which is a
  future R-round (see README ``What does NOT work yet``).
* ``raw``: anything else — surfaced as-is so the user can still see
  whatever gdb said.

The intent is "lossless surface text for the DAP variables panel"
rather than perfect NOVA-type recovery. A user typing ``x + y`` in
the watch window sees the same ``"3"`` they'd see from gdb's
``print`` plus a ``type`` hint the DAP UI can colourize.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Value classification.
# ---------------------------------------------------------------------------


# gdb's printed form for an int: optional minus, then a decimal /
# hex / octal literal. We use a strict-ish match — gdb will sometimes
# append ``\000`` or trailing whitespace, so we ``.strip()`` first.
_INT_RE = re.compile(r"^-?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)$")
# Char display: ``<int> '<char>'`` — gdb prints the numeric value
# followed by the quoted character. We capture both halves.
_CHAR_RE = re.compile(r"^(-?\d+)\s+'(.+)'$")
# Pointer with string preview: ``0x... "<text>"``. The text portion
# may contain escaped quotes — see ``_extract_cstring`` for the
# escape-aware splitter we use instead of a regex.
_PTR_PREFIX_RE = re.compile(r"^(0[xX][0-9a-fA-F]+)\s*")
# Bare pointer with no string: ``0x...`` (NOVA list / object
# pointer that gdb couldn't decode further).
_BARE_PTR_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")


@dataclass
class DecodedValue:
    """The decoded form of a gdb-MI ``value="..."`` string.

    ``display`` is what the DAP client should render; ``type`` is the
    classification tag we surface alongside it (``int`` / ``str`` /
    ``char`` / ``ptr`` / ``raw``). ``raw`` is reserved for values we
    can't classify — the display string is verbatim gdb output."""

    display: str
    type: str


def _extract_cstring(rest: str) -> Optional[Tuple[str, str]]:
    """Parse a C-string literal at the start of ``rest``. Returns
    ``(text, leftover)`` on success or ``None`` if the rest doesn't
    start with a ``"``.

    Handles ``\\\\`` ``\\"`` ``\\n`` ``\\t`` ``\\r`` ``\\0`` escapes.
    Anything else is left as-is."""
    if not rest.startswith('"'):
        return None
    out = []
    i = 1
    n = len(rest)
    while i < n:
        ch = rest[i]
        if ch == '"':
            return ("".join(out), rest[i + 1 :])
        if ch == "\\" and i + 1 < n:
            nxt = rest[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "0":
                out.append("\0")
            elif nxt in ('"', "\\", "'"):
                out.append(nxt)
            else:
                # Unknown escape — preserve literally so we don't
                # lose information.
                out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    # No closing quote — return whatever we accumulated so far.
    return ("".join(out), "")


def decode_value(raw: str) -> DecodedValue:
    """Classify a gdb-MI ``value`` field into a ``DecodedValue``.

    The decoder is deliberately tolerant: anything that doesn't match
    one of the recognised shapes falls into the ``raw`` category and
    is surfaced verbatim. That way a DAP client always sees *some*
    text, even for exotic gdb output we haven't taught it about."""
    if raw is None:
        return DecodedValue(display="", type="raw")
    text = raw.strip()
    if not text:
        return DecodedValue(display="", type="raw")

    # Integer literal (decimal / hex / octal, optional sign).
    if _INT_RE.match(text):
        return DecodedValue(display=text, type="int")

    # Char: ``104 'h'`` — surface the quoted char.
    m = _CHAR_RE.match(text)
    if m:
        char_text = m.group(2)
        return DecodedValue(display=f"'{char_text}'", type="char")

    # Pointer-prefixed string: ``0x7fff... "hello"``.
    m = _PTR_PREFIX_RE.match(text)
    if m:
        rest = text[m.end() :]
        cstr = _extract_cstring(rest)
        if cstr is not None:
            decoded_text, _ = cstr
            return DecodedValue(display=decoded_text, type="str")
        # Bare pointer with no string preview: NOVA list / object.
        if _BARE_PTR_RE.match(text):
            return DecodedValue(display=text, type="ptr")
        # Pointer with non-string suffix — still surface raw.
        return DecodedValue(display=text, type="raw")

    # Pure C-string with no pointer (rare; happens when gdb is given
    # an rvalue string).
    cstr = _extract_cstring(text)
    if cstr is not None:
        decoded_text, _ = cstr
        return DecodedValue(display=decoded_text, type="str")

    # Boolean (gdb prints ``true``/``false`` for C++ bool).
    if text in ("true", "false"):
        return DecodedValue(display=text, type="bool")

    return DecodedValue(display=text, type="raw")


# ---------------------------------------------------------------------------
# gdb-MI command builder.
# ---------------------------------------------------------------------------


def quote_expression(expr: str) -> str:
    """Quote a NOVA / C expression for inclusion in an MI command.

    Mirrors :func:`nova_dap.gdb_bridge.quote_path` but tuned for
    arbitrary user input. We always wrap in double quotes and escape
    embedded backslashes / quotes so a DAP client can pass any
    expression string verbatim."""
    escaped = expr.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_evaluate_command(
    expression: str,
    thread_id: Optional[int] = None,
    frame_level: Optional[int] = None,
) -> str:
    """Compose ``-data-evaluate-expression`` with optional thread/frame
    routing. In non-stop mode the thread + frame MUST be specified or
    gdb evaluates in the wrong context; in all-stop mode they are
    optional but harmless to include."""
    parts = ["-data-evaluate-expression"]
    if thread_id is not None:
        parts.append(f"--thread {thread_id}")
    if frame_level is not None:
        parts.append(f"--frame {frame_level}")
    parts.append(quote_expression(expression))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# High-level API consumed by ``server.handle_evaluate``.
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    """Result of a complete evaluate call.

    ``ok`` mirrors the gdb result class. On success ``decoded`` carries
    the parsed value; on failure ``message`` carries gdb's error text
    so the DAP layer can surface a useful diagnostic instead of a bare
    ``success: false``."""

    ok: bool
    decoded: Optional[DecodedValue] = None
    message: str = ""


def evaluate_via_bridge(
    bridge,
    expression: str,
    thread_id: Optional[int] = None,
    frame_level: Optional[int] = None,
    timeout: float = 5.0,
) -> EvaluationResult:
    """Run ``-data-evaluate-expression`` through the given
    :class:`GdbBridge` and return a decoded result.

    ``bridge`` is duck-typed (any object with a ``command(cmd,
    timeout=...)`` method returning something with ``.ok``, ``.fields``,
    and ``.error_message``). The test suite uses fake bridges to
    exercise the decoder + thread/frame routing without needing a
    real gdb subprocess."""
    cmd = build_evaluate_command(expression, thread_id, frame_level)
    try:
        result = bridge.command(cmd, timeout=timeout)
    except TimeoutError as exc:
        return EvaluationResult(ok=False, message=f"gdb timed out: {exc}")
    except RuntimeError as exc:
        return EvaluationResult(ok=False, message=f"gdb error: {exc}")
    if not result.ok:
        return EvaluationResult(ok=False, message=result.error_message or "evaluate failed")
    raw_value = result.fields.get("value")
    if not isinstance(raw_value, str):
        return EvaluationResult(
            ok=False,
            message=f"gdb returned no value field: {result.fields!r}",
        )
    return EvaluationResult(ok=True, decoded=decode_value(raw_value))
