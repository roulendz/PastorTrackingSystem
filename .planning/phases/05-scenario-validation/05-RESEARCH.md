# Phase 5: Scenario Validation - Research

**Researched:** 2026-03-01
**Domain:** Automated test scenario design, full-pipeline integration testing, deterministic simulation
**Confidence:** HIGH

## Summary

Phase 5 builds automated scenario tests that exercise the full tracking pipeline (scripted pose positions -> control algorithm -> SimulatedMotor) across a library of realistic pastor movement patterns. The codebase already has all the building blocks: `_build_test_controller()` and `_run_frames()` from `test_motion_integration.py` create a full pipeline with FakeClock, SimulatedMotorInterface, and mock PoseTracker; the `_compute_pixel_rms_for_trajectory()` pattern from `test_time_sync.py` demonstrates RMS validation; and the detection state machine from Phase 4 is fully operational with 105 tests passing.

The primary new work is: (1) a scenario-aware pose mock that feeds time-varying positions through the existing `_run_frames` infrastructure, (2) a set of individual test functions for each required movement pattern, (3) RMS validation that measures virtual center vs expected position at the full-pipeline level, and (4) a V-marker overlay in the debug visualization for real-world alignment verification.

**Primary recommendation:** Extend the existing `_run_frames()` helper with angle-space scenario waypoints converted to pixel positions, validate RMS at the pipeline output level (commanded angle vs expected angle), and keep each scenario as its own test function for isolation and debuggability.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Scenarios feed **scripted pose positions** directly into the pipeline (not synthetic video + MediaPipe) -- fast, deterministic, no MediaPipe dependency in tests
- Scenarios define pastor position in **angle-space** (degrees relative to center), not pixel-space -- the scenario runner converts angles to pixel positions using the known FOV (6.77 degrees / 1280 pixels)
- Each scenario waypoint can **optionally specify detection confidence** (0.0 to 1.0) -- default confidence is high (e.g., 0.9) when not specified
- Each of the 5 required scenarios (TEST-02) is its own **individual test function** -- isolated, easy to debug
- Plus **1-2 combined multi-segment scenarios** that chain movements (e.g., walk left -> pause -> walk right -> off FOV)
- **Varying speeds** implemented via `@pytest.mark.parametrize` on walk left/right tests (slow/medium/fast)
- One **additional detection dropout scenario** with intermittent flickering confidence during movement
- Walk outside FOV tests the **full detection cycle**: person moves past FOV edge, detection drops to zero, camera holds position, then person walks back and camera re-acquires
- Pause at lectern: pastor walks to center, stands still for **3-5 seconds**, validates **camera stability** -- no drift, no jitter from pose noise
- Scenarios run through the **full TrackerController pipeline**: scripted positions -> control algorithm -> SimulatedMotor
- **Summary stats on failure**: print RMS, max error, worst frame, and scenario segment where it failed; silent on pass
- V-marker rendered in the **debug visualization overlay** (not embedded in synthetic video) -- **always visible** in debug mode
- V-marker alignment at home position verified **programmatically** -- automated test assertion that virtual center line pixel equals V-marker pixel when motor angle is 0

