"""Regression coverage for continuous clip generation and playback progress."""

from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import clip_output
from core.midi_buffer import TimestampedMidiMsg
from core.sampling_state import SessionState
from modes import manual_mode
from modes.manual_mode import ManualModeSession
from modes.osc_controller import OscController


def session(**kwargs):
    return ManualModeSession("in", "out", aria_engine=Mock(), **kwargs)


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("empty_slots, output_slots", [([0, 1, 2], [0, 1, 2]), ([2, 1], [2, 1, 0])])
def test_loop_records_seed_once_and_generates_without_waiting(
    monkeypatch, tmp_path, auto, empty_slots, output_slots,
):
    seed, first, second, third = [tmp_path / f"{n}.mid" for n in range(4)]
    for path in (seed, first, second, third):
        path.touch()
    osc = Mock()
    osc.get_empty_clip_slots.return_value = empty_slots
    osc.get_tempo.return_value = 120
    osc.write_clip.return_value = (10, 8)
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    start_record = Mock(return_value=0)
    stop_record = Mock()
    monkeypatch.setattr(clip_output, "start_clip_record", start_record)
    monkeypatch.setattr(clip_output, "stop_clip_record", stop_record)
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
        expected = [(2, slot, 8) for slot in output_slots] + [None] if auto else [None]
        assert [fire_q.get(timeout=2) for _ in expected] == expected

    s._generate_cancellable = generate
    s._fire_scheduler = scheduler
    s._begin_recording()
    s.recording_flag.clear()
    s._stop_native_clip_record()
    s._run_clip_loop(str(first), str(seed), 0.9, 0.95, None, 100)
    start_record.assert_called_once_with(host="127.0.0.1", port=11000, track=1, slot=0, auto_advance=True)
    stop_record.assert_called_once_with(host="127.0.0.1", port=11000, track=1, disarm=False)
    assert [(c.args[1], c.args[2]) for c in osc.write_clip.call_args_list] == [
        (2, slot) for slot in output_slots
    ]
    assert [c.kwargs["replace"] for c in osc.write_clip.call_args_list] == [
        slot not in empty_slots for slot in output_slots
    ]
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


@pytest.mark.parametrize("ready_tick, next_start", [(1, 3), (5, 6)])
def test_scheduler_hands_off_to_ready_or_late_clip(ready_tick, next_start):
    events = []
    s = session(clip_track=2, osc_playback_duration_cb=lambda d: events.append(("duration", d)),
                osc_playback_progress_cb=lambda p: events.append(("progress", p)),
                osc_playback_stopped_cb=lambda: events.append(("stopped", None)))
    q = queue.Queue()
    q.put((2, 0, 8))
    stop = FakeStop()
    wait = stop.wait
    fired = []

    def advance(seconds):
        wait(seconds)
        if stop.ticks == ready_tick:
            q.put((2, 1, 8))
            q.put(None)

    stop.wait = advance

    def send(addr, args):
        if addr == "/live/clip/fire":
            fired.append((args, stop.ticks))

    def is_playing(track, slot):
        if slot == 0:
            return stop.ticks < 3
        return next_start <= stop.ticks < next_start + 3

    def position(track, slot):
        tick = stop.ticks if slot == 0 else stop.ticks - next_start
        return (0, 4, 6)[tick]

    osc = SimpleNamespace(send=send, get_clip_is_playing=is_playing,
                          get_clip_playing_position=position, get_tempo=lambda: 120,
                          get_playing_slot_index=lambda track: -1,
                          can_launch_at_clip_end=lambda *args: True)
    s._fire_scheduler(osc, 120, q, stop)
    assert fired == [([2, 0], 0), ([2, 1], ready_tick)]
    take = [("duration", 4), ("progress", 0), ("progress", 0.5), ("progress", 0.75)]
    stopped = [("stopped", None)]
    gap = stopped if ready_tick >= 3 else []
    assert events == take + gap + take + stopped


