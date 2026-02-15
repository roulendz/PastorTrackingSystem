# Architecture Research: Camera-Motor Synchronization and Control Loop Testing

**Domain:** Real-time camera tracking with stepper motor synchronization
**Researched:** 2026-02-15
**Confidence:** HIGH (core patterns), MEDIUM (motion profiling), MEDIUM (test harness)

## Problem Statement

The existing PastorTrackingSystem has a working pipeline: Camera -> PoseTracker -> TrackerController -> MotorInterface. Three architectural problems need solving:

1. **Virtual center drift** -- the motor angle used to compute `iHomeLineX = iCenterX - (motorAngle / anglePerPixel)` lags or jitters at speed because motor feedback (every ~20ms) and camera frames (every ~33ms) are asynchronous and use different clock sources.
2. **Motion feels robotic** -- AccelStepper uses trapezoidal velocity profiles (constant acceleration), producing abrupt starts/stops that look unnatural on camera.
3. **No test harness** -- control algorithms cannot be validated without physical hardware; tuning PID gains requires live sessions.

---

## 1. Timestamped Motor Position Estimation (Virtual Center Fix)

### Root Cause Analysis

The existing code has three distinct timing problems:

**Problem A: Asynchronous sample rates.** Motor feedback arrives every ~20ms via serial. Camera frames arrive every ~33ms. When the controller reads `get_latest_motor_state()`, it gets the most recent feedback, which could be up to 20ms stale. At a motor velocity of 30 deg/s, 20ms of staleness = 0.6 degrees of error, which at a 0.05 deg/pixel mapping equals **12 pixels of virtual center drift**.

**Problem B: Clock domain mismatch.** The camera timestamps use `time.perf_counter()`. The PIDController and VelocityController use `time.time()`. The Arduino uses `micros()`. These are three independent clock sources. The existing exponential smoothing bridge (`_dArduinoToPerfCounterOffsetSeconds`) converts Arduino micros to perf_counter space, but only with alpha=0.02 -- too slow to converge and vulnerable to jitter during the first seconds of operation.

**Problem C: Frame-capture-to-use delay.** The camera frame is captured, then pose detection runs (~15-30ms for MediaPipe), then the motor angle is read. By the time the control correction is computed, the motor has moved further. The correction is being applied to a stale world-state.

### Recommended Architecture: Timestamped State Interpolation

**Confidence: HIGH** -- This is the standard approach in robotics (ROS uses tf2 for exactly this), PTZ tracking systems, and industrial servo control.

```
Motor Feedback Thread                     Main Control Loop
=====================                     ==================

  Arduino FB @ t_m1  ----+
  Arduino FB @ t_m2  ----+--> MotorStateRingBuffer    Frame captured @ t_f
  Arduino FB @ t_m3  ----+    (timestamped history)       |
  Arduino FB @ t_m4  ----+           |                    v
                                     |            Query: "What was motor
                                     |             angle at time t_f?"
                                     |                    |
                                     +-----> interpolate(t_f) -> angle_at_capture
                                                          |
                                                          v
                                                 Build TrackingSample with
                                                 true motor angle at capture time
```

#### Component: MotorStateRingBuffer

**What it owns:** Timestamped history of motor feedback reports, interpolation between them.

**Already partially exists:** The current `_obMotorStateHistory = deque(maxlen=32)` in `MotorInterface` and the `get_estimated_motor_angle_degrees()` method already implement linear interpolation over the history. This is the right pattern.

**What needs to change:**
- The ring buffer size of 32 is correct (32 * 20ms = 640ms of history, more than enough to cover frame latency)
- The interpolation logic in `get_estimated_motor_angle_degrees()` already does linear interpolation between timestamps -- this is sound
- The timestamp conversion from Arduino micros to perf_counter needs improvement (see below)

#### Component: UnifiedClock

**What it owns:** A single authoritative time source and the Arduino-to-Python clock bridge.

**Why:** The system currently uses three clock sources. Everything must flow through one.

| Current Usage | Clock Source | Problem |
|---------------|-------------|---------|
| Camera frame timestamps | `time.perf_counter()` | Good -- monotonic, high resolution |
| PIDController delta time | `time.time()` | Bad -- wall clock, can jump, different domain |
| VelocityController delta time | `time.time()` | Bad -- same issue |
| Arduino feedback timestamps | Arduino `micros()` | Different device, needs bridge |
| Motor state timestamps | Bridged perf_counter | Good in principle, but bridge alpha too slow |

