---
phase: 05-intent-and-control
plan: 06
subsystem: testing
tags: [phase-5, intent-control, composition, smoke, integration, multi-stage, test-only]

# Dependency graph
requires:
  - phase: 05-intent-and-control
    provides: MotionAnalyzer (Plan 02 / Wave 1) + intent/__init__.py
  - phase: 05-intent-and-control
    provides: Framer (Plan 03 / Wave 1) + intent/__init__.py
  - phase: 05-intent-and-control
    provides: PanController (Plan 04 / Wave 2) + control/__init__.py
  - phase: 05-intent-and-control
    provides: CommandDispatcher (Plan 05 / Wave 3) + control/__init__.py
  - phase: 05-intent-and-control
    provides: trajectories.py fixtures (Plan 01) -- borderline_chatter / dwell_then_walk / ramp
provides:
  - Composition smoke test file -- 7 tests over the four-stage Phase 5 pipeline
  - _drive_pipeline helper (orchestrator-style now_ns = subject.timestamp_ns) -- Pitfall 5 parity
  - Cross-stage invariant proofs (zero emissions on borderline chatter; clamp invariant; analytic emission bound)
  - Phase 5 ship-gate evidence -- full pytest, ruff, mypy --strict all green save the pre-existing deferred test_geometry flake
