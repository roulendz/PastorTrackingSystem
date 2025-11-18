# 🏗️ Architecture Documentation

Technical overview of the Pastor Tracking System.

---

## 🎯 Design Principles

### 1. Single Responsibility Principle (SRP)
Each module has ONE clear purpose.

### 2. Don't Repeat Yourself (DRY)
No code duplication, shared logic in utilities.

### 3. Clean Interfaces
Minimal, clear APIs between modules.

### 4. Dependency Injection
Modules receive dependencies, not create them.

---

## 📊 System Overview

```
┌─────────────┐
│   Camera    │ (30 FPS)
└──────┬──────┘
       │ Frame + timestamp
       ▼
┌─────────────────────┐         ┌─────────────┐
│   PoseTracker       │         │   Motor     │ (feedback)
│  (MediaPipe Pose)   │         │  Arduino    │
└──────┬──────────────┘         └──────┬──────┘
       │ Person position                │ Actual angle
       │                                │
       ▼                                ▼
┌────────────────────────────────────────────┐
│          TrackerController                 │
│  ┌──────────────────────────────────────┐  │
│  │ Build TrackingSample:                │  │
│  │ • timestamp                          │  │
│  │ • motor_angle (ACTUAL, not target)  │  │
│  │ • person_x                           │  │
│  └──────────────────────────────────────┘  │
│              │                              │
│              ├──► FOVEstimator (learning)  │
│              │                              │
│              └──► ControlAlgorithm         │
│                   (calculate correction)   │
└───────────────────┬────────────────────────┘
                    │ Move command
                    ▼
              ┌─────────────┐
              │MotorInterface│
              └─────────────┘
```

---

## 📦 Module Breakdown

### 1. TrackingSample (Core Data Structure)

**Purpose:** Atomic measurement pairing camera + motor

```python
@dataclass
class TrackingSample:
    timestamp: float
    motor_angle: float        # ACTUAL angle, not target!
    person_center_x: float
    person_detected: bool
```

**Why:** Single source of truth for each instant.

---

### 2. MotorInterface

**Purpose:** Arduino communication ONLY

**Responsibilities:**
- Send commands via serial
- Receive feedback asynchronously
- Maintain latest motor state (thread-safe)

**API:**
```python
motor.connect_to_motor_controller()
motor.send_move_to_angle_command(45.0)
state = motor.get_latest_motor_state()
```

**Key Design:** Thread-safe state storage with lock

---

### 3. CameraInterface

**Purpose:** Frame capture ONLY

**Responsibilities:**
- Open/configure camera
- Capture frames with timestamps
- Handle errors gracefully

**API:**
```python
camera.open_camera_device()
frame, timestamp = camera.capture_frame_with_timestamp()
```

**Key Design:** Minimal latency capture

---

### 4. PoseTracker

**Purpose:** Person detection ONLY

**Responsibilities:**
- Initialize MediaPipe Pose
- Detect person in frame
- Calculate person center position

**API:**
```python
result = tracker.detect_person_in_frame(frame)
# result.person_center_x
# result.detected
# result.confidence
```

**Key Design:** Optimized for speed (model_complexity=1)

---

### 5. FieldOfViewEstimator

**Purpose:** FOV learning ONLY

**Algorithm:**
```python
1. Compare consecutive samples
2. Calculate Δθ = angle2 - angle1
3. Calculate Δx = pixel2 - pixel1
4. If motion significant:
   new_sample = Δθ / Δx
   estimate = 0.95 * estimate + 0.05 * new_sample
```

**API:**
```python
estimator.update_field_of_view_estimator_with_sample(sample)
angle_per_pixel = estimator.get_estimated_angle_per_pixel_ratio()
```

**Key Design:** Exponential moving average for stability

---

### 6. ControlAlgorithm

**Purpose:** Calculate corrections ONLY

**Implementations:**
- **ProportionalController:** Simple, fast (output = Kp * error)
- **PIDController:** Advanced with anti-windup
- **VelocityController:** Smooth continuous motion

**API:**
```python
correction = controller.calculate_correction_from_error(error)
```

**Key Design:** Strategy pattern for swappable algorithms

---

### 7. TrackerController

**Purpose:** Orchestrate all modules

**Main Loop:**
```python
def execute_main_tracking_loop_tick():
    # 1. Capture frame
    frame, timestamp = camera.capture_frame_with_timestamp()
    
    # 2. Get motor state (snapshot)
    motor_state = motor.get_latest_motor_state()
    
    # 3. Detect person
    pose_result = pose_tracker.detect_person_in_frame(frame)
    
    # 4. Build sample
    sample = TrackingSample(
        timestamp=timestamp,
        motor_angle=motor_state.angle,  # ACTUAL!
        person_x=pose_result.center_x,
        detected=pose_result.detected
    )
    
    # 5. Update FOV
    fov_estimator.update_with_sample(sample)
    
    # 6. Control (if tracking active)
    if tracking:
        execute_centering_control(sample)
```

**Key Design:** No business logic, only orchestration

---

## 🔄 Data Flow Example

