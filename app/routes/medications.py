"""Medication list, add/edit forms, and reading a prescription photo with Gemini."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.models import Member
from app.services.ai import AiUnavailable
from app.services.care import NotFound, PermissionDenied
from app.uploads import UploadRejected, validate_image
from app.web import CsrfDep, MemberDep, Services, ServicesDep, flash, redirect, render

router = APIRouter(prefix="/medications")

MAX_TIMES = 8


def _parse_times(raw: list[str]) -> list[str]:
    return [t.strip() for t in raw if t and t.strip()][:MAX_TIMES]


def _parse_date(raw: str) -> date | None:
    return date.fromisoformat(raw) if raw.strip() else None


def _parse_supply(raw: str) -> int | None:
    """Empty means 'not tracked'; otherwise a whole number of doses."""
    return int(raw) if raw.strip() else None


@router.get("", response_class=HTMLResponse)
def list_medications(request: Request, member: MemberDep, services: ServicesDep) -> HTMLResponse:
    circle = services.care.get_circle(member.circle_id)
    meds = services.care.list_medications(circle.id, include_inactive=True)
    return render(
        request,
        "medications.html",
        member=member,
        circle=circle,
        active=[m for m in meds if m.active],
        stopped=[m for m in meds if not m.active],
        ai_enabled=services.ai.enabled,
    )


@router.get("/new", response_class=HTMLResponse)
def new_medication(request: Request, member: MemberDep, services: ServicesDep) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/medications")
    circle = services.care.get_circle(member.circle_id)
    today = datetime.now(UTC).astimezone(ZoneInfo(circle.timezone)).date()
    return render(
        request, "medication_form.html", member=member, circle=circle, med=None, today=today
    )


@router.get("/scan", response_class=HTMLResponse)
def scan_form(request: Request, member: MemberDep, services: ServicesDep) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/medications")
    circle = services.care.get_circle(member.circle_id)
    return render(
        request, "scan.html", member=member, circle=circle, ai_enabled=services.ai.enabled
    )


@router.post("/scan", response_class=HTMLResponse)
async def scan_prescription(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    photo: Annotated[UploadFile, File()],
) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/medications")
    if not services.ai_limiter.hit(f"ai:{member.circle_id}:{member.id}"):
        flash(request, "You have used the AI reader a lot just now. Try again later.", "error")
        return redirect("/medications/scan")
    limit = services.settings.max_upload_bytes
    data = await photo.read(limit + 1)
    try:
        mime = validate_image(data, limit)
        extracted = services.ai.read_prescription(data, mime)
    except (UploadRejected, AiUnavailable) as exc:
        flash(request, str(exc), "error")
        return redirect("/medications/scan")
    except Exception:
        flash(request, "The photo could not be read. Please try a clearer photo.", "error")
        return redirect("/medications/scan")
    circle = services.care.get_circle(member.circle_id)
    today = datetime.now(UTC).astimezone(ZoneInfo(circle.timezone)).date()
    return render(
        request, "scan_review.html", member=member, circle=circle, found=extracted, today=today
    )


@router.get("/{medication_id}/edit", response_class=HTMLResponse)
def edit_medication(
    request: Request, medication_id: str, member: MemberDep, services: ServicesDep
) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/medications")
    try:
        med = services.care.get_medication(member.circle_id, medication_id)
    except NotFound:
        flash(request, "That medicine was not found.", "error")
        return redirect("/medications")
    circle = services.care.get_circle(member.circle_id)
    return render(
        request, "medication_form.html", member=member, circle=circle, med=med, today=None
    )


def _save(
    request: Request,
    member: Member,
    services: Services,
    *,
    medication_id: str | None,
    name: str,
    dosage: str,
    instructions: str,
    times: list[str],
    start_date: str,
    end_date: str,
    supply: str,
) -> RedirectResponse:
    try:
        start = _parse_date(start_date) or datetime.now(UTC).date()
        end = _parse_date(end_date)
        if end is not None and end < start:
            raise ValueError("end before start")
        med = services.care.save_medication(
            member,
            medication_id=medication_id,
            name=name.strip(),
            dosage=dosage.strip(),
            instructions=instructions.strip(),
            times=_parse_times(times),
            start_date=start,
            end_date=end,
            supply=_parse_supply(supply),
        )
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
        return redirect("/medications")
    except NotFound:
        flash(request, "That medicine was not found.", "error")
        return redirect("/medications")
    except (ValidationError, ValueError):
        flash(
            request,
            "Please check the form: a name, at least one time (HH:MM), valid dates "
            "and a whole number of doses left are needed.",
            "error",
        )
        return redirect(
            f"/medications/{medication_id}/edit" if medication_id else "/medications/new"
        )
    flash(request, f"Saved {med.name}.", "success")
    return redirect("/medications")


@router.post("")
def create_medication(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    name: Annotated[str, Form(max_length=80)],
    times: Annotated[list[str], Form()],
    dosage: Annotated[str, Form(max_length=60)] = "",
    instructions: Annotated[str, Form(max_length=200)] = "",
    start_date: Annotated[str, Form(max_length=10)] = "",
    end_date: Annotated[str, Form(max_length=10)] = "",
    supply: Annotated[str, Form(max_length=4)] = "",
) -> RedirectResponse:
    return _save(
        request,
        member,
        services,
        medication_id=None,
        name=name,
        dosage=dosage,
        instructions=instructions,
        times=times,
        start_date=start_date,
        end_date=end_date,
        supply=supply,
    )


@router.post("/{medication_id}")
def update_medication(
    request: Request,
    medication_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    name: Annotated[str, Form(max_length=80)],
    times: Annotated[list[str], Form()],
    dosage: Annotated[str, Form(max_length=60)] = "",
    instructions: Annotated[str, Form(max_length=200)] = "",
    start_date: Annotated[str, Form(max_length=10)] = "",
    end_date: Annotated[str, Form(max_length=10)] = "",
    supply: Annotated[str, Form(max_length=4)] = "",
) -> RedirectResponse:
    return _save(
        request,
        member,
        services,
        medication_id=medication_id,
        name=name,
        dosage=dosage,
        instructions=instructions,
        times=times,
        start_date=start_date,
        end_date=end_date,
        supply=supply,
    )


@router.post("/{medication_id}/refill")
def refill_medication(
    request: Request,
    medication_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    added: Annotated[str, Form(max_length=4)] = "",
) -> RedirectResponse:
    try:
        med = services.care.refill_medication(member, medication_id, int(added))
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except NotFound:
        flash(request, "That medicine was not found.", "error")
    except ValueError:
        flash(request, "Enter how many doses you bought, from 1 to 1000.", "error")
    else:
        flash(
            request,
            f"{med.name}: {med.supply} doses in hand, about {med.days_left} days.",
            "success",
        )
    return redirect("/medications")


@router.post("/{medication_id}/stop")
def stop_medication(
    request: Request,
    medication_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
) -> RedirectResponse:
    try:
        services.care.stop_medication(member, medication_id)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except NotFound:
        flash(request, "That medicine was not found.", "error")
    else:
        flash(request, "Medicine stopped. Its history is kept.", "success")
    return redirect("/medications")
