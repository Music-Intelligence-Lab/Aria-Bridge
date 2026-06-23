"""MIDI Clock tempo tracker for real-time Ableton sync."""

import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# MIDI clock: 24 pulses per quarter note (standard)
PPQN = 24


class TempoTracker:
    """
    Listens to MIDI clock messages and tracks current BPM.
    Computes BPM from inter-clock intervals using a rolling average.
    """

    def __init__(self, clock_port_name: str = "ARIA_CLOCK", window_pulses: int = 96):
        """
        Args:
            clock_port_name: MIDI input port name for clock (e.g., "ARIA_CLOCK")
            window_pulses: Number of clock pulses for rolling average (96 = 4 beats at 24ppqn)
        """
        self.clock_port_name = clock_port_name
        self.window_pulses = window_pulses
        
        self.clock_port = None
        self.is_running = False
        self.current_bpm = 120.0  # Default fallback
        
        # Clock tracking
        self.last_clock_time = None
        self.clock_intervals = deque(maxlen=window_pulses)
        self.pulse_count = 0
        
        # Thread control
        self.lock = threading.RLock()
        self.running = False
        self.thread = None
        
        # Last BPM update time (for throttled logging)
        self.last_bpm_log_time = 0

    def start(self):
        """Start listening for MIDI clock messages."""
        try:
            import mido
        except ImportError:
            raise ImportError("mido is required. Install with: pip install mido")

        try:
            self.clock_port = mido.open_input(self._resolve_port_name())
            logger.info(f"Clock port opened: {self.clock_port_name}")
        except Exception as e:
            logger.error(f"Failed to open clock port '{self.clock_port_name}': {e}")
            logger.info("Listing available input ports: " + ", ".join(mido.get_input_names()))
            raise

        self.running = True
        self.thread = threading.Thread(target=self._clock_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop listening for MIDI clock."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.clock_port:
            self.clock_port.close()
            logger.info("Clock port closed")

    def _resolve_port_name(self) -> str:
        """
        Resolve port name, handling the case where port names include numbers.
        E.g., "ARIA_CLOCK" might be listed as "ARIA_CLOCK 0".
        """
        import mido
        
        try:
            # Try exact name first
            avail = mido.get_input_names()
            if self.clock_port_name in avail:
                return self.clock_port_name

            # Prefer prefix (Windows loopMIDI), fall back to substring (macOS IAC names).
            n = self.clock_port_name.lower()
            matched = [p for p in avail if p.lower().startswith(n)] or \
                      [p for p in avail if n in p.lower()]
            if matched:
                return matched[0]

            # Fallback to exact name (will error with helpful message)
            return self.clock_port_name
        except Exception:
            return self.clock_port_name

    def _clock_loop(self):
        """Listen for MIDI clock messages."""
        logger.info("Clock listening thread started")
        try:
            while self.running:
                for msg in self.clock_port.iter_pending():
                    self._handle_clock_message(msg)
                time.sleep(0.001)  # Small sleep to avoid busy loop
        except Exception as e:
            logger.exception(f"Clock loop error: {e}")

    def _handle_clock_message(self, msg):
        """Process a single MIDI clock message."""
        if msg.type == 'start':
            with self.lock:
                self.is_running = True
                self.pulse_count = 0
                self.last_clock_time = None
                self.clock_intervals.clear()
                logger.info("MIDI Clock: START")

        elif msg.type == 'continue':
            with self.lock:
                self.is_running = True
                logger.debug("MIDI Clock: CONTINUE")

        elif msg.type == 'stop':
            with self.lock:
                self.is_running = False
                logger.info("MIDI Clock: STOP")

        elif msg.type == 'clock':
            self._handle_clock_pulse()

    def _handle_clock_pulse(self):
        """Process a clock pulse and update BPM estimate."""
        now = time.monotonic()

        with self.lock:
            if self.last_clock_time is not None:
                interval = now - self.last_clock_time
                self.clock_intervals.append(interval)

                # Update BPM from rolling average
                if len(self.clock_intervals) > 1:
                    avg_interval = sum(self.clock_intervals) / len(self.clock_intervals)
                    # avg_interval is seconds per clock pulse
                    # BPM = 60 / (seconds per beat) = 60 / (avg_interval * 24)
                    if avg_interval > 0:
                        self.current_bpm = 60.0 / (avg_interval * PPQN)

                # Log BPM updates (throttled)
                now_time = time.time()
                if now_time - self.last_bpm_log_time > 1.0:
                    logger.info(f"BPM: {self.current_bpm:.1f}")
                    self.last_bpm_log_time = now_time

            self.last_clock_time = now
            self.pulse_count += 1

    def get_bpm(self) -> float:
        """Get current BPM estimate."""
        with self.lock:
            return self.current_bpm

    def get_is_running(self) -> bool:
        """Check if MIDI clock is running."""
        with self.lock:
            return self.is_running

    def get_microseconds_per_beat(self) -> int:
        """Get tempo in microseconds per beat (for MIDI meta messages)."""
        bpm = self.get_bpm()
        return int(60_000_000 / bpm)