### Frame-by-Frame Breakdown

**Frame 1:**
```
t=0.000s
Camera: Person at pixel 800
Motor: At 10.0°
→ Sample(t=0.000, angle=10.0, person_x=800, detected=True)
→ FOV: No previous sample, store
→ Control: person left of center, move right
```

**Frame 2 (33ms later):**
```
t=0.033s
Camera: Person at pixel 760
Motor: At 12.0° (moved!)
→ Sample(t=0.033, angle=12.0, person_x=760, detected=True)
→ FOV: Δθ=2°, Δx=-40px, ratio=0.05°/px, update estimate
→ Control: still left, continue correction
```

**Frame 50:**
```
t=1.650s
Camera: Person at pixel 640 (centered!)
Motor: At 32.5°
→ Sample(t=1.650, angle=32.5, person_x=640, detected=True)
→ FOV: Estimate=0.0498°/px (converged)
→ Control: error < deadband, no correction needed
```

---

## 🧩 Key Design Decisions

### 1. Why TrackingSample?

**Problem:** Motor might still be moving when frame arrives

**Solution:** Always use ACTUAL reported angle, not commanded target

**Benefit:** Accurate even during motion

### 2. Why Separate FOV Learning?

**Problem:** FOV and control are different concerns

**Solution:** FOVEstimator knows nothing about control

**Benefit:** Can learn during manual movement, testing, etc.

### 3. Why Thread-Safe Motor State?

**Problem:** Motor feedback arrives asynchronously

**Solution:** Thread with lock-protected state storage

**Benefit:** Main loop always gets latest state

### 4. Why Hungarian Notation?

**Example:**
```python
flMotorAngleDegrees: float   # fl = float
iCameraWidthPixels: int      # i = integer
bPersonWasDetected: bool     # b = boolean
sMotorSerialPortName: str    # s = string
obFrameImage: np.ndarray     # ob = object
```

**Benefit:** Type immediately obvious, self-documenting

---

## 🎨 Design Patterns Used

### 1. Strategy Pattern
```python
class ControlAlgorithm(ABC):
    @abstractmethod
    def calculate_correction(self, error): pass

class ProportionalController(ControlAlgorithm): ...
class PIDController(ControlAlgorithm): ...
```

### 2. Dependency Injection
```python
controller = TrackerController(
    motor_interface,    # Injected
    camera_interface,   # Injected
    pose_tracker,       # Injected
    fov_estimator,      # Injected
    control_algorithm   # Injected
)
```

### 3. Observer Pattern (Implicit)
Motor feedback uses callback for state updates.

### 4. Singleton (Configuration)
One ConfigurationManager per application.

---

## ⚡ Performance Optimizations

### 1. MediaPipe Configuration
```python
mp.solutions.pose.Pose(
    static_image_mode=False,  # Video mode (faster)
    model_complexity=1,       # Balance speed/accuracy
    enable_segmentation=False # Skip if not needed
)
```

### 2. Frame Capture
Timestamp immediately after capture for accuracy.

### 3. Thread-Safe State
Lock held only during copy, not during computation.

### 4. Exponential Moving Average
O(1) update, no history storage needed.

---

## 🔐 Thread Safety

### Critical Sections

**MotorInterface:**
```python
with self._lock:
    state_copy = copy(self._latest_state)
return state_copy
```

**Why:** Feedback thread updates, main thread reads

---

## 📏 Naming Conventions

### Variables
- `flValue`: float
- `iCount`: integer
- `bFlag`: boolean
- `sText`: string
- `obObject`: object (OpenCV Mat, custom class)
- `vList`: vector/list
- `dTimestamp`: double (high-precision)

### Functions
```python
def calculate_angle_delta_degrees()  # ✓ Verb + descriptive
def send_move_to_angle_command()     # ✓ Clear action
def get_estimated_field_of_view()    # ✓ Self-explanatory
```

### Classes
```python
class MotorInterface         # ✓ PascalCase, descriptive
class FieldOfViewEstimator   # ✓ Clear purpose
class ProportionalController # ✓ Specific implementation
```

---

## 🧪 Testability

Each module can be tested independently:

```python
# Test FOV Estimator without hardware
estimator = FieldOfViewEstimator(0.1)
for i in range(100):
    sample = create_mock_sample(angle=i, x=960-i*20)
    estimator.update(sample)
assert abs(estimator.get_angle_per_pixel() - 0.05) < 0.01
```

---

## 🔮 Future Enhancements

### Possible Additions:
1. **Multi-person tracking** - Select primary person
2. **Zoom control** - Adjust based on person size
3. **Predictive control** - Lead person movement
4. **Cloud recording** - Save tracking sessions
5. **Web interface** - Remote monitoring

### How to Add:
Each feature gets its own module, following SRP.

---

## 📚 References

- **MediaPipe Pose:** https://google.github.io/mediapipe/solutions/pose
- **AccelStepper:** http://www.airspayce.com/mikem/arduino/AccelStepper/
- **Clean Code Principles:** Robert C. Martin

---

**Architecture designed for clarity, maintainability, and performance.**
