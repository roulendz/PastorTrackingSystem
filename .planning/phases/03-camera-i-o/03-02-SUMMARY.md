---
phase: 03-camera-i-o
plan: 02
subsystem: io
tags:
  - camera
  - asyncio
  - threading
  - lifecycle
  - fallback
  - stall-recovery
  - orchestrator
dependency_graph:
  requires:
    - core/types.py:Frame DTO contract (Phase 1)
    - io/obs_camera.py:VideoSource Protocol + OpenCvVideoSource + discover_obs_camera_index + _CamState + _P95Detector (Plan 03-01)
    - io/arduino_motor.py (Phase 2 -- structural template for capture thread + bounded queue + close-order)
    - config.py:obs_camera_name / capture_width / capture_height / capture_fps
  provides:
    - "pastor_tracker.io.obs_camera.ObsCamera (orchestrator class)"
    - "pastor_tracker.io.obs_camera.ObsCamera.start / stop / frames (async lifecycle + iterator)"
    - "pastor_tracker.io.obs_camera.ObsCamera.state / is_running / current_resolution / last_error (status surface for Phase 7 dashboard)"
    - "tests.fixtures.camera_traces._started_camera + wait_for_state (async helpers)"
  affects:
    - "Phase 4 perception/pose_detector.py (consumes Frame stream from ObsCamera.frames())"
    - "Phase 6 pipeline.py (orchestrates ObsCamera lifecycle alongside ArduinoMotor)"
    - "Phase 7 ui/dashboard.py (reads state / current_resolution / last_error)"
tech-stack:
  added: []
  patterns:
    - "Daemon capture thread + asyncio.Queue(maxsize=64) + loop.call_soon_threadsafe bridge -- mirrors Phase 2 ArduinoMotor"
    - "Pitfall 7 close-order: stop_event.set() -> thread.join(timeout) -> source.release() -> CLOSED"
    - "T-03-03 mitigation: source.release() on first-frame timeout BEFORE raising CameraOpenError"
    - "Two-factory injection (video_source_factory + filter_graph_factory) -- Pitfall 10 mitigation"
    - "One-shot 1080p->720p fallback inside 2 s warmup window; never re-promote, never re-fallback"
    - "3-attempt linear-backoff reopen state machine (200/500/1000 ms) -> CameraStallError on terminal failure"
    - "Consumer-side stale-drop > 100 ms with frame_stale_dropped WARN (capture thread does NOT decide age)"
    - "Tiger-style typed-error hierarchy: 2 documented noqa: BLE001 translators (capture loop + reopen factory call)"
    - "Short-fuse monkeypatch pattern in test files compresses timing constants -- mirrors Phase 2 short-fuse idiom"
key-files:
  created:
    - pastor_tracker/tests/test_obs_camera_lifecycle.py
    - pastor_tracker/tests/test_obs_camera_stale_drop.py
    - pastor_tracker/tests/test_obs_camera_fallback.py
    - pastor_tracker/tests/test_obs_camera_stall.py
    - .planning/phases/03-camera-i-o/03-02-SUMMARY.md
  modified:
    - pastor_tracker/src/pastor_tracker/io/obs_camera.py
    - pastor_tracker/tests/fixtures/camera_traces.py
decisions:
  - "ObsCamera mirrors ArduinoMotor structure verbatim -- single producer thread + bounded asyncio.Queue + cross-thread bridge via call_soon_threadsafe"
  - "Pitfall 7 close-order pinned: stop_event -> thread.join -> source.release -> CLOSED; releasing source before joining races in-flight read()"
  - "T-03-03 release-on-timeout: start() awaits first frame within _FIRST_FRAME_TIMEOUT_SEC=3.0 then explicitly releases the handle on timeout BEFORE raising CameraOpenError"
  - "Frame DTO constructed inside the capture thread (timestamp + shape validation off the asyncio hot path); ValueError on shape mismatch is treated as a stall (Open Question 1)"
  - "Resolution fallback is one-shot inside the 2 s warmup window only; never re-promote (CONTEXT.md Area 3 lock); already-720p case logs ERROR but continues capturing"
  - "Stall recovery is bounded: 3 attempts at 200/500/1000 ms backoff; failure latches CameraStallError with full reopen_history; cam.last_error / cam.state==FAULTED surface to consumer"
  - "Consumer-side stale-drop (> 100 ms age) at frames() iterator -- the capture thread MUST NOT decide staleness"
  - "Bounded asyncio.Queue(maxsize=64) drop-oldest with frames_queue_full WARN (T-03-02 mitigation; ~380 MiB ceiling at 1080p)"
  - "Plan's short-fuse table under-specified _P95_WINDOW_SIZE; production 30-sample window outlived compressed warmup at realistic test delays. Test files now also patch _P95_WINDOW_SIZE=5 + _P95_INDEX=4 to keep per-test runtime <2 s while still exercising the real state machine"
  - "_started_camera default script shares one BGR ndarray across 600 frames -- 600 distinct 1920x1080x3 buffers (~3.6 GB allocation) dominated per-test runtime; capture thread treats Frame.image read-only so aliasing is safe"
