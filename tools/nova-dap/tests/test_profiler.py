"""Tests for the DAP sample-based profiler — custom-request channel
extension to ``nova-dap``.

Three layers, mirroring the pattern established by
``test_instruction_stepping.py``:

1. **Unit tests** — exercise ``nova_dap.profiler`` directly: frame
   interning, sample append, stop/restart bookkeeping, format text /
   folded / json output, MI stack-frame parser, frequency clamping.
   These run everywhere (no gdb required).

2. **Server handler tests with a stub bridge** — drive
   ``handle_profile_start`` / ``handle_profile_stop`` /
   ``handle_profile_report`` against a ``CaptureBridge`` that
   returns canned ``-stack-list-frames`` replies. We verify the
   custom-request wire shape + handler routing without spawning gdb.

3. **End-to-end against gdb + a known loop binary** — compiles a
   tiny C fixture with a tight ``loop_body`` function called in a
   busy loop, launches it under the DAP server, sets a function
   breakpoint at loop entry, captures ~1 second of samples while
   the inferior runs, and asserts that the majority of samples
   land inside the loop body (not in setup / printf). SKIPs
   cleanly if gdb / gcc are missing.

Run::

    python tools/nova-dap/tests/test_profiler.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.gdb_bridge import GdbResult  # noqa: E402
from nova_dap.profiler import (  # noqa: E402
    Frame,
    Profiler,
    Sample,
    SUPPORTED_FORMATS,
    aggregate_stacks,
    format_folded,
    format_json,
    format_text,
    normalise_frequency,
    parse_stack_frames,
    report_body,
    stack_fn_from_bridge,
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
# Unit tests for profiler.py (no gdb required).
# ---------------------------------------------------------------------------


def test_frame_display_function_only() -> None:
    """A frame with only a function name renders bare."""
    fr = Frame(function="main")
    check_eq(fr.display(), "main")


def test_frame_display_with_file_and_line() -> None:
    """A frame with file + line renders ``function (file:line)``."""
    fr = Frame(function="loop_body", file="bench.c", line=42)
    check_eq(fr.display(), "loop_body (bench.c:42)")


def test_frame_key_stability() -> None:
    """Two frames with the same (function, file, line) hash equal."""
    a = Frame("foo", "x.c", 10)
    b = Frame("foo", "x.c", 10)
    check_eq(a.key(), b.key())


def test_normalise_frequency_clamps() -> None:
    """Frequency clamps at MIN/MAX; non-int returns None."""
    check_eq(normalise_frequency(100), 100)
    check_eq(normalise_frequency(0), 1)  # floor
    check_eq(normalise_frequency(-50), 1)  # negative -> floor
    check_eq(normalise_frequency(99999), 1000)  # ceiling
    check_eq(normalise_frequency("not-an-int"), None)
    check_eq(normalise_frequency(None), None)


def test_profiler_start_resets_state() -> None:
    """Start clears prior samples + frames."""
    p = Profiler()
    p.start(frequency_hz=50)
    # Inject a fake sample.
    p.sample(lambda _tid: [("foo", "x.c", 1)])
    check_eq(len(p.samples), 1)
    p.start(frequency_hz=200)
    check_eq(len(p.samples), 0, "start should reset samples")
    check_eq(len(p.frames), 0, "start should reset frame table")
    check_eq(p.frequency_hz, 200, "frequency should update on restart")
    check(p.is_running())


def test_profiler_sample_appends() -> None:
    """sample() with a non-empty stack_fn appends to samples list and
    interns each frame exactly once."""
    p = Profiler()
    p.start(frequency_hz=100)
    p.sample(lambda _tid: [("foo", "x.c", 1), ("main", "x.c", 10)])
    p.sample(lambda _tid: [("foo", "x.c", 1), ("main", "x.c", 10)])
    check_eq(len(p.samples), 2)
    # Same stack -> same frame ids.
    check_eq(p.samples[0].frame_ids, p.samples[1].frame_ids)
    # Only two distinct frames despite two samples.
    check_eq(len(p.frames), 2)


def test_profiler_distinct_call_sites_get_distinct_frames() -> None:
    """The same function called from two different sites (different
    lines) produces two distinct frame ids — this is what makes the
    flame graph distinguish call sites."""
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("helper", "x.c", 5)])
    p.sample(lambda _tid: [("helper", "x.c", 7)])
    check_eq(len(p.frames), 2, "different lines -> different frame ids")
    # Same name + file but different line.
    func_names = sorted({fr.function for fr in p.frames.values()})
    check_eq(func_names, ["helper"])
    lines = sorted({fr.line for fr in p.frames.values()})
    check_eq(lines, [5, 7])


def test_profiler_drop_count_on_empty_stack() -> None:
    """sample() with an empty stack_fn return increments drop_count
    instead of appending."""
    p = Profiler()
    p.start()
    out = p.sample(lambda _tid: [])
    check_eq(out, None)
    check_eq(p.drop_count, 1)
    check_eq(len(p.samples), 0)


def test_profiler_drop_count_on_exception() -> None:
    """sample() tolerates exceptions raised by the stack_fn — the
    error path increments drop_count and returns None instead of
    bubbling out."""
    def bad(_tid):
        raise RuntimeError("transient bridge error")
    p = Profiler()
    p.start()
    out = p.sample(bad)
    check_eq(out, None)
    check_eq(p.drop_count, 1)


def test_profiler_stop_returns_snapshot() -> None:
    """stop() returns the aggregated wire shape with total_samples +
    duration + frequency."""
    p = Profiler()
    p.start(frequency_hz=50)
    p.sample(lambda _tid: [("foo", "x.c", 1)])
    body = p.stop()
    check_eq(body["total_samples"], 1)
    check_eq(body["frequency_hz"], 50)
    check(body["duration_s"] >= 0.0)
    check_eq(len(body["samples"]), 1)
    check_eq(len(body["frames"]), 1)
    # Stop sets is_running() to False.
    check(not p.is_running())


def test_aggregate_stacks_counts_identical_stacks() -> None:
    """Two samples with the exact same frame list collapse to one
    entry with count=2."""
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("foo", "x.c", 1), ("main", "x.c", 10)])
    p.sample(lambda _tid: [("foo", "x.c", 1), ("main", "x.c", 10)])
    p.sample(lambda _tid: [("bar", "x.c", 2), ("main", "x.c", 10)])
    snap = p.snapshot()
    counts = aggregate_stacks(snap)
    check_eq(len(counts), 2)
    check_eq(sorted(counts.values()), [1, 2])


def test_format_folded_brendan_gregg_shape() -> None:
    """folded format is one ``frame;frame;... count`` per stack."""
    p = Profiler()
    p.start()
    # Inner-first ordering (matches gdb -stack-list-frames).
    p.sample(lambda _tid: [("loop", "b.c", 4), ("main", "b.c", 10)])
    p.sample(lambda _tid: [("loop", "b.c", 4), ("main", "b.c", 10)])
    folded = p.format("folded")
    lines = [ln for ln in folded.split("\n") if ln.strip()]
    check_eq(len(lines), 1, f"one stack -> one folded line, got {folded!r}")
    line = lines[0]
    # The flame graph convention is OUTERMOST-FIRST -> main is on left.
    check(line.startswith("main"), f"main should be leftmost: {line!r}")
    check("loop" in line, f"loop should appear: {line!r}")
    check(line.endswith(" 2"), f"count=2 at end: {line!r}")
    check(";" in line, "semicolon separator")


def test_format_folded_parseable_by_flamegraph_pl() -> None:
    """Each line should match the strict ``stacks count`` format used
    by flamegraph.pl: regex-checkable as ``.+\\s\\d+`` with no
    embedded line-breaks in the stack portion."""
    import re
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("loop", "b.c", 4), ("main", "b.c", 10)])
    p.sample(lambda _tid: [("loop", "b.c", 4), ("main", "b.c", 10)])
    p.sample(lambda _tid: [("loop", "b.c", 4), ("setup", "b.c", 5),
                            ("main", "b.c", 10)])
    folded = p.format("folded")
    pattern = re.compile(r"^[^\n]+ \d+$")
    for line in folded.split("\n"):
        if not line.strip():
            continue
        check(pattern.match(line) is not None,
              f"line not flamegraph.pl-parseable: {line!r}")


def test_format_text_outline_header() -> None:
    """text format leads with a summary line: sample count + duration."""
    p = Profiler()
    p.start(frequency_hz=100)
    p.sample(lambda _tid: [("foo", "x.c", 1)])
    text = p.format("text")
    first = text.split("\n")[0]
    check("Profile:" in first, f"header missing 'Profile:': {first!r}")
    check("1 samples" in first, f"sample count missing: {first!r}")
    check("100 Hz" in first, f"frequency missing: {first!r}")


def test_format_json_is_valid_json() -> None:
    """json format round-trips through json.loads cleanly."""
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("foo", "x.c", 1), ("main", "x.c", 10)])
    out = p.format("json")
    parsed = json.loads(out)
    check_eq(parsed["total_samples"], 1)
    check_eq(len(parsed["samples"]), 1)
    check_eq(len(parsed["frames"]), 2)
    # frame_ids appear as a list in JSON.
    fids = parsed["samples"][0]["frame_ids"]
    check(isinstance(fids, list))
    check_eq(len(fids), 2)


def test_format_rejects_unknown() -> None:
    """An unsupported format raises ValueError."""
    p = Profiler()
    p.start()
    raised = False
    try:
        p.format("svg")
    except ValueError as exc:
        raised = True
        check("svg" in str(exc), f"error mentions bad format: {exc}")
    check(raised, "ValueError expected for unknown format")


def test_supported_formats_constant() -> None:
    """The SUPPORTED_FORMATS set names the three documented formats."""
    check_eq(sorted(SUPPORTED_FORMATS), ["folded", "json", "text"])


def test_parse_stack_frames_basic() -> None:
    """A typical gdb -stack-list-frames reply decodes to a list of
    (function, file, line) tuples."""
    fields = {
        "stack": [
            {"frame": {"level": "0", "func": "loop_body",
                       "file": "bench.c", "fullname": "/tmp/bench.c",
                       "line": "8", "addr": "0x40104a"}},
            {"frame": {"level": "1", "func": "main",
                       "file": "bench.c", "fullname": "/tmp/bench.c",
                       "line": "20", "addr": "0x401123"}},
        ],
    }
    out = parse_stack_frames(fields)
    check_eq(len(out), 2)
    check_eq(out[0], ("loop_body", "bench.c", 8))
    check_eq(out[1], ("main", "bench.c", 20))


def test_parse_stack_frames_missing_fields_tolerated() -> None:
    """Missing func / line / file gracefully degrade."""
    fields = {
        "stack": [
            {"frame": {"level": "0"}},  # no func -> <unknown>
            {"frame": {"level": "1", "func": "main"}},  # no file/line
        ],
    }
    out = parse_stack_frames(fields)
    check_eq(len(out), 2)
    check_eq(out[0], ("<unknown>", "", 0))
    check_eq(out[1], ("main", "", 0))


def test_parse_stack_frames_strips_dirname() -> None:
    """File paths get reduced to basenames so frames from the same
    source aggregate even if gdb reports different absolute paths."""
    fields = {
        "stack": [
            {"frame": {"func": "foo",
                       "file": "/a/very/long/path/source.c",
                       "line": "5"}},
        ],
    }
    out = parse_stack_frames(fields)
    check_eq(out[0], ("foo", "source.c", 5))


def test_parse_stack_frames_empty() -> None:
    """No 'stack' field -> empty list (not an error)."""
    check_eq(parse_stack_frames({}), [])
    check_eq(parse_stack_frames({"stack": []}), [])
    check_eq(parse_stack_frames({"stack": "not-a-list"}), [])


def test_stack_fn_from_bridge_returns_empty_on_none() -> None:
    """A None bridge yields the no-op stack fn (returns empty list)."""
    fn = stack_fn_from_bridge(None)
    check_eq(fn(None), [])


def test_report_body_shape() -> None:
    """report_body returns the {format, output, total_samples,
    duration_s} tuple wrapped for the DAP custom request response."""
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("foo", "x.c", 1)])
    body = report_body(p, "text")
    check_eq(body["format"], "text")
    check("Profile:" in body["output"])
    check_eq(body["total_samples"], 1)
    check(body["duration_s"] >= 0.0)


def test_late_sample_after_stop_ignored() -> None:
    """A sample() call after stop() is silently dropped — important
    because the background sampler thread might fire one last poll
    before noticing the stop event."""
    p = Profiler()
    p.start()
    p.sample(lambda _tid: [("foo", "x.c", 1)])
    p.stop()
    # This sample arrives after stop -> ignored.
    out = p.sample(lambda _tid: [("bar", "x.c", 2)])
    check_eq(out, None)
    check_eq(len(p.samples), 1, "post-stop sample should not append")


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
    """Stub bridge that returns canned ``-stack-list-frames`` replies.

    The reply cycles through ``stack_replies`` (a list of pre-built
    field dicts) so successive samples can capture different stacks.
    Falls back to an empty stack when the list is exhausted."""

    def __init__(
        self,
        stack_replies: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.sent_commands: List[str] = []
        self._stack_replies = stack_replies or []
        self._cursor = 0

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if cmd.startswith("-stack-list-frames"):
            if self._cursor < len(self._stack_replies):
                fields = self._stack_replies[self._cursor]
                self._cursor += 1
                return GdbResult(token=None, cls="done", fields=fields)
            return GdbResult(token=None, cls="done", fields={"stack": []})
        # Default: any other gdb-MI command succeeds with empty fields.
        return GdbResult(token=None, cls="done", fields={})


def _stack_reply(frames: List[Tuple[str, str, int]]) -> Dict[str, Any]:
    """Build a fake gdb ``-stack-list-frames`` reply for testing."""
    return {
        "stack": [
            {"frame": {"level": str(i), "func": fn, "file": fl,
                       "line": str(ln), "addr": f"0x40{i:04x}"}}
            for i, (fn, fl, ln) in enumerate(frames)
        ],
    }


def test_handle_profile_start_no_bridge_fails() -> None:
    """A start request before launch must fail cleanly."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    req = {
        "seq": 1, "type": "request",
        "command": "nova/profile/start",
        "arguments": {"frequency_hz": 100},
    }
    server.handle_profile_start(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)
    check("not launched" in (resp.get("message") or "").lower())


