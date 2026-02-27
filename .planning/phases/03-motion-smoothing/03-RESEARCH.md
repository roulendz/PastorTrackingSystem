# Phase 3: Motion Smoothing - Research

**Researched:** 2026-02-27
**Domain:** Motion profiling, signal filtering, confidence-weighted control
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Motion profile feel**: Slow human pan style with gentle ease-in, gentle coast to stop, symmetric S-curves for accel/decel, smooth direction reversals (decel-pause-accel through zero), camera matches pastor's pace, speed adapts smoothly, church broadcast style, dead zone preserved alongside S-curve smoothing, S-curve parameters exposed in DearPyGui settings panel
- **Home return behavior**: 1-2 second delay before starting return, delay is runtime-tunable, S-curve return at same speed as normal tracking, cancel return immediately if pastor leaves safe zone during mid-return, zero overshoot at home position (zero velocity landing), safe zone definition unchanged, camera completely locked at home when pastor in safe zone (zero movement from gestures), slightly faster S-curve re-engagement when leaving home
- **Jitter filtering**: Absolutely zero visible camera movement when pastor is stationary, jitter filter parameters exposed in DearPyGui settings panel
- **Confidence-based response**: Gradual blend where tracking strength scales smoothly with confidence, below hold threshold camera holds position entirely, sustained low confidence triggers home return timer, confidence threshold and blend curve exposed in DearPyGui settings panel

### Claude's Discretion
- Filter placement in the pipeline (pre-control vs post-control)
- Jitter detection algorithm (sustained direction, magnitude, or hybrid approach)
- Whether to blend tracking during safe-zone return or suspend it
- Whether to add velocity-based pass-through detection on top of the delay timer
- S-curve mathematical implementation details
- Exact default values for all tunable parameters

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MOTN-01 | Camera movements use jerk-limited (S-curve) motion profiles instead of trapezoidal acceleration -- no visible start/stop jerk | S-curve velocity profiler using smoothstep/sigmoid math applied at command output stage; Ruckig library evaluated but hand-rolled quintic smoothstep recommended for simplicity |
| MOTN-02 | Home return uses S-curve easing -- camera gently decelerates to home position, not linear velocity clamp | Replace `_compute_home_return_target_angle()` linear clamp with S-curve profiler; add delay timer and cancellation logic |
| MOTN-03 | Pose detection noise is filtered before control input using adaptive filter (OneEuroFilter or latching) -- suppresses jitter without adding lag on real movements | OneEuroFilter (~35 lines, zero dependencies) applied to pose X coordinate before angle error calculation; adaptive cutoff handles jitter-vs-lag tradeoff |
| MOTN-04 | Control gain scales with detection confidence -- low confidence produces gentle corrections, high confidence allows full tracking speed | Confidence scaling factor multiplied into control correction; smooth blend from 0.0 to 1.0 over configurable confidence range with hold-position below threshold |
</phase_requirements>

## Summary

Phase 3 transforms the camera's motion from mechanically correct but robotic into smooth, human-operated-looking movement. This requires four distinct but interacting subsystems: (1) an S-curve velocity profiler that shapes all motor commands with jerk-limited acceleration/deceleration, (2) a home return state machine with delay timer, S-curve easing, and cancellation, (3) a OneEuroFilter on pose detection input to suppress jitter without adding lag, and (4) confidence-based gain scaling that reduces tracking aggressiveness for uncertain detections.

The existing codebase is well-structured for these additions. The control pipeline in `TrackerController._execute_centering_control_algorithm()` provides clear injection points: the OneEuroFilter goes before the control algorithm (filtering `flPersonCenterXPixels`), the confidence scaling multiplies the control algorithm's output, and the S-curve profiler shapes the final motor command. The home return logic in `_compute_home_return_target_angle()` needs replacement with a state machine that manages delay, S-curve profiling, and cancellation.

