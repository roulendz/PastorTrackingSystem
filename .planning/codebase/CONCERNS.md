# Codebase Concerns

**Analysis Date:** 2026-02-15

## Tech Debt

**No Test Suite:**
- Issue: CLAUDE.md explicitly states "No test suite exists yet (`pytest tests/` is a TODO)"
- Files: Entire codebase - no `tests/` directory exists
- Impact: Zero automated verification of functionality. Changes to motor control, pose detection, or control algorithms cannot be validated without manual testing with physical hardware. Regression bugs can silently propagate.
- Fix approach: Start with critical path unit tests (motor interface serial protocol parsing, control algorithm calculations, TrackingSample state management). Add integration tests for camera/pose detection pipeline. Mock hardware interfaces for CI/CD.

**Silent Exception Swallowing:**
- Issue: Widespread use of bare `except Exception:` handlers that suppress errors without logging
- Files: `src/ui/live_settings_panel.py` (19 instances at lines 108, 192, 201, 257, 271, 298, 320, 351, 375, 417, 434, 441, 554, 559, 601), `src/main.py` (lines 203, 415, 420, 424, 435), `src/tracking/pose_tracker.py` (lines 182, 186, 269), `src/control/tracker_controller.py` (lines 109, 264, 269), `src/utilities/config_manager.py` (lines 125, 224)
- Impact: Errors fail silently, making debugging extremely difficult. UI updates may fail without user notification. Configuration parsing errors are hidden. Motor angle estimation falls back to stale values without warning.
- Fix approach: Replace bare `except Exception:` with specific exception types. Add logging for all caught exceptions. Use explicit pass only when failure is truly acceptable with comment explaining why.

**Global State in UI Module:**
- Issue: `live_settings_panel.py` uses global variables for UI state management
- Files: `src/ui/live_settings_panel.py` lines 293, 385, 395, 576 (`_g_dSavedUI`, `_g_iCenterAngleItem`, `_g_iFovDegreesItem`, `_g_bProgrammaticUpdate`)
- Impact: State is not encapsulated. Multiple instances of settings panel would conflict. Difficult to test. Thread safety not guaranteed (UI runs on separate thread via DearPyGUI).
- Fix approach: Encapsulate globals into a class instance. Pass UI controller object to callbacks instead of relying on module-level state. Consider using dataclass for UI state.

**Hardcoded Sleep Durations:**
- Issue: Thread sleep durations are magic numbers without explanation
- Files: `src/interfaces/motor_interface.py` lines 98, 113 (2 second sleep after serial open), line 307 (1ms busy-wait avoidance), line 311 (100ms error recovery), `src/main.py` line 455 (2 second sleep before motor disable)
- Impact: 2-second blocking delays during initialization slow startup. No justification for why 2 seconds is correct (Arduino boot time varies by model). 100ms error recovery may be too aggressive or too slow.
- Fix approach: Extract to named constants with comments explaining duration choice. Make Arduino initialization timeout configurable. Consider polling ready state instead of fixed sleep.

**Incomplete NullMotorInterface Implementation:**
- Issue: `NullMotorInterface` doesn't fully simulate real motor behavior
- Files: `src/interfaces/motor_interface.py` lines 387-446
- Impact: Angle changes are instant (line 409, 417) - no simulation of acceleration/velocity. No motor state history maintained, so `get_estimated_motor_angle_degrees()` always returns current angle. Testing without hardware won't catch timing-dependent control bugs. Sequence numbers don't increment.
- Fix approach: Add simple physics simulation (velocity, acceleration limits). Maintain state history deque. Increment sequence numbers. Simulate realistic feedback timing delays.

## Known Bugs

**Arduino Timestamp Rollover Logic:**
- Symptoms: Timestamp synchronization uses 32-bit unsigned integer rollover detection
- Files: `src/interfaces/motor_interface.py` lines 334-344
- Trigger: Arduino `micros()` rolls over every ~71.6 minutes. Rollover counter increments, offset smoothing applies. If rollover happens during connection loss, desynchronization occurs.
- Workaround: System restarts before 71 minutes typically. Reconnecting motor resets offset calculation.
- Impact: Long-running sessions (multi-hour events) may see timestamp drift after rollover, causing control loop timing errors.

