"""Review OUR patient bot's side of each call: does it respond like a coherent human caller?

The mirror of evaluate.py: evaluate.py judges the clinic agent; this judges PATIENT_BOT turns for
ignored questions, contradictions with its own facts, invented details, repetition, broken character
and unnatural closings. Writes calls/_analysis/bot_review.json and prints a summary.

Run:  uv run python -m src.analysis.bot_review [call prefixes...]
"""

import json
import sys

import anthropic
import yaml

from src import config
from src.analysis.evaluate import MODEL
from src.persona import SCENARIOS_DIR

SYSTEM = """\
You review the PATIENT_BOT side of a phone call. PATIENT_BOT is a simulated patient (our test bot) calling a
medical practice's AI agent (CLINIC_AGENT). We want it to sound like a coherent, natural human caller.
List only PATIENT_BOT turns that a listener would find weird, illogical or unnatural:
- ignores or doesn't answer what the clinic just asked
- contradicts its own facts or something it said earlier, or invents details it shouldn't know
- repeats itself, loops, or re-asks something already answered
- breaks character (sounds like a test script, mentions being an AI/test, reads instructions)
- unnatural closing (says bye prematurely, says goodbye twice, asks a question while hanging up)
- tone or wording a real person wouldn't use on the phone
Deliberate probes from the scenario (pushing back, giving a wrong DOB on purpose, changing its mind) are NOT
problems. CLINIC_AGENT text is speech-to-text and may be garbled; judge the bot's reply to what the clinic most
likely said. Be strict but fair: an empty list is a good result."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overall": {"type": "string", "enum": ["natural", "minor_issues", "noticeable_issues"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "quote": {"type": "string"},
                    "problem": {"type": "string"},
                    "kind": {"type": "string", "enum": [
                        "ignored_question", "contradiction", "invented_detail", "repetition",
                        "broke_character", "unnatural_closing", "unnatural_wording", "other"]},
                    "severity": {"type": "string", "enum": ["minor", "noticeable"]},
                },
                "required": ["timestamp", "quote", "problem", "kind", "severity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall", "issues"],
    "additionalProperties": False,
}


def review(client, call_dir) -> dict:
    meta = json.loads((call_dir / "transcript.json").read_text())
    scenario = yaml.safe_load((SCENARIOS_DIR / f"{meta['scenario']}.yaml").read_text()) if meta.get("scenario") else {}
    prompt = (
        f"Scenario goal and probes (what the bot was told to do):\n"
        f"{yaml.safe_dump({k: scenario.get(k) for k in ('goal', 'probes', 'caller')}, sort_keys=False)}\n"
        f"Persona: {meta.get('persona')}\n\nTranscript:\n{(call_dir / 'transcript.txt').read_text()}"
    )
    response = client.messages.create(
        model=MODEL, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def main() -> None:
    prefixes = sys.argv[1:]
    calls = sorted(p for p in config.CALLS_DIR.iterdir() if p.is_dir() and p.name[:3].isdigit())
    calls = [c for c in calls if not prefixes or any(c.name.startswith(x) for x in prefixes)]
    client = anthropic.Anthropic()
    out_path = config.CALLS_DIR / "_analysis" / "bot_review.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for c in calls:
        try:
            results[c.name] = review(client, c)
        except Exception as e:
            print(f"{c.name}: ERROR {e}", file=sys.stderr)
            continue
        r = results[c.name]
        print(f"{c.name:30} {r['overall']:18} " + "; ".join(
            f"{i['timestamp']} {i['kind']}({i['severity'][0]})" for i in r["issues"]))
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
