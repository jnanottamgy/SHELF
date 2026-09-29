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

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from shelf import sessions
from shelf.config import settings
from shelf.db import SessionLocal, get_db
from shelf.models import User
from shelf.storage import MemoryStore, get_store

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "academic-assistant.sql"

PHONE = "919380651594"
SIGNUP = datetime(2026, 1, 1, tzinfo=UTC)


def _swap_database_name(url: str, name: str) -> str:
    head, _, _ = url.rpartition("/")
    return f"{head}/{name}"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Same server and credentials, name swapped, so the real one is untouched."""
    return _swap_database_name(settings.database_url, "shelf_test")


@pytest.fixture(scope="session")
def test_engine(test_database_url: str) -> Iterator[Engine]:
    admin = create_engine(
        _swap_database_name(settings.database_url, "postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS shelf_test"))
        connection.execute(text("CREATE DATABASE shelf_test"))
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
    db: Session, ph_no: str = PHONE, *, paid_until: datetime | None = None
) -> User:
    """Authorise a number the way the payment site does.

    Neither the assistant nor SHELF creates users -- the payment site is the
    only writer -- so a test has to say explicitly that somebody paid.
    """
    user = User(
        ph_no=ph_no,
        created_at=SIGNUP,
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


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def web(db_session: Session, store: MemoryStore) -> Iterator[TestClient]:
    """The real app with only Postgres and object storage substituted.

    ``follow_redirects=False`` because the redirect *is* the behaviour under
    test in half of these: getting the one-time token out of the address bar.
    """
    from shelf.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app, follow_redirects=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def sign_in(web: TestClient, db: Session, user: User) -> str:
    """Do what the assistant does, then what the student does with the link."""
    token = issue_link(db, user.id, datetime.now(tz=UTC))
    response = web.get(f"/f/{token}")
    assert response.status_code == 303
    return token
