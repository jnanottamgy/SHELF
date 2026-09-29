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

    # Where the assistant lives: the dialable number students message, used for
    # the sign-up deep link and the "ask for a new link" dead ends. Digits only,
    # 91XXXXXXXXXX -- the same shape ck_users_ph_no_digits enforces on the row.
    whatsapp_chat_url: str = ""
    whatsapp_display_number: str = ""

    # --- legal identity ------------------------------------------------
    # Every one of these appears verbatim on a published policy page, and an
    # unfilled one is a policy that names nobody. ``shelf/legal.py`` refuses to
    # start a production process while any is blank -- a privacy policy reading
    # "[GRIEVANCE OFFICER NAME]" is worse than none, because it is a visible
    # admission that nobody read it.
    legal_entity: str = ""            # the registered name that takes the money
    legal_entity_type: str = ""       # sole proprietorship / LLP / private limited
    legal_address: str = ""           # registered address, as on the GST or MCA record
    support_email: str = ""           # answered by a person
    support_phone: str = ""           # required on a Razorpay-facing contact page
    # DPDP s.13 and the Consumer Protection (E-Commerce) Rules both require a
    # named human, reachable, who answers grievances within a stated window.
    grievance_officer: str = ""
    grievance_email: str = ""
    jurisdiction_city: str = ""       # courts named in the terms
    # Empty means not GST-registered. If set, it is printed on the pricing and
    # contact pages, and the price must state whether it includes tax.
    gst_number: str = ""
    price_includes_gst: bool = True
    # Changed whenever a policy changes in substance. Students are told what
    # changed, and when, rather than being asked to diff two pages.
    policy_effective_date: str = ""   # YYYY-MM-DD
    # Days to answer a rights request. DPDP does not fix a number; saying one
    # and meeting it is the point.
    rights_response_days: int = 30
    # Days from first payment in which a first subscription is fully refundable.
    refund_window_days: int = 7

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