### Claude's Discretion
- Scenario scripting format (data-driven vs composable functions vs other)
- V-marker visual design (shape, color, size)
- Exact measurement metric for 2-pixel validation (virtual center vs expected position, or person-center vs frame-center)
- Per-scenario vs uniform tolerance thresholds

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TEST-02 | Scripted scenarios include: pastor walks left, walks right, pauses at lectern, walks outside FOV (+-20 deg), varying walking speeds | Scenario waypoint format, angle-to-pixel conversion, `_run_frames()` extension with `vPersonXSequence`, `@pytest.mark.parametrize` for speed variants |
| TEST-03 | Synthetic video includes static lectern with V marker at center for visual verification of virtual center lock | V-marker overlay in `draw_visualization_overlay()`, programmatic alignment test (motor=0 -> V-marker pixel == center pixel) |
| TEST-06 | Test harness validates virtual center stays within tolerance (SYNC-06) across all scripted scenarios | Full-pipeline RMS computation (commanded angle vs expected angle), 2-pixel threshold assertion, diagnostic output on failure |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 9.0.2 | Test framework | Already in use, 105 tests passing |
| numpy | (installed) | RMS computation, array math | Already used in test_time_sync.py for np.sqrt, np.mean, np.square |
| unittest.mock | stdlib | Mock PoseTracker | Already used in conftest.py and test_motion_integration.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| OpenCV (cv2) | (installed) | V-marker rendering in debug overlay | Drawing V-shape on visualization frame in main.py |
| pytest.mark.parametrize | built-in | Speed variants for walk scenarios | `@pytest.mark.parametrize("flSpeedDegreesPerSecond", [1.0, 3.0, 8.0])` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Data-driven waypoint lists | Composable scenario builder functions | Data-driven is simpler for this number of scenarios; builder functions add abstraction overhead without clear benefit for 5-7 scenarios |
| RMS of commanded vs expected angle | RMS of person-center vs frame-center in pixels | Commanded vs expected is closer to what SYNC-06 actually measures; person-center vs frame-center includes control algorithm response lag which is intentional behavior, not error |

**Installation:** No new dependencies required. Everything is already installed.

## Architecture Patterns

### Recommended Project Structure
```
tests/
├── conftest.py                    # Existing fixtures (unchanged)
├── test_motion_integration.py     # Existing helpers: _build_test_controller, _run_frames (reused)
├── test_scenario_validation.py    # NEW: All scenario tests (TEST-02, TEST-06)
└── ...existing test files...

src/
└── main.py                        # V-marker overlay added to draw_visualization_overlay()
```

### Pattern 1: Scenario-Aware Pose Mock via _run_frames vPersonXSequence

**What:** The existing `_run_frames()` helper already accepts `vPersonXSequence` (a list of per-frame X pixel positions) and `bDetected`/`flConfidence` parameters. Scenarios define waypoints in angle-space that are pre-converted to a per-frame pixel sequence before calling `_run_frames()`.

**When to use:** Every scenario test.

**Example:**
```python
# Source: Existing _run_frames signature in test_motion_integration.py
def _build_scenario_pixel_sequence(
    vWaypoints: list,
    flFrameIntervalSeconds: float = 1.0 / 30.0,
    flFovDegrees: float = 6.77,
    iImageWidthPixels: int = 1280,
) -> tuple:
    """
    Convert angle-space waypoints to per-frame pixel X positions.

    Each waypoint: (flAngleDegrees, flDurationSeconds, flConfidence)
    Confidence defaults to 0.9 if not specified.

    Returns:
        (vPixelXSequence, vConfidenceSequence, vDetectedSequence)
    """
    flPixelsPerDegree = iImageWidthPixels / flFovDegrees  # ~189.07 px/deg
    flCenterPixel = iImageWidthPixels / 2.0  # 640.0

    vPixelX = []
    vConfidence = []
    vDetected = []

    for i, waypoint in enumerate(vWaypoints):
        flAngle = waypoint[0]
        flDuration = waypoint[1]
        flConf = waypoint[2] if len(waypoint) > 2 else 0.9

        iFrames = int(round(flDuration / flFrameIntervalSeconds))

        if i + 1 < len(vWaypoints):
            flNextAngle = vWaypoints[i + 1][0]
            # Linear interpolation between waypoints
            for j in range(iFrames):
                flT = j / max(iFrames, 1)
                flCurrentAngle = flAngle + flT * (flNextAngle - flAngle)
                flPixel = flCenterPixel + flCurrentAngle * flPixelsPerDegree
                vPixelX.append(flPixel)
                bDet = flConf > 0.0
                vConfidence.append(flConf)
                vDetected.append(bDet)
        else:
            # Last waypoint: hold position
            for j in range(iFrames):
                flPixel = flCenterPixel + flAngle * flPixelsPerDegree
                vPixelX.append(flPixel)
                bDet = flConf > 0.0
                vConfidence.append(flConf)
                vDetected.append(bDet)

    return vPixelX, vConfidence, vDetected
```