metrics:
  duration: ~80 minutes
  completed: 2026-05-04
  tasks_completed: 3
  files_created: 4
  files_modified: 2
  commits: 3
  baseline_tests: 213
  new_tests: 24
  total_tests: 237
  obs_camera_tests: 44
  line_coverage_obs_camera: "93% (21 uncovered lines: 14 in OpenCvVideoSource real-cv2 paths -- deferred to QA-04; 7 minor early-return branches)"
  branch_coverage_discover: "100% (inherited from Plan 03-01)"
---

# Phase 3 Plan 2: OBS Camera Orchestrator Summary

`ObsCamera` async orchestrator class appended to `obs_camera.py` (~470 LOC) -- daemon capture thread + bounded asyncio.Queue(maxsize=64) + cross-thread bridge via `loop.call_soon_threadsafe` mirroring Phase 2 ArduinoMotor verbatim. Adds `start()` (3 s first-frame timeout with T-03-03 source-release mitigation), `stop()` (Pitfall 7 close-order), `frames()` AsyncIterator with consumer-side stale-drop > 100 ms, one-shot 1080p->720p resolution fallback inside 2 s warmup window, 3-attempt linear-backoff (200/500/1000 ms) reopen state machine -> `CameraStallError`, and read-only status surface (`state`, `is_running`, `current_resolution`, `last_error`) for Phase 7 dashboard. Closes IO-CAM-01 (orchestrator-side lifecycle), IO-CAM-03 (resolution fallback), IO-CAM-04 (timestamp + stall recovery + stale-drop).

## Files Created

