# Patient Simulator Voice QA Bot

A Python voice bot that phones a clinic's AI phone agent, plays a realistic patient,
and records both sides of the call for QA. Built on LiveKit Agents in pipeline mode
(Deepgram STT → Claude → Cartesia TTS).

> Status: Phase 2 (recording, transcripts, metrics). Scenarios and analysis come next — see [PLAN.md](PLAN.md).

**Caller number used for all calls:** `+16504560751`
**The only number this bot can dial:** `+18054398008` (enforced in [src/guard.py](src/guard.py))

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- [LiveKit CLI](https://github.com/livekit/livekit-cli) (`lk`) for trunk setup
- Accounts: LiveKit Cloud, Twilio (paid, not trial), Deepgram, Anthropic, Cartesia

## Setup

1. **Install dependencies**
   ```bash
   uv sync
   uv run python -m src.agent download-files   # Silero VAD model
   ```
2. **LiveKit Cloud**: create a project and copy `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
3. **Twilio** (account must be upgraded: trial accounts play a notice and can only call verified numbers):
   - Buy one US local number. This is the single caller ID for every call.
   - Elastic SIP Trunking → create a trunk → Termination: set a termination URI
     (e.g. `my-patient-bot.pstn.twilio.com`) and a credential list (username + password).
   - Attach the number to the trunk.
4. **LiveKit outbound trunk** pointing at Twilio:
   ```bash
   lk sip outbound create \
     --name twilio-out \
     --address my-patient-bot.pstn.twilio.com \
     --numbers +1XXXXXXXXXX \
     --auth-user <twilio-credential-user> --auth-pass <twilio-credential-pass>
   lk sip outbound list   # copy the trunk ID into SIP_OUTBOUND_TRUNK_ID
   ```
5. **Environment**: `cp .env.example .env` and fill it in.
6. **Check everything** (no calls placed, costs < $0.01):
   ```bash
   uv run python scripts/preflight.py
   ```

## Run

```bash
# One call, one command (starts the worker, dials, waits for hang-up, stops the worker)
uv run python -m src.run --scenario hello-world

# Talk to the bot locally through your mic/speakers, no phone call
uv run python -m src.agent console

# Tests
uv run pytest
```

Each call writes a folder under `calls/`, e.g. `calls/001-hello-world/`:

| File | Contents |
|---|---|
| `recording.ogg` / `recording.mp3` | Stereo: clinic agent on one channel, patient bot on the other |
| `transcript.txt` | `[mm:ss.s] SPEAKER: text`, timed from the start of the recording |
| `transcript.json` | Same turns with end times, STT confidence and per-turn latency |
| `metrics.json` | Bot latency (median/p90), clinic response latency, talk-over, longest silence, end reason |

## Environment variables

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit Cloud project |
| `SIP_OUTBOUND_TRUNK_ID` | LiveKit outbound trunk (from `lk sip outbound list`) |
| `CALLER_ID_E164` | The one caller number, E.164 |
| `TARGET_NUMBER` | Must be `+18054398008`; anything else is refused |
| `DEEPGRAM_API_KEY` | Speech-to-text |
| `ANTHROPIC_API_KEY` | Live patient LLM (Claude Haiku 5.5) and later offline analysis |
| `CARTESIA_API_KEY` | Text-to-speech |
| `PATIENT_NAME`, `PATIENT_DOB` | Phase 1 placeholder patient identity |
