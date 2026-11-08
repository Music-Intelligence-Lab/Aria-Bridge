# Stream mode, and where overdubbing, layering and looping fit

Branch `jo/stream-mode` (Nov 4 to 8 2026, on top of `064f105`). New files: `core/stream_engine.py`, `modes/stream_mode.py`, the control page in `control/`, the set `ableton/Aria Duet.als`, and `launch-mac.sh` at the repo root. One change to `ableton_bridge.py`: a `stream` preset, `--mode stream`, five `--stream-*` options, and dispatch next to manual mode. Nothing else is touched, and the 58 existing tests pass.

## Review: what the bridge does today

All three front ends (the M4L device, `Plugin/`, `StudioPlugin/`) are remote controls. Each control sends one OSC message to the Python backend on port 9000, and all the musical logic lives in the backend. New behavior therefore means backend work plus new OSC addresses on the front ends.

| Use | What exists | Limit |
|---|---|---|
| Call and response | `clock` mode: N bars from you, then M bars from Aria, on Ableton's MIDI clock | Strict turn-taking. Aria never hears its own earlier turns, and nothing is generated while it plays. |
| Looping | `--loop`: each output becomes the next prompt, written down the clip column | The prompt is only the previous clip (one clip of memory), and it stops when the column fills. A self-fed model drifts into repetition (below). |
| Overdub | Clip mode can now record you over the playing output clip (commit `7cab395`) | Your take becomes the next prompt on its own. It isn't merged with what Aria was playing. |
| Layering | none | Aria only continues forward in time and can't add a part under an existing one. |
| Continuous | none | Each generation is a separate call that re-reads a MIDI file. |

Bugs found during the review (not fixed on this branch):
- **Streaming output blocks recording.** In manual mode without clips, `_handle_play_request` plays synchronously inside the command loop, so Record pressed during playback waits until the output ends. A Stop sent in that window is then dropped ("Not recording; record_stop ignored") and the session sticks in RECORDING until Cancel.
- **`top_p` is never applied.** `_sample_batch_mlx` tests `if min_p is not None`, which is true for the default `min_p = 0.0`, so it always takes the min-p branch with 0 and samples from the full distribution at the given temperature.
- **`get_inference_prompt` keeps the oldest notes.** It keeps notes from the start of the file up to `prompt_len_ms`, so a long history with a short prompt length gives the oldest music. This is harmless today because the bridge always passes the whole clip.
- `tests/test_clock_grid.py` imports a `clock_grid` module that no longer exists.

## What was measured first (01-tests/aria-sliding-window)

16k-token runs from one prompt, comparing no sliding with 1k, 4k and 7k context windows:
- **Without sliding**, it breaks 1 to 2.5k tokens past the trained 8192 positions. Predictive entropy goes to about 3.3 nats and the output thins to near silence.
- **With sliding** there is no wall, but entropy decays steadily as the window fills with Aria's own output. With a 1k window the run ends in an exact 11-note loop.

So continuous generation needs (a) a sliding window and (b) something to stop the collapse into repetition: outside input, or a temperature guard.

## Stream mode (built)

```
cd real-time
../venv/bin/python ableton_bridge.py stream --checkpoint ../models/model-gen.safetensors --device mlx
```

Or from the control page (`python3 real-time/control/aria_control.py`), mode Duet. Played live on a Yamaha CP50 in `ableton/Aria Duet.als`.

It works with the current device, with no new controls:

| Control | In stream mode |
|---|---|
| Record on, play, Record off | Records a seed phrase. Streaming starts about 1 s after Record off. |
| Playing while it streams | Merged into Aria's context at the real time you played it. No button needed. |
| Temperature, top p, min p | Applied live, per token (top p actually works here) |
| Cancel | Stops the stream. The history is kept, and the stream is saved as a two-track MIDI (you, aria) in `data/streams/`. |
| Play | Resumes from the history, or starts from nothing if there is none |

How it works (`core/stream_engine.py`):
- **One note history.** The model's KV cache holds a window of it: the header plus every note from a 5 s segment boundary onward. Aria's onsets are relative to 5 s segments, so a window that starts on a boundary is a valid piece.
- **Lookahead.** Aria generates token by token, 3 s ahead of the playhead. Notes are scheduled on the wall clock to `ARIA_OUT`.
- **Merging your playing.** Every 500 ms, your finished notes (held ones after 600 ms) are inserted at their onsets. Aria notes due within 600 ms keep playing; later notes that haven't been heard are dropped. The cache is rolled back to the change point and only the new suffix is prefilled, which takes about 20 ms. That path was checked token-for-token against a full rebuild (logits within 2e-5).
- **Listening lookahead.** While you're playing, Aria plans only 1 s ahead, so less gets thrown away and it answers sooner. 1.5 s after you stop, it goes back to 3 s.
- **Elastic clock.** If generation falls behind, as it can in very dense passages, the playhead waits for Aria instead of skipping notes.
- **Entropy guard.** While the rolling predictive entropy is below 0.8 nats, temperature is raised (by up to +0.5), against the repetition collapse.
- **Window slide.** When the cache fills, the window moves to the newest notes that fit in 75% of it and is re-prefilled (about 0.2 to 0.5 s; the lookahead covers it).

