# Iteration Log

What went wrong with **our** bot and tooling, how we found it, and what changed.
Bugs in the clinic agent under test are in [BUG_REPORT.md](BUG_REPORT.md).

Each entry: symptom → evidence → cause → fix → before/after. Commits are in `git log`.

## Baseline (call 002, first full call)

| Metric (from `calls/002-hello-world/metrics.json`) | Value | Target |
|---|---|---|
| Bot response latency, end of clinic speech → bot audio (median / p90) | 1.91 s / 2.24 s | < 1.2 s |
| …of which end-of-turn wait (median) | 0.60 s | |
| …of which Claude time-to-first-token (median) | 0.90 s | |
| …of which TTS time-to-first-audio (median) | 0.17 s | |
| Talk-over events (bot over clinic) | 1 (4.5 s, at the greeting) | 0 |
| Call outcome | Booked, hung up itself (`goal_met`), 2:50 | |

Call 003 (`simple-scheduling`, rushed persona, Phase 3 prompt): median response 1.71 s (p90 1.91 s),
Claude TTFT 0.88 s, 1 talk-over (0.9 s, issue 9), booked and hung up itself, 2:51.

---

## Open

### 6. Bot talks over the clinic agent's greeting
- **Symptom:** the bot started its request while the clinic agent was still greeting.
- **Evidence:** `calls/002-hello-world/transcript.txt` at 00:10.4; `metrics.json` → `talk_over: 1 event, 4.46 s`;
  audible in `recording.mp3` at ~00:08–00:15 (clinic on the left channel, bot on the right).
  The clinic's words during the overlap are missing from the transcript (they are in the recording),
  because uninterruptible bot speech discards incoming audio before STT.
- **Suspected cause:** the 0.6 s endpointing delay treated the pause after the recorded
  disclaimer ("This call may be recorded…", ends 00:08.2) as the end of the clinic's turn.
- **Reproduction:** 1/2. Did **not** happen in call 003, where the clinic delivered disclaimer and greeting
  as one continuous turn (04.9–16.7) with no pause; the bot waited and spoke at 18.6.
- **Status:** left unfixed on purpose to debug on camera (Loom 2). Fix + after-metrics go here.

### 7. Bot response latency above target
- **Evidence:** baseline table above. The largest component is Claude's time-to-first-token (0.9 s median),
  even with thinking disabled and effort `low` on Claude Haiku 5.5.
- **Status:** Phase 4. Candidates: compare Sonnet 5.5 with thinking off, check prompt caching
  actually hits, tune endpointing per persona.

### 8. Bot broke character
- **Symptom:** when the clinic agent offered to create a "demo patient profile", the bot replied
  "I'm not really looking to make a demo profile, I'm an actual patient."
- **Evidence:** `calls/002-hello-world/transcript.txt` at 00:27.1.
- **Fix (prompt, commit `1314aa7`):** persona prompt now says "If they offer to set up or look up a
  profile, go along with it."
- **Status:** no recurrence in call 003.

### 9. Bot says "Sure, thanks" over the clinic's answer after "let me check"
- **Evidence:** `calls/003-simple-scheduling/transcript.txt` at 00:58.5; `metrics.json` → talk-over 0.93 s.
  Clinic said "Let me check…" (ends 57.2), started the slot offer at 57.9; bot's filler landed on top of it.
- **Cause:** our own prompt rule: "If they say they're checking something, just wait or say 'sure'."
- **Status:** Phase 4: change to "stay silent while they check".

### 10. Bot says goodbye twice
- **Evidence:** `calls/003-simple-scheduling/transcript.txt` at 02:30.1 (goodbye) and again at 02:47.0 after
  `end_call` ran at 02:46.8.
- **Status:** Phase 4 (low priority): suppress speech after `end_call`.

---

## Fixed

### 1. Calls rejected by Twilio: `403 No International Permission`
- **Evidence:** attempt archived in `calls/_iterations/001-hello-world-failed-sip-403/` (no conversation).
- **Cause:** the new Twilio account had voice dialing to United States/Canada disabled
  (`low_risk_numbers_enabled: false`), despite the destination being domestic.
- **Fix:** enabled standard US/Canada dialing via Twilio's Dialing Permissions API; high-risk and
  premium-rate prefixes left disabled.
- **After:** call 002 connected in 5.0 s.

### 2. Dialer crashed before placing the call
- **Cause:** `livekit.api` has no `Duration`; `max_call_duration` takes a protobuf `Duration`.
- **Fix:** `google.protobuf.duration_pb2.Duration` (commit `964dd8d`).

### 3. Failed dial left a room behind; the next run's worker picked up the stale job
- **Evidence:** second attempt's worker joined the first attempt's room (`call-001-…`), which had no phone line.
- **Fix:** `dial.py` deletes the room when the SIP call fails; `save_call` exits cleanly when no
  session ever started (commit `964dd8d`).

### 4. Every HTTPS/WSS call failed certificate verification
- **Evidence:** preflight: `SSLCertVerificationError … unable to get local issuer certificate` for Deepgram and Cartesia.
- **Cause:** python.org builds of Python on macOS ship without CA certificates; aiohttp builds its
  SSL context at import time.
- **Fix:** `src/config.py` sets `SSL_CERT_FILE` to certifi's bundle and is imported before aiohttp in
  every entry point (commit `37d233c`). Works on a reviewer's Mac too.

### 5. Metrics overstated talk-over and silence
- **Evidence:** call 002 first reported talk-over 15.26 s and longest silence 12.18 s; the audio shows
  4.5 s of overlap and no 12 s silence.
- **Cause:** overlap/silence compared each turn only with the previous one, but the clinic's long turn
  (08.8–25.7) contained the bot's whole turn (10.4–14.9).
- **Fix:** compare against whichever turn is still on air; regression test built from call 002's timings
  (`tests/test_call_outputs.py::test_bot_talking_inside_a_long_clinic_turn`, commit `964dd8d`).
- **After:** talk-over 4.46 s, longest silence 4.22 s.

## Design changes after early results

- **Recording:** switched from Room Composite Egress → S3 to LiveKit Agents' built-in session recorder.
  Same stereo OGG (clinic L / bot R), no storage account or egress cost, and transcript timestamps align
  with the audio by construction. Verified on call 002: transcript's first clinic line at 00:05.0 matches
  the left channel's audio onset (RMS 0.17 vs 0.0 on the bot channel).
- **No `mark_progress` tool:** each tool call adds a Claude round trip (~1 s of dead air). Goal completion
  is judged offline from the transcript instead.
