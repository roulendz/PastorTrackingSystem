---
phase: 03-camera-i-o
reviewed: 2026-05-04T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/io/obs_camera.py
  - pastor_tracker/tests/fixtures/camera_traces.py
  - pastor_tracker/tests/test_obs_camera_discovery.py
  - pastor_tracker/tests/test_obs_camera_lifecycle.py
  - pastor_tracker/tests/test_obs_camera_stale_drop.py
  - pastor_tracker/tests/test_obs_camera_fallback.py
  - pastor_tracker/tests/test_obs_camera_stall.py
  - pastor_tracker/pyproject.toml
findings:
  critical: 3
  warning: 7
  info: 4
  total: 14
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-05-04
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Implementation broadly mirrors the Phase 2 arduino_motor pattern correctly: VideoSource Protocol seam is clean, threading <-> asyncio bridge uses `loop.call_soon_threadsafe`, drop-oldest semantics are observable, and the two `noqa: BLE001` translators each carry the documented marker. Discovery, P95 detector, and stale-drop logic are sound. Lifecycle close-order (`stop_event.set` -> join -> release) is correct.

However, the resolution-fallback path on the daemon capture thread has **two BLOCKER-class defects** that will silently kill the only camera in production: (1) the factory call inside `_maybe_fallback` is not wrapped in a try/except, so a cv2 / DirectShow open failure during fallback nulls `self._source` and the next loop iteration silently `return`s with state still `RUNNING` and `last_error=None` — consumer hangs forever; (2) `last_grab_ns` is not reset across the fallback transition, so the new 720p source's CAP_DSHOW first-frame latency (documented as up to 3 s) will deterministically trip the 200 ms stall detector and force an immediate spurious 3-attempt reopen budget burn.

A third BLOCKER affects observability: when `start()` raises before the capture thread is launched (e.g. `OBSCameraNotFoundError` from discovery, or factory raise on initial `VideoCapture` open), the state is left in `OPENING`, `last_error` is never latched, and a subsequent `stop()` call overwrites the final state to `CLOSED`, hiding the fault from the dashboard.

The remaining warnings cluster around: silent thread exit on `self._source is None`, `stop()` clobbering `_CamState.FAULTED` -> `_CamState.CLOSED`, missing typed translation on `start()` factory exceptions, dead `_CamState.REOPENING` enum value, and the warmup-window equality boundary. Tests are well-structured: parked-camera helper is correct, short-fuse monkeypatching is clean (and the regression-guard `test_reopen_backoffs_match_constant` correctly runs WITHOUT short-fuse), and the threat-model citations in code comments match the implemented mitigations.

## Critical Issues

