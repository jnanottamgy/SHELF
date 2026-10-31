"""Google sign-in: the only way into SHELF.

**What this module is for.** Google tells us who somebody is. Nothing here
decides what they may *do* -- that is still `users.paid_until`, written only by
the assistant's Razorpay webhook. A valid Google sign-in for an account nobody
has paid for gets the pitch page, not a dashboard.

**Why `sub` and not email.** Google returns both. `sub` is a permanent account
id; the email is editable. Matching on email would silently lock a student out
the day they change their Google address, and would hand their account to
whoever Google later issues that address to. `email` is for display and support
and nothing else.

**The three checks that matter**, in the order an attacker meets them:

1. ``state`` -- a random value we put in the visitor's cookie before sending
   them to Google, and require back on return. Without it a malicious page can
   complete *its own* sign-in in the student's browser, leaving them quietly
   signed in as the attacker, filing their notes into the attacker's account.
2. ``nonce`` -- a second random value, sent to Google and echoed inside the
   signed token. ``state`` proves this browser started a sign-in; ``nonce``
   proves the token came back from *that* sign-in and is not an older one
   replayed. They are separate because they fail separately.
3. The token's **signature, issuer, audience and expiry**. An ID token is only
   worth what its signature is worth: unverified, it is a JSON blob the caller
   chose. We verify against Google's published keys, require the issuer to be
   Google, require the audience to be *our* client id (a token minted for a
   different application is a valid Google token and must still be refused),
   and require it to be unexpired.

``email_verified`` is also required. Google will assert an address the account
has not proven, and we show it to support staff as if it were the student's.

The verification is done with ``joserfc`` rather than by hand. Hand-rolled JWT
checking is where the famous holes are -- accepting ``alg: none``, or trusting
the token's own header to pick the key.
"""

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import httpx
from fastapi import Depends
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from shelf.config import settings

logger = logging.getLogger(__name__)

#: Google's OpenID configuration, pinned rather than discovered. Discovery is a
#: network call on every start, and these endpoints have not moved in a decade.
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

#: Both spellings are valid Google issuers and both appear in real tokens.
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

#: Only asymmetric signing. Naming the algorithms is what refuses ``alg: none``
#: and an HMAC token signed with the public key as its secret.
ALGORITHMS = ["RS256"]

#: The least Google can tell us and still let us address a student by name.
SCOPE = "openid email profile"

#: Clock skew allowed when checking expiry, in seconds.
LEEWAY = 60

_HTTP_TIMEOUT = 10.0


class GoogleError(RuntimeError):
    """Sign-in could not be completed. Never shown to the student verbatim."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Who Google says this is. ``sub`` is the only field we key anything on."""

    sub: str
    email: str
    name: str
    picture: str


