from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, Identity, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(postgresql.BIGINT, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(postgresql.BIGINT, ForeignKey("users.id"), nullable=False)
    term_id: Mapped[int] = mapped_column(postgresql.BIGINT, ForeignKey("terms.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
