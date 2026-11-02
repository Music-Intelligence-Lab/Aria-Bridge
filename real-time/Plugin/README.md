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
- tag `8.0.7`

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

The editor matches the compact Ableton device layout: square Record / Clip / Loop / Auto toggles,
circular Play / Cancel / Sync / Commit actions, eight cyan arc knobs, and a shared vertical
generation / playback progress slider. Progress works in clip and streamed MIDI modes, including
each loop pass. Playback shows time remaining and takes priority while the next take generates.
Progress state survives closing and reopening the editor.

Track and Slot use Ableton's zero-based indices from 0 to 10. Measures ranges from 0 to 60,
where 0 keeps the full clip. Auto sends `/aria/fire` to launch a clip after writing it. Enabling Loop
also enables its required Clip output; disabling Clip turns Loop off. Sync sends all knob values
and the Clip / Loop / Auto states. Right-click any knob or button for MIDI Learn.

Control values and MIDI mappings are saved with the project and retained when reopening the editor.
Existing version 1 MIDI mappings are migrated to the same controls.

To run the Windows control regressions, stop the backend and close other bridge instances so UDP ports 9000 and 9001 are free:

```powershell
cmake -S real-time/Plugin -B real-time/Plugin/build -DARIA_BUILD_TESTS=ON
cmake --build real-time/Plugin/build --config Release --target AriaBridgeControlTests
ctest --test-dir real-time/Plugin/build -C Release --output-on-failure
```

Pass an absolute output directory to `build/Release/AriaBridgeControlTests.exe` to also generate
PNG previews at the minimum, reference, enlarged, and tall window sizes.

- The plugin currently supports:
  - `VST3`
  - `Standalone`
- The codebase includes:
  - OSC send/receive
  - MIDI learn and CC mapping
  - resizable standalone UI
- No separate DAW-specific project files are required; CMake is the source of truth.
