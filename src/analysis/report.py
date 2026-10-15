"""Cross-call report: cluster per-call findings into distinct issues and check consistency.

Reads every calls/<id>/findings.json (from evaluate.py) and transcript, asks a strong model to
(1) merge findings that are the same underlying issue across calls, counting reproductions, and
(2) compare facts the agent stated in different calls (addresses, hours, provider names, policies).
Writes calls/_analysis/clusters.json and calls/_analysis/review.md: the sheet a human works through,
listening at each timestamp before anything goes into BUG_REPORT.md.

Run:  uv run python -m src.analysis.report
"""

import json

import anthropic

from src import config
from src.analysis.evaluate import MODEL

SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "occurrences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "call": {"type": "string"},
                                "timestamp": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["call", "timestamp", "quote"],
                            "additionalProperties": False,
                        },
                    },
                    "opportunities": {
                        "type": "integer",
                        "description": "How many calls created the conditions for this issue to show up",
                    },
                    "description": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "expected_behavior": {"type": "string"},
                    "verification_steps": {"type": "string"},
                },
                "required": ["title", "category", "severity", "occurrences", "opportunities",
                             "description", "why_it_matters", "expected_behavior",
                             "verification_steps"],
                "additionalProperties": False,
            },
        },
        "consistency": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "statements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "call": {"type": "string"},
                                "timestamp": {"type": "string"},
                                "said": {"type": "string"},
                            },
                            "required": ["call", "timestamp", "said"],
                            "additionalProperties": False,
                        },
                    },
                    "consistent": {"type": "boolean"},
                },
                "required": ["topic", "statements", "consistent"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["issues", "consistency"],
    "additionalProperties": False,
}

SYSTEM = """\
You are consolidating QA results for a medical practice's AI phone agent across many test calls.
Per-call findings were drafted by another reviewer and are unverified; some are false positives.
Merge findings that describe the same underlying behaviour into one issue, list every occurrence, and
estimate how many calls created the conditions for it (for repro rates like "3/4"). Drop findings that
other calls clearly contradict, and say so in the description of a related issue if relevant. Then
compare factual statements the agent made across calls (practice addresses/locations, hours, provider
names, policies) and mark each topic consistent or not. CLINIC_AGENT text is speech-to-text: treat
spelling variants of the same name cautiously and say what to listen for. Order issues by severity."""


def load_calls() -> list[dict]:
    calls = []
    for d in sorted(p for p in config.CALLS_DIR.iterdir() if p.is_dir() and p.name[:3].isdigit()):
        if not (d / "findings.json").exists():
            continue
        f = json.loads((d / "findings.json").read_text())
        calls.append({
            "call": d.name,
            "summary": f["summary"],
            "expected_checks": f["expected_checks"],
            "findings": [
                {k: x[k] for k in ("title", "category", "severity", "timestamp", "quote",
                                    "what_happened", "possible_stt_error", "verification", "notes")}
                for x in f["findings"]
            ],
            "transcript": (d / "transcript.txt").read_text(),
        })
    return calls


def to_markdown(result: dict, n_calls: int) -> str:
    lines = [
        "# Review sheet (generated, unverified)",
        "",
        f"Generated by `src/analysis/report.py` from {n_calls} calls. Listen at each timestamp "
        "(`calls/<id>/recording.mp3`, clinic on the left channel) and mark each finding in "
        "`calls/<id>/findings.json` as `verified`, `stt_error` or `rejected`.",
        "",
        "| # | Issue | Severity | Seen | Calls |",
        "|---|---|---|---|---|",
    ]
    for i, issue in enumerate(result["issues"], 1):
        calls = ", ".join(sorted({o["call"][:3] for o in issue["occurrences"]}))
        seen = f"{len({o['call'] for o in issue['occurrences']})}/{issue['opportunities']}"
        lines.append(f"| {i} | {issue['title']} | {issue['severity']} | {seen} | {calls} |")
    for i, issue in enumerate(result["issues"], 1):
        lines += ["", f"## {i}. {issue['title']}",
                  f"**Severity:** {issue['severity']} · **Category:** {issue['category']}", "",
                  issue["description"], "", f"**Why it matters:** {issue['why_it_matters']}",
                  f"**Expected:** {issue['expected_behavior']}",
                  f"**To verify:** {issue['verification_steps']}", "", "Occurrences:"]
        lines += [f"- `{o['call']}` @ {o['timestamp']}: \"{o['quote']}\"" for o in issue["occurrences"]]
    lines += ["", "## Cross-call consistency", ""]
    for c in result["consistency"]:
        lines.append(f"**{c['topic']}**: {'consistent' if c['consistent'] else '**inconsistent**'}")
        lines += [f"- `{s['call']}` @ {s['timestamp']}: {s['said']}" for s in c["statements"]]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    calls = load_calls()
    client = anthropic.Anthropic()
    # Long output over many calls: stream so the request isn't cut off by an HTTP timeout.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(calls, indent=1)}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise SystemExit(f"refused: {response.stop_details}")
    result = json.loads(next(b.text for b in response.content if b.type == "text"))

    out = config.CALLS_DIR / "_analysis"
    out.mkdir(exist_ok=True)
    (out / "clusters.json").write_text(json.dumps(result, indent=2))
    (out / "review.md").write_text(to_markdown(result, len(calls)))
    print(f"{len(result['issues'])} issues from {len(calls)} calls -> {out / 'review.md'}")


if __name__ == "__main__":
    main()
