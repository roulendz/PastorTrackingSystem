---
phase: 03-camera-i-o
plan: 01
subsystem: io
tags:
  - camera
  - opencv
  - directshow
  - discovery
  - transport
  - protocol
dependency_graph:
  requires:
    - core/types.py:Frame DTO contract (Phase 1)
    - io/arduino_motor.py + io/arduino_transport.py (Phase 2 -- structural template)
    - config.py: obs_camera_name / capture_width / capture_height / capture_fps
  provides:
    - "pastor_tracker.io.obs_camera.VideoSource (Protocol DI seam)"
    - "pastor_tracker.io.obs_camera.OpenCvVideoSource (real cv2 wrapper)"
    - "pastor_tracker.io.obs_camera.discover_obs_camera_index (DirectShow enumerator)"
    - "pastor_tracker.io.obs_camera.CameraError + 3 typed subclasses"
    - "pastor_tracker.io.obs_camera._CamState (6-state enum)"
    - "pastor_tracker.io.obs_camera._P95Detector (rolling p95 helper)"
    - "tests.fixtures.camera_traces.FakeVideoSource + _ScriptedFrame + make_solid_bgr"
    - "16 module-level Final constants (no magic numbers)"
  affects:
    - "pastor_tracker.io.obs_camera (Plan 03-02 will append ObsCamera orchestrator class)"
tech-stack:
  added:
    - "opencv-python>=4.10,<5.0"
    - "pygrabber==0.2"
  patterns:
    - "Protocol DI seam (runtime_checkable) -- mirrors Phase 2 SerialTransport"
    - "Factory closure injection (Callable[[], FilterGraph]) -- Pitfall 10 mitigation"
    - "Distinct success log events (camera_discovered vs camera_multiple_matches)"
    - "Rolling p95 over collections.deque -- one-shot detector with persistence gate"
    - "Tiger-style typed-error hierarchy (CameraError root + 3 subclasses)"
key-files:
  created:
    - pastor_tracker/src/pastor_tracker/io/obs_camera.py
    - pastor_tracker/tests/fixtures/camera_traces.py
    - pastor_tracker/tests/test_obs_camera_discovery.py
  modified:
    - pastor_tracker/pyproject.toml
    - pastor_tracker/uv.lock
decisions:
  - "VideoSource Protocol DI seam -- production wires OpenCvVideoSource, tests wire FakeVideoSource (Phase 2 idiom)"
  - "Factory closure for FilterGraph injection -- avoids CoInitialize race in pytest workers (Pitfall 10)"
  - "Two distinct discovery log events -- camera_discovered (single) vs camera_multiple_matches (multi); mirrors Phase 2 WARN 5"
  - "OBSCameraNotFoundError carries .expected and .available attributes for operator triage"
  - "_P95Detector is one-shot per breach -- recovery resets first_breach_ns; partial breaches do NOT accumulate"
  - "Cast _ImageArray at OpenCvVideoSource.read seam (cv2 stubs widen dtype to int|float; runtime guarantee is uint8)"
metrics:
  duration: ~50 minutes
  completed: 2026-05-04
  tasks_completed: 3
  files_created: 3
  files_modified: 2
  commits: 3
  baseline_tests: 193
  new_tests: 20
  total_tests: 213
  branch_coverage_obs_camera: "14/14 (100%) -- discover function + _P95Detector"
  line_coverage_obs_camera: "88% (12 lines uncovered in OpenCvVideoSource real-cv2 paths -- deferred to QA-04)"
---

# Phase 3 Plan 1: OBS Camera Transport Seam + Discovery Summary

JWT-style transport seam + DirectShow enumeration helper for the OBS Virtual
Camera frame source -- ships VideoSource Protocol DI, OpenCvVideoSource real
impl, discover_obs_camera_index with exact-case-sensitive friendly-name match,
CameraError typed-error hierarchy, _P95Detector rolling-p95 helper, and the
FakeVideoSource test fixture. Closes IO-CAM-01 (discovery half) and IO-CAM-02
(missing-device hard-fail). The ObsCamera orchestrator class lands in Plan
03-02 (marker comment at EOF of obs_camera.py).

## Files Created

