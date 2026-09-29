"""The SHELF service.

Its own process and its own repository, separate from the assistant for one
reason: the assistant must run **exactly one instance**, because its debounce
timers live in memory and a second instance would answer every student twice.
Nothing a browser talks to should inherit that constraint. SHELF holds no
in-process state at all -- sessions are rows -- so it scales out and restarts
freely.

What it does not own is the schema. Migrations run in the Academic Assistant
repository and nowhere else; two services racing ``alembic upgrade head``
against one database is how a half-applied schema happens.
"""

import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text

from shelf.db import DbSession
from shelf.logging_config import configure_logging
from shelf.router import NotSignedIn, entry, not_signed_in
from shelf.router import router as files_router

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="SHELF", docs_url=None, redoc_url=None)
app.include_router(files_router)
app.include_router(entry)
app.add_exception_handler(NotSignedIn, not_signed_in)
logger.info("shelf starting")


@app.get("/", include_in_schema=False)
def root() -> Response:
    """One canonical home. The paths keep their /files prefix because the
    links the assistant has already sent point at them."""
    return RedirectResponse(url="/files", status_code=307)


@app.get("/health")
def health() -> dict[str, str]:
    """Cheap by design: no database, so a monitor can hit it constantly."""
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: DbSession) -> dict[str, str]:
    """The one that proves the database. For a human to call, not a monitor."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