class GoogleClient(Protocol):
    def authorization_url(self, *, redirect_uri: str, state: str, nonce: str) -> str: ...

    def identity(self, *, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity: ...


def new_secret() -> str:
    """A ``state`` or ``nonce``. 32 bytes of urandom, URL-safe."""
    return secrets.token_urlsafe(32)


class _KeyCache:
    """Google's signing keys, cached and refreshed on an unknown ``kid``.

    Google rotates these. Refetching on every sign-in would make Google's
    availability our availability; never refetching would break sign-in the
    day they rotate. So: cache with a ttl, and refetch once when a token names
    a key we do not hold -- which is exactly what a rotation looks like.
    """

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self._ttl = ttl_seconds
        self._keys: KeySet | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def get(self, *, force: bool = False) -> KeySet:
        with self._lock:
            cached = self._keys
            fresh = cached is not None and time.monotonic() - self._fetched_at < self._ttl
            if cached is not None and fresh and not force:
                return cached
            try:
                response = httpx.get(JWKS_URI, timeout=_HTTP_TIMEOUT)
                response.raise_for_status()
                keys = KeySet.import_key_set(response.json())
            except (httpx.HTTPError, ValueError, JoseError) as error:
                if cached is not None:
                    # Serve the stale set rather than failing every sign-in
                    # because Google blipped. A stale key still verifies.
                    logger.warning("could not refresh Google's keys; using the cached set")
                    return cached
                raise GoogleError("could not fetch Google's signing keys") from error
            self._keys = keys
            self._fetched_at = time.monotonic()
            return keys


class RealGoogle:
    """The live client. Holds the client secret; never logs a token."""

    def __init__(self, keys: _KeyCache | None = None) -> None:
        self._keys = keys or _KeyCache()

    def authorization_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        query = httpx.QueryParams(
            {
                "client_id": settings.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "nonce": nonce,
                # Ask Google to pick an account rather than silently reusing the
                # one already signed in; students share machines.
                "prompt": "select_account",
            }
        )
        return f"{AUTH_ENDPOINT}?{query}"

    def identity(self, *, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity:
        id_token = self._exchange(code, redirect_uri)
        claims = self._verify(id_token, nonce)
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(claims.get("email", "")).strip().lower(),
            name=str(claims.get("name", "")).strip(),
            picture=str(claims.get("picture", "")).strip(),
        )

    def _exchange(self, code: str, redirect_uri: str) -> str:
        try:
            response = httpx.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise GoogleError("could not reach Google to exchange the code") from error
        if response.status_code != 200:
            # Status only. The body echoes the code and can carry the secret.
            logger.warning(
                "google rejected the code exchange",
                extra={"status": response.status_code},
            )
            raise GoogleError("Google rejected the sign-in")
        payload: dict[str, Any] = response.json()
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise GoogleError("Google returned no id_token")
        return id_token

    def _verify(self, id_token: str, nonce: str) -> dict[str, Any]:
        token = self._decode(id_token)
        registry = jwt.JWTClaimsRegistry(
            iss={"essential": True, "values": list(ISSUERS)},
            aud={"essential": True, "value": settings.google_client_id},
            exp={"essential": True},
            sub={"essential": True},
            leeway=LEEWAY,
        )
        try:
            registry.validate(token.claims)
        except JoseError as error:
            raise GoogleError("Google's token did not pass validation") from error

        claims: dict[str, Any] = dict(token.claims)
        # compare_digest rather than == : there is no reason to leak how much of
        # a guessed nonce was right, and it costs nothing.
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise GoogleError("the token does not belong to this sign-in")
        if claims.get("email_verified") is not True:
            raise GoogleError("Google has not verified that address")
        if not claims.get("sub"):
            raise GoogleError("Google's token names no account")
        return claims

    def _decode(self, id_token: str) -> jwt.Token:
        """Verify the signature, refetching Google's keys once on an unknown kid."""
        for force in (False, True):
            try:
                return jwt.decode(id_token, self._keys.get(force=force), algorithms=ALGORITHMS)
            except JoseError:
                if force:
                    raise GoogleError("Google's token is not correctly signed") from None
        raise GoogleError("Google's token is not correctly signed")  # pragma: no cover


class FakeGoogle:
    """Test double. Returns a scripted identity, or raises a scripted error.

    It deliberately still checks the nonce, so a route test cannot pass while
    the route forgets to thread it through.
    """

    def __init__(self, identity: GoogleIdentity | None = None) -> None:
        self.identity_to_return = identity or GoogleIdentity(
            sub="google-sub-1", email="asha@gmail.com", name="Asha", picture=""
        )
        self.error: GoogleError | None = None
        self.last_state: str | None = None
        self.last_nonce: str | None = None

    def authorization_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        self.last_state = state
        self.last_nonce = nonce
        return f"https://accounts.google.test/auth?state={state}&nonce={nonce}"

    def identity(self, *, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity:
        if self.error is not None:
            raise self.error
        if self.last_nonce is not None and not secrets.compare_digest(nonce, self.last_nonce):
            raise GoogleError("the token does not belong to this sign-in")
        return self.identity_to_return


_real = RealGoogle()


def get_google() -> GoogleClient:
    """Injected, so a test substitutes the whole client instead of the network."""
    return _real


GoogleDep = Annotated[GoogleClient, Depends(get_google)]
