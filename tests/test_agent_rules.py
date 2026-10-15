import pytest

from src.agent import is_hold_only


@pytest.mark.parametrize(
    "text",
    [
        # Real clinic lines from calls 003, 004 and 006 that the bot should not answer.
        "Thanks for confirming. Let me check for weekday morning openings before eleven next week for your annual physical.",
        "Thanks, Jane. Let me check your upcoming appointment and insurance details.",
        "I'm still working on your Aetna PPO insurance question. Please hold while I get that information.",
        "I am processing the cancellation for your appointment.",
    ],
)
def test_hold_only_turns_get_no_reply(text):
    assert is_hold_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "Let me check, can you confirm your date of birth?",
        "The first available slot is Tuesday, October twentieth, at ten AM. Would you like to book that time?",
        "Can you please provide your date of birth?",
        "Your appointment is set for Tuesday at ten AM.",
    ],
)
def test_real_turns_still_get_a_reply(text):
    assert not is_hold_only(text)
