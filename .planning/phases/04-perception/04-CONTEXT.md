# Phase 4: Perception - Context

**Gathered:** 2026-05-05
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

Detect the pastor on every frame with YOLO11-pose, lock onto them as the primary subject via BoT-SORT, and smooth their trajectory with a 4-state Kalman filter that predicts cleanly through occlusions and detection gaps.

**In scope:**
- `pastor_tracker/perception/pose_detector.py` — YOLO11-pose wrapper running in a `concurrent.futures.ProcessPoolExecutor(max_workers=1)`; BGR uint8 ndarrays passed via `multiprocessing.shared_memory` (zero-copy); emits per-frame `Detection` records with bbox, keypoints, mean kp confidence, BoT-SORT track id
- `pastor_tracker/perception/subject_tracker.py` — primary-subject lock state machine + 4-state Kalman filter; consumes `Detection` stream, emits `TrackedSubject` per frame
- BoT-SORT track-id persistence via `model.track(persist=True, tracker="botsort.yaml")`
- Subject centroid = weighted mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); reject detections with mean kp conf < 0.55 (per PERC-02)
- Initial lock = highest-conf person whose centroid sits in central 60% of frame (PERC-04)
- Re-acquisition after > 2.0 s lock loss = same heuristic on next qualifying frame, log WARN (PERC-05)
- 3 consecutive frames without detection → hold filter posterior, log WARN (PERC-07)
- 4-state Kalman `[x, y, vx, vy]` in normalized frame coords (`filterpy`); CV process model; predict during gaps, update on detection
- Public API: `async def tracked_subjects(self) -> AsyncIterator[TrackedSubject]` (mirrors Phase 2 `motor.events()` / Phase 3 `camera.frames()`)
- Read-only observable state for Phase 7 dashboard: `is_locked`, `current_track_id`, `last_lock_loss_ts_ns`
- Test surface: `PoseEngine` `Protocol` + in-tree `FakePoseEngine` producing scripted detection sequences; no GPU/model weights in CI

**Out of scope (later phases):**
- Motion intent / framer / pan controller / dispatcher (Phase 5)
- Pipeline orchestrator wiring (Phase 6)
- DearPyGui dashboard (Phase 7)
- On-stage smoke test against a real OBS install + real speaker (Phase 8 / QA-04)
- Multi-subject tracking — out of scope per PROJECT.md
- Tilt-axis / 3D pose / depth estimation
- Continuous device re-promotion / hot-swap of inference backend at runtime

**Requirements covered:** PERC-01..07.

</domain>

<decisions>
## Implementation Decisions

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts
- Detector = `ultralytics` YOLO11-pose; tracker = built-in BoT-SORT (`botsort.yaml`)
- State estimation = `filterpy` Kalman, 4-state `[x, y, vx, vy]` in normalized frame coords (PERC-06)
- Subject centroid weighted mean: nose 0.4, shoulder mid 0.4, hip mid 0.2; mean kp conf floor 0.55 (PERC-02)
- Initial lock = highest-conf person in central 60% of frame at start (PERC-04)
- Lock-loss timeout = 2.0 s → re-acquire via central-frame heuristic + WARN (PERC-05)
- 3-consecutive-no-detection rule → hold position + WARN (PERC-07)
- Process pool for inference; non-blocking on capture loop (PERC-01)
- Pure-core / dirty-edges layering — perception is a "dirty edge" (external models, GPU, inter-process), pure logic (centroid math, lock scoring, Kalman wiring) lives on typed DTOs
- `Frame.__post_init__` already asserts `dtype == np.uint8` AND `flags["C_CONTIGUOUS"] is True` (Phase 3 deep review, commit aac3da1) — perception consumes `Frame` and skips its own dtype/contiguity guards; test fixtures using `frame[..., ::-1]` views must wrap with `np.ascontiguousarray(...)`
- Pydantic v2 frozen DTOs; dataclass `frozen=True, slots=True` for ndarray-bearing types; mutate via `.model_copy(update=...)` / `dataclasses.replace(...)`
- Tiger-style fail-fast — typed exceptions, no silent fallback, no bare `except`
- `structlog` JSON logging only; no `print()`
- `mypy --strict` no `Any`; ≤ 2-level conditional nesting; no magic numbers (`Config` is authoritative)
- Conventional Commits, one logical change per commit
- Test policy — no mocked Kalman / damping math (CLAUDE.md); in-tree fakes permitted at the I/O seam (Phase 2 `FakeSerialTransport`, Phase 3 `FakeVideoSource` precedent)

### Inference Architecture (Area 1, all accepted)
- Single-worker process pool — `concurrent.futures.ProcessPoolExecutor(max_workers=1)`. YOLO11n on GPU saturates one device; multi-worker creates cross-process GPU contention. CPU fallback also single (same reason)
- IPC for ndarray = `multiprocessing.shared_memory` — zero-copy 1080p uint8 frame (~6 MB) per call; pickle would copy + serialize each frame
- Backpressure when inference > capture interval = drop oldest input frame at the queue boundary + structlog WARN (mirror Phase 2 / Phase 3 drop-oldest semantics); freshest frame matters for control, stale inference creates lag
- Device selection = new `Config` field `yolo_device: Literal["auto", "cuda", "cpu"]` default `"auto"` (ultralytics resolves); fail-fast WARN if `"cuda"` is requested but unavailable

