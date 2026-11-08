"""Stream mode: Aria plays continuously and listens while it plays.

Works with the existing M4L device / plugins, no new controls needed:
  Record on/off   capture a seed phrase, then streaming starts from it
  (while streaming) anything you play on ARIA_IN is merged into Aria's context at its
                  real time, so the next phrases answer you (overdub / duet)
  Temp, top p, min p   applied live, per token
  Cancel          stop the stream (history is kept)
  Play            resume the stream (or start one from nothing)

Aria generates `lookahead` seconds ahead of the playhead. Live notes are merged every
`merge_ms`: Aria notes scheduled within `commit_ms` keep playing, later unheard ones are
regenerated with your notes in context. See core/stream_engine.py.
"""

from __future__ import annotations

import bisect
import heapq
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from core.stream_engine import StreamEngine, Note, SEG_MS
except ImportError:
    from ..core.stream_engine import StreamEngine, Note, SEG_MS

logger = logging.getLogger(__name__)


def _resolve_port(name: str, kind: str) -> str:
    import mido
    available = mido.get_input_names() if kind == "input" else mido.get_output_names()
    n = name.lower()
    matched = [p for p in available if p.lower().startswith(n)] or [p for p in available if n in p.lower()]
    if not matched:
        raise RuntimeError(f"No MIDI {kind} port matching '{name}'. Available: {available}")
    return matched[0]


