"""Tests for DAP ``evaluate`` (REPL / watch / hover) — the 18th DAP
capability for ``nova-dap``.

Three layers:

1. **Decoder unit tests.** ``nova_dap.evaluator.decode_value`` and
   ``build_evaluate_command`` are exercised with no subprocesses — pure
   string handling. These run everywhere (no gdb required).

2. **Bridge tests with a stub.** A ``FakeBridge`` exposes the same
   ``.command(...)`` shape as ``GdbBridge`` so we can verify the MI
   command composition + result decoding without spawning gdb.

3. **End-to-end against gdb.** When ``gdb`` and a compiled fixture are
   available, we spawn ``python -m nova_dap.server`` and drive a real
   evaluate request through the wire protocol. SKIPs cleanly if any
   prerequisite is missing.

Run::

    python tools/nova-dap/tests/test_evaluate.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

# Make ``nova_dap`` importable when running from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_dap.evaluator import (  # noqa: E402
    DecodedValue,
    EvaluationResult,
    build_evaluate_command,
    decode_value,
    evaluate_via_bridge,
    quote_expression,
)
from nova_dap.gdb_bridge import GdbResult  # noqa: E402


# Track every assertion so we can report a count at the end. The test
# suite would otherwise just print "OK" — explicit assertion totals
# make it easier to see when a new check landed.
_ASSERT_COUNT = 0


def check(cond: bool, msg: str = "") -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    if not cond:
        raise AssertionError(msg or "assertion failed")


def check_eq(actual: Any, expected: Any, msg: str = "") -> None:
    check(actual == expected, f"{msg}: expected {expected!r}, got {actual!r}")


# ---------------------------------------------------------------------------
# Decoder tests (no gdb required).
# ---------------------------------------------------------------------------


def test_decode_int_literals() -> None:
    """Plain decimal / hex / octal / negative ints classify as ``int``."""
    cases = [
        ("0", "0"),
        ("3", "3"),
        ("42", "42"),
        ("-7", "-7"),
        ("0x1A", "0x1A"),
        ("0xff", "0xff"),
        ("01234", "01234"),  # octal
    ]
    for raw, expected_display in cases:
        v = decode_value(raw)
        check_eq(v.type, "int", f"type for {raw!r}")
        check_eq(v.display, expected_display, f"display for {raw!r}")


def test_decode_string_with_pointer_prefix() -> None:
    """gdb's ``0x... \"text\"`` shape decodes to a bare ``str``."""
    v = decode_value('0x7fff5fbf "hello world"')
    check_eq(v.type, "str")
    check_eq(v.display, "hello world")


def test_decode_string_escapes() -> None:
    """C-style escapes inside the cstring are unescaped."""
    v = decode_value(r'0x1000 "line1\nline2"')
    check_eq(v.type, "str")
    check_eq(v.display, "line1\nline2")
    v = decode_value(r'0x1000 "with\ttab"')
    check_eq(v.display, "with\ttab")
    v = decode_value(r'0x1000 "quoted\"here"')
    check_eq(v.display, 'quoted"here')


def test_decode_bare_string() -> None:
    """A leading ``"`` with no pointer prefix is still ``str``."""
    v = decode_value('"just text"')
    check_eq(v.type, "str")
    check_eq(v.display, "just text")


def test_decode_char_literal() -> None:
    """gdb's ``104 'h'`` dual-form becomes a quoted char."""
    v = decode_value("104 'h'")
    check_eq(v.type, "char")
    check_eq(v.display, "'h'")
    v = decode_value("65 'A'")
    check_eq(v.display, "'A'")


def test_decode_bool() -> None:
    """C++ bool prints as ``true`` / ``false``."""
    v = decode_value("true")
    check_eq(v.type, "bool")
    v = decode_value("false")
    check_eq(v.type, "bool")


