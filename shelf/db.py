"""One engine, one sessionmaker, one request-scoped session.

SHELF is a reader. It writes exactly two things -- a login link row and a
session's ``last_seen_at`` -- and nothing else in this service has a reason to
hold a transaction open. So the pool is small and connections are returned
quickly, which is the opposite of the bot's problem: there, a turn holds its
connection across an LLM call and the ceiling is concurrent students.

``get_db`` deliberately does **not** commit. The two places that write say so
themselves, which keeps "this request changed something" visible in the route
rather than implied by the dependency.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shelf.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
    echo=settings.sql_echo,
    # A failed statement's error text would otherwise carry its bound values,
    # and those values are a student's files and phone number.
    hide_parameters=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
