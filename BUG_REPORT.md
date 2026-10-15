# Bug Report: Clinic Phone Agent

> **Draft.** Nothing below is verified yet. A finding moves to **Verified** only after (1) a human
> listens to the audio at the timestamp and confirms our STT heard it correctly, and (2) it reproduces
> on at least one more call. Until then entries are **Leads**.

## Summary

| ID | Finding | Severity (proposed) | Status | Reproduced | First seen |
|---|---|---|---|---|---|
| L-01 | Existing patient not found; agent creates a new "demo profile" instead | High | Lead | 1/1 | 002 @ 00:40.6 |
| L-02 | Routine checkup / annual physical booked at an orthopedics practice | Medium | Lead | 2/2 | 002 @ 02:05.0, 003 @ 01:22.2 |
| L-03 | Profile creation required before any scheduling help | Low | Lead (may be by design) | 1/2 | 002 @ 00:08.8 |
| L-04 | Provider's name said differently each time it's mentioned | Medium | Lead (needs audio check) | 1/1 | 003 @ 00:57.9–02:23.7 |
| L-05 | 8.7 s of silence after the caller gives their date of birth | Low | Lead | 1/1 | 003 @ 00:32.2 |

Severity scale: **Critical** patient safety/privacy harm · **High** wrong action or false confirmation ·
**Medium** wrong or inconsistent information · **Low** conversational quality.

**How to read timestamps:** `[mm:ss.s]` in `calls/<id>/transcript.txt` is the same position in
`calls/<id>/recording.mp3` (both are timed from the recording's first sample). The clinic agent is on the
**left** channel. Clinic-side text is our speech-to-text; check the audio before quoting exact wording
(e.g. the doctor's name is transcribed three different ways in call 002).

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
- **Proposed severity:** Medium · **Category:** wrong/inconsistent information · **Reproduced:** 1/1
- **Call:** `calls/003-simple-scheduling/transcript.txt` at 00:57.9, 01:22.2, 01:57.3, 02:12.3, 02:23.7
- **What happened (as transcribed):** "doctor Judy Hauser" → "Dugi Houser" → "Doo Dehauser" → "Duvi Hauser",
  then spelled "h o w s e r". The caller noticed and asked which it was (02:03.7).
- **Why it matters:** the patient can't tell which provider they're booked with.
- **Caveat:** this may be our STT, not the agent. **Must listen** to the left channel at those timestamps.
  If the agent's TTS pronounces the name inconsistently, it's an agent bug; if the audio is consistent, reject.

### L-05: Long silence after date of birth
- **Proposed severity:** Low · **Category:** conversational quality · **Reproduced:** 1/1
- **Call:** `calls/003-simple-scheduling/transcript.txt`: caller finished DOB at 00:32.2, agent spoke at 00:40.9 (8.7 s).
- **Why it matters:** on a real call 8–9 s of dead air makes callers think they've been dropped.

---

## What the agent did well (calls 002–003)

- Resolved "next Tuesday" to Tuesday October 20th correctly (call made Wednesday October 14th).
- Confirmed the request back ("general office visit for a routine checkup, next week, morning") before searching.
- Read back the booked date, time and provider, and listed what to bring.
- Spelled the provider's last name when asked (003 @ 02:23.7).

## Measured agent metrics

| Metric | Value | Calls |
|---|---|---|
| Clinic response latency, end of bot speech → clinic speech (median / p90) | 002: 3.22 / 3.58 s · 003: 2.88 / 6.81 s | 2 |
| Talk-over by the clinic agent | 0 | 2 |
| Goal met | 2/2 | 2 |
