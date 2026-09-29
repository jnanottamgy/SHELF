from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base


class SignupCode(Base):
    """A number somebody has claimed, before anybody has proved it.

    The whole point of this table is the order it enforces: a number is proved
    reachable *before* money is taken against it. CLAUDE.md records why that
    matters -- the assistant's entire response to an unknown number is silence,
    so a payment captured against a wrong or mistyped number is invisible from
    every direction until the student gives up.

    A row here **grants nothing**. It records that someone typed a number into
    a form, which is not evidence of anything: the proof is a WhatsApp message
    arriving *from* that number carrying this code. That is why the site may
    write these rows while remaining unable to mint access -- the credential is
    ``users.paid_until``, and only the payment webhook writes that.

    ``code`` is the primary key rather than a surrogate id because it is what
    both sides look up by: the site polls it, the assistant matches an inbound
    body against it. Redemption additionally requires ``ph_no`` to match the
    sender, so a code claimed for someone else's number is useless in any hand
    but theirs.
    """

    __tablename__ = "signup_codes"
    __table_args__ = (
        # Same shape the users table enforces. This one is written by the site
        # too, so the constraint is the only leverage over a writer we do not
        # watch -- a malformed number fails here instead of silently later.
        CheckConstraint("ph_no ~ '^91[0-9]{10}$'", name="ck_signup_codes_ph_no_digits"),
        # Erasure deletes by number, and "is this number verified" reads by it.
        Index("ix_signup_codes_ph_no", "ph_no"),
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    ph_no: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Short. The code sits in a form on a screen someone may walk away from.
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # NULL until a message carrying this code arrives from ph_no. A timestamp
    # rather than a flag, for the same reason paid_until is: the moment it
    # happened answers more questions than the fact that it did, and it is what
    # makes a redelivery a no-op instead of a second confirmation.
    verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
