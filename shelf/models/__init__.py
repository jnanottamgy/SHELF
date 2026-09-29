"""A **read-only mirror** of the assistant's tables. Not the source of truth.

The schema is owned by the Academic Assistant repository: it holds the Alembic
chain, and its migrations are the only ones that ever run. Nothing here creates
a table, alters one, or is pointed at by a migration.

That leaves one real risk, and it is the reason this package exists in this
shape rather than as a convenient copy-paste: these classes can fall out of
step with the database, silently, on a deploy nobody thought concerned SHELF.
``tests/test_schema_drift.py`` is the guard -- it reflects the live database
and fails if a column read here has moved. A red build is the whole point; the
alternative is a 500 that only some students see.

Only the tables SHELF reads are mirrored. Adding one means adding the drift
test's coverage with it.
"""

from shelf.models.base import Base
from shelf.models.deadlines import Deadline
from shelf.models.resources import Resource
from shelf.models.subjects import Subject
from shelf.models.terms import Term
from shelf.models.user import User
from shelf.models.web_sessions import WebSession

__all__ = [
    "Base",
    "Deadline",
    "Resource",
    "Subject",
    "Term",
    "User",
    "WebSession",
]