**FOV Calibration State Leak:**
- Symptoms: Pressing 'C' to start calibration followed by error causes partial calibration state to persist
- Files: `src/main.py` lines 394-425
- Trigger: Press 'C' to capture first point, then exception occurs in calculation block. `dFovCalibration` remains set to first point instead of resetting to `None`.
- Workaround: Press 'C' twice more to complete/reset or restart application
- Impact: Next calibration attempt will think it's on second point when it's actually first, producing incorrect FOV calculation.

**Frame Capture Returns Timestamp Even on Failure:**
- Symptoms: `capture_frame_with_timestamp()` returns valid timestamp even when frame is None
- Files: `src/interfaces/camera_interface.py` lines 124-126
- Trigger: Camera read fails (disconnected, out of memory, driver error)
- Impact: Controller creates TrackingSample with None frame image but valid timestamp. Downstream code checks `obFrameImage is not None` before use, but timestamp represents when failure occurred, not when valid data was captured. Could cause subtle timing artifacts if failure is transient.

## Security Considerations

**No Serial Port Validation:**
- Risk: Motor interface attempts to auto-connect to all available serial ports
- Files: `src/interfaces/motor_interface.py` lines 105-121
- Current mitigation: None - code iterates through all COM ports and tries to connect
- Recommendations: Validate port name against expected pattern (COM\d+, /dev/ttyUSB\d+, /dev/ttyACM\d+). Add config whitelist for allowed ports. Require user confirmation before auto-connecting to unknown devices. Could accidentally connect to USB serial devices that aren't the motor controller.

**Configuration Files Store Absolute Serial Port Paths:**
- Risk: User config files may contain system-specific paths that leak directory structure
- Files: `src/utilities/config_manager.py` line 25 (default `/dev/ttyUSB0`), `config/default_config.json`, `config/user_config.json`
- Current mitigation: Config files are not versioned in git (`.gitignore` would prevent)
- Recommendations: Verify user_config.json is in .gitignore. Document that config files should not be committed. Consider platform-agnostic port naming ("auto", "first", "arduino") with discovery logic.

**No Input Sanitization on Motor Commands:**
- Risk: Motor angle commands are formatted directly into serial strings
- Files: `src/interfaces/motor_interface.py` line 153 (`f"M,{flTargetAngleDegrees:.2f}\n"`)
- Current mitigation: Arduino firmware likely validates input, angles are clamped before sending (tracker_controller.py lines 244-247)
- Recommendations: Already mitigated by clamping in tracker_controller. Could add assertion to verify angle is finite float before sending.

## Performance Bottlenecks

**MediaPipe Pose Runs on Every Frame:**
- Problem: Pose detection is computationally expensive, runs at full camera FPS (30 Hz)
- Files: `src/tracking/pose_tracker.py` lines 80-126, `src/control/tracker_controller.py` line 113
- Cause: No frame skipping or adaptive processing. Every camera frame goes through full MediaPipe pipeline even when person is centered and stationary.
- Improvement path: Implement adaptive frame rate - run detection at 30 Hz when moving, drop to 10 Hz when person centered for >2 seconds. Use motion detection (frame differencing) to trigger full pose pipeline. Model complexity is configurable (line 63) but hardcoded to 1.

**BGR to RGB Conversion on Every Frame:**
- Problem: OpenCV captures BGR, MediaPipe requires RGB, conversion happens every frame
- Files: `src/tracking/pose_tracker.py` line 96 (`cv2.cvtColor(obFrameImage, cv2.COLOR_BGR2RGB)`)
- Cause: OpenCV default color space mismatch with MediaPipe
- Improvement path: Check if camera driver supports native RGB output (`CAP_PROP_CONVERT_RGB`). Pre-allocate conversion buffer. Consider YUV color space if camera supports it natively.

