"""The elder's 'Today' view and recording doses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models import DoseStatus
from app.services.care import NotFound, PermissionDenied, summarise_adherence
from app.web import CsrfDep, MemberDep, ServicesDep, flash, redirect, render

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
    pending = [d for d in doses if d.status is None]
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
        zone=zone,
        now=now,
        today=local_today,
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
    return redirect("/today")
