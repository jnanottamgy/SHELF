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
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shelf import legal, signup, throttle, visitor
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
            # Who Google says this is, from the signed cookie. Every template
            # gets it, because the header renders on all of them -- and it is
            # never an entitlement: the file browser still asks the database.
            "visitor": visitor.google(request),
            "d": legal.details(),
            "blanks": legal.missing(),
            "whatsapp_url": f"https://wa.me/{settings.whatsapp_display_number}"
            if settings.whatsapp_display_number
            else "",
            **context,
        },
    )


def _signed_in(request: Request) -> visitor.Visitor | None:
    """Who Google says this is, or nothing. Never an entitlement."""
    return visitor.google(request)


def _to_google() -> RedirectResponse:
    return RedirectResponse(url="/auth/google", status_code=303)


def _mask_email(email: str) -> str:
    """``asha@gmail.com`` -> ``a•••@gmail.com``.

    Enough for the right person to recognise their own address and not enough
    for the wrong person to learn a new one. Shown only to somebody who has
    already proved the phone number it belongs to.
    """
    name, _, domain = email.partition("@")
    if not name or not domain:
        return "that account"
    return f"{name[0]}•••@{domain}"


def _owner_conflict(
    request: Request, db: DbSession, phone: str, caller: visitor.Visitor
) -> HTMLResponse | None:
    """Does this number already belong to a *different* Google account?

    Checked when the number is typed and again before checkout, because the
    gap between them is long enough for the first answer to go stale.

    Matched on ``google_sub``: the email is display text and a student can
    change it. A number owned by the same account is not a conflict at all --
    they are simply signing in again, from a browser we have not seen.

    **A row with no ``google_sub`` is not a conflict either.** That is an
    existing customer who paid before Google sign-in existed, and telling them
    their own number belongs to somebody else would be both wrong and a dead
    end. They fall through to the plain "you already have this" message, which
    points them at the assistant -- the one place that can still let them in.
    Linking an old row to a Google account is its own flow and does not exist
    yet; see the note in README.
    """
    owner = signup.already_a_customer(db, phone)
    if owner is None or owner.google_sub is None or owner.google_sub == caller.sub:
        return None
    return _page(
        request,
        "buy.html",
        taken=True,
        masked=_mask_email(owner.email or ""),
        pretty_phone=_pretty(phone),
    )


