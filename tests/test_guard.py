import pytest

from src.guard import ALLOWED_NUMBER, ForbiddenNumberError, check_dial_target


@pytest.mark.parametrize(
    "number", ["+18054398008", "18054398008", "8054398008", "+1 (805) 439-8008"]
)
def test_allows_target_number_in_any_format(number):
    assert check_dial_target(number) == ALLOWED_NUMBER


@pytest.mark.parametrize(
    "number",
    [
        "",
        "+15555555555",
        "+180543980080",  # extra digit
        "+18054398009",  # off by one
        "+448054398008",  # wrong country code
        "911",
    ],
)
def test_rejects_everything_else(number):
    with pytest.raises(ForbiddenNumberError):
        check_dial_target(number)
