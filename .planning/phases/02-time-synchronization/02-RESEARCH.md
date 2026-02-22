# Phase 2: Time Synchronization - Research

**Researched:** 2026-02-22
**Domain:** Timing synchronization, motor angle interpolation, clock abstraction, control algorithm determinism
**Confidence:** HIGH

## Summary

Phase 2 fixes the core virtual center line drift by ensuring every camera frame is paired with an accurately interpolated motor angle at the exact moment of capture. The current codebase has two distinct timing issues: (1) control algorithms (`PIDController`, `VelocityController`) use `time.time()` internally to compute delta time, making them non-deterministic and mixing wall-clock time with monotonic timing elsewhere, and (2) `CameraInterface.capture_frame_with_timestamp()` uses `read()` which blocks until the frame is decoded, then timestamps *after* the blocking call, introducing variable latency between true capture moment and recorded timestamp.

The fix involves six coordinated changes: a Clock abstraction for dependency-injectable timing, switching `CameraInterface` to `grab()`/`retrieve()` with timestamp between them, upgrading motor angle interpolation from linear to physics-informed quadratic during acceleration/deceleration phases, replacing the EMA clock offset estimator with linear regression, adding explicit `flDeltaTime` parameter to control algorithm signatures, and adding dt clamping at the pipeline boundary. The existing `SimulatedMotorInterface` from Phase 1 provides the deterministic test foundation -- tests can use `FakeClock` plus `advance_simulation(dt)` to verify interpolation accuracy against known ground truth.

**Primary recommendation:** Build a thin Clock protocol (abc or Protocol class) with `RealClock` and `FakeClock` implementations. Thread it through all components as a constructor parameter. This single change unlocks deterministic testing for every timing-related behavior.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Interpolate motor angle from a timestamped history buffer -- not nearest-report
- Best accuracy is the priority: interpolation from buffered motor reports at the frame's capture timestamp
- Arduino motor report frequency is unknown -- researcher must investigate the firmware
- Physics-informed interpolation using known motor parameters (configured accel, max velocity from AccelStepper)
- Single smooth curve through direction reversals -- not three discrete phases (decel/stop/accel)
- Interpolation always runs -- no rest-detection bypass
- Operating angle range limited to +/-25 degrees for this setup (pastor motion envelope)
- When pastor enters home safe zone, motor smoothly returns to 0 degrees (home position for picture symmetry)
- Smooth but sticky: no jitter, but responsive tracking is the priority
- Single clock utility wrapping time.perf_counter() -- all components call it, including the motor background thread
- Clock utility supports dependency injection: RealClock for production, FakeClock for deterministic tests
- TrackingSample carries an absolute timestamp (from clock utility), not delta-time -- consumers compute dt
- Absolute perf_counter values as source of truth; derive relative/session-start values at boundaries if needed
- Control algorithms receive explicit dt parameter: calculate_correction_from_error(flError, flDeltaTime)
- dt computed at the pipeline boundary by the caller, not inside control algorithms
- dt clamped at the pipeline boundary (max ~2x frame interval, e.g., ~66ms for 30fps); skip control update if dt exceeds max
- Optional dt_min to avoid near-zero values
- On dropped frames: motor telemetry still recorded in history buffer, but control update skipped for that cycle
- Motor thread uses the same shared clock utility (not raw perf_counter) for consistency and testability
- 2-pixel threshold measured as RMS (or MAE) over a configurable window (default ~1 second) -- not per-frame instantaneous
- Transient exceedance during direction reversals is acceptable; must settle back within a configurable window (default ~0.75s)
- 45 deg/s is the validated operating limit -- tolerance guaranteed up to this speed
- 2-pixel RMS is primarily a validation/acceptance metric for Phase 2
- Debug overlay (gated behind debug flag) showing: pixel error (signed), raw motor angle, interpolated motor angle, delta between them
- RMS window length is a configurable parameter (default ~1s)
- Reversal settling window is a configurable parameter (default ~0.75s)
- Primary test validation: synthetic known trajectories (constant velocity, acceleration, direction change)
- Secondary: recorded real-world replays as a regression suite (Phase 5 scope for full scenario library)

