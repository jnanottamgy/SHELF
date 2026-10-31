"""The SHELF service.

Its own process and its own repository, separate from the assistant for one
reason: the assistant must run **exactly one instance**, because its debounce
timers live in memory and a second instance would answer every student twice.
Nothing a browser talks to should inherit that constraint. SHELF holds no
in-process state at all -- sessions are rows -- so it scales out and restarts
freely.

The policy pages live here too -- terms, privacy, refunds, cancellation and
contact. They belong wherever a customer and a payment aggregator will look
for them, which is the public site, and ``shelf/legal.py`` refuses to start a
production process while any of them would render a bracket instead of a name.

What it does not own is the schema. Migrations run in the Academic Assistant
repository and nowhere else; two services racing ``alembic upgrade head``
against one database is how a half-applied schema happens.
"""

import logging

from fastapi import FastAPI
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from shelf import legal, preflight
from shelf.auth import router as auth_router
from shelf.config import settings
from shelf.db import DbSession
from shelf.logging_config import configure_logging
from shelf.router import NotSignedIn, entry, not_signed_in
from shelf.router import router as files_router
from shelf.site import not_found
from shelf.site import router as site_router

configure_logging()

# Refuses to start a production site whose policy pages would name nobody.
legal.check()
# ...or one that would sign its visitor cookies with the placeholder below.
preflight.check()

logger = logging.getLogger(__name__)

app = FastAPI(title="SHELF", docs_url=None, redoc_url=None)

# The pre-payment identity: who Google says a visitor is and which number they
# proved, signed rather than stored. Nothing in it is a permission -- every
# entitlement is still read from `users` on the request that needs it -- so the
# worst a forged cookie buys is a pitch page. See shelf/visitor.py.
#
# samesite=lax rather than strict is load-bearing, not a default left alone:
# Google's callback is a top-level cross-site GET, and a strict cookie is not
# sent on it, so the state we are about to compare against would be missing
# from every single sign-in.
app.add_middleware(
    SessionMiddleware,
    secret_key=preflight.session_secret(),
    session_cookie="shelf_visitor",
    max_age=settings.verified_phone_days * 86400,
    same_site="lax",
    https_only=settings.app_env == "prod",
)

app.include_router(auth_router)
app.include_router(site_router)
app.include_router(files_router)
app.include_router(entry)
app.add_exception_handler(NotSignedIn, not_signed_in)
app.add_exception_handler(404, not_found)
logger.info("shelf starting")


@app.get("/health")
def health() -> dict[str, str]:
    """Cheap by design: no database, so a monitor can hit it constantly."""
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: DbSession) -> dict[str, str]:
    """The one that proves the database. For a human to call, not a monitor."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
