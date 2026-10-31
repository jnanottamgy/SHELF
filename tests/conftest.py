"""The harness.

Two things make this different from an ordinary FastAPI test setup, and both
follow from SHELF not owning its schema.

**The schema comes from a snapshot, not from ``create_all()``.** Creating
tables from ``shelf/models/`` would test the mirror against itself: every
drift between these classes and the real database would be invisible, because
both sides of the comparison would be the same file. ``schema/`` holds a dump
of what the assistant's migration chain actually produces, and that is what
the test database is built from.

**Savepoint rollback per test.** ``db_session`` binds ``SessionLocal`` to an
open connection with ``join_transaction_mode="create_savepoint"`` and rolls the
outer transaction back in teardown, so code under test can commit for real and
still leave no trace. That is what makes it safe to test a function whose
entire job is committing -- and every write in this service is one.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from shelf import sessions
from shelf.config import settings
from shelf.db import SessionLocal, get_db
from shelf.google import FakeGoogle, GoogleIdentity, get_google
from shelf.models import User
from shelf.storage import MemoryStore, get_store

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "academic-assistant.sql"

TEST_DB_NAME = "shelf_test"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# The assistant's docker-compose server; where tests go when the dev URL is hosted.
COMPOSE_TEST_DATABASE_URL = (
    f"postgresql+psycopg://academic_assistant:dev_password@localhost:5432/{TEST_DB_NAME}"
)

PHONE = "919380651594"
SIGNUP = datetime(2026, 1, 1, tzinfo=UTC)


def _swap_database_name(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _derive_test_database_url(env_url: str | None, dev_url: str) -> str:
    """TEST_DATABASE_URL if set, else the dev server only when it is local, else docker-compose.

    The harness drops and recreates its database, so following a hosted dev URL
    (Neon, Render) would do that on a server we never meant to touch, ~50ms a query away.
    """
    if env_url:
        return env_url
    if urlsplit(dev_url).hostname in LOCAL_HOSTS:
        return _swap_database_name(dev_url, TEST_DB_NAME)
    return COMPOSE_TEST_DATABASE_URL


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """A scratch database on a local server, so the real one is untouched."""
    return _derive_test_database_url(os.environ.get("TEST_DATABASE_URL"), settings.database_url)


@pytest.fixture(scope="session")
def test_engine(test_database_url: str) -> Iterator[Engine]:
    # The admin connection goes to the test server, never the dev one.
    admin = create_engine(
        _swap_database_name(test_database_url, "postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        name = urlsplit(test_database_url).path.lstrip("/")
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    engine = create_engine(test_database_url, hide_parameters=True)
    with engine.begin() as connection:
        # The real schema, not one derived from the models under test.
        connection.execute(text(SCHEMA.read_text()))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()  # undoes everything, including real commits
        connection.close()


def make_student(
    db: Session,
    ph_no: str = PHONE,
    *,
    paid_until: datetime | None = None,
    google_sub: str | None = None,
    email: str | None = None,
) -> User:
    """Authorise a number the way the payment site does.

    Neither the assistant nor SHELF creates users -- the payment site is the
    only writer -- so a test has to say explicitly that somebody paid.
    """
    user = User(
        ph_no=ph_no,
        created_at=SIGNUP,
        google_sub=google_sub,
        email=email,
        paid_until=paid_until or datetime.now(tz=UTC) + timedelta(days=30),
    )
    db.add(user)
    db.commit()
    return user


def issue_link(db: Session, user_id: int, now: datetime) -> str:
    """Stand in for the assistant, which is the only thing that mints links.

    Deliberately written out here rather than imported: SHELF does not have
    this function, and it must not grow one. The service exposed to the open
    internet cannot be the one that can create a credential.
    """
    from shelf.models import WebSession

    token = sessions._new_id()
    db.add(
        WebSession(
            id=token, user_id=user_id, created_at=now, last_seen_at=now, activated_at=None
        )
    )
    db.commit()
    return token


@pytest.fixture(autouse=True)
def _fresh_throttle() -> Iterator[None]:
    """The claim throttle is process-global, like the debounce registries.

    Without this the first few tests use up the window and every later one
    posting a number gets the "too many attempts" page instead of the thing it
    is testing — a failure that looks like the feature is broken and is really
    the harness leaking state between tests.
    """
    from shelf import throttle

    throttle.claims = throttle.Throttle(
        limit=throttle.claims._limit, window_seconds=throttle.claims._window
    )
    yield


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def google() -> FakeGoogle:
    """Stands in for Google. Still checks the nonce, so a route that forgets
    to thread it through fails here rather than in production."""
    return FakeGoogle()


@pytest.fixture
def web(db_session: Session, store: MemoryStore, google: FakeGoogle) -> Iterator[TestClient]:
    """The real app with only Postgres and object storage substituted.

    ``follow_redirects=False`` because the redirect *is* the behaviour under
    test in half of these: getting the one-time token out of the address bar.
    """
    from shelf.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_google] = lambda: google
    try:
        with TestClient(app, follow_redirects=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def sign_in_with_google(
    web: TestClient, google: FakeGoogle, *, sub: str = "google-sub-1", email: str = "asha@gmail.com"
) -> None:
    """Put a signed-in Google visitor in the cookie, the way the real flow does.

    Deliberately driven through the real routes rather than by writing the
    cookie directly: a test that forges the session would keep passing if the
    callback stopped setting it.
    """
    google.identity_to_return = GoogleIdentity(sub=sub, email=email, name="Asha Rao", picture="")
    started = web.get("/auth/google")
    state = str(started.headers["location"]).split("state=")[1].split("&")[0]
    web.get(f"/auth/google/callback?code=ok&state={state}")


def sign_in(web: TestClient, db: Session, user: User) -> str:
    """Do what the assistant does, then what the student does with the link."""
    token = issue_link(db, user.id, datetime.now(tz=UTC))
    response = web.get(f"/f/{token}")
    assert response.status_code == 303
    return token
