"""The public site: what a student sees before they are a student.

**Verification is wired; payment is not.** A number can be claimed and proved
here today. Nothing charges anybody and nothing writes a ``users`` row -- see
the note on ownership below.

The flow is verify, then pay, then hand off to WhatsApp, and the order is the
point. CLAUDE.md records the one state that must never happen: a payment taken
with no reachable number. The assistant's entire response to an unknown number
is silence, so that failure is invisible from every direction -- no bounce, no
error, no complaint until the student gives up. Verifying first makes it
unreachable by construction instead of recoverable by alerting.

**Verification is the student messaging us, not us messaging them.** They send
a six-character code from their own WhatsApp. The message arriving from that
number is the proof, which costs nothing (they speak first, so it is inside
the service window and needs no approved template), needs no OTP vendor, and
has nothing to phish: there is no code travelling *to* them for anyone to talk
them into forwarding.

**Who should own the write, when this is wired.** Not this service. The
Razorpay webhook already lives in the assistant's repository and is the only
thing that writes ``users``; keeping it there is what lets SHELF stay a reader
that cannot mint access. These pages collect and hand off; the provisioning
stays where it is.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from shelf import legal, signup
from shelf.config import settings
from shelf.db import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["site"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Placeholder until the price is read from the assistant's settings. Rupees,
#: because the page shows rupees; paise is the unit Razorpay bills in and the
#: unit that write should use.
RUPEES = 89


def _page(request: Request, name: str, **context: object) -> HTMLResponse:
    """Every page gets the legal details and the price, because the footer and
    the policy pages both need them and neither should reach for them itself."""
    return templates.TemplateResponse(
        request=request,
        name=f"site/{name}",
        context={
            "rupees": RUPEES,
            "d": legal.details(),
            "blanks": legal.missing(),
            "whatsapp_url": f"https://wa.me/{settings.whatsapp_display_number}"
            if settings.whatsapp_display_number
            else "",
            **context,
        },
    )


def _pretty(phone: str) -> str:
    """98765 43210 -- how an Indian mobile is actually read aloud."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    return f"{digits[:5]} {digits[5:]}".strip()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    return _page(request, "landing.html")


@router.post("/verify", response_class=HTMLResponse)
def verify(request: Request, db: DbSession, phone: str = Form(default="")) -> HTMLResponse:
    """Claim the number and show the code to send.

    A number that already belongs to a customer is sent back to the landing
    page rather than charged again -- and told so plainly, because "nothing
    happened" is how a paying student concludes the product is broken.
    """
    if signup.already_a_customer(db, phone) is not None:
        return _page(
            request,
            "landing.html",
            phone=phone,
            error=(
                "That number already has a subscription. Message the assistant "
                "on WhatsApp and it will pick up where you left off."
            ),
        )

    try:
        code, normalised = signup.claim(db, phone, datetime.now(tz=UTC))
    except signup.UnverifiableNumber as refused:
        # Refused now rather than after a payment: a number we cannot reach is
        # a number we must not take money for.
        return _page(request, "landing.html", phone=phone, error=str(refused))
    db.commit()

    number = settings.whatsapp_display_number
    return _page(
        request,
        "verify.html",
        code=code,
        pretty_phone=_pretty(normalised),
        display_number=_pretty(number),
        expires_minutes=int(signup.TTL.total_seconds() // 60),
        # The deep link carries the code, so the student only presses send.
        whatsapp_url=f"https://wa.me/{number}?text={quote(code)}" if number else "",
    )


@router.get("/verify/{code}/state")
def verify_state(code: str, db: DbSession) -> dict[str, str]:
    """What the page polls. ``waiting``, ``verified`` or ``expired``.

    Keyed by the code, so polling reveals nothing the caller did not already
    hold -- they have the code because the page gave it to them. Answering by
    phone number instead would turn this into a way to ask whether a stranger
    is midway through signing up.
    """
    return {"state": signup.state_of(db, code, datetime.now(tz=UTC))}


@router.get("/checkout", response_class=HTMLResponse)
def checkout(request: Request, db: DbSession, code: str = "") -> HTMLResponse:
    """The gate. Nothing unverified gets past here toward a payment.

    Gating the *start* of checkout rather than provisioning is deliberate: if
    nothing unverified can reach the payment provider, everything the webhook
    later sees is verified by construction -- and the webhook stays permissive,
    because a payment taken and not recorded is the one state worse than an
    unverified signup.

    Not wired to Razorpay yet. When it is, this is where the subscription is
    created and the student is handed over; the ``users`` row still comes from
    the webhook, in the assistant's repository, and not from here.
    """
    if not code or signup.state_of(db, code, datetime.now(tz=UTC)) != "verified":
        return _page(
            request,
            "landing.html",
            error="We could not confirm that number. Start again and we'll send a fresh code.",
        )
    return _page(request, "checkout.html")


@router.get("/done", response_class=HTMLResponse)
def done(request: Request) -> HTMLResponse:
    """Where the payment provider returns them. Deliberately promises nothing.

    Access is granted by the webhook, not by this page being reached, so the
    copy says "setting up" rather than "you're in" -- the two are seconds apart
    and occasionally are not.
    """
    number = settings.whatsapp_display_number or ""
    return _page(
        request,
        "done.html",
        display_number=_pretty(number) or "93806 51594",
        whatsapp_url=f"https://wa.me/{number}" if number else "",
    )


# --- the published policies -----------------------------------------------
#
# Served from this repository rather than the assistant's because this is the
# service a customer and a payment aggregator actually visit. The text is
# rendered from one set of settings (``shelf/legal.py``), so the entity name
# and the grievance officer cannot say one thing on the terms page and
# something else on the contact page.


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request) -> HTMLResponse:
    return _page(request, "terms.html")


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> HTMLResponse:
    return _page(request, "privacy.html")


@router.get("/refunds", response_class=HTMLResponse)
def refunds(request: Request) -> HTMLResponse:
    return _page(request, "refunds.html")


@router.get("/cancel", response_class=HTMLResponse)
def cancel(request: Request) -> HTMLResponse:
    return _page(request, "cancel.html")


@router.get("/contact", response_class=HTMLResponse)
def contact(request: Request) -> HTMLResponse:
    return _page(request, "contact.html")


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    """Index the public pages; keep crawlers out of the signed-in ones.

    Not a security control -- ``/files`` is behind a session either way. It
    stops a crawler from spending requests on pages that answer it a sign-in
    prompt, and keeps sign-up URLs carrying a code out of any index.
    """
    disallow = ("/files", "/f/", "/verify", "/checkout")
    return "\n".join(["User-agent: *", *(f"Disallow: {path}" for path in disallow), ""])


def not_found(request: Request, _: Exception) -> Response:
    """A wrong URL on a branded site should not answer in raw JSON.

    Keeps the 404 status: a page that looks like an error but answers 200 is
    worse than the JSON, because every crawler and monitor believes it worked.
    """
    page = _page(request, "notfound.html")
    return HTMLResponse(page.body, status_code=404)
