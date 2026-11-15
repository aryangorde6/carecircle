"""The elder's 'Today' view, recording doses, and the daily 'how are you feeling' check-in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models import CheckIn, Circle, DoseStatus, Feeling, Member
from app.services.care import NotFound, PermissionDenied, summarise_adherence
from app.services.emailer import EmailFailed
from app.web import CsrfDep, MemberDep, Services, ServicesDep, flash, redirect, render

router = APIRouter()


@router.get("/today", response_class=HTMLResponse)
def today(request: Request, member: MemberDep, services: ServicesDep) -> HTMLResponse:
    care = services.care
    circle = care.get_circle(member.circle_id)
    zone = ZoneInfo(circle.timezone)
    now = datetime.now(UTC)
    local_today = now.astimezone(zone).date()
    # One read of the last 7 days serves both today's list and the weekly figure.
    week_doses = care.schedule_for_days(circle, local_today - timedelta(days=6), local_today)
    doses = [d for d in week_doses if d.scheduled_for.astimezone(zone).date() == local_today]
    week, _ = summarise_adherence(week_doses, now)
    appointments = care.list_appointments(circle.id, upcoming_from=now)[:3]
    todays_check_in = care.list_check_ins(circle.id, local_today, local_today)
    pending = [d for d in doses if d.status is None]
    meds = {d.medication.id: d.medication for d in week_doses}
    low_meds = sorted((m for m in meds.values() if m.running_low), key=lambda m: m.days_left or 0)
    return render(
        request,
        "today.html",
        member=member,
        circle=circle,
        doses=doses,
        pending_count=len(pending),
        next_dose=pending[0] if pending else None,
        week=week,
        appointments=appointments,
        low_meds=low_meds,
        check_in=todays_check_in[0] if todays_check_in else None,
        zone=zone,
        now=now,
        today=local_today,
        voice_enabled=services.ai.enabled and member.can_edit,
    )


@router.post("/doses")
def record_dose(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    medication_id: Annotated[str, Form(max_length=64)],
    scheduled_for: Annotated[str, Form(max_length=40)],
    status: Annotated[DoseStatus, Form()],
) -> RedirectResponse:
    try:
        when = datetime.fromisoformat(scheduled_for)
        log = services.care.record_dose(member, medication_id, when, status)
    except (ValueError, NotFound):
        flash(request, "That dose could not be found.", "error")
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    else:
        med = services.care.get_medication(member.circle_id, log.medication_id)
        verb = "taken" if status == DoseStatus.TAKEN else "skipped"
        flash(request, f"{med.name} marked as {verb}.", "success")
        # The entry that was just stamped gets the stamp-down animation.
        return redirect(f"/today?stamped={quote(log.id)}")
    return redirect("/today")


@router.post("/checkin")
def check_in(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    feeling: Annotated[Feeling, Form()],
    note: Annotated[str, Form(max_length=280)] = "",
    tell: Annotated[str, Form(max_length=3)] = "",
) -> RedirectResponse:
    """Record how the elder feels. Emailing the circle is the elder's choice, never automatic."""
    care = services.care
    circle = care.get_circle(member.circle_id)
    now = datetime.now(UTC)
    zone = ZoneInfo(circle.timezone)
    try:
        entry = care.record_check_in(member, now.astimezone(zone).date(), feeling, note, now)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
        return redirect("/today")
    message = "Thank you. The circle can see how today is going."
    if tell and feeling == Feeling.UNWELL:
        message += share_bad_day(request, services, circle, member, entry, now)
    flash(request, message, "success")
    return redirect("/today#checkin")


def share_bad_day(
    request: Request,
    services: Services,
    circle: Circle,
    member: Member,
    entry: CheckIn,
    now: datetime,
) -> str:
    """Email the circle about a bad day, only because the elder chose to. Returns a status note."""
    care = services.care
    zone = ZoneInfo(circle.timezone)
    to = sorted(
        {
            str(m.email)
            for m in care.list_members(circle.id)
            if m.email and m.notify and m.id != member.id
        }
    )
    if not to:
        return " Nobody else in the circle has an email address yet."
    if not services.login_limiter.hit(f"checkin-email:{circle.id}"):
        return " The circle was emailed a few minutes ago, so no new email was sent."
    body = "\n".join(
        [
            f'{circle.elder_name} marked today as "Not well" in CareCircle.',
            *([f"Note: {entry.note}"] if entry.note else []),
            f"Shared by {member.name} at {now.astimezone(zone):%I:%M %p}.",
            "",
            "A call or a visit could help.",
            f"Open CareCircle: {services.settings.public_base_url}/today",
            "",
            "In an emergency, call your local emergency number (112 in India).",
        ]
    )
    try:
        services.reminders.emailer.send(to, f"{circle.elder_name} is not feeling well today", body)
    except EmailFailed:
        flash(request, "The email could not be sent just now.", "error")
        return ""
    people = "1 person" if len(to) == 1 else f"{len(to)} people"
    return f" We emailed {people} in the circle."
