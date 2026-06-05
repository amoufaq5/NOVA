"""GDB Machine Interface (MI3) bridge.

Wraps a ``gdb --interpreter=mi3`` subprocess and exposes a tiny request /
response API plus an async-record event queue. The DAP server in
``server.py`` translates DAP messages into MI commands via this bridge
and turns the bridge's events back into DAP events.

The MI3 grammar we care about (see the gdb manual, "GDB/MI Output Syntax"):

* Result records start with ``^``; e.g. ``^done,bkpt={...}``.
* Async records start with ``*`` (exec), ``=`` (notify), ``+`` (status).
* Stream records start with ``~`` (console), ``@`` (target),
  ``&`` (log) and carry a C-style string payload.
* A ``(gdb)`` line terminates a record batch.

We only parse enough of the value grammar (cstring, tuple ``{k=v,...}``,
list ``[v,...]`` or ``[k=v,...]``) to extract the fields we use:
``bkpt``, ``stack``, ``variables``, ``reason``, ``frame``, ``thread-id``,
``exit-code``. Everything else is preserved verbatim as a string.

The bridge runs a single reader thread that drains gdb's stdout and
dispatches each record either to a token-keyed Future (for matched
``-command`` responses) or to a callback (for async events). Commands
are tagged with a monotonically-increasing integer token so responses
can be correlated even when they arrive out of order.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


GdbValue = Any  # str | List[GdbValue] | Dict[str, GdbValue]


class GdbParseError(ValueError):
    """Raised when an MI record cannot be parsed."""


# ---------------------------------------------------------------------------
# MI value parser.
# ---------------------------------------------------------------------------


class _MiParser:
    """Recursive-descent parser for the MI value grammar.

    Accepts the substring AFTER the result-class word (e.g. given
    ``^done,bkpt={...}`` the parser is fed ``bkpt={...}``)."""

    def __init__(self, src: str) -> None:
        self.src = src
        self.pos = 0

    # --- low-level ---------------------------------------------------------

    def _peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def _eat(self, ch: str) -> None:
        if self._peek() != ch:
            raise GdbParseError(
                f"expected {ch!r} at pos {self.pos} of {self.src!r}"
            )
        self.pos += 1

    def _eof(self) -> bool:
        return self.pos >= len(self.src)

    # --- entry points ------------------------------------------------------

    def parse_results(self) -> Dict[str, GdbValue]:
        """Parse a comma-separated list of ``name=value`` pairs."""
        out: Dict[str, GdbValue] = {}
        if self._eof():
            return out
        while True:
            name = self._parse_identifier()
            self._eat("=")
            out[name] = self._parse_value()
            if self._peek() == ",":
                self.pos += 1
                continue
            break
        return out

    # --- terminals ---------------------------------------------------------

    def _parse_identifier(self) -> str:
        start = self.pos
        while self.pos < len(self.src):
            ch = self.src[self.pos]
            if ch.isalnum() or ch in "-_":
                self.pos += 1
            else:
                break
        if start == self.pos:
            raise GdbParseError(
                f"expected identifier at pos {self.pos} of {self.src!r}"
            )
        return self.src[start:self.pos]

    def _parse_cstring(self) -> str:
        self._eat('"')
        out: List[str] = []
        while not self._eof():
            ch = self.src[self.pos]
            if ch == '"':
                self.pos += 1
                return "".join(out)
            if ch == "\\" and self.pos + 1 < len(self.src):
                nxt = self.src[self.pos + 1]
                self.pos += 2
                if nxt == "n":
                    out.append("\n")
                elif nxt == "t":
                    out.append("\t")
                elif nxt == "r":
                    out.append("\r")
                elif nxt == "\\":
                    out.append("\\")
                elif nxt == '"':
                    out.append('"')
                elif nxt == "'":
                    out.append("'")
                elif nxt == "0":
                    out.append("\0")
                else:
                    # unknown escape — keep literally
                    out.append(nxt)
                continue
            out.append(ch)
            self.pos += 1
        raise GdbParseError(f"unterminated cstring in {self.src!r}")

    def _parse_value(self) -> GdbValue:
        ch = self._peek()
        if ch == '"':
            return self._parse_cstring()
        if ch == "{":
            return self._parse_tuple()
        if ch == "[":
            return self._parse_list()
        raise GdbParseError(
            f"unexpected char {ch!r} at pos {self.pos} of {self.src!r}"
        )

    def _parse_tuple(self) -> Dict[str, GdbValue]:
        self._eat("{")
        out: Dict[str, GdbValue] = {}
        if self._peek() == "}":
            self.pos += 1
            return out
        while True:
            name = self._parse_identifier()
            self._eat("=")
            out[name] = self._parse_value()
            if self._peek() == ",":
                self.pos += 1
                continue
            break
        self._eat("}")
        return out

    def _parse_list(self) -> List[GdbValue]:
        self._eat("[")
        out: List[GdbValue] = []
        if self._peek() == "]":
            self.pos += 1
            return out
        # MI lists can be either values or ``name=value`` pairs; we
        # always preserve them as a flat list. For ``name=value`` we
        # store ``{name: value}`` per-element so consumers can iterate
        # without caring which flavour gdb used.
        while True:
            if self._is_at_result():
                name = self._parse_identifier()
                self._eat("=")
                out.append({name: self._parse_value()})
            else:
                out.append(self._parse_value())
            if self._peek() == ",":
                self.pos += 1
                continue
            break
        self._eat("]")
        return out

    def _is_at_result(self) -> bool:
        # Look ahead: identifier followed by ``=``?
        i = self.pos
        if i >= len(self.src):
            return False
        if not (self.src[i].isalpha() or self.src[i] == "_"):
            return False
        while i < len(self.src) and (self.src[i].isalnum() or self.src[i] in "-_"):
            i += 1
        return i < len(self.src) and self.src[i] == "="


def parse_mi_results(payload: str) -> Dict[str, GdbValue]:
    """Parse an MI result payload (the bit after the result-class word)."""
    if not payload:
        return {}
    if payload.startswith(","):
        payload = payload[1:]
    return _MiParser(payload).parse_results()


# ---------------------------------------------------------------------------
# Record types.
# ---------------------------------------------------------------------------


@dataclass
class GdbResult:
    """A synchronous result record (``token^class,fields``)."""

    token: Optional[int]
    cls: str  # done, running, connected, error, exit
    fields: Dict[str, GdbValue] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.cls in ("done", "running", "connected", "exit")

    @property
    def error_message(self) -> str:
        if self.cls != "error":
            return ""
        msg = self.fields.get("msg", "")
        return msg if isinstance(msg, str) else str(msg)


@dataclass
class GdbAsyncRecord:
    """An async record (``*class,fields`` / ``=class,fields``)."""

    kind: str  # "exec", "notify", "status"
    cls: str   # e.g. "stopped", "running", "thread-group-exited"
    fields: Dict[str, GdbValue] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# GdbBridge.
# ---------------------------------------------------------------------------


class GdbBridge:
    """Wraps a ``gdb --interpreter=mi3`` subprocess.

    Usage::

        bridge = GdbBridge(on_event=lambda rec: ...)
        bridge.start()
        bridge.command("-file-exec-and-symbols /path/to/binary")
        bridge.command("-break-insert main")
        bridge.command("-exec-run")
        # ... events flow to on_event ...
        bridge.terminate()
    """

    def __init__(
        self,
        gdb_path: Optional[str] = None,
        on_event: Optional[Callable[[GdbAsyncRecord], None]] = None,
        on_console: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.gdb_path = gdb_path or _resolve_gdb()
        self.on_event = on_event or (lambda _rec: None)
        # on_console(stream, text) — stream is one of "console", "log", "target"
        self.on_console = on_console or (lambda _s, _t: None)
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._next_token = 1
        self._pending: Dict[int, "queue.Queue[GdbResult]"] = {}
        self._stopped = threading.Event()

    # ------------------------------------------------------------------ life

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("gdb already started")
        if not self.gdb_path:
            raise FileNotFoundError("gdb not found on PATH")
        self._proc = subprocess.Popen(
            [self.gdb_path, "--interpreter=mi3", "--quiet", "--nx"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=False,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name="gdb-mi-reader", daemon=True
        )
        self._reader.start()

    def terminate(self, timeout: float = 2.0) -> None:
        if self._proc is None:
            return
        self._stopped.set()
        try:
            # Best-effort polite shutdown; ignore errors if gdb is already gone.
            if self._proc.stdin and not self._proc.stdin.closed:
                try:
                    self._proc.stdin.write(b"-gdb-exit\n")
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
        finally:
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        self._proc = None

    # -------------------------------------------------------------- commands

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        """Send an MI command and block until its tagged result arrives."""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("gdb not started")
        token = self._allocate_token()
        slot: "queue.Queue[GdbResult]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[token] = slot
        line = f"{token}{cmd}\n".encode("utf-8")
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._lock:
                self._pending.pop(token, None)
            raise RuntimeError(f"gdb write failed: {exc}") from exc
        try:
            return slot.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(token, None)
            raise TimeoutError(f"gdb did not respond to {cmd!r} within {timeout}s") from exc

    def _allocate_token(self) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
        return token

    # ---------------------------------------------------------------- reader

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while not self._stopped.is_set():
            line = stream.readline()
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                continue
            if not text or text == "(gdb)":
                continue
            try:
                self._dispatch(text)
            except Exception:
                # Never let a single bad line kill the reader thread.
                pass
        # Wake any pending waiters so callers don't hang on shutdown.
        with self._lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            try:
                slot.put_nowait(
                    GdbResult(token=None, cls="error", fields={"msg": "gdb exited"})
                )
            except queue.Full:
                pass

    def _dispatch(self, line: str) -> None:
        # Optional leading integer token.
        i = 0
        while i < len(line) and line[i].isdigit():
            i += 1
        token: Optional[int] = int(line[:i]) if i > 0 else None
        if i >= len(line):
            return
        kind_ch = line[i]
        rest = line[i + 1 :]
        if kind_ch == "^":
            cls, _, payload = rest.partition(",")
            fields = parse_mi_results(payload)
            result = GdbResult(token=token, cls=cls, fields=fields)
            if token is not None:
                with self._lock:
                    slot = self._pending.pop(token, None)
                if slot is not None:
                    try:
                        slot.put_nowait(result)
                    except queue.Full:
                        pass
            return
        if kind_ch in ("*", "=", "+"):
            cls, _, payload = rest.partition(",")
            fields = parse_mi_results(payload)
            kind_name = {"*": "exec", "=": "notify", "+": "status"}[kind_ch]
            self.on_event(GdbAsyncRecord(kind=kind_name, cls=cls, fields=fields))
            return
        if kind_ch in ("~", "@", "&"):
            # cstring follows
            stream_name = {"~": "console", "@": "target", "&": "log"}[kind_ch]
            try:
                payload = _MiParser(rest)._parse_cstring()
            except GdbParseError:
                payload = rest
            self.on_console(stream_name, payload)
            return
        # Anything else: ignore.


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _resolve_gdb() -> Optional[str]:
    env_override = os.environ.get("NOVA_DAP_GDB")
    if env_override:
        return env_override
    return shutil.which("gdb")


def gdb_available() -> bool:
    """Return True if a usable gdb is on PATH (or NOVA_DAP_GDB is set)."""
    return _resolve_gdb() is not None


def quote_path(path: str) -> str:
    """Quote a path for inclusion in an MI command.

    MI uses double-quoted C-strings when an argument contains spaces; we
    always quote to keep things simple."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
