"""Call folders and audio files.

The recording itself comes from LiveKit's built-in session recorder (RecorderIO): a stereo
OGG/Opus file with the clinic on one channel and the bot on the other, on one shared
timeline. Here we copy it into calls/<id>/ and make an MP3 copy with PyAV (no ffmpeg needed).
"""

import re
import shutil
from pathlib import Path

import av

from src.config import CALLS_DIR

MP3_RATE = 44100
MP3_BITRATE = 96_000


def next_call_id(scenario: str) -> str:
    """Sequential, sortable folder names: 001-hello-world, 002-..., skipping _iterations."""
    CALLS_DIR.mkdir(exist_ok=True)
    numbers = [
        int(m.group(1))
        for p in CALLS_DIR.iterdir()
        if (m := re.match(r"(\d{3})-", p.name))
    ]
    slug = re.sub(r"[^a-z0-9-]+", "-", scenario.lower()).strip("-")
    return f"{max(numbers, default=0) + 1:03d}-{slug}"


def save_audio(source_ogg: Path, call_dir: Path) -> None:
    call_dir.mkdir(parents=True, exist_ok=True)
    ogg = call_dir / "recording.ogg"
    shutil.copyfile(source_ogg, ogg)
    ogg_to_mp3(ogg, call_dir / "recording.mp3")


def ogg_to_mp3(src: Path, dest: Path) -> None:
    with av.open(str(src)) as inp, av.open(str(dest), "w", format="mp3") as out:
        stream = out.add_stream("libmp3lame", rate=MP3_RATE, layout="stereo")
        stream.bit_rate = MP3_BITRATE
        # MP3 frames have a fixed size; the resampler re-chunks the Opus frames to match.
        resampler = av.AudioResampler(
            format="fltp",
            layout="stereo",
            rate=MP3_RATE,
            frame_size=stream.codec_context.frame_size,
        )

        def encode(frame):
            for resampled in resampler.resample(frame):
                for packet in stream.encode(resampled):
                    out.mux(packet)

        for frame in inp.decode(audio=0):
            encode(frame)
        encode(None)  # flush resampler
        for packet in stream.encode(None):  # flush encoder
            out.mux(packet)