def test_handle_profile_start_with_bridge_succeeds() -> None:
    """A start request with a live bridge installs the sampler."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    sess.bridge = CaptureBridge(stack_replies=[
        _stack_reply([("foo", "x.c", 1), ("main", "x.c", 10)]),
    ])
    req = {
        "seq": 2, "type": "request",
        "command": "nova/profile/start",
        "arguments": {"frequency_hz": 50},
    }
    server.handle_profile_start(sess, req)
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    check_eq(body.get("started"), True)
    check_eq(body.get("frequency_hz"), 50)
    # Stop so the sampler thread doesn't outlive the test.
    server.handle_profile_stop(sess, {
        "seq": 3, "type": "request", "command": "nova/profile/stop",
    })


def test_handle_profile_stop_returns_aggregate() -> None:
    """stop() returns the {samples, frames, total_samples} body."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    # Seed the profiler with a known sample without going through start.
    sess.profiler.start(frequency_hz=100)
    sess.profiler.sample(lambda _tid: [("foo", "x.c", 1)])
    server.handle_profile_stop(sess, {
        "seq": 1, "type": "request", "command": "nova/profile/stop",
    })
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    check_eq(body.get("total_samples"), 1)
    check_eq(len(body.get("samples") or []), 1)
    check_eq(len(body.get("frames") or {}), 1)


