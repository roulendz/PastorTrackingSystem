# Pitfalls Research

**Domain:** Camera-motor synchronization for automated person tracking
**Researched:** 2026-02-15
**Confidence:** HIGH (based on codebase analysis, domain literature, and Python official documentation)

## Critical Pitfalls

### Pitfall 1: Mixed Clock Domains (time.time vs time.perf_counter)

**What goes wrong:**
The system uses two different clock sources that cannot be meaningfully compared. In this codebase, `CameraInterface.capture_frame_with_timestamp()` uses `time.perf_counter()` for frame timestamps, while `VelocityController` and `PIDController` use `time.time()` for delta-time calculations. When the control algorithm computes `flDeltaTime = dCurrentTime - self.dPreviousTime` using `time.time()`, and the TrackingSample carries a `dSampleTimestampSeconds` from `time.perf_counter()`, these two time domains are fundamentally incompatible. The Python documentation explicitly states: "The reference point of the returned value is undefined, so that only the difference between the results of two calls is valid" -- and this applies within the SAME clock, not across clocks.

The specific manifestation in this codebase: `VelocityController.calculate_correction_from_error()` at line 223 uses `time.time()` to compute elapsed time since last call, then uses this delta to convert velocity to position change (`flPositionChange = flSmoothedVelocity * flDeltaTime`). But `time.time()` is not monotonic -- it can jump forward or backward when Windows synchronizes with NTP. A single NTP adjustment of even 50ms would cause the velocity controller to produce a wildly incorrect position change for one frame, which at 6.8 degrees FOV translates to visible camera jerk.

**Why it happens:**
Developers use `time.time()` out of habit because it is the most familiar Python timing function. The PID and Velocity controllers were likely written independently from the camera interface, and nobody enforced a single clock policy. The bug is invisible during short testing sessions because NTP adjustments are infrequent.

**How to avoid:**
Establish a single monotonic clock for the entire system. Use `time.perf_counter()` everywhere that measures elapsed time or timestamps events. Specifically:
1. Create a single `get_timestamp()` utility function wrapping `time.perf_counter()`.
2. Pass timestamps into control algorithms rather than having them call `time.time()` internally.
3. The control algorithm signature should accept `dDeltaTimeSeconds` as a parameter rather than computing it internally. This makes the algorithm deterministically testable and eliminates clock-choice bugs.

**Warning signs:**
- Occasional single-frame jerks that cannot be reproduced on demand
- Slightly different control behavior when system is under load (time.time resolution degrades under load on Windows)
- Delta-time values that are occasionally negative or unreasonably large
- The `if flDeltaTime <= 0.0: flDeltaTime = 0.001` guard in PIDController (line 128) -- this guard exists precisely because the developers encountered negative deltas from `time.time()`

**Phase to address:**
Phase 1 (Foundation). This is the root cause of the center-line drift bug described in the project context. No other work should proceed until the clock domain is unified, because every subsequent feature (smoothing, interpolation, testing) depends on consistent timestamps.

---

### Pitfall 2: Virtual Reference Point Drift During Motor Motion

**What goes wrong:**
The "home line" overlay in `main.py` (line 244) computes the virtual center position as `iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))`. This calculation assumes `flMotorAngle` accurately represents where the motor is RIGHT NOW. But the motor angle comes from the last Arduino feedback message, which arrives every ~20ms, while frames arrive every ~33ms. When the motor is moving at velocity, the displayed angle can be stale by up to 20ms. At 45 deg/sec max velocity, 20ms of staleness equals 0.9 degrees of error. With a 6.8 degree FOV across 1280 pixels, 0.9 degrees is approximately 169 pixels of visible home-line drift.

The `get_estimated_motor_angle_degrees()` interpolation method (motor_interface.py line 217) attempts to fix this by interpolating between motor state history entries. However, the interpolation itself has a flaw: it uses timestamps that have already been filtered through the exponential moving average clock offset correction (line 343: `0.98 * offset + 0.02 * measured`). This EMA has a time constant of approximately 50 samples (1/0.02), meaning the clock offset takes about 1 second to converge. During that convergence period, interpolated motor positions are systematically biased.

**Why it happens:**
The fundamental challenge is that camera and motor are asynchronous data sources with different sample rates (30 FPS vs ~50 Hz motor feedback). Naive approaches either use stale data or introduce interpolation artifacts. The EMA filter for clock offset seems reasonable but introduces its own bias: the 0.98/0.02 weighting means the filter tracks slowly, which is good for noise rejection but bad for convergence after startup or after a communication glitch.

