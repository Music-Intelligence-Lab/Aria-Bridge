#!/usr/bin/env python3
"""
Real-time Ableton bridge for Aria.

Usage (preset):
    python ableton_bridge.py plugin    --checkpoint <path>  # JUCE plugin / Max for Live
    python ableton_bridge.py m4l       --checkpoint <path>  # Max for Live device
    python ableton_bridge.py automatic --checkpoint <path>  # Clock-sync auto-generation
    python ableton_bridge.py manual    --checkpoint <path>  # Keyboard-driven, no OSC

Usage (explicit flags, still supported):
    python ableton_bridge.py --mode manual --m4l --in ARIA_IN --out ARIA_OUT --checkpoint <path>
"""

import argparse
import logging
import os
import platform
import sys
import threading
import queue
from pathlib import Path
from typing import Optional, Dict

try:
    from core.datastore import DataStore
except ImportError:  # Package import path
    from .core.datastore import DataStore

# ---------------------------------------------------------------------------
# Launch presets — each maps a short name to a set of argument defaults.
# Explicit CLI flags always override preset defaults.
# ---------------------------------------------------------------------------
PRESETS: dict = {
    # JUCE plugin standalone: manual recording, OSC control plane enabled.
    "plugin": {"mode": "manual", "m4l": True},
    # Max for Live device: same OSC-driven workflow as plugin.
    "m4l":    {"mode": "manual", "m4l": True},
    # Clock-sync: generation triggered automatically by Ableton MIDI clock.
    "automatic": {"mode": "clock"},
    # Keyboard-only: no OSC, start/stop via keyboard keys.
    "manual": {"mode": "manual"},
}

