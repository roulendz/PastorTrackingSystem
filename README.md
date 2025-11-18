# 🎥 Pastor Tracking System - Python Implementation

**Complete automated person-tracking camera system using MediaPipe, OpenCV, and Arduino.**

---

## ✨ Features

- ✅ **Real-time person detection** using MediaPipe Pose
- ✅ **Automatic FOV learning** - no manual calibration needed!
- ✅ **Smooth motor control** with Arduino stepper controller
- ✅ **Multiple control algorithms** (P, PID, Velocity-based)
- ✅ **Live visualization** with pose overlay
- ✅ **Configuration via JSON** - easy to customize
- ✅ **Persistent calibration** - remembers FOV between sessions
- ✅ **Clean architecture** - follows SRP and DRY principles

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Upload Arduino Firmware

```
1. Open Arduino IDE
2. Open: arduino/StepperController/StepperController.ino
3. Install AccelStepper library (Sketch → Include Library → Manage Libraries)
4. Select your board and port
5. Click Upload
```

### 3. Configure

Edit `config/default_config.json`:
- Set `sMotorSerialPortName` (e.g., `/dev/ttyUSB0` or `COM3`)
- Adjust camera settings if needed

### 4. Run!

```bash
cd src
python main.py
```

**Controls:**
- `S` - Start tracking
- `P` - Pause
- `C` - Calibration mode
- `H` - Home motor
- `Q` - Quit

---

## 📁 Project Structure

```
PastorTrackingSystem_Python/
├── src/                          # Python application
│   ├── main.py                   # Entry point
│   ├── core/                     # Core data structures
│   │   └── tracking_sample.py
│   ├── interfaces/               # Hardware interfaces
│   │   ├── motor_interface.py
│   │   └── camera_interface.py
│   ├── tracking/                 # Pose detection & FOV learning
│   │   ├── pose_tracker.py
│   │   └── fov_estimator.py
│   ├── control/                  # Control algorithms
│   │   ├── control_algorithm.py
│   │   └── tracker_controller.py
│   └── utilities/                # Configuration & helpers
│       └── config_manager.py
│
├── arduino/                      # Arduino firmware
│   └── StepperController/
│       └── StepperController.ino
│
├── config/                       # Configuration files
│   └── default_config.json
│
├── docs/                         # Documentation
│   ├── INSTALLATION.md
│   └── ARCHITECTURE.md
│
└── requirements.txt              # Python dependencies
```

---

## 🎯 How It Works

### The Core Concept

Every camera frame is **paired** with the motor's actual angle at that exact instant:

```python
sample = TrackingSample(
    timestamp = frame_capture_time,
    motor_angle = motor.get_actual_angle(),  # ACTUAL, not target!
    person_x = detected_person_center,
    detected = True
)
```

### FOV Learning Algorithm

The system automatically learns the camera's field of view:

1. **Observe motion:** When motor moves and person appears to shift in frame
2. **Calculate deltas:** Δθ (motor angle change) and Δx (pixel change)
3. **Compute ratio:** `angle_per_pixel = Δθ / Δx`
4. **Update estimate:** Exponential moving average for stability

Example:
```
Frame 1: Motor at 10°, Person at pixel 800
Frame 2: Motor at 12°, Person at pixel 760

Δθ = 2°
Δx = -40 pixels
angle_per_pixel = 2° / -40px = -0.05 deg/px
```

After ~50 samples, the estimate converges!

### Control Loop

```python
# 1. Get pixel offset from center
pixel_error = person_x - (image_width / 2)

# 2. Convert to angle error using learned FOV
angle_error = pixel_error * angle_per_pixel

# 3. Calculate correction (P controller)
correction = Kp * angle_error

# 4. Command new position
target_angle = current_angle + correction
motor.move_to(target_angle)
```

---

## ⚙️ Configuration

Edit `config/default_config.json`:

### Motor Settings
```json
{
  "sMotorSerialPortName": "/dev/ttyUSB0",  // Your serial port
  "flMotorMaxSpeedStepsPerSecond": 25000.0,
  "flMotorMaxAccelerationStepsPerSecondSquared": 12500.0
}
```