**How to avoid:**
1. Store motor state history with raw `perf_counter()` receive timestamps (do not pre-correct to Arduino time domain). The Arduino timestamp can be used for detecting gaps and reordering, but the PC-side receive timestamp is more reliable for interpolation because both the camera and the motor feedback receiver live on the same PC clock.
2. Use linear interpolation between the two most recent motor states that bracket the frame timestamp, based on PC-side receive timestamps.
3. If extrapolation is needed (frame timestamp is after the latest motor state), extrapolate using the motor's current velocity and target angle, but clamp the extrapolation window to at most one motor feedback interval (~20ms).
4. Remove the Arduino-to-PC clock offset EMA entirely. It is solving the wrong problem -- we do not need to know Arduino's absolute time; we need to know the motor angle at the moment the frame was captured, which is a PC-local question.

**Warning signs:**
- Home line visibly wobbles or slides when motor changes direction
- Home line is correct when motor is stationary but offset when motor is moving
- The offset magnitude correlates with motor speed (faster = more drift)
- The offset is worse immediately after startup (EMA has not converged)

**Phase to address:**
Phase 1 (Foundation). This is the second half of the center-line drift bug. After unifying clocks (Pitfall 1), the interpolation approach must be rebuilt using PC-local timestamps.

---

### Pitfall 3: Gear Ratio Backlash Creates Phantom Position Errors

**What goes wrong:**
With a 180:1 gear ratio, the motor must rotate 180 full revolutions for one revolution of the output shaft. A typical stepper motor with 200 steps/rev and 16x microstepping gives 3200 microsteps per motor revolution, or 576,000 microsteps per output revolution. One microstep equals 0.000625 degrees of output rotation (0.36 degrees / 576,000). This extreme reduction ratio means position resolution is excellent, BUT it also means gear backlash is amplified. Even 0.5 degrees of backlash at the motor equals 0.5/180 = 0.003 degrees at the output -- which sounds small but compounds with the control loop.

The real danger: when the motor reverses direction, the first N steps are consumed by backlash and produce zero output motion. The Arduino reports position based on step count (open-loop), so it believes it has moved, but the output shaft has not. The control algorithm sees the "new" position, computes a smaller error, and reduces correction -- even though the camera has not actually moved. This creates a dead zone around direction reversals where the system is sluggish and the home line appears to "stick" before catching up.

At 6.8 degrees FOV and 1280px width, even 0.05 degrees of uncompensated backlash equals ~9.4 pixels of position error -- clearly visible as jitter on direction changes.

**Why it happens:**
Stepper motors are open-loop: the Arduino counts steps commanded, not steps actually delivered to the output. Without a rotary encoder on the output shaft, backlash is invisible to the firmware. The software trusts the reported angle absolutely.

**How to avoid:**
1. Add backlash compensation in the motor interface: when direction reverses, add an offset equal to the measured backlash before reporting the position. This requires one-time calibration of the backlash amount.
2. Add a `flBacklashCompensationDegrees` config parameter. When a move command reverses direction from the previous command, overshoot by this amount.
3. For the visualization (home line), track whether the motor recently reversed direction and widen the uncertainty band during the backlash window.
4. Long-term: add a rotary encoder to the output shaft for closed-loop position feedback. This eliminates backlash uncertainty entirely.

**Warning signs:**
- Visible "hesitation" when tracking reverses direction (person walks back the other way)
- Home line jumps when motor changes direction
- Different tracking accuracy for left-to-right vs right-to-left motion
- The control algorithm oscillates around the target when the error is small (backlash creates a dead zone that the PID integral term fights against)

**Phase to address:**
Phase 2 (Control Refinement). This is a mechanical issue that exists regardless of software architecture. Address it after the timing foundation is solid, as timing bugs mask backlash effects and vice versa.

---

### Pitfall 4: Brownfield Rewrite Regression -- Losing Working Behavior

