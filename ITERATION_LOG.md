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
| 007 | reschedule / hesitant elderly | + fixes 9–14 | 3:09 | goal_met | 2.13 s | 1.52 s | 1.40 s | 0.00 s |
| 008 | privacy-shared-phone / rushed | + fixes 9–14 | 2:33 | goal_met | 1.67 s | 0.96 s | 0.85 s | 0.00 s |
| 009 | simple-scheduling / rushed | Sonnet 5.5 (A/B vs 003) | 2:01 | goal_met | 2.16 s | 1.07 s | 1.27 s | 0.00 s |
| 010–028 | 18-scenario batch (see `scripts/recompute_metrics.py`) | + fixes 9–14, Haiku | 1:28–4:23 | 12 goal_met · 8 timeout · 6 other | 1.33–3.04 s | 0.71–1.79 s (median 1.07) | 0.70–1.45 s | ≤ 1.16 s |

"Response" includes the bot deliberately waiting up to 3 s through the clinic's "let me check…" pauses;
"after end-of-turn decided" isolates the bot's own speed (LLM + TTS). Target for the latter: < 0.8 s.

---

## Open

### 7. Bot responsiveness
- **Evidence:** table above. Once the bot decides the clinic is done it takes 0.70–1.26 s to start speaking
  (median per call); Claude Haiku 5.5's time-to-first-token (0.71–0.90 s, thinking off, effort `low`) is most of it.
- **Offline benchmark** (`scripts/bench_llm.py`, results in `calls/_bench/llm_ttft.json`): replays the real
  persona prompt + the bot's tool definitions + the first turns of call 003, ending on the clinic's slot offer,
  8 trials per config.

  | Live LLM config | TTFT median | TTFT p90 | Full reply median |
  |---|---|---|---|
  | **Haiku 5.5, thinking off, effort `low` (chosen)** | **0.71 s** | **0.77 s** | 0.82 s |
  | Haiku 5.5, default adaptive thinking | 1.43 s | 1.68 s | 1.56 s |
  | Sonnet 5.5, `thinking: between_tools`, effort `low` | 1.15 s | 1.20 s | 1.32 s |

- **A misleading first result, caught by a live call:** the first version of the benchmark sent no tool
  definitions, and there Sonnet 5.5 looked *faster* than Haiku (0.55 s vs 0.65 s TTFT). A real Sonnet call (009,
  same scenario and persona as 003) measured 1.27 s. Re-running with and without the bot's three tools showed why:
  tools take Sonnet from 0.57 s to 1.24 s, and leave Haiku unchanged (0.73–0.85 s). The benchmark now always
  includes the tools, so it measures what production sends.
- **Decision:** keep Claude Haiku 5.5 with thinking off and effort `low` as the live model: fastest in the real
  configuration and ~20x cheaper. Thinking off is worth ~0.7 s median per reply. `src.run --llm claude-sonnet-5-5`
  stays available (each model gets its own thinking-off setting from `config.THINKING_OFF`).
- **Remaining gap to the 0.8 s target:** 0.70–1.52 s per call after end-of-turn. TTFT is the bulk; TTS first
  audio is ~0.15 s. Next lever would be shortening the prompt or trying a non-Anthropic fast model, not pursued.

### 11. Ground-truth drift: the bot argued with correct clinic records
- **Evidence:** `calls/004-simple-scheduling/transcript.txt` at 01:13.8. The clinic said Bob already had an
  appointment (true: booked in call 003); `facts.yaml` still listed none, so the bot insisted it had nothing
  booked, the clinic cancelled the real appointment at the bot's request, and the call ran out of time.
- **Fix:** `facts.yaml` now records each account's booking history; prompt rule "If they mention an appointment
  or detail you don't remember, don't argue; ask them what they have on file."
- **Status:** fixed, awaiting a call to confirm. Lesson: scenarios that change state (book/cancel/reschedule) need
  `facts.yaml` updated after every call, or a run order that accounts for them.

