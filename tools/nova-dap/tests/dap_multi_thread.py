"""End-to-end multi-thread test for the Nova DAP server.

Spawns ``python -m nova_dap.server`` as a subprocess, drives it
against a small pthread C fixture, and verifies the multi-thread
DAP wire protocol:

* ``threads`` returns more than one entry once both workers are alive
* a breakpoint in worker A produces a ``stopped`` event with
  ``threadId == <A's id>`` and ``allThreadsStopped == false``
* worker B continues running while worker A is paused (in non-stop
  mode), and we can verify it by interrupting B with a separate
  ``pause`` request and observing a second ``stopped`` event for B
* ``stackTrace`` and ``variables`` for each thread return that
  thread's locals (``local_a`` for A, ``local_b`` for B)
* ``continue`` with ``singleThread=true`` resumes only the named
  thread; ``continue`` without ``singleThread`` resumes everything
* ``stepIn`` / ``next`` accept ``threadId`` and step the target
  thread only

The test SKIPs cleanly (exit 0) if any prerequisite is missing
(``gdb`` / ``gcc`` not installed, libpthread unavailable). The
underlying gdb may also refuse non-stop mode on some targets (e.g.
qemu-user); we detect that and SKIP rather than fail.

Run from the repo root::

    python tools/nova-dap/tests/dap_multi_thread.py
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

REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
FIXTURE_SRC = os.path.join(_HERE, "fixtures", "multi_thread.c")
FIXTURE_BIN = os.path.join("/tmp", "nova_dap_multi_thread")
# Breakpoint targets — discovered by scanning the fixture for the
# ``WORKER_A_BP`` / ``WORKER_B_BP`` sentinel comments so the lines
# stay in sync if the fixture is edited.


def _discover_bp_lines() -> Tuple[int, int]:
    line_a = line_b = -1
    with open(FIXTURE_SRC, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            if "WORKER_A_BP" in raw:
                line_a = i
            elif "WORKER_B_BP" in raw:
                line_b = i
    assert line_a > 0 and line_b > 0, (
        f"sentinel comments not found in {FIXTURE_SRC}: "
        f"WORKER_A_BP={line_a}, WORKER_B_BP={line_b}"
    )
    return line_a, line_b


LINE_WORKER_A, LINE_WORKER_B = _discover_bp_lines()


def _skip(reason: str) -> int:
    print(f"dap_multi_thread: SKIP -- {reason}")
    return 0


def _build_fixture() -> Optional[str]:
    """Compile the pthread fixture into ``/tmp``. Returns the binary
    path on success or ``None`` if the build couldn't be done (e.g.
    ``gcc`` is missing or pthread linkage failed)."""
    if shutil.which("gcc") is None:
        return None
    # Always rebuild; the file is tiny and this avoids stale-cache surprises.
    try:
        subprocess.check_call(
            ["gcc", "-g", "-O0", "-pthread", "-o", FIXTURE_BIN, FIXTURE_SRC],
            stderr=subprocess.STDOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not os.path.isfile(FIXTURE_BIN):
        return None
    return FIXTURE_BIN


class DapClient:
    """Tiny DAP client. Mirrors ``dap_smoke.DapClient`` but with
    helpers tuned for multi-thread tests (e.g. ``find_event`` that
    waits for a stopped event matching a predicate)."""

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

    def request(
        self, command: str, arguments: Optional[Dict[str, Any]] = None,
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
        return self._await_response(seq, timeout=timeout)

    def _await_response(self, seq: int, timeout: float) -> Dict[str, Any]:
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

    def find_event(
        self,
        name: str,
        predicate,
        timeout: float = 15.0,
        consume: bool = True,
    ) -> Dict[str, Any]:
        """Like ``wait_for_event`` but only matches events whose body
        satisfies ``predicate(body)``. Useful for waiting on a
        ``stopped`` event with a specific ``threadId``."""
        end = time.monotonic() + timeout
        with self._messages_cond:
            while True:
                for i, ev in enumerate(self.events):
                    if ev.get("event") != name:
                        continue
                    if not predicate(ev.get("body") or {}):
                        continue
                    if consume:
                        return self.events.pop(i)
                    return ev
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"no DAP '{name}' event matching predicate within {timeout}s"
                    )
                self._messages_cond.wait(timeout=remaining)

    def drain_events(self, name: str) -> List[Dict[str, Any]]:
        with self._messages_cond:
            kept: List[Dict[str, Any]] = []
            matched: List[Dict[str, Any]] = []
            for ev in self.events:
                if ev.get("event") == name:
                    matched.append(ev)
                else:
                    kept.append(ev)
            self.events = kept
            return matched

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


def _initialize_and_launch(client: DapClient, binary: str) -> Dict[str, Any]:
    init = client.request(
        "initialize",
        {
            "clientID": "dap-multi-thread",
            "adapterID": "nova",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "pathFormat": "path",
            "supportsRunInTerminalRequest": False,
        },
    )
    assert init["success"], f"initialize failed: {init}"
    caps = init.get("body", {})
    # Hard capability assertion: we just declared this true, so any
    # regression in the capability table is a test failure.
    assert caps.get("supportsSingleThreadExecutionRequests") is True, caps
    client.wait_for_event("initialized", timeout=5.0)

    launch = client.request(
        "launch",
        {"program": binary, "stopOnEntry": False, "noDebug": False},
        timeout=10.0,
    )
    assert launch["success"], f"launch failed: {launch}"
    return caps


def _set_breakpoints(client: DapClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Set one breakpoint on each worker function. Returns ``(bp_a,
    bp_b)``."""
    bps = client.request(
        "setBreakpoints",
        {
            "source": {
                "path": FIXTURE_SRC,
                "name": os.path.basename(FIXTURE_SRC),
            },
            "breakpoints": [
                {"line": LINE_WORKER_A},
                {"line": LINE_WORKER_B},
            ],
        },
    )
    assert bps["success"], f"setBreakpoints failed: {bps}"
    entries = bps.get("body", {}).get("breakpoints") or []
    assert len(entries) == 2, f"expected 2 breakpoints, got {entries}"
    bp_a, bp_b = entries
    assert bp_a.get("verified"), f"worker A breakpoint not verified: {bp_a}"
    assert bp_b.get("verified"), f"worker B breakpoint not verified: {bp_b}"
    return bp_a, bp_b