affects: [06-pipeline-orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Test-only plan -- zero production-code changes. The composition test file imports four real production classes via the public package surfaces (pastor_tracker.intent / pastor_tracker.control); no internal symbols, no mocks, no stubs (CLAUDE.md TEST-05)."
    - "Same now_ns across all four stages on each tick: _drive_pipeline pulls now_ns = subject.timestamp_ns and threads it through analyzer.consume / framer.consume / controller.consume / dispatcher.decide -- mirrors the Phase-6 orchestrator's wall-clock-tick discipline (Pitfall 5)."
    - "asyncio.run per-stage per-frame: the three async stages each run inside their own asyncio.run inside the synchronous test body. Slower than a shared loop (each call spins up + tears down a ProactorEventLoop) but produces zero event-loop-leak warnings AND keeps the test body sync, matching the test_subject_tracker_lock.py precedent."
    - "D-12 numeric discipline: every threshold is read from valid_config_dict via float(...) / int(...) coercion. The only literal numbers in the test bodies are unit conversions (1/30, 1e-9, 1000) and structural multipliers (_BORDERLINE_FACTOR=1.05, _DWELL_MULTIPLIER=2.0, _ABOVE_THRESHOLD_FACTOR=2.0, etc.) -- all module-level Final constants with intent-revealing names."
    - "Cross-stage scope guard: the velocity_clamp test uses dwell_then_walk (NOT a pure ramp). With ramp from t=0, the analyzer flips to moving_right on the first hysteresis-satisfying frame and the framer's D-06 at-target seed pins the controller at the left-third angle for the rest of the run -- the clamp invariant would hold vacuously. dwell_then_walk forces a center seed first, then the walk phase drives a real target transition through both dampers, exercising the clamp on every post-flip frame. Documented inline in the test docstring."

key-files:
  created:
    - "pastor_tracker/tests/test_intent_control_pipeline.py -- 353 lines, 7 sync composition tests, 1 _drive_pipeline helper, 1 _make_pipeline helper"
  modified: []

key-decisions:
  - "dwell_then_walk over ramp for the velocity-clamp test. The plan's <action> block sketched a ramp-based clamp test; landed implementation switched to dwell_then_walk to avoid the D-06 at-target seed making the clamp invariant vacuously true. The plan's <behavior> assertion (per-step delta <= vmax*dt + 1e-9) is preserved exactly; only the trajectory was changed to make the test actually exercise the clamp. Real measured max delta in this run is 0.282 deg, well below the 1.000 deg/frame ceiling -- a meaningful safety margin, not a tautology."
  - "Test bodies are sync; async stages invoked via asyncio.run per call. test_subject_tracker_lock.py precedent. Avoids @pytest.mark.asyncio at the test-body level and keeps the orchestrator-style invocation pattern straightforward to read."
  - "Module-level Final constants for every numeric multiplier: _BORDERLINE_FACTOR=1.05, _ABOVE_THRESHOLD_FACTOR=2.0, _DWELL_MULTIPLIER=2.0, _PRE_SEED_VX_FACTOR=3.0, _HYSTERESIS_MARGIN_FACTOR=2.0, _FRAMING_SETTLE_FACTOR=5.0, _X_UPPER_CAP=0.99, _X_WALK_SAFETY_MARGIN=0.1, _EMISSION_BOUND_SLACK=2, _MS_PER_SEC=1000. CLAUDE.md rule 6 (no magic numbers in code) applied to test bodies as well; every multiplier is named for intent."
  - "Walk-window time-cap: ramp() and dwell_then_walk() both produce CONSTANT vx = (x_end - x_start) / total_sec. To guarantee the resulting vx exceeds motion_threshold, total_sec must be CAPPED so the geometric x-range divided by total_sec stays >= walk_vx. Computed as min(time_budget, span_max/walk_vx - safety_margin). Without this cap, a long total_sec would dilute the synthetic ramp's vx below threshold -- analyzer never flips, controller never seeds, the test fails on the 'len(non_none_angles) > 0' guard. Caught and fixed during initial test execution (Rule 1 inline fix)."

requirements-completed: [TEST-03]

# Metrics
duration: 12min
completed: 2026-05-06
---

# Phase 05 Plan 06: Composition Smoke Test (Phase 5 four-stage pipeline) Summary

## One-liner

Same-thread end-to-end test wiring `MotionAnalyzer + Framer + PanController + CommandDispatcher` through scripted trajectories under the orchestrator-style `consume()/decide()` pattern; proves cross-stage invariants (zero emissions on borderline chatter; framer center->third progression on dwell_then_walk; per-step controller delta <= `vmax*dt`; emission count <= analytic bound) that no single-stage unit test can prove.

## What Was Built

### Files Created

| File | Purpose | Lines | Tests |
|------|---------|-------|-------|
| `pastor_tracker/tests/test_intent_control_pipeline.py` | Phase 5 composition smoke tests (TEST-03 cross-stage portion) | 353 | 7 |

### Test Coverage Map

| Test | Scenario | Invariant Asserted |
|------|----------|--------------------|
| `test_pipeline_constructible_from_single_config` | Build all four stages from one Config | Initial-state properties match contract (T-05-06-04) |
| `test_borderline_chatter_produces_no_emissions_after_seed` | 5 s borderline-vx chatter at threshold * 1.05 | Zero dispatcher emissions, analyzer stays indeterminate (T-05-06-02) |
| `test_dwell_then_walk_produces_expected_target_progression` | Long dwell, then walk at 2 * threshold | Final intent == "moving_right"; framer target < 0.5 (moving toward 1/3) |
| `test_velocity_clamp_holds_across_pipeline` | dwell_then_walk with `pan_deadband_deg=0.0` | Max controller per-step delta <= `vmax*dt + 1e-9` (T-05-06-03) |
| `test_emission_count_bounded_over_dwell_then_walk` | Same dwell_then_walk run, count emissions | emissions <= floor(T*1000/min_interval_ms) + 2 (Plan 05 Issue 9) |
| `test_none_upstream_clean_propagation` | Seed pipeline; then feed `subject=None` for one tick | Each stage handles None correctly; controller HOLDS, dispatcher state UNCHANGED (Pitfall 7) |
| `test_pre_seed_pipeline_emits_correctly_first_frame` | Single moving_right frame on a fresh pipeline | No motor command issued; hysteresis prevents stray emissions |

## Cross-stage Measurements

### Emission count over dwell_then_walk

- `total_sec = 6.000 s` (dwell_sec=3.000 + walk_sec=3.000 -- walk capped by `(1-0.5)/walk_vx - 0.1`)
- `min_interval_ms = 50` -> analytic bound = floor(6000 / 50) + 2 = **122**
- Actual emissions: **26** (well under bound; bound is a worst-case ceiling, not a target)

### Velocity clamp over dwell_then_walk

- `vmax = 30.0 deg/s`, `dt = 1/30 s` -> ceiling = **1.000 deg/frame**
- Actual max per-frame controller delta: **0.282 deg/frame** (real, non-vacuous)
- 134 non-None angle frames driven; clamp ceiling never approached -- the framer's stage-1 damping plus the pan controller's stage-2 damping bound the per-frame delta well below the hard clamp threshold under default Config

### Borderline chatter

- 5 s at 30 Hz = 150 frames at vx alternating between +0.084 and -0.084 (threshold * 1.05)
- Dispatcher emissions: **0**
- analyzer.current_intent at end: **"indeterminate"**
- framer.current_target_x_normalized at end: **None**
- controller.current_angle_deg at end: **None**
- Strongest cross-stage anti-thrash assertion in Phase 5 -- if any single-stage regression introduces a flip, the test fails immediately.

## Cross-stage Gotchas Discovered

1. **D-06 at-target seed makes pure-ramp clamp tests vacuous**: Initial implementation followed the plan's <action> sketch and built the velocity_clamp test on a constant-vx ramp. With ramp starting at t=0, the analyzer flips to moving_right within hysteresis_sec, the framer's D-06 logic seeds `FollowerState(position=1/3, velocity=0)`, the controller seeds at the corresponding degree value, and -- because the framer's target stays at 1/3 forever (intent never changes) -- the controller's `current_angle_deg` never moves. All measured per-step deltas were 0.0; the assertion held vacuously. **Resolution**: Switched the velocity_clamp test to `dwell_then_walk`. The dwell phase seeds the framer at center (0.5); the walk phase then transitions the framer's target to 1/3, driving real angle motion through both dampers. Real measured max delta is 0.282 deg/frame, comfortably below the 1.0 deg/frame clamp ceiling. The plan's <behavior> assertion is preserved exactly; only the trajectory choice changed. Documented in the test docstring.

2. **ramp/dwell_then_walk constant-vx dilution**: Both helpers produce CONSTANT `vx = (x_end - x_start) / total_sec`, NOT a configurable per-frame vx. A naive choice of total_sec longer than `(1.0 - x_start) / desired_vx` dilutes vx below `motion_threshold`, the analyzer never flips, and downstream stages never seed -- the test fails on `assert len(non_none_angles) > 0`. **Resolution**: every test that needs a sustained above-threshold ramp computes `total_sec = min(time_budget, (span_max / walk_vx) - safety_margin)`. Caught during initial test execution (Rule 1 inline fix); applied to test_velocity_clamp_holds_across_pipeline and test_none_upstream_clean_propagation.

3. **Framer clears state on indeterminate; controller HOLDS state on None target**: This asymmetry (D-07 differs between the two stages) is exactly the kind of cross-stage invariant a composition test catches. After a None upstream tick, the framer returns None and clears its damper state -- but the controller, when it receives None from the framer, returns its `_last_emitted_angle_deg` unchanged. test_none_upstream_clean_propagation asserts both behaviours simultaneously: `framer.current_target_x_normalized is None` AND `ang == pre_none_controller_angle`. No surprise; the contract is documented in pan_controller.py:150 (`_hold` does NOT clear `_state`). Confirmed under composition.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test design bug] velocity_clamp test was vacuously passing on pure ramp**
- **Found during:** Task 1 first pytest run after initial implementation
- **Issue:** `ramp` trajectory plus framer D-06 at-target seed produces zero per-frame angle deltas; the clamp invariant held trivially without exercising the clamp logic. The plan's <action> sketch suggested ramp; the real-behavior test required dwell_then_walk.
- **Fix:** Switched test to `dwell_then_walk`, which forces a center seed during the dwell phase and then drives a real target transition through stage-1 + stage-2 dampers during the walk phase. Documented the rationale in the test docstring.
- **Files modified:** pastor_tracker/tests/test_intent_control_pipeline.py
- **Commit:** ea1d480

