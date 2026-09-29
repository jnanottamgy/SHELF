"""Getting in, staying in, and never seeing anyone else's files.

Three properties carry this file. The link is single-use, because it lives in
a chat log forever. Entitlement is re-checked on every request, which is the
only reason sessions are rows rather than tokens. And an id in a URL is never
trusted -- every lookup is scoped to the signed-in student, so a guessed
number returns the same answer as a deleted one.

The pages themselves are rendered rather than asserted line by line: a
template that raises is a 500 nobody sees until a student does, and Jinja
fails loudly only when something actually renders it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from shelf import categories, sessions
from shelf.config import settings
from shelf.models import Deadline, Resource, Subject, Term, User, WebSession
from shelf.storage import MemoryStore
from tests.conftest import issue_link, make_student, sign_in

NOW = datetime.now(tz=UTC)


@dataclass
class Stock:
    """One term, two subjects, and a file on each shelf plus an unfiled one.

    A dataclass rather than a dict so a test names the row it means and the
    checker agrees -- ``stock.pyq.subject_id`` says what ``made.pyq`` only
    implied.
    """

    term: Term
    dbms: Subject
    material: Resource
    pyq: Resource
    info: Resource
    legacy: Resource
    unfiled: Resource


def stock(db: Session, user: User) -> Stock:
    term = Term(user_id=user.id, sequence_no=5, is_active=True, created_at=NOW)
    db.add(term)
    db.flush()
    subjects: dict[str, Subject] = {}
    for name in ("DBMS", "Operating Systems"):
        subject = Subject(user_id=user.id, term_id=term.id, name=name, created_at=NOW)
        db.add(subject)
        db.flush()
        subjects[name] = subject

    def add(**kwargs: Any) -> Resource:
        resource = Resource(
            user_id=user.id,
            status="ready" if kwargs.get("subject_id") else "pending_clarification",
            created_at=NOW,
            **kwargs,
        )
        db.add(resource)
        db.flush()
        return resource

    made = Stock(
        term=term,
        dbms=subjects["DBMS"],
        material=add(
            subject_id=subjects["DBMS"].id, title="unit 2", body="notes",
            category=categories.MATERIAL,
        ),
        pyq=add(
            subject_id=subjects["DBMS"].id, title="2025 paper", body="questions",
            category=categories.PYQ,
        ),
        info=add(
            subject_id=subjects["Operating Systems"].id, title="exam timetable",
            body="x", category=categories.INFO,
        ),
        legacy=add(
            subject_id=subjects["DBMS"].id, title="filed before shelves existed", body="x",
        ),
        unfiled=add(title="a bare scan", storage_ref="u/1/x.pdf"),
    )
    db.commit()
    return made


class TestGettingIn:
    def test_the_link_works_once(self, web: TestClient, db_session: Session) -> None:
        """A WhatsApp message is forever; the link in it must not be.

        Rotation is the mechanism -- opening it deletes the row the token
        named -- which is also why there is no table of spent tokens.
        """
        student = make_student(db_session)
        token = sign_in(web, db_session, student)

        again = web.get(f"/f/{token}")
        assert again.status_code == 200
        assert "expired" in again.text.lower()

    def test_the_token_does_not_stay_in_the_address_bar(
        self, web: TestClient, db_session: Session
    ) -> None:
        """Redirect, not render: otherwise the credential sits in history."""
        student = make_student(db_session)
        token = issue_link(db_session, student.id, datetime.now(tz=UTC))

        response = web.get(f"/f/{token}")
        assert response.headers["location"] == "/files"
        assert sessions.COOKIE in response.cookies

    def test_an_expired_link_is_refused_and_removed(
        self, web: TestClient, db_session: Session
    ) -> None:
        student = make_student(db_session)
        token = issue_link(db_session, student.id, datetime.now(tz=UTC))
        stale = db_session.get(WebSession, token)
        assert stale is not None
        stale.created_at = datetime.now(tz=UTC) - timedelta(
            minutes=settings.web_link_minutes + 1
        )
        db_session.commit()

        assert web.get(f"/f/{token}").status_code == 200
        assert db_session.get(WebSession, token) is None  # not left to accumulate

    def test_a_guessed_token_is_refused(self, web: TestClient) -> None:
        assert web.get("/f/not-a-real-token").status_code == 200
        assert sessions.COOKIE not in web.cookies

    def test_without_a_cookie_the_answer_is_the_sign_in_page(self, web: TestClient) -> None:
        """And it is not a form. Typing a number would prove nothing."""
        response = web.get("/files")
        assert response.status_code == 200
        assert "WhatsApp" in response.text

    def test_shelf_cannot_mint_a_link(self) -> None:
        """The service on the open internet must not be able to create a
        credential. Only the assistant can, because only it can verify that
        the person asking holds the phone the row names.
        """
        assert not hasattr(sessions, "issue_link")


class TestStayingIn:
    def test_a_lapsed_student_loses_the_site_on_the_next_request(
        self, web: TestClient, db_session: Session
    ) -> None:
        """The whole reason sessions are rows. A token would still be valid."""
        student = make_student(db_session)
        sign_in(web, db_session, student)
        assert web.get("/files").status_code == 200

        student.paid_until = datetime.now(tz=UTC) - timedelta(days=settings.grace_days + 1)
        db_session.commit()

        assert "Ask the assistant" in web.get("/files").text

    def test_grace_still_opens_the_site(self, web: TestClient, db_session: Session) -> None:
        """Consistency with chat. The assistant is still answering them."""
        student = make_student(db_session)
        student.paid_until = datetime.now(tz=UTC) - timedelta(hours=1)
        db_session.commit()
        sign_in(web, db_session, student)

        assert "Everything you" in web.get("/files").text

    def test_a_third_browser_evicts_the_least_recently_used(
        self, db_session: Session
    ) -> None:
        """Evict, never refuse: the person who hits this just replaced a phone."""
        student = make_student(db_session)
        now = datetime.now(tz=UTC)
        ids = []
        for minute in range(3):
            at = now + timedelta(minutes=minute)
            token = issue_link(db_session, student.id, at)
            ids.append(sessions.activate(db_session, token, at))
        db_session.commit()

        left = set(
            db_session.scalars(select(WebSession.id).where(WebSession.user_id == student.id))
        )
        assert left == set(ids[1:])  # the oldest is gone, the newest is not

    def test_signing_out_drops_the_row(self, web: TestClient, db_session: Session) -> None:
        student = make_student(db_session)
        sign_in(web, db_session, student)
        web.post("/files/signout")

        assert db_session.scalar(
            select(WebSession).where(WebSession.user_id == student.id)
        ) is None


class TestTheTree:
    def test_every_page_renders(self, web: TestClient, db_session: Session) -> None:
        """A template that raises is a 500 the student finds first."""
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)
        subject_id = made.material.subject_id
        term_id = made.term.id

        for page in (
            "/files", "/files/misc", "/files/deadlines", "/files/search?q=unit",
            f"/files/t/{term_id}", f"/files/t/{term_id}/admin",
            f"/files/s/{subject_id}", f"/files/s/{subject_id}/material",
            f"/files/s/{subject_id}/pyq", f"/files/s/{subject_id}/info",
            f"/files/r/{made.material.id}",
        ):
            assert web.get(page).status_code == 200, page

    def test_the_root_is_the_public_site_not_the_drive(self, web: TestClient) -> None:
        """One origin, two audiences: marketing at /, the signed-in drive at
        /files. The links the assistant has already sent point at /files and at
        /f/<token>, so neither may move."""
        assert web.get("/").status_code == 200
        assert "Forward it once" in web.get("/").text

    def test_the_shelves_are_always_all_three(
        self, web: TestClient, db_session: Session
    ) -> None:
        """A tree whose shape depends on what is in it cannot be learned."""
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)

        page = web.get(f"/files/s/{made.material.subject_id}").text
        # escape(): Jinja autoescapes, and one shelf label contains an ampersand.
        for label in categories.LABELS.values():
            assert escape(label) in page

    def test_an_unshelved_file_shows_on_the_default_shelf(
        self, web: TestClient, db_session: Session
    ) -> None:
        """NULL is not a fourth folder. Everything filed before shelves
        existed would otherwise be invisible while still being counted."""
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)

        page = web.get(f"/files/s/{made.material.subject_id}/material").text
        assert "filed before shelves existed" in page

    def test_the_term_drawer_gathers_info_from_every_subject(
        self, web: TestClient, db_session: Session
    ) -> None:
        """A cross-section, not a move: the timetable is filed under OS and
        still on OS's own shelf."""
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)
        term_id = made.term.id

        page = web.get(f"/files/t/{term_id}/admin").text
        assert "exam timetable" in page
        assert "unit 2" not in page

    def test_unfiled_files_have_a_drawer(self, web: TestClient, db_session: Session) -> None:
        student = make_student(db_session)
        stock(db_session, student)
        sign_in(web, db_session, student)

        assert "a bare scan" in web.get("/files/misc").text

    def test_a_note_is_shown_not_downloaded(
        self, web: TestClient, db_session: Session
    ) -> None:
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)

        response = web.get(f"/files/r/{made.material.id}")
        assert "text/html" in response.headers["content-type"]
        assert "notes" in response.text

    def test_a_file_comes_back_as_its_bytes(
        self, web: TestClient, db_session: Session, store: MemoryStore
    ) -> None:
        student = make_student(db_session)
        term = Term(user_id=student.id, sequence_no=5, is_active=True, created_at=NOW)
        db_session.add(term)
        db_session.flush()
        subject = Subject(user_id=student.id, term_id=term.id, name="DBMS", created_at=NOW)
        db_session.add(subject)
        db_session.flush()
        store.put("u/1/scan.pdf", b"%PDF-1.7 real bytes")
        resource = Resource(
            user_id=student.id, subject_id=subject.id, status="ready", title="unit 2",
            filename="unit2.pdf", storage_ref="u/1/scan.pdf", mime_type="application/pdf",
            created_at=NOW,
        )
        db_session.add(resource)
        db_session.commit()
        sign_in(web, db_session, student)

        response = web.get(f"/files/r/{resource.id}")
        assert response.content == b"%PDF-1.7 real bytes"
        assert "unit2.pdf" in response.headers["content-disposition"]

    def test_a_missing_object_does_not_500(
        self, web: TestClient, db_session: Session
    ) -> None:
        """The row outlives the object, and the page has to survive that."""
        student = make_student(db_session)
        made = stock(db_session, student)
        sign_in(web, db_session, student)

        response = web.get(f"/files/r/{made.unfiled.id}")
        assert response.status_code == 200
        assert "wouldn" in response.text


