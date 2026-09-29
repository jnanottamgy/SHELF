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

from shelf import legal
from shelf.db import DbSession
from shelf.logging_config import configure_logging
from shelf.router import NotSignedIn, entry, not_signed_in
from shelf.router import router as files_router
from shelf.site import not_found
from shelf.site import router as site_router

configure_logging()

# Refuses to start a production site whose policy pages would name nobody.
legal.check()

logger = logging.getLogger(__name__)

app = FastAPI(title="SHELF", docs_url=None, redoc_url=None)
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
