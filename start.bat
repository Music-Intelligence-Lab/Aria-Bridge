@echo off
setlocal

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

"%~dp0venv\Scripts\python.exe" real-time\ableton_bridge.py ^
  plugin ^
  --feedback ^
  --data-dir "C:\Code\Aria Feedback" ^
  --checkpoint "%~dp0models\model-gen.safetensors" ^
  --device "cuda"
