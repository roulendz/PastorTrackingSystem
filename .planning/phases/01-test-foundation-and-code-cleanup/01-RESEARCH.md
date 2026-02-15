# Phase 1: Test Foundation and Code Cleanup - Research

**Researched:** 2026-02-15
**Domain:** Python test infrastructure, motor physics simulation, synthetic video, code quality
**Confidence:** HIGH

## Summary

Phase 1 transforms the Pastor Tracking System from hardware-dependent to fully testable by building three capabilities: (1) a synthetic video source that feeds pre-recorded or generated video into the existing CameraInterface, (2) a physics-based NullMotorInterface that simulates realistic stepper motor dynamics instead of instant teleportation, and (3) a clean codebase with no dead code or silent exception swallowing.

The existing codebase is well-structured for this work. The dependency injection pattern in TrackerController already accepts motor, camera, pose tracker, and control algorithm as constructor parameters. The NullMotorInterface already exists but needs physics simulation. The CameraInterface already uses `cv2.VideoCapture` which natively supports video files -- the constructor just needs to accept a file path instead of only a device index.

**Primary recommendation:** Extend the existing interfaces (not replace them) to support testability. CameraInterface should accept a string path to a video file. NullMotorInterface should be upgraded to simulate AccelStepper-like trapezoidal motion profiles with configurable acceleration, max velocity, and position ramping.

## Standard Stack

### Core (already installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | latest (8.x) | Test framework | De facto standard for Python testing; fixture-based DI maps directly to this project's DI pattern |
| opencv-python | 4.8.1.78 | Video capture from files | Already installed; `cv2.VideoCapture` accepts file paths natively |
| numpy | 1.24.3 | Synthetic frame generation, motor physics math | Already installed |

### Supporting (new)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 8.x | Test runner and fixture framework | All tests in this phase |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pytest | unittest | pytest fixtures are far better suited for the DI pattern already used in this codebase |
| Custom motor sim | steppyr library | steppyr targets real hardware GPIO; we need a pure-math simulation with no hardware dependencies |

**Installation:**
```bash
pip install pytest
```

No other new dependencies needed. OpenCV, numpy, and all other libraries are already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
src/
  interfaces/
    camera_interface.py          # Extended: accept video file path OR device index
    motor_interface.py           # Extended: NullMotorInterface upgraded to SimulatedMotorInterface
  control/
    control_algorithm.py         # Existing (no changes in this phase)
    tracker_controller.py        # Existing (no changes in this phase)
  core/
    tracking_sample.py           # Existing
  tracking/
    pose_tracker.py              # Existing
  utilities/
    config_manager.py            # Extended: FOV default set to 6.8 degrees
    text_renderer.py             # Existing
  ui/
    live_settings_panel.py       # Existing
    config_editor.py             # Existing
  main.py                       # Extended: --video CLI arg, synthetic mode

tests/
  conftest.py                   # Shared fixtures: simulated motor, synthetic camera, config
  test_camera_interface.py      # CameraInterface accepts video files
  test_motor_simulation.py      # SimulatedMotorInterface physics verification
  test_pipeline_no_hardware.py  # Full pipeline runs without hardware
  test_fov_calculation.py       # FOV = 6.8 degrees produces correct pixel-to-degree mapping
  test_exception_handling.py    # No silent exception swallowing in critical paths
```

### Pattern 1: Extend CameraInterface to Accept Video Files
**What:** OpenCV's `cv2.VideoCapture` already accepts both integer device indices and string file paths. The CameraInterface constructor currently only accepts an integer `iCameraDeviceIndex`. Extend it to also accept a string `sVideoFilePath` parameter.
**When to use:** When running without a camera (testing, development, CI).
**Example:**
```python
# Source: OpenCV documentation - cv2.VideoCapture accepts int or str
class CameraInterface:
    def __init__(
        self,
        iCameraDeviceIndex: int = 0,
        iCameraWidthPixels: int = 1280,
        iCameraHeightPixels: int = 720,
        iCameraFramesPerSecond: int = 30,
        sVideoFilePath: str = ""  # NEW: if non-empty, use file instead of device
    ):
        self.sVideoFilePath = sVideoFilePath
        # ... rest unchanged

    def open_camera_device(self) -> bool:
        if self.sVideoFilePath:
            self.obVideoCapture = cv2.VideoCapture(self.sVideoFilePath)
        else:
            self.obVideoCapture = cv2.VideoCapture(self.iCameraDeviceIndex)
        # ... rest of validation unchanged
