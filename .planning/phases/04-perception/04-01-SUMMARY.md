---
phase: 04-perception
plan: 01
subsystem: perception
tags: [perception, ipc, shared-memory, process-pool, ultralytics, yolo, botsort, filterpy, kalman]
requirements: [PERC-01, PERC-03]
dependency_graph:
  requires:
    - "pastor_tracker/src/pastor_tracker/config.py (existing field idiom + FilePath import)"
    - "pastor_tracker/src/pastor_tracker/core/types.py (Detection DTO base + _FrozenModel)"
    - "pastor_tracker/src/pastor_tracker/io/obs_camera.py (state-enum + Protocol seam pattern)"
    - "pastor_tracker/src/pastor_tracker/io/arduino_transport.py (runtime_checkable Protocol idiom)"
    - "pastor_tracker/tests/fixtures/arduino_traces.py + camera_traces.py (fixture shape)"
  provides:
    - "PoseEngine Protocol (runtime_checkable) -- DI seam for production + tests"
    - "UltralyticsPoseEngine production class with start()/close() lifecycle"
    - "_pose_worker.warmup + _pose_worker.infer (top-level, spawn-safe)"
    - "PoseEngineResult + _PoseDetection picklable DTOs"
    - "_keypoints.weighted_keypoint_centroid -- single source of truth for PERC-02 math"
    - "PerceptionError + PoseEngineUnavailableError typed hierarchy"
    - "_DetectorState enum (DISCONNECTED/STARTING/RUNNING/FAULTED/CLOSED)"
    - "FakePoseEngine + make_detection + 3 baseline scripted traces"
    - "Detection.track_id: int | None field (BoT-SORT propagation)"
    - "Config.yolo_model_path / yolo_device / botsort_yaml_path"
  affects:
    - "Plan 04-02 (Wave 2 -- subject_tracker.py + Kalman) consumes Detection.track_id, FakePoseEngine, make_detection, _keypoints.weighted_keypoint_centroid"
    - "Plan 04-03 (Wave 3 -- orchestrator + integration) consumes UltralyticsPoseEngine.start/close, PoseEngine Protocol, scripted traces"
tech_stack:
  added:
    - "ultralytics>=8.4,<9.0 (8.4.46 resolved -- pulled torch 2.11.0, torchvision 0.26.0, scipy, matplotlib, ~28 transitive)"
    - "filterpy>=1.4,<2.0 (1.4.5 resolved)"
  patterns:
    - "Protocol seam (runtime_checkable) for PoseEngine -- mirrors SerialTransport / FrameSource"
    - "Long-lived SharedMemory block allocated in parent.start(), name-attached in worker.infer"
    - "Lazy YOLO load in worker (_ensure_model singleton + 640x640 zero-image warmup)"
    - "Top-level functions only as ProcessPoolExecutor targets (Pitfall 4 -- spawn pickling)"
    - "Single PERC-02 centroid implementation in _keypoints.weighted_keypoint_centroid (B1 contract)"
key_files:
  created:
    - "pastor_tracker/src/pastor_tracker/perception/_keypoints.py"
    - "pastor_tracker/src/pastor_tracker/perception/_pose_worker.py"
    - "pastor_tracker/src/pastor_tracker/perception/pose_detector.py"
    - "pastor_tracker/tests/fixtures/pose_traces.py"
    - "pastor_tracker/tests/test_pose_detector.py"
    - "pastor_tracker/tests/test_subject_tracker_kalman.py"
    - ".planning/phases/04-perception/deferred-items.md"
  modified:
    - "pastor_tracker/pyproject.toml (added ultralytics + filterpy)"
    - "pastor_tracker/uv.lock (28 new packages)"
    - "pastor_tracker/src/pastor_tracker/config.py (3 perception fields)"
    - "pastor_tracker/src/pastor_tracker/core/types.py (Detection.track_id)"
    - "pastor_tracker/src/pastor_tracker/perception/__init__.py (public re-exports)"
    - "pastor_tracker/tests/test_config.py (3 new perception field tests)"
    - "pastor_tracker/tests/test_types.py (track_id optional test)"
