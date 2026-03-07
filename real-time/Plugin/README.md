# Aria Bridge Plugin Build Guide

This directory contains the JUCE-based `Aria Bridge` plugin project.

## What It Builds

- `AriaBridge_VST3`
  Builds the VST3 plugin.
- `AriaBridge_Standalone`
  Builds the standalone app (`Aria Bridge.exe`).

Both targets are generated from the same JUCE project and share the same source code.

## Prerequisites

On Windows, install:

- CMake
- Visual Studio 2022 Build Tools or Visual Studio 2022 with C++ support
- A working MSVC toolchain
- Git

JUCE is fetched automatically by CMake from:

- `https://github.com/juce-framework/JUCE.git`
- tag `7.0.9`

## Project Layout

- `CMakeLists.txt`
  Main build configuration
- `Source/`
  Plugin processor/editor code
- `build/`
  Local CMake build directory

## Configure

From the repo root:

```powershell
cmake -S real-time/Plugin -B real-time/Plugin/build
```

You only need to run configure again if:

- `CMakeLists.txt` changes
- JUCE version changes
- the build directory is deleted

## Build Everything

```powershell
cmake --build real-time/Plugin/build --config Release
```

This builds both:

- VST3
- Standalone

## Build Individual Targets

Build only the standalone app:

```powershell
cmake --build real-time/Plugin/build --config Release --target AriaBridge_Standalone
```

Build only the VST3:

```powershell
cmake --build real-time/Plugin/build --config Release --target AriaBridge_VST3
```

## Output Locations

Standalone app:

`real-time\Plugin\build\AriaBridge_artefacts\Release\Standalone\Aria Bridge.exe`

VST3 plugin:

`real-time\Plugin\build\AriaBridge_artefacts\Release\VST3\Aria Bridge.vst3`

## Run the Standalone App

After building, launch:

```powershell
& ".\real-time\Plugin\build\AriaBridge_artefacts\Release\Standalone\Aria Bridge.exe"
```

## Install the VST3

System-wide VST3 install target:

```powershell
cmake --install real-time/Plugin/build --config Release --component AriaBridgeVST3
```

This installs to:

`C:\Program Files\Common Files\VST3\`

On Windows, that location usually requires Administrator privileges.

If install fails with `Permission denied`, open PowerShell as Administrator and run the same command again.

## Common Issues

### Standalone `.exe` is locked during rebuild

If build fails with a linker error like:

`LNK1104: cannot open file '...Aria Bridge.exe'`

then the standalone app is still running. Close `Aria Bridge.exe`, then rebuild:

```powershell
cmake --build real-time/Plugin/build --config Release --target AriaBridge_Standalone
```

### VST3 install fails with permission denied

That means Windows blocked writing to:

`C:\Program Files\Common Files\VST3\`

Run the install command from an elevated Administrator shell.

### First configure downloads JUCE

The first `cmake -S ... -B ...` step fetches JUCE through `FetchContent`, so it requires network access.

## Typical Workflow

1. Configure once:

```powershell
cmake -S real-time/Plugin -B real-time/Plugin/build
```

2. Build after code changes:

```powershell
cmake --build real-time/Plugin/build --config Release
```

3. Run the standalone app, or load the VST3 in a DAW.

## Notes

- The plugin currently supports:
  - `VST3`
  - `Standalone`
- The codebase includes:
  - OSC send/receive
  - MIDI learn and CC mapping
  - resizable standalone UI
- No separate DAW-specific project files are required; CMake is the source of truth.
