# 📥 Installation Guide

Complete setup guide for the Pastor Tracking System.

---

## 📋 System Requirements

### Hardware:
- **PC/Laptop:** 4GB RAM minimum, 8GB recommended
- **Arduino:** Uno, Mega, or compatible board
- **Stepper Motor:** NEMA 17/23 with driver (e.g., DM542)
- **Camera:** USB webcam, 720p minimum, 1080p recommended
- **OS:** Windows 10+, macOS 10.14+, or Linux (Ubuntu 20.04+)

### Software:
- Python 3.8 or newer
- Arduino IDE 1.8.13 or newer
- pip (Python package manager)

---

## 🚀 Quick Installation

### Option 1: Automated Install (Recommended)

**Linux/Mac:**
```bash
cd PastorTrackingSystem_Python
chmod +x scripts/install.sh
./scripts/install.sh
```

**Windows:**
```cmd
cd PastorTrackingSystem_Python
scripts\install.bat
```

### Option 2: Manual Install

#### Step 1: Create Virtual Environment

```bash
# Linux/Mac
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

#### Step 2: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 3: Verify Installation

```bash
python -c "import cv2, mediapipe, serial; print('✓ All dependencies installed')"
```

---

## 🔌 Arduino Setup

### Step 1: Install Arduino IDE

Download from: https://www.arduino.cc/en/software

### Step 2: Install AccelStepper Library

```
1. Open Arduino IDE
2. Sketch → Include Library → Manage Libraries
3. Search: "AccelStepper"
4. Install: "AccelStepper by Mike McCauley"
```

### Step 3: Upload Firmware

```
1. File → Open → arduino/StepperController/StepperController.ino
2. Tools → Board → Select your Arduino
3. Tools → Port → Select COM port
4. Click Upload button (→)
5. Wait for "Done uploading"
```

### Step 4: Verify Upload

```
1. Tools → Serial Monitor
2. Set baud rate to 115200
3. You should see:
   FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState
   READY
```

---

## ⚙️ Configuration

### Step 1: Find Your Serial Port

**Linux:**
```bash
ls /dev/ttyUSB*  # Usually /dev/ttyUSB0
# OR
ls /dev/ttyACM*  # Sometimes /dev/ttyACM0
```

**Windows:**
```
Device Manager → Ports (COM & LPT) → Look for "Arduino" or "CH340"
Usually COM3, COM4, etc.
```

**Mac:**
```bash
ls /dev/tty.usbserial-*  # OR /dev/tty.usbmodem*
```

### Step 2: Edit Configuration

Edit `config/default_config.json`:

```json
{
    "sMotorSerialPortName": "/dev/ttyUSB0",  // ← YOUR PORT HERE
    "iCameraDeviceIndex": 0,
    "iCameraWidthPixels": 1280,
    "iCameraHeightPixels": 720
}
```

### Step 3: Test Camera

```bash
python -c "import cv2; cap = cv2.VideoCapture(0); print('Camera OK' if cap.isOpened() else 'Camera FAIL')"
```

---

## 🏃 Running the System

### Basic Usage

```bash
# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate     # Windows

# Run application
cd src
python main.py
```

### With Custom Config

```bash
python main.py --config ../config/my_config.json
```

### Debug Mode

```bash
python main.py --debug
```

---

## 🎮 Controls

| Key | Action |
|-----|--------|
| `S` | Start tracking |
| `P` | Pause tracking |
| `C` | Calibration mode |
| `H` | Home motor (move to 0°) |
| `Q` | Quit |

---

## 🐛 Troubleshooting

### Issue: "Permission denied" on Linux

```bash
# Add user to dialout group
sudo usermod -a -G dialout $USER
# OR temporary fix
sudo chmod 666 /dev/ttyUSB0
# Then logout and login
```

### Issue: Camera not found

```bash
# List available cameras (Linux)
v4l2-ctl --list-devices

# Test different indices
python -c "import cv2; print([i for i in range(5) if cv2.VideoCapture(i).isOpened()])"
```

### Issue: MediaPipe not working

```bash
# Reinstall MediaPipe
pip uninstall mediapipe
pip install mediapipe==0.10.8
```

### Issue: Motor not responding

1. Check connections: DIR, STEP, ENABLE pins
2. Verify power to motor driver
3. Test with Arduino Serial Monitor:
   - Send: `M,45` (should move to 45°)
   - Send: `Q` (should print status)

### Issue: Tracking is jittery

Edit config:
```json
{
    "flControlProportionalGain": 0.5,  // Lower = smoother
    "flControlDeadbandDegrees": 0.5    // Higher = less sensitive
}
```

---

## 📦 Dependencies Explained

### Python Packages

- **opencv-python** (4.8.1.78): Camera capture and image processing
- **mediapipe** (0.10.8): Body pose detection
- **pyserial** (3.5): Arduino communication
- **numpy** (1.24.3): Numerical operations

### Arduino Libraries

- **AccelStepper**: Smooth stepper motor control with acceleration

---

## 🔄 Updating

### Update Python Dependencies

```bash
pip install --upgrade -r requirements.txt
```

### Update Arduino Firmware

1. Make changes to .ino file
2. Re-upload to Arduino

### Update Configuration

Edit `config/default_config.json` and restart application

---

## 🗑️ Uninstallation

```bash
# Remove virtual environment
rm -rf venv

# Remove generated files
rm -rf __pycache__
rm -rf src/__pycache__
rm -rf src/*/__pycache__
```

---

## ✅ Installation Checklist

- [ ] Python 3.8+ installed
- [ ] Virtual environment created
- [ ] Dependencies installed
- [ ] Arduino IDE installed
- [ ] AccelStepper library installed
- [ ] Firmware uploaded to Arduino
- [ ] Serial port configured
- [ ] Camera tested
- [ ] Configuration edited
- [ ] Application runs successfully

---

## 📞 Getting Help

If you encounter issues:

1. Check this guide thoroughly
2. Review error messages carefully
3. Test each component individually:
   - Arduino (Serial Monitor)
   - Camera (OpenCV test)
   - Serial port (permissions)
4. Check configuration file syntax

---

## 🎓 Next Steps

After installation:
1. Read `README.md` for usage guide
2. Review `docs/ARCHITECTURE.md` for technical details
3. Customize `config/default_config.json` for your setup
4. Experiment with different control algorithms

---

**Installation complete! Time to track! 🎥**
