---
phase: 03-motion-smoothing
plan: 02
subsystem: control
tags: [integration, pipeline-wiring, dearpygui, settings-panel, motion-profiling, jitter-filter, confidence-scaling, home-return]

# Dependency graph
requires:
  - phase: 03-motion-smoothing
    plan: 01
    provides: OneEuroFilter, MotionProfiler, HomeReturnController, confidence scaling, config fields
provides:
  - Integrated motion smoothing pipeline in TrackerController
  - DearPyGui runtime tuning sliders for all Phase 3 parameters
  - Integration tests validating full pipeline behavior
affects: [04-detection-handling]

# Tech tracking
tech-stack:
  added: []
  patterns: [pipeline integration, DearPyGui runtime callbacks, helper method extraction]

key-files:
  created:
    - tests/test_motion_integration.py
  modified:
    - src/control/tracker_controller.py
    - src/ui/live_settings_panel.py

key-decisions:
  - "OneEuroFilter lazy-initialized on first valid detection frame (needs initial timestamp and value)"
  - "Extracted _send_profiled_motor_command helper to avoid duplication between normal tracking and low-confidence home return paths"
  - "Low-confidence timer feeds HomeReturnController with angle 0.0 to trigger safe-zone state transitions"
  - "DearPyGui slider callbacks update both obConfig and live module attributes (GIL makes float assignment atomic)"
  - "Integration tests use commanded angle tracking (_flLastCommandedAngle) rather than simulated motor position to isolate pipeline logic from motor physics"

patterns-established:
  - "Pose filter -> Control -> Confidence scaling -> S-curve profiling pipeline ordering"
  - "Helper method extraction for shared motor command paths"

requirements-completed: [MOTN-01, MOTN-02, MOTN-03, MOTN-04]

# Metrics
duration: 10min
completed: 2026-02-27
---

# Phase 3 Plan 02: Pipeline Integration and Settings Panel Summary

**Full motion smoothing pipeline wired into TrackerController with 14 runtime-tunable DearPyGui sliders and 6 integration tests -- OneEuroFilter on pose input, confidence scaling on control output, S-curve velocity profiling on motor commands, HomeReturnController for safe zone behavior**

## Performance

- **Duration:** 10 min
- **Started:** 2026-02-27T10:43:24Z
- **Completed:** 2026-02-27T10:53:17Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- TrackerController uses all four Phase 3 subsystems in correct pipeline order: OneEuroFilter -> Control Algorithm -> Confidence Scaling -> S-curve Profiling
- Sustained low confidence (below hold threshold) accumulates timer; after timeout, triggers S-curve home return via HomeReturnController
- Old linear velocity clamp (`_compute_home_return_target_angle`) deleted and replaced by HomeReturnController state machine
- DearPyGui settings panel has 4 new collapsible sections with 14 tunable parameter sliders
- All slider callbacks update both config and live module attributes for immediate runtime effect
- 6 integration tests validate: jitter reduction, S-curve acceleration limiting, confidence gating, sustained low-confidence home return, deadband home return after delay, smooth direction reversal
- 84 total tests passing (78 existing + 6 new) with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Integrate motion smoothing into TrackerController** - `8874ae6` (feat)
2. **Task 2: DearPyGui sliders and integration tests** - `dae2787` (feat)

## Files Created/Modified
- `src/control/tracker_controller.py` - Full Phase 3 pipeline integration (OneEuroFilter, MotionProfiler, HomeReturnController, confidence scaling, _send_profiled_motor_command helper)
- `src/ui/live_settings_panel.py` - 4 new sections (Motion Smoothing, Home Return, Jitter Filter, Confidence) with 14 slider ranges, tooltips, and live callbacks
- `tests/test_motion_integration.py` - 6 integration tests with _build_test_controller fixture and _run_frames helper

## Decisions Made
- **Lazy OneEuroFilter initialization:** Filter cannot be constructed without initial timestamp/value, so it's created as None and initialized on the first valid detection frame. Reset to None in start_tracking_mode for clean restarts.
- **Helper method extraction:** `_send_profiled_motor_command` extracted to share S-curve profiling + clamping + command sending logic between normal tracking path and low-confidence home return path (avoiding code duplication per plan instruction).
- **Low-confidence home return mechanism:** When low-confidence timer exceeds timeout, feed HomeReturnController with angle 0.0 (as if person is in safe zone) to trigger the standard SAFE_ZONE_DELAY -> RETURNING_HOME -> AT_HOME state machine transitions.
- **Integration test strategy:** Tests track `_flLastCommandedAngle` (the angle actually sent to the motor) rather than simulated motor position, isolating pipeline logic from motor physics simulation dynamics.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] FakeClock API mismatch in tests**
- **Found during:** Task 2 (first test run)
- **Issue:** Integration tests called `obFakeClock.advance()` but FakeClock's actual method is `advance_time_seconds()`
- **Fix:** Changed all calls to use correct method name
- **Files modified:** tests/test_motion_integration.py
- **Committed in:** dae2787

**2. [Rule 1 - Bug] Integration test jitter assertion measured wrong signal**
- **Found during:** Task 2 (test failure analysis)
- **Issue:** Original test measured motor simulation position std, which includes physics dynamics unrelated to jitter filtering. Motor position continues moving toward previously commanded targets.
- **Fix:** Changed test to directly verify OneEuroFilter output (filtered X std < raw X std and filtered std < 1px)
- **Files modified:** tests/test_motion_integration.py
- **Committed in:** dae2787

**3. [Rule 1 - Bug] Home return test used unreachable pixel positions**
- **Found during:** Task 2 (test failure analysis)
- **Issue:** Test tried to position person to cancel motor angle, but with motor at 8.97 deg and 6.77 deg FOV, the required pixel position (-1056px) was far outside the 1280px frame
- **Fix:** Redesigned test to: (1) move motor only 1 deg off home, (2) reset tracking mode to clear filter state, (3) use 2.0 deg deadband to accommodate filter latency, (4) dynamically position person to keep them in deadband
- **Files modified:** tests/test_motion_integration.py
- **Committed in:** dae2787

---

**Total deviations:** 3 auto-fixed (1 API mismatch, 2 test design issues)
**Impact on plan:** All fixes necessary for test correctness. No scope changes -- all 6 planned integration test behaviors are validated.

## Issues Encountered
None beyond the deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All Phase 3 requirements (MOTN-01 through MOTN-04) are complete
- TrackerController pipeline is ready for Phase 4 detection handling additions
- The `bPersonWasDetected=False` path in `_execute_centering_control_algorithm` is stubbed for Phase 4
- No blockers for Phase 4

---
*Phase: 03-motion-smoothing*
*Completed: 2026-02-27*
