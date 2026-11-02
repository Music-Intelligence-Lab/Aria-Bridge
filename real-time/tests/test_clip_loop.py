"""Regression coverage for continuous clip generation and per-pass progress."""

from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import clip_output
from modes import manual_mode
from modes.manual_mode import ManualModeSession
from modes.osc_controller import OscController


def session(**kwargs):
    return ManualModeSession("in", "out", aria_engine=Mock(), **kwargs)


@pytest.mark.parametrize("auto", [False, True])
def test_loop_records_seed_once_and_generates_without_waiting(monkeypatch, tmp_path, auto):
    seed, first, second, third = [tmp_path / f"{n}.mid" for n in range(4)]
    for path in (seed, first, second, third):
        path.touch()
    osc = Mock()
    osc.get_num_scenes.return_value = 3
    osc.get_tempo.return_value = 120
    osc.write_clip.return_value = (10, 8)
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    start_record = Mock(return_value=0)
    stop_record = Mock()
    monkeypatch.setattr(clip_output, "start_clip_record", start_record)
    monkeypatch.setattr(clip_output, "stop_clip_record", stop_record)
    monkeypatch.setattr(clip_output, "set_track_mute", Mock())
    s = session(clip_output=True, loop_mode=True, clip_track=2, clip_fire=auto)
    s._midi_stats = Mock(return_value=(480, 2))
    generated = threading.Event()

    def generate(**kwargs):
        # Playback is deliberately blocked until both generations have happened.
        assert not generated.is_set()
        if kwargs["prompt_midi_path"] == str(first):
            return str(second)
        assert kwargs["prompt_midi_path"] == str(second)
        generated.set()
        return str(third)

    def scheduler(osc, tempo, fire_q, stop_evt, manual_q=None):
        assert generated.wait(2), "Generation waited for playback"
        expected = [(0, 8), (1, 8), (2, 8), None] if auto else [None]
        assert [fire_q.get(timeout=2) for _ in expected] == expected

    s._generate_cancellable = generate
    s._fire_scheduler = scheduler
    s._begin_recording()
    s.recording_flag.clear()
    s._stop_native_clip_record()
    s._run_clip_loop(str(first), str(seed), 0.9, 0.95, None, 100)
    start_record.assert_called_once_with(host="127.0.0.1", port=11000, track=1, slot=0, auto_advance=True)
    stop_record.assert_called_once()
    assert [(c.args[1], c.args[2]) for c in osc.write_clip.call_args_list] == [(2, 0), (2, 1), (2, 2)]
    assert [c.args[0] for c in osc.write_clip.call_args_list] == [str(first), str(second), str(third)]
    assert generated.is_set()
    assert not any(p.exists() for p in (seed, first, second, third))


def test_every_generation_resets_and_finishes_progress():
    events = []
    s = session(osc_generation_start_cb=lambda: events.append("start"),
                osc_generation_done_cb=lambda: events.append("done"),
                osc_generation_progress_cb=lambda p: events.append(p))

    def generate(**kwargs):
        kwargs["progress_cb"](0.5)
        return "output.mid"

    s.aria_engine.generate.side_effect = generate
    for _ in range(2):
        assert s._generate_cancellable(progress_cb=s.osc_generation_progress_cb) == "output.mid"
    assert events == ["start", 0.0, 0.5, "done"] * 2


def test_clip_progress_enabled_and_playback_has_priority():
    osc = OscController("127.0.0.1", 9000, 9001, None, None, None)
    osc.client = Mock()
    osc.clip_enabled = True
    osc.send_generation_progress(0.5)
    osc.client.send_message.assert_called_with("/generation_progress", 50)
    osc.send_playback_duration(8)
    osc.client.reset_mock()
    osc.send_generation_progress(0.9)
    osc.client.send_message.assert_not_called()
    osc.send_playback_progress(0.25)
    osc.client.send_message.assert_called_with("/generation_progress", 25)
    osc.send_playback_stopped()
    assert not osc.playback_active
    osc.client.send_message.assert_called_with("/generation_progress", 0)


class FakeStop:
    def __init__(self):
        self.ticks = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.ticks += 1
        assert self.ticks < 50, "Playback scheduler did not finish"


