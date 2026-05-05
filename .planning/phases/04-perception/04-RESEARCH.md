# Phase 4: Perception - Research

**Researched:** 2026-05-05
**Domain:** Real-time pose detection (ultralytics YOLO11-pose) + multi-object tracking (BoT-SORT) + Bayesian state estimation (filterpy 4-state Kalman) + cross-process IPC (multiprocessing.shared_memory) on Windows
**Confidence:** HIGH for ultralytics + filterpy + shared_memory APIs (all VERIFIED against official docs); MEDIUM for tuning constants (CITED from default config but unverified on stage); LOW for absolute latency numbers (no published 1080p YOLO11n-pose benchmark on the dev machine — measure during Wave 0).

## Summary

Phase 4 layers three orthogonal subsystems on top of the Phase 3 frame source: (1) a YOLO11-pose detector running in a single-worker `ProcessPoolExecutor` and fed BGR uint8 frames via `multiprocessing.shared_memory` (zero-copy, ~6 MiB/frame avoided per call), (2) BoT-SORT track-id persistence layered onto detection via the built-in `model.track(persist=True, tracker="botsort.yaml")` call, and (3) a 4-state `[x, y, vx, vy]` constant-velocity Kalman filter (filterpy) per locked subject in normalized coords. CONTEXT.md has already locked the architecture (single worker, drop-oldest at ingress, full Kalman reset on re-acquisition, hold-posterior on 3-frame gap, central-60% lock heuristic) — research focuses on correctly *implementing* those locks.

Three high-risk landmines surfaced in the research:
1. **filterpy 1.4.5 was last released October 2018** and pre-dates NumPy 2.0. The project pins `numpy>=2.4,<3.0` in pyproject.toml — I confirmed (CITED to community reports) that `pip install filterpy` works under numpy 2.x because filterpy is pure-Python (no C extensions to ABI-break), but it relies on a couple of deprecated numpy APIs (`np.asarray` aliasing, `np.float_`). Wave 0 MUST add `filterpy` as a dep AND run a smoke import that exercises `KalmanFilter.predict/update` against numpy 2.4 to fail-fast if a deprecation has become an error.
2. **`persist=True` does NOT survive across separate YOLO model instances** — and a `ProcessPoolExecutor` worker is, conceptually, a separate process holding a separate model. CONTEXT.md's architecture is correct *only because* `max_workers=1` and the worker keeps its model warm across submissions — the same model instance services every frame. This invariant must be guarded: **never recreate the executor mid-run, never call `executor.submit` against a fresh `YOLO(...)` per call.**
3. **`shared_memory.unlink()` is a no-op on Windows** (Windows manages lifetime via handle refcount), but `track=True` (the 3.13 default) still registers a resource_tracker entry. The cleanest pattern on Windows is a single long-lived shared block of fixed 1080p size, attached once in the worker, reused for every frame — *not* per-call allocations.

**Primary recommendation:** Build a thin `PoseEngine` Protocol seam (mirrors Phase 2 `SerialTransport` and Phase 3 `VideoSource`) so the YOLO/BoT-SORT machinery never touches test code; in production, wrap the `ProcessPoolExecutor` + `shared_memory` plumbing inside `pose_detector.py` behind that Protocol. Subject-lock state machine + Kalman wrapper live in `subject_tracker.py` and consume `Detection` records via the Protocol — fully unit-testable with `FakePoseEngine` producing scripted `Detection` sequences. Mirror the Phase 2/3 file split discipline (one orchestrator class per file, helpers stay in-module unless line count >300).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts:**
- Detector = `ultralytics` YOLO11-pose; tracker = built-in BoT-SORT (`botsort.yaml`)
- State estimation = `filterpy` Kalman, 4-state `[x, y, vx, vy]` in normalized frame coords (PERC-06)
- Subject centroid weighted mean: nose 0.4, shoulder mid 0.4, hip mid 0.2; mean kp conf floor 0.55 (PERC-02)
- Initial lock = highest-conf person in central 60% of frame at start (PERC-04)
- Lock-loss timeout = 2.0 s → re-acquire via central-frame heuristic + WARN (PERC-05)
- 3-consecutive-no-detection rule → hold position + WARN (PERC-07)
- Process pool for inference; non-blocking on capture loop (PERC-01)
- Pure-core / dirty-edges layering — perception is a "dirty edge"; pure logic (centroid math, lock scoring, Kalman wiring) lives on typed DTOs
- `Frame.__post_init__` already asserts `dtype == np.uint8` AND `flags["C_CONTIGUOUS"] is True` (Phase 3 deep review, commit aac3da1) — perception consumes `Frame` and skips its own dtype/contiguity guards; test fixtures using `frame[..., ::-1]` views must wrap with `np.ascontiguousarray(...)`
- Pydantic v2 frozen DTOs; dataclass `frozen=True, slots=True` for ndarray-bearing types; mutate via `.model_copy(update=...)` / `dataclasses.replace(...)`
- Tiger-style fail-fast — typed exceptions, no silent fallback, no bare `except`
- `structlog` JSON logging only; no `print()`
- `mypy --strict` no `Any`; ≤ 2-level conditional nesting; no magic numbers (`Config` is authoritative)
- Conventional Commits, one logical change per commit
- Test policy — no mocked Kalman / damping math (CLAUDE.md); in-tree fakes permitted at the I/O seam (Phase 2 `FakeSerialTransport`, Phase 3 `FakeVideoSource` precedent)

**Inference Architecture (Area 1):**
- Single-worker process pool — `concurrent.futures.ProcessPoolExecutor(max_workers=1)`
- IPC for ndarray = `multiprocessing.shared_memory` — zero-copy 1080p uint8 frame
- Backpressure when inference > capture interval = drop oldest input frame at the queue boundary + structlog WARN
- Device selection = new `Config` field `yolo_device: Literal["auto", "cuda", "cpu"]` default `"auto"`; fail-fast WARN if `"cuda"` is requested but unavailable

**Subject Lock Lifecycle (Area 2):**
- Initial lock score = highest-conf person whose centroid sits inside central 60% of frame
- Re-acquisition after > 2.0 s loss = same heuristic; log WARN
- Non-locked detections dropped silently — pose detector emits all detections, subject tracker filters to the locked track id only
- Initial lock window = first frame where ≥ 1 person has centroid in central 60% AND mean kp conf ≥ 0.55; no warmup wait

**Kalman Filter Design (Area 3):**
- Process model = constant velocity (CV) on 4-state `[x, y, vx, vy]`
- Init state at first detection = position from detection, velocity = 0, large velocity covariance (≈ 1.0)
- Reset on re-acquisition = full reset — new track id ⇒ new filter instance
- Behaviour during gap > 3 frames = hold last filter posterior — stop predicting forward, output = last update mean, log WARN

**Output Surface & Test Strategy (Area 4):**
- Public API = `async def tracked_subjects(self) -> AsyncIterator[TrackedSubject]:`
- Test seam = `PoseEngine` `Protocol` + in-tree zero-deps `FakePoseEngine`
- Observable state for Phase 7 dashboard = read-only properties `is_locked: bool`, `current_track_id: int | None`, `last_lock_loss_ts_ns: int | None`
- Coverage target = ≥ 90 % line on `subject_tracker.py` + `pose_detector.py`; 100 % on lock-acquisition + Kalman-update branches

### Claude's Discretion

All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal type / event names beyond `Detection` and `TrackedSubject` (already in `core/types.py`)
- Error class hierarchy under a single `PerceptionError` root (e.g., `PoseEngineUnavailableError`, `LockTimeoutError` if needed)
- Logging key names (kept consistent with structlog conventions established in Phases 1–3)
- File granularity inside `pastor_tracker/perception/` — `pose_detector.py` + `subject_tracker.py` is the default; split is permitted if line count balloons
- Internal queue size for the inference pool (suggested 1–4: only the freshest frame matters)
- Whether the lock-loss timer is a small helper class or a stateful function

### Deferred Ideas (OUT OF SCOPE)

