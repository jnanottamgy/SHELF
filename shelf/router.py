"""SHELF's routes: a student's own drive, read-only.

Its whole purpose is to answer the retrieval questions that would otherwise be
WhatsApp messages. Every file a student finds here is a turn the assistant did
not have to take, an LLM call not made, and -- from October 2026, when Meta
prices service messages per message -- a charge not incurred. That is why
browsing is worth serving even though the assistant can already find things:
asking costs money and a round trip, looking costs neither.

**Read-only on purpose.** Nothing here files, renames, deletes or uploads. The
product's promise is that talking to it is enough, and a second way to change
the same rows would be a second set of rules about what is allowed -- with the
gates, the tickets and the one-reply invariant all living in the assistant.
SHELF shows; the chat decides. It is also why this service holds no Meta token
and no LLM key: a process that cannot reach them cannot leak them.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shelf import categories, sessions
from shelf.config import settings
from shelf.db import DbSession
from shelf.library import IST, LibraryRepository
from shelf.models import Resource, User
from shelf.naming import filename_for
from shelf.storage import StorageError, StoreDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
# A second router, off the /files prefix, so the link the assistant sends is as
# short as it can be -- it is read off a phone screen and sometimes retyped.
entry = APIRouter(tags=["files"])

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: How many recent files the front page shows before it stops being a summary.
RECENT = 6
#: Results one search returns. Higher than the bot's ten: a page can show a list
#: without the length itself being the problem.
SEARCH_LIMIT = 60

#: Rendered inline instead of downloaded. Everything else is handed over as a
#: file, because guessing wrong means a browser rendering a PDF as text.
INLINE_TYPES = ("image/", "application/pdf", "text/plain")


class NotSignedIn(Exception):
    """Raised by the dependency; the app turns it into the sign-in page."""


def current_student(request: Request, db: DbSession) -> User:
    """Resolve the cookie on **every** request, and re-check entitlement there.

    Doing this per request rather than once at sign-in is the entire reason
    sessions are rows. A student whose subscription ends at noon loses the
    website at noon; with a signed token they would keep it until the token
    felt like expiring, and the row saying they had lapsed would sit there
    being ignored.
    """
    student = sessions.resolve(db, request.cookies.get(sessions.COOKIE), datetime.now(tz=UTC))
    if student is None:
        raise NotSignedIn
    # last_seen_at moved, and the device cap is ordered by it.
    db.commit()
    return student


Student = Annotated[User, Depends(current_student)]


def _page(request: Request, name: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context=context)


# --- getting in ----------------------------------------------------------------


@entry.get("/f/{token}", response_class=HTMLResponse)
def enter(request: Request, token: str, db: DbSession) -> Response:
    """Spend the one-time link and hand the browser a cookie.

    The redirect matters as much as the cookie: it gets the token out of the
    address bar, so it is not sitting in history, in a screenshot, or in the
    referrer of the next request.
    """
    session_id = sessions.activate(db, token, datetime.now(tz=UTC))
    if session_id is None:
        db.commit()  # an expired link deletes itself; keep that
        return _page(
            request,
            "gate.html",
            headline="That link has expired",
            message=(
                "Login links last a few minutes and work once, which is what "
                "keeps an old message in your chat from being a spare key."
            ),
            hint="Ask the assistant for a new one — just say “my files”.",
            whatsapp_url=settings.whatsapp_chat_url,
        )

    db.commit()
    response = RedirectResponse(url="/files", status_code=303)
    response.set_cookie(
        sessions.COOKIE,
        session_id,
        max_age=settings.web_session_days * 86400,
        httponly=True,  # script must never be able to read it
        samesite="lax",
        secure=settings.app_env == "prod",  # plain http in dev, or it never sets
        path="/",
    )
    return response


@router.post("/signout")
def signout(request: Request, db: DbSession) -> Response:
    sessions.sign_out(db, request.cookies.get(sessions.COOKIE))
    db.commit()
    response = RedirectResponse(url="/files", status_code=303)
    response.delete_cookie(sessions.COOKIE, path="/")
    return response


# --- the tree ------------------------------------------------------------------


def _shell(request: Request, db: DbSession, student: User, **context: object) -> dict[str, object]:
    """What every page needs: the counts in the header and the search box state."""
    now = datetime.now(tz=UTC)
    files, due = LibraryRepository(db).counts(student.id, now)
    return {"file_count": files, "due_count": due, "query": "", **context}


@router.get("", response_class=HTMLResponse)
def home(request: Request, db: DbSession, student: Student) -> HTMLResponse:
    library = LibraryRepository(db)
    return _page(
        request,
        "home.html",
        **_shell(
            request,
            db,
            student,
            terms=library.terms(student.id),
            unfiled=library.unfiled(student.id),
            recent=library.recent(student.id, RECENT),
        ),
    )


@router.get("/t/{term_id}", response_class=HTMLResponse)
def term(request: Request, term_id: int, db: DbSession, student: Student) -> Response:
    library = LibraryRepository(db)
    found = library.term(student.id, term_id)
    if found is None:
        return _missing(request, db, student)
    return _page(
        request,
        "term.html",
        **_shell(
            request, db, student, term=found, subjects=library.subjects(student.id, term_id)
        ),
    )


@router.get("/s/{subject_id}", response_class=HTMLResponse)
def subject(request: Request, subject_id: int, db: DbSession, student: Student) -> Response:
    library = LibraryRepository(db)
    found = library.subject(student.id, subject_id)
    if found is None:
        return _missing(request, db, student)
    return _page(
        request,
        "subject.html",
        **_shell(
            request,
            db,
            student,
            subject=found,
            term=library.term(student.id, found.term_id),
            shelves=library.shelves(student.id, subject_id),
        ),
    )


@router.get("/s/{subject_id}/{shelf}", response_class=HTMLResponse)
def shelf(
    request: Request, subject_id: int, shelf: str, db: DbSession, student: Student
) -> Response:
    library = LibraryRepository(db)
    found = library.subject(student.id, subject_id)
    if found is None:
        return _missing(request, db, student)
    key = categories.normalise(shelf)
    return _page(
        request,
        "files.html",
        **_shell(
            request,
            db,
            student,
            heading=categories.LABELS[key],
            blurb=categories.BLURBS[key],
            subject=found,
            term=library.term(student.id, found.term_id),
            shelf_key=key,
            files=library.on_shelf(student.id, subject_id, key),
            empty=(
                "Nothing on this shelf yet. Forward a file to the assistant "
                "and say what it is — it lands here."
            ),
        ),
    )


@router.get("/t/{term_id}/admin", response_class=HTMLResponse)
def term_admin(request: Request, term_id: int, db: DbSession, student: Student) -> Response:
    """The sketch's per-term admin drawer, as a cross-section rather than a move.

    A syllabus or a timetable is filed under whichever subject it arrived with,
    which is right -- but at the start of a term it is the *term's* admin a
    student is looking for, not one subject's. Same rows, gathered differently.
    """
    library = LibraryRepository(db)
    found = library.term(student.id, term_id)
    if found is None:
        return _missing(request, db, student)
    return _page(
        request,
        "files.html",
        **_shell(
            request,
            db,
            student,
            heading="Admin & Sem Info",
            blurb=(
                "Everything on an Info & Admin shelf this semester. Each file "
                "still lives under its own subject."
            ),
            term=found,
            shelf_key=categories.INFO,
            files=library.term_admin(student.id, term_id),
            empty=(
                "Nothing yet. Syllabus, timetables and circulars land here once "
                "you send them — say what they are and they file themselves."
            ),
        ),
    )


@router.get("/misc", response_class=HTMLResponse)
def miscellaneous(request: Request, db: DbSession, student: Student) -> HTMLResponse:
    """Files the assistant asked about once and was never told about.

    One ask per ticket is the rule in chat, so these would otherwise be
    invisible. Here they are simply a drawer, which is what they always were.
    """
    return _page(
        request,
        "files.html",
        **_shell(
            request,
            db,
            student,
            heading="Miscellaneous",
            blurb="Sent without a subject, so they were never filed",
            files=LibraryRepository(db).unfiled(student.id),
            empty="Nothing unfiled. Everything you have sent found a subject.",
        ),
    )


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, db: DbSession, student: Student, q: str = "") -> HTMLResponse:
    """The same matcher the assistant uses, with room to show the whole answer.

    In chat, more matches than fit is a problem -- a list the student has to
    read back. Here it is just a longer page, which is the point: the website
    is where "all my DBMS notes" stops being an awkward question.
    """
    terms = [word for word in q.split() if len(word) > 2]
    found = (
        LibraryRepository(db).search(student.id, terms, limit=SEARCH_LIMIT)
        if terms
        else []
    )
    return _page(
        request,
        "files.html",
        **_shell(
            request,
            db,
            student,
            heading="Search",
            blurb=f"{len(found)} match{'' if len(found) == 1 else 'es'} for “{q}”" if q else "",
            files=found,
            query=q,
            empty=(
                "Nothing matched. Try fewer words — the assistant searches what "
                "you called it, not what the file was named."
                if q
                else "Type something to search everything you have saved."
            ),
        ),
    )


@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(request: Request, db: DbSession, student: Student) -> HTMLResponse:
    now = datetime.now(tz=UTC)
    library = LibraryRepository(db)
    upcoming = library.upcoming_deadlines(student.id, now, limit=50)
    subjects = {
        folder.subject.id: folder.subject.name
        for term_folder in library.terms(student.id)
        for folder in library.subjects(student.id, term_folder.term.id)
    }
    return _page(
        request,
        "deadlines.html",
        **_shell(
            request,
            db,
            student,
            deadlines=[
                {
                    "title": deadline.title,
                    "subject": subjects.get(deadline.subject_id or -1),
                    # Back into the student's own timezone before they read it.
                    "due": deadline.due_at.astimezone(IST),
                    # Calendar days in IST, not a truncated timedelta. Something
                    # due at 9am tomorrow is 23 hours away, which .days rounds
                    # to 0 -- and a page that says "today" next to tomorrow's
                    # date is worse than one that says nothing.
                    "days": (
                        deadline.due_at.astimezone(IST).date()
                        - now.astimezone(IST).date()
                    ).days,
                }
                for deadline in upcoming
            ],
        ),
    )


# --- the leaves ----------------------------------------------------------------


@router.get("/r/{resource_id}")
def open_file(
    request: Request, resource_id: int, db: DbSession, student: Student, store: StoreDep
) -> Response:
    """Hand over one file, or show a note.

    Served through the app rather than as a signed R2 URL, deliberately. A
    signed URL is issued once and cannot be withdrawn: it would still open
    after a subscription lapsed, after a device was evicted, and after a DPDP
    erasure had removed every row that mentioned it. Proxying keeps the same
    check on every byte handed over, and R2's egress to us is free, so what it
    costs is one request's worth of the site's own bandwidth.
    """
    found = LibraryRepository(db).resource(student.id, resource_id)
    if found is None:
        return _missing(request, db, student)

    if not found.storage_ref:
        return _page(request, "note.html", **_shell(request, db, student, note=found))

    try:
        payload = store.get(found.storage_ref)
    except StorageError:
        logger.exception("could not read a stored object for the website")
        return _page(
            request,
            "gate.html",
            headline="That file wouldn't open",
            message="It is still listed, but the copy behind it could not be read.",
            hint="Ask the assistant for it — it may still be able to send it.",
            whatsapp_url=settings.whatsapp_chat_url,
        )

    name = filename_for(found)
    mime = found.mime_type or "application/octet-stream"
    inline = any(mime.startswith(kind) for kind in INLINE_TYPES)
    return Response(
        content=payload,
        media_type=mime,
        headers={
            # RFC 5987: the plain filename= is ASCII-only and a student's own
            # words are routinely not, so both forms go out.
            "Content-Disposition": (
                f"{'inline' if inline else 'attachment'}; "
                f'filename="{name.encode("ascii", "ignore").decode() or "file"}"; '
                f"filename*=UTF-8''{quote(name)}"
            ),
            "Cache-Control": "private, max-age=0, no-store",
        },
    )


def _missing(request: Request, db: DbSession, student: User) -> HTMLResponse:
    """One answer for "not yours" and "not there".

    Telling the two apart would confirm that somebody else's file exists, which
    is the only thing an id in a URL could otherwise be used to learn.
    """
    return _page(
        request,
        "gate.html",
        headline="Not found",
        message="That isn't one of yours, or it isn't here any more.",
        hint="",
        whatsapp_url=settings.whatsapp_chat_url,
        back=True,
    )


def label(resource: Resource) -> str:
    """What the student called it, falling back to what it was called.

    Chat shows both on one line because a WhatsApp reply has one line to spend.
    A page has two, so the caption leads and the filename drops to the detail
    line -- on a phone the combined form was truncated mid-filename, which hid
    the half that identifies an uncaptioned forward.
    """
    return resource.title or resource.filename or "Untitled"


templates.env.filters["label"] = label


def not_signed_in(request: Request, _: Exception) -> Response:
    """The sign-in page, which is not a form.

    There is nothing to type. The only thing anyone can prove about a ``users``
    row is possession of the phone it names, so the way in is to ask in the
    chat that already proves it. A number field here would prove nothing and
    would teach students to type their number into pages that ask.
    """
    return _page(
        request,
        "gate.html",
        headline="Ask the assistant for your link",
        message=(
            "Your files are opened with a one-time link the assistant sends you "
            "on WhatsApp. There is no password here, because the only thing "
            "worth proving is that you have your phone."
        ),
        hint="Message the assistant and say “my files”.",
        whatsapp_url=settings.whatsapp_chat_url,
    )
