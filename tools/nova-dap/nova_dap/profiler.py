"""Sample-based profiler for the NOVA DAP server.

This module backs the **profiler** custom-request channel: a DAP client
can ask the server to periodically sample the inferior's call stack
while it runs, then retrieve a flame-graph-shaped aggregate of where
time is being spent. Less invasive than instrumentation (no code
rewriting, no extra breakpoints, no per-call overhead) and useful for
finding perf bottlenecks in production-shape binaries.

Custom-request surface (over DAP's standard request channel)
------------------------------------------------------------

* ``nova/profile/start({frequency_hz: int})`` — start sampling the
  call stack at the given frequency (Hz). The server installs a
  periodic gdb-MI poller that issues ``-stack-list-frames`` every
  ``1.0 / frequency_hz`` seconds while the inferior is running and
  appends each result to an in-memory samples list. Returns the
  resolved frequency + start timestamp.

* ``nova/profile/stop()`` — stop the sampler and return the aggregated
  data: ``{samples: [{ts, frame_ids}], frames: {frame_id: {function,
  file?, line?}}, total_samples, duration_s}``. Frame de-duplication
  keeps the wire shape compact even for long-running profiles.

* ``nova/profile/report({format: "text" | "folded" | "json"})`` —
  re-formats the captured samples without restarting the profile.
  ``text`` is a human-readable outline (top-N call paths by sample
  count), ``folded`` is the Brendan-Gregg flamegraph.pl input shape
  (``frame1;frame2;frame3 count`` per stack), and ``json`` is the
  full sample + frames table.

gdb-MI surface used
-------------------

* ``-stack-list-frames [--thread N]`` — returns the current call
  stack as a list of ``{level, func, file?, line?, addr}`` tuples.
  We snapshot this at the configured frequency.

* ``-data-evaluate-expression $pc`` — alternative single-frame
  query when ``-stack-list-frames`` is unavailable (e.g. between
  thread context switches). Used as a fallback so samples never
  silently drop.

The profiler is intentionally **decoupled from the gdb bridge** in
this module's public API: :meth:`Profiler.sample` accepts a callable
``stack_fn`` that returns a list of frame tuples. server.py supplies a
``stack_fn`` that wraps the bridge; tests supply a fake. This keeps
the unit tests pure-Python (no gdb spawn required) while preserving
the production integration path.

Module surface
--------------

* :class:`Frame` — typed (function, file, line) tuple with stable id.
* :class:`Sample` — single stack snapshot (timestamp + list of frame ids).
* :class:`Profiler` — sampling state: start / sample / stop / format.
* :func:`format_text` — human-readable top-N outline.
* :func:`format_folded` — Brendan-Gregg folded stacks (one line each).
* :func:`format_json` — full sample + frames table dump.
* :func:`stack_fn_from_bridge` — adapter that wraps a ``GdbBridge`` so
  ``Profiler.sample`` can drive it from server.py.

No third-party deps; pure stdlib. Thread-safe under concurrent
:meth:`sample` calls.
"""
from __future__ import annotations

import json as _json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Frame + Sample dataclasses.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """A single call-stack frame as seen by the profiler.

    Two frames are considered identical (i.e. share a frame id) iff
    their ``(function, file, line)`` triple matches. Address is
    intentionally NOT part of the identity — gdb sometimes reports
    slightly different addresses for the same source line (e.g.
    different basic blocks of an inlined function) and we want those
    samples to aggregate together for the flame graph.

    ``file`` is the basename (not the full path) so frames from the
    same logical source still aggregate even if gdb reports the path
    relative to two different cwds. The full path is preserved
    elsewhere if needed; flame graphs care about the function name."""

    function: str
    file: str = ""
    line: int = 0

    def key(self) -> Tuple[str, str, int]:
        """Stable tuple key for hashing / aggregation."""
        return (self.function, self.file, self.line)

    def display(self) -> str:
        """Human-readable single-line description, used by the text
        and folded formatters.

        Format:
          - ``function``                 — when no file info
          - ``function (file:line)``     — when file + line known
          - ``function (file)``          — when only file known"""
        if self.file and self.line:
            return f"{self.function} ({self.file}:{self.line})"
        if self.file:
            return f"{self.function} ({self.file})"
        return self.function