**Primary recommendation:** Implement all four subsystems as pure-Python modules with no new external dependencies. The OneEuroFilter is ~35 lines of standard-library-only code. The S-curve profiler uses quintic smoothstep math (numpy already available). Ruckig is powerful but overkill for a single-axis system with simple state-to-state moves -- the complexity of a C++ binding dependency outweighs its benefits here.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| numpy | 1.24.3 | Math for smoothstep, interpolation, filter computations | Already in project; provides efficient array math |
| Python stdlib (math) | 3.10 | OneEuroFilter core math (exp, pi, sqrt) | Zero-dependency filter implementation |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| dearpygui | 1.11.1 | Runtime tuning sliders for all new parameters | Already in project; new sliders for S-curve, jitter, confidence params |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled S-curve profiler | Ruckig (pip install ruckig) | Ruckig is time-optimal and mathematically rigorous, but adds a C++ compiled dependency, is overkill for 1-DOF simple moves, and STATE.md flags "Ruckig + AccelStepper interaction needs empirical testing." Hand-rolled quintic smoothstep is adequate and debuggable. |
| Hand-rolled OneEuroFilter | OneEuroFilter PyPI package | PyPI package adds a dependency for ~35 lines of code. Hand-rolling from the reference implementation is trivial, avoids dependency, and allows Hungarian notation naming. |
| OneEuroFilter | Moving average / EMA | Moving average adds fixed lag regardless of speed. OneEuroFilter's adaptive cutoff is specifically designed for the jitter-vs-lag tradeoff in interactive tracking. |
| OneEuroFilter | Kalman filter | Out of scope per REQUIREMENTS.md ("Predictive person position (Kalman) -- frame-to-frame displacement is tiny at 30 FPS, prediction causes overshoot"). OneEuroFilter is reactive, not predictive. |

**Installation:**
```bash
# No new packages needed -- all implementations use existing dependencies
pip install -r requirements.txt  # unchanged
```

## Architecture Patterns

### Recommended Module Structure
```
src/
├── control/
│   ├── control_algorithm.py         # Existing -- unchanged
│   ├── tracker_controller.py        # Modified -- integrates all new subsystems
│   ├── motion_profiler.py           # NEW -- S-curve velocity profiler
│   └── home_return_controller.py    # NEW -- Home return state machine
├── tracking/
│   ├── pose_tracker.py              # Existing -- unchanged
│   └── pose_filter.py               # NEW -- OneEuroFilter for pose input
├── utilities/
│   └── config_manager.py            # Modified -- new config fields
└── ui/
    └── live_settings_panel.py        # Modified -- new tuning sliders
```

### Pattern 1: S-Curve Velocity Profiler (Motion Profiler)

**What:** A stateful object that accepts raw target-angle commands and outputs smoothed target-angle commands using jerk-limited velocity profiles. The profiler maintains internal velocity state and applies symmetric S-curve acceleration/deceleration.

**When to use:** Every motor command passes through this profiler before being sent to the motor interface. It sits between the control algorithm output and `send_move_to_angle_command()`.

**Mathematical basis:** Quintic smoothstep (5th-order Hermite interpolation):
```python
# Quintic smoothstep: f(t) = 6t^5 - 15t^4 + 10t^3
# Has zero first AND second derivatives at t=0 and t=1
# This gives S-curve with zero jerk at start/end
def _quintic_smoothstep(flT: float) -> float:
    """Map t in [0,1] to smoothed [0,1] with zero velocity and acceleration at endpoints."""
    flT = max(0.0, min(1.0, flT))
    return flT * flT * flT * (flT * (flT * 6.0 - 15.0) + 10.0)
```

