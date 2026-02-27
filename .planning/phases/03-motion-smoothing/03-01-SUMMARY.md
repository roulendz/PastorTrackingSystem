---
phase: 03-motion-smoothing
plan: 01
subsystem: control
tags: [one-euro-filter, s-curve, motion-profiler, state-machine, confidence-scaling, smoothstep]

# Dependency graph
requires:
  - phase: 02-time-synchronization
    provides: Clock ABC, FakeClock for deterministic tests, dt-parameterized control
provides:
  - OneEuroFilter for adaptive pose jitter suppression
  - MotionProfiler for S-curve velocity smoothing
  - HomeReturnController state machine for safe-zone return
  - compute_confidence_scale_factor for detection-quality scaling
  - 15 new config fields for all motion smoothing parameters
affects: [03-02 integration, 04-detection-handling]

# Tech tracking
tech-stack:
  added: []
  patterns: [velocity-domain S-curve profiling, 1-Euro adaptive filtering, state machine with profiler integration]

key-files:
  created:
    - src/tracking/pose_filter.py
    - src/control/motion_profiler.py
    - src/control/home_return_controller.py
    - tests/test_pose_filter.py
    - tests/test_motion_profiler.py
    - tests/test_home_return.py
  modified:
    - src/utilities/config_manager.py
    - config/default_config.json

key-decisions:
  - "OneEuroFilter tuned to minCutoff=0.01Hz, derivativeCutoff=0.1Hz (plan spec 1.0Hz could not satisfy <1px jitter suppression)"
  - "HomeReturnController resets profiler velocity on overshoot clamp to prevent oscillation around home"
  - "Overshoot detection uses sign-flip of target angle rather than distance-to-home comparison"

patterns-established:
  - "Velocity-domain S-curve: exponential approach with separate accel/decel time constants via MotionProfiler"
  - "State machine + profiler: HomeReturnController owns an internal MotionProfiler for return motion"
  - "Confidence scaling: smoothstep interpolation between hold/full thresholds"

requirements-completed: [MOTN-01, MOTN-02, MOTN-03, MOTN-04]

# Metrics
duration: 8min
completed: 2026-02-27
---

# Phase 3 Plan 01: Core Motion Smoothing Modules Summary

**1-Euro adaptive pose filter, velocity-domain S-curve profiler, 4-state home return machine, and smoothstep confidence scaling -- all TDD with 22 new tests**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-27T10:32:28Z
- **Completed:** 2026-02-27T10:40:46Z
- **Tasks:** 1 (single comprehensive task with TDD RED-GREEN flow)
- **Files modified:** 8

## Accomplishments
- OneEuroFilter suppresses 5px-std jitter to <0.5px range on stationary input while tracking fast movement with <6% lag
- MotionProfiler provides S-curve velocity ramping with smooth direction reversals through zero
- HomeReturnController cycles through TRACKING/SAFE_ZONE_DELAY/RETURNING_HOME/AT_HOME with zero overshoot at home
- Confidence scale factor produces smooth cubic interpolation between hold (0.0) and full (1.0) thresholds
- 22 new tests, 78 total tests passing with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests** - `3ee16a4` (test)
2. **Task 1 (GREEN): Implementation passing all tests** - `d950cea` (feat)

## Files Created/Modified
- `src/tracking/pose_filter.py` - OneEuroFilter (1-Euro adaptive low-pass filter) and confidence scale factor
- `src/control/motion_profiler.py` - Velocity-domain S-curve profiler with exponential approach
- `src/control/home_return_controller.py` - Home return state machine with internal MotionProfiler
- `src/utilities/config_manager.py` - 15 new SystemConfiguration fields for motion smoothing
- `config/default_config.json` - Matching JSON config defaults
- `tests/test_pose_filter.py` - 9 tests (jitter suppression, fast tracking, zero-dt, reset, confidence scaling)
- `tests/test_motion_profiler.py` - 5 tests (ramp up/down, direction reversal, reset, dead zone)
- `tests/test_home_return.py` - 8 tests (state transitions, return to home, cancellation, overshoot)

## Decisions Made
- **OneEuroFilter parameter tuning:** Plan specified minCutoffHz=1.0 and derivativeCutoffHz=1.0, but these values produce 7.5px jitter range on stationary input (far exceeding the <1px done criterion). Tuned to minCutoffHz=0.01, derivativeCutoffHz=0.1 which achieves 0.48px jitter range while maintaining <6% tracking lag. Beta kept at plan's 0.007.
- **HomeReturnController overshoot prevention:** Used sign-flip detection on target angle rather than distance-to-home comparison. When the target crosses zero during integration, it is clamped to 0.0 and the profiler velocity is reset to prevent oscillation.
- **HomeReturnController arrival detection:** The controller transitions to AT_HOME when both target angle < 0.01 deg and profiler velocity < 0.01 deg/s, ensuring the motor has truly settled.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] OneEuroFilter default parameters could not satisfy done criteria**
- **Found during:** Task 1 (GREEN phase, test_stationary_input_suppresses_jitter failing)
- **Issue:** Plan defaults minCutoffHz=1.0, derivativeCutoffHz=1.0 produce 7.5px filtered range vs <1px required. The noise-amplified derivative (1/dt factor at 30FPS) inflated the adaptive cutoff even for stationary input.
- **Fix:** Tuned minCutoffHz=0.01 (very low base cutoff for stationary), derivativeCutoffHz=0.1 (suppresses derivative noise). Both criteria now satisfied: <0.5px jitter, <6% tracking lag.
- **Files modified:** src/tracking/pose_filter.py, src/utilities/config_manager.py, config/default_config.json, tests/test_pose_filter.py
- **Verification:** All 9 pose filter tests pass
- **Committed in:** d950cea (GREEN phase commit)

**2. [Rule 1 - Bug] HomeReturnController overshoot clamping logic inverted**
- **Found during:** Task 1 (GREEN phase, test_no_overshoot_past_zero failing)
- **Issue:** Original clamp checked `flDistanceToHome` sign which was computed BEFORE integration, causing the condition to never trigger. Target oscillated between +/- 0.07 deg indefinitely.
- **Fix:** Track sign of target BEFORE integration; if sign flips after integration, clamp to 0.0 and reset profiler velocity.
- **Files modified:** src/control/home_return_controller.py
- **Verification:** All 8 home return tests pass, no overshoot in 300-frame test
- **Committed in:** d950cea (GREEN phase commit)

---

**Total deviations:** 2 auto-fixed (2 bugs in plan specification)
**Impact on plan:** Both fixes necessary for correctness per done criteria. No scope creep -- parameters and logic adjusted to match stated behavioral requirements.

## Issues Encountered
None beyond the deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All three modules (OneEuroFilter, MotionProfiler, HomeReturnController) are ready for integration into TrackerController (Plan 03-02)
- Config fields are in place for DearPyGui settings panel wiring
- No blockers for Plan 03-02

---
*Phase: 03-motion-smoothing*
*Completed: 2026-02-27*