class TestDeadlines:
    def test_tomorrow_is_not_today(self, web: TestClient, db_session: Session) -> None:
        """Calendar days, not a truncated timedelta. Something due at 9am
        tomorrow is 23 hours away, and timedelta.days calls that 0."""
        from shelf.library import IST

        student = make_student(db_session)
        now = datetime.now(tz=UTC)
        tomorrow = (now.astimezone(IST) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        db_session.add(
            Deadline(
                user_id=student.id, due_at=tomorrow.astimezone(UTC),
                title="assignment 3", created_at=now,
            )
        )
        db_session.commit()
        sign_in(web, db_session, student)

        assert "tomorrow" in web.get("/files/deadlines").text


class TestSomeoneElsesFiles:
    @pytest.mark.parametrize("path", ["/files/r/{resource}", "/files/s/{subject}"])
    def test_another_students_rows_are_not_found(
        self, web: TestClient, db_session: Session, path: str
    ) -> None:
        """Ids come out of a URL, which is to say out of a stranger's hands."""
        mine = make_student(db_session)
        theirs = make_student(db_session, "919000000002")
        made = stock(db_session, theirs)
        sign_in(web, db_session, mine)

        response = web.get(
            path.format(resource=made.material.id, subject=made.material.subject_id)
        )
        assert "Not found" in response.text
        assert "unit 2" not in response.text

    def test_search_never_crosses_students(
        self, web: TestClient, db_session: Session
    ) -> None:
        mine = make_student(db_session)
        theirs = make_student(db_session, "919000000004")
        stock(db_session, theirs)
        sign_in(web, db_session, mine)

        assert "2025 paper" not in web.get("/files/search?q=paper").text


class TestThePublicSite:
    """The pages a student sees before they are a student.

    UI only so far: nothing here charges anybody or writes a users row, so
    what these pin down is that the flow renders and that its order holds.
    Verification comes before payment because the one state that must never
    happen is a payment taken against a number we cannot reach -- the
    assistant's whole response to an unknown number is silence, so that
    failure is invisible from every other direction.
    """

    def test_the_landing_page_asks_for_a_number_before_any_price(
        self, web: TestClient
    ) -> None:
        page = web.get("/").text
        assert page.index('id="phone"') < page.index("Get started")
        assert "Verify this number" in page

    def test_the_verify_page_carries_the_code_into_the_deep_link(
        self, web: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The student presses send, nothing more. A code they have to retype
        into WhatsApp is a code most of them will mistype."""
        monkeypatch.setattr(settings, "whatsapp_display_number", "919380651594")
        page = web.post("/verify", data={"phone": "9876543210"}).text

        assert "wa.me/919380651594?text=" in page
        assert "98765 43210" in page  # their number, read back the way they say it

    def test_no_configured_number_still_renders_a_usable_page(
        self, web: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead deep link is worse than none: the fallback tells them what to
        send and to whom."""
        monkeypatch.setattr(settings, "whatsapp_display_number", "")
        page = web.post("/verify", data={"phone": "9876543210"}).text

        assert "wa.me" not in page
        assert "send exactly" in page

    def test_done_promises_nothing_it_cannot_keep(self, web: TestClient) -> None:
        """Access is granted by the payment webhook, not by this page loading.
        The two are seconds apart and occasionally are not."""
        page = web.get("/done").text
        assert "Setting up your number" in page
        assert "Payment received" in page
