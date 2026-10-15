# Bug Report: Clinic Phone Agent

> **Draft.** Nothing below is verified yet. A finding moves to **Verified** only after (1) a human
> listens to the audio at the timestamp and confirms our STT heard it correctly, and (2) it reproduces
> on at least one more call. Until then entries are **Leads**.

## Summary

| ID | Finding | Severity (proposed) | Status | Reproduced | First seen |
|---|---|---|---|---|---|
| L-06 | Identifies the caller from caller ID with a yes/no question ("Am I speaking with Jane?"), although two patients share that number | High (privacy) | Lead: targeted test planned | 1/1 | 006 @ 00:31.9 |
| L-02 | Routine checkup / annual physical booked at an orthopedics practice | Medium | Lead | 2/2 | 002 @ 02:05.0, 003 @ 01:22.2 |
| L-04 | Provider's name said differently each time it's mentioned | Medium | Lead (needs audio check) | 2/2 | 003 @ 00:57.9, 004 @ 00:57.8 |
| L-07 | Caller asked about her **mother's** coverage; agent offered to look up her **brother's** | Medium | Lead (needs audio check) | 1/1 | 006 @ 01:40.8 |
| L-08 | Tells the caller to "call the office directly", though it is the office | Low | Lead | 1/1 | 006 @ 02:29.2 |
| L-05 | 8.7 s of silence after the caller gives their date of birth | Low | Lead | 1/1 | 003 @ 00:32.2 |
| L-03 | Profile creation required before any scheduling help | Low | Lead (may be by design) | 1/5 | 002 @ 00:08.8 |
| L-01 | Portal account not recognized on the phone line | Low (downgraded from High) | Lead, likely by design | 1/1 | 002 @ 00:40.6 |

Severity scale: **Critical** patient safety/privacy harm · **High** wrong action or false confirmation ·
**Medium** wrong or inconsistent information · **Low** conversational quality.