@pytest.mark.parametrize("aligned, fire_tick", [(True, 1), (False, 3)])
def test_auto_waits_for_clip_already_playing_in_live(aligned, fire_tick):
    s = session(clip_track=2)
    stop = FakeStop()
    fire_q = queue.Queue()
    fire_q.put((2, 5, 8))
    fire_q.put(None)
    launches = []
    osc = Mock()
    osc.get_tempo.return_value = 120
    osc.get_playing_slot_index.side_effect = lambda track: 3 if stop.ticks < 3 else -1
    osc.get_clip_loop_length.return_value = 8
    osc.can_launch_at_clip_end.return_value = aligned
    osc.get_clip_is_playing.side_effect = lambda track, slot: (
        stop.ticks < 3 if slot == 3 else 3 <= stop.ticks < 6
    )
    osc.get_clip_playing_position.side_effect = lambda track, slot: (stop.ticks % 3) * 2 + 2

    def send(address, args):
        if address == "/live/clip/fire":
            launches.append((args, stop.ticks))

    osc.send.side_effect = send
    s._fire_scheduler(osc, 120, fire_q, stop)
    assert launches == [([2, 5], fire_tick)]
    osc.get_clip_loop_length.assert_called_once_with(2, 3)


@pytest.mark.parametrize("quant, global_quant, clip_end, song_time, expected", [
    (0, 4, 8.0, 14.0, True),
    (5, 0, 8.0, 14.0, True),
    (0, 0, 8.0, 14.0, False),
    (1, 4, 8.0, 14.0, False),
    (0, 4, 7.0, 14.0, False),
    (0, 4, 8.0, 10.0, False),
    (0, 4, 8.0, 16.0, False),
])
def test_early_launch_requires_matching_live_bar_boundary(
    quant, global_quant, clip_end, song_time, expected,
):
    replies = {
        "/live/clip/get/launch_quantization": (2, 5, quant),
        "/live/song/get/clip_trigger_quantization": (global_quant,),
        "/live/clip/get/looping": (2, 3, False),
        "/live/clip/get/start_time": (2, 3, 8.0),
        "/live/clip/get/end_marker": (2, 3, clip_end),
        "/live/song/get/signature_numerator": (4,),
        "/live/song/get/signature_denominator": (4,),
        "/live/song/get/current_song_time": (song_time,),
    }
    osc = object.__new__(clip_output.AbletonOSCClient)
    osc.query = lambda address, args: replies[address]
    assert osc.can_launch_at_clip_end(2, 3, 5, 4) is expected


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("measures", [0, 1, 4])
def test_clip_writers_disable_loop_and_preserve_measures(monkeypatch, persistent, measures):
    from pythonosc import udp_client

    client = Mock()
    notes = [(60, 0, 1, 80, 0), (64, 6, 1, 80, 0)]
    monkeypatch.setattr(clip_output, "extract_notes", lambda path: (notes, 7, 120))
    if persistent:
        osc = clip_output.AbletonOSCClient.__new__(clip_output.AbletonOSCClient)
        osc.client = client
        assert osc.write_clip("output.mid", 2, 3, fire=True, measures_out=measures) == (
            2, 4 if measures == 1 else 8,
        )
    else:
        monkeypatch.setattr(udp_client, "SimpleUDPClient", Mock(return_value=client))
        assert clip_output.send_midi_to_clip("output.mid", track=2, slot=3, measures_out=measures) == (2, 3)

    expected = [
        call("/live/clip_slot/delete_clip", [2, 3]),
        call("/live/clip_slot/create_clip", [2, 3, 8.0]),
        call("/live/clip/add/notes", [2, 3, 60, 0.0, 1.0, 80, 0, 64, 6.0, 1.0, 80, 0]),
        call("/live/clip/set/looping", [2, 3, False]),
    ]
    if measures == 1:
        expected.append(call("/live/clip/set/end_marker", [2, 3, 4.0]))
    expected.append(call("/live/clip/fire", [2, 3]))
    assert client.send_message.call_args_list == expected


@pytest.mark.parametrize("reply, expected", [
    ((2, None, 4.0, 4.0, None, None), [3, 4, 0]),
    ((2, 4.0, 4.0, 4.0), []),
    (None, None),
])
def test_empty_slot_search_wraps_from_selection(reply, expected):
    osc = object.__new__(clip_output.AbletonOSCClient)
    osc.query = Mock(return_value=reply)
    assert osc.get_empty_clip_slots(2, 2) == expected
    osc.query.assert_called_once_with("/live/track/get/clips/length", [2])


