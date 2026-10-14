"""Per-call metrics computed from the transcript turns.

Bot latency is what we control and tune (Phase 4 before/after evidence).
Clinic latency, talk-over and silences describe the agent under test.
"""

from statistics import median

from src.transcript import BOT, CLINIC

OVERLAP_TOLERANCE = 0.2  # seconds of overlap ignored as timing noise


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


def compute(turns: list[dict], duration: float | None, end_reason: str) -> dict:
    speech = [t for t in turns if t["speaker"] in (BOT, CLINIC)]

    def latency(key: str) -> list[float]:
        return [t["latency"][key] for t in speech if t["speaker"] == BOT and key in t["latency"]]

    clinic_response, overlaps, gaps = [], [], []
    for prev, cur in zip(speech, speech[1:]):
        if prev["t_end"] is None:
            continue
        gap = cur["t_start"] - prev["t_end"]
        if prev["speaker"] != cur["speaker"] and gap < -OVERLAP_TOLERANCE:
            overlaps.append({"at": cur["t_start"], "speaker": cur["speaker"], "seconds": round(-gap, 2)})
        if gap > 0:
            gaps.append(gap)
        if prev["speaker"] == BOT and cur["speaker"] == CLINIC:
            clinic_response.append(gap)

    return {
        "duration_s": round(duration, 1) if duration else None,
        "end_reason": end_reason,
        "turns": {
            "clinic": sum(t["speaker"] == CLINIC for t in speech),
            "bot": sum(t["speaker"] == BOT for t in speech),
        },
        "bot_latency_s": {
            "response_e2e": summarize(latency("e2e_latency")),
            "end_of_turn_delay": summarize(
                [t["latency"]["end_of_turn_delay"] for t in speech
                 if t["speaker"] == CLINIC and "end_of_turn_delay" in t["latency"]]
            ),
            "llm_ttft": summarize(latency("llm_node_ttft")),
            "tts_ttfb": summarize(latency("tts_node_ttfb")),
        },
        "clinic_response_latency_s": summarize(clinic_response),
        "talk_over": {"count": len(overlaps), "events": overlaps},
        "longest_silence_s": round(max(gaps), 2) if gaps else None,
        "bot_turns_cut_off": sum(t["speaker"] == BOT and t["interrupted"] for t in speech),
    }
