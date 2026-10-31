"""Refuse to start a production site that is missing a secret it needs.

Same reasoning as ``shelf/legal.py`` and the assistant's own preflight, applied
to the credentials sign-in depends on. Every one of these **degrades silently**
rather than crashing, which is right on a laptop and dangerous on a server:

- no ``SESSION_SECRET`` and the visitor cookie is signed with a placeholder
  that is in this repository. Anyone who reads it can mint a cookie claiming
  to be any Google account. That is the whole pre-payment identity, and
  nothing about it would look broken.
- no ``GOOGLE_CLIENT_ID``/``SECRET`` and every sign-in fails at Google with an
  error the student cannot act on. The site is up; nobody can get in.
- no ``SITE_URL`` and the Google redirect URI is derived from the request's own
  host header, which a misconfigured proxy can rewrite.

So the check runs at import, before the app object exists, and raises. A
warning in a log nobody reads is the same as no check. Everything wrong is
reported at once, because a deploy cycle costs minutes.
"""

import logging

from shelf.config import Settings, settings

logger = logging.getLogger(__name__)

#: Used only when APP_ENV is not prod. Deliberately obvious, and deliberately
#: not random: a per-boot random key would sign out every dev on every reload.
DEV_SESSION_SECRET = "dev-only-session-secret-never-use-in-production"  # noqa: S105

REQUIRED: dict[str, str] = {
    "session_secret": "signs the visitor cookie; treat it like a password",
    "google_client_id": "from the Google Cloud console's OAuth client",
    "google_client_secret": "from the same OAuth client",
    "site_url": "this site's public origin, which the Google redirect URI is built from",
}


class Misconfigured(RuntimeError):
    """A production site is missing something that fails silently."""


def missing(config: Settings | None = None) -> list[str]:
    config = config or settings
    return [name for name in REQUIRED if not getattr(config, name)]


def session_secret(config: Settings | None = None) -> str:
    """The signing key, or the dev placeholder -- never a silent empty string.

    Separate from ``check`` so there is exactly one expression of "what key do
    we sign with", rather than a fallback written inline where the middleware
    is constructed and a refusal written somewhere else that has to agree.
    """
    config = config or settings
    if config.session_secret:
        return config.session_secret
    if config.app_env == "prod":
        raise Misconfigured("refusing to sign production cookies with the dev placeholder")
    return DEV_SESSION_SECRET


def check(config: Settings | None = None) -> None:
    """Called at import by ``main``. Raises in production, warns in dev."""
    config = config or settings
    blanks = missing(config)
    if not blanks:
        return

    report = "\n".join(f"  {name.upper()} — {REQUIRED[name]}" for name in blanks)

    if config.app_env != "prod":
        logger.warning("google sign-in is not configured; it will not work:\n%s", report)
        return

    raise Misconfigured(
        "Refusing to start: this production site cannot sign anybody in safely.\n"
        f"{report}"
    )
