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
  --mode manual ^
  --m4l ^
  --feedback ^
  --data-dir "%~dp0data" ^
  --osc-host 127.0.0.1 ^
  --osc-in-port 9000 ^
  --osc-out-port 9001 ^
  --in ARIA_IN ^
  --out ARIA_OUT ^
  --checkpoint "%~dp0models\model-gen.safetensors"
