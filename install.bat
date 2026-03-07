@echo off
setlocal

echo Aria Bridge Installer - Windows
echo This will set up Python 3.11 and all required dependencies.
echo Please do not close this window.
echo.

py -3.11 --version >nul 2>&1
if %errorlevel% equ 0 goto python_ready

echo Python 3.11 not found. Downloading installer...
curl -L "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -o "%TEMP%\python311_installer.exe"
if errorlevel 1 (
    echo Failed to download Python 3.11 installer.
    pause
    exit /b 1
)

"%TEMP%\python311_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
if errorlevel 1 (
    echo Python 3.11 installation failed.
    pause
    exit /b 1
)

echo Python 3.11 installed successfully.
echo.

:python_ready
py -3.11 -m venv "%~dp0venv"
if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
)

echo Virtual environment created.
echo.

"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

"%~dp0venv\Scripts\pip.exe" install torch torchvision torchaudio ^
--index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (
    echo Failed to install PyTorch with CUDA 12.1 support.
    pause
    exit /b 1
)

echo PyTorch with CUDA installed.
echo.

"%~dp0venv\Scripts\pip.exe" install -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
    echo Failed to install backend requirements.
    pause
    exit /b 1
)

echo All dependencies installed.
echo.
echo Installation complete!
echo Next steps:
echo   1. Download the Aria model from HuggingFace:
echo      https://huggingface.co/eleutherai/aria
echo   2. Place model-gen.safetensors in the models\ folder
echo   3. Launch Aria Bridge.exe
pause
