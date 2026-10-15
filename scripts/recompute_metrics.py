"""Recompute metrics.json for saved calls (after the metrics code changes) and print a
one-line summary per call. Keeps each call's duration and end reason.

Run:  uv run python scripts/recompute_metrics.py            (all calls)
      uv run python scripts/recompute_metrics.py 004 005    (by number prefix)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import metrics  # noqa: E402
from src.audio_analysis import analyze  # noqa: E402
from src.config import CALLS_DIR  # noqa: E402


def recompute(call_dir) -> dict:
    old = json.loads((call_dir / "metrics.json").read_text())
    turns = json.loads((call_dir / "transcript.json").read_text())["turns"]
    recording = call_dir / "recording.ogg"
    audio = analyze(recording) if recording.exists() else None
    m = metrics.compute(turns, old["duration_s"], old["end_reason"], audio)
    (call_dir / "metrics.json").write_text(json.dumps(m, indent=2))
    return m


def main() -> None:
    prefixes = sys.argv[1:]
    calls = sorted(p for p in CALLS_DIR.iterdir() if p.is_dir() and p.name[:3].isdigit())
    calls = [c for c in calls if not prefixes or any(c.name.startswith(x) for x in prefixes)]
    print(f"{'call':26} {'dur':>5} {'end':>14} {'e2e med':>8} {'after-eot':>9} {'ttft':>5} "
          f"{'overlap s':>9} {'events':>6} {'open pause':>10} {'cut in':>6}")
    for c in calls:
        m = recompute(c)
        lat = m["bot_latency_s"]
        audio = m["talk_over_audio"] or {}
        opening = m["opening_audio"] or {}
        pauses = opening.get("pauses") or []
        print(
            f"{c.name:26} {m['duration_s'] or 0:5.0f} {m['end_reason']:>14} "
            f"{(lat['response_e2e'] or {}).get('median', 0):8.2f} "
            f"{(lat['response_after_turn_end'] or {}).get('median', 0):9.2f} "
            f"{(lat['llm_ttft'] or {}).get('median', 0):5.2f} "
            f"{audio.get('seconds', 0):9.2f} {len(audio.get('events', [])):6d} "
            f"{(pauses[0]['seconds'] if pauses else 0):10.2f} {str(opening.get('bot_cut_in')):>6}"
        )


if __name__ == "__main__":
    main()
