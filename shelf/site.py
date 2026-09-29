"""The public site: what a student sees before they are a student.

**UI only, so far.** These routes render; none of them charge anybody or write
a ``users`` row. That is deliberate rather than unfinished -- see the note on
ownership below -- and it means the whole flow can be looked at and argued
with before any money moves.

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
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shelf.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["site"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Placeholder until the price is read from the assistant's settings. Rupees,
#: because the page shows rupees; paise is the unit Razorpay bills in and the
#: unit that write should use.
RUPEES = 89

#: Long enough that a lock-screen glance is not enough to act on, short enough
#: to type. Unambiguous alphabet: no O/0, no I/1.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
CODE_MINUTES = 10


def _page(request: Request, name: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name=f"site/{name}", context={"rupees": RUPEES, **context}
    )


def _pretty(phone: str) -> str:
    """98765 43210 -- how an Indian mobile is actually read aloud."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    return f"{digits[:5]} {digits[5:]}".strip()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    return _page(request, "landing.html")


@router.post("/verify", response_class=HTMLResponse)
def verify(request: Request, phone: str = Form(default="")) -> HTMLResponse:
    """Show the code and the pre-filled WhatsApp link.

    Not wired: the code below is fixed, nothing is stored, and no message is
    watched for. What the page proves today is the shape of the step.
    """
    code = "K7QX4M"  # fixed while this is UI only
    number = settings.whatsapp_display_number or ""
    return _page(
        request,
        "verify.html",
        code=code,
        pretty_phone=_pretty(phone) or "98765 43210",
        display_number=_pretty(number) or "93806 51594",
        expires_minutes=CODE_MINUTES,
        # The deep link carries the code, so the student only presses send.
        whatsapp_url=(
            f"https://wa.me/{number}?text={quote(code)}" if number else ""
        ),
    )


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
