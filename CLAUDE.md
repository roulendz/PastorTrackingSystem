# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated person-tracking camera system using MediaPipe Pose detection, OpenCV, and Arduino stepper motor control. Designed for live events (church services, presentations) where the camera must follow a moving person. Python 3.10, runs on Windows.

## Running the Application

```bash
cd src
python main.py                                    # Default config
python main.py --config ../config/my_config.json  # Custom config
python main.py --debug                            # Debug logging
python main.py --edit-config                      # Open config editor GUI first
```

Keyboard controls at runtime: S=start tracking, P=pause, C=calibrate, H=home motor, Q=quit.

No test suite exists yet (`pytest tests/` is a TODO).

## Architecture

**Data flow:** Camera (30 FPS) -> PoseTracker (MediaPipe) -> TrackerController (orchestrator) -> MotorInterface (Arduino serial)

Key architectural decisions:
- **TrackingSample** (`src/core/tracking_sample.py`): Atomic measurement pairing a camera frame with the motor's ACTUAL reported angle (not the commanded target). This is the fundamental data unit flowing through the system.
- **Strategy pattern for control**: `ControlAlgorithm` is an abstract base class with P, PID, and Velocity implementations in `src/control/control_algorithm.py`. Swappable via config `sControlAlgorithmType`.
- **Dependency injection**: `TrackerController` receives all its dependencies (motor, camera, pose tracker, control algorithm). It orchestrates but contains no business logic.
- **Thread-safe motor state**: `MotorInterface` uses a background thread for async Arduino feedback with lock-protected state. Main loop gets thread-safe copies via `get_latest_motor_state()`.
- **Configuration layering**: `config/default_config.json` is the base; `config/user_config.json` overlays user-specific values. Managed by `ConfigurationManager` singleton.
- **NullMotorInterface**: Allows running without hardware connected (controlled by `bAllowStartWithoutMotor` config flag).

## Module Map

| Module | Path | Purpose |
|--------|------|---------|
| Entry point | `src/main.py` | Init, event loop, visualization overlay |
| Motor comms | `src/interfaces/motor_interface.py` | Arduino serial protocol, thread-safe state |
| Camera capture | `src/interfaces/camera_interface.py` | OpenCV capture with precise timestamps |
| Pose detection | `src/tracking/pose_tracker.py` | MediaPipe Pose, returns center + confidence |
| Control algorithms | `src/control/control_algorithm.py` | P/PID/Velocity controllers |
| Orchestrator | `src/control/tracker_controller.py` | Main tracking loop, builds TrackingSamples |
| Config manager | `src/utilities/config_manager.py` | JSON config load/save/merge |
| Settings GUI | `src/ui/live_settings_panel.py` | Dear ImGui real-time parameter tuning |
| Config editor | `src/ui/config_editor.py` | Standalone config file editor GUI |

## Naming Conventions

This codebase uses **Hungarian notation** consistently. Follow it in all new code:

- `fl` = float (`flMotorAngleDegrees`)
- `i` = int (`iCameraWidthPixels`)
- `b` = bool (`bPersonWasDetected`)
- `s` = string (`sMotorSerialPortName`)
- `ob` = object (`obFrameImage`)
- `v` = list/vector (`vLandmarks`)
- `d` = double/high-precision float (`dSampleTimestampSeconds`)

Functions use `verb_noun_descriptor` format: `send_move_to_angle_command()`, `detect_person_in_frame()`, `calculate_correction_from_error()`.

Config keys also use Hungarian prefixes matching the type of their value.

## Adding a New Control Algorithm

Subclass `ControlAlgorithm` and implement `calculate_correction_from_error(flErrorDegrees) -> float`. Register it in the algorithm selection logic in `tracker_controller.py`.

## Dependencies

OpenCV, MediaPipe, pyserial, numpy, dataclasses-json, dearpygui. Install via `pip install -r requirements.txt`. Arduino firmware requires the AccelStepper library.