**2. [Rule 1 - Bug] ramp's constant-vx dilution caused test_none_upstream and test_velocity_clamp guards to trip**
- **Found during:** Task 1 second pytest run after first fix
- **Issue:** `ramp(x_start, x_end, total_sec)` produces constant `vx = (x_end - x_start) / total_sec`; an over-long total_sec dilutes vx below motion_threshold so analyzer never flips, controller never seeds, and the `assert len(non_none_angles) > 0` (or `assert pre_none_controller_angle is not None`) trips.
- **Fix:** Cap total_sec at `min(time_budget, (span_max / walk_vx) - safety_margin)` in both tests, ensuring the produced ramp's vx is at least `walk_vx > threshold`.
- **Files modified:** pastor_tracker/tests/test_intent_control_pipeline.py
- **Commit:** ea1d480

No architectural changes; no Rule 4 escalations.

## Verification

### Automated

```
$ uv run pytest tests/test_intent_control_pipeline.py -x -v
============================== 7 passed in 1.94s ==============================

$ uv run pytest tests/test_motion_analyzer.py tests/test_framer.py tests/test_pan_controller.py tests/test_command_dispatcher.py tests/test_intent_control_pipeline.py
============================== 65 passed in 2.79s ==============================

$ uv run ruff check tests/test_intent_control_pipeline.py
All checks passed!

$ uv run ruff check src tests
All checks passed!

$ uv run mypy --strict src
Success: no issues found in 26 source files
```

