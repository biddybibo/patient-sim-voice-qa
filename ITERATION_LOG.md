# Iteration Log

What went wrong with **our** bot and tooling, how we found it, and what changed.
Bugs in the clinic agent under test are in [BUG_REPORT.md](BUG_REPORT.md).

Each entry: symptom → evidence → cause → fix → before/after. Commits are in `git log`.
All numbers below come from `calls/<id>/metrics.json` (regenerate with `scripts/recompute_metrics.py`).

## Calls so far

| Call | Scenario / persona | Bot build | Length | End | Response, end of clinic speech → bot audio (median) | …after end-of-turn decided (median) | Claude TTFT (median) | Real overlap (audio) |
|---|---|---|---|---|---|---|---|---|
| 002 | hello-world (placeholder) | Phase 1 | 2:50 | goal_met | 1.91 s | 1.26 s | 0.90 s | 0.00 s |
| 003 | simple-scheduling / rushed | Phase 3 | 2:50 | goal_met | 1.71 s | 0.85 s | 0.88 s | 0.36 s |
| 004 | simple-scheduling / rushed | + greeting fix | 4:07 | timeout | 3.05 s | 1.06 s | 0.71 s | 0.36 s |
| 005 | routine-refill / non-native | + greeting fix | 1:28 | transferred | 1.60 s | 1.00 s | 0.79 s | 0.64 s |
| 006 | insurance-question / neutral | + greeting fix | 3:17 | timeout | 1.73 s | 0.70 s | 0.75 s | 1.52 s |

"Response" includes the bot deliberately waiting up to 3 s through the clinic's "let me check…" pauses;
"after end-of-turn decided" isolates the bot's own speed (LLM + TTS). Target for the latter: < 0.8 s.

---

## Open

### 7. Bot responsiveness
- **Evidence:** table above. Once the bot decides the clinic is done it takes 0.70–1.26 s to start speaking
  (median per call); Claude Haiku 5.5's time-to-first-token (0.71–0.90 s, thinking off, effort `low`) is most of it.
- **Next:** measured comparison with Sonnet 5.5; check whether the prompt cache is actually hit.

### 11. Ground-truth drift: the bot argued with correct clinic records
- **Evidence:** `calls/004-simple-scheduling/transcript.txt` at 01:13.8. The clinic said Bob already had an
  appointment (true: booked in call 003); `facts.yaml` still listed none, so the bot insisted it had nothing
  booked, the clinic cancelled the real appointment at the bot's request, and the call ran out of time.
- **Fix:** `facts.yaml` now records each account's booking history; prompt rule "If they mention an appointment
  or detail you don't remember, don't argue; ask them what they have on file."
- **Status:** fixed, awaiting a call to confirm. Lesson: scenarios that change state (book/cancel/reschedule) need
  `facts.yaml` updated after every call, or a run order that accounts for them.

### Fixed in code, awaiting a verification call
- **9. Fillers spoken over the clinic.** "Sure, thanks" (003 @ 00:58.5), "Sure, take your time" (006 @ 00:52.2,
  cut the clinic off mid-word: "Point…"), "No problem, I'll hold" (006 @ 01:09.0). Cause: our prompt rule
  "just wait or say 'sure'". **Fix:** a clinic turn that is only a hold phrase ("let me check", "one moment",
  "please hold", "I'm processing…", no question) now gets no reply at all (`is_hold_only` + `StopResponse`,
  tested on 8 real clinic lines); prompt changed to "say nothing; wait until they speak again".
- **10. Bot kept talking after hanging up.** 003: goodbye said twice; 006 @ 03:07.7: after its goodbye and
  `end_call`, the bot started a new question ("Sorry, I cut off there…"). **Fix:** once `end_call` runs, every
  later clinic turn is ignored (`StopResponse`), and the silence check and time limit no longer speak.
- **12. Hung up the moment it heard "Transferring you now".** 005 @ 01:21.4, so we never saw whether the transfer
  reaches anyone. **Fix:** new `wait_on_transfer` tool keeps the bot on the line, silent; hangs up only if nobody
  speaks within 30 s (`end_reason: transferred_no_answer`).
- **13. Time-limit goodbye cut the clinic off mid-sentence** (006 @ 03:01, clinic: "If doctor…"). **Fix:** the
  wrap-up waits up to 10 s for a gap in the clinic's speech. Limits raised from 3 to 4 min for scenarios where
  the clinic puts you on hold.
- **14. A worker still shutting down blocked the next call.** The batch's `reschedule` call failed:
  `address already in use` on port 8081 (another session's worker was running), reported misleadingly as
  "check .env credentials". **Fix:** worker binds any free port (`port=0`); `run.py` prints the worker's real error.

---

## Fixed and verified

### 6. Bot answered the recorded disclaimer instead of waiting for the greeting
- **Symptom (call 002):** the clinic plays "This call may be recorded…" (05.2–08.1), pauses 0.66 s, then greets.
  The bot's end-of-turn fired at 08.77 after the minimum 0.6 s wait (the turn detector judged the disclaimer
  a finished sentence), so it answered the disclaimer. Its reply was then uninterruptible, and while it was
  pending LiveKit discarded the incoming audio for STT, so the greeting never reached the transcript.