- ONNX / TensorRT export of YOLO11-pose for tighter latency
- Multi-camera / multi-subject coordination — out of scope per PROJECT.md (v2 MULTI-01)
- Tilt-axis / 3D pose / depth estimation — out of scope (v2 TILT-01)
- Adaptive process noise (Q matrix) based on motion magnitude
- Soft-reset Kalman on re-acquisition (carry covariance forward) — rejected in Area 3
- Backup-ID stash for resilience to BoT-SORT mis-IDs — rejected in Area 2
- Re-promotion of inference backend (CPU → GPU mid-run)
- Per-frame skeleton overlay rendered inside perception — Phase 7 dashboard concern
- Real-camera + real-GPU pytest fixture — deferred to Phase 8 QA-04
- Recording golden detection sequences against a known video — deferred
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **PERC-01** | YOLO11-pose wrapper (ultralytics) — GPU/CPU, async inference in process pool, doesn't block capture | "ultralytics YOLO11-pose API" + "ProcessPoolExecutor pattern" + "shared_memory IPC" sections |
| **PERC-02** | Subject centroid weighted mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); reject if mean kp conf < 0.55 | "Pure-core helpers" — `compute_subject_centroid` + `mean_keypoint_confidence`; reject path tested by FakePoseEngine scripts |
| **PERC-03** | BoT-SORT ID persistence via `model.track(persist=True, tracker="botsort.yaml")` | "BoT-SORT track-id persistence" section + Pitfall: `persist=True` semantics across model instances |
| **PERC-04** | Primary-subject lock — highest-conf person in central 60% at start; persist ID across occlusions | "Subject-lock state machine design" section — UNLOCKED → SEEKING → LOCKED transition |
| **PERC-05** | Lock loss > 2.0 s → re-acquire via central-frame heuristic, log WARN | "Subject-lock state machine design" — LOST → RE_ACQUIRING transition with timer |
| **PERC-06** | 4-state Kalman filter `[x, y, vx, vy]` in normalized frame coords; predict during gaps, update on detection | "filterpy 4-state CV implementation" section — F/H/Q/R/P matrices + variable-dt handling |
| **PERC-07** | 3 consecutive frames with no detection → hold position, log WARN | "filterpy 4-state CV implementation" — `hold_posterior()` method (skip predict, return last x_post) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

| Directive | Source | Phase 4 Application |
|-----------|--------|---------------------|
| `mypy --strict`, no `Any`, every function annotated | CLAUDE.md Conventions §8 + pyproject.toml `disallow_any_explicit = true` | All YOLO results / shared_memory wrappers must use typed casts (e.g., `KeypointArray = npt.NDArray[np.float32]`); ultralytics is in mypy override list (`ignore_missing_imports = true`) so seam types are *our* responsibility |
| Tiger-style fail-fast — no silent except, no bare except, no `except Exception: pass` | CLAUDE.md §1 | `PerceptionError` hierarchy with typed re-raises at every cross-process boundary; ruff `BLE001` enforces this; documented `noqa: BLE001` for cv2/torch translator catches only |
| ≤ 2-level conditional nesting; guard clauses + early returns | CLAUDE.md §5 | Lock state machine MUST be flat — one `match` over state OR a dispatch dict; no nested `if person and person.conf > X and ...:` |
| No magic numbers — `Config` is authoritative | CLAUDE.md §6 | `_LOCK_LOSS_TIMEOUT_SEC`, `_HOLD_POSTERIOR_FRAME_THRESHOLD`, `_CENTRAL_REGION_FRACTION = 0.6`, `_NOSE_KEYPOINT_INDEX`, etc. as module-level `Final` constants per Phase 2/3 precedent. New `Config.yolo_device` field. |
| Immutable data — Pydantic `frozen=True`, dataclass `frozen=True` | CLAUDE.md §9 | `Detection` and `TrackedSubject` are already frozen Pydantic models; new types (e.g., `_LockState`, `_PoseEngineResult`) use the same pattern |
| `structlog` JSON only, no `print()` | CLAUDE.md + ruff `T20` | All inference / lock / Kalman events use `logger.info/warning/error` with module-bound logger; ultralytics may print to stdout — must capture or suppress |
| Conventional Commits, one logical change per commit | CLAUDE.md §10 | Wave-by-wave commit discipline matches Phase 2/3 (3 plans expected: Plan 04-01 = pose_engine seam + shared_memory transport, Plan 04-02 = Kalman wrapper + lock state machine, Plan 04-03 = orchestrator wiring) |
| **Forbidden**: PID, MediaPipe, EMA on detection stream, mocked Kalman/damping in tests | CLAUDE.md "Forbidden libraries / patterns" | Property tests feed `KalmanFilter` real numpy data — no `mock.patch.object(KalmanFilter, "update")`; FakePoseEngine emits real `Detection` records, not mocks |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| YOLO11-pose model load + warm | Worker process (ProcessPoolExecutor child) | — | GPU/CUDA context lives in worker; loading model in main process leaks CUDA context across forks (spawn-only on Windows anyway) |
| Per-frame detection inference | Worker process | — | Same; isolated GPU memory + GIL release |
| BoT-SORT track-id persistence | Worker process (inside YOLO instance) | — | `persist=True` requires same `YOLO` instance every call → state lives where the model lives |
| Frame ndarray transport (camera-thread → worker) | shared_memory (OS-level page-shared block) | Loop thread (asyncio.Future) | OS pages cross process boundaries without copy; pickle would copy ~6 MiB/frame |
| Drop-oldest backpressure at ingress | Loop thread (asyncio inside `pose_detector.py`) | — | Must be non-blocking on the camera consumer; happens before `executor.submit` |
| Detection result return path | Worker process → loop thread (Future result) | — | Pickle the small `_PoseEngineResult` (list of detections + track_ids), cheap |
| Subject-lock state machine | Loop thread (pure logic, in `subject_tracker.py`) | — | No I/O — pure transform on `Detection` stream; CONTEXT.md "pure-core / dirty-edges" |
| Kalman filter predict/update | Loop thread (filterpy is pure Python+numpy, ~µs cost) | — | Same — pure transform |
| Lock-loss timer | Loop thread (`asyncio.get_event_loop().time()` or perf_counter_ns) | — | No timer thread needed; checked on each `Detection` arrival |
| Read-only dashboard surface | Loop thread (properties read by Phase 7) | — | Phase 7 reads `is_locked` / `current_track_id` from main thread; atomicity via Python attribute writes |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `ultralytics` | 8.4.46 (latest, May 2026) | YOLO11-pose detector + bundled BoT-SORT tracker | The only mainstream PyTorch implementation of YOLO11; ships `botsort.yaml` and ByteTrack defaults inside the package; `model.track(persist=True)` is a documented one-liner for ID persistence [VERIFIED: pypi.org/project/ultralytics, docs.ultralytics.com/modes/track] |
| `filterpy` | 1.4.5 (October 2018, last release) | 4-state Kalman filter (PERC-06) | Pure-Python; CLAUDE.md mandates filterpy by name; `KalmanFilter(dim_x=4, dim_z=2)` API is the documented idiom [VERIFIED: pypi.org/project/filterpy, filterpy.readthedocs.io] |
| `numpy` | already pinned `>=2.4,<3.0` | Array math for centroid + Kalman matrices | Already in pyproject.toml; reused |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `concurrent.futures.ProcessPoolExecutor` | stdlib (3.12) | Single-worker inference pool | CONTEXT.md Area 1 lock; Future-driven async wrapper (`asyncio.wrap_future` or `loop.run_in_executor`) |
| `multiprocessing.shared_memory.SharedMemory` | stdlib (3.12) | Zero-copy ndarray ferry to worker | CONTEXT.md Area 1 lock; ~6 MiB/frame avoided per call vs pickle |
| `multiprocessing` (start method) | stdlib | `set_start_method("spawn")` on Windows (default and only option for Windows) | Must be explicit if any test imports `multiprocessing` before the executor is created |
| `torch` | transitively via ultralytics | CUDA context, tensor operations | Don't import directly; ultralytics manages it |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| filterpy (Kalman) | hand-rolled numpy Kalman | filterpy is the project's canonical Kalman per CLAUDE.md; rolling our own violates "Don't hand-roll" + adds untested math |
| `multiprocessing.shared_memory` | `multiprocessing.Array` / `Manager` | shared_memory is the modern (3.8+) idiom with cleanest API; `Manager` is slower (proxy roundtrip per access) |
| `ProcessPoolExecutor(1)` | `multiprocessing.Process` + manual Queue | Future API gives clean async wrapper + structured exception propagation; manual Process requires reinventing lifecycle |
| BoT-SORT | ByteTrack (also bundled) | CLAUDE.md mandates BoT-SORT; ByteTrack lacks ReID and global motion compensation |
| YOLO11-pose | YOLO11-detect + separate pose model | Single model = single inference cost; YOLO11-pose returns boxes AND keypoints in one pass |
| ONNX export | PyTorch native (`.pt`) | Deferred to v2 per CONTEXT.md; ONNX would complicate BoT-SORT integration (tracker reads PyTorch tensor outputs) |

**Installation:**