@pytest.mark.parametrize("slots, replace, target", [
    ([4], True, 4), ([], True, 0), ([], False, None), (None, True, None),
])
def test_output_uses_empty_slot_or_replaces_slot_zero(monkeypatch, slots, replace, target):
    from pythonosc import udp_client

    client = Mock()
    monkeypatch.setattr(udp_client, "SimpleUDPClient", Mock(return_value=client))
    monkeypatch.setattr(clip_output, "extract_notes", lambda _: ([], 0, 120))
    lookup = Mock(return_value=slots)
    monkeypatch.setattr(clip_output, "_query_empty_clip_slots", lookup)

    result = clip_output.send_midi_to_clip("output.mid", track=2, slot=3, fire=False,
                                          auto_advance=True, replace=replace)
    lookup.assert_called_once_with("127.0.0.1", 11000, 2, 3)
    if target is None:
        assert result == (-1, 3)
        client.send_message.assert_not_called()
    else:
        assert result == (0, target)
        expected = [call("/live/clip_slot/delete_clip", [2, 0])] if target == 0 else []
        assert client.send_message.call_args_list == expected + [
            call("/live/clip_slot/create_clip", [2, target, 4.0]),
            call("/live/clip/set/looping", [2, target, False]),
        ]


@pytest.mark.parametrize("released", [False, True])
def test_full_track_checks_playing_slot_before_deleting(monkeypatch, released):
    from pythonosc import udp_client

    osc = Mock()
    osc.wait_for_slot_release.return_value = released
    client = Mock()
    client.send_message.side_effect = lambda *args: osc.wait_for_slot_release.assert_called_once()
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    monkeypatch.setattr(clip_output, "_query_empty_clip_slots", Mock(return_value=[]))
    monkeypatch.setattr(clip_output, "extract_notes", lambda _: ([], 0, 120))
    monkeypatch.setattr(udp_client, "SimpleUDPClient", Mock(return_value=client))
    cancelled = lambda: False

    assert clip_output.send_midi_to_clip("output.mid", track=2, auto_advance=True,
                                        cancelled=cancelled, fire=False) == (0 if released else -1, 0)
    osc.wait_for_slot_release.assert_called_once_with(2, 0, cancelled)
    if released:
        assert client.send_message.call_args_list[0] == call("/live/clip_slot/delete_clip", [2, 0])
    else:
        client.send_message.assert_not_called()


def test_replacement_waits_until_live_reports_slot_stopped(monkeypatch):
    osc = object.__new__(clip_output.AbletonOSCClient)
    osc.query = Mock(side_effect=[(2, 0, True), (2, 0, True), (2, 0, False)])
    monkeypatch.setattr(manual_mode.time, "sleep", Mock())
    assert osc.wait_for_slot_release(2, 0, lambda: False)
    assert osc.query.call_args_list == [call("/live/clip_slot/get/is_playing", [2, 0])] * 3


@pytest.mark.parametrize("empty_slots", [[1], []])
def test_native_record_uses_empty_input_slot_without_replacing_a_take(monkeypatch, empty_slots):
    from pythonosc import udp_client

    client = Mock()
    monkeypatch.setattr(udp_client, "SimpleUDPClient", Mock(return_value=client))
    monkeypatch.setattr(clip_output, "_query_empty_clip_slots", Mock(return_value=empty_slots))

    result = clip_output.start_clip_record(track=2, slot=4)
    if empty_slots:
        assert result == 1
        assert client.send_message.call_args_list == [
            call("/live/track/set/arm", [2, 1]),
            call("/live/clip_slot/fire", [2, 1]),
        ]
    else:
        assert result == -1
        client.send_message.assert_not_called()


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


@pytest.mark.parametrize("fire_values, auto", [((), False), ((1,), True), ((1, 0), False)])
def test_stream_auto_plays_when_generation_finishes(monkeypatch, tmp_path, fire_values, auto):
    import mido

    prompt_path = tmp_path / "prompt.mid"
    prompt_path.touch()
    output_path = tmp_path / "output.mid"
    midi = mido.MidiFile()
    note_on = mido.Message("note_on", note=60)
    note_off = mido.Message("note_off", note=60)
    midi.add_track().extend([note_on, note_off])
    midi.save(output_path)
    monkeypatch.setattr(manual_mode, "buffer_to_tempfile_midi", Mock(return_value=str(prompt_path)))

    state = SessionState()
    s = session(clip_output=False, session_state=state)
    s.recorded.append(TimestampedMidiMsg("note_on", note=60, velocity=64))
    s.out_port = Mock()
    s._midi_stats = Mock(return_value=(0, 0))
    s._generate_once = Mock(return_value=str(output_path))
    s._wait_for_play = Mock()
    osc = OscController("127.0.0.1", 9000, 9001, None, state, None)
    osc.set_fire_cb = s.set_clip_fire
    for value in fire_values:
        osc._handle_fire("/aria/fire", value)

    s._finish_recording_and_generate()

    assert not prompt_path.exists()
    if auto:
        s._wait_for_play.assert_not_called()
        assert s.out_port.send.call_args_list == [call(note_on), call(note_off)]
        assert not output_path.exists()
        assert not state.has_pending_output
        assert state.get_snapshot()["status"] == "IDLE"
    else:
        s._wait_for_play.assert_called_once()
        s.out_port.send.assert_not_called()
        assert output_path.exists()
        assert state.has_pending_output
        assert state.get_snapshot()["status"] == "READY"


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
        calls.append((client, tempo, fire_q.get_nowait(), fire_q.get_nowait()))
        assert manual_q.empty()

    s._fire_scheduler = track
    assert s._write_clip_output()
    if s._clip_monitor_thread is not None:
        s._clip_monitor_thread.join(timeout=2)
    assert not write.call_args.kwargs["fire"]
    assert calls == [(osc, 120, (2, 3, 8), None)]
    osc.close.assert_called_once()
    assert not path.exists()


