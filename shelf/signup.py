"""Claiming a number, and waiting for the student to prove it.

**This is the one thing SHELF writes.** Everywhere else it is a reader, and
that is load-bearing: the service exposed to the open internet must not be
able to hand out access. Writing here does not break that, because a
``signup_codes`` row is a *claim*, not a credential. It records that somebody
typed a number into a form -- which is evidence of nothing. The proof is a
WhatsApp message arriving from that number carrying the code, and only the
assistant can observe such a message. ``users.paid_until`` remains the
credential, and only the payment webhook writes it.

**The direction of the code is the security property.** The student sends one
to us; we never send one to them. A code travelling *to* a phone can be read
off a lock screen or talked out of someone on a call. A code travelling *from*
one cannot, because possession of the phone is the act itself. It also costs
nothing and needs no SMS vendor: the student messages first, so the
assistant's confirmation is inside WhatsApp's service window.

**What is duplicated across the two repositories, and what is not.** The code
alphabet and the TTL are this service's own choice -- the assistant does not
validate against them, and deliberately so: a validator there that disagreed
with the generator here would mean no code ever verifies, silently, with
neither repository's tests noticing. What *must* agree is the table shape and
the normalised phone format, and both are pinned by the database itself
(``ck_signup_codes_ph_no_digits``) rather than by two copies of a rule.
"""

import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from shelf.models import SignupCode, User
from shelf.phone import InvalidPhoneNumber, normalise

logger = logging.getLogger(__name__)

#: No O/0 and no I/1: read off one screen, typed into another, and those are
#: the two misreadings worth removing entirely.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 6

#: Short, because it sits on a screen someone may walk away from. Long enough
#: to switch apps, find the chat and press send without hurrying.
TTL = timedelta(minutes=10)


class UnverifiableNumber(ValueError):
    """Not a number the assistant could ever reach on WhatsApp."""


def claim(db: Session, raw_phone: str, now: datetime) -> tuple[str, str]:
    """Record a claim and return ``(code, normalised_phone)``.

    Normalising here rather than trusting the form is the point. The CHECK on
    the table is a backstop that should never fire; defence belongs at the
    writer. A number that cannot be normalised is one the assistant could
    never reach, so it is refused now rather than after a payment.

    Earlier unredeemed claims on the same number are cleared: they prove
    nothing and grant nothing, but a student who reloads three times should
    not leave three live codes behind them.
    """
    try:
        phone = normalise(raw_phone)
    except InvalidPhoneNumber as error:
        raise UnverifiableNumber(str(error)) from error

    db.execute(
        delete(SignupCode).where(
            SignupCode.ph_no == phone, SignupCode.verified_at.is_(None)
        )
    )
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    db.add(
        SignupCode(
            code=code, ph_no=phone, created_at=now, expires_at=now + TTL, verified_at=None
        )
    )
    db.flush()
    logger.info("signup code claimed")  # never the number, never the code
    return code, phone


def state_of(db: Session, code: str, now: datetime) -> str:
    """What the page should show: ``waiting``, ``verified`` or ``expired``.

    Keyed by the code rather than by the number, so polling this endpoint
    tells a caller nothing they did not already hold. Someone who has the code
    has it because the page gave it to them.
    """
    row = db.get(SignupCode, code)
    if row is None or row.expires_at <= now:
        return "expired"
    return "verified" if row.verified_at is not None else "waiting"


def already_a_customer(db: Session, raw_phone: str) -> User | None:
    """So the page can say "you already have this" instead of charging twice."""
    try:
        phone = normalise(raw_phone)
    except InvalidPhoneNumber:
        return None
    return db.scalar(select(User).where(User.ph_no == phone))
