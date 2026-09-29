from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Identity, Text
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deadlines can be unattached to any subject.
    subject_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subjects.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