### Fixes 9–14: verification so far (calls 007, 008)
- **9 (fillers):** verified. 0.00 s of overlap in both calls; the hold rule fired in 008 ("clinic is checking
  something; staying quiet"). Issue 6's change 2 also fired 6 times, each when the clinic split a turn in two
  ("I see you're calling from the number we have on file." / "Am I speaking with Jane?"): the bot dropped its
  reply to the first half and answered the question instead.
- **10 (speech after hang-up):** verified. One goodbye per call, nothing after `end_call`.
- **11 (arguing about records):** verified. In 008 the bot said it had no appointments it knew of and asked the
  agent to check, without arguing.
- **12 (transfer):** verified in 013: `wait_on_transfer` kept the bot on the line; the transfer reached the test
  line's goodbye message, and the bot then ended the call.
- **13 (time-limit cut-off):** partly. The wrap-up now waits for a gap, but see issue 16.
- **14 (port clash):** no recurrence.

### Fixed in code (details)
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

### 15. A call's bot session stayed alive 84 minutes after the phone line was gone (batch run, call 025)
- **Evidence:** `calls/_iterations/025-multi-intent-stuck-84min/` (MP3 dropped, it was 60 MB; OGG and transcript kept).
  The bot's last line is at 05:05.7, past its 4-minute limit; the batch's next call started 89 minutes late.
  Twilio shows no call longer than 257 s, so the phone leg ended normally; the agent sat alone in the LiveKit room.
- **Causes (two layers failed):** LiveKit only closes the session for some participant-disconnect reasons
  (`DEFAULT_CLOSE_ON_DISCONNECT_REASONS`), and our time-limit task didn't hang up (its `delete_room` came after
  steps that could fail). The worker logs were lost because `run_batch.py` kept only the last line of output.
- **Fix, three independent layers:** (1) the bot hangs up whenever the phone participant leaves, for any reason;
  (2) the time limit hangs up in a `finally` block; (3) an external watchdog in `run.py`, outside the bot process,
  deletes the room 90 s after the scenario limit. Also: `run.py` force-kills a worker that won't stop, and
  `run_batch.py` saves every run's full log to `calls/_logs/`.
- **Cost:** a few dollars of LiveKit and STT minutes; no phone time.

### 16. Time-limit wrap-up ignored: the bot keeps asking questions until it's cut off
- **Evidence:** 020 @ 03:08.7 and 023 @ 04:16.8: instead of "I have to go, goodbye" the bot repeats its question and
  is cut off mid-sentence ("[cut off]"). 8 of 26 calls ended on the time limit (019–023 mostly: the clinic's long
  lookups and contradictions used up the 4 minutes).
- **Status:** open. Fix: make the wrap-up a fixed spoken line (`session.say`) rather than an LLM instruction, and
  raise limits for scenarios where the clinic is slow. These calls aren't used as showcase calls.

### 17. Bot attributed its own appointment to the person it was calling about (fixed: see 19A)
- **Evidence:** 011 @ 00:59.8: calling about his wife Jane, the bot offered Bob's own appointment ("Tuesday,
  October 20th at 10 AM with Dr. Doogie Howser") as hers.
- **Cause:** the persona prompt lists the caller's appointments; nothing says they're his, not hers.
- **Status:** open (minor). Fix: label `known_appointments` as "your own" in the prompt for on-behalf scenarios.

### 18. Unnatural closings: "bye" as the answer to "anything else?", then a second goodbye
- **Evidence:** 008 @ 02:19.9 "Okay, thanks. That's all I needed. Bye." in reply to "Would you like directions or any
  other details?", then "Thanks, you too. Bye." after the clinic's farewell (spotted by listening).