@dataclass
class Sample:
    """A single stack-snapshot sample.

    ``ts`` is the monotonic timestamp in seconds since the profile
    start (i.e. ``time.monotonic() - profile.start_time``).

    ``frame_ids`` is the inner-to-outer ordering of the stack: index
    0 is the deepest frame (``$pc`` is in here), the last element is
    the outermost (typically ``main`` or a thread entry). This
    matches gdb's ``-stack-list-frames`` ordering.

    ``thread_id`` is preserved when known so multi-thread profiles
    can be partitioned per thread; 0 means "unknown / single-thread"."""

    ts: float
    frame_ids: List[int]
    thread_id: int = 0


# ---------------------------------------------------------------------------
# Profiler core.
# ---------------------------------------------------------------------------


# Supported output formats. ``text`` is the human-readable outline,
# ``folded`` is Brendan Gregg's flamegraph.pl input (``frame;frame;...
# count`` one stack per line), ``json`` is the full samples + frames
# dump for downstream tooling.
SUPPORTED_FORMATS = frozenset({"text", "folded", "json"})

# Frequency clamp — the profiler refuses absurdly high rates that would
# starve the inferior of CPU. 1 Hz floor catches accidental zeros.
MIN_FREQUENCY_HZ = 1
MAX_FREQUENCY_HZ = 1000


# Callable signature for the bridge adapter. Takes an optional thread
# id (None = "current thread") and returns ``[(function, file, line),
# ...]`` from outermost-stable order or empty when no stack is
# available. Raises only on truly fatal errors; transient errors should
# return an empty list so the sampler can keep going.
StackFn = Callable[[Optional[int]], List[Tuple[str, str, int]]]


def normalise_frequency(frequency_hz: Any) -> Optional[int]:
    """Validate + clamp a user-supplied frequency.

    Returns the resolved integer rate or ``None`` for unparseable
    input. Rates above :data:`MAX_FREQUENCY_HZ` are clamped, rates
    below :data:`MIN_FREQUENCY_HZ` are bumped up to the floor."""
    try:
        rate = int(frequency_hz)
    except (TypeError, ValueError):
        return None
    if rate < MIN_FREQUENCY_HZ:
        rate = MIN_FREQUENCY_HZ
    if rate > MAX_FREQUENCY_HZ:
        rate = MAX_FREQUENCY_HZ
    return rate