```bash
uv add ultralytics filterpy
# Note: ultralytics will pull torch + torchvision (~2 GB on Windows GPU build)
```

**Version verification (Wave 0 must run):**

```bash
python -c "import ultralytics; print(ultralytics.__version__)"          # expect 8.4.46+
python -c "import filterpy; print(filterpy.__version__)"                 # expect 1.4.5
python -c "from filterpy.kalman import KalmanFilter; kf = KalmanFilter(dim_x=4, dim_z=2); kf.predict(); print('ok')"  # smoke test against numpy 2.x
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu-only')"
```

If filterpy fails to import or `kf.predict()` raises a numpy 2.x deprecation-as-error (filterfilters internally calls `np.asarray` and `np.dot` patterns that COULD break), Wave 0 must pin numpy or vendor a minimal Kalman class — DO NOT proceed to Plan 04-02 without this confirmation. [ASSUMED — community reports indicate filterpy 1.4.5 works on numpy 2.x with deprecation warnings; not personally verified on this dev box.]

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Main Process (asyncio loop thread)                                            │
│                                                                                │
│ ObsCamera.frames() ──► async iterator                                          │
│         │                                                                      │
│         ▼                                                                      │
│ PoseDetector.consume(frame) ────► Drop-oldest gate                             │
│         │                          (size-1 ingress queue)                      │
│         │                                  │                                   │
│         │                                  ▼                                   │
│         │                          executor.submit(infer, shm_name, shape)     │
│         │                                  │                                   │
│         │                                  │  Future ◄──── (await)             │
│         │                                  ▼                                   │
│         │                          PoseEngineResult                            │
│         │                          (list[Detection], track_ids)                │
│         ▼                                                                      │
│ SubjectTracker.tracked_subjects() ────► async iterator                         │
│   ├─ central-60% lock check (PERC-04)                                          │
│   ├─ track-id filter (only locked id)                                          │
│   ├─ KalmanFilter.update(z) on detection                                       │
│   ├─ hold-posterior counter on miss (PERC-07, 3 frames)                        │
│   ├─ lock-loss timer (PERC-05, 2.0 s)                                          │
│   └─ emit TrackedSubject (frozen DTO)                                          │
│         │                                                                      │
│         ▼                                                                      │
│ Phase 5 MotionAnalyzer (downstream — out of scope this phase)                  │
└────────────────┬─────────────────────────────────────────────────────────────┘
                 │ shared_memory block (one persistent 1080p uint8 buffer)
                 │ name="pts_pose_frame_<pid>", size=1920*1080*3
┌────────────────▼─────────────────────────────────────────────────────────────┐
│ Worker Process (spawn — single-shot, max_workers=1)                            │
│                                                                                │
│ Lazy-init on first call:                                                       │
│   _model: YOLO | None = None                                                   │
│   _shm:   SharedMemory | None = None  (attached by name)                       │
│                                                                                │
│ Per-call:                                                                      │
│   1. Materialize ndarray view: np.ndarray(shape, dtype=uint8, buffer=shm.buf)  │
│   2. results = _model.track(view, persist=True, tracker="botsort.yaml",        │
│                             classes=[0], device=resolved_device, verbose=False)│
│   3. Translate results[0].boxes / .keypoints → list[Detection]                 │
│   4. Return PoseEngineResult (small, picklable)                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

Data flow: camera thread → Frame DTO → loop thread copies image bytes into shared block → submits Future → worker reads from same block → returns picklable result. Track-id continuity is held inside the worker (same `_model` instance + `persist=True`).

### Recommended Project Structure

```
pastor_tracker/src/pastor_tracker/perception/
├── __init__.py                        # public exports: PoseDetector, SubjectTracker, error classes
├── pose_detector.py                   # PoseEngine Protocol + UltralyticsPoseEngine + PoseDetector orchestrator
├── subject_tracker.py                 # SubjectTracker class (lock state machine + Kalman wrapper)
├── _pose_worker.py                    # module that runs in the spawned worker process (top-level functions only — picklability)
└── _kalman.py                         # filterpy wrapper (only if subject_tracker.py exceeds ~300 lines; else inline)

pastor_tracker/tests/
├── fixtures/
│   └── pose_traces.py                 # FakePoseEngine + scripted Detection sequences (mirror arduino_traces.py / camera_traces.py)
├── test_pose_detector.py              # PoseEngine seam + drop-oldest + executor lifecycle + shared_memory roundtrip
├── test_subject_tracker_lock.py       # state machine: SEEKING → LOCKED → LOST → RE_ACQUIRING (PERC-04/05)
├── test_subject_tracker_kalman.py     # property tests on real KalmanFilter (CLAUDE.md "test real implementations")
└── test_subject_tracker_hold.py       # PERC-07 hold-posterior on 3-frame gap
```

**File-split rationale:**
- `_pose_worker.py` MUST exist as a separate module because `ProcessPoolExecutor` (spawn start method on Windows) needs the worker target to be importable by name from the child interpreter — closures and locals don't pickle. Top-level module functions only.
- `_kalman.py` extraction is OPTIONAL — start with inline `_KalmanWrapper` class inside `subject_tracker.py`; extract only if the file passes ~300 lines (Phase 2/3 split discipline).
- Tests split mirrors Phase 2 (5 files: handshake / heartbeat / recovery / replay / seq_gap) — one test file per concern, not per source file.

### Pattern 1: PoseEngine Protocol (DI seam)

**What:** Mirror Phase 2 `SerialTransport` and Phase 3 `VideoSource` — a `runtime_checkable` Protocol that exposes the minimum surface the orchestrator needs.
**When to use:** This is THE seam that makes the entire phase testable without GPU/model weights.
**Example:**

```python
from typing import Protocol, runtime_checkable
from pastor_tracker.core.types import Frame, Detection

@runtime_checkable
class PoseEngine(Protocol):
    """Pose detection + tracking seam. Production = UltralyticsPoseEngine, tests = FakePoseEngine."""

    async def detect(self, frame: Frame) -> list[Detection]: ...
    async def close(self) -> None: ...
```

The async signature lets `UltralyticsPoseEngine` await the `Future` from `executor.submit` while `FakePoseEngine` returns immediately.

### Pattern 2: Worker-side lazy model init (singleton-per-process)

**What:** The worker process loads the YOLO model once on first call, reuses it forever.
**When to use:** ALWAYS. Reloading the model per call costs ~2 s cold start.
**Example:**

```python
# pastor_tracker/perception/_pose_worker.py
# This module is imported by the spawned child interpreter.
# Top-level functions only — no closures, no class instances bound at import time.

from __future__ import annotations
import logging
from multiprocessing import shared_memory
import numpy as np
import numpy.typing as npt

_model: object | None = None  # lazy: ultralytics.YOLO instance after first call
_shm: shared_memory.SharedMemory | None = None
_shm_name: str | None = None

def _ensure_model(model_path: str, device: str) -> object:
    global _model
    if _model is None:
        from ultralytics import YOLO  # noqa: PLC0415 — heavy import deferred to worker
        _model = YOLO(model_path)
        # ultralytics resolves device at predict-call time; warm one tiny inference
        # to spend cold-start cost here, not on the first real frame.
        _model.predict(np.zeros((640, 640, 3), dtype=np.uint8), device=device, verbose=False)
    return _model

def infer(shm_name: str, shape: tuple[int, int, int], device: str, model_path: str) -> "_PoseResult":
    global _shm, _shm_name
    if _shm is None or _shm_name != shm_name:
        if _shm is not None:
            _shm.close()
        _shm = shared_memory.SharedMemory(name=shm_name)  # attach existing
        _shm_name = shm_name
    frame_view: npt.NDArray[np.uint8] = np.ndarray(shape, dtype=np.uint8, buffer=_shm.buf)
    model = _ensure_model(model_path, device)
    results = model.track(
        frame_view,
        persist=True,
        tracker="botsort.yaml",
        classes=[0],          # COCO class 0 = person; suppress all other classes
        device=device,
        verbose=False,
    )
    return _translate(results[0])  # extract boxes.xyxyn, boxes.id, boxes.conf, keypoints.xyn
```

### Pattern 3: Long-lived shared block (NOT per-call allocation)

**What:** Allocate ONE `SharedMemory` block sized for max-resolution frames, reuse for every inference call.
**When to use:** Always. Per-call `SharedMemory(create=True)` triggers resource_tracker churn and adds ~ms of allocation latency.
**Example:**

