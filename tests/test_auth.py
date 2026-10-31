"""Google sign-in: the routes, and the token verification underneath them.

Two layers, tested separately because they fail separately.

``TestTheSignInRoutes`` drives the real app with ``FakeGoogle`` substituted,
and is about what the *browser* is allowed to do: which redirect it lands on,
whether a session cookie is set, and whether a replayed callback works twice.

``TestVerifyingGooglesToken`` builds real signed JWTs with a key the test owns
and runs them through ``RealGoogle``. That is where "is this token genuine"
actually lives, and every one of these is a token an attacker can construct:
signed with the wrong key, minted for a different application, expired,
replayed from an older sign-in, or asserting an address Google never checked.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from shelf import sessions
from shelf.config import settings
from shelf.google import FakeGoogle, GoogleError, GoogleIdentity, RealGoogle, _KeyCache
from shelf.models import User, WebSession
from tests.conftest import make_student

SUB = "google-sub-1"
CLIENT_ID = "shelf-test-client-id.apps.googleusercontent.com"


def _start(web: TestClient) -> str:
    """Begin a sign-in and return the ``state`` the site chose."""
    response = web.get("/auth/google")
    assert response.status_code == 303
    location = str(response.headers["location"])
    return location.split("state=")[1].split("&")[0]


class TestTheSignInRoutes:
    def test_starting_a_sign_in_redirects_to_google(
        self, web: TestClient, google: FakeGoogle
    ) -> None:
        response = web.get("/auth/google")

        assert response.status_code == 303
        assert response.headers["location"].startswith("https://accounts.google.test/auth")
        assert google.last_state
        assert google.last_nonce
        assert google.last_state != google.last_nonce

    def test_every_sign_in_gets_a_fresh_state_and_nonce(
        self, web: TestClient, google: FakeGoogle
    ) -> None:
        """Reusing either across sign-ins would make a replay indistinguishable."""
        first = _start(web)
        first_nonce = google.last_nonce
        second = _start(web)

        assert first != second
        assert first_nonce != google.last_nonce

    def test_a_paid_student_lands_on_their_files(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        make_student(db_session, google_sub=SUB)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha Rao", "")
        state = _start(web)

        response = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert response.status_code == 303
        assert response.headers["location"] == "/files"
        assert response.cookies.get(sessions.COOKIE)

    def test_a_student_in_grace_still_lands_on_their_files(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Grace counts here exactly as it does in chat.

        Being answered on WhatsApp while locked out of your own files is the
        kind of inconsistency a student reasonably reads as the product being
        broken, so the two must agree.
        """
        lapsed = datetime.now(tz=UTC) - timedelta(days=settings.grace_days - 1)
        make_student(db_session, google_sub=SUB, paid_until=lapsed)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        state = _start(web)

        response = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert response.headers["location"] == "/files"

    def test_a_lapsed_student_gets_the_pitch_not_the_dashboard(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """A real account with no current subscription is not a way in."""
        long_gone = datetime.now(tz=UTC) - timedelta(days=settings.grace_days + 30)
        make_student(db_session, google_sub=SUB, paid_until=long_gone)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        state = _start(web)

        response = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert response.headers["location"] == "/"
        assert not response.cookies.get(sessions.COOKIE)

    def test_an_unknown_google_account_gets_the_pitch(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        google.identity_to_return = GoogleIdentity("nobody", "new@gmail.com", "New", "")
        state = _start(web)

        response = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert response.headers["location"] == "/"
        assert not response.cookies.get(sessions.COOKIE)

    def test_signing_in_never_creates_a_users_row(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """SHELF cannot grant access, and a visitor who never pays leaves nothing.

        Both halves of the architecture in one assertion: only the assistant's
        Razorpay webhook writes ``users``, and under DPDP a thousand people who
        looked and did not buy must not cost a thousand rows of personal data.
        """
        google.identity_to_return = GoogleIdentity("nobody", "new@gmail.com", "New", "")
        state = _start(web)

        web.get(f"/auth/google/callback?code=ok&state={state}")

        assert db_session.scalars(select(User)).all() == []

    def test_a_callback_whose_state_does_not_match_is_refused(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Without this, a malicious page signs a student into the attacker's
        account, and every note they file afterwards lands in it."""
        make_student(db_session, google_sub=SUB)
        _start(web)

        response = web.get("/auth/google/callback?code=ok&state=attacker-chosen")

        assert response.headers["location"] == "/?signin=failed"
        assert not response.cookies.get(sessions.COOKIE)

    def test_a_callback_with_no_sign_in_started_is_refused(
        self, web: TestClient, db_session: Session
    ) -> None:
        make_student(db_session, google_sub=SUB)

        response = web.get("/auth/google/callback?code=ok&state=anything")

        assert response.headers["location"] == "/?signin=failed"
        assert not response.cookies.get(sessions.COOKIE)

    def test_a_callback_with_no_code_is_refused(
        self, web: TestClient, db_session: Session
    ) -> None:
        make_student(db_session, google_sub=SUB)
        state = _start(web)

        response = web.get(f"/auth/google/callback?state={state}")

        assert response.headers["location"] == "/?signin=failed"

    def test_the_same_callback_cannot_be_replayed(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """``state`` is consumed on use, so an intercepted callback URL is
        worth exactly one attempt -- and that one was the student's own."""
        make_student(db_session, google_sub=SUB)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        state = _start(web)
        url = f"/auth/google/callback?code=ok&state={state}"
        assert web.get(url).headers["location"] == "/files"

        web.cookies.delete(sessions.COOKIE)
        again = web.get(url)

        assert again.headers["location"] == "/?signin=failed"

    def test_a_failed_attempt_also_consumes_the_state(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Otherwise a refused callback leaves a live ``state`` to retry against."""
        make_student(db_session, google_sub=SUB)
        state = _start(web)
        google.error = GoogleError("nope")
        web.get(f"/auth/google/callback?code=ok&state={state}")

        google.error = None
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        retry = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert retry.headers["location"] == "/?signin=failed"

    def test_a_rejected_token_never_signs_anybody_in(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        make_student(db_session, google_sub=SUB)
        state = _start(web)
        google.error = GoogleError("forged")

        response = web.get(f"/auth/google/callback?code=ok&state={state}")

        assert response.headers["location"] == "/?signin=failed"
        assert not response.cookies.get(sessions.COOKIE)
        assert db_session.scalars(select(WebSession)).all() == []

    def test_google_sign_in_counts_against_the_device_cap(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """A cap is only a cap if every door counts against it."""
        student = make_student(db_session, google_sub=SUB)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        for _ in range(settings.web_devices + 2):
            state = _start(web)
            web.get(f"/auth/google/callback?code=ok&state={state}")
            web.cookies.delete(sessions.COOKIE)

        signed_in = db_session.scalars(
            select(WebSession).where(WebSession.user_id == student.id)
        ).all()
        assert len(signed_in) == settings.web_devices

    def test_signing_out_ends_the_session_row_as_well_as_the_cookie(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Deleting the cookie alone would leave a live row for a stolen copy."""
        make_student(db_session, google_sub=SUB)
        google.identity_to_return = GoogleIdentity(SUB, "asha@gmail.com", "Asha", "")
        state = _start(web)
        web.get(f"/auth/google/callback?code=ok&state={state}")

        response = web.post("/auth/signout")

        assert response.status_code == 303
        assert db_session.scalars(select(WebSession)).all() == []


# --- the token itself ----------------------------------------------------------


class _FixedKeys(_KeyCache):
    """Google's key set, owned by the test instead of fetched."""

    def __init__(self, key: RSAKey) -> None:
        super().__init__()
        self._fixed = KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    def get(self, *, force: bool = False) -> KeySet:
        return self._fixed


@pytest.fixture
def signing_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "test-key"})


@pytest.fixture
def real_google(signing_key: RSAKey, monkeypatch: pytest.MonkeyPatch) -> RealGoogle:
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    return RealGoogle(keys=_FixedKeys(signing_key))


def _token(key: RSAKey, *, drop: tuple[str, ...] = (), **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": SUB,
        "exp": now + 600,
        "iat": now,
        "nonce": "the-nonce",
        "email": "Asha@Gmail.com",
        "email_verified": True,
        "name": "Asha Rao",
        "picture": "https://pic.test/a.jpg",
    }
    claims.update(overrides)
    for name in drop:
        claims.pop(name, None)
    header = {"alg": "RS256", "kid": str(key.kid)}
    return jwt.encode(header, claims, key)


class TestVerifyingGooglesToken:
    def test_a_genuine_token_yields_the_identity(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        claims = real_google._verify(_token(signing_key), "the-nonce")

        assert claims["sub"] == SUB

    def test_the_email_is_lowercased_but_the_sub_is_not_touched(
        self, real_google: RealGoogle, signing_key: RSAKey, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sign-in matches on ``sub``, which is an opaque id, not text."""
        monkeypatch.setattr(
            real_google, "_exchange", lambda code, redirect_uri: _token(signing_key)
        )

        identity = real_google.identity(code="c", redirect_uri="r", nonce="the-nonce")

        assert identity.email == "asha@gmail.com"
        assert identity.sub == SUB

    def test_a_token_signed_with_another_key_is_refused(
        self, real_google: RealGoogle
    ) -> None:
        """The whole point of the signature. Unverified, an ID token is a JSON
        blob the caller chose."""
        attacker = RSAKey.generate_key(2048, parameters={"kid": "test-key"})

        with pytest.raises(GoogleError):
            real_google._verify(_token(attacker), "the-nonce")

    def test_a_token_minted_for_another_application_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        """It is a perfectly genuine Google token -- for somebody else's app.

        Without the audience check, any site a student signs into could hand
        us its own token and be let in as them.
        """
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, aud="some-other-app"), "the-nonce")

    def test_an_expired_token_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        past = int(time.time()) - 3600

        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, exp=past), "the-nonce")

    def test_a_token_from_another_issuer_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, iss="https://evil.test"), "the-nonce")

    def test_a_token_from_a_different_sign_in_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        """What ``nonce`` is for: ``state`` proves this browser started a
        sign-in, ``nonce`` proves the token came back from *that* one."""
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, nonce="an-older-nonce"), "the-nonce")

    def test_a_token_with_no_nonce_at_all_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, drop=("nonce",)), "the-nonce")

    def test_an_unverified_address_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        """Google will assert an address the account never proved, and support
        would read it as the student's own."""
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, email_verified=False), "the-nonce")

    def test_an_unsigned_token_is_refused(self, real_google: RealGoogle) -> None:
        """``alg: none`` is the oldest JWT hole there is. Naming the permitted
        algorithms is what closes it."""
        with pytest.raises(GoogleError):
            real_google._verify("eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.", "the-nonce")

    def test_a_token_hmac_signed_with_googles_public_key_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        """Algorithm confusion, the real version of the attack.

        Google's signing key is *public*. If a symmetric algorithm were
        permitted, anyone could take that public value, use it as an HMAC
        secret, and sign a token claiming to be any student -- the verifier
        would dutifully check it against the same value and agree.

        Measured, not assumed: this is refused for **two** independent reasons.
        ``ALGORITHMS`` excludes HS256, and joserfc separately refuses to use an
        RSA key for HMAC. Adding HS256 back to the list does not make this test
        fail, so what it pins is the property, not either mechanism. Both would
        have to go for the attack to land -- which is the point of having two.
        """
        secret = str(signing_key.as_dict(private=False)["n"])
        forged = jwt.encode(
            {"alg": "HS256", "kid": str(signing_key.kid)},
            {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "intruder"},
            OctKey.import_key(secret),
        )

        with pytest.raises(GoogleError):
            real_google._verify(forged, "the-nonce")

    def test_a_token_with_no_expiry_at_all_is_refused(
        self, real_google: RealGoogle, signing_key: RSAKey
    ) -> None:
        """An expired token is refused by the library; one that never expires
        is refused by *us* requiring the claim to be present. Without that,
        a token minted once would be a password that never rotates.
        """
        with pytest.raises(GoogleError):
            real_google._verify(_token(signing_key, drop=("exp",)), "the-nonce")
