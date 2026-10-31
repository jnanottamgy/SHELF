"""Strip pg_dump's preamble so the schema snapshot diffs cleanly.

Comments, SET lines and set_config calls change with the dumping client's
version and settings, which would make every refresh look like a schema change
and train everyone to skim the diff.
"""

import re
import sys

# Comments and SET lines vary with the client. \restrict and \unrestrict are
# psql meta-commands, not SQL: newer pg_dump emits them, and anything loading
# this file through a driver rather than through psql chokes on them.
SKIP = ("--", "SET ", "SELECT pg_catalog.set_config", "\\restrict", "\\unrestrict")

# Written out here rather than left in the file by hand. Stripping every `--`
# line is what removes pg_dump's preamble, and it removed this along with it --
# so the documented refresh command used to produce a file that differed from
# the committed one on its first seventeen lines. A refresh instruction that
# does not reproduce the file it refreshes is how a snapshot stops being
# refreshed at all.
HEADER = """\
-- Schema snapshot of the Academic Assistant database.
--
-- NOT the source of truth and NOT edited by hand. The assistant's repository
-- owns the Alembic chain and is the only place a migration is ever run; this
-- is a dump of what that chain produces, committed here so SHELF's tests have
-- a real schema to run against without cross-repo credentials -- and so that a
-- schema change shows up in a SHELF pull request as a diff somebody reads.
--
-- Refresh it with:
--   pg_dump --schema-only --no-owner --no-privileges <assistant db> \\
--     | python scripts/trim_dump.py > schema/academic-assistant.sql
--
-- If this file changes, check shelf/models/ against it. tests/test_schema_drift.py
-- fails when a column SHELF reads is missing from the live database, but it
-- cannot tell you about a column that was added and ought to be mirrored.


"""


def main() -> None:
    kept = [line for line in sys.stdin.read().splitlines() if not line.startswith(SKIP)]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    sys.stdout.write(HEADER + body + "\n")


if __name__ == "__main__":
    main()