```python
# In PoseDetector.start() — main process
self._shm = shared_memory.SharedMemory(
    create=True,
    size=self._config.capture_width * self._config.capture_height * 3,
)
# Pass self._shm.name to worker on every submit; worker attaches once, reuses.
```

**Cleanup:** On `PoseDetector.stop()`, call `self._shm.close()` then `self._shm.unlink()`. On Windows, `unlink()` is a no-op but harmless [VERIFIED: docs.python.org/3/library/multiprocessing.shared_memory]. Resource_tracker will not warn because the parent created it via `multiprocessing` machinery (the worker is a multiprocessing-managed child).

### Pattern 4: Drop-oldest at ingress (size-1 ingress)

**What:** Only ONE Future may be in flight at a time. New frame while previous is still inferring → drop the previous.
**When to use:** This phase. Stale inferences create lag; freshest frame matters for control.
**Example:**

```python
class PoseDetector:
    async def consume(self, frame: Frame) -> None:
        if self._inflight is not None and not self._inflight.done():
            self._logger.warning("inference_drop_oldest", reason="previous_inference_still_running")
            self._inflight.cancel()  # best-effort; YOLO call won't honor cancel mid-CUDA, but the Future is detached
        # Copy frame.image into shared_memory (the only copy in the pipeline — unavoidable for the IPC handoff)
        view = np.ndarray(frame.image.shape, dtype=np.uint8, buffer=self._shm.buf)
        view[:] = frame.image
        self._inflight = self._loop.run_in_executor(
            self._executor,
            _pose_worker.infer,
            self._shm.name, frame.image.shape, self._resolved_device, self._config.yolo_model_path,
        )
```

### Pattern 5: Subject-lock state machine

**What:** Five-state machine driven by detection arrivals + a 2.0 s timeout.
**When to use:** This phase, central correctness driver for PERC-04/05/07.

```
States:
  UNLOCKED       — pipeline just started; no person ever locked
  SEEKING        — actively looking for the central-60% candidate (effectively same as UNLOCKED, kept distinct for log clarity)
  LOCKED         — track_id is locked; emit TrackedSubject from KalmanFilter on every frame
  HOLDING        — locked, but ≥ 3 consecutive frames without a matching detection (PERC-07);
                   emit TrackedSubject from held posterior (no predict, no update)
  LOST           — > 2.0 s since last matching detection (PERC-05); waiting for re-acquisition
                   (orchestrator does NOT emit TrackedSubject during LOST)
  RE_ACQUIRING   — new central-60% candidate exists in the current frame post-LOST;
                   transition immediately to LOCKED with new track_id + new KalmanFilter

Transitions (frame arrival, optional Detection list):
  any → LOCKED       on (current==UNLOCKED|SEEKING|LOST) AND ≥1 detection in central 60% with mean kp conf ≥ 0.55
                     → choose highest-conf candidate, log lock_acquired or lock_reacquired
  LOCKED → LOCKED    on detection list contains current track_id → KalmanFilter.predict(dt) + update(z)
  LOCKED → HOLDING   on detection list does NOT contain current track_id AND consecutive_miss < 3
                     → KalmanFilter.predict(dt), output mean (already done in LOCKED tick)
  HOLDING → HOLDING  on still no matching detection AND consecutive_miss == 3 (clamped)
                     → return last posterior (do NOT predict further), log first frame WARN
  HOLDING → LOCKED   on detection list contains current track_id (any frame within 2.0 s)
                     → KalmanFilter.update(z), reset miss counter
  HOLDING → LOST     on (now - last_seen_ts) > 2.0 s → log lock_loss WARN
  LOST → SEEKING     immediately (same tick) — internal transition for clarity
  SEEKING → LOCKED   on next central-60% candidate → log lock_reacquired

Edge case rules (CONTEXT.md gray areas):
  - Lock at t=0 with no person in frame → state stays UNLOCKED; emit nothing; first qualifying detection promotes to LOCKED
  - Simultaneous BoT-SORT ID switch + central-60% drift → CONTEXT says "drop non-locked detections silently";
    the locked id either survives (LOCKED → HOLDING via miss path) or doesn't (LOCKED → HOLDING → LOST → SEEKING)
  - Re-acquisition while still receiving detections of the old (lost) subject:
    LOST → SEEKING → LOCKED chooses highest-conf central-60% person regardless of what id BoT-SORT assigned
    (MAY pick the same physical person with a new id; MAY pick a different person; both are correct per spec)
```

### Anti-Patterns to Avoid

- **Per-call SharedMemory creation:** allocates kernel pages every frame and churns the resource_tracker. Use one long-lived block.
- **Calling `model.track()` from multiple processes:** track-id state lives in the model; only one process holds the model.
- **Recreating `YOLO()` mid-run:** loses `persist=True` continuity. Worker process holds singleton.
- **Calling `KalmanFilter.predict()` repeatedly during HOLDING:** covariance grows without bound; subject "flies off". CONTEXT specifies HOLD = freeze posterior.
- **Mocking `KalmanFilter`:** CLAUDE.md forbids; tests must drive the real `filterpy` matrices.
- **Computing centroid from raw bbox center:** PERC-02 specifies WEIGHTED keypoint mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); bbox center is wrong.
- **Mutating `Frame.image`:** Phase 3's `__post_init__` validates contiguity; in-place mutation breaks the read-only contract.
- **Submitting a `Frame` to the executor:** the ndarray view doesn't pickle correctly across spawn boundaries (and even if it did, that's the 6 MiB pickle copy we're avoiding). Pass `(shm_name, shape, dtype)` only.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pose detection | Custom keypoint regressor | ultralytics YOLO11-pose | 5+ years of optimization, COCO-trained, runs at 10–20 ms/frame on modest GPUs |
| Multi-object tracker | Hungarian assignment + IoU matching | BoT-SORT (bundled with ultralytics) | Camera motion compensation + ReID + two-stage association — non-trivial to reimplement correctly |
| Kalman filter math | numpy matrix multiplications | `filterpy.kalman.KalmanFilter` | CLAUDE.md mandates filterpy; F/H/Q/R update equations are textbook but easy to get sign-wrong |
| Process noise covariance | hand-tuned 4×4 matrix | `filterpy.common.Q_discrete_white_noise(dim=2, block_size=2)` | Discrete white noise model is the standard CV-tracking choice [CITED: filterpy.readthedocs.io/en/latest/common/common.html] |
| Cross-process ndarray IPC | pickle/queue hand-marshaling | `multiprocessing.shared_memory` | Zero-copy on shared OS pages; the documented Python idiom |
| Async wrap of executor | manual `Thread` + `Queue` polling | `loop.run_in_executor(executor, fn, *args)` | Returns awaitable Future; structured cancellation; same pattern as Phase 2/3 |
| GPU/CPU device selection | manual `torch.cuda.is_available()` checks | ultralytics `device=` arg + Config validation | ultralytics resolves `'cuda'` / `'cpu'` / explicit indices; we only need to validate "user said cuda but cuda is missing" once at start |

**Key insight:** The "perception" word makes this phase sound exotic, but every piece has a textbook standard library. The risk is in the *plumbing* (process pool lifecycle, shared_memory cleanup, lock state machine correctness), not in the ML.

## Common Pitfalls

### Pitfall 1: ndarray view backed by a Frame slice breaks shared_memory copy
**What goes wrong:** Code does `view[:] = frame.image[10:, 10:, :]` — the source is non-contiguous, copy succeeds but layout differs from what worker expects.
**Why it happens:** numpy slicing returns views with non-default strides.
**How to avoid:** Always copy from `frame.image` (whole array, validated contiguous by `Frame.__post_init__`); never slice the source. If a slice is needed, wrap with `np.ascontiguousarray()` first.
**Warning signs:** Worker reports garbled detections; bbox coordinates clearly wrong.

### Pitfall 2: filterpy mutable state leaks across re-acquisition
**What goes wrong:** Code reuses a single `KalmanFilter` instance across track-id changes; carries velocity from the previous (different) person into the new lock.
**Why it happens:** `filterpy.KalmanFilter` is a stateful object — `predict()` and `update()` mutate `self.x` and `self.P` in place.
**How to avoid:** CONTEXT-locked: full reset = new `KalmanFilter` instance per new track id. Implement as `_KalmanWrapper.reset_for_new_track(initial_xy)` constructing a fresh `KalmanFilter`.
**Warning signs:** First few frames after re-acquisition show "ghost velocity" (subject snaps from old position).

