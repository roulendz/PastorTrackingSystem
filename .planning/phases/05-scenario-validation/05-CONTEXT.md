# Phase 5: Scenario Validation - Context

**Gathered:** 2026-03-01
**Status:** Ready for planning

<domain>
## Phase Boundary

The full tracking system passes automated tests across a library of realistic pastor movement scenarios. Scripted test scenarios exist for the required movements (walk left, walk right, pause at lectern, walk outside FOV, varying speeds) and automated tolerance verification validates the 2-pixel RMS threshold (SYNC-06) across all scenarios without manual inspection.

Requirements covered: TEST-02, TEST-03, TEST-06.

</domain>

<decisions>
## Implementation Decisions

### Scenario Input Method
- Scenarios feed **scripted pose positions** directly into the pipeline (not synthetic video + MediaPipe)
- Fast, deterministic, no MediaPipe dependency in tests
- Existing mock pose tracker pattern in conftest.py provides the integration point
- A scenario-aware pose mock replaces the static `obMockPoseTracker` for these tests

### Motion Coordinate System
- Scenarios define pastor position in **angle-space** (degrees relative to center), not pixel-space
- The scenario runner converts angles to pixel positions using the known FOV (6.77 degrees / 1280 pixels)
- More intuitive for describing real-world movement and independent of camera resolution

### Confidence Per Waypoint
- Each scenario waypoint can **optionally specify detection confidence** (0.0 to 1.0)
- Default confidence is high (e.g., 0.9) when not specified
- Enables testing confidence scaling, detection handling, and dropout behavior within scenarios

### Scenario Test Structure
- Each of the 5 required scenarios (TEST-02) is its own **individual test function** — isolated, easy to debug
- Plus **1-2 combined multi-segment scenarios** that chain movements (e.g., walk left -> pause -> walk right -> off FOV)
- **Varying speeds** implemented via `@pytest.mark.parametrize` on walk left/right tests (slow/medium/fast)
- One **additional detection dropout scenario** with intermittent flickering confidence during movement

### Walk Outside FOV Behavior
- Scenario tests the **full detection cycle**: person moves past FOV edge, detection drops to zero, camera holds position (Phase 4 behavior), then person walks back into FOV and camera re-acquires
- End-to-end test of the detection state machine (Phase 4) within a realistic movement sequence

### Pause at Lectern
- Pastor walks to center, stands still for **3-5 seconds**, then resumes movement
- During pause, validate **camera stability** — no drift, no jitter from pose noise
- Tests OneEuroFilter's suppression of detection noise on a stationary subject

### Validation Pipeline Level
- Scenarios run through the **full TrackerController pipeline**: scripted positions -> control algorithm -> SimulatedMotor
- Validates the entire chain including control algorithm response, motion smoothing, and detection handling
- Existing motor-level tests in test_time_sync.py remain as lower-level regression tests

### Diagnostic Output
- **Summary stats on failure**: print RMS, max error, worst frame, and scenario segment where it failed
- Silent on pass — standard pytest pattern

### V-Marker Lectern
- V-marker rendered in the **debug visualization overlay** (not embedded in synthetic video)
- **Always visible** in debug mode (--debug flag), whether running live or test scenarios
- Useful for real-world debugging, not just testing
- Alignment at home position verified **programmatically** — automated test assertion that virtual center line pixel equals V-marker pixel when motor angle is 0

### Claude's Discretion
- Scenario scripting format (data-driven vs composable functions vs other)
- V-marker visual design (shape, color, size)
- Exact measurement metric for 2-pixel validation (virtual center vs expected position, or person-center vs frame-center)
- Per-scenario vs uniform tolerance thresholds

</decisions>

<specifics>
## Specific Ideas

- Walk outside FOV should exercise the full Phase 4 detection state machine (hold -> return -> re-acquisition), not just the boundary crossing
- The detection dropout scenario should simulate realistic flickering (intermittent low/zero confidence frames mid-movement), not just clean on/off transitions
- Lectern pause stability test is specifically about proving OneEuroFilter suppresses jitter on a stationary subject

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `generate_test_video()` in `tests/conftest.py`: Generates synthetic video with moving circle + shoulder markers (may not be needed since scenarios use scripted positions, but available for visual verification)
- `SimulatedMotorInterface`: Full trapezoidal velocity physics, FakeClock support, `advance_simulation()` for deterministic stepping
- `FakeClock`: Deterministic time control, `advance_time_seconds()` for frame-by-frame simulation
- `obMockPoseTracker` fixture: Returns static PoseResult — needs extension to return scenario-driven positions
- `_compute_pixel_rms_for_trajectory()` in `test_time_sync.py`: RMS computation pattern (motor-level) that can inform full-pipeline RMS calculation

### Established Patterns
- Motor simulation at 1000Hz steps, frame capture at 30fps (1 frame = 33 simulation steps)
- `TrackerController.execute_main_tracking_loop_tick()` is the single-tick pipeline entry point
- `ProportionalController` used in pipeline tests (via `_create_tracker_controller()` helper)
- `PoseResult` dataclass: `flPersonCenterXPixels`, `flPersonCenterYPixels`, `bPersonWasDetected`, `flPersonConfidenceScore`, `vLandmarks`
- FOV constants: 6.77 degrees, 1280 pixels, ~0.00529 deg/pixel

### Integration Points
- New scenario-aware pose mock plugs into `TrackerController` constructor (same slot as `obMockPoseTracker`)
- V-marker overlay integrates into `src/main.py` debug visualization (around line 342 where pixel error is computed)
- Tests go in `tests/test_scenario_validation.py` (or similar) alongside existing test files

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 05-scenario-validation*
*Context gathered: 2026-03-01*
