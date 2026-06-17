# Aria-Bridge Real-Time: Full Architecture & Code Walkthrough

---

## Table of Contents

1. [System Overview](#system-overview)
2. [ableton_bridge.py The Entry Point](#ableton_bridgepy-the-entry-point)
3. [core/aria_engine.py The Model Wrapper](#corearia_enginepy-the-model-wrapper)
4. [core/midi_buffer.py The Rolling Buffer](#coremidi_bufferpy-the-rolling-buffer)
5. [core/prompt_midi.py Buffer to MIDI File](#coreprompt_midipy-buffer-to-midi-file)
6. [modes/manual_mode.py The Recording Session](#modesmanual_modepy-the-recording-session)
7. [modes/osc_controller.py The Plugin Communication Layer](#modesosc_controllerpy-the-plugin-communication-layer)
8. [core/sampling_state.py Thread-Safe Knob State](#coresampling_statepy-thread-safe-knob-state)
9. [modes/sampling_hotkeys.py Real-Time Parameter Tweaking](#modessampling_hotkeyspy-real-time-parameter-tweaking)
10. [Full Data Flow End-to-End (Plugin / M4L Mode)](#full-data-flow-end-to-end-plugin--m4l-mode)

---

## System Overview

Aria-Bridge records human MIDI input via virtual MIDI ports, converts it into a prompt, runs it through the Aria transformer model for music generation, and plays the generated continuation back — triggered manually via the plugin or M4L device.

**Three physical components:**
- **Python backend** (`real-time/`) all logic lives here
- **VST3/standalone plugin** (`real-time/Plugin/`) JUCE C++ UI that sends OSC commands to the backend
- **Electron launcher** (`front-end/`) starts the backend process and surfaces status

**Two virtual MIDI cables** (created in loopMIDI on Windows, IAC Driver on Mac):
- `ARIA_IN` you play into this from Ableton
- `ARIA_OUT` the AI writes back out, Ableton listens on it

**How a session works at a high level:**
1. The plugin or M4L device sends `/aria/record 1` over OSC — the backend starts capturing notes from `ARIA_IN`
2. You play. The backend records every note with a wall-clock timestamp.
3. You send `/aria/record 0` — recording stops, generation starts automatically
4. The Aria model runs inference and produces a `.mid` file
5. The backend sends `/aria/status READY` back to the plugin
6. You press Play in the plugin — the backend sends the generated notes out on `ARIA_OUT`

---

## `ableton_bridge.py` The Entry Point

This is the file you run. It parses CLI args, sets up shared state, starts the OSC server, loads the model, then hands off to `ManualModeSession`.

### Presets

```python
PRESETS = {
    "plugin": {"mode": "manual", "m4l": True},
    "m4l":    {"mode": "manual", "m4l": True},
    "manual": {"mode": "manual"},
}
```

When you run `python ableton_bridge.py plugin`, it calls `parser.set_defaults(**PRESETS["plugin"])` before parsing. `plugin` and `m4l` are the same thing — manual recording mode with the OSC server enabled. `manual` is keyboard-only with no OSC.

### Device Auto-Detection

```python
def _auto_detect_device():
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try: import mlx.core; return "mlx"
    try:
        import torch
        if torch.cuda.is_available(): return "cuda"
    return "cpu"
```

On Mac with Apple Silicon it tries `mlx` first. On Windows/Linux it tries CUDA. Falls back to CPU. Called only if `--device` is not specified.

### Checkpoint Discovery

```python
def find_checkpoint(checkpoint_hint):
```

If you pass `--checkpoint path/to/model.safetensors` it tries that path directly, then relative to the script, then relative to the repo root. If you pass nothing, it scans a `models/` folder next to the executable (or script), picks the most recently modified `.safetensors` or `.gen` file. This is how the Electron launcher's "just works" experience works — it drops the model in `models/` and the backend finds it automatically.

### Shared State Initialization

Before loading the heavy model, the code creates shared objects that every module uses:

```python
sampling_state = SamplingState(temperature, top_p, min_p)  # thread-safe knob state
session_state  = SessionState(mode=args.mode)               # IDLE/RECORDING/GENERATING/READY
cmd_queue      = queue.Queue()                              # OSC/UI -> session commands
log_queue      = queue.Queue()                              # session -> UI log messages
```

These are passed by reference into every subsystem — the OSC controller, the session, and the UI panel all share the same objects. No global variables, no message bus — just shared mutable state guarded by locks.

### OSC Startup Sync

```python
osc = OscController(...)
osc.start()
startup_state = sync_state_on_startup(osc, timeout=2.0)
```

The OSC server starts listening **before** the model loads. This is intentional — it takes Max for Live (or the plugin) up to 2 seconds to push its current dial values when the backend starts. `sync_state_on_startup()` waits for all four `threading.Event` objects (`temp`, `top_p`, `min_p`, `tokens`) to be set by incoming OSC messages, or times out and uses defaults. This way, the model loads already knowing the right parameters.

### Model Loading

```python
engine = AriaEngine(
    checkpoint_path=checkpoint_path,
    device=args.device,
    config_name="medium",
)
print("STATUS:ready", flush=True)
```

`STATUS:ready` printed to stdout is how the Electron launcher knows the backend is ready. `main.js` parses lines starting with `STATUS:` from the backend's stdout.

### FeedbackManager

This class lives in `ableton_bridge.py` and wraps `DataStore`. It tracks one "draft episode" at a time:

- `record_generation()` called after each generation; creates a draft with the prompt bytes, output bytes, and sampling params. Only one draft can be pending at a time.
- `set_grade()` and `set_feedback_param()` called by OSC handlers as you move knobs. Updates in-memory values only, nothing written to disk yet.
- `commit()` called when you hit the commit button. Writes the episode to disk with the final grade and ratings. If no `current_episode_id` is set (e.g. the backend restarted), it scans for the most recent uncommitted draft as a recovery path.

---

## `core/aria_engine.py` The Model Wrapper

### `_load_model()`

```python
model_config = ModelConfig(**load_model_config(name="medium"))
model_config.set_vocab_size(AbsTokenizer().vocab_size)
self.tokenizer = AbsTokenizer()
```

Loads the model config by name ("medium"), sets the vocabulary size to match the tokenizer (~10K tokens), then branches by device:

**MLX path (Apple Silicon):**
```python
from aria.inference.model_mlx import TransformerLM
self.model.load_weights(self.checkpoint_path, strict=False)
mx.eval(self.model.parameters())  # compile the graph
```

**CUDA/CPU path:**
```python
from aria.inference.model_cuda import TransformerLM
state_dict = load_file(filename=self.checkpoint_path)  # safetensors format
self.model.load_state_dict(state_dict, strict=False)
self.model = self.model.to(self.device)
self.model.eval()
# dtype = bfloat16 if supported, else float32
```

`strict=False` means missing or unexpected keys in the checkpoint don't crash the load — useful for loading checkpoints across slightly different model versions.

### `generate()`

```python
midi_dict = MidiDict.from_midi(prompt_midi_path)
prompt = get_inference_prompt(
    midi_dict=midi_dict,
    tokenizer=self.tokenizer,
    prompt_len_ms=int(1e3 * prompt_duration_s),
)
```

`MidiDict` is ariautils' internal representation of a MIDI file. `get_inference_prompt` tokenizes it into a list of integers — the raw input to the transformer.

```python
max_new_tokens = min(512, int(horizon_s * 200))
max_new_tokens = min(8096 - len(prompt), max_new_tokens)
```

Token budget: cap at 512, also ensure prompt + generation fits within the model's 8192-token context window (the code uses 8096 as a conservative cap). `horizon_s * 200` is a rough conversion, about 200 tokens per second of music.

**Sampling:**
```python
# CUDA:
with torch.inference_mode():
    results = sample_batch(model, tokenizer, prompt, compile=False, ...)
# MLX:
results = sample_batch(model, tokenizer, prompt, ...)
```

`torch.inference_mode()` disables gradient tracking, saving memory and speeding up inference. `compile=False` skips torch.compile — it adds latency on first call and isn't worth it for one-shot real-time inference. `num_variations=1` generates exactly one output sequence.

**Detokenize:**
```python
tokenized_seq = results[0]
midi_dict = self.tokenizer.detokenize(tokenized_seq)
midi_obj = midi_dict.to_midi()
tmp = tempfile.NamedTemporaryFile(suffix='.mid', delete=False)
midi_obj.save(tmp.name)
return tmp.name
```

The token sequence goes back through the tokenizer to a `MidiDict`, then to a `mido.MidiFile`, saved to a temp `.mid` file. The caller owns that file and must delete it.

---

## `core/midi_buffer.py` The Rolling Buffer

```python
@dataclass
class TimestampedMidiMsg:
    msg_type: str       # 'note_on', 'note_off', 'control_change'
    note: Optional[int]
    velocity: Optional[int]
    control: Optional[int]
    value: Optional[int]
    timestamp: float    # time.monotonic()
    pulse: Optional[int]
```

Every message gets stamped with a wall-clock timestamp (`time.monotonic()`) at the moment it arrives. This is what allows the prompt builder to reconstruct correct relative timing when converting to MIDI.

```python
class RollingMidiBuffer:
    def __init__(self, window_seconds=4.0):
        self.buffer = deque()
        self.lock = threading.RLock()

    def add_message(self, msg_type, **kwargs):
        with self.lock:
            self.buffer.append(TimestampedMidiMsg(...))
            self._trim_old_messages()

    def _trim_old_messages(self):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        while self.buffer and self.buffer[0].timestamp < cutoff:
            self.buffer.popleft()
```

It's a `deque` under a reentrant lock. Every `add_message` also trims the front, so the buffer never holds more than `window_seconds` worth of events.

---

## `core/prompt_midi.py` Buffer to MIDI File

`buffer_to_tempfile_midi()` bridges raw captured events and a `.mid` file Aria can read.

```python
seconds_per_beat = 60.0 / current_bpm if current_bpm else 0.5
ticks_per_second = ticks_per_beat / seconds_per_beat
for msg in sorted_msgs:
    tick = int((msg.timestamp - first_timestamp) * ticks_per_second)
    delta = max(0, tick - last_tick)
```

Wall-clock timestamps are converted to MIDI ticks. BPM is inferred from note-onset timing in `infer_bpm_from_onsets()` using the median inter-onset interval, defaulting to 120 BPM if inference fails. Delta times are differences between consecutive absolute ticks.

---

## `modes/manual_mode.py` The Recording Session

This is the main session loop for plugin and M4L mode. It manages the full record → generate → play lifecycle.

### The MIDI Thread

```python
def _midi_loop(self):
    while not self.cancel_event.is_set():
        for msg in self.in_port.iter_pending():
            if not self.recording_flag.is_set():
                continue  # silently drop messages when not recording
            timestamp = time.monotonic()
            self.recorded.append(TimestampedMidiMsg(msg_type=msg.type, timestamp=timestamp, ...))
        time.sleep(0.001)
```

This runs as a background daemon thread the entire time the session is alive. It polls the port at 1KHz. The `recording_flag` threading.Event acts as a gate — messages are captured only when the flag is set, but the port is always being read so no backlog builds up.

### The Main Loop (`run()`)

```python
while not self.cancel_event.is_set():
    # 1. Wait for OSC record_start (or 'r' key)
    start_evt = threading.Event()
    while not start_evt.is_set():
        self._drain_commands(start_event=start_evt)
        time.sleep(0.05)

    # 2. Start recording
    self._begin_recording()

    # 3. Wait for OSC record_stop (or 'r' key again)
    stop_key_event = threading.Event()
    while not stop_key_event.is_set():
        self._drain_commands(stop_key_event)
        if self.max_seconds and (now - start_time) >= self.max_seconds:
            stop_key_event.set()
        time.sleep(0.02)

    # 4. Generate and await play
    self._finish_recording_and_generate()
```

The loop: wait for start → record → wait for stop → generate. Each wait drains the `command_queue` in case an OSC message arrived. This is how OSC and keyboard share the same control path — both just put items onto `command_queue`.

### Generation with Cancellation

```python
def _run_generate():
    gen_result[0] = self.aria_engine.generate(...)

gen_thread = threading.Thread(target=_run_generate, daemon=True)
gen_thread.start()

while gen_thread.is_alive():
    if self.generation_cancel_event.is_set():
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(gen_thread_id[0]),
            ctypes.py_object(_GenerationCanceled),
        )
```

Generation runs in its own thread. The main thread injects a `_GenerationCanceled` exception using `PyThreadState_SetAsyncExc` — a CPython internal that raises an exception in the target thread at its next Python bytecode. This is needed because the generation thread is deep inside C extension code (PyTorch) and can't check a Python-level event. There's also a 90-second hard timeout using the same mechanism.

### Play Gate

```python
if self.play_gate:  # always True in plugin/m4l mode
    self.pending_output_path = generated_path
    self.session_state.set_status("READY")
    self.osc_status_cb("READY")
    self._wait_for_play()
```

When `play_gate=True`, the session holds the generated MIDI and waits for an explicit play command before sending anything. `_wait_for_play()` blocks until OSC `/aria/play` is received (or `'p'` is pressed). This drives the "READY" state shown in the plugin UI — the generation is done but nothing plays until you decide to.

---

## `modes/osc_controller.py` The Plugin Communication Layer

```python
# Listens on port 9000 (incoming from plugin):
disp.map("/aria/record", self._handle_record)
disp.map("/aria/temp",   self._handle_temp)
disp.map("/aria/top_p",  self._handle_top_p)
disp.map("/aria/tokens", self._handle_tokens)
disp.map("/aria/play",   self._handle_play)
disp.map("/aria/cancel", self._handle_cancel)

# Sends on port 9001 (outgoing to plugin):
self.client.send_message("/aria/status", "GENERATING")
self.client.send_message("/aria/params", [0.9, 0.95, 0.0])
self.client.send_message("/playback_progress", 0.65)
```

The OSC server runs in its own daemon thread. All handlers write into `sampling_state`, `session_state`, or `command_queue` — they never call session methods directly. This decouples the protocol from the session logic.

```python
def _handle_record(self, addr, *args):
    flag = self._coerce_flag(args[0])  # normalize bool/int/string to 0 or 1
    # Idempotency guards:
    if flag == 1 and is_recording:
        return  # already recording — ignore duplicate start
    if flag == 0 and not is_recording:
        self.session_state.set_record_level(flag)
        return  # not recording — ignore duplicate stop
    self.command_queue.put(("record_start" if flag else "record_stop", None))
```

Record handlers are idempotent. If Ableton sends `record=1` twice due to UI bounce, the second one is silently dropped. `_coerce_flag()` normalizes `"1"`, `"true"`, `True`, `1.0` all to integer `1` — OSC messages can carry different types depending on the sender.

---

## `core/sampling_state.py` Thread-Safe Knob State

```python
class SamplingState:
    def increase_temperature(self):
        with self._lock:
            self.temperature = round(clamp(self.temperature + 0.05, 0.1, 2.0), 2)
```

Every mutation holds the lock and clamps to the valid range. `round(..., 2)` prevents floating-point drift from accumulating when hotkeys are pressed many times. `get_values()` returns a snapshot `(temp, top_p, min_p)` atomically.

`SessionState` tracks the workflow state (`IDLE` / `RECORDING` / `GENERATING` / `READY` / `PLAYING`) and whether there's pending output waiting to be played. `get_snapshot()` returns a dict copy under the lock so callers get a consistent view.

---

## `modes/sampling_hotkeys.py` Real-Time Parameter Tweaking

```python
keyboard.on_press(on_key)  # preferred: captures keys globally even in background
# fallback: msvcrt.kbhit() polling on Windows
```

| Key | Action |
|-----|--------|
| `1` | temperature − 0.05 |
| `2` | temperature + 0.05 |
| `3` | top_p − 0.01 |
| `4` | top_p + 0.01 |
| `5` | min_p − 0.01 |
| `6` | min_p + 0.01 |

All changes apply to the shared `SamplingState` object — the next generation call reads the updated values via `sampling_state.get_values()`.

---

## Full Data Flow End-to-End (Plugin / M4L Mode)

```
Plugin or M4L device (JUCE / Max)
  → sends /aria/record 1 over OSC UDP port 9000
  ↓
OscController._handle_record()
  → command_queue.put("record_start")
  ↓
ManualModeSession._drain_commands()
  → sees "record_start" → calls _begin_recording()
      → recording_flag.set()
  ↓
ManualModeSession._midi_loop() (1KHz background thread)
  → reads ARIA_IN port continuously
  → recording_flag is set → appends TimestampedMidiMsg to self.recorded

You play notes on your instrument (routed to ARIA_IN)

Plugin or M4L device
  → sends /aria/record 0 over OSC
  ↓
OscController._handle_record()
  → command_queue.put("record_stop")
  ↓
ManualModeSession._drain_commands()
  → sees "record_stop" → calls _finish_recording_and_generate()
      → recording_flag.clear()
      → buffer_to_tempfile_midi(self.recorded) → prompt.mid
          → wall-clock timestamps → MIDI ticks (BPM inferred from note onsets)
      → aria_engine.generate(prompt.mid) runs in a background thread
          → MidiDict.from_midi() → tokenize → sample_batch() → detokenize → output.mid
      → osc.send_status("GENERATING") → plugin shows spinner
      → generation finishes → osc.send_status("READY")
      → play_gate=True → _wait_for_play() blocks

Plugin or M4L device
  → sends /aria/play over OSC
  ↓
OscController._handle_play()
  → generation_cancel_event.clear() → play event set
  ↓
ManualModeSession._wait_for_play() unblocks
  → reads output.mid, iterates note events
  → sends note_on / note_off to ARIA_OUT in real time
  → sends /playback_progress updates back to plugin
  → sends /playback_stopped when done

Ableton Live
  ← receives note_on/note_off on ARIA_OUT
  ← instrument track plays the AI continuation
```