### Pattern 2: Full-Pipeline RMS Validation

**What:** After running a scenario through the pipeline, compute RMS between the commanded motor angle at each frame and the expected angle (where the person actually was in world-space). This is the full-pipeline analog of `_compute_pixel_rms_for_trajectory()` from test_time_sync.py.

**When to use:** Every scenario test assertion.

**Example:**
```python
def _compute_scenario_rms_pixels(
    vCommandedAngles: list,
    vExpectedAngles: list,
    flFovDegrees: float = 6.77,
    iImageWidthPixels: int = 1280,
) -> dict:
    """
    Compute RMS pixel error between commanded and expected motor angles.

    Returns dict with: flRmsPixels, flMaxPixelError, iWorstFrame
    """
    flDegreesPerPixel = flFovDegrees / iImageWidthPixels
    vErrors = []
    flMaxError = 0.0
    iWorstFrame = 0

    for i, (flCmd, flExp) in enumerate(zip(vCommandedAngles, vExpectedAngles)):
        flAngleError = abs(flCmd - flExp)
        flPixelError = flAngleError / flDegreesPerPixel
        vErrors.append(flPixelError)
        if flPixelError > flMaxError:
            flMaxError = flPixelError
            iWorstFrame = i

    flRms = float(np.sqrt(np.mean(np.square(vErrors))))
    return {
        'flRmsPixels': flRms,
        'flMaxPixelError': flMaxError,
        'iWorstFrame': iWorstFrame,
    }
```

### Pattern 3: Diagnostic Failure Output

**What:** On test failure, print summary statistics (RMS, max error, worst frame, scenario segment) so the developer can quickly identify what went wrong without re-running with verbose logging.

**When to use:** Every scenario assertion.

**Example:**
```python
obResult = _compute_scenario_rms_pixels(vCommandedAngles, vExpectedAngles)
assert obResult['flRmsPixels'] < 2.0, (
    f"SYNC-06 FAILED: RMS = {obResult['flRmsPixels']:.2f} px (limit: 2.0 px), "
    f"Max error = {obResult['flMaxPixelError']:.2f} px at frame {obResult['iWorstFrame']}, "
    f"Scenario: walk_left at {flSpeedDegreesPerSecond} deg/s"
)
```

### Anti-Patterns to Avoid
- **Feeding synthetic video through MediaPipe:** Per locked decision, scenarios use scripted positions. MediaPipe adds nondeterminism, slowness, and a heavy dependency to tests.
- **Testing pixel-space coordinates directly:** Scenarios define movement in angle-space, which is camera-resolution-independent. Convert at the boundary only.
- **Sharing mutable state between scenario tests:** Each test function must construct its own controller via `_build_test_controller()`. No shared test state.
- **Asserting on commanded angles during transient periods:** The control algorithm and S-curve profiler introduce intentional lag. Measure steady-state RMS, not instantaneous error on every frame.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Full-pipeline test controller | Custom TrackerController setup | `_build_test_controller()` from test_motion_integration.py | Already wired with FakeClock, SimulatedMotor, mock camera, mock pose, ProportionalController |
| Frame-by-frame simulation loop | Manual clock/motor/pose stepping | `_run_frames()` from test_motion_integration.py | Handles clock advance, motor simulation, pose mock update, camera timestamp update, and tick execution |
| RMS pixel error computation | Custom error math | Pattern from `_compute_pixel_rms_for_trajectory()` in test_time_sync.py | Already validates SYNC-06 at motor level; adapt for full-pipeline level |
| V-marker overlay rendering | Complex custom shape | OpenCV `cv2.line()` calls forming a V or inverted triangle | Two lines converging at a point is sufficient; matches the simplicity of existing overlay code |