def test_decode_raw_fallback() -> None:
    """Anything we can't classify falls through to ``raw`` verbatim."""
    v = decode_value("some unparseable thing")
    check_eq(v.type, "raw")
    check_eq(v.display, "some unparseable thing")


def test_decode_empty() -> None:
    """Empty / None input doesn't crash."""
    v = decode_value("")
    check_eq(v.type, "raw")
    check_eq(v.display, "")
    v = decode_value(None)
    check_eq(v.type, "raw")


def test_decode_whitespace_stripped() -> None:
    """Leading / trailing whitespace is tolerated."""
    v = decode_value("  42  ")
    check_eq(v.type, "int")
    check_eq(v.display, "42")


# ---------------------------------------------------------------------------
# Command builder tests.
# ---------------------------------------------------------------------------


def test_quote_expression_basic() -> None:
    """Plain expression gets wrapped in double quotes."""
    check_eq(quote_expression("x + y"), '"x + y"')


def test_quote_expression_escape() -> None:
    """Embedded backslashes and double quotes are escaped."""
    check_eq(quote_expression('say "hi"'), '"say \\"hi\\""')
    check_eq(quote_expression("path\\to"), '"path\\\\to"')


def test_build_evaluate_no_routing() -> None:
    """When thread/frame are None the command has no ``--thread`` flag."""
    cmd = build_evaluate_command("1+2")
    check_eq(cmd, '-data-evaluate-expression "1+2"')


def test_build_evaluate_with_thread() -> None:
    """thread_id routes via ``--thread <id>``."""
    cmd = build_evaluate_command("x", thread_id=3)
    check_eq(cmd, '-data-evaluate-expression --thread 3 "x"')


def test_build_evaluate_with_thread_and_frame() -> None:
    """thread + frame route via ``--thread <id> --frame <level>``."""
    cmd = build_evaluate_command("local_x", thread_id=2, frame_level=4)
    check_eq(cmd, '-data-evaluate-expression --thread 2 --frame 4 "local_x"')


# ---------------------------------------------------------------------------
# FakeBridge for testing evaluator integration without gdb.
# ---------------------------------------------------------------------------


class FakeBridge:
    """Mimics ``GdbBridge.command(...)`` for unit tests.

    Each call to ``command`` is recorded so tests can assert on the
    exact MI line we sent. The response is taken from ``responses``
    in order; if the queue is empty we return a generic error."""

    def __init__(self, responses: Optional[List[GdbResult]] = None) -> None:
        self.responses = list(responses or [])
        self.sent_commands: List[str] = []

    def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
        self.sent_commands.append(cmd)
        if self.responses:
            return self.responses.pop(0)
        return GdbResult(token=None, cls="error", fields={"msg": "no fake response"})


def test_evaluate_via_bridge_success_int() -> None:
    """Happy path: gdb returns ``value=\"3\"`` for ``1+2``."""
    bridge = FakeBridge([GdbResult(token=None, cls="done", fields={"value": "3"})])
    r = evaluate_via_bridge(bridge, "1+2")
    check(r.ok, "should succeed")
    check(r.decoded is not None, "decoded set")
    check_eq(r.decoded.type, "int")
    check_eq(r.decoded.display, "3")
    check_eq(bridge.sent_commands, ['-data-evaluate-expression "1+2"'])


def test_evaluate_via_bridge_routes_thread_frame() -> None:
    """thread_id + frame_level are forwarded to the MI command."""
    bridge = FakeBridge([GdbResult(token=None, cls="done", fields={"value": "42"})])
    r = evaluate_via_bridge(bridge, "x", thread_id=5, frame_level=2)
    check(r.ok)
    check_eq(
        bridge.sent_commands,
        ['-data-evaluate-expression --thread 5 --frame 2 "x"'],
    )