Options: `--stream-window` (2048), `--stream-lookahead` (3.0 s), `--stream-commit-ms` (600), `--stream-entropy-floor` (0.8, where 0 turns it off), `--stream-dir`.

### Test (01-tests/aria-stream-mode/stream_test.py)

Headless, on private virtual MIDI ports and OSC 9100, so a running Ableton session is untouched. The test records a 12 s nocturne seed, lets Aria stream alone for 20 s, overdubs an 8 s loud high arpeggio in a key a tritone away, then gives Aria 25 s alone, stops, resumes for 8 s, and stops.

- First Aria note 1.14 s after Record off. 711 notes in about 65 s. Longest gap 0.46 s. 1 late note, 0 skipped, 0 stalls, and no stuck notes (830 note-ons, 830 note-offs).
- Resume: first note after 0.35 s, rebuilt from a 67 s history (847 notes) in 178 ms.
- Response to the overdub: before it, Aria sat in C# (key fit 0.89) at mean pitch 57, 5 notes/s. During and after it, Aria moved up to the overdub's register (mean pitch 72 to 74), roughly tripled its density, and left C# (fit 0.18). About 10 s later it drifted back down toward Ab, the dominant of the original key, rather than taking on the overdub's G. Entropy spiked to 2.9 at the intrusion and relaxed to about 1.0 to 1.2.
- An earlier version, with no listening lookahead and no elastic clock, fell behind during the overdub and went silent. That's why both exist.

Limits: MLX only (Apple Silicon). Decoding runs at about 65 tokens/s on an M3 Max, which is about 21 notes/s, so very dense passages are near the real-time ceiling (the elastic clock covers bursts). Held notes get provisional durations. Time is free (no tempo grid).

### Memory (fixed Nov 6)

MLX keeps freed buffers in a cache for reuse, and each window slide left about 1.3 GB behind. In a live session that reached 22.9 GB after about 4.5 minutes and 15 slides, and macOS killed the process (`memorystatus: killing largest compressed process`). The engine now caps the cache at 512 MB and clears it on each slide. Memory stays at about 3 GB (100 s soak with 7 slides, 5000-token run with 5 slides). The stream also autosaves its MIDI every 30 s, and a stop by SIGINT saves it and exits.

## Next amendments (proposed, not built)

All of them run on the same engine. Only the source of the forced notes and the destination of the output change.

1. **Overdub to clips.** Write the stream (you and Aria, on separate tracks) into Ableton clips every N bars via `clip_output`, so a good passage can be kept, edited and looped in Live. Today it's saved as MIDI on Stop.
2. **Layering.** Read a clip's notes over AbletonOSC and force them into the history at their onsets (`src="layer"`) while Aria samples notes in between. Output only Aria's notes, as a new clip of the same length. Since the whole clip is known, this runs offline, faster than real time. Options: a register band (mask pitch tokens outside it, for example a bass layer below C3), and density.
3. **Looping with memory.** Loop length in bars at Live's tempo. Each pass is generated with the previous passes in context and the first pass pinned at the head of the window, so the theme stays in view, and each pass is written to the next clip. This replaces the one-clip memory of `--loop` and doesn't stop when the column fills (it replaces clips that have already played).
4. **Tempo grid (optional).** Start stream time on a downbeat and optionally quantize output onsets to Live's grid, for layering against clips. Free time stays the default for solo playing.

Front-end controls these would need (backend handlers first, then the M4L device and StudioPlugin):

| OSC | Meaning |
|---|---|
| `/aria/stream 0/1` | start or stop streaming (instead of reusing Record and Cancel) |
| `/aria/listen 0/1` | merge live input on or off (off = Aria plays alone while you practice over it) |
| `/aria/lookahead`, `/aria/window`, `/aria/entropy_floor` | the stream options above |
| `/aria/layer` | bang: generate a layer over the clip at track/slot |
| `/aria/register_low`, `/aria/register_high` | pitch band for layers and for the stream |
| `/aria/loop_bars`, `/aria/pin 0/1` | loop length; keep the first pass in context |