def _auto_detect_device() -> str:
    """Return the best available inference device: cuda > mlx > cpu."""
    # Apple Silicon — prefer MLX
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            import mlx.core  # noqa: F401
            return "mlx"
        except ImportError:
            pass
        return "cpu"
    # NVIDIA GPU — prefer CUDA
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def find_checkpoint(checkpoint_hint: Optional[str] = None) -> str:
    """
    Locate the checkpoint file. 
    
    Args:
        checkpoint_hint: Explicit path provided by user, or None to search defaults.
    
    Returns:
        Path to checkpoint file.
    
    Raises:
        FileNotFoundError: If checkpoint cannot be found.
    """
    # If user provided a path, use it directly
    if checkpoint_hint:
        if os.path.isfile(checkpoint_hint):
            logger.info(f"Using checkpoint: {checkpoint_hint}")
            return checkpoint_hint
        
        # Try relative paths from script location
        rel_paths = [
            Path(checkpoint_hint),
            Path(__file__).parent / checkpoint_hint,
            Path(__file__).parent.parent / checkpoint_hint,
        ]
        for p in rel_paths:
            if p.exists():
                logger.info(f"Found checkpoint: {p}")
                return str(p.resolve())

    # If no hint or hint not found, scan models/ directories for any .safetensors/.gen file
    import sys as _sys
    if getattr(_sys, "frozen", False):
        # exe is at <app>/resources/aria_backend.exe; models/ is at <app>/models/
        _exe = Path(_sys.executable).parent
        _bases = [_exe, _exe.parent]
    else:
        _bases = [Path(__file__).parent, Path(__file__).parent.parent, Path(".")]

    for base in _bases:
        models_dir = base / "models"
        if models_dir.is_dir():
            candidates = sorted(
                [f for f in models_dir.iterdir() if f.suffix in (".safetensors", ".gen")],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                logger.info(f"Found checkpoint: {candidates[0]}")
                return str(candidates[0].resolve())

    # Not found
    if checkpoint_hint:
        raise FileNotFoundError(
            f"Could not find checkpoint '{checkpoint_hint}'. "
            f"Provide --checkpoint with the correct path."
        )
    else:
        raise FileNotFoundError(
            f"No model found. Place a .safetensors file in the models/ folder next to the launcher."
        )


def get_midi_ports():
    """
    List available MIDI ports (input and output).
    """
    try:
        import mido
        logger.info("Available MIDI input ports:")
        for port in mido.get_input_names():
            logger.info(f"  - {port}")
        logger.info("Available MIDI output ports:")
        for port in mido.get_output_names():
            logger.info(f"  - {port}")
    except Exception as e:
        logger.warning(f"Could not list MIDI ports: {e}")


def sync_state_on_startup(osc_controller, timeout: float = 2.0):
    """
    Kick off the OSC controller's initial state sync and return the received state.
    """
    if not osc_controller:
        return None
    try:
        return osc_controller.sync_state_on_startup(timeout=timeout)
    except Exception as e:
        logger.warning(f"OSC startup sync failed: {e}")
        return None


class FeedbackManager:
    def __init__(self, datastore: DataStore):
        self.datastore = datastore
        self.lock = threading.Lock()
        self.current_episode_id: Optional[str] = None
        self.draft_pending: bool = False
        self.latest_grade: Optional[int] = None
        self.coherence: Optional[float] = None
        self.repetition: Optional[float] = None
        self.taste: Optional[float] = None
        self.continuity: Optional[float] = None

    def record_generation(self, prompt_bytes: bytes, output_bytes: bytes, params: Dict, mode: str) -> Optional[str]:
        with self.lock:
            if self.draft_pending:
                logger.warning("Feedback episode already pending commit; skipping new episode.")
                return None
            enriched = dict(params)
            enriched.update(
                {
                    "coherence": self.coherence,
                    "repetition": self.repetition,
                    "taste": self.taste,
                    "continuity": self.continuity,
                }
            )
            episode_id = self.datastore.create_episode(prompt_bytes, output_bytes, enriched, mode=mode)
            self.current_episode_id = episode_id
            self.draft_pending = True
            logger.info(f"[feedback] draft_pending -> True ({episode_id})")
            return episode_id

    def set_grade(self, grade: int):
        with self.lock:
            self.latest_grade = int(grade)

    def set_feedback_param(self, name: str, value: float):
        with self.lock:
            try:
                v = float(value)
            except Exception:
                return
            if name == "coherence":
                self.coherence = v
            elif name == "repetition":
                self.repetition = v
            elif name == "taste":
                self.taste = v
            elif name == "continuity":
                self.continuity = v

    def commit(self):
        with self.lock:
            grade = self.latest_grade if self.latest_grade is not None else 0
            feedback = {
                "coherence": self.coherence,
                "repetition": self.repetition,
                "taste": self.taste,
                "continuity": self.continuity,
            }

            episode_id = self.current_episode_id if (self.draft_pending and self.current_episode_id) else None

            if episode_id is None:
                logger.warning("Commit requested without draft_pending; checking for recent uncommitted episode.")
                episode_id = self.datastore.find_most_recent_draft_episode()

                if episode_id is None:
                    logger.warning("No pending feedback episode to commit.")
                    return

                logger.warning(f"Recovered recent uncommitted feedback episode: {episode_id}")

            self.datastore.finalize_episode(episode_id, grade, feedback=feedback)
            logger.info(f"Feedback episode {episode_id} finalized with grade={grade}.")
            self.current_episode_id = None
            self.draft_pending = False
            self.latest_grade = None


def _make_tray_icon():
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new('RGBA', (size, size), (26, 26, 46, 255))
    draw = ImageDraw.Draw(img)
    sw = max(3, int(size * 0.115))
    m = size * 0.12
    apex = (size * 0.5, m)
    left_pt = (m, size - m)
    right_pt = (size - m, size - m)
    bar_y = size * 0.57
    bar_lx = apex[0] + (bar_y - apex[1]) / (left_pt[1] - apex[1]) * (left_pt[0] - apex[0])
    bar_rx = apex[0] + (bar_y - apex[1]) / (right_pt[1] - apex[1]) * (right_pt[0] - apex[0])
    green = (80, 175, 76)
    draw.line([apex, left_pt],           fill=green, width=sw)
    draw.line([apex, right_pt],          fill=green, width=sw)
    draw.line([(bar_lx, bar_y), (bar_rx, bar_y)], fill=green, width=sw)
    return img


def _stdin_watchdog():
    try:
        while sys.stdin.buffer.read(1):
            pass
    except Exception:
        pass
    os._exit(0)


def _start_backend_tray():
    try:
        import pystray
        icon = pystray.Icon(
            'aria_backend',
            _make_tray_icon(),
            'Aria Backend — running',
            menu=pystray.Menu(
                pystray.MenuItem('Quit Backend', lambda: os._exit(0))
            ),
        )
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Real-time Aria + Ableton bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "preset",
        nargs="?",
        choices=list(PRESETS),
        default=None,
        metavar="PRESET",
        help="Launch preset: plugin | m4l | automatic | manual. "
             "Sets sensible defaults; individual flags still override.",
    )
    parser.add_argument(
        "--in",
        dest="in_port",
        default="ARIA_IN",
        help="Input MIDI port name (default: ARIA_IN)",
    )
    parser.add_argument(
        "--out",
        dest="out_port",
        default="ARIA_OUT",
        help="Output MIDI port name (default: ARIA_OUT)",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Path to Aria checkpoint (required unless using --feedback-only). "
             "Can be relative (e.g., '../models/model-gen.safetensors') or absolute.",
    )
    parser.add_argument(
        "--listen_seconds",
        type=float,
        default=4.0,
        help="Duration to listen for human input before generating (default: 4.0)",
    )
    parser.add_argument(
        "--gen_seconds",
        type=float,
        default=1.0,
        help="Duration of continuation to generate (default: 1.0)",
    )
    parser.add_argument(
        "--cooldown_seconds",
        type=float,
        default=0.2,
        help="Cooldown after generation before listening again (default: 0.2)",
    )
    parser.add_argument(
        "--clock_in",
        dest="clock_in",
        default="ARIA_CLOCK",
        help="MIDI clock input port name (default: ARIA_CLOCK)",
    )
    parser.add_argument(
        "--measures",
        type=int,
        default=2,
        help="Number of measures per human/model block (default: 2)",
    )
    parser.add_argument(
        "--beats_per_bar",
        type=int,
        default=4,
        help="Beats per bar (time signature numerator, default: 4)",
    )
    parser.add_argument(
        "--gen_measures",
        type=int,
        default=None,
        help="Measures to generate (default: same as --measures)",
    )
    parser.add_argument(
        "--human_measures",
        type=int,
        default=1,
        help="Number of human measures to collect before generating (default: 1)",
    )
    parser.add_argument(
        "--quantize",
        action="store_true",
        help="Quantize generated output to 1/16 note grid (default: off)",
    )
    parser.add_argument(
        "--ticks_per_beat",
        type=int,
        default=480,
        help="MIDI ticks per quarter note (default: 480)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.9,
        help="Sampling temperature (default: 0.9)",
    )
    parser.add_argument(
        "--top_p",
        type=float,
        default=0.95,
        help="Top-p sampling (default: 0.95)",
    )
    parser.add_argument(
        "--min_p",
        type=float,
        default=None,
        help="Min-p sampling threshold (default: None)",
    )
    parser.add_argument(
        "--device",
        default=None,
        choices=["cuda", "mlx", "cpu"],
        help="Device for model inference: cuda (NVIDIA), mlx (Apple Silicon), cpu. "
             "Auto-detected if omitted.",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available MIDI ports and exit",
    )
    parser.add_argument(
        "--mode",
        choices=["clock", "manual"],
        default="clock",
        help="Bridge mode: 'clock' uses Ableton MIDI clock (default), 'manual' uses keyboard start/stop",
    )
    parser.add_argument(
        "--manual-key",
        default="r",
        help="Keyboard key to start/stop recording in manual mode (default: r)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Manual mode: optional safety timeout to stop recording after N seconds",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Manual mode: optional safety timeout expressed in bars (requires tempo inference)",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=None,
        help="Override token budget for generation (otherwise derived from --gen_seconds; higher = longer/more detail)",
    )
    parser.add_argument(
        "--play-key",
        default=None,
        help="Manual mode: optional keyboard key to arm playback of generated MIDI (defaults to auto-play).",
    )
    parser.add_argument(
        "--m4l",
        action="store_true",
        help="Enable OSC control plane for Max for Live (optional, default off)",
    )
    parser.add_argument(
        "--osc-host",
        default="127.0.0.1",
        help="OSC host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--osc-in-port",
        type=int,
        default=9000,
        help="OSC UDP port to listen for incoming control (default: 9000)",
    )
    parser.add_argument(
        "--osc-out-port",
        type=int,
        default=9001,
        help="OSC UDP port to send status/logs (default: 9001)",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch optional Tkinter UI panel for live control/status",
    )
    parser.add_argument(
        "--feedback",
        action="store_true",
        help="Enable real-time feedback dataset capture mode",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Directory for storing feedback dataset (default: <base>/feedback/)",
    )

    # Apply preset defaults before full parse so explicit flags can still override.
    preset_name = next((a for a in sys.argv[1:] if a in PRESETS), None)
    if preset_name:
        parser.set_defaults(**PRESETS[preset_name])

    args = parser.parse_args()

    threading.Thread(target=_stdin_watchdog, daemon=True).start()
    _start_backend_tray()

    # Handle port listing early to avoid requiring checkpoint or other setup
    if args.list_ports:
        get_midi_ports()
        return 0

    if args.feedback:
        if args.data_dir is not None:
            data_dir = Path(args.data_dir)
        else:
            # Resolve base directory: exe folder when frozen, repo root when running as script
            if getattr(sys, "frozen", False):
                _base = Path(sys.executable).parent
            else:
                _base = Path(__file__).parent.parent
            data_dir = _base / "feedback"
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Feedback] Using dataset directory: {data_dir}")
        datastore = DataStore(data_dir)
        feedback_manager = FeedbackManager(datastore)
    else:
        feedback_manager = None

    # Import and start bridge
    try:
        # Handle both module and script execution (with new directory structure)
        try:
            from .core.midi_buffer import RollingMidiBuffer
            from .core.aria_engine import AriaEngine
            from .core.bridge_engine import AbletonBridge
            from .core.tempo_tracker import TempoTracker
            from .core.sampling_state import SamplingState, SessionState
            from .modes.manual_mode import ManualModeSession
            from .modes.sampling_hotkeys import start_sampling_hotkeys
            from .modes.osc_controller import OscController
            from .ui.ui_panel import run_ui
            import_mode = "package"
        except ImportError:
            from core.midi_buffer import RollingMidiBuffer
            from core.aria_engine import AriaEngine
            from core.bridge_engine import AbletonBridge
            from core.tempo_tracker import TempoTracker
            from core.sampling_state import SamplingState, SessionState
            from modes.manual_mode import ManualModeSession
            from modes.sampling_hotkeys import start_sampling_hotkeys
            from modes.osc_controller import OscController
            from ui.ui_panel import run_ui
            import_mode = "script"

        logger.debug(f"Import mode: {import_mode}")

        # Shared state + queues (init before heavy model load so OSC can sync immediately)
        sampling_state = SamplingState(
            temperature=args.temperature,
            top_p=args.top_p,
            min_p=args.min_p if args.min_p is not None else 0.0,
        )
        session_state = SessionState(mode=args.mode)
        cmd_queue = queue.Queue()
        log_queue = queue.Queue()
        hotkey_stop = threading.Event()

        osc = None
        startup_state = None
        if args.m4l:
            osc = OscController(
                host=args.osc_host,
                in_port=args.osc_in_port,
                out_port=args.osc_out_port,
                sampling_state=sampling_state,
                session_state=session_state,
                command_queue=cmd_queue,
                commit_cb=(feedback_manager.commit if feedback_manager else None),
                grade_cb=(feedback_manager.set_grade if feedback_manager else None),
                feedback_param_cb=(feedback_manager.set_feedback_param if feedback_manager else None),
            )
            # Start OSC server first, then pull current dial state from Max
            osc.start()
            startup_state = sync_state_on_startup(osc, timeout=2.0)
            if startup_state:
                logger.info(f"OSC params after startup sync: {startup_state}")
                t = startup_state.get('temp', 0)
                tp = startup_state.get('top_p', 0)
                tok = startup_state.get('tokens', 0)
                print(f"STATUS:synced:temp={t:.2f} top_p={tp:.2f} tokens={tok}", flush=True)

        # Resolve device (auto-detect if not specified)
        if args.device is None:
            args.device = _auto_detect_device()
            logger.info(f"Auto-detected device: {args.device}")

        if args.device == "cuda":
            import torch
            if not torch.cuda.is_available():
                logger.error("CUDA requested but not available. Use --device mlx (Apple Silicon) or --device cpu")
                return 1
            logger.info(f"CUDA device: {torch.cuda.get_device_name(0)}")
        elif args.device == "mlx":
            try:
                import mlx.core  # noqa: F401
            except ImportError:
                logger.error("MLX requested but not installed. Run: pip install mlx")
                return 1
            logger.info("MLX backend (Apple Silicon)")
        else:
            logger.info("CPU device (inference will be slow)")

        checkpoint_path = find_checkpoint(args.checkpoint)

        # Keyboard hotkeys (after OSC sync so defaults reflect Max state)
        start_sampling_hotkeys(sampling_state, hotkey_stop)

        logger.info(f"Connecting to ports: IN={args.in_port}, OUT={args.out_port}")
        logger.info(f"Checkpoint: {checkpoint_path}")
        logger.info(
            f"Listen {args.listen_seconds}s -> Generate {args.gen_seconds}s -> "
            f"Cooldown {args.cooldown_seconds}s"
        )
        if args.mode == "manual":
            logger.info("Manual mode selected: keyboard-driven recording without MIDI clock.")
        else:
            if args.clock_in:
                logger.info(f"MIDI Clock input: {args.clock_in}")

        # Create shared engine
        engine = AriaEngine(
            checkpoint_path=checkpoint_path,
            device=args.device,
            config_name="medium",
        )
        print("STATUS:ready", flush=True)

        if osc:
            osc.send_status(session_state.status if hasattr(session_state, "status") else "IDLE")
            osc.send_params()

        if args.mode == "manual":
            session = ManualModeSession(
                in_port_name=args.in_port,
                out_port_name=args.out_port,
                aria_engine=engine,
                manual_key=args.manual_key,
                ticks_per_beat=args.ticks_per_beat,
                gen_seconds=args.gen_seconds,
                max_seconds=args.max_seconds,
                max_bars=args.max_bars,
                beats_per_bar=args.beats_per_bar,
                max_new_tokens=args.max_new_tokens,
                play_key=args.play_key,
                sampling_state=sampling_state,
                command_queue=cmd_queue if (args.ui or args.m4l) else None,
                log_queue=log_queue if (args.ui or args.m4l) else None,
                session_state=session_state if (args.ui or args.m4l) else None,
                osc_status_cb=osc.send_status if osc else None,
                osc_log_cb=osc.send_log if osc else None,
                osc_params_cb=osc.send_params if osc else None,
                osc_generation_start_cb=osc.send_generation_start if osc else None,
                osc_generation_done_cb=osc.send_generation_done if osc else None,
                osc_playback_progress_cb=osc.send_playback_progress if osc else None,
                osc_playback_stopped_cb=osc.send_playback_stopped if osc else None,
                osc_playback_duration_cb=osc.send_playback_duration if osc else None,
                play_gate=bool(args.m4l),
                feedback_manager=feedback_manager,
            )
            if osc:
                osc.cancel_playback_cb = session.playback_cancel_event.set
                osc.generation_cancel_cb = session.generation_cancel_event.set
            if args.ui:
                session_thread = threading.Thread(target=session.run, daemon=True)
                session_thread.start()
                try:
                    run_ui(sampling_state, session_state, cmd_queue, log_queue, stop_event=hotkey_stop)
                finally:
                    hotkey_stop.set()
                    session.cancel_event.set()
                    session_thread.join(timeout=2)
                return 0
            else:
                rc = session.run()
                hotkey_stop.set()
                return rc

        # CLOCK MODE (existing behavior)
        buffer = RollingMidiBuffer(window_seconds=args.listen_seconds)

        # TempoTracker conflicts with ClockGrid on the same MIDI port; skip when clock_in is set.
        tempo_tracker = None
        if args.clock_in:
            logger.info(f"Using ClockGrid on '{args.clock_in}'; disabling TempoTracker (port conflict)")
        
        bridge = AbletonBridge(
            in_port_name=args.in_port,
            out_port_name=args.out_port,
            midi_buffer=buffer,
            aria_engine=engine,
            tempo_tracker=tempo_tracker,
            sampling_state=sampling_state,
            clock_in=args.clock_in,
            measures=args.measures,
            beats_per_bar=args.beats_per_bar,
            gen_measures=args.gen_measures,
            human_measures=args.human_measures,
            cooldown_seconds=args.cooldown_seconds,
            quantize=args.quantize,
            ticks_per_beat=args.ticks_per_beat,
            feedback_manager=feedback_manager,
        )

        if args.ui:
            bridge_thread = threading.Thread(target=bridge.run, daemon=True)
            bridge_thread.start()
            try:
                run_ui(sampling_state, session_state, cmd_queue, log_queue, stop_event=hotkey_stop)
            finally:
                hotkey_stop.set()
            return 0
        else:
            bridge.run()
            hotkey_stop.set()
            return 0

    except FileNotFoundError as e:
        logger.error(str(e))
        print(f"STATUS:error:{e}", flush=True)
        return 1
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        print(f"STATUS:error:{e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