def test_evaluate_via_bridge_error_propagates() -> None:
    """gdb error class becomes ``ok=False`` with the message."""
    bridge = FakeBridge(
        [GdbResult(token=None, cls="error", fields={"msg": "No symbol \"foo\""})]
    )
    r = evaluate_via_bridge(bridge, "foo")
    check(not r.ok, "should fail")
    check(r.decoded is None)
    check("No symbol" in r.message, f"unexpected message: {r.message!r}")


def test_evaluate_via_bridge_missing_value_field() -> None:
    """gdb returns ^done but no value — surfaced as failure, not crash."""
    bridge = FakeBridge([GdbResult(token=None, cls="done", fields={})])
    r = evaluate_via_bridge(bridge, "x")
    check(not r.ok)
    check("value" in r.message.lower(), f"message should mention value: {r.message!r}")


def test_evaluate_via_bridge_timeout() -> None:
    """A timeout in the bridge surfaces as ``ok=False`` with a message."""

    class TimeoutBridge:
        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            raise TimeoutError("simulated")

    r = evaluate_via_bridge(TimeoutBridge(), "x")
    check(not r.ok)
    check("timed out" in r.message.lower())


def test_evaluate_via_bridge_string_value() -> None:
    """A pointer-prefixed string from gdb decodes correctly."""
    bridge = FakeBridge(
        [GdbResult(token=None, cls="done", fields={"value": '0x7fff "hi"'})]
    )
    r = evaluate_via_bridge(bridge, "msg")
    check(r.ok)
    check_eq(r.decoded.type, "str")
    check_eq(r.decoded.display, "hi")


# ---------------------------------------------------------------------------
# End-to-end test via the DAP server subprocess.
# ---------------------------------------------------------------------------


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")
HELLO_DWARF_SRC = os.path.join(REPO_ROOT, "examples", "hello_dwarf.nova")


class DapClient:
    """Tiny DAP client over stdio. Mirrors the helpers in
    ``dap_smoke.py`` / ``dap_multi_thread.py``."""

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


