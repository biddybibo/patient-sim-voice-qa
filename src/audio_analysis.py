"""Talk-over and pauses measured from the stereo recording itself.

Transcript timestamps are not reliable for overlap: a clinic "turn" in the chat history
can span seconds of silence (call 002 reported 4.5 s of talk-over from transcript spans;
the audio shows none). The recording has the clinic on the left channel and the bot on
the right, so "both channels have sound" is the ground truth for talking over each other.
"""

from pathlib import Path

import av
import numpy as np

WINDOW = 0.02  # seconds per RMS window
SPEECH_RMS = 0.01  # above this a window counts as sound
MIN_EVENT = 0.3  # overlaps / pauses shorter than this are ignored as noise
WORD_GAP = 0.1  # gaps this short are joined into one run of speech


def channel_activity(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Per-window booleans (clinic, bot): does each channel have sound in that window?"""
    with av.open(str(path)) as f:
        frames = [fr.to_ndarray() for fr in f.decode(audio=0)]
        rate = f.streams.audio[0].rate
    audio = np.concatenate(frames, axis=1)
    hop = int(WINDOW * rate)
    n = audio.shape[1] // hop
    rms = np.sqrt((audio[:, : n * hop].reshape(2, n, hop) ** 2).mean(axis=2))
    return rms[0] > SPEECH_RMS, rms[1] > SPEECH_RMS


def runs(active: np.ndarray) -> list[tuple[float, float]]:
    """(start, end) seconds of contiguous sound, joining gaps shorter than WORD_GAP."""
    out: list[list[float]] = []
    for i, on in enumerate(active):
        if not on:
            continue
        t = i * WINDOW
        if out and t - out[-1][1] <= WORD_GAP:
            out[-1][1] = t + WINDOW
        else:
            out.append([t, t + WINDOW])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def overlap(clinic: np.ndarray, bot: np.ndarray) -> dict:
    both = clinic & bot
    events = [{"at": a, "seconds": round(b - a, 2)} for a, b in runs(both) if b - a >= MIN_EVENT]
    return {"seconds": round(float(both.sum()) * WINDOW, 2), "events": events}


def opening(clinic: np.ndarray, bot: np.ndarray) -> dict:
    """The clinic's opening before the bot first spoke: its pauses, and whether the bot
    started while the clinic was still talking (clinic sound within 1 s after the bot began)."""
    clinic_runs, bot_runs = runs(clinic), runs(bot)
    bot_first = bot_runs[0][0] if bot_runs else None
    before = [r for r in clinic_runs if bot_first is None or r[0] < bot_first]
    pauses = [
        {"after": a[1], "seconds": round(b[0] - a[1], 2)}
        for a, b in zip(before, before[1:])
        if b[0] - a[1] >= MIN_EVENT
    ]
    cut_in = bot_first is not None and any(
        start < bot_first + 1.0 and end > bot_first for start, end in clinic_runs
    )
    return {"pauses": pauses, "bot_first_sound": bot_first, "bot_cut_in": cut_in}


def analyze(recording: Path) -> dict:
    clinic, bot = channel_activity(recording)
    return {"talk_over": overlap(clinic, bot), "opening": opening(clinic, bot)}
