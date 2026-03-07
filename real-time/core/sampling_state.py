"""Thread-safe sampling hyperparameter state with bounded adjustments."""

import threading


class SamplingState:
    """Holds temperature, top_p, min_p with thread-safe getters/setters."""

    def __init__(self, temperature: float, top_p: float, min_p: float | None):
        self._lock = threading.Lock()
        self.temperature = round(temperature, 2)
        self.top_p = round(top_p, 2)
        self.min_p = round(0.0 if min_p is None else min_p, 2)

    # --- helpers ---
    def _clamp(self, val, lo, hi):
        return max(lo, min(hi, val))

    def increase_temperature(self):
        with self._lock:
            self.temperature = round(self._clamp(self.temperature + 0.05, 0.1, 2.0), 2)
            return self.temperature

    def decrease_temperature(self):
        with self._lock:
            self.temperature = round(self._clamp(self.temperature - 0.05, 0.1, 2.0), 2)
            return self.temperature

    def increase_top_p(self):
        with self._lock:
            self.top_p = round(self._clamp(self.top_p + 0.01, 0.1, 1.0), 2)
            return self.top_p

    def decrease_top_p(self):
        with self._lock:
            self.top_p = round(self._clamp(self.top_p - 0.01, 0.1, 1.0), 2)
            return self.top_p

    def increase_min_p(self):
        with self._lock:
            self.min_p = round(self._clamp(self.min_p + 0.01, 0.0, 0.2), 2)
            return self.min_p

    def decrease_min_p(self):
        with self._lock:
            self.min_p = round(self._clamp(self.min_p - 0.01, 0.0, 0.2), 2)
            return self.min_p

    def get_values(self):
        with self._lock:
            return self.temperature, self.top_p, self.min_p

    # direct setters (used by OSC)
    def set_temperature(self, v: float):
        with self._lock:
            self.temperature = round(self._clamp(v, 0.1, 2.0), 2)
            return self.temperature

    def set_top_p(self, v: float):
        with self._lock:
            self.top_p = round(self._clamp(v, 0.1, 1.0), 2)
            return self.top_p

    def set_min_p(self, v: float):
        with self._lock:
            self.min_p = round(self._clamp(v, 0.0, 0.2), 2)
            return self.min_p


class SessionState:
    """Thread-safe session status and last output path."""

    def __init__(self, mode: str = "manual"):
        self._lock = threading.Lock()
        self.status = "IDLE"
        self.mode = mode
        self.last_output_path = None
        self.has_pending_output = False
        self.is_recording = False
        self.last_record_level = None
        self.max_tokens = None  # Optional override for generation budget

    def set_status(self, status: str):
        with self._lock:
            self.status = status

    def set_last_output(self, path: str | None):
        with self._lock:
            self.last_output_path = path
            self.has_pending_output = path is not None

    def get_snapshot(self):
        with self._lock:
            return {
                "mode": self.mode,
                "status": self.status,
                "last_output_path": self.last_output_path,
                "has_pending_output": self.has_pending_output,
                "is_recording": self.is_recording,
                "last_record_level": self.last_record_level,
                "max_tokens": self.max_tokens,
            }

    def set_record_level(self, level: int):
        with self._lock:
            self.last_record_level = level

    def set_recording(self, flag: bool):
        with self._lock:
            self.is_recording = flag

    def set_max_tokens(self, val: int | None):
        with self._lock:
            self.max_tokens = val

    def get_max_tokens(self) -> int | None:
        with self._lock:
            return self.max_tokens