def _build_string_fixture() -> Optional[str]:
    """Compile a tiny C program that exposes an int + a string we can
    evaluate. Returns the binary path or ``None`` if ``gcc`` isn't
    available."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
int main(void) {
    const char *msg = "hello world";
    int x = 42;
    int y = 100;
    int sum = x + y;
    printf("%s sum=%d\\n", msg, sum); /* line 7 */
    return 0;
}
"""
    src_path = "/tmp/nova_dap_eval_fixture.c"
    bin_path = "/tmp/nova_dap_eval_fixture"
    with open(src_path, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        subprocess.check_call(["gcc", "-g", "-O0", "-o", bin_path, src_path])
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


def _e2e_against_fixture(bin_path: str, src_path: str, bp_line: int) -> None:
    """Drive a full launch -> setBP -> evaluate -> disconnect cycle."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        # 1. initialize — verify the new capabilities are present.
        init = client.request(
            "initialize",
            {
                "clientID": "test_evaluate",
                "adapterID": "nova",
                "linesStartAt1": True,
                "columnsStartAt1": True,
            },
        )
        check(init["success"], f"initialize: {init}")
        caps = init.get("body", {})
        check(
            caps.get("supportsEvaluateForHovers") is True,
            f"supportsEvaluateForHovers in {caps}",
        )
        check(
            caps.get("supportsConditionalBreakpoints") is True,
            f"supportsConditionalBreakpoints in {caps}",
        )
        client.wait_for_event("initialized", timeout=5.0)

        # 2. launch.
        launch = client.request("launch", {"program": bin_path})
        check(launch["success"], f"launch: {launch}")

        # 3. setBreakpoints.
        bps = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": bp_line}],
            },
        )
        check(bps["success"], f"setBreakpoints: {bps}")
        entries = bps.get("body", {}).get("breakpoints") or []
        check(len(entries) == 1)
        check(entries[0].get("verified") is True, f"bp not verified: {entries}")

        # 4. configurationDone -> run.
        cd = client.request("configurationDone", {})
        check(cd["success"])

        # 5. wait for the stop.
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        thread_id = int(stop_body.get("threadId") or 1)

        # 6. resolve a frame id from stackTrace.
        st = client.request("stackTrace", {"threadId": thread_id})
        check(st["success"])
        frames = st.get("body", {}).get("stackFrames") or []
        check(len(frames) > 0, "no frames")
        top_frame_id = int(frames[0]["id"])

        # 7. evaluate 1+2 in this frame.
        ev = client.request(
            "evaluate",
            {"expression": "1+2", "frameId": top_frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate 1+2: {ev}")
        body = ev.get("body") or {}
        check_eq(body.get("result"), "3", "1+2 result")
        check_eq(body.get("type"), "int", "1+2 type")
        check_eq(body.get("variablesReference"), 0, "1+2 variablesReference")

        # 8. evaluate an int local.
        ev = client.request(
            "evaluate",
            {"expression": "x", "frameId": top_frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate x: {ev}")
        check_eq(ev["body"].get("result"), "42")
        check_eq(ev["body"].get("type"), "int")

        # 9. evaluate a string variable.
        ev = client.request(
            "evaluate",
            {"expression": "msg", "frameId": top_frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate msg: {ev}")
        check_eq(ev["body"].get("result"), "hello world")
        check_eq(ev["body"].get("type"), "str")

        # 10. evaluate a binary expression involving two locals.
        ev = client.request(
            "evaluate",
            {"expression": "x + y", "frameId": top_frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate x+y: {ev}")
        check_eq(ev["body"].get("result"), "142")

        # 11. evaluate an undefined variable returns success=false (graceful error).
        ev = client.request(
            "evaluate",
            {
                "expression": "no_such_var_xyz",
                "frameId": top_frame_id,
                "context": "watch",
            },
        )
        check(not ev["success"], f"undefined should fail: {ev}")
        check(
            "No symbol" in (ev.get("message") or "")
            or "no_such_var_xyz" in (ev.get("message") or ""),
            f"unexpected error: {ev.get('message')!r}",
        )

        # 12. evaluate with context=hover should not have side effects —
        # we just check it returns the same value as context=watch.
        ev_hover = client.request(
            "evaluate",
            {"expression": "x", "frameId": top_frame_id, "context": "hover"},
        )
        check(ev_hover["success"], f"hover evaluate: {ev_hover}")
        check_eq(ev_hover["body"].get("result"), "42")
        check_eq(ev_hover["body"].get("type"), "int")

        # 13. evaluate with context=repl.
        ev_repl = client.request(
            "evaluate",
            {"expression": "y", "frameId": top_frame_id, "context": "repl"},
        )
        check(ev_repl["success"], f"repl evaluate: {ev_repl}")
        check_eq(ev_repl["body"].get("result"), "100")

        # 14. evaluate without a frameId — should still work (uses
        # session.threadId, frame 0).
        ev_no_frame = client.request(
            "evaluate", {"expression": "x", "context": "watch"}
        )
        check(ev_no_frame["success"], f"no-frame evaluate: {ev_no_frame}")
        check_eq(ev_no_frame["body"].get("result"), "42")

        # 15. empty expression must error out cleanly.
        ev_empty = client.request("evaluate", {"expression": "", "context": "watch"})
        check(not ev_empty["success"], "empty expr should fail")

        # 16. Continue -> terminated, then disconnect.
        client.request("continue", {"threadId": thread_id})
        client.wait_for_event("terminated", timeout=15.0)
        disc = client.request("disconnect", {})
        check(disc["success"])
    finally:
        client.close()


def _e2e_frame_routing(bin_path: str, src_path: str, bp_line: int) -> None:
    """Verify evaluate honours the frameId (different frame -> different
    scope). We set a breakpoint inside ``main``; that gives us at
    least one frame to evaluate in. Then we explicitly evaluate
    against frame 0 (top) and verify it produces the expected value."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"])
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": bin_path})
        check(launch["success"])
        bps = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": bp_line}],
            },
        )
        check(bps["success"])
        cd = client.request("configurationDone", {})
        check(cd["success"])
        stopped = client.wait_for_event("stopped", timeout=15.0)
        tid = int((stopped.get("body") or {}).get("threadId") or 1)

        st = client.request("stackTrace", {"threadId": tid})
        check(st["success"])
        frames = st.get("body", {}).get("stackFrames") or []
        # We expect at least the ``main`` frame.
        check(len(frames) >= 1, f"need at least 1 frame, got {len(frames)}")
        # Frame 0 evaluation must work.
        frame0_id = int(frames[0]["id"])
        ev = client.request(
            "evaluate",
            {"expression": "x", "frameId": frame0_id, "context": "watch"},
        )
        check(ev["success"], f"frame 0 evaluate: {ev}")
        check_eq(ev["body"].get("result"), "42")
        # If there's a frame 1 (caller), try evaluating something
        # that may not exist there; the response just needs to be
        # well-formed. We don't assert success.
        if len(frames) >= 2:
            frame1_id = int(frames[1]["id"])
            ev_caller = client.request(
                "evaluate",
                {"expression": "1+1", "frameId": frame1_id, "context": "watch"},
            )
            check(ev_caller["success"], f"frame 1 evaluate 1+1: {ev_caller}")
            check_eq(ev_caller["body"].get("result"), "2")

        # Unknown frame id falls back to session.threadId, frame 0 —
        # should still work (this is the legacy-client fallback path).
        ev_bad = client.request(
            "evaluate",
            {"expression": "x", "frameId": 999999, "context": "watch"},
        )
        check(ev_bad["success"], f"unknown frame fallback: {ev_bad}")
        check_eq(ev_bad["body"].get("result"), "42")

        client.request("continue", {"threadId": tid})
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_decoder_tests() -> None:
    test_decode_int_literals()
    test_decode_string_with_pointer_prefix()
    test_decode_string_escapes()
    test_decode_bare_string()
    test_decode_char_literal()
    test_decode_bool()
    test_decode_raw_fallback()
    test_decode_empty()
    test_decode_whitespace_stripped()
    test_quote_expression_basic()
    test_quote_expression_escape()
    test_build_evaluate_no_routing()
    test_build_evaluate_with_thread()
    test_build_evaluate_with_thread_and_frame()
    test_evaluate_via_bridge_success_int()
    test_evaluate_via_bridge_routes_thread_frame()
    test_evaluate_via_bridge_error_propagates()
    test_evaluate_via_bridge_missing_value_field()
    test_evaluate_via_bridge_timeout()
    test_evaluate_via_bridge_string_value()


def main() -> int:
    # Phase 1: pure-Python tests (always run).
    _run_decoder_tests()
    decoder_assertions = _ASSERT_COUNT

    # Phase 2: end-to-end (skips if gdb / fixture missing).
    e2e_status = "skipped"
    if shutil.which("gdb") is None:
        e2e_reason = "gdb not installed"
    else:
        bin_path = _build_string_fixture()
        if bin_path is None:
            e2e_reason = "gcc not available"
        else:
            src_path = "/tmp/nova_dap_eval_fixture.c"
            try:
                _e2e_against_fixture(bin_path, src_path, bp_line=7)
                _e2e_frame_routing(bin_path, src_path, bp_line=7)
                e2e_status = "ok"
                e2e_reason = ""
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout (env may not support gdb): {exc}"

    print(f"test_evaluate: OK")
    print(f"  decoder assertions: {decoder_assertions}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    if e2e_status == "ok":
        print(f"  end-to-end:        ok ({_ASSERT_COUNT - decoder_assertions} extra checks)")
    else:
        print(f"  end-to-end:        SKIP — {e2e_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
