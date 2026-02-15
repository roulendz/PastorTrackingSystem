# External Integrations

**Analysis Date:** 2026-02-15

## APIs & External Services

**Machine Learning:**
- MediaPipe Pose (Google) - Local ML model for pose detection
  - SDK/Client: `mediapipe==0.10.8` Python package
  - Auth: None required (offline model)
  - Usage: Real-time person detection in camera frames (`src/tracking/pose_tracker.py`)
  - Model: Runs locally, no cloud API calls

## Data Storage

**Databases:**
- None

**File Storage:**
- Local filesystem only
  - Configuration: `config/default_config.json`, `config/user_config.json`
  - No persistent data storage beyond config files

**Caching:**
- None (real-time processing only)

## Authentication & Identity

**Auth Provider:**
- None (standalone desktop application)

## Monitoring & Observability

**Error Tracking:**
- None (local application)

**Logs:**
- Python standard library `logging` module
  - Configured in `src/main.py` lines 30-33
  - Format: `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`
  - Level: INFO default, DEBUG with `--debug` flag
  - Output: Console only (no log file rotation)

## Hardware Integrations

**Arduino Serial Communication:**
- Protocol: Custom ASCII text protocol over USB serial
- Connection: pyserial (COM port on Windows, `/dev/ttyUSB*` on Linux)
- Implementation: `src/interfaces/motor_interface.py`
- Baud rate: 115200 (configured via `iMotorBaudRate` in config)
- Port: Configurable via `sMotorSerialPortName` (e.g., "COM6")
- Firmware: `arduino/StepperController/StepperController.ino`
- Hardware: DM542 stepper driver, NEMA stepper motor
- Commands sent: Move to angle, speed settings, home position
- Feedback received: Current angle, target angle, speed, moving state, timestamps, sequence numbers
- Thread safety: Background thread for async feedback reception with lock-protected state

**Camera Device:**
- Protocol: USB Video Class (UVC) via OpenCV
- Implementation: `src/interfaces/camera_interface.py`
- Device selection: Index-based (e.g., 0 for default, 1 for external USB camera)
- Configuration: Resolution (1280x720), FPS (30), autofocus
- Settings via: `cv2.VideoCapture` API (`CAP_PROP_FRAME_WIDTH`, `CAP_PROP_FRAME_HEIGHT`, `CAP_PROP_FPS`)

## CI/CD & Deployment

**Hosting:**
- Not applicable (local desktop application)

**CI Pipeline:**
- None

## Environment Configuration

**Required configuration keys (JSON):**
- `sMotorSerialPortName` - Serial port name
- `iMotorBaudRate` - Serial baud rate
- `iCameraDeviceIndex` - Camera device index
- `iCameraWidthPixels`, `iCameraHeightPixels`, `iCameraFramesPerSecond` - Camera settings
- `flPoseMinDetectionConfidence`, `flPoseMinTrackingConfidence` - MediaPipe thresholds
- `sControlAlgorithmType` - Algorithm selection ("P", "PID", "Velocity")
- Motor limits: `flMotorMinAngleDegrees`, `flMotorMaxAngleDegrees`
- Control gains: `flControlProportionalGain`, `flControlIntegralGain`, `flControlDerivativeGain`

**Secrets location:**
- None required (no API keys or credentials)

**Offline operation:**
- Fully offline capable
- MediaPipe model runs locally
- No internet connectivity required

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

## Development Tools

**GUI Framework:**
- DearPyGUI - Immediate mode GUI for live settings panel (`src/ui/live_settings_panel.py`) and config editor (`src/ui/config_editor.py`)
- Runs in separate thread to avoid blocking main tracking loop

**Embedded Toolchain:**
- Arduino IDE - Firmware compilation and upload
- AccelStepper library dependency (installed via Arduino Library Manager)

---

*Integration audit: 2026-02-15*
