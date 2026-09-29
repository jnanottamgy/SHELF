from datetime import datetime, timedelta

from sqlalchemy import TIMESTAMP, BigInteger, CheckConstraint, Identity, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base

term_type = postgresql.ENUM(
    "semester", "trimester", "quarter", "annual",
    name="term_type",
    create_type=False,
)


class User(Base):
    __tablename__ = "users"

    # The payment site is the only writer and we do not control its form handling.
    # A stored "+91 98760 00110" -- or a bare "9876000110" -- never matches Meta's
    # 91xxxxxxxxxx, so the student pays and hears nothing. Fail that write at checkout.
    __table_args__ = (CheckConstraint("ph_no ~ '^91[0-9]{10}$'", name="ck_users_ph_no_digits"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ph_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    term_system: Mapped[str | None] = mapped_column(term_type, nullable=True)
    # Written by the payment site, never by the bot. NOT NULL: a row exists because someone paid.
    paid_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # The Razorpay subscription currently charging them. Nullable: a hand-provisioned
    # pilot row has none, and so does a student whose series has ended.
    subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    def is_entitled(self, now: datetime) -> bool:
        """Access derived from a timestamp, so it can never drift the way a flag would."""
        return self.paid_until > now

    def is_in_grace(self, now: datetime, grace: timedelta) -> bool:
        """Lapsed, but recently enough that we keep serving them.

        A renewal that lands a day late, or a UPI mandate that retries, must not
        read to a paying student as the product being broken. Grace is silent on
        purpose -- being nagged about a payment that is about to go through is
        worse than not noticing at all.
        """
        return not self.is_entitled(now) and self.paid_until + grace > now
