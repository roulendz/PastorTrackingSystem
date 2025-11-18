@echo off
REM Installation script for Pastor Tracking System (Windows)

echo ==================================================
echo   Pastor Tracking System - Installation
echo ==================================================
echo.

REM Check Python
echo [1/5] Checking Python version...
python --version
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found! Please install Python 3.8 or newer.
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Create virtual environment
echo [2/5] Creating virtual environment...
python -m venv venv
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to create virtual environment!
    pause
    exit /b 1
)

REM Activate virtual environment
echo [3/5] Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo [4/5] Installing Python dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install dependencies!
    pause
    exit /b 1
)

REM Create config directory
echo [5/5] Setting up configuration...
if not exist config mkdir config

echo.
echo ==================================================
echo   Installation Complete!
echo ==================================================
echo.
echo Next steps:
echo 1. Upload Arduino firmware:
echo    - Open Arduino IDE
echo    - File -^> Open -^> arduino\StepperController\StepperController.ino
echo    - Install AccelStepper library
echo    - Upload to Arduino
echo.
echo 2. Configure:
echo    - Edit config\default_config.json
echo    - Set your serial port (e.g., COM3)
echo.
echo 3. Run:
echo    venv\Scripts\activate
echo    cd src
echo    python main.py
echo.
pause
