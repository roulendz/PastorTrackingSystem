# Phase 4: Perception — Pattern Map

**Mapped:** 2026-05-05
**Files analyzed:** 13 (5 source + 1 fixture + 5 tests + 1 config edit + 1 type edit)
**Analogs found:** 13 / 13 (100% — all closely mirror Phase 2 / Phase 3 / core)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/pastor_tracker/perception/pose_detector.py` | controller (orchestrator + DI seam) | request-response across IPC | `src/pastor_tracker/io/obs_camera.py` (+ `arduino_motor.py` for executor lifecycle) | exact |
| `src/pastor_tracker/perception/_pose_worker.py` | utility (top-level worker module) | transform (one ndarray in -> result out) | `src/pastor_tracker/io/arduino_motor.py::_rx_loop` (top-level thread target shape) + Pattern 2 in 04-RESEARCH | role-match (no existing process-pool worker; closest is the daemon-thread target idiom) |
| `src/pastor_tracker/perception/subject_tracker.py` | service (state machine + Kalman wrapper) | streaming transform on Detection -> TrackedSubject | `src/pastor_tracker/core/damping.py` (pure step transform with internal state) + `arduino_motor.py::_recover` (state-machine discipline + latched-error gate) | exact (composite: math from `damping`, lifecycle from `arduino_motor`) |
| `src/pastor_tracker/perception/_kalman.py` (optional) | utility (small math helper class) | pure transform with internal state | `src/pastor_tracker/core/damping.py::CriticallyDampedFollower` | exact |
| `src/pastor_tracker/perception/__init__.py` (modify) | config | n/a | `src/pastor_tracker/io/__init__.py` (currently empty) + `obs_camera.__all__` | role-match |
| `src/pastor_tracker/config.py` (modify) | config | n/a | existing field declarations in `config.py` | exact |
| `src/pastor_tracker/core/types.py` (verify; possibly extend) | model | n/a | `core/types.py::Detection`, `TrackedSubject` already declared | exact |
| `tests/fixtures/pose_traces.py` | test fixture | scripted producer | `tests/fixtures/arduino_traces.py` + `tests/fixtures/camera_traces.py` (FakeSerialTransport / FakeVideoSource pattern) | exact |
| `tests/test_pose_detector.py` | test (lifecycle + IPC roundtrip + drop-oldest) | request-response | `tests/test_obs_camera_lifecycle.py` + `tests/test_obs_camera_stale_drop.py` | exact |
| `tests/test_subject_tracker_lock.py` | test (state machine) | event-driven | `tests/test_arduino_motor_handshake.py` + `tests/test_arduino_motor_recovery.py` | exact |
| `tests/test_subject_tracker_kalman.py` | test (property tests on real math) | pure transform | `tests/test_damping.py` | exact |
| `tests/test_subject_tracker_hold.py` | test (state machine — HOLDING branch) | event-driven | `tests/test_arduino_motor_recovery.py` | role-match |
| `tests/test_perception_e2e.py` | test (end-to-end pipeline) | streaming | `tests/test_obs_camera_lifecycle.py::test_frames_yields_in_order` | role-match |

---

## Pattern Assignments

### `pastor_tracker/perception/pose_detector.py` (orchestrator + DI seam, request-response across IPC)

**Primary analog:** `pastor_tracker/io/obs_camera.py` (lines 1–1046)
**Secondary analog:** `pastor_tracker/io/arduino_motor.py` (lines 1–960) for executor + `add_done_callback` discipline

**Module docstring shape** — copy from `obs_camera.py:1-33`. Header explains: pure-edge orchestrator, executor lifecycle, single-producer/single-consumer queue invariant, drop-oldest semantics, `_pose_worker` sibling split note (mirrors Plan 02-01/02-02 split commentary).

**Imports pattern** — mirror `obs_camera.py:34-51`:
```python
from __future__ import annotations

import asyncio
import enum
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future, ProcessPoolExecutor
from multiprocessing import shared_memory
from typing import Final, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, Frame
from pastor_tracker.perception import _pose_worker
```

**Module-level Final constants pattern** — mirror `obs_camera.py:53-88` and `arduino_motor.py:86-103`. Every literal cited (CLAUDE.md rule 6). Examples to define:
- `_INFLIGHT_SLOTS: Final[int] = 1` (Pattern 4 — drop-oldest at ingress, RESEARCH 04-pattern-4)
- `_EXECUTOR_SHUTDOWN_TIMEOUT_SEC: Final[float] = 5.0` (mirror `_RX_JOIN_TIMEOUT_SEC = 1.0`)
- `_WORKER_WARMUP_TIMEOUT_SEC: Final[float] = 30.0` (Pitfall 10 — first CUDA inference)
- `_NS_PER_SEC: Final[int] = 1_000_000_000` (already used in `obs_camera.py:88`)

**`__all__` re-export pattern** — mirror `obs_camera.py:104-114` and `arduino_motor.py:74-84`:
```python
__all__ = [
    "PerceptionError",
    "PoseDetector",
    "PoseEngine",
    "PoseEngineUnavailableError",
    "UltralyticsPoseEngine",
]
```

**Error class hierarchy pattern** — mirror `obs_camera.py:122-160` (root + 3 typed subclasses) and `arduino_motor.py:121-154` (root + 5 typed subclasses):
```python
class PerceptionError(Exception):
    """Root for all perception-originated errors."""