| File | LOC | Purpose |
|------|-----|---------|
| pastor_tracker/tests/test_obs_camera_lifecycle.py | 169 | 9 tests: start returns after first frame, double-start raises, first-frame timeout with source release (T-03-03), source release on normal exit, frames yields in order, perf_counter_ns timestamps, queue drop-oldest WARN, status properties during RUNNING |
| pastor_tracker/tests/test_obs_camera_stale_drop.py | 184 | 4 tests: consumer drops stale frame (200 ms age) with frame_stale_dropped WARN, fresh frame passes through, stale-drop threshold constant regression guard, frame just under 100 ms NOT dropped. Uses `_BlockingVideoSource` parking idiom (mirror Phase 2 _BlockingTransport) so tests own the queue deterministically |
| pastor_tracker/tests/test_obs_camera_fallback.py | 252 | 4 tests: warmup breach triggers one-shot fallback with camera_resolution_fallback WARN; post-warmup breach inhibited; already-720p case logs camera_resolution_breach_at_720p ERROR (state stays RUNNING); fallback fires exactly once |
| pastor_tracker/tests/test_obs_camera_stall.py | 363 | 7 tests: locked _REOPEN_BACKOFFS_MS=(200,500,1000) regression guard, reopen attempt-1 success, 3-attempt failure -> CameraStallError + state=FAULTED, attempt-tuple structure validation, capture-thread exception path (BLE001 #1 coverage), reopen-factory exception path (BLE001 #2 coverage), frame_shape_mismatch -> stall (Open Question 1 coverage) |

## Files Modified

| File | LOC change | Purpose |
|------|------------|---------|
| pastor_tracker/src/pastor_tracker/io/obs_camera.py | 360 -> 826 (+466) | Append ObsCamera orchestrator class: __init__, start, stop, frames, _capture_loop, _enqueue_frame, _on_capture_failed, _fault_with_stall, _attempt_reopen, _maybe_fallback + 4 read-only properties; add asyncio/contextlib/threading/time/AsyncIterator/Frame/Config imports; add ObsCamera to __all__ |
| pastor_tracker/tests/fixtures/camera_traces.py | 90 -> 163 (+73) | Append _started_camera + wait_for_state async helpers (mirror arduino_traces.py); shares one BGR ndarray across default 600-frame script (~20 s headroom) -- avoids 3.6 GB allocation |

## Test Count and Coverage

- **Tests added:** 24 across 4 new test files
  - 9 lifecycle (`test_obs_camera_lifecycle.py`)
  - 4 stale-drop (`test_obs_camera_stale_drop.py`)
  - 4 fallback (`test_obs_camera_fallback.py`)
  - 7 stall (`test_obs_camera_stall.py`) -- includes 3 BLE001 / shape-mismatch coverage tests added beyond plan minimum to lift coverage from 88% to 93%
- **Baseline preserved:** 213 Phase 1 + Phase 2 + Plan 03-01 tests still green
- **Phase 3 obs_camera_*.py suite:** 44 tests (20 discovery + 24 new) in ~14 s
- **Total suite:** 237 passing in 26 s
- **Line coverage on obs_camera.py:** 93% (gate >= 90%)
  - 21 uncovered lines:
    - 14 lines in `OpenCvVideoSource` real-cv2 paths (constructor, _apply_props, read, set_resolution, is_opened, release) -- deferred to Phase 8 QA-04 stage smoke per Plan 03-01 acceptance criteria
    - 7 lines in minor early-return branches (511-512 source-release-on-timeout already-released variant; 575 _source-None thread-exit; 617-618 shape-mismatch terminal branch; 703 _device_index None during reopen; 760, 764, 815 misc edges)
- **Branch coverage on `discover_obs_camera_index`:** 100% (inherited from Plan 03-01)
- **Per-test runtime budget:** all tests <= 2 s (slowest is post_warmup_breach_inhibited at 1.84 s)

## Public Symbols Exported (final spec for Phase 4 / Phase 6 consumers)

```python
# Public surface added by Plan 03-02 (combined with Plan 03-01 export)
__all__ = [
    "CameraError",
    "CameraOpenError",
    "CameraStallError",
    "OBSCameraNotFoundError",
    "ObsCamera",                # NEW in Plan 03-02
    "OpenCvVideoSource",
    "VideoSource",
    "_CamState",
    "discover_obs_camera_index",
]


class ObsCamera:
    """Asyncio orchestrator for the OBS Virtual Camera frame source."""

    def __init__(
        self,
        config: Config,
        *,
        video_source_factory: Callable[[int, int, int, int], VideoSource],
        filter_graph_factory: _FilterGraphFactory,
    ) -> None: ...

    # Lifecycle
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def frames(self) -> AsyncIterator[Frame]: ...

    # Read-only status surface (Phase 7 dashboard)
    @property
    def state(self) -> _CamState: ...
    @property
    def is_running(self) -> bool: ...
    @property
    def current_resolution(self) -> tuple[int, int]: ...
    @property
    def last_error(self) -> CameraError | None: ...
```

Contract reminders:
- `start()` is single-shot; calling twice raises `CameraError(f"start() called twice (state={state.value})")`.
- `start()` blocks until the first valid `Frame` arrives via the capture thread, OR raises `CameraOpenError` after `_FIRST_FRAME_TIMEOUT_SEC=3.0`. On timeout `_stop_event` is set, the capture thread is joined with bounded timeout, and `source.release()` is called BEFORE the error is raised (T-03-03).
- `frames()` yields `Frame` instances from a bounded `asyncio.Queue(maxsize=64)` with drop-oldest semantics; consumer-side stale-drop discards frames where `(time.perf_counter_ns() - frame.timestamp_ns) > _STALE_FRAME_MAX_AGE_NS` (100 ms).
- `stop()` close-order is `_stop_event.set()` -> thread join (timeout) -> `source.release()` -> `_state = CLOSED` (Pitfall 7).
- `last_error` is `None` while RUNNING; populated with the typed `CameraError` subclass once a terminal failure latches.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 3 - Blocking] `__all__` sort order**
- **Found during:** Task 1 ruff check
- **Issue:** Ruff `RUF022` flagged `__all__` as unsorted -- "ObsCamera" (newly added) was placed alphabetically between "CameraStallError" and "OBSCameraNotFoundError" but isort-style sorting requires uppercase identifiers (`OBSCameraNotFoundError`) to precede `ObsCamera` (mixed case).
- **Fix:** Re-sorted `__all__` so `OBSCameraNotFoundError` comes before `ObsCamera`.
- **Files modified:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py`
- **Commit:** d099d36 (folded into the feat commit)

**2. [Rule 3 - Blocking] Test file import-block sort order**
- **Found during:** Task 2 + Task 3 ruff checks
- **Issue:** Ruff `I001` flagged the `from pastor_tracker.io.obs_camera import (...)` blocks in three test files. Ruff's isort sorts by exact identifier; `_STALE_FRAME_MAX_AGE_NS` (underscore prefix) sorts before `ObsCamera`, and `_REOPEN_BACKOFFS_MS` before `CameraStallError` and `ObsCamera`.
- **Fix:** Reordered the multi-line imports in `test_obs_camera_stale_drop.py`, `test_obs_camera_fallback.py`, `test_obs_camera_stall.py` to put underscore-prefixed identifiers first.
- **Files modified:** the three test files above
- **Commits:** cf29d0c (stale-drop) + f999e58 (fallback + stall)

**3. [Rule 1 - Bug] Default `_started_camera` script allocates 3.6 GB of BGR buffers**
- **Found during:** Task 2 first test run
- **Issue:** With `script = [_ScriptedFrame(bgr=make_solid_bgr(1920, 1080, ...), ...) for _ in range(600)]`, every test paid for 600 distinct 1920x1080x3 uint8 buffers -- 600 * 6 MiB = ~3.6 GB allocation per test. Per-test runtime ballooned from <100 ms to ~10 s; full Task 2 suite ran in 75 s instead of the expected ~3 s.
- **Fix:** Share a single `make_solid_bgr` ndarray across all 600 entries. The capture thread documents `Frame.image` as read-only (`core/types.py` line 12-15), so aliasing is safe. Documented the rationale in a comment at the allocation site.
- **Files modified:** `pastor_tracker/tests/fixtures/camera_traces.py`
- **Commit:** cf29d0c (folded into the test commit)

**4. [Rule 3 - Blocking] Plan's short-fuse table under-specifies `_P95_WINDOW_SIZE`**
- **Found during:** Task 3 first test run on `test_warmup_breach_triggers_fallback`
- **Issue:** The plan's recommended short-fuse values (lines 1000-1006) compressed `_WARMUP_WINDOW_SEC` from 2.0 to 0.2 s but did NOT compress `_P95_WINDOW_SIZE` (still 30). At 30 fps with realistic test inter-grab delays in the 50-60 ms range (chosen to be > budget ~40 ms but < stall threshold), filling a 30-sample window takes ~1.5-1.8 s -- which exceeds the compressed 200 ms warmup window. The detector never reached `_P95_WINDOW_SIZE` samples inside the warmup, so the fallback could never fire. The plan's example math (`12 ms each -- well above the compressed 6.7 ms budget`) is also off: at 30 fps the budget is ~40 ms, not 6.7 ms.
- **Fix:** Extended `_short_fuse` in the fallback test file to also patch `_P95_WINDOW_SIZE=5`, `_P95_INDEX=4` (index of p95 in 5-sample list), and use `_WARMUP_WINDOW_SEC=1.0` + `_BUDGET_PERSIST_NS=10_000_000` + `_STALL_THRESHOLD_NS=300_000_000` to keep per-test runtime under 2 s while still exercising the real state machine. Documented rationale in module docstring.
- **Files modified:** `pastor_tracker/tests/test_obs_camera_fallback.py`
- **Commit:** f999e58 (folded into the test commit)

**5. [Rule 2 - Hardening] Added 3 coverage tests beyond plan minimum to hit 90% gate**
- **Found during:** Task 3 coverage check
- **Issue:** Plan acceptance criteria require `>= 90%` line coverage on `obs_camera.py`. After the 4 fallback + 4 stall tests landed, coverage stood at 88% -- the two `# noqa: BLE001 -- documented translator` paths and the `frame_shape_mismatch` branch were unexercised. Per Plan 03-01's pattern (Rule 2 coverage hardening) I added three targeted tests: `test_capture_thread_exception_latches_stall_error`, `test_reopen_factory_exception_recorded_in_history`, `test_frame_shape_mismatch_treated_as_stall`. These exercise the BLE001 #1 / BLE001 #2 / Open-Question-1 paths via custom `_RaisingAfterFirstReadSource` / `_ShapeMismatchSource` fakes plus a raising factory closure.
- **Fix:** Added 3 tests at the bottom of `tests/test_obs_camera_stall.py`. Final coverage: 93%.
- **Files modified:** `pastor_tracker/tests/test_obs_camera_stall.py`
- **Commit:** f999e58