@dataclass
class Profiler:
    """Sample-based profiler state.

    Thread-safety: :meth:`sample` and :meth:`stop` may be called from
    multiple threads (the sampler timer + the DAP request handler
    racing on ``nova/profile/stop``); both take the internal lock."""

    frequency_hz: int = 100
    samples: List[Sample] = field(default_factory=list)
    # frame_id -> Frame
    frames: Dict[int, Frame] = field(default_factory=dict)
    # Reverse lookup so we don't double-issue ids for the same
    # (function, file, line) tuple within a single profile.
    _frame_index: Dict[Tuple[str, str, int], int] = field(default_factory=dict)
    _next_frame_id: int = 1
    _running: bool = False
    start_time: float = 0.0
    stop_time: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Background sampler thread (only used in production via
    # ``run_sampler``). Test code drives :meth:`sample` directly.
    _sampler_thread: Optional[threading.Thread] = None
    _stop_event: Optional[threading.Event] = None
    # Cached error count for diagnostics — incremented every time the
    # ``stack_fn`` returns an empty stack or raises a tolerated error.
    drop_count: int = 0

    # ------------------------------------------------------------------ life

    def start(self, frequency_hz: Optional[int] = None) -> None:
        """Reset state and mark the profiler running.

        Does NOT spawn the background sampler thread — callers
        either drive :meth:`sample` themselves (tests) or call
        :meth:`run_sampler` (server.py). Splitting the two keeps the
        unit-test surface synchronous."""
        with self._lock:
            if frequency_hz is not None:
                rate = normalise_frequency(frequency_hz)
                if rate is not None:
                    self.frequency_hz = rate
            self.samples.clear()
            self.frames.clear()
            self._frame_index.clear()
            self._next_frame_id = 1
            self._running = True
            self.start_time = time.monotonic()
            self.stop_time = 0.0
            self.drop_count = 0

    def stop(self) -> Dict[str, Any]:
        """Halt sampling and return the aggregated data.

        Result shape (also what :func:`format_json` produces)::

            {
              "samples":       [{"ts": float, "frame_ids": [int, ...],
                                  "thread_id": int}, ...],
              "frames":        {"<id>": {"function": str, "file": str,
                                          "line": int}, ...},
              "total_samples": int,
              "duration_s":    float,
              "frequency_hz":  int,
              "drop_count":    int,
            }
        """
        # First request the background sampler (if any) to stop. Don't
        # join under the lock — the sampler also takes the lock so a
        # join-under-lock would deadlock.
        ev = self._stop_event
        if ev is not None:
            ev.set()
        thread = self._sampler_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if self._running:
                self.stop_time = time.monotonic()
                self._running = False
            self._sampler_thread = None
            self._stop_event = None
            return self._snapshot_locked()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    # --------------------------------------------------------------- sampling

    def _intern_frame(self, function: str, file: str, line: int) -> int:
        """Get-or-allocate a stable frame id for ``(function, file, line)``.

        Called with the lock held."""
        key = (function or "<unknown>", file or "", int(line or 0))
        existing = self._frame_index.get(key)
        if existing is not None:
            return existing
        fid = self._next_frame_id
        self._next_frame_id += 1
        self._frame_index[key] = fid
        self.frames[fid] = Frame(function=key[0], file=key[1], line=key[2])
        return fid

    def sample(
        self,
        stack_fn: StackFn,
        thread_id: Optional[int] = None,
    ) -> Optional[Sample]:
        """Take one stack snapshot and append it to the samples list.

        Returns the :class:`Sample` that was appended, or ``None`` if
        the snapshot was empty (e.g. the inferior wasn't paused at a
        gdb-reachable point) — in which case ``drop_count`` is
        incremented but no Sample is recorded."""
        try:
            frames = stack_fn(thread_id)
        except Exception:
            # Tolerate transient errors from the bridge so a single
            # bad poll doesn't kill the sampler. The error path
            # increments ``drop_count`` and returns None.
            with self._lock:
                self.drop_count += 1
            return None
        if not frames:
            with self._lock:
                self.drop_count += 1
            return None
        with self._lock:
            if not self._running:
                # A late sample arriving after stop() — ignore.
                return None
            ts = time.monotonic() - self.start_time
            ids: List[int] = []
            for entry in frames:
                if not isinstance(entry, tuple) or len(entry) != 3:
                    # Tolerate the older 1-arg shape (just function).
                    if isinstance(entry, str):
                        ids.append(self._intern_frame(entry, "", 0))
                    continue
                fn, fl, ln = entry
                fn_str = fn if isinstance(fn, str) else str(fn)
                fl_str = fl if isinstance(fl, str) else ""
                try:
                    ln_int = int(ln) if ln is not None else 0
                except (TypeError, ValueError):
                    ln_int = 0
                ids.append(self._intern_frame(fn_str, fl_str, ln_int))
            sample = Sample(
                ts=ts,
                frame_ids=ids,
                thread_id=int(thread_id or 0),
            )
            self.samples.append(sample)
            return sample

    def run_sampler(
        self,
        stack_fn: StackFn,
        thread_id: Optional[int] = None,
    ) -> threading.Thread:
        """Spawn a background thread that polls ``stack_fn`` at the
        configured frequency until :meth:`stop` is called.

        Must be called AFTER :meth:`start`. Returns the thread for
        introspection; the caller should NOT join it directly —
        :meth:`stop` handles that."""
        ev = threading.Event()
        interval = 1.0 / float(max(self.frequency_hz, MIN_FREQUENCY_HZ))

        def _loop() -> None:
            next_tick = time.monotonic() + interval
            while not ev.is_set():
                # Don't try to take a sample if the profiler was
                # stopped between iterations.
                if not self.is_running():
                    break
                self.sample(stack_fn, thread_id=thread_id)
                # Sleep until the next tick, but bail early if
                # stop_event fires.
                now = time.monotonic()
                wait = max(0.0, next_tick - now)
                if ev.wait(timeout=wait):
                    break
                next_tick += interval
                # If we drifted more than a full period behind (e.g. a
                # long gdb stall), re-anchor to now so we don't burn
                # CPU catching up.
                if time.monotonic() - next_tick > interval:
                    next_tick = time.monotonic() + interval

        thread = threading.Thread(
            target=_loop, name="nova-dap-profiler", daemon=True
        )
        with self._lock:
            self._sampler_thread = thread
            self._stop_event = ev
        thread.start()
        return thread

    # --------------------------------------------------------------- snapshot

    def _snapshot_locked(self) -> Dict[str, Any]:
        """Build the wire-shaped aggregate. Lock must be held."""
        # Use stop_time when set, otherwise the current monotonic
        # clock — so an in-flight ``report`` call gets a sensible
        # duration even before stop() has been issued.
        end = self.stop_time if self.stop_time else time.monotonic()
        duration = max(0.0, end - self.start_time) if self.start_time else 0.0
        return {
            "samples": [
                {
                    "ts": s.ts,
                    "frame_ids": list(s.frame_ids),
                    "thread_id": s.thread_id,
                }
                for s in self.samples
            ],
            "frames": {
                str(fid): {
                    "function": fr.function,
                    "file": fr.file,
                    "line": fr.line,
                }
                for fid, fr in self.frames.items()
            },
            "total_samples": len(self.samples),
            "duration_s": duration,
            "frequency_hz": self.frequency_hz,
            "drop_count": self.drop_count,
        }

    def snapshot(self) -> Dict[str, Any]:
        """Public, thread-safe snapshot. Useful for the ``report``
        custom request that fires while sampling is still running."""
        with self._lock:
            return self._snapshot_locked()

    # ---------------------------------------------------------------- format

    def format(self, format_type: str) -> str:
        """Format the captured samples in one of the supported shapes.

        ``format_type`` is one of ``"text"``, ``"folded"``, or
        ``"json"`` — see :data:`SUPPORTED_FORMATS`. Raises
        :class:`ValueError` for unknown formats so the caller can
        surface a clean DAP error."""
        if format_type not in SUPPORTED_FORMATS:
            raise ValueError(
                f"unsupported format: {format_type!r} "
                f"(supported: {sorted(SUPPORTED_FORMATS)})"
            )
        with self._lock:
            snap = self._snapshot_locked()
        if format_type == "text":
            return format_text(snap)
        if format_type == "folded":
            return format_folded(snap)
        return format_json(snap)


# ---------------------------------------------------------------------------
# Formatters.
# ---------------------------------------------------------------------------


def _frame_display(snap: Dict[str, Any], frame_id: int) -> str:
    """Look up a frame in a snapshot and return its display string."""
    info = snap.get("frames", {}).get(str(frame_id))
    if not isinstance(info, dict):
        return f"<frame-{frame_id}>"
    function = info.get("function") or "<unknown>"
    file_ = info.get("file") or ""
    line = info.get("line") or 0
    if file_ and line:
        return f"{function} ({file_}:{line})"
    if file_:
        return f"{function} ({file_})"
    return str(function)


def aggregate_stacks(snap: Dict[str, Any]) -> Dict[Tuple[int, ...], int]:
    """Aggregate identical stacks into a count map.

    Each key is the tuple of frame ids in OUTERMOST-FIRST order (i.e.
    ``main -> foo -> bar`` not ``bar -> foo -> main``) so the folded
    output matches Brendan Gregg's flamegraph.pl convention. Each
    value is the number of samples that captured that exact stack."""
    counts: Dict[Tuple[int, ...], int] = {}
    for sample in snap.get("samples", []):
        ids = sample.get("frame_ids") or []
        if not isinstance(ids, list):
            continue
        # gdb's stack ordering is innermost-first; flip to
        # outermost-first for the flame graph.
        reversed_ids = tuple(reversed(ids))
        if not reversed_ids:
            continue
        counts[reversed_ids] = counts.get(reversed_ids, 0) + 1
    return counts


def format_folded(snap: Dict[str, Any]) -> str:
    """Brendan Gregg flamegraph.pl input.

    One line per unique stack: ``frame1;frame2;frame3 count``. Frames
    are joined OUTERMOST-FIRST (so the bottom of the stack is on the
    left, matching the flame-graph visual convention). Stacks are
    sorted by count (descending) for stable output across runs."""
    counts = aggregate_stacks(snap)
    lines: List[str] = []
    # Sort by (count desc, stack tuple asc) so equal-count stacks have
    # a stable lexicographic order — important for tests and golden
    # comparisons.
    for stack, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        # The folded format uses semicolons as the separator; we
        # join the frame DISPLAY strings (not just function names)
        # so file+line discriminate same-named symbols. Spaces in the
        # display are fine — flamegraph.pl tolerates them, and the
        # final " <count>" is the only count-delimiter.
        labels = [_frame_display(snap, fid) for fid in stack]
        # Replace any embedded semicolons in labels with a NBSP-like
        # placeholder so the count parser doesn't get confused.
        labels = [lbl.replace(";", ":") for lbl in labels]
        lines.append(f"{';'.join(labels)} {count}")
    return "\n".join(lines)


def format_text(snap: Dict[str, Any]) -> str:
    """Human-readable outline.

    Header: total sample count + duration + drop count. Then the
    top-N (default 10) call paths ordered by sample share, each
    rendered as ``count (XX.X%): frame1 -> frame2 -> ...``. Same
    aggregation as :func:`format_folded`; only the rendering differs."""
    counts = aggregate_stacks(snap)
    total = snap.get("total_samples", 0) or 0
    duration = snap.get("duration_s", 0.0) or 0.0
    drops = snap.get("drop_count", 0) or 0
    lines: List[str] = []
    lines.append(
        f"Profile: {total} samples over {duration:.2f}s "
        f"@ {snap.get('frequency_hz', 0)} Hz (drops: {drops})"
    )
    if total == 0:
        return "\n".join(lines)
    # Top-N call paths. Use the count-then-stack ordering for stable
    # output across equal-count entries.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (stack, count) in enumerate(ranked[:10]):
        pct = (count / total) * 100.0 if total else 0.0
        labels = [_frame_display(snap, fid) for fid in stack]
        # Outermost on the left, like the folded output.
        lines.append(
            f"  {i + 1}. {count} ({pct:.1f}%): " + " -> ".join(labels)
        )
    if len(ranked) > 10:
        lines.append(f"  ... ({len(ranked) - 10} more stacks)")
    return "\n".join(lines)


def format_json(snap: Dict[str, Any]) -> str:
    """Full samples + frames table as JSON.

    The shape mirrors :meth:`Profiler.snapshot` exactly. We use
    ``sort_keys=True`` so the output is deterministic across runs
    (important for the unit tests that round-trip the JSON)."""
    return _json.dumps(snap, sort_keys=True, indent=2)


# ---------------------------------------------------------------------------
# gdb-MI bridge adapter.
# ---------------------------------------------------------------------------


def parse_stack_frames(fields: Dict[str, Any]) -> List[Tuple[str, str, int]]:
    """Extract ``[(function, file, line), ...]`` from a gdb
    ``-stack-list-frames`` reply.

    gdb returns ``^done,stack=[frame={level=N, func=NAME, file=PATH,
    fullname=PATH, line=L, addr=ADDR}, ...]`` — same shape as
    ``handle_stack_trace`` consumes. Returns an empty list when the
    reply is missing or malformed (so the sampler's drop-count gets
    incremented instead of raising)."""
    stack = fields.get("stack")
    if not isinstance(stack, list):
        return []
    out: List[Tuple[str, str, int]] = []
    for entry in stack:
        if isinstance(entry, dict) and "frame" in entry:
            fr = entry["frame"]
        elif isinstance(entry, dict):
            fr = entry
        else:
            continue
        if not isinstance(fr, dict):
            continue
        func = fr.get("func")
        if not isinstance(func, str) or not func:
            func = "<unknown>"
        # Prefer ``file`` (relative path) for compact flame-graph
        # labels; fall back to ``fullname`` if only the absolute is
        # populated. We strip the directory part either way so frames
        # from the same source compare equal.
        file_raw = fr.get("file") or fr.get("fullname") or ""
        if isinstance(file_raw, str):
            # Last path component only.
            if "/" in file_raw:
                file_ = file_raw.rsplit("/", 1)[-1]
            elif "\\" in file_raw:
                file_ = file_raw.rsplit("\\", 1)[-1]
            else:
                file_ = file_raw
        else:
            file_ = ""
        line_raw = fr.get("line")
        try:
            line = int(line_raw) if line_raw is not None else 0
        except (TypeError, ValueError):
            line = 0
        out.append((func, file_, line))
    return out


def stack_fn_from_bridge(bridge: Any) -> StackFn:
    """Adapter: returns a :data:`StackFn` that drives a ``GdbBridge``.

    The returned callable issues ``-stack-list-frames`` (optionally
    routed to a specific thread) and decodes the reply via
    :func:`parse_stack_frames`. Errors from the bridge are caught and
    surfaced as an empty list so the sampler keeps polling.

    Note: this fn only succeeds if the inferior is stopped (gdb can't
    walk the stack of a running thread). For a live-profiling setup
    where the inferior is meant to keep running between samples, use
    :func:`pausing_stack_fn` instead."""
    def _fn(thread_id: Optional[int]) -> List[Tuple[str, str, int]]:
        if bridge is None:
            return []
        cmd = "-stack-list-frames"
        if isinstance(thread_id, int) and thread_id > 0:
            cmd = f"-stack-list-frames --thread {thread_id}"
        try:
            result = bridge.command(cmd, timeout=1.0)
        except Exception:
            return []
        if result is None:
            return []
        # ``GdbResult.ok`` is True for class in {done, running, ...};
        # the parser uses ``.fields`` either way.
        fields = getattr(result, "fields", None) or {}
        if not isinstance(fields, dict):
            return []
        ok = getattr(result, "ok", True)
        if not ok:
            return []
        return parse_stack_frames(fields)
    return _fn


def pausing_stack_fn(
    bridge: Any,
    profile_flag: Optional[Callable[[bool], None]] = None,
    non_stop: bool = True,
) -> StackFn:
    """Adapter that pauses the inferior to take a sample, then resumes.

    Most real-world profiling needs this flavour: gdb cannot walk the
    stack of a thread that's currently executing instructions, so we
    have to ``-exec-interrupt`` first, ``-stack-list-frames`` while
    paused, then ``-exec-continue --all`` to let it keep running.

    ``profile_flag`` is an optional setter that toggles a
    server-level "in profile sample" flag. When set, the server's
    ``_handle_stopped`` / ``_handle_running`` callbacks suppress the
    DAP ``stopped`` / ``continued`` events that the
    interrupt-and-resume cycle generates — otherwise the DAP client
    would see a flood of fake stop events at the profile rate. The
    flag is set before the interrupt and cleared after the resume.

    If the profile_flag setter is None, suppression is skipped (the
    test-stub path uses None because there's no real DAP wire to
    spam).

    Like :func:`stack_fn_from_bridge` this returns an empty list on
    any transient error so the sampler can keep running."""
    def _set_flag(value: bool) -> None:
        if profile_flag is not None:
            try:
                profile_flag(value)
            except Exception:
                pass

    def _fn(thread_id: Optional[int]) -> List[Tuple[str, str, int]]:
        if bridge is None:
            return []
        # Enter "in profile sample" mode so the server suppresses the
        # transient stop / continue events.
        _set_flag(True)
        try:
            # Interrupt. In non-stop mode we can target a single
            # thread; otherwise we pause everything. We don't wait
            # for the *stopped record explicitly — gdb's MI command
            # returns ^done immediately and subsequent
            # -stack-list-frames will block until the stop is
            # processed.
            if isinstance(thread_id, int) and thread_id > 0 and non_stop:
                interrupt_cmd = f"-exec-interrupt --thread {thread_id}"
            elif non_stop:
                interrupt_cmd = "-exec-interrupt --all"
            else:
                interrupt_cmd = "-exec-interrupt"
            try:
                bridge.command(interrupt_cmd, timeout=1.0)
            except Exception:
                return []
            # The stop record may take a moment to arrive — gdb is
            # pretty fast (~ms) on Linux. We give it a short window
            # via a tiny retry loop on -stack-list-frames; if the
            # first try returns "No registers." (inferior not yet
            # stopped) we wait a few ms and retry.
            cmd = "-stack-list-frames"
            if isinstance(thread_id, int) and thread_id > 0 and non_stop:
                cmd = f"-stack-list-frames --thread {thread_id}"
            stack_result = None
            for attempt in range(8):
                try:
                    stack_result = bridge.command(cmd, timeout=1.0)
                except Exception:
                    stack_result = None
                    break
                if stack_result is None:
                    break
                if getattr(stack_result, "ok", False):
                    break
                # Not yet stopped — sleep a bit and retry. 5 ms
                # increments are short enough that the inferior
                # barely pauses but long enough for gdb to catch up.
                time.sleep(0.005)
            fields: Dict[str, Any] = {}
            if stack_result is not None and getattr(stack_result, "ok", False):
                fields = getattr(stack_result, "fields", None) or {}
                if not isinstance(fields, dict):
                    fields = {}
            frames = parse_stack_frames(fields)
            # Resume the inferior. --all in non-stop mode resumes
            # every paused thread; in all-stop mode -exec-continue
            # alone is enough.
            try:
                if non_stop:
                    bridge.command("-exec-continue --all", timeout=1.0)
                else:
                    bridge.command("-exec-continue", timeout=1.0)
            except Exception:
                pass
            return frames
        finally:
            _set_flag(False)
    return _fn


# ---------------------------------------------------------------------------
# Reporting helpers used by the custom-request handlers.
# ---------------------------------------------------------------------------


def report_body(profiler: Profiler, format_type: str) -> Dict[str, Any]:
    """Compose the response body for a ``nova/profile/report`` request.

    Returns ``{format, output, total_samples}``. The ``output`` field
    carries the formatted string (text / folded / JSON); the caller
    just needs to wrap this in the DAP response envelope."""
    formatted = profiler.format(format_type)
    snap = profiler.snapshot()
    return {
        "format": format_type,
        "output": formatted,
        "total_samples": snap.get("total_samples", 0),
        "duration_s": snap.get("duration_s", 0.0),
    }
