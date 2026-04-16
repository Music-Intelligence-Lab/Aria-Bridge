# Aria Bridge

Aria Bridge is a real-time generative MIDI system that connects the Aria music language model to any DAW via loopMIDI, controlled through a standalone desktop application.

## Launcher (Recommended)

The easiest way to run Aria Bridge:

1. Install Node.js (https://nodejs.org)
2. Run `cd front-end && npm install`
3. Run `npm start`
4. Select your mode and follow the on-screen instructions

No terminal knowledge required.

---

## Requirements

- Windows 10/11 64-bit
- NVIDIA GPU recommended (CUDA 12.1) for real-time performance
- CPU inference supported but slower
- loopMIDI (free) for virtual MIDI ports
- Ableton Live, Reaper, or any DAW that supports loopMIDI

## Installation

1. Download the latest release zip from the Releases page.
2. Unzip to a permanent location, for example `C:\Aria Bridge\`.
3. Run `install.bat` to install Python 3.11 and all dependencies.
4. Download the Aria model from HuggingFace:
   `https://huggingface.co/loubb/aria-medium-base/resolve/main/model-gen.safetensors?download=true`
   File needed: `model-gen.safetensors` or any equivalent model
5. Place `model-gen.safetensors` in the `models\` folder.
6. Install loopMIDI:
   `https://www.tobias-erichsen.de/software/loopmidi.html`
7. In loopMIDI, create two ports named exactly: `ARIA_IN` and `ARIA_OUT`.

## Running

1. Open `Aria Bridge.exe`.
2. The backend starts automatically. Status shows `IDLE` when ready.
3. In your DAW, route a MIDI track output to `ARIA_IN`.
4. Route a second MIDI track input from `ARIA_OUT` to an instrument.

## Controls

- `temp` / `top_p` / `min_p` / `tokens`: generation parameters
- `record`: start/stop recording your MIDI input
- `play`: play back the generated output once it is ready
- `cancel`: stops whatever is currently happening — cancels an active recording, interrupts generation mid-way, stops playback, or discards a pending output and returns to the record prompt
- `commit`: save the current generation with feedback ratings
- `sync`: resync all parameters to the backend
- `coherence` / `taste` / `repetition` / `continuity` / `grade`: rate the generation (1-5) before committing

## Progress Display

Two progress indicators appear in the plugin during activity:

- **Generating bar**: shown while the model is running, displaying elapsed seconds. Pressing cancel interrupts generation immediately.
- **Playback bar**: shown during MIDI playback with a `M:SS left` countdown. Pressing cancel stops the MIDI feed.

## Feedback Data

Committed episodes are saved to the `data\` folder alongside the exe. Each episode contains `prompt.mid`, `output.mid`, and `meta.json`.

## Troubleshooting

- Status shows `DISCONNECTED`: make sure `install.bat` completed successfully and `start.bat` is next to the exe.
- No MIDI captured: check loopMIDI ports are named `ARIA_IN` / `ARIA_OUT` and your DAW track is routed correctly.
- Generation is slow: ensure PyTorch CUDA is installed and your GPU is recognized. `install.bat` handles this automatically.

## License

MIT
