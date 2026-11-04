# Aria Real-Time Bridge

Real-time MIDI generation with the [Aria](https://github.com/EleutherAI/aria) music model. Record a musical prompt in Ableton Live, and the model instantly generates a continuation via virtual MIDI ports.

---

## Quick Start

### 1. Install prerequisites

<details>
<summary><strong>Windows</strong></summary>

1. **Python 3.11** — [python.org](https://www.python.org/downloads/) (check "Add Python to PATH")
2. **loopMIDI** — [download](https://www.tobias-erichsen.de/software/loopmidi.html), then create two ports: `ARIA_IN` and `ARIA_OUT`
3. **PyTorch** — GPU (recommended): `pip install torch --index-url https://download.pytorch.org/whl/cu121` · CPU only: `pip install torch`
4. **Aria package + bridge dependencies** — run both from the repo root:
   ```
   pip install -e ".[real-time]"
   cd real-time && pip install -r requirements.txt
   ```
   - `pip install -e ".[real-time]"` installs the Aria model code (`aria/inference/`, `aria/model.py`, etc.) plus MIDI deps.
   - `pip install -r requirements.txt` adds any remaining bridge-specific packages.

</details>

<details>
<summary><strong>macOS</strong></summary>

1. **Python 3.11** — `brew install python@3.11` or [python.org](https://www.python.org/downloads/)
2. **Virtual MIDI ports** — Open **Audio MIDI Setup → Window → Show MIDI Studio → IAC Driver**, enable it, add ports `ARIA_IN` and `ARIA_OUT`
3. **Xcode CLI tools** — `xcode-select --install`
4. **PyTorch** — Apple Silicon: `pip install torch` · Intel: `pip install torch`
5. **Aria package + bridge dependencies** — run both from the repo root:
   ```bash
   pip install -e ".[real-time]"
   cd real-time && pip install -r requirements.txt
   ```
   - `pip install -e ".[real-time]"` installs the Aria model code plus MIDI deps.
   - `pip install -r requirements.txt` adds any remaining packages, including `mlx` automatically on Apple Silicon.

</details>

### 2. Download the model

Get `model-gen.safetensors` from [Hugging Face](https://huggingface.co/EleutherAI/aria) and note the path — you pass it as `--checkpoint` every time.

### 3. Verify

```bash
python ableton_bridge.py --list-ports
```

You should see `ARIA_IN` and `ARIA_OUT` in the output.

---

## Choose Your Workflow

Pick the preset that matches your setup. Click for full instructions.

| Preset | What it does | Best for |
|--------|-------------|----------|
| [**plugin**](#plugin) | OSC-controlled, manual trigger | JUCE standalone plugin |
| [**m4l**](#m4l) | OSC-controlled via Max for Live device | Ableton + Max for Live |
| [**automatic**](#automatic) | Syncs to Ableton's MIDI clock, auto-generates at bar boundaries | Hands-free jamming |
| [**manual**](#manual) | Keyboard-driven, no extras | Quick testing / no Ableton |

---

### plugin

```bash
python ableton_bridge.py plugin --checkpoint <path-to-checkpoint>
```

Uses OSC for control from a JUCE standalone plugin. Mode is `manual` with OSC enabled.

<details>
<summary>With feedback collection</summary>

```bash
python ableton_bridge.py plugin --checkpoint <path> --feedback --data-dir ./data
```

</details>

---

### m4l

```bash
python ableton_bridge.py m4l --checkpoint <path-to-checkpoint>
```

<details>
<summary><strong>Full setup walkthrough (Windows)</strong></summary>

**What you need:** [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html), Ableton Live with Max for Live, the device file `real-time/ableton/Aria Bridge.amxd`.

**Step 1 — loopMIDI ports**
Open loopMIDI, add two ports: `ARIA_IN` and `ARIA_OUT`.

**Step 2 — Ableton**
1. **Preferences → MIDI**: enable `ARIA_IN` and `ARIA_OUT` (Track + Remote on both).
2. Create a **MIDI track** for your instrument. Set its MIDI output to `ARIA_IN`.
3. Create a second **MIDI track** for generated output. Set its MIDI input to `ARIA_OUT` and arm it.
4. Drag `Aria Bridge.amxd` onto any MIDI track.

**Step 3 — Start the bridge**

```bash
python ableton_bridge.py m4l --checkpoint <path-to-checkpoint>
```

You should see `OSC server listening on 127.0.0.1:9000`. The M4L device status will show **IDLE**.

**Step 4 — M4L device controls**

| Control | Action |
|---------|--------|
| **Record** | Start/stop capturing MIDI input |
| **Play** | Send generated MIDI to `ARIA_OUT` |
| **Auto** | Play the output as soon as generation finishes. With Clip on, launch the written clip |
| **Cancel** | Stops whatever is active: cancels recording, interrupts generation mid-way, stops playback, or discards a pending output and returns to ready-to-record |
| **Temp / Top-p / Min-p** knobs | Adjust sampling parameters live |
| **Tokens** knob | Generation token budget (0–2048) |

</details>

<details>
<summary>With feedback collection</summary>

```bash
python ableton_bridge.py m4l --checkpoint <path> --feedback --data-dir ./data
```

</details>

---

### automatic

```bash
python ableton_bridge.py automatic --checkpoint <path-to-checkpoint>
```

Listens to Ableton's MIDI clock on `ARIA_CLOCK` and triggers generation at bar boundaries. Requires a third loopMIDI port named `ARIA_CLOCK`.

<details>
<summary>Custom bar/measure options</summary>

```bash
python ableton_bridge.py automatic \
  --checkpoint <path> \
  --human_measures 2 \
  --gen_measures 4 \
  --temperature 0.85
```

</details>

---

### manual

```bash
python ableton_bridge.py manual --checkpoint <path-to-checkpoint>
```

Keyboard-driven, no OSC. Press `r` to toggle recording, `p` to trigger playback.

---

## Ableton clip output & infinite loop (AbletonOSC)

Instead of streaming generated notes out `ARIA_OUT`, the bridge can write them **directly into an Ableton
clip** via [AbletonOSC](https://github.com/ideoforms/AbletonOSC) — so Ableton's own engine plays them
(sample-accurate, downbeat-locked, re-triggerable).

**Setup:** install AbletonOSC and add it as a Control Surface (Preferences → MIDI), with **Input = None,
Output = None** (it speaks OSC on UDP 11000/11001, not MIDI).

**Clip output** — write each generation into a clip when generation finishes:

```bash
python ableton_bridge.py m4l --checkpoint <path> --clip --track 0 --slot 0
```

Clips are named `Output 1`, `Output 2`, … and play once, ending on a downbeat. Aria searches from
the selected Slot, wraps to the top, and uses the first empty slot. When the output track is full,
it replaces slot 0. Add `--clip-tempo` to use the generated tempo as Live's tempo.

Record captures your playing on the MIDI track directly before the output track while the current
output clip keeps playing. When you finish recording, Ableton saves the take stopped and leaves
the input track armed so you can keep playing with Monitor set to Auto. If the input track is
full, Aria still captures your input for generation. Auto queues the generated clip behind the
one playing on the output track. With one-bar launch quantization and an aligned clip end, Aria
can queue the launch for that downbeat. Otherwise it waits for Live to report the clip has stopped.
If slot 0 needs to be replaced, Aria waits for playback in that slot to stop before writing.
Play launches the saved clip at the currently selected Slot, including during generation.

**Infinite loop** — seed once, then feed each output back as the next prompt, stacking clips down the
column (Cancel stops):

```bash
python ableton_bridge.py m4l --checkpoint <path> --loop --track 0 --slot 0
```

With **fire-on-write** on (`/aria/fire 1`), clips play in order. Set Live's Global Launch Quantization
to **1 Bar** for downbeat handoffs. If the next clip is late, the current clip ends and playback
waits for the next clip.
Generation continues immediately from each finished output, independently of playback. Only the
original live input is saved on the input track; generated continuations stack on the output track.
Each run uses the available slots and can replace slot 0 once after they fill.
Press Record in the plug-in to start a new take during a run. Aria ends the previous generation
chain and Live can keep playing its current clip while you record.
The progress bar shows generation progress while waiting for playback and the time remaining
while a clip plays.

**Overdub** layers each new performance over an Aria clip and generates one new output per take.

From the repo root on Windows, run `./start_overdub.bat`. It uses the same environment, model,
and feedback settings as `start.bat`, with the second Ableton track as the default output.

```bash
python ableton_bridge.py plugin --checkpoint <path> --overdub --track 1
```

The first take uses your live MIDI. On later takes, Aria aligns your playing to the playhead of
the Aria clip playing on the output track. If no Aria clip is playing, it layers the take from
the start of the most recent output. Auto can launch each new output for you. Press **Cancel**
once to clear the backing and start a new chain with your next take. Overdub enables Clip mode
and uses Record to advance the chain.

---

## CLI Reference

<details>
<summary><strong>All flags</strong></summary>

```
python ableton_bridge.py [PRESET] [OPTIONS]
```

**MIDI ports**

| Flag | Default | Description |
|------|---------|-------------|
| `--in` | `ARIA_IN` | Input port (human performance) |
| `--out` | `ARIA_OUT` | Output port (generated MIDI) |
| `--clock_in` | `ARIA_CLOCK` | Clock input (clock mode only) |

**Sampling**

| Flag | Default | Description |
|------|---------|-------------|
| `--temperature` | `0.9` | Sampling temperature (0.1–2.0) |
| `--top_p` | `0.95` | Top-p nucleus sampling (0.1–1.0) |
| `--min_p` | `None` | Min-p sampling threshold |
| `--max-new-tokens` | `None` | Token budget override |

**Clock / automatic mode**

| Flag | Default | Description |
|------|---------|-------------|
| `--measures` | `2` | Measures per human+AI cycle |
| `--beats_per_bar` | `4` | Time signature numerator |
| `--human_measures` | `1` | Bars before generation triggers |
| `--gen_measures` | same as `--measures` | Bars for model to generate |
| `--quantize` | off | Quantize to 1/16 grid |

**Manual mode**

| Flag | Default | Description |
|------|---------|-------------|
| `--manual-key` | `r` | Toggle recording |
| `--play-key` | `p` | Trigger playback |
| `--max-seconds` | `None` | Recording timeout (seconds) |
| `--max-bars` | `None` | Recording timeout (bars) |
| `--gen_seconds` | `1.0` | Generated continuation length |

**OSC (plugin/m4l presets)**

| Flag | Default | Description |
|------|---------|-------------|
| `--m4l` | off | Enable OSC server |
| `--osc-host` | `127.0.0.1` | OSC host |
| `--osc-in-port` | `9000` | Incoming OSC port |
| `--osc-out-port` | `9001` | Outgoing OSC port |

**Clip output & infinite loop (AbletonOSC)** — requires [AbletonOSC](https://github.com/ideoforms/AbletonOSC) running as a Control Surface (Input/Output = None)

| Flag | Default | Description |
|------|---------|-------------|
| `--clip` | off | On PLAY, write the generated MIDI into an Ableton clip instead of streaming to `ARIA_OUT` |
| `--track` | `0` | Target track index (0-based) |
| `--slot` | `0` | Target clip slot index (0-based) |
| `--clip-host` | `127.0.0.1` | AbletonOSC host |
| `--clip-port` | `11000` | AbletonOSC receive port |
| `--clip-tempo` | off | Set Live's tempo to the generated tempo (plays exactly as generated; changes the global tempo) |
| `--measures-out` | `0` | Limit clip playback to this many bars (0 = full length, all notes kept) |
| `--no-clip-fire` | — | Don't auto-fire the clip after writing |
| `--no-clip-replace` | — | Skip replacing slot 0 when the output track is full |
| `--loop` | off | Infinite loop: feed each output back as the next prompt, stacking clips down the column (implies `--clip`). Record once to seed; Cancel stops |
| `--overdub` | off | Layer the live take over Aria's prior clip before generating. Implies `--clip` and is mutually exclusive with `--loop` |
| `--loop-buffer` | `4` | Clips to keep ahead of the playing slot (currently free-run) |

**Feedback**

| Flag | Default | Description |
|------|---------|-------------|
| `--feedback` | off | Enable feedback capture |
| `--data-dir` | — | Storage directory (required with `--feedback`) |

**Global**

| Flag | Description |
|------|-------------|
| `--checkpoint <path>` | Aria model checkpoint (required) |
| `--device {cuda,mlx,cpu}` | Inference device — auto-detected if omitted (mlx on Apple Silicon, else cuda, else cpu) |
| `--list-ports` | List MIDI ports and exit |

Any explicit flag overrides the preset default.

</details>

<details>
<summary><strong>Live keyboard hotkeys</strong></summary>

| Key | Action |
|-----|--------|
| `1` / `2` | Decrease / Increase temperature |
| `3` / `4` | Decrease / Increase top-p |
| `5` / `6` | Decrease / Increase min-p |

</details>

<details>
<summary><strong>OSC message reference</strong></summary>

**Incoming (client → bridge)**

| Address | Payload | Purpose |
|---------|---------|---------|
| `/aria/record` | `1` or `0` | Start / stop recording |
| `/aria/temp` | float 0.1–2.0 | Set temperature |
| `/aria/top_p` | float 0.1–1.0 | Set top-p |
| `/aria/min_p` | float 0.0–0.2 | Set min-p |
| `/aria/tokens` | int 0–2048 | Set max generation tokens |
| `/aria/play` | — | Trigger playback of pending output |
| `/aria/cancel` | — | Cancel recording, interrupt generation, discard pending output (stops the loop) |
| `/cancel_playback` | — | Stop active MIDI playback immediately |
| `/aria/ping` | — | Request status snapshot |
| `/aria/clip` | `1` or `0` | Route output to an Ableton clip (on) vs `ARIA_OUT` (off) |
| `/aria/loop` | `1` or `0` | Arm the infinite loop (needs clip on); Record starts it, off stops it |
| `/aria/track` | int | Target clip track (0-based) |
| `/aria/slot` | int | Target clip slot (0-based) |
| `/aria/fire` | `1` or `0` | Fire-on-write — auto-launch each clip as it's written |
| `/aria/measures` | int | Truncate the clip loop to N bars (0 = full; keeps all notes; ignored if N ≥ the generated length) |

**Outgoing (bridge → client)**

| Address | Payload | Purpose |
|---------|---------|---------|
| `/aria/status` | string | `IDLE`, `RECORDING`, `GENERATING`, `READY` |
| `/aria/params` | `[temp, top_p, min_p]` | Current sampling parameters |
| `/aria/log` | string | Event log message |
| `/generation_start` | — | Model has started generating |
| `/generation_progress` | int 0–100 | Generation progress (drive a M4L slider, range 0–100) |
| `/generation_done` | — | Generation finished or was canceled |
| `/playback_duration` | float (seconds) | Total duration of the MIDI about to play |
| `/playback_progress` | float 0.0–1.0 | Playback position |
| `/playback_stopped` | — | Playback ended or was canceled |

</details>

---

## Troubleshooting

<details>
<summary><strong>Checkpoint not found</strong></summary>

Use a relative path from the `real-time/` folder or an absolute path:

```bash
python ableton_bridge.py plugin --checkpoint ../models/model-gen.safetensors
python ableton_bridge.py plugin --checkpoint C:/Aria/models/model-gen.safetensors
```

</details>

<details>
<summary><strong>MIDI port not found</strong></summary>

loopMIDI appends a numeric suffix to port names (e.g. `ARIA_IN 3`). The bridge uses case-insensitive prefix matching, so `ARIA_IN` matches `ARIA_IN 3` automatically. Always pass the base name (`ARIA_IN`, `ARIA_OUT`). Run `python ableton_bridge.py --list-ports` to see what ports are available.

</details>

<details>
<summary><strong>Generation is slow</strong></summary>

Use CUDA: `--device cuda`. Check GPU: `python -c "import torch; print(torch.cuda.is_available())"`.

</details>

<details>
<summary><strong>No MIDI being captured</strong></summary>

Verify in Ableton that the MIDI track output is routed to `ARIA_IN` and no other app has an exclusive lock on the port.

</details>

<details>
<summary><strong>OSC not working</strong></summary>

Run `python tools/osc_sanity.py --in-port 9000 --out-port 9001` and check your firewall allows UDP on those ports.

</details>

---

## Project Structure

<details>
<summary>Expand</summary>

```
real-time/
├── ableton_bridge.py       # Main entry point
├── requirements.txt
├── README.md
├── docs/
│   ├── ARCHITECTURE.md     # Full code walkthrough + data flow
│   ├── QUICKSTART.md       # Quick start guide
│   └── TOKENS_AND_CLOCK.md # Tokens knob + clock-mode explainer
├── core/
│   ├── aria_engine.py      # Model inference wrapper
│   ├── bridge_engine.py    # Core orchestration (clock mode)
│   ├── midi_buffer.py      # Rolling MIDI message buffer
│   ├── prompt_midi.py      # Buffer-to-MIDI conversion
│   ├── sampling_state.py   # Thread-safe parameter state
│   ├── tempo_tracker.py    # MIDI clock tempo tracking
│   └── datastore.py        # Feedback dataset storage
├── modes/
│   ├── clock_mode.py       # Pulse-based bar detection
│   ├── manual_mode.py      # Keyboard-driven mode
│   ├── osc_controller.py   # OSC server for M4L
│   └── sampling_hotkeys.py # Live parameter tweaks
├── ui/
│   └── ui_panel.py         # Optional Tkinter UI
├── tools/
│   ├── calibrate.py        # MIDI latency calibration
│   ├── count_tokens.py     # Prompt token-length tester
│   ├── sanity.py           # Testing & validation
│   └── osc_sanity.py       # OSC debugging
├── ableton/
│   ├── aria.als            # Ableton Live set
│   └── Aria Bridge.amxd    # Max for Live device
└── tests/
```

</details>

---

## Development

```bash
pytest tests/
```

To add a new mode: create a file in `modes/`, implement a class accepting `aria_engine`, `midi_buffer`, and shared state, then import it in `ableton_bridge.py` and optionally add a preset entry.

---

## License

[MIT](LICENSE)
