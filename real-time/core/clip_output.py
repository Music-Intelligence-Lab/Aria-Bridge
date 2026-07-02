"""Send a generated .mid into an Ableton clip via AbletonOSC (opt-in output path).

This is an alternative to streaming notes out ARIA_OUT: instead, the notes are
written into an Ableton clip so Live's own engine plays them — sample-accurate,
downbeat-locked (with Live launch quantization), and re-triggerable.

Requires AbletonOSC running as a Control Surface (Input/Output = None). Notes are
placed in beats (tempo-independent); the clip plays at the Live Set tempo.
"""

import logging
import math

logger = logging.getLogger(__name__)

# AbletonOSC defaults: receives on 11000, replies on 11001 (localhost).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11000
RECV_PORT = 11001
NOTES_PER_MSG = 40  # keep each /live/clip/add/notes packet under the UDP limit


def find_first_empty_slot(host, port, track, start_slot, recv_port=RECV_PORT,
                          max_scan=128, timeout=0.4):
    """Ask AbletonOSC for the first empty clip slot at/below ``start_slot``.

    Returns that slot index, or ``start_slot`` if it can't be determined (e.g.
    AbletonOSC not responding). Used to drop a new clip into the slot *under* an
    existing one instead of overwriting it.
    """
    try:
        from pythonosc import udp_client, dispatcher, osc_server
        import threading
        import time
    except ImportError:
        return start_slot

    results = {}
    lock = threading.Lock()

    def _on_has_clip(addr, *a):
        # AbletonOSC replies: track, slot, has_clip
        if len(a) >= 3:
            with lock:
                results[(int(a[0]), int(a[1]))] = bool(a[2])

    disp = dispatcher.Dispatcher()
    disp.map("/live/clip_slot/has_clip", _on_has_clip)
    try:
        server = osc_server.ThreadingOSCUDPServer((host, recv_port), disp)
    except Exception as e:
        logger.warning(f"clip_output: cannot open reply listener on {recv_port}: {e}")
        return start_slot
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = udp_client.SimpleUDPClient(host, port)

    found = start_slot
    try:
        for slot in range(start_slot, start_slot + max_scan):
            with lock:
                results.pop((track, slot), None)
            client.send_message("/live/clip_slot/has_clip", [track, slot])
            deadline = time.time() + timeout
            val = None
            while time.time() < deadline:
                with lock:
                    if (track, slot) in results:
                        val = results[(track, slot)]
                        break
                time.sleep(0.01)
            if val is None:          # no reply -> can't tell; fall back
                found = start_slot
                break
            if val is False:         # empty slot found
                found = slot
                break
        else:
            found = start_slot       # all scanned slots full
    finally:
        server.shutdown()
    return found


def query_tempo(host=DEFAULT_HOST, port=DEFAULT_PORT, recv_port=RECV_PORT, timeout=0.5):
    """One-shot query of Ableton's Set tempo (BPM) via AbletonOSC. Returns float or None.

    Used to place a recorded performance on Live's actual beat grid (the tempo you
    recorded to) instead of an inferred BPM.
    """
    try:
        from pythonosc import udp_client, dispatcher, osc_server
        import threading
        import time
    except ImportError:
        return None

    result = {}

    def _on(addr, *a):
        if a:
            result["bpm"] = a[-1]

    disp = dispatcher.Dispatcher()
    disp.map("/live/song/get/tempo", _on)
    try:
        server = osc_server.ThreadingOSCUDPServer((host, recv_port), disp)
    except Exception as e:
        logger.warning(f"query_tempo: cannot open reply listener on {recv_port}: {e}")
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        udp_client.SimpleUDPClient(host, port).send_message("/live/song/get/tempo", [])
        deadline = time.time() + timeout
        while time.time() < deadline and "bpm" not in result:
            time.sleep(0.01)
    finally:
        server.shutdown()
    try:
        return float(result["bpm"])
    except (KeyError, TypeError, ValueError):
        return None