| File | LOC | Purpose |
|------|-----|---------|
| pastor_tracker/src/pastor_tracker/io/obs_camera.py | 360 | VideoSource Protocol seam, OpenCvVideoSource real impl, discover_obs_camera_index, CameraError + 3 subclasses, _CamState enum, _P95Detector helper, 16 Final constants |
| pastor_tracker/tests/fixtures/camera_traces.py | 89 | FakeVideoSource + _ScriptedFrame + make_solid_bgr (mirrors arduino_traces.py shape) |
| pastor_tracker/tests/test_obs_camera_discovery.py | 284 | 20 tests covering discovery matrix, error class behaviour, FakeVideoSource Protocol compliance, _P95Detector arithmetic |

## Files Modified

| File | Change | Purpose |
|------|--------|---------|
| pastor_tracker/pyproject.toml | +2 dependency entries | Pin opencv-python>=4.10,<5.0 + pygrabber==0.2 (mypy overrides at lines 81-83 already shipped) |
| pastor_tracker/uv.lock | regenerated | Lock new deps (comtypes 1.4.16, opencv-python 4.13.0.92, pygrabber 0.2) |

## Test Count and Coverage

- **Tests added:** 20 in `tests/test_obs_camera_discovery.py`
  - 7 `test_discover_*` (exact match, missing+device list, empty devices, multi-match, available_count, case-sensitive, factory invocation contract)
  - 2 `test_OBSCameraNotFoundError_*` (attributes carry-through, CameraError parent-class catch)
  - 1 `test_CameraStallError_carries_attempts_attribute`
  - 6 `test_P95Detector_*` (budget arithmetic at 30 fps, short-window guard, no-breach below budget, persistence requirement, recovery-resets-first-breach)
  - 4 `test_FakeVideoSource_*` (runtime_checkable Protocol satisfaction, release observability, scripted-then-None read, set_resolution observability)
- **Baseline preserved:** 193 Phase 1 + Phase 2 tests still green
- **Total suite:** 213 passing in ~10 s
- **Branch coverage on obs_camera.py:** 14/14 (100%) -- every branch in `discover_obs_camera_index` + `_P95Detector.budget_breached_persistent` is hit
- **Line coverage on obs_camera.py:** 88% -- 12 uncovered lines all in `OpenCvVideoSource` real-cv2 paths (constructor, _apply_props, read, set_resolution, is_opened, release). These require a real DirectShow device and are deferred to Phase 8 QA-04 stage smoke per the plan acceptance criteria.

## Public Symbols Exported (final spec for Plan 03-02)