**Implementation approach -- velocity-domain S-curve profiler:**
```python
class MotionProfiler:
    """
    Jerk-limited velocity profiler for smooth camera motion.

    Accepts a desired velocity (from control algorithm) and outputs
    a smoothed velocity that respects acceleration and jerk limits.
    Uses exponential smoothing with adaptive rate for S-curve-like behavior.
    """
    def __init__(
        self,
        flMaxVelocityDegreesPerSecond: float = 30.0,
        flAccelerationTimeSeconds: float = 0.5,
        flDecelerationTimeSeconds: float = 0.5,
    ):
        self._flCurrentVelocity = 0.0
        self._flMaxVelocity = flMaxVelocityDegreesPerSecond
        self._flAccelTime = flAccelerationTimeSeconds
        self._flDecelTime = flDecelerationTimeSeconds

    def compute_smoothed_velocity(
        self, flDesiredVelocity: float, flDeltaTimeSeconds: float
    ) -> float:
        """
        Apply S-curve smoothing to transition from current velocity
        toward desired velocity.
        """
        # Determine if accelerating or decelerating
        flDelta = flDesiredVelocity - self._flCurrentVelocity
        if abs(flDelta) < 0.001:
            return self._flCurrentVelocity

        # Use different time constants for accel vs decel
        bDecelerating = abs(flDesiredVelocity) < abs(self._flCurrentVelocity)
        flTimeConstant = self._flDecelTime if bDecelerating else self._flAccelTime

        # Exponential approach with S-curve shaping
        if flTimeConstant > 0.0:
            flAlpha = 1.0 - math.exp(-flDeltaTimeSeconds / (flTimeConstant * 0.2))
        else:
            flAlpha = 1.0

        self._flCurrentVelocity += flAlpha * flDelta
        return self._flCurrentVelocity
```

**Key design insight:** Rather than computing full time-optimal trajectories (Ruckig-style), this profiler simply rate-limits velocity changes with an exponential approach that naturally produces S-curve-like acceleration profiles. This is simpler, fully deterministic, has no dependency on trajectory duration estimation, and produces the "gentle camera operator" feel the user wants. The acceleration/deceleration time constants directly control how fast the camera ramps up/down.

### Pattern 2: OneEuroFilter for Pose Jitter Suppression

**What:** An adaptive low-pass filter applied to the pose detection X-coordinate before it enters the control pipeline. At low speeds (stationary pastor), the filter aggressively smooths, suppressing jitter. At high speeds (walking pastor), the filter backs off to maintain responsiveness.

**When to use:** Applied to `flPersonCenterXPixels` from `PoseResult` before angle error computation in `_execute_centering_control_algorithm()`.

**Implementation (~35 lines, zero external dependencies):**
```python
import math

def _smoothing_factor(flTimeDelta: float, flCutoffHz: float) -> float:
    """Compute exponential smoothing factor from time delta and cutoff frequency."""
    flR = 2.0 * math.pi * flCutoffHz * flTimeDelta
    return flR / (flR + 1.0)

def _exponential_smoothing(flAlpha: float, flRaw: float, flPrevious: float) -> float:
    """Apply exponential smoothing."""
    return flAlpha * flRaw + (1.0 - flAlpha) * flPrevious

class OneEuroFilter:
    """
    Adaptive low-pass filter for jitter reduction.

    Parameters:
        flMinCutoffHz: Minimum cutoff frequency (lower = less jitter, more lag)
        flBeta: Speed coefficient (higher = less lag at high speeds)
        flDerivativeCutoffHz: Cutoff for the derivative computation
    """
    def __init__(
        self,
        flInitialTimestamp: float,
        flInitialValue: float,
        flMinCutoffHz: float = 1.0,
        flBeta: float = 0.0,
        flDerivativeCutoffHz: float = 1.0,
    ):
        self._flMinCutoffHz = flMinCutoffHz
        self._flBeta = flBeta
        self._flDerivativeCutoffHz = flDerivativeCutoffHz
        self._flPreviousRaw = flInitialValue
        self._flPreviousFiltered = flInitialValue
        self._flPreviousDerivative = 0.0
        self._dPreviousTimestamp = flInitialTimestamp

    def filter_value(self, dTimestamp: float, flRawValue: float) -> float:
        """Filter a new raw value, returning the smoothed result."""
        flDt = dTimestamp - self._dPreviousTimestamp
        if flDt <= 0.0:
            return self._flPreviousFiltered

        # Estimate derivative (speed)
        flDerivative = (flRawValue - self._flPreviousRaw) / flDt
        flAlphaD = _smoothing_factor(flDt, self._flDerivativeCutoffHz)
        flSmoothedDerivative = _exponential_smoothing(flAlphaD, flDerivative, self._flPreviousDerivative)

        # Adaptive cutoff based on speed
        flCutoff = self._flMinCutoffHz + self._flBeta * abs(flSmoothedDerivative)
        flAlpha = _smoothing_factor(flDt, flCutoff)
        flFiltered = _exponential_smoothing(flAlpha, flRawValue, self._flPreviousFiltered)

        # Store state
        self._flPreviousRaw = flRawValue
        self._flPreviousFiltered = flFiltered
        self._flPreviousDerivative = flSmoothedDerivative
        self._dPreviousTimestamp = dTimestamp
        return flFiltered
```