class StreamSession:
    def __init__(self, in_port_name, out_port_name, aria_engine, sampling_state,
                 command_queue: Optional[queue.Queue] = None, session_state=None,
                 osc_status_cb=None, osc_log_cb=None,
                 window: int = 2048, lookahead_s: float = 3.0, commit_ms: int = 600,
                 merge_ms: int = 500, hold_merge_ms: int = 600, entropy_floor: float = 0.8,
                 listen_lookahead_s: float = 1.0, listen_hold_s: float = 1.5,
                 startup_ms: int = 800, max_late_ms: int = 150, stream_dir: Optional[str] = None,
                 rng_seed: Optional[int] = None):
        if getattr(aria_engine, "device", None) != "mlx":
            raise RuntimeError("Stream mode currently needs the MLX backend (Apple Silicon).")
        self.in_port_name, self.out_port_name = in_port_name, out_port_name
        self.engine = StreamEngine(aria_engine.model, aria_engine.tokenizer, window=window,
                                   entropy_floor=entropy_floor, rng_seed=rng_seed)
        self.sampling_state = sampling_state
        self.command_queue = command_queue
        self.session_state = session_state
        self.osc_status_cb, self.osc_log_cb = osc_status_cb, osc_log_cb
        self.lookahead_ms = int(lookahead_s * 1000)
        # while you are playing, plan only a little ahead: less gets thrown away at each
        # merge, and Aria answers sooner
        self.listen_lookahead_ms = int(listen_lookahead_s * 1000)
        self.listen_hold_s = listen_hold_s
        self.last_input_wall = 0.0
        self.commit_ms, self.merge_ms, self.hold_merge_ms = commit_ms, merge_ms, hold_merge_ms
        self.startup_ms, self.max_late_ms = startup_ms, max_late_ms
        self.stream_dir = Path(stream_dir) if stream_dir else None

        self.cancel_event = threading.Event()   # whole session (Ctrl-C)
        self.stream_stop = threading.Event()    # current stream
        self.lock = threading.Lock()
        self.in_q: "queue.Queue[tuple]" = queue.Queue()
        self.state = "IDLE"
        self.inport = self.outport = None
        self.wall0 = None
        self.sched: List[Note] = []    # Aria notes not yet started, by onset
        self.offs: list = []           # (off_ms, seq, pitch)
        self.active = {}               # pitch -> seq of the note holding it
        self._seq = 0
        self.threads: list = []
        self.in_notes = 0        # note-ons seen on the input port, ever
        self.in_pedal = 0
        self.in_last = None
        self.seed_base = 0       # in_notes when the seed recording started
        self._reset_input()
        self.played_aria: List[Note] = []
        self.human_log: List[Note] = []
        self.stats = dict(late_played=0, late_skipped=0, merges=0, dropped=0, stalls=0, stall_ms=0)

    # ---------- small helpers ----------
    def _log(self, msg: str):
        logger.info(f"[stream] {msg}")
        if self.osc_log_cb:
            try:
                self.osc_log_cb(msg)
            except Exception:
                pass

    def _status(self, s: str, line: str):
        self.state = s
        print(f"STATUS:{line}", flush=True)
        if self.session_state:
            try:
                self.session_state.set_status("PLAYING" if s == "STREAMING" else s)
            except Exception:
                pass
        if self.osc_status_cb:
            self.osc_status_cb("PLAYING" if s == "STREAMING" else s)

    def now_ms(self) -> int:
        return int((time.monotonic() - self.wall0) * 1000)

    def _reset_input(self):
        self.held = {}       # pitch -> [t_on, vel]
        self.pedal = False
        self.sustained = {}  # pitch -> [t_on, vel, t_release]
        self.ready: List[tuple] = []  # (t_on, pitch, vel, t_off)
        self.merged_held = set()

    # ---------- MIDI in ----------
    def _on_midi(self, msg):
        if msg.type in ("note_on", "note_off", "control_change"):
            self.in_q.put((time.monotonic(), msg))
            if msg.type == "note_on" and msg.velocity > 0:
                self.in_notes += 1
                self.in_last = msg.note
            elif msg.type == "control_change" and msg.control == 64:
                self.in_pedal += 1

    def _telemetry(self):
        """Report what is reaching the session, for the log viewer (STATUS:signal lines)."""
        prev = None
        last_save = time.monotonic()
        while not self.cancel_event.is_set():
            if self.state == "STREAMING" and time.monotonic() - last_save >= 30:
                last_save = time.monotonic()
                try:
                    self._save(quiet=True)  # a killed process must not lose the take
                except Exception as e:
                    logger.warning(f"[stream] Autosave failed: {e}")
            seed = self.in_notes - self.seed_base if self.state == "RECORDING" else 0
            cur = (self.state, self.in_notes, self.in_pedal, len(self.played_aria), seed)
            if cur != prev:
                print(f"STATUS:signal:state={self.state} in={self.in_notes} pedal={self.in_pedal} "
                      f"last={self.in_last} out={len(self.played_aria)} seed={seed}", flush=True)
                prev = cur
            time.sleep(0.25)

    def _check_live(self):
        """One read of the Live set: is any track listening to the port Aria plays on?"""
        try:
            try:
                from core.clip_output import AbletonOSCClient
            except ImportError:
                from ..core.clip_output import AbletonOSCClient
            osc = AbletonOSCClient()
        except Exception as e:
            logger.warning(f"[stream] Could not reach AbletonOSC: {e}")
            return
        try:
            names = osc.query("/live/song/get/track_names", [], timeout=1.0)
            if not names:
                logger.warning("[stream] AbletonOSC did not answer. Is Live open with AbletonOSC enabled?")
                return
            want = self.out_port_name.lower()
            hearing = []
            for i, name in enumerate(names):
                r = osc.query("/live/track/get/input_routing_type", [i], timeout=0.5)
                if r and len(r) > 1 and want in str(r[1]).lower():
                    arm = osc.query("/live/track/get/arm", [i], timeout=0.5)
                    mon = osc.query("/live/track/get/current_monitoring_state", [i], timeout=0.5)
                    armed = bool(arm and len(arm) > 1 and arm[1])
                    state = mon[1] if mon and len(mon) > 1 else None
                    if state == 0 or (state == 1 and armed):
                        hearing.append(name)
            if hearing:
                logger.info(f"[stream] Ableton: '{hearing[0]}' hears {self.out_port_name}")
            else:
                logger.warning(f"[stream] Ableton: no track is hearing {self.out_port_name}, so Aria will be silent. "
                               f"Set a track's MIDI From to {self.out_port_name} and Monitor to In.")
        finally:
            osc.close()

    def _drain_input(self):
        """Turn raw MIDI into finished notes (pedal-aware); held notes merge after hold_merge_ms."""
        while True:
            try:
                t, m = self.in_q.get_nowait()
            except queue.Empty:
                break
            if m.type in ("note_on", "note_off"):
                self.last_input_wall = t
            if m.type == "note_on" and m.velocity > 0:
                if m.note in self.sustained:
                    s = self.sustained.pop(m.note)
                    self.ready.append((s[0], m.note, s[1], t))
                self.held[m.note] = [t, m.velocity]
                self.merged_held.discard(m.note)
            elif m.type in ("note_off", "note_on"):
                h = self.held.pop(m.note, None)
                if h is None:
                    continue
                if m.note in self.merged_held:
                    self.merged_held.discard(m.note)
                    continue
                if self.pedal:
                    self.sustained[m.note] = [h[0], h[1], t]
                else:
                    self.ready.append((h[0], m.note, h[1], t))
            elif m.type == "control_change" and m.control == 64:
                down = m.value >= 64
                if self.pedal and not down:
                    for p, s in self.sustained.items():
                        self.ready.append((s[0], p, s[1], t))
                    self.sustained.clear()
                self.pedal = down

    def _take_ready(self, now_wall: float, provisional: bool) -> List[tuple]:
        out, self.ready = self.ready, []
        if provisional:  # long-held / sustained notes join with their length so far
            for p, h in list(self.held.items()):
                if p not in self.merged_held and (now_wall - h[0]) * 1000 >= self.hold_merge_ms:
                    out.append((h[0], p, h[1], now_wall))
                    self.merged_held.add(p)
            for p, s in list(self.sustained.items()):
                if (now_wall - s[0]) * 1000 >= self.hold_merge_ms:
                    out.append((s[0], p, s[1], now_wall))
                    del self.sustained[p]
        return out

    # ---------- MIDI out ----------
    def _player(self):
        import mido
        while not self.stream_stop.is_set():
            now = self.now_ms()
            with self.lock:
                while self.sched and self.sched[0].on <= now:
                    n = self.sched.pop(0)
                    late = now - n.on
                    n.played = True
                    if late > self.max_late_ms:
                        self.stats["late_skipped"] += 1
                        continue
                    if late > 30:
                        self.stats["late_played"] += 1
                    if n.pitch in self.active:
                        self.outport.send(mido.Message("note_off", note=n.pitch, velocity=0))
                    self._seq += 1
                    self.active[n.pitch] = self._seq
                    self.outport.send(mido.Message("note_on", note=n.pitch, velocity=max(1, min(127, n.vel))))
                    heapq.heappush(self.offs, (time.monotonic() + max(30, n.dur) / 1000, self._seq, n.pitch))
                    self.played_aria.append(n)
                while self.offs and self.offs[0][0] <= time.monotonic():
                    _, seq, p = heapq.heappop(self.offs)
                    if self.active.get(p) == seq:
                        del self.active[p]
                        self.outport.send(mido.Message("note_off", note=p, velocity=0))
            time.sleep(0.002)

    def _all_notes_off(self):
        import mido
        with self.lock:
            for p in list(self.active):
                self.outport.send(mido.Message("note_off", note=p, velocity=0))
            self.active.clear()
            self.offs.clear()
            self.sched.clear()

    # ---------- generation ----------
    def _generator(self):
        eng = self.engine
        last_merge = 0.0
        last_stats = time.monotonic()
        tok_count = 0
        while not self.stream_stop.is_set():
            self._drain_input()
            now_wall = time.monotonic()
            if (now_wall - last_merge) * 1000 >= self.merge_ms:
                batch = self._take_ready(now_wall, provisional=True)
                if batch:
                    notes = [Note(on=int((a - self.wall0) * 1000), pitch=p, vel=v,
                                  dur=max(30, int((b - a) * 1000)), src="human") for a, p, v, b in batch]
                    cut = self.now_ms() + self.commit_ms
                    t0 = time.monotonic()
                    with self.lock:
                        dropped = eng.insert(notes, cut)
                        if dropped:
                            gone = set(map(id, dropped))
                            self.sched = [n for n in self.sched if id(n) not in gone]
                    self.human_log.extend(notes)
                    self.stats["merges"] += 1
                    self.stats["dropped"] += len(dropped)
                    logger.info(f"[stream] merged {len(notes)} live notes, regenerating {len(dropped)} "
                                f"unheard ({(time.monotonic() - t0) * 1000:.0f} ms)")
                last_merge = now_wall
            now = self.now_ms()
            cursor = (eng.w_seg0 + eng.seg) * SEG_MS
            if self.sched:
                cursor = max(cursor, self.sched[-1].on)
            listening = (now_wall - self.last_input_wall) < self.listen_hold_s
            target = self.listen_lookahead_ms if listening else self.lookahead_ms
            if cursor - now < target:
                temp, top_p, min_p = self.sampling_state.get_values()
                n = eng.step(temp, top_p, min_p or 0.0)  # ~15 ms; keep the player unblocked
                if n is not None:
                    late = self.now_ms() - n.on
                    if late > self.max_late_ms // 2:
                        # Elastic clock: if generation falls behind, hold the playhead for
                        # Aria (a short stall) rather than skipping notes it can't catch up on.
                        shift = late + 80
                        self.wall0 += shift / 1000
                        self.stats["stalls"] += 1
                        self.stats["stall_ms"] += shift
                    with self.lock:
                        bisect.insort(self.sched, n, key=lambda x: x.on)
                tok_count += 1
            else:
                time.sleep(0.01)
            if now_wall - last_stats >= 5:
                st = eng.stats()
                ahead = ((self.sched[-1].on - now) / 1000) if self.sched else 0.0
                ent = f"{st['entropy']:.2f}" if st["entropy"] is not None else "-"
                logger.info(f"[stream] t={now / 1000:.1f}s ahead={ahead:.1f}s entropy={ent} "
                            f"temp+{st['temp_boost']:.2f} ctx={st['ctx']} tok/s={tok_count / (now_wall - last_stats):.0f} "
                            f"played={len(self.played_aria)} late={self.stats['late_played']}/{self.stats['late_skipped']} "
                            f"merges={self.stats['merges']} stalls={self.stats['stalls']}/{self.stats['stall_ms']}ms "
                            f"rebuilds={st['rebuilds']} mem={st['mem_gb']}GB{' listening' if listening else ''}")
                tok_count, last_stats = 0, now_wall

    # ---------- stream lifecycle ----------
    def _start_stream(self, seed: List[Note], seed_end_ms: int):
        self.stream_stop.clear()
        t0 = time.monotonic()
        with self.lock:
            self.engine.reset(seed)
        self._log(f"Streaming from a {seed_end_ms / 1000:.1f} s seed ({len(seed)} notes); "
                  f"prefill {1000 * (time.monotonic() - t0):.0f} ms")
        self.wall0 = time.monotonic() - (seed_end_ms - self.startup_ms) / 1000
        self.played_aria = []
        self.stream_path = (self.stream_dir / f"stream_{datetime.now():%Y%m%d_%H%M%S}.mid") if self.stream_dir else None
        self.human_log = [n for n in seed if n.src in ("seed", "human")]
        self.stats = dict(late_played=0, late_skipped=0, merges=0, dropped=0, stalls=0, stall_ms=0)
        while not self.in_q.empty():  # drop anything played while idle
            self.in_q.get_nowait()
        self._reset_input()
        self.threads = [threading.Thread(target=self._generator, daemon=True),
                        threading.Thread(target=self._player, daemon=True)]
        for th in self.threads:
            th.start()
        self._status("STREAMING", "streaming")

    def _resume(self):
        ev = self.engine.events
        end = (ev[-1].off if ev else 0) + 500
        self._start_stream(list(ev), end)

    def stop_stream(self):
        if self.state != "STREAMING":
            return
        self.stream_stop.set()
        for th in self.threads:
            th.join(timeout=2)
        self._all_notes_off()
        self._save()
        self._status("IDLE", "stopped")
        self._log(f"Stream stopped: {len(self.played_aria)} Aria notes played, "
                  f"{self.stats['merges']} merges of your playing, "
                  f"{self.stats['late_played']} late, {self.stats['late_skipped']} skipped, "
                  f"{self.stats['stalls']} stalls ({self.stats['stall_ms'] / 1000:.1f} s)")

    def _save(self, quiet: bool = False):
        if not self.stream_dir or not self.played_aria:
            return
        import mido
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        path = self.stream_path
        mf = mido.MidiFile(ticks_per_beat=480)  # 120 bpm: 1 ms = 0.96 ticks
        for name, notes in (("you", list(self.human_log)), ("aria", list(self.played_aria))):
            tr = mido.MidiTrack()
            tr.append(mido.MetaMessage("track_name", name=name))
            evs = []
            for n in notes:
                evs.append((n.on, 1, mido.Message("note_on", note=n.pitch, velocity=max(1, min(127, n.vel)))))
                evs.append((n.off, 0, mido.Message("note_off", note=n.pitch, velocity=0)))
            evs.sort(key=lambda e: (e[0], e[1]))
            last = 0
            for t, _, m in evs:
                tr.append(m.copy(time=max(0, int(round((t - last) * 0.96)))))
                last = t
            mf.tracks.append(tr)
        tmp = path.with_suffix(".tmp")
        mf.save(tmp)
        tmp.replace(path)
        if not quiet:
            self._log(f"Saved {path.name}")

    # ---------- main loop ----------
    def run(self) -> int:
        import mido
        self.inport = mido.open_input(_resolve_port(self.in_port_name, "input"), callback=self._on_midi)
        self.outport = mido.open_output(_resolve_port(self.out_port_name, "output"))
        logger.info(f"Stream mode ports opened: IN={self.inport.name}, OUT={self.outport.name}")
        print("STATUS:ports_ready", flush=True)
        self._check_live()
        threading.Thread(target=self._telemetry, daemon=True).start()
        self._log("Stream mode ready. Record a seed phrase, or press Play to start from nothing.")
        rec_start = None
        try:
            while not self.cancel_event.is_set():
                try:
                    cmd, payload = self.command_queue.get(timeout=0.1) if self.command_queue else (None, None)
                except queue.Empty:
                    continue
                logger.info(f"[stream] Command {cmd} while {self.state}")
                if cmd in ("record_start", "record_stop", "record", "toggle_record"):
                    starting = (cmd == "record_start" or (cmd == "record" and payload)
                                or (cmd == "toggle_record" and self.state != "RECORDING"))
                    if starting and self.state == "IDLE":
                        while not self.in_q.empty():
                            self.in_q.get_nowait()
                        self._reset_input()
                        rec_start = time.monotonic()
                        self.seed_base = self.in_notes
                        self._status("RECORDING", "recording")
                        self._log("Recording a seed. Press record again to start streaming.")
                    elif starting and self.state == "STREAMING":
                        self._log("Already streaming: your playing is merged as you play.")
                    elif not starting and self.state == "RECORDING":
                        stop = time.monotonic()
                        self._drain_input()
                        raw = self.ready + [(h[0], p, h[1], stop) for p, h in self.held.items()] \
                            + [(s[0], p, s[1], stop) for p, s in self.sustained.items()]
                        if not raw:
                            self._status("IDLE", "stopped")
                            self._log("No notes reached ARIA_IN, so there is no seed. Press Play to start from nothing.")
                            continue
                        first = min(r[0] for r in raw)
                        seed = [Note(on=int((a - first) * 1000), pitch=p, vel=v,
                                     dur=max(30, int((b - a) * 1000)), src="seed") for a, p, v, b in raw]
                        self._start_stream(seed, int((stop - first) * 1000))
                elif cmd == "play":
                    if self.state == "IDLE":
                        if self.engine.events:
                            self._resume()
                        else:
                            self._start_stream([], self.startup_ms)
                elif cmd in ("cancel", "cancel_playback"):
                    if self.state == "STREAMING":
                        self.stop_stream()
                    elif self.state == "RECORDING":
                        self._status("IDLE", "stopped")
                        self._log("Seed recording canceled.")
                if self.command_queue:
                    self.command_queue.task_done()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_stream()
            self.cancel_event.set()
            try:
                self.outport.close()
            except Exception:
                pass
            # Closing an rtmidi input with a live callback can hang on macOS, here and at
            # interpreter shutdown. The take is saved by now, so leave directly.
            logger.info("[stream] Backend exiting")
            logging.shutdown()
            sys.stdout.flush()
            os._exit(0)
