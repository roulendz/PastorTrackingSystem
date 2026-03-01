---
phase: quick
plan: 1
subsystem: ui
tags: [opencv, overlay, calibration, config]

requires:
  - phase: 05-scenario-validation
    provides: V-marker and home line overlay in draw_visualization_overlay
provides:
  - flCameraMotorOffsetDegrees config parameter for camera-motor alignment
  - Offset-corrected home line calculation in overlay
affects: [live-settings-panel, config-editor]

tech-stack:
  added: []
  patterns: [getattr-with-default for backward-compatible config reads]

key-files:
  created: []
  modified:
    - config/default_config.json
    - src/main.py

key-decisions:
  - "Offset subtracted from motor angle (not added to center) -- aligns with existing formula semantics"
  - "Default 0.0 preserves backward compatibility for all existing installations"
  - "getattr with fallback used so older config files without the key still work"

patterns-established:
  - "Config-driven overlay correction: calibration offsets live in config, applied at render time"

requirements-completed: []

duration: 1min
completed: 2026-03-01
---

# Quick Task 1: Align Blue Virtual Center Line with Yellow Camera Center Line

**Configurable camera-motor offset (flCameraMotorOffsetDegrees) corrects home line misalignment in overlay**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-01T10:55:21Z
- **Completed:** 2026-03-01T10:56:08Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Added `flCameraMotorOffsetDegrees` config parameter (default 0.0) to default_config.json
- Modified home line calculation in `draw_visualization_overlay()` to subtract the offset before pixel mapping
- V-marker and deadzone overlay automatically corrected since they derive from iHomeLineX

## Task Commits

Each task was committed atomically:

1. **Task 1: Add camera-motor offset config and apply to home line calculation** - `6cc40b7` (feat)

## Files Created/Modified
- `config/default_config.json` - Added `flCameraMotorOffsetDegrees: 0.0` after FOV parameters
- `src/main.py` - Modified `draw_visualization_overlay()` to read offset from config and apply to iHomeLineX calculation

## Decisions Made
- Offset subtracted from motor angle in the formula: `iCenterX - ((flMotorAngle - flCameraMotorOffsetDegrees) / flAnglePerPixel)`. This means when the motor is at the offset angle, the home line aligns with camera center.
- Used `getattr(obConfig, 'flCameraMotorOffsetDegrees', 0.0)` for backward compatibility with config files that don't yet have the key.
- Did NOT add to user_config.json per plan -- user tunes via live settings panel or manual edit.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - default offset of 0.0 preserves existing behavior. User can tune `flCameraMotorOffsetDegrees` in their config to match their physical camera-motor alignment.

## Next Phase Readiness
- The offset parameter is ready for integration into the live settings panel (DearPyGui slider) in a future task if desired
- User can immediately tune via manual config file edit

## Self-Check: PASSED

All artifacts verified:
- config/default_config.json: FOUND
- src/main.py: FOUND
- 1-SUMMARY.md: FOUND
- Commit 6cc40b7: FOUND

---
*Quick Task: 1-align-blue-virtual-center-line-with-yell*
*Completed: 2026-03-01*
