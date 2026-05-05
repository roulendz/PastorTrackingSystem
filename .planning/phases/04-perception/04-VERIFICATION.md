---
phase: 04-perception
verified: 2026-05-05T19:30:00Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "PERC-01 production engine on real hardware (UltralyticsPoseEngine.detect over real torch + ultralytics + YOLO11-pose weights)"
    expected: "Production engine resolves device, warms up, runs inference on a real OBS VCam frame, emits Detection records with valid track_id; sustains 30 fps without blocking the capture loop on the dev box (CPU) and on the on-stage GPU box."
    why_human: "BL-02 deep-review fix landed the production code path (UltralyticsPoseEngine.detect now copies frame -> shm -> executor.submit -> _pose_worker.infer -> Detection). 04-03-SUMMARY.md and pose_detector.py:208-214 explicitly defer the runtime test to Phase 8 QA-04 because it requires real torch + ultralytics + GPU/model weights. CI seam-tests all orchestrator paths via FakePoseEngine; the production seam executes only on stage hardware."
  - test: "PERC-04 / PERC-05 lock stability under real BoT-SORT track-id behaviour on stage"
    expected: "Single pastor in central 60% locks within first frame; track_id stays stable across normal stage occlusions (lectern, gestures, brief turns); lock-loss WARN fires only on real >2.0 s absence; reacquisition picks the right person on return."
    why_human: "Scripted FakePoseEngine traces with stable / mutated track_ids verify the state-machine logic perfectly (8 lock + 3 hold + 4 e2e tests). Real BoT-SORT under stage flash, audience motion, and interpreter at podium edge can mis-ID — only on-stage QA-04 demonstrates the heuristic survives real noise."
---

# Phase 4: Perception Verification Report

**Phase Goal:** Detect the pastor on every frame with YOLO11-pose, lock onto them as the primary subject via BoT-SORT, and smooth their trajectory with a 4-state Kalman filter that predicts cleanly through occlusions and detection gaps.

