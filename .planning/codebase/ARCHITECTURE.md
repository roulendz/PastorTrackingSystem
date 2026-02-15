# Architecture

**Analysis Date:** 2026-02-15

## Pattern Overview

**Overall:** Layered Pipeline Architecture with Dependency Injection

**Key Characteristics:**
- Unidirectional data flow: Camera → PoseTracker → TrackerController → MotorInterface → Arduino
- Orchestrator pattern with `TrackerController` coordinating all components
- Strategy pattern for swappable control algorithms
- Thread-safe asynchronous motor state management
- Atomic measurements via `TrackingSample` pairing camera frames with motor angles

## Layers

**Hardware Interface Layer:**
- Purpose: Abstract external devices (camera, motor) from business logic
- Location: `src/interfaces/`
- Contains: Serial communication, OpenCV capture, hardware state management
- Depends on: pyserial, OpenCV, external hardware (Arduino, camera)
- Used by: TrackerController (orchestrator)

**Core Data Layer:**
- Purpose: Define fundamental data structures shared across system
- Location: `src/core/`
- Contains: `TrackingSample` dataclass (immutable measurement pairing frame with motor angle)
- Depends on: Nothing (pure data)
- Used by: All layers reference TrackingSample

**Tracking Layer:**
- Purpose: Person detection and pose estimation
- Location: `src/tracking/`
- Contains: MediaPipe Pose integration, confidence scoring, landmark processing
- Depends on: MediaPipe, OpenCV, numpy
- Used by: TrackerController

**Control Layer:**
- Purpose: Calculate motor corrections from tracking errors
- Location: `src/control/`
- Contains: Abstract `ControlAlgorithm` base class, P/PID/Velocity implementations, TrackerController orchestrator
- Depends on: Core data layer, all interface and tracking layers
- Used by: Main entry point

**Utilities Layer:**
- Purpose: Cross-cutting concerns (config, rendering, logging)
- Location: `src/utilities/`
- Contains: JSON configuration management with layering, text rendering helpers
- Depends on: Python stdlib, dataclasses-json
- Used by: All layers

**UI Layer:**
- Purpose: Real-time parameter tuning and configuration editing
- Location: `src/ui/`
- Contains: Dear ImGui settings panel (live control), standalone config editor GUI
- Depends on: dearpygui, ConfigurationManager
- Used by: Main loop for visualization and tuning

## Data Flow

**Main Tracking Loop (30 FPS):**

1. `CameraInterface.capture_frame_with_timestamp()` → Returns (frame, timestamp) using `time.perf_counter()`
2. `MotorInterface.get_latest_motor_state()` → Thread-safe snapshot of current motor angle (NOT target angle)
3. `PoseTracker.detect_person_in_frame(frame)` → MediaPipe processes RGB frame, returns `PoseResult` with center position and confidence
4. `TrackerController` builds atomic `TrackingSample` combining frame timestamp, ACTUAL motor angle, person position, detection confidence
5. If tracking enabled: `ControlAlgorithm.calculate_correction_from_error(angle_error)` → Returns correction in degrees
6. TrackerController clamps correction to safety limits, sends `MotorInterface.send_move_to_angle_command(target)`
7. Arduino executes move, sends feedback via serial in background thread
8. Feedback thread updates `MotorInterface._obLatestMotorState` (lock-protected)
9. Visualization overlay drawn on frame using OpenCV primitives
10. Loop repeats

**State Management:**
- Motor state updated asynchronously by background thread reading Arduino serial feedback
- Configuration layered: `default_config.json` overridden by `user_config.json` via `ConfigurationManager`
- Tracking state machine: IDLE ↔ TRACKING (with ERROR state)

## Key Abstractions

**TrackingSample:**
- Purpose: Atomic measurement unit pairing camera frame with actual motor position
- Examples: `src/core/tracking_sample.py`
- Pattern: Immutable dataclass with validation methods (`is_valid_for_tracking()`, `get_pixel_offset_from_center()`)

**ControlAlgorithm:**
- Purpose: Strategy interface for swappable control algorithms
- Examples: `src/control/control_algorithm.py` (ProportionalController, PIDController, VelocityController)
- Pattern: Abstract base class with `calculate_correction_from_error()` and `reset_controller()` methods

**MotorState:**
- Purpose: Snapshot of motor position, target, speed, and movement status
- Examples: `src/interfaces/motor_interface.py`
- Pattern: Dataclass with thread-safe access via lock

**PoseResult:**
- Purpose: Encapsulate person detection output (position, confidence, landmarks)
- Examples: `src/tracking/pose_tracker.py`
- Pattern: Dataclass separating detection logic from result representation

**NullMotorInterface:**
- Purpose: Null Object pattern for running without hardware
- Examples: `src/interfaces/motor_interface.py` (lines 387-446)
- Pattern: Implements same interface as MotorInterface but with no-op operations

## Entry Points

**Main Application:**
- Location: `src/main.py`
- Triggers: `python main.py` (with optional `--config`, `--debug`, `--edit-config` flags)
- Responsibilities: Parse CLI args, load config, initialize all components via dependency injection, run 30 FPS event loop with keyboard controls, cleanup on shutdown

**Configuration Editor:**
- Location: `src/ui/config_editor.py`
- Triggers: `python main.py --edit-config` or standalone
- Responsibilities: GUI for editing JSON config files before runtime

**Arduino Firmware:**
- Location: `arduino/StepperController/StepperController.ino`
- Triggers: Uploaded to Arduino, receives serial commands from Python
- Responsibilities: Execute stepper motor commands, send feedback messages (FB: format)

## Error Handling

**Strategy:** Defensive programming with graceful degradation

**Patterns:**
- Hardware connection failures fall back to `NullMotorInterface` if `bAllowStartWithoutMotor` enabled
- Missing person detection returns empty `PoseResult` (bPersonWasDetected=False) rather than throwing
- Invalid config values logged as errors with validation in `ConfigurationManager.validate_configuration()`
- Serial communication errors caught in `_send_command()` and logged, returning False
- Main loop catches all exceptions in `execute_main_tracking_loop_tick()`, sets state to ERROR, returns None
- Thread synchronization via `threading.Lock` for motor state prevents race conditions

## Cross-Cutting Concerns

**Logging:** Python stdlib `logging` module with configurable levels (INFO default, DEBUG with `--debug` flag). All modules use `logger = logging.getLogger(__name__)` pattern.

**Validation:** Configuration validation in `ConfigurationManager.validate_configuration()` checks motor angle ranges, camera resolution, control gains. TrackingSample validates confidence thresholds via `is_valid_for_tracking()`.

**Authentication:** Not applicable (local desktop application with direct hardware control)

---

*Architecture analysis: 2026-02-15*