```

### Pattern 2: SimulatedMotorInterface with Trapezoidal Physics
**What:** Replace instant-teleportation in NullMotorInterface with a physics simulation that models AccelStepper's trapezoidal velocity profile: accelerate at configured rate, cruise at max speed, decelerate to stop at target.
**When to use:** All testing that needs realistic motor behavior.
**Example:**
```python
class SimulatedMotorInterface:
    """
    Motor simulation with realistic trapezoidal motion profile.
    Replaces NullMotorInterface for testing.

    Physics model:
    - Accelerates at flAccelerationDegreesPerSecondSquared toward target
    - Cruises at flMaxSpeedDegreesPerSecond
    - Decelerates to stop at target position
    - Reports position based on physics, not commanded target
    """

    def __init__(self):
        self._flCurrentAngleDegrees = 0.0
        self._flTargetAngleDegrees = 0.0
        self._flCurrentVelocityDegreesPerSecond = 0.0
        self._flMaxSpeedDegreesPerSecond = 30.0
        self._flAccelerationDegreesPerSecondSquared = 60.0
        self._dLastUpdateTimestampSeconds = time.perf_counter()
        self._obStateLock = threading.Lock()
        self._obMotorStateHistory = deque(maxlen=100)

    def advance_simulation(self, dDeltaTimeSeconds: float):
        """
        Step the motor physics forward.
        Called either by a background thread or explicitly in tests.

        Uses trapezoidal velocity profile:
        1. Compute distance to target
        2. Compute stopping distance at current velocity
        3. If stopping distance >= remaining distance: decelerate
        4. Else if below max speed: accelerate
        5. Else: cruise at max speed
        """
        with self._obStateLock:
            flDistanceToTarget = self._flTargetAngleDegrees - self._flCurrentAngleDegrees
            flDirection = 1.0 if flDistanceToTarget > 0 else -1.0
            flAbsDistance = abs(flDistanceToTarget)

            # Stopping distance: v^2 / (2*a)
            flStoppingDistance = (self._flCurrentVelocityDegreesPerSecond ** 2) / \
                                (2.0 * self._flAccelerationDegreesPerSecondSquared)

            flAbsVelocity = abs(self._flCurrentVelocityDegreesPerSecond)

            if flAbsDistance < 0.001 and flAbsVelocity < 0.01:
                # At target, stop
                self._flCurrentVelocityDegreesPerSecond = 0.0
                self._flCurrentAngleDegrees = self._flTargetAngleDegrees
            elif flStoppingDistance >= flAbsDistance:
                # Decelerate
                flDecel = self._flAccelerationDegreesPerSecondSquared * dDeltaTimeSeconds
                if flAbsVelocity <= flDecel:
                    self._flCurrentVelocityDegreesPerSecond = 0.0
                else:
                    flSign = 1.0 if self._flCurrentVelocityDegreesPerSecond > 0 else -1.0
                    self._flCurrentVelocityDegreesPerSecond -= flSign * flDecel
            elif flAbsVelocity < self._flMaxSpeedDegreesPerSecond:
                # Accelerate
                self._flCurrentVelocityDegreesPerSecond += \
                    flDirection * self._flAccelerationDegreesPerSecondSquared * dDeltaTimeSeconds
                # Clamp to max speed
                if abs(self._flCurrentVelocityDegreesPerSecond) > self._flMaxSpeedDegreesPerSecond:
                    self._flCurrentVelocityDegreesPerSecond = \
                        flDirection * self._flMaxSpeedDegreesPerSecond
            # else: cruise at current velocity

            # Update position
            self._flCurrentAngleDegrees += \
                self._flCurrentVelocityDegreesPerSecond * dDeltaTimeSeconds