**What goes wrong:**
The project context states the user "wants to rewrite from clean slate." This is the single highest-risk decision in the project. The current system, despite its bugs, has significant embedded knowledge:
- The exponential smoothing alpha (0.3) for velocity control was likely tuned through real-world testing.
- The deadband of 0.3 degrees was calibrated for this specific FOV and gear ratio.
- The motor speed/acceleration limits (10000 steps/sec, 12500 steps/sec^2) are tuned to avoid stalling this specific stepper motor with this specific gear ratio and load.
- The Arduino feedback format (`FB:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState`) is a protocol contract that cannot change without updating firmware.
- The pose detection center calculation (shoulder midpoint with torso fallback) was iterated to find the most stable tracking point.

A "clean slate" rewrite discards all of this. The typical failure mode: the new code is architecturally cleaner but functionally worse because tuning parameters were not transferred, edge cases were not re-discovered, or the Arduino protocol was subtly misimplemented.

**Why it happens:**
Developers see messy code and assume the entire design is wrong. In reality, brownfield systems contain two types of code: (1) structurally poor code that should be rewritten, and (2) empirically tuned values and edge-case handling that was earned through production experience. A rewrite throws out both.

**How to avoid:**
Use the Strangler Fig pattern: replace components incrementally while keeping the old system runnable as a reference.
1. Before ANY rewrite, extract all tuning parameters and document their source/rationale.
2. Create a recording/replay system (see Pitfall 6) that captures real-world input sequences.
3. Rewrite one component at a time: clock unification first, then motor interpolation, then control algorithms. After each component change, run the same recorded input and verify output matches or improves.
4. Never change more than one layer at a time. Never simultaneously change timing AND control algorithm AND motor protocol.
5. Keep the old `VelocityController`, `PIDController`, and `ProportionalController` in the codebase (renamed to `Legacy_*`) until the new versions match or exceed their performance.

**Warning signs:**
- "Let me just quickly rewrite the whole thing" instinct
- New code works in simulation but behaves differently with real hardware
- Tracking quality degrades but nobody can pinpoint why because too many things changed
- "It used to work but now it does not" with no way to bisect the regression

**Phase to address:**
Every phase. This is a process discipline, not a one-time fix. Every phase should follow the pattern: record baseline, change one thing, verify against baseline.

---

### Pitfall 5: Motion Smoothing That Feels Robotic

**What goes wrong:**
The current `VelocityController` applies exponential smoothing (EMA) to the velocity output: `flSmoothedVelocity = prev + alpha * (desired - prev)`. EMA produces exponential approach curves -- the motion starts fast and decelerates asymptotically. This is mathematically smooth but looks unnatural. Human camera operators produce S-curve motion profiles: slow acceleration, constant velocity in the middle, slow deceleration. EMA produces the deceleration half but not the acceleration half.

Professional PTZ camera systems (Lumens, PTZOptics) use S-shaped acceleration/deceleration ramps with "micro-step changes in speed" specifically because exponential curves look mechanical. At the 6.8 degree narrow FOV of the Sony AX700, the difference between exponential and S-curve motion is highly visible to viewers because the narrow FOV magnifies all motion characteristics.

A second problem: the smoothing alpha (0.3) is frame-rate dependent. If the system drops from 30 FPS to 15 FPS (e.g., due to MediaPipe processing spike), the EMA filter becomes twice as aggressive per unit time, causing the motion to feel jerky during load spikes.

**Why it happens:**
EMA is the simplest smoothing filter and the first one developers reach for. It works "well enough" in early testing because the developer is watching the debug overlay, not a live video feed. The frame-rate dependency is a classic EMA mistake: the alpha should be computed from elapsed time, not applied per-frame.

**How to avoid:**
1. Make smoothing time-aware: compute alpha from elapsed time as `alpha = 1 - exp(-dDeltaTime / flTimeConstantSeconds)`. This produces identical smoothing behavior regardless of frame rate.
2. Use trapezoidal or S-curve motion profiles instead of EMA for the velocity envelope. An S-curve is defined by maximum jerk (rate of change of acceleration), maximum acceleration, and maximum velocity. The AccelStepper library on the Arduino already provides trapezoidal profiles for position moves -- the software-side velocity profile should match.
3. Add a minimum velocity threshold: when the desired velocity drops below a threshold, snap to zero instead of asymptotically approaching zero. The exponential tail of EMA means the motor never quite stops, producing sub-pixel creep that is visible as gentle drift.
4. Test motion quality by recording the output video (not just looking at debug overlays). The difference is obvious in playback.