def _pretty(phone: str) -> str:
    """98765 43210 -- how an Indian mobile is actually read aloud."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    return f"{digits[:5]} {digits[5:]}".strip()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, signin: str = "") -> HTMLResponse:
    """The welcome page, in whichever of its two states applies.

    Signed out it pitches and offers Google; signed in but unpaid it pitches
    and offers Buy now. A *paid* visitor never lands here from the callback --
    they are sent to /files -- but they can still navigate here, and the header
    tells them who they are either way.
    """
    return _page(
        request,
        "landing.html",
        # One message for every way a sign-in can fail. Which one it was is
        # information the caller did not already have.
        error="That sign-in didn't complete. Please try again."
        if signin == "failed"
        else None,
    )


@router.get("/buy", response_class=HTMLResponse)
def buy(request: Request) -> Response:
    """Step two: which WhatsApp number should the assistant file for.

    Behind Google, which is not ceremony. The number step reveals whether a
    number already belongs to a customer, and leaving that reachable by anyone
    makes it a way to ask that question about any number in India. Requiring
    an account does not make enumeration impossible; it makes it attributable
    and rate-limitable, which is the difference that matters.
    """
    caller = _signed_in(request)
    if caller is None:
        return _to_google()
    remembered = visitor.verified_phone(request, datetime.now(tz=UTC))
    return _page(
        request,
        "buy.html",
        remembered=remembered,
        pretty_remembered=_pretty(remembered) if remembered else "",
    )


@router.post("/verify", response_class=HTMLResponse)
def verify(request: Request, db: DbSession, phone: str = Form(default="")) -> Response:
    """Claim the number and show the code to send.

    A number that already belongs to a customer is not charged again. Which
    answer they get depends on *whose* account it is, and that is decided by
    ``google_sub``: their own means they are simply signing in from a new
    browser; somebody else's means a masked hint and a route to a human.
    """
    caller = _signed_in(request)
    if caller is None:
        return _to_google()

    conflict = _owner_conflict(request, db, phone, caller)
    if conflict is not None:
        return conflict

    if signup.already_a_customer(db, phone) is not None:
        return _page(
            request,
            "buy.html",
            phone=phone,
            error=(
                "That number already has a subscription. Message the assistant "
                "on WhatsApp and it will pick up where you left off."
            ),
        )

    # Three keys, all capped: the address, the Google account, and the number
    # being claimed. The account key is the one with teeth now that sign-in is
    # required -- an address is a proxy away from being someone else's, while
    # getting a fresh Google account costs real effort. The number key is what
    # stops a college's numbering block being walked from anywhere at all.
    address = request.client.host if request.client else "unknown"
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    if (
        not throttle.claims.allow(f"ip:{address}")
        or not throttle.claims.allow(f"sub:{caller.sub}")
        or not throttle.claims.allow(f"no:{digits}")
    ):
        logger.info("throttled a signup claim")  # never the number, never the address
        return _page(
            request,
            "buy.html",
            phone=phone,
            error="That's a lot of attempts. Give it a few minutes and try again.",
        )

    try:
        code, normalised = signup.claim(db, phone, datetime.now(tz=UTC))
    except signup.UnverifiableNumber as refused:
        # Refused now rather than after a payment: a number we cannot reach is
        # a number we must not take money for.
        return _page(request, "buy.html", phone=phone, error=str(refused))

    # Bind the code to this browser before it is shown. The polling endpoint is
    # keyed by the code, so anyone who sees it can watch it; spending it is the
    # part that must stay with the browser that started the claim.
    visitor.remember_code(request, code)
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
def checkout(request: Request, db: DbSession, code: str = "") -> Response:
    """The gate. Nothing unverified gets past here toward a payment.

    Gating the *start* of checkout rather than provisioning is deliberate: if
    nothing unverified can reach the payment provider, everything the webhook
    later sees is verified by construction -- and the webhook stays permissive,
    because a payment taken and not recorded is the one state worse than an
    unverified signup.

    Four things must hold, and all four are re-checked here rather than
    trusted from the page that sent them:

    1. somebody is signed in with Google -- the subscription has to belong to
       an account, and that account is named in the Razorpay notes;
    2. the code finished verifying;
    3. **this browser is the one that claimed it.** The polling endpoint is
       public by design, so a code can be watched by anyone who sees it;
    4. the number still does not belong to a different Google account. Checked
       again because minutes have passed since the first check, and the answer
       can have changed in them.

    Not wired to Razorpay yet. When it is, this is where the subscription is
    created and the student is handed over; the ``users`` row still comes from
    the webhook, in the assistant's repository, and not from here.
    """
    caller = _signed_in(request)
    if caller is None:
        return _to_google()

    now = datetime.now(tz=UTC)
    if not code or signup.state_of(db, code, now) != "verified":
        return _page(
            request,
            "buy.html",
            error="We could not confirm that number. Start again and we'll send a fresh code.",
        )
    if not visitor.holds_code(request, code):
        # One message for "wrong browser" and "no such code": which it was is
        # information the caller did not already have.
        logger.info("refused a verified code claimed by another browser")
        return _page(
            request,
            "buy.html",
            error="We could not confirm that number. Start again and we'll send a fresh code.",
        )

    phone = signup.phone_for(db, code)
    if phone is None:  # pragma: no cover - a verified code always has one
        return _page(request, "buy.html", error="Start again and we'll send a fresh code.")

    conflict = _owner_conflict(request, db, phone, caller)
    if conflict is not None:
        return conflict

    # Only now, having finished, is the number worth remembering. A half-typed
    # one is not a fact about anybody.
    visitor.remember_verified_phone(request, phone, now)
    return _page(request, "checkout.html", pretty_phone=_pretty(phone))


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