### Out-of-scope deviations

None. All issues found were directly caused by Plan 03-02's changes.

## Authentication Gates

None.

## Conventional Commits

| Hash | Type | Subject |
|------|------|---------|
| `d099d36` | feat | `feat(03-02): obs_camera orchestrator -- lifecycle + capture thread + fallback + stall recovery (IO-CAM-01 / IO-CAM-03 / IO-CAM-04)` |
| `cf29d0c` | test | `test(03-02): obs_camera lifecycle + stale-drop tests + _started_camera fixture (IO-CAM-04 + lifecycle)` |
| `f999e58` | test | `test(03-02): obs_camera fallback + stall recovery tests (IO-CAM-03 / IO-CAM-04)` |

## Verification Gates Final

- `ruff check src tests`: PASS (no violations)
- `mypy --strict src` (17 source files, disallow_any_explicit): PASS
- `pytest -ra` (full suite): 237 passed in 26.13 s (213 baseline + 24 new)
- `pytest tests/test_obs_camera_*.py`: 44 passed in ~14 s (20 discovery + 9 lifecycle + 4 stale-drop + 4 fallback + 7 stall)
- Line coverage on `obs_camera.py`: 93% (gate >= 90%)
- Branch coverage on `discover_obs_camera_index`: 100% (inherited from Plan 03-01)
- Pure-import smoke: `from pastor_tracker.io.obs_camera import ObsCamera, VideoSource, _CamState; assert hasattr(ObsCamera, 'start') and hasattr(ObsCamera, 'stop') and hasattr(ObsCamera, 'frames')` exits 0
- Per-test runtime budget: max test duration 1.84 s (well under 5 s acceptance criterion)
- Pitfall 7 close-order verified by `test_stop_releases_source_on_normal_exit` (release_calls == 1 after stop)
- T-03-03 release-on-timeout verified by `test_first_frame_timeout_raises_camera_open_error` (release_calls >= 1 after timeout)
- 13 distinct structlog event names emitted across the lifecycle:
  - `camera_discovered` / `camera_multiple_matches` (Plan 03-01 discovery)
  - `camera_started` / `camera_thread_exited` / `camera_first_frame_timeout` (lifecycle)
  - `frames_queue_full` / `frame_stale_dropped` / `frame_shape_mismatch` (queue + consumer)
  - `camera_stall_detected` / `camera_reopen_attempt_failed` / `camera_reopen_succeeded` / `camera_stall_unrecoverable` (stall recovery)
  - `camera_resolution_fallback` / `camera_resolution_breach_at_720p` (resolution fallback)