**Rule: All timestamps in the system must be in `time.perf_counter()` space.** The control algorithms must not call `time.time()` internally; they must receive `dDeltaTimeSeconds` as a parameter computed from the sample timestamps.

```python
class ControlAlgorithm(ABC):
    @abstractmethod
    def calculate_correction_from_error(
        self,
        flErrorDegrees: float,
        dDeltaTimeSeconds: float   # <-- injected, not self-measured
    ) -> float:
        pass
```

This single change eliminates Problem B entirely and makes the controllers testable with simulated time.

#### Component: ArduinoClockBridge

**What it owns:** Converting Arduino `micros()` timestamps to Python `perf_counter()` space.

**Current implementation review:** The existing code uses exponential smoothing with alpha=0.02:
```python
self._dArduinoToPerfCounterOffsetSeconds = (0.98 * offset) + (0.02 * measured)
```

This converges too slowly. After 50 feedback messages (1 second at 50Hz), the offset has only adapted ~63% toward the true value. During that first second, virtual center calculations are wrong.

**Recommended approach:** Use a two-phase bridge:

1. **Initialization phase** (first 10 messages, ~200ms): Collect offset samples, take the median (robust to outliers from serial latency jitter). Set the bridge offset to this median.
2. **Steady-state phase** (ongoing): Use exponential smoothing with alpha=0.05 (faster than current 0.02 but still stable) to track clock drift. Arduino crystal oscillators drift ~50ppm, meaning ~50 microseconds per second. Over a 1-hour service, that is 180ms of drift -- significant enough to matter.

**Rollover handling:** The existing rollover detection (`iTimestampMicros < self._iArduinoLastTimestampMicros`) is correct. Arduino `micros()` overflows every ~71.6 minutes (2^32 microseconds). The unwrapping math is sound.

### Data Flow for Virtual Center Calculation

```
Frame captured (timestamp = dFrameTime via perf_counter)
    |
    v
MotorInterface.get_estimated_motor_angle_degrees(dFrameTime)
    |
    +--> ArduinoClockBridge already converted FB timestamps to perf_counter space
    +--> Linear interpolation in ring buffer at dFrameTime
    |
    v
flMotorAngleAtCapture (degrees)
    |
    v
iHomeLineX = iCenterX - (flMotorAngleAtCapture / flAnglePerPixel)
    |
    v
TrackingSample.flMotorAngleDegrees = flMotorAngleAtCapture
```

**This is already close to what the code does.** The `tracker_controller.py` line 108 calls `get_estimated_motor_angle_degrees(dFrameTimestamp)` with the frame timestamp. The architecture is correct. The fixes needed are:

1. Unify clock sources in control algorithms (stop using `time.time()`)
2. Speed up clock bridge convergence
3. Ensure pose detection latency does not shift the effective query time

### Handling Pose Detection Latency

MediaPipe pose detection takes 15-30ms. The frame is captured at `t_capture`, but pose detection finishes at `t_capture + t_detection`. The motor has moved during that time.

**Option A (recommended): Use the capture timestamp, not the detection-complete timestamp.** The pixel coordinates of the person are relative to the captured frame. The motor angle that matters is the angle **when that frame was exposed**. The current code already does this correctly -- it timestamps at capture time and queries the motor angle at that timestamp.

**Option B (only if Option A shows residual lag): Predict forward.** After computing the error at `t_capture`, predict where the motor will be at `t_now` using current velocity, and adjust the command accordingly. This adds complexity and is only needed if the detection latency is large relative to the control period.

**Recommendation:** Option A is sufficient for this system. At 30 FPS with ~20ms detection latency, the motor moves at most 0.6 degrees during detection at max tracking speed. The control loop will correct for this on the next frame.

---

## 2. Motion Profiling: Human-Like Camera Movement

### Why AccelStepper's Trapezoidal Profile Feels Wrong

AccelStepper (v1.66, confirmed current as of 2026-01-21) implements trapezoidal velocity profiles: constant acceleration up to max speed, cruise, constant deceleration to stop. This produces abrupt jerk (the derivative of acceleration) at the transitions, which reads as "mechanical" or "robotic" on camera.

**Confidence: HIGH** -- AccelStepper documentation confirms trapezoidal profiles only. S-curve is not built in.

