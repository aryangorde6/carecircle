"""The emergency card: a QR code for the fridge or wallet.

Anyone who scans it (a neighbour, a doctor, an ambulance crew) sees the
elder's current medicines, allergies and emergency contacts without a
passcode. The elder alone turns it on or off, and the circle is emailed
whenever it is opened, so the card doubles as an early alarm.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import segno
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.models import BLOOD_GROUPS, Circle, Medication
from app.services.care import CareService, PermissionDenied
from app.services.emailer import EmailFailed
from app.web import CsrfDep, MemberDep, Services, ServicesDep, client_ip, flash, redirect, render

router = APIRouter()
logger = logging.getLogger(__name__)

CARD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
# Headers that keep the public card out of search engines, caches and referrers.
PRIVATE_PAGE_HEADERS = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def card_url(services: Services, circle: Circle) -> str:
    return f"{services.settings.public_base_url}/card/{circle.card_id}"


def qr_data_uri(url: str) -> str:
    """An SVG QR code as a data URI (no external service, allowed by the CSP)."""
    return str(segno.make_qr(url, error="m").svg_data_uri(scale=8, border=2))


def current_medicines(care: CareService, circle: Circle, now: datetime) -> list[Medication]:
    today = now.astimezone(ZoneInfo(circle.timezone)).date()
    return [m for m in care.list_medications(circle.id) if m.is_scheduled_on(today)]


@router.get("/card/{card_id}", response_class=HTMLResponse)
def public_card(request: Request, card_id: str, services: ServicesDep) -> HTMLResponse:
    """The page the QR code opens. No sign-in, read-only, and only what helps in an emergency."""
    if not CARD_ID_PATTERN.match(card_id):
        raise HTTPException(404, "This emergency card is not active.")
    if not services.public_limiter.hit(f"card:{client_ip(request)}"):
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    now = datetime.now(UTC)
    circle = services.care.open_card(card_id, now)
    if circle is None:
        raise HTTPException(404, "This emergency card is not active.")
    logger.info("emergency card opened", extra={"circle": circle.id})
    _tell_circle(services, circle, now)
    response = render(
        request,
        "emergency_card.html",
        card=circle,
        meds=current_medicines(services.care, circle, now),
        contact_lines=[line for line in circle.emergency_contacts.splitlines() if line.strip()],
    )
    response.headers.update(PRIVATE_PAGE_HEADERS)
    return response


def _tell_circle(services: Services, circle: Circle, now: datetime) -> None:
    """Email the circle that the card was opened, at most once every 30 minutes."""
    members = services.care.list_members(circle.id)
    to = sorted({str(m.email) for m in members if m.email and m.notify})
    if not to or not services.alert_limiter.hit(f"card-opened:{circle.id}"):
        return
    local = now.astimezone(ZoneInfo(circle.timezone))
    body = "\n".join(
        [
            f"Someone opened {circle.elder_name}'s emergency card at {local:%I:%M %p} "
            f"on {local:%d %B}.",
            "",
            "This can mean a neighbour, doctor or ambulance crew needed the medicine list.",
            f"Please call {circle.elder_name}, or someone with them, to check they are okay.",
            "If it was you, there is nothing to do.",
            "",
            f"Open CareCircle: {services.settings.public_base_url}/circle#card",
            "In an emergency, call your local emergency number (112 in India).",
        ]
    )
    try:
        services.reminders.emailer.send(
            to, f"{circle.elder_name}'s emergency card was just opened", body
        )
    except EmailFailed:
        logger.warning("card-opened email failed", extra={"circle": circle.id})


@router.post("/circle/health")
def update_health_info(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    blood_group: Annotated[str, Form(max_length=3)] = "",
    allergies: Annotated[str, Form(max_length=200)] = "",
    conditions: Annotated[str, Form(max_length=200)] = "",
    emergency_contacts: Annotated[str, Form(max_length=300)] = "",
) -> RedirectResponse:
    if blood_group and blood_group not in BLOOD_GROUPS:
        flash(request, "Please choose a blood group from the list.", "error")
        return redirect("/circle#card")
    try:
        services.care.update_health_info(
            member,
            blood_group=blood_group,
            allergies=allergies,
            conditions=conditions,
            emergency_contacts=emergency_contacts,
        )
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except ValidationError:
        flash(request, "Please check the details and try again.", "error")
    else:
        flash(request, "The emergency card details were saved.", "success")
    return redirect("/circle#card")


@router.post("/circle/card/on")
def turn_card_on(
    request: Request, member: MemberDep, services: ServicesDep, _csrf: CsrfDep
) -> RedirectResponse:
    """Turn the card on, or make a new link so an old printed QR code stops working."""
    try:
        circle = services.care.get_circle(member.circle_id)
        replacing = circle.card_id is not None
        services.care.enable_card(member)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    else:
        flash(
            request,
            "New link made. Old printed cards no longer work; print the new one."
            if replacing
            else "The emergency card is on. Print it and keep it on the fridge.",
            "success",
        )
    return redirect("/circle#card")


@router.post("/circle/card/off")
def turn_card_off(
    request: Request, member: MemberDep, services: ServicesDep, _csrf: CsrfDep
) -> RedirectResponse:
    try:
        services.care.disable_card(member)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, "The emergency card is off. Its QR code no longer works.", "success")
    return redirect("/circle#card")


@router.get("/circle/card/print", response_class=HTMLResponse)
def print_card(request: Request, member: MemberDep, services: ServicesDep) -> Response:
    """A printable fridge card with the QR code."""
    circle = services.care.get_circle(member.circle_id)
    if not circle.card_id:
        flash(request, "Turn the emergency card on first.", "error")
        return redirect("/circle#card")
    url = card_url(services, circle)
    return render(
        request,
        "card_print.html",
        member=member,
        circle=circle,
        qr=qr_data_uri(url),
        url=url,
        meds=current_medicines(services.care, circle, datetime.now(UTC)),
    )
