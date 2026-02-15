# Testing Patterns

**Analysis Date:** 2026-02-15

## Test Framework

**Current Status:** No test suite exists

**Planned Framework:**
- pytest (mentioned in CLAUDE.md as TODO: `pytest tests/` is a TODO)
- No test files found in repository
- No test configuration files (no `pytest.ini`, `setup.cfg`, `pyproject.toml` with pytest config)

**Dependencies Available:**
```txt
opencv-python==4.8.1.78
mediapipe==0.10.8
pyserial==3.5
numpy==1.24.3
dataclasses-json==0.6.3
dearpygui==1.11.1
```

**Suggested Installation:**
```bash
pip install pytest pytest-cov pytest-mock
```

**Run Commands (when implemented):**
```bash
pytest tests/              # Run all tests
pytest tests/ -v           # Verbose mode
pytest tests/ --cov=src    # With coverage
pytest tests/ -k "motor"   # Run specific test subset
```

## Test File Organization

**Recommended Location:**
- Separate `tests/` directory at project root (not co-located)
- Mirror source structure:
  ```
  tests/
  ├── test_interfaces/
  │   ├── test_motor_interface.py
  │   └── test_camera_interface.py
  ├── test_tracking/
  │   └── test_pose_tracker.py
  ├── test_control/
  │   ├── test_control_algorithm.py
  │   └── test_tracker_controller.py
  └── test_utilities/
      └── test_config_manager.py
  ```

**Naming:**
- `test_*.py` for test files (pytest convention)
- Match module names: `motor_interface.py` → `test_motor_interface.py`

## Test Structure

**Recommended Suite Organization:**
```python
import pytest
from interfaces.motor_interface import MotorInterface, MotorState

class TestMotorInterface:
    """Test suite for MotorInterface serial communication."""

    def test_connect_to_motor_controller_success(self, mocker):
        """Test successful connection to motor on specified port."""
        # Arrange
        mock_serial = mocker.patch('serial.Serial')
        obMotorInterface = MotorInterface('/dev/ttyUSB0', 115200)

        # Act
        bResult = obMotorInterface.connect_to_motor_controller()

        # Assert
        assert bResult is True
        assert obMotorInterface.is_connected_to_motor_controller()

    def test_send_move_to_angle_command_when_disconnected(self):
        """Test move command fails gracefully when not connected."""
        # Arrange
        obMotorInterface = MotorInterface('/dev/ttyUSB0', 115200)

        # Act
        bResult = obMotorInterface.send_move_to_angle_command(45.0)

        # Assert
        assert bResult is False
```

**Patterns:**
- Arrange-Act-Assert structure
- Descriptive test names: `test_<method>_<condition>` format
- Docstrings on tests explaining intent
- Hungarian notation in test variables matching source convention

## Mocking

**Framework:** pytest-mock (wraps unittest.mock)

**Recommended Patterns:**

**External Hardware (Serial, Camera):**
```python
def test_motor_connection_fallback(self, mocker):
    """Test motor connects to first available port on fallback."""
    # Mock serial to fail on primary, succeed on fallback
    mock_serial = mocker.patch('serial.Serial')
    mock_serial.side_effect = [
        serial.SerialException("Port not found"),
        mocker.Mock(is_open=True)
    ]
    mock_list_ports = mocker.patch('serial.tools.list_ports.comports')
    mock_list_ports.return_value = [mocker.Mock(device='/dev/ttyUSB1')]

    obMotorInterface = MotorInterface('/dev/ttyUSB0', 115200)
    bResult = obMotorInterface.connect_to_motor_controller()

    assert bResult is True
    assert obMotorInterface.sSerialPortName == '/dev/ttyUSB1'
```