### Recommended Architecture: Velocity Command Smoothing in Python

Do NOT try to implement S-curve profiles in the Arduino firmware. Instead, keep AccelStepper doing what it does well (real-time step generation with acceleration limiting) and apply motion smoothing at the command level in Python.

```
Control Algorithm                  Motion Smoother              Arduino/AccelStepper
=================                  ===============              ====================

 flRawCorrection  ------>  Smooth velocity profile  ------>  M,{angle}\n
 (may be jerky,            (S-curve or exponential           (AccelStepper handles
  proportional to           easing on the velocity            acceleration to reach
  error)                    command stream)                   target)
```

#### Component: MotionProfiler

**What it owns:** Smoothing the stream of target angle commands sent to the motor.

**Location in pipeline:** Between the control algorithm output and the serial command.

The existing `VelocityController` already has a primitive version of this with `flVelocitySmoothingAlpha` (exponential smoothing on velocity). But it operates at the wrong level -- it smooths the velocity calculation inside the controller, not the command output. And it uses `time.time()` for its delta.

**Recommended implementation:**

```python
class MotionProfiler:
    """
    Smooths target angle commands to produce natural-feeling camera motion.

    Takes raw target angles from the control algorithm and applies
    velocity/acceleration limiting with S-curve easing to produce
    smooth, human-like camera movement.
    """

    def smooth_target_angle(
        self,
        flRawTargetAngleDegrees: float,
        flCurrentMotorAngleDegrees: float,
        dDeltaTimeSeconds: float
    ) -> float:
        # 1. Compute desired velocity from position delta
        # 2. Apply velocity limit
        # 3. Apply acceleration limit (jerk-limiting gives S-curve)
        # 4. Apply jerk limit (rate of change of acceleration)
        # 5. Integrate to get smoothed target position
        pass
```

#### S-Curve via Jerk Limiting

The simplest path to S-curve motion is **jerk limiting** -- limiting the rate of change of acceleration. This naturally produces sigmoid-shaped velocity profiles.

Three levels of smoothing, in order of complexity:

| Level | What's Limited | Velocity Shape | Feel |
|-------|---------------|----------------|------|
| 1. Velocity only | Max speed | Square wave | Terrible |
| 2. Velocity + Acceleration | Max speed, max accel | Trapezoidal | Mechanical |
| 3. Velocity + Acceleration + Jerk | Max speed, max accel, max jerk | S-curve (sigmoid) | Human-like |

**For this system, Level 3 (jerk-limited) is the right choice** because:
- The camera output is directly viewed by an audience
- A pastor walking across a stage moves at 1-3 deg/s from the camera's perspective
- Audience tolerance for jerky motion is low
- The computational cost is negligible (a few floating-point operations per frame)

#### Easing Functions for Short Movements

For movements within the deadzone (small corrections), full S-curve profiling is overkill. Instead, use a simple easing function. Based on research, **easeInOutCubic** provides the best balance of natural feel and simplicity for camera movements:

```python
def ease_in_out_cubic(t: float) -> float:
    """t in [0,1], returns eased value in [0,1]."""
    if t < 0.5:
        return 4.0 * t * t * t
    else:
        return 1.0 - pow(-2.0 * t + 2.0, 3) / 2.0
```

#### Interaction with AccelStepper

AccelStepper on the Arduino also applies its own acceleration profile. This creates a **double-profiling** situation: Python smooths the command stream, then AccelStepper smooths the execution.

**This is acceptable and desirable** when configured correctly:
- Set AccelStepper's acceleration high enough that it can track the smoothed command stream without falling behind
- The Python-side smoother becomes the authoritative motion profile
- AccelStepper acts as a safety backstop preventing impossible step rates

**Recommended AccelStepper settings for this architecture:**
- `setMaxSpeed()`: 2-3x the maximum velocity the Python smoother will ever command
- `setAcceleration()`: 3-5x the maximum acceleration the Python smoother will ever produce
- This ensures AccelStepper "gets out of the way" and lets Python control the profile

---

## 3. Deterministic Test Harness for Control Loops

### Architecture: Software-in-the-Loop (SIL) Testing

**Confidence: MEDIUM** -- The SIL pattern is well-established in industrial control, but applying it to this specific Python/Arduino system requires custom implementation.

