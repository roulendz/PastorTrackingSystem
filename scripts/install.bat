@echo off
echo ==================================================
echo   Pastor Tracking System - Installation
echo ==================================================
echo.

setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\.."
set "PYENV_EXE=pyenv"
if exist "%USERPROFILE%\.pyenv\pyenv-win\bin\pyenv.bat" set "PYENV_EXE=%USERPROFILE%\.pyenv\pyenv-win\bin\pyenv.bat"

set "PYENV_SHIMS=%USERPROFILE%\.pyenv\pyenv-win\shims"
set "PY_CMD=python"
if exist "%PYENV_SHIMS%\python.exe" set "PY_CMD=%PYENV_SHIMS%\python.exe"

call :check_python
call :create_venv
call :activate_venv
call :install_deps
call :setup_config
call :complete
exit /b 0

:check_python
echo [1/5] Checking Python...
"%PY_CMD%" --version
exit /b 0

:create_venv
echo [2/5] Creating virtual environment...
if exist venv\Scripts\python.exe (
    echo Using existing virtual environment.
) else (
    "%PY_CMD%" -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment!
        pause
        exit /b 1
    )
)
exit /b 0

:activate_venv
echo [3/5] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment!
    pause
    exit /b 1
)
exit /b 0

:install_deps
echo [4/5] Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install dependencies!
    pause
    exit /b 1
)
exit /b 0

:setup_config
echo [5/5] Setting up configuration...
if not exist config mkdir config
exit /b 0

:complete
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
echo DONE
exit /b 0