**Recommended starting parameters for this system (30 FPS, ~6.8 deg FOV, pixel units):**
- `flMinCutoffHz = 1.0` -- aggressive smoothing when stationary (1 Hz cutoff at rest)
- `flBeta = 0.007` -- responsive at walking speed (~2 deg/s = ~380 px/s at this FOV)
- `flDerivativeCutoffHz = 1.0` -- standard derivative smoothing

These are starting points; the user will tune them live via the DearPyGui panel.

### Pattern 3: Confidence-Based Gain Scaling

**What:** A multiplier applied to the control correction output that scales from 0.0 (hold position) to 1.0 (full correction) based on detection confidence.

**When to use:** After `calculate_correction_from_error()` returns a correction, multiply by the confidence scale factor before applying to target angle.

```python
def compute_confidence_scale_factor(
    flConfidence: float,
    flHoldThreshold: float = 0.3,
    flFullThreshold: float = 0.7,
) -> float:
    """
    Compute gain scaling factor from detection confidence.

    Below flHoldThreshold: returns 0.0 (hold position)
    Above flFullThreshold: returns 1.0 (full correction)
    Between: smooth interpolation using smoothstep
    """
    if flConfidence <= flHoldThreshold:
        return 0.0
    if flConfidence >= flFullThreshold:
        return 1.0
    flT = (flConfidence - flHoldThreshold) / (flFullThreshold - flHoldThreshold)
    # Smoothstep for gentle transition
    return flT * flT * (3.0 - 2.0 * flT)
```

### Pattern 4: Home Return State Machine

**What:** A state machine managing the home-return behavior with delay timer, S-curve profiling, and instant cancellation.

**States:**
1. `TRACKING` -- normal tracking, no home return active
2. `SAFE_ZONE_DELAY` -- pastor in safe zone, waiting for delay timer to expire before starting return
3. `RETURNING_HOME` -- actively S-curve returning to home (0 deg)
4. `AT_HOME` -- at home position, camera locked (zero output)

**Transitions:**
- `TRACKING -> SAFE_ZONE_DELAY`: person enters safe zone (angle within deadband)
- `SAFE_ZONE_DELAY -> RETURNING_HOME`: delay timer expires (~1-2 seconds)
- `SAFE_ZONE_DELAY -> TRACKING`: person leaves safe zone before timer expires
- `RETURNING_HOME -> TRACKING`: person leaves safe zone during return (cancel immediately, resume tracking with slightly faster re-engagement S-curve)
- `RETURNING_HOME -> AT_HOME`: motor reaches home position (0 deg) with zero velocity
- `AT_HOME -> TRACKING`: person leaves safe zone

