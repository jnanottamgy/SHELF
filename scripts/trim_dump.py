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


def main() -> None:
    kept = [line for line in sys.stdin.read().splitlines() if not line.startswith(SKIP)]
    sys.stdout.write(re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n")


if __name__ == "__main__":
    main()
