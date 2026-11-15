"""Doctor appointments: Google Calendar and Maps links, reminders, and a printable visit sheet."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote_plus, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.models import Appointment, Member
from app.routes.insights import latest_summary
from app.services.care import NotFound, PermissionDenied, summarise_adherence
from app.web import CsrfDep, MemberDep, Services, ServicesDep, flash, redirect, render

router = APIRouter(prefix="/appointments")


def google_calendar_url(appt: Appointment, elder_name: str) -> str:
    """Build a Google Calendar 'create event' link (no personal notes included)."""
    start = appt.starts_at.astimezone(UTC)
    end = start + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%SZ"
    params = {
        "action": "TEMPLATE",
        "text": f"{elder_name}: {appt.doctor}",
        "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        "details": appt.purpose or "Doctor's appointment",
        "location": appt.location,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def google_maps_url(location: str) -> str:
    """A Google Maps search link for the clinic, so the family can get directions."""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(location)


@router.get("", response_class=HTMLResponse)
def list_appointments(request: Request, member: MemberDep, services: ServicesDep) -> HTMLResponse:
    circle = services.care.get_circle(member.circle_id)
    now = datetime.now(UTC)
    items = services.care.list_appointments(circle.id)
    upcoming = [a for a in items if a.starts_at >= now]
    past = [a for a in items if a.starts_at < now][-5:]
    return render(
        request,
        "appointments.html",
        member=member,
        circle=circle,
        upcoming=upcoming,
        past=list(reversed(past)),
        zone=ZoneInfo(circle.timezone),
        calendar_url=lambda a: google_calendar_url(a, circle.elder_name),
        maps_url=google_maps_url,
    )


@router.get("/{appointment_id}/sheet", response_class=HTMLResponse)
def visit_sheet(
    request: Request, appointment_id: str, member: MemberDep, services: ServicesDep
) -> Response:
    """One printable page to take to the doctor: medicines, adherence, feelings, questions."""
    care = services.care
    try:
        appt = care.get_appointment(member.circle_id, appointment_id)
    except NotFound:
        flash(request, "That appointment was not found.", "error")
        return redirect("/appointments")
    circle = care.get_circle(member.circle_id)
    zone = ZoneInfo(circle.timezone)
    now = datetime.now(UTC)
    today = now.astimezone(zone).date()
    overall, per_med = summarise_adherence(
        care.schedule_for_days(circle, today - timedelta(days=29), today), now
    )
    check_ins = care.list_check_ins(circle.id, today - timedelta(days=13), today)
    summary, _ = latest_summary(care, circle.id)
    return render(
        request,
        "visit_sheet.html",
        member=member,
        circle=circle,
        appt=appt,
        when=appt.starts_at.astimezone(zone),
        today=today,
        meds=care.list_medications(circle.id),
        overall=overall,
        per_med=per_med,
        check_ins=check_ins,
        unwell=[c for c in check_ins if c.feeling.value != "good"],
        good_days=sum(1 for c in check_ins if c.feeling.value == "good"),
        summary=summary,
    )


def _form_page(
    request: Request, member: Member, services: Services, appt: Appointment | None
) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/appointments")
    circle = services.care.get_circle(member.circle_id)
    local = appt.starts_at.astimezone(ZoneInfo(circle.timezone)) if appt else None
    return render(
        request,
        "appointment_form.html",
        member=member,
        circle=circle,
        appt=appt,
        local_value=local.strftime("%Y-%m-%dT%H:%M") if local else "",
    )


@router.get("/new", response_class=HTMLResponse)
def new_appointment(request: Request, member: MemberDep, services: ServicesDep) -> Response:
    return _form_page(request, member, services, None)


@router.get("/{appointment_id}/edit", response_class=HTMLResponse)
def edit_appointment(
    request: Request, appointment_id: str, member: MemberDep, services: ServicesDep
) -> Response:
    try:
        appt = services.care.get_appointment(member.circle_id, appointment_id)
    except NotFound:
        flash(request, "That appointment was not found.", "error")
        return redirect("/appointments")
    return _form_page(request, member, services, appt)


def _save(
    request: Request,
    member: Member,
    services: Services,
    appointment_id: str | None,
    doctor: str,
    purpose: str,
    location: str,
    starts_at: str,
    notes: str,
) -> RedirectResponse:
    circle = services.care.get_circle(member.circle_id)
    try:
        local = datetime.fromisoformat(starts_at)
        when = local.replace(tzinfo=ZoneInfo(circle.timezone)) if local.tzinfo is None else local
        services.care.save_appointment(
            member,
            appointment_id=appointment_id,
            doctor=doctor.strip(),
            purpose=purpose.strip(),
            location=location.strip(),
            starts_at=when,
            notes=notes.strip(),
        )
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
        return redirect("/appointments")
    except NotFound:
        flash(request, "That appointment was not found.", "error")
        return redirect("/appointments")
    except (ValidationError, ValueError):
        flash(request, "Please enter the doctor's name and a valid date and time.", "error")
        target = f"/appointments/{appointment_id}/edit" if appointment_id else "/appointments/new"
        return redirect(target)
    flash(request, "Appointment saved. Everyone in the circle will get reminders.", "success")
    return redirect("/appointments")


@router.post("")
def create_appointment(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    doctor: Annotated[str, Form(max_length=80)],
    starts_at: Annotated[str, Form(max_length=25)],
    purpose: Annotated[str, Form(max_length=120)] = "",
    location: Annotated[str, Form(max_length=120)] = "",
    notes: Annotated[str, Form(max_length=500)] = "",
) -> RedirectResponse:
    return _save(request, member, services, None, doctor, purpose, location, starts_at, notes)


@router.post("/{appointment_id}")
def update_appointment(
    request: Request,
    appointment_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    doctor: Annotated[str, Form(max_length=80)],
    starts_at: Annotated[str, Form(max_length=25)],
    purpose: Annotated[str, Form(max_length=120)] = "",
    location: Annotated[str, Form(max_length=120)] = "",
    notes: Annotated[str, Form(max_length=500)] = "",
) -> RedirectResponse:
    return _save(
        request, member, services, appointment_id, doctor, purpose, location, starts_at, notes
    )


@router.post("/{appointment_id}/delete")
def delete_appointment(
    request: Request,
    appointment_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
) -> RedirectResponse:
    try:
        services.care.delete_appointment(member, appointment_id)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except NotFound:
        flash(request, "That appointment was not found.", "error")
    else:
        flash(request, "Appointment removed.", "success")
    return redirect("/appointments")