**MediaPipe Pose Detection:**
```python
def test_detect_person_in_frame_no_detection(self, mocker):
    """Test pose detection returns empty result when no person found."""
    # Mock MediaPipe to return no landmarks
    mock_pose = mocker.patch('mediapipe.solutions.pose.Pose')
    mock_pose.return_value.process.return_value.pose_landmarks = None

    obPoseTracker = PoseTracker()
    obFrame = np.zeros((480, 640, 3), dtype=np.uint8)
    obResult = obPoseTracker.detect_person_in_frame(obFrame)

    assert obResult.bPersonWasDetected is False
    assert obResult.flPersonConfidenceScore == 0.0
```

**Time-Dependent Code:**
```python
def test_pid_controller_derivative_term(self, mocker):
    """Test PID derivative term calculation over time."""
    mock_time = mocker.patch('time.time')
    mock_time.side_effect = [0.0, 0.1, 0.2]  # Three calls

    obController = PIDController(1.0, 0.0, 0.5)
    obController.calculate_correction_from_error(10.0)  # t=0.0
    flCorrection = obController.calculate_correction_from_error(5.0)  # t=0.1

    # Derivative should react to error change: (5.0 - 10.0) / 0.1 = -50
    # Expected: Kp*5 + Kd*(-50) = 5.0 + 0.5*(-50) = -20.0
    assert abs(flCorrection - (-20.0)) < 0.1
```

**What to Mock:**
- Serial port communication (`serial.Serial`, `serial.tools.list_ports`)
- Camera capture (`cv2.VideoCapture`)
- MediaPipe pose detection (`mediapipe.solutions.pose.Pose`)
- Time functions (`time.time()`, `time.perf_counter()`)
- File I/O for configuration (`open()`, `Path.exists()`)

**What NOT to Mock:**
- Pure logic (control algorithms, calculations)
- Dataclass constructors (`TrackingSample`, `MotorState`)
- Math operations (numpy, standard operators)
- Configuration dataclasses (`SystemConfiguration`)

## Fixtures and Factories

**Recommended Test Data:**
```python
# conftest.py
import pytest
import numpy as np
from core.tracking_sample import TrackingSample
from interfaces.motor_interface import MotorState

@pytest.fixture
def valid_camera_frame():
    """Provide a valid BGR camera frame for testing."""
    return np.zeros((720, 1280, 3), dtype=np.uint8)

@pytest.fixture
def sample_motor_state():
    """Provide a sample motor state for testing."""
    return MotorState(
        flMotorAngleDegrees=15.0,
        flMotorTargetAngleDegrees=20.0,
        flMotorSpeedStepsPerSecond=5000.0,
        bMotorIsMoving=True,
        dMotorTimestampSeconds=1234.5,
        iMotorSequenceNumber=42
    )

@pytest.fixture
def tracking_sample_with_detection():
    """Provide a tracking sample with person detected."""
    return TrackingSample(
        dSampleTimestampSeconds=1234.5,
        flMotorAngleDegrees=15.0,
        flPersonCenterXPixels=640.0,
        flPersonCenterYPixels=360.0,
        bPersonWasDetected=True,
        iSampleSequenceNumber=1,
        flPersonConfidenceScore=0.85,
        flMinimumConfidenceRequired=0.5
    )

@pytest.fixture
def mock_config():
    """Provide a default system configuration for testing."""
    from utilities.config_manager import SystemConfiguration
    return SystemConfiguration(
        iCameraWidthPixels=1280,
        iCameraHeightPixels=720,
        flFieldOfViewDegrees=60.0,
        flControlProportionalGain=1.0
    )
```

**Location:**
- `tests/conftest.py` for shared fixtures
- `tests/<package>/conftest.py` for package-specific fixtures

## Coverage

**Requirements:** None enforced (no test suite exists)

**Recommended Targets:**
- Overall: 70%+ (achievable without hardware integration tests)
- Core logic: 90%+ (`control_algorithm.py`, `tracker_controller.py`, `tracking_sample.py`)
- Interfaces: 50%+ (hardware-dependent, harder to test fully)

**View Coverage:**
```bash
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html  # View in browser
```