```python
# Module-level Final constants (16 total -- RESEARCH Constants table verbatim)
_FRAMES_QUEUE_MAX_SIZE: Final[int] = 64
_FIRST_FRAME_TIMEOUT_SEC: Final[float] = 3.0
_CAPTURE_JOIN_TIMEOUT_SEC: Final[float] = 1.0
_STALL_THRESHOLD_NS: Final[int] = 200_000_000
_STALE_FRAME_MAX_AGE_NS: Final[int] = 100_000_000
_REOPEN_BACKOFFS_MS: Final[tuple[int, int, int]] = (200, 500, 1000)
_WARMUP_WINDOW_SEC: Final[float] = 2.0
_BUDGET_MULTIPLIER: Final[float] = 1.2
_BUDGET_PERSIST_NS: Final[int] = 1_000_000_000
_P95_WINDOW_SIZE: Final[int] = 30
_P95_INDEX: Final[int] = 28
_FALLBACK_WIDTH: Final[int] = 1280
_FALLBACK_HEIGHT: Final[int] = 720
_NS_PER_MS: Final[int] = 1_000_000
_MS_PER_SEC: Final[float] = 1_000.0
_CAPTURE_THREAD_TICK_SEC: Final[float] = 0.1
_NS_PER_SEC: Final[int] = 1_000_000_000

# Type aliases (PEP 695)
type _ImageArray = npt.NDArray[np.uint8]
type _FilterGraphFactory = Callable[[], FilterGraph]

# Protocol DI seam
@runtime_checkable
class VideoSource(Protocol):
    def read(self) -> tuple[bool, _ImageArray | None]: ...
    def set_resolution(self, width: int, height: int, fps: int) -> None: ...
    def is_opened(self) -> bool: ...
    def release(self) -> None: ...

# Production impl
class OpenCvVideoSource:
    def __init__(self, device_index: int, width: int, height: int, fps: int) -> None: ...
    def read(self) -> tuple[bool, _ImageArray | None]: ...
    def set_resolution(self, width: int, height: int, fps: int) -> None: ...
    def is_opened(self) -> bool: ...
    def release(self) -> None: ...

# Discovery
def discover_obs_camera_index(
    expected_name: str,
    *,
    factory: _FilterGraphFactory,
    logger: structlog.stdlib.BoundLogger,
) -> int: ...

# Error hierarchy
class CameraError(Exception): ...
class OBSCameraNotFoundError(CameraError):
    expected: str
    available: list[str]
class CameraOpenError(CameraError): ...
class CameraStallError(CameraError):
    attempts: list[tuple[int, int, str]]

# State machine
class _CamState(enum.Enum):
    DISCONNECTED, OPENING, RUNNING, REOPENING, FAULTED, CLOSED

# Pure helper
class _P95Detector:
    def __init__(self, target_fps: int) -> None: ...
    def observe(self, delta_ns: int) -> None: ...
    def budget_breached_persistent(self, now_ns: int) -> bool: ...
    @property
    def current_p95_ns(self) -> int: ...
    @property
    def budget_ns(self) -> int: ...
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] PEP 695 `type` keyword instead of `TypeAlias` annotation**
- **Found during:** Task 2 ruff check
- **Issue:** Ruff `UP040` flagged `_ImageArray: TypeAlias = ...` and `_FilterGraphFactory: TypeAlias = ...` as outdated; project targets Python 3.12 (PEP 695 native).
- **Fix:** Switched to `type _ImageArray = npt.NDArray[np.uint8]` and `type _FilterGraphFactory = Callable[[], FilterGraph]`.
- **Files modified:** pastor_tracker/src/pastor_tracker/io/obs_camera.py
- **Commit:** 61e49eb (folded into the feat commit)

**2. [Rule 3 - Blocking] `Callable` import path**
- **Found during:** Task 2 ruff check
- **Issue:** Ruff `UP035` flagged `from typing import Callable` (deprecated re-export); should be `from collections.abc import Callable`.
- **Fix:** Moved `Callable` import to `from collections.abc import Callable`; `Final, Protocol, runtime_checkable` stay in `typing`. Mirrors arduino_motor.py:36-37 split.
- **Files modified:** pastor_tracker/src/pastor_tracker/io/obs_camera.py
- **Commit:** 61e49eb (folded into the feat commit)

**3. [Rule 3 - Blocking] cv2 stub dtype widening at the seam**
- **Found during:** Task 2 mypy check
- **Issue:** opencv-python's stubs type `VideoCapture.read()` as returning `(bool, Mat | ndarray[Any, dtype[integer | floating]])` -- which is wider than the `_ImageArray = NDArray[uint8]` contract our Protocol requires.
- **Fix:** Added a single `# type: ignore[return-value]` at the seam in `OpenCvVideoSource.read()` with a documented narrowing rationale. The runtime guarantee is uint8 for VideoCapture (verified via OpenCV docs); downstream `Frame.__post_init__` validates the shape.
- **Files modified:** pastor_tracker/src/pastor_tracker/io/obs_camera.py
- **Commit:** 61e49eb (folded into the feat commit)

**4. [Rule 3 - Blocking] pygrabber `get_input_devices` untyped call**
- **Found during:** Task 2 mypy check
- **Issue:** mypy --strict + `disallow_any_explicit` flagged `graph.get_input_devices()` as a call to an untyped function (pygrabber has no stubs; mypy override only suppresses `import-untyped`, not `no-untyped-call`).
- **Fix:** Added `# type: ignore[no-untyped-call]` at the call site with a documented rationale.
- **Files modified:** pastor_tracker/src/pastor_tracker/io/obs_camera.py
- **Commit:** 61e49eb (folded into the feat commit)

**5. [Plan deviation - reformatted long-comment lines]**
- **Found during:** Task 2 ruff check
- **Issue:** Ruff `E501` (line length 100). The plan's verbatim Constants-table block had aligned trailing comments that exceeded 100 chars. The verbatim sample was illustrative; ruff's hard line-length limit took precedence.
- **Fix:** Moved the citation comments to dedicated lines above each constant (preserves citation traceability without violating line length). All 16 constants still pinned with citations.
- **Files modified:** pastor_tracker/src/pastor_tracker/io/obs_camera.py
- **Commit:** 61e49eb (folded into the feat commit)