**Warning signs:**
- Camera motion starts abruptly (no ease-in) but slows down gradually (exponential ease-out)
- Motion feels "correct" at a consistent 30 FPS but jerky when frame rate varies
- The motor never quite reaches a resting state -- always creeping slightly
- Viewers describe the camera as "robotic" or "surveillance-like"

**Phase to address:**
Phase 3 (Motion Quality). This should be addressed AFTER timing (Phase 1) and control correctness (Phase 2) are solid. Smooth motion on top of broken timing just makes the bugs feel smoother, not correct.

---

### Pitfall 6: Untestable Real-Time Control Logic

**What goes wrong:**
The current codebase has zero tests (`pytest tests/` is a TODO). The control algorithms (`PIDController`, `VelocityController`) internally call `time.time()`, making them impossible to test deterministically. Every test run will produce slightly different results because wall-clock time varies. The `TrackerController` depends on live `MotorInterface` and `CameraInterface` instances, making unit testing require hardware.

Without tests, every change is a potential regression. The developers cannot verify whether a bug fix in clock handling actually fixed the center-line drift or just changed when it manifests. There is no way to replay a known scenario and verify the output.

**Why it happens:**
Real-time systems are inherently harder to test than request/response systems. The temptation is "I'll test manually by running it and watching." This works for obvious bugs but fails for subtle timing issues that manifest intermittently. The mixed clock domains (Pitfall 1) make the code even harder to test because the behavior depends on when exactly the OS returns from `time.time()`.

**How to avoid:**
1. Make all control algorithms accept elapsed time as a parameter rather than reading it from the clock. The `calculate_correction_from_error(flErrorDegrees)` signature should become `calculate_correction_from_error(flErrorDegrees, dDeltaTimeSeconds)`. This allows deterministic testing: pass in known deltas and verify outputs.
2. Create a `RecordingMotorInterface` that logs every motor state to a file with timestamps, and a `ReplayMotorInterface` that plays them back. Similarly for camera frames.
3. Build a "golden run" test: record a real tracking session (motor states, camera frames, pose detections) and replay it through the control pipeline. The output motor commands should match within tolerance. Run this test on every code change.
4. For the interpolation logic (motor angle at frame time), create a test with synthetic motor states at known times and verify the interpolated angle is within 0.01 degrees of the expected value.
5. Use `NullMotorInterface` (already exists) as the basis for simulated tests, but extend it to simulate realistic motor dynamics (acceleration curves, feedback delay).

**Warning signs:**
- "I tested it manually and it looks fine" as the only verification
- A bug fix in one area introduces a regression in another area
- Developers are afraid to refactor because they cannot verify correctness
- The same bug reappears after being "fixed" because the fix was for a symptom, not the root cause

**Phase to address:**
Phase 1 (Foundation), built incrementally. The recording/replay infrastructure should be the FIRST deliverable, before any control code changes. This provides the regression safety net for all subsequent phases.

---

### Pitfall 7: Arduino Timestamp Rollover and Clock Drift

**What goes wrong:**
The Arduino `micros()` function returns a 32-bit unsigned integer that rolls over every ~71.6 minutes (2^32 microseconds = 4294.97 seconds). The current code (motor_interface.py line 334-337) handles rollover by detecting when the new timestamp is less than the previous one and incrementing a rollover counter. However, this detection has a race condition: if two consecutive feedback messages arrive with timestamps that are close to the rollover boundary, a legitimate small backward step (due to USB serial reordering) could be misinterpreted as a rollover, adding 4294 seconds of phantom offset.

Additionally, the Arduino crystal oscillator typically has accuracy of +/- 50-100 ppm, meaning the Arduino clock drifts relative to the PC clock by up to 100 microseconds per second (0.36 seconds per hour). The EMA filter (0.98/0.02 weighting) is supposed to track this drift, but its slow convergence means it is always slightly behind the actual drift, creating a systematic bias in motor timestamp estimation.

**Why it happens:**
Arduino timestamp handling is a well-known footgun. The rollover at 71.6 minutes means it will definitely happen during a typical church service (1-2 hours). USB serial communication adds variable latency (0.1-10ms), which means the PC-side receive timestamps do not perfectly reflect the order or timing of events on the Arduino side.

