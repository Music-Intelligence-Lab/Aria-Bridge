@echo off
setlocal

cd /d "%~dp0backend"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Please run install.bat first.
    pause
    exit /b 1
)

if not exist "%~dp0models\model-gen.safetensors" (
    echo ERROR: Model file not found.
    echo Please download model-gen.safetensors from:
    echo https://huggingface.co/eleutherai/aria
    echo and place it in the models\ folder.
    pause
    exit /b 1
)

"%~dp0venv\Scripts\python.exe" ableton_bridge.py ^
  plugin ^
  --feedback ^
  --data-dir "%~dp0data" ^
  --checkpoint "%~dp0models\model-gen.safetensors"
