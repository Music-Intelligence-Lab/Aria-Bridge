"""Core orchestration for real-time Ableton-Aria bridge."""

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from .prompt_midi import buffer_to_tempfile_midi
except ImportError:
    from prompt_midi import buffer_to_tempfile_midi

logger = logging.getLogger(__name__)


class GenerationJob:
    """A job to generate music for a specific bar/bars."""
    def __init__(
        self,
        bar_index: int,
        prompt_events: list,
        aria_engine,
        sampling_state,
        gen_bars: int = 2,
    ):
        self.bar_index = bar_index  # Starting bar index
        self.prompt_events = prompt_events
        self.aria_engine = aria_engine
        self.sampling_state = sampling_state
        self.gen_bars = gen_bars  # Number of measures to generate
        self.result_midi_path = None  # Set when generation completes


class GenerationWorker(threading.Thread):
    """Background thread that processes generation jobs asynchronously."""

    def __init__(self, job_queue: queue.Queue, feedback_manager=None):
        """
        Args:
            job_queue: Queue of GenerationJob objects
        """
        super().__init__(daemon=True)
        self.job_queue = job_queue
        self.running = False
        self.feedback_manager = feedback_manager

    def run(self):
        """Process generation jobs from queue."""
        logger.info("GenerationWorker thread started")
        try:
            while self.running:
                try:
                    job = self.job_queue.get(timeout=0.1)
                    if job is None:  # Sentinel to stop
                        break
                    
                    logger.info(f"[gen_worker] Starting generation for bar {job.bar_index} ({job.gen_bars} bars)")
                    
                    # Build prompt MIDI
                    prompt_midi_path = buffer_to_tempfile_midi(
                        job.prompt_events,
                        window_seconds=0,
                        ticks_per_beat=480,
                    )
                    
                    # Call Aria to generate N bars
                    start_time = time.time()
                    try:
                        temp, top_p, min_p = job.sampling_state.get_values()
                        logger.info(f"[GEN] temp={temp:.2f} top_p={top_p:.2f} min_p={min_p if min_p is not None else 0.0:.2f}")
                        # Horizon in seconds: gen_bars * 1.0s per bar (roughly)
                        horizon_s = job.gen_bars * 1.0
                        midi_path = job.aria_engine.generate(
                            prompt_midi_path=prompt_midi_path,
                            prompt_duration_s=4,
                            horizon_s=horizon_s,
                            temperature=temp,
                            top_p=top_p,
                            min_p=min_p,
                        )
                        gen_time = time.time() - start_time
                        
                        if midi_path:
                            job.result_midi_path = midi_path
                            logger.info(f"[gen_worker] Bar {job.bar_index} ({job.gen_bars}-bar generation) done in {gen_time:.2f}s")
                            if self.feedback_manager:
                                try:
                                    prompt_bytes = Path(prompt_midi_path).read_bytes()
                                    output_bytes = Path(midi_path).read_bytes()
                                    params = {
                                        "temperature": temp,
                                        "top_p": top_p,
                                        "min_p": min_p,
                                        "max_tokens": None,
                                        "seed": None,
                                    }
                                    episode_id = self.feedback_manager.record_generation(
                                        prompt_bytes=prompt_bytes,
                                        output_bytes=output_bytes,
                                        params=params,
                                        mode="clock",
                                    )
                                    if episode_id:
                                        logger.info(f"[feedback] Draft episode created: {episode_id}")
                                except Exception as e:
                                    logger.warning(f"[feedback] Failed to create episode: {e}")
                        else:
                            logger.warning(f"[gen_worker] Bar {job.bar_index} generation returned None")
                    except Exception as e:
                        logger.exception(f"[gen_worker] Bar {job.bar_index} generation failed: {e}")
                        job.error = str(e)
                    finally:
                        # Cleanup prompt
                        try:
                            os.unlink(prompt_midi_path)
                        except Exception:
                            pass
                    
                    self.job_queue.task_done()
                    
                except queue.Empty:
                    pass
        except Exception as e:
            logger.exception(f"GenerationWorker error: {e}")
        finally:
            logger.info("GenerationWorker thread stopped")


