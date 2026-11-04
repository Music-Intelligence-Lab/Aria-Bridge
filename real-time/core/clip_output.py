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


def _query_empty_clip_slots(host, port, track, start_slot, recv_port=RECV_PORT):
    osc = AbletonOSCClient(host, port, recv_port=recv_port)
    try:
        return osc.get_empty_clip_slots(track, start_slot)
    finally:
        osc.close()


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
    a clip (the bridge still buffers the same MIDI over ARIA_IN for the prompt).
    With ``auto_advance``, search for an empty slot from the selected slot and
    wrap to the top without overwriting a take.

    Requires the input track's MIDI-From to be set to the same controller as ARIA_IN
    (arming only enables record; it doesn't route input). Returns the slot used,
    or -1 if recording could not be started.
    """
    try:
        from pythonosc import udp_client
    except ImportError:
        logger.error("python-osc not available; cannot start clip record.")
        return -1
    used_slot = slot
    if auto_advance:
        empty_slots = _query_empty_clip_slots(host, port, track, slot, recv_port)
        if not empty_slots:
            logger.warning("No confirmed empty recording slot on Ableton track %s", track)
            return -1
        used_slot = empty_slots[0]
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
    """Return the full clip length and its playable length in beats.

    The full clip holds every note. A positive Measures setting can shorten
    playback via the end marker without removing notes or padding the clip.
    """
    full_len = max(bpb, math.ceil(round(end_beat, 4) / bpb) * bpb)
    if measures_out and measures_out > 0:
        return full_len, min(full_len, measures_out * bpb)
    return full_len, full_len


def _set_one_shot(client, track, slot, clip_len, play_len):
    client.send_message("/live/clip/set/looping", [track, slot, False])
    if play_len < clip_len:
        client.send_message("/live/clip/set/end_marker", [track, slot, float(play_len)])


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
            threading.Thread(target=self.server.serve_forever,
                             kwargs={"poll_interval": 0.05}, daemon=True).start()
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
        """Return Live's playing slot index, or None if the reply is unavailable."""
        r = self.query("/live/track/get/playing_slot_index", [int(track)])
        if r and len(r) >= 2 and r[0] == int(track):
            try:
                return int(r[-1])
            except (ValueError, TypeError):
                pass
        return None

    def can_launch_at_clip_end(self, track, slot, next_slot, beats_per_bar):
        """Check whether a one-bar launch will land on the playing clip's end."""
        quant = self.query("/live/clip/get/launch_quantization", [track, next_slot])
        if not quant:
            return False
        if quant[-1] == 0:  # Global
            global_quant = self.query("/live/song/get/clip_trigger_quantization", [])
            if not global_quant or global_quant[-1] != 4:  # 1 Bar in the Song API
                return False
        elif quant[-1] != 5:  # 1 Bar in the Clip API
            return False

        args = [track, slot]
        looping = self.query("/live/clip/get/looping", args)
        if not looping or looping[-1] != 0:
            return False
        start = self.query("/live/clip/get/start_time", args)
        end = self.query("/live/clip/get/end_marker", args)
        numerator = self.query("/live/song/get/signature_numerator", [])
        denominator = self.query("/live/song/get/signature_denominator", [])
        now = self.query("/live/song/get/current_song_time", [])
        if not all((start, end, numerator, denominator, now)):
            return False
        bar_length = 4 * numerator[-1] / denominator[-1]
        end_time = start[-1] + end[-1]
        end_bar = end_time / bar_length
        return (bar_length == beats_per_bar
                and math.isclose(end_bar, round(end_bar), rel_tol=0, abs_tol=1e-6)
                and 0 < end_time - now[-1] < bar_length)

    def wait_for_slot_release(self, track, slot, cancelled):
        """Wait for the slot to stop playing before replacing its clip."""
        import time

        while not cancelled():
            reply = self.query("/live/clip_slot/get/is_playing", [track, slot])
            if not reply or reply[-1] not in (0, 1):
                return False
            if reply[-1] == 0:
                return not cancelled()
            time.sleep(0.05)
        return False

    def get_empty_clip_slots(self, track, start_slot):
        """Return empty slots from the selected slot, wrapping to the top."""
        r = self.query("/live/track/get/clips/length", [int(track)])
        if not r or len(r) < 2 or r[0] != int(track):
            return None
        lengths = r[1:]
        start = start_slot % len(lengths)
        slots = range(len(lengths))
        return [slot for slot in (*slots[start:], *slots[:start]) if lengths[slot] is None]

    def get_tempo(self):
        r = self.query("/live/song/get/tempo", [])
        if r:
            try:
                return float(r[-1])
            except (ValueError, TypeError):
                return None
        return None

    def get_clip_is_playing(self, track, slot):
        """Return whether the clip is playing, or None if the state is unknown."""
        r = self.query("/live/clip/get/is_playing", [int(track), int(slot)])
        if r:
            try:
                return bool(int(r[-1]))
            except (ValueError, TypeError):
                pass
        return None

    def get_clip_playing_position(self, track, slot):
        """Current playback position of the clip at (track, slot), in beats. None if unknown."""
        r = self.query("/live/clip/get/playing_position", [int(track), int(slot)])
        if r:
            try:
                return float(r[-1])
            except (ValueError, TypeError):
                return None
        return None

    def get_clip_loop_length(self, track, slot):
        """Read the clip's playback region, in beats, with or without Loop."""
        start = self.query("/live/clip/get/loop_start", [int(track), int(slot)])
        end = self.query("/live/clip/get/loop_end", [int(track), int(slot)])
        if start and end:
            try:
                length = float(end[-1]) - float(start[-1])
                return length if length > 0 else None
            except (ValueError, TypeError):
                pass
        return None

    def write_clip(self, midi_path, track, slot, name=None, beats_per_bar=4,
                   set_tempo=False, replace=True, fire=False, measures_out=0):
        """Write a .mid into (track, slot). Send-only (uses this client's socket).

        The clip plays once, up to ``measures_out`` bars if specified. Returns
        (n_notes, play_len_beats).
        """
        notes, end_beat, bpm = extract_notes(midi_path)
        bpb = beats_per_bar if beats_per_bar and beats_per_bar > 0 else 4
        clip_len, play_len = _clip_lengths(end_beat, bpb, measures_out)
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
        _set_one_shot(self.client, track, slot, clip_len, play_len)
        if fire:
            self.client.send_message("/live/clip/fire", [track, slot])
        return len(notes), play_len

    def close(self):
        if self.server:
            try:
                self.server.shutdown()
            finally:
                self.server.server_close()
                self.server = None


def send_midi_to_clip(midi_path, track=0, slot=0, host=DEFAULT_HOST, port=DEFAULT_PORT,
                      replace=True, fire=True, beats_per_bar=4, set_tempo=False, name=None,
                      auto_advance=False, measures_out=0, cancelled=None):
    """Write the notes of ``midi_path`` into Ableton clip (track, slot) via AbletonOSC.

    If ``auto_advance`` is True, look for an empty slot starting at ``slot`` and
    wrapping to the top. If every slot is occupied, replace slot 0 when allowed.
    With a ``cancelled`` callback, wait for slot 0 to stop playing before replacing it.

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
    clip_len, length_beats = _clip_lengths(end_beat, bpb, measures_out)

    used_slot = slot
    if auto_advance:
        empty_slots = _query_empty_clip_slots(host, port, track, slot)
        if empty_slots is None or (not empty_slots and not replace):
            logger.warning("No writable clip slot confirmed on Ableton track %s", track)
            return -1, slot
        used_slot = empty_slots[0] if empty_slots else 0
        replace = not empty_slots
        if replace and cancelled is not None:
            osc = AbletonOSCClient(host, port)
            try:
                if not osc.wait_for_slot_release(track, used_slot, cancelled):
                    return -1, used_slot
            finally:
                osc.close()

    if cancelled is not None and cancelled():
        return -1, used_slot

    client = udp_client.SimpleUDPClient(host, port)
    if set_tempo:
        client.send_message("/live/song/set/tempo", [float(bpm)])
    if replace:
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
    _set_one_shot(client, track, used_slot, clip_len, length_beats)
    if fire:
        client.send_message("/live/clip/fire", [track, used_slot])

    logger.info(
        f"clip_output: wrote {len(notes)} notes to track {track} slot {used_slot} "
        f"{('[' + str(name) + '] ') if name else ''}"
        f"({length_beats} beats{', set tempo %g' % bpm if set_tempo else ''}) "
        f"via AbletonOSC {host}:{port}"
    )
    return len(notes), used_slot