class PoseEngineUnavailableError(PerceptionError):
    """Worker process failed to load YOLO model OR CUDA requested but missing."""

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(f"expected device={expected!r}, available={available}")
        self.expected: str = expected
        self.available: list[str] = available
```
Note: copy the `OBSCameraNotFoundError(__init__)` shape verbatim — `expected=` / `available=` keyword args, structured payload for triage. RESEARCH 04 Specifics §6 explicitly cites this shape.

**State enum pattern** — mirror `obs_camera.py:168-176` (`_CamState`) and `arduino_motor.py:105-113` (`_MotorState`):
```python
class _DetectorState(enum.Enum):
    DISCONNECTED = "disconnected"
    STARTING = "starting"   # executor.submit warmup pending
    RUNNING = "running"
    FAULTED = "faulted"
    CLOSED = "closed"
```

**Protocol DI seam pattern** — mirror `arduino_transport.py:77-89` (`SerialTransport`) and `obs_camera.py:185-199` (`VideoSource`):
```python
@runtime_checkable
class PoseEngine(Protocol):
    """Minimal DI surface — production = UltralyticsPoseEngine, tests = FakePoseEngine."""

    async def detect(self, frame: Frame) -> list[Detection]: ...

    async def close(self) -> None: ...
```

**Production wrapper class pattern** — mirror `obs_camera.py:202-251` (`OpenCvVideoSource`) and `arduino_transport.py:92-128` (`PySerialTransport`). `UltralyticsPoseEngine` owns: `ProcessPoolExecutor(max_workers=1)`, the long-lived `SharedMemory` block, the `_inflight: Future | None` field, and the `loop.run_in_executor`/`asyncio.wrap_future` bridge.

**Lifecycle pattern (start/stop/close-order)** — mirror `obs_camera.py:468-622` exactly:
- `start()` — single-shot guard (`obs_camera.py:481-484`), `_loop = asyncio.get_running_loop()`, allocate `SharedMemory(create=True, size=W*H*3)`, spawn `ProcessPoolExecutor(max_workers=1)`, run a tiny warmup inference (Pitfall 10 mitigation), latch typed errors before propagation (`obs_camera.py:487-513` translator pattern):
```python
if self._state is not _DetectorState.DISCONNECTED:
    raise PerceptionError(f"start() called twice (state={self._state.value})")
self._loop = asyncio.get_running_loop()
self._state = _DetectorState.STARTING
try:
    self._executor = ProcessPoolExecutor(max_workers=1)
    self._shm = shared_memory.SharedMemory(
        create=True,
        size=self._config.capture_width * self._config.capture_height * 3,
    )
    # Pitfall 10 — warm CUDA / spend cold-start cost here
    await self._loop.run_in_executor(
        self._executor, _pose_worker.warmup,
        self._resolved_device, self._config.yolo_model_path,
    )
except Exception as exc:
    self._state = _DetectorState.FAULTED
    self._latched_error = PoseEngineUnavailableError(...)
    raise self._latched_error from exc
self._state = _DetectorState.RUNNING
```

- `stop()` — close-order from `obs_camera.py:581-622` adapted: cancel `_inflight` Future first (mirrors `_recover_task` cancel in `arduino_motor.py:269-273`), then `executor.shutdown(wait=True, cancel_futures=True)`, then `_shm.close()` + `_shm.unlink()` (Pitfall 7 — wrap in try/finally), then state transition to `CLOSED` (preserve `FAULTED` per `obs_camera.py:621-622`):
```python
if self._inflight is not None:
    self._inflight.cancel()
    self._inflight = None
if self._executor is not None:
    await asyncio.to_thread(
        self._executor.shutdown, wait=True, cancel_futures=True
    )
    self._executor = None
if self._shm is not None:
    try:
        self._shm.close()
        self._shm.unlink()  # no-op on Windows but cheap insurance
    finally:
        self._shm = None
if self._state is not _DetectorState.FAULTED:
    self._state = _DetectorState.CLOSED