### Camera Settings
```json
{
  "iCameraDeviceIndex": 0,  // 0 = default camera
  "iCameraWidthPixels": 1280,
  "iCameraHeightPixels": 720,
  "iCameraFramesPerSecond": 30
}
```

### Control Settings
```json
{
  "sControlAlgorithmType": "P",  // "P", "PID", or "Velocity"
  "flControlProportionalGain": 1.0,  // Increase for faster response
  "flControlDeadbandDegrees": 0.3    // Ignore errors < 0.3°
}
```

---

## 🔧 Advanced Usage

### Using Different Control Algorithms

**P Controller (Default):**
- Fast, stable
- Good for most applications

**PID Controller:**
```json
{
  "sControlAlgorithmType": "PID",
  "flControlProportionalGain": 1.0,
  "flControlIntegralGain": 0.1,    // Eliminates steady-state error
  "flControlDerivativeGain": 0.05   // Reduces overshoot
}
```

**Velocity Controller:**
- Smoother motion
- Better for continuous tracking

### Manual Calibration

If you know your camera's FOV:
```json
{
  "bUseStoredCalibration": true,
  "flStoredAnglePerPixelDegrees": 0.05  // Your known value
}
```

---

## 🐛 Troubleshooting

### Camera Not Opening
- Check `iCameraDeviceIndex` in config
- Try index 1, 2, etc. for external cameras
- Linux: Ensure user in `video` group

### Motor Not Responding
- Verify serial port in config
- Linux: Check permissions (`sudo chmod 666 /dev/ttyUSB0`)
- Ensure Arduino firmware uploaded successfully

### Person Not Detected
- Ensure good lighting
- Check `flPoseMinDetectionConfidence` (try lowering to 0.3)
- Person should be visible head to hips minimum

### Tracking Jittery
- Increase `flControlDeadbandDegrees` to 0.5 or 1.0
- Lower `flControlProportionalGain` to 0.5 or 0.7
- Check that FOV has converged (wait for ~100 samples)

---

## 📊 Performance

Typical performance on modern hardware:
- **Frame rate:** 30 FPS
- **Pose detection:** ~30 ms per frame
- **Motor latency:** <50 ms
- **Total latency:** ~80-100 ms (imperceptible)

---

## 🏗️ Architecture Principles

### Single Responsibility Principle (SRP)
Each module does ONE thing:
- `MotorInterface` → Arduino communication ONLY
- `CameraInterface` → Frame capture ONLY
- `PoseTracker` → Person detection ONLY
- `FOVEstimator` → FOV learning ONLY

### Don't Repeat Yourself (DRY)
- Angle conversions in ONE place
- Configuration in ONE file
- No duplicated logic

### Clean Interfaces
```python
# Each module has clear, minimal API
motor.send_move_to_angle_command(45.0)
frame, timestamp = camera.capture_frame_with_timestamp()
result = pose_tracker.detect_person_in_frame(frame)
```

---

## 📝 Development

### Running Tests
```bash
# TODO: Add pytest tests
pytest tests/
```

### Adding New Control Algorithm
```python
from control.control_algorithm import ControlAlgorithm

class MyController(ControlAlgorithm):
    def calculate_correction_from_error(self, error):
        # Your algorithm here
        return correction
```

---

## 🤝 Contributing

Contributions welcome! Please:
1. Follow existing code style (Hungarian notation)
2. One feature per module (SRP)
3. No code duplication (DRY)
4. Add documentation

---

## 📄 License

MIT License - see LICENSE file

---

## 🙏 Acknowledgments

- **MediaPipe** by Google for pose detection
- **OpenCV** for computer vision
- **AccelStepper** library for Arduino

---

## 📞 Support

For issues or questions:
- Check `docs/INSTALLATION.md` for detailed setup
- Check `docs/ARCHITECTURE.md` for technical details
- Review configuration in `config/default_config.json`

---

**Built with ❤️ following clean code principles**

Python 3.8+ • MediaPipe • OpenCV • Arduino