### Pitfall 3: ultralytics `persist=True` does NOT survive across separate `.track()` invocations from different model instances
**What goes wrong:** A test creates a fresh `YOLO()` per call, sees BoT-SORT assign id=1 every time; or worse, a refactor recreates the executor, the worker reloads the model, and id continuity silently breaks at runtime.
**Why it happens:** Tracker state is stored on the `YOLO` instance, not on disk; `persist=True` means "treat next frame as continuation of MY history" — a fresh instance has no history.
**How to avoid:** Worker process loads model exactly once (Pattern 2). Never recreate `YOLO()` mid-run. Document the invariant in `_pose_worker.py` module docstring. [CITED: ultralytics docs/discussions]
**Warning signs:** All track ids reset to 1 every frame; `boxes.id` is None.

### Pitfall 4: ProcessPoolExecutor on Windows requires `if __name__ == "__main__":` guard
**What goes wrong:** Importing `pastor_tracker.perception.pose_detector` from a child interpreter re-executes top-level code → infinite spawn loop → process bomb.
**Why it happens:** Windows uses `spawn` start method; child re-imports the parent module to find the worker target.
**How to avoid:** Worker target functions live in `_pose_worker.py` (module-level, importable), NOT inside `pose_detector.py`'s class. Application entry point (`__main__.py`) needs the standard `if __name__ == "__main__":` guard. The `executor` is created inside `PoseDetector.start()`, never at module import.
**Warning signs:** "RuntimeError: An attempt has been made to start a new process before the current process has finished its bootstrapping phase" or runaway python.exe processes in Task Manager.