**Key behavior:** When in `AT_HOME` state, the control output is forced to 0.0 regardless of any jitter in pose detection. This ensures "camera completely locked at home" per the user decision.

### Anti-Patterns to Avoid

- **Filtering the motor output instead of the pose input:** Filtering the motor command introduces lag between the control algorithm's decision and what the motor does. This breaks the tight feedback loop. Filter the input (pose), not the output.
- **Using the S-curve profiler inside the control algorithm:** The profiler must sit OUTSIDE the control loop (between control output and motor command), not inside the PID/P/Velocity algorithm. Otherwise it interferes with the algorithm's dynamics (e.g., PID integral windup).
- **Applying confidence scaling before the deadband check:** The deadband check uses the raw angle error. Confidence scaling applies to the correction magnitude after the control algorithm has computed it.
- **Resetting the OneEuroFilter on every frame:** The filter maintains state across frames. Only reset it when tracking starts fresh (e.g., detection recovered after loss) or when the tracking mode is toggled.
- **Hard-coding S-curve parameters:** Every S-curve, jitter, and confidence parameter MUST be exposed in the DearPyGui settings panel. This is a feel-intensive phase that requires live tuning.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Adaptive jitter filter | Custom moving average or FIR filter | OneEuroFilter (hand-ported, ~35 lines) | OneEuroFilter is the established standard for pose tracking jitter; moving average adds fixed lag; EMA lacks adaptive cutoff |
| Complex trajectory planning | Full 7-segment S-curve trajectory planner | Simple velocity-domain exponential profiler | Full trajectory planning requires solving cubic/quintic equations for time-optimal segments, handling all edge cases (target changes mid-trajectory, very short moves). The velocity-domain approach is simpler and adequate for camera panning. |

**Key insight:** This system does not need time-optimal trajectory planning. It needs motion that LOOKS smooth and human-like. An exponential velocity smoother with tunable time constants achieves the same visual result with far less complexity than a full jerk-limited trajectory planner.

## Common Pitfalls

### Pitfall 1: Filter-Induced Deadband Failure
**What goes wrong:** The OneEuroFilter smooths the pose position, which means when the pastor is stationary but the raw pose jitters, the filtered position may slowly drift. If this drift crosses the deadband boundary, the camera starts moving despite the pastor being still.
**Why it happens:** The OneEuroFilter is not a thresholding mechanism -- it smooths but does not eliminate signal drift entirely.
**How to avoid:** The deadband check must use the filtered signal AND have a minimum threshold. The existing `flControlDeadbandDegrees` (default 0.3 deg) provides ~57 pixels of tolerance at 6.8 deg FOV, which is far larger than filtered jitter. The OneEuroFilter reduces jitter from ~5-10px to <1px, and the deadband catches any remaining drift.
**Warning signs:** Camera slowly drifts when pastor is stationary at the lectern.

### Pitfall 2: S-Curve Profiler State on Target Change
**What goes wrong:** When the control algorithm changes the desired velocity direction (e.g., pastor reverses direction), the profiler must smoothly decelerate through zero before accelerating in the new direction. Naive implementations snap velocity to zero or produce a discontinuity.
**Why it happens:** The profiler forgets that the camera has momentum in the old direction.
**How to avoid:** The velocity profiler maintains a continuous internal velocity state. Direction reversals are just changes in desired velocity sign -- the exponential approach naturally decelerates, passes through zero, and accelerates in the new direction. No special-case code needed.
**Warning signs:** Camera "flicks" briefly when pastor reverses walking direction.

### Pitfall 3: Home Return Cancellation Smoothness
**What goes wrong:** When the pastor leaves the safe zone during a home return, the camera snaps from "returning home" to "tracking pastor." If the return was in progress, the camera had velocity toward home but now needs velocity toward the pastor.
**Why it happens:** The home return state is discarded and tracking resumes with fresh profiler state.
**How to avoid:** On cancellation, DO NOT reset the motion profiler. Let the profiler's current velocity (toward home) naturally decelerate and redirect toward the new tracking target. The profiler handles this automatically as a velocity direction change.
**Warning signs:** Visible jerk when pastor steps out of safe zone during home return.

