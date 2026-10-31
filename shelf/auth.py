"""The two routes Google sign-in is made of, and the one that ends it.

``/auth/google`` starts a sign-in; ``/auth/google/callback`` finishes it;
``/signout`` throws it all away. Everything that decides *whether a token is
real* lives in ``shelf.google``; what lives here is what the browser is
allowed to do with the answer.

**Signing in is not the same as being allowed in.** The callback ends in one
of exactly two places, and which one is decided by the database, never by the
token:

- Google's ``sub`` matches a ``users`` row that is entitled (grace included)
  -> a real ``web_sessions`` row, and ``/files``.
- anything else -> the Google details go in the signed cookie and they land on
  the welcome page with Buy now.

A student who paid, lapsed, and signs in again is the second case. They are a
real person with a real Google account and no current subscription, and the
page that serves them is the pitch, not the dashboard.

**There is no ``next`` parameter**, deliberately. Every redirect target here is
a literal written in this file. An open redirect on a sign-in route is how a
phishing page borrows your domain for its address bar.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from shelf import sessions, visitor
from shelf.config import settings
from shelf.db import DbSession
from shelf.google import GoogleDep, GoogleError, new_secret
from shelf.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

#: Where Google sends them back. Must equal the redirect URI registered in the
#: Google Cloud console exactly, including scheme and trailing path.
CALLBACK_PATH = "/auth/google/callback"


def _redirect_uri(request: Request) -> str:
    """Built from SITE_URL when we have one, so a proxy cannot rewrite it.

    Google compares this against a registered list, and the token exchange
    sends it a second time; if a forwarded Host header could change it, a
    misconfigured proxy would silently break sign-in -- or point it elsewhere.
    """
    if settings.site_url:
        return f"{settings.site_url.rstrip('/')}{CALLBACK_PATH}"
    return str(request.url_for("google_callback"))


def _cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        sessions.COOKIE,
        session_id,
        max_age=settings.web_session_days * 86400,
        httponly=True,  # script must never be able to read it
        samesite="lax",
        secure=settings.app_env == "prod",  # plain http in dev, or it never sets
        path="/",
    )


@router.get("/auth/google")
def google_start(request: Request, google: GoogleDep) -> Response:
    """Send them to Google, having first written down that we did.

    ``state`` and ``nonce`` are generated here and stored in the signed cookie.
    Both are required back. See ``shelf.google`` for what each one refuses.
    """
    state, nonce = new_secret(), new_secret()
    visitor.start_oauth(request, state, nonce)
    url = google.authorization_url(
        redirect_uri=_redirect_uri(request), state=state, nonce=nonce
    )
    # 303: this is a GET that must stay a GET.
    return RedirectResponse(url=url, status_code=303)


@router.get(CALLBACK_PATH, name="google_callback")
def google_callback(
    request: Request,
    db: DbSession,
    google: GoogleDep,
    code: str = "",
    state: str = "",
) -> Response:
    """Google's answer. Every failure lands on the welcome page, never an error.

    The expected ``state``/``nonce`` are **consumed first and unconditionally**,
    before anything is checked. A failed attempt must not leave a reusable one
    behind: that would turn a single intercepted callback into a replayable one.
    """
    expected_state, nonce = visitor.take_oauth(request)

    if not code or not state or not expected_state or state != expected_state:
        # One message for all four. Telling a caller which half of their guess
        # was wrong is free information they did not have.
        logger.info("rejected a google callback that did not match a sign-in we started")
        return _back_to_welcome()

    if nonce is None:
        logger.info("rejected a google callback with no nonce on record")
        return _back_to_welcome()

    try:
        identity = google.identity(code=code, redirect_uri=_redirect_uri(request), nonce=nonce)
    except GoogleError:
        # Never the exception text: it can quote the token.
        logger.info("google sign-in did not complete")
        return _back_to_welcome()

    visitor.remember_google(
        request,
        sub=identity.sub,
        email=identity.email,
        name=identity.name,
        picture=identity.picture,
    )

    now = datetime.now(tz=UTC)
    student = db.scalar(select(User).where(User.google_sub == identity.sub))
    if student is None or not _entitled(student, now):
        # Known face, no subscription. The pitch, not the dashboard.
        return RedirectResponse(url="/", status_code=303)

    session_id = sessions.sign_in(db, student.id, now)
    db.commit()
    response = RedirectResponse(url="/files", status_code=303)
    _cookie(response, session_id)
    return response


def _entitled(student: User, now: datetime) -> bool:
    """The same rule the file browser re-checks on every request."""
    return student.is_entitled(now) or student.is_in_grace(
        now, timedelta(days=settings.grace_days)
    )


def _back_to_welcome() -> RedirectResponse:
    return RedirectResponse(url="/?signin=failed", status_code=303)


@router.post("/auth/signout")
def signout(request: Request, db: DbSession) -> Response:
    """Drop both halves: the database session and everything in the cookie.

    POST, not GET, so a third-party page cannot sign a student out by
    embedding an image. Harmless as attacks go, infuriating as an experience.
    """
    sessions.sign_out(db, request.cookies.get(sessions.COOKIE))
    db.commit()
    visitor.forget(request)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(sessions.COOKIE, path="/")
    return response