The key insight from SIL testing: **because the plant model and controller are both running on the same simulator, timing with the real world is no longer critical. It can be faster or slower than real-time with no effect on the simulation results.**

This means tests are:
- **Deterministic** -- same inputs always produce same outputs
- **Fast** -- no waiting for real-time delays
- **Reproducible** -- no hardware variability
- **Scriptable** -- scenarios can be defined as data

### Component Boundaries for Test Harness

```
Production System                    Test Harness
==================                   ============

CameraInterface --------+
                        |
PoseTracker     --------+--> TrackerController <--+-- SimulatedCamera
                        |                         |
MotorInterface  --------+                         +-- SimulatedPoseTracker
                                                  |
                                                  +-- SimulatedMotor
                                                  |
                                                  +-- DeterministicClock
                                                  |
                                                  +-- ScenarioRunner
```

#### Component: SimulatedMotor

**What it owns:** Physics model of the stepper motor + AccelStepper behavior.

**Key behaviors to simulate:**
1. **Trapezoidal velocity profile** -- given a target angle, accelerate at configured rate to max speed, cruise, decelerate to stop at target
2. **Position reporting** -- report current position at configurable intervals (default 20ms simulated time)
3. **Command latency** -- serial transmission delay (~1ms) and command parsing delay
4. **Step granularity** -- 1.8 degrees per step / microstepping factor

```python
class SimulatedMotor:
    """
    Physics simulation of stepper motor with AccelStepper-like behavior.

    Implements the MotorInterface protocol so TrackerController
    cannot distinguish it from real hardware.
    """

    def __init__(self, obClock: DeterministicClock):
        self.obClock = obClock
        self.flCurrentAngleDegrees = 0.0
        self.flTargetAngleDegrees = 0.0
        self.flCurrentVelocityDegreesPerSecond = 0.0
        self.flMaxSpeedDegreesPerSecond = 30.0
        self.flAccelerationDegreesPerSecondSquared = 60.0
        self.flFeedbackIntervalSeconds = 0.020  # 20ms

    def advance_simulation(self, dDeltaTimeSeconds: float):
        """Step the motor physics forward by dDeltaTimeSeconds."""
        # Compute acceleration toward target using trapezoidal profile
        # Update velocity and position
        # Generate feedback messages at configured interval
        pass

    # Implements MotorInterface protocol
    def send_move_to_angle_command(self, flTargetAngleDegrees: float) -> bool:
        self.flTargetAngleDegrees = flTargetAngleDegrees
        return True

    def get_latest_motor_state(self) -> MotorState:
        return MotorState(
            flMotorAngleDegrees=self.flCurrentAngleDegrees,
            flMotorTargetAngleDegrees=self.flTargetAngleDegrees,
            flMotorSpeedStepsPerSecond=self._degrees_to_steps(
                self.flCurrentVelocityDegreesPerSecond
            ),
            bMotorIsMoving=abs(self.flCurrentVelocityDegreesPerSecond) > 0.01,
            dMotorTimestampSeconds=self.obClock.now()
        )
```

**Contrast with existing NullMotorInterface:** The current `NullMotorInterface` instantly teleports to the target position (`flMotorAngleDegrees = flTargetAngleDegrees`). This is useless for testing control dynamics because it removes all the timing behavior that causes real-world bugs.

#### Component: SimulatedCamera

**What it owns:** Generating synthetic frames with known person positions.

**Does NOT need to generate actual images** for control loop testing. Instead, it produces `PoseResult` objects directly, bypassing MediaPipe entirely.

```python
class SimulatedCamera:
    """
    Generates synthetic tracking data at configurable frame rate.
    No actual image processing -- returns PoseResult directly.
    """

    def __init__(self, obClock: DeterministicClock):
        self.obClock = obClock
        self.iWidthPixels = 1280
        self.iHeightPixels = 720
        self.flFrameIntervalSeconds = 1.0 / 30.0
        self.obPersonTrajectory = None  # Set by scenario

    def capture_frame_with_timestamp(self):
        """Returns (None, timestamp) -- no actual frame needed."""
        dTimestamp = self.obClock.now()
        return None, dTimestamp

    def get_person_position_at_time(self, dTimestamp: float) -> PoseResult:
        """Query the trajectory for person position at given time."""
        if self.obPersonTrajectory is None:
            return PoseResult(bPersonWasDetected=False, ...)
        return self.obPersonTrajectory.get_position_at(dTimestamp)
```

