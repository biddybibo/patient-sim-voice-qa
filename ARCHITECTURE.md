# Architecture

**How a call works.**
1. `src/run.py` starts a LiveKit Agents worker. `src/dial.py` then creates a LiveKit room, dispatches the patient
   agent into it, and dials the clinic over a LiveKit outbound SIP trunk connected to a Twilio Elastic SIP trunk.
   It calls from one number, and `src/guard.py` refuses any destination other than the test line.
2. The agent (`src/agent.py`) runs in LiveKit's **pipeline mode**: Deepgram Nova-3 transcribes the clinic, LiveKit's
   turn detector and Silero VAD decide when the clinic has finished speaking, Claude Haiku 5.5 writes the patient's
   reply, and Cartesia Sonic-3 speaks it in the persona's voice. Cartesia starts on the first sentence while Claude
   is still writing.
3. Each call is driven by three YAML files that `src/persona.py` turns into a short system prompt:
   - a **scenario**: goal, a couple of probing questions, and the rubric the clinic is graded on, which the bot never sees
   - a **persona**: speaking style, voice, speed and emotion
   - **patient facts**: the two test accounts
4. Code, not the model, handles the things an LLM gets wrong on the phone:
   - when to stay silent ("let me check…", a split turn)
   - the opening greeting
   - hang-ups, transfers and keypad presses
   - the time limit, which ends with a fixed goodbye
   - a loop guard
   - three independent ways to hang up a stuck call
5. LiveKit's built-in recorder writes a stereo OGG (clinic left, bot right), which is converted to MP3 with PyAV.
   The transcript, timed from the recording's first sample, is written with `metrics.json` (latency, plus overlap
   measured from the audio itself).
6. Afterwards, Claude Opus 5.5 drafts findings for each call (`src/analysis/evaluate.py`), merges them across calls
   and checks the agent's facts for consistency (`report.py`), and separately reviews our own bot's turns
   (`bot_review.py`). A person listens at each timestamp before anything goes into `BUG_REPORT.md`.

**Why it's built this way.**
- **Conversation quality comes first,** so the design spends its effort on turn-taking and latency.
- **Turn detection: semantic, not silence-only.** A silence timer alone cuts the clinic off mid-thought. Even
  semantic detection answered the clinic's recorded disclaimer (call 002), so the first turn waits 1.5 s and every
  later turn 0.6 s (ITERATION_LOG #6).
- **Interruptions are off once the bot is speaking,** which prevents two bots talking over each other in a loop. A
  reply that hasn't started playing yet is dropped if the clinic keeps talking. The result is ≤ 1.5 s of
  simultaneous speech per call, median 0 s, measured from the audio.
- **The live model was chosen by measuring time to first token on our real prompt, with the bot's tools attached**
  (`scripts/bench_llm.py`):
  - Haiku 5.5 with thinking off: 0.71 s median
  - Haiku 5.5 with adaptive thinking: 1.43 s
  - Sonnet 5.5: 1.15 s
  - A first benchmark without tools had Sonnet ahead; a live Sonnet call (009) disproved it.
- **The remaining gap:** once it has decided the clinic is done, the bot needs a median ~1.1 s to start speaking,
  against a 0.8 s target. That is mostly the model's time to first token. We accepted it after measuring the
  alternatives.
- **Recording:** the built-in recorder replaced the planned egress-to-S3 setup. That's one account fewer, no egress
  cost, and timestamps that line up with the audio.
- **Telephony:** Twilio was chosen over Telnyx because LiveKit documents it well. Two surprises cost time: a
  compliance profile was required before buying a number, and US dialing was disabled by default.
- **Analysis:** a strong model is used only offline, where latency doesn't matter. It never decides alone: it
  produced false positives (findings it judged against facts that changed later, or a caller ID it didn't know was
  on file), which is why every reported bug is checked by hand.

## Stack and alternatives

| Layer | Choice | Why | Alternatives considered |
|---|---|---|---|
| Framework | LiveKit Agents 1.8 (Python), pipeline mode | Required; per-stage control and per-turn metrics (`llm_node_ttft`, `e2e_latency`) | — |
| Telephony | LiveKit outbound SIP → Twilio Elastic SIP, one caller ID | Native `create_sip_participant`, wait-until-answered | Telnyx (cheaper per minute); LiveKit phone numbers |
| STT | Deepgram Nova-3 streaming, keyterms for names/meds | Low latency on 8 kHz phone audio, interim results | Whisper (not streaming); Deepgram Flux (built-in end-of-turn) |
| Turn detection | LiveKit turn detector + Silero VAD; 1.5 s first turn, 0.6 s after | Waits through "let me check…"; doesn't answer the recorded disclaimer | Silence-only endpointing (cut the clinic off) |
| Live LLM | Claude Haiku 5.5, thinking off, effort `low` | Fastest measured TTFT with tools attached (0.71 s) | Sonnet 5.5 (1.15 s with tools); Haiku with thinking (1.43 s) |
| TTS | Cartesia Sonic-3, per-persona voice/speed/emotion | ~150 ms to first audio; expressive controls | ElevenLabs Flash (pricier); OpenAI TTS (slower) |
| Recording | LiveKit built-in recorder (stereo OGG) → MP3 via PyAV | Both sides, separable channels, aligned timestamps, no extra infra | Room Composite Egress → S3 (original plan) |
| Analysis | Claude Opus 5.5, structured output, offline | Quality over speed; schema-checked findings | Same small model (misses subtle issues) |
