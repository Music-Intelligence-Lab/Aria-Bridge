"""Manual, keyboard-driven recording mode for the Aria real-time bridge.

This mode bypasses Ableton's MIDI clock and instead starts/stops recording
based on a user-selected computer keyboard key. The recorded MIDI is converted
into a prompt for Aria with timing preserved from the captured deltas.
"""

from __future__ import annotations

import ctypes
import logging
import os
import statistics
import threading
import time
import queue
from typing import Iterable, List, Optional, Tuple
from pathlib import Path

from core.midi_buffer import TimestampedMidiMsg
from core.prompt_midi import buffer_to_tempfile_midi

logger = logging.getLogger(__name__)


class _GenerationCanceled(Exception):
    pass


class KeyboardToggle:
    """Minimal keyboard listener that works on Windows-first, with fallbacks."""

    def __init__(self, key: str = "r", osc_driven: bool = False):
        self.key = key
        # When recording is driven over OSC (m4l/UI), the keyboard fallback should
        # idle instead of blocking on stdin / echoing a "press Enter" prompt.
        self.osc_driven = osc_driven
        self.backend = self._detect_backend()

    def _detect_backend(self) -> str:
        try:
            import keyboard  # type: ignore  # noqa: F401
            return "keyboard"
        except Exception:
            if os.name == "nt":
                try:
                    import msvcrt  # type: ignore  # noqa: F401
                    return "msvcrt"
                except Exception:
                    return "stdin"
            return "stdin"

    def wait_for_press(self, message: str, cancel_event: threading.Event) -> bool:
        print(message)
        try:
            if self.backend == "keyboard":
                import keyboard  # type: ignore
                pressed = threading.Event()
                def _on_key(_):
                    pressed.set()
                hook = keyboard.on_press_key(self.key, _on_key, suppress=False)
                try:
                    while not cancel_event.is_set() and not pressed.is_set():
                        time.sleep(0.05)
                finally:
                    keyboard.unhook(hook)
                return pressed.is_set()
            if self.backend == "msvcrt":
                import msvcrt  # type: ignore
                while not cancel_event.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch.lower() == self.key.lower():
                            return True
                    time.sleep(0.05)
                return False
            # stdin fallback (macOS/Linux without the `keyboard` module).
            if self.osc_driven:
                # OSC drives start/stop; just idle until canceled. No second print,
                # no blocking stdin read, no leaked input() thread per record cycle.
                while not cancel_event.is_set():
                    time.sleep(0.1)
                return False
            if cancel_event.is_set():
                return False
            input("(press Enter to continue)")
            return True
        except (KeyboardInterrupt, EOFError):
            cancel_event.set()
            return False


def infer_bpm_from_onsets(messages: Iterable[TimestampedMidiMsg]) -> Optional[float]:
    onsets = [m.timestamp for m in messages if m.msg_type == "note_on" and m.velocity and m.velocity > 0]
    if len(onsets) < 2:
        return None
    deltas = [b - a for a, b in zip(onsets[:-1], onsets[1:]) if b > a]
    if not deltas:
        return None
    bpm = 60.0 / statistics.median(deltas)
    return max(30.0, min(bpm, 240.0))


def retime_midi_to_120bpm(midi_path: str) -> bytes:
    """Return the MIDI at `midi_path` rewritten at a fixed 120 BPM, preserving wall-clock timing.

    The saved feedback prompt is written at an inferred BPM (`infer_bpm_from_onsets` over-estimates
    when playing denser than one note per beat), so it half-tempos when a DAW re-grids it onto a
    120 project grid. Aria reads absolute-ms timing and ignores tempo, so this only normalizes the
    *stored* prompt's audition to match output.mid's canonical 120 BPM. Ticks are rescaled by
    old_tempo/new_tempo, so the real (wall-clock) duration is unchanged — only the beat grid moves.
    """
    import io
    import mido

    mid = mido.MidiFile(midi_path)
    new_tempo = 500_000  # microseconds per beat == 120 BPM
    old_tempo = next(
        (m.tempo for tr in mid.tracks for m in tr if m.type == "set_tempo"),
        new_tempo,
    )
    if old_tempo == new_tempo:
        return Path(midi_path).read_bytes()
    scale = old_tempo / new_tempo

    out = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
    for tr in mid.tracks:
        new_tr = mido.MidiTrack()
        out.tracks.append(new_tr)
        abs_old = abs_new_prev = 0
        for msg in tr:
            abs_old += msg.time
            abs_new = round(abs_old * scale)
            new_msg = msg.copy(time=max(0, abs_new - abs_new_prev))
            abs_new_prev = abs_new
            if new_msg.type == "set_tempo":
                new_msg = new_msg.copy(tempo=new_tempo)
            new_tr.append(new_msg)

    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()


def _play_midi_file(midi_path: str, out_port, progress_cb=None, duration_cb=None, stop_event=None) -> Tuple[int, float]:
    import mido
    mid = mido.MidiFile(midi_path)
    total_time = mid.length
    if duration_cb and total_time > 0:
        duration_cb(total_time)
    if total_time > 0:
        print(f"STATUS:play_duration:{total_time:.3f}", flush=True)
    sent = 0
    elapsed = 0.0
    last_report = -1.0
    print("STATUS:playing:0.0", flush=True)
    stopped_printed = False
    for msg in mid.play():
        if stop_event and stop_event.is_set():
            logger.info("[playback] Stop event received — MIDI feed halted")
            print("[playback] Stop event received — MIDI feed halted")
            print("STATUS:stopped", flush=True)
            stopped_printed = True
            break
        elapsed += msg.time
        if hasattr(msg, "type") and msg.type in ("note_on", "note_off", "control_change"):
            out_port.send(msg)
            sent += 1
        if total_time > 0 and elapsed - last_report >= 0.05:
            progress = min(1.0, elapsed / total_time)
            if progress_cb:
                progress_cb(progress)
            print(f"STATUS:playing:{progress:.3f}", flush=True)
            last_report = elapsed
    if not stopped_printed:
        print("STATUS:stopped", flush=True)
    return sent, total_time


