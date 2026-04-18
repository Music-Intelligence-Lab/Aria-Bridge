"""Convert rolling MIDI buffer to prompt format for Aria model."""

import tempfile
import time
from typing import List

try:
    from .midi_buffer import TimestampedMidiMsg
except ImportError:
    from midi_buffer import TimestampedMidiMsg

try:
    from mido import MidiFile, MidiTrack, Message, MetaMessage
except ImportError:
    raise ImportError("mido is required. Install with: pip install mido")


def buffer_to_midi_dict(messages: List[TimestampedMidiMsg]) -> dict:
    """
    Convert buffer messages to ariautils.MidiDict-compatible format.
    
    This creates a dict matching the structure expected by ariautils.MidiDict:
    {
        'note_msgs': [{'data': {'start': tick, 'duration': tick_len, 'pitch': p, 'velocity': v}, 'tick': ...}],
        'pedal_msgs': [{'tick': ..., 'data': ...}],
        'resolution': <ppq>,
        ...
    }
    """
    if not messages:
        return {
            'note_msgs': [],
            'pedal_msgs': [],
            'resolution': 480,
        }

    # For MVP: use a reference tempo of 120 BPM = 2 beats/sec = 500ms per beat
    # Standard MIDI resolution: 480 ticks per quarter note
    # So: 480 ticks = 500ms => 1 tick = 1.04ms
    RESOLUTION = 480  # ticks per quarter note
    MS_PER_QUARTER = 500  # at 120 BPM
    ticks_per_ms = RESOLUTION / MS_PER_QUARTER

    # Get time reference
    if not messages:
        return {'note_msgs': [], 'pedal_msgs': [], 'resolution': RESOLUTION}

    first_timestamp = messages[0].timestamp
    note_msgs = []
    pedal_msgs = []
    active_notes = {}  # {(note_pitch, velocity): (msg_index, start_tick)}

    for msg in messages:
        relative_ms = (msg.timestamp - first_timestamp) * 1000  # Convert to ms
        tick = int(relative_ms * ticks_per_ms)

        if msg.msg_type == 'note_on' and msg.velocity and msg.velocity > 0:
            # Record start of note
            key = (msg.note, msg.velocity)
            active_notes[key] = tick

        elif msg.msg_type == 'note_off' or (msg.msg_type == 'note_on' and msg.velocity == 0):
            # Find and finalize note
            pitch = msg.note
            # Try to find a matching note_on (prefer match by pitch)
            end_tick = int(relative_ms * ticks_per_ms)
            found = False

            # Find first matching note with this pitch
            matching_key = None
            for (note_pitch, velocity), start_tick in list(active_notes.items()):
                if note_pitch == pitch:
                    matching_key = (note_pitch, velocity)
                    duration_ticks = max(1, end_tick - start_tick)
                    note_msgs.append({
                        'data': {
                            'start': start_tick,
                            'duration': duration_ticks,
                            'pitch': pitch,
                            'velocity': velocity,
                        },
                        'tick': start_tick,
                    })
                    del active_notes[matching_key]
                    found = True
                    break

            if not found and pitch in [n[0] for n in active_notes.keys()]:
                # Fallback: just use first note with this pitch
                for (note_pitch, vel) in list(active_notes.keys()):
                    if note_pitch == pitch:
                        start_tick = active_notes[(note_pitch, vel)]
                        duration_ticks = max(1, end_tick - start_tick)
                        note_msgs.append({
                            'data': {
                                'start': start_tick,
                                'duration': duration_ticks,
                                'pitch': pitch,
                                'velocity': vel,
                            },
                            'tick': start_tick,
                        })
                        del active_notes[(note_pitch, vel)]
                        break

        elif msg.msg_type == 'control_change' and msg.control == 64:
            # Sustain pedal
            pedal_msgs.append({
                'tick': tick,
                'data': 1 if msg.value and msg.value > 64 else 0,
            })

    # Convert remaining active notes to closed notes with zero duration
    for (note_pitch, velocity), start_tick in active_notes.items():
        note_msgs.append({
            'data': {
                'start': start_tick,
                'duration': 1,
                'pitch': note_pitch,
                'velocity': velocity,
            },
            'tick': start_tick,
        })

    return {
        'note_msgs': note_msgs,
        'pedal_msgs': pedal_msgs,
        'resolution': RESOLUTION,
    }


