"""The guard that makes a mirrored model package honest.

``shelf/models/`` is a copy of tables another repository owns. Copies rot, and
this one rots *silently*: a migration lands in the assistant, nobody thinks it
concerns SHELF, and the first symptom is a 500 on the page that reads the
column that moved -- seen only by whichever students happen to open it.

So this reflects the real database and checks every column SHELF reads is
still there, with a type that still round-trips. It is a narrow guarantee and
worth stating plainly: it catches a column that was **removed or renamed**. It
cannot catch a column that was **added** and ought to be mirrored, because
nothing here knows that column was supposed to exist. That one is caught by
reading the diff when `schema/academic-assistant.sql` is refreshed, which is
why that file is committed rather than generated at test time.
"""

from typing import Any

import pytest
from sqlalchemy import Engine, inspect

from shelf.models import Base

MIRRORED = ("users", "terms", "subjects", "resources", "deadlines", "web_sessions")


@pytest.fixture(scope="module")
def live(test_engine: Engine) -> dict[str, dict[str, Any]]:
    inspector = inspect(test_engine)
    return {
        table: {column["name"]: column for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }


class TestTheMirrorMatchesTheDatabase:
    @pytest.mark.parametrize("table", MIRRORED)
    def test_the_table_still_exists(
        self, table: str, live: dict[str, dict[str, Any]]
    ) -> None:
        assert table in live, f"{table} is gone from the assistant's schema"

    def test_every_mirrored_column_exists(self, live: dict[str, dict[str, Any]]) -> None:
        """A renamed column reads as a removed one here, which is correct:
        SHELF's query would fail either way."""
        missing: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in live:
                continue
            for column in table.columns:
                if column.name not in live[table.name]:
                    missing.append(f"{table.name}.{column.name}")
        assert not missing, (
            "these columns are mirrored in shelf/models but not in the database: "
            + ", ".join(missing)
            + " -- refresh schema/academic-assistant.sql and update the models"
        )

    def test_nullability_has_not_tightened(self, live: dict[str, dict[str, Any]]) -> None:
        """A column that became NOT NULL upstream is fine to read. One that
        became nullable is not: SHELF's model says it cannot be None, and the
        first NULL row turns into an attribute that lies rather than an error.
        """
        wrong: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in live:
                continue
            for column in table.columns:
                real = live[table.name].get(column.name)
                if real is None:
                    continue
                if real["nullable"] and not column.nullable:
                    wrong.append(f"{table.name}.{column.name}")
        assert not wrong, (
            "these are nullable in the database but not in shelf/models: " + ", ".join(wrong)
        )

    def test_the_mirror_is_not_the_source_of_truth(self) -> None:
        """A migration directory here would mean two chains against one
        database, which is how a half-applied schema happens."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        assert not (root / "alembic").exists()
        assert not (root / "alembic.ini").exists()