- **Cause:** our prompt rule "when your goal is done, say thanks and goodbye, then call end_call".
- **Fix:** closing in steps, the way people do it: answer "anything else?" ("No, that's everything, thank you so
  much"), let them say goodbye, then one short goodbye + `end_call`. If the clinic goes quiet after "that's
  everything", the silence check says goodbye instead of "are you still there?".

### 19. Bot-side review of all calls: incoherent responses, five root causes
A second reviewer pass (`src/analysis/bot_review.py`, Claude Opus 5.5) judged **our** bot's turns in all 27 calls for
ignored questions, contradictions, invented details, repetition, broken character and unnatural closings
(`calls/_analysis/bot_review.json`). 15 calls had a noticeable issue; after discarding reviewer false positives
(007 was judged against a goal edited after the call; 008's contradictions are the scenario's deliberate probe),
they trace to five causes:
- **A. Recites appointments it called to ask about** (012 @ 01:06.7, 013 @ 01:00.5) or brings up unrelated ones
  while booking (018, 021, 022, 029). Cause: every prompt listed the caller's appointments. **Fix:** only scenarios
  about an existing appointment (`knows_appointments: true`: reschedule, cancel, multi-intent, insurance) get them;
  others are told "you don't remember the details; ask what they have on file". This also fixes issue 17.
- **B. Time-limit goodbye lands right after the bot's own question** (004, 018, 029). **Fix:** at the limit, if the
  bot spoke last it waits up to 25 s for the clinic's answer; goodbye lines now start by acknowledging it
  ("Okay, thanks. Sorry, I have to hop off…").
- **C. Talks to a dead line / comments on the system** (027 @ 02:33.9 "Is this the right line for that?" after a
  transfer reached a recorded goodbye, spotted by listening; 026, 020). **Fix:** prompt and `wait_on_transfer`
  say "if it's a recording or they say goodbye, just say okay, bye"; never comment on the line or tests; plus
  issue 15's hang-up-on-disconnect.
- **D. Reacts to transcription spelling** (023 @ 01:56.4 "you said Howser, earlier Hauser"). **Fix:** "you hear them,
  you don't read them: names that sound alike are the same name."
- **E. Scenario script leaking into speech** (021: read out the "November 31st" fallback and then called it not a real
  date). **Fix:** scenario rewritten so the caller believes the dates are normal.

### 22. Interruptions were untested: new barge-in scenario
- **Gap found** by re-reading the brief, which lists "interruptions" among the edge cases. Our bot deliberately never
  talks over the clinic, so the agent's barge-in handling had never been exercised.
- **Built:** scenario flag `barge_in`. Once the opening is over and the clinic has talked for more than 4 s in one turn, the
  bot cuts in once with a short, goal-driven line ("Sorry to jump in…"). The "drop a queued reply" rule is suspended
  for it, and the cut-in time is written to `transcript.json` (`barge_in_at_s`).
- **Result:** calls 052 and 053 cut in at 58.0 s and 59.2 s, after 4.2 s and 4.0 s of clinic speech. The agent stopped
  within 0.42–0.54 s of overlap (stereo audio) and continued in context. Positive finding, plus BUG-11.

### 21. Re-recording round 1 (calls 032–044): what the bot review still found
After fixes A–E, re-recorded calls no longer recite appointments or talk to recordings. The bot-side review still
flagged three patterns:
- **F. Time-limit goodbye didn't fit the moment** (040, 041, 042, 044): a fixed line "thanked" answers that never came,
  or promised "I'll call back" right after a booking was confirmed. Underneath: the clinic's lookups are slow, and half
  the calls hit the 4-minute limit. **Fix:** limits raised to 5 min (SIP backstop 6 min); the final goodbye is composed
  by the LLM from context (one sentence, no questions), with a guaranteed hang-up after playout and the persona's
  fixed line as fallback.
- **G. Probes forced in after they stopped making sense** (036: "who is my appointment with?" while booking; 042:
  "Dr. Okonkwo works at your main office?" after being told twice he doesn't exist). **Fix:** "skip any that no
  longer make sense"; the scheduling probe now waits for a slot offer.
- **H. Invented beliefs** (039 "I thought your office was in Palo Alto", inferred from the pharmacy address; 041 "I
  thought I already had something set up"). **Fix:** "Don't invent facts about yourself or the practice beyond what's
  written here."

### 20. Worker-log scan: the hang-up-on-disconnect handler threw an error
- **Evidence:** scanning every saved worker log (`calls/_logs/`) for errors: in call 032, "error running user callback
  for participant_disconnected … TypeError: a coroutine was expected, got <Task …>". `ctx.delete_room()` already
  schedules its own task; wrapping it in `asyncio.create_task` raised after the deletion was scheduled, so the call
  still ended, but one of the three hang-up safety layers was crashing.
- Also: "event loop blocked for 1086ms" in `save_call` (audio analysis + MP3 conversion on the agent's loop).
- **Fix:** call `ctx.delete_room()` directly; run the audio work with `asyncio.to_thread`.
- The remaining log errors are benign shutdown noise (LiveKit FFI handle cleanup at process exit, data channels
  closing when the room is deleted).

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