**Key insight:** The existing test infrastructure already solves 80% of the problem. Phase 5 is primarily about defining scenarios (data) and wiring them through the existing test harness (infrastructure).

## Common Pitfalls

### Pitfall 1: Control Algorithm Lag Inflating RMS
**What goes wrong:** The full pipeline includes OneEuroFilter, confidence scaling, and S-curve profiling, all of which introduce intentional tracking lag. If RMS is measured as "commanded angle vs person's instantaneous world-angle," the lag causes RMS to exceed 2 pixels even though the system is working correctly.
**Why it happens:** SYNC-06's 2-pixel threshold was validated at the motor-interpolation level (test_time_sync.py), not at the full-pipeline level. The full pipeline adds control-loop lag on top of interpolation accuracy.
**How to avoid:** Measure RMS as "virtual center position vs expected position," where the expected position accounts for the person's world-angle. Alternatively, measure RMS only during steady-state segments (person not moving or moving at constant velocity), excluding transient acceleration/deceleration frames. Use a generous settling period after scenario transitions.
**Warning signs:** RMS consistently exceeds 2 pixels even during stationary person periods. If it does, the issue is jitter, not lag.

### Pitfall 2: Angle-to-Pixel Conversion Direction Error
**What goes wrong:** The sign convention for pixel offset vs motor angle is easy to get backwards. Person at +2 degrees from center means pixel X > 640 (right of center), and motor should move positive. If the conversion inverts this, the control loop diverges.
**Why it happens:** The conversion `flPixel = flCenterPixel + flAngle * flPixelsPerDegree` assumes positive angle = right of center, which matches the existing pipeline. But it's easy to accidentally negate.
**How to avoid:** Verify with the simplest possible scenario first (person at +1 degree, motor should move positive). Use the same FOV constants already defined in test_time_sync.py: `_FL_FOV_DEGREES = 6.77`, `_I_WIDTH_PIXELS = 1280`.
**Warning signs:** Motor moves in the opposite direction of the person in the first scenario test.

### Pitfall 3: FOV Half-Width vs Full-Width Edge Case
**What goes wrong:** "Walk outside FOV" means person's angle exceeds half the FOV (6.77 / 2 = 3.385 degrees) relative to the camera center. But the camera is tracking the person, so the person's angle relative to the camera center stays small. The person goes out of FOV when their world-angle minus motor angle exceeds half-FOV.
**Why it happens:** Confusion between world-angle (person relative to home) and camera-angle (person relative to current camera pointing direction).
**How to avoid:** In the "walk outside FOV" scenario, move the person to a world-angle where even after the motor tracks, the person's camera-angle (world_angle - motor_angle) exceeds half-FOV. This requires either moving very fast (motor can't keep up) or moving beyond the motor's angle range. Alternatively, define the scenario as: person walks past +/-20 degrees in world space (per TEST-02), which is well within motor range but the tracking tests the full pipeline's ability to follow.
**Warning signs:** "Walk outside FOV" test passes trivially because the motor tracks the person and they never actually leave the camera frame.

### Pitfall 4: FakeClock Not Advanced Before Motor Simulation
**What goes wrong:** Per locked decision [02-04]: "Clock must advance before simulation step for timestamp alignment." If `_run_frames()` is extended and this order is changed, interpolation timestamps mismatch motor state history.
**Why it happens:** The existing `_run_frames()` helper already has this correct order. The pitfall is if someone writes a new simulation loop that reverses the order.
**How to avoid:** Always use `_run_frames()` for scenario execution. If writing custom simulation loops, follow the pattern in `_run_frames()` exactly: `obFakeClock.advance_time_seconds(dt)` -> `obMotor.advance_simulation(dt)` -> `obTracker.execute_main_tracking_loop_tick()`.
**Warning signs:** Interpolation errors appear in tests that worked fine at the motor level.