```

### Pattern 3: Pytest Fixtures for Dependency Injection
**What:** Use pytest fixtures in conftest.py to provide pre-configured test components that mirror the production DI pattern.
**When to use:** All tests.
**Example:**
```python
# tests/conftest.py
import pytest
from interfaces.motor_interface import MotorState
from interfaces.camera_interface import CameraInterface
from utilities.config_manager import ConfigurationManager

@pytest.fixture
def obSimulatedMotor():
    """Simulated motor with realistic physics."""
    from interfaces.motor_interface import SimulatedMotorInterface
    return SimulatedMotorInterface()

@pytest.fixture
def obSyntheticCamera(tmp_path):
    """Camera interface reading from a synthetic test video."""
    # Generate a simple test video with a moving dot
    sVideoPath = str(tmp_path / "test_video.mp4")
    _generate_test_video(sVideoPath, iFrameCount=90, iWidth=1280, iHeight=720)
    obCamera = CameraInterface(sVideoFilePath=sVideoPath)
    obCamera.open_camera_device()
    yield obCamera
    obCamera.close_camera_device()

@pytest.fixture
def obTestConfig():
    """Configuration with known FOV for Sony AX700."""
    obManager = ConfigurationManager()
    obConfig = obManager.get_system_configuration()
    obConfig.flFieldOfViewDegrees = 6.77
    obConfig.bAllowStartWithoutMotor = True
    return obConfig