```

**Drop-oldest at ingress pattern** — mirror `obs_camera.py:_enqueue_frame:732-749` and `arduino_motor.py:_enqueue:473-489`. The Phase 4 twist: instead of a full `asyncio.Queue` of size N, the in-flight Future itself IS the size-1 queue (RESEARCH 04 Open Question 1 recommendation):
```python
async def consume(self, frame: Frame) -> None:
    if self._inflight is not None and not self._inflight.done():
        self._logger.warning(
            "inference_drop_oldest",
            reason="previous_inference_still_running",
            dropped_timestamp_ns=frame.timestamp_ns,
        )
        self._inflight.cancel()  # best-effort
    # Copy into the long-lived shared block (only memcpy in pipeline)
    view = np.ndarray(frame.image.shape, dtype=np.uint8, buffer=self._shm.buf)
    view[:] = frame.image
    self._inflight = asyncio.wrap_future(
        self._executor.submit(
            _pose_worker.infer,
            self._shm.name, frame.image.shape,
            frame.timestamp_ns,
            self._resolved_device, self._config.yolo_model_path,
        )
    )
```
WARN log key naming follows the established `_logger.warning("event_name", **structured_fields)` shape (`obs_camera.py:744-748`, `arduino_motor.py:486-488`).

**Async iterator surface** — mirror `obs_camera.py::frames:976-1045` and `arduino_motor.py::events:951-959`:
```python
async def detections(self) -> AsyncIterator[list[Detection]]:
    while True:
        if self._latched_error is not None and self._inflight is None:
            raise self._latched_error
        # await the in-flight future; drain to a list[Detection]
        ...
        yield detections_for_this_frame
```

**Read-only status surface pattern** — mirror `obs_camera.py:444-462` and `arduino_motor.py:200-208`:
```python
@property
def state(self) -> _DetectorState: return self._state

@property
def is_running(self) -> bool: return self._state is _DetectorState.RUNNING

@property
def last_error(self) -> PerceptionError | None: return self._latched_error
```

**Translator-catch / `noqa: BLE001` pattern** — mirror `obs_camera.py:657` and `arduino_motor.py:390`. Cross-process boundary catches use `except Exception as exc:  # noqa: BLE001 -- documented translator` with re-raise as typed error. Document the rationale in a comment line above the catch.

**Logger binding pattern** — `self._logger = structlog.get_logger(module="pose_detector")` (mirror `obs_camera.py:438`, `arduino_motor.py:194`).

---

### `pastor_tracker/perception/_pose_worker.py` (utility, transform — runs in spawned child process)

**Primary analog:** RESEARCH 04 Pattern 2 (Code Example, lines 281-324). No existing process-pool worker in the codebase — this is a new shape.
**Secondary analog (for top-level function discipline):** `arduino_motor.py::_rx_loop` (top-level method-as-thread-target, lines 379-409) — the constraint "no closures, picklable target" is the same.

**Module docstring** — explicitly state the spawn-pickle invariants (Pitfall 4):
```python
"""Worker target for ProcessPoolExecutor (Phase 4 perception).

This module is imported by the spawned child interpreter (Windows: spawn-only).
Top-level functions only — no closures, no class instances bound at import time.

Invariants (RESEARCH 04 Pitfalls 3 + 4):
    * Worker process loads YOLO model EXACTLY ONCE on first call;
      reuse keeps BoT-SORT track-id continuity (`persist=True` semantics).
    * Never re-instantiate `YOLO(...)` mid-run — track-id state is
      bound to the model instance.
    * Top-level module functions only — closures don't pickle across spawn.
"""
```

**Lazy-init module globals** — copy RESEARCH 04 Pattern 2 verbatim:
```python
_model: object | None = None
_shm: shared_memory.SharedMemory | None = None
_shm_name: str | None = None
```

**`infer(...)` entry point** — copy RESEARCH 04 Pattern 2 (lines 306-323) but enrich the return type to a frozen DTO compatible with `core/types.Detection`. The translation step (`_translate(results[0])`) MUST consume `boxes.xyxyn` (Pitfall 12), not `boxes.xyxy`, and forward `frame.timestamp_ns` from caller, not compute its own (Pitfall 11).

**`warmup(...)` entry point** — separate top-level function called by `PoseDetector.start()` to spend cold-start cost off the hot path (Pitfall 10):
```python
def warmup(device: str, model_path: str) -> None:
    _ensure_model(model_path, device)
```

**Suppress ultralytics stdout** — first line of `_ensure_model` (Pitfall 6):
```python
import logging
logging.getLogger("ultralytics").setLevel(logging.WARNING)
```

---

### `pastor_tracker/perception/subject_tracker.py` (service, streaming transform)

**Primary analog:** `pastor_tracker/io/arduino_motor.py` for the state-machine + latched-error + async-iterator output surface
**Secondary analog:** `pastor_tracker/core/damping.py` for the pure-math idiom (frozen-config + `step` transform)

**Module docstring shape** — adapt `arduino_motor.py:1-28`. State the five-state machine (UNLOCKED / SEEKING / LOCKED / HOLDING / LOST / RE_ACQUIRING per RESEARCH 04 Pattern 5), the lock-loss timer source (`time.perf_counter_ns()`), and the "no Kalman mock — drive real filterpy" test contract.