**How to avoid:**
1. Do NOT use Arduino timestamps for control decisions. Use PC-side `perf_counter()` receive timestamps exclusively. The Arduino timestamp is only useful for detecting dropped or reordered messages.
2. If Arduino timestamps are needed for any purpose, use 64-bit timestamps on the Arduino (maintain your own uint64 counter) to eliminate rollover. Or send a "session start" sync message and always compute offsets relative to session start.
3. For rollover detection, require the backward step to be larger than a threshold (e.g., > 2^31 microseconds = ~35 minutes) to distinguish rollover from reordering.
4. Remove the EMA clock offset filter entirely. As argued in Pitfall 2, the PC-side receive timestamp is sufficient and avoids the entire clock-domain-crossing problem.

**Warning signs:**
- System works fine for 60 minutes, then suddenly jumps or glitches (rollover at 71.6 minutes)
- Motor position interpolation accuracy degrades slowly over time (clock drift)
- Debug logging shows motor timestamps that are inconsistent with expected 20ms intervals
- The EMA offset value drifts continuously rather than converging to a stable value

**Phase to address:**
Phase 1 (Foundation). Simplify by eliminating Arduino-to-PC clock mapping entirely. Use PC-side receive timestamps.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Control algorithms internally calling `time.time()` | Simple API (one parameter) | Untestable, mixed clock domains, frame-rate-dependent behavior | Never -- always pass dt as parameter |
| EMA for Arduino clock offset (0.98/0.02) | Simple noise rejection | Convergence bias, systematic timestamp error, complexity | Never -- use PC-side timestamps instead |
| Using `flMotorAngleDegrees` directly without interpolation | Simpler code, no history buffer | Stale angle data during motion (up to 20ms lag) | Only when motor is stationary |
| NullMotorInterface with instant position teleportation | Easy to test without hardware | Does not simulate real motor dynamics (acceleration, delay, backlash) | Early development only; replace with SimulatedMotorInterface for testing |
| Catching all exceptions with bare `except Exception` | Prevents crashes | Hides timing bugs, swallows meaningful errors silently | Never for control-path code; acceptable for UI/visualization |

## Integration Gotchas

Common mistakes when connecting the hardware components in this system.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Arduino serial (USB) | Assuming serial messages arrive in order and without delay. USB buffers data and can deliver multiple messages in a burst after a pause. | Parse ALL available serial data before acting on any of it. Use sequence numbers to detect gaps. Never assume inter-message timing is consistent. |
| OpenCV VideoCapture (USB camera) | Assuming `read()` returns the CURRENT frame. OpenCV buffers 2-5 frames internally, so `read()` may return a frame from 100-166ms ago. | Set `cv2.CAP_PROP_BUFFERSIZE` to 1 if supported. Or call `read()` in a separate thread and always use the latest frame, discarding stale ones. |
| MediaPipe Pose Detection | Assuming processing time is constant. MediaPipe can spike from 10ms to 80ms depending on pose complexity and GPU load. | Measure pose detection time per frame and account for it in the control loop timing. If detection takes too long, skip frames rather than queuing them. |
| AccelStepper on Arduino | Sending new position commands faster than the motor can process them. AccelStepper recalculates its trajectory on every `moveTo()` call, which can cause the motor to stutter if called too frequently. | Implement command deduplication: do not send a new command if the target angle has not changed by more than `flCommandMinDeltaDegrees`. The current code already does this (line 250) -- preserve this behavior in any rewrite. |

## Performance Traps

Patterns that work at low speed/load but fail during real operation.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Processing every frame through MediaPipe | Pose detection takes 30-80ms per frame, consuming the entire 33ms frame budget at 30 FPS | Run pose detection asynchronously. Use the most recent result, not one-per-frame. Drop frames that arrive while detection is still running. | Immediately -- this is likely already an issue at 30 FPS. Verify by logging actual FPS vs configured FPS. |
| Storing `obFrameImage` in every `TrackingSample` | Each 1280x720 BGR frame is ~2.76 MB. At 30 FPS, this is 83 MB/sec of allocations | Only store frame reference when debug visualization is enabled. Set `obFrameImage = None` in production mode. Use a ring buffer of at most 2-3 frames. | After ~5 minutes, Python GC pauses become noticeable. After ~30 minutes, memory pressure causes OS paging. |
| Motor state history deque with maxlen=32 | 32 entries at 50 Hz = 640ms of history. If motor feedback drops for > 640ms (serial glitch), all history is lost | Increase to at least 100 entries (2 seconds). Also persist the last known velocity for extrapolation when history is insufficient. | During any USB hiccup or when another process temporarily blocks the serial port. |
| Logging debug messages in the control loop | Each `logger.debug()` call formats a string and checks handlers, even when debug logging is disabled | Use `logger.isEnabledFor(logging.DEBUG)` guard before string formatting, or use lazy formatting: `logger.debug("angle=%f", angle)` instead of `logger.debug(f"angle={angle}")` | At high log verbosity, the string formatting overhead can consume 1-2ms per frame, reducing effective frame rate. |