### CR-01: `_maybe_fallback` factory call not wrapped — silent thread death on cv2 open error

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:779-787`
**Issue:** Inside the daemon capture thread, the resolution-fallback path releases the old source then calls `self._video_source_factory(self._device_index, _FALLBACK_WIDTH, _FALLBACK_HEIGHT, ...)` with **no try/except**. Production `OpenCvVideoSource.__init__` calls `cv2.VideoCapture(device_index, cv2.CAP_DSHOW)` which can raise on driver-side failure (the same failure mode the documented `noqa: BLE001` translator at line 711 exists to handle on the reopen path). If the factory raises here, `self._source` remains `None` (it was set to `None` on line 781 before the factory call). The exception propagates up to `_capture_loop`, which has its own `noqa: BLE001` translator at line 577 — but that translator is on `self._source.read()`, not on the fallback factory call.

Re-tracing: the exception at line 782 is NOT caught by the line-577 translator (different control point); it escapes the entire `_capture_loop` function. Because `_capture_loop` is the daemon thread target with no outer handler, the thread dies via Python's unhandled-exception path (silently, with only a stderr traceback that structlog never sees). Camera state remains `RUNNING`, `last_error` remains `None`, and `frames()` blocks forever on `_frames_queue.get()`. Even if you wrap the call defensively, the secondary defense at `_capture_loop:574-575` (`if self._source is None: return`) ALSO exits silently without latching an error.

This is the inverse of the documented T-03-03 mitigation: instead of "release on failure", the code "leaves a bare null and hopes for the best". The threat model claim that "resource leak on failure -- release in try/finally" is implemented for the fallback path is FALSE.
**Fix:**
```python
def _maybe_fallback(self, p95: _P95Detector, now_ns: int) -> None:
    if self._fallback_consumed:
        return
    if not p95.budget_breached_persistent(now_ns):
        return
    if self._device_index is None:
        return
    p95_ms = p95.current_p95_ns / _NS_PER_MS
    budget_ms = p95.budget_ns / _NS_PER_MS
    already_at_fallback = (
        self._current_width == _FALLBACK_WIDTH
        and self._current_height == _FALLBACK_HEIGHT
    )
    if already_at_fallback:
        self._logger.error(
            "camera_resolution_breach_at_720p",
            p95_ms=p95_ms, budget_ms=budget_ms,
        )
        self._fallback_consumed = True
        return
    if self._source is not None:
        self._source.release()
        self._source = None
    try:
        self._source = self._video_source_factory(
            self._device_index, _FALLBACK_WIDTH, _FALLBACK_HEIGHT,
            self._config.capture_fps,
        )
    except Exception as exc:  # noqa: BLE001 -- documented translator
        # Fallback factory bridge: cv2 / DirectShow open errors translate
        # into a typed terminal stall rather than crashing the daemon thread.
        self._logger.error(
            "camera_fallback_factory_failed",
            reason=str(exc),
            from_dim=f"{self._current_width}x{self._current_height}",
            to_dim=f"{_FALLBACK_WIDTH}x{_FALLBACK_HEIGHT}",
        )
        self._fault_with_stall(exc)
        self._fallback_consumed = True
        return
    self._logger.warning(
        "camera_resolution_fallback",
        from_dim=f"{self._current_width}x{self._current_height}",
        to_dim=f"{_FALLBACK_WIDTH}x{_FALLBACK_HEIGHT}",
        p95_ms=p95_ms, budget_ms=budget_ms,
        device_index=self._device_index,
    )
    self._current_width = _FALLBACK_WIDTH
    self._current_height = _FALLBACK_HEIGHT
    self._fallback_consumed = True
```

Add a regression test in `test_obs_camera_fallback.py` modelled on `test_reopen_factory_exception_recorded_in_history` where the factory's second invocation raises.

---

### CR-02: Fallback transition deterministically trips false stall detection in production

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:629, 752-798`
**Issue:** `_maybe_fallback` releases the old source and opens a new one, but does NOT reset `last_grab_ns`. After fallback returns, `_capture_loop` line 629 sets `last_grab_ns = now_ns` — using the timestamp from BEFORE the source swap. On the next loop iteration, `self._source.read()` is the NEW (just-opened) 720p CAP_DSHOW source. The module-level docstring at line 63 explicitly cites OpenCV first-frame latency on CAP_DSHOW as up to 3 s (`_FIRST_FRAME_TIMEOUT_SEC = 3.0`). When that read finally returns, `now_ns_new - last_grab_ns` will be on the order of seconds, blowing through `_STALL_THRESHOLD_NS = 200_000_000` (200 ms) by 1-2 orders of magnitude.

Result: every successful 1080p->720p fallback in production immediately triggers the stall path, which calls `_attempt_reopen` and burns through the 3-attempt linear backoff (200 + 500 + 1000 ms = 1.7 s of waiting on a healthy camera) before recovering. If the fresh 720p source's first frame happens to take longer than the cumulative reopen budget allows, fallback transitions into a false `CameraStallError` -- the camera dies on what should have been a graceful resolution downgrade.

This bug is invisible in the test suite because `FakeVideoSource.read()` returns instantly (no first-frame latency). The production code path is untested. The CONTEXT.md Area 3 lock guarantees fallback as a successful one-shot recovery; the current code makes it a stall trigger.
**Fix:**
After the source swap in `_maybe_fallback`, signal the caller to reset its grab clock. Either:

Option A (preferred — explicit signal):
```python
def _maybe_fallback(self, p95: _P95Detector, now_ns: int) -> bool:
    """Returns True iff fallback fired and caller MUST reset last_grab_ns."""
    ...
    self._fallback_consumed = True
    return True   # caller must reset last_grab_ns

# in _capture_loop:
if last_grab_ns is not None and not self._fallback_consumed:
    p95_detector.observe(now_ns - last_grab_ns)
    warmup_remaining_ns = ...
    if warmup_remaining_ns >= 0:
        if self._maybe_fallback(p95_detector, now_ns):
            last_grab_ns = None
            continue   # next iteration re-grabs from fresh source
```

Option B: have `_maybe_fallback` itself set `last_grab_ns` via a stored attribute. Option A is cleaner and matches the stall-recovery pattern (`last_grab_ns = None; continue`) used at lines 597-598 and 617-618.