def buffer_to_tempfile_midi(
    messages: List[TimestampedMidiMsg],
    window_seconds: float = 4.0,
    current_bpm: float = None,
    ticks_per_beat: int = 480,
) -> str:
    """
    Convert buffer to a temporary MIDI file and return path.
    
    Extracts only messages from the last window_seconds of the buffer.
    Uses NamedTemporaryFile suffix='.mid', delete=False to keep it around.
    
    Args:
        messages: List of timestamped MIDI messages
        window_seconds: Time window to extract (default: 4.0s)
        current_bpm: Current BPM from tempo tracker (optional)
        ticks_per_beat: MIDI resolution (default: 480)
    
    Returns:
        Path to the temporary .mid file.
    """
    # Handle empty input
    if not messages:
        mid = MidiFile()
        track = MidiTrack()
        mid.tracks.append(track)
        if current_bpm:
            microseconds_per_beat = int(60_000_000 / current_bpm)
            track.append(MetaMessage('set_tempo', tempo=microseconds_per_beat, time=0))
        track.append(Message('program_change', program=0, time=0))
        mid.ticks_per_beat = ticks_per_beat
    else:
        # If messages include pulse information, prefer pulse-based conversion
        has_pulse = any(getattr(m, 'pulse', None) is not None for m in messages)
        if has_pulse:
            windowed_msgs = list(messages)
        else:
            now = time.monotonic()
            cutoff_time = now - window_seconds
            windowed_msgs = [msg for msg in messages if msg.timestamp >= cutoff_time]

        if not windowed_msgs:
            mid = MidiFile()
            track = MidiTrack()
            mid.tracks.append(track)
            if current_bpm:
                microseconds_per_beat = int(60_000_000 / current_bpm)
                track.append(MetaMessage('set_tempo', tempo=microseconds_per_beat, time=0))
            track.append(Message('program_change', program=0, time=0))
            mid.ticks_per_beat = ticks_per_beat
        else:
            # Reconstruct MIDI from windowed messages
            mid = MidiFile()
            track = MidiTrack()
            mid.tracks.append(track)
            mid.ticks_per_beat = ticks_per_beat

            if current_bpm:
                microseconds_per_beat = int(60_000_000 / current_bpm)
                track.append(MetaMessage('set_tempo', tempo=microseconds_per_beat, time=0))

            uses_pulse = any(getattr(m, 'pulse', None) is not None for m in windowed_msgs)

            if uses_pulse:
                PPQN = 24.0
                first_pulse = min(m.pulse for m in windowed_msgs if getattr(m, 'pulse', None) is not None)
                last_tick = 0
                sorted_msgs = sorted(windowed_msgs, key=lambda m: (m.pulse if getattr(m, 'pulse', None) is not None else 0))

                for msg in sorted_msgs:
                    if getattr(msg, 'pulse', None) is None:
                        continue
                    tick = int(((msg.pulse - first_pulse) / PPQN) * ticks_per_beat)
                    delta = max(0, tick - last_tick)

                    if msg.msg_type == 'note_on' and msg.velocity and msg.velocity > 0:
                        track.append(Message('note_on', note=msg.note, velocity=msg.velocity, time=delta))
                        last_tick = tick
                    elif msg.msg_type == 'note_off' or (msg.msg_type == 'note_on' and msg.velocity == 0):
                        vel = msg.velocity if msg.velocity else 0
                        track.append(Message('note_off', note=msg.note, velocity=vel, time=delta))
                        last_tick = tick
                    elif msg.msg_type == 'control_change' and msg.control == 64:
                        value = 127 if (msg.value and msg.value > 64) else 0
                        track.append(Message('control_change', control=64, value=value, time=delta))
                        last_tick = tick

            else:
                first_timestamp = windowed_msgs[0].timestamp
                last_tick = 0
                sorted_msgs = sorted(windowed_msgs, key=lambda m: m.timestamp)
                seconds_per_beat = 60.0 / current_bpm if current_bpm else 0.5  # default 120 BPM
                ticks_per_second = ticks_per_beat / seconds_per_beat

                for msg in sorted_msgs:
                    relative_seconds = (msg.timestamp - first_timestamp)
                    tick = int(relative_seconds * ticks_per_second)
                    delta = max(0, tick - last_tick)

                    if msg.msg_type == 'note_on' and msg.velocity and msg.velocity > 0:
                        track.append(Message('note_on', note=msg.note, velocity=msg.velocity, time=delta))
                        last_tick = tick
                    elif msg.msg_type == 'note_off' or (msg.msg_type == 'note_on' and msg.velocity == 0):
                        vel = msg.velocity if msg.velocity else 0
                        track.append(Message('note_off', note=msg.note, velocity=vel, time=delta))
                        last_tick = tick
                    elif msg.msg_type == 'control_change' and msg.control == 64:
                        value = 127 if (msg.value and msg.value > 64) else 0
                        track.append(Message('control_change', control=64, value=value, time=delta))
                        last_tick = tick

    # Write to temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.mid', delete=False)
    tmp.close()
    mid.save(tmp.name)
    return tmp.name
