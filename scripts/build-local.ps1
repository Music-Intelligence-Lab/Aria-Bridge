#Requires -Version 5.1
<#
.SYNOPSIS
    Local build script for Aria Bridge (Windows).
    Each step is skipped automatically when its output already exists.
    Delete the relevant artifact to force a rebuild.

    Builds the JUCE plugin (Standalone + VST3) and the PyInstaller backend as loose
    artifacts. The Electron launcher was removed; a new front-end will be rebuilt later.

.PARAMETER SkipJuce
    Force-skip CMake / MSVC even if artifacts are missing.

.PARAMETER SkipBackend
    Force-skip PyInstaller even if dist\aria_backend.exe is missing.

.EXAMPLE
    # First run builds everything; re-runs only rebuild what changed.
    .\build-local.ps1

    # Force a backend rebuild even though the exe exists.
    Remove-Item dist\aria_backend.exe; .\build-local.ps1
#>
param(
    [switch]$SkipJuce,
    [switch]$SkipBackend,
    [string]$Out = "$env:USERPROFILE\Downloads\AriaBridge"
)

$ErrorActionPreference = "Stop"
$Root   = Split-Path $PSScriptRoot -Parent
$RelDir = $Out

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Done($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

$sw = [System.Diagnostics.Stopwatch]::StartNew()

Step "Killing any running Aria processes..."
# taskkill /T kills the process tree (child processes too), more reliable than Stop-Process
cmd /c "taskkill /F /T /IM ""aria_backend.exe"" >nul 2>&1"
cmd /c "taskkill /F /T /IM ""Aria Bridge.exe""  >nul 2>&1"
cmd /c "taskkill /F /T /IM ""python.exe""       >nul 2>&1"
cmd /c "taskkill /F /T /IM ""pythonw.exe""      >nul 2>&1"
Start-Sleep -Seconds 2

New-Item -ItemType Directory -Force -Path "$Root\dist" | Out-Null

$juceBase    = "$Root\real-time\Plugin\build\AriaBridge_artefacts\Release"
$juceExe     = "$juceBase\Standalone\Aria Bridge.exe"
$juceVst     = "$juceBase\VST3\Aria Bridge.vst3"
$backendExe  = "$Root\dist\aria_backend.exe"

# ── 1. JUCE ──────────────────────────────────────────────────────────────────
if ($SkipJuce) {
    Warn "Skipping JUCE build (flag set)."
} elseif ((Test-Path $juceExe) -and (Test-Path $juceVst)) {
    Warn "JUCE artifacts found, skipping build. Delete real-time\Plugin\build to rebuild."
} else {
    Step "Configuring JUCE (CMake)..."
    Push-Location "$Root\real-time\Plugin"
    cmake -B build -G "Visual Studio 17 2022" -A x64
    Step "Building JUCE Standalone + VST3..."
    cmake --build build --config Release --target AriaBridge_Standalone
    cmake --build build --config Release --target AriaBridge_VST3
    Pop-Location
}

# Stage JUCE artifacts into dist\.
Copy-Item $juceExe "$Root\dist\Aria Bridge.exe" -Force
Copy-Item $juceVst "$Root\dist\Aria Bridge.vst3" -Recurse -Force
Done "JUCE artifacts staged to dist\."

# ── 2. Python backend ─────────────────────────────────────────────────────────
if ($SkipBackend) {
    Warn "Skipping PyInstaller (flag set)."
} elseif (Test-Path $backendExe) {
    Warn "dist\aria_backend.exe found, skipping PyInstaller. Delete it to rebuild."
} else {
    Step "Building aria_backend.exe (PyInstaller)..."
    Push-Location $Root
    python -m PyInstaller scripts\aria_backend.spec
    Pop-Location
    # Remove PyInstaller work dir (~2.4 GB, not needed after build)
    Remove-Item "$Root\build" -Recurse -Force -ErrorAction SilentlyContinue
    Done "PyInstaller work dir cleaned."
}
Done "aria_backend.exe ready in dist\."

# ── 3. Package into $RelDir (loose plugin + backend) ──────────────────────────
Step "Packaging into $RelDir ..."
Remove-Item $RelDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$RelDir\models"   | Out-Null
New-Item -ItemType Directory -Force -Path "$RelDir\feedback" | Out-Null

Copy-Item "$Root\dist\Aria Bridge.exe"  "$RelDir\Aria Bridge.exe"  -Force
Copy-Item "$Root\dist\Aria Bridge.vst3" "$RelDir\Aria Bridge.vst3" -Recurse -Force
Copy-Item $backendExe "$RelDir\aria_backend.exe" -Force
Done "Plugin + backend copied."

# Ableton MIDI Remote Scripts
Copy-Item "$Root\real-time\ableton" "$RelDir\ableton" -Recurse -Force
Done "Ableton files copied."

# README
if (Test-Path "$Root\README.md") {
    Copy-Item "$Root\README.md" "$RelDir\README.md" -Force
}

# Model - copy the newest .safetensors/.gen from repo models\
$srcModel = Get-ChildItem "$Root\models" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".safetensors",".gen" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($srcModel) {
    Copy-Item $srcModel.FullName "$RelDir\models\$($srcModel.Name)" -Force
    Done "Model copied: $($srcModel.Name)"
} else {
    Set-Content "$RelDir\models\PUT_MODEL_HERE.txt" "Download model-gen.safetensors from HuggingFace and place it in this folder."
    Warn "No model found in models\ - place .safetensors in $RelDir\models\ before running."
}

$sw.Stop()
$secs = [math]::Round($sw.Elapsed.TotalSeconds)
Write-Host ""
Write-Host "Build complete in ${secs}s" -ForegroundColor Green
Write-Host "Output: $RelDir" -ForegroundColor Green
Write-Host "Launch: open ""$RelDir\Aria Bridge.exe""" -ForegroundColor Green