### Pitfall 5: Scenario RMS Measurement Must Account for Tracking Intent
**What goes wrong:** During "walk outside FOV," the person disappears and the camera holds position (Phase 4 behavior). Measuring RMS during the hold period against the person's world-angle produces huge errors because the camera is supposed to hold, not track.
**Why it happens:** The SYNC-06 criterion ("virtual center stays within 2 pixels of correct position") applies to the virtual center line's relationship to the physical background, not to where the person is. During hold, the camera is stationary and the virtual center is correct by definition.
**How to avoid:** Segment RMS computation. During "person detected" segments, measure commanded angle vs person's world-angle. During "hold" segments, measure commanded angle stability (no drift). During "returning home" segments, don't include in RMS at all.
**Warning signs:** "Walk outside FOV" scenario has extremely high RMS (50+ pixels) because hold frames are being measured against the off-screen person position.

### Pitfall 6: OneEuroFilter Warmup Period
**What goes wrong:** The OneEuroFilter is lazy-initialized on first valid detection frame. The first few frames after initialization have the filter converging from an initial value, producing transient errors.
**Why it happens:** Per decision [03-02]: OneEuroFilter lazy-initialized on first valid detection frame. The filter needs a few samples to converge.
**How to avoid:** Add a warmup period (5-10 frames) at the start of each scenario before measuring RMS. Alternatively, exclude the first 10 frames from the RMS window.
**Warning signs:** RMS is high at the start of every scenario but settles quickly.

## Code Examples

Verified patterns from existing codebase:

### Building a Test Controller (from test_motion_integration.py)
```python
# Source: tests/test_motion_integration.py lines 28-86
def _build_test_controller(obFakeClock=None, flProportionalGain=1.0, obConfig=None):
    if obFakeClock is None:
        obFakeClock = FakeClock(dStartTimeSeconds=0.0)
    obMotor = SimulatedMotorInterface(obClock=obFakeClock)
    obMotor.connect_to_motor_controller()
    obMockCamera = Mock()
    obMockCamera.get_frame_dimensions.return_value = (1280, 720)
    obMockCamera.capture_frame_with_timestamp.return_value = (
        np.zeros((720, 1280, 3), dtype=np.uint8), obFakeClock.get_time_seconds()
    )
    obMockPose = Mock()
    obMockPose.detect_person_in_frame.return_value = PoseResult(
        flPersonCenterXPixels=640.0, flPersonCenterYPixels=360.0,
        bPersonWasDetected=True, flPersonConfidenceScore=0.9, vLandmarks=None
    )
    obAlgo = ProportionalController(flProportionalGain)
    if obConfig is None:
        obConfig = SystemConfiguration()
    obConfig.bAllowStartWithoutMotor = True
    obConfig.bEnableVisualization = False
    obConfig.flFieldOfViewDegrees = 6.77
    obTracker = TrackerController(
        obMotorInterface=obMotor, obCameraInterface=obMockCamera,
        obPoseTracker=obMockPose, obControlAlgorithm=obAlgo, obClock=obFakeClock
    )
    obTracker.apply_configuration(obConfig)
    obTracker.start_tracking_mode()
    return (obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig)
```

### Running Frames with Per-Frame Sequence (from test_motion_integration.py)
```python
# Source: tests/test_motion_integration.py lines 89-157
# The vPersonXSequence parameter already supports per-frame position sequences:
vCommandedAngles = _run_frames(
    obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
    iFrameCount=60,
    flConfidence=0.9,
    vPersonXSequence=vXSequence  # list of per-frame pixel X values
)
```

### V-Marker Overlay in Debug Visualization
```python
# Integration point: src/main.py draw_visualization_overlay(), around line 266-278
# Existing home line drawing:
iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))
cv2.line(obFrame, (iHomeLineX, 0), (iHomeLineX, iHeight), (255, 255, 0), 1)

# V-marker at iCenterX (lectern at center of frame):
# Draw a V shape at the bottom of the frame, centered at iHomeLineX
iVMarkerY = iHeight - 40  # 40px from bottom
iVMarkerSize = 20  # Half-width of V
cv2.line(obFrame, (iHomeLineX - iVMarkerSize, iVMarkerY - iVMarkerSize),
         (iHomeLineX, iVMarkerY), (0, 255, 255), 2)  # Left arm of V
cv2.line(obFrame, (iHomeLineX + iVMarkerSize, iVMarkerY - iVMarkerSize),
         (iHomeLineX, iVMarkerY), (0, 255, 255), 2)  # Right arm of V
```

