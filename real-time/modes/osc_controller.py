"""Optional OSC control plane for Max for Live integration."""

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class OscController:
    def __init__(
        self,
        host: str,
        in_port: int,
        out_port: int,
        sampling_state,
        session_state,
        command_queue,
        commit_cb=None,
        grade_cb=None,
        feedback_param_cb=None,
    ):
        self.host = host
        self.in_port = in_port
        self.out_port = out_port
        self.sampling_state = sampling_state
        self.session_state = session_state
        self.command_queue = command_queue
        self.commit_cb = commit_cb
        self.grade_cb = grade_cb
        self.feedback_param_cb = feedback_param_cb
        self.server = None
        self.client = None
        self.stop_event = threading.Event()
        self.thread = None
        self.dispatcher = None
        # Track initial sync from M4L
        self._startup_lock = threading.Lock()
        self._startup_events: Dict[str, threading.Event] = {
            "temp": threading.Event(),
            "top_p": threading.Event(),
            "min_p": threading.Event(),
            "tokens": threading.Event(),
        }
        self._startup_state: Dict[str, Optional[float]] = {
            "temp": None,
            "top_p": None,
            "min_p": None,
            "tokens": None,
        }
        self._debug_enabled = False

    def start(self):
        try:
            from pythonosc import dispatcher, osc_server, udp_client
        except Exception as e:  # pragma: no cover - optional dep
            logger.error(f"python-osc not available: {e}")
            return

        disp = dispatcher.Dispatcher()
        disp.map("/aria/record", self._handle_record)
        disp.map("/aria/temp", self._handle_temp)
        disp.map("/aria/top_p", self._handle_top_p)
        disp.map("/aria/min_p", self._handle_min_p)
        disp.map("/aria/tokens", self._handle_tokens)
        disp.map("/aria/cancel", self._handle_cancel)
        disp.map("/aria/ping", self._handle_ping)
        disp.map("/aria/play", self._handle_play)
        disp.map("/aria/coherence", self._handle_coherence)
        disp.map("/aria/repetition", self._handle_repetition)
        disp.map("/aria/taste", self._handle_taste)
        disp.map("/aria/continuity", self._handle_continuity)
        disp.map("/aria/grade", self._handle_grade)
        disp.map("/aria/commit", self._handle_commit)
        self.dispatcher = disp

        try:
            # Outbound client remains for status/logs; no startup request is sent.
            self.client = udp_client.SimpleUDPClient(self.host, self.out_port)
            self.server = osc_server.ThreadingOSCUDPServer((self.host, self.in_port), disp)
            self.server.timeout = 0.2
        except Exception as e:
            logger.error(f"Failed to start OSC server: {e}")
            return

        def _serve():
            logger.info(f"OSC server listening on {self.host}:{self.in_port}")
            while not self.stop_event.is_set():
                self.server.handle_request()
            logger.info("OSC server stopped")

        self.thread = threading.Thread(target=_serve, daemon=True)
        self.thread.start()

    def _record_startup_value(self, key: str, value: float | int):
        # Save first-seen values for startup sync and mark event
        with self._startup_lock:
            self._startup_state[key] = value
        if key in self._startup_events:
            self._startup_events[key].set()

    def _startup_snapshot(self) -> Dict[str, Optional[float]]:
        with self._startup_lock:
            return dict(self._startup_state)

    def _enable_debug_logging(self):
        if self.dispatcher and not self._debug_enabled:
            self.dispatcher.set_default_handler(self._debug_handler, needs_reply_address=False)
            self._debug_enabled = True

    def _disable_debug_logging(self):
        if self.dispatcher and self._debug_enabled:
            self.dispatcher.set_default_handler(None)
            self._debug_enabled = False

    def _debug_handler(self, address, *args):
        logger.info(f"[OSC][debug] {address} {args}")

    def sync_state_on_startup(self, timeout: float = 3.0) -> Dict[str, Optional[float]]:
        """
        Wait briefly for Max for Live to push its current parameters, then
        apply them to the shared sampling/session config.
        """
        if not self.client or not self.server:
            logger.warning("OSC controller not started; skipping startup sync.")
            temp, top_p, min_p = self.sampling_state.get_values()
            return {
                "temp": temp,
                "top_p": top_p,
                "min_p": min_p,
                "tokens": self.session_state.get_max_tokens(),
            }

        # Default state falls back to current values; overwritten on success
        base_temp, base_top_p, base_min_p = self.sampling_state.get_values()
        state: Dict[str, Optional[float | int]] = {
            "temp": base_temp,
            "top_p": base_top_p,
            "min_p": base_min_p,
            "tokens": self.session_state.get_max_tokens(),
        }

        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if all(ev.is_set() for ev in self._startup_events.values()):
                    break
                time.sleep(0.01)

            snapshot = self._startup_snapshot()
            missing = [k for k, v in snapshot.items() if v is None]

            temp, top_p, min_p = self.sampling_state.get_values()
            state = {
                "temp": float(snapshot["temp"]) if snapshot["temp"] is not None else temp,
                "top_p": float(snapshot["top_p"]) if snapshot["top_p"] is not None else top_p,
                "min_p": float(snapshot["min_p"]) if snapshot["min_p"] is not None else min_p,
                "tokens": (
                    int(snapshot["tokens"])
                    if snapshot["tokens"] is not None
                    else self.session_state.get_max_tokens()
                ),
            }

            if missing:
                logger.warning("No OSC state received at startup. Waiting for Ableton push...")

            # Apply to live config
            self.sampling_state.set_temperature(state["temp"])
            self.sampling_state.set_top_p(state["top_p"])
            self.sampling_state.set_min_p(state["min_p"])
            if state["tokens"] is not None:
                self.session_state.set_max_tokens(int(state["tokens"]))

            logger.info(f"Startup sync complete. Current params: {state}")
        finally:
            # Turn off noisy default handler after sync attempt (if ever enabled)
            self._disable_debug_logging()

        return state

    def stop(self):
        self.stop_event.set()
        if self.server:
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1)

    # Outgoing helpers
    def send_status(self, status: str):
        if not self.client:
            return
        try:
            self.client.send_message("/aria/status", status)
        except Exception:
            logger.debug("Failed to send OSC status")

    def send_params(self):
        if not self.client:
            return
        try:
            t, tp, mp = self.sampling_state.get_values()
            self.client.send_message("/aria/params", [t, tp, mp])
        except Exception:
            logger.debug("Failed to send OSC params")

    def send_log(self, msg: str):
        if not self.client:
            return
        try:
            self.client.send_message("/aria/log", msg)
        except Exception:
            logger.debug("Failed to send OSC log")

    @staticmethod
    def _coerce_flag(val):
        try:
            # Numeric path
            f = float(val)
            return 1 if f >= 0.5 else 0
        except Exception:
            pass
        if isinstance(val, str):
            if val.strip() in ("1", "true", "True", "on"):
                return 1
            if val.strip() in ("0", "false", "False", "off"):
                return 0
        if isinstance(val, bool):
            return 1 if val else 0
        return None

    # Handlers
    def _handle_record(self, addr, *args):
        # Debug: show raw payload
        logger.info(f"[OSC] {addr} {args} {type(args[0]) if args else None}")
        if not args:
            return

        flag = self._coerce_flag(args[0])
        if flag is None:
            self.send_log("Invalid /aria/record payload (ignored)")
            return

        snap = self.session_state.get_snapshot()
        last_level = snap.get("last_record_level")
        if last_level == flag:
            self.send_log("Record level unchanged (ignored)")
            return
        is_recording = snap.get("is_recording")
        if flag == 1 and is_recording:
            self.send_log("Already recording; record=1 ignored")
            logger.info("[OSC] record=1 ignored (already recording)")
            return
        if flag == 0 and not is_recording:
            self.send_log("Not recording; record=0 ignored")
            logger.info("[OSC] record=0 ignored (not recording)")
            self.session_state.set_record_level(flag)
            return
        self.session_state.set_record_level(flag)
        if flag == 1:
            logger.info("[OSC] record=1 -> START")
            self.command_queue.put(("record_start", None))
            self.send_log("Record start requested (OSC)")
        else:
            logger.info("[OSC] record=0 -> STOP+GENERATE")
            self.command_queue.put(("record_stop", None))
            self.send_log("Record stop requested (OSC)")

    def _handle_cancel(self, addr, *args):
        self.command_queue.put(("cancel", 1))
        self.send_log("Cancel requested (OSC)")

    def _handle_play(self, addr, *args):
        logger.info("[OSC] play -> SEND OUTPUT")
        self.command_queue.put(("play", None))
        self.send_log("Play requested (OSC)")

    def _handle_feedback_param(self, name: str, args):
        if not args:
            return
        try:
            val = float(args[0])
        except Exception:
            logger.warning(f"Invalid payload for /aria/{name} (ignored)")
            return
        logger.info(f"[OSC] {name} -> {val}")
        if self.feedback_param_cb:
            self.feedback_param_cb(name, val)

    def _handle_coherence(self, addr, *args):
        self._handle_feedback_param("coherence", args)

    def _handle_repetition(self, addr, *args):
        self._handle_feedback_param("repetition", args)

    def _handle_taste(self, addr, *args):
        self._handle_feedback_param("taste", args)

    def _handle_continuity(self, addr, *args):
        self._handle_feedback_param("continuity", args)

    def _handle_grade(self, addr, *args):
        print("OSC RECEIVED:", addr, args)
        if not args:
            return
        try:
            grade = int(float(args[0]))
        except Exception:
            logger.warning("Invalid /aria/grade payload (ignored)")
            return
        logger.info(f"[OSC] grade -> {grade}")
        if self.grade_cb:
            self.grade_cb(grade)

    def _handle_commit(self, addr, *args):
        flag = 1
        if args:
            try:
                flag = int(float(args[0]))
            except Exception:
                flag = 0
        if flag >= 1:
            logger.info("[OSC] commit received")
            if self.commit_cb:
                self.commit_cb()
        else:
            logger.info("[OSC] commit ignored (flag not set)")

    def _handle_temp(self, addr, *args):
        if not args:
            return
        try:
            v = round(float(args[0]), 2)
        except Exception:
            return
        logger.info(f"Received /aria/temp: {v:.2f}")
        self.sampling_state.set_temperature(v)
        self._record_startup_value("temp", v)
        self.send_params()
        self.send_log(f"Temp -> {self.sampling_state.get_values()[0]:.2f}")

    def _handle_top_p(self, addr, *args):
        if not args:
            return
        try:
            v = round(float(args[0]), 2)
        except Exception:
            return
        logger.info(f"Received /aria/top_p: {v:.2f}")
        self.sampling_state.set_top_p(v)
        self._record_startup_value("top_p", v)
        self.send_params()
        self.send_log(f"Top_p -> {self.sampling_state.get_values()[1]:.2f}")

    def _handle_min_p(self, addr, *args):
        if not args:
            return
        try:
            v = round(float(args[0]), 2)
        except Exception:
            return
        logger.info(f"Received /aria/min_p: {v:.2f}")
        self.sampling_state.set_min_p(v)
        self._record_startup_value("min_p", v)
        self.send_params()
        self.send_log(f"Min_p -> {self.sampling_state.get_values()[2]:.2f}")

    def _handle_tokens(self, addr, *args):
        logger.info(f"Received /aria/tokens: {args[0] if args else 'None'}")
        if not args:
            return
        try:
            v = float(args[0])
        except Exception:
            self.send_log("Invalid /aria/tokens payload (ignored)")
            return
        # Clamp to integer range 0-2048
        clamped = int(max(0, min(2048, round(v))))
        self.session_state.set_max_tokens(clamped)
        self._record_startup_value("tokens", clamped)
        self.send_log(f"Max tokens -> {clamped}")

    def _handle_ping(self, addr, *args):
        self.send_status(self.session_state.get_snapshot().get("status", "UNKNOWN"))
        self.send_params()