def start_clip_record(host=DEFAULT_HOST, port=DEFAULT_PORT, track=0, slot=0,
                      recv_port=RECV_PORT, auto_advance=True):
    """Arm ``track`` and fire clip slot ``slot`` to begin native recording in Live.

    This is how Aria's Record button drives Ableton to record your live playing into
    a clip (the bridge still buffers the same MIDI over ARIA_IN for the prompt). If
    ``auto_advance`` and the target slot already holds a clip, the first empty slot
    at/below it is used instead — so an existing take is never overwritten.

    Requires the input track's MIDI-From to be set to the same controller as ARIA_IN
    (arming only enables record; it doesn't route input). Returns the slot actually
    used, or -1 if python-osc is unavailable.
    """
    try:
        from pythonosc import udp_client
    except ImportError:
        logger.error("python-osc not available; cannot start clip record.")
        return -1
    used_slot = slot
    if auto_advance:
        used_slot = find_first_empty_slot(host, port, track, slot)
    client = udp_client.SimpleUDPClient(host, port)
    client.send_message("/live/track/set/arm", [int(track), 1])
    client.send_message("/live/clip_slot/fire", [int(track), int(used_slot)])
    logger.info(
        f"clip_output: armed track {track}, firing slot {used_slot} to record "
        f"via AbletonOSC {host}:{port}"
    )
    return used_slot


def stop_clip_record(host=DEFAULT_HOST, port=DEFAULT_PORT, track=0, disarm=True):
    """Stop the recording clip on ``track`` (finalises the take) and optionally disarm.

    Uses ``/live/track/stop_all_clips`` so the recording clip stops and becomes a
    normal clip without launching into playback.
    """
    try:
        from pythonosc import udp_client
    except ImportError:
        return
    client = udp_client.SimpleUDPClient(host, port)
    client.send_message("/live/track/stop_all_clips", [int(track)])
    if disarm:
        client.send_message("/live/track/set/arm", [int(track), 0])
    logger.info(
        f"clip_output: stopped clips + {'disarmed' if disarm else 'kept armed'} "
        f"track {track} via AbletonOSC {host}:{port}"
    )


def _clip_lengths(end_beat, bpb, measures_out):
    """Return (clip_len, loop_len, play_len) in beats.

    - clip_len: full clip — holds ALL notes, ending on the downbeat after the last note.
    - loop_len: truncated loop length (``measures_out`` bars), or None if not truncating.
    - play_len: what plays each pass (loop_len when truncating, else clip_len).

    Truncation only kicks in when ``measures_out`` is SHORTER than the generated content —
    it just shortens the loop region, keeping every note. If the knob is >= the generated
    length (or 0), it's ignored and the clip is the full length rounded up to the last
    note's bar (never padded with silence).
    """
    full_len = max(bpb, math.ceil(round(end_beat, 4) / bpb) * bpb)
    if measures_out and measures_out > 0:
        loop_len = measures_out * bpb
        if loop_len < full_len:
            return full_len, loop_len, loop_len
    return full_len, None, full_len


