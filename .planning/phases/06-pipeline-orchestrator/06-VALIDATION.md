---
phase: 6
slug: pipeline-orchestrator
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-10
---

# Phase 6 — Validation Strategy

> Per-phase validation contract. Substance lives in `06-RESEARCH.md` §"Validation Architecture" (lines 809–853); this file is the canonical Nyquist artifact.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest` 8.x (existing repo config; Phase 5 ship-gate baseline 370 passed) |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/test_pipeline.py -x -v` |
| **Full suite command** | `uv run pytest` |
| **Coverage command** | `uv run pytest --cov=src/pastor_tracker tests/test_pipeline.py --cov-report=term-missing` |
| **Estimated runtime** | ~5 sec (pipeline tests), ~30 sec (full suite) |

---

## Sampling Rate

- **After every task commit:** `uv run pytest tests/test_pipeline.py -x`
- **After every plan wave:** `uv run pytest`
- **Before `/gsd-verify-work`:** Full suite green AND `uv run mypy --strict src` AND `uv run ruff check src tests`
- **Max feedback latency:** ~5 sec per task

---

## Per-Task Verification Map

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| PIPE-01 | Pipeline constructs with 8 stages from one Config; tick loop progresses; dispatcher emits expected count | integration | `pytest tests/test_pipeline.py::test_tick_loop_drives_all_stages -x` | ❌ Wave 2 |
| PIPE-01 | `latest_frame` slot updates each tick (D-17) | integration | `pytest tests/test_pipeline.py::test_latest_frame_slot_updates -x` | ❌ Wave 2 |
| PIPE-01 | `snapshot()` returns frozen DTO with all 7 fields populated (D-16) | integration | `pytest tests/test_pipeline.py::test_snapshot_dto_complete -x` | ❌ Wave 2 |
| PIPE-02 | Tick body accesses ONLY `consume()` / `decide()` / `Frame.timestamp_ns` | static | `grep` audit + structural review | manual |
| PIPE-03 | All 6 lifecycle methods work on valid source states | integration | `pytest tests/test_pipeline.py::test_lifecycle_valid_transitions -x` | ❌ Wave 2 |
| PIPE-03 | Invalid transitions raise `OrchestratorRejected` (D-15) | integration | `pytest tests/test_pipeline.py::test_lifecycle_invalid_transitions_rejected -x` | ❌ Wave 2 |
| PIPE-03 | `e_stop()` completes within 200 ms even with slow detector (D-07) | integration (timing) | `pytest tests/test_pipeline.py::test_e_stop_completes_within_heartbeat_budget -x` | ❌ Wave 2 |
| PIPE-03 | `home()` rejected when 0° outside `[pan_min, pan_max]` (D-08) | integration | `pytest tests/test_pipeline.py::test_home_rejected_when_zero_out_of_limits -x` | ❌ Wave 2 |
| PIPE-03 | `quit()` is idempotent (D-09) | integration | `pytest tests/test_pipeline.py::test_quit_idempotent -x` | ❌ Wave 2 |
| D-06 | `pause()` keeps tick running but suppresses motor sends | integration | `pytest tests/test_pipeline.py::test_pause_suppresses_motor_send_but_tick_continues -x` | ❌ Wave 2 |
| D-10 | `CameraOpenError` propagates; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_camera_open_error_propagates -x` | ❌ Wave 2 |
| D-11 | `WatchdogResetError` recovered in-place by motor; tick continues | integration | `pytest tests/test_pipeline.py::test_watchdog_reset_recovered_pipeline_continues -x` | ❌ Wave 2 |
| D-12 | `LinkLostError` propagates; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_arduino_link_lost_propagates -x` | ❌ Wave 2 |
| D-13 | Detector exception propagates; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_perception_failure_propagates -x` | ❌ Wave 2 |
| D-14 | Unknown exception in tick logged + propagated | integration | `pytest tests/test_pipeline.py::test_tick_loop_unknown_exception_propagates -x` | ❌ Wave 2 |
| `__main__` | SIGINT triggers clean `quit()` (Shape A: EXIT_HARDWARE_FAILED proxy) | integration | `pytest tests/test_pipeline.py::test_main_sigint_clean_shutdown -x` | ❌ Wave 2 |
| `__main__` | Invalid Config exits with `EXIT_INVALID_CONFIG` | integration | `pytest tests/test_pipeline.py::test_main_invalid_config_exit_code -x` | ❌ Wave 2 |

---

## Wave 0 Requirements

- [ ] `tests/test_pipeline.py` — covers PIPE-01..03 and D-05..D-14 (~17 tests). Created in Plan 06-04 (Wave 2).
- [ ] (Optional) `tests/fixtures/pipeline_helpers.py` — `_pipeline_with_fakes(...)` helper. Created in Plan 06-04.
- [ ] (Pre-Wave 1) Confirm or add `PoseDetector.stream(frames)` per RESEARCH OQ1 — handled in Plan 06-01 (Wave 0).
- [ ] No new framework / fixture modules; all four upstream fakes already exist.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Smooth pan from real Uno + real OBS + real speaker | PIPE-01 (success criterion #1) | Requires hardware + stage | Run `python -m pastor_tracker` against live Uno + OBS VCam; visually confirm cinematic pan with no jerk/overshoot. Phase 8 / QA-04. |
| True SIGINT-mid-tick clean shutdown | PIPE-03 (success criterion #3) | Subprocess-with-real-hardware path; Shape A in-process EXIT_HARDWARE_FAILED proxy ships in CI | Phase 8 / QA-04 on-stage smoke test. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or are flagged Manual / Wave 2
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (PoseDetector.stream helper)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-05-10 (substance ratified in 06-RESEARCH.md §Validation Architecture, planner-checker confirmed all 17 tests wired into 06-04 acceptance criteria).