### Pitfall 4: Confidence Timer vs Detection Loss Timer Overlap
**What goes wrong:** Phase 3 adds "sustained low confidence triggers home return timer." Phase 4 adds "detection loss triggers hold + home return." These can conflict -- if confidence drops but detection is not lost, which timer runs?
**Why it happens:** Two different systems (confidence scaling and detection loss handling) both trigger home return with different semantics.
**How to avoid:** Phase 3 handles ONLY confidence-based gain scaling and its home return trigger. Phase 4 handles complete detection loss (bPersonWasDetected = False). The confidence hold-threshold (below which we hold position) should be ABOVE the minimum tracking confidence that PoseTracker considers "detected." This creates a clear layering: confidence < hold_threshold (Phase 3: hold position) vs detection_lost (Phase 4: hold + timeout + return).
**Warning signs:** Camera returns home when pastor is visible but partially occluded, before the Phase 3 timer expires.

### Pitfall 5: DearPyGui Thread Safety with Filter State
**What goes wrong:** Changing filter parameters from the DearPyGui thread while the main tracking loop reads them causes race conditions.
**Why it happens:** DearPyGui runs in a separate thread (see `live_settings_panel.py` line 637-638).
**How to avoid:** Filter parameters (flMinCutoffHz, flBeta, etc.) are simple floats. Python's GIL makes float assignment atomic for CPython. The DearPyGui callback sets the attribute, and the next main-loop iteration reads the new value. No explicit locking needed for parameter updates (same pattern used by existing config attribute updates throughout the codebase).
**Warning signs:** Occasional NaN or infinite values from the filter during parameter changes.

## Code Examples

### Integration Point: TrackerController._execute_centering_control_algorithm()

The main integration happens in this method. Here is the conceptual flow after all Phase 3 changes:

```python
def _execute_centering_control_algorithm(self, obCurrentSample: TrackingSample):
    # --- Skip if no person detected (Phase 4 will add hold logic here) ---
    if not obCurrentSample.bPersonWasDetected:
        return

    # --- PHASE 3 ADDITION: Confidence-based gate ---
    flConfidenceScale = compute_confidence_scale_factor(
        obCurrentSample.flPersonConfidenceScore,
        self.flConfidenceHoldThreshold,
        self.flConfidenceFullThreshold
    )
    if flConfidenceScale <= 0.0:
        # Below hold threshold: freeze camera at current position
        # Start low-confidence home return timer if needed
        return

    # --- Existing: Calculate pixel offset from center ---
    iImageWidth, _ = self.obCameraInterface.get_frame_dimensions()

    # --- PHASE 3 ADDITION: Filter pose input ---
    flFilteredX = self._obPoseFilter.filter_value(
        obCurrentSample.dSampleTimestampSeconds,
        obCurrentSample.flPersonCenterXPixels
    )
    flPixelOffset = flFilteredX - (iImageWidth / 2.0)

    flAnglePerPixel = self._get_angle_per_pixel_degrees(iImageWidth)
    flAngleError = flPixelOffset * flAnglePerPixel
    flPersonAngleRelativeToHome = obCurrentSample.flMotorAngleDegrees + flAngleError

    # --- Compute dt (existing logic) ---
    # ... (unchanged) ...

    # --- PHASE 3 ADDITION: Home return state machine ---
    eHomeState = self._obHomeReturnController.update(
        flPersonAngleRelativeToHome,
        self.flDeadbandDegrees,
        flDeltaTimeSeconds
    )
    if eHomeState == HomeReturnState.AT_HOME:
        # Camera locked at home -- output nothing
        return
    if eHomeState == HomeReturnState.RETURNING_HOME:
        # Use home return controller's target
        flNewTargetAngle = self._obHomeReturnController.get_target_angle()
    else:
        # --- Existing: Control algorithm ---
        flCorrection = self.obControlAlgorithm.calculate_correction_from_error(
            flAngleError, flDeltaTimeSeconds
        )
        # --- PHASE 3 ADDITION: Apply confidence scaling ---
        flCorrection *= flConfidenceScale
        flNewTargetAngle = obCurrentSample.flMotorAngleDegrees + flCorrection

    # --- PHASE 3 ADDITION: S-curve velocity profiling ---
    flSmoothedTarget = self._obMotionProfiler.compute_smoothed_target(
        flNewTargetAngle,
        obCurrentSample.flMotorAngleDegrees,
        flDeltaTimeSeconds
    )

    # --- Existing: Clamp and send ---
    flSmoothedTarget = max(self.flMinimumMotorAngleDegrees,
                           min(self.flMaximumMotorAngleDegrees, flSmoothedTarget))
    self.obMotorInterface.send_move_to_angle_command(flSmoothedTarget)
```