#### Component: DeterministicClock

**What it owns:** All time in the simulation.

**Why not use freezegun?** Freezegun mocks `datetime.now()` and `time.time()`, but this system uses `time.perf_counter()`. More importantly, freezegun freezes time at a point -- it does not advance time in controlled steps. For a control loop simulation, we need `clock.advance(dt)`.

```python
class DeterministicClock:
    """
    Provides deterministic, manually-advanced time for simulation.
    Replaces all calls to time.perf_counter() and time.time().
    """

    def __init__(self, dStartTimeSeconds: float = 0.0):
        self._dCurrentTimeSeconds = dStartTimeSeconds

    def now(self) -> float:
        return self._dCurrentTimeSeconds

    def advance(self, dDeltaSeconds: float):
        self._dCurrentTimeSeconds += dDeltaSeconds
```

All components in the test harness receive the clock as a dependency. Production code uses a `RealClock` wrapper around `time.perf_counter()`. This is the standard approach for testable time-dependent systems.

#### Component: ScenarioRunner

**What it owns:** Orchestrating a complete test scenario: defining person trajectory, stepping the simulation, collecting results.

```python
class ScenarioRunner:
    """
    Runs a scripted scenario through the control loop.

    A scenario defines:
    - Person trajectory over time (position in world-angle-space)
    - Duration
    - Expected outcomes (settling time, overshoot, steady-state error)
    """

    def run_scenario(self, obScenario: Scenario) -> ScenarioResult:
        dSimulationStep = 0.001  # 1ms steps for accuracy

        while self.obClock.now() < obScenario.dDurationSeconds:
            # 1. Advance clock
            self.obClock.advance(dSimulationStep)

            # 2. Advance motor physics
            self.obSimulatedMotor.advance_simulation(dSimulationStep)

            # 3. At frame intervals: run control loop tick
            if self._is_frame_due():
                obSample = self.obController.execute_main_tracking_loop_tick()
                self.vSamples.append(obSample)

            # 4. At feedback intervals: generate motor feedback
            if self._is_feedback_due():
                self.obSimulatedMotor.emit_feedback()

        return ScenarioResult(self.vSamples)
```

#### Scenario Examples

| Scenario | Person Trajectory | Tests | Expected Result |
|----------|------------------|-------|-----------------|
| Step response | Person teleports 10deg right at t=1s | Settling time, overshoot | Settles within 5 deg in <1s, <20% overshoot |
| Ramp tracking | Person walks at constant 2 deg/s | Steady-state error | Error < 1 degree during steady motion |
| Start-stop | Person walks, stops, walks opposite | Direction reversal response | No oscillation at reversal |
| Occlusion | Person disappears for 2s, reappears | Loss-of-track behavior | Motor holds position, resumes tracking |
| Jitter rejection | Person center fluctuates +/-3px randomly | Noise rejection | Motor does not oscillate |
| Speed sweep | Person walks at increasing speeds | Max trackable speed | Find speed where error exceeds threshold |

### Test Data Flow

```
Scenario Definition (YAML or Python)
    |
    v
ScenarioRunner
    |
    +--> DeterministicClock (time source for all components)
    |
    +--> SimulatedCamera + PersonTrajectory (synthetic detections)
    |
    +--> TrackerController (REAL production code, unmodified)
    |
    +--> SimulatedMotor (physics model)
    |
    v
ScenarioResult
    |
    +--> Assertions (pytest): settling time, overshoot, steady-state error
    +--> Plots (matplotlib): angle vs time, error vs time (optional)
    +--> Regression baseline: save results for comparison
```

The critical property: **TrackerController is the REAL production code**. Only the I/O boundaries (camera, motor, clock) are simulated. This guarantees that passing tests mean the production control logic works correctly.

---

## Component Boundaries Summary

| Component | Responsibility | Communicates With | Build Phase |
|-----------|---------------|-------------------|-------------|
| UnifiedClock | Single time source, wraps perf_counter | All components | Phase 1 |
| ArduinoClockBridge | micros() to perf_counter conversion | MotorInterface | Phase 1 |
| MotorStateRingBuffer | Timestamped motor history + interpolation | TrackerController | Phase 1 (exists, needs refinement) |
| MotionProfiler | Jerk-limited velocity smoothing | Between controller and motor | Phase 2 |
| DeterministicClock | Simulated time for testing | Test harness | Phase 3 |
| SimulatedMotor | Motor physics model | Test harness | Phase 3 |
| SimulatedCamera | Synthetic person trajectories | Test harness | Phase 3 |
| ScenarioRunner | Test orchestration | All test components | Phase 3 |