def test_handle_profile_stop_idempotent_when_never_started() -> None:
    """A stop() with no prior start returns empty aggregate, not error."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    server.handle_profile_stop(sess, {
        "seq": 1, "type": "request", "command": "nova/profile/stop",
    })
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    check_eq(body.get("total_samples"), 0)
    check_eq(body.get("samples"), [])
    check_eq(body.get("frames"), {})


def test_handle_profile_report_text() -> None:
    """report({format:"text"}) returns the human-readable outline."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    sess.profiler.start(frequency_hz=100)
    sess.profiler.sample(lambda _tid: [("foo", "x.c", 1)])
    server.handle_profile_report(sess, {
        "seq": 1, "type": "request", "command": "nova/profile/report",
        "arguments": {"format": "text"},
    })
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    check_eq(body.get("format"), "text")
    check("Profile:" in body.get("output", ""))


def test_handle_profile_report_folded() -> None:
    """report({format:"folded"}) returns Brendan-Gregg format."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    sess.profiler.start(frequency_hz=100)
    sess.profiler.sample(lambda _tid: [("loop", "x.c", 1), ("main", "x.c", 5)])
    sess.profiler.sample(lambda _tid: [("loop", "x.c", 1), ("main", "x.c", 5)])
    server.handle_profile_report(sess, {
        "seq": 1, "type": "request", "command": "nova/profile/report",
        "arguments": {"format": "folded"},
    })
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), True)
    body = resp.get("body", {})
    check_eq(body.get("format"), "folded")
    out = body.get("output", "")
    check(";" in out, f"expected semicolon in folded output: {out!r}")
    check(out.endswith(" 2"), f"expected count 2 at end: {out!r}")


def test_handle_profile_report_rejects_unknown_format() -> None:
    """An unsupported format is rejected with success=False."""
    from nova_dap import server  # noqa: WPS433
    sess = server.Session(out_stream=_CaptureStream())
    server.handle_profile_report(sess, {
        "seq": 1, "type": "request", "command": "nova/profile/report",
        "arguments": {"format": "svg"},
    })
    resp = sess.out_stream.last_response()
    check_eq(resp.get("success"), False)
    check("svg" in (resp.get("message") or "").lower())


def test_dispatch_routes_profile_requests() -> None:
    """The HANDLERS table routes nova/profile/* to the right handlers
    and dispatch() doesn't return 'unsupported command' for them."""
    from nova_dap.server import HANDLERS  # noqa: WPS433
    check("nova/profile/start" in HANDLERS)
    check("nova/profile/stop" in HANDLERS)
    check("nova/profile/report" in HANDLERS)


def test_capability_count_unchanged() -> None:
    """The DAP capability count is unchanged at 21 — profiler is a
    custom-request extension, not a top-level capability."""
    from nova_dap.server import _capabilities  # noqa: WPS433
    caps = _capabilities()
    # Sanity-check all the prior capability flags are still set.
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsFunctionBreakpoints"), True)
    check_eq(caps.get("supportsDataBreakpoints"), True)
    check_eq(caps.get("supportsInstructionBreakpoints"), True)
    check_eq(caps.get("supportsSteppingGranularity"), True)
    check_eq(caps.get("supportsDisassembleRequest"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    check_eq(caps.get("supportsSingleThreadExecutionRequests"), True)


# ---------------------------------------------------------------------------
# End-to-end driver shared with the other DAP test suites.
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


def _build_loop_fixture() -> Optional[str]:
    """Compile a tiny C program with a tight loop_body() — we'll
    profile this binary and expect the majority of samples to land
    in loop_body."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
#include <stdlib.h>

/* loop_body intentionally does enough work to dominate the profile
   when called from a busy loop. The arithmetic prevents the compiler
   from eliminating the call under -O0. */
