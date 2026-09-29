from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Identity, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from shelf.models.base import Base

resource_status = postgresql.ENUM(
    "pending_clarification", "ready",
    name="resource_status",
    create_type=False,
)


class Resource(Base):
    """A stored file or link.

    ``subject_id NULL`` together with ``status='pending_clarification'`` is the
    bare-file state — one row from arrival to resolution. There is deliberately
    no separate pending-files table.
    """

    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subjects.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(resource_status, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inline text payload (pasted notes, a forwarded text message). Mutually
    # exclusive with storage_ref in practice, not enforced at the DB level.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the file was called when it arrived. Separate from ``title``, which
    # is the student's own words: they search by what they said, not by
    # "CamScanner 03-01-2026 10.14.pdf".
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which shelf this sits on, inferred from what the student said ("DBMS
    # notes u2" -> material). TEXT rather than a Postgres enum because the set
    # is ours and may grow, and an enum change needs a hand-written migration.
    # The values are closed in code (app/services/categories.py) so the folder
    # tree stays a fixed shape -- free-form types would grow a folder per
    # phrasing. NULL means nobody said, which reads as the default shelf.
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