- **Diagnosis:** AI-assisted debugging session (recorded as Loom 2): H1 premature end-of-turn, H2 an
  uncancellable queued reply. Evidence: `end_of_turn_delay` = 0.601 s (the configured minimum) and the bot's
  `e2e_latency` 2.239 s subtracted from its start at 10.41 lands exactly on the disclaimer's end (08.17).
- **Fix (`src/agent.py`):**
  1. Greeting-only endpointing: 1.5 s until the bot first speaks, then 0.6 s (`session.update_options`).
     Rejected: 1.0 s on every turn, since it adds ~0.4 s to every reply.
  2. If the clinic starts speaking before any of the bot's queued reply has played, drop that reply.
     Once audio is playing, interruptions stay off (avoids bot-vs-bot overlap loops).
  3. From code review: change 2 could cancel `end_call` if the clinic says goodbye during the bot's goodbye;
     `end_call` now marks the call as ending and change 2 skips it.
- **After:** calls 004, 005, 006. The clinic paused after the disclaimer every time (0.64–0.66 s), and every
  time the bot waited for the full greeting and spoke first at 19.3–21.5 s (call 002: 10.6 s). The full
  greeting is in all three transcripts. Cost: the first reply only, about +0.9 s.

### 8. Bot broke character
- **Evidence:** 002 @ 00:27.1, "I'm not really looking to make a demo profile, I'm an actual patient."
- **Fix:** prompt rule "If they offer to set up or look up a profile, go along with it" (commit `1314aa7`).
- **After:** no recurrence in 003–006.

### 1. Calls rejected by Twilio: `403 No International Permission`
- **Evidence:** attempt archived in `calls/_iterations/001-hello-world-failed-sip-403/`.
- **Cause:** the new Twilio account had dialing to United States/Canada disabled.
- **Fix:** enabled standard US/Canada dialing via Twilio's Dialing Permissions API (high-risk and premium-rate
  prefixes left off). **After:** call 002 connected in 5.0 s.

### 2. Dialer crashed before placing the call
- **Fix:** `max_call_duration` takes a protobuf `Duration`, not `livekit.api.Duration` (commit `964dd8d`).

### 3. Failed dial left a room behind; the next run's worker picked up the stale job
- **Fix:** `dial.py` deletes the room when the SIP call fails; `save_call` exits cleanly when no session
  started (commit `964dd8d`).

### 4. Every HTTPS/WSS call failed certificate verification
- **Cause:** python.org builds of Python on macOS ship without CA certificates; aiohttp builds its SSL context
  at import. **Fix:** `src/config.py` points `SSL_CERT_FILE` at certifi and is imported first (commit `37d233c`).

### 5. Talk-over was measured from the transcript, which is wrong (two rounds)
- **Round 1 (commit `964dd8d`):** call 002 first reported 15.26 s of talk-over and a 12.18 s silence, because
  overlap was only compared with the previous turn. Fixed to compare against the turn still on air: 4.46 s.
- **Round 2:** measuring the stereo recording showed call 002 had **0.0 s** of simultaneous speech: the clinic's
  chat "turn" (08.8–25.7) contained 6.8 s of silence. Chat-turn spans aren't speech spans. **Fix:**
  `src/audio_analysis.py` measures overlap where both channels have sound at once; `metrics.json` now reports
  `talk_over_audio` (ground truth) and keeps `talk_over_transcript_estimate` only for comparison.

---

## Corrections

Two claims made earlier (in this log and in the Loom 2 debugging session) were wrong, found by measuring the audio:
- **"Call 003 is the control: the clinic didn't pause after the disclaimer."** It did, by the same 0.66 s; the
  transcript merged the two utterances into one line. The clinic pauses there on every call (002–006); whether the
  bot jumped in depended on the turn detector, not on the pause. The fix is unaffected, and every call tests it.
- **"4.5 s of talk-over in call 002."** That was the transcript-based estimate; there was no simultaneous speech.
  What the audio shows instead is consistent with the bot cutting the clinic's greeting short: the greeting ran
  ~2 s (vs ~8 s on every later call) and stopped as the bot started, then the clinic stayed silent for 6.8 s.

## Design changes after early results

- **Recording:** Room Composite Egress → S3 replaced by LiveKit Agents' built-in session recorder. Same stereo OGG
  (clinic L / bot R), no storage account or egress cost, and timestamps align with the audio by construction.
- **No `mark_progress` tool:** each tool call adds a Claude round trip (~1 s of dead air). Goal completion is
  judged offline from the transcript instead.
- **Audio-based metrics:** see issue 5.
