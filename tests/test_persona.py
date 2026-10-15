import pytest
import yaml

from src.persona import SCENARIOS_DIR, list_scenarios, load_plan

PERSONAS = yaml.safe_load((SCENARIOS_DIR / "personas.yaml").read_text())


@pytest.mark.parametrize("scenario_id", list_scenarios())
def test_every_scenario_builds_a_plan(scenario_id):
    plan = load_plan(scenario_id)
    scenario = plan.scenario
    assert plan.facts["name"] in plan.instructions
    assert scenario["goal"].strip() in plan.instructions
    assert plan.voice == PERSONAS[plan.persona_id]["voices"][plan.facts["gender"]]
    assert 0.6 <= plan.speed <= 2.0
    # The grading rubric must never leak into the caller's prompt.
    for expectation in scenario.get("expected", []):
        assert expectation not in plan.instructions


@pytest.mark.parametrize("persona_id", list(PERSONAS))
def test_persona_override(persona_id):
    plan = load_plan("simple-scheduling", persona_override=persona_id)
    assert plan.persona_id == persona_id
    assert PERSONAS[persona_id]["style"].strip() in plan.instructions


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError, match="Unknown scenario"):
        load_plan("does-not-exist")


@pytest.mark.parametrize("persona_id", list(PERSONAS))
def test_every_persona_has_a_spoken_goodbye(persona_id):
    # Used verbatim at the time limit (not an LLM instruction): must be a real goodbye, no question.
    plan = load_plan("simple-scheduling", persona_override=persona_id)
    assert "bye" in plan.goodbye.lower() and "?" not in plan.goodbye


def test_caller_appointments_are_labelled_as_their_own():
    # Call 011: calling about his wife, the bot offered its own appointment as hers.
    plan = load_plan("spouse-asks-partner")
    assert "Your own upcoming appointments (yours only" in plan.instructions