```

### Anti-Patterns to Avoid
- **Creating a separate SyntheticCameraInterface class:** OpenCV's VideoCapture already handles files. Do not duplicate the class; extend the existing one with an optional file path parameter.
- **Running a background thread in SimulatedMotorInterface during tests:** For deterministic tests, call `advance_simulation()` explicitly with known time deltas. Only use a background thread for the interactive `python main.py --video` mode.
- **Testing with `time.sleep()` for timing:** Tests must be fast and deterministic. Pass explicit time deltas, never sleep.
- **Bare `except Exception: pass` in new code:** This phase explicitly bans this pattern (CODE-02).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Video file reading | Custom frame reader | `cv2.VideoCapture(filepath)` | OpenCV handles codecs, frame seeking, loop detection natively |
| Test framework | Custom test runner | pytest | Industry standard, fixture system matches existing DI pattern |
| Synthetic video generation | Frame-by-frame file I/O | `cv2.VideoWriter` | Handles codec selection, frame rate, proper container format |
| Motor physics | Discrete step simulation | Continuous trapezoidal profile math | AccelStepper uses continuous math internally; step-level simulation is unnecessary overhead |
| Dead code detection | Manual inspection | Python AST analysis + grep | Use `vulture` or manual grep; AST tools catch unreachable code that visual inspection misses |

**Key insight:** This phase is about extending existing interfaces, not replacing them. The DI architecture already supports swappable implementations -- just provide better implementations.

## Common Pitfalls

### Pitfall 1: NullMotorInterface Instant Teleportation Hides Bugs
**What goes wrong:** The current NullMotorInterface sets `flMotorAngleDegrees = flTargetAngleDegrees` instantly. Tests pass because there's no lag between command and position. In production, the motor takes time to reach the target. This mismatch means control algorithms appear to work in tests but produce overshoot/oscillation with real hardware.
**Why it happens:** NullMotorInterface was designed for "run without hardware" convenience, not for realistic testing.
**How to avoid:** Replace with SimulatedMotorInterface that models acceleration, cruising, and deceleration. The simulation must take time to reach the target, just like real hardware.
**Warning signs:** Tests pass but tracking jitters on real hardware. Control gains tuned in simulation do not transfer to production.

### Pitfall 2: Video File vs Camera Behavioral Differences
**What goes wrong:** `cv2.VideoCapture` behaves differently with files vs cameras:
- Files: `read()` returns frames as fast as the CPU can decode them (no 33ms wait)
- Files: When the video ends, `read()` returns `(False, None)` -- no automatic looping
- Files: Some codecs do not support `CAP_PROP_POS_FRAMES` seek accurately
- Camera: `read()` blocks until the next frame arrives (natural frame rate limiting)
**Why it happens:** OpenCV's VideoCapture is a generic abstraction over very different sources.
**How to avoid:** When using a video file:
1. Loop the video: when `read()` returns False, call `cap.set(cv2.CAP_PROP_POS_FRAMES, 0)` to restart
2. For frame-rate limiting in interactive mode, add `time.sleep()` or use `cv2.waitKey(33)` to pace playback
3. Use MP4 with H.264 codec for reliable seeking
4. For tests, do NOT add sleep -- read as fast as possible for speed
**Warning signs:** Test video plays instantly in 0.1 seconds instead of 3 seconds. Video "freezes" at end instead of looping.

### Pitfall 3: Silent Exception Swallowing Is Load-Bearing
**What goes wrong:** The codebase has ~25 instances of `except Exception: pass`. Some of these are genuinely load-bearing -- they prevent crashes when optional UI components (DearPyGui) are not available or when config keys are missing. Blindly replacing all with `raise` will break the application.
**Why it happens:** During development, `except: pass` is the quickest way to handle "I don't care if this fails." Over time, some of these become critical error hiding (e.g., the motor timestamp parsing at motor_interface.py line 345).
**How to avoid:** Categorize each `except Exception: pass` into three buckets:
1. **Genuinely acceptable:** UI panel updates that may fail when panel is not open (live_settings_panel.py lines 108, 192, etc.) -- keep but add logging
2. **Must be specific:** Motor timestamp parsing (motor_interface.py line 345) -- catch specific ValueError/TypeError, log with traceback
3. **Must not swallow:** Control path errors (tracker_controller.py lines 109, 264, 269) -- catch specific exceptions, log with traceback
**Warning signs:** Application crashes after "fixing" exception handling. The fix: change to specific exception types (ValueError, AttributeError, etc.) instead of bare re-raise.

### Pitfall 4: FOV Calculation Using Wrong Sensor Dimensions
**What goes wrong:** The Sony AX700 is marketed as having a "1-inch sensor" but the actual active area is smaller than the nominal 1-inch (which is 13.2mm x 8.8mm). Using the wrong sensor dimensions produces an incorrect FOV calculation.
**Why it happens:** "1-inch sensor" is a legacy tube-era designation. The actual imaging area varies by manufacturer.
**How to avoid:** Use the 35mm-equivalent focal length to back-calculate. Sony specs state:
- Actual focal length: 9.3-111.6mm
- 35mm equivalent: 29-348mm (16:9)
- At max optical zoom (111.6mm actual, 348mm equiv):
  - 1-inch sensor horizontal active area: ~13.2mm (Sony Exmor RS)
  - FOV = 2 * arctan(13.2 / (2 * 111.6)) = 2 * arctan(0.0591) = **~6.77 degrees**
- This confirms the ~6.8 degree target in the requirements
**Warning signs:** Pixel-to-degree conversion produces angles that do not match physical measurements.

### Pitfall 5: Dead Code Removal Breaks Import Chains
**What goes wrong:** Removing a module or function that appears unused but is imported dynamically (via `importlib`, `getattr`, or string-based config values like `sControlAlgorithmType`).
**Why it happens:** Python's dynamic nature means static analysis cannot catch all usages. The `VelocityController` import in main.py (line 121-122) is conditional on config value, and would not be caught by a naive dead code scanner.
**How to avoid:**
1. Before removing any code, grep the entire codebase for the class/function name as a string literal
2. Check config files for references (e.g., `sControlAlgorithmType: "Velocity"` references `VelocityController`)
3. Run the application in all modes after removal to verify nothing breaks
4. Use `vulture` for initial detection, but verify each finding manually
**Warning signs:** Application works with default config but crashes when user changes control algorithm to "Velocity" via the settings panel.

## Code Examples

### Generating a Synthetic Test Video with Moving Person Marker
```python
# Source: OpenCV VideoWriter documentation
import cv2
import numpy as np