def test_scheduler_counts_down_through_handoff_and_resets_each_pass():
    events = []
    s = session(clip_track=2, osc_playback_duration_cb=lambda d: events.append(("duration", d)),
                osc_playback_progress_cb=lambda p: events.append(("progress", p)),
                osc_playback_stopped_cb=lambda: events.append(("stopped", None)))
    q = queue.Queue()
    # Start with no successor: the first clip must keep reporting on its next pass.
    q.put((0, 8))
    positions = {0: iter([0, 4, 6, 0, 4, 6]), 1: iter([0, 4, 6, 7.95])}
    polls = 0
    fired = []

    def send(addr, args):
        fired.append((addr, args))

    def is_playing(track, slot):
        nonlocal polls
        if slot == 0:
            return True
        polls += 1
        return polls >= 2  # delayed by launch quantization

    def position(track, slot):
        pos = next(positions[slot])
        if slot == 0 and len(events) >= 4 and pos == 0:
            q.put((1, 8))
            q.put(None)
        return pos

    osc = SimpleNamespace(send=send, get_clip_is_playing=is_playing,
                          get_clip_playing_position=position, get_tempo=lambda: 120)
    s._fire_scheduler(osc, 120, q, FakeStop())
    assert fired == [("/live/track/set/mute", [2, 0]), ("/live/clip/fire", [2, 0]),
                     ("/live/track/set/mute", [2, 0]), ("/live/clip/fire", [2, 1])]
    assert [v for kind, v in events if kind == "duration"] == [4, 4, 4]
    assert [v for kind, v in events if kind == "progress"] == [0, 0.5, 0.75, 0, 0.5, 0.75, 0, 0.5, 0.75, 7.95 / 8]
    assert events[-1] == ("stopped", None)


def test_scheduler_cancel_clears_progress():
    stopped = Mock()
    s = session(osc_playback_stopped_cb=stopped)
    stop = threading.Event()
    stop.set()
    s._fire_scheduler(Mock(), 120, queue.Queue(), stop)
    stopped.assert_called_once()


