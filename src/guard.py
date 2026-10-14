"""Hard allowlist for outbound dialing.

The bot may only ever call the assessment test line. This is enforced in code,
independent of .env or CLI input, so a typo or a bad config can't dial anyone else.
"""

import re

ALLOWED_NUMBER = "+18054398008"


class ForbiddenNumberError(ValueError):
    pass


def normalize(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


def check_dial_target(number: str) -> str:
    """Return the E.164 target if it is the allowed number, otherwise raise."""
    normalized = normalize(number)
    if normalized != ALLOWED_NUMBER:
        raise ForbiddenNumberError(
            f"Refusing to dial {number!r}: only {ALLOWED_NUMBER} is allowed."
        )
    return ALLOWED_NUMBER