def generate_test_video_with_moving_marker(
    sOutputPath: str,
    iFrameCount: int = 90,    # 3 seconds at 30fps
    iWidth: int = 1280,
    iHeight: int = 720,
    iFramesPerSecond: int = 30,
    flPersonStartXNormalized: float = 0.5,  # Start at center
    flPersonEndXNormalized: float = 0.8,    # Walk right
    flPersonYNormalized: float = 0.4        # Upper-body level
):
    """
    Generate a test video with a colored circle moving linearly.
    The circle simulates a person center point that MediaPipe would detect.
    """
    obFourCC = cv2.VideoWriter_fourcc(*'mp4v')
    obWriter = cv2.VideoWriter(sOutputPath, obFourCC, iFramesPerSecond, (iWidth, iHeight))

    for iFrame in range(iFrameCount):
        # Black background
        obFrame = np.zeros((iHeight, iWidth, 3), dtype=np.uint8)

        # Interpolate person position
        flProgress = float(iFrame) / float(max(1, iFrameCount - 1))
        flX = flPersonStartXNormalized + (flPersonEndXNormalized - flPersonStartXNormalized) * flProgress
        iPersonX = int(flX * iWidth)
        iPersonY = int(flPersonYNormalized * iHeight)

        # Draw a colored circle as the "person"
        cv2.circle(obFrame, (iPersonX, iPersonY), 30, (0, 0, 255), -1)

        # Draw shoulder-like markers for MediaPipe detection
        cv2.circle(obFrame, (iPersonX - 40, iPersonY), 10, (0, 255, 0), -1)  # Left shoulder
        cv2.circle(obFrame, (iPersonX + 40, iPersonY), 10, (0, 255, 0), -1)  # Right shoulder

        obWriter.write(obFrame)

    obWriter.release()
```

### Looping a Video File in CameraInterface
```python
# Source: OpenCV documentation, CAP_PROP_POS_FRAMES
def capture_frame_with_timestamp(self) -> Tuple[Optional[np.ndarray], float]:
    if not self.is_camera_device_open():
        return None, 0.0

    bSuccess, obFrame = self.obVideoCapture.read()
    dTimestampSeconds = time.perf_counter()

    # Loop video file when it reaches the end
    if not bSuccess and self.sVideoFilePath:
        self.obVideoCapture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        bSuccess, obFrame = self.obVideoCapture.read()
        dTimestampSeconds = time.perf_counter()

    if not bSuccess or obFrame is None:
        return None, dTimestampSeconds

    return obFrame, dTimestampSeconds
```

### FOV Validation Test
```python
# Source: Optics formula - FOV = 2 * arctan(sensor_width / (2 * focal_length))
import math

def test_sony_ax700_fov_at_max_optical_zoom():
    """
    Verify FOV calculation for Sony AX700 at maximum optical zoom.

    Specs:
    - 1-inch Exmor RS CMOS sensor: ~13.2mm horizontal active area
    - Max optical zoom focal length: 111.6mm actual
    - 35mm equivalent at max zoom: 348mm
    - Expected horizontal FOV: ~6.8 degrees
    """
    flSensorWidthMm = 13.2    # 1-inch sensor horizontal
    flFocalLengthMm = 111.6   # Max optical zoom

    flFovRadians = 2.0 * math.atan(flSensorWidthMm / (2.0 * flFocalLengthMm))
    flFovDegrees = math.degrees(flFovRadians)

    # Should be approximately 6.77 degrees
    assert 6.5 <= flFovDegrees <= 7.0, f"FOV {flFovDegrees:.2f} outside expected range"

    # Verify pixel-to-degree conversion
    iImageWidthPixels = 1280
    flAnglePerPixel = flFovDegrees / float(iImageWidthPixels)

    # At 6.77 degrees FOV, 1280px: ~0.00529 degrees per pixel
    assert 0.005 <= flAnglePerPixel <= 0.006, f"Angle per pixel {flAnglePerPixel:.6f} outside expected range"

    # A person 100 pixels from center should be ~0.53 degrees off-center
    flOffsetDegrees = 100.0 * flAnglePerPixel
    assert 0.5 <= flOffsetDegrees <= 0.6, f"100px offset = {flOffsetDegrees:.3f} degrees"