## UX Pitfalls

Common user experience mistakes in automated camera tracking.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Camera snaps to center when tracking starts | Jarring transition visible in the live feed; audience notices the "robot waking up" | Ease into tracking: ramp up control authority over the first 1-2 seconds after tracking starts. Use a startup gain multiplier that increases from 0 to 1. |
| Camera oscillates around target position | Distracting back-and-forth "hunting" motion, especially visible at narrow FOV | Widen the deadband for the at-center condition. The current 0.3 degrees is only ~56 pixels -- consider 0.5-1.0 degrees for a "good enough" zone. Also ensure derivative gain is sufficient to dampen oscillations. |
| Camera tracks false positives (audience members, shadows) | Camera suddenly swings to wrong person mid-service | Implement tracking persistence: require N consecutive frames of detection before switching targets. Use a tracking ID (not just "person detected") to maintain identity. Consider a "stage zone" mask that limits tracking to a defined area. |
| Camera returns to home during brief detection drops | Camera swings away from the pastor during a brief occlusion (behind podium) and then swings back, creating a distracting double-motion | Add a "hold" timer: maintain last known position for 2-5 seconds after detection is lost before beginning home return. The current code immediately stops correction when `is_valid_for_tracking()` returns False. |
| Deadband drag in visualization changes motor behavior in production | User adjusts deadband for visual clarity but inadvertently changes tracking behavior during a live event | Separate "visualization deadband" (cosmetic) from "control deadband" (functional). Or require confirmation before applying deadband changes during active tracking. |

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **Clock unification:** All `time.time()` calls replaced with `time.perf_counter()` -- verify by grepping the ENTIRE codebase for `time.time()`. The PID controller, Velocity controller, motor interface, AND any future code must use the same clock.
- [ ] **Motor interpolation:** Interpolated angle is correct during motion -- verify by logging `(frame_timestamp, reported_angle, interpolated_angle, actual_camera_position)` and plotting the error. The error should be < 0.05 degrees (< 10 pixels) during steady-state motion.
- [ ] **Frame-rate independent smoothing:** Smoothing produces the same physical result at 15 FPS as at 30 FPS -- verify by running the same recorded input at different simulated frame rates and comparing output trajectories.
- [ ] **Backlash compensation:** Position tracking is accurate during direction reversals -- verify by commanding a slow oscillation and checking whether the reported output angle matches the expected angle. Look for "flat spots" in the output angle vs time plot.
- [ ] **Recording/replay system:** A recorded session can be replayed deterministically -- verify by replaying the same recording twice and checking that the output motor commands are identical (bitwise, not just approximately).
- [ ] **Startup convergence:** The system reaches stable tracking within 2 seconds of pressing 'S' -- verify by logging the first 60 frames of tracking and checking for position overshoot or filter convergence artifacts.
- [ ] **Arduino rollover:** System operates correctly past the 71.6-minute boundary -- verify by running a 90-minute test (or by simulating rollover in the replay system).

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Mixed clocks causing drift | LOW | Grep for `time.time()` in control code, replace with `perf_counter()` or passed-in timestamps. Run golden test. |
| Home line drift during motion | MEDIUM | Rebuild motor interpolation using PC-side timestamps. Requires understanding the motor state history data structure. Verify with plotted data. |
| Gear backlash position errors | MEDIUM | Add backlash compensation parameter. Requires physical measurement of the backlash amount (rotate back and forth, measure dead zone). |
| Brownfield rewrite regression | HIGH | If caught early: `git bisect` to find the breaking change, revert, apply incrementally. If caught late (many changes): compare old and new behavior on recorded sessions, identify which component diverged. |
| Robotic motion smoothing | LOW | Replace EMA with time-aware smoothing (one-line formula change). S-curve profiles are more work but have well-documented implementations. |
| No tests for control code | MEDIUM | Refactor control algorithm signature to accept dt parameter. Build recording/replay infrastructure. Initial investment is 2-3 days but pays for itself immediately. |
| Arduino timestamp rollover | LOW | Switch to PC-side timestamps only. Remove Arduino timestamp processing. Or add rollover threshold check (> 2^31 = genuine rollover). |

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Mixed clock domains | Phase 1: Foundation | Zero `time.time()` calls in control code. All timestamps from unified clock source. |
| Home line drift | Phase 1: Foundation | Plot motor angle error vs motor speed. Error < 0.05 deg at all speeds. |
| Arduino timestamp rollover | Phase 1: Foundation | 90-minute continuous operation test. No position jumps. |
| Recording/replay system | Phase 1: Foundation | Replay same recording twice, get identical motor commands. |
| Brownfield regression | Phase 1+: All phases | Golden-run test passes after every component change. |
| Gear backlash | Phase 2: Control Refinement | Direction-reversal test shows < 0.03 deg position error. |
| Untestable control logic | Phase 2: Control Refinement | Control algorithms accept dt parameter. 100% of control paths have unit tests. |
| Robotic motion smoothing | Phase 3: Motion Quality | Recorded output video reviewed by non-technical viewer. Camera motion judged "natural." |
| Frame-rate dependent smoothing | Phase 3: Motion Quality | Same input at 15/30/60 FPS produces trajectories within 0.1 deg of each other. |
| False positive tracking | Phase 4: Robustness | Stage zone mask configured. Multi-frame persistence required for target switch. |

