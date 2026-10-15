"""Shared settings loaded from .env."""

import os
from pathlib import Path

import certifi
from dotenv import load_dotenv

load_dotenv()

# python.org builds of Python on macOS ship without CA certificates, which makes every
# HTTPS/WSS call (LiveKit, Deepgram, Cartesia) fail verification. Fall back to certifi.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

AGENT_NAME = "patient-caller"

# One folder per call: recording.ogg/.mp3, transcript.txt/.json, metrics.json
CALLS_DIR = Path(__file__).resolve().parents[1] / "calls"

# Live conversation model. Override per run with `src.run --llm ...` (sets LIVE_LLM_MODEL).
LIVE_LLM_MODEL = os.getenv("LIVE_LLM_MODEL", "claude-haiku-5-5")

# How to turn thinking off for each model: a patient's reply doesn't need reasoning, and thinking
# roughly doubles time-to-first-token (scripts/bench_llm.py: Haiku 1.12 s -> 0.65 s median).
# Haiku 5.5 accepts "disabled"; Sonnet 5.5 rejects it and offers "between_tools" instead.
THINKING_OFF = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}
STT_MODEL = "nova-3"
TTS_MODEL = "sonic-3"

# Hard cap enforced by LiveKit SIP itself, independent of anything the bot does. Above the longest
# scenario limit (5 min): the clinic's lookups are slow, and 4-minute limits cut half the calls short.
MAX_CALL_SECONDS = 6 * 60


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing {name} in .env (see .env.example)")
    return value