```

### Motor Simulation Verification Test
```python
def test_simulated_motor_does_not_teleport():
    """
    Verify the simulated motor takes time to reach target.
    It must accelerate, cruise, and decelerate -- not instant teleport.
    """
    obMotor = SimulatedMotorInterface()
    obMotor._flMaxSpeedDegreesPerSecond = 30.0
    obMotor._flAccelerationDegreesPerSecondSquared = 60.0

    # Command a 10-degree move
    obMotor.send_move_to_angle_command(10.0)

    # After 1ms, motor should NOT be at target
    obMotor.advance_simulation(0.001)
    obState = obMotor.get_latest_motor_state()
    assert obState.flMotorAngleDegrees < 1.0, "Motor teleported to target!"

    # After enough time, motor should reach target
    for _ in range(1000):  # 1000 * 1ms = 1 second
        obMotor.advance_simulation(0.001)

    obState = obMotor.get_latest_motor_state()
    assert abs(obState.flMotorAngleDegrees - 10.0) < 0.1, \
        f"Motor did not reach target: {obState.flMotorAngleDegrees:.3f}"

def test_simulated_motor_respects_velocity_limit():
    """Motor velocity should never exceed flMaxSpeedDegreesPerSecond."""
    obMotor = SimulatedMotorInterface()
    obMotor._flMaxSpeedDegreesPerSecond = 20.0
    obMotor._flAccelerationDegreesPerSecondSquared = 100.0  # High accel

    obMotor.send_move_to_angle_command(90.0)  # Large move

    flMaxObservedVelocity = 0.0
    for _ in range(5000):  # 5 seconds
        obMotor.advance_simulation(0.001)
        flVelocity = abs(obMotor._flCurrentVelocityDegreesPerSecond)
        flMaxObservedVelocity = max(flMaxObservedVelocity, flVelocity)

    # Allow 1% tolerance for floating point
    assert flMaxObservedVelocity <= 20.0 * 1.01, \
        f"Velocity exceeded limit: {flMaxObservedVelocity:.3f} > 20.0"
```

## Existing Codebase Audit: Silent Exception Handling

Comprehensive inventory of `except Exception: pass` blocks in the codebase. Each categorized for the planner.

### Critical Path (MUST fix -- CODE-02)
| File | Line | Context | Action |
|------|------|---------|--------|
| motor_interface.py | 345 | Arduino timestamp parsing fails silently | Catch ValueError, log with traceback |
| tracker_controller.py | 109 | Motor angle estimation fails silently | Catch specific exception, log warning |
| tracker_controller.py | 264 | Config getattr fails silently | Catch AttributeError, use default |
| tracker_controller.py | 269 | Config getattr fails silently | Catch AttributeError, use default |
| config_manager.py | 125 | FOV extraction from nested JSON fails silently | Catch KeyError/TypeError, log debug |
| config_manager.py | 224 | FOV extraction from nested JSON fails silently (duplicate) | Catch KeyError/TypeError, log debug |
| config_editor.py | 210 | Config save fails silently | Catch IOError, log error to user |
| main.py | 424 | FOV calibration entire block fails silently | Catch specific, log error |

### UI Path (acceptable with logging)
| File | Line | Context | Action |
|------|------|---------|--------|
| main.py | 203, 415, 420, 435 | UI slider updates fail when panel not open | Keep, add `logger.debug()` |
| live_settings_panel.py | 108, 192, 201, etc. | DearPyGui operations may fail during setup | Keep, add `logger.debug()` |
| live_settings_panel.py | 320 | Config save fails | Add `logger.error()` |
| live_settings_panel.py | 375, 417 | Screen metrics / font scale fails | Keep with fallback, acceptable |

### Cleanup Path (resource release)
| File | Line | Context | Action |
|------|------|---------|--------|
| pose_tracker.py | 182, 186, 269 | MediaPipe detector close in destructor | Keep -- destructors must not raise |

## Existing Codebase Audit: Dead Code Candidates

Based on reading all source files, these are potential dead code candidates to investigate:

| Item | File | Evidence | Verdict |
|------|------|----------|---------|
| `dataclasses-json` dependency | requirements.txt | Not imported anywhere in src/ | REMOVE from requirements.txt |
| `set_camera_exposure()` | camera_interface.py:139 | Not called from any file | REMOVE (or mark as API for future use) |
| `enable_auto_exposure()` | camera_interface.py:153 | Not called from any file | REMOVE (or mark as API for future use) |
| `validate_configuration()` | config_manager.py:174 | Not called from any file | KEEP -- useful for future validation |
| `get_frame_dimensions()` | camera_interface.py:130 | Called from live_settings_panel.py:542 and tracker_controller.py:163,217 | KEEP -- in use |
| `TextRenderer` class | text_renderer.py | Only wraps cv2.putText with one line | KEEP -- provides consistent text rendering |
| `_obPreviousMotorState` | motor_interface.py:66 | Set but never read | REMOVE unused field |
| `_flLastCommandedTargetAngleDegrees` | motor_interface.py:68 | Set but never read externally | REVIEW -- may be useful for debugging |
| `_dLastCommandTimestampSeconds` | motor_interface.py:69 | Set but never read externally | REVIEW -- may be useful for debugging |

## FOV Calculation: Sony AX700

### Verified Calculation (HIGH confidence)

**Specifications:**
- Sony FDR-AX700 1-inch Exmor RS CMOS sensor
- Active sensor area: approximately 13.2mm x 8.8mm (1-inch type)
- Lens: Zeiss Vario-Sonnar T*, 9.3-111.6mm (actual), 29-348mm (35mm equiv at 16:9)
- Maximum optical zoom: 12x (focal length 111.6mm actual)

**Calculation:**
```
Horizontal FOV = 2 * arctan(sensor_width / (2 * focal_length))
               = 2 * arctan(13.2 / (2 * 111.6))
               = 2 * arctan(0.05914)
               = 2 * 3.386 degrees
               = 6.77 degrees
