"""The published policies, and the refusal to ship one that names nobody.

A policy page reading "[GRIEVANCE OFFICER NAME]" is worse than no page at all:
it is a published admission that nobody read it before putting it in front of
customers, and it is the first document a payment aggregator or a regulator
opens. The failure is silent -- the page renders, the site deploys, the
brackets sit there for months.

So the test that matters here is not that the pages render. It is that a
production process **refuses to start** while any of them would.
"""

import pytest
from fastapi.testclient import TestClient

from shelf import legal
from shelf.config import Settings

# Everything a live site needs, so each test can spoil exactly one thing.
_FILLED = {
    "database_url": "postgresql+psycopg://u:real@db.example.com:5432/aa",
    "app_env": "prod",
    "legal_entity": "Example Labs",
    "legal_entity_type": "sole proprietorship",
    "legal_address": "12 Example Road, Bengaluru 560001",
    "support_email": "hello@example.in",
    "support_phone": "+91 80 4000 0000",
    "grievance_officer": "A. Example",
    "grievance_email": "privacy@example.in",
    "jurisdiction_city": "Bengaluru",
    "policy_effective_date": "2026-10-01",
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_FILLED, **overrides})  # type: ignore[arg-type]


class TestTheRefusalToShipHalfFinished:
    def test_a_complete_site_starts(self) -> None:
        legal.check(_settings())

    @pytest.mark.parametrize("field", sorted(legal.REQUIRED))
    def test_any_blank_detail_stops_production(self, field: str) -> None:
        """Every one of these is printed verbatim on a published page."""
        with pytest.raises(legal.IncompletePolicies):
            legal.check(_settings(**{field: ""}))

    def test_it_names_everything_missing_at_once(self) -> None:
        """A deploy cycle costs minutes; finding four blanks one restart at a
        time is where those minutes go."""
        with pytest.raises(legal.IncompletePolicies) as raised:
            legal.check(_settings(legal_entity="", support_email="", grievance_officer=""))

        message = str(raised.value)
        assert "LEGAL_ENTITY" in message
        assert "SUPPORT_EMAIL" in message
        assert "GRIEVANCE_OFFICER" in message

    def test_development_warns_and_carries_on(self) -> None:
        """Working on the site must not require a registered company."""
        legal.check(_settings(app_env="dev", legal_entity="", grievance_officer=""))

    def test_a_blank_renders_as_a_bracket_not_as_nothing(self) -> None:
        """A sentence that silently loses its subject reads as a typo; a
        bracket reads as a task."""
        rendered = legal.details(_settings(app_env="dev", jurisdiction_city=""))
        assert rendered.jurisdiction.startswith("[")


class TestThePagesThemselves:
    PAGES = ("/terms", "/privacy", "/refunds", "/cancel", "/contact")

    @pytest.mark.parametrize("path", PAGES)
    def test_it_renders(self, web: TestClient, path: str) -> None:
        assert web.get(path).status_code == 200

    @pytest.mark.parametrize("path", PAGES)
    def test_it_is_reachable_from_every_page(self, web: TestClient, path: str) -> None:
        """A policy nobody can find is a policy that does not exist, and the
        aggregator's check looks for the link, not the URL."""
        assert f'href="{path}"' in web.get("/").text

    @pytest.mark.parametrize("path", PAGES)
    def test_it_says_who_is_responsible(
        self, web: TestClient, path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shelf.config import settings

        monkeypatch.setattr(settings, "legal_entity", "Example Labs")
        monkeypatch.setattr(settings, "grievance_officer", "A. Example")
        page = web.get(path).text

        assert "Example Labs" in page
        assert "A. Example" in page  # the named human both DPDP and the e-commerce rules want

    def test_an_unfinished_page_says_so_on_its_face(
        self, web: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Visible to whoever opens it, not only to whoever reads the logs."""
        from shelf.config import settings

        monkeypatch.setattr(settings, "grievance_officer", "")
        assert "Not finished" in web.get("/privacy").text

    def test_the_privacy_page_names_every_processor(self, web: TestClient) -> None:
        """"We may share data with service providers" is not a disclosure."""
        page = web.get("/privacy").text
        for processor in ("Meta", "Razorpay", "Cloudflare", "Render"):
            assert processor in page

    def test_the_privacy_page_admits_what_is_unresolved(self, web: TestClient) -> None:
        """The LLM provider is the most significant disclosure on the page and
        cannot be written until it is chosen -- including whether a student's
        text leaves India. Silence there would be the dishonest option."""
        page = web.get("/privacy").text
        assert "LLM PROVIDER" in page
        assert "under 18" in page.lower()

    def test_the_refunds_page_states_a_timeline(self, web: TestClient) -> None:
        """What a payment aggregator's activation check is actually looking for."""
        page = web.get("/refunds").text
        assert "working days" in page
        assert "original payment method" in page

    def test_a_wrong_url_is_a_branded_404_and_still_a_404(self, web: TestClient) -> None:
        """A page that looks like an error but answers 200 is worse than raw
        JSON: every crawler and monitor believes it worked."""
        response = web.get("/no-such-page")
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]

    def test_robots_keeps_crawlers_off_the_signed_in_pages(self, web: TestClient) -> None:
        body = web.get("/robots.txt").text
        assert "Disallow: /files" in body
        assert "Disallow: /f/" in body  # sign-up links carry a one-time token
