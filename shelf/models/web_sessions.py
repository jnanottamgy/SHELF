from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base


class WebSession(Base):
    """One browser that has proved it holds the student's phone.

    Server-side rather than a signed token, for one reason: a token cannot be
    taken back. Access has to stop the moment a subscription lapses or a device
    is kicked, and with a row that is a delete; with a JWT it is a wait.

    The id **is** the credential -- a long random string -- so it is never
    logged, and it appears in a URL exactly once, in the link the assistant
    sends over WhatsApp. Opening that link rotates the id into a fresh one and
    moves it to an HttpOnly cookie, which is what makes the link single-use
    without a second table to record that it was spent.

    ``activated_at`` is what separates the two states. NULL means the row is an
    unopened link and not a session at all: it grants nothing, counts against
    no device cap, and expires in minutes rather than weeks.

    Two activated rows per student, by design. A third login evicts the least
    recently seen rather than refusing the new one: a student who has just
    bought a new phone must not be the one locked out.
    """

    __tablename__ = "web_sessions"
    # The device cap reads every row for one student on every login, which is
    # the only query here that is not a primary-key lookup.
    __table_args__ = (Index("ix_web_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Touched on every request, and what decides which device is evicted. Not
    # created_at: the oldest login is often the phone they still use daily.
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # NULL until the link is opened. Nullable rather than a status column for
    # the same reason users.paid_until is a timestamp and not a flag: the
    # moment it happened answers more questions than the fact that it did.
    activated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