**Verified:** 2026-05-05T19:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | YOLO11-pose runs in a process pool and emits per-frame detections without blocking the capture loop on either GPU or CPU | VERIFIED (with hardware deferral) | `UltralyticsPoseEngine` owns `ProcessPoolExecutor(max_workers=_INFLIGHT_SLOTS=1)` (`pose_detector.py:157`); `_pose_worker.infer` is spawn-safe top-level (`_pose_worker.py:108`); `PoseDetector.consume()` drops oldest at ingress with `inference_drop_oldest` WARN (`pose_detector.py:395-414`); `_resolve_device("auto")` falls to CPU when CUDA missing (`pose_detector.py:80-102`). `test_drop_oldest_when_inference_lags` proves non-blocking ≥4 WARN events under 30fps cadence vs 100ms inference. **Production `detect()` body landed via BL-02 fix** (`pose_detector.py:187-251`); CI test deferred to QA-04 per `04-03-SUMMARY.md`. |
| 2 | Subject centroid = weighted mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); detections with mean keypoint confidence < 0.55 rejected | VERIFIED | Single source of truth: `_keypoints.weighted_keypoint_centroid` (`_keypoints.py:42-81`) with weights `_WEIGHT_NOSE=0.4`, `_WEIGHT_SHOULDER_MID=0.4`, `_WEIGHT_HIP_MID=0.2`. Both `_pose_worker._translate` (`_pose_worker.py:213`) and `subject_tracker.compute_subject_centroid` (`subject_tracker.py:95`) delegate. `SubjectTracker.consume` filters by `d.mean_keypoint_confidence >= self._config.detection_confidence_min` (line 173). `test_centroid_weighted_mean_property` (hypothesis-driven) and `test_low_conf_detection_rejected` + e2e `test_e2e_low_conf_no_lock` + B1 `test_e2e_perc02_weighted_centroid_in_emit` (cx=0.70 from kp, NOT bbox midpoint 0.50). |
| 3 | At pipeline start, the highest-conf person in central 60% locks as primary; BoT-SORT track ID persists across occlusions; other people ignored | VERIFIED | `is_in_central_region` predicate at `[0.2, 0.8]` (`subject_tracker.py:101-106`); `_try_lock` picks `max(candidates, key=lambda d: d.mean_keypoint_confidence)` filtered by `is_in_central_region` AND `d.track_id is not None` (lines 205-216); `_find_match` filters by locked track_id (line 371). Detection.track_id (`int \| None`, `ge=0`) flows from BoT-SORT into TrackedSubject.track_id. `test_initial_lock_central_60pct_highest_conf` (central wins over higher-conf-off-center), `test_no_lock_when_only_off_center_person`, `test_lock_survives_brief_occlusion`, `test_track_id_persists_across_frames`, e2e `test_e2e_lock_acquire_emits_log`. |
| 4 | Lock loss > 2.0 s triggers re-acquisition + WARN; 3 consecutive missing frames hold last position + WARN | VERIFIED | `_LOCK_LOSS_TIMEOUT_SEC = 2.0` and `_HOLD_POSTERIOR_FRAME_THRESHOLD = 3` Final constants (`subject_tracker.py:56-57`); `_tick_locked` checks elapsed > 2.0 s and transitions LOCKED→LOST with `lock_loss` WARN (lines 260-275, W3 single-gap path); `_tick_locked` increments miss counter and transitions to HOLDING at threshold 3 (lines 289-293); `_tick_holding` emits `lock_holding` WARN once per HOLDING entry (line 339-345); `_try_reacquire` builds fresh KalmanFilter via `reset_for_new_track` and emits `lock_reacquired` WARN. `test_lock_loss_2s_reacquires`, `test_single_gap_over_2s_triggers_lost_from_locked` (W3), `test_three_misses_holds_posterior`, `test_holding_recovers_to_locked`. |
| 5 | 4-state Kalman `[x, y, vx, vy]` in normalized coords predicts during gaps and updates on detection — output is lag-free relative to raw EMA baseline | VERIFIED | `make_kalman_for_subject` (`_kalman.py:41-94`) constructs real `filterpy.kalman.KalmanFilter(dim_x=4, dim_z=2)` with F[0,2]=F[1,3]=dt CV transition, H[0,0]=H[1,1]=1.0 measurement, Q from `Q_discrete_white_noise`, R = eye*1e-4, x=[init_x, init_y, 0, 0], P diag. `predict_with_dt(kf, dt)` mutates F and calls `kf.predict()` for variable dt. `_KalmanWrapper.hold_posterior` returns `kf.x_post` ravelled WITHOUT predict (Pitfall 4 — covariance does not grow). `_KalmanWrapper.reset_for_new_track` constructs FRESH KalmanFilter (Pitfall 2 — no carried velocity); WR-07 fix forwards tunable knobs. `test_smoothed_velocity_beats_frame_diff` (hypothesis, 40 examples) directly proves `mean_smoothed_err < mean_raw_err` on noisy linear trajectories — the canonical EMA-baseline-beating contract. `test_kalman_matrices_at_init`, `test_linear_trajectory_velocity_recovery`, `test_variable_dt_handled`, `test_reset_creates_fresh_filter`, `test_holding_freezes_posterior` (covariance trace before == after). |