class ManualModeSession:
    """Keyboard-driven record -> prompt -> generate -> play pipeline."""

    def __init__(
        self,
        in_port_name: str,
        out_port_name: str,
        aria_engine,
        manual_key: str = "r",
        ticks_per_beat: int = 480,
        gen_seconds: float = 1.0,
        max_seconds: Optional[float] = None,
        max_bars: Optional[int] = None,
        beats_per_bar: int = 4,
        max_new_tokens: Optional[int] = None,
        play_key: Optional[str] = None,
        sampling_state=None,
        command_queue: Optional[queue.Queue] = None,
        log_queue: Optional[queue.Queue] = None,
        session_state=None,
        osc_status_cb=None,
        osc_log_cb=None,
        osc_params_cb=None,
        osc_generation_start_cb=None,
        osc_generation_done_cb=None,
        osc_generation_progress_cb=None,
        osc_playback_progress_cb=None,
        osc_playback_stopped_cb=None,
        osc_playback_duration_cb=None,
        play_gate: bool = False,
        feedback_manager=None,
        clip_output: bool = False,
        clip_track: int = 0,
        clip_slot: int = 0,
        clip_host: str = "127.0.0.1",
        clip_port: int = 11000,
        clip_replace: bool = True,
        clip_fire: bool = True,
        clip_set_tempo: bool = False,
        clip_auto_advance: bool = False,
        clip_measures: int = 0,
        loop_mode: bool = False,
        loop_buffer: int = 4,
        loop_max_slot: int = 7,
        record_clip: bool = False,
        variants: int = 1,
    ):
        self.in_port_name = in_port_name
        self.out_port_name = out_port_name
        self.aria_engine = aria_engine
        self.manual_key = manual_key
        self.ticks_per_beat = ticks_per_beat
        self.gen_seconds = gen_seconds
        self.max_seconds = max_seconds
        self.max_bars = max_bars
        self.beats_per_bar = beats_per_bar
        self.max_new_tokens = max_new_tokens
        # Variant collection: >1 turns on Option-A "each Play press generates + plays the next
        # take of the same prompt", each graded/committed as its own episode. 1 = normal flow.
        self.variants = max(1, int(variants or 1))
        self._variant_group_id = None
        # Default play key to 'p' so manual playback always available (even if flag omitted).
        self.play_key = play_key or "p"
        self.play_toggle = KeyboardToggle(self.play_key, osc_driven=bool(command_queue))
        self.sampling_state = sampling_state
        self.command_queue = command_queue
        self.log_queue = log_queue
        self.session_state = session_state
        self.osc_status_cb = osc_status_cb
        self.osc_log_cb = osc_log_cb
        self.osc_params_cb = osc_params_cb
        self.osc_generation_start_cb = osc_generation_start_cb
        self.osc_generation_done_cb = osc_generation_done_cb
        self.osc_generation_progress_cb = osc_generation_progress_cb
        self.osc_playback_progress_cb = osc_playback_progress_cb
        self.osc_playback_stopped_cb = osc_playback_stopped_cb
        self.osc_playback_duration_cb = osc_playback_duration_cb
        # Gate playback to explicit PLAY command/key to keep manual + OSC paths consistent.
        self.play_gate = True
        self.feedback_manager = feedback_manager
        # Opt-in: write generated MIDI into an Ableton clip (AbletonOSC) instead of
        # streaming it out ARIA_OUT. Default off — leaves the normal path untouched.
        self.clip_output = clip_output
        self.clip_track = clip_track
        self.clip_slot = clip_slot
        self.clip_host = clip_host
        self.clip_port = clip_port
        self.clip_replace = clip_replace
        self.clip_fire = clip_fire
        self.clip_set_tempo = clip_set_tempo
        self.clip_auto_advance = clip_auto_advance
        self.clip_measures = clip_measures   # 0 = full clip; N = truncate loop to N bars
        self.loop_mode = loop_mode
        self.loop_buffer = loop_buffer
        self.loop_max_slot = loop_max_slot
        # Opt-in: on Record, drive Ableton to natively record your playing into a clip
        # on the track before the output track (clip_track - 1), same slot (one slot
        # under if occupied). The bridge still buffers ARIA_IN for the prompt.
        self.record_clip = record_clip
        self._rec_input_track = None   # track/slot of the in-progress native recording
        self._rec_input_slot = None
        self.loop_running = False
        self.clip_index = 0  # increments per written clip -> "Output 1", "Output 2", ...
        self.pending_prompt_path = None  # recorded prompt kept to write as an input clip
        self.pending_output_path = None
        self._msg_count = 0
        self._note_on_count = 0
        self.state = "IDLE"

        self.cancel_event = threading.Event()
        self.playback_cancel_event = threading.Event()
        self.generation_cancel_event = threading.Event()
        self.skip_pending_event = threading.Event()
        self.recording_flag = threading.Event()
        self.recorded: List[TimestampedMidiMsg] = []
        self.start_time: Optional[float] = None
        self.stop_time: Optional[float] = None

        self.toggle = KeyboardToggle(manual_key, osc_driven=bool(command_queue))
        self.in_port = None
        self.out_port = None
        self.midi_thread = None

    def _resolve_max_tokens(self) -> Optional[int]:
        if self.session_state:
            val = self.session_state.get_max_tokens()
            if val is not None:
                return int(val)
        return self.max_new_tokens

    @staticmethod
    def _resolve_port(name: str, kind: str) -> str:
        """Return the first port matching 'name' (case-insensitive). Prefers a prefix
        match (Windows loopMIDI 'ARIA_IN 3') but falls back to a substring match so
        macOS IAC names ('IAC Driver ARIA_IN') also resolve."""
        import mido
        available = mido.get_input_names() if kind == "input" else mido.get_output_names()
        n = name.lower()
        matched = [p for p in available if p.lower().startswith(n)] or \
                  [p for p in available if n in p.lower()]
        if matched:
            return matched[0]
        raise RuntimeError(
            f"Could not find a MIDI {kind} port matching '{name}'. "
            f"On Windows make sure loopMIDI is running; on macOS enable the IAC Driver "
            f"and add the port in Audio MIDI Setup. Available ports: {available}"
        )

    def _open_ports(self) -> None:
        import mido
        in_name = self._resolve_port(self.in_port_name, "input")
        out_name = self._resolve_port(self.out_port_name, "output")
        self.in_port = mido.open_input(in_name)
        self.out_port = mido.open_output(out_name)
        logger.info(f"Manual mode ports opened: IN={in_name}, OUT={out_name}")
        print("STATUS:ports_ready", flush=True)

    def _close_ports(self) -> None:
        try:
            if self.in_port:
                self.in_port.close()
        finally:
            self.in_port = None
        try:
            if self.out_port:
                self.out_port.close()
        finally:
            self.out_port = None

    def _midi_loop(self) -> None:
        try:
            while not self.cancel_event.is_set():
                if self.in_port is None:
                    break
                for msg in self.in_port.iter_pending():
                    if not self.recording_flag.is_set():
                        continue
                    if msg.type not in ("note_on", "note_off", "control_change"):
                        continue
                    timestamp = time.monotonic()
                    data = {"msg_type": msg.type, "timestamp": timestamp, "pulse": None}
                    if hasattr(msg, "note"):
                        data["note"] = msg.note
                    if hasattr(msg, "velocity"):
                        data["velocity"] = msg.velocity
                    if msg.type == "control_change":
                        data["control"] = msg.control
                        data["value"] = msg.value
                    self.recorded.append(TimestampedMidiMsg(**data))
                    self._msg_count += 1
                    if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                        self._note_on_count += 1
                time.sleep(0.001)
        except Exception as e:
            logger.exception(f"Manual MIDI loop error: {e}")
            self.cancel_event.set()

    def _start_midi_thread(self) -> None:
        self.midi_thread = threading.Thread(target=self._midi_loop, daemon=True)
        self.midi_thread.start()

    def _drain_commands(
        self,
        stop_key_event: Optional[threading.Event] = None,
        start_event: Optional[threading.Event] = None,
        play_event: Optional[threading.Event] = None,
        play_callback=None,
    ):
        if not self.command_queue:
            return
        try:
            while True:
                cmd, payload = self.command_queue.get_nowait()
                if cmd == "toggle_record":
                    if self.recording_flag.is_set():
                        if stop_key_event:
                            stop_key_event.set()
                    else:
                        if start_event:
                            start_event.set()
                        else:
                            self._log_ui("Record start ignored (not armed)")
                elif cmd == "record":
                    if payload:
                        if not self.recording_flag.is_set():
                            if start_event:
                                start_event.set()
                            else:
                                self._log_ui("Record start ignored (not armed)")
                    else:
                        if stop_key_event:
                            stop_key_event.set()
                elif cmd == "record_start":
                    if self.recording_flag.is_set():
                        self._log_ui("Already recording; record_start ignored")
                    else:
                        if start_event:
                            start_event.set()
                        else:
                            self._log_ui("Record start ignored (not armed)")
                elif cmd == "record_stop":
                    if not self.recording_flag.is_set():
                        self._log_ui("Not recording; record_stop ignored")
                    elif stop_key_event:
                        stop_key_event.set()
                elif cmd == "cancel":
                    if stop_key_event:
                        stop_key_event.set()
                    self.generation_cancel_event.set()
                    self.skip_pending_event.set()
                    self.recorded.clear()
                    self._log_ui("Canceled")
                    if self.session_state:
                        self.session_state.set_status("IDLE")
                        self.session_state.has_pending_output = False
                elif cmd == "play_last":
                    if self.session_state and self.session_state.last_output_path and self.out_port:
                        self._log_ui("Playing last output (UI)")
                        _play_midi_file(self.session_state.last_output_path, self.out_port)
                elif cmd == "cancel_playback":
                    self.playback_cancel_event.set()
                    self._log_ui("Playback canceled")
                elif cmd == "play":
                    if play_event:
                        play_event.set()
                    elif play_callback:
                        play_callback()
                    else:
                        self._handle_play_request()
                self.command_queue.task_done()
        except queue.Empty:
            pass

    def _start_immediate_record(self, stop_key_event: Optional[threading.Event] = None):
        if self.recording_flag.is_set():
            return
        self.recorded.clear()
        self._msg_count = 0
        self._note_on_count = 0
        self.recording_flag.set()
        self.start_time = time.monotonic()
        if self.session_state:
            self.session_state.set_status("RECORDING")
        self._log_ui("Recording started (UI)")
        if stop_key_event is None:
            return

        def _wait_stop():
            while not self.cancel_event.is_set():
                try:
                    cmd, _ = self.command_queue.get(timeout=0.1)
                    if cmd == "toggle_record":
                        stop_key_event.set()
                        break
                except queue.Empty:
                    continue
        threading.Thread(target=_wait_stop, daemon=True).start()

    def _handle_play_request(self) -> bool:
        """Play pending output in a single shared path (keyboard + OSC)."""
        if not self.play_gate:
            return False
        path = self.pending_output_path or (self.session_state.last_output_path if self.session_state else None)
        if not path:
            self._log_ui("No pending output to play")
            logger.info("[manual] Play requested but no pending output.")
            return False
        # Clip output (AbletonOSC): write the generated MIDI into an Ableton clip
        # instead of streaming it out ARIA_OUT. Opt-in via --clip.
        if self.clip_output:
            from core.clip_output import send_midi_to_clip
            self.clip_index += 1
            clip_name = f"Output {self.clip_index}"
            self._log_ui(f"Play -> writing clip '{clip_name}' via AbletonOSC")
            n, used_slot = send_midi_to_clip(
                path, track=self.clip_track, slot=self.clip_slot,
                host=self.clip_host, port=self.clip_port,
                replace=self.clip_replace, fire=self.clip_fire,
                beats_per_bar=self.beats_per_bar, set_tempo=self.clip_set_tempo,
                name=clip_name, auto_advance=self.clip_auto_advance,
                measures_out=self.clip_measures,
            )
            msg = (f"Wrote clip '{clip_name}' ({n} notes) -> track {self.clip_track} slot {used_slot}"
                   if n >= 0 else "Clip write failed (is AbletonOSC running?)")
            logger.info(f"[manual] {msg}")
            self._log_ui(msg)
            if self.osc_log_cb:
                self.osc_log_cb(msg)
            # Pair: write the recorded input to the track before, at the same slot.
            # Skipped when native record is active — Ableton already recorded the take
            # there, so reconstructing it from the buffer would clobber it.
            input_track = self.clip_track - 1
            if n >= 0 and input_track >= 0 and self.pending_prompt_path and not self._native_record_active():
                try:
                    send_midi_to_clip(
                        self.pending_prompt_path, track=input_track, slot=used_slot,
                        host=self.clip_host, port=self.clip_port,
                        replace=self.clip_replace, fire=False,
                        beats_per_bar=self.beats_per_bar, set_tempo=False,
                        name=f"Input {self.clip_index}", auto_advance=False,
                        measures_out=self.clip_measures,
                    )
                except Exception:
                    logger.exception("[clip] input clip write failed")
            if self.pending_prompt_path:
                try:
                    os.unlink(self.pending_prompt_path)
                except Exception:
                    pass
                self.pending_prompt_path = None
            if self.osc_playback_stopped_cb:
                self.osc_playback_stopped_cb()
            try:
                os.unlink(path)
            except Exception:
                pass
            self.pending_output_path = None
            if self.session_state:
                self.session_state.has_pending_output = False
                self.session_state.set_status("IDLE")
                self.session_state.set_last_output(None)
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            return True
        if not self.out_port:
            logger.warning("[manual] Play requested but output port is unavailable.")
            return False
        self._log_ui("Play requested")
        self.playback_cancel_event.clear()
        sent, total = _play_midi_file(path, self.out_port, progress_cb=self.osc_playback_progress_cb, duration_cb=self.osc_playback_duration_cb, stop_event=self.playback_cancel_event)
        if self.osc_playback_stopped_cb:
            self.osc_playback_stopped_cb()
        logger.info(f"[manual] Played pending MIDI ({sent} msgs, {total:.2f}s)")
        if self.osc_log_cb:
            self.osc_log_cb(f"Played pending MIDI ({sent} msgs, {total:.2f}s)")
        try:
            os.unlink(path)
        except Exception:
            pass
        self.pending_output_path = None
        if self.session_state:
            self.session_state.has_pending_output = False
            self.session_state.set_status("IDLE")
            self.session_state.set_last_output(None)
        if self.osc_status_cb:
            self.osc_status_cb("IDLE")
        return True

    def _wait_for_play(self):
        """Block until either manual 'p' or OSC /aria/play arrives, then play once."""
        play_event = threading.Event()

        def _wait_keyboard_play():
            prompt = f"Output ready. Press '{self.play_key}' to play."
            if self.play_toggle.wait_for_press(prompt, self.cancel_event):
                play_event.set()

        threading.Thread(target=_wait_keyboard_play, daemon=True).start()

        while not self.cancel_event.is_set() and not self.skip_pending_event.is_set():
            self._drain_commands(play_event=play_event)
            if play_event.is_set():
                self._handle_play_request()
                break
            time.sleep(0.05)

        if self.skip_pending_event.is_set():
            logger.info("[manual] Pending output canceled — returning to record")
            print("[manual] Pending output canceled — returning to record")
            self.skip_pending_event.clear()
            if self.pending_output_path:
                try:
                    os.unlink(self.pending_output_path)
                except Exception:
                    pass
            self.pending_output_path = None
            if self.session_state:
                self.session_state.has_pending_output = False
                self.session_state.set_status("IDLE")
                self.session_state.set_last_output(None)
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            self._log_ui("Pending output discarded — ready to record")

        # Ensure we leave READY state if globally canceled.
        if self.cancel_event.is_set() and self.session_state:
            self.session_state.has_pending_output = False
            self.session_state.set_status("IDLE")
            self.session_state.set_last_output(None)
            self.pending_output_path = None

    def _begin_recording(self):
        """Shared start logic for keyboard + OSC."""
        # Starting a new take supersedes any un-graded previous draft: delete it so it
        # never enters the training set and the new session isn't blocked/overwritten.
        if self.feedback_manager:
            self.feedback_manager.discard_pending()
        self.recorded.clear()
        self._msg_count = 0
        self._note_on_count = 0
        self.skip_pending_event.clear()
        self.playback_cancel_event.clear()
        self.recording_flag.set()
        self.start_time = time.monotonic()
        logger.info(f"[manual] Recording started at {self.start_time:.3f}")
        self._log_ui("Recording started")
        if self.session_state:
            self.session_state.set_status("RECORDING")
            self.session_state.set_recording(True)
        if self.osc_status_cb:
            self.osc_status_cb("RECORDING")
        self._start_native_clip_record()

    def _native_record_active(self):
        """Whether Record should drive an Ableton clip recording.

        Default: on whenever clip output is on (no extra M4L button needed) — the
        recorded take pairs with the generated output. ``--record-clip`` forces it on
        even without clip output. A future packaged app can expose an explicit toggle.
        """
        return bool(self.record_clip or self.clip_output)

    def _start_native_clip_record(self):
        """If enabled, arm+fire a clip on clip_track-1 so Live records the live take."""
        if not self._native_record_active():
            return
        input_track = self.clip_track - 1
        if input_track < 0:
            return
        try:
            from core.clip_output import start_clip_record
            used = start_clip_record(
                host=self.clip_host, port=self.clip_port,
                track=input_track, slot=self.clip_slot, auto_advance=True,
            )
            if used >= 0:
                self._rec_input_track = input_track
                self._rec_input_slot = used
                self._log_ui(f"Ableton recording -> track {input_track} slot {used}")
        except Exception:
            logger.exception("[record-clip] failed to start Ableton recording")

    def _stop_native_clip_record(self):
        """Stop the in-progress native recording (finalises the take) and disarm."""
        if self._rec_input_track is None:
            return
        try:
            from core.clip_output import stop_clip_record
            stop_clip_record(host=self.clip_host, port=self.clip_port,
                             track=self._rec_input_track)
            self._log_ui(f"Ableton recording stopped -> track {self._rec_input_track} slot {self._rec_input_slot}")
        except Exception:
            logger.exception("[record-clip] failed to stop Ableton recording")
        finally:
            self._rec_input_track = None
            self._rec_input_slot = None

    # --- Live control setters (wired to OSC handlers; safe to call from the OSC thread) ---
    def set_record_clip(self, on):
        self.record_clip = bool(on)
        self._log_ui(f"Record->Ableton clip {'ON' if self.record_clip else 'OFF'}")

    def set_clip_output(self, on):
        on = bool(on)
        self.clip_output = on
        if not on and self.loop_mode:
            self.loop_mode = False           # loop can't run without clip output
            if self.loop_running:
                self.generation_cancel_event.set()
        self._log_ui(f"Clip output {'ON' if on else 'OFF'}")

    def set_loop_mode(self, on):
        on = bool(on)
        if on and not self.clip_output:
            self._log_ui("Loop needs Clip ON — ignored")
            logger.info("[ctrl] loop-on ignored (clip output off)")
            return
        self.loop_mode = on
        if not on and self.loop_running:
            self.generation_cancel_event.set()   # stop a running loop
        self._log_ui(f"Loop {'ARMED (press Record to start)' if on else 'OFF'}")

    def set_clip_track(self, n):
        try:
            self.clip_track = int(n)
        except (TypeError, ValueError):
            return
        self._log_ui(f"Clip track -> {self.clip_track}")

    def set_clip_slot(self, n):
        try:
            self.clip_slot = int(n)
        except (TypeError, ValueError):
            return
        self._log_ui(f"Clip slot -> {self.clip_slot}")

    def set_clip_fire(self, on):
        self.clip_fire = bool(on)
        self._log_ui(f"Fire-on-write {'ON' if self.clip_fire else 'OFF'}")

    def set_clip_measures(self, n):
        try:
            self.clip_measures = max(0, int(round(float(n))))
        except (TypeError, ValueError):
            return
        self._log_ui(f"Measures out -> {self.clip_measures if self.clip_measures else 'full'}")

    def _generate_cancellable(self, **gen_kwargs):
        """Run aria_engine.generate in a thread so /aria/cancel interrupts it
        mid-generation (same mechanism as the normal record->generate path).
        Returns the generated path, or None if canceled/failed."""
        result = [None]
        tid = [None]

        def _run():
            tid[0] = threading.current_thread().ident
            try:
                result[0] = self.aria_engine.generate(**gen_kwargs)
            except _GenerationCanceled:
                logger.info("[loop] generation interrupted mid-token")
            except Exception as e:
                logger.exception(f"[loop] generation error: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        while t.is_alive():
            if (self.generation_cancel_event.is_set() or self.cancel_event.is_set()
                    or self.skip_pending_event.is_set()) and tid[0] is not None:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(tid[0]), ctypes.py_object(_GenerationCanceled))
                tid[0] = None
            time.sleep(0.03)
        t.join(timeout=2.0)
        return result[0]

    def _fire_scheduler(self, osc, tempo, fire_q, stop_evt):
        """Fire queued clips one after another: fire a clip, wait until Ableton reports
        it has (nearly) finished its pass, then fire the next. Avoids cutting off the
        clip already playing on the track. With Live Global Quant = 1 Bar the launches
        land on the grid. Runs until the sentinel (None) or stop_evt.
        """
        import queue as _q
        while not stop_evt.is_set():
            try:
                item = fire_q.get(timeout=0.2)
            except _q.Empty:
                continue
            if item is None:
                break
            slot, length_beats = item
            try:
                osc.send("/live/clip/fire", [self.clip_track, slot])
                self._log_ui(f"Fired slot {slot}")
            except Exception:
                logger.exception("[loop] fire failed")
            # Wait for the clip to actually finish (polled from Ableton), not a
            # seconds estimate, before firing the next.
            self._wait_for_clip_end(osc, slot, length_beats, tempo, stop_evt)

    def _wait_for_clip_end(self, osc, slot, length_beats, tempo, stop_evt):
        """Block until the clip on (clip_track, slot) is about to finish its pass, using
        AbletonOSC playback position instead of a beats/tempo seconds estimate.

        Two phases: (1) wait for the clip to actually start playing (Live's launch
        quantization delays it past the fire), then (2) wait until its playing_position
        enters the final bar — firing the next clip then lets Quant = 1 Bar hand over
        exactly on the loop point. Falls back to a time-based wait if Ableton doesn't
        answer, and is always bounded so it can't hang the loop.
        """
        bpb = self.beats_per_bar if self.beats_per_bar and self.beats_per_bar > 0 else 4
        tempo = tempo or 120.0
        # Fire the next clip once we're inside the last bar; Quant=1Bar aligns the switch
        # to the loop point. For a 1-bar clip this is 0 (fire as soon as it's confirmed playing).
        fire_at = max(0.0, length_beats - bpb)
        est_s = max(0.1, length_beats * 60.0 / tempo)

        # Phase 1: confirm the clip is playing (bounded by ~2 clip-lengths of grace).
        started = False
        start_deadline = time.monotonic() + max(4.0, 2.0 * est_s)
        while not stop_evt.is_set() and time.monotonic() < start_deadline:
            if osc.get_clip_is_playing(self.clip_track, slot):
                started = True
                break
            time.sleep(0.05)
        if not started:  # couldn't confirm via OSC — fall back to the seconds estimate
            end = time.monotonic() + est_s
            while time.monotonic() < end and not stop_evt.is_set():
                time.sleep(0.05)
            return

        # Phase 2: wait until it reaches the fire point (bounded so a missed reply can't hang).
        hard_deadline = time.monotonic() + est_s + 6.0
        while not stop_evt.is_set() and time.monotonic() < hard_deadline:
            pos = osc.get_clip_playing_position(self.clip_track, slot)
            if pos is not None and pos >= fire_at:
                return
            time.sleep(0.05)

    def _run_clip_loop(self, first_output_path, seed_input_path, temp, top_p, min_p, tokens):
        """Self-feeding clip loop: write the latest output as a clip, then generate the
        next from it (whole output as prompt), stacking down the column.

        - free-run (generate continuously down to the bottom slot),
        - each output's PROMPT is also written to the track before it (same slot) when
          clip output is on and that track exists (clip_track-1 >= 0),
        - fire-on-write plays clips SEQUENTIALLY (each after the previous ends) via
          _fire_scheduler; otherwise no fire (you launch clips in Ableton),
        - same sampling params for every generation,
        - stops on Cancel (generation_cancel_event / cancel_event) or at the bottom slot.
        """
        from core.clip_output import AbletonOSCClient

        osc = AbletonOSCClient(self.clip_host, self.clip_port)
        start_slot = self.clip_slot
        # Bottom of the column: prefer Live's actual scene count, else the CLI cap.
        num_scenes = osc.get_num_scenes()
        max_slot = (num_scenes - 1) if num_scenes else self.loop_max_slot

        self.generation_cancel_event.clear()
        self.skip_pending_event.clear()

        def cancelled():
            return (self.cancel_event.is_set()
                    or self.generation_cancel_event.is_set()
                    or self.skip_pending_event.is_set())

        slot = start_slot
        prompt_path = first_output_path  # the output to write this iteration (Output 1 first)
        input_path = seed_input_path     # the prompt that produced `prompt_path`
        input_track = self.clip_track - 1
        write_input = input_track >= 0   # only pair if there's a track before the output track
        idx = 0
        self.loop_running = True
        self._log_ui(f"Loop started -> slots {start_slot}..{max_slot} (free-run)")
        if self.session_state:
            self.session_state.set_status("GENERATING")

        # Fire-on-write: play clips sequentially (each after the previous ends) instead of
        # firing immediately (which would cut off the clip already playing on the track).
        fire_q = None
        fire_stop = threading.Event()
        if self.clip_fire:
            tempo = osc.get_tempo() or 120.0
            fire_q = queue.Queue()
            threading.Thread(target=self._fire_scheduler, args=(osc, tempo, fire_q, fire_stop),
                             daemon=True).start()
            self._log_ui(f"Fire = sequential (tempo {tempo:g}); set Live Global Quant to 1 Bar")
        try:
            while not cancelled():
                if slot > max_slot:
                    self._log_ui(f"Loop reached bottom slot {max_slot}; stopping.")
                    break

                # Write the current output into the next slot. Fire only if the
                # fire-on-write toggle is on (default: you launch clips yourself).
                idx += 1
                name = f"Output {idx}"
                try:
                    n, length_beats = osc.write_clip(
                        prompt_path, self.clip_track, slot, name=name,
                        beats_per_bar=self.beats_per_bar, set_tempo=self.clip_set_tempo,
                        replace=self.clip_replace, fire=False, measures_out=self.clip_measures,
                    )
                    self._log_ui(f"Loop wrote '{name}' ({n} notes) -> slot {slot}")
                    if self.osc_log_cb:
                        self.osc_log_cb(f"Loop '{name}' -> slot {slot}")
                    # Pair: write this output's prompt to the track before it, same slot.
                    # Skip the seed (idx 1) when native record is active — Ableton already
                    # recorded the live take into that slot; don't overwrite it.
                    seed_recorded = idx == 1 and self._native_record_active()
                    if write_input and input_path and not seed_recorded:
                        try:
                            osc.write_clip(
                                input_path, input_track, slot, name=f"Input {idx}",
                                beats_per_bar=self.beats_per_bar, set_tempo=False,
                                replace=self.clip_replace, fire=False,
                                measures_out=self.clip_measures,
                            )
                        except Exception:
                            logger.exception("[loop] input clip write failed")
                    if fire_q is not None:  # sequential fire: queue it to play after the prev clip
                        fire_q.put((slot, length_beats))
                except Exception as e:
                    logger.exception(f"[loop] clip write failed: {e}")
                    self._log_ui("Loop clip write failed (is AbletonOSC running?)")
                    break
                slot += 1

                # Generate the next output from the whole previous output —
                # cancellable mid-token so /aria/cancel stops the loop immediately.
                out_seconds = self._midi_stats(prompt_path)[1]
                nxt = self._generate_cancellable(
                    prompt_midi_path=prompt_path,
                    prompt_duration_s=max(1, int(out_seconds) + 1),
                    horizon_s=self.gen_seconds,
                    temperature=temp, top_p=top_p, min_p=min_p,
                    max_new_tokens=tokens,
                    progress_cb=self.osc_generation_progress_cb,
                )
                if cancelled():
                    break
                # The old input has been written as a clip; free it. Keep the current
                # output — it becomes the prompt (input) for the next one.
                if input_path and input_path != prompt_path:
                    try:
                        os.unlink(input_path)
                    except Exception:
                        pass
                    input_path = None
                if not nxt:
                    self._log_ui("Loop generation returned nothing; stopping.")
                    break
                input_path = prompt_path
                prompt_path = nxt
        finally:
            self.loop_running = False
            fire_stop.set()
            if fire_q is not None:
                fire_q.put(None)
            for _p in {input_path, prompt_path}:
                if _p:
                    try:
                        os.unlink(_p)
                    except Exception:
                        pass
            osc.close()
            self.generation_cancel_event.clear()
            self.skip_pending_event.clear()
            if self.session_state:
                self.session_state.has_pending_output = False
                self.session_state.set_status("IDLE")
                self.session_state.set_last_output(None)
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            self._log_ui("Loop stopped — ready to record again.")

    def _generate_once(self, prompt_midi_path, duration, temp, top_p, min_p, tokens, seed=None,
                       silent=False):
        """Run one generation with the same cancel/timeout/STATUS handling as before. Returns the
        generated MIDI path, or None if canceled, timed out, or failed.

        silent=True suppresses the generation LED/progress OSC + STATUS output. Used for the variant
        prefetch that runs DURING playback, so background generation doesn't light the 'generating'
        LED or move the shared slider while a take is playing (playback owns the UI)."""
        self.generation_cancel_event.clear()
        if self.osc_generation_start_cb and not silent:
            self.osc_generation_start_cb()

        gen_result: List[Optional[str]] = [None]
        gen_thread_id: List[Optional[int]] = [None]

        def _run_generate():
            gen_thread_id[0] = threading.current_thread().ident
            try:
                gen_result[0] = self.aria_engine.generate(
                    prompt_midi_path=prompt_midi_path,
                    prompt_duration_s=max(1, int(duration)),
                    horizon_s=self.gen_seconds,
                    temperature=temp,
                    top_p=top_p,
                    min_p=min_p,
                    max_new_tokens=tokens,
                    progress_cb=(None if silent else self.osc_generation_progress_cb),
                    seed=seed,
                )
            except _GenerationCanceled:
                logger.info("[manual] Generation interrupted mid-token")
                print("[manual] Generation interrupted mid-token")
            except Exception as e:
                logger.exception(f"[manual] Generation error: {e}")

        MAX_GEN_TIMEOUT_S = 90

        gen_thread = threading.Thread(target=_run_generate, daemon=True)
        if not silent:
            print("STATUS:generating", flush=True)
        gen_thread.start()

        gen_start_time = time.time()
        last_status_elapsed = -1.0
        timed_out = False
        while gen_thread.is_alive():
            if self.generation_cancel_event.is_set() and gen_thread_id[0] is not None:
                logger.info("[manual] Injecting cancel into generation thread")
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(gen_thread_id[0]),
                    ctypes.py_object(_GenerationCanceled),
                )
                gen_thread_id[0] = None
            elapsed = time.time() - gen_start_time
            if elapsed > MAX_GEN_TIMEOUT_S:
                logger.error(f"[manual] Generation timed out after {int(elapsed)}s")
                print(f"STATUS:error:Generation timed out after {int(elapsed)}s — check GPU/model.", flush=True)
                if gen_thread_id[0] is not None:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_ulong(gen_thread_id[0]),
                        ctypes.py_object(_GenerationCanceled),
                    )
                    gen_thread_id[0] = None
                timed_out = True
                break
            if not silent and elapsed - last_status_elapsed >= 0.5:
                print(f"STATUS:generating:{elapsed:.1f}", flush=True)
                last_status_elapsed = elapsed
            time.sleep(0.05)

        gen_thread.join(timeout=5.0)
        if not timed_out and not silent:
            print("STATUS:generation_done", flush=True)
        if self.osc_generation_done_cb and not silent:
            self.osc_generation_done_cb()

        generated_path = gen_result[0]
        if timed_out or self.generation_cancel_event.is_set():
            self.generation_cancel_event.clear()
            if generated_path:
                try:
                    os.unlink(generated_path)
                except Exception:
                    pass
            return None
        return generated_path

    def _wait_for_play_signal(self) -> bool:
        """Block until a Play signal (keyboard play key or OSC /aria/play). Returns True on Play,
        False if canceled/skipped. Unlike _wait_for_play it plays nothing — the caller generates
        and plays the next variant."""
        play_event = threading.Event()

        def _wait_keyboard_play():
            if self.play_toggle.wait_for_press(
                f"Press '{self.play_key}' for the next variant (Cancel to stop).", self.cancel_event
            ):
                play_event.set()

        threading.Thread(target=_wait_keyboard_play, daemon=True).start()
        while not self.cancel_event.is_set() and not self.skip_pending_event.is_set():
            self._drain_commands(play_event=play_event)
            if play_event.is_set():
                return True
            time.sleep(0.05)
        if self.skip_pending_event.is_set():
            self.skip_pending_event.clear()
        return False

    def _play_variant(self, path):
        """Stream a generated variant out ARIA_OUT (grade by ear), then delete its temp file."""
        if not self.out_port:
            logger.warning("[variant] No output port; cannot play variant.")
            self._log_ui("No output port to play variant")
            return
        self.playback_cancel_event.clear()
        sent, total = _play_midi_file(
            path, self.out_port,
            progress_cb=self.osc_playback_progress_cb,
            duration_cb=self.osc_playback_duration_cb,
            stop_event=self.playback_cancel_event,
        )
        if self.osc_playback_stopped_cb:
            self.osc_playback_stopped_cb()
        logger.info(f"[variant] Played ({sent} msgs, {total:.2f}s)")
        try:
            os.unlink(path)
        except Exception:
            pass

    def _run_variant_loop(self, prompt_midi_path, duration, temp, top_p, min_p, tokens):
        """Option-A collection: each Play press plays the next variant of this prompt, each saved as
        its own graded episode (linked by group_id). Runs up to self.variants takes, or until Cancel.
        Grade + commit each between Play presses; commit targets the active variant.

        Prefetch (always-on): the NEXT take is generated in a background thread while the current one
        plays, so a Play press usually plays instantly. Its sampling params are snapshotted when the
        prefetch kicks off (as the current take starts playing) — so a knob change between takes lands
        on the take-after-next, not the immediate next. The first take is generated live on Play."""
        import uuid
        import random

        group_id = uuid.uuid4().hex[:12]
        self._variant_group_id = group_id
        try:
            prompt_bytes = retime_midi_to_120bpm(prompt_midi_path)
        except Exception:
            prompt_bytes = Path(prompt_midi_path).read_bytes()

        self._log_ui(
            f"Variant mode: press '{self.play_key}' for each take (up to {self.variants}); "
            "grade + commit between takes. Next take generates while the current one plays."
        )

        def _snapshot_params():
            """Read the live sampling knobs (temp/top_p/min_p/tokens) right now."""
            if self.sampling_state:
                t, tp, mp = self.sampling_state.get_values()
                return t, tp, mp, self._resolve_max_tokens()
            return temp, top_p, min_p, tokens

        # Background prefetch of the next take. "thread" None => nothing in flight; when it finishes
        # "result"[0] holds the generated path (or None on cancel/fail) and "params" its sampling meta.
        prefetch = {"thread": None, "result": [None], "params": None}

        def _start_prefetch():
            t, tp, mp, tok = _snapshot_params()   # temp snapshot at kickoff ("always prefetch")
            seed = random.randint(1, 2**31 - 1)
            prefetch["params"] = {"temperature": t, "top_p": tp, "min_p": mp,
                                  "max_tokens": tok, "seed": seed}
            prefetch["result"] = [None]
            logger.info(
                f"[variant] prefetch next: temp={t:.2f} top_p={tp:.2f} "
                f"min_p={mp if mp is not None else 0.0:.2f} seed={seed}"
            )

            def _work():
                # silent=True: the background prefetch must not light the generation LED or move
                # the shared slider — the currently-playing take owns the UI.
                prefetch["result"][0] = self._generate_once(
                    prompt_midi_path, duration, t, tp, mp, tok, seed=seed, silent=True
                )

            th = threading.Thread(target=_work, daemon=True)
            prefetch["thread"] = th
            th.start()

        idx = 0
        try:
            while idx < self.variants and not self.cancel_event.is_set():
                if self.session_state:
                    self.session_state.set_status("READY")
                if self.osc_status_cb:
                    self.osc_status_cb("READY")
                print("STATUS:awaiting_play", flush=True)

                if not self._wait_for_play_signal():
                    break  # canceled

                # Take this variant: the prefetched one if we started it during the previous
                # playback, else generate now (first take honors the live knob on Play).
                if prefetch["thread"] is not None:
                    prefetch["thread"].join()
                    generated = prefetch["result"][0]
                    params = prefetch["params"]
                    prefetch["thread"] = None
                else:
                    t, tp, mp, tok = _snapshot_params()
                    seed = random.randint(1, 2**31 - 1)
                    params = {"temperature": t, "top_p": tp, "min_p": mp,
                              "max_tokens": tok, "seed": seed}
                    logger.info(
                        f"[variant] take {idx + 1}/{self.variants} (live): temp={t:.2f} "
                        f"top_p={tp:.2f} min_p={mp if mp is not None else 0.0:.2f}"
                    )
                    generated = self._generate_once(
                        prompt_midi_path, duration, t, tp, mp, tok, seed=seed
                    )

                if generated is None:
                    break  # canceled/failed mid-generation

                if self.feedback_manager:
                    try:
                        output_bytes = Path(generated).read_bytes()
                        ep_params = dict(params)
                        ep_params.update({"group_id": group_id, "variant": idx})
                        ep_id = self.feedback_manager.create_variant_episode(
                            prompt_bytes, output_bytes, ep_params, mode="manual"
                        )
                        if ep_id:
                            self.feedback_manager.set_active_episode(ep_id)
                    except Exception as e:
                        logger.warning(f"[variant] capture failed: {e}")

                idx += 1

                # Kick off the NEXT take before playing this one, so it generates while the
                # audio streams (nothing left to prefetch after the final take).
                if idx < self.variants and not self.cancel_event.is_set():
                    _start_prefetch()

                if self.session_state:
                    self.session_state.set_status("PLAYING")
                if self.osc_status_cb:
                    self.osc_status_cb("PLAYING")
                self._play_variant(generated)
                self._log_ui(
                    f"Variant {idx}/{self.variants} played — grade + commit, then Play for the next."
                )
            if idx >= self.variants:
                self._log_ui(f"All {self.variants} variants done — record a new prompt to continue.")
        finally:
            # Drain any in-flight prefetch and delete its temp file so a canceled series leaves
            # nothing behind (its episode was never created — it only becomes active on Play).
            if prefetch["thread"] is not None:
                self.generation_cancel_event.set()   # stop a running prefetch fast
                prefetch["thread"].join(timeout=5.0)
                leftover = prefetch["result"][0]
                if leftover:
                    try:
                        os.unlink(leftover)
                    except Exception:
                        pass
                self.generation_cancel_event.clear()
            self._variant_group_id = None
            try:
                os.unlink(prompt_midi_path)
            except Exception:
                pass
            if self.session_state:
                self.session_state.set_status("IDLE")
                self.session_state.has_pending_output = False
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")

    def _finish_recording_and_generate(self):
        """Stop, generate, and arm playback (prompting for 'p')."""
        self.recording_flag.clear()
        self._stop_native_clip_record()   # finalise the Ableton take before generating
        self.stop_time = time.monotonic()
        duration = (self.stop_time - self.start_time) if self.start_time else 0.0
        logger.info(f"[manual] Recording stopped at {self.stop_time:.3f} (duration={duration:.2f}s)")
        self._log_ui(f"Recording stopped (events={self._msg_count}, note_on={self._note_on_count})")
        if self.session_state:
            self.session_state.set_status("GENERATING")
            self.session_state.set_recording(False)
        if self.osc_status_cb:
            self.osc_status_cb("GENERATING")

        if not self.recorded:
            logger.warning("[manual] No MIDI captured. Nothing to generate.")
            self._log_ui("No MIDI captured. Check Ableton routing/monitor on ARIA_IN or competing readers.")
            if self.session_state:
                self.session_state.set_status("IDLE")
                self.session_state.has_pending_output = False
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            return

        bpm = infer_bpm_from_onsets(self.recorded)
        if bpm:
            logger.info(f"[manual] Estimated BPM from onsets: {bpm:.2f}")
            if self.max_bars:
                max_duration = (60.0 / bpm) * self.beats_per_bar * self.max_bars
                if duration > max_duration:
                    cutoff = (self.start_time or 0) + max_duration
                    original_len = len(self.recorded)
                    self.recorded = [m for m in self.recorded if m.timestamp <= cutoff]
                    duration = max_duration
                    logger.info(
                        f"[manual] Trimmed recording to {self.max_bars} bars ({max_duration:.2f}s); kept {len(self.recorded)}/{original_len} events."
                    )
        else:
            logger.info("[manual] Could not infer BPM; using default 120 BPM conversion.")

        # For clip output, place the recording on Ableton's ACTUAL Set tempo (what you
        # recorded to with the metronome) instead of the inferred BPM — otherwise the
        # input clip comes back stretched / off-beat. Changing the tempo here does not
        # change what Aria generates (the absolute-time performance is invariant); it only
        # fixes the beat grid used for the clip.
        if self.clip_output:
            try:
                from core.clip_output import query_tempo
                set_bpm = query_tempo(self.clip_host, self.clip_port)
            except Exception:
                set_bpm = None
            if set_bpm:
                logger.info(f"[manual] Using Ableton Set tempo {set_bpm:.2f} for clip placement (inferred was {bpm})")
                bpm = set_bpm

        prompt_midi_path = buffer_to_tempfile_midi(
            messages=self.recorded,
            window_seconds=duration,
            current_bpm=bpm,
            ticks_per_beat=self.ticks_per_beat,
        )

        prompt_ticks, prompt_seconds = self._midi_stats(prompt_midi_path)
        logger.info(
            f"[manual] Prompt stats: events={len(self.recorded)}, duration={duration:.2f}s, midi_len={prompt_seconds:.2f}s, ticks={prompt_ticks}"
        )

        gen_start = time.time()
        temp, top_p, min_p = self.sampling_state.get_values() if self.sampling_state else (0.9, 0.95, None)
        logger.info(f"[GEN] temp={temp:.2f} top_p={top_p:.2f} min_p={min_p if min_p is not None else 0.0:.2f}")
        self._log_ui(
            f"Generating with temp={temp:.2f} top_p={top_p:.2f} min_p={min_p if min_p is not None else 0.0:.2f}"
        )
        if self.osc_params_cb:
            self.osc_params_cb()
        tokens = self._resolve_max_tokens()
        if tokens is not None:
            logger.info(f"[GEN] max_new_tokens={tokens}")
            self._log_ui(f"Max tokens -> {tokens}")
        # Variant collection (Option A): each Play press makes the next take of this prompt,
        # each its own graded episode. Default variants=1 keeps the single-take flow below.
        if self.variants != 1:
            self._run_variant_loop(prompt_midi_path, duration, temp, top_p, min_p, tokens)
            return

        generated_path = self._generate_once(prompt_midi_path, duration, temp, top_p, min_p, tokens)
        if generated_path is None:
            # canceled, timed out, or failed — return to record-ready
            if self.session_state:
                self.session_state.set_status("IDLE")
                self.session_state.has_pending_output = False
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            self._log_ui("Ready to record")
            return
        gen_time = time.time() - gen_start
        logger.info(f"[manual] Generation finished in {gen_time:.2f}s")
        if self.session_state:
            self.session_state.set_status("PLAYING")
        if self.osc_status_cb:
            self.osc_status_cb("PLAYING")

        self._capture_feedback(prompt_midi_path, generated_path, temp, top_p, min_p, tokens)

        # Infinite clip-loop: feed each output back as the next prompt, writing clips
        # down the column (no fire — you launch them). Buffer of N ahead of the playing
        # slot; stops at Cancel or the bottom slot. Bypasses the normal play-gate.
        if self.loop_mode and self.clip_output:
            # Pass the recorded prompt as the seed input (loop writes it to track-1 and
            # owns its temp file cleanup).
            self._run_clip_loop(generated_path, prompt_midi_path, temp, top_p, min_p, tokens)
            return

        # Clip output (no loop): writing the clip IS the output action, so do it
        # automatically when generation finishes instead of waiting for Play.
        if self.clip_output:
            self.pending_output_path = generated_path
            self.pending_prompt_path = prompt_midi_path  # write as the paired input clip
            if self.session_state:
                self.session_state.set_last_output(generated_path)
                self.session_state.has_pending_output = True
            self._handle_play_request()
            return

        if self.play_gate:
            self.pending_output_path = generated_path
            if self.session_state:
                self.session_state.set_last_output(generated_path)
                self.session_state.has_pending_output = True
                self.session_state.set_status("READY")
            if self.osc_status_cb:
                self.osc_status_cb("READY")
            self._log_ui("Output ready. Press 'p' to play.")
            logger.info("[MANUAL] Output ready. Press 'p' to play.")
            print("STATUS:awaiting_play", flush=True)
            self._wait_for_play()
        else:
            if self.play_toggle:
                pressed = self.play_toggle.wait_for_press(
                    f"Press '{self.play_key}' to PLAY generated output, or Ctrl+C to quit.",
                    self.cancel_event,
                )
                if not pressed:
                    logger.info("[manual] Playback canceled.")
                    self._log_ui("Playback canceled")
                    return
            self.playback_cancel_event.clear()
            sent, total = _play_midi_file(generated_path, self.out_port, progress_cb=self.osc_playback_progress_cb, duration_cb=self.osc_playback_duration_cb, stop_event=self.playback_cancel_event)
            if self.osc_playback_stopped_cb:
                self.osc_playback_stopped_cb()
            logger.info(f"[manual] Played generated MIDI ({sent} msgs, {total:.2f}s)")
            self._log_ui(f"Played generated MIDI ({sent} msgs, {total:.2f}s)")
            if self.session_state:
                self.session_state.set_last_output(generated_path)
                self.session_state.has_pending_output = False
            if self.osc_log_cb:
                self.osc_log_cb(f"Played generated MIDI ({sent} msgs, {total:.2f}s)")
            try:
                os.unlink(prompt_midi_path)
            except Exception:
                pass
            try:
                os.unlink(generated_path)
            except Exception:
                pass
            if self.session_state:
                self.session_state.set_status("IDLE")
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")

    def _log_ui(self, msg: str):
        if self.log_queue:
            ts = time.strftime("%H:%M:%S")
            self.log_queue.put(f"[{ts}] {msg}")
        if self.osc_log_cb:
            self.osc_log_cb(msg)

    def _capture_feedback(self, prompt_path: str, output_path: str | None, temp, top_p, min_p, tokens):
        if not self.feedback_manager or not output_path:
            return
        try:
            output_bytes = Path(output_path).read_bytes()
        except Exception as e:
            logger.warning(f"[feedback] Failed to read MIDI files: {e}")
            return
        # Store the prompt at a fixed 120 BPM (matches output.mid) so it doesn't half-tempo
        # when auditioned in a DAW. Falls back to raw bytes if retiming fails.
        try:
            prompt_bytes = retime_midi_to_120bpm(prompt_path)
        except Exception as e:
            logger.warning(f"[feedback] Prompt retime to 120 BPM failed, storing as-is: {e}")
            prompt_bytes = Path(prompt_path).read_bytes()
        params = {
            "temperature": temp,
            "top_p": top_p,
            "min_p": min_p,
            "max_tokens": tokens,
            "seed": None,
        }
        episode_id = self.feedback_manager.record_generation(
            prompt_bytes=prompt_bytes,
            output_bytes=output_bytes,
            params=params,
            mode="manual",
        )
        if episode_id:
            logger.info(f"[feedback] Draft episode created: {episode_id}")

    def run(self) -> int:
        try:
            self._open_ports()
            self._start_midi_thread()

            while not self.cancel_event.is_set():
                stop_key_event = threading.Event()

                # Wait for either keyboard start or UI/OSC record start
                start_evt = threading.Event()

                def _wait_keyboard_start():
                    if self.toggle.wait_for_press(
                        f"Manual mode armed. Press '{self.manual_key}' to START recording.",
                        self.cancel_event,
                    ):
                        start_evt.set()

                threading.Thread(target=_wait_keyboard_start, daemon=True).start()

                while not self.cancel_event.is_set() and not start_evt.is_set():
                    self._drain_commands(stop_key_event, start_event=start_evt)
                    time.sleep(0.05)

                if self.cancel_event.is_set():
                    break
                if not start_evt.is_set() and not self.recording_flag.is_set():
                    continue

                self._begin_recording()

                stop_key_event = threading.Event()
                threading.Thread(
                    target=lambda: (self.toggle.wait_for_press(
                        f"Recording... Press '{self.manual_key}' again to STOP.", stop_key_event), stop_key_event.set()),
                    daemon=True,
                ).start()

                if self.max_bars:
                    logger.info(f"[manual] max-bars flag set to {self.max_bars}; will apply after tempo inference if possible.")

                while not self.cancel_event.is_set():
                    self._drain_commands(stop_key_event)
                    now = time.monotonic()
                    if stop_key_event.is_set():
                        break
                    if self.max_seconds and self.start_time and (now - self.start_time) >= self.max_seconds:
                        logger.info(f"[manual] Max seconds reached ({self.max_seconds}s); stopping.")
                        stop_key_event.set()
                        break
                    time.sleep(0.02)

                self._finish_recording_and_generate()

            return 0

        except KeyboardInterrupt:
            logger.info("Manual mode interrupted by user.")
            return 0
        except Exception as e:
            logger.exception(f"Manual mode fatal error: {e}")
            return 1
        finally:
            self.cancel_event.set()
            if self.midi_thread and self.midi_thread.is_alive():
                self.midi_thread.join(timeout=1.0)
            self._close_ports()

    @staticmethod
    def _midi_stats(path: str) -> Tuple[int, float]:
        import mido
        mid = mido.MidiFile(path)
        total_ticks = 0
        for track in mid.tracks:
            ticks = 0
            for msg in track:
                ticks += getattr(msg, "time", 0)
            total_ticks = max(total_ticks, ticks)
        return total_ticks, mid.length