### RMS Computation Pattern (from test_time_sync.py)
```python
# Source: tests/test_time_sync.py lines 68-114
flAngleError = abs(flInterpolated - flTrue)
flPixelError = flAngleError / _FL_DEGREES_PER_PIXEL
vPixelErrors.append(flPixelError)
# ...
flRms = float(np.sqrt(np.mean(np.square(vPixelErrors))))
```

### Existing Detection Handling Test (from test_detection_handling.py)
```python
# Source: tests/test_detection_handling.py lines 34-51
# Shows how to simulate detection loss and verify hold behavior:
_run_frames(obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=10, flPersonX=700.0, flConfidence=0.9)
flBaselineAngle = obTracker._flLastCommandedAngle
_run_frames(obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=1, bDetected=False)
```

## Design Recommendations (Claude's Discretion)

### Scenario Scripting Format: Data-Driven Waypoint Lists
**Recommendation:** Use simple data-driven waypoint tuples `(flAngleDegrees, flDurationSeconds, flConfidence)` rather than composable builder functions.

**Rationale:** With only 5-7 scenarios, a builder pattern adds abstraction without reducing complexity. Waypoint lists are self-documenting and can be read top-to-bottom as a timeline. The `_build_scenario_pixel_sequence()` helper converts them to the `vPersonXSequence` format that `_run_frames()` already accepts.

### V-Marker Visual Design: Inverted Triangle / V-Shape
**Recommendation:** Cyan inverted-V at bottom of frame, same color as the center line (0, 255, 255), 20px half-width, positioned at `iHomeLineX`. This ties visually to the existing home line.

**Rationale:** Simple, distinct from existing overlays (red=person, green=debug text, yellow=home line), and cheap to render with two `cv2.line()` calls. The V-shape is universally recognizable as a marker/pointer.

### Measurement Metric: Commanded Angle vs Person World-Angle, Segmented
**Recommendation:** Measure `abs(flCommandedAngle - flPersonWorldAngle)` converted to pixels, but only during "TRACKING" segments (person detected, control active). Exclude HOLDING and RETURNING_HOME frames from RMS. This aligns with SYNC-06's intent: the virtual center line tracks the physical background correctly while the camera is actively tracking.

**Rationale:** Including hold/return frames in RMS would either trivially pass (camera not moving = no error relative to background) or trivially fail (camera not following person = large error relative to person). Neither tells us about tracking accuracy.

### Tolerance: Uniform 2-Pixel Threshold
**Recommendation:** Use a uniform 2.0-pixel RMS threshold across all scenarios, matching SYNC-06. No per-scenario thresholds needed.

**Rationale:** SYNC-06 is a system-level requirement, not scenario-specific. If a particular scenario needs a relaxed threshold, it indicates a system issue, not a testing issue. The 2-pixel threshold has already been validated at the motor level (test_time_sync.py), so full-pipeline tests should achieve the same or better given that the control loop is slower than interpolation errors.

### Extension to _run_frames for Confidence and Detection Sequences
**Current limitation:** `_run_frames()` accepts a single `bDetected` and `flConfidence` for all frames, or `vPersonXSequence` for per-frame X positions. Scenarios need per-frame control of all three (X position, confidence, detection boolean).

**Recommendation:** Extend `_run_frames()` or create a wrapper `_run_scenario_segment()` that accepts parallel sequences for position, confidence, and detection status. This keeps the core `_run_frames()` intact for existing tests while adding scenario-level flexibility.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Synthetic video + MediaPipe | Scripted pose positions | Phase 5 decision | 100x faster tests, fully deterministic |
| Motor-level SYNC-06 validation only | Full-pipeline SYNC-06 validation | Phase 5 | End-to-end confidence that the whole chain works |
| Manual visual verification of V-marker | Programmatic alignment assertion | Phase 5 | Automated regression, no human in the loop |

