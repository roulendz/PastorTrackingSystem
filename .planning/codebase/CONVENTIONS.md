# Coding Conventions

**Analysis Date:** 2026-02-15

## Naming Patterns

**Files:**
- Snake_case for modules: `motor_interface.py`, `tracker_controller.py`, `config_manager.py`
- Lowercase for packages: `control/`, `interfaces/`, `tracking/`, `utilities/`, `ui/`, `core/`
- Empty `__init__.py` files (no package initialization code)

**Functions:**
- `verb_noun_descriptor` format: `send_move_to_angle_command()`, `detect_person_in_frame()`, `capture_frame_with_timestamp()`, `calculate_correction_from_error()`, `get_latest_motor_state()`
- Private methods prefixed with underscore: `_send_command()`, `_parse_feedback_message()`, `_calculate_person_center()`, `_execute_centering_control_algorithm()`

**Variables - Hungarian Notation (CRITICAL):**
- `fl` = float: `flMotorAngleDegrees`, `flProportionalGain`, `flPersonCenterXPixels`
- `i` = int: `iCameraWidthPixels`, `iSampleSequenceNumber`, `iFramesProcessedCount`
- `b` = bool: `bPersonWasDetected`, `bIsConnected`, `bEnableVisualization`
- `s` = string: `sMotorSerialPortName`, `sConfigFilePath`, `sCommand`
- `ob` = object: `obFrameImage`, `obMotorInterface`, `obCameraInterface`, `obPoseTracker`
- `v` = list/vector: `vLandmarks`, `vTorsoIndices`, `vInfoLines`, `vPaths`
- `d` = double/high-precision float/timestamp: `dSampleTimestampSeconds`, `dFrameTimestamp`, `dCurrentTime`
- `e` = enum: `eCurrentState` (TrackerState enum)
- `fn` = function/callback: `fnCallback`, `fnReset`

**Classes:**
- PascalCase: `MotorInterface`, `TrackerController`, `PoseTracker`, `SystemConfiguration`, `TrackingSample`
- Suffixes indicate purpose: `Interface` for hardware abstraction, `Controller` for orchestration, `Tracker` for detection, `Manager` for configuration

**Types:**
- Dataclasses for data containers: `TrackingSample`, `MotorState`, `PoseResult`, `SystemConfiguration`
- Enums for state: `TrackerState` with values `IDLE`, `TRACKING`, `ERROR`

**Config Keys:**
- Hungarian prefixes match value types: `flControlProportionalGain`, `iCameraDeviceIndex`, `bEnableVisualization`, `sMotorSerialPortName`

## Code Style

**Formatting:**
- No automated formatter detected (no `.prettierrc`, `.black`, `.yapf`)
- 4-space indentation (standard Python)
- Line length: ~100-120 characters (observed in practice)
- Blank line before class methods
- Two blank lines between top-level functions/classes

**Linting:**
- No linter config found (no `.pylintrc`, `.flake8`, `pyproject.toml` with linting)
- Code follows PEP 8 conventions organically

**Imports:**
- Standard library first, then third-party, then local modules
- Absolute imports within `src/`: `from interfaces.motor_interface import MotorInterface`
- Path manipulation for src in `main.py`: `sys.path.insert(0, str(Path(__file__).parent))`

**String Formatting:**
- f-strings preferred: `f"Motor state updated: {flCurrentAngle:.2f}° (seq {iSequence})"`
- Format specifiers for precision: `{flValue:.2f}`, `{iValue:.1f}`, `{sText.strip()}`

## Import Organization

**Order:**
1. Standard library (alphabetical): `import argparse`, `import cv2`, `import json`, `import logging`, `import time`
2. Third-party (alphabetical): `import mediapipe as mp`, `import numpy as np`, `import serial`, `import dearpygui.dearpygui as dpg`
3. Local modules (grouped by package):
   ```python
   from interfaces.motor_interface import MotorInterface
   from interfaces.camera_interface import CameraInterface
   from tracking.pose_tracker import PoseTracker
   from control.control_algorithm import ControlAlgorithm
   from utilities.config_manager import ConfigurationManager
   ```

**Path Aliases:**
- None used (relative imports from `src/` root)

## Error Handling

**Patterns:**
- Try-except with logging at boundaries (I/O operations, external library calls)
- Guard clauses for invalid state: `if not self.is_connected_to_motor_controller(): logger.error(...); return False`
- Silent failure with logging: catch broad `Exception`, log error, return default/None
- No custom exception classes defined

**Examples:**
```python
# Serial communication failure
try:
    self.obSerial.write(sCommand.encode('utf-8'))
    logger.debug(f"Sent command: {sCommand.strip()}")
    return True
except Exception as e:
    logger.error(f"Failed to send command: {e}")
    return False

# Frame capture failure
if not bSuccess or obFrame is None:
    logger.warning("Failed to capture frame")
    return None, dTimestampSeconds
```