## Data Flow (Complete System After Refactoring)

```
                    UnifiedClock (perf_counter)
                         |
           +-------------+------------------+
           |             |                  |
           v             v                  v
    CameraInterface   ArduinoClockBridge   ControlAlgorithm
    (frame + ts)      (micros -> perf)     (receives dt, does not measure it)
           |             |                  ^
           v             v                  |
    PoseTracker    MotorStateRingBuffer     |
    (detection)    (interpolated angle)     |
           |             |                  |
           +------+------+                 |
                  |                         |
                  v                         |
           TrackerController ---------------+
           (builds TrackingSample,           |
            computes error,                  |
            calls control algorithm)         |
                  |                          |
                  v                          |
           MotionProfiler                    |
           (jerk-limited smoothing)          |
                  |
                  v
           MotorInterface
           (serial command)
```

## Suggested Build Order

**Phase 1: Fix Time Synchronization (prerequisite for everything)**
- Introduce `UnifiedClock` protocol/interface
- Refactor `ControlAlgorithm.calculate_correction_from_error()` to accept `dDeltaTimeSeconds` parameter
- Fix PIDController and VelocityController to stop calling `time.time()`
- Improve ArduinoClockBridge convergence (median initialization + faster alpha)
- Verify `get_estimated_motor_angle_degrees()` interpolation is correct

**Phase 2: Motion Profiling**
- Build `MotionProfiler` with jerk-limited velocity smoothing
- Insert between TrackerController and MotorInterface
- Configure AccelStepper to have higher acceleration headroom
- Tune jerk/acceleration/velocity limits for natural camera feel

**Phase 3: Test Harness**
- Build `DeterministicClock`
- Build `SimulatedMotor` with trapezoidal physics model
- Build `SimulatedCamera` with trajectory-based person positions
- Build `ScenarioRunner`
- Write standard scenario suite (step response, ramp, occlusion, jitter)

**Phase ordering rationale:**
- Phase 1 is prerequisite because the test harness (Phase 3) requires injectable time, and Phase 1 creates that abstraction. You cannot build deterministic tests for code that internally calls `time.time()`.
- Phase 2 depends on Phase 1 because the MotionProfiler needs correct time deltas.
- Phase 3 depends on Phases 1 and 2 because the test harness validates the complete pipeline including motion profiling.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Measuring Time Inside Control Algorithms

**What people do:** Controllers call `time.time()` or `time.perf_counter()` internally to compute delta-t.
**Why it is wrong:** Makes the controller untestable with simulated time. Creates clock domain mismatches. The PIDController currently does this on line 124-125 of control_algorithm.py.
**Do this instead:** Pass `dDeltaTimeSeconds` as a parameter. The orchestrator (TrackerController) computes dt from sample timestamps.

### Anti-Pattern 2: Using Latest State Instead of Interpolated State

**What people do:** Call `get_latest_motor_state()` to get the motor angle for the virtual center formula.
**Why it is wrong:** The latest state could be 20ms old. At speed, that is 12+ pixels of drift.
**Do this instead:** Query `get_estimated_motor_angle_degrees(dFrameTimestamp)` to interpolate the motor angle at the exact frame capture time. The existing code already does this correctly in tracker_controller.py line 108.

### Anti-Pattern 3: Double Motion Profiling Without Headroom

**What people do:** Apply smooth velocity profiling in Python, then let AccelStepper also apply its own acceleration ramp with the same limits.
**Why it is wrong:** The motor cannot keep up with the smoothed commands, introducing additional lag. The two profilers fight each other.
**Do this instead:** Set AccelStepper's acceleration 3-5x higher than the Python-side smoother's maximum. Let Python be the authoritative profiler; AccelStepper is just a safety backstop.

### Anti-Pattern 4: NullMotorInterface for Control Testing

**What people do:** Use `NullMotorInterface` (instant teleportation) to test control logic.
**Why it is wrong:** Removes all the timing dynamics that cause real-world bugs. Tests pass but the system still jitters in production.
**Do this instead:** Use `SimulatedMotor` with realistic physics (acceleration curves, feedback delays, step granularity).