**State enum pattern** — mirror `arduino_motor.py:105-113` (`_MotorState`):
```python
class _LockState(enum.Enum):
    UNLOCKED = "unlocked"
    SEEKING = "seeking"
    LOCKED = "locked"
    HOLDING = "holding"
    LOST = "lost"
    RE_ACQUIRING = "re_acquiring"
```

**Module-level Final constants** — same discipline as `obs_camera.py:53-88`. Per RESEARCH 04 (Project Constraints table) and Open Question 2:
```python
_LOCK_LOSS_TIMEOUT_SEC: Final[float] = 2.0           # PERC-05
_HOLD_POSTERIOR_FRAME_THRESHOLD: Final[int] = 3      # PERC-07
_CENTRAL_REGION_FRACTION: Final[float] = 0.6         # PERC-04
_CENTRAL_HALF: Final[float] = (1.0 - _CENTRAL_REGION_FRACTION) / 2.0  # 0.2
_NOSE_KP_INDEX: Final[int] = 0                       # COCO 17
_LEFT_SHOULDER_KP_INDEX: Final[int] = 5
_RIGHT_SHOULDER_KP_INDEX: Final[int] = 6
_LEFT_HIP_KP_INDEX: Final[int] = 11
_RIGHT_HIP_KP_INDEX: Final[int] = 12
_WEIGHT_NOSE: Final[float] = 0.4                     # PERC-02
_WEIGHT_SHOULDER_MID: Final[float] = 0.4
_WEIGHT_HIP_MID: Final[float] = 0.2
_KALMAN_PROCESS_NOISE_VAR: Final[float] = 1e-3       # tune Phase 8
_KALMAN_MEASUREMENT_NOISE_VAR: Final[float] = 1e-4
_KALMAN_INITIAL_VEL_COV: Final[float] = 1.0          # CONTEXT Area 3
```

**Pure helpers (centroid + central-region)** — mirror `core/damping.py` purity (no I/O, returns by value). Copy code excerpts from RESEARCH 04 Code Examples 4–5 (lines 605-637) verbatim into the module body.

**State machine `tick()` entry point** — flat `match` over current state (CLAUDE.md rule 5: ≤2 nesting). Mirror the `_on_rx_event` dispatch shape in `arduino_motor.py:411-471`:
```python
async def consume(self, detections: list[Detection], now_ns: int) -> TrackedSubject | None:
    self._raise_if_latched()  # mirror arduino_motor._raise_if_latched:795-805
    match self._state:
        case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
            return self._try_lock(detections, now_ns)
        case _LockState.LOCKED:
            return self._tick_locked(detections, now_ns)
        case _LockState.HOLDING:
            return self._tick_holding(detections, now_ns)
        case _LockState.LOST:
            return self._try_reacquire(detections, now_ns)
```

**Async iterator surface** — mirror `arduino_motor.py::events:951-959`:
```python
async def tracked_subjects(self) -> AsyncIterator[TrackedSubject]:
    while True:
        ts = await self._out_queue.get()
        yield ts
```

**Read-only dashboard surface (PERC-04 dashboard contract)** — mirror `arduino_motor.py:200-208` and `obs_camera.py:444-462`:
```python
@property
def is_locked(self) -> bool:
    return self._state is _LockState.LOCKED

@property
def current_track_id(self) -> int | None:
    return self._locked_track_id

@property
def last_lock_loss_ts_ns(self) -> int | None:
    return self._last_lock_loss_ts_ns
```

**Lock acquisition WARN log shape** — copy from CONTEXT.md Specifics §3 (lines 129-130 of CONTEXT.md):
```python
self._logger.warning(
    "lock_loss",
    track_id=self._locked_track_id,
    last_seen_ts_ns=self._last_seen_ts_ns,
    age_ms=(now_ns - self._last_seen_ts_ns) / _NS_PER_MS,
)
self._logger.warning(
    "lock_reacquired",
    old_track_id=old_id,
    new_track_id=new_id,
    gap_ms=...,
)
```

---

### `pastor_tracker/perception/_kalman.py` (utility, pure transform — optional, only if `subject_tracker.py` > 300 lines)

**Primary analog:** `pastor_tracker/core/damping.py:1-77` (entire file)

**Class shape** — copy `CriticallyDampedFollower` shape (`damping.py:36-77`):
- Frozen dataclass holding the static config (process-noise variance, measurement-noise variance, initial-velocity covariance).
- `__post_init__` validates inputs (`damping.py:47-51`).
- An `initial_state(...)` factory that builds a fresh `KalmanFilter` instance per RESEARCH 04 Pitfall 2 (full reset on re-acquisition).
- A pure `step(state, measurement, dt) -> NewState` returning a new immutable record.