**How to read timestamps:** `[mm:ss.s]` in `calls/<id>/transcript.txt` is the same position in
`calls/<id>/recording.mp3` (both are timed from the recording's first sample). The clinic agent is on the
**left** channel. Clinic-side text is our speech-to-text; check the audio before quoting exact wording
(e.g. the doctor's name is transcribed differently on almost every mention, see L-04).

---

## Leads

### L-01: Existing patient not found; agent creates a new profile instead
- **Proposed severity:** High · **Category:** state / identity · **Reproduced:** 1/1 so far
- **Call:** `calls/002-hello-world/transcript.txt` at 00:27.1–00:40.6
- **What happened:** the caller gave the name and date of birth of an account that already exists in the
  practice's portal (Jane Doe, July 4th, 2000). The agent replied: "To schedule your checkup, I'll need to
  create a demo patient profile for you first… Your patient profile is set up."
- **Why it matters:** if the portal and the phone agent share records, this duplicates the patient, so
  bookings, medications and history split across two records.
- **Expected:** look up the existing record by name + DOB, then verify.
- **Caveat:** the portal accounts may not be connected to this test line at all. To confirm:
  `reschedule` (does the agent find the appointment booked in call 002?) and checking the portal for
  the October 20th booking.
- **Update (calls 004, 006):** the agent *does* find records it created itself: in 004 it found Bob's
  appointment booked in 003, and in 006 it recognized Jane from caller ID. So the phone line keeps its own
  records, separate from the web portal. **Downgraded to Low, likely by design** for this demo line; kept
  only until the portal check is done.

### L-02: Routine checkup booked at an orthopedics practice
- **Proposed severity:** Medium · **Category:** wrong information / routing · **Reproduced:** 1/1 so far
- **Call:** `calls/002-hello-world/transcript.txt` at 02:05.0
- **What happened:** the caller asked for "a routine checkup". The agent booked "a general office visit"
  with Dr. Lukowski "at Pivot Point Orthopedics" and said to bring "any imaging discs you have".
- **Why it matters:** a patient expecting a primary-care checkup arrives at a specialist practice.
- **Expected:** offer primary-care providers, or state the practice is orthopedics-only and ask
  whether that's what the patient wants.
- **Reproduced in call 003:** caller asked for "an annual physical"; agent confirmed "a general office visit
  for your annual physical" and booked it "At Pivot Point Orthopedics" (`calls/003-simple-scheduling/transcript.txt`
  at 01:22.2). The greeting in 003 names the practice ("Thanks for calling Pivot Point Orthopaedics"), so
  callers are told it's orthopedics, but the agent still books a primary-care visit type there without comment.
- **To confirm:** check whether the demo practice is orthopedics-only (portal); if so this becomes
  "agent books primary-care visits at a specialist practice without flagging it".

### L-03: Profile creation required before any help
- **Proposed severity:** Low · **Category:** conversational flow · **Reproduced:** 1/1 so far
- **Call:** `calls/002-hello-world/transcript.txt` at 00:08.8
- **What happened:** the agent's first substantive turn asked to "create a demo patient profile" before
  addressing the request.
- **Note:** likely intended for this demo line. Kept only so we can drop it explicitly if every call shows
  it as a fixed flow.

### L-04: Provider's name changes every time it's said
- **Proposed severity:** Medium · **Category:** wrong/inconsistent information · **Reproduced:** 2/2
- **Call:** `calls/003-simple-scheduling/transcript.txt` at 00:57.9, 01:22.2, 01:57.3, 02:12.3, 02:23.7
- **What happened (as transcribed):** "doctor Judy Hauser" → "Dugi Houser" → "Doo Dehauser" → "Duvi Hauser",
  then spelled "h o w s e r". The caller noticed and asked which it was (02:03.7).
- **Reproduced in call 004:** "doctor Duveehauser" (00:57.8) → "doctor Judy Hauser" (01:51.4) →
  "Doctor. Doobie Houser" (03:02.3). The likely real name is "Doogie Howser" (spelled H-O-W-S-E-R in 003).
- **Why it matters:** the patient can't tell which provider they're booked with.
- **Caveat:** this may be our STT, not the agent. **Must listen** to the left channel at those timestamps.
  If the agent's TTS pronounces the name inconsistently, it's an agent bug; if the audio is consistent, reject.

### L-05: Long silence after date of birth
- **Proposed severity:** Low · **Category:** conversational quality · **Reproduced:** 1/1
- **Call:** `calls/003-simple-scheduling/transcript.txt`: caller finished DOB at 00:32.2, agent spoke at 00:40.9 (8.7 s).
- **Why it matters:** on a real call 8–9 s of dead air makes callers think they've been dropped.

### L-06: Caller identified from caller ID with a leading yes/no question
- **Proposed severity:** High (privacy) · **Category:** identity verification · **Reproduced:** 1/1
- **Call:** `calls/006-insurance-question/transcript.txt` at 00:31.9
- **What happened:** "I see you're calling from the number we have on file. Am I speaking with Jane?"
  Our caller ID (+16504560751) is on file for **two** patients on this line: Jane's profile was created from
  it in call 002 and Bob booked from it in call 003. The agent picked one and asked a question that only needs
  a "yes". (The caller then volunteered her date of birth, so we can't yet tell whether it would have asked.)
- **Why it matters:** anyone with access to a family phone can answer "yes" and be treated as that patient.
  A number shared by two patients should trigger "who am I speaking with?" plus name and DOB verification.
- **Expected:** open-ended identification ("May I have your name and date of birth?"), verified before any
  appointment or insurance details are discussed.
- **To confirm:** targeted scenario: Bob calls, answers "yes" to "Am I speaking with Jane?", gives no DOB, and asks
  about "my" upcoming appointment. If the agent reveals Jane's appointment, this becomes Critical.

### L-07: "Mother" became "brother"
- **Proposed severity:** Medium · **Category:** misunderstanding · **Reproduced:** 1/1
- **Call:** `calls/006-insurance-question/transcript.txt`: caller at 01:40.9 says "I'm calling for my mother
  too"; agent at 01:40.8–02:15 says "If you have your brother's insurance details, I can help look that up too."
  The caller corrected it (02:15.7) and the agent apologized ("Sorry for the mix up", 02:29.2).
- **Caveat:** could be the agent's speech recognition or ours. Listen to both channels before reporting.

### L-08: Tells the caller to call the office, though it is the office
- **Proposed severity:** Low · **Category:** deflection · **Reproduced:** 1/1
- **Call:** `calls/006-insurance-question/transcript.txt` at 02:29.2: "For doctor Lukowski, I recommend calling
  the office directly or checking with your Medi Cal plan to confirm if they are in network."
- **Expected:** offer to have billing follow up, or transfer, instead of sending the caller to itself.

---

## What the agent did well (calls 002–006)

- Resolved "next Tuesday" to Tuesday October 20th correctly (call made Wednesday October 14th).
- Confirmed the request back ("general office visit for a routine checkup, next week, morning") before searching.
- Read back the booked date, time and provider, and listed what to bring.
- Spelled the provider's last name when asked (003 @ 02:23.7).
- Kept state across calls: found Bob's appointment from call 003 when he called again in 004 (00:57.8).
- On insurance, hedged instead of guessing ("coverage can depend on the specific plan details", 006 @ 01:40.8).
- Didn't refill a medication that isn't on the chart; offered a transfer to clinic support instead (005 @ 00:39.1).

## Measured agent metrics

| Metric | Value | Calls |
|---|---|---|
| Clinic response latency, end of bot speech → clinic speech (median per call) | 2.9–4.2 s | 002–006 |
| Longest clinic silence after a caller turn | 8.7 s (003 @ 00:32.2) | 002–006 |
| Goal met | 2 of 5 (002, 003). 004 and 006 hit our time limit; 005 was transferred | 002–006 |