Add a regression test that wraps the post-fallback source in a slow-first-frame fake (e.g. `time.sleep(0.5)` on first read after instantiation) and asserts no `camera_stall_detected` event fires across the fallback boundary.

---

### CR-03: `start()` swallows pre-thread errors and `stop()` clobbers `_CamState.FAULTED`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:469-531, 533-554`
**Issue:** Two coupled defects in the lifecycle surface that together hide terminal errors from operators:

1. In `start()` lines 481-499, the calls to `discover_obs_camera_index` (line 483) and `self._video_source_factory(...)` (line 488) run AFTER `self._state = _CamState.OPENING` is set, but their exceptions are NOT caught. If discovery raises `OBSCameraNotFoundError` or the factory raises a cv2 open error, the exception propagates to the caller — but `self._state` is left at `_CamState.OPENING` (never `_CamState.FAULTED`), and `self._latched_error` is never assigned. This contradicts the only-other terminal path (line 518-523) which explicitly latches both. The dashboard sees `state=OPENING` indefinitely; `is_running` is False; `last_error` is None — so monitoring code cannot distinguish "started successfully" from "blew up at discovery".

2. `stop()` at line 554 unconditionally sets `self._state = _CamState.CLOSED` regardless of prior state. If `start()` raised `CameraOpenError` (lines 519-523) and the caller properly invokes `stop()` for cleanup, the FAULTED state and its diagnostic value are erased — `cam.state` reads `CLOSED` after the stop, but `cam.last_error` still holds the CameraOpenError. The two status fields disagree about whether the camera failed.

Combined, these defects mean: the only correctly-latched fault state is the first-frame timeout. Discovery failure, factory failure on initial open, and post-stop view of any fault all silently mis-report.
**Fix:**
1. Wrap the pre-thread setup in `start()` and translate to typed errors:
```python
async def start(self) -> None:
    if self._state is not _CamState.DISCONNECTED:
        raise CameraError(f"start() called twice (state={self._state.value})")
    self._loop = asyncio.get_running_loop()
    self._state = _CamState.OPENING
    try:
        self._device_index = discover_obs_camera_index(
            self._config.obs_camera_name,
            factory=self._filter_graph_factory,
            logger=self._logger,
        )
        self._source = self._video_source_factory(
            self._device_index, self._current_width, self._current_height,
            self._config.capture_fps,
        )
    except OBSCameraNotFoundError as exc:
        self._state = _CamState.FAULTED
        self._latched_error = exc
        raise
    except Exception as exc:  # noqa: BLE001 -- documented translator
        # cv2 / DirectShow open errors -> typed CameraOpenError.
        self._state = _CamState.FAULTED
        self._latched_error = CameraOpenError(
            f"VideoCapture open failed: {exc!r}"
        )
        raise self._latched_error from exc
    # ... rest unchanged
```

2. In `stop()`, preserve terminal fault states:
```python
async def stop(self) -> None:
    self._stop_event.set()
    capture_thread = self._capture_thread
    if capture_thread is not None:
        await asyncio.to_thread(capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC)
        self._logger.info("camera_thread_exited", clean=not capture_thread.is_alive())
        self._capture_thread = None
    if self._source is not None:
        self._source.release()
        self._source = None
    if self._state is not _CamState.FAULTED:
        self._state = _CamState.CLOSED
```

## Warnings

### WR-01: Silent thread exit when `self._source is None`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:574-575`
**Issue:** If `self._source` is ever observed as `None` inside `_capture_loop`, the thread does `return` with no log, no error latch, and no state change. This is the safety net that catches CR-01 today but masks the symptom — the thread dies, state stays `RUNNING`, `last_error` is None, consumer hangs. Even after CR-01 is fixed there are other paths (e.g. future refactors, a forgotten `self._source = None` in a recovery branch) where this guard could fire silently.
**Fix:** Replace silent return with an explicit fault:
```python
if self._source is None:
    self._fault_with_stall(
        RuntimeError("capture loop observed source=None; recovery path bug")
    )
    return
```