**Deprecated/outdated:**
- `generate_test_video()` in conftest.py: Still available for camera interface tests but not used for scenario validation (per locked decision: scripted positions, not synthetic video)
- `obMockPoseTracker` fixture with static PoseResult: Not directly used for scenarios (scenarios need per-frame varying positions), but the pattern informs scenario mock design

## Open Questions

1. **How should "varying speeds" be quantified?**
   - What we know: TEST-02 requires "varying walking speeds." The parametrize decision calls for slow/medium/fast.
   - What's unclear: Exact angular velocities for slow/medium/fast. Typical pastor walking speed at 6.77-degree FOV needs translating to degrees/second.
   - Recommendation: Use 1.0 deg/s (slow, ~2 steps/second), 3.0 deg/s (medium, brisk walk), 8.0 deg/s (fast, urgent crossing). These are well within the 45 deg/s motor limit validated in SYNC-06 and represent realistic church stage movement at narrow FOV. Can be tuned during implementation.

2. **Should the _run_frames helper be extended in-place or wrapped?**
   - What we know: `_run_frames()` is imported by `test_detection_handling.py` from `test_motion_integration.py`. Changing its signature could break existing tests.
   - What's unclear: Whether adding optional parameters (`vConfidenceSequence`, `vDetectedSequence`) is safe or whether a separate helper is cleaner.
   - Recommendation: Add optional parameters with None defaults (backward-compatible). Alternatively, create `_run_scenario_frames()` in the new test file that calls `_run_frames()` in segments, one segment per waypoint. This avoids modifying any shared code.

3. **What constitutes "camera stability" during pause at lectern?**
   - What we know: Pause at lectern should validate no drift, no jitter. OneEuroFilter should suppress pose noise on a stationary subject.
   - What's unclear: Exact threshold for "no jitter." The OneEuroFilter was tuned to < 1px jitter in test_pose_filter.py, but full-pipeline jitter includes control loop response.
   - Recommendation: Measure commanded angle standard deviation during the pause period. It should be < 0.5 pixels equivalent (stricter than 2-pixel RMS to specifically catch jitter). If the filter is working, this should be trivially satisfied. Add small pose noise (~2px std) during the pause to stress-test the filter.

## Sources

### Primary (HIGH confidence)
- `tests/test_motion_integration.py` - `_build_test_controller()` and `_run_frames()` helpers, the direct integration point for scenario tests
- `tests/test_time_sync.py` - RMS computation pattern, FOV constants, SYNC-06 validation methodology
- `tests/test_detection_handling.py` - Detection state machine testing patterns, hold/recovery behavior verification
- `src/control/tracker_controller.py` - Full pipeline implementation including Phase 3 and Phase 4 additions
- `src/main.py` lines 252-358 - Visualization overlay structure and integration point for V-marker
- `tests/conftest.py` - Existing fixtures and mock patterns

### Secondary (MEDIUM confidence)
- FOV calculation validated in `test_fov_calculation.py`: 6.77 degrees / 1280 pixels = 0.00529 deg/pixel

### Tertiary (LOW confidence)
- Pastor walking speed estimates (1.0 / 3.0 / 8.0 deg/s) are approximate and based on general stage movement patterns, not measured data. Should be validated empirically but are reasonable starting points.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - No new libraries needed; all infrastructure exists
- Architecture: HIGH - Direct extension of established test patterns (`_build_test_controller`, `_run_frames`, RMS computation)
- Pitfalls: HIGH - Drawn from direct analysis of the existing codebase and its documented decisions
- Scenarios: MEDIUM - Walking speed values are estimates; scenario waypoint durations may need tuning

**Research date:** 2026-03-01
**Valid until:** 2026-03-31 (stable; no external dependencies to age)
