# Aria Studio

A separately named VST3 MIDI control plugin with the same Aria Bridge controls and OSC protocol. Aria Studio has its own product code (`ArSt`), bundle ID (`com.aria.studio`), build directory, and install component. The existing Aria Bridge project and installed bundle are preserved.

The interface adapts the section-based layout and explicit state indicators of [Devicemeister Stepic](https://devicemeister.com/stepic/). [Steinberg Retrologue](https://www.steinberg.net/vst-instruments/retrologue/) was studied for control readability. All artwork is drawn in JUCE as vectors, with original Aria branding. Graphite panels and cyan numeric displays identify this as a digital MIDI control surface.

## Controls

- Generation: temperature, top P, min P, and tokens.
- Destination and feedback: zero-based track and slot, measures (0 keeps the full clip), and grade.
- Transport: Record, Play, Cancel, Sync, and Commit.
- Clip modes: Clip, Loop, and Auto. Loop enables Clip; disabling Clip disables Loop. Auto launches a clip after writing it.
- Activity: a segmented generation/playback progress display. Clip output disables progress, matching the backend protocol.

Drag or scroll knobs; double-click a knob to restore its default. Right-click a knob or button for MIDI Learn. Values and mappings are saved in the host project. The editor scales from 768 × 400 to 1920 × 1000. No audio synthesis or model launch is added; the existing backend must be running.

## Build on Windows

```powershell
cmake -S real-time/StudioPlugin -B real-time/StudioPlugin/build -G "Visual Studio 17 2022" -A x64 -DFETCHCONTENT_SOURCE_DIR_JUCE="C:/Code/Github Serious/Aria-Bridge/real-time/Plugin/.juce-src" -DARIA_BUILD_TESTS=ON
cmake --build real-time/StudioPlugin/build --config Release --target AriaStudio_VST3 AriaStudio_Standalone AriaStudioControlTests
```

Omit `FETCHCONTENT_SOURCE_DIR_JUCE` to fetch JUCE 8.0.7 on another machine. The VST3 bundle is `build/AriaStudio_artefacts/Release/VST3/Aria Studio.vst3`.

Run the install command from an administrator terminal:

```powershell
cmake --install real-time/StudioPlugin/build --config Release --component AriaStudioVST3
```

This installs **Aria Studio.vst3** next to **Aria Bridge.vst3**. Rescan VST3 plugins in Ableton and select Aria Studio under Aria.

## Validation and previews

Close bridge instances and stop the backend before running the control harness, which temporarily uses localhost ports 9000 and 9001:

```powershell
& '.\real-time\StudioPlugin\build\Release\AriaStudioControlTests.exe' 'C:\Code\Github Serious\Aria-Bridge\output\aria-studio'
```

The harness checks OSC routing, MIDI button press/release, shared progress, saved state, legacy mappings, and editor bounds at different sizes. It renders the real editor; the active preview uses simulated backend progress. The standalone executable is a convenient UI preview; Ableton remains the intended host.
