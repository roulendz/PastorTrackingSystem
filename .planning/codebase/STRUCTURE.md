# Codebase Structure

**Analysis Date:** 2026-02-15

## Directory Layout

```
PastorTrackingSystem/
├── .claude/               # Claude Code configuration
├── .planning/             # GSD planning documents
│   └── codebase/          # Codebase analysis (this directory)
├── .venv/                 # Python virtual environment
├── arduino/               # Arduino firmware
│   └── StepperController/ # Stepper motor controller sketch
├── config/                # Configuration files
├── docs/                  # Documentation
├── scripts/               # Utility scripts
├── src/                   # Python source code
│   ├── control/           # Control algorithms and orchestrator
│   ├── core/              # Core data structures
│   ├── interfaces/        # Hardware interfaces
│   ├── tracking/          # Person detection
│   ├── ui/                # User interface components
│   └── utilities/         # Configuration and helpers
├── CLAUDE.md              # Claude Code onboarding guide
├── README.md              # Project README
└── requirements.txt       # Python dependencies
```

## Directory Purposes

**`src/`:**
- Purpose: All Python source code
- Contains: Main entry point, module packages
- Key files: `main.py` (application entry point)

**`src/control/`:**
- Purpose: Control algorithms and main tracking orchestrator
- Contains: Abstract ControlAlgorithm base class, P/PID/Velocity implementations, TrackerController
- Key files: `control_algorithm.py` (strategy implementations), `tracker_controller.py` (orchestrator)

**`src/core/`:**
- Purpose: Core data structures shared across system
- Contains: TrackingSample dataclass
- Key files: `tracking_sample.py` (atomic measurement pairing frame with motor angle)

**`src/interfaces/`:**
- Purpose: Hardware abstraction layer
- Contains: Camera capture, motor serial communication
- Key files: `motor_interface.py` (Arduino serial protocol, thread-safe state), `camera_interface.py` (OpenCV capture)

**`src/tracking/`:**
- Purpose: Person detection using MediaPipe
- Contains: Pose detection, landmark processing, confidence scoring
- Key files: `pose_tracker.py` (MediaPipe Pose wrapper)

**`src/ui/`:**
- Purpose: User interface components
- Contains: Real-time settings panel, configuration editor
- Key files: `live_settings_panel.py` (Dear ImGui runtime tuning), `config_editor.py` (standalone config GUI)

**`src/utilities/`:**
- Purpose: Cross-cutting concerns
- Contains: Configuration management, text rendering
- Key files: `config_manager.py` (JSON config loading with layering), `text_renderer.py` (OpenCV text helpers)

**`config/`:**
- Purpose: System configuration files
- Contains: Base config and user overrides
- Key files: `default_config.json` (base configuration), `user_config.json` (user-specific overrides)

**`arduino/`:**
- Purpose: Arduino firmware for motor controller
- Contains: Stepper controller sketch using AccelStepper
- Key files: `StepperController/StepperController.ino` (firmware)

**`docs/`:**
- Purpose: User and developer documentation
- Contains: Installation guide, architecture overview
- Key files: `INSTALLATION.md`, `ARCHITECTURE.md`

**`scripts/`:**
- Purpose: Utility scripts for setup/development
- Contains: Installation helpers
- Key files: `install.bat`

**`.planning/`:**
- Purpose: GSD command planning documents
- Contains: Codebase analysis documents for AI-assisted development
- Generated: Yes (by `/gsd:map-codebase`)
- Committed: Yes

**`.venv/`:**
- Purpose: Python virtual environment (Python 3.10)
- Contains: Installed packages from requirements.txt
- Generated: Yes
- Committed: No

## Key File Locations

**Entry Points:**
- `src/main.py`: Main application entry point with event loop and visualization
- `src/ui/config_editor.py`: Standalone configuration editor GUI

**Configuration:**
- `config/default_config.json`: Base configuration with all defaults
- `config/user_config.json`: User-specific overrides (layered on top of default)
- `.python-version`: Python version specification (3.10)
- `requirements.txt`: Python package dependencies

