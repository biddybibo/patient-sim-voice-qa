"""Per-call metrics.

Bot latency is what we control and tune (Phase 4 before/after evidence). Two views:
- response_e2e: end of clinic speech -> bot audio. Includes deliberately waiting through
  the clinic's "let me check..." pauses (end-of-turn up to 3 s), so it overstates slowness.
- response_after_turn_end: e2e minus the end-of-turn wait, i.e. how fast the bot answers
  once it has decided the clinic is done (LLM + TTS + overhead). This is responsiveness.

Talk-over comes from the stereo recording (src/audio_analysis.py) when available; the
transcript-based estimate is kept only for comparison because chat turns can span silences.
"""

from statistics import median

from src.transcript import BOT, CLINIC

OVERLAP_TOLERANCE = 0.2  # seconds of transcript overlap ignored as timing noise


def summarize(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]
    return {
        "n": len(ordered),
        "median": round(median(ordered), 3),
        "p90": round(p90, 3),
        "max": round(ordered[-1], 3),
    }


def compute(
    turns: list[dict], duration: float | None, end_reason: str, audio: dict | None = None
) -> dict:
    speech = [t for t in turns if t["speaker"] in (BOT, CLINIC)]

    def latency(key: str) -> list[float]:
        return [t["latency"][key] for t in speech if t["speaker"] == BOT and key in t["latency"]]

    # Pair each bot reply with the end-of-turn wait of the clinic turn it answered.
    after_turn_end = []
    last_eot = None
    for t in speech:
        if t["speaker"] == CLINIC:
            last_eot = t["latency"].get("end_of_turn_delay")
        elif "e2e_latency" in t["latency"] and last_eot is not None:
            after_turn_end.append(t["latency"]["e2e_latency"] - last_eot)
            last_eot = None

    clinic_response, overlaps, gaps = [], [], []
    # Compare each turn with whichever earlier turn ends last (the one still "on air"),
    # not just the previous one: a long clinic turn can contain a whole bot turn.
    on_air = None
    for prev, cur in zip([None] + speech, speech):
        if on_air is not None and on_air["t_end"] is not None:
            gap = cur["t_start"] - on_air["t_end"]
            if on_air["speaker"] != cur["speaker"] and gap < -OVERLAP_TOLERANCE:
                overlap_end = min(on_air["t_end"], cur["t_end"] or on_air["t_end"])
                overlaps.append({
                    "at": cur["t_start"],
                    "speaker": cur["speaker"],
                    "seconds": round(overlap_end - cur["t_start"], 2),
                })
            if gap > 0:
                gaps.append(gap)
        if prev and prev["speaker"] == BOT and cur["speaker"] == CLINIC and prev["t_end"] is not None:
            clinic_response.append(cur["t_start"] - prev["t_end"])
        if on_air is None or (cur["t_end"] or 0) > (on_air["t_end"] or 0):
            on_air = cur

    return {
        "duration_s": round(duration, 1) if duration else None,
        "end_reason": end_reason,
        "turns": {
            "clinic": sum(t["speaker"] == CLINIC for t in speech),
            "bot": sum(t["speaker"] == BOT for t in speech),
        },
        "bot_latency_s": {
            "response_e2e": summarize(latency("e2e_latency")),
            "response_after_turn_end": summarize(after_turn_end),
            "end_of_turn_delay": summarize(
                [t["latency"]["end_of_turn_delay"] for t in speech
                 if t["speaker"] == CLINIC and "end_of_turn_delay" in t["latency"]]
            ),
            "llm_ttft": summarize(latency("llm_node_ttft")),
            "tts_ttfb": summarize(latency("tts_node_ttfb")),
        },
        "clinic_response_latency_s": summarize(clinic_response),
        "talk_over_audio": audio["talk_over"] if audio else None,
        "opening_audio": audio["opening"] if audio else None,
        "talk_over_transcript_estimate": {"count": len(overlaps), "events": overlaps},
        "longest_silence_s": round(max(gaps), 2) if gaps else None,
        "bot_turns_cut_off": sum(t["speaker"] == BOT and t["interrupted"] for t in speech),
    }
