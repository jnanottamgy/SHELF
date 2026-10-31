"""The refusal to start a production site that cannot sign anybody in safely.

Every setting here **degrades silently** when it is missing, which is the
reason the check exists at all. A site with no ``SESSION_SECRET`` starts,
serves, and looks healthy -- while signing its visitor cookies with a value
printed in this repository. Nothing would ever surface that in a log.
"""

import pytest

from shelf import preflight
from shelf.config import Settings

FILLED = {
    "database_url": "postgresql+psycopg://u:p@localhost/db",
    "session_secret": "a-real-secret",
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "site_url": "https://shelf.example",
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**FILLED, **overrides})  # type: ignore[arg-type]


class TestRefusingToStart:
    def test_a_fully_configured_production_site_starts(self) -> None:
        preflight.check(_settings(app_env="prod"))

    @pytest.mark.parametrize("name", sorted(preflight.REQUIRED))
    def test_production_refuses_while_any_one_is_blank(self, name: str) -> None:
        with pytest.raises(preflight.Misconfigured):
            preflight.check(_settings(app_env="prod", **{name: ""}))

    def test_it_names_every_blank_at_once(self) -> None:
        """One restart per missing value is how a deploy afternoon disappears."""
        config = _settings(app_env="prod", session_secret="", google_client_id="")

        with pytest.raises(preflight.Misconfigured) as refused:
            preflight.check(config)

        assert "SESSION_SECRET" in str(refused.value)
        assert "GOOGLE_CLIENT_ID" in str(refused.value)

    def test_development_warns_and_carries_on(self) -> None:
        """Working on the site must not require a Google account."""
        preflight.check(_settings(app_env="dev", session_secret="", google_client_id=""))


class TestTheSigningKey:
    def test_a_configured_secret_is_used_as_given(self) -> None:
        assert preflight.session_secret(_settings()) == "a-real-secret"

    def test_development_falls_back_to_a_stable_placeholder(self) -> None:
        """Stable, not random: a per-boot key would sign every dev out on
        every reload, and they would learn to distrust the sign-out."""
        first = preflight.session_secret(_settings(app_env="dev", session_secret=""))
        second = preflight.session_secret(_settings(app_env="dev", session_secret=""))

        assert first == second == preflight.DEV_SESSION_SECRET

    def test_the_placeholder_can_never_be_returned_in_production(self) -> None:
        """The check above raises first, so this is the second lock on the
        same door -- and the one that holds if somebody adds a code path that
        builds the middleware without calling check()."""
        with pytest.raises(preflight.Misconfigured):
            preflight.session_secret(_settings(app_env="prod", session_secret=""))
