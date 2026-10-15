"""Build one call's plan from a scenario + persona + patient facts.

scenarios/<id>.yaml   what the caller wants (goal, probes) and what a good agent does (expected)
scenarios/personas.yaml  how the caller talks and sounds (style, voice, speed, emotion)
scenarios/facts.yaml     who the caller is (test accounts A and B)

The `expected` list is deliberately NOT in the prompt: it's the grading rubric for the
clinic agent, and a caller who knows it starts steering toward it like a test script.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
DATA_FILES = {"facts.yaml", "personas.yaml"}


@dataclass
class CallPlan:
    scenario: dict
    persona_id: str
    facts: dict
    instructions: str
    voice: str
    speed: float
    emotion: str | None
    allow_interruptions: bool
    max_seconds: int
    keyterms: list[str] = field(default_factory=list)


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml") if p.name not in DATA_FILES)


def _load(name: str) -> dict:
    return yaml.safe_load((SCENARIOS_DIR / name).read_text())


def load_plan(scenario_id: str, persona_override: str | None = None) -> CallPlan:
    path = SCENARIOS_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown scenario {scenario_id!r}; have: {', '.join(list_scenarios())}")
    scenario = yaml.safe_load(path.read_text())
    personas = _load("personas.yaml")
    persona_id = persona_override or scenario.get("persona", "neutral")
    persona = personas[persona_id]
    facts = {**_load("facts.yaml")["accounts"][scenario.get("caller", "A")],
             **scenario.get("facts_override", {})}
    flags = scenario.get("flags", {})

    return CallPlan(
        scenario=scenario,
        persona_id=persona_id,
        facts=facts,
        instructions=build_instructions(scenario, persona, facts),
        voice=persona["voices"][facts["gender"]],
        speed=float(persona.get("speed", 1.0)),
        emotion=persona.get("emotion"),
        allow_interruptions=bool(flags.get("allow_interruptions", False)),
        max_seconds=int(scenario.get("max_minutes", 4) * 60),
        keyterms=[facts["name"], facts.get("pharmacy", ""), *facts.get("meds", [])],
    )


def build_instructions(scenario: dict, persona: dict, facts: dict) -> str:
    appointments = "; ".join(facts.get("known_appointments") or []) or "none"
    probes = "\n".join(f"- {p}" for p in scenario.get("probes", []))
    return f"""\
You are {facts["name"]}, a patient calling your doctor's office. This is a real phone call.

About you: date of birth {facts["dob"]}; phone {facts["phone"]}; insurance {facts.get("insurance", "unknown")}; \
pharmacy {facts.get("pharmacy", "unknown")}; medications {", ".join(facts.get("meds", [])) or "none"}. \
Upcoming appointments: {appointments}.

What you want: {scenario["goal"].strip()}
Work these in naturally when they fit, one at a time:
{probes}

How you talk: {persona["style"].strip()}

Phone rules:
- One or two short sentences per turn. No lists, markdown or emojis. Say dates and numbers the way people say them aloud.
- Let them lead. Answer what they ask; give details only when asked. Spell your name only if asked.
- If they offer to set up or look up a profile, go along with it.
- Never say or hint that you're an AI, a bot or a test.
- If they drift, steer back politely to what you want.
- If they say they're checking something or ask you to hold, say nothing; wait until they speak again.
- If they mention an appointment or detail you don't remember, don't argue; ask them what they have on file.
- If they say they're transferring you, call wait_on_transfer instead of saying goodbye.
- When your goal is done and confirmed, or they clearly can't help after two tries, say thanks and goodbye, then call end_call. Never call end_call before saying goodbye.
- If you hear a phone menu asking you to press a key, use send_dtmf.
"""