### Claude's Discretion
- Frame timestamp placement relative to grab()/retrieve()
- Camera-to-motor latency offset calibration (whether needed and approach)
- Constant-velocity interpolation method (linear vs quadratic)
- Exact dt clamp values and skip-vs-clamp policy
- Clock utility API design details
- Debug overlay layout and formatting
- Exact RMS vs MAE choice for drift metric

### Deferred Ideas (OUT OF SCOPE)
- Runtime tracking quality degradation response (soft gain reduction, health signal) -- Phase 5
- Runtime drift-exceeded escalation policy (pause correction after sustained threshold breach) -- Phase 5
- UI indicator for tracking quality status -- Phase 5
- Recorded real-world replay regression suite -- Phase 5
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| time (stdlib) | Python 3.10+ | `time.perf_counter()` for monotonic timing | Guaranteed monotonic on Windows (CPython 3.10+), ~1us resolution via QueryPerformanceCounter |
| numpy | existing dep | Linear regression for clock offset, RMS calculation | Already in requirements.txt, standard for numerical computation |
| abc (stdlib) | Python 3.10+ | Abstract base class for Clock protocol | Standard Python pattern for DI interfaces |
| collections.deque | Python 3.10+ | Bounded history buffers for motor states and clock samples | Already used in motor_interface.py, O(1) append/pop |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| dataclasses (stdlib) | Python 3.10+ | Clock-related data structures | MotorState already uses this |
| threading (stdlib) | Python 3.10+ | Lock protection for shared clock/state | Already used for motor thread |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| abc.ABC for Clock | typing.Protocol | Protocol is structural typing (duck typing), ABC forces explicit inheritance. ABC is safer for this codebase's style |
| numpy.polyfit for regression | Manual least-squares | numpy already available, polyfit is well-tested and one-liner |
| deque for history | list with manual trimming | deque with maxlen handles trimming automatically, already used in codebase |

**Installation:**
```bash
# No new packages needed -- all dependencies already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
src/
  utilities/
    clock.py              # NEW: Clock protocol, RealClock, FakeClock
  interfaces/
    motor_interface.py    # MODIFIED: Accept Clock, use for all timestamps
    camera_interface.py   # MODIFIED: Accept Clock, grab()/retrieve() split
  control/
    control_algorithm.py  # MODIFIED: Add flDeltaTime parameter to signatures
    tracker_controller.py # MODIFIED: Accept Clock, dt clamping at boundary
  core/
    tracking_sample.py    # UNCHANGED (already carries absolute timestamp)
tests/
  test_clock.py           # NEW: Clock utility tests
  test_interpolation.py   # NEW: Motor angle interpolation accuracy tests
  test_time_sync.py       # NEW: End-to-end timing synchronization tests
  test_control_dt.py      # NEW: Control algorithm determinism tests
```

### Pattern 1: Clock Abstraction with Dependency Injection
**What:** A thin Clock interface that wraps time.perf_counter() with RealClock for production and FakeClock for deterministic tests.
**When to use:** Every component that needs a timestamp.
**Example:**
```python
# src/utilities/clock.py
from abc import ABC, abstractmethod
import time


class Clock(ABC):
    """Abstract clock interface for dependency injection."""

    @abstractmethod
    def get_time_seconds(self) -> float:
        """Return current time in seconds (monotonic)."""
        pass


class RealClock(Clock):
    """Production clock wrapping time.perf_counter()."""

    def get_time_seconds(self) -> float:
        return time.perf_counter()


class FakeClock(Clock):
    """Deterministic clock for testing. Time only advances when told."""

    def __init__(self, dStartTimeSeconds: float = 0.0):
        self._dCurrentTimeSeconds = dStartTimeSeconds

    def get_time_seconds(self) -> float:
        return self._dCurrentTimeSeconds

    def advance_time_seconds(self, dDeltaSeconds: float):
        """Advance clock by given amount."""
        self._dCurrentTimeSeconds += dDeltaSeconds

    def set_time_seconds(self, dTimeSeconds: float):
        """Set clock to specific time."""
        self._dCurrentTimeSeconds = dTimeSeconds
```

