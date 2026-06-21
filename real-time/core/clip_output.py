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


def send_midi_to_clip(midi_path, track=0, slot=0, host=DEFAULT_HOST, port=DEFAULT_PORT,
                      replace=True, fire=True, beats_per_bar=4, set_tempo=False, name=None,
                      auto_advance=False):
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
    length_beats = max(bpb, math.ceil(end_beat / bpb) * bpb)

    # If asked, drop into the slot under an existing clip instead of overwriting.
    used_slot = slot
    if auto_advance:
        used_slot = find_first_empty_slot(host, port, track, slot)

    client = udp_client.SimpleUDPClient(host, port)
    if set_tempo:
        client.send_message("/live/song/set/tempo", [float(bpm)])
    if replace and not auto_advance:
        client.send_message("/live/clip_slot/delete_clip", [track, used_slot])
    client.send_message("/live/clip_slot/create_clip", [track, used_slot, float(length_beats)])
    if name:
        client.send_message("/live/clip/set/name", [track, used_slot, str(name)])
    for i in range(0, len(notes), NOTES_PER_MSG):
        chunk = notes[i:i + NOTES_PER_MSG]
        flat = [track, used_slot]
        for (pitch, start, dur, vel, mute) in chunk:
            flat += [int(pitch), float(start), float(dur), int(vel), int(mute)]
        client.send_message("/live/clip/add/notes", flat)
    if fire:
        client.send_message("/live/clip/fire", [track, used_slot])

    logger.info(
        f"clip_output: wrote {len(notes)} notes to track {track} slot {used_slot} "
        f"{('[' + str(name) + '] ') if name else ''}"
        f"({length_beats} beats{', set tempo %g' % bpm if set_tempo else ''}) "
        f"via AbletonOSC {host}:{port}"
    )
    return len(notes), used_slot
