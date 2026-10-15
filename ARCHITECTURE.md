# Architecture

**How it works.** `src/run.py` starts a LiveKit Agents worker. `src/dial.py` creates a room, dispatches the patient agent
into it, and dials the clinic through a LiveKit outbound SIP trunk on a Twilio Elastic SIP trunk. It always uses the same
caller ID, and `src/guard.py` refuses any number but the test line. The agent (`src/agent.py`) runs in pipeline mode:
Deepgram Nova-3 transcribes the clinic; LiveKit's semantic turn detector and Silero VAD decide when the clinic has
finished; Claude Haiku 5.5 (thinking off) writes the patient's reply; and Cartesia Sonic-3 speaks it in a per-persona
voice, starting on the first sentence. The prompt is built from three YAML files: a scenario (goal plus probes; its
grading rubric is never shown to the bot), a persona (style, voice, speed, emotion) and patient facts. Code, not the
model, handles what an LLM gets wrong on the phone: staying silent through "let me check…", waiting out the greeting,
transfers, keypad menus, the time limit's goodbye, and three independent ways to hang up a stuck call. LiveKit's
built-in recorder writes a stereo OGG (clinic left, bot right), converted to MP3. The transcript is timed from the
recording's first sample, and talk-over is measured from the audio. Afterwards, Claude Opus 5.5 drafts findings per
call, merges them across calls and reviews our own bot's turns; a person listens at every timestamp before a bug is
reported.

**Why these choices.** Conversation quality is the gate, so the effort went into turn-taking and latency, decided by
measurement:
- **Turn detection:** silence-only endpointing cut the clinic off mid-thought. Even semantic detection answered the
  clinic's recorded disclaimer, so the first turn now waits 1.5 s and later turns 0.6 s.
- **Interruptions:** off while the bot is speaking, to avoid two bots talking over each other in a loop. A reply that
  hasn't started yet is dropped if the clinic keeps talking. Result: at most 1.5 s of simultaneous speech per call.
- **Model choice:** benchmarked on the real prompt with the bot's tools attached. Haiku with thinking off reached its
  first token in 0.71 s, versus 1.43 s with thinking and 1.15 s for Sonnet 5.5. A first benchmark without tools had
  Sonnet ahead, and a live call proved it wrong. What's left (about 1.1 s median from end of turn to speech, against a
  0.8 s goal) is mostly model latency, which we accepted.
- **Recording:** Deepgram over Whisper for streaming on 8 kHz phone audio. The built-in recorder over egress to S3: one
  account fewer, no egress cost, timestamps aligned by construction.
- **Telephony:** Twilio because LiveKit documents it well; Telnyx would be cheaper per minute.
- **Analysis:** the strong model is used only offline, and never decides alone. It produced false positives, which is
  why every reported bug was checked by ear.

Details and evidence for each decision are in [ITERATION_LOG.md](ITERATION_LOG.md).

| Layer | Choice | Alternatives considered |
|---|---|---|
| Framework | LiveKit Agents 1.8 (Python), pipeline mode | — (required) |
| Telephony | LiveKit outbound SIP → Twilio Elastic SIP, one caller ID | Telnyx; LiveKit phone numbers |
| STT | Deepgram Nova-3 streaming, keyterms for names/meds | Whisper (not streaming); Deepgram Flux |
| Turn detection | LiveKit turn detector + Silero VAD; 1.5 s first turn, 0.6 s after | Silence-only endpointing |
| Live LLM | Claude Haiku 5.5, thinking off, effort `low` (0.71 s TTFT) | Sonnet 5.5 (1.15 s); Haiku with thinking (1.43 s) |
| TTS | Cartesia Sonic-3, per-persona voice/speed/emotion (~150 ms to first audio) | ElevenLabs Flash; OpenAI TTS |
| Recording | LiveKit built-in recorder (stereo OGG) → MP3 via PyAV | Room Composite Egress → S3 |
| Analysis | Claude Opus 5.5, structured output, offline, human-verified | Same small model |