**Core Logic:**
- `src/control/tracker_controller.py`: Main orchestrator coordinating all components
- `src/control/control_algorithm.py`: Strategy pattern for P/PID/Velocity controllers
- `src/core/tracking_sample.py`: Atomic measurement data structure
- `src/interfaces/motor_interface.py`: Arduino serial communication with async feedback thread
- `src/tracking/pose_tracker.py`: MediaPipe Pose detection wrapper

**Testing:**
- Not detected (no test suite present; TODO in CLAUDE.md mentions `pytest tests/`)

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `tracker_controller.py`, `pose_tracker.py`)
- Config files: `snake_case.json` (e.g., `default_config.json`, `user_config.json`)
- Arduino sketches: `PascalCase.ino` (e.g., `StepperController.ino`)
- Documentation: `UPPERCASE.md` for root docs (e.g., `README.md`, `CLAUDE.md`), `PascalCase.md` for subdirs (e.g., `INSTALLATION.md`)

**Directories:**
- All lowercase, no separators (e.g., `control`, `interfaces`, `tracking`, `utilities`)

**Variables (Hungarian Notation):**
- `fl` prefix: float (e.g., `flMotorAngleDegrees`, `flProportionalGain`)
- `i` prefix: int (e.g., `iCameraWidthPixels`, `iSampleSequenceNumber`)
- `b` prefix: bool (e.g., `bPersonWasDetected`, `bIsConnected`)
- `s` prefix: string (e.g., `sMotorSerialPortName`, `sConfigFilePath`)
- `ob` prefix: object (e.g., `obFrameImage`, `obPoseTracker`)
- `v` prefix: list/vector (e.g., `vLandmarks`, `vTorsoIndices`)
- `d` prefix: double/high-precision float (e.g., `dSampleTimestampSeconds`, `dFrameTimestamp`)
- `e` prefix: enum (e.g., `eCurrentState`)

**Functions:**
- Pattern: `verb_noun_descriptor` (e.g., `send_move_to_angle_command`, `detect_person_in_frame`, `calculate_correction_from_error`)
- Private methods: Leading underscore (e.g., `_parse_feedback_message`, `_execute_centering_control_algorithm`)

**Classes:**
- PascalCase (e.g., `TrackerController`, `MotorInterface`, `PoseTracker`)

**Config Keys:**
- Hungarian notation matching value type (e.g., `flMotorMaxSpeedStepsPerSecond`, `iCameraDeviceIndex`, `bEnableVisualization`)

## Where to Add New Code

**New Feature:**
- Primary code: Add module to appropriate package (`src/control/`, `src/tracking/`, etc.)
- Tests: `tests/` directory (create if needed, use pytest)
- Config: Add new config keys to `src/utilities/config_manager.py` `SystemConfiguration` dataclass

**New Control Algorithm:**
- Implementation: Add class to `src/control/control_algorithm.py` inheriting from `ControlAlgorithm`
- Registration: Update algorithm selection logic in `src/main.py` `initialize_system()` function
- Config: Add algorithm-specific parameters to `SystemConfiguration` in `config_manager.py`

**New Hardware Interface:**
- Implementation: Create new interface module in `src/interfaces/` (e.g., `servo_interface.py`)
- Integration: Inject into `TrackerController` via dependency injection in `main.py`
- Config: Add hardware-specific settings to `SystemConfiguration`

**Utilities:**
- Shared helpers: `src/utilities/` for cross-cutting concerns
- Visualization helpers: Add to `src/utilities/text_renderer.py` or create new rendering module

**UI Components:**
- Live settings: Extend `src/ui/live_settings_panel.py` with new Dear ImGui controls
- Config editor: Extend `src/ui/config_editor.py` for new config sections

## Special Directories

**`.venv/`:**
- Purpose: Python virtual environment with all dependencies
- Generated: Yes (via `python -m venv .venv`)
- Committed: No (in `.gitignore`)

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes (automatically by Python interpreter)
- Committed: No

**`.planning/codebase/`:**
- Purpose: Codebase analysis documents for GSD commands
- Generated: Yes (by `/gsd:map-codebase`)
- Committed: Yes (used by other GSD commands)

**`.claude/`:**
- Purpose: Claude Code local settings
- Generated: Yes (user-specific configuration)
- Committed: Partially (`.gitignore` excludes sensitive settings)

---

*Structure analysis: 2026-02-15*