### Subject Lock Lifecycle (Area 2, all accepted)
- Initial lock score = highest-conf person whose centroid sits inside central 60% of frame (PERC-04 verbatim) — no central-distance weighting
- Re-acquisition after > 2.0 s loss = same heuristic as initial — highest-conf person in central 60% on the next qualifying frame after the timeout fires (PERC-05 verbatim); log WARN
- Non-locked detections dropped silently — pose detector emits all detections, subject tracker filters to the locked track id only; BoT-SORT handles occlusion/ID persistence; no fallback ID stash
- Initial lock window = first frame where ≥ 1 person has centroid in central 60% AND mean kp conf ≥ 0.55; no warmup wait (PERC-04 says "at pipeline start")

### Kalman Filter Design (Area 3, all accepted)
- Process model = constant velocity (CV) on 4-state `[x, y, vx, vy]` per PERC-06 verbatim; matches a person walking/standing on stage; CA would add noise without payoff
- Init state at first detection = position from detection, velocity = 0, large velocity covariance (≈ 1.0) so the next detection corrects quickly (standard `filterpy` idiom)
- Reset on re-acquisition = full reset — new track id ⇒ new filter instance; carrying velocity from a different person creates tracking artifacts
- Behaviour during gap > 3 frames (PERC-07) = hold last filter posterior — stop predicting forward, output = last update mean, log WARN; track stays alive until the 2.0 s lock-loss timeout fires