def extract_notes(midi_path):
    """Parse a .mid into (notes, end_beat, bpm).

    notes = [(pitch, start_beat, duration_beat, velocity, mute)], times in beats
    (tick / ticks_per_beat). bpm = the file's first tempo (default 120) — the tempo
    the content was authored at, used if we want the clip to play as generated.
    """
    import mido

    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat or 480
    merged = mido.merge_tracks(mid.tracks)

    abs_tick = 0
    active = {}  # pitch -> list of (start_tick, velocity), a stack for overlaps
    notes = []
    tempo_us = None
    for msg in merged:
        abs_tick += msg.time
        if msg.type == "set_tempo" and tempo_us is None:
            tempo_us = msg.tempo
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault(msg.note, []).append((abs_tick, msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            stack = active.get(msg.note)
            if stack:
                start_tick, vel = stack.pop(0)
                start_beat = start_tick / tpb
                dur_beat = max((abs_tick - start_tick) / tpb, 1.0 / tpb)
                notes.append((msg.note, round(start_beat, 6), round(dur_beat, 6), vel, 0))

    notes.sort(key=lambda n: n[1])
    end_beat = max((n[1] + n[2] for n in notes), default=0.0)
    bpm = round(mido.tempo2bpm(tempo_us), 3) if tempo_us else 120.0
    return notes, end_beat, bpm


class AbletonOSCClient:
    """Persistent AbletonOSC client with a reply listener — for the loop driver,
    which needs to both send (write clips) and read state (which clip is playing).

    Owns the reply port (11001) for its lifetime, so don't run the function-based
    helpers (which also bind it) while an instance is alive. Call close() when done.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, recv_port=RECV_PORT):
        from pythonosc import udp_client, dispatcher, osc_server
        import threading

        self._replies = {}
        self._lock = threading.Lock()
        self.client = udp_client.SimpleUDPClient(host, port)
        disp = dispatcher.Dispatcher()
        disp.set_default_handler(self._on_reply)
        self.server = None
        try:
            self.server = osc_server.ThreadingOSCUDPServer((host, recv_port), disp)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        except Exception as e:
            logger.warning(f"AbletonOSCClient: no reply listener on {recv_port}: {e}")

    def _on_reply(self, addr, *args):
        with self._lock:
            self._replies[addr] = args

    def send(self, addr, args):
        self.client.send_message(addr, args)

    def query(self, addr, args, timeout=0.4):
        import time
        with self._lock:
            self._replies.pop(addr, None)
        self.client.send_message(addr, args)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if addr in self._replies:
                    return self._replies[addr]
            time.sleep(0.01)
        return None

    def get_playing_slot_index(self, track):
        """Currently playing clip slot on the track, or -1 if none."""
        r = self.query("/live/track/get/playing_slot_index", [int(track)])
        if r and len(r) >= 2:
            try:
                return int(r[-1])
            except (ValueError, TypeError):
                return -1
        return -1

    def get_num_scenes(self):
        r = self.query("/live/song/get/num_scenes", [])
        if r:
            try:
                return int(r[-1])
            except (ValueError, TypeError):
                return None
        return None

    def get_tempo(self):
        r = self.query("/live/song/get/tempo", [])
        if r:
            try:
                return float(r[-1])
            except (ValueError, TypeError):
                return None
        return None

    def get_clip_is_playing(self, track, slot):
        """True if the clip at (track, slot) is currently playing (not just triggered)."""
        r = self.query("/live/clip/get/is_playing", [int(track), int(slot)])
        if r:
            try:
                return bool(int(r[-1]))
            except (ValueError, TypeError):
                return False
        return False

    def get_clip_playing_position(self, track, slot):
        """Current playback position of the clip at (track, slot), in beats. None if unknown."""
        r = self.query("/live/clip/get/playing_position", [int(track), int(slot)])
        if r:
            try:
                return float(r[-1])
            except (ValueError, TypeError):
                return None
        return None

    def write_clip(self, midi_path, track, slot, name=None, beats_per_bar=4,
                   set_tempo=False, replace=True, fire=False, measures_out=0):
        """Write a .mid into (track, slot). Send-only (uses this client's socket).

        If ``measures_out`` > 0, the loop is truncated to that many bars via loop_end
        — all notes are kept, only the loop region is shortened. Returns
        (n_notes, play_len_beats).
        """
        notes, end_beat, bpm = extract_notes(midi_path)
        bpb = beats_per_bar if beats_per_bar and beats_per_bar > 0 else 4
        clip_len, loop_len, play_len = _clip_lengths(end_beat, bpb, measures_out)
        if set_tempo:
            self.client.send_message("/live/song/set/tempo", [float(bpm)])
        if replace:
            self.client.send_message("/live/clip_slot/delete_clip", [track, slot])
        self.client.send_message("/live/clip_slot/create_clip", [track, slot, float(clip_len)])
        if name:
            self.client.send_message("/live/clip/set/name", [track, slot, str(name)])
        for i in range(0, len(notes), NOTES_PER_MSG):
            chunk = notes[i:i + NOTES_PER_MSG]
            flat = [track, slot]
            for (pitch, start, dur, vel, mute) in chunk:
                flat += [int(pitch), float(start), float(dur), int(vel), int(mute)]
            self.client.send_message("/live/clip/add/notes", flat)
        if loop_len:
            self.client.send_message("/live/clip/set/loop_start", [track, slot, 0.0])
            self.client.send_message("/live/clip/set/loop_end", [track, slot, float(loop_len)])
        if fire:
            self.client.send_message("/live/clip/fire", [track, slot])
        return len(notes), play_len

    def close(self):
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass


def send_midi_to_clip(midi_path, track=0, slot=0, host=DEFAULT_HOST, port=DEFAULT_PORT,
                      replace=True, fire=True, beats_per_bar=4, set_tempo=False, name=None,
                      auto_advance=False, measures_out=0):
    """Write the notes of ``midi_path`` into Ableton clip (track, slot) via AbletonOSC.

    If ``auto_advance`` is True, an occupied target slot is skipped — the clip is
    written into the first empty slot at/below ``slot`` instead of overwriting it.

    If ``set_tempo`` is True, the Live Set tempo is set to the file's tempo so the
    clip plays exactly as generated (a MIDI clip has no tempo of its own — it always
    plays at the Set tempo). Note: this changes the *global* project tempo.

    Returns (n_notes, used_slot); n_notes is -1 on failure.
    """
    try:
        from pythonosc import udp_client
    except ImportError:
        logger.error("python-osc not available; cannot send clip to AbletonOSC.")
        return -1, slot

    try:
        notes, end_beat, bpm = extract_notes(midi_path)
    except Exception as e:
        logger.exception(f"clip_output: failed to parse {midi_path}: {e}")
        return -1, slot

    bpb = beats_per_bar if beats_per_bar and beats_per_bar > 0 else 4
    clip_len, loop_len, length_beats = _clip_lengths(end_beat, bpb, measures_out)

    # If asked, drop into the slot under an existing clip instead of overwriting.
    used_slot = slot
    if auto_advance:
        used_slot = find_first_empty_slot(host, port, track, slot)

    client = udp_client.SimpleUDPClient(host, port)
    if set_tempo:
        client.send_message("/live/song/set/tempo", [float(bpm)])
    if replace and not auto_advance:
        client.send_message("/live/clip_slot/delete_clip", [track, used_slot])
    client.send_message("/live/clip_slot/create_clip", [track, used_slot, float(clip_len)])
    if name:
        client.send_message("/live/clip/set/name", [track, used_slot, str(name)])
    for i in range(0, len(notes), NOTES_PER_MSG):
        chunk = notes[i:i + NOTES_PER_MSG]
        flat = [track, used_slot]
        for (pitch, start, dur, vel, mute) in chunk:
            flat += [int(pitch), float(start), float(dur), int(vel), int(mute)]
        client.send_message("/live/clip/add/notes", flat)
    if loop_len:
        # Truncate the loop to N bars (keeps all notes; shortens the loop region).
        client.send_message("/live/clip/set/loop_start", [track, used_slot, 0.0])
        client.send_message("/live/clip/set/loop_end", [track, used_slot, float(loop_len)])
    if fire:
        client.send_message("/live/clip/fire", [track, used_slot])

    logger.info(
        f"clip_output: wrote {len(notes)} notes to track {track} slot {used_slot} "
        f"{('[' + str(name) + '] ') if name else ''}"
        f"({length_beats} beats{', set tempo %g' % bpm if set_tempo else ''}) "
        f"via AbletonOSC {host}:{port}"
    )
    return len(notes), used_slot
