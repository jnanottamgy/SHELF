"""A cap on how often one caller may claim a number.

``/verify`` is unauthenticated and writes a row. Nobody can be spammed by it --
the assistant never messages a claimed number, which is the whole direction of
the handshake -- and the sweep clears expired rows. What is unbounded is the
writing: one script can fill ``signup_codes`` with other people's numbers as
fast as it can post.

Deliberately in-process rather than in Postgres or Redis. Two reasons: a limit
that needs a round trip to the thing it protects is not much of a limit, and
this service is allowed to scale out, so a per-process cap is a *floor* on
protection rather than a promise. The honest framing is that this stops a
single careless or curious person, not a distributed attempt; the latter wants
a rate limit at the edge, which is a Cloudflare setting and not code.

Two keys per attempt, both capped: the caller's address, and the number being
claimed. The second is what stops one address cycling through a college's
numbering block, and it is the one that matters -- a claimed number is a row
with somebody's real phone in it.
"""

import time
from collections import deque


class Throttle:
    """Fixed-window counter per key. Small, and small on purpose."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        """True if this key may act. Records the attempt when it may."""
        moment = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and moment - hits[0] > self._window:
            hits.popleft()
        if not hits:
            # Nothing left in the window: drop the key so a quiet process does
            # not grow a dictionary entry per address it has ever seen.
            self._hits.pop(key, None)
            hits = self._hits.setdefault(key, deque())
        if len(hits) >= self._limit:
            return False
        hits.append(moment)
        return True

    def forget(self, key: str) -> None:
        """Clear one key. Used by tests; cheap enough to expose."""
        self._hits.pop(key, None)


#: Generous for a person, useless for a script. A student who mistypes their
#: number three times and reloads still gets through.
claims = Throttle(limit=8, window_seconds=60 * 10)