### Pitfall 5: BoT-SORT default `track_high_thresh=0.25` may be too low for noisy stage lighting
**What goes wrong:** Stage lighting fluctuations (spotlight rim, flash photography from audience) produce momentary low-conf detections; BoT-SORT updates the locked track with a poor-quality detection, the centroid jumps, the camera jerks.
**Why it happens:** 0.25 is general-purpose default; speaker on stage typically has conf > 0.7.
**How to avoid:** Stage on Phase 8 smoke test for tuning. v1 default = stick with `botsort.yaml` defaults but expose `Config.botsort_yaml_path` (Claude's discretion field) so a custom override file can be dropped next to the config without code change. **Do NOT modify the bundled `botsort.yaml` in site-packages** — that gets clobbered on every `pip install -U ultralytics`.
**Warning signs:** Visible jerk on the camera output during stage flash; detections briefly switch ids during normal speech.

### Pitfall 6: ultralytics prints to stdout/stderr by default — pollutes structlog JSON
**What goes wrong:** YOLO logs every inference call (`0: 1080x1920 1 person, 23.4ms`) to stdout; structlog JSON line-oriented log is interleaved with text.
**Why it happens:** ultralytics has a `verbose=True` default for `model.predict()` / `model.track()`.
**How to avoid:** Pass `verbose=False` on EVERY call. Also redirect ultralytics' internal logger via `logging.getLogger("ultralytics").setLevel(logging.WARNING)` at worker init.
**Warning signs:** structlog JSON parsing breaks downstream; "tracker on" / "tracker off" lines appear in stdout.

### Pitfall 7: `shared_memory` resource_tracker leak warning at shutdown
**What goes wrong:** "UserWarning: resource_tracker: There appear to be 1 leaked shared_memory objects to clean up at shutdown."
**Why it happens:** The parent created the block but did not call `unlink()` before exit. On Linux this leaves the block in `/dev/shm`; on Windows the OS reaps it but the warning still fires (Python's resource_tracker isn't OS-aware).
**How to avoid:** `PoseDetector.stop()` calls `self._shm.close()` then `self._shm.unlink()` (no-op on Windows but cheap insurance for cross-platform CI). Wrap in `try/finally` so an exception during stop still unlinks. Mirror Phase 3's stop-order discipline (Pitfall 7 from `obs_camera.py`). [VERIFIED: docs.python.org/3/library/multiprocessing.shared_memory + community reports]
**Warning signs:** Warning at process exit; CI logs show resource_tracker output.

### Pitfall 8: `model.track()` with `classes=[0]` filter still allocates compute for non-person classes
**What goes wrong:** Setting `classes=[0]` makes ultralytics filter results post-NMS; doesn't reduce inference cost. Frames with no person still cost full inference.
**Why it happens:** YOLO is a single forward pass producing all 80 COCO classes; filtering is a results-list slice.
**How to avoid:** Acceptable — `classes=[0]` is still required to suppress audience members partially in frame from polluting the result list. Latency is fixed; what we save is BoT-SORT bookkeeping cost.
**Warning signs:** None — this is a "won't optimize what you might think it does" pitfall.

### Pitfall 9: filterpy `KalmanFilter` matrices accidentally typed `float64` while we want `float32` — slow and memory-noisy
**What goes wrong:** filterpy default is float64; on hot path with many predict/update cycles per second, GC pressure and cache miss rate go up unnecessarily.
**Why it happens:** filterpy uses numpy default dtype.
**How to avoid:** This is NOT a real problem at our cadence (30 Hz × ~100 µs/cycle = negligible). Document and skip. Property tests should use float64 to match production exactly.
**Warning signs:** None — premature optimization.

### Pitfall 10: First inference on `device='cuda'` includes 1–3 s of CUDA kernel JIT compilation
**What goes wrong:** First real frame's inference takes 1.5 s instead of 15 ms; downstream `consume()` returns immediately (drop-oldest), camera produces hundreds of dropped-frame WARNs.
**Why it happens:** PyTorch lazily compiles CUDA kernels on first use.
**How to avoid:** Worker init does a tiny warm-up inference (`np.zeros((640,640,3), dtype=np.uint8)`) BEFORE returning ready (Pattern 2). Time-budget the warm-up and log `pose_engine_warmup_complete` with elapsed ms.
**Warning signs:** First 30+ frames after start emit `inference_drop_oldest` WARN.

### Pitfall 11: `Detection.timestamp_ns` uses worker's perf_counter_ns, not the original Frame's
**What goes wrong:** Kalman gets the wrong dt because the timestamp is "when inference finished" rather than "when frame was captured".
**Why it happens:** Easy to write `now_ns = time.perf_counter_ns()` in the worker.
**How to avoid:** Pass `frame.timestamp_ns` to the worker call; worker copies it onto every emitted `Detection` verbatim. Kalman dt = `current_frame.timestamp_ns - previous_frame.timestamp_ns` (in seconds = `dt_ns / 1e9`).
**Warning signs:** Velocity estimates show suspicious sign flips or spikes at startup.

### Pitfall 12: `Detection`'s normalized coords vs ultralytics `boxes.xyxyn` semantic mismatch
**What goes wrong:** ultralytics returns `boxes.xyxy` (pixel coords) and `boxes.xyxyn` (normalized to image dims); developer reads `xyxyn` but indexes as `xyxy`.
**Why it happens:** Easy typo; both attributes exist.
**How to avoid:** ALWAYS use `.xyxyn` for `Detection.bbox_*_normalized`; ALWAYS divide keypoint pixel coords by `(width, height)` for normalized centroid. Add an explicit `assert 0.0 <= x <= 1.0` guard at the seam (matches `Detection`'s Pydantic field validators) so a unit-mismatch fails the Pydantic constructor immediately.
**Warning signs:** `Detection` constructor raises validation error "value is greater than 1.0".

## Code Examples

### Example 1: filterpy 4-state CV Kalman setup (verified)

```python
# Source: filterpy.readthedocs.io/en/latest/kalman/KalmanFilter.html
# +     filterpy.readthedocs.io/en/latest/common/common.html (Q_discrete_white_noise)
import numpy as np
from filterpy.kalman import KalmanFilter
from filterpy.common import Q_discrete_white_noise
from scipy.linalg import block_diag

def make_kalman_for_subject(initial_x: float, initial_y: float, dt: float) -> KalmanFilter:
    """Construct a fresh 4-state CV Kalman filter at first detection.

    State vector: [x, y, vx, vy] in normalized frame coords.
    Measurement: [x, y] (centroid from PERC-02 weighted keypoint mean).
    """
    kf = KalmanFilter(dim_x=4, dim_z=2)

    # State transition (CV model): position += velocity * dt
    kf.F = np.array([
        [1.0, 0.0, dt,  0.0],
        [0.0, 1.0, 0.0, dt ],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])

    # Measurement: we observe x and y only (not velocity)
    kf.H = np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
    ])

    # Process noise — discrete white noise model on each axis, then block_diag
    # var_pos picks how much the model "trusts" CV; tune on stage. Start ~1e-3 for normalized coords.
    q_axis = Q_discrete_white_noise(dim=2, dt=dt, var=1e-3)
    kf.Q = block_diag(q_axis, q_axis)  # 4x4 block-diagonal

    # Measurement noise — YOLO11-pose centroid is ±~3 px in normalized 1080p ≈ 0.003 std → var ~1e-5
    kf.R = np.eye(2) * 1e-4  # tune on stage; CONTEXT permits Claude discretion

    # Initial state — position from detection, velocity = 0
    kf.x = np.array([initial_x, initial_y, 0.0, 0.0])

    # Initial covariance — high uncertainty on velocity, low on position (we just measured it)
    # CONTEXT specifies "large velocity covariance (~1.0)"
    kf.P = np.diag([1e-3, 1e-3, 1.0, 1.0])

    return kf
```

### Example 2: Variable-dt predict (filterpy doesn't natively support per-call dt)

```python
# Source: github.com/rlabbe/filterpy/issues/196 — recompute F per call

def predict_with_dt(kf: KalmanFilter, dt: float) -> None:
    """Recompute F for the actual frame interval, then predict."""
    kf.F[0, 2] = dt
    kf.F[1, 3] = dt
    # Q also depends on dt; for tight tolerances recompute, else accept slight mismatch.
    # At 30 fps with dt jitter < 5 ms, recomputation is overkill. Skip and document.
    kf.predict()
```

### Example 3: ultralytics YOLO11-pose track call (canonical)

```python
# Source: docs.ultralytics.com/modes/track + practitioner examples
from ultralytics import YOLO
import numpy as np

model = YOLO("yolo11n-pose.pt")  # auto-downloads weights to ~/.cache/Ultralytics

results = model.track(
    frame_bgr,                    # numpy ndarray, BGR uint8, HxWx3 — ultralytics auto-converts internally
    persist=True,                 # critical: continue tracker history from previous call
    tracker="botsort.yaml",       # bundled config inside ultralytics package
    classes=[0],                  # COCO class 0 = person (filter audience-non-person)
    device="cuda",                # or "cpu", or 0, or None (auto)
    verbose=False,                # suppress stdout pollution
)

# Results extraction (single image → results[0])
result = results[0]
boxes = result.boxes              # ultralytics.engine.results.Boxes
keypoints = result.keypoints      # ultralytics.engine.results.Keypoints

# track_ids may be None on the very first frame before BoT-SORT assigns ids
track_ids: list[int] | None = (
    boxes.id.int().cpu().tolist() if boxes.id is not None else None
)
xyxyn: np.ndarray = boxes.xyxyn.cpu().numpy()           # (N, 4) normalized [x1, y1, x2, y2]
confs: np.ndarray = boxes.conf.cpu().numpy()            # (N,) detection confidence
kp_xyn: np.ndarray = keypoints.xyn.cpu().numpy()        # (N, 17, 2) normalized keypoint coords (COCO 17 layout)
kp_conf: np.ndarray = keypoints.conf.cpu().numpy()      # (N, 17) per-keypoint confidence
```

### Example 4: Subject centroid weighted-mean (PERC-02)

```python
# COCO keypoint indices (verified in ultralytics keypoint constants)
NOSE = 0
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_HIP = 11
RIGHT_HIP = 12

WEIGHT_NOSE = 0.4
WEIGHT_SHOULDER_MID = 0.4
WEIGHT_HIP_MID = 0.2

def compute_subject_centroid(
    kp_xyn: npt.NDArray[np.float32],   # shape (17, 2) for one person
) -> tuple[float, float]:
    """PERC-02 weighted mean. Caller has already filtered conf >= 0.55."""
    nose = kp_xyn[NOSE]
    shoulder_mid = (kp_xyn[LEFT_SHOULDER] + kp_xyn[RIGHT_SHOULDER]) * 0.5
    hip_mid = (kp_xyn[LEFT_HIP] + kp_xyn[RIGHT_HIP]) * 0.5
    cx = WEIGHT_NOSE * nose[0] + WEIGHT_SHOULDER_MID * shoulder_mid[0] + WEIGHT_HIP_MID * hip_mid[0]
    cy = WEIGHT_NOSE * nose[1] + WEIGHT_SHOULDER_MID * shoulder_mid[1] + WEIGHT_HIP_MID * hip_mid[1]
    return float(cx), float(cy)
```

### Example 5: Central-60% lock heuristic

```python
_CENTRAL_REGION_FRACTION: Final[float] = 0.6
_CENTRAL_HALF: Final[float] = (1.0 - _CENTRAL_REGION_FRACTION) / 2.0  # = 0.2

def is_in_central_region(cx: float, cy: float) -> bool:
    """PERC-04: central 60% of frame in normalized coords."""
    return _CENTRAL_HALF <= cx <= 1.0 - _CENTRAL_HALF and _CENTRAL_HALF <= cy <= 1.0 - _CENTRAL_HALF
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MediaPipe pose | YOLO11-pose | 2024 (YOLO11 release) | YOLO11 is faster + more accurate per ultralytics benchmarks; bundled BoT-SORT eliminates separate tracker integration |
| EMA on detection stream | filterpy Kalman | 2026 PROJECT.md decision | EMA adds lag (1-pole low-pass = phase shift); Kalman predicts during gaps without lag |
| Manual pickle/Queue ndarray transfer | `multiprocessing.shared_memory` | Python 3.8+, idiomatic since 3.10+ | Zero-copy; reduces 1080p frame ferry from ~6 MiB pickle copy to a name string |
| `multiprocessing.Process` + manual lifecycle | `concurrent.futures.ProcessPoolExecutor` | stdlib since 3.2; idiomatic for ML in 2026 | `Future` API integrates cleanly with asyncio via `loop.run_in_executor` |
| Global tracker state across models | `persist=True` per-model-instance | ultralytics 8.x | Cleaner contract: tracker history bound to model lifetime |

**Deprecated/outdated:**
- ByteTrack-only setups: BoT-SORT is the default since ultralytics 8.x and CLAUDE.md mandates it.
- `torch.multiprocessing` for inference fanout: standard `multiprocessing` works fine for max_workers=1; torch's variant is for multi-GPU training.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | filterpy 1.4.5 imports cleanly under numpy 2.4.x | Standard Stack + Pitfall list | Wave 0 import test will fail-loud; mitigation = pin numpy<2 or vendor a 50-line Kalman class |
| A2 | ultralytics 8.4.46 supports `tracker="botsort.yaml"` as a string (not requiring file path) | Code Example 3 | Falls back to absolute path of bundled yaml; cosmetic |
| A3 | BoT-SORT default `track_high_thresh=0.25` is acceptable for v1 stage video | Pitfall 5 | Phase 8 stage smoke test will surface; mitigation = `Config.botsort_yaml_path` override |
| A4 | YOLO11n-pose CPU inference fits within 33 ms/frame budget on dev box | (no published benchmark for this exact case) | If CPU is too slow, drop-oldest WARN floods; mitigation = require CUDA in production, document in README |
| A5 | `multiprocessing.shared_memory` on Windows correctly attaches by name across spawn'd workers | Pattern 3 | Documented behavior; failure mode is loud (worker raises FileNotFoundError on attach) |
| A6 | `persist=True` works correctly when called from inside a single worker process across many `executor.submit` cycles | Pitfall 3 | Single worker = single model instance, so this is just "calls in sequence on same instance" — well-supported per ultralytics docs |
| A7 | The `ultralytics` mypy override in pyproject.toml suffices; we won't need stub packages | Project Constraints table | Override line 81 already in place; if narrow type leaks expose `Any`, add typed `cast(...)` shims at the seam |
| A8 | ultralytics' `verbose=False` flag silences ALL stdout for `model.track()` (not just predict) | Pitfall 6 | Spot-check during Wave 0; mitigation = redirect `sys.stdout` during call (heavy-handed but safe) |
| A9 | Kalman R (measurement noise) ~1e-4 and Q ~1e-3 in normalized coords produce visually-smooth tracking | Code Example 1 | Phase 8 smoke test tunes on stage; CONTEXT permits Claude discretion on initial values |
| A10 | `ProcessPoolExecutor.shutdown(wait=True)` cleanly tears down the worker even if mid-inference | Lifecycle (implicit in Pattern 4) | `shutdown(cancel_futures=True, wait=True)` is the documented modern API; CUDA cleanup happens in worker's `__atexit` |

**Risk hierarchy:** A1 is the highest-risk assumption (could block Wave 0). A4 is the next (could redefine architecture if dev box is CPU-only and slow). All others are low-risk implementation details verifiable inline.

## Open Questions

1. **Bounded-queue size for inference ingress (Claude's discretion per CONTEXT)**
   - What we know: CONTEXT says "1–4: only the freshest frame matters"; current Future-driven design works with size-1 in-flight (Pattern 4)
   - What's unclear: Whether to keep an explicit `asyncio.Queue` ahead of `executor.submit` or just track `_inflight: Future | None` directly
   - Recommendation: **No queue; track `_inflight` directly.** Cleaner. Queue adds latency and a knob with no payoff at size-1.

2. **Exact Q (process noise) and R (measurement noise) values for normalized-coord Kalman**
   - What we know: Code Example 1 starts at Q=1e-3, R=1e-4; CONTEXT permits Claude discretion
   - What's unclear: Stage tuning hasn't happened (Phase 8 task)
   - Recommendation: **Hard-code as module-level Final constants in `_kalman.py` with docstring "v1 starting values; tune in Phase 8 / QA-04". Do NOT expose to Config in v1** (live tuning is Phase 7 dashboard scope; Q/R are not in PROMPT.md Config block).

3. **Custom `botsort.yaml` override path**
   - What we know: Pitfall 5 — defaults may be too low for stage
   - What's unclear: Whether to ship a `pastor_tracker/perception/botsort.yaml` in the package or rely on ultralytics defaults
   - Recommendation: **v1 = ultralytics default.** Add `Config.botsort_yaml_path: Path | None = None` (Claude's discretion field) so an operator can drop a tuned file at deploy time without code change. Document path semantics: relative to CWD if not None, else `"botsort.yaml"` string handed to ultralytics (which resolves to bundled).

4. **Where does `device='auto'` resolve to `cuda` vs `cpu`?**
   - What we know: ultralytics `device=None` (and likely `device='auto'` if accepted) auto-selects via `torch.cuda.is_available()`
   - What's unclear: Whether ultralytics accepts the literal string `"auto"`
   - Recommendation: **Resolve in `Config.yolo_device` validator.** `"auto"` → `"cuda"` if `torch.cuda.is_available()` else `"cpu"`, log the resolution. Always pass an explicit `"cuda"` or `"cpu"` to `model.track()`. Also catches the "user said cuda but no GPU" fail-loud case at start.

5. **Lifecycle of the shared_memory block across `PoseDetector.start()` retries**
   - What we know: Phase 3 `ObsCamera.start()` is single-shot
   - What's unclear: If user re-starts the orchestrator (Phase 6 lifecycle), does the previous shm block leak?
   - Recommendation: **Single-shot per Phase 2/3 precedent.** Restart = construct fresh `PoseDetector`. Document. Phase 6 orchestrator constructs new instances on restart anyway.

6. **Does YOLO11-pose return None for `boxes.id` on the first frame (before BoT-SORT initializes), even with `persist=True`?**
   - What we know: discussions report `boxes.id is None` is possible
   - What's unclear: Frequency
   - Recommendation: **Guard at the seam.** `_translate(...)` returns empty `list[Detection]` when `boxes.id is None`; subject_tracker stays in UNLOCKED for that frame. Test in `FakePoseEngine` by scripting a "first-frame-no-ids" sequence.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All Phase 4 | ✓ (per pyproject.toml constraint) | requires-python = ">=3.12,<3.13" | — |
| `ultralytics` | PERC-01, PERC-03 | ✗ (not yet in deps) | will install 8.4.46 | None — must add via `uv add ultralytics` in Wave 0 |
| `filterpy` | PERC-06, PERC-07 | ✗ (not yet in deps) | will install 1.4.5 | Hand-rolled Kalman (rejected by CLAUDE.md) |
| `torch` | transitively via ultralytics | ✗ | will install with ultralytics | Pure-CPU torch wheel; works without CUDA |
| `multiprocessing.shared_memory` | PERC-01 IPC | ✓ stdlib 3.12 | n/a | None — required |
| `concurrent.futures` | PERC-01 executor | ✓ stdlib 3.12 | n/a | None — required |
| CUDA / NVIDIA driver | optional GPU acceleration | unknown on dev box | TBD — verify in Wave 0 | CPU fallback works (slower; latency budget at risk per A4) |
| `scipy` | filterpy `block_diag` (Code Example 1) | ✗ (not yet in deps) | will install with filterpy as transitive dep | None — required for Q matrix construction |

**Missing dependencies with no fallback:**
- `ultralytics`, `filterpy` — both are direct asks of CONTEXT/CLAUDE.md; Wave 0 must `uv add` them.

**Missing dependencies with fallback:**
- CUDA — CPU fallback acceptable; A4 risk noted.

**Wave 0 Pre-flight (must be in Plan 04-01):**
1. `uv add ultralytics filterpy`
2. Run version + smoke imports listed under "Standard Stack — Version verification"
3. Confirm `pyproject.toml` mypy override list already includes `ultralytics` and `filterpy` (it does — line 81 `[[tool.mypy.overrides]]`)
4. Add new `Config.yolo_device: Literal["auto","cuda","cpu"] = "auto"` field + `Config.yolo_model_path: Path` field (default `Path("yolo11n-pose.pt")`)
5. Add new `Config.botsort_yaml_path: Path | None = None` field (Claude's discretion, see Open Question 3)

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.4 + pytest-asyncio (asyncio_mode = "auto") + hypothesis 6.152 (already configured per pyproject.toml) |
| Config file | pastor_tracker/pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `cd pastor_tracker && uv run pytest tests/test_pose_detector.py tests/test_subject_tracker_lock.py tests/test_subject_tracker_kalman.py tests/test_subject_tracker_hold.py -x` |
| Full suite command | `cd pastor_tracker && uv run pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PERC-01 | YOLO11-pose runs in process pool, doesn't block capture | unit (lifecycle) | `pytest tests/test_pose_detector.py::test_drop_oldest_when_inference_lags -x` | ❌ Wave 0 |
| PERC-01 | PoseEngine Protocol satisfied by FakePoseEngine | unit (DI seam) | `pytest tests/test_pose_detector.py::test_pose_engine_protocol_compliance -x` | ❌ Wave 0 |
| PERC-01 | Process pool starts/stops cleanly without resource_tracker leak | integration | `pytest tests/test_pose_detector.py::test_executor_lifecycle_no_leak -x` | ❌ Wave 0 |
| PERC-01 | shared_memory roundtrip preserves ndarray bytes | unit (real shm — no fake) | `pytest tests/test_pose_detector.py::test_shared_memory_ndarray_roundtrip -x` | ❌ Wave 0 |
| PERC-02 | Centroid weighted mean computes correct value | property (hypothesis) | `pytest tests/test_subject_tracker_lock.py::test_centroid_weighted_mean_property -x` | ❌ Wave 0 |
| PERC-02 | mean kp conf < 0.55 → detection rejected | unit | `pytest tests/test_subject_tracker_lock.py::test_low_conf_detection_rejected -x` | ❌ Wave 0 |
| PERC-03 | BoT-SORT track_id flows through Detection → TrackedSubject | unit (FakePoseEngine scripted ids) | `pytest tests/test_subject_tracker_lock.py::test_track_id_persists_across_frames -x` | ❌ Wave 0 |
| PERC-04 | At t=0, highest-conf central-60% person locks | unit | `pytest tests/test_subject_tracker_lock.py::test_initial_lock_central_60pct_highest_conf -x` | ❌ Wave 0 |
| PERC-04 | Person outside central 60% does NOT lock initially | unit | `pytest tests/test_subject_tracker_lock.py::test_no_lock_when_only_off_center_person -x` | ❌ Wave 0 |
| PERC-04 | Lock survives BoT-SORT id continuity through occlusion | unit (FakePoseEngine scripted gap) | `pytest tests/test_subject_tracker_lock.py::test_lock_survives_brief_occlusion -x` | ❌ Wave 0 |
| PERC-05 | Lock loss > 2.0 s triggers re-acquisition + WARN | unit (clock-injected) | `pytest tests/test_subject_tracker_lock.py::test_lock_loss_2s_reacquires -x` | ❌ Wave 0 |
| PERC-06 | KalmanFilter matrices F, H, Q, R, P, x correctly initialized | unit (real filterpy — no mock) | `pytest tests/test_subject_tracker_kalman.py::test_kalman_matrices_at_init -x` | ❌ Wave 0 |
| PERC-06 | predict + update on linear-trajectory test recovers velocity within tolerance | property (hypothesis) | `pytest tests/test_subject_tracker_kalman.py::test_linear_trajectory_velocity_recovery -x` | ❌ Wave 0 |
| PERC-06 | Smoothed vx is closer to truth than raw frame-diff (CLAUDE.md "test real implementations") | property | `pytest tests/test_subject_tracker_kalman.py::test_smoothed_velocity_beats_frame_diff -x` | ❌ Wave 0 |
| PERC-06 | Variable dt between frames does not break filter | unit | `pytest tests/test_subject_tracker_kalman.py::test_variable_dt_handled -x` | ❌ Wave 0 |
| PERC-06 | Re-acquisition creates a fresh KalmanFilter (no state leak) | unit | `pytest tests/test_subject_tracker_kalman.py::test_reset_creates_fresh_filter -x` | ❌ Wave 0 |
| PERC-07 | 3-consecutive-no-detection triggers HOLDING + WARN | unit | `pytest tests/test_subject_tracker_hold.py::test_three_misses_holds_posterior -x` | ❌ Wave 0 |
| PERC-07 | HOLDING does NOT advance Kalman predict (frozen output) | unit | `pytest tests/test_subject_tracker_hold.py::test_holding_freezes_posterior -x` | ❌ Wave 0 |
| PERC-07 | HOLDING → LOCKED on next matching detection within 2.0 s | unit | `pytest tests/test_subject_tracker_hold.py::test_holding_recovers_to_locked -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `cd pastor_tracker && uv run pytest tests/test_pose_detector.py tests/test_subject_tracker_lock.py tests/test_subject_tracker_kalman.py tests/test_subject_tracker_hold.py -x`
- **Per wave merge:** `cd pastor_tracker && uv run pytest`
- **Phase gate:** Full suite green + coverage ≥ 90 % on `subject_tracker.py` + `pose_detector.py`; 100 % on lock-acquisition + Kalman-update branches (CONTEXT Area 4 lock); `ruff check` + `mypy --strict` green; before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `pastor_tracker/tests/fixtures/pose_traces.py` — `FakePoseEngine` + `make_detection_sequence(...)` helpers (mirror `arduino_traces.py` shape)
- [ ] `pastor_tracker/tests/test_pose_detector.py` — covers PERC-01 (shared_memory + executor lifecycle + drop-oldest + Protocol seam)
- [ ] `pastor_tracker/tests/test_subject_tracker_lock.py` — covers PERC-02 + PERC-03 + PERC-04 + PERC-05 (lock state machine)
- [ ] `pastor_tracker/tests/test_subject_tracker_kalman.py` — covers PERC-06 (real filterpy property tests)
- [ ] `pastor_tracker/tests/test_subject_tracker_hold.py` — covers PERC-07 (HOLDING state)
- [ ] Dependency add: `uv add ultralytics filterpy` (via Wave 0 task)
- [ ] Config field add: `yolo_device`, `yolo_model_path`, optional `botsort_yaml_path`

## Security Domain

> Phase 4 has minimal attack surface — pure inference + pure logic on local data. ASVS coverage is brief but not skipped (security_enforcement = true in config.json).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | n/a — local desktop app, no remote endpoints |
| V3 Session Management | no | n/a |
| V4 Access Control | no | n/a |
| V5 Input Validation | yes | `Config.yolo_model_path` MUST be validated as an existing file at boot (Pydantic `FilePath` type or `model_validator`); `Config.yolo_device: Literal["auto","cuda","cpu"]` validates input |
| V6 Cryptography | no | n/a |

### Known Threat Patterns for {python + multiprocessing + pyTorch + cv2 stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious YOLO weight file (.pt) deserialization (PyTorch loads pickle on `YOLO(path)`) | Tampering / RCE | `Config.yolo_model_path` MUST point to a vetted weights file; document the SHA256 hash of `yolo11n-pose.pt` in README; reject paths outside the project root via Pydantic validator |
| `multiprocessing.shared_memory` name collision with another process | Information Disclosure | Use the auto-generated unique name (don't pass `name=` explicitly); document that the block holds frame data only — no secrets |
| ultralytics auto-downloads weights from `github.com/ultralytics` on first use | Tampering | Pin `Config.yolo_model_path` to a known local file; pre-download weights during install/Wave 0; do NOT rely on auto-download in production (no internet at the church venue) |
| Resource exhaustion via inference floods (DoS at the camera consumer) | Denial of Service | Drop-oldest at ingress (Pattern 4) bounds in-flight work to 1 |
| stdout pollution by ultralytics logs (Pitfall 6) | (logging integrity) | `verbose=False` everywhere; redirect ultralytics logger |
| GPU OOM (CUDA) on cold model load | DoS | One worker only (CONTEXT lock); 1-frame-in-flight cap |

**Phase 4 has NO network, NO file writes outside `~/.cache/Ultralytics` (auto-download cache), NO untrusted input.** The principal security control is "validated config + vetted weights file path".

## Sources

### Primary (HIGH confidence)
- [Python 3.12 multiprocessing.shared_memory docs](https://docs.python.org/3/library/multiprocessing.shared_memory.html) — Windows behavior, resource_tracker semantics, pickle behavior across processes
- [filterpy KalmanFilter docs](https://filterpy.readthedocs.io/en/latest/kalman/KalmanFilter.html) — KalmanFilter(dim_x, dim_z) API, F/H/Q/R/P/x conventions
- [filterpy.common.Q_discrete_white_noise docs](https://filterpy.readthedocs.io/en/latest/common/common.html) — discrete white noise model + block_diag pattern
- [ultralytics Multi-Object Tracking docs](https://docs.ultralytics.com/modes/track/) — `model.track(persist=True, tracker="botsort.yaml")` canonical example
- [ultralytics botsort.yaml source](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/trackers/botsort.yaml) — verified default values for track_high_thresh, track_buffer, match_thresh, etc.
- [ultralytics 8.4.46 PyPI page](https://pypi.org/project/ultralytics/) — current version + Python compatibility
- [filterpy 1.4.5 PyPI page](https://pypi.org/project/filterpy/) — current version (last release Oct 2018)
- Existing project code: `pastor_tracker/src/pastor_tracker/io/{arduino_motor.py,obs_camera.py,arduino_transport.py}` — Phase 2/3 patterns to mirror
- Existing project code: `pastor_tracker/src/pastor_tracker/core/types.py` — Frame/Detection/TrackedSubject DTO contracts already in place
- Project policy: `D:\System\Documents\PastorTrackingSystem\CLAUDE.md` — Tiger-style + forbidden patterns + mandated libraries

### Secondary (MEDIUM confidence)
- [ultralytics issue #16984 — persist=True semantics](https://github.com/ultralytics/ultralytics/pull/16984) — confirms persist resettability behavior
- [ultralytics discussion #20699 — inconsistent tracking](https://github.com/orgs/ultralytics/discussions/20699) — community guidance on tuning track_high_thresh
- [ultralytics issue #19288 — batch tracking results differ despite persist](https://github.com/ultralytics/ultralytics/issues/19288) — confirms tracker state is per-instance
- [Ultralytics thread-safe inference guide](https://docs.ultralytics.com/guides/yolo-thread-safe-inference/) — multiprocessing recommended over threading
- [filterpy GitHub issue #196 — variable dt handling](https://github.com/rlabbe/filterpy/issues/196) — recompute F per epoch pattern
- [SuperFastPython — sharing numpy arrays via SharedMemory](https://superfastpython.com/numpy-array-sharedmemory/) — canonical pattern for ndarray-backed shared block
- [Python issue 41447 — resource_tracker leak](https://bugs.python.org/issue41447) — context for "leaked shared_memory" warnings

### Tertiary (LOW confidence — flagged for validation)
- Stage-video-specific tuning of BoT-SORT parameters — no published benchmark; deferred to Phase 8 stage smoke
- YOLO11n-pose 1080p latency on the dev box — measure during Wave 0
- filterpy 1.4.5 numpy 2.4.x compatibility — community reports work; Wave 0 smoke import will confirm or fail-loud

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions verified against PyPI; APIs verified against official docs
- Architecture (process pool + shared_memory + Protocol seam): HIGH — mirrors well-established Phase 2/3 patterns; stdlib APIs documented
- Pitfalls: HIGH on the documented ones (1, 2, 3, 4, 6, 7, 11, 12); MEDIUM on tuning ones (5, 9, 10) — those need stage tuning to confirm
- Tuning constants (Q, R, BoT-SORT thresholds): LOW — placeholder values are starting points, not proven-on-stage

**Research date:** 2026-05-05
**Valid until:** 2026-06-05 (30 days; ultralytics releases bi-weekly so reverify version pin if planning slips). Trigger re-research earlier if any of: ultralytics 8.5.x ships with breaking API changes; filterpy releases 1.4.6+; numpy releases 2.5+ that filterpy explicitly fails against.