### WR-02: `_attempt_reopen` does not release the final failed source

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:725-746`
**Issue:** When the reopen attempt sets `self._source` (line 705), then `read()` returns False (line 725), the failed source is appended to history but NOT released — the for-loop iterates and on the NEXT attempt the leading `if self._source is not None: self._source.release()` at line 697 releases it. But on the FINAL failed iteration (the 3rd), the loop falls through to `return False` (line 746) leaving `self._source` pointing at the failed handle. The `_capture_loop` then calls `_fault_with_stall` and returns; only a subsequent `stop()` releases the handle. Until `stop()` runs, the cv2 handle is leaked. T-03-03 says "release in try/finally" — this path doesn't.
**Fix:**
```python
self._reopen_history.append((attempt_index, backoff_ms, "first_frame_after_reopen_returned_False"))
self._logger.warning("camera_reopen_attempt_failed", ...)
# Release the failed source so the final iteration doesn't leak.
self._source.release()
self._source = None
```

### WR-03: `start()` first-frame timeout path does not null `self._capture_thread`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:503-523`
**Issue:** On first-frame timeout, `start()` joins the thread (line 507) and releases the source, but does NOT set `self._capture_thread = None`. If the caller then invokes `stop()` for cleanup, line 542 sees the thread reference, calls `join()` again on the already-joined thread (no-op, OK), and logs `camera_thread_exited` — surprising telemetry for a code path that has already faulted and emitted `camera_first_frame_timeout`. Compare line 550 in `stop()` which DOES null the attribute. Inconsistent state-tracking.
**Fix:** After the join in the timeout branch, add `self._capture_thread = None`.

### WR-04: `_CamState.REOPENING` declared but never assigned

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:175`
**Issue:** The state enum declares `REOPENING = "reopening"` but no code path ever sets `self._state = _CamState.REOPENING`. The `_attempt_reopen` method runs while `_state is _CamState.RUNNING`. Test `test_stall_reopen_succeeds_attempt_1` line 141 hedges with `cam.state in (_CamState.RUNNING, _CamState.REOPENING)` — passing only because `RUNNING` is in the tuple. Either the state machine is incomplete (REOPENING should be visible during recovery for dashboard purposes) or the enum value is dead code.
**Fix:** Either remove the unused enum member, or transition into/out of it around `_attempt_reopen`:
```python
def _attempt_reopen(self) -> bool:
    self._state = _CamState.REOPENING
    try:
        # ... existing body ...
        return True_or_False
    finally:
        if self._state is _CamState.REOPENING:
            self._state = _CamState.RUNNING  # only if recovery succeeded
```
Note: setting state from the daemon thread requires care since `_state` is read by the loop-thread `is_running` property; Python attribute writes are atomic so no lock needed, but consider `call_soon_threadsafe` for consistency with `_on_capture_failed`.

### WR-05: Warmup-window boundary uses `>= 0` instead of `> 0`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:623-628`
**Issue:** `warmup_remaining_ns = int(_WARMUP_WINDOW_SEC * _NS_PER_SEC) - (now_ns - warmup_started_ns)`; check `if warmup_remaining_ns >= 0`. At the exact boundary (`now_ns - warmup_started_ns == _WARMUP_WINDOW_SEC * _NS_PER_SEC`) the window is over but the fallback path is still entered. The window should be a half-open interval `[0, W)` per the typical "inside warmup" semantics. CONTEXT.md says "2 s warmup window" — the boundary case is ambiguous but `>` is more defensible.
**Fix:** Change to `if warmup_remaining_ns > 0:`. Add a regression test that pins the boundary behavior.

### WR-06: `frames()` raises latched error after queue drain — but the queue may NEVER drain if producer dies

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:813-816`
**Issue:** `frames()` only raises `self._latched_error` when `self._frames_queue.empty()`. If the capture thread terminates after a fault but the queue had pending frames, the consumer will yield those frames (good), then on the next iteration find the queue empty AND latched_error set, and raise. OK.

But: `frame = await self._frames_queue.get()` blocks indefinitely if the queue is empty and no producer is alive. In the buggy paths described in CR-01 and WR-01 (silent thread exit), `latched_error` is None AND queue is empty AND no producer is running. The consumer hangs forever with no diagnostic. Even with those fixes, a future regression could re-introduce the symptom. Add a timeout + watchdog to the consumer-side wait, or a "thread-died" sentinel.
**Fix:** Either (a) have the capture thread always latch SOME error before exit (defense in depth via WR-01 fix), or (b) augment `frames()` to wake on a producer-dead signal:
```python
async def frames(self) -> AsyncIterator[Frame]:
    while True:
        if self._latched_error is not None and self._frames_queue.empty():
            raise self._latched_error
        # Add a timeout so a dead producer doesn't deadlock forever.
        try:
            frame = await asyncio.wait_for(
                self._frames_queue.get(),
                timeout=_FIRST_FRAME_TIMEOUT_SEC,
            )
        except TimeoutError:
            if self._capture_thread is not None and not self._capture_thread.is_alive():
                raise CameraStallError(attempts=[(0, 0, "capture thread dead, no error latched")])
            continue
        # ... rest of stale-drop logic
