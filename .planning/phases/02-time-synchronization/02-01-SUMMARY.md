---
phase: 02-time-synchronization
plan: 01
subsystem: timing
tags: [clock, dependency-injection, monotonic-time, deterministic-testing, perf_counter]

# Dependency graph
requires:
  - phase: 01-test-foundation
    provides: "SimulatedMotorInterface, test fixtures, clean codebase"
provides:
  - "Clock ABC with RealClock and FakeClock implementations"
  - "All components accept injected Clock via constructor"
  - "Control algorithms accept explicit flDeltaTimeSeconds parameter"
  - "FakeClock pytest fixture for deterministic tests"
  - "Zero time.time() calls in src/"
affects: [02-time-synchronization, 03-motion-smoothing, 04-calibration, 05-ui-polish]

# Tech tracking
tech-stack:
  added: []
  patterns: [clock-dependency-injection, explicit-delta-time-parameter, abc-based-clock-abstraction]

key-files:
  created:
    - src/utilities/clock.py
  modified:
    - src/control/control_algorithm.py
    - src/interfaces/motor_interface.py
    - src/interfaces/camera_interface.py
    - src/control/tracker_controller.py
    - src/main.py
    - tests/conftest.py

key-decisions:
  - "Clock ABC with abc.ABC (not Protocol) for explicit interface contract"
  - "RealClock wraps time.perf_counter() exclusively (monotonic, high-resolution)"
  - "Background simulation loop keeps raw time.perf_counter() for real-time sleep delta"
  - "Optional[Clock] = None defaulting to RealClock() preserves backward compatibility"
  - "VelocityController.reset_controller() now resets _flPreviousVelocity (was only resetting time)"

patterns-established:
  - "Clock injection: all time-dependent components accept obClock parameter"
  - "Explicit dt: control algorithms receive flDeltaTimeSeconds, never compute internally"
  - "FakeClock fixture: deterministic tests use obFakeClock starting at 0.0"

# Metrics
duration: 5min
completed: 2026-02-22
---

# Phase 2 Plan 01: Clock Abstraction Summary

**Clock ABC (RealClock + FakeClock) injected into all components, explicit delta-time on all control algorithms, zero time.time() calls remaining**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-22T10:33:34Z
- **Completed:** 2026-02-22T10:38:36Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Created Clock abstraction layer with RealClock (production) and FakeClock (testing) implementations
- Removed all time.time() calls from the entire src/ directory
- Updated all three control algorithms (P, PID, Velocity) to accept explicit flDeltaTimeSeconds parameter
- Injected shared Clock instance into MotorInterface, CameraInterface, TrackerController via constructor
- All 17 Phase 1 tests pass without modification

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Clock abstraction and update control algorithm signatures** - `d8af3e3` (feat)
2. **Task 2: Inject Clock into all components and purge raw time calls** - `548d2c0` (feat)

## Files Created/Modified
- `src/utilities/clock.py` - Clock ABC, RealClock (perf_counter), FakeClock (deterministic)
- `src/control/control_algorithm.py` - All algorithms accept flDeltaTimeSeconds, removed time import
- `src/interfaces/motor_interface.py` - MotorInterface, NullMotorInterface, SimulatedMotorInterface accept obClock
- `src/interfaces/camera_interface.py` - CameraInterface accepts obClock for frame timestamps
- `src/control/tracker_controller.py` - TrackerController accepts obClock, passes dt to algorithms
- `src/main.py` - Creates shared RealClock, passes to all components
- `tests/conftest.py` - FakeClock fixture and obSimulatedMotorWithClock fixture added

## Decisions Made
- Used abc.ABC (not Protocol) for Clock interface -- matches existing codebase pattern and provides explicit contract
- RealClock wraps time.perf_counter() exclusively -- monotonic and high-resolution, superior to time.time()
- Background simulation loop retains raw time.perf_counter() -- needs real wall-clock for sleep interval, not injected clock
- All Clock parameters are Optional defaulting to RealClock() -- preserves backward compatibility with existing code
- VelocityController.reset_controller() now resets _flPreviousVelocity instead of dPreviousTime -- correct behavior after time state removal

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Clock abstraction is the architectural keystone for all Phase 2 plans
- Plan 02 (grab/retrieve camera timing) can use the injected Clock for precise timestamp placement
- Plan 03 (dt clamping and sanitization) can build on the explicit flDeltaTimeSeconds parameter
- Plan 04 (verification and stress tests) can use FakeClock for deterministic timing tests
- All 17 existing tests continue to pass

## Self-Check: PASSED

- All 8 claimed files exist on disk
- Commit d8af3e3 (Task 1) verified in git log
- Commit 548d2c0 (Task 2) verified in git log
- 17/17 tests pass
- Zero time.time() calls in src/ (code only, comments excluded)
- time.perf_counter() only in clock.py and motor_interface.py background loop

---
*Phase: 02-time-synchronization*
*Completed: 2026-02-22*