**filterpy 4-state CV setup** — copy verbatim from RESEARCH 04 Code Example 1 (lines 506-554). Use `Q_discrete_white_noise(dim=2, dt=dt, var=_KALMAN_PROCESS_NOISE_VAR)` + `block_diag` per RESEARCH 04 Don't-Hand-Roll table.

**Variable-dt predict** — copy from RESEARCH 04 Code Example 2 (lines 556-568): recompute `kf.F[0, 2] = dt; kf.F[1, 3] = dt` per call.

**HOLDING-state freeze** — provide a method that does NOT call `predict()`/`update()` (Pitfall 4 anti-pattern — covariance grows unbounded). Just return `kf.x_post.copy()`.

---

### `pastor_tracker/perception/__init__.py` (modify — public re-exports)

**Analog:** `pastor_tracker/io/__init__.py` (currently empty) is a weak analog; mirror the `obs_camera.py::__all__` shape (lines 104-114) but at package-init level. Re-export `PoseDetector`, `SubjectTracker`, error classes, and the `PoseEngine` Protocol.

---

### `pastor_tracker/config.py` (modify — add three fields)

**Analog:** `pastor_tracker/config.py` itself (lines 78-186) — extend the existing pattern.

**Field add pattern** — mirror existing field declarations like `obs_camera_name` (line 115) and `arduino_protocol_version` (line 98 — `Literal[2]` for type pinning):
```python
yolo_model_path: Path = Field(
    default=Path("yolo11n-pose.pt"),
    description="Path to YOLO11-pose weights; auto-downloads to ~/.cache if absent.",
)
yolo_device: Literal["auto", "cuda", "cpu"] = Field(
    default="auto",
    description=(
        "GPU device selection. 'auto' resolves to 'cuda' if available, else "
        "'cpu' (logged at start). Fail-fast WARN if 'cuda' requested + missing."
    ),
)
botsort_yaml_path: Path | None = Field(
    default=None,
    description=(
        "Override path for ultralytics botsort.yaml. None -> bundled "
        "ultralytics default. RESEARCH 04 Open Question 3."
    ),
)
```

**Cross-field validator pattern** — mirror `_max_above_min` (lines 179-186). If we want to validate that `yolo_model_path` exists when explicitly overridden, add a similar `@model_validator(mode="after")`. ASVS V5 (RESEARCH 04 Security Domain) endorses this.

---

### `pastor_tracker/core/types.py` (verify; possibly extend)

**Analog:** the file itself.

`Detection` (lines 119-148) and `TrackedSubject` (lines 151-159) already exist with the right shape. The planner should:
1. Verify `Detection` carries `mean_keypoint_confidence` (it does, line 129) for the PERC-02 0.55 floor.
2. Verify the current `Detection` has no `track_id` field — Phase 4 needs BoT-SORT id propagation. **The planner must add a `track_id: int | None = Field(ge=0, default=None)` field** (CONTEXT.md `Out of scope` § says ID persistence flows downstream). Use the existing field-decl idiom (line 154 `track_id: int = Field(ge=0)`).
3. Verify `TrackedSubject` has the `is_locked` indicator if Phase 5 needs it — currently it only carries position+velocity. CONTEXT.md Specifics §8 mentions Kalman covariance trace as a deferred decision; planner should NOT add it in v1 unless explicitly asked.

**Mutation pattern** — `Detection.model_copy(update={"track_id": ...})` (frozen-Pydantic idiom — `core/types.py:8-10` documents the contract).

---

### `tests/fixtures/pose_traces.py` (test fixture — scripted producer)

**Primary analog:** `tests/fixtures/arduino_traces.py` (lines 1-110) and `tests/fixtures/camera_traces.py` (lines 1-164)

**Module docstring + import-path note** — copy from `arduino_traces.py:1-11` adapted for pose:
```python
"""Canned Detection sequences and async helpers for perception integration tests.

Drive scripted traces via :class:`FakePoseEngine`; pre-load a list of
``list[Detection]`` per-frame outputs and hand the fake to ``SubjectTracker``
or ``PoseDetector`` via the ``PoseEngine`` Protocol injection seam.

Import path is ``tests.fixtures.pose_traces`` -- pytest ``rootdir`` is
``pastor_tracker/`` (testpaths=["tests"], packages=["src/pastor_tracker"]).
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package.
"""
```

**`FakePoseEngine` class shape** — mirror `FakeVideoSource` (`camera_traces.py:41-87`) and `FakeSerialTransport` (`arduino_transport.py:131-`):
```python
class FakePoseEngine:
    """In-memory fake PoseEngine. Tests pre-load a script of per-frame Detection lists."""

    def __init__(self, script: Iterable[list[Detection]]) -> None:
        self._script: collections.deque[list[Detection]] = collections.deque(script)
        self._closed: bool = False
        self.detect_calls: list[int] = []  # observable: timestamp_ns of each frame

    async def detect(self, frame: Frame) -> list[Detection]:
        if not self._script:
            return []
        self.detect_calls.append(frame.timestamp_ns)
        return self._script.popleft()

    async def close(self) -> None:
        self._closed = True
```