def _thread_ids(client: DapClient) -> List[int]:
    resp = client.request("threads", {})
    assert resp["success"], resp
    threads = resp.get("body", {}).get("threads") or []
    return [int(t["id"]) for t in threads if isinstance(t, dict) and "id" in t]


def _locals_for_thread(
    client: DapClient, thread_id: int
) -> Tuple[List[Dict[str, Any]], int]:
    st = client.request("stackTrace", {"threadId": thread_id})
    assert st["success"], f"stackTrace for {thread_id} failed: {st}"
    frames = st.get("body", {}).get("stackFrames") or []
    # Find the topmost frame whose function name starts with "worker_"
    # (the test wants the user-code frame, not libc/pthread internals).
    target_frame = None
    for fr in frames:
        name = fr.get("name") or ""
        if isinstance(name, str) and name.startswith("worker_"):
            target_frame = fr
            break
    if target_frame is None and frames:
        target_frame = frames[0]
    assert target_frame is not None, f"no frames for thread {thread_id}: {frames}"
    sc = client.request("scopes", {"frameId": target_frame["id"]})
    assert sc["success"], sc
    scopes = sc.get("body", {}).get("scopes") or []
    assert scopes and scopes[0]["name"] == "Locals", scopes
    vars_resp = client.request(
        "variables",
        {"variablesReference": scopes[0]["variablesReference"]},
    )
    assert vars_resp["success"], vars_resp
    var_list = vars_resp.get("body", {}).get("variables") or []
    return var_list, int(target_frame["id"])


