import av
import numpy as np
from livekit.agents.llm import ChatContext, ChatMessage, FunctionCall

from src import metrics, transcript
from src.recording import next_call_id, ogg_to_mp3

T0 = 1_000_000.0


def make_stereo_ogg(path, seconds=2.0, rate=48000):
    """Opus/OGG like LiveKit's recorder writes: left = 440 Hz, right = silence."""
    with av.open(str(path), "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=rate, layout="stereo")
        n = int(seconds * rate)
        tone = np.sin(2 * np.pi * 440 * np.arange(n) / rate).astype(np.float32) * 0.3
        samples = np.stack([tone, np.zeros(n, dtype=np.float32)])
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
    assert m["talk_over"]["count"] == 1 and m["talk_over"]["events"][0]["speaker"] == "CLINIC_AGENT"
    assert m["longest_silence_s"] == 4.0  # 10.0 -> 14.0
    assert m["clinic_response_latency_s"]["n"] == 2


def test_call_ids_are_sequential(tmp_path, monkeypatch):
    monkeypatch.setattr("src.recording.CALLS_DIR", tmp_path)
    assert next_call_id("Simple Scheduling") == "001-simple-scheduling"
    (tmp_path / "001-simple-scheduling").mkdir()
    (tmp_path / "_iterations").mkdir()
    assert next_call_id("refill") == "002-refill"