decisions:
  - "Aligned PoseEngineUnavailableError message to literal `expected={!r}, available=...` to match the contract test (plan template said 'expected device=' but behaviour assertion demanded 'expected=')"
  - "Scoped `pytest.mark.filterwarnings('ignore::SyntaxWarning')` on test_filterpy_smoke_import only -- silences the unrelated upstream `\\Sum` docstring SyntaxWarning under Python 3.12 + filterwarnings=error, while leaving numpy DeprecationWarning escalation intact for the actual A1 fail-fast guard"
  - "Worker accepts model_path / botsort_yaml_path as `str` (not `Path`) across the spawn pickle boundary -- ultralytics + the tracker= kwarg both accept strings; strings pickle without filesystem-handle baggage"
  - "Added `# type: ignore[attr-defined]` on `from ultralytics import YOLO` -- ultralytics 8.4.46 ships partial type stubs, defeating the project-wide `ignore_missing_imports` override; explicit ignore is the minimum-surface fix"
  - "_BYTES_PER_PIXEL=3 + _INFLIGHT_SLOTS=1 + _WORKER_WARMUP_TIMEOUT_SEC=30.0 + _NS_PER_SEC=1_000_000_000 declared as module-level Final constants in pose_detector.py per CLAUDE.md rule 6"
metrics:
  started: "2026-05-05T13:42Z"
  completed: "2026-05-05T14:50Z"
  duration_minutes: 68
  task_count: 2
  file_count: 13
  test_count_added: 11
---

# Phase 4 Plan 1: Wave 1 — Perception Seam + Dependency Bootstrap Summary

Lay the entire seam Phase 4 needs to be testable without GPU / model weights: add `ultralytics + filterpy` deps and prove they resolve under numpy 2.4, extend `Config` and `Detection` with the three perception fields and BoT-SORT `track_id`, write the spawn-safe `_pose_worker` + the `PoseEngine` Protocol seam in `pose_detector.py` with the production `UltralyticsPoseEngine` start/close lifecycle, and ship `FakePoseEngine` + scripted traces so Plan 02 / Plan 03 can drive the full state machine with zero ML deps. **All 39 plan tests pass; ruff src+tests clean; mypy src clean (20 files); 5 Wave-0 fail-fast guards green; B1 weighted-centroid contract verified.**

## Final Config Field Count

`Config.model_fields` reports **28 fields** after this plan (`{**existing 25**} ∪ {yolo_model_path, yolo_device, botsort_yaml_path}`). New fields:

| Field                | Type                                | Default                | Description                                                                 |
| -------------------- | ----------------------------------- | ---------------------- | --------------------------------------------------------------------------- |
| `yolo_model_path`    | `pathlib.Path`                      | `Path("yolo11n-pose.pt")` | Weights file; auto-download on first use; ASVS V5 SHA256 documented in README |
| `yolo_device`        | `Literal["auto", "cuda", "cpu"]`    | `"auto"`               | Inference device; explicit `cuda` fail-fasts loud if CUDA missing            |
| `botsort_yaml_path`  | `Path \| None`                      | `None`                 | Optional override; `None` uses bundled `botsort.yaml`                         |

Per the **W2 fix**, the docstring field-count line was NOT updated; downstream tests use `assert {fields}.issubset(Config.model_fields)` instead of an absolute count compare.

## Detection.track_id Contract

`Detection.track_id: int | None = Field(ge=0, default=None)` placed AFTER `timestamp_ns`, BEFORE `_bbox_well_ordered`. Default `None` covers (a) the very first frame before BoT-SORT initializes (RESEARCH 04 Pitfall 3 / Open Question 6) and (b) synthetic / fake detections that bypass tracking. Negative values rejected via Pydantic `ge=0`. Cross-field validator unchanged (track_id does not participate in bbox ordering).

`_translate` in `_pose_worker.py` populates `track_id` from `result.boxes.id.int().cpu().tolist()[idx]` when present, `None` otherwise.

## RESEARCH 04 Pitfall A1 Outcome — filterpy under numpy 2.4.x

filterpy 1.4.5 imports cleanly under numpy 2.4.0 — **no numpy DeprecationWarning fires** in `KalmanFilter(dim_x=4, dim_z=2).predict()`. **No numpy pin needed.** Plan 02 can proceed with the real `filterpy.kalman.KalmanFilter` (CLAUDE.md hard rule: test real implementations).

