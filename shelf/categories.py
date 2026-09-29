"""The shelves a file can sit on, and the words that map onto them.

The folder tree on the website is Term -> Subject -> shelf, and a **fixed three
shelves** is what makes that tree navigable. Storing the student's phrasing
verbatim instead would grow a folder for every way of saying the same thing --
"notes", "note", "lecture notes", "unit notes" -- and a drive with forty
folders per subject is worse than no drive at all.

So the student's vocabulary is still what decides; it is just resolved to one
of three. Everything the product stores verbatim (subject names, titles) it
still stores verbatim -- this is a routing decision, not a rename.

Deliberately not a Postgres enum: the set is ours rather than Meta's and may
grow, and an enum change costs a hand-written migration every time.
"""

from typing import Final

MATERIAL: Final = "material"
PYQ: Final = "pyq"
INFO: Final = "info"

#: Closed set. A value outside it never reaches the database -- the gate rejects it.
CATEGORIES: Final = (MATERIAL, PYQ, INFO)

#: Nobody said, so it goes on the shelf most things belong on.
DEFAULT: Final = MATERIAL

#: What each shelf is called on the website, in the student's language.
LABELS: Final = {
    MATERIAL: "Material",
    PYQ: "PYQ & Questions",
    INFO: "Info & Admin",
}

#: One line each, shown under the folder name so an empty shelf still explains itself.
BLURBS: Final = {
    MATERIAL: "Notes, slides, textbooks, recordings",
    PYQ: "Past papers, question banks, model answers",
    INFO: "Syllabus, timetables, announcements",
}


def normalise(proposed: str | None) -> str:
    """Resolve what the model proposed to a real shelf.

    Unrecognised or absent lands on the default rather than being refused: a
    file whose shelf we cannot name is still a file the student sent, and
    hiding it because a word did not match would be the worse failure.
    """
    if proposed is None:
        return DEFAULT
    candidate = proposed.strip().lower()
    return candidate if candidate in CATEGORIES else DEFAULT
