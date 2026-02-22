---
phase: 02-time-synchronization
plan: 03
subsystem: motor-interface
tags: [hermite-interpolation, clock-sync, linear-regression, numpy, trapezoidal-velocity]

# Dependency graph
requires:
  - phase: 02-01
    provides: "Clock ABC with RealClock/FakeClock for deterministic timestamps"
provides:
  - "MotorState.iAccelerationState field (0=stopped, 1=accel, 2=constant, 3=decel)"
  - "Hermite cubic interpolation for C1-smooth motor angle estimation"
  - "Linear regression clock offset replacing EMA (SYNC-04)"
  - "Signed velocity extraction from MotorState"
affects: [02-04, 03-motion-smoothing, frame-motor-pairing]

# Tech tracking
tech-stack:
  added: [numpy (polyfit for linear regression)]
  patterns: [Hermite cubic interpolation, module-level helper functions shared across classes]

key-files:
  modified:
    - src/interfaces/motor_interface.py

key-decisions:
  - "Module-level _hermite_interpolate_from_history() shared by both MotorInterface and SimulatedMotorInterface to avoid duplication"
  - "Velocity direction inferred from target-current delta with iAccelerationState=0 returning zero velocity"
  - "Module-level _FL_DEGREES_PER_STEP constant (360/288000) for velocity conversion in helper functions"

patterns-established:
  - "Hermite interpolation: position+velocity at endpoints produces C1 curve through reversals"
  - "Acceleration state enum: 0=stopped, 1=accel, 2=constant, 3=decel across all motor interfaces"

# Metrics
duration: 4min
completed: 2026-02-22
---

# Phase 2 Plan 3: Motor Interpolation & Clock Sync Summary

**Hermite cubic interpolation with acceleration-state-aware velocity for C1-smooth motor angle estimation, plus linear regression clock sync replacing EMA**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-22T10:41:22Z
- **Completed:** 2026-02-22T10:45:48Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- MotorState now carries iAccelerationState field (0=stopped, 1=accel, 2=constant, 3=decel) populated by Arduino feedback parser and SimulatedMotorInterface physics
- Motor angle interpolation upgraded from linear to Hermite cubic with C1 continuity through direction reversals (SYNC-03)
- Arduino-to-PC clock offset uses numpy linear regression over 50-sample sliding window for faster convergence than EMA (SYNC-04)
- Motor history buffer increased from 32 to 50 entries (1 second at 50Hz feedback rate)

## Task Commits

Each task was committed atomically:

1. **Task 1: MotorState enhancement + Arduino accelState parsing + linear regression clock offset** - `4eb8505` (feat)
2. **Task 2: Physics-informed Hermite interpolation in get_estimated_motor_angle_degrees()** - `dcf8af6` (feat)

## Files Created/Modified
- `src/interfaces/motor_interface.py` - Added iAccelerationState to MotorState, Hermite interpolation functions, linear regression clock sync, acceleration state computation in SimulatedMotorInterface

## Decisions Made
- **Shared interpolation function:** Created `_hermite_interpolate_from_history()` as a module-level function used by both MotorInterface and SimulatedMotorInterface, avoiding code duplication while keeping the same behavior
- **Velocity direction inference:** When `iAccelerationState == 0` (stopped), velocity is forced to 0.0 regardless of speed field; otherwise direction is inferred from target-vs-current angle delta
- **Module-level constant:** Added `_FL_DEGREES_PER_STEP = 360.0 / 288000.0` at module level for use by the velocity helper, matching the existing class-level constant in SimulatedMotorInterface

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Motor angle estimation now uses physics-informed Hermite interpolation (SYNC-03 complete)
- Clock sync uses linear regression (SYNC-04 complete)
- All 17 existing tests pass with the new interpolation
- Ready for plan 02-04 (frame-motor pairing / TrackingSample timestamps)

---
*Phase: 02-time-synchronization*
*Completed: 2026-02-22*

## Self-Check: PASSED

- FOUND: src/interfaces/motor_interface.py
- FOUND: .planning/phases/02-time-synchronization/02-03-SUMMARY.md
- FOUND: 4eb8505 (Task 1 commit)
- FOUND: dcf8af6 (Task 2 commit)
- All 17 tests pass
