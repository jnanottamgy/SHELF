"""Spending a login link, and keeping the session it becomes revocable.

**SHELF does not issue links.** The assistant does, in reply to a student
asking for one in the chat that already proves they hold the phone. This
service only ever *spends* a link and manages what it becomes. That split is
the security property, not an accident of layering: the only process that can
create a credential here is the one that can already verify identity, and this
one -- the one exposed to the open internet -- cannot mint anything.

Both services write ``web_sessions``, with disjoint operations: the assistant
inserts unopened links, SHELF activates, touches and deletes. Same shape of
coupling as the payment site writing ``users``, and for the same reason -- the
table is the contract.

Opening a link **rotates** it: the row the token named is deleted and a fresh
one with a new secret is written in its place. That is what makes the link
single-use, and it is why there is no table recording spent tokens -- the id
that travelled through WhatsApp simply stops existing. A chat message is
forever, so the link inside it must not be.

Sessions are rows rather than signed tokens because access has to be
withdrawable: a lapse, a device eviction or a DPDP erasure must take effect on
the next request, not when a token happens to expire.
"""

import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shelf.config import settings
from shelf.models import User, WebSession

logger = logging.getLogger(__name__)

#: Cookie holding an activated session id. HttpOnly, so script cannot read it.
COOKIE = "aa_session"

#: Must match the assistant's token width, since it mints the links this spends.
_TOKEN_BYTES = 32


def _new_id() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def activate(db: Session, token: str, now: datetime) -> str | None:
    """Spend a link and return the session id to put in the cookie.

    None when the token is unknown, already spent, or too old -- deliberately
    one answer for all three, because distinguishing them tells a guesser which
    half of their guess was right.
    """
    row = db.get(WebSession, token)
    if row is None or row.activated_at is not None:
        return None

    if row.created_at + timedelta(minutes=settings.web_link_minutes) <= now:
        # Expired links are removed rather than left to accumulate; nothing
        # else ever sweeps them, and they are the one row here with no owner
        # watching over it.
        db.delete(row)
        logger.info("web login link had expired", extra={"user_id": row.user_id})
        return None

    user_id = row.user_id
    db.delete(row)
    db.flush()  # the rotation is a delete and an insert, in that order

    session_id = _new_id()
    db.add(
        WebSession(
            id=session_id, user_id=user_id, created_at=now, last_seen_at=now, activated_at=now
        )
    )
    db.flush()
    _enforce_device_cap(db, user_id, keep=session_id)
    logger.info("browser signed in", extra={"user_id": user_id})
    return session_id


def _enforce_device_cap(db: Session, user_id: int, *, keep: str) -> None:
    """Keep the newest ``web_devices`` browsers; evict by least recently seen.

    Evicting rather than refusing is the deliberate half. A student who has
    just replaced their phone is exactly the person who hits the cap, and
    telling them to sign out on a device they no longer own is not an answer
    they can act on.
    """
    signed_in = list(
        db.scalars(
            select(WebSession)
            .where(WebSession.user_id == user_id, WebSession.activated_at.is_not(None))
            .order_by(WebSession.last_seen_at.desc(), WebSession.activated_at.desc())
        )
    )
    for row in signed_in[settings.web_devices :]:
        if row.id == keep:
            continue
        db.delete(row)
        logger.info("evicted the least recently used browser", extra={"user_id": user_id})


def resolve(db: Session, session_id: str | None, now: datetime) -> User | None:
    """Turn a cookie into the student, or into nothing.

    Every gate is re-checked on every request -- the session exists, it has not
    gone idle, and the subscription still entitles them. That is the point of
    keeping sessions in the database: a student who lapses at noon loses the
    website at noon, not whenever a token they already hold runs out.

    Grace counts, exactly as it does in chat. Somebody whose renewal is a day
    late is still being answered by the assistant, and locking them out of
    their own files meanwhile would be an inconsistency they would reasonably
    read as broken. ``grace_days`` must therefore match the assistant's.
    """
    if not session_id:
        return None

    row = db.get(WebSession, session_id)
    if row is None or row.activated_at is None:
        return None

    if row.last_seen_at + timedelta(days=settings.web_session_days) <= now:
        db.delete(row)
        return None

    user = db.get(User, row.user_id)
    if user is None:
        return None
    if not user.is_entitled(now) and not user.is_in_grace(
        now, timedelta(days=settings.grace_days)
    ):
        return None

    row.last_seen_at = now
    return user


def sign_out(db: Session, session_id: str | None) -> None:
    """Drop this browser's row. Idempotent -- an unknown id is already signed out."""
    if not session_id:
        return
    row = db.get(WebSession, session_id)
    if row is not None:
        db.delete(row)
