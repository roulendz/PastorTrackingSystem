---
phase: 01-test-foundation-and-code-cleanup
plan: 01
subsystem: infra
tags: [motor-simulation, trapezoidal-motion, video-input, fov, pytest, testing-foundation]

# Dependency graph
requires: []
provides:
  - SimulatedMotorInterface with trapezoidal velocity profile physics
  - CameraInterface video file input with automatic looping
  - --video CLI flag for full synthetic (hardware-free) operation
  - Correct FOV defaults for Sony AX700 at max optical zoom
  - pytest in requirements.txt for test infrastructure
affects: [01-test-foundation-and-code-cleanup, 02-time-sync-fix]

# Tech tracking
tech-stack:
  added: [pytest]
  patterns: [trapezoidal-velocity-profile, synthetic-testing-mode, background-simulation-thread]

key-files:
  created: []
  modified:
    - src/interfaces/motor_interface.py
    - src/interfaces/camera_interface.py
    - src/main.py
    - src/utilities/config_manager.py
    - config/default_config.json
    - requirements.txt

key-decisions:
  - "SimulatedMotorInterface replaces NullMotorInterface in all fallback paths (motor-less mode now uses physics simulation)"
  - "Background simulation thread at ~1000 Hz for interactive mode; explicit dt calls for deterministic tests"
  - "Gear ratio constant 360/288000 degrees/step used to convert steps/s to degrees/s"
  - "dataclasses-json removed as dead dependency (not imported anywhere in src/)"

patterns-established:
  - "Synthetic mode pattern: --video flag triggers SimulatedMotorInterface + video CameraInterface for full hardware-free operation"
  - "Simulation advance pattern: advance_simulation(dt) for test determinism, background thread for interactive use"

# Metrics
duration: 5min
completed: 2026-02-15
---

# Phase 1 Plan 1: Hardware-Free Operation Foundation Summary

**SimulatedMotorInterface with trapezoidal motion profile, CameraInterface video file input with looping, and corrected Sony AX700 FOV defaults (6.77 deg)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-15T13:39:08Z
- **Completed:** 2026-02-15T13:44:01Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments
- SimulatedMotorInterface with trapezoidal velocity profile: accelerate/cruise/decelerate physics, thread-safe state, background simulation thread, and deterministic advance_simulation(dt) for testing
- CameraInterface extended with sVideoFilePath parameter for video file input, with automatic looping at end-of-file
- main.py --video flag creates full synthetic mode (SimulatedMotorInterface + video) with no hardware required
- FOV corrected from 0.0/0.05 to 6.77/0.00529 (Sony AX700 at max optical zoom, 111.6mm focal length)
- pytest added to requirements.txt; dead dataclasses-json dependency removed

## Task Commits

Each task was committed atomically:

1. **Task 1: Create SimulatedMotorInterface with trapezoidal motion physics** - `39a2a7c` (feat)
2. **Task 2: Extend CameraInterface for video file input with looping** - `c4421b2` (feat)
3. **Task 3: Fix FOV configuration and add pytest to requirements** - `383e3bf` (fix)

## Files Created/Modified
- `src/interfaces/motor_interface.py` - Added SimulatedMotorInterface class (222 lines) with trapezoidal velocity profile, background simulation thread, full MotorInterface API compatibility
- `src/interfaces/camera_interface.py` - Added sVideoFilePath parameter, video file open path (skips camera-only properties), auto-loop on end-of-file
- `src/main.py` - Added --video CLI argument, synthetic mode initialization (SimulatedMotorInterface + video CameraInterface), replaced NullMotorInterface fallback with SimulatedMotorInterface
- `src/utilities/config_manager.py` - Updated SystemConfiguration defaults: flFieldOfViewDegrees=6.77, flInitialAnglePerPixelDegrees=0.00529
- `config/default_config.json` - Set correct FOV values, added _sFovNote documenting calculation basis
- `requirements.txt` - Added pytest>=8.0.0, removed dataclasses-json==0.6.3

## Decisions Made
- **SimulatedMotorInterface replaces NullMotorInterface in fallback paths:** When motor connection fails and bAllowStartWithoutMotor is true, the system now uses SimulatedMotorInterface (with physics) instead of NullMotorInterface (instant teleportation). NullMotorInterface class is preserved for backward compatibility but no longer instantiated in initialize_system.
- **Background simulation at ~1000 Hz:** Interactive mode needs continuous motor state updates. The background thread uses perf_counter delta timing. Tests call advance_simulation() directly for deterministic behavior.
- **Gear ratio constant:** 360.0 / 288000.0 degrees/step (200 steps/rev * 180:1 gear ratio * 8 microsteps = 288,000 steps/rev). Used to convert between steps/s and degrees/s in speed settings.
- **dataclasses-json removed:** Confirmed not imported anywhere in src/ -- safe to remove as dead dependency.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Development environment lacks project dependencies (cv2, serial, numpy, mediapipe). All verification was done through mock imports and structural source file analysis. The SimulatedMotorInterface physics were fully verified with a mocked serial module. CameraInterface and main.py changes were verified structurally.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Hardware-free operation foundation is complete: any future test or development can run without camera or motor hardware
- SimulatedMotorInterface provides realistic motion physics for integration testing
- CameraInterface video input enables repeatable test scenarios with recorded footage
- FOV defaults are correct, eliminating a source of tracking error
- pytest is available for test infrastructure in subsequent plans

## Self-Check: PASSED

All 6 modified files verified present. All 3 task commits verified in git log (39a2a7c, c4421b2, 383e3bf). SUMMARY.md exists at expected path.

---
*Phase: 01-test-foundation-and-code-cleanup*
*Completed: 2026-02-15*
