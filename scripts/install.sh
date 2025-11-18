#!/bin/bash
# Installation script for Pastor Tracking System (Linux/Mac)

echo "=================================================="
echo "  Pastor Tracking System - Installation"
echo "=================================================="
echo ""

# Check Python version
echo "[1/5] Checking Python version..."
python3 --version
if [ $? -ne 0 ]; then
    echo "ERROR: Python 3 not found! Please install Python 3.8 or newer."
    exit 1
fi

# Create virtual environment
echo "[2/5] Creating virtual environment..."
python3 -m venv venv
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to create virtual environment!"
    exit 1
fi

# Activate virtual environment
echo "[3/5] Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "[4/5] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install dependencies!"
    exit 1
fi

# Create config directory
echo "[5/5] Setting up configuration..."
mkdir -p config
if [ ! -f config/default_config.json ]; then
    echo "Config file already exists or created."
fi

echo ""
echo "=================================================="
echo "  ✓ Installation Complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "1. Upload Arduino firmware:"
echo "   - Open Arduino IDE"
echo "   - File → Open → arduino/StepperController/StepperController.ino"
echo "   - Install AccelStepper library"
echo "   - Upload to Arduino"
echo ""
echo "2. Configure:"
echo "   - Edit config/default_config.json"
echo "   - Set your serial port (e.g., /dev/ttyUSB0)"
echo ""
echo "3. Run:"
echo "   source venv/bin/activate"
echo "   cd src"
echo "   python main.py"
echo ""
