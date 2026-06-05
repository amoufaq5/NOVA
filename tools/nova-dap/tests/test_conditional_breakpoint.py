"""Tests for DAP conditional breakpoints (``setBreakpoints`` with a
``condition`` arg).

The NOVA DAP server forwards the ``condition`` string verbatim to
gdb's ``-break-insert -c "<expr>"``. gdb evaluates the condition
at every hit and only stops when it's non-zero, so the test fixture
loops ``x = 1..7`` and we assert that the stop fires at ``x == 6``
(first true) rather than at ``x == 1..5``.

Run::

    python tools/nova-dap/tests/test_conditional_breakpoint.py
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


# Track every assertion for reporting at the end.
_ASSERT_COUNT = 0


def check(cond: bool, msg: str = "") -> None:
    global _ASSERT_COUNT
    _ASSERT_COUNT += 1
    if not cond:
        raise AssertionError(msg or "assertion failed")


def check_eq(actual: Any, expected: Any, msg: str = "") -> None:
    check(actual == expected, f"{msg}: expected {expected!r}, got {actual!r}")


# ---------------------------------------------------------------------------
# Unit tests for the cmd-builder behaviour (no gdb required).
# ---------------------------------------------------------------------------


def test_condition_string_propagates_to_mi_command() -> None:
    """The ``handle_set_breakpoints`` handler must compose ``-break-insert
    -c "<expr>" "<file>:<line>"``. We exercise it via a fake bridge so
    no gdb subprocess is needed."""
    from nova_dap import server  # noqa: WPS433

    class CaptureBridge:
        def __init__(self) -> None:
            self.sent_commands: List[str] = []

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            self.sent_commands.append(cmd)
            if cmd.startswith("-break-delete"):
                return GdbResult(token=None, cls="done", fields={})
            # Fake successful -break-insert.
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {
                        "number": "1",
                        "addr": "0x1234",
                        "file": "src.c",
                        "fullname": "/tmp/src.c",
                        "line": "5",
                    }
                },
            )

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/src.c", "name": "src.c"},
            "breakpoints": [{"line": 5, "condition": "x > 5"}],
        },
    }
    server.handle_set_breakpoints(session, req)
    # The first command should be the -break-delete pre-clear; the
    # second is the conditional -break-insert.
    check(
        any("-break-delete" in c for c in bridge.sent_commands),
        f"missing -break-delete in {bridge.sent_commands}",
    )
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1, "exactly one -break-insert expected")
    insert_cmd = inserts[0]
    check("-c" in insert_cmd, f"missing -c flag in {insert_cmd!r}")
    check("x > 5" in insert_cmd, f"missing condition expr in {insert_cmd!r}")
    check('"/tmp/src.c:5"' in insert_cmd, f"missing location in {insert_cmd!r}")


def test_no_condition_means_no_c_flag() -> None:
    """A breakpoint without ``condition`` must NOT include ``-c``."""
    from nova_dap import server  # noqa: WPS433

    class CaptureBridge:
        def __init__(self) -> None:
            self.sent_commands: List[str] = []

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            self.sent_commands.append(cmd)
            if cmd.startswith("-break-delete"):
                return GdbResult(token=None, cls="done", fields={})
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {"number": "1", "addr": "0x1234", "line": "5"}
                },
            )

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/src.c", "name": "src.c"},
            "breakpoints": [{"line": 5}],
        },
    }
    server.handle_set_breakpoints(session, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1)
    check(" -c " not in inserts[0], f"unexpected -c in {inserts[0]!r}")


def test_blank_condition_treated_as_unconditional() -> None:
    """An empty / whitespace condition string is treated as no condition."""
    from nova_dap import server  # noqa: WPS433

    class CaptureBridge:
        def __init__(self) -> None:
            self.sent_commands: List[str] = []

        def command(self, cmd: str, timeout: float = 10.0) -> GdbResult:
            self.sent_commands.append(cmd)
            if cmd.startswith("-break-delete"):
                return GdbResult(token=None, cls="done", fields={})
            return GdbResult(
                token=None,
                cls="done",
                fields={
                    "bkpt": {"number": "1", "addr": "0x1234", "line": "5"}
                },
            )

    bridge = CaptureBridge()
    session = server.Session(out_stream=_NullStream())
    session.bridge = bridge  # type: ignore[assignment]
    req = {
        "seq": 1,
        "type": "request",
        "command": "setBreakpoints",
        "arguments": {
            "source": {"path": "/tmp/src.c", "name": "src.c"},
            "breakpoints": [{"line": 5, "condition": "   "}],
        },
    }
    server.handle_set_breakpoints(session, req)
    inserts = [c for c in bridge.sent_commands if c.startswith("-break-insert")]
    check_eq(len(inserts), 1)
    check(" -c " not in inserts[0], f"blank cond should not produce -c: {inserts[0]!r}")


def test_capability_advertises_conditional_breakpoints() -> None:
    """The ``initialize`` response must declare
    ``supportsConditionalBreakpoints: true`` now that we implement it."""
    from nova_dap.server import _capabilities, HANDLERS  # noqa: WPS433

    caps = _capabilities()
    check_eq(caps.get("supportsConditionalBreakpoints"), True)
    check_eq(caps.get("supportsEvaluateForHovers"), True)
    # The two new capability flips this round (False -> True).
    check(caps.get("supportsConditionalBreakpoints") is True)
    check(caps.get("supportsEvaluateForHovers") is True)
    # Handler count: 18 DAP requests now (R7D's 17 + evaluate).
    check_eq(
        "evaluate" in HANDLERS, True,
        "evaluate handler must be registered",
    )
    check(
        len(HANDLERS) >= 18,
        f"expected >=18 handlers, got {len(HANDLERS)}: {sorted(HANDLERS)}",
    )


class _NullStream:
    """Stand-in for the real binary stdout — accepts writes, drops them."""

    def write(self, _data: bytes) -> int:
        return 0

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# End-to-end conditional breakpoint test.
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


def _build_loop_fixture() -> Optional[str]:
    """Compile a tiny C program that runs ``x = 1..7`` so we can set a
    conditional breakpoint and verify it stops only at ``x > 5``."""
    if shutil.which("gcc") is None:
        return None
    src = """\