### Phase 5 ship gate

| Gate | Result |
|------|--------|
| Full `uv run pytest` | **370 passed, 1 failed** |
| `uv run ruff check src tests` | green |
| `uv run mypy --strict src` | green |
| Phase 5 isolated suite | 65 / 65 passed |
| New plan 06 suite | 7 / 7 passed |

The single full-suite failure is the **pre-existing deferred** `test_geometry::test_inverse_map_output_in_unit_interval` flake (full-suite-only, unraisable asyncio ResourceWarning) -- registered in `.planning/phases/05-intent-and-control/deferred-items.md` since Plan 05-04, status: Open. Not introduced by Plan 06; not in scope for Plan 06.

### Acceptance criteria

| Criterion | Result |
|-----------|--------|
| File exists, >= 150 lines | 353 lines (passed) |
| `grep -c "def test_"` >= 5 | 7 (passed) |
| pytest >= 5 passed, 0 failed | 7 passed, 0 failed (passed) |
| Full Phase 5 suite green | 65 passed (passed) |
| Imports all four stages | MotionAnalyzer / Framer / PanController / CommandDispatcher all imported via package surfaces (passed) |
| ruff green | green (passed) |
| No `unittest.mock` import | confirmed absent (passed) |
| No production-code touch | `grep -c "src/pastor_tracker"` = 0 in test file; only public-package imports (passed) |
| Magic-number leak check | no Phase-5 thresholds (0.08, 0.3, 0.03, 1.5, 0.4, 30.0, 0.2, 50, 0.6, 0.8) appear in test bodies (passed) |
| `_drive_pipeline` helper present | yes (passed) |

## Threat Mitigations Confirmed

| Threat | Mitigation Test | Status |
|--------|-----------------|--------|
| T-05-06-01 (cross-stage time-source mismatch) | `_drive_pipeline` uses single `now_ns = subject.timestamp_ns` for all four stages | Mitigated |
| T-05-06-02 (borderline chatter cascading to emissions) | `test_borderline_chatter_produces_no_emissions_after_seed` -- 0 emissions over 5 s | Mitigated |
| T-05-06-03 (clamp engaged but emission exceeds limit) | `test_velocity_clamp_holds_across_pipeline` -- max 0.282 deg << 1.000 deg ceiling | Mitigated |
| T-05-06-04 (mismatched Config across stages) | `_make_pipeline` builds one `Config` shared across all four stages | Mitigated |
| T-05-06-05 (log floods from emission-storm bug) | Borderline chatter test counts emissions; storm would fail count assertion | Mitigated |
| T-05-06-06 (large trajectories slow CI) | All trajectories <= 6 s synthetic time at 30 Hz; full 7-test suite runs in ~2 s | Accepted (per plan) |
| T-05-06-07 (future maintainer swaps a stage for a stub) | Test imports four production classes by name; no `unittest.mock` import | Mitigated |

## Self-Check: PASSED

- File created: `pastor_tracker/tests/test_intent_control_pipeline.py` -- FOUND
- Commit `ea1d480` -- FOUND in git log
- Phase 5 suite (motion_analyzer + framer + pan_controller + command_dispatcher + intent_control_pipeline) -- 65/65 PASSED
- Ship gate (`mypy --strict src`, `ruff check src tests`, full pytest) -- green save the pre-existing deferred test_geometry flake (Plan 05-04 carryover)
- Plan 06 acceptance criteria 10 / 10 -- all PASSED