```

**Configuration value:** Set `flFieldOfViewDegrees = 6.77` in default_config.json. The requirements state ~6.8 degrees, and 6.77 is consistent with this.

**Pixel-to-degree mapping at 1280px width:**
- `flAnglePerPixel = 6.77 / 1280 = 0.00529 degrees/pixel`
- This replaces the current `flInitialAnglePerPixelDegrees = 0.05` which is ~10x too large

**Validation approach:** A person standing 100 pixels from center at this FOV is approximately 0.53 degrees off-axis. At 10 meters distance, this corresponds to about 0.09 meters (9cm) of lateral offset -- consistent with a person being slightly off-center on stage.

### Current Config Problem
The default config has `flInitialAnglePerPixelDegrees: 0.05`, which implies an FOV of `0.05 * 1280 = 64 degrees`. This is wildly wrong for max optical zoom and would cause the control algorithm to command enormous motor corrections for small pixel offsets. The `flFieldOfViewDegrees` is set to 0.0 (unset), so the system falls back to the incorrect angle-per-pixel value.

**Fix:** Set `flFieldOfViewDegrees: 6.77` in default_config.json and set `flInitialAnglePerPixelDegrees: 0.00529`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| NullMotorInterface (teleportation) | SimulatedMotorInterface (physics) | This phase | Enables meaningful control algorithm testing |
| Device-only CameraInterface | File + device CameraInterface | This phase | Enables testing without camera hardware |
| `flInitialAnglePerPixelDegrees: 0.05` | `flFieldOfViewDegrees: 6.77` | This phase | Control corrections become correctly scaled |
| Silent `except: pass` | Specific exceptions + logging | This phase | Bugs become visible instead of hidden |

**Deprecated/outdated:**
- `flInitialAnglePerPixelDegrees: 0.05`: This is 10x wrong for the AX700 at max zoom. Replace with FOV-based calculation.

## Open Questions

1. **What test video content should be used for TEST-01?**
   - What we know: A simple moving circle/rectangle can serve as a visual marker for human verification. For actual pose detection, MediaPipe needs a human-shaped figure.
   - What's unclear: Should the test video contain actual human footage (requires recording) or synthetic shapes? If synthetic shapes, how does PoseTracker handle non-human content?
   - Recommendation: Create two test modes: (1) bypass PoseTracker entirely with synthetic PoseResult injection for control algorithm testing, (2) use a real video clip of a person walking for end-to-end visual verification. The phase 1 requirement (TEST-01) specifically says "scripted scenarios" which suggests pre-recorded video files.

2. **Should SimulatedMotorInterface run a background thread like MotorInterface?**
   - What we know: The real MotorInterface runs a feedback thread that updates state asynchronously. For deterministic tests, explicit `advance_simulation()` calls are better.
   - What's unclear: For the `python main.py --video` interactive mode, should the simulated motor run in real-time with a background thread?
   - Recommendation: Support both modes. For tests: explicit stepping. For interactive mode: background thread with `time.perf_counter()` for real-time simulation.

3. **How to handle DearPyGui in headless test environments?**
   - What we know: The live_settings_panel requires DearPyGui context which may not be available in CI/headless environments.
   - What's unclear: Whether pytest will try to import DearPyGui transitively.
   - Recommendation: The settings panel is started from main.py, not from TrackerController. Tests that exercise TrackerController directly will not import DearPyGui. Ensure no test imports main.py directly.

## Sources

### Primary (HIGH confidence)
- Sony FDR-AX700 specifications -- [Sony Asia specifications page](https://www.sony-asia.com/electronics/support/memory-camcorders-fdr-ax-series/fdr-ax700/specifications) -- focal length 9.3-111.6mm, 35mm equiv 29-348mm
- [B&H Photo specifications](https://www.bhphotovideo.com/c/product/1362622-REG/sony_fdr_ax700_b_fdr_ax700_4k_camcorder.html/specs) -- 12x optical zoom, 1-inch Exmor RS
- [Image sensor format - Wikipedia](https://en.wikipedia.org/wiki/Image_sensor_format) -- 1-inch sensor dimensions: 13.2mm x 8.8mm
- OpenCV VideoCapture documentation -- [OpenCV Class Reference](https://docs.opencv.org/3.4/d8/dfe/classcv_1_1VideoCapture.html) -- accepts int device index or string file path
- [OpenCV Video Display Tutorial](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html) -- VideoWriter and CAP_PROP_POS_FRAMES
- [pytest fixtures documentation](https://docs.pytest.org/en/stable/how-to/fixtures.html) -- fixture-based dependency injection pattern
- Codebase analysis -- all 11 source files in src/ read and audited for exception handling and dead code

### Secondary (MEDIUM confidence)
- [Capture video from camera/file with OpenCV in Python](https://note.nkmk.me/en/python-opencv-videocapture-file-camera/) -- video file looping patterns
- [PMD Corporation: Mathematics of Motion Control Profiles](https://www.pmdcorp.com/resources/type/articles/get/mathematics-of-motion-control-profiles-article) -- trapezoidal profile equations
- Project research documents (.planning/research/ARCHITECTURE.md, PITFALLS.md) -- SimulatedMotor design and pitfall analysis

### Tertiary (LOW confidence)
- FOV calculation cross-verification using 35mm equivalent method gives ~5.9 degrees (due to approximation of 35mm frame dimensions at 16:9). The direct calculation using actual sensor dimensions (6.77 degrees) is more reliable. The discrepancy is because 35mm film at 16:9 is not exactly 36mm wide -- different sources cite different values. **Use the direct calculation (6.77 degrees).**

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- pytest, OpenCV file playback, numpy are all well-established
- Architecture: HIGH -- extending existing interfaces with dependency injection is the obvious approach given the codebase design
- Motor simulation physics: MEDIUM -- trapezoidal profile math is straightforward but edge cases (overshoot near target, direction reversal) need careful implementation
- FOV calculation: HIGH -- multiple calculation methods converge on ~6.8 degrees
- Dead code audit: MEDIUM -- identified candidates but dynamic Python imports mean some "unused" code may be reachable via config or UI
- Exception handling audit: HIGH -- every instance catalogued from reading full source

**Research date:** 2026-02-15
**Valid until:** 2026-03-15 (stable domain, no fast-moving dependencies)