**Caveat / Deviation Rule 3 (applied inline):** filterpy 1.4.5 ships an upstream cosmetic `\Sum` docstring escape that Python 3.12 raises as `SyntaxWarning: invalid escape sequence '\S'` at filterpy import time. Under the project-wide `pytest filterwarnings = ["error"]` this promoted to a hard `SyntaxError` on first import, BEFORE any numpy interop was exercised. We applied a single per-test `@pytest.mark.filterwarnings("ignore::SyntaxWarning")` to `test_filterpy_smoke_import` only; numpy `DeprecationWarning` remains escalated globally so the A1 fail-fast guard for genuine numpy/filterpy regressions stays intact. See `.planning/phases/04-perception/deferred-items.md` for the long-term cleanup hook.

## `_resolved_device` on Dev Box

`torch.cuda.is_available() == False` on the dev workstation. `_resolve_device("auto")` resolves to `"cpu"`. No CUDA JIT warmup latency observed (warmup not exercised end-to-end in Plan 01 — `start()` warmup happens against the real `_ensure_model` only when an integration test or the orchestrator calls it; Plan 03 will time it). The `_WORKER_WARMUP_TIMEOUT_SEC = 30.0` Final constant is sized for CUDA cold-start and well over the CPU warmup budget.

If an operator deploys with `yolo_device="cuda"` on this box, `_resolve_device` will raise `PoseEngineUnavailableError(expected="cuda", available=["cpu"])` exactly as designed (RESEARCH 04 Open Question 4 fail-fast).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Aligned `PoseEngineUnavailableError` message format with the contract test**
- **Found during:** Task 2, running `tests/test_pose_detector.py::test_perception_error_hierarchy`.
- **Issue:** Plan's `<action>` code template emitted `f"expected device={expected!r}, available={available}"`, but the `<behavior>` contract on the same task asserted `"expected='cuda'" in str(err)`. The literal word "device" in the action template would have failed the test.
- **Fix:** Dropped "device" — final form: `f"expected={expected!r}, available={available}"`. Test passes; downstream callers see a stable, contract-driven shape.
- **Files modified:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:54`.
- **Commit:** `3db7ced`.

**2. [Rule 3 — Blocking] Scoped `filterwarnings("ignore::SyntaxWarning")` on the filterpy smoke test**
- **Found during:** Task 1, first run of `test_filterpy_smoke_import`.
- **Issue:** filterpy 1.4.5 contains a docstring with the literal sequence `\Sum` at `filterpy/common/helpers.py:367`. Python 3.12 raises `SyntaxWarning: invalid escape sequence '\S'`; project-wide `pytest filterwarnings = ["error"]` promotes it to a hard `SyntaxError` at filterpy import, BEFORE any numpy 2.4 interop is exercised.
- **Fix:** Single per-test `@pytest.mark.filterwarnings("ignore::SyntaxWarning")` on `test_filterpy_smoke_import`. Numpy `DeprecationWarning` remains globally escalated — the actual A1 contract (filterpy / numpy 2.4 interop) is preserved.
- **Files modified:** `pastor_tracker/tests/test_subject_tracker_kalman.py`.
- **Commit:** `c7470d2`.

**3. [Rule 1 — Bug] Added `# type: ignore[attr-defined]` to `from ultralytics import YOLO`**
- **Found during:** Task 2, running `mypy src`.
- **Issue:** ultralytics 8.4.46 ships partial type stubs that defeat the project-wide `[[tool.mypy.overrides]] module = ["ultralytics.*"] ignore_missing_imports = true` rule — mypy now sees real types and reports `Module "ultralytics" does not explicitly export attribute "YOLO"`.
- **Fix:** `from ultralytics import YOLO  # type: ignore[attr-defined]` — minimum-surface ignore on the single import line. Inline justification preserved.
- **Files modified:** `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py:76`.
- **Commit:** `3db7ced`.

### Deferred (out of scope per execute-plan SCOPE BOUNDARY rule)

- **Pre-existing tests/-side mypy cascade:** Adding three Config fields widened the union enumeration on every existing test that does `Config(**dict[str, Any])` — pre-existing baseline 498 errors → 591 after Plan 01 (all in tests, mypy on `src` remains clean). Pattern is `dict[str, Any]` fixture annotation + `**unpack` into Pydantic-mypy's synthesized `__init__`. Logged to `.planning/phases/04-perception/deferred-items.md` for a future test-hygiene plan; out of scope for Plan 04-01/02/03 (perception focus).

## Auth Gates

None.

## Verification