@pytest.mark.parametrize("clip", [False, True])
def test_record_starts_native_capture_on_input_track(monkeypatch, clip):
    record = Mock(return_value=4)
    monkeypatch.setattr(clip_output, "start_clip_record", record)
    s = session(clip_output=clip, clip_track=3, clip_slot=4)
    s._begin_recording()
    assert s.recording_flag.is_set()
    if clip:
        assert record.call_args.kwargs["track"] == 2
        assert record.call_args.kwargs["slot"] == 4
    else:
        record.assert_not_called()


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
        assert s._clip_auto_q.get_nowait() == (2, 5, 8)
    assert s._clip_manual_q.empty()
    # The source MIDI is gone; Play must launch the currently selected saved clip.
    s.set_clip_track(4)
    s.set_clip_slot(7)
    assert s._handle_play_request()
    assert s._clip_manual_q.get_nowait() == (4, 7, None)
    write.assert_called_once()
    assert write.call_args.kwargs["auto_advance"] is True


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
    stop = FakeStop()
    osc.get_clip_is_playing.side_effect = lambda track, slot: stop.ticks < 2
    osc.get_tempo.return_value = 120
    osc.get_clip_playing_position.side_effect = lambda track, slot: next(positions)
    duration = Mock()
    s = session(clip_output=True, clip_track=2, clip_slot=0, osc_playback_duration_cb=duration)
    s._fire_scheduler(osc, 120, auto_q, stop, manual_q)
    assert events == [("/live/track/set/mute", [4, 0]), ("/live/clip/fire", [4, 7])]
    osc.get_clip_loop_length.assert_called_once_with(4, 7)
    duration.assert_called_once_with(4)
    osc.write_clip.assert_not_called()


def test_play_during_recording_keeps_current_clip_selection():
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


def test_osc_record_ends_previous_generation_chain_and_queues_new_take():
    commands = queue.Queue()
    state = SessionState()
    s = session(clip_output=True, loop_mode=True, command_queue=commands, session_state=state)
    s.loop_running = True
    osc = OscController("127.0.0.1", 9000, 9001, None, state, commands)
    osc.record_start_cb = s.interrupt_loop_for_recording
    osc._record_target = 1

    osc._commit_record()

    assert s.generation_cancel_event.is_set()
    assert not s.playback_cancel_event.is_set()
    assert commands.get_nowait() == ("record_start", None)


def test_selected_clip_loop_length_uses_live_region():
    osc = object.__new__(clip_output.AbletonOSCClient)
    osc.query = Mock(side_effect=[(3, 4, 8.0), (3, 4, 24.0)])
    assert osc.get_clip_loop_length(3, 4) == 16


def test_recording_releases_osc_listener_without_stopping_live_clip(monkeypatch):
    events = []
    entered = threading.Event()
    osc = Mock()
    osc.get_tempo.return_value = 120
    osc.close.side_effect = lambda: events.append("closed")
    monkeypatch.setattr(clip_output, "AbletonOSCClient", Mock(return_value=osc))
    record = Mock(side_effect=lambda **kwargs: events.append("record") or 0)
    monkeypatch.setattr(clip_output, "start_clip_record", record)
    s = session(clip_output=True, clip_track=3)

    def track(client, tempo, fire_q, stop, manual_q):
        manual_q.get_nowait()
        entered.set()
        assert stop.wait(2), "New recording did not stop playback monitor"

    s._fire_scheduler = track
    assert s.request_clip_play()
    assert entered.wait(2)
    s._begin_recording()
    assert events == ["closed", "record"]
    osc.send.assert_not_called()
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
