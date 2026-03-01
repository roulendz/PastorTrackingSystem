---
phase: 04-detection-handling
plan: 01
subsystem: control
tags: [detection-state-machine, dropout-filter, recovery-easing, smoothstep, frame-counter, hold-behavior, home-return]

# Dependency graph
requires:
  - phase: 03-motion-smoothing
    plan: 02
    provides: Integrated motion smoothing pipeline (OneEuroFilter, MotionProfiler, HomeReturnController, confidence scaling, _send_profiled_motor_command helper)
provides:
  - DetectionState enum (TRACKING, HOLDING, RETURNING_HOME) as sub-state of TrackerState.TRACKING
  - Frame-counter dropout filter absorbing 1-2 dropped frames silently
  - Hold behavior with low-confidence timer accumulation and MotionProfiler reset
  - S-curve home return via existing HomeReturnController on timeout
  - Recovery easing ramp (smoothstep over 0.4s) multiplied into confidence scale
  - Config fields iDetectionDropoutFrameThreshold and flRecoveryEasingDurationSeconds
  - 21 tests covering all detection handling behaviors
affects: [04-02-PLAN (settings panel sliders and debug overlay), 05-scenario-validation]

# Tech tracking
tech-stack:
  added: []
  patterns: [detection state machine as sub-state of TRACKING, frame-counter dropout filter, smoothstep recovery ramp]

key-files:
  created:
    - tests/test_detection_handling.py
  modified:
    - src/control/tracker_controller.py
    - src/utilities/config_manager.py
    - config/default_config.json
    - pytest.ini

key-decisions:
  - "Frame-counter dropout filter with threshold of 3 frames (~100ms at 30 FPS) -- simpler and more deterministic than time-based at fixed frame rate"
  - "DetectionState is a sub-state of TRACKING (not a replacement for TrackerState) -- keeps top-level state machine unchanged"
  - "Recovery from HOLDING preserves OneEuroFilter state (math handles large dt correctly via alpha approaching 1.0)"
  - "Recovery from RETURNING_HOME resets OneEuroFilter (filter state too stale after home return motion)"
  - "MotionProfiler.reset() on entering HOLDING prevents stale velocity burst on recovery"
  - "Previous control timestamp set to now on recovery to prevent dt gap cascade skip (Pitfall 5)"
  - "pytest.ini pythonpath extended to include tests directory for cross-test-module imports"

patterns-established:
  - "Detection state machine at STEP 1 of _execute_centering_control_algorithm, before dt computation"
  - "Smoothstep recovery ramp multiplied into confidence scale at STEP 3b, after confidence gate"
  - "Detection loss feeds into same low-confidence timer path as low-confidence frames"

requirements-completed: [DTCT-01, DTCT-02, DTCT-03]

# Metrics
duration: 6min
completed: 2026-03-01
---

# Phase 4 Plan 01: Detection State Machine Summary

**DetectionState enum with frame-counter dropout filter absorbing 1-2 dropped frames, hold-then-home-return on sustained loss, and smoothstep recovery easing ramp -- 21 new tests, 105 total passing, zero regressions**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-01T09:23:26Z
- **Completed:** 2026-03-01T09:29:28Z
- **Tasks:** 3 (TDD: RED, GREEN, config update)
- **Files modified:** 5

## Accomplishments
- DetectionState enum (TRACKING, HOLDING, RETURNING_HOME) governs behavior when detection is lost, holding, or recovering
- Frame-counter dropout filter absorbs 1-2 dropped frames with zero motor movement -- single MediaPipe misses are invisible
- Hold behavior stops sending motor commands, accumulates low-confidence timer, resets MotionProfiler to prevent stale velocity
- Sustained detection loss beyond flConfidenceLowTimeoutSeconds triggers S-curve home return via existing HomeReturnController
- Recovery easing ramp (smoothstep from 0.0 to 1.0 over 0.4s) prevents snap-to-target on reappearance
- Recovery from RETURNING_HOME cancels return, resets HomeReturnController and OneEuroFilter, eases back
- 21 new tests across 7 test classes covering all behaviors; 105 total tests passing with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests + config fields** - `7fc2cd9` (test)
2. **Task 2 (GREEN): Detection state machine implementation** - `0ea49a5` (feat)
3. **Task 3: Config file update** - `9af385e` (chore)

_TDD flow: RED (failing tests) -> GREEN (implementation passing all tests)_

## Files Created/Modified
- `tests/test_detection_handling.py` - 21 tests: dropout filtering (4), hold behavior (3), home return (2), recovery easing (4), recovery from returning home (3), config fields (3), regression guard (2)
- `src/control/tracker_controller.py` - DetectionState enum, dropout filter at STEP 1, recovery easing at STEP 3b, HOLDING/RETURNING_HOME state management, new instance variables, updated start_tracking_mode and apply_configuration
- `src/utilities/config_manager.py` - iDetectionDropoutFrameThreshold (int=3), flRecoveryEasingDurationSeconds (float=0.4) in SystemConfiguration dataclass
- `config/default_config.json` - New detection handling config fields added
- `pytest.ini` - Added tests to pythonpath for cross-test-module imports

## Decisions Made
- **Frame-counter vs time-based dropout filter:** Chose frame-counter (count consecutive bDetected=False frames). At fixed 30 FPS, frame counting is simpler, more deterministic, and avoids floating-point accumulation errors. Threshold of 3 frames = ~100ms.
- **DetectionState as sub-state:** DetectionState is a sub-state within TrackerState.TRACKING, not a replacement. Top-level state machine (IDLE/TRACKING/ERROR) is unchanged.
- **OneEuroFilter reset policy:** Preserve during HOLDING (alpha approaches 1.0 for large dt, passes raw value through correctly). Reset only on RETURNING_HOME recovery where filter state is meaningless after the motor has moved home.
- **Recovery timestamp handling:** Set `_dPreviousControlTimestampSeconds = dNow` on recovery to prevent the dt gap from exceeding flDtMaxSeconds and causing a skip on the first recovery frame (per Research Pitfall 5).
- **pytest.ini pythonpath:** Extended to include `tests` directory so test modules can import helpers from each other (needed for reusing `_build_test_controller` and `_run_frames`).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] pytest.ini pythonpath needed tests directory**
- **Found during:** Task 2 (GREEN phase test execution)
- **Issue:** `from test_motion_integration import _build_test_controller, _run_frames` failed because pytest.ini only had `src` in pythonpath, not `tests`
- **Fix:** Added `tests` to `pythonpath` in pytest.ini
- **Files modified:** pytest.ini
- **Verification:** All 105 tests pass
- **Committed in:** 0ea49a5

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Minor config fix needed for test infrastructure. No scope changes.

## Issues Encountered
None beyond the deviation documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Detection state machine is fully functional and tested
- Ready for 04-02 (settings panel sliders and debug overlay for detection handling)
- DetectionState accessible via `obTracker._eDetectionState` for debug overlay
- Config fields `iDetectionDropoutFrameThreshold` and `flRecoveryEasingDurationSeconds` ready for DearPyGui slider callbacks
- No blockers for 04-02

---
*Phase: 04-detection-handling*
*Completed: 2026-03-01*
