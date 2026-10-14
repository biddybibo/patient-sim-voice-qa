"""Turn the session's chat history into a timestamped, speaker-labeled transcript.

Timestamps are seconds from the first sample of the recording, so a line at [01:23.4]
is at 01:23.4 in recording.mp3. The clinic side is our STT's transcription of their
audio; check the recording before treating a wording issue as their bug.
"""

import json
from pathlib import Path

CLINIC = "CLINIC_AGENT"
BOT = "PATIENT_BOT"
TOOL = "TOOL"

# Per-turn latency fields LiveKit attaches to each message (seconds).
LATENCY_KEYS = (
    "transcription_delay",
    "end_of_turn_delay",
    "llm_node_ttft",
    "tts_node_ttfb",
    "e2e_latency",
)


def build_turns(chat_items, t0: float) -> list[dict]:
    turns = []
    for item in chat_items:
        if item.type == "message" and item.role in ("user", "assistant"):
            text = item.text_content
            if not text:
                continue
            m = item.metrics or {}
            start = m.get("started_speaking_at", item.created_at)
            stop = m.get("stopped_speaking_at")
            turns.append(
                {
                    "speaker": CLINIC if item.role == "user" else BOT,
                    "t_start": round(start - t0, 2),
                    "t_end": round(stop - t0, 2) if stop else None,
                    "text": text,
                    "interrupted": item.interrupted,
                    "stt_confidence": item.transcript_confidence,
                    "latency": {k: round(m[k], 3) for k in LATENCY_KEYS if k in m},
                }
            )
        elif item.type == "function_call":
            turns.append(
                {
                    "speaker": TOOL,
                    "t_start": round(item.created_at - t0, 2),
                    "t_end": None,
                    "text": f"{item.name}({item.arguments})",
                }
            )
    turns.sort(key=lambda t: t["t_start"])
    return turns


def timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def format_txt(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        if t["speaker"] == TOOL:
            lines.append(f"[{timestamp(t['t_start'])}] -- {t['text']}")
        else:
            cut = " [cut off]" if t.get("interrupted") else ""
            lines.append(f"[{timestamp(t['t_start'])}] {t['speaker']}: {t['text']}{cut}")
    return "\n".join(lines) + "\n"


def write_transcript(turns: list[dict], call_dir: Path, header: dict) -> None:
    call_dir.mkdir(parents=True, exist_ok=True)
    (call_dir / "transcript.json").write_text(
        json.dumps({**header, "turns": turns}, indent=2)
    )
    (call_dir / "transcript.txt").write_text(format_txt(turns))