```

### WR-07: `_enqueue_frame` `contextlib.suppress(asyncio.QueueEmpty)` is unreachable

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:639-647`
**Issue:** The block is gated by `if self._frames_queue.full():` so the queue is non-empty by definition; `get_nowait()` cannot raise `QueueEmpty`. The `contextlib.suppress` wrapper is dead defensive code. Per tiger-style "fail fast, fail loud" (CLAUDE.md rule 1), unreachable error-suppression hides real bugs (e.g., if a future refactor removes the `full()` guard).
**Fix:** Drop the suppress — let any unexpected `QueueEmpty` propagate:
```python
def _enqueue_frame(self, frame: Frame) -> None:
    if self._frames_queue.full():
        # full() guarantees get_nowait() returns; QueueEmpty would be a contract bug.
        self._frames_queue.get_nowait()
        self._logger.warning(
            "frames_queue_full",
            dropped_timestamp_ns=frame.timestamp_ns,
            queue_max=_FRAMES_QUEUE_MAX_SIZE,
        )
    self._frames_queue.put_nowait(frame)
```

## Info

### IN-01: `_logger` parameter typed as `structlog.stdlib.BoundLogger` but module uses `structlog.get_logger`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:265, 439`
**Issue:** `discover_obs_camera_index` is annotated with `logger: structlog.stdlib.BoundLogger`, but `ObsCamera.__init__` line 439 builds the logger via `structlog.get_logger(...)` whose return type is `structlog.stdlib.BoundLogger | structlog.types.FilteringBoundLogger` depending on configuration. Tests get away with it because pytest's structlog fixture configures `BoundLogger`, but production may use the filtering variant. Loosen the annotation to `structlog.typing.FilteringBoundLogger` (the protocol) or `structlog.stdlib.BoundLogger` consistently.
**Fix:** Use the protocol type `structlog.typing.FilteringBoundLogger` in the discovery signature.

### IN-02: Tests use snake_case-violating names for error-class checks

**File:** `pastor_tracker/tests/test_obs_camera_discovery.py:142, 149, 153`
**Issue:** Test names `test_OBSCameraNotFoundError_carries_attributes`, `test_OBSCameraNotFoundError_is_CameraError`, `test_CameraStallError_carries_attempts_attribute` mix PascalCase mid-name. Per-file ignores in `pyproject.toml:66` waive ANN/PLR2004/S for tests but do not waive PEP8 N802. Either rename to `test_obs_camera_not_found_error_carries_attributes` (clearer), or add `N802` to the per-file ignore. Not flagged by current ruff config (N is not enabled), but the project says "PEP8 strict naming" in CLAUDE.md rule 7.
**Fix:** Rename to lowercase or document the deviation.

### IN-03: `OpenCvVideoSource.read()` `# type: ignore[return-value]` could be tightened

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:236-239`
**Issue:** The `# type: ignore[return-value]` narrows cv2's widened dtype to `_ImageArray`. A safer approach is `np.asarray(frame, dtype=np.uint8)` which is a no-op for already-uint8 arrays and tightens the type at runtime. Currently, if cv2 ever changes its runtime contract (unlikely), the type assertion would be silently wrong.
**Fix:** Optional. Either keep the ignore with a citation comment, or use `np.asarray` for runtime safety.

### IN-04: `FakeVideoSource.read()` `time.sleep(...)` blocks the capture-thread tick

**File:** `pastor_tracker/tests/fixtures/camera_traces.py:74-76`
**Issue:** The fake's `time.sleep(scripted.delay_sec)` faithfully simulates a slow grab, but compounds with the `_CAPTURE_THREAD_TICK_SEC = 0.1` (100 ms) cadence in unexpected ways for tests that mix small delays with the stop-event tick. This isn't a bug — the comment "OK in test code; never in app code" is correct — but document in the fixture docstring that scripts with `delay_sec >= _CAPTURE_THREAD_TICK_SEC` produce a coarser stop-event response.
**Fix:** Add a note to the FakeVideoSource docstring.

---

_Reviewed: 2026-05-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
