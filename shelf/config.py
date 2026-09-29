"""Settings for SHELF.

Deliberately a **subset** of the assistant's settings, not a copy of them. This
service never sends a WhatsApp message, never calls an LLM and never takes a
payment, so it has no business holding the credentials for any of that. A
process that cannot reach Meta cannot leak a Meta token.

The database URL and the R2 credentials are the two it genuinely shares, and
they are shared because the data is shared -- which is the whole premise of
splitting this out rather than duplicating a store.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_BARE_POSTGRES_SCHEMES = ("postgresql://", "postgres://")
_DRIVER_SCHEME = "postgresql+psycopg://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The same database the assistant writes. SHELF only ever reads it.
    database_url: str

    app_env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    sql_echo: bool = False

    # Smaller than the bot's pool on purpose: a page render holds its
    # connection for milliseconds, not across a network call to an LLM.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 10

    # How long an unopened login link stays good. Short, because it lives in a
    # chat log forever and the only thing keeping it harmless is that it stops
    # working. Opening it rotates the id, so it is single-use as well.
    web_link_minutes: int = 15
    # Idle timeout for a signed-in browser.
    web_session_days: int = 30
    # Browsers per student. A third login evicts the least recently seen.
    web_devices: int = 2
    # Days past paid_until still served. Must match the assistant's GRACE_DAYS,
    # or a student in grace is answered in chat and locked out of their files.
    grace_days: int = 3

    # Where the assistant lives, for the "ask for a new link" dead ends.
    whatsapp_chat_url: str = ""

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


def _use_psycopg(url: str) -> str:
    """Accept the URL a managed host gives you, unedited.

    Render and most managed hosts expose it as ``postgresql://``; SQLAlchemy
    reads that as psycopg2, which is not installed, and the process dies at
    import with an error that says nothing about the URL.
    """
    for scheme in _BARE_POSTGRES_SCHEMES:
        if url.startswith(scheme):
            return _DRIVER_SCHEME + url[len(scheme) :]
    return url


settings = Settings()  # type: ignore[call-arg]
settings.database_url = _use_psycopg(settings.database_url)