**Constant trace lists** — mirror `arduino_traces.py:21-51` (`ARDUINO_TRACE_BOOT_ONLY`, `ARDUINO_TRACE_GOLDEN`, `ARDUINO_TRACE_WATCHDOG_RESET`). Examples to script:
- `POSE_TRACE_INITIAL_LOCK` — one central-60% high-conf person at frame 0
- `POSE_TRACE_OFF_CENTER_NO_LOCK` — sole person outside central 60%
- `POSE_TRACE_LOW_CONF_REJECT` — central person with mean kp conf < 0.55
- `POSE_TRACE_OCCLUSION_3F` — locked person, 3 frames empty, then returns (PERC-07 HOLDING)
- `POSE_TRACE_LOCK_LOSS_2S` — locked person, then > 2 s of empty frames (PERC-05)
- `POSE_TRACE_TRACK_ID_PERSIST` — same physical subject across 10 frames with stable track_id

**Helper-builder pattern** — mirror `make_solid_bgr` (`camera_traces.py:90-94`) for cheap synthesis:
```python
def make_detection(
    *,
    cx: float,
    cy: float,
    track_id: int,
    conf: float = 0.9,
    timestamp_ns: int = 0,
) -> Detection:
    return Detection(
        subject_center_x_normalized=cx,
        subject_center_y_normalized=cy,
        mean_keypoint_confidence=conf,
        bbox_x1_normalized=max(0.0, cx - 0.05),
        bbox_y1_normalized=max(0.0, cy - 0.1),
        bbox_x2_normalized=min(1.0, cx + 0.05),
        bbox_y2_normalized=min(1.0, cy + 0.1),
        timestamp_ns=timestamp_ns,
        # track_id added by Phase 4 type extension
    )
```

**Async lifecycle helper** — mirror `_started_motor` (`arduino_traces.py:54-69`) and `_started_camera` (`camera_traces.py:102-138`):
```python
async def _started_tracker(
    valid_config_dict: dict[str, object],
    *,
    script: list[list[Detection]] | None = None,
) -> tuple[SubjectTracker, FakePoseEngine]:
    ...
```

**Polling helpers** — mirror `wait_for_state` (`arduino_traces.py:72-95`) for `_LockState` polling:
```python
async def wait_for_lock_state(
    tracker: SubjectTracker, target: _LockState, timeout: float = 1.0,
) -> None: ...
```

**Pitfall 1 mitigation** (Frame contiguity) — RESEARCH 04 lines 432-436: any fixture that builds frames via `frame[..., ::-1]` (RGB->BGR) MUST wrap with `np.ascontiguousarray(...)` because `Frame.__post_init__` (lines 87-98 of `core/types.py`) asserts `flags["C_CONTIGUOUS"] is True`.

---

### `tests/test_pose_detector.py` (test — lifecycle + IPC roundtrip + drop-oldest)

**Primary analog:** `tests/test_obs_camera_lifecycle.py` (lines 1-150 read) and `tests/test_obs_camera_stale_drop.py`

**Imports + structure** — mirror `test_obs_camera_lifecycle.py:1-29`:
```python
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseDetector,
    PoseEngine,
    PoseEngineUnavailableError,
    _DetectorState,
)
from tests.fixtures.pose_traces import (
    FakePoseEngine,
    POSE_TRACE_INITIAL_LOCK,
    _started_detector,
    make_detection,
)
```

**Test function shape** — copy `test_start_returns_after_first_frame:31-41`, `test_start_then_stop_clean:44-50`, `test_double_start_raises:53-61`. The same try/finally with `await detector.stop()` discipline.

**Drop-oldest test pattern** — copy `test_queue_drop_oldest_when_full:127-150` shape: burst many frames, capture log, assert WARN event count.

**Real-shared-memory roundtrip test** — no analog (this is a Phase 4 first). Plan: allocate a real `SharedMemory` block, write bytes from caller, attach by name in a worker-stand-in (synchronous helper, NOT a process — keeps the test deterministic), read back, assert byte-identity.

**`PoseEngine` Protocol compliance** — copy the `runtime_checkable` assertion idiom (RESEARCH 04 Architecture Pattern 1):
```python
def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakePoseEngine(script=[]), PoseEngine)
```

---

### `tests/test_subject_tracker_lock.py` (test — state machine PERC-02/03/04/05)

**Primary analog:** `tests/test_arduino_motor_handshake.py` (lines 1-56 read) for state-transition assertion shape; `tests/test_arduino_motor_recovery.py` for multi-step state walks.