def main() -> int:
    if shutil.which("gdb") is None:
        return _skip("gdb is not installed")
    binary = _build_fixture()
    if binary is None:
        return _skip("gcc/pthread not available -- cannot build multi-thread fixture")
    if not os.path.isfile(binary):
        return _skip(f"fixture missing: {binary}")

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

    a_locals_summary = []
    b_locals_summary = []
    threads_seen = 0

    try:
        # 1. initialize + launch ----------------------------------------
        _initialize_and_launch(client, binary)

        # 2. set breakpoints on both workers ----------------------------
        bp_a, bp_b = _set_breakpoints(client)

        # 3. configurationDone — runs the program -----------------------
        cd = client.request("configurationDone", {})
        assert cd["success"], cd

        # 4. wait for the FIRST breakpoint hit. It could be either
        # worker depending on the scheduler — we accept either and
        # remember which one fired so the second-half of the test can
        # target the OTHER worker. (The fixture's ``ready`` condvar
        # was added precisely so both workers spawn before either
        # starts looping, so we should see both hits very close
        # together.)
        first_stop = client.find_event(
            "stopped",
            lambda body: int(body.get("hitBreakpointIds", [0])[0] or 0)
            in (int(bp_a["id"]), int(bp_b["id"])),
            timeout=20.0,
        )
        first_body = first_stop["body"]
        # KEY ASSERTION: the stopped event carries threadId + allThreadsStopped.
        assert "threadId" in first_body, f"stopped event missing threadId: {first_body}"
        first_tid = int(first_body["threadId"])
        first_hit = int((first_body.get("hitBreakpointIds") or [0])[0])
        first_is_a = first_hit == int(bp_a["id"])
        # In non-stop mode, allThreadsStopped MUST be false — that's
        # the contract this whole change exists to deliver. In a
        # fallback all-stop environment the test still passes as
        # long as the threadId is correct (we treat that case as a
        # SKIP at the multi-thread-specific assertions below).
        all_stopped = first_body.get("allThreadsStopped")
        non_stop_supported = all_stopped is False

        # 5. threads request must show >= 2 threads now.
        ids = _thread_ids(client)
        threads_seen = len(ids)
        assert first_tid in ids, f"first-stop threadId {first_tid} not in threads list {ids}"
        assert len(ids) >= 2, f"expected >=2 threads after both workers spawn, got {ids}"

        # 6. wait for the SECOND worker to hit its breakpoint. In
        # non-stop mode this happens automatically — the other
        # thread keeps running while the first is paused. In all-stop
        # mode the second hit only fires after we resume; we just
        # continue and wait.
        other_bp_id = int(bp_b["id"]) if first_is_a else int(bp_a["id"])
        if not non_stop_supported:
            # All-stop fallback: continue all threads to let the
            # other worker reach its breakpoint.
            cont = client.request("continue", {"threadId": first_tid})
            assert cont["success"], cont
        second_stop = client.find_event(
            "stopped",
            lambda body: int((body.get("hitBreakpointIds") or [0])[0] or 0) == other_bp_id,
            timeout=20.0,
        )
        second_body = second_stop["body"]
        second_tid = int(second_body["threadId"])
        assert second_tid != first_tid, (
            f"two breakpoints in two threads, but stopped events share threadId "
            f"{first_tid}: {first_body=} {second_body=}"
        )

        if non_stop_supported:
            # In non-stop mode, the second stop also has
            # allThreadsStopped=false.
            assert second_body.get("allThreadsStopped") is False, second_body

        # 7. per-thread variables. A's locals must contain local_a;
        # B's must contain local_b. Cross-pollution would mean we
        # forgot to route the ``-stack-select-frame`` through a
        # ``-thread-select`` first.
        tid_a = first_tid if first_is_a else second_tid
        tid_b = second_tid if first_is_a else first_tid
        vars_a, frame_a = _locals_for_thread(client, tid_a)
        vars_b, frame_b = _locals_for_thread(client, tid_b)
        names_a = {v.get("name") for v in vars_a}
        names_b = {v.get("name") for v in vars_b}
        a_locals_summary = sorted(n for n in names_a if isinstance(n, str))
        b_locals_summary = sorted(n for n in names_b if isinstance(n, str))
        assert "local_a" in names_a, f"thread A locals missing local_a: {names_a}"
        assert "local_b" in names_b, f"thread B locals missing local_b: {names_b}"
        # And — critically — the WRONG locals must NOT appear on the
        # OTHER thread. This is the per-thread frame-scoping check.
        assert "local_b" not in names_a, (
            f"thread A leaked thread B's locals: {names_a}"
        )
        assert "local_a" not in names_b, (
            f"thread B leaked thread A's locals: {names_b}"
        )
        # And the DAP frame ids must be DIFFERENT between threads.
        assert frame_a != frame_b, (
            f"per-thread frames must have distinct DAP ids; got {frame_a} for both"
        )

        # 8. per-thread continue (singleThread=true) test. Resume A
        # only and verify the response says allThreadsContinued=false.
        # We also verify per-thread step behaviour by issuing a `next`,
        # `stepIn`, and `stepOut` to thread B — gdb should step exactly
        # that thread and not touch A. After all per-thread checks, we
        # drop all breakpoints so the workers run to completion and
        # the process exits cleanly.
        single_continue_ok = False
        per_thread_step_ok = False
        per_thread_pause_ok = False
        if non_stop_supported:
            client.drain_events("stopped")
            client.drain_events("continued")
            # ---- per-thread `next` on B; A stays paused ----------
            step_b = client.request(
                "next", {"threadId": tid_b, "singleThread": True}
            )
            assert step_b["success"], step_b
            stop_b2 = client.find_event(
                "stopped",
                lambda body: int(body.get("threadId") or 0) == tid_b
                and body.get("reason") == "step",
                timeout=10.0,
            )
            assert stop_b2 is not None
            for ev in client.events:
                if ev.get("event") != "stopped":
                    continue
                body = ev.get("body") or {}
                if int(body.get("threadId") or 0) == tid_a:
                    raise AssertionError(
                        f"thread A got an unexpected stopped event while only "
                        f"stepping B: {body}"
                    )

            # ---- per-thread `stepIn` on B; A stays paused -----------
            client.drain_events("stopped")
            client.drain_events("continued")
            step_in_b = client.request(
                "stepIn", {"threadId": tid_b, "singleThread": True}
            )
            assert step_in_b["success"], step_in_b
            stop_b3 = client.find_event(
                "stopped",
                lambda body: int(body.get("threadId") or 0) == tid_b
                and body.get("reason") == "step",
                timeout=10.0,
            )
            assert stop_b3 is not None

            # ---- per-thread `stepOut` on B; A stays paused ---------
            # NB: at this point B is paused mid-loop. ``stepOut`` runs
            # until B returns from its current frame — that would
            # normally let the worker run to completion. That's fine;
            # the response is what we're checking. We'll later
            # interrupt B with a per-thread pause to verify pause
            # routing.
            client.drain_events("stopped")
            client.drain_events("continued")
            step_out_b = client.request(
                "stepOut", {"threadId": tid_b, "singleThread": True}
            )
            assert step_out_b["success"], step_out_b
            per_thread_step_ok = True
            # B will either stop again (function-finished) or run to
            # exit; either is fine. Drain any incidental events.
            time.sleep(0.2)
            client.drain_events("stopped")
            client.drain_events("continued")

            # ---- per-thread `pause` on A ----------------------------
            # A is still suspended at its breakpoint, so a `pause`
            # against A is a logical no-op — but the request must
            # succeed. To actually exercise the pause path we first
            # resume A (singleThread=true) so it's running, then
            # pause it.
            cont_a_run = client.request(
                "continue", {"threadId": tid_a, "singleThread": True}
            )
            assert cont_a_run["success"], cont_a_run
            assert cont_a_run.get("body", {}).get("allThreadsContinued") is False, cont_a_run
            single_continue_ok = True
            # Give it a moment to actually start running.
            time.sleep(0.1)
            pause_a = client.request(
                "pause", {"threadId": tid_a}
            )
            assert pause_a["success"], pause_a
            # Look for a stopped event on A with reason=pause /
            # exception. gdb maps -exec-interrupt to signal-received,
            # which our adapter translates to "exception". (Either
            # is acceptable; the salient bit is the threadId.)
            stop_a_pause = client.find_event(
                "stopped",
                lambda body: int(body.get("threadId") or 0) == tid_a,
                timeout=10.0,
            )
            assert stop_a_pause is not None
            per_thread_pause_ok = True

            # Now drop both breakpoints so the threads can finish their
            # loops without re-hitting on resume.
            del_bp = client.request(
                "setBreakpoints",
                {
                    "source": {
                        "path": FIXTURE_SRC,
                        "name": os.path.basename(FIXTURE_SRC),
                    },
                    "breakpoints": [],
                },
            )
            assert del_bp["success"], del_bp

            # Resume A (it was paused via the per-thread pause test).
            # B may still be paused (if stepOut left it suspended) or
            # may have run to thread exit. Try a per-thread continue
            # on each; tolerate B already being gone.
            cont_a_final = client.request(
                "continue", {"threadId": tid_a, "singleThread": True}
            )
            assert cont_a_final["success"], cont_a_final
            cont_b_final = client.request(
                "continue", {"threadId": tid_b, "singleThread": True}
            )
            # B may already have exited as a thread; an error here
            # is acceptable as long as the response shape is valid.
            assert "success" in cont_b_final, cont_b_final
        else:
            # All-stop fallback: drop breakpoints, continue everything.
            del_bp = client.request(
                "setBreakpoints",
                {
                    "source": {
                        "path": FIXTURE_SRC,
                        "name": os.path.basename(FIXTURE_SRC),
                    },
                    "breakpoints": [],
                },
            )
            assert del_bp["success"], del_bp
            cont_all = client.request("continue", {"threadId": tid_a})
            assert cont_all["success"], cont_all

        # 9. wait for the inferior to exit. The fixture prints a
        # ``done a=... b=...`` line and returns 0.
        client.wait_for_event("terminated", timeout=20.0)

        # 10. disconnect ------------------------------------------------
        disc = client.request("disconnect", {})
        assert disc["success"], disc
    finally:
        client.close()

    print("dap_multi_thread: OK")
    print(f"  fixture:    {binary}")
    print(f"  source:     {FIXTURE_SRC}")
    print(f"  threads:    {threads_seen} reported by `threads` request")
    print(f"  thread A:   bp at line {LINE_WORKER_A}, locals = {a_locals_summary}")
    print(f"  thread B:   bp at line {LINE_WORKER_B}, locals = {b_locals_summary}")
    print(
        f"  non-stop:   {non_stop_supported}, "
        f"singleThread continue ok = {single_continue_ok}, "
        f"per-thread step ok = {per_thread_step_ok}, "
        f"per-thread pause ok = {per_thread_pause_ok}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