**Synchronous Serial Communication:**
- Problem: Motor commands are sent synchronously, blocking until write completes
- Files: `src/interfaces/motor_interface.py` lines 258-270 (`_send_command`)
- Cause: `serial.Serial.write()` blocks until bytes are transmitted. At 115200 baud with 20-byte command, this is ~1.7ms - negligible but unnecessary.
- Improvement path: Already mitigated by asynchronous feedback thread. Write operations are fast enough at 115200 baud. Only optimize if profiling shows this is a bottleneck (unlikely).

**Visualization Drawing Overhead:**
- Problem: OpenCV drawing operations (overlays, landmarks, text) on every frame even when window not visible
- Files: `src/main.py` lines 222-298 (visualization overlay), `src/tracking/pose_tracker.py` lines 146-153 (landmark drawing)
- Cause: Visualization enabled by default, no check if window is minimized/hidden, drawing happens before `cv2.imshow`
- Improvement path: Add config flag to disable overlay when running headless. Skip drawing if `cv2.getWindowProperty` indicates window not visible. Move text rendering to GPU if available.

## Fragile Areas

**Motor State Timestamp Synchronization:**
- Files: `src/interfaces/motor_interface.py` lines 332-346
- Why fragile: Complex time synchronization between Arduino `micros()` and Python `time.perf_counter()`. Uses exponential smoothing on offset (98% old, 2% new). Assumes monotonic clocks, no NTP sync during operation. Rollover detection relies on unsigned integer wrap.
- Safe modification: Do not modify offset smoothing alpha without testing multi-hour sessions. Do not change rollover threshold (4294967296). Test thoroughly if Arduino timestamp format changes.
- Test coverage: None - requires hardware and long-running tests

**FOV-to-Pixel Conversion Math:**
- Files: `src/control/tracker_controller.py` lines 280-290 (`_get_angle_per_pixel_degrees`), lines 221-222 (pixel error to angle error conversion)
- Why fragile: Division by image width, fallback logic when FOV not calibrated, multiple configuration sources (flFieldOfViewDegrees, flInitialAnglePerPixelDegrees). Returning 0.0 when width is 0 causes division by zero in caller code (line 222, 249).
- Safe modification: Always validate FOV > 0 before using. Check for zero image width at entry point. Add assertions for non-zero denominators. Document units clearly (degrees vs radians).
- Test coverage: None

**Deadzone UI Mouse Dragging:**
- Files: `src/main.py` lines 154-220 (`DeadzoneUIController`)
- Why fragile: Stateful mouse interaction (bDraggingLeft, bDraggingRight, bDraggingCenter), pixel-to-angle conversion, direct motor commands during drag, cross-module function calls to update UI sliders (lines 201-204)
- Safe modification: Test all edge cases (drag outside window, release outside window, rapid clicks). Ensure state resets on window close. Verify motor safety limits are enforced during drag.
- Test coverage: None

**PID Controller State Management:**
- Files: `src/control/control_algorithm.py` lines 105-107 (integral accumulator, previous error, previous time)
- Why fragile: Shared mutable state across control loop iterations. Integral windup clamping (lines 137-140). Time delta validation (lines 128-129) prevents divide-by-zero but caps at 0.001s regardless of actual delta. Derivative term amplifies noise (line 144).
- Safe modification: Always call `reset_controller()` when switching between tracking/idle states (tracker_controller.py line 147 does this). Do not modify anti-windup limits without testing oscillation scenarios. Validate time delta is positive before division.
- Test coverage: None

## Scaling Limits

**Single-Person Detection Only:**
- Current capacity: MediaPipe Pose configured for single-person detection (`static_image_mode=False`)
- Limit: If multiple people appear in frame, MediaPipe may switch tracking between them erratically, causing camera to oscillate
- Scaling path: Add person tracking ID persistence (MediaPipe provides tracking). Implement "lock-on" mode where first detected person is followed exclusively. Add UI to select which person to track when multiple detected. Requires major refactor of PoseTracker to maintain person identity across frames.