**Validation:**
- Boolean return values for success/failure
- None returns for missing data
- Default values in constructors prevent invalid states

## Logging

**Framework:** Python `logging` module (standard library)

**Configuration:**
```python
# In main.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
```

**Patterns:**
- Module-level logger: `logger = logging.getLogger(__name__)`
- Levels used:
  - `logger.info()`: System events (initialization, state changes, connection status)
  - `logger.debug()`: Detailed operation data (motor commands, PID terms, frame capture)
  - `logger.warning()`: Non-critical failures (frame capture miss, missing config)
  - `logger.error()`: Failures requiring attention (connection failure, invalid state, exceptions)
- Include context in messages: `f"Connected to motor on {sPort}"`, `f"PID gains updated: Kp={flProportionalGain}, Ki={flIntegralGain}, Kd={flDerivativeGain}"`
- Use `exc_info=True` for stack traces: `logger.error(f"Error in main loop: {e}", exc_info=True)`

## Comments

**When to Comment:**
- Module docstrings with purpose and architectural notes (SRP compliance noted in several modules)
- Class docstrings with responsibilities list
- Function docstrings with Args/Returns (Google style)
- Inline comments for non-obvious logic (e.g., Arduino timestamp rollover handling)
- Rationale for magic numbers: `time.sleep(2.0)  # Allow Arduino to reset`

**Docstring Style (Google format):**
```python
def send_move_to_angle_command(self, flTargetAngleDegrees: float) -> bool:
    """
    Command motor to move to absolute angle.

    Args:
        flTargetAngleDegrees: Target angle in degrees

    Returns:
        True if command sent successfully
    """
```

**File-level docstrings:**
```python
"""
motor_interface.py - Serial communication with Arduino motor controller

This module handles all communication with the Arduino stepper controller.

Follows:
- SRP: Only handles motor communication, nothing else
- Thread-safe state storage
- Async feedback reception
"""
```

## Function Design

**Size:**
- Most functions under 50 lines
- Main loop functions can be longer (100+ lines) when handling sequential steps
- Extraction to private helpers when logic is reusable: `_calculate_person_center()`, `_parse_feedback_message()`

**Parameters:**
- Type hints on all public methods
- Hungarian notation in parameter names: `flTargetAngleDegrees: float`, `iImageWidthPixels: int`
- Default parameters for optional config: `flMinimumDetectionConfidence: float = 0.5`

**Return Values:**
- Boolean for success/failure operations
- None for missing/failed data acquisition
- Typed returns (dataclass instances): `-> MotorState`, `-> PoseResult`, `-> Optional[TrackingSample]`
- Tuples for multi-value returns: `-> Tuple[Optional[np.ndarray], float]`

## Module Design

**Exports:**
- No explicit `__all__` definitions
- Public API is anything not prefixed with underscore
- Dataclasses and main classes exported from modules

**Barrel Files:**
- Not used (empty `__init__.py` files)
- Import directly from modules: `from interfaces.motor_interface import MotorInterface`

**Architecture Patterns:**
- Single Responsibility Principle explicitly documented in module docstrings
- Dependency injection: `TrackerController` receives all dependencies in `__init__`
- Strategy pattern: `ControlAlgorithm` ABC with `ProportionalController`, `PIDController`, `VelocityController` implementations
- Thread safety: Lock-protected shared state in `MotorInterface._obStateLock`
- Null Object pattern: `NullMotorInterface` for hardware-less operation

## Special Patterns

**Dataclasses:**
- Used for data containers with type safety: `@dataclass` decorator
- Immutable-by-design (no setters, state copied on access): `get_latest_motor_state()` returns copy
- Optional fields with defaults: `obFrameImage: Optional[np.ndarray] = None`

**Threading:**
- Daemon threads for background tasks: `threading.Thread(target=self._feedback_reception_loop, daemon=True)`
- Lock-protected state updates: `with self._obStateLock:`
- Graceful shutdown: `_bFeedbackThreadRunning` flag checked in loop

**Type Conversion:**
- Explicit float/int casting for config values: `float(getattr(obConfig, 'flFieldOfViewDegrees', 0.0))`
- Defensive casting in calculations: `float(dDeltaTime)`, `int(iWidth)`

**Configuration:**
- Singleton pattern: `ConfigurationManager` manages global config
- Layered loading: `default_config.json` + `user_config.json` overlay
- `getattr()` with defaults for safe access: `getattr(obConfig, 'bEnableDeadzoneOverlay', True)`

---

*Convention analysis: 2026-02-15*