**Test shape (state-transition)** — copy `test_handshake_succeeds_with_full_preamble:17-30`:
```python
async def test_initial_lock_central_60pct_highest_conf(
    valid_config_dict: dict[str, object],
) -> None:
    """PERC-04: highest-conf central-60% person locks at t=0."""
    script = [[
        make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000),
        make_detection(cx=0.1, cy=0.5, track_id=2, conf=0.95, timestamp_ns=1_000),  # off-center
    ]]
    tracker, _fake = await _started_tracker(valid_config_dict, script=script)
    try:
        ts = await asyncio.wait_for(anext(tracker.tracked_subjects()), 1.0)
        assert ts.track_id == 1
        assert tracker.is_locked is True
    finally:
        await tracker.stop()
```

**Lock-loss + WARN log assertion** — copy the `structlog.testing.capture_logs()` idiom from `test_obs_camera_lifecycle.py:150` (line 150 of the read excerpt) and `test_arduino_motor_recovery.py`-style multi-step poll using `wait_for_lock_state`.

**Centroid weighted-mean property test** — pure-math test, drive the function directly. Copy hypothesis idiom from `test_damping.py:32-68`:
```python
@given(...)
def test_centroid_weighted_mean_property(...) -> None: ...
```

---

### `tests/test_subject_tracker_kalman.py` (test — property tests on real filterpy)

**Primary analog:** `tests/test_damping.py` (entire file, lines 1-105) — this is the canonical "test real math" example.

**Header constant block** — mirror `test_damping.py:17-29`:
```python
KALMAN_INIT_VEL_COV: float = 1.0
SMOOTHED_VS_RAW_TOLERANCE: float = 0.5  # smoothed must be within 50% of truth
HYPOTHESIS_MAX_EXAMPLES: int = 40
DT_30HZ: float = 1.0 / 30.0
LINEAR_TRAJECTORY_HORIZON: int = 60
```

**Property test shape** — copy `test_critically_damped_no_overshoot:42-68`:
```python
@given(
    vx_truth=st.floats(min_value=-0.3, max_value=0.3, allow_nan=False),
    noise_sigma=st.floats(min_value=0.001, max_value=0.05, allow_nan=False),
)
@settings(deadline=None, max_examples=HYPOTHESIS_MAX_EXAMPLES)
def test_smoothed_velocity_beats_frame_diff(vx_truth: float, noise_sigma: float) -> None:
    """Per CLAUDE.md 'test real implementations': drive real KalmanFilter."""
    ...
```

**Init-state inspection test** — assert `kf.F`, `kf.H`, `kf.Q.shape == (4, 4)`, `kf.R.shape == (2, 2)`, `kf.P[2,2] == _KALMAN_INITIAL_VEL_COV`. Mirror `test_zero_or_negative_time_constant_rejected:71-75` discipline.

**Variable-dt test** — feed `dt = 1/30, 1/60, 1/30, 1/15` sequence; assert filter state remains finite and tracks. No mock — real `KalmanFilter`.

**Re-acquisition reset test** — instantiate filter, run 5 updates, call `reset_for_new_track(...)`, assert `kf.x` and `kf.P` are at initial-state values (no carried velocity — Pitfall 2).

---

### `tests/test_subject_tracker_hold.py` (test — PERC-07 HOLDING branch)

**Primary analog:** `tests/test_arduino_motor_recovery.py` (state-walk: RUNNING -> RECOVERING -> RUNNING).

**Test shape**:
```python
async def test_three_misses_holds_posterior(...) -> None:
    """PERC-07: 3 consecutive empty frames -> HOLDING + WARN."""
    script = [
        [make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000)],
        [],  # miss 1
        [],  # miss 2
        [],  # miss 3 -> HOLDING
    ]
    tracker, _fake = await _started_tracker(valid_config_dict, script=script)
    ...
    with structlog.testing.capture_logs() as caplog:
        # consume 4 ticks
        ...
    assert any(e["event"] == "lock_holding" for e in caplog)
    assert tracker._state is _LockState.HOLDING  # access for test only
```

**Posterior-freeze assertion** — drive 5 misses; assert the position emitted on miss 3 == miss 4 == miss 5 (no drift). This validates RESEARCH 04 Pitfall 4 anti-pattern is avoided.

---

### `tests/test_perception_e2e.py` (test — end-to-end)

**Primary analog:** `tests/test_obs_camera_lifecycle.py::test_frames_yields_in_order:99-111`

**Shape** — chain a real `PoseDetector` (with `FakePoseEngine`) into a real `SubjectTracker` and assert the output stream. Verify integration of the two modules at the orchestrator level (Phase 6 will own the full pipeline; this test validates the seam Phase 4 ships).

---

## Shared Patterns

### Authentication / Authorization
**N/A** — Phase 4 has no auth surface (RESEARCH 04 Security Domain table: V2/V3/V4 = no).