class AbletonBridge:
    """
    Orchestrates real-time MIDI I/O and Aria generation.

    Flow:
    1. Input thread reads MIDI from loopMIDI port and adds to rolling buffer
    2. Generation thread runs every N ms:
       - Snapshot rolling buffer
       - Convert to MIDI file
       - Run Aria inference
       - Queue output events
    3. Output thread sends queued MIDI events to loopMIDI port

    No human->human feedback by default: only ingests human input, not generated notes.
    """

    def __init__(
        self,
        in_port_name: str,
        out_port_name: str,
        midi_buffer,
        aria_engine,
        tempo_tracker=None,
        # Grid / clock parameters
        clock_in: Optional[str] = None,
        measures: int = 4,
        beats_per_bar: int = 4,
        gen_measures: Optional[int] = None,
        human_measures: int = 1,
        cooldown_seconds: float = 0.2,
        sampling_state=None,
        quantize: bool = False,
        ticks_per_beat: int = 480,
        feedback_manager=None,
    ):
        """
        Args:
            in_port_name: Input MIDI port (e.g., "ARIA_IN")
            out_port_name: Output MIDI port (e.g., "ARIA_OUT")
            midi_buffer: RollingMidiBuffer instance
            aria_engine: AriaEngine instance
            tempo_tracker: TempoTracker instance (optional)
            listen_seconds: Duration to listen before generating (e.g., 4.0)
            gen_seconds: Duration of continuation to generate (e.g., 1.0)
            cooldown_seconds: Cooldown after generation before listening again (e.g., 0.2)
            temperature: Sampling temperature
            top_p: Top-p sampling
            quantize: Whether to quantize output to 1/16 grid
            ticks_per_beat: MIDI ticks per quarter note
        """
        self.in_port_name = in_port_name
        self.out_port_name = out_port_name
        self.midi_buffer = midi_buffer
        self.aria_engine = aria_engine
        self.tempo_tracker = tempo_tracker
        # Grid/clock
        self.clock_in = clock_in
        self.measures = measures
        self.beats_per_bar = beats_per_bar
        self.gen_measures = gen_measures if gen_measures is not None else measures
        self.human_measures = human_measures  # Number of measures to collect before generating

        self.cooldown_seconds = cooldown_seconds
        self.sampling_state = sampling_state
        self.quantize = quantize
        self.ticks_per_beat = ticks_per_beat
        self.feedback_manager = feedback_manager

        # MIDI I/O
        self.in_port = None
        self.out_port = None

        # Queue of (msg_type, msg_data)
        self.event_queue = queue.Queue()

        # Control
        self.running = False
        self.threads = []

        # PHASE state machine
        self.PHASE_HUMAN = 'human'       # Collecting human input
        self.PHASE_AI_PLAY = 'ai_play'   # Playing AI N-measure response
        self.phase = self.PHASE_HUMAN

        # ClockGrid will be set if clock_in provided
        self.clock_grid = None

        self.listen_start_time = None
        self.cooldown_end_time = None

        # Stats
        self.generation_count = 0
        self.skip_count = 0
        self.generation_times = []

        # Scheduler for model output: list of (target_pulse, mido.Message)
        self.scheduled_messages = []
        self.scheduled_lock = threading.RLock()
        self.model_end_pulse = None
        self.last_boundary_pulse = None

        # Anchor-based boundary tracking (counts from first human note, not transport start)
        self.anchor_pulse = None
        self.bar_index = 0
        self.next_bar_boundary_pulse = None
        self.bars_collected_in_phase = 0  # Track bars collected in current PHASE_HUMAN

        # Per-bar buffering: bar_index -> list of (pulse, event_type, msg_data)
        self.human_bar_buffers = {}  # dict[int, list[TimestampedMidiMsg]]
        self.generated_bar_queue = {}  # dict[int, list[mido.Message]]
        self.last_scheduled_bar = None  # Highest bar index we've scheduled for playback

        # Failsafe: force generation after 6 seconds of no generation
        self.last_generation_time = time.time()
        self.failsafe_forced = False

        # Asynchronous generation worker for MVP (1-bar-in -> N-measures-out cycle)
        self.gen_job_queue = queue.Queue()
        self.pending_ai_job = None  # Current job being processed
        self.pending_ai_response = None  # Path to N-measure MIDI when ready
        self.pending_ai_response_lock = threading.RLock()
        self.gen_worker = GenerationWorker(self.gen_job_queue, feedback_manager=self.feedback_manager)

    def run(self):
        """Start the bridge: input, generation, output threads."""
        try:
            self._setup_midi_ports()
            # Start clock grid if requested
            if self.clock_in:
                try:
                    from ..modes.clock_mode import ClockGrid
                except ImportError:
                    from modes.clock_mode import ClockGrid

                self.clock_grid = ClockGrid(clock_port_name=self.clock_in, measures=self.measures, beats_per_bar=self.beats_per_bar)
                # Do NOT register boundary callback; we use anchor-based boundary detection in _generation_loop
                try:
                    self.clock_grid.start()
                    logger.info(f"ClockGrid started on '{self.clock_in}' (measures={self.measures})")
                except Exception as e:
                    logger.warning(f"Failed to start ClockGrid: {e}")
            self.running = True

            # Start generation worker
            self.gen_worker.running = True
            self.gen_worker.start()

            # Start threads
            t_input = threading.Thread(target=self._input_loop, daemon=True)
            t_gen = threading.Thread(target=self._generation_loop, daemon=True)
            t_output = threading.Thread(target=self._output_loop, daemon=True)

            for t in [t_input, t_gen, t_output]:
                t.start()
                self.threads.append(t)

            logger.info("Bridge started. Press Ctrl+C to stop.")

            # Keep main thread alive
            while self.running:
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Interrupt received, shutting down...")
            self.shutdown()
        except Exception as e:
            logger.exception(f"Bridge error: {e}")
            self.shutdown()

    def shutdown(self):
        """Stop all threads and close ports."""
        self.running = False
        self.gen_worker.running = False

        # Wait for generation worker to finish
        if self.gen_worker.is_alive():
            self.gen_job_queue.put(None)  # Send sentinel
            self.gen_worker.join(timeout=2)

        for t in self.threads:
            t.join(timeout=2)

        if self.tempo_tracker:
            self.tempo_tracker.stop()

        if self.in_port:
            self.in_port.close()
            logger.info("Input port closed")

        if self.out_port:
            self.out_port.close()
            logger.info("Output port closed")

        logger.info(
            f"Bridge shutdown. Stats: {self.generation_count} generations, "
            f"{self.skip_count} skips"
        )

    def _setup_midi_ports(self):
        """Open MIDI input and output ports."""
        try:
            import mido
        except ImportError:
            raise ImportError("mido is required. Install with: pip install mido")

        # Input port
        available_in = mido.get_input_names()
        matched_in = [p for p in available_in if p.lower().startswith(self.in_port_name.lower())]
        if not matched_in:
            raise RuntimeError(
                f"Could not find a MIDI input port starting with '{self.in_port_name}'. "
                f"Make sure loopMIDI is running and the port is created. "
                f"Available ports: {available_in}"
            )
        in_port_name = matched_in[0]
        self.in_port = mido.open_input(in_port_name)
        logger.info(f"Input port opened: {in_port_name}")

        # Output port
        available_out = mido.get_output_names()
        matched_out = [p for p in available_out if p.lower().startswith(self.out_port_name.lower())]
        if not matched_out:
            raise RuntimeError(
                f"Could not find a MIDI output port starting with '{self.out_port_name}'. "
                f"Make sure loopMIDI is running and the port is created. "
                f"Available ports: {available_out}"
            )
        out_port_name = matched_out[0]
        self.out_port = mido.open_output(out_port_name)
        logger.info(f"Output port opened: {out_port_name}")

    def _input_loop(self):
        """Read live MIDI input and add to rolling buffer."""
        logger.info("Input thread started")
        try:
            while self.running:
                # Poll for messages (non-blocking)
                for msg in self.in_port.iter_pending():
                    # Tag messages with current pulse if clock available
                    pulse = None
                    if self.clock_grid:
                        try:
                            pulse = self.clock_grid.get_pulse_count()
                        except Exception:
                            pulse = None

                    if msg.type == 'note_on':
                        # Set anchor on first human note if clock is running
                        if self.anchor_pulse is None and self.clock_grid and self.clock_grid.get_is_running():
                            self.anchor_pulse = pulse
                            pulses_per_bar = self.clock_grid.get_pulses_per_bar()
                            self.next_bar_boundary_pulse = self.anchor_pulse + pulses_per_bar
                            self.bar_index = 0
                            logger.info(f"[anchor] set at pulse={self.anchor_pulse}, pulses_per_bar={pulses_per_bar}")

                        # Assign to bar buffer
                        if self.anchor_pulse is not None and pulse is not None:
                            pulses_per_bar = self.clock_grid.get_pulses_per_bar()
                            bar = (pulse - self.anchor_pulse) // pulses_per_bar
                            if bar not in self.human_bar_buffers:
                                self.human_bar_buffers[bar] = []
                        else:
                            bar = None

                        msg_obj = type('MidiMsg', (), {'pulse': pulse, 'msg_type': 'note_on', 'note': msg.note, 'velocity': msg.velocity})()
                        self.midi_buffer.add_message('note_on', note=msg.note, velocity=msg.velocity, pulse=pulse)
                        if bar is not None:
                            self.human_bar_buffers[bar].append(msg_obj)
                        logger.info(f"[HUMAN] bar={bar} note_on pitch={msg.note} vel={msg.velocity} pulse={pulse}")

                    elif msg.type == 'note_off':
                        # Assign note_off to the same bar as note_on
                        if self.anchor_pulse is not None and pulse is not None:
                            pulses_per_bar = self.clock_grid.get_pulses_per_bar()
                            bar = (pulse - self.anchor_pulse) // pulses_per_bar
                            if bar not in self.human_bar_buffers:
                                self.human_bar_buffers[bar] = []
                        else:
                            bar = None

                        msg_obj = type('MidiMsg', (), {'pulse': pulse, 'msg_type': 'note_off', 'note': msg.note, 'velocity': msg.velocity})()
                        self.midi_buffer.add_message(
                            'note_off',
                            note=msg.note,
                            velocity=msg.velocity,
                            pulse=pulse,
                        )
                        if bar is not None:
                            self.human_bar_buffers[bar].append(msg_obj)
                        logger.debug(f"[HUMAN] bar={bar} note_off pitch={msg.note} pulse={pulse}")

                    elif msg.type == 'control_change' and msg.control == 64:
                        # Sustain pedal - assign to bar buffer
                        if self.anchor_pulse is not None and pulse is not None:
                            pulses_per_bar = self.clock_grid.get_pulses_per_bar()
                            bar = (pulse - self.anchor_pulse) // pulses_per_bar
                            if bar not in self.human_bar_buffers:
                                self.human_bar_buffers[bar] = []
                        else:
                            bar = None

                        msg_obj = type('MidiMsg', (), {'pulse': pulse, 'msg_type': 'control_change', 'control': 64, 'value': msg.value})()
                        self.midi_buffer.add_message(
                            'control_change',
                            control=64,
                            value=msg.value,
                            pulse=pulse,
                        )
                        if bar is not None:
                            self.human_bar_buffers[bar].append(msg_obj)
                        logger.debug(f"[HUMAN] bar={bar} sustain={msg.value} pulse={pulse}")

                time.sleep(0.001)  # Small sleep to avoid busy loop

        except Exception as e:
            logger.exception(f"Input loop error: {e}")

    def _generation_loop(self):
        """
        MVP Generation Loop:
        - Monitor bar boundaries (PHASE_HUMAN)
        - Check if AI response is ready and schedule it (if PHASE_HUMAN and response ready)
        - Block during PHASE_AI_PLAY (no new generation)
        """
        logger.info(f"Generation thread started (MVP 1-bar-in -> {self.gen_measures}-measures-out)")
        try:
            last_failsafe_check = time.time()
            while self.running:
                # Check for pending AI response ready to schedule
                if self.phase == self.PHASE_HUMAN and self.pending_ai_job is not None:
                    self._check_and_schedule_ai_response()

                # Bar-based boundary detection (only in HUMAN phase)
                if self.phase == self.PHASE_HUMAN and self.clock_grid and self.next_bar_boundary_pulse is not None:
                    current_pulse = self.clock_grid.get_pulse_count()
                    if current_pulse >= self.next_bar_boundary_pulse:
                        finished_bar = self.bar_index
                        self._on_bar_boundary(finished_bar)
                        # Update for next bar
                        pulses_per_bar = self.clock_grid.get_pulses_per_bar()
                        self.bar_index += 1
                        self.next_bar_boundary_pulse += pulses_per_bar

                # Failsafe check
                now = time.time()
                if (now - last_failsafe_check > 6.0) and self.anchor_pulse is not None:
                    all_msgs = self.midi_buffer.get_messages()
                    has_notes = any(m.msg_type == 'note_on' and m.velocity > 0 for m in all_msgs)
                    if has_notes and not self.failsafe_forced:
                        logger.warning(f"[FAILSAFE] No generation in 6s despite human input.")
                        self.failsafe_forced = True
                    last_failsafe_check = now

                time.sleep(0.01)  # Check 100 times per second
        except Exception as e:
            logger.exception(f"Generation loop error: {e}")

    def _has_human_activity(self) -> bool:
        """Check if there's any human activity (note_on or CC changes) in the buffer."""
        messages = self.midi_buffer.get_messages()
        for msg in messages:
            # Human activity: note_on with vel>0 or sustain pedal
            if (msg.msg_type == 'note_on' and msg.velocity and msg.velocity > 0) or \
               (msg.msg_type == 'control_change' and msg.control == 64):
                return True
        return False

    def _trigger_generation(self):
        """Snapshot buffer and queue generation."""
        try:
            # legacy: time-based trigger not used when ClockGrid is active
            logger.debug("_trigger_generation called (legacy/time-based). Ignored when using ClockGrid.")

        except Exception as e:
            logger.exception(f"Generation trigger error (will skip): {e}")
            self.skip_count += 1

    def _output_loop(self):
        """Send queued MIDI files with precise timing using mido.play()."""
        logger.info("Output thread started")
        try:
            import mido

            while self.running:
                try:
                    try:
                        msg_type, msg_data = self.event_queue.get(timeout=0.05)
                    except queue.Empty:
                        msg_type = None

                    if msg_type == 'midi_file':
                        midi_path = msg_data
                        # When a midi_file event arrives from legacy path, play immediately
                        self._play_midi_file_with_timing(midi_path)

                    # Also service scheduled model messages (pulse-scheduled)
                    self._service_scheduled_messages()

                except queue.Empty:
                    pass
                except Exception as e:
                    logger.exception(f"Output error: {e}")

        except Exception as e:
            logger.exception(f"Output loop error: {e}")

    def _service_scheduled_messages(self):
        """Send any scheduled model messages whose target_pulse <= current pulse.
        
        Ensures events are removed from queue after sending (one-shot, no repeats).
        """
        if not self.clock_grid:
            return

        current_pulse = self.clock_grid.get_pulse_count()

        to_send = []
        with self.scheduled_lock:
            remaining = []
            for target_pulse, msg in self.scheduled_messages:
                if current_pulse >= target_pulse:
                    to_send.append((target_pulse, msg))
                else:
                    remaining.append((target_pulse, msg))
            self.scheduled_messages = remaining

        # Send messages due (one-shot: removed from queue immediately after)
        for tp, msg in to_send:
            try:
                self.out_port.send(msg)
                logger.debug(f"OUT scheduled: {msg.type} target_pulse={tp} now={current_pulse}")
            except Exception:
                logger.exception("Failed to send scheduled message")

        # If model end pulse reached, switch back to HUMAN and clear buffers
        if self.model_end_pulse is not None and current_pulse >= self.model_end_pulse:
            if self.phase == self.PHASE_AI_PLAY:
                queue_size = len(self.scheduled_messages)
                logger.info(f"[phase] AI_PLAY -> HUMAN at pulse={current_pulse}, playback finished, queue_size={queue_size}")
                if queue_size > 0:
                    logger.warning(f"[service] {queue_size} events still queued, clearing.")
                    with self.scheduled_lock:
                        self.scheduled_messages.clear()
                # Clear human buffers for next cycle
                self.human_bar_buffers.clear()
                logger.debug("[service] Cleared human_bar_buffers for next cycle")
                # Reset bars collected counter for next collection phase
                self.bars_collected_in_phase = 0
                logger.debug("[service] Reset bars_collected_in_phase for next cycle")
            self.phase = self.PHASE_HUMAN
            self.model_end_pulse = None

    def _on_bar_boundary(self, finished_bar: int):
        """
        MVP Cycle: N-bars-in -> M-measures-out (both configurable)
        
        PHASE_HUMAN:
        - Collect input for each bar (increment bars_collected_in_phase)
        - When bars_collected_in_phase == human_measures: trigger M-measure generation
        - Switch to PHASE_AI_PLAY once response is ready
        
        PHASE_AI_PLAY:
        - Play the M-measure AI response
        - Do NOT trigger new generation (block generation while playing)
        - At playback end: switch back to PHASE_HUMAN, clear buffers, reset counter
        """
        try:
            logger.info(f"[bar_boundary] finished_bar={finished_bar}, phase={self.phase}, bars_collected={self.bars_collected_in_phase}")

            if self.phase == self.PHASE_HUMAN:
                # Check if we have events for this bar
                if finished_bar not in self.human_bar_buffers:
                    logger.info(f"[bar_boundary] No human events for bar {finished_bar}, skipping")
                    return

                human_events = self.human_bar_buffers[finished_bar]
                if not human_events:
                    logger.info(f"[bar_boundary] Empty buffer for bar {finished_bar}, skipping")
                    return

                # Increment collected bars counter
                self.bars_collected_in_phase += 1
                logger.info(f"[bar_boundary] Bar {finished_bar}: {len(human_events)} events, collected {self.bars_collected_in_phase}/{self.human_measures} bars")

                # Check if we've collected enough bars to trigger generation
                if self.bars_collected_in_phase < self.human_measures:
                    logger.info(f"[bar_boundary] Waiting for {self.human_measures - self.bars_collected_in_phase} more bars before generating")
                    return

                # Collect all bars for prompt
                prompt_events = []
                for i in range(finished_bar - self.human_measures + 1, finished_bar + 1):
                    if i >= 0 and i in self.human_bar_buffers:
                        prompt_events.extend(self.human_bar_buffers[i])

                logger.info(f"[bar_boundary] Collected {self.human_measures} bars ({len(prompt_events)} total events), triggering {self.gen_measures}-measure generation")

                # Enqueue generation job for M measures
                job = GenerationJob(
                    bar_index=finished_bar,
                    prompt_events=prompt_events,
                    aria_engine=self.aria_engine,
                    sampling_state=self.sampling_state,
                    gen_bars=self.gen_measures,  # Generate M measures per cycle
                )
                self.pending_ai_job = job
                self.gen_job_queue.put(job)
                logger.info(f"[enqueue] {self.gen_measures}-measure generation job for bar {finished_bar} queued (after {self.human_measures}-bar collection)")
                self.last_generation_time = time.time()
                self.generation_count += 1
                
                # Reset counter for next collection phase
                self.bars_collected_in_phase = 0

            elif self.phase == self.PHASE_AI_PLAY:
                # Block new generation while AI is playing
                logger.debug(f"[bar_boundary] In PHASE_AI_PLAY, skipping generation trigger")

        except Exception as e:
            logger.exception(f"Error on bar boundary: {e}")

    def _check_and_schedule_ai_response(self):
        """
        Check if the pending AI job has finished generation.
        If ready, schedule the N-measure response and switch to PHASE_AI_PLAY.
        """
        if self.pending_ai_job is None or self.pending_ai_job.result_midi_path is None:
            return  # Not ready yet

        midi_path = self.pending_ai_job.result_midi_path
        job_bar = self.pending_ai_job.bar_index
        
        logger.info(f"[ai_ready] {self.gen_measures}-measure response ready for job at bar {job_bar}, scheduling playback")
        
        # Get current pulse as boundary for playback
        if not self.clock_grid:
            logger.warning("[ai_ready] No clock grid available, cannot schedule")
            return
        
        boundary_pulse = self.clock_grid.get_pulse_count()
        pulses_per_bar = self.clock_grid.get_pulses_per_bar()
        
        # Schedule the N-measure playback
        self._schedule_two_bar_response(midi_path, boundary_pulse, pulses_per_bar)
        
        # Switch phase
        self.phase = self.PHASE_AI_PLAY
        self.pending_ai_job = None  # Clear pending job
        
        logger.info(f"[phase] HUMAN -> AI_PLAY at pulse={boundary_pulse}")

    def _schedule_two_bar_response(self, midi_path: str, boundary_pulse: int, pulses_per_bar: int):
        """
        Schedule an N-measure AI response for playback.
        
        Enforces strict N-measure limit:
        - Keep only events with 0 <= offset_pulse < N*pulses_per_bar
        - Force note-offs for any unclosed notes
        - Send CC123 at end
        """
        try:
            import mido
            
            max_offset_pulses = self.gen_measures * pulses_per_bar  # N measures in pulses
            
            mid = mido.MidiFile(midi_path)
            tpq = mid.ticks_per_beat if mid.ticks_per_beat else self.ticks_per_beat
            
            messages = []
            active_notes = {}  # {pitch: velocity} for unclosed notes
            abs_tick = 0
            
            # Parse MIDI and apply N-measure limit
            for track in mid.tracks:
                abs_tick = 0
                for msg in track:
                    abs_tick += msg.time
                    if not hasattr(msg, 'type'):
                        continue
                    
                    # Compute offset in pulses
                    offset_pulses = int((abs_tick / float(tpq)) * 24.0)
                    
                    # **ENFORCE N-measure limit**: Discard events beyond boundary
                    if offset_pulses >= max_offset_pulses:
                        logger.debug(f"[schedule_2bar] Discarding event at offset={offset_pulses} (>= limit {max_offset_pulses})")
                        continue
                    
                    target_pulse = boundary_pulse + offset_pulses
                    
                    if msg.type == 'note_on' and msg.velocity > 0:
                        active_notes[msg.note] = msg.velocity
                        messages.append((target_pulse, msg.copy()))
                    
                    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                        if msg.note in active_notes:
                            del active_notes[msg.note]
                        messages.append((target_pulse, msg.copy()))
                    
                    elif msg.type == 'control_change':
                        messages.append((target_pulse, msg.copy()))
            
            # **ENFORCE**: Force note-offs for unclosed notes at N-measure end
            end_pulse = boundary_pulse + max_offset_pulses
            for pitch in list(active_notes.keys()):
                note_off = mido.Message('note_off', note=pitch, velocity=0)
                messages.append((end_pulse, note_off))
                logger.debug(f"[schedule_2bar] Forced note_off for pitch {pitch} at {self.gen_measures}-measure end")
            
            # Send CC123 at end
            all_notes_off = mido.Message('control_change', control=123, value=0)
            messages.append((end_pulse, all_notes_off))
            
            # Sort by target_pulse
            messages.sort(key=lambda x: x[0])
            
            # Clear old events and schedule new ones
            queue_size_before = len(self.scheduled_messages)
            if queue_size_before > 0:
                logger.warning(f"[schedule_2bar] Clearing {queue_size_before} old scheduled events before new response")
            
            with self.scheduled_lock:
                self.scheduled_messages.clear()
                self.scheduled_messages.extend(messages)
            
            # Set model end pulse
            self.model_end_pulse = end_pulse
            
            # Log pulse ranges
            if messages:
                pulse_min = min(tp for tp, _ in messages)
                pulse_max = max(tp for tp, _ in messages)
                logger.info(
                    f"[schedule_2bar] {self.gen_measures}-measure response: {len(messages)} events in pulse [{boundary_pulse}..{end_pulse}), "
                    f"min={pulse_min} max={pulse_max}"
                )
            
            # Cleanup temp MIDI
            try:
                os.unlink(midi_path)
            except Exception:
                pass
        
        except Exception as e:
            logger.exception(f"Failed to schedule {self.gen_measures}-measure response: {e}")

    def _try_schedule_ready_bar(self, current_bar: int):
        """DEPRECATED: use _check_and_schedule_ai_response for MVP"""
        pass

    def _schedule_single_bar_playback(self, bar_index: int, midi_path: str, boundary_pulse: int):
        """DEPRECATED: use _schedule_two_bar_response for MVP"""
        pass

    def _on_block_boundary(self, boundary_pulse: int):
        """Legacy handler - not used in pipelined mode. Kept for compatibility."""
        logger.debug(f"[_on_block_boundary] called but pipelined mode uses _on_bar_boundary")
        pass

    def _schedule_generated_midi(self, midi_path: str, boundary_pulse: int):
        """Convert generated MIDI file into pulse-scheduled messages and enqueue them.
        boundary_pulse is the pulse index at which the model should start playing (i.e., immediate next pulse).
        """
        try:
            import mido

            mid = mido.MidiFile(midi_path)
            tpq = mid.ticks_per_beat if mid.ticks_per_beat else self.ticks_per_beat

            abs_tick = 0
            messages = []
            for track in mid.tracks:
                abs_tick = 0
                for msg in track:
                    abs_tick += msg.time
                    if not hasattr(msg, 'type'):
                        continue
                    if msg.type in ('note_on', 'note_off', 'control_change'):
                        # Convert tick -> pulse: pulse_delta = (tick / ticks_per_beat) * 24
                        pulse_delta = int((abs_tick / float(tpq)) * 24.0)
                        target_pulse = boundary_pulse + pulse_delta
                        messages.append((target_pulse, msg.copy()))

            # Merge into scheduled_messages list (thread-safe)
            with self.scheduled_lock:
                self.scheduled_messages.extend(messages)

            # Set model end pulse
            pulses_per_block = self.clock_grid.get_pulses_per_block()
            self.model_end_pulse = boundary_pulse + pulses_per_block

            # Cleanup generated midi file
            try:
                os.unlink(midi_path)
            except Exception:
                pass

            logger.info(f"[schedule] Scheduled {len(messages)} generated events starting at pulse={boundary_pulse}")

        except Exception as e:
            logger.exception(f"Failed to schedule generated MIDI: {e}")

    def _parse_generated_midi_for_bar(self, bar_index: int, midi_path: str):
        """Parse generated MIDI and store for bar playback."""
        try:
            import mido
            mid = mido.MidiFile(midi_path)
            messages = []
            for track in mid.tracks:
                for msg in track:
                    if hasattr(msg, 'type') and msg.type in ('note_on', 'note_off', 'control_change'):
                        messages.append(msg.copy())
            self.generated_bar_queue[bar_index] = messages
            logger.info(f"[parse] Bar {bar_index}: {len(messages)} messages stored")
        except Exception as e:
            logger.exception(f"Failed to parse generated MIDI for bar {bar_index}: {e}")

    def _schedule_2bar_playback(self, bar1: int, bar2: int):
        """DEPRECATED: Legacy 2-bar scheduling. Use _schedule_single_bar_playback instead.
        
        Kept for reference; not called in pipelined mode.
        """
        logger.debug(f"[_schedule_2bar_playback] DEPRECATED - called for bars {bar1}-{bar2} (ignored)")
        pass
    
    def _play_midi_file_with_timing(self, midi_path: str):
        """Load and play a MIDI file with proper timing to output port."""
        try:
            import mido
            
            mid = mido.MidiFile(midi_path)
            total_time = mid.length
            msg_count = 0
            
            # Use mid.play() to iterate through messages with absolute timing
            for msg in mid.play():
                if not self.running:
                    break
                    
                # Filter out meta messages, only send channel messages
                if msg.type in ('note_on', 'note_off', 'control_change'):
                    # Sleep for the message's elapsed time
                    msg_time = msg.time
                    
                    # Optionally quantize to 1/16 grid
                    if self.quantize and self.tempo_tracker:
                        bpm = self.tempo_tracker.get_bpm()
                        # Sixteenth note duration in seconds
                        sixteenth_dur = (60.0 / bpm) / 4.0
                        # Quantize to nearest sixteenth
                        quantized_time = round(msg_time / sixteenth_dur) * sixteenth_dur
                        msg_time = max(0, quantized_time)
                    
                    if msg_time > 0:
                        time.sleep(msg_time)
                    
                    self.out_port.send(msg)
                    msg_count += 1
                    logger.debug(f"OUT: {msg.type} (t={msg.time:.3f}s)")
            
            logger.info(f"Sent {msg_count} MIDI messages in {total_time:.2f}s")
            
        except Exception as e:
            logger.error(f"Failed to play MIDI file {midi_path}: {e}")
        finally:
            # Cleanup temp file after playback
            try:
                os.unlink(midi_path)
            except:
                pass