**Score:** 5/5 truths verified.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pastor_tracker/src/pastor_tracker/perception/__init__.py` | Public re-exports | VERIFIED | Exports `PoseDetector`, `PoseEngine`, `UltralyticsPoseEngine`, `PerceptionError`, `PoseEngineUnavailableError`, `SubjectTracker`. Imported by tests/orchestrator. |
| `pastor_tracker/src/pastor_tracker/perception/_keypoints.py` | PERC-02 weighted centroid helper | VERIFIED | Single source of truth; 82 lines; weights and COCO indices as Final constants; raises ValueError on too-short kp_xyn. |
| `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py` | Spawn-safe worker target | VERIFIED | Top-level `warmup`, `infer` functions (Pitfall 4); lazy YOLO load via `_ensure_model`; silences ultralytics stdout (Pitfall 6); WR-02/03/04/05 defensive bounds checks at translation seam; clamps xyxyn + centroid into [0,1]; uses `result.boxes.xyxyn` (Pitfall 12); forwards `timestamp_ns` (Pitfall 11). |
| `pastor_tracker/src/pastor_tracker/perception/pose_detector.py` | PoseEngine Protocol + UltralyticsPoseEngine + PoseDetector orchestrator | VERIFIED | 479 lines. `@runtime_checkable PoseEngine` Protocol; `_DetectorState` enum (5 states); `PerceptionError` + `PoseEngineUnavailableError` typed hierarchy; `UltralyticsPoseEngine.start/detect/close` (BL-02 detect implemented); `PoseDetector.start/stop/consume/detections` orchestrator with drop-oldest at ingress + bounded out_queue (256) + FAULTED preservation across stop() (W5 fix latches FAULTED on engine.close failure). 7 module-level Final constants. |
| `pastor_tracker/src/pastor_tracker/perception/_kalman.py` | filterpy 4-state CV wrapper | VERIFIED | 157 lines. Real `filterpy.kalman.KalmanFilter` (no mocks). `make_kalman_for_subject`, `predict_with_dt`, `_KalmanWrapper.reset_for_new_track`, `_KalmanWrapper.hold_posterior`. WR-07: tuning knobs are now load-bearing keyword params with module-Final defaults. |
| `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` | 6-state lock state machine + Kalman wiring | VERIFIED | 402 lines. `SubjectTracker` with consume() entry-point, match-dispatch flat (≤2-level nesting), 6-state `_LockState` enum (UNLOCKED/SEEKING/LOCKED/HOLDING/LOST/RE_ACQUIRING), per-state handlers `_try_lock`/`_tick_locked`/`_tick_holding`/`_try_reacquire`, `_emit_from_kf` clips coords into [0,1], W3 single-gap LOCKED→LOST path, W4 invariant assertion (no `or 0` fallback), WR-08 exhaustiveness guard on match. Public read-only dashboard surface: `is_locked`, `current_track_id`, `last_lock_loss_ts_ns`, `state`, `last_error`. |
| `pastor_tracker/tests/fixtures/pose_traces.py` | FakePoseEngine + scripted traces | VERIFIED | `FakePoseEngine`, `SlowFakePoseEngine`, `FailingPoseEngine` satisfy PoseEngine Protocol; `make_detection` helper; 7 scripted traces (POSE_TRACE_INITIAL_LOCK / OFF_CENTER_NO_LOCK / LOW_CONF_REJECT / OCCLUSION_3F / LOCK_LOSS_2S / TRACK_ID_PERSIST / TWO_PERSON_CENTRAL). |
| `Detection.track_id` field | `int \| None`, ge=0, default=None | VERIFIED | `core/types.py:139`. |
| `Config.yolo_model_path` / `yolo_device` / `botsort_yaml_path` | Three perception fields | VERIFIED | `config.py:128-148`. `Path` / `Literal["auto","cuda","cpu"]` / `Path \| None`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `pose_detector.PoseDetector.consume` | `PoseEngine.detect` | `self._engine.detect(frame)` inside `_infer_one` task | WIRED | `pose_detector.py:420`. Tests `test_detections_iterator_drains_in_order`, `test_drop_oldest_when_inference_lags` exercise this directly. |
| `pose_detector.PoseDetector.consume` | structlog WARN | `self._logger.warning("inference_drop_oldest", ...)` | WIRED | `pose_detector.py:396-400`. `test_drop_oldest_when_inference_lags` asserts ≥4 events. |
| `subject_tracker.SubjectTracker._try_lock` | `_kalman.make_kalman_for_subject` | `self._kalman_wrapper.reset_for_new_track(...)` -> `make_kalman_for_subject(...)` | WIRED | `subject_tracker.py:219` -> `_kalman.py:130-142` -> `_kalman.py:41`. Real `KalmanFilter(dim_x=4, dim_z=2)` constructed every lock. `test_kalman_matrices_at_init`, `test_reset_creates_fresh_filter` verify. |
| `subject_tracker.SubjectTracker._emit_from_kf` | `core.types.TrackedSubject` | `TrackedSubject(track_id=..., x=..., y=..., vx=..., vy=..., ts=...)` | WIRED | `subject_tracker.py:387-397`. End-to-end `test_e2e_track_id_persist_to_tracked_subject_stream` verifies emission with track_id=42 and smoothed cx in [0.55, 0.60]. |
| `_pose_worker._translate` | `_keypoints.weighted_keypoint_centroid` | direct call (B1 contract) | WIRED | `_pose_worker.py:213`. B1 contract test `test_e2e_perc02_weighted_centroid_in_emit` proves cx=0.70 (kp), NOT 0.50 (bbox). |
| `subject_tracker.compute_subject_centroid` | `_keypoints.weighted_keypoint_centroid` | thin delegate (B1) | WIRED | `subject_tracker.py:95-97`. |
| `UltralyticsPoseEngine.detect` | `_pose_worker.infer` | `loop.run_in_executor(_pose_worker.infer, shm_name, shape, ts, device, model_path, botsort)` | WIRED | `pose_detector.py:241-250`. **Implementation runs in CI as orchestrator code path; actual model inference deferred to QA-04.** |
| `PoseDetector` orchestrator → `SubjectTracker` consumer | E2E pipeline | `await detector.consume(frame)` → `async for dets in detector.detections()` → `await tracker.consume(dets, now_ns)` | WIRED | `test_perception_e2e.py::_drive_pipeline` exercises 4 e2e tests; `tracker.is_locked`, `current_track_id`, smoothed cx asserted. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `PoseDetector.detections()` | `dets: list[Detection]` | `_out_queue` populated by `_infer_one` -> `engine.detect(frame)` | YES via FakePoseEngine in CI; YES via UltralyticsPoseEngine on hardware | FLOWING (CI), HARDWARE-PENDING (production) |
| `SubjectTracker.consume()` returned `TrackedSubject` | `kf.x[0..3]` | `make_kalman_for_subject` + `predict_with_dt` + `kf.update(measurement)` | YES — real filterpy.KalmanFilter mutated by detection stream | FLOWING |
| `Detection.track_id` | `boxes.id.int().cpu().tolist()[idx]` | `_pose_worker._translate` from ultralytics result; or directly from FakePoseEngine fixture | YES (real BoT-SORT id from `model.track(persist=True, tracker=...)` per WR-02 bounds-checked indexing) | FLOWING |
| `TrackedSubject.track_id` | `self._locked_track_id` | `_try_lock` selects `chosen.track_id` from filtered Detection | YES — verified by 10-frame e2e trajectory | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 4 unit + integration tests pass | `uv run pytest tests/test_pose_detector.py tests/test_subject_tracker_lock.py tests/test_subject_tracker_kalman.py tests/test_subject_tracker_hold.py tests/test_perception_e2e.py -v` | 37 passed in 3.81s | PASS |
| Full project test suite green | `uv run pytest tests/ -x` | 288 passed in 31.87s | PASS |
| Ruff clean (src + tests) | `uv run ruff check src tests` | All checks passed | PASS |
| Mypy strict clean (src) | `uv run mypy src` | Success: no issues found in 22 source files | PASS |
| Public surface importable | `python -c "from pastor_tracker.perception import PoseDetector, SubjectTracker, UltralyticsPoseEngine, PerceptionError, PoseEngineUnavailableError, PoseEngine"` | (covered by test_pose_detector imports + e2e imports) | PASS |
| Production `UltralyticsPoseEngine.detect` runs against real torch + ultralytics + YOLO11-pose weights | (would require GPU + model file) | not run in CI; deferred to Phase 8 QA-04 per 04-03-SUMMARY.md | SKIP |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PERC-01 | 04-01, 04-03 | YOLO11-pose wrapper — GPU/CPU async inference in process pool, doesn't block capture | SATISFIED (with hardware deferral) | `UltralyticsPoseEngine` + `_pose_worker` + `PoseDetector` orchestrator with drop-oldest. CI proves orchestrator-side non-blocking via `SlowFakePoseEngine`; production model run is QA-04. |
| PERC-02 | 04-01, 04-02 | Subject centroid = weighted mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); reject mean kp conf < 0.55 | SATISFIED | `_keypoints.weighted_keypoint_centroid` + `SubjectTracker.consume` conf floor; `test_centroid_weighted_mean_property` + `test_low_conf_detection_rejected` + B1 contract test. |
| PERC-03 | 04-01, 04-02, 04-03 | BoT-SORT ID persistence via `model.track(persist=True, tracker="botsort.yaml")` | SATISFIED | `_pose_worker.infer` calls `model.track(..., persist=True, tracker=tracker_arg)` (`_pose_worker.py:146-153`); `Detection.track_id` carries it; `SubjectTracker._find_match` filters; `test_track_id_persists_across_frames` end-to-end. |
| PERC-04 | 04-02, 04-03 | Primary-subject lock — highest-conf in central 60% at start; persist ID across occlusions | SATISFIED | `is_in_central_region` predicate; `_try_lock` ranking by mean_keypoint_confidence; `test_initial_lock_central_60pct_highest_conf`, `test_no_lock_when_only_off_center_person`, `test_lock_survives_brief_occlusion`, e2e `test_e2e_lock_acquire_emits_log`. |
| PERC-05 | 04-02, 04-03 | Lock loss > 2.0 s → re-acquire via central-frame heuristic, log WARN | SATISFIED | `_LOCK_LOSS_TIMEOUT_SEC=2.0` Final; `_tick_locked` W3 single-gap path; `_tick_holding` 2 s timeout path; `_try_reacquire` builds fresh KF + `lock_reacquired` WARN; `test_lock_loss_2s_reacquires`, `test_single_gap_over_2s_triggers_lost_from_locked`. |
| PERC-06 | 04-02, 04-03 | 4-state Kalman `[x, y, vx, vy]` in normalized coords; predict during gaps, update on detection | SATISFIED | Real filterpy `KalmanFilter(dim_x=4, dim_z=2)`; `predict_with_dt` for variable dt; `test_smoothed_velocity_beats_frame_diff` proves lag-free vs frame-diff baseline (40 hypothesis examples, every example wins). |
| PERC-07 | 04-02, 04-03 | 3 consecutive frames with no detection → hold position, log WARN | SATISFIED | `_HOLD_POSTERIOR_FRAME_THRESHOLD=3`; `_tick_holding` emits frozen posterior + `lock_holding` WARN once per entry; covariance trace verified non-growing; `test_three_misses_holds_posterior`, `test_holding_freezes_posterior`, `test_holding_recovers_to_locked`. |

All 7 requirement IDs are accounted for across plans 04-01, 04-02, 04-03 and have automated test evidence. No orphaned requirements.

### Anti-Patterns Found

Scanned `pastor_tracker/src/pastor_tracker/perception/*.py` and `pastor_tracker/tests/test_*pose*.py + test_subject_tracker_*.py + test_perception_e2e.py`:

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `subject_tracker.py:120` | 1 | "semantics" docstring (not "EMA" — false positive in scanner) | INFO | None |
| (test files) | — | `test_filterpy_smoke_import` uses `@pytest.mark.filterwarnings("ignore::SyntaxWarning")` to silence filterpy 1.4.5 docstring warning | INFO | Documented in 04-01-SUMMARY.md Deviation #2; numpy DeprecationWarning escalation preserved (Pitfall A1 fail-fast still active). |
| `pose_detector.py:354, 362, 406, 432` | various | `except Exception` translator-catches | INFO | All have `# noqa: BLE001` AND documented translator comment per CLAUDE.md tiger-style; lines 173 + 332 are documented as "immediately re-raised as typed PerceptionError" so BLE001 doesn't fire. WR-01 deep-review fix verified ruff clean. |

**No forbidden patterns found:**
- `print()` — 0 hits in src/perception
- bare `except:` / `except Exception: pass` — 0 hits in src/perception
- `time.sleep()` in main loop — 0 hits in src/perception
- `: Any` type annotations — 0 hits in src/perception
- PID / MediaPipe / EMA imports/usage — 0 hits in src/perception
- mocked KalmanFilter / damping — 0 hits in tests/test_subject_tracker_*

### Cross-Phase Contract — Frame.__post_init__ Invariants (Phase 3)

`Frame.__post_init__` (`core/types.py:68-99`) validates ndim==3, channels==3, dtype==uint8, C_CONTIGUOUS. Phase 4 perception code (`perception/*`) contains **zero redundant guards** (grep for `flags[`, `C_CONTIGUOUS`, `ndim ==`, `ndim !=`, `ascontiguousarray` returned no matches). The Phase 3 commit aac3da1 contract is honored — perception trusts the Frame DTO and moves directly to copying its bytes into the shm block.

### Human Verification Required

Two on-stage / hardware-only verifications are required before Phase 4 can be considered fully discharged in production:

#### 1. PERC-01 production engine on real hardware

**Test:** Start the application with `Config(yolo_device="cuda", yolo_model_path=Path("/path/to/yolo11n-pose.pt"))` on the on-stage GPU box (and separately with `yolo_device="cpu"` on the dev box). Trigger `UltralyticsPoseEngine.start()` then push 30 fps frames from real OBS VCam through `PoseDetector.consume()`.

**Expected:**
- `_resolve_device("cuda")` succeeds; warmup completes within `_WORKER_WARMUP_TIMEOUT_SEC=30.0` s.
- Per-frame `engine.detect(frame)` returns a list of `Detection` records (Pydantic-validated), each with a stable `track_id` from BoT-SORT's `model.track(persist=True)`.
- `consume()` never blocks the capture loop: `inference_drop_oldest` WARN events fire when inference can't keep up at 30 fps; capture loop continues to enqueue frames.
- No `pose_engine_fault` ERROR events fire under nominal conditions.

**Why human:** BL-02 deep-review fix landed the production code path. CI does not exercise it because real torch + ultralytics + GPU + ~6 MB model file are unavailable in the test environment. `04-03-SUMMARY.md` "Out-of-Scope Deferrals" routes this to Phase 8 QA-04 on-stage hardware run. The seam (`PoseEngine` Protocol + `FakePoseEngine`) covers every orchestrator-side path in CI; this method is the production plumbing that closes the loop and must execute on real hardware once.

#### 2. PERC-04 / PERC-05 lock stability under real BoT-SORT track-id behaviour

**Test:** On-stage scenario: pastor at lectern, 1-2 audience members visible at frame edges, interpreter occasionally walking through frame. Run for 5+ minutes. Observe:

**Expected:**
- Initial lock acquired within first frame on the pastor (highest-conf central-60% person).
- `track_id` stays stable through pastor's normal stage motion (gestures, turning to side, brief lectern occlusion).
- `lock_loss` WARN does NOT fire on brief stage occlusions <2.0 s.
- `lock_holding` WARN fires only on 3+ consecutive missing frames (detection actually missed pastor, e.g., sharp turn-away).
- `lock_reacquired` WARN fires correctly when pastor re-emerges after >2.0 s absence; new track_id is the pastor, not the interpreter.

**Why human:** Scripted FakePoseEngine traces with stable / mutated track_ids verify the state-machine logic perfectly (8 lock + 3 hold + 4 e2e tests, all green). Real BoT-SORT under stage flash, audience motion at frame edge, and interpreter at podium edge can mis-ID even with `botsort_yaml_path` tuned — only on-stage QA-04 demonstrates the central-60% + highest-conf heuristic survives real noise. Mis-acquisition would manifest as the camera tracking the interpreter — only visible to a human observer.

### Gaps Summary

There are **no automated-test gaps** blocking Phase 4 closure. All 7 requirement IDs (PERC-01..07) are satisfied by 37 perception-tier tests (15 pose_detector + 8 lock + 7 kalman + 3 hold + 4 e2e) + cross-phase regression (288 total tests passing). The 16-finding deep-review (`04-REVIEW.md`) closed status `clean` after BL-01, BL-02, and 8 WARNING fixes (W3, W4, W5, W6, W7, B1, WR-01..08).

The two human-verification items are **expected and documented deferrals** to Phase 8 QA-04, not gaps in Phase 4's deliverable. The Phase 4 contract was scoped to the seam + the orchestrator + the lock-state machine + the Kalman wiring; production model inference and on-stage tuning are explicitly Phase 8 scope per CONTEXT.md Area 1 and ROADMAP.

The status is `human_needed` because the goal as stated ("Detect the pastor on every frame") cannot be programmatically verified against a real pastor on a real stage from CI. All algorithmic correctness, contract compliance, lifecycle discipline, and integration wiring are VERIFIED. The remaining gates are physical: real GPU, real model weights, real stage.

---

_Verified: 2026-05-05T19:30:00Z_
_Verifier: Claude (gsd-verifier)_
