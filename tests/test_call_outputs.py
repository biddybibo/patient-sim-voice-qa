import av
import numpy as np
import pytest
from livekit.agents.llm import ChatContext, ChatMessage, FunctionCall

from src import metrics, transcript
from src.audio_analysis import analyze
from src.recording import next_call_id, ogg_to_mp3

T0 = 1_000_000.0


def make_stereo_ogg(path, seconds=2.0, rate=48000, left=(0.0, 2.0), right=None):
    """Opus/OGG like LiveKit's recorder writes: a 440 Hz tone on each channel during the
    given (start, end) seconds, silence elsewhere. Left = clinic, right = bot."""
    with av.open(str(path), "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=rate, layout="stereo")
        n = int(seconds * rate)
        t = np.arange(n) / rate
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.3

        def channel(span):
            if span is None:
                return np.zeros(n, dtype=np.float32)
            return np.where((t >= span[0]) & (t < span[1]), tone, 0).astype(np.float32)

        samples = np.stack([channel(left), channel(right)])
        for start in range(0, n, 960):
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(samples[:, start : start + 960]), format="fltp", layout="stereo"
            )
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def test_ogg_to_mp3_keeps_stereo_and_duration(tmp_path):
    ogg, mp3 = tmp_path / "in.ogg", tmp_path / "out.mp3"
    make_stereo_ogg(ogg)
    ogg_to_mp3(ogg, mp3)
    with av.open(str(mp3)) as f:
        stream = f.streams.audio[0]
        assert f.format.name == "mp3"
        assert stream.layout.name == "stereo"
        frames = list(f.decode(audio=0))
    seconds = sum(fr.samples for fr in frames) / frames[0].sample_rate
    assert 1.9 < seconds < 2.2


def msg(role, text, start, stop, **latency):
    m = ChatMessage(role=role, content=[text], created_at=T0 + start)
    m.metrics = {"started_speaking_at": T0 + start, "stopped_speaking_at": T0 + stop, **latency}
    return m


def sample_chat():
    ctx = ChatContext()
    ctx.items.extend(
        [
            msg("user", "Thanks for calling, how can I help?", 0.5, 2.5, end_of_turn_delay=0.7),
            msg("assistant", "Hi, I'd like to book a checkup.", 3.6, 5.6,
                e2e_latency=1.1, llm_node_ttft=0.35, tts_node_ttfb=0.12),
            # clinic starts 0.5 s before the bot finished: talk-over
            msg("user", "Sure, what's your date of birth?", 5.1, 7.0, end_of_turn_delay=0.6),
            msg("assistant", "July fourth, two thousand.", 8.3, 10.0,
                e2e_latency=1.3, llm_node_ttft=0.4, tts_node_ttfb=0.1),
            msg("user", "You're all set for Tuesday at nine.", 14.0, 16.0),
        ]
    )
    ctx.items.append(
        FunctionCall(call_id="c1", name="end_call", arguments='{"reason": "goal_met"}', created_at=T0 + 18.0)
    )
    return ctx


def test_transcript_is_timed_from_recording_start():
    turns = transcript.build_turns(sample_chat().items, T0)
    assert [t["speaker"] for t in turns] == ["CLINIC_AGENT", "PATIENT_BOT", "CLINIC_AGENT",
                                             "PATIENT_BOT", "CLINIC_AGENT", "TOOL"]
    assert turns[1]["t_start"] == 3.6
    txt = transcript.format_txt(turns)
    assert "[00:03.6] PATIENT_BOT: Hi, I'd like to book a checkup." in txt
    assert '[00:18.0] -- end_call({"reason": "goal_met"})' in txt


def test_metrics():
    turns = transcript.build_turns(sample_chat().items, T0)
    m = metrics.compute(turns, duration=20.0, end_reason="goal_met")
    assert m["turns"] == {"clinic": 3, "bot": 2}
    assert m["bot_latency_s"]["response_e2e"]["median"] == 1.2
    assert m["bot_latency_s"]["end_of_turn_delay"]["n"] == 2
    est = m["talk_over_transcript_estimate"]
    assert est["count"] == 1 and est["events"][0]["speaker"] == "CLINIC_AGENT"
    assert m["longest_silence_s"] == 4.0  # 10.0 -> 14.0
    assert m["clinic_response_latency_s"]["n"] == 2


def test_bot_talking_inside_a_long_clinic_turn():
    """Pattern from call 002: bot jumped in after the recording disclaimer while the
    clinic kept talking; the clinic's turn (8.8-25.7) contains the bot's (10.4-14.9)."""
    ctx = ChatContext()
    ctx.items.extend([
        msg("user", "This call may be recorded.", 5.0, 8.2),
        msg("user", "I can help with that.", 8.8, 25.7),
        msg("assistant", "Hi, I'd like to book a checkup.", 10.4, 14.9, e2e_latency=2.2),
        msg("assistant", "I'm Jane Doe.", 27.1, 37.4, e2e_latency=1.4),
    ])
    m = metrics.compute(transcript.build_turns(ctx.items, T0), duration=40.0, end_reason="x")
    est = m["talk_over_transcript_estimate"]
    assert est["count"] == 1
    assert est["events"][0] == {"at": 10.4, "speaker": "PATIENT_BOT", "seconds": 4.5}
    assert m["longest_silence_s"] == 1.4  # 25.7 -> 27.1, not 14.9 -> 27.1


def test_response_after_turn_end_excludes_endpointing_wait():
    m = metrics.compute(transcript.build_turns(sample_chat().items, T0), duration=20.0, end_reason="x")
    # bot replies: e2e 1.1 after eot 0.7 -> 0.4; e2e 1.3 after eot 0.6 -> 0.7
    assert m["bot_latency_s"]["response_after_turn_end"]["median"] == 0.55


def test_audio_overlap_is_measured_from_the_channels(tmp_path):
    ogg = tmp_path / "call.ogg"
    make_stereo_ogg(ogg, seconds=3.0, left=(0.0, 1.0), right=(0.5, 1.5))
    audio = analyze(ogg)
    assert 0.4 <= audio["talk_over"]["seconds"] <= 0.6
    assert len(audio["talk_over"]["events"]) == 1
    assert audio["opening"]["bot_first_sound"] == pytest.approx(0.5, abs=0.05)
    assert audio["opening"]["bot_cut_in"] is True


def test_no_overlap_when_turns_alternate(tmp_path):
    ogg = tmp_path / "call.ogg"
    make_stereo_ogg(ogg, seconds=3.0, left=(0.0, 1.0), right=(1.3, 2.3))
    audio = analyze(ogg)
    assert audio["talk_over"]["seconds"] == 0.0
    assert audio["opening"]["bot_cut_in"] is False


def test_call_ids_are_sequential(tmp_path, monkeypatch):
    monkeypatch.setattr("src.recording.CALLS_DIR", tmp_path)
    assert next_call_id("Simple Scheduling") == "001-simple-scheduling"
    (tmp_path / "001-simple-scheduling").mkdir()
    (tmp_path / "_iterations").mkdir()
    assert next_call_id("refill") == "002-refill"
