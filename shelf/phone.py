"""Turning whatever someone typed into the one form Meta will ever send.

This is the highest-consequence function in the payment site, and it is worth
being blunt about why. The bot's entire response to an unknown number is
silence. So a number stored in any shape other than ``91XXXXXXXXXX`` does not
produce an error, a bounce, or a support ticket -- it produces a student who
paid and then heard nothing, with nothing anywhere to say why.

``ck_users_ph_no_digits`` is the backstop that should never fire. Defence
belongs here, at the writer; the constraint is only proof that it held.

Rejecting beats guessing. A number this cannot confidently normalise is a
number we must not take money for.
"""

import re

_NON_DIGITS = re.compile(r"\D")

COUNTRY_CODE = "91"
NATIONAL_DIGITS = 10
# TRAI allocates mobile numbers starting 6-9. A 10-digit number starting 0-5 is
# a landline, a service code, or a typo -- none of which WhatsApp can reach.
MOBILE_PREFIXES = frozenset("6789")


class InvalidPhoneNumber(ValueError):
    """Not a number we can reach. Fail the checkout rather than take the money."""


def normalise(raw: str) -> str:
    """Return ``91XXXXXXXXXX``, or raise.

    Accepts the shapes a person actually types -- +91, a leading 0, spaces,
    dashes, brackets -- and nothing else.
    """
    digits = _NON_DIGITS.sub("", raw or "")
    if not digits:
        raise InvalidPhoneNumber("no digits in phone number")

    # A leading 0 is India's national trunk prefix and is never part of the number.
    if len(digits) == NATIONAL_DIGITS + 1 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == NATIONAL_DIGITS:
        national = digits
    elif len(digits) == len(COUNTRY_CODE) + NATIONAL_DIGITS and digits.startswith(COUNTRY_CODE):
        national = digits[len(COUNTRY_CODE) :]
    else:
        raise InvalidPhoneNumber(f"expected a 10-digit Indian mobile, got {len(digits)} digits")

    if national[0] not in MOBILE_PREFIXES:
        raise InvalidPhoneNumber("not an Indian mobile number")

    return COUNTRY_CODE + national


def is_valid(raw: str) -> bool:
    """For a form's client-side-ish check. The write still goes through normalise."""
    try:
        normalise(raw)
    except InvalidPhoneNumber:
        return False
    return True