**Critical Coverage Areas:**
- Control algorithm calculations (`calculate_correction_from_error()`)
- Configuration loading/merging (`ConfigurationManager`)
- TrackingSample validation (`is_valid_for_tracking()`)
- Error angle calculations (`get_pixel_offset_from_center()`)
- State machine transitions (`TrackerState` handling)

## Test Types

**Unit Tests:**
- Scope: Individual functions/methods in isolation
- Target: Pure logic without I/O
- Examples:
  - `ProportionalController.calculate_correction_from_error()`
  - `TrackingSample.get_pixel_offset_from_center()`
  - `ConfigurationManager.validate_configuration()`
  - `PoseTracker._calculate_person_center()`

**Integration Tests:**
- Scope: Component interaction without hardware
- Target: Orchestration logic with mocked interfaces
- Examples:
  - `TrackerController` coordinating camera + motor + pose (all mocked)
  - Configuration loading with real JSON files
  - Control algorithm integration with tracker controller

**Hardware Integration Tests:**
- Not recommended for CI/CD (require physical hardware)
- Scope: Full system with real Arduino and camera
- Run manually during development
- Consider marking with `@pytest.mark.hardware` and skip by default

## Common Patterns

**Async Testing (Threading):**
```python
def test_motor_feedback_thread_updates_state(self, mocker):
    """Test background thread updates motor state from serial."""
    import time
    mock_serial = mocker.Mock()
    mock_serial.in_waiting = 1
    mock_serial.readline.return_value = b'FB:15.5,20.0,5000.0,1,1000000,1,0\n'

    mocker.patch('serial.Serial', return_value=mock_serial)

    obMotorInterface = MotorInterface('/dev/ttyUSB0', 115200)
    obMotorInterface.connect_to_motor_controller()

    time.sleep(0.1)  # Allow thread to process

    obState = obMotorInterface.get_latest_motor_state()
    assert abs(obState.flMotorAngleDegrees - 15.5) < 0.01
```

**Error Testing:**
```python
def test_camera_capture_handles_disconnection_gracefully(self, mocker):
    """Test camera capture returns None when device disconnects."""
    mock_capture = mocker.patch('cv2.VideoCapture')
    mock_capture.return_value.read.return_value = (False, None)

    obCameraInterface = CameraInterface()
    obCameraInterface.open_camera_device()

    obFrame, dTimestamp = obCameraInterface.capture_frame_with_timestamp()

    assert obFrame is None
    assert dTimestamp > 0  # Timestamp still captured
```

**Parametrized Tests:**
```python
@pytest.mark.parametrize("flError,flKp,flExpected", [
    (10.0, 1.0, 10.0),    # Positive error
    (-5.0, 1.0, -5.0),    # Negative error
    (10.0, 2.0, 20.0),    # Different gain
    (0.0, 1.0, 0.0),      # Zero error
])
def test_p_controller_correction(flError, flKp, flExpected):
    """Test proportional controller with various error values."""
    obController = ProportionalController(flKp)
    flCorrection = obController.calculate_correction_from_error(flError)
    assert abs(flCorrection - flExpected) < 0.001
```

## Test Priority (Recommended Implementation Order)

**Phase 1 - Pure Logic (No Mocking):**
1. `test_control_algorithm.py` - P/PID/Velocity controllers
2. `test_tracking_sample.py` - Data container methods
3. `test_config_manager.py` - Configuration validation

**Phase 2 - Mocked I/O:**
4. `test_motor_interface.py` - Serial communication (mocked)
5. `test_camera_interface.py` - OpenCV capture (mocked)
6. `test_pose_tracker.py` - MediaPipe detection (mocked)

**Phase 3 - Integration:**
7. `test_tracker_controller.py` - Orchestration with mocked dependencies
8. Integration tests for full tracking loop

---

*Testing analysis: 2026-02-15*

**Note:** This document describes the recommended testing approach for a codebase that currently has no tests. All patterns are based on the existing code structure and Python/pytest best practices. Implement tests incrementally starting with pure logic (control algorithms) before tackling hardware-dependent components.