### Anti-Pattern 5: Testing Control Loops with Real Time

**What people do:** Run control loop tests using wall-clock time, with `time.sleep()` for timing.
**Why it is wrong:** Tests are slow (run in real-time), non-deterministic (OS scheduling jitter), and flaky (different results on different machines).
**Do this instead:** Use `DeterministicClock` with fixed time steps. A 60-second scenario runs in milliseconds.

## Integration Points

### Internal Boundaries

| Boundary | Communication | Direction | Notes |
|----------|---------------|-----------|-------|
| TrackerController <-> MotorInterface | Method calls (send commands) + ring buffer queries | Bidirectional | Motor feedback is async via background thread |
| TrackerController <-> ControlAlgorithm | `calculate_correction_from_error(error, dt)` | Unidirectional | Must inject dt, not self-measure |
| TrackerController <-> MotionProfiler | `smooth_target_angle(raw, current, dt)` | Unidirectional | New component, inserted before motor command |
| MotorInterface <-> Arduino | Serial protocol (`M,angle\n` / `FB:...`) | Bidirectional | 115200 baud, ~1ms per message |
| CameraInterface <-> TrackerController | `capture_frame_with_timestamp()` | Unidirectional | Returns (frame, perf_counter timestamp) |

### External Boundaries (for test harness)

| Boundary | Production | Test |
|----------|-----------|------|
| Time source | `time.perf_counter()` via `RealClock` | `DeterministicClock` |
| Motor I/O | `MotorInterface` (serial) | `SimulatedMotor` (physics model) |
| Camera I/O | `CameraInterface` (OpenCV) | `SimulatedCamera` (trajectory-based) |
| Pose Detection | `PoseTracker` (MediaPipe) | Direct `PoseResult` injection |

## Sources

- [Arducam Hardware Timestamping for Multi-Sensor Synchronization](https://blog.arducam.com/industrial-grade-hardware-timestamping-usb-3-camera-modules-synchronization/) -- Confidence: MEDIUM
- [Precision Time Protocol for Ethernet Cameras](https://www.e-consystems.com/blog/camera/technology/the-role-of-precision-time-protocol-synchronization-in-ethernet-cameras/) -- Confidence: HIGH
- [Roboflow: PTZ Camera Control with Computer Vision](https://blog.roboflow.com/control-ptz-camera-computer-vision/) -- Confidence: HIGH
- [Frigate Camera Autotracking Documentation](https://docs.frigate.video/configuration/autotracking/) -- Confidence: MEDIUM
- [AccelStepper Library v1.66 Documentation](https://www.airspayce.com/mikem/arduino/AccelStepper/) -- Confidence: HIGH
- [Maxicrane: Trapezoidal vs S-Curve Motion Profiles](https://maxicrane.com/blogs/news/understanding-motion-control-profiles-trapezoidal-and-s-curve-for-maxicrane-s-ostrich-and-scarab-systems) -- Confidence: HIGH
- [Easing Functions Reference](https://easings.net/) -- Confidence: HIGH
- [SIL Testing Overview](https://www.dspace.com/en/inc/home/news/engineers-insights/sil-introduction.cfm) -- Confidence: HIGH
- [PEP 418: time.perf_counter() specification](https://peps.python.org/pep-0418/) -- Confidence: HIGH
- [Extrapolation for Encoder Position Estimation](https://www.researchgate.net/publication/3415396_Extrapolation_Technique_for_Improving_the_Effective_Resolution_of_Position_Encoders_in_Permanent-Magnet_Motor_Drives) -- Confidence: MEDIUM
- [PID Controller Simulation in Python (DigiKey)](https://www.digikey.com/en/maker/tutorials/2024/how-to-simulate-a-pid-controller-in-python-for-a-dc-motor) -- Confidence: MEDIUM
- [ROS tf2 Time Travel / Sensor Fusion Concepts](https://ar5iv.labs.arxiv.org/html/2103.16045) -- Confidence: MEDIUM
- [Kalman Filter for Motor Position Estimation](https://medium.com/@QuarkAndCode/sensor-fusion-cheat-sheet-kalman-filters-imu-gnss-tracking-27108a0ce771) -- Confidence: LOW (not directly applicable to this stepper system)

---
*Architecture research for: Camera-Motor Synchronization and Control Loop Testing*
*Researched: 2026-02-15*