### New Config Fields for SystemConfiguration

```python
# Motion profiling (MOTN-01)
flMotionAccelerationTimeSeconds: float = 0.4     # S-curve ramp-up time
flMotionDecelerationTimeSeconds: float = 0.5     # S-curve ramp-down time (slightly longer = coast feel)
flMotionMaxVelocityDegreesPerSecond: float = 30.0  # Overall velocity cap

# Home return (MOTN-02)
flHomeReturnDelaySeconds: float = 1.5            # Wait before starting return
flHomeReturnAccelerationTimeSeconds: float = 0.5 # S-curve for home return
flHomeReturnReengagementTimeSeconds: float = 0.3 # Faster S-curve when leaving home

# Jitter filtering (MOTN-03)
flPoseFilterMinCutoffHz: float = 1.0             # Low-speed jitter suppression
flPoseFilterBeta: float = 0.007                  # Speed responsiveness
flPoseFilterDerivativeCutoffHz: float = 1.0      # Derivative smoothing

# Confidence scaling (MOTN-04)
flConfidenceHoldThreshold: float = 0.3           # Below this: hold position
flConfidenceFullThreshold: float = 0.7           # Above this: full correction
flConfidenceLowTimeoutSeconds: float = 5.0       # Sustained low conf -> home return
```

### DearPyGui Slider Additions

New sliders follow the existing pattern in `live_settings_panel.py`:

