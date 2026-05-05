---
phase: 4
slug: perception
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-05
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.4 + pytest-asyncio (`asyncio_mode = "auto"`) + hypothesis 6.152 (already configured per `pastor_tracker/pyproject.toml`) |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `cd pastor_tracker && uv run pytest tests/test_pose_detector.py tests/test_subject_tracker_lock.py tests/test_subject_tracker_kalman.py tests/test_subject_tracker_hold.py -x` |
| **Full suite command** | `cd pastor_tracker && uv run pytest` |
| **Estimated runtime** | ~10 seconds (Phase 4 tests) / ~30 seconds (full suite after Phase 4 lands) |

---

## Sampling Rate

- **After every task commit:** Run quick command above
- **After every plan wave:** Run full suite command above
- **Before `/gsd-verify-work`:** Full suite must be green AND coverage ≥ 90 % on `subject_tracker.py` + `pose_detector.py`; 100 % on lock-acquisition + Kalman-update branches
- **Max feedback latency:** ~10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | PERC-01 | T-04-V5 | `Config.yolo_model_path` = validated `FilePath`; `Config.yolo_device` = `Literal["auto","cuda","cpu"]` | unit | `pytest tests/test_config.py::test_yolo_fields_validation -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | PERC-01 | — | PoseEngine Protocol satisfied by FakePoseEngine | unit (DI seam) | `pytest tests/test_pose_detector.py::test_pose_engine_protocol_compliance -x` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | PERC-01 | — | shared_memory roundtrip preserves ndarray bytes | unit (real shm) | `pytest tests/test_pose_detector.py::test_shared_memory_ndarray_roundtrip -x` | ❌ W0 | ⬜ pending |
| 04-01-04 | 01 | 1 | PERC-01 | — | filterpy.kalman.KalmanFilter imports + predict() under numpy 2.4.x | smoke | `pytest tests/test_subject_tracker_kalman.py::test_filterpy_smoke_import -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 2 | PERC-02 | — | Centroid weighted mean (nose 0.4, shoulder 0.4, hip 0.2) computes correct value | property (hypothesis) | `pytest tests/test_subject_tracker_lock.py::test_centroid_weighted_mean_property -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 2 | PERC-02 | — | mean kp conf < 0.55 → detection rejected | unit | `pytest tests/test_subject_tracker_lock.py::test_low_conf_detection_rejected -x` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 2 | PERC-03 | — | BoT-SORT track_id flows through Detection → TrackedSubject | unit (FakePoseEngine scripted ids) | `pytest tests/test_subject_tracker_lock.py::test_track_id_persists_across_frames -x` | ❌ W0 | ⬜ pending |
| 04-02-04 | 02 | 2 | PERC-04 | — | At t=0, highest-conf central-60% person locks | unit | `pytest tests/test_subject_tracker_lock.py::test_initial_lock_central_60pct_highest_conf -x` | ❌ W0 | ⬜ pending |
| 04-02-05 | 02 | 2 | PERC-04 | — | Person outside central 60% does NOT lock initially | unit | `pytest tests/test_subject_tracker_lock.py::test_no_lock_when_only_off_center_person -x` | ❌ W0 | ⬜ pending |
| 04-02-06 | 02 | 2 | PERC-04 | — | Lock survives BoT-SORT id continuity through brief occlusion | unit (FakePoseEngine scripted gap) | `pytest tests/test_subject_tracker_lock.py::test_lock_survives_brief_occlusion -x` | ❌ W0 | ⬜ pending |
| 04-02-07 | 02 | 2 | PERC-05 | — | Lock loss > 2.0 s triggers re-acquisition + WARN | unit (clock-injected) | `pytest tests/test_subject_tracker_lock.py::test_lock_loss_2s_reacquires -x` | ❌ W0 | ⬜ pending |
| 04-02-08 | 02 | 2 | PERC-06 | — | KalmanFilter F, H, Q, R, P, x correctly initialized | unit (real filterpy — no mock) | `pytest tests/test_subject_tracker_kalman.py::test_kalman_matrices_at_init -x` | ❌ W0 | ⬜ pending |
| 04-02-09 | 02 | 2 | PERC-06 | — | predict + update on linear-trajectory test recovers velocity within tolerance | property (hypothesis) | `pytest tests/test_subject_tracker_kalman.py::test_linear_trajectory_velocity_recovery -x` | ❌ W0 | ⬜ pending |
| 04-02-10 | 02 | 2 | PERC-06 | — | Smoothed vx is closer to truth than raw frame-diff (CLAUDE.md "test real implementations") | property | `pytest tests/test_subject_tracker_kalman.py::test_smoothed_velocity_beats_frame_diff -x` | ❌ W0 | ⬜ pending |
| 04-02-11 | 02 | 2 | PERC-06 | — | Variable dt between frames does not break filter | unit | `pytest tests/test_subject_tracker_kalman.py::test_variable_dt_handled -x` | ❌ W0 | ⬜ pending |
| 04-02-12 | 02 | 2 | PERC-06 | — | Re-acquisition creates a fresh KalmanFilter (no state leak) | unit | `pytest tests/test_subject_tracker_kalman.py::test_reset_creates_fresh_filter -x` | ❌ W0 | ⬜ pending |
| 04-02-13 | 02 | 2 | PERC-07 | — | 3-consecutive-no-detection triggers HOLDING + WARN | unit | `pytest tests/test_subject_tracker_hold.py::test_three_misses_holds_posterior -x` | ❌ W0 | ⬜ pending |
| 04-02-14 | 02 | 2 | PERC-07 | — | HOLDING does NOT advance Kalman predict (frozen output) | unit | `pytest tests/test_subject_tracker_hold.py::test_holding_freezes_posterior -x` | ❌ W0 | ⬜ pending |
| 04-02-15 | 02 | 2 | PERC-07 | — | HOLDING → LOCKED on next matching detection within 2.0 s | unit | `pytest tests/test_subject_tracker_hold.py::test_holding_recovers_to_locked -x` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 3 | PERC-01 | — | Process pool starts/stops cleanly without resource_tracker leak | integration | `pytest tests/test_pose_detector.py::test_executor_lifecycle_no_leak -x` | ❌ W0 | ⬜ pending |
| 04-03-02 | 03 | 3 | PERC-01 | — | Drop-oldest fires when inference lags capture (WARN logged) | unit (timing-injected) | `pytest tests/test_pose_detector.py::test_drop_oldest_when_inference_lags -x` | ❌ W0 | ⬜ pending |
| 04-03-03 | 03 | 3 | PERC-01..07 | — | End-to-end FakePoseEngine → PoseDetector → SubjectTracker stream | integration | `pytest tests/test_perception_e2e.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pastor_tracker/tests/fixtures/pose_traces.py` — `FakePoseEngine` + `make_detection_sequence(...)` helpers (mirror `arduino_traces.py` shape from Phase 2)
- [ ] `pastor_tracker/tests/test_pose_detector.py` — covers PERC-01 (shared_memory roundtrip + executor lifecycle + drop-oldest + Protocol seam)
- [ ] `pastor_tracker/tests/test_subject_tracker_lock.py` — covers PERC-02 + PERC-03 + PERC-04 + PERC-05 (lock state machine)
- [ ] `pastor_tracker/tests/test_subject_tracker_kalman.py` — covers PERC-06 (real filterpy property tests, no mocks per CLAUDE.md)
- [ ] `pastor_tracker/tests/test_subject_tracker_hold.py` — covers PERC-07 (HOLDING state freeze)
- [ ] `pastor_tracker/tests/test_perception_e2e.py` — end-to-end integration through FakePoseEngine
- [ ] Dependency add: `uv add ultralytics filterpy` (Wave 0 task in Plan 04-01)
- [ ] Config field add: `yolo_device: Literal["auto","cuda","cpu"] = "auto"`, `yolo_model_path: FilePath`, optional `botsort_yaml_path: Path | None = None`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real-stage BoT-SORT track_high_thresh tuning | PERC-03..05 | Tuning depends on actual stage lighting / pastor distance / camera FOV — synthetic FakePoseEngine cannot reproduce flash photography or backlight | Deferred to Phase 8 / QA-04 stage smoke. Operator runs the full pipeline against the real OBS VCam during a service rehearsal; if track-id flickers, override `Config.botsort_yaml_path` with a tuned YAML. |
| YOLO11n-pose 1080p latency on dev box | PERC-01 | Hardware-dependent measurement | Wave 0 task: time `model.track(...)` over 100 frames on a representative laptop GPU/CPU; record p50/p95 in SUMMARY.md to confirm process-pool design is on-budget |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15 s
- [ ] Coverage gate set: ≥ 90 % line on `subject_tracker.py` + `pose_detector.py`; 100 % on lock-acquisition + Kalman-update branches
- [ ] No mocked Kalman / damping math anywhere (CLAUDE.md hard rule)
- [ ] `nyquist_compliant: true` set in frontmatter (set after planner verifies coverage)

**Approval:** pending
