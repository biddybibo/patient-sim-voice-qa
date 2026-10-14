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

# Live conversation model: small and fast. Thinking is turned off in agent.py for latency.
LIVE_LLM_MODEL = "claude-haiku-5-5"
STT_MODEL = "nova-3"
TTS_MODEL = "sonic-3"

# Hard cap enforced by LiveKit SIP itself, independent of anything the bot does.
MAX_CALL_SECONDS = 5 * 60


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing {name} in .env (see .env.example)")
    return value