**Camera Frame Rate Ceiling:**
- Current capacity: Hardcoded 30 FPS processing loop (`src/interfaces/camera_interface.py` line 36)
- Limit: MediaPipe Pose Full model (complexity=1) at 1280x720 achieves ~25-35 FPS on mid-range CPU. Higher FPS requires GPU or lighter model (complexity=0). Control loop runs at camera rate, so faster camera doesn't improve tracking if pose detection can't keep up.
- Scaling path: Decouple camera capture FPS from pose detection FPS. Run capture at 60 FPS, process every Nth frame for pose, interpolate motor angle between detections. Requires multi-threaded frame buffer.

**Serial Communication Bandwidth:**
- Current capacity: Arduino sends feedback at ~100 Hz (10ms interval typical for AccelStepper). At 115200 baud, ~100 bytes/message = ~10KB/s. Feedback thread reads continuously (line 296).
- Limit: Increasing feedback rate to 500 Hz would saturate serial buffer. No flow control implemented. If Arduino sends faster than Python reads, buffer overflow causes stale data.
- Scaling path: Implement binary protocol instead of ASCII (more efficient). Add sequence number gap detection. Use higher baud rate (230400, 460800). Add flow control handshaking.

**Configuration File Size:**
- Current capacity: JSON config parsed fully into memory on every load, user config overlaid (lines 200-234)
- Limit: Current config is ~100 lines. If UI parameters expand to hundreds of sliders (per-landmark weights, advanced PID tuning), full reload on every change becomes slow. No incremental update.
- Scaling path: Already not a problem for current use case. If needed, implement incremental config updates (patch semantics), only reload changed sections, cache parsed config tree.

## Dependencies at Risk

**OpenCV 4.8.1.78 (from Oct 2023):**
- Risk: Not latest version (4.10.x available). Security patches and bug fixes missed.
- Impact: Camera drivers evolve rapidly. Newer cameras may not work with older OpenCV. Potential security issues in image codecs or video capture backends.
- Migration plan: Test with opencv-python==4.10.0.84. Verify camera enumeration, frame capture timing, color conversion still work. Check for breaking API changes in CAP_PROP constants. Update requirements.txt after testing.

**MediaPipe 0.10.8 (from Dec 2023):**
- Risk: MediaPipe development has slowed/shifted to MediaPipe Tasks API. Pose solution may be deprecated.
- Impact: No critical issues yet, but future Python versions (3.12+) may drop support. Performance improvements in newer versions unavailable.
- Migration plan: Monitor MediaPipe releases. Test compatibility with Python 3.11/3.12. Consider migrating to MediaPipe Tasks PoseLandmarker when stable. Benchmark performance difference before migrating.

**NumPy 1.24.3 (from April 2023):**
- Risk: NumPy 2.0 released with breaking changes. Current version is pre-2.0 API.
- Impact: Many deprecation warnings likely on modern Python. Future MediaPipe/OpenCV versions may require NumPy 2.x.
- Migration plan: Pin to numpy<2.0.0 in requirements.txt to prevent surprise breakage. Test with numpy==2.1.0 in isolated environment. Update array construction patterns (dtype specification).

**PySerial 3.5 (from 2020):**
- Risk: Old but stable. No major updates needed for basic serial communication.
- Impact: Minimal - serial protocol is stable. New USB chipsets may lack drivers.
- Migration plan: Low priority. Upgrade to pyserial==3.5.x latest patch version if issues arise.

**DearPyGUI 1.11.1:**
- Risk: Relatively new GUI framework, smaller community than tkinter/Qt. Breaking API changes possible.
- Impact: UI code is isolated in `src/ui/`. If DearPyGUI becomes unmaintained, entire UI needs rewrite.
- Migration plan: Monitor project activity. If maintenance stops, migrate to Tkinter (stdlib, no dependency) or PyQt (more features). UI is ~860 lines total, manageable rewrite.

