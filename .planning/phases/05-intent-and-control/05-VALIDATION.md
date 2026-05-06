---
phase: 5
slug: intent-and-control
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-06
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest >=8.4,<9.0` + `pytest-asyncio >=0.26,<2.0` + `hypothesis >=6.152,<7.0` |
| **Config file** | `pastor_tracker/pyproject.toml [tool.pytest.ini_options]` (testpaths=["tests"], asyncio_mode="auto", strict-markers, strict-config, filterwarnings=error) |
| **Quick run command** | `cd pastor_tracker && uv run pytest tests/test_motion_analyzer.py tests/test_framer.py tests/test_pan_controller.py tests/test_command_dispatcher.py -x` |
| **Full suite command** | `cd pastor_tracker && uv run pytest -x --strict-markers` |
| **Coverage command** | `cd pastor_tracker && uv run pytest --cov=src/pastor_tracker/intent --cov=src/pastor_tracker/control --cov-branch --cov-report=term-missing` |
| **Estimated runtime** | ~10 seconds (pure-core math, no I/O, no GPU) |

---

## Sampling Rate

- **After every task commit:** Run quick run command above
- **After every plan wave:** Run full suite command above
- **Before `/gsd-verify-work`:** Full suite must be green AND coverage ≥ targets
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-W0-01 | W0 | 0 | TEST-03 | — | trajectory fixtures pure helpers | unit | `uv run pytest tests/fixtures/test_trajectories_self.py -x` (or import-only smoke if no self-test) | ❌ W0 | ⬜ pending |
| 5-02-01 | 02 | 1 | INTENT-01 | V11 | sustained right-bound classifies after motion_hysteresis_sec | unit | `uv run pytest tests/test_motion_analyzer.py::test_sustained_right_flips_intent -x` | ❌ W0 | ⬜ pending |
| 5-02-02 | 02 | 1 | INTENT-01 | V11 | sustained left-bound classifies after motion_hysteresis_sec | unit | `uv run pytest tests/test_motion_analyzer.py::test_sustained_left_flips_intent -x` | ❌ W0 | ⬜ pending |
| 5-02-03 | 02 | 1 | INTENT-01 | V11 | sustained dwell classifies after dwell_duration_sec | unit | `uv run pytest tests/test_motion_analyzer.py::test_sustained_dwell_flips_intent -x` | ❌ W0 | ⬜ pending |
| 5-02-04 | 02 | 1 | INTENT-02 | V11 | borderline chatter never flips intent (hypothesis property) | property | `uv run pytest tests/test_motion_analyzer.py::test_borderline_chatter_no_thrash -x` | ❌ W0 | ⬜ pending |
| 5-02-05 | 02 | 1 | INTENT-02 | V11 | threshold un-cross resets crossing timer | unit | `uv run pytest tests/test_motion_analyzer.py::test_un_cross_resets_timer -x` | ❌ W0 | ⬜ pending |
| 5-02-06 | 02 | 1 | INTENT-02 | V11 | None upstream resets all timers + emits indeterminate | unit | `uv run pytest tests/test_motion_analyzer.py::test_none_upstream_emits_indeterminate -x` | ❌ W0 | ⬜ pending |
| 5-03-01 | 03 | 1 | INTENT-03 | V11 | moving_right → target 0.333 (left third) | unit | `uv run pytest tests/test_framer.py::test_moving_right_yields_left_third -x` | ❌ W0 | ⬜ pending |
| 5-03-02 | 03 | 1 | INTENT-03 | V11 | moving_left → target 0.667 (right third) | unit | `uv run pytest tests/test_framer.py::test_moving_left_yields_right_third -x` | ❌ W0 | ⬜ pending |
| 5-03-03 | 03 | 1 | INTENT-03 | V11 | dwelling → target 0.5 (center) | unit | `uv run pytest tests/test_framer.py::test_dwelling_yields_center -x` | ❌ W0 | ⬜ pending |
| 5-03-04 | 03 | 1 | INTENT-03 | V11 | indeterminate → no target / held | unit | `uv run pytest tests/test_framer.py::test_indeterminate_holds_or_returns_none -x` | ❌ W0 | ⬜ pending |
| 5-03-05 | 03 | 1 | INTENT-04 | V11 | step-response damped by stage-1 τ; no overshoot, monotonic | unit | `uv run pytest tests/test_framer.py::test_step_response_no_overshoot -x` | ❌ W0 | ⬜ pending |
| 5-03-06 | 03 | 1 | INTENT-04 | V11 | damper state held during None upstream | unit | `uv run pytest tests/test_framer.py::test_hold_during_none_upstream -x` | ❌ W0 | ⬜ pending |
| 5-04-01 | 04 | 2 | CTRL-01 | V11 | step-response damped by stage-2 τ; no overshoot | unit | `uv run pytest tests/test_pan_controller.py::test_step_response_no_overshoot -x` | ❌ W0 | ⬜ pending |
| 5-04-02 | 04 | 2 | CTRL-01 | V11 | FOV conversion: target=0.5 maps to angle=0.0 | unit | `uv run pytest tests/test_pan_controller.py::test_center_target_maps_to_zero_angle -x` | ❌ W0 | ⬜ pending |
| 5-04-03 | 04 | 2 | CTRL-02 | V11 | small delta returns previous emitted angle | unit | `uv run pytest tests/test_pan_controller.py::test_deadband_suppresses_small_changes -x` | ❌ W0 | ⬜ pending |
| 5-04-04 | 04 | 2 | CTRL-02 | V11 | damper continues stepping during deadband (catch-up smoothness) | unit | `uv run pytest tests/test_pan_controller.py::test_deadband_does_not_freeze_damper -x` | ❌ W0 | ⬜ pending |
| 5-04-05 | 04 | 2 | CTRL-03 | V11 | per-step delta ≤ pan_max_velocity_deg_per_sec * dt | unit | `uv run pytest tests/test_pan_controller.py::test_velocity_clamp_caps_step_size -x` | ❌ W0 | ⬜ pending |
| 5-04-06 | 04 | 2 | CTRL-03 | V11 | after clamp, FollowerState.position == clamped (anti-windup) | unit | `uv run pytest tests/test_pan_controller.py::test_velocity_clamp_overwrites_state -x` | ❌ W0 | ⬜ pending |
| 5-05-01 | 05 | 2 | CTRL-04 | V11 | Δ ≤ command_min_delta_deg → no emission | unit | `uv run pytest tests/test_command_dispatcher.py::test_small_delta_suppressed -x` | ❌ W0 | ⬜ pending |
| 5-05-02 | 05 | 2 | CTRL-04 | V11 | interval < command_min_interval_ms → no emission even if Δ large | unit | `uv run pytest tests/test_command_dispatcher.py::test_short_interval_suppressed -x` | ❌ W0 | ⬜ pending |
| 5-05-03 | 05 | 2 | CTRL-04 | V11 | first call always emits | unit | `uv run pytest tests/test_command_dispatcher.py::test_first_call_always_emits -x` | ❌ W0 | ⬜ pending |
| 5-05-04 | 05 | 2 | CTRL-04 | V11 | None upstream returns None and does NOT update state | unit | `uv run pytest tests/test_command_dispatcher.py::test_none_does_not_update_state -x` | ❌ W0 | ⬜ pending |
| 5-05-05 | 05 | 2 | CTRL-04 | V11 | emission count over synthetic ramp ≤ analytic bound | unit | `uv run pytest tests/test_command_dispatcher.py::test_ramp_emission_count_bounded -x` | ❌ W0 | ⬜ pending |
| 5-06-01 | 06 | 3 | TEST-03 | V11 | end-to-end smoke: trajectory → analyzer → framer → pan → dispatcher | integration | `uv run pytest tests/test_intent_control_pipeline.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pastor_tracker/tests/fixtures/trajectories.py` — pure helpers `step`, `ramp`, `dwell_then_walk`, `borderline_chatter` returning `list[TrackedSubject]` parametrized by `Config` (no hardcoded thresholds)
- [ ] `pastor_tracker/tests/test_motion_analyzer.py` — covers INTENT-01, INTENT-02; uses real `MotionAnalyzer` + scripted trajectories; `Config` fixture parametrizes thresholds
- [ ] `pastor_tracker/tests/test_framer.py` — covers INTENT-03, INTENT-04; uses real `CriticallyDampedFollower` (no mocks)
- [ ] `pastor_tracker/tests/test_pan_controller.py` — covers CTRL-01, CTRL-02, CTRL-03; uses real `CriticallyDampedFollower` + real `normalized_x_to_angle_deg`
- [ ] `pastor_tracker/tests/test_command_dispatcher.py` — covers CTRL-04
- [ ] `pastor_tracker/tests/test_intent_control_pipeline.py` — composition smoke (recommended per RESEARCH Open Question 5)

Framework install: not needed — `pytest`, `pytest-asyncio`, `hypothesis` already pinned in `pastor_tracker/pyproject.toml`; `uv sync` fetches them.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none) | — | All Phase 5 behavior is pure-core deterministic math; every requirement has automated coverage above | — |

*All phase behaviors have automated verification.*

---

## Coverage Targets

- **Hysteresis classifier branches** (`motion_analyzer.py`): 100% line + 100% branch
- **Deadband + velocity-clamp branches** (`pan_controller.py`): 100% line + 100% branch
- **Dispatcher gate** (`command_dispatcher.py`): 100% line + 100% branch
- **Framer + PanController overall**: ≥ 90% line
- **Verified by:** `uv run pytest --cov=src/pastor_tracker/intent --cov=src/pastor_tracker/control --cov-branch --cov-report=term-missing`

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (6 new test files)
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter (after Wave 0 lands and tests pass)

**Approval:** pending