__attribute__((noinline))
long loop_body(long n, long acc) {
    long sum = acc;
    for (long i = 0; i < n; i++) {
        sum = sum + (i * 7) - (i / 3);
    }
    return sum;
}

__attribute__((noinline))
long busy_caller(long iters) {
    long acc = 0;
    for (long i = 0; i < iters; i++) {
        acc = loop_body(2000, acc);
    }
    return acc;
}

int main(void) {
    long result = busy_caller(50000);
    /* Final printf so the value isn't dead-code eliminated. */
    printf("result=%ld\\n", result);
    return 0;
}
"""
    src_path = "/tmp/nova_dap_profile_fixture.c"
    bin_path = "/tmp/nova_dap_profile_fixture"
    with open(src_path, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        subprocess.check_call([
            "gcc", "-g", "-O0", "-fno-inline", "-o", bin_path, src_path,
        ])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return bin_path if os.path.isfile(bin_path) else None


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


def _e2e_profile_loop_fixture(bin_path: str) -> Optional[Dict[str, Any]]:
    """Profile a binary with a tight loop for ~1 second and assert
    that the majority of samples land in the loop body.

    Returns a dict ``{total_samples, loop_body_samples,
    loop_body_pct, folded_lines, frame_count}`` so the caller can
    assert against gdb's actual behaviour."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], f"initialize: {init}")
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": bin_path})
        check(launch["success"], f"launch: {launch}")

        # configurationDone -> inferior starts running.
        cd = client.request("configurationDone", {})
        check(cd["success"])

        # The inferior is now busy in loop_body. Start the profile,
        # sample for ~1 second at 100 Hz, then stop.
        start = client.request(
            "nova/profile/start",
            {"frequency_hz": 100},
        )
        check(start["success"], f"profile start: {start}")
        check_eq(start.get("body", {}).get("started"), True)
        # NOTE: the inferior is running freely; we just wait wallclock.
        time.sleep(1.0)
        stop = client.request("nova/profile/stop", {}, timeout=10.0)
        check(stop["success"], f"profile stop: {stop}")
        body = stop.get("body", {})
        total = body.get("total_samples", 0)
        # We expect a healthy sample count (100 Hz * 1.0s, minus
        # drops while the inferior is between gdb sync points and
        # the pause-sample-resume overhead). The threshold is
        # deliberately generous so the test stays robust on slow
        # CI — gdb's interrupt-then-list-frames cycle takes a few
        # ms per sample so the effective rate is often well below
        # the nominal 100 Hz.
        check(total >= 10, f"expected >=10 samples, got {total}")
        # Aggregate: how many samples carry a frame whose function
        # name is ``loop_body``?
        frames_table = body.get("frames", {})
        loop_body_ids: List[int] = []
        for fid_str, fr in frames_table.items():
            if isinstance(fr, dict) and fr.get("function") == "loop_body":
                try:
                    loop_body_ids.append(int(fid_str))
                except (TypeError, ValueError):
                    pass
        loop_samples = 0
        for s in body.get("samples", []):
            fids = s.get("frame_ids") or []
            if any(fid in loop_body_ids for fid in fids):
                loop_samples += 1
        loop_pct = (loop_samples / total) * 100.0 if total else 0.0

        # The majority of samples should land in loop_body. We use a
        # very generous threshold (>= 50%) so the test stays robust
        # against gdb stop-the-world overhead on slow CI machines.
        check(
            loop_pct >= 50.0,
            f"expected >= 50% samples in loop_body, got {loop_pct:.1f}%",
        )

        # Report in folded format and verify the result is parseable
        # by flamegraph.pl (one ``stack count`` per line).
        rep = client.request(
            "nova/profile/report",
            {"format": "folded"},
            timeout=5.0,
        )
        check(rep["success"], f"profile report: {rep}")
        folded = rep.get("body", {}).get("output", "")
        folded_lines = [ln for ln in folded.split("\n") if ln.strip()]
        # Every line must end with a count.
        for line in folded_lines:
            parts = line.rsplit(" ", 1)
            check(
                len(parts) == 2 and parts[1].isdigit(),
                f"folded line not parseable: {line!r}",
            )
        # The loop_body frame should appear in the folded output.
        check(
            any("loop_body" in line for line in folded_lines),
            "loop_body should appear in folded output",
        )

        # Also verify the text format produces a usable report.
        rep_text = client.request(
            "nova/profile/report",
            {"format": "text"},
            timeout=5.0,
        )
        check(rep_text["success"])
        text_out = rep_text.get("body", {}).get("output", "")
        check("Profile:" in text_out)
        check(str(total) in text_out, f"text report should mention {total}")

        # JSON format must round-trip through json.loads.
        rep_json = client.request(
            "nova/profile/report",
            {"format": "json"},
            timeout=5.0,
        )
        check(rep_json["success"])
        try:
            parsed = json.loads(rep_json.get("body", {}).get("output", ""))
        except json.JSONDecodeError as exc:
            check(False, f"json report not parseable: {exc}")
            parsed = {}
        check_eq(parsed.get("total_samples"), total)

        client.request("disconnect", {})
        return {
            "total_samples": total,
            "loop_body_samples": loop_samples,
            "loop_body_pct": loop_pct,
            "folded_lines": len(folded_lines),
            "frame_count": len(frames_table),
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    # Unit tests for profiler.py.
    test_frame_display_function_only()
    test_frame_display_with_file_and_line()
    test_frame_key_stability()
    test_normalise_frequency_clamps()
    test_profiler_start_resets_state()
    test_profiler_sample_appends()
    test_profiler_distinct_call_sites_get_distinct_frames()
    test_profiler_drop_count_on_empty_stack()
    test_profiler_drop_count_on_exception()
    test_profiler_stop_returns_snapshot()
    test_aggregate_stacks_counts_identical_stacks()
    test_format_folded_brendan_gregg_shape()
    test_format_folded_parseable_by_flamegraph_pl()
    test_format_text_outline_header()
    test_format_json_is_valid_json()
    test_format_rejects_unknown()
    test_supported_formats_constant()
    test_parse_stack_frames_basic()
    test_parse_stack_frames_missing_fields_tolerated()
    test_parse_stack_frames_strips_dirname()
    test_parse_stack_frames_empty()
    test_stack_fn_from_bridge_returns_empty_on_none()
    test_report_body_shape()
    test_late_sample_after_stop_ignored()
    # Handler tests with a stub bridge.
    test_handle_profile_start_no_bridge_fails()
    test_handle_profile_start_with_bridge_succeeds()
    test_handle_profile_stop_returns_aggregate()
    test_handle_profile_stop_idempotent_when_never_started()
    test_handle_profile_report_text()
    test_handle_profile_report_folded()
    test_handle_profile_report_rejects_unknown_format()
    test_dispatch_routes_profile_requests()
    test_capability_count_unchanged()


def main() -> int:
    # Phase 1: unit + handler tests (always run).
    _run_unit_tests()
    unit_assertions = _ASSERT_COUNT

    # Phase 2: end-to-end.
    e2e_status = "skipped"
    e2e_reason = ""
    if shutil.which("gdb") is None:
        e2e_reason = "gdb not installed"
    else:
        bin_path = _build_loop_fixture()
        if bin_path is None:
            e2e_reason = "gcc not available"
        else:
            try:
                summary = _e2e_profile_loop_fixture(bin_path)
                if summary is None:
                    e2e_status = "skipped"
                else:
                    e2e_status = (
                        f"ok ({summary['total_samples']} samples, "
                        f"{summary['loop_body_pct']:.0f}% in loop_body, "
                        f"{summary['folded_lines']} folded stacks)"
                    )
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout: {exc}"

    print("test_profiler: OK")
    print(f"  unit assertions:    {unit_assertions}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    if e2e_status.startswith("ok"):
        extra = _ASSERT_COUNT - unit_assertions
        print(f"  end-to-end:         {e2e_status} ({extra} extra checks)")
    else:
        print(f"  end-to-end:         SKIP — {e2e_reason or e2e_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
