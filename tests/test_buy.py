"""Buying: the number step, and the four things checked before checkout.

The product rule underneath all of it is one sentence: **nobody is charged
against a number they have not proved.** The assistant answers an unknown
number with silence, so a payment taken against a mistyped number is invisible
from every direction -- no bounce, no error, nothing until the student gives
up. These pin the checks that make that state unreachable rather than merely
alertable.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from shelf import visitor
from shelf.config import settings
from shelf.google import FakeGoogle
from shelf.models import SignupCode
from tests.conftest import make_student, sign_in_with_google

MINE = "google-sub-1"
THEIRS = "google-sub-2"
NUMBER = "9876543210"
NORMALISED = "919876543210"


def _claim(web: TestClient, db: Session, number: str = NUMBER) -> SignupCode:
    web.post("/verify", data={"phone": number})
    row: SignupCode | None = db.scalar(select(SignupCode))
    assert row is not None
    return row


def _verify(db: Session, row: SignupCode) -> None:
    """Stand in for the assistant, the only thing that can mark a code proved."""
    row.verified_at = datetime.now(tz=UTC)
    db.commit()


class TestGettingToTheNumberStep:
    def test_buying_requires_signing_in_first(self, web: TestClient) -> None:
        """The number step reveals whether a number is already a customer.

        Left open, that is a way to ask the question about any number in
        India. Requiring an account does not make enumeration impossible; it
        makes it attributable and rate-limitable.
        """
        response = web.get("/buy")

        assert response.status_code == 303
        assert response.headers["location"] == "/auth/google"

    def test_claiming_a_number_requires_signing_in_first(self, web: TestClient) -> None:
        response = web.post("/verify", data={"phone": NUMBER})

        assert response.status_code == 303
        assert response.headers["location"] == "/auth/google"

    def test_a_signed_in_visitor_gets_the_number_form(
        self, web: TestClient, google: FakeGoogle
    ) -> None:
        sign_in_with_google(web, google)

        page = web.get("/buy").text

        assert 'id="phone"' in page


class TestTheCodeIsBoundToTheBrowser:
    def test_checkout_refuses_a_code_claimed_by_another_browser(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """The polling endpoint is public by design -- it is keyed by the code,
        so anyone holding one can watch it. *Spending* it has to stay with the
        browser that started the claim, or seeing a code over a shoulder is
        enough to buy a subscription against somebody else's number.
        """
        sign_in_with_google(web, google)
        row = _claim(web, db_session)
        _verify(db_session, row)
        web.cookies.clear()
        sign_in_with_google(web, google, sub=THEIRS, email="other@gmail.com")

        page = web.get(f"/checkout?code={row.code}").text

        assert "could not confirm" in page

    def test_checkout_admits_the_browser_that_claimed_it(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        sign_in_with_google(web, google)
        row = _claim(web, db_session)
        _verify(db_session, row)

        page = web.get(f"/checkout?code={row.code}").text

        assert "could not confirm" not in page

    def test_checkout_still_refuses_an_unverified_code(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Binding is an extra lock, not a replacement for the proof itself."""
        sign_in_with_google(web, google)
        row = _claim(web, db_session)

        assert "could not confirm" in web.get(f"/checkout?code={row.code}").text

    def test_checkout_requires_signing_in(self, web: TestClient) -> None:
        response = web.get("/checkout?code=ANYTHING")

        assert response.status_code == 303
        assert response.headers["location"] == "/auth/google"


class TestWhoTheNumberBelongsTo:
    def test_a_number_on_another_google_account_routes_to_a_human(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Masked, not hidden: enough for the right person to recognise their
        own address, not enough for the wrong person to learn a new one."""
        make_student(db_session, NORMALISED, google_sub=THEIRS, email="asha@gmail.com")
        sign_in_with_google(web, google, sub=MINE)

        page = web.post("/verify", data={"phone": NUMBER}).text

        assert "a•••@gmail.com" in page
        assert "asha@gmail.com" not in page
        assert settings.support_email in page or "support" in page.lower()

    def test_a_number_on_your_own_account_is_not_a_conflict(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Signing in from a browser we have not seen is not an intrusion."""
        make_student(db_session, NORMALISED, google_sub=MINE, email="asha@gmail.com")
        sign_in_with_google(web, google, sub=MINE)

        page = web.post("/verify", data={"phone": NUMBER}).text

        assert "another account" not in page

    def test_an_older_customer_with_no_google_account_is_not_a_conflict(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Every row predating Google sign-in has google_sub NULL.

        Treating NULL as "somebody else's" would tell those students their own
        number belongs to a stranger, and offer them no way out.
        """
        make_student(db_session, NORMALISED)
        sign_in_with_google(web, google, sub=MINE)

        page = web.post("/verify", data={"phone": NUMBER}).text

        assert "another account" not in page
        assert "already has a subscription" in page

    def test_ownership_is_checked_again_at_checkout(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Minutes pass between the two checks, and the answer can change in
        them -- somebody else can finish buying that number meanwhile."""
        sign_in_with_google(web, google, sub=MINE)
        row = _claim(web, db_session)
        _verify(db_session, row)
        make_student(db_session, NORMALISED, google_sub=THEIRS, email="asha@gmail.com")

        page = web.get(f"/checkout?code={row.code}").text

        assert "a•••@gmail.com" in page


class TestRememberingAVerifiedNumber:
    def test_a_finished_verification_is_offered_back(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        sign_in_with_google(web, google)
        row = _claim(web, db_session)
        _verify(db_session, row)
        web.get(f"/checkout?code={row.code}")

        page = web.get("/buy").text

        # The marker is the offer, not the digits: "98765 43210" is also the
        # form's placeholder, present either way, so asserting the digits
        # passed while proving nothing.
        assert "Yes, use this number" in page

    def test_an_abandoned_verification_is_not_remembered(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """A half-typed number is not a fact about anybody."""
        sign_in_with_google(web, google)
        _claim(web, db_session)

        page = web.get("/buy").text

        assert "Yes, use this number" not in page

    def test_the_memory_expires(self, web: TestClient) -> None:
        """Checked directly: driving the clock forward through the routes
        would mean faking time inside the app rather than at the boundary."""

        class _Req:
            session: dict[str, object] = {}

        stale = datetime.now(tz=UTC) - timedelta(days=settings.verified_phone_days + 1)
        request = _Req()
        visitor.remember_verified_phone(request, NORMALISED, stale)  # type: ignore[arg-type]

        assert visitor.verified_phone(request, datetime.now(tz=UTC)) is None  # type: ignore[arg-type]

    def test_a_number_is_only_remembered_once_it_is_proved(
        self, web: TestClient, db_session: Session, google: FakeGoogle
    ) -> None:
        """Claiming is not proving. The row is a claim until the assistant
        sees a message arrive from that phone."""
        sign_in_with_google(web, google)
        row = _claim(web, db_session)

        web.get(f"/checkout?code={row.code}")  # refused: not verified

        assert "Yes, use this number" not in web.get("/buy").text