@pytest.mark.parametrize("cancel", [False, True])
def test_stream_progress_moves_during_rests_and_cancel_is_prompt(monkeypatch, cancel):
    import mido

    now = [0.0]
    stop = threading.Event()
    values = []
    stopped = Mock()
    out = Mock()
    midi = SimpleNamespace(length=2.0)
    messages = [mido.Message("note_on", note=60, time=0),
                mido.Message("note_off", note=60, time=2)]

    class Midi:
        length = midi.length

        def __iter__(self):
            return iter(messages)

    def sleep(seconds):
        now[0] += seconds
        if cancel and now[0] >= 0.5:
            stop.set()

    monkeypatch.setattr(mido, "MidiFile", lambda _: Midi())
    monkeypatch.setattr(manual_mode.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(manual_mode.time, "sleep", sleep)
    sent, duration = manual_mode._play_midi_file(
        "rest.mid", out, values.append, stop_event=stop, stopped_cb=stopped)
    assert duration == 2
    assert len(values) >= 8
    assert values == sorted(values)
    stopped.assert_called_once()
    if cancel:
        assert now[0] < 0.55
        assert sent == 1
    else:
        assert sent == 2
        assert values[-1] == 1


def test_stream_error_releases_progress_ownership(monkeypatch):
    import mido

    class Midi:
        length = 1

        def __iter__(self):
            yield mido.Message("note_on", note=60, time=0)

    monkeypatch.setattr(mido, "MidiFile", lambda _: Midi())
    osc = OscController("127.0.0.1", 9000, 9001, None, None, None)
    osc.client = Mock()
    out = Mock()
    out.send.side_effect = RuntimeError("MIDI port closed")
    with pytest.raises(RuntimeError, match="MIDI port closed"):
        manual_mode._play_midi_file("error.mid", out,
                                    duration_cb=osc.send_playback_duration,
                                    stopped_cb=osc.send_playback_stopped)
    assert not osc.playback_active
    osc.send_generation_progress(0.5)
    osc.client.send_message.assert_called_with("/generation_progress", 50)


def test_single_clip_auto_uses_playback_tracker(monkeypatch, tmp_path):
    path = tmp_path / "output.mid"
    path.touch()
    write = Mock(return_value=(4, 3))
    monkeypatch.setattr(clip_output, "send_midi_to_clip", write)
    monkeypatch.setattr(clip_output, "extract_notes", lambda _: ([], 8, 120))
    osc = Mock()
    osc.get_tempo.return_value = 120
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    s = session(clip_output=True, clip_fire=True, clip_track=2)
    s.pending_output_path = str(path)
    calls = []

    def track(client, tempo, fire_q, stop, manual_q):
        calls.append((client, tempo, fire_q.get_nowait(), manual_q.get_nowait()))

    s._fire_scheduler = track
    assert s._write_clip_output()
    if s._clip_monitor_thread is not None:
        s._clip_monitor_thread.join(timeout=2)
    assert not write.call_args.kwargs["fire"]
    assert calls == [(osc, 120, None, (2, 3, 8))]
    osc.close.assert_called_once()
    assert not path.exists()


@pytest.mark.parametrize("clip", [False, True])
@pytest.mark.parametrize("start", ["_begin_recording", "_start_immediate_record"])
def test_record_mutes_output_before_native_capture(monkeypatch, clip, start):
    events = []
    monkeypatch.setattr(clip_output, "set_track_mute",
                        lambda host, port, track, muted: events.append(("mute", track, muted)))

    def record(**kwargs):
        events.append(("record", kwargs["track"], kwargs["slot"]))
        return kwargs["slot"]

    monkeypatch.setattr(clip_output, "start_clip_record", record)
    s = session(clip_output=clip, clip_track=3, clip_slot=4)
    getattr(s, start)()
    if clip:
        assert events[0] == ("mute", 3, True)
        if start == "_begin_recording":
            assert events[1] == ("record", 2, 4)
    else:
        assert events == []


@pytest.mark.parametrize("auto", [False, True])
def test_writing_clip_only_auto_launches_and_play_uses_current_selection(monkeypatch, tmp_path, auto):
    path = tmp_path / "output.mid"
    path.touch()
    write = Mock(return_value=(4, 5))
    monkeypatch.setattr(clip_output, "send_midi_to_clip", write)
    monkeypatch.setattr(clip_output, "extract_notes", lambda _: ([], 8, 120))
    s = session(clip_output=True, clip_fire=auto, clip_track=2, clip_slot=5)
    s.loop_running = True  # Hold launch requests for the shared worker.
    s.pending_output_path = str(path)
    assert s._write_clip_output()
    assert not path.exists()
    if auto:
        assert s._clip_manual_q.get_nowait() == (2, 5, 8)
    assert s._clip_manual_q.empty()
    # The source MIDI is gone; Play must launch the currently selected saved clip.
    s.set_clip_track(4)
    s.set_clip_slot(7)
    assert s._handle_play_request()
    assert s._clip_manual_q.get_nowait() == (4, 7, None)
    write.assert_called_once()


def test_manual_launch_unmutes_same_track_then_fires_selected_saved_clip():
    events = []
    manual_q = queue.Queue()
    manual_q.put((4, 7, None))
    auto_q = queue.Queue()
    auto_q.put(None)
    positions = iter([0, 7.95])
    osc = Mock()
    osc.send.side_effect = lambda addr, args: events.append((addr, args))
    osc.get_clip_loop_length.return_value = 8
    osc.get_clip_is_playing.return_value = True
    osc.get_tempo.return_value = 120
    osc.get_clip_playing_position.side_effect = lambda track, slot: next(positions)
    duration = Mock()
    s = session(clip_output=True, clip_track=2, clip_slot=0, osc_playback_duration_cb=duration)
    s._fire_scheduler(osc, 120, auto_q, FakeStop(), manual_q)
    assert events == [("/live/track/set/mute", [4, 0]), ("/live/clip/fire", [4, 7])]
    osc.get_clip_loop_length.assert_called_once_with(4, 7)
    duration.assert_called_once_with(4)
    osc.write_clip.assert_not_called()


def test_play_during_recording_keeps_output_muted():
    s = session(clip_output=True, clip_track=3, clip_slot=4)
    s.recording_flag.set()
    s._ensure_clip_monitor = Mock()
    assert s.request_clip_play()
    assert s._clip_manual_q.empty()
    s._ensure_clip_monitor.assert_not_called()


@pytest.mark.parametrize("clip", [False, True])
def test_osc_play_is_immediate_in_clip_mode_and_queued_for_streaming(clip):
    commands = queue.Queue()
    s = session(clip_output=clip, clip_track=3, clip_slot=4)
    s.loop_running = True  # Simulate a busy generation loop; no main command drain.
    osc = OscController("127.0.0.1", 9000, 9001, None, None, commands)
    osc.play_clip_cb = s.request_clip_play
    osc._handle_play("/aria/play")
    if clip:
        assert commands.empty()
        assert s._clip_manual_q.get_nowait() == (3, 4, None)
    else:
        assert commands.get_nowait() == ("play", None)


def test_selected_clip_loop_length_uses_live_region():
    osc = object.__new__(clip_output.AbletonOSCClient)
    osc.query = Mock(side_effect=[(3, 4, 8.0), (3, 4, 24.0)])
    assert osc.get_clip_loop_length(3, 4) == 16


def test_recording_stops_old_clip_monitor_before_muting(monkeypatch):
    events = []
    entered = threading.Event()
    osc = Mock()
    osc.get_tempo.return_value = 120
    osc.close.side_effect = lambda: events.append("closed")
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    monkeypatch.setattr(clip_output, "set_track_mute", lambda *a: events.append("muted"))
    monkeypatch.setattr(clip_output, "start_clip_record", Mock(return_value=0))
    s = session(clip_output=True, clip_track=3)

    def track(client, tempo, fire_q, stop, manual_q):
        manual_q.get_nowait()
        entered.set()
        assert stop.wait(2), "New recording did not stop playback monitor"

    s._fire_scheduler = track
    assert s.request_clip_play()
    assert entered.wait(2)
    s._begin_recording()
    assert events == ["closed", "muted"]
    assert s._clip_monitor_thread is None


def test_closing_clip_client_releases_reply_port():
    import socket

    osc = clip_output.AbletonOSCClient(port=9, recv_port=0)
    assert osc.server is not None
    server = osc.server
    port = server.socket.getsockname()[1]
    osc.close()
    assert server.socket.fileno() == -1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", port))
