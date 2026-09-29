"""The details every policy page has to name, and a refusal to ship without them.

A privacy policy that reads "[GRIEVANCE OFFICER NAME]" is worse than no policy
at all: it is a published admission that nobody read the thing before putting
it in front of customers, and it is the first document a regulator or a payment
aggregator looks at. The failure mode is the one this codebase keeps guarding
against -- it is **silent**. The page renders, the site deploys, the brackets
sit there for months.

So this does what ``app/preflight.py`` does in the assistant: refuses to start
a production process while anything here is blank, and names everything that is
missing at once rather than one restart at a time. In development it warns and
carries on, because working on the site should not require a registered
company.

The values themselves come from the environment rather than from the templates,
so the same sentence cannot say one thing on the terms page and another on the
contact page.
"""

import logging
from dataclasses import dataclass

from shelf.config import Settings, settings

logger = logging.getLogger(__name__)


class IncompletePolicies(RuntimeError):
    """A production site was asked to start with an unfilled policy page."""


#: Setting name -> what it is, in the words a person filling it in would use.
#: Every one of these is printed verbatim on at least one published page.
REQUIRED: dict[str, str] = {
    "legal_entity": "the registered name of the entity that takes the money",
    "legal_entity_type": "sole proprietorship, LLP, private limited",
    "legal_address": "registered address, as on the GST or MCA record",
    "support_email": "a mailbox a person actually reads",
    "support_phone": "a number Razorpay's contact page check can see",
    "grievance_officer": "a named human (DPDP s.13, and the e-commerce rules)",
    "grievance_email": "where grievances go, if not the support mailbox",
    "jurisdiction_city": "the city whose courts the terms name",
    "policy_effective_date": "YYYY-MM-DD, changed when a policy changes",
}


@dataclass(frozen=True)
class Details:
    """What the templates render. One source, so two pages cannot disagree."""

    entity: str
    entity_type: str
    address: str
    support_email: str
    support_phone: str
    grievance_officer: str
    grievance_email: str
    jurisdiction: str
    gst_number: str
    price_includes_gst: bool
    effective_date: str
    rights_response_days: int
    refund_window_days: int

    @property
    def is_gst_registered(self) -> bool:
        return bool(self.gst_number)


def missing(config: Settings | None = None) -> list[str]:
    """Which required details are still blank. Empty means the pages are whole."""
    config = config or settings
    return [name for name in REQUIRED if not getattr(config, name)]


def details(config: Settings | None = None) -> Details:
    """The values as the templates want them, with visible stand-ins in dev.

    A blank renders as an obvious bracket rather than as nothing at all: a
    sentence that silently loses its subject ("These terms are governed by the
    laws of India, with courts at having jurisdiction") reads as a typo, while
    a bracket reads as a task.
    """
    config = config or settings

    def value(name: str) -> str:
        return str(getattr(config, name)) or f"[{REQUIRED[name].split(',')[0].upper()}]"

    return Details(
        entity=value("legal_entity"),
        entity_type=value("legal_entity_type"),
        address=value("legal_address"),
        support_email=value("support_email"),
        support_phone=value("support_phone"),
        grievance_officer=value("grievance_officer"),
        grievance_email=value("grievance_email"),
        jurisdiction=value("jurisdiction_city"),
        gst_number=config.gst_number,
        price_includes_gst=config.price_includes_gst,
        effective_date=value("policy_effective_date"),
        rights_response_days=config.rights_response_days,
        refund_window_days=config.refund_window_days,
    )


def check(config: Settings | None = None) -> None:
    """Called at import by ``main``. Raises in production, warns in dev.

    Everything wrong is reported at once. A deploy cycle costs minutes, and
    discovering four blank fields one restart at a time is where those minutes
    go -- the same reasoning as the assistant's preflight.
    """
    config = config or settings
    blanks = missing(config)
    if not blanks:
        return

    lines = [f"  {name.upper()} — {REQUIRED[name]}" for name in blanks]
    report = "\n".join(lines)

    if config.app_env != "prod":
        logger.warning(
            "policy pages are incomplete and will render brackets:\n%s", report
        )
        return

    raise IncompletePolicies(
        "Refusing to start: the published policy pages would name nobody.\n"
        f"{report}\n"
        "A policy page with brackets in it is a published admission that nobody "
        "read it, and it is the first thing a payment aggregator checks."
    )
