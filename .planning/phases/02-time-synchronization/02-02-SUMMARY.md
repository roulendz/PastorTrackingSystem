---
phase: 02-time-synchronization
plan: 02
subsystem: timing
tags: [camera-timestamping, grab-retrieve, dt-clamping, dropped-frame-handling, pipeline-boundary]

# Dependency graph
requires:
  - phase: 02-time-synchronization
    plan: 01
    provides: "Clock ABC with RealClock/FakeClock, explicit dt parameter on control algorithms"
provides:
  - "Camera frame timestamps between grab()/retrieve() (SYNC-02)"
  - "dt clamping at pipeline boundary with configurable min/max"
  - "Dropped-frame handling: skip control update, log at debug level"
  - "Config parameters for flDtMaxSeconds, flDtMinSeconds, flDriftRmsWindowSeconds, flReversalSettlingWindowSeconds"
affects: [02-time-synchronization, 03-motion-smoothing, 04-calibration]

# Tech tracking
tech-stack:
  added: []
  patterns: [grab-retrieve-split, pipeline-boundary-dt-clamping, dropped-frame-skip]

key-files:
  created: []
  modified:
    - src/interfaces/camera_interface.py
    - src/control/tracker_controller.py
    - config/default_config.json

key-decisions:
  - "Timestamp placed between grab() and retrieve() -- closest proxy to true capture moment"
  - "dt > flDtMaxSeconds skips control update entirely and updates previous timestamp to prevent cascade"
  - "dt < flDtMinSeconds clamped up to minimum to avoid division issues"
  - "First frame uses nominal 1/30s interval rather than zero dt"
  - "Consistent flDeltaTimeSeconds variable name throughout control pipeline"

patterns-established:
  - "grab()/retrieve() split: camera timestamps between acquire and decode"
  - "Pipeline boundary clamping: dt sanitized before reaching control algorithms"
  - "Dropped frame handling: skip control, update timestamp, log at debug"

# Metrics
duration: 3min
completed: 2026-02-22
---

# Phase 2 Plan 02: Camera Timestamping and dt Clamping Summary

**Precise camera timestamps via grab()/retrieve() split with configurable dt clamping at pipeline boundary, skipping control on dropped frames**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-22T10:40:55Z
- **Completed:** 2026-02-22T10:43:50Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Replaced VideoCapture.read() with grab()/retrieve() split in CameraInterface, placing timestamp between the two calls for most accurate frame timing
- Implemented robust dt clamping at the TrackerController pipeline boundary: min 1ms, max 66ms (~2x 30fps interval)
- Dropped frames (dt > max) skip control update entirely with debug logging, preventing garbage dt values from reaching control algorithms
- Added four new config parameters for timing thresholds per locked decisions: flDtMaxSeconds, flDtMinSeconds, flDriftRmsWindowSeconds, flReversalSettlingWindowSeconds

## Task Commits

Each task was committed atomically:

1. **Task 1: Camera grab()/retrieve() split for precise timestamps** - `833ee52` (feat)
2. **Task 2: TrackerController dt clamping, dropped-frame handling, and config parameters** - `2bc32e3` (feat)

## Files Created/Modified
- `src/interfaces/camera_interface.py` - grab()/retrieve() split with timestamp between calls, video looping preserved
- `src/control/tracker_controller.py` - dt clamping at pipeline boundary, dropped-frame skip with logging, config reading
- `config/default_config.json` - Four new timing parameters (flDtMaxSeconds, flDtMinSeconds, flDriftRmsWindowSeconds, flReversalSettlingWindowSeconds)

## Decisions Made
- Timestamp placed between grab() and retrieve() per plan and CONTEXT.md discretion -- grab() acquires from camera buffer (fast, ~constant), retrieve() decodes (slower, variable), so timestamp between them is closest to true capture moment
- Dropped frames update the previous timestamp before returning early -- prevents cascade of skips where each successive frame sees accumulated skipped time
- First frame uses nominal 1/30s interval instead of the previous zero-dt fallback -- avoids both zero and artificially large initial dt
- Renamed dDeltaTime to flDeltaTimeSeconds for consistency with Hungarian notation (fl prefix for float type)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Camera timestamps are now physically meaningful (SYNC-02 complete)
- Pipeline boundary enforces timing sanity for all control algorithms
- Config parameters for drift RMS window and reversal settling window are ready for Plan 03 (motor angle interpolation) and Plan 04 (verification)
- All 17 existing tests continue to pass

## Self-Check: PASSED

- All 3 modified files exist on disk
- SUMMARY.md created at .planning/phases/02-time-synchronization/02-02-SUMMARY.md
- Commit 833ee52 (Task 1) verified in git log
- Commit 2bc32e3 (Task 2) verified in git log
- 17/17 tests pass
- Zero .read() calls in camera_interface.py
- grab()/retrieve() pattern in use (2 grab, 1 retrieve)
- dt clamping logic present with configurable bounds

---
*Phase: 02-time-synchronization*
*Completed: 2026-02-22*
