# Bug Report: Clinic Phone Agent

> **Draft.** Nothing below is verified yet. A finding moves to **Verified** only after (1) a human
> listens to the audio at the timestamp and confirms our STT heard it correctly, and (2) it reproduces
> on at least one more call. Until then entries are **Leads**.

## Summary

| ID | Finding | Severity (proposed) | Status | Reproduced | First seen |
|---|---|---|---|---|---|
| L-01 | Existing patient not found; agent creates a new "demo profile" instead | High | Lead | 1/1 | 002 @ 00:40.6 |
| L-02 | Routine checkup booked at an orthopedics practice, with imaging instructions | Medium | Lead | 1/1 | 002 @ 02:05.0 |
| L-03 | Profile creation required before any scheduling help | Low | Lead (may be by design) | 1/1 | 002 @ 00:08.8 |

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
- **To confirm:** check whether the demo practice is orthopedics-only (portal); if so this becomes
  "agent doesn't tell the caller what kind of practice this is" (Low). `simple-scheduling` repeats the request.

### L-03: Profile creation required before any help
- **Proposed severity:** Low · **Category:** conversational flow · **Reproduced:** 1/1 so far
- **Call:** `calls/002-hello-world/transcript.txt` at 00:08.8
- **What happened:** the agent's first substantive turn asked to "create a demo patient profile" before
  addressing the request.
- **Note:** likely intended for this demo line. Kept only so we can drop it explicitly if every call shows
  it as a fixed flow.

---

## What the agent did well (call 002)

- Resolved "next Tuesday" to Tuesday October 20th correctly (call made Wednesday October 14th).
- Confirmed the request back ("general office visit for a routine checkup, next week, morning") before searching.
- Read back the booked date, time and provider, and listed what to bring.

## Measured agent metrics

| Metric | Value | Calls |
|---|---|---|
| Clinic response latency, end of bot speech → clinic speech (median / p90) | 3.22 s / 3.58 s | 1 |
| Talk-over by the clinic agent | 0 | 1 |
| Goal met | 1/1 | 1 |
