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

    def __init__(self, key: str = "r"):
        self.key = key
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
            if cancel_event.is_set():
                return False
            input(f"{message} (press Enter to continue)")
            return True
        except KeyboardInterrupt:
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
        osc_playback_progress_cb=None,
        osc_playback_stopped_cb=None,
        osc_playback_duration_cb=None,
        play_gate: bool = False,
        feedback_manager=None,
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
        # Default play key to 'p' so manual playback always available (even if flag omitted).
        self.play_key = play_key or "p"
        self.play_toggle = KeyboardToggle(self.play_key)
        self.sampling_state = sampling_state
        self.command_queue = command_queue
        self.log_queue = log_queue
        self.session_state = session_state
        self.osc_status_cb = osc_status_cb
        self.osc_log_cb = osc_log_cb
        self.osc_params_cb = osc_params_cb
        self.osc_generation_start_cb = osc_generation_start_cb
        self.osc_generation_done_cb = osc_generation_done_cb
        self.osc_playback_progress_cb = osc_playback_progress_cb
        self.osc_playback_stopped_cb = osc_playback_stopped_cb
        self.osc_playback_duration_cb = osc_playback_duration_cb
        # Gate playback to explicit PLAY command/key to keep manual + OSC paths consistent.
        self.play_gate = True
        self.feedback_manager = feedback_manager
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

        self.toggle = KeyboardToggle(manual_key)
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
        """Return the first port whose name starts with 'name' (case-insensitive)."""
        import mido
        available = mido.get_input_names() if kind == "input" else mido.get_output_names()
        matched = [p for p in available if p.lower().startswith(name.lower())]
        if matched:
            return matched[0]
        raise RuntimeError(
            f"Could not find a MIDI {kind} port starting with '{name}'. "
            f"Make sure loopMIDI is running and the port is created. "
            f"Available ports: {available}"
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
        self.recorded.clear()
        self._msg_count = 0
        self._note_on_count = 0
        self.recording_flag.set()
        self.start_time = time.monotonic()
        logger.info(f"[manual] Recording started at {self.start_time:.3f}")
        self._log_ui("Recording started")
        if self.session_state:
            self.session_state.set_status("RECORDING")
            self.session_state.set_recording(True)
        if self.osc_status_cb:
            self.osc_status_cb("RECORDING")

    def _finish_recording_and_generate(self):
        """Stop, generate, and arm playback (prompting for 'p')."""
        self.recording_flag.clear()
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
        self.generation_cancel_event.clear()
        if self.osc_generation_start_cb:
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
                )
            except _GenerationCanceled:
                logger.info("[manual] Generation interrupted mid-token")
                print("[manual] Generation interrupted mid-token")
            except Exception as e:
                logger.exception(f"[manual] Generation error: {e}")

        gen_thread = threading.Thread(target=_run_generate, daemon=True)
        print("STATUS:generating", flush=True)
        gen_thread.start()

        gen_start_time = time.time()
        last_status_elapsed = -1.0
        while gen_thread.is_alive():
            if self.generation_cancel_event.is_set() and gen_thread_id[0] is not None:
                logger.info("[manual] Injecting cancel into generation thread")
                print("[manual] Cancel received — interrupting generation")
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(gen_thread_id[0]),
                    ctypes.py_object(_GenerationCanceled),
                )
                gen_thread_id[0] = None
            elapsed = time.time() - gen_start_time
            if elapsed - last_status_elapsed >= 0.5:
                print(f"STATUS:generating:{elapsed:.1f}", flush=True)
                last_status_elapsed = elapsed
            time.sleep(0.05)

        gen_thread.join(timeout=10.0)
        print("STATUS:generation_done", flush=True)

        if self.osc_generation_done_cb:
            self.osc_generation_done_cb()

        generated_path = gen_result[0]

        if self.generation_cancel_event.is_set():
            logger.info("[manual] Generation canceled — discarding output")
            print("[manual] Generation canceled — discarding output")
            self.generation_cancel_event.clear()
            if generated_path:
                try:
                    os.unlink(generated_path)
                except Exception:
                    pass
            if self.session_state:
                self.session_state.set_status("IDLE")
                self.session_state.has_pending_output = False
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            self._log_ui("Generation canceled — ready to record")
            return
        gen_time = time.time() - gen_start
        logger.info(f"[manual] Generation finished in {gen_time:.2f}s")
        if self.session_state:
            self.session_state.set_status("PLAYING")
        if self.osc_status_cb:
            self.osc_status_cb("PLAYING")

        if not generated_path:
            logger.warning("[manual] Generation returned None; aborting playback.")
            self._log_ui("Generation returned None")
            if self.session_state:
                self.session_state.set_status("IDLE")
            if self.osc_status_cb:
                self.osc_status_cb("IDLE")
            return

        self._capture_feedback(prompt_midi_path, generated_path, temp, top_p, min_p, tokens)

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
            print("[MANUAL] Output ready. Press 'p' to play.")
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
            prompt_bytes = Path(prompt_path).read_bytes()
            output_bytes = Path(output_path).read_bytes()
        except Exception as e:
            logger.warning(f"[feedback] Failed to read MIDI files: {e}")
            return
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
