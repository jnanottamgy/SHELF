"""The folder tree the website draws, read-only and scoped to one student.

The tree is Term -> Subject -> shelf, and every level of it is derived rather
than stored. There is no folders table and there should not be one: a folder
here is a GROUP BY over rows the student already created by talking, so it can
never disagree with what is actually filed. A real folder tree would need
creating, renaming, moving and repairing, and the student never asked for any
of that -- they asked for their notes back.

Counts come back with the rows because an empty folder is worth showing
differently from a full one, and finding that out by opening it is one page
load the student should not have to spend.

Cross-user scoping is the rule this file exists to keep. Every id that reaches
these methods came out of a URL, which is to say out of a stranger's hands, so
no lookup is ever by id alone -- ``user_id`` is in the WHERE clause even where
the join already implies it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from shelf import categories
from shelf.models import Deadline, Resource, Subject, Term

# India observes no DST, so a fixed offset is exact rather than approximate,
# and it avoids depending on a tzdata package being installed. Defined here
# because there is no users.timezone column yet -- when there is, this becomes
# the default rather than the rule. Must agree with the assistant's gates.IST.
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Shelf:
    """One of the three fixed shelves inside a subject, with how full it is."""

    key: str
    label: str
    blurb: str
    count: int


@dataclass(frozen=True)
class SubjectFolder:
    subject: Subject
    count: int


@dataclass(frozen=True)
class TermFolder:
    term: Term
    count: int


class LibraryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # --- the tree ---------------------------------------------------------

    def terms(self, user_id: int) -> list[TermFolder]:
        """Newest term first -- the one they are in is the one they want."""
        counts = (
            select(Subject.term_id.label("term_id"), func.count(Resource.id).label("n"))
            .join(Resource, Resource.subject_id == Subject.id)
            .where(Subject.user_id == user_id, Resource.status == "ready")
            .group_by(Subject.term_id)
            .subquery()
        )
        rows = self._db.execute(
            select(Term, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.term_id == Term.id)
            .where(Term.user_id == user_id)
            .order_by(Term.is_active.desc(), Term.sequence_no.desc())
        ).all()
        return [TermFolder(term=term, count=count) for term, count in rows]

    def term(self, user_id: int, term_id: int) -> Term | None:
        return self._db.scalar(
            select(Term).where(Term.id == term_id, Term.user_id == user_id)
        )

    def subjects(self, user_id: int, term_id: int) -> list[SubjectFolder]:
        counts = (
            select(Resource.subject_id.label("subject_id"), func.count(Resource.id).label("n"))
            .where(Resource.user_id == user_id, Resource.status == "ready")
            .group_by(Resource.subject_id)
            .subquery()
        )
        rows = self._db.execute(
            select(Subject, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.subject_id == Subject.id)
            .where(Subject.user_id == user_id, Subject.term_id == term_id)
            .order_by(Subject.name)
        ).all()
        return [SubjectFolder(subject=subject, count=count) for subject, count in rows]

    def subject(self, user_id: int, subject_id: int) -> Subject | None:
        return self._db.scalar(
            select(Subject).where(Subject.id == subject_id, Subject.user_id == user_id)
        )

    def shelves(self, user_id: int, subject_id: int) -> list[Shelf]:
        """All three, always, including the empty ones.

        A shelf that disappears when it is empty makes the tree a different
        shape in every subject, and a student cannot learn where a thing lives
        if where it lives depends on what is already there. The blurb is what
        makes an empty one explain itself instead of looking broken.
        """
        rows = self._db.execute(
            select(Resource.category, func.count(Resource.id))
            .where(
                Resource.user_id == user_id,
                Resource.subject_id == subject_id,
                Resource.status == "ready",
            )
            .group_by(Resource.category)
        ).all()
        # NULL is not a fourth shelf: nobody named one, which reads as the default.
        tally: dict[str, int] = dict.fromkeys(categories.CATEGORIES, 0)
        for key, count in rows:
            tally[categories.normalise(key)] += count
        return [
            Shelf(
                key=key,
                label=categories.LABELS[key],
                blurb=categories.BLURBS[key],
                count=tally[key],
            )
            for key in categories.CATEGORIES
        ]

    # --- the leaves -------------------------------------------------------

    def _newest_first(
        self, query: Select[Resource], limit: int | None = None
    ) -> list[Resource]:
        """Newest first, because that is what a student means by "my notes"."""
        ordered = query.order_by(Resource.created_at.desc(), Resource.id.desc())
        return list(self._db.scalars(ordered.limit(limit) if limit else ordered))

    def on_shelf(self, user_id: int, subject_id: int, category: str) -> list[Resource]:
        shelf = categories.normalise(category)
        query = select(Resource).where(
            Resource.user_id == user_id,
            Resource.subject_id == subject_id,
            Resource.status == "ready",
        )
        if shelf == categories.DEFAULT:
            # The default shelf also holds everything filed before anyone was
            # asked which shelf it belonged on, which is what NULL means here.
            query = query.where(
                (Resource.category == shelf) | (Resource.category.is_(None))
            )
        else:
            query = query.where(Resource.category == shelf)
        return self._newest_first(query)

    def term_admin(self, user_id: int, term_id: int) -> list[Resource]:
        """The term's Info & Admin shelves, gathered into one drawer.

        The sketch asks for an "ADMIN / SEM INFO" folder beside the subjects,
        and the schema cannot express one: a resource hangs off a subject and a
        subject hangs off a term, so nothing belongs to a term alone. Rather
        than add ``resources.term_id`` for a folder, this is a **cross-section**
        -- the same rows, gathered by what they are instead of by which subject
        they came in under. A timetable filed under DBMS is still a timetable.

        Nothing moves and nothing is duplicated: each file remains on its own
        subject's shelf, and this is a second way to reach it.
        """
        return self._newest_first(
            select(Resource)
            .join(Subject, Resource.subject_id == Subject.id)
            .where(
                Resource.user_id == user_id,
                Subject.term_id == term_id,
                Resource.status == "ready",
                Resource.category == categories.INFO,
            )
        )

    def unfiled(self, user_id: int) -> list[Resource]:
        """Everything with no subject: the sketch's MISCELLANEOUS drawer.

        These are the files the assistant asked about once and was never told
        about. They are not lost and they are not hidden -- one ask per ticket
        is the rule in chat, and this is where the ones nobody answered live.
        """
        return self._newest_first(
            select(Resource).where(
                Resource.user_id == user_id, Resource.subject_id.is_(None)
            )
        )

    def resource(self, user_id: int, resource_id: int) -> Resource | None:
        return self._db.scalar(
            select(Resource).where(Resource.id == resource_id, Resource.user_id == user_id)
        )

    def recent(self, user_id: int, limit: int) -> list[Resource]:
        """What landed lately, for the front page. Filed or not."""
        return self._newest_first(
            select(Resource).where(Resource.user_id == user_id), limit=limit
        )

    def upcoming_deadlines(self, user_id: int, now: datetime, limit: int = 50) -> list[Deadline]:
        """Soonest first, and only what is still ahead.

        Past deadlines are dropped rather than archived: a deadline you can no
        longer meet is not information, it is clutter on the one page a student
        opens to find out what is urgent.
        """
        return list(
            self._db.scalars(
                select(Deadline)
                .where(Deadline.user_id == user_id, Deadline.due_at >= now)
                .order_by(Deadline.due_at, Deadline.id)
                .limit(limit)
            )
        )

    def search(self, user_id: int, terms: list[str], limit: int) -> list[Resource]:
        """The same matcher the assistant uses, with room to show the answer.

        Every term must appear somewhere -- title, body or filename -- rather
        than any. The filename counts because an uncaptioned forward has
        nothing else: its title is empty, and "DBMS_Unit2.pdf" is the only name
        the student knows.

        Pending rows are excluded: a file we could not file is one we cannot
        honestly claim to have found.
        """
        query = select(Resource).where(
            Resource.user_id == user_id, Resource.status == "ready"
        )
        for term in terms:
            if not term.strip():
                continue
            pattern = f"%{term.strip()}%"
            query = query.where(
                or_(
                    Resource.title.ilike(pattern),
                    Resource.body.ilike(pattern),
                    Resource.filename.ilike(pattern),
                )
            )
        return self._newest_first(query, limit=limit)

    def counts(self, user_id: int, now: datetime) -> tuple[int, int]:
        """Files kept and deadlines still ahead -- the two numbers on the header."""
        files = (
            self._db.scalar(
                select(func.count(Resource.id)).where(Resource.user_id == user_id)
            )
            or 0
        )
        due = (
            self._db.scalar(
                select(func.count(Deadline.id)).where(
                    Deadline.user_id == user_id, Deadline.due_at >= now
                )
            )
            or 0
        )
        return files, due