## Missing Critical Features

**No Emergency Stop Mechanism:**
- Problem: No hardware emergency stop button or safety interlock
- Blocks: Cannot safely deploy in production environment (church service) where motor malfunction could injure someone or damage equipment
- Priority: High - safety-critical for live deployment
- Fix approach: Add GPIO input on Arduino for physical E-stop button. Implement "dead-man switch" requiring periodic heartbeat from Python. Add motor angle rate-of-change limit to detect runaway conditions.

**No Recording/Playback for Testing:**
- Problem: Cannot record camera feed + motor angles for replay testing
- Blocks: Unable to reproduce bugs that occur during live events. Cannot test control algorithm changes without physical setup. No benchmarking dataset.
- Priority: Medium - quality-of-life for development
- Fix approach: Add `--record` mode that saves frames + TrackingSample data to file. Add `--playback` mode that replays recording through control loop without camera/motor. Use compressed video (H.264) + JSON metadata sidecar.

**No Multi-Camera Support:**
- Problem: Cannot switch between multiple camera angles or use camera array
- Blocks: Advanced setups with wide-angle + telephoto cameras, or multi-room installations
- Priority: Low - not required for initial use case
- Fix approach: Refactor CameraInterface to support multiple VideoCapture instances. Add camera selection in UI. Implement camera switching based on person position (wide when far, tele when close).

**No Network Control Interface:**
- Problem: All control is local keyboard/UI - no remote operation
- Blocks: Camera operator cannot control system from sound booth or remote location. No integration with streaming software (OBS).
- Priority: Medium - important for production use
- Fix approach: Add REST API or WebSocket server for remote control. Implement web UI for mobile devices. Add OBS plugin for direct integration. Security concern: requires authentication if exposed on network.

## Test Coverage Gaps

**Untested area: Motor Interface Serial Protocol Parsing:**
- What's not tested: Feedback message parsing (`_parse_feedback_message` lines 313-380), timestamp rollover handling, malformed message handling
- Files: `src/interfaces/motor_interface.py`
- Risk: Malformed Arduino messages could crash feedback thread, corrupt motor state, or cause infinite loop. Timestamp rollover has never been tested in practice (requires 71+ minute runtime).
- Priority: High - critical path for motor control

**Untested area: Control Algorithm Calculations:**
- What's not tested: PID integral windup, derivative term on noisy input, velocity controller smoothing, edge cases (NaN, infinity)
- Files: `src/control/control_algorithm.py`
- Risk: Control instability, motor oscillation, or runaway could occur with extreme inputs. No validation that PID parameters produce stable response.
- Priority: High - safety and stability critical

**Untested area: Configuration Validation:**
- What's not tested: Invalid config values (negative resolutions, reversed angle limits, malformed JSON), config overlay merging logic
- Files: `src/utilities/config_manager.py`
- Risk: Bad config could brick the system (camera fails to open, motor limits allow mechanical collision). Silent failures in overlay merging (lines 212-225) mean user config may not apply.
- Priority: Medium - impacts reliability

**Untested area: Camera Capture Timing:**
- What's not tested: Frame timestamp accuracy, dropped frame handling, camera disconnection recovery
- Files: `src/interfaces/camera_interface.py`
- Risk: Timestamp drift could desync with motor state, causing control errors. No verification that `time.perf_counter()` timestamp matches actual frame acquisition time (camera buffering introduces lag).
- Priority: Medium - affects control accuracy

**Untested area: TrackingSample State Consistency:**
- What's not tested: Pixel offset calculation edge cases (zero width), confidence thresholding, sequence number overflow
- Files: `src/core/tracking_sample.py`
- Risk: Core data structure assumptions (width > 0, sequence numbers unique) not validated. Could cause silent data corruption.
- Priority: Low - simple logic, but foundational

---

*Concerns audit: 2026-02-15*