- Exactly 2 documented `# noqa: BLE001 -- documented translator` comments (capture loop outer try, reopen factory call) -- ruff BLE001 raises on any third occurrence
- No `print`, no `time.sleep` in app code, no magic numbers (all literals sourced from `Final` constants)

## Self-Check: PASSED

All claimed files exist:
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`: FOUND (826 LOC, modified)
- `pastor_tracker/tests/fixtures/camera_traces.py`: FOUND (163 LOC, modified)
- `pastor_tracker/tests/test_obs_camera_lifecycle.py`: FOUND (169 LOC, created)
- `pastor_tracker/tests/test_obs_camera_stale_drop.py`: FOUND (184 LOC, created)
- `pastor_tracker/tests/test_obs_camera_fallback.py`: FOUND (252 LOC, created)
- `pastor_tracker/tests/test_obs_camera_stall.py`: FOUND (363 LOC, created)

All claimed commits exist on `fresh-2026`:
- `d099d36`: FOUND (`feat(03-02): obs_camera orchestrator ...`)
- `cf29d0c`: FOUND (`test(03-02): obs_camera lifecycle + stale-drop tests ...`)
- `f999e58`: FOUND (`test(03-02): obs_camera fallback + stall recovery tests ...`)

## Phase 3 Closure

Phase 3 requirements all complete:

- **IO-CAM-01** (orchestrator-side first-frame open lifecycle): `ObsCamera.start()` blocks until first frame OR raises `CameraOpenError` after 3 s timeout; on timeout the source handle is released (T-03-03 mitigation); double-start raises `CameraError`. Discovery half closed by Plan 03-01.
- **IO-CAM-02** (missing-device hard-fail): `OBSCameraNotFoundError` carrying `.expected` + `.available` -- closed by Plan 03-01.
- **IO-CAM-03** (resolution fallback): one-shot 1080p->720p inside 2 s warmup window when `p95 > 1.2 x target_interval` persists >= 1 s; never re-promote, never re-fallback; already-720p case logs ERROR and continues capturing.
- **IO-CAM-04** (timestamp + stall recovery + stale-drop): every Frame carries `time.perf_counter_ns()` timestamp at grab time; consumer-side stale-drop > 100 ms with `frame_stale_dropped` WARN; capture-thread stall detection > 200 ms inter-grab triggers `_attempt_reopen` with linear backoffs (200, 500, 1000) ms; 3 failed reopens latch `CameraStallError(attempts=...)` on the loop side; `cam.last_error` exposes the typed error; `cam.state == FAULTED`.

ROADMAP.md Phase 3 entry should now be marked complete; REQUIREMENTS.md IO-CAM-01..04 rows flipped to "Complete" via `gsd-sdk query requirements.mark-complete`.
