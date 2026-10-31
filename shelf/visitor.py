"""What we know about somebody who has not paid yet, kept out of the database.

**Nothing about a visitor is written to Postgres until Razorpay confirms the
mandate.** Someone who signs in with Google, reads the pitch and leaves must
leave no trace. Under DPDP, personal data you did not need is not an asset: it
is a thing you now have to secure, answer access requests about, delete on
request, and report if it leaks. A thousand people who looked and did not buy
should not cost a thousand rows of that.

So the pre-payment identity lives in a **signed cookie** instead -- Starlette's
``SessionMiddleware``, which is ``itsdangerous`` underneath. The student can
read it; they cannot change it without the signing key. That is the right
trade here, because nothing in it is a *permission*: it says who Google
believes they are and which number they proved, and every actual entitlement
is still read from ``users`` on the request that needs it. A forged cookie,
if the key ever leaked, would get somebody a pitch page.

Three rules this module exists to keep in one place:

- **A phone number is written here only after it finished verifying.** A
  half-typed number is not a fact about anybody.
- **The verification code is bound to the cookie that claimed it**, so a
  second browser that happens to see the code cannot claim the verification.
- **Nothing here is ever read as entitlement.** `signed_in_user` goes to the
  database for that, every time.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from starlette.requests import Request

from shelf.config import settings

logger = logging.getLogger(__name__)

#: Keys inside the signed cookie. Namespaced so nothing else collides.
_STATE = "oauth_state"
_NONCE = "oauth_nonce"
_GOOGLE = "google"
_PHONE = "verified_phone"
_PHONE_AT = "verified_phone_at"
_CODE = "verify_code"


@dataclass(frozen=True)
class Visitor:
    """A signed-in-but-unpaid caller, as the cookie describes them."""

    sub: str
    email: str
    name: str
    picture: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""


def start_oauth(request: Request, state: str, nonce: str) -> None:
    """Remember this sign-in attempt so the callback can recognise it."""
    request.session[_STATE] = state
    request.session[_NONCE] = nonce


def take_oauth(request: Request) -> tuple[str | None, str | None]:
    """Read the attempt and **consume it**, whatever happens next.

    Single-use on purpose. Leaving a spent ``state`` readable turns one
    intercepted callback URL into a replayable one; popping it means a second
    delivery of the same callback finds nothing to match against.
    """
    return request.session.pop(_STATE, None), request.session.pop(_NONCE, None)


def remember_google(request: Request, *, sub: str, email: str, name: str, picture: str) -> None:
    request.session[_GOOGLE] = {"sub": sub, "email": email, "name": name, "picture": picture}


def google(request: Request) -> Visitor | None:
    """Who Google said this is -- never whether they may see anything."""
    raw = request.session.get(_GOOGLE)
    if not isinstance(raw, dict):
        return None
    sub = raw.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return Visitor(
        sub=sub,
        email=str(raw.get("email", "")),
        name=str(raw.get("name", "")),
        picture=str(raw.get("picture", "")),
    )


def remember_code(request: Request, code: str) -> None:
    """Bind a verification code to this browser."""
    request.session[_CODE] = code


def holds_code(request: Request, code: str) -> bool:
    """Did *this* browser claim that code?

    The polling endpoint is keyed by the code, so anyone who sees the code can
    watch it. Letting them *act* on it is the part that must not happen: the
    number is proved by a WhatsApp message, and the browser that gets to spend
    that proof should be the one that started it.
    """
    stored = request.session.get(_CODE)
    return isinstance(stored, str) and stored == code


def remember_verified_phone(request: Request, phone: str, now: datetime) -> None:
    """Only ever called once a number has actually finished verifying."""
    request.session[_PHONE] = phone
    request.session[_PHONE_AT] = now.isoformat()
    request.session.pop(_CODE, None)


def verified_phone(request: Request, now: datetime) -> str | None:
    """The remembered number, if it is still recent enough to offer back."""
    phone = request.session.get(_PHONE)
    stamp = request.session.get(_PHONE_AT)
    if not isinstance(phone, str) or not phone or not isinstance(stamp, str):
        return None
    try:
        verified_at = datetime.fromisoformat(stamp)
    except ValueError:
        # A cookie we cannot read is a cookie we do not trust.
        return None
    if verified_at + timedelta(days=settings.verified_phone_days) <= now:
        return None
    return phone


def forget(request: Request) -> None:
    """Sign out: drop everything this browser was told, in one place.

    One function rather than a list of pops at the call site, because the way
    a sign-out leaks is that somebody adds a key and forgets to clear it.
    """
    request.session.clear()