```python
# Motion Smoothing section
_add_section_header_with_tooltip("Motion Smoothing", dTips.get("Motion", ""))
_add_slider_with_range("flMotionAccelerationTimeSeconds", obConfig.flMotionAccelerationTimeSeconds, dItems,
    fnOnChange=lambda v: setattr(obConfig, 'flMotionAccelerationTimeSeconds', float(v)), iWidth=500)
_add_slider_with_range("flMotionDecelerationTimeSeconds", obConfig.flMotionDecelerationTimeSeconds, dItems,
    fnOnChange=lambda v: setattr(obConfig, 'flMotionDecelerationTimeSeconds', float(v)), iWidth=500)

# Jitter Filter section
_add_section_header_with_tooltip("Jitter Filter", dTips.get("Jitter", ""))
_add_slider_with_range("flPoseFilterMinCutoffHz", obConfig.flPoseFilterMinCutoffHz, dItems,
    fnOnChange=lambda v: (
        setattr(obConfig, 'flPoseFilterMinCutoffHz', float(v)),
        setattr(obTracker._obPoseFilter, '_flMinCutoffHz', float(v))
    ), iWidth=500)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Trapezoidal velocity profiles | Jerk-limited S-curve profiles | Standard since ~2015 in broadcast robotics | Eliminates visible start/stop jerk |
| Fixed cutoff low-pass filters | Adaptive filters (OneEuroFilter) | CHI 2012, widely adopted by 2018 | Jitter suppression without lag penalty |
| Binary confidence thresholds | Continuous confidence scaling | Common in modern pose tracking systems | Smoother transitions, fewer false triggers |
| Linear velocity clamp for home return | State machine with delay + S-curve | Standard in broadcast auto-tracking | Professional look, eliminates premature returns |

**Deprecated/outdated:**
- Simple EMA smoothing for pose jitter: replaced by OneEuroFilter's adaptive approach
- Linear velocity clamp for home return (currently in codebase as `_compute_home_return_target_angle`): replaced by S-curve state machine

## Open Questions

1. **Exact default parameter values for OneEuroFilter**
   - What we know: `flMinCutoffHz=1.0`, `flBeta=0.007` are reasonable starting points based on 30 FPS / ~6.8 deg FOV
   - What's unclear: Optimal values depend on actual MediaPipe jitter characteristics at this specific FOV and distance
   - Recommendation: Start with these defaults, document that runtime tuning is expected. The DearPyGui panel makes this easy.

2. **AccelStepper interaction with S-curve commands (from STATE.md blocker)**
   - What we know: The Arduino runs AccelStepper which has its own trapezoidal acceleration profile. Sending S-curve-profiled target angles from Python means two motion planners in series.
   - What's unclear: Whether AccelStepper's internal acceleration fights with the Python-side S-curve, causing oscillation or overshoot
   - Recommendation: The Python-side profiler outputs target POSITIONS (not velocities). AccelStepper treats each new target as a position command and plans its own accel/decel to reach it. Because the Python side sends smoothly-changing targets at 30 Hz, AccelStepper sees a slowly-moving target and tracks it closely without its own accel/decel kicking in hard. This should be fine -- the SimulatedMotorInterface can validate this behavior in tests before hardware testing.

3. **Whether to apply OneEuroFilter to Y coordinate as well**
   - What we know: The system only uses X for horizontal tracking. Y is only used for visualization.
   - What's unclear: Future phases might use Y for something.
   - Recommendation: Only filter X for now. Filtering Y is trivial to add later if needed.

## Sources

### Primary (HIGH confidence)
- OneEuroFilter reference implementation and paper: https://gery.casiez.net/1euro/ (CHI 2012, Casiez/Roussel/Vogel)
- Smoothstep mathematical definition: https://en.wikipedia.org/wiki/Smoothstep (quintic variant with zero first and second derivatives at endpoints)
- Ruckig documentation: https://docs.ruckig.com/ (evaluated but not recommended for this use case)

### Secondary (MEDIUM confidence)
- OneEuroFilter Python implementations: https://github.com/jaantollander/OneEuroFilter and https://github.com/casiez/OneEuroFilter (reference implementations, MIT/BSD licensed)
- Ruckig GitHub: https://github.com/pantor/ruckig (confirmed Windows CI, Python 3.10 support via pip)
- S-curve trajectory generation academic literature: https://www.mdpi.com/2079-9292/12/5/1135

### Tertiary (LOW confidence)
- Optimal OneEuroFilter parameters for pose tracking (flMinCutoffHz=0.004, flBeta=0.7 reported for one application): https://mohamedalirashad.github.io/FreeFaceMoCap/2021-12-25-filters-for-stability/ -- these values are application-specific and may not transfer directly

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all implementations use existing dependencies (numpy, stdlib math), no new packages needed
- Architecture: HIGH -- clear injection points in existing pipeline, well-understood patterns, existing codebase DI structure supports additions cleanly
- Pitfalls: HIGH -- identified from direct codebase analysis (threading model, existing deadband logic, home return implementation) and domain knowledge (filter-deadband interaction, profiler state on direction change)

**Research date:** 2026-02-27
**Valid until:** 2026-03-27 (stable domain, no fast-moving dependencies)
