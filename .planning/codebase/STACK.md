# Technology Stack

**Analysis Date:** 2026-02-15

## Languages

**Primary:**
- Python 3.10.6 - All application code in `src/`

**Secondary:**
- C++ (Arduino) - Motor controller firmware in `arduino/StepperController/StepperController.ino`

## Runtime

**Environment:**
- Python 3.10.6

**Package Manager:**
- pip (requirements.txt)
- Lockfile: Not present (no requirements.lock or Pipfile.lock)

**Virtual Environment:**
- `.venv/` directory present (venv standard library)

## Frameworks

**Core:**
- OpenCV 4.8.1.78 - Camera capture, video frame processing, visualization overlays
- MediaPipe 0.10.8 - Pose detection and person tracking
- DearPyGUI 1.11.1 - Real-time settings panel GUI and config editor

**Testing:**
- Not configured (no test framework in requirements.txt)

**Build/Dev:**
- No build system (interpreted Python)
- Arduino IDE required for firmware compilation

## Key Dependencies

**Critical:**
- `opencv-python==4.8.1.78` - Camera interface, frame capture, visualization
- `mediapipe==0.10.8` - Person pose detection ML model
- `pyserial==3.5` - Serial communication with Arduino motor controller
- `numpy==1.24.3` - Array operations for pose calculations and image processing

**Infrastructure:**
- `dataclasses-json==0.6.3` - Configuration serialization and data structure handling
- `dearpygui==1.11.1` - Live parameter tuning UI and config editor GUI

**Embedded:**
- AccelStepper (Arduino library) - Required for stepper motor control firmware

## Configuration

**Environment:**
- JSON-based configuration system
- Base config: `config/default_config.json`
- User overrides: `config/user_config.json`
- No environment variables used (.env files not present)
- Configuration managed by singleton `ConfigurationManager` in `src/utilities/config_manager.py`

**Build:**
- No build configuration (interpreted language)
- Arduino firmware compiled via Arduino IDE

## Platform Requirements

**Development:**
- Windows 10 Pro (designed for Windows, may work on Linux/macOS)
- Python 3.10+
- Arduino IDE (for firmware upload)
- Camera device (USB webcam or integrated)
- Arduino board (Uno/Nano/Mega)
- DM542 stepper driver
- Hardware stepper motor with 180:1 gearbox
- Serial port (USB-to-serial for Arduino)

**Production:**
- Same as development (desktop application, not server-deployed)
- Runs locally on presentation/church service computer
- Real-time performance requirements (30 FPS camera capture)

---

*Stack analysis: 2026-02-15*