| Gate                                                     | Command                                                                              | Result                                |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------- |
| Plan-level pytest                                        | `uv run pytest tests/test_config.py tests/test_types.py tests/test_pose_detector.py tests/test_subject_tracker_kalman.py -x` | **39 passed** (5.52s)                 |
| Wave-0 fail-fast tests (per `<verify>`)                  | 5 named tests in plan                                                                | **5 passed** (1.89s)                  |
| Task 2 verification                                      | `uv run pytest tests/test_pose_detector.py -x`                                       | **6 passed** (0.79s)                  |
| Ruff src + tests                                         | `uv run ruff check src tests`                                                        | **All checks passed**                 |
| Mypy src (the contract surface)                          | `uv run mypy src`                                                                    | **0 errors in 20 files**              |
| Public surface import                                    | `from pastor_tracker.perception import PoseDetector, PoseEngine, UltralyticsPoseEngine, PerceptionError, PoseEngineUnavailableError` | **ok**                                |
| Config positive-subset assertion (W2 fix)                | `assert {'yolo_device','yolo_model_path','botsort_yaml_path'}.issubset(Config.model_fields)` | **passes**                            |
| B1 PERC-02 weighted-centroid contract                    | `weighted_keypoint_centroid` returns `cx≈0.7, mean_conf≈0.9` for kp at x=0.7         | **passes** (fp tol < 1e-6)            |
| Default Config snapshot                                  | `c=Config(); c.yolo_device, c.yolo_model_path.name, c.botsort_yaml_path`             | **`auto yolo11n-pose.pt None`**       |

## Acceptance Criteria — Self-Audit

- [x] `grep -c '"ultralytics"' pyproject.toml` ≥ 2 (deps + mypy override)
- [x] `grep -c '"filterpy"' pyproject.toml` ≥ 2
- [x] `grep -c 'yolo_device:' config.py` == 1
- [x] `grep -c 'yolo_model_path:' config.py` == 1
- [x] `grep -c 'botsort_yaml_path:' config.py` == 1
- [x] `grep -c 'track_id:' core/types.py` ≥ 1
- [x] `_pose_worker.py` exposes `warmup`, `infer`, `_translate`, `_ensure_model`, `_silence_ultralytics_stdout`
- [x] `_keypoints.py` exposes `weighted_keypoint_centroid`; B1 contract sample passes
- [x] `pose_detector.py` defines `PoseEngine`, `UltralyticsPoseEngine`, `_DetectorState`, `PerceptionError`, `PoseEngineUnavailableError`
- [x] `@runtime_checkable` decorator present
- [x] Module-level `Final[...]` constants ≥ 5
- [x] `pose_traces.py` defines `FakePoseEngine` and `make_detection`
- [x] `test_pose_detector.py` defines exactly 6 test functions
- [x] `perception/__init__.py` re-exports the five public names
- [x] All 6 task-2 verify tests pass
- [x] `mypy src` exits 0; `ruff check src tests` exits 0
- [x] Negative check: pose_detector.py uses `from pastor_tracker.perception import _pose_worker` (module reference; **not** `from ... import warmup, infer`) per Pitfall 4

## Threat Model Disposition

All Plan 01 mitigations from `<threat_model>` are in place:

| Threat ID | Mitigation Status                                                                                  |
| --------- | -------------------------------------------------------------------------------------------------- |
| T-04-01   | `Config.yolo_model_path: Path` typed; runtime existence enforced by `PoseDetector.start` (Plan 03) |
| T-04-02   | Accepted with operator control — README SHA256 doc deferred to Phase 4 close                       |
| T-04-03   | `Config.yolo_model_path` defaulting to a relative path means missing-file fails loud at first use  |
| T-04-04   | `SharedMemory(create=True, size=...)` uses auto-generated unique name (no `name=` passed)          |
| T-04-05   | `_INFLIGHT_SLOTS = 1` Final constant in place; drop-oldest at ingress lands in Plan 03             |
| T-04-06   | `Literal["auto","cuda","cpu"]` Pydantic Field rejects any other value                              |
| T-04-07   | `_silence_ultralytics_stdout()` in `_ensure_model`; `verbose=False` on every track/predict call    |

No new security-relevant surface was introduced beyond the threat register.

## Self-Check: PASSED

Verified via:
- `git log --oneline` shows commits `c7470d2` (Task 1) and `3db7ced` (Task 2) on `worktree-agent-af300903209b8a2f8`
- All 7 created files exist on disk under the worktree path
- All 6 modified files reflect the documented edits
