"""End-to-end smoke test for the Nova DAP server.

Spawns ``python -m nova_dap.server`` as a subprocess, drives it with
the canonical VS Code request sequence, and verifies that the
breakpoint at ``examples/hello_dwarf.nova:20`` actually fires and the
program runs to completion.

Skips cleanly (exit code 0, prints ``SKIP`` line) if either ``gdb`` or
the pre-built ``bin/hello_dwarf`` binary is missing. Run after::

    make smoke-dwarf      # builds bin/hello_dwarf

The test runs without any external Python dependencies — DAP framing,
JSON, and subprocess management are stdlib-only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

# Make ``nova_dap`` importable when running from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-dap/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
BINARY = os.path.join(REPO_ROOT, "bin", "hello_dwarf")
SOURCE = os.path.join(REPO_ROOT, "examples", "hello_dwarf.nova")
BREAKPOINT_LINE = 20  # `fn main()` in hello_dwarf.nova


def _skip(reason: str) -> int:
    print(f"dap_smoke: SKIP — {reason}")
    return 0


class DapClient:
    """Tiny DAP client that talks to ``python -m nova_dap.server`` over stdio."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._seq = 1
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._messages_cond = threading.Condition()
        self.responses: Dict[int, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---- request/response wiring ------------------------------------------

    def request(self, command: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        return self._await_response(seq)

    def _await_response(self, seq: int, timeout: float = 15.0) -> Dict[str, Any]:
        end = time.monotonic() + timeout
        with self._messages_cond:
            while True:
                if seq in self.responses:
                    return self.responses.pop(seq)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no DAP response for seq {seq}")
                self._messages_cond.wait(timeout=remaining)

    def wait_for_event(
        self, name: str, timeout: float = 15.0, consume: bool = True
    ) -> Dict[str, Any]:
        end = time.monotonic() + timeout
        with self._messages_cond:
            while True:
                for i, ev in enumerate(self.events):
                    if ev.get("event") == name:
                        if consume:
                            return self.events.pop(i)
                        return ev
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no DAP event '{name}' within {timeout}s")
                self._messages_cond.wait(timeout=remaining)

    # ---- framing ----------------------------------------------------------

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
                with self._messages_cond:
                    if parsed.get("type") == "response":
                        self.responses[parsed.get("request_seq", -1)] = parsed
                    else:
                        self.events.append(parsed)
                    self._messages_cond.notify_all()
        # Wake any blocked waiters on EOF.
        with self._messages_cond:
            self._messages_cond.notify_all()

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


def main() -> int:
    if shutil.which("gdb") is None:
        return _skip("gdb is not installed")
    if not os.path.isfile(BINARY):
        return _skip(f"binary missing: {BINARY} (run `make smoke-dwarf` first)")
    if not os.path.isfile(SOURCE):
        return _skip(f"source missing: {SOURCE}")

    env = os.environ.copy()
    env["PYTHONPATH"] = _PKG_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "nova_dap.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
    )
    client = DapClient(proc)

    try:
        # 1. initialize -----------------------------------------------------
        init = client.request(
            "initialize",
            {
                "clientID": "dap-smoke",
                "adapterID": "nova",
                "linesStartAt1": True,
                "columnsStartAt1": True,
                "pathFormat": "path",
            },
        )
        assert init["success"], f"initialize failed: {init}"
        caps = init.get("body", {})
        assert caps.get("supportsConfigurationDoneRequest") is True, caps
        assert caps.get("supportsStepBack") is False, caps

        # initialized event must follow.
        client.wait_for_event("initialized", timeout=5.0)

        # 2. launch ---------------------------------------------------------
        launch = client.request(
            "launch",
            {"program": BINARY, "stopOnEntry": False, "noDebug": False},
        )
        assert launch["success"], f"launch failed: {launch}"

        # 3. setBreakpoints at hello_dwarf.nova:20 -------------------------
        bps = client.request(
            "setBreakpoints",
            {
                "source": {"path": SOURCE, "name": os.path.basename(SOURCE)},
                "breakpoints": [{"line": BREAKPOINT_LINE}],
            },
        )
        assert bps["success"], f"setBreakpoints failed: {bps}"
        body = bps.get("body", {})
        bp_entries = body.get("breakpoints") or []
        assert len(bp_entries) == 1, f"expected 1 breakpoint, got {bp_entries}"
        bp = bp_entries[0]
        assert bp.get("verified") is True, f"breakpoint not verified: {bp}"
        assert bp.get("line") == BREAKPOINT_LINE, bp
        assert "id" in bp, bp

        # 4. configurationDone — runs the program ---------------------------
        cd = client.request("configurationDone", {})
        assert cd["success"], f"configurationDone failed: {cd}"

        # 5. wait for the breakpoint to hit ---------------------------------
        stopped = client.wait_for_event("stopped", timeout=20.0)
        stop_body = stopped.get("body", {})
        assert stop_body.get("reason") == "breakpoint", stop_body
        hit_ids = stop_body.get("hitBreakpointIds") or []
        assert bp["id"] in hit_ids, f"breakpoint {bp['id']} not in {hit_ids}"

        # 6. stack trace + scopes + variables exercise --------------------
        st = client.request("stackTrace", {"threadId": 1})
        assert st["success"], st
        frames = st.get("body", {}).get("stackFrames") or []
        assert frames, f"no frames in stackTrace: {st}"
        top = frames[0]
        assert top.get("name") == "main", top
        assert top.get("line") == BREAKPOINT_LINE, top

        sc = client.request("scopes", {"frameId": top["id"]})
        assert sc["success"], sc
        scopes = sc.get("body", {}).get("scopes") or []
        assert scopes and scopes[0]["name"] == "Locals", scopes

        vars_resp = client.request(
            "variables", {"variablesReference": scopes[0]["variablesReference"]}
        )
        assert vars_resp["success"], vars_resp
        # We don't assert the exact variable list (DWARF DIEs for
        # locals aren't shipping yet) — only that the call succeeds.

        # 7. continue → terminated -----------------------------------------
        cont = client.request("continue", {"threadId": 1})
        assert cont["success"], cont
        client.wait_for_event("terminated", timeout=15.0)

        # 8. disconnect ----------------------------------------------------
        disc = client.request("disconnect", {})
        assert disc["success"], disc
    finally:
        client.close()

    print("dap_smoke: OK")
    print(f"  binary:     {BINARY}")
    print(f"  source:     {SOURCE}")
    print(f"  breakpoint: line {BREAKPOINT_LINE} (id={bp.get('id')})")
    print(f"  top frame:  {top['name']} at line {top['line']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