**6. [Rule 2 - Hardening] Added 7 _P95Detector tests beyond plan minimum**
- **Found during:** Task 3 coverage check
- **Issue:** Plan acceptance criteria required 100% branch coverage on `discover_obs_camera_index`. Initial 12 tests hit that target. However, `_P95Detector` was also explicitly shipped in this plan (not deferred), and only the smoke-import check exercised it -- 64% file coverage with multiple uncovered branches in the rolling-p95 logic. Per CLAUDE.md tiger-style + the plan's verification step 6 (`d.budget_ns == int(...)` check), the helper class warranted explicit test coverage.
- **Fix:** Added 6 `test_P95Detector_*` tests covering budget arithmetic, short-window guard (the missing branch), no-breach-below-budget, persistence requirement, and recovery-resets-first-breach. Also added `test_CameraStallError_carries_attempts_attribute` to cover the error class body. Final branch coverage: 14/14 (100%); line coverage 88% (12 uncovered lines are all real-cv2 OpenCvVideoSource paths deferred to QA-04).
- **Files modified:** pastor_tracker/tests/test_obs_camera_discovery.py
- **Commit:** 0d69a42

## Authentication Gates

None.

## Conventional Commits

| Hash | Type | Subject |
|------|------|---------|
| `fa71f22` | chore | `chore(03-01): add opencv-python 4.10 + pygrabber 0.2 deps` |
| `61e49eb` | feat | `feat(03-01): obs_camera transport seam + discovery + p95 helper (IO-CAM-01 / IO-CAM-02 part 1)` |
| `0d69a42` | test | `test(03-01): obs_camera discovery matrix + FakeVideoSource fixture (IO-CAM-01 / IO-CAM-02)` |

## Verification Gates Final

- ruff check src tests: PASS (no violations)
- mypy --strict (17 source files, disallow_any_explicit): PASS
- pytest -ra (full suite): 213 passed in 9.99 s
- pytest tests/test_obs_camera_discovery.py: 20 passed in 0.65 s
- Branch coverage on obs_camera.py: 14/14 (100%)
- Line coverage on obs_camera.py: 88% (12 uncovered lines all in real-cv2 paths)
- Pure-import smoke: `from pastor_tracker.io.obs_camera import VideoSource, OpenCvVideoSource, discover_obs_camera_index, CameraError, OBSCameraNotFoundError, CameraOpenError, CameraStallError, _CamState, _P95Detector` exits 0
- _P95Detector budget arithmetic: `_P95Detector(30).budget_ns == int(int(1e9 / 30) * 1.2) == 39_999_999` (verified by test)
- isinstance gate: `OBSCameraNotFoundError` and `CameraOpenError` both subclass `CameraError`; distinct types
- Pitfall coverage:
  - Pitfall 1 (CAP_DSHOW backend hint mandatory): cv2.CAP_DSHOW pinned at OpenCvVideoSource.__init__:212
  - Pitfall 2 (OBS not running): documented as `CameraOpenError` distinct from `OBSCameraNotFoundError`; first-frame wait deferred to Plan 03-02 orchestrator
  - Pitfall 5 ((False, None) read contract): typed `tuple[bool, _ImageArray | None]` at the Protocol seam; OpenCvVideoSource.read narrows correctly
  - Pitfall 10 (FilterGraph CoInitialize race): factory closure pattern; tests use SimpleNamespace stubs (verified by `test_discover_factory_invoked_per_call`)

## Self-Check: PASSED

All claimed files exist:
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`: FOUND (360 LOC)
- `pastor_tracker/tests/fixtures/camera_traces.py`: FOUND (89 LOC)
- `pastor_tracker/tests/test_obs_camera_discovery.py`: FOUND (284 LOC)
- `pastor_tracker/pyproject.toml`: FOUND (modified -- 2 dep entries appended)
- `pastor_tracker/uv.lock`: FOUND (regenerated)

All claimed commits exist on `worktree-agent-a421f8204be2bb580`:
- `fa71f22`: FOUND (chore: deps)
- `61e49eb`: FOUND (feat: transport seam + discovery + p95)
- `0d69a42`: FOUND (test: discovery matrix + FakeVideoSource)