## Sources

- [Python `time` module official documentation](https://docs.python.org/3/library/time.html) -- `time.time()` is adjustable and non-monotonic; `time.perf_counter()` is monotonic with highest resolution. Mixing clocks produces undefined results. (HIGH confidence)
- [PEP 418 -- Add monotonic time, performance counter, and process time functions](https://peps.python.org/pep-0418/) -- Design rationale for separating clock types in Python. (HIGH confidence)
- [Super Fast Python: time.time vs time.perf_counter](https://superfastpython.com/time-time-vs-time-perf_counter/) -- Practical examples of bugs caused by mixing clocks, including NTP adjustment glitches. (MEDIUM confidence)
- [Analog Devices: Synchronization of Multiaxis Motion Control](https://www.analog.com/en/resources/analog-dialogue/articles/synchronization-of-multi-axis-motion-control-over-real-time-networks.html) -- Timing error has the same effect as position and velocity error. (HIGH confidence)
- [Lumens: Breakthrough Smooth PTZ Movement](https://www.mylumens.com/en/Blog_detail/134/Breakthrough-Smooth-PTZ-Movement) -- Professional PTZ cameras use S-shaped acceleration/deceleration ramps, not exponential smoothing. (MEDIUM confidence)
- [PTZOptics: PTZ Speed Sync](https://ptzoptics.com/ptz-speed-sync/) -- Synchronizing pan/tilt/zoom speeds for natural motion. (MEDIUM confidence)
- [Martin Fowler: Strangler Fig Application](https://martinfowler.com/bliki/StranglerFigApplication.html) -- Incremental replacement pattern for brownfield systems. (HIGH confidence)
- [Hackaday: How Accurate Is Microstepping Really?](https://hackaday.com/2016/08/29/how-accurate-is-microstepping-really/) -- Microstepping accuracy is +/- 5% non-cumulative; beyond 10 microsteps, accuracy does not meaningfully improve. (MEDIUM confidence)
- [Gecko Drive: Accuracy and Resolution](https://www.geckodrive.com/support/accuracy-and-resolution/) -- Stepper motor position accuracy fundamentals. (HIGH confidence)
- [Antithesis: Deterministic Simulation Testing](https://antithesis.com/resources/deterministic_simulation_testing/) -- Testing real-time systems by virtualizing time and making execution deterministic. (MEDIUM confidence)
- [ISA: How to Avoid Common Tuning Mistakes With PID Controllers](https://blog.isa.org/avoid-common-tuning-mistakes-pid) -- PID tuning pitfalls including measurement lag masking instability. (HIGH confidence)
- Codebase analysis: `src/control/control_algorithm.py`, `src/interfaces/motor_interface.py`, `src/interfaces/camera_interface.py`, `src/control/tracker_controller.py`, `src/main.py` -- Direct evidence of mixed clocks, EMA filter parameters, and motor protocol. (HIGH confidence)

---
*Pitfalls research for: Camera-motor synchronization for automated person tracking (Pastor Tracking System)*
*Researched: 2026-02-15*