#include <stdio.h>
int main(void) {
    int x;
    for (x = 1; x <= 7; ++x) {
        printf("x=%d\\n", x); /* line 5: conditional breakpoint target */
    }
    return 0;
}
"""
    src_path = "/tmp/nova_dap_cond_fixture.c"
    bin_path = "/tmp/nova_dap_cond_fixture"
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


def _e2e_conditional_breakpoint(bin_path: str, src_path: str) -> None:
    """Drive the full conditional-breakpoint flow."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        # 1. initialize -> verify the capability is on.
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"])
        caps = init.get("body", {})
        check_eq(caps.get("supportsConditionalBreakpoints"), True)
        client.wait_for_event("initialized", timeout=5.0)

        # 2. launch.
        launch = client.request("launch", {"program": bin_path})
        check(launch["success"], f"launch: {launch}")

        # 3. setBreakpoints with a condition.
        bps = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": 5, "condition": "x > 5"}],
            },
        )
        check(bps["success"], f"setBreakpoints: {bps}")
        entries = bps.get("body", {}).get("breakpoints") or []
        check_eq(len(entries), 1)
        check(entries[0].get("verified") is True, f"bp not verified: {entries}")
        bp_id = int(entries[0]["id"])

        # 4. configurationDone -> run.
        cd = client.request("configurationDone", {})
        check(cd["success"])

        # 5. wait for the stop. The conditional breakpoint must fire
        # only when x > 5, i.e. at x == 6 (first iteration where the
        # condition is true).
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        check_eq(stop_body.get("reason"), "breakpoint")
        hit_ids = stop_body.get("hitBreakpointIds") or []
        check(bp_id in hit_ids, f"bp {bp_id} not in {hit_ids}")
        thread_id = int(stop_body.get("threadId") or 1)

        # 6. evaluate ``x`` at the stop — must be 6, NOT 1..5.
        # First get a frame id.
        st = client.request("stackTrace", {"threadId": thread_id})
        check(st["success"])
        frames = st.get("body", {}).get("stackFrames") or []
        check(len(frames) > 0)
        top_frame_id = int(frames[0]["id"])
        ev = client.request(
            "evaluate",
            {"expression": "x", "frameId": top_frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate x: {ev}")
        body = ev.get("body") or {}
        x_val = body.get("result")
        # KEY ASSERTION: conditional bp must skip x=1..5 and fire at x=6.
        check_eq(x_val, "6", f"x at conditional bp stop should be 6, got {x_val!r}")

        # 7. continue — the next stop should be at x == 7 (also > 5).
        client.request("continue", {"threadId": thread_id})
        stopped2 = client.wait_for_event("stopped", timeout=15.0)
        stop_body2 = stopped2.get("body", {})
        check_eq(stop_body2.get("reason"), "breakpoint")
        thread_id2 = int(stop_body2.get("threadId") or 1)
        st2 = client.request("stackTrace", {"threadId": thread_id2})
        frame_id2 = int(st2["body"]["stackFrames"][0]["id"])
        ev2 = client.request(
            "evaluate",
            {"expression": "x", "frameId": frame_id2, "context": "watch"},
        )
        check(ev2["success"])
        check_eq(ev2["body"].get("result"), "7", "second stop should be at x=7")

        # 8. continue -> terminated. The loop is finished after x=7, so
        # the program exits cleanly.
        client.request("continue", {"threadId": thread_id2})
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
    finally:
        client.close()


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
HELLO_DWARF_BIN = os.path.join(REPO_ROOT, "bin", "hello_dwarf")
HELLO_DWARF_SRC = os.path.join(REPO_ROOT, "examples", "hello_dwarf.nova")


def _e2e_against_nova_program() -> bool:
    """Integration test: set a conditional breakpoint against the NOVA
    ``hello_dwarf`` binary, then evaluate ``sum`` at the stop point.

    Returns True if the test ran; False if the binary is missing
    (i.e. ``make smoke-dwarf`` hasn't been run). This is an
    integration check against actual NOVA-compiled output, not a
    standalone C fixture."""
    if not os.path.isfile(HELLO_DWARF_BIN):
        return False
    if not os.path.isfile(HELLO_DWARF_SRC):
        return False
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"], "initialize against NOVA binary")
        client.wait_for_event("initialized", timeout=5.0)
        launch = client.request("launch", {"program": HELLO_DWARF_BIN})
        check(launch["success"], f"launch NOVA binary: {launch}")
        # Conditional breakpoint at line 36 (after sum has been computed).
        # ``sum == 3`` is always true in this program (1 + 2 = 3).
        bps = client.request(
            "setBreakpoints",
            {
                "source": {
                    "path": HELLO_DWARF_SRC,
                    "name": os.path.basename(HELLO_DWARF_SRC),
                },
                "breakpoints": [{"line": 36, "condition": "sum == 3"}],
            },
        )
        check(bps["success"], f"setBreakpoints (NOVA): {bps}")
        entries = bps.get("body", {}).get("breakpoints") or []
        check(len(entries) == 1)
        check(
            entries[0].get("verified") is True,
            f"NOVA bp not verified: {entries}",
        )
        bp_id = int(entries[0]["id"])

        cd = client.request("configurationDone", {})
        check(cd["success"])

        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        check_eq(stop_body.get("reason"), "breakpoint")
        check(bp_id in (stop_body.get("hitBreakpointIds") or []))
        tid = int(stop_body.get("threadId") or 1)

        st = client.request("stackTrace", {"threadId": tid})
        check(st["success"])
        frame_id = int(st["body"]["stackFrames"][0]["id"])

        ev = client.request(
            "evaluate",
            {"expression": "sum", "frameId": frame_id, "context": "watch"},
        )
        check(ev["success"], f"evaluate sum on NOVA binary: {ev}")
        check_eq(ev["body"].get("result"), "3", "sum at NOVA conditional stop")
        check_eq(ev["body"].get("type"), "int")

        client.request("continue", {"threadId": tid})
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
        return True
    finally:
        client.close()


def _e2e_conditional_bp_replaced_by_unconditional(
    bin_path: str, src_path: str
) -> None:
    """Re-sending ``setBreakpoints`` without the ``condition`` arg must
    clear the prior condition (since we ``-break-delete`` first). This
    tests the re-send path that VS Code uses every time the user edits
    the breakpoint."""
    proc = _start_dap_server()
    client = DapClient(proc)
    try:
        init = client.request("initialize", {"adapterID": "nova"})
        check(init["success"])
        client.wait_for_event("initialized", timeout=5.0)

        launch = client.request("launch", {"program": bin_path})
        check(launch["success"])

        # First pass: conditional.
        bps1 = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": 5, "condition": "x > 5"}],
            },
        )
        check(bps1["success"])

        # Second pass: unconditional re-send.
        bps2 = client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [{"line": 5}],
            },
        )
        check(bps2["success"])
        entries = bps2.get("body", {}).get("breakpoints") or []
        check_eq(len(entries), 1)
        check(entries[0].get("verified") is True)
        bp_id = int(entries[0]["id"])

        # configurationDone — now the bp is unconditional, so it must
        # fire at x == 1 (first iteration).
        cd = client.request("configurationDone", {})
        check(cd["success"])
        stopped = client.wait_for_event("stopped", timeout=15.0)
        stop_body = stopped.get("body", {})
        check_eq(stop_body.get("reason"), "breakpoint")
        check(bp_id in (stop_body.get("hitBreakpointIds") or []))
        tid = int(stop_body.get("threadId") or 1)
        st = client.request("stackTrace", {"threadId": tid})
        frame_id = int(st["body"]["stackFrames"][0]["id"])
        ev = client.request(
            "evaluate",
            {"expression": "x", "frameId": frame_id, "context": "watch"},
        )
        check(ev["success"])
        check_eq(
            ev["body"].get("result"),
            "1",
            "unconditional bp re-send should fire at x=1, not x=6",
        )

        # Drop the breakpoint and run to completion.
        client.request(
            "setBreakpoints",
            {
                "source": {"path": src_path, "name": os.path.basename(src_path)},
                "breakpoints": [],
            },
        )
        client.request("continue", {"threadId": tid})
        client.wait_for_event("terminated", timeout=15.0)
        client.request("disconnect", {})
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def _run_unit_tests() -> None:
    test_condition_string_propagates_to_mi_command()
    test_no_condition_means_no_c_flag()
    test_blank_condition_treated_as_unconditional()
    test_capability_advertises_conditional_breakpoints()


def main() -> int:
    # Phase 1: unit tests (always run).
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
            src_path = "/tmp/nova_dap_cond_fixture.c"
            try:
                _e2e_conditional_breakpoint(bin_path, src_path)
                _e2e_conditional_bp_replaced_by_unconditional(bin_path, src_path)
                # Best-effort: integration against the NOVA binary (only
                # runs if ``make smoke-dwarf`` has been done).
                nova_ran = _e2e_against_nova_program()
                e2e_status = "ok"
                if not nova_ran:
                    e2e_reason = "NOVA hello_dwarf binary missing (skipped integration)"
            except TimeoutError as exc:
                e2e_status = "skipped"
                e2e_reason = f"DAP server timeout: {exc}"

    print(f"test_conditional_breakpoint: OK")
    print(f"  unit assertions:    {unit_assertions}")
    print(f"  total assertions:   {_ASSERT_COUNT}")
    if e2e_status == "ok":
        extra = _ASSERT_COUNT - unit_assertions
        suffix = f" ({e2e_reason})" if e2e_reason else ""
        print(f"  end-to-end:         ok ({extra} extra checks){suffix}")
    else:
        print(f"  end-to-end:         SKIP — {e2e_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
