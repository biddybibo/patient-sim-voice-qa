"""Benchmark candidate live LLMs on the bot's real workload: time to first spoken token.

Replays a real call (the persona prompt + the bot's tools + the first turns of a saved transcript,
ending on a clinic turn) and streams the bot's next reply from each model config. TTFT here is what the
caller hears as dead air on top of end-of-turn detection and TTS.

Run:  uv run python scripts/bench_llm.py [--call 003-simple-scheduling] [--trials 8]
Writes: calls/_bench/llm_ttft.json
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config  # noqa: E402  (loads .env)

import anthropic  # noqa: E402

from src.persona import load_plan  # noqa: E402

# The live agent sends its tool definitions with every request, so the benchmark does too: without them
# Sonnet 5.5 looked 2x faster than in real calls (0.57 s vs 1.24 s TTFT); with them it matches call 009.
TOOLS = [
    {"name": "end_call", "description": "Hang up the phone. Only call this after you have already said goodbye.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
    {"name": "wait_on_transfer", "description": "Call this when they say they're transferring you.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_dtmf", "description": "Press keys on the phone keypad.",
     "input_schema": {"type": "object", "properties": {"digits": {"type": "string"}}, "required": ["digits"]}},
]

# (label, model, extra request fields). Haiku 5.5 accepts thinking "disabled" at effort <= high;
# Sonnet 5.5 rejects "disabled" and uses "between_tools" (no thinking outside tool calls) instead.
CONFIGS = [
    ("haiku-5.5 thinking off, effort low (current)", "claude-haiku-5-5",
     {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}),
    ("haiku-5.5 default (adaptive thinking)", "claude-haiku-5-5", {}),
    ("sonnet-5.5 thinking between_tools, effort low", "claude-sonnet-5-5",
     {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}),
]


def conversation(call_id: str, clinic_turns: int) -> tuple[str, list[dict]]:
    meta = json.loads((config.CALLS_DIR / call_id / "transcript.json").read_text())
    plan = load_plan(meta["scenario"] or "simple-scheduling")
    messages: list[dict] = []
    seen_clinic = 0
    for t in meta["turns"]:
        if t["speaker"] == "TOOL":
            continue
        role = "user" if t["speaker"] == "CLINIC_AGENT" else "assistant"
        if role == "user":
            if seen_clinic == clinic_turns:
                break
            seen_clinic += 1
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += " " + t["text"]
        else:
            messages.append({"role": role, "content": t["text"]})
    while messages[-1]["role"] != "user":
        messages.pop()
    return plan.instructions, messages


def ttft(client, model, extra, system, messages) -> tuple[float, float, str]:
    start = time.perf_counter()
    first = None
    text = ""
    with client.messages.stream(
        model=model, max_tokens=200, system=system, messages=messages, tools=TOOLS, extra_body=extra
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                if first is None:
                    first = time.perf_counter() - start
                text += event.delta.text
    return first or float("nan"), time.perf_counter() - start, text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", default="003-simple-scheduling")
    parser.add_argument("--clinic-turns", type=int, default=5)
    parser.add_argument("--trials", type=int, default=8)
    args = parser.parse_args()

    client = anthropic.Anthropic()
    system, messages = conversation(args.call, args.clinic_turns)
    print(f"Replaying {args.call}: {len(messages)} messages, last clinic turn: {messages[-1]['content'][:70]!r}\n")

    results = {}
    for label, model, extra in CONFIGS:
        firsts, totals, sample = [], [], ""
        ttft(client, model, extra, system, messages)  # warm-up, not counted
        for _ in range(args.trials):
            f, total, text = ttft(client, model, extra, system, messages)
            firsts.append(f)
            totals.append(total)
            sample = text
        # A reply with no text (e.g. the model chose a tool call) has no TTFT; count it, don't sort NaN.
        no_text = sum(f != f for f in firsts)
        firsts = [f for f in firsts if f == f]
        ordered = sorted(firsts)
        results[label] = {
            "model": model,
            "replies_without_text": no_text,
            "ttft_median_s": round(statistics.median(firsts), 3),
            "ttft_p90_s": round(ordered[round(0.9 * (len(ordered) - 1))], 3),
            "full_reply_median_s": round(statistics.median(totals), 3),
            "sample_reply": sample,
        }
        r = results[label]
        print(f"{label:48} TTFT median {r['ttft_median_s']:.2f}s  p90 {r['ttft_p90_s']:.2f}s  "
              f"full {r['full_reply_median_s']:.2f}s  no-text {no_text}\n    e.g. {sample.strip()[:110]!r}")

    out = config.CALLS_DIR / "_bench"
    out.mkdir(exist_ok=True)
    (out / "llm_ttft.json").write_text(json.dumps(
        {"call": args.call, "trials": args.trials, "results": results}, indent=2))
    print(f"\nSaved {out / 'llm_ttft.json'}")


if __name__ == "__main__":
    main()