### Error Handling
**Source:** `pastor_tracker/io/arduino_motor.py:121-154` (`ArduinoError` root + 5 typed subclasses) and `obs_camera.py:122-160` (`CameraError` root + 3 typed subclasses)
**Apply to:** `pose_detector.py` (`PerceptionError` root + 2-3 typed subclasses: `PoseEngineUnavailableError`, `LockTimeoutError` if needed)
**Pattern excerpt** (from `arduino_motor.py:137-143`):
```python
class FirmwareErrorReceived(ArduinoError):
    def __init__(self, code: ErrorCode | int, message: str) -> None:
        super().__init__(f"firmware ERROR:{int(code)} -- {message}")
        self.code: ErrorCode | int = code
        self.message: str = message
```
Apply: structured payload, super().__init__ formatted message, typed attributes for triage.

**Latched-error gate pattern** — `arduino_motor.py:_raise_if_latched:795-805`. Apply to `SubjectTracker.consume()` and any `PoseDetector` public surface that should surface a fault to the caller deterministically.

**Translator-catch pattern** (cross-process / cross-thread boundary):
```python
except Exception as exc:  # noqa: BLE001 -- documented translator
    # ... translate to typed error and re-raise OR latch
```
Apply: every `executor.submit` boundary in `pose_detector.py`.

### Validation
**Source:** `core/types.py:68-110` (`Frame.__post_init__`) and `core/types.py:136-148` (`Detection._bbox_well_ordered`)
**Apply to:** any new types under `perception/`. CONTEXT.md `Decisions` line 50 explicitly calls out that perception consumes already-validated `Frame` and skips re-validation.

### Logging
**Source:** `arduino_motor.py:194` and `obs_camera.py:438`
**Apply to:** every new module. Pattern:
```python
self._logger = structlog.get_logger(module="<module_name>")
```
Then `self._logger.warning("event_name", **structured_fields)` — never f-strings inside the message; always structured kwargs (RESEARCH 04 Project Constraints + ruff `T20`).

**Specific event names** (from CONTEXT.md Specifics §3 + RESEARCH 04):
- `inference_drop_oldest` — drop at ingress
- `pose_engine_warmup_complete` — Pitfall 10
- `lock_acquired` / `lock_reacquired` / `lock_loss` — state machine
- `lock_holding` — PERC-07
- `low_conf_detection_rejected` — PERC-02
- `cuda_requested_but_unavailable` — Config validator

### Async-iterator surface contract
**Source:** `arduino_motor.py::events:951-959` and `obs_camera.py::frames:976-1045`
**Apply to:** `PoseDetector.detections()` and `SubjectTracker.tracked_subjects()` — both MUST be `AsyncIterator[T]`-typed `async def` generators (CONTEXT.md `In scope` line 21 explicit: "mirrors Phase 2 motor.events() / Phase 3 camera.frames()").

### Single-shot lifecycle pattern
**Source:** `obs_camera.py:481-484` and `arduino_motor.py:216-219`
**Apply to:** `PoseDetector.start()` and `SubjectTracker.start()`:
```python
if self._state is not _<StateEnum>.DISCONNECTED:
    raise <ErrorRoot>(f"start() called twice (state={self._state.value})")
```

### Bounded queue + drop-oldest
**Source:** `arduino_motor.py::_enqueue:473-489` and `obs_camera.py::_enqueue_frame:732-749`
**Apply to:** `SubjectTracker._out_queue` if buffering is needed (CONTEXT.md Discretion line 88: "Internal queue size for the inference pool (suggested 1–4)"). For `PoseDetector` the in-flight Future replaces a queue (see Open Question 1 recommendation).

### Test-fixture import-path note
**Source:** `arduino_traces.py:6-11` and `camera_traces.py:8-13`
**Apply to:** `pose_traces.py` — copy the `pytest rootdir` clarification verbatim (test tree is not a sub-package).

---

## No Analog Found

No files in this phase lack an analog — all 13 file roles map cleanly to existing Phase 1/2/3 code. The `_pose_worker.py` shape is novel within the project (no existing process-pool worker), but RESEARCH 04 Pattern 2 provides the canonical Python-stdlib idiom verbatim.

---

## Metadata

**Analog search scope:** `pastor_tracker/src/pastor_tracker/{io,core}/` and `pastor_tracker/tests/{,fixtures}/` — full Phase 1/2/3 surface.
**Files scanned:** 26 (8 source + 18 test/fixture)
**Strong-match analogs read in detail:** 6 (`obs_camera.py`, `arduino_motor.py`, `core/damping.py`, `core/types.py`, `arduino_traces.py`, `camera_traces.py`); 3 partial (`arduino_transport.py`, `test_damping.py`, `test_obs_camera_lifecycle.py`).
**Pattern extraction date:** 2026-05-05