### Output Surface & Test Strategy (Area 4, all accepted)
- Public API = `async def consume(self, detections, now_ns) -> TrackedSubject | None:` per BL-01 simplification (2026-05-05 deep-review fix). The earlier `tracked_subjects()` async-iterator surface deadlocked on an empty queue — Phase 6 orchestrator iterates by composing this consume() call inside its own outer loop (mirrors `PoseDetector.detections() -> SubjectTracker.consume()` flow). Phase 2 `motor.events()` and Phase 3 `camera.frames()` remain iterator-based; SubjectTracker is consume-based by design.
- `UltralyticsPoseEngine.detect` is fully implemented as of BL-02 fix (2026-05-05). Production path (executor.submit + shm copy + `_pose_worker.infer`) is wired; CI tests still go through `FakePoseEngine` (zero ML deps). Production seam exercised on stage in Phase 8 QA-04 hardware run.
- Test seam = `PoseEngine` `Protocol` wrapping YOLO + BoT-SORT; in-tree zero-deps `FakePoseEngine` produces scripted detections (track ids + keypoints + conf) — covers lock acquisition, occlusion, ID switch, conf-floor reject without GPU / model weights in CI
- Observable state for Phase 7 dashboard = read-only properties `is_locked: bool`, `current_track_id: int | None`, `last_lock_loss_ts_ns: int | None` (minimal, no implementation leak)
- Coverage target = ≥ 90 % line on `subject_tracker.py` + `pose_detector.py`; 100 % on lock-acquisition + Kalman-update branches (matches Phase 3's safety-critical 100% rule)

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal type / event names beyond `Detection` and `TrackedSubject` (already in `core/types.py`)
- Error class hierarchy under a single `PerceptionError` root (e.g., `PoseEngineUnavailableError`, `LockTimeoutError` if needed)
- Logging key names (kept consistent with structlog conventions established in Phases 1–3)
- File granularity inside `pastor_tracker/perception/` — `pose_detector.py` + `subject_tracker.py` is the default; split is permitted if line count balloons (e.g., a `_kalman.py` helper)
- Internal queue size for the inference pool (suggested 1–4: only the freshest frame matters)
- Whether the lock-loss timer is a small helper class or a stateful function

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/config.py` — frozen `Config` already exposes detection knobs from PROMPT.md (`yolo_model_path`, `min_keypoint_confidence`, etc.); a new `yolo_device: Literal["auto","cuda","cpu"]` field is the only addition needed for Area 1 Q4
- `pastor_tracker/core/types.py` — `Frame` (dataclass frozen+slots, BGR uint8 / HxWx3 / contiguity asserted), `Detection`, `TrackedSubject` already defined; perception produces these directly
- `pastor_tracker/io/obs_camera.py` (Phase 3) — exposes `async def frames() -> AsyncIterator[Frame]` consumer surface; perception subscribes via the orchestrator (Phase 6)
- `pastor_tracker/io/arduino_motor.py` + `arduino_protocol.py` (Phase 2) — establish the dedicated-thread + bounded-queue + typed-error pattern; same shape applies to the inference process pool boundary
- `structlog` logger configured at module entry (Phase 1) — bind `module="pose_detector"` / `module="subject_tracker"`

### Established Patterns (Phases 1–3)
- Pure-core / dirty-edges layering — `core/` is side-effect-free, `io/` and `perception/` are dirty edges
- Pydantic v2 `frozen=True` + `extra='forbid'` for DTOs; mutate via `.model_copy(update=...)`; dataclass `frozen=True, slots=True` for ndarray-bearing types
- Bounded `asyncio.Queue` with drop-oldest + WARN log on drop (Phases 2 / 3 use N=256 on event surface; perception inference ingress should be much smaller — only freshest frame matters)
- `Protocol`-based seam for I/O (Phase 2 `SerialTransport`, Phase 3 `VideoSource`); production wires the real backend, tests wire an in-tree zero-deps fake
- Lint policy rejects `print(`, bare `except:`, `except Exception: pass` (`ruff` rules T20 / BLE001 / E722); `mypy --strict` no `Any`
- Pure-parser-layer 100 % line+branch coverage rule from Phase 2 — apply equivalently to centroid math + lock scoring + Kalman wrapper

### Integration Points
- Upstream — `obs_camera.frames()` (Phase 3) provides `Frame` records; perception consumes via the orchestrator (Phase 6)
- Downstream — Phase 5 motion analyzer consumes `TrackedSubject` records (`vx`, `vy`, position, lock state)
- Pipeline orchestrator (Phase 6) wires `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → ...`; perception lifecycle (`start`, `stop`) must compose with the orchestrator's overall lifecycle and propagate typed errors upward
- Dashboard (Phase 7) reads `is_locked`, `current_track_id`, `last_lock_loss_ts_ns`, mean detection confidence (status panel), live skeleton (preview overlay)
- `Config` read once at construction; no live reconfig in Phase 4 (dashboard "Save Config" reloads + restarts pipeline, not Phase 4's concern)

</code_context>

<specifics>
## Specific Ideas

- Mirror Phase 2's `SerialTransport` / Phase 3's `VideoSource` pattern: introduce `PoseEngine` Protocol in `pose_detector.py` so production wires the real `ultralytics.YOLO(...).track(persist=True, tracker="botsort.yaml")` and tests wire `FakePoseEngine`
- The inference-ingress queue (capture-thread → process-pool worker) should be size 1 with drop-oldest semantics — only the latest `Frame` is worth running; older frames in flight = wasted GPU cycles
- Use a `ProcessPoolExecutor(max_workers=1)` `submit(...)` + `Future`-driven async wrapper rather than `multiprocessing.Process` directly — clean cancellation, structured exception propagation, no manual lifecycle
- Worker process loads the model once at startup (lazy-init guarded by `if _model is None`) — avoids per-call cold start; subsequent calls reuse the warm model
- `multiprocessing.shared_memory.SharedMemory` block created per-call (or pooled with size guard) — zero-copy reduces 1080p frame transfer cost from ~6 MB pickle copy to a name string + ndarray view in the worker
- `OBSCameraNotFoundError` shape inspired error message format here too: `expected device='cuda', available=['cpu']` for the cuda-not-available WARN
- Lock-loss WARN shape: `event="lock_loss" track_id=… last_seen_ts_ns=… age_ms=…` so post-mortem is one structlog query (mirror Phase 2 RX log shape)
- Re-acquisition WARN: `event="lock_reacquired" old_track_id=… new_track_id=… gap_ms=…`
- `TrackedSubject` should carry the Kalman covariance trace (or a single confidence scalar) so Phase 5 motion analyzer / Phase 7 dashboard can show predicted-vs-measured uncertainty if needed (deferred decision — the field exists if `core/types.py` already supports it; if not, add when planner asks)
- Property tests for the Kalman wrapper — feed a synthetic linear trajectory + Gaussian noise, assert smoothed `vx` is closer to ground truth than raw frame-diff (per CLAUDE.md "test real implementations" rule)

</specifics>

<deferred>
## Deferred Ideas

- ONNX / TensorRT export of YOLO11-pose for tighter latency — deferred; ultralytics PT default is good enough for v1, and re-export complicates BoT-SORT integration
- Multi-camera / multi-subject coordination — out of scope per PROJECT.md (v2 MULTI-01)
- Tilt-axis / 3D pose / depth estimation — out of scope (v2 TILT-01)
- Adaptive process noise (Q matrix) based on motion magnitude — deferred; CV with fixed Q is the PERC-06 contract, sufficient for stage motion
- Soft-reset Kalman on re-acquisition (carry covariance forward) — rejected in Area 3; revisit only if v2 evidence shows hard reset causes visible artifacts
- Backup-ID stash for resilience to BoT-SORT mis-IDs — rejected in Area 2; revisit only with on-stage data showing BoT-SORT failures
- Re-promotion of inference backend (CPU → GPU mid-run) — out of scope
- Per-frame skeleton overlay rendered inside perception — Phase 7 dashboard concern, not perception's
- Real-camera + real-GPU pytest fixture (`pytest --hardware` marker) — deferred to Phase 8 QA-04 stage smoke
- Recording golden detection sequences against a known video — deferred; `FakePoseEngine` scripted sequences cover the same paths with zero deps

</deferred>