### Pattern 2: Camera grab()/retrieve() Split for Precise Timestamps
**What:** Replace `VideoCapture.read()` with separate `grab()` and `retrieve()` calls, placing the timestamp call between them.
**When to use:** CameraInterface.capture_frame_with_timestamp()
**Rationale:** `read()` = `grab()` + `retrieve()`. `grab()` acquires the raw frame from the camera driver buffer (fast, ~4ms when buffer has data). `retrieve()` decodes the frame (slower, involves demosaicing/JPEG decompression). The best timestamp of "when the physical frame was captured" is immediately after `grab()` returns, since `grab()` is the actual hardware acquisition step.

**Recommendation (Claude's discretion):** Place timestamp immediately AFTER `grab()` returns successfully. This is the closest approximation to the true capture moment that OpenCV provides. Placing it before `grab()` would include wait time; placing it after `retrieve()` would include decode time.

**Example:**
```python
def capture_frame_with_timestamp(self) -> Tuple[Optional[np.ndarray], float]:
    if not self.is_camera_device_open():
        return None, 0.0

    # grab() acquires raw frame from camera buffer
    bGrabbed = self.obVideoCapture.grab()

    # Timestamp IMMEDIATELY after grab -- closest to true capture moment
    dTimestampSeconds = self._obClock.get_time_seconds()

    if not bGrabbed:
        # Handle video file looping
        if self.sVideoFilePath:
            self.obVideoCapture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            bGrabbed = self.obVideoCapture.grab()
            dTimestampSeconds = self._obClock.get_time_seconds()
            if not bGrabbed:
                return None, dTimestampSeconds

    # retrieve() decodes the grabbed frame (slower step)
    bSuccess, obFrame = self.obVideoCapture.retrieve()

    if not bSuccess or obFrame is None:
        return None, dTimestampSeconds

    return obFrame, dTimestampSeconds
```

### Pattern 3: Physics-Informed Quadratic Motor Angle Interpolation
**What:** Interpolate motor angle at arbitrary timestamps using kinematic equations that account for acceleration/deceleration phases, not just linear interpolation between reported points.
**When to use:** `get_estimated_motor_angle_degrees(dAtTimestampSeconds)` on all motor interface implementations.

**Key physics equations for trapezoidal velocity profile:**
- **Acceleration phase:** `p(t) = p0 + v0 * dt + 0.5 * a * dt^2` (quadratic)
- **Constant velocity phase:** `p(t) = p0 + v_max * dt` (linear)
- **Deceleration phase:** `p(t) = p0 + v0 * dt - 0.5 * a * dt^2` (quadratic, negative accel)

Where: p0 = position at segment start, v0 = velocity at segment start, a = configured acceleration, dt = time since segment start.

**Recommendation (Claude's discretion):** Use quadratic interpolation during acceleration and deceleration phases, linear during constant velocity. The Arduino firmware reports `accelState` (0=stopped, 1=accel, 2=constant, 3=decel) in every feedback message, which can tag each history entry to select the correct interpolation formula. For the SimulatedMotorInterface, the internal state already knows velocity and acceleration.

### Pattern 4: Control Algorithm Explicit Delta Time
**What:** Change `calculate_correction_from_error(flError)` to `calculate_correction_from_error(flError, flDeltaTime)`, removing internal time.time() calls.
**When to use:** All ControlAlgorithm subclasses (P, PID, Velocity).
**Example:**
```python
class ControlAlgorithm(ABC):
    @abstractmethod
    def calculate_correction_from_error(
        self, flErrorDegrees: float, flDeltaTimeSeconds: float
    ) -> float:
        pass

class PIDController(ControlAlgorithm):
    def calculate_correction_from_error(
        self, flErrorDegrees: float, flDeltaTimeSeconds: float
    ) -> float:
        # No time.time() call -- dt comes from caller
        flProportionalTerm = self.flProportionalGain * flErrorDegrees

        self.flIntegralAccumulator += flErrorDegrees * flDeltaTimeSeconds
        self.flIntegralAccumulator = max(
            -self.flMaximumIntegralValue,
            min(self.flMaximumIntegralValue, self.flIntegralAccumulator)
        )
        flIntegralTerm = self.flIntegralGain * self.flIntegralAccumulator

        flErrorDerivative = (flErrorDegrees - self.flPreviousError) / flDeltaTimeSeconds
        flDerivativeTerm = self.flDerivativeGain * flErrorDerivative

        self.flPreviousError = flErrorDegrees

        return flProportionalTerm + flIntegralTerm + flDerivativeTerm
```

### Pattern 5: Delta Time Clamping at Pipeline Boundary
**What:** Compute dt from absolute timestamps at the call site (tracker_controller.py), clamp it, and decide whether to skip the control update.
**When to use:** `_execute_centering_control_algorithm()` in TrackerController.

**Recommendation (Claude's discretion):**
- `flDtMaxSeconds = 0.066` (2x frame interval at 30fps) -- skip control update if exceeded
- `flDtMinSeconds = 0.001` (1ms) -- clamp up to avoid near-zero division in derivative terms
- On skip: log at debug level, do not send motor command, do not reset previous timestamp (so next frame gets a valid dt)

### Pattern 6: Linear Regression for Arduino Clock Offset (SYNC-04)
**What:** Replace the current EMA (98%/2% weighting) for Arduino-to-PC clock offset with linear regression over a sliding window.
**When to use:** `_parse_feedback_message()` in MotorInterface.

The current code (line 342):
```python
self._dArduinoToPerfCounterOffsetSeconds = (
    0.98 * self._dArduinoToPerfCounterOffsetSeconds
) + (0.02 * dMeasuredOffset)
```

This EMA converges very slowly (98% weight on old value means it takes ~50 samples to half-converge, ~150 samples to 95%-converge). At 50Hz feedback rate, that is 3 seconds to reach useful accuracy. Linear regression over a sliding window (e.g., 50 samples = 1 second) provides near-optimal offset estimation from the first full window.

**Implementation approach:**
```python
# In MotorInterface.__init__:
self._vClockSyncSamples = deque(maxlen=50)  # 1 second at 50Hz

# In _parse_feedback_message:
self._vClockSyncSamples.append((dArduinoSeconds, dNow))
if len(self._vClockSyncSamples) >= 2:
    vArduino = np.array([s[0] for s in self._vClockSyncSamples])
    vPC = np.array([s[1] for s in self._vClockSyncSamples])
    # Linear fit: pc_time = slope * arduino_time + offset
    # slope should be ~1.0, offset is the clock difference
    vCoeffs = np.polyfit(vArduino, vPC, 1)
    flSlope = vCoeffs[0]
    flOffset = vCoeffs[1]
    # Convert arduino timestamp to PC time: pc_time = slope * arduino_time + offset
    dTimestampSeconds = flSlope * dArduinoSeconds + flOffset
```

### Anti-Patterns to Avoid
- **Calling time.time() anywhere:** Use the Clock abstraction everywhere. time.time() is wall-clock, not monotonic, and its resolution on Windows can be 15.6ms.
- **Calling time.perf_counter() directly in components:** Always go through the Clock interface for testability.
- **Computing dt inside control algorithms:** This makes them non-deterministic and untestable. dt is the caller's responsibility.
- **Using read() instead of grab()/retrieve():** read() combines acquisition and decoding, making it impossible to timestamp between them.
- **Bypassing interpolation when motor is "at rest":** The decision was locked: interpolation always runs. Rest detection adds edge cases for negligible savings.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Linear regression | Manual matrix math | `numpy.polyfit(x, y, 1)` | Numerically stable, well-tested, handles edge cases |
| RMS calculation | Manual sum-of-squares loop | `numpy.sqrt(numpy.mean(numpy.square(errors)))` | One-liner, vectorized, handles empty arrays |
| Bounded history buffer | List with manual len checks | `collections.deque(maxlen=N)` | O(1) append, automatic oldest-item eviction |
| Monotonic timestamps | Raw `time.perf_counter()` calls | Clock abstraction class | Enables deterministic testing via FakeClock |

**Key insight:** The primary complexity in this phase is not algorithmic -- the math is basic kinematics and linear regression. The complexity is in *threading the clock through all components consistently* and *ensuring every timestamp in the system comes from the same source*. The Clock abstraction is the architectural keystone.

## Common Pitfalls

### Pitfall 1: Mixed Clock Sources
**What goes wrong:** Some components use time.perf_counter(), others use time.time(), motor thread uses its own perf_counter() calls while main thread uses the Clock abstraction.
**Why it happens:** Incremental migration leaves some call sites unconverted.
**How to avoid:** After all changes, grep the entire `src/` directory for `time.time()` and raw `time.perf_counter()` calls. The ONLY raw perf_counter call should be inside `RealClock.get_time_seconds()`. Zero hits for `time.time()`.
**Warning signs:** Timestamps that are close but not identical when they should be the same; intermittent test failures.

### Pitfall 2: Stale Motor History During Fast Direction Reversals
**What goes wrong:** During a fast direction change, the motor history buffer may have entries from before the reversal command. Linear interpolation between pre-reversal and post-reversal points produces incorrect intermediate angles because the motor actually decelerated, stopped, and re-accelerated.
**Why it happens:** The history buffer only records reported positions at feedback intervals (20ms on Arduino), not the reversal command itself.
**How to avoid:** Use the accelState field from Arduino feedback (0=stopped, 1=accel, 2=constant, 3=decel) to select the appropriate interpolation formula for each segment. For SimulatedMotorInterface, the internal velocity state provides equivalent information.
**Warning signs:** 2-pixel tolerance fails specifically during direction change test trajectories.

### Pitfall 3: FakeClock Not Advancing in Motor Thread
**What goes wrong:** In tests, the motor background thread uses `time.perf_counter()` for its sleep loop instead of the injected Clock. FakeClock advances but the motor thread's view of time doesn't.
**Why it happens:** Background thread loop in SimulatedMotorInterface uses `time.perf_counter()` (line 652-654).
**How to avoid:** For Phase 2 tests, do NOT use the background simulation thread. Use explicit `advance_simulation(dt)` calls (already the pattern from Phase 1 tests). The background thread is for interactive mode only.
**Warning signs:** Tests hang or produce wildly wrong results because motor never advances.

### Pitfall 4: Division by Zero in Derivative Term
**What goes wrong:** When flDeltaTimeSeconds is very small or zero, the PID derivative term `(error - prev_error) / dt` produces infinity or raises an exception.
**Why it happens:** First frame after tracking starts has no previous timestamp, or two frames arrive with timestamps that round to the same value.
**How to avoid:** Apply dt_min clamp at the pipeline boundary (recommendation: 1ms minimum). The clamping happens in TrackerController before calling the control algorithm, so the algorithm never sees dt < 1ms.
**Warning signs:** NaN or Inf values in motor commands; motor jumps to angle limit on first tracking frame.

### Pitfall 5: Breaking Existing Tests with Signature Change
**What goes wrong:** Changing `calculate_correction_from_error(flError)` to include `flDeltaTime` breaks all existing callers including Phase 1 integration tests.
**Why it happens:** Direct signature change without updating all call sites.
**How to avoid:** Update all callers systematically: TrackerController._execute_centering_control_algorithm() and any test files that call the control algorithms directly. Run the full test suite after the change.
**Warning signs:** Phase 1 tests fail after Phase 2 changes.

### Pitfall 6: Arduino Timestamp Rollover Incorrect Handling
**What goes wrong:** Arduino `micros()` overflows every ~71.6 minutes (2^32 microseconds). The current rollover detection compares consecutive timestamps, which works but the linear regression approach smooths over rollover artifacts better than the current point-wise offset.
**Why it happens:** Arduino hardware limitation of 32-bit unsigned counter.
**How to avoid:** Keep the existing rollover unwrapping logic (it works), feed the unwrapped timestamps to the linear regression. The regression naturally handles small jitter in the offset measurements.
**Warning signs:** Sudden jump in interpolated motor angles after running for >71 minutes.

## Code Examples

### Current State: Timing Issues to Fix

**Issue 1: PIDController uses time.time() (control_algorithm.py lines 107, 124, 166)**
```python
# CURRENT (broken) -- uses wall clock, non-deterministic
self.dPreviousTime = time.time()
# ...
dCurrentTime = time.time()
flDeltaTime = dCurrentTime - self.dPreviousTime
```

**Issue 2: VelocityController uses time.time() (control_algorithm.py lines 209, 223, 249)**
```python
# CURRENT (broken) -- same problem
self.dPreviousTime = time.time()
# ...
dCurrentTime = time.time()
```

**Issue 3: CameraInterface timestamps after read() (camera_interface.py lines 147-150)**
```python
# CURRENT (imprecise) -- timestamps AFTER blocking read
bSuccess, obFrame = self.obVideoCapture.read()
dTimestampSeconds = time.perf_counter()  # Too late!
```

**Issue 4: TrackerController uses time.time() for statistics (tracker_controller.py lines 81, 191)**
```python
# CURRENT (inconsistent) -- wall clock for stats
self.dStartTime = time.time()
# ...
dElapsedTime = time.time() - self.dStartTime
```

**Issue 5: Motor interface EMA offset (motor_interface.py line 342)**
```python
# CURRENT (slow convergence) -- 98%/2% EMA
self._dArduinoToPerfCounterOffsetSeconds = (
    0.98 * float(self._dArduinoToPerfCounterOffsetSeconds)
) + (0.02 * dMeasuredOffset)
```

**Issue 6: Motor history linear-only interpolation (motor_interface.py lines 238-241)**
```python
# CURRENT (insufficient) -- linear interpolation only
dFrac = (dAt - dPrev) / (dNext - dPrev)
flA0 = float(obPrev.flMotorAngleDegrees)
flA1 = float(obNext.flMotorAngleDegrees)
return flA0 + (flA1 - flA0) * float(dFrac)
```

### Arduino Firmware Details (Critical Research Finding)

The Arduino firmware (`arduino/StepperController/StepperController.ino`) sends feedback every **20 milliseconds** (line 78: `const unsigned long FEEDBACK_INTERVAL = 20;`). This is 50Hz.

Each feedback message format: `FB:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState`

The `accelState` field (lines 337-354) reports:
- 0 = stopped
- 1 = accelerating
- 2 = constant speed
- 3 = decelerating

This accelState is critical for selecting the correct interpolation formula:
- State 1 or 3: quadratic interpolation `p(t) = p0 + v0*dt + 0.5*a*dt^2`
- State 2: linear interpolation `p(t) = p0 + v*dt`
- State 0: position constant (motor at rest)

The firmware uses AccelStepper with these constants (lines 39-42):
- STEPS_PER_REV = 200
- GEAR_RATIO = 180
- MICROSTEPS = 8
- TOTAL_STEPS_PER_REV = 200 * 180 * 8 = 288,000 steps/rev

This matches the Python-side constant `_FL_DEGREES_PER_STEP = 360.0 / 288000.0`.

Motor configured defaults: maxSpeed = 25000 steps/s, maxAccel = 12500 steps/s^2 (firmware lines 69-70). In degrees: maxSpeed = 31.25 deg/s, maxAccel = 15.625 deg/s^2. However, the Python config overrides these via the S command.

### MotorState Enhancement for Interpolation

The `MotorState` dataclass needs an accelState field to support physics-informed interpolation:
```python
@dataclass
class MotorState:
    flMotorAngleDegrees: float
    flMotorTargetAngleDegrees: float
    flMotorSpeedStepsPerSecond: float
    bMotorIsMoving: bool
    dMotorTimestampSeconds: float
    iMotorSequenceNumber: int = 0
    iAccelerationState: int = 0  # NEW: 0=stopped, 1=accel, 2=constant, 3=decel
```

### Drift Validation Test Pattern
```python
def test_virtual_center_within_2_pixels_constant_velocity():
    """Verify center line accuracy during constant-velocity motor motion."""
    obClock = FakeClock(0.0)
    obMotor = SimulatedMotorInterface(obClock=obClock)
    # Configure for 30 deg/s max speed
    obMotor.send_speed_and_acceleration_settings(
        30.0 / (360.0 / 288000.0),  # steps/s
        60.0 / (360.0 / 288000.0),  # steps/s^2
    )
    obMotor.send_move_to_angle_command(20.0)

    flFovDegrees = 6.77
    iWidthPixels = 1280
    flDegreesPerPixel = flFovDegrees / iWidthPixels

    vPixelErrors = []
    for iFrame in range(90):  # 3 seconds at 30fps
        dFrameTime = iFrame / 30.0
        obClock.set_time_seconds(dFrameTime)

        # Advance motor simulation in small steps up to frame time
        obMotor.advance_simulation(1.0 / 30.0)

        # Get interpolated angle at frame time
        flInterpolated = obMotor.get_estimated_motor_angle_degrees(dFrameTime)

        # Get "true" angle (from simulation state)
        obState = obMotor.get_latest_motor_state()
        flTrue = obState.flMotorAngleDegrees

        # Convert angle error to pixel error
        flAngleError = abs(flInterpolated - flTrue)
        flPixelError = flAngleError / flDegreesPerPixel
        vPixelErrors.append(flPixelError)

    flRmsPixels = numpy.sqrt(numpy.mean(numpy.square(vPixelErrors)))
    assert flRmsPixels < 2.0, f"RMS pixel error {flRmsPixels:.2f} exceeds 2-pixel tolerance"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `time.time()` for intervals | `time.perf_counter()` | Python 3.3+ (PEP 418) | Monotonic, sub-microsecond on Windows, not affected by NTP adjustments |
| EMA for clock sync | Linear regression sliding window | Academic best practice | Converges in one window fill vs hundreds of samples; handles drift linearly |
| `read()` for camera | `grab()` + `retrieve()` | OpenCV 2.x+ (always available) | Enables timestamp between acquisition and decode |
| Internal dt in controllers | Explicit dt parameter | Modern control design | Enables deterministic testing, separation of concerns |

**Deprecated/outdated:**
- `time.time()` for measuring durations: Not monotonic, affected by system clock adjustments, 15.6ms resolution on Windows (unless timeBeginPeriod is called). Use `time.perf_counter()`.
- `time.clock()`: Removed in Python 3.8. Was replaced by `time.perf_counter()`.

## Discretionary Recommendations

### Frame Timestamp Placement (Claude's Discretion)
**Recommendation:** Immediately AFTER `grab()` returns successfully.
**Rationale:** `grab()` is the hardware acquisition step -- it waits for the next frame from the camera driver. The moment `grab()` returns is the closest proxy for "when the frame was actually captured." Timestamping before `grab()` would include the time waiting for the next frame. Timestamping after `retrieve()` would include JPEG decompression or Bayer demosaicing time (variable, typically 1-5ms).
**Confidence:** HIGH -- this is well-documented OpenCV practice for multi-camera synchronization.

### Camera-to-Motor Latency Offset (Claude's Discretion)
**Recommendation:** Do NOT implement a latency offset calibration for Phase 2.
**Rationale:** The primary issue being fixed is the mixed clock sources and coarse timestamping, not a systematic camera-motor latency offset. At 30fps (33ms frame interval) with motor feedback at 50Hz (20ms interval), the maximum interpolation error from timing alone is on the order of 10ms. At the maximum operating velocity of 45 deg/s, 10ms of timing error = 0.45 degrees = ~85 pixels/degree * 0.45 = 0.38 pixels. This is well within the 2-pixel tolerance. A latency offset can be added in Phase 5 if real-world testing reveals systematic bias.
**Confidence:** MEDIUM -- the math supports this, but real-world latency could be higher than estimated.

### Constant-Velocity Interpolation Method (Claude's Discretion)
**Recommendation:** Linear interpolation during constant velocity phases.
**Rationale:** During constant velocity, position is truly a linear function of time. Quadratic interpolation during constant velocity would overfit and could introduce error if the acceleration estimate is imperfect. Use quadratic ONLY during accel/decel phases.
**Confidence:** HIGH -- this follows directly from the physics.

### dt Clamp Values (Claude's Discretion)
**Recommendation:**
- `flDtMaxSeconds = 0.066` (2x frame interval for 30fps). If dt exceeds this, SKIP the control update entirely (do not send a motor command). This handles dropped frames gracefully.
- `flDtMinSeconds = 0.001` (1ms). Clamp dt up to this minimum. This handles the rare case of two frames arriving with near-identical timestamps (e.g., initial buffered frame).
- These values should be configurable parameters in default_config.json.
**Confidence:** HIGH -- standard practice in real-time control loops.

### RMS vs MAE for Drift Metric (Claude's Discretion)
**Recommendation:** Use RMS (Root Mean Square).
**Rationale:** RMS penalizes large transient errors more heavily than MAE. Since the acceptance criterion allows transient exceedance during reversals with settling, RMS correctly weights the steady-state accuracy higher than brief spikes. RMS is also the standard metric in control systems for tracking error measurement.
**Confidence:** HIGH -- standard choice for control system error measurement.

## Open Questions

1. **SimulatedMotorInterface clock injection**
   - What we know: SimulatedMotorInterface currently uses `time.perf_counter()` directly in `advance_simulation()` (line 613) and the background loop (lines 652-654).
   - What's unclear: For deterministic tests with FakeClock, advance_simulation() should use the injected clock for the timestamp recorded in history. But the background simulation loop needs real time for its sleep interval.
   - Recommendation: In advance_simulation(), use the injected Clock for the `dMotorTimestampSeconds` stored in history. Keep `time.sleep(0.001)` in the background loop (it's a real-time delay, not a timestamp). For tests, don't use the background thread -- call advance_simulation() directly with FakeClock.

2. **Smooth curve through direction reversals**
   - What we know: User locked "single smooth curve through direction reversals -- not three discrete phases."
   - What's unclear: The AccelStepper firmware inherently uses discrete accel/constant/decel phases. The "smooth curve" requirement means the interpolation should produce a smooth position function even across the transition from one feedback report (state=decel) to the next (state=accel in opposite direction).
   - Recommendation: Use Hermite interpolation between history points. Each point has a known position and velocity (from speed + direction). Hermite cubic interpolation produces a smooth C1 curve through points with known derivatives, automatically handling the velocity transition through zero at reversal points.

3. **History buffer sizing**
   - What we know: Current MotorInterface history buffer is 32 entries (deque maxlen=32). SimulatedMotorInterface uses 100. At 50Hz feedback, 32 entries = 640ms of history.
   - What's unclear: Is 640ms enough for interpolation? Camera frame could be up to 33ms old. 640ms is overkill for interpolation but appropriate for the linear regression window.
   - Recommendation: Keep 50 entries for motor state history (1 second at 50Hz). Use a separate 50-entry buffer for clock sync regression samples. These are independent concerns.

## Sources

### Primary (HIGH confidence)
- Arduino firmware source: `arduino/StepperController/StepperController.ino` -- Feedback interval 20ms (50Hz), accelState field, AccelStepper parameters
- Current codebase: `src/interfaces/motor_interface.py` -- EMA offset (line 342), linear interpolation (lines 238-241), history buffer (maxlen=32)
- Current codebase: `src/control/control_algorithm.py` -- time.time() usage (lines 107, 124, 166, 209, 223, 249)
- Current codebase: `src/interfaces/camera_interface.py` -- read() with post-capture timestamp (lines 147-150)
- [Python time module documentation](https://docs.python.org/3/library/time.html) -- perf_counter() is monotonic, system-wide on Windows 3.10+
- [PEP 418](https://peps.python.org/pep-0418/) -- perf_counter() specification and guarantees

### Secondary (MEDIUM confidence)
- [OpenCV VideoCapture documentation](https://docs.opencv.org/3.4/d8/dfe/classcv_1_1VideoCapture.html) -- grab()/retrieve() separation, timing benefits for multi-camera sync
- [CPython issue #115637](https://github.com/python/cpython/issues/115637) -- perf_counter() monotonic guarantee discussion
- [Motion profiling trapezoidal equations](https://www.ctrlaltftc.com/advanced/motion-profiling) -- Position equations for trapezoidal profile phases
- [Adaptive time window linear regression for clock sync](https://www.sciencedirect.com/science/article/abs/pii/S1570870514001735) -- Linear regression approach for clock offset estimation

### Tertiary (LOW confidence)
- Camera-to-motor latency estimate (0.38 pixels at 45 deg/s) -- based on theoretical calculation, not measured. Needs real-world validation in Phase 5.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib + existing numpy dependency
- Architecture (Clock abstraction): HIGH -- standard DI pattern, well-proven in testing
- Architecture (grab/retrieve): HIGH -- documented OpenCV practice
- Architecture (quadratic interpolation): HIGH -- basic kinematics, Arduino provides accelState
- Architecture (linear regression offset): HIGH -- numpy.polyfit, standard approach
- Pitfalls: HIGH -- identified from direct codebase inspection
- Drift tolerance achievability: MEDIUM -- theoretical analysis supports 2-pixel at 45 deg/s, needs synthetic test validation

**Research date:** 2026-02-22
**Valid until:** 2026-04-22 (60 days -- stable domain, no fast-moving dependencies)
