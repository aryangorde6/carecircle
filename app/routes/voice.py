"""Voice notes: the elder says what they took and how they feel, in their own language.

Gemini listens and suggests updates against today's numbered dose list. Nothing
is saved until a person ticks and confirms each suggestion on a review page,
and every save goes through the same permission checks as the normal buttons.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from app.models import DoseStatus, Feeling, ScheduledDose
from app.routes.today import share_bad_day
from app.services.ai import AiUnavailable, VoiceNote
from app.services.care import NotFound, PermissionDenied
from app.uploads import AudioRejected, validate_audio
from app.web import CsrfDep, MemberDep, ServicesDep, flash, redirect, render

router = APIRouter(prefix="/voice")
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 3 * 1024 * 1024  # about a minute in any common format


@dataclass(frozen=True)
class DoseSuggestion:
    """One dose update Gemini heard, ready to show as a tick box."""

    label: str
    status: DoseStatus
    value: str  # "medication_id|scheduled_for|status", re-checked on save


def dose_list_for_prompt(doses: list[ScheduledDose], zone: ZoneInfo, now: datetime) -> str:
    """Today's doses, numbered, as plain data for the model."""
    lines = [f"Time now: {now.astimezone(zone):%H:%M}"]
    for number, dose in enumerate(doses, start=1):
        med = dose.medication
        details = "; ".join(part for part in (med.dosage, med.instructions) if part)
        state = dose.status.value if dose.status else "not recorded yet"
        lines.append(
            f"{number}. {dose.scheduled_for.astimezone(zone):%H:%M} {med.name}"
            f"{f' ({details})' if details else ''}: {state}"
        )
    return "\n".join(lines)


def suggestions_from(
    note: VoiceNote, doses: list[ScheduledDose], zone: ZoneInfo
) -> list[DoseSuggestion]:
    """Keep only suggestions that point at a real dose in today's list."""
    found: dict[int, DoseSuggestion] = {}
    for item in note.doses:
        if not 1 <= item.number <= len(doses) or item.status not in {"taken", "skipped"}:
            continue
        dose = doses[item.number - 1]
        status = DoseStatus(item.status)
        when = dose.scheduled_for.astimezone(zone)
        med = dose.medication
        name = f"{med.name} {med.dosage}".strip()
        found[item.number] = DoseSuggestion(
            label=f"{name}, {when:%I:%M %p}",
            status=status,
            value=f"{med.id}|{dose.scheduled_for.isoformat()}|{status.value}",
        )
    return [found[n] for n in sorted(found)]


@router.post("", response_class=HTMLResponse)
async def understand_voice_note(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    audio: Annotated[UploadFile, File()],
) -> Response:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/today")
    if not services.ai_limiter.hit(f"ai:{member.circle_id}:{member.id}"):
        flash(request, "That was a lot of voice notes just now. Please try again later.", "error")
        return redirect("/today#voice")
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    care = services.care
    circle = care.get_circle(member.circle_id)
    zone = ZoneInfo(circle.timezone)
    now = datetime.now(UTC)
    doses = care.schedule_for_day(circle, now.astimezone(zone).date())
    try:
        mime = validate_audio(data, MAX_AUDIO_BYTES)
        note = await run_in_threadpool(
            services.ai.understand_voice, data, mime, dose_list_for_prompt(doses, zone, now)
        )
    except (AudioRejected, AiUnavailable) as exc:
        flash(request, str(exc), "error")
        return redirect("/today#voice")
    except Exception:
        logger.exception("voice note failed", extra={"circle": circle.id, "bytes": len(data)})
        flash(request, "Sorry, that voice note could not be understood. Please try again.", "error")
        return redirect("/today#voice")
    feeling = note.feeling if note.feeling in {f.value for f in Feeling} else ""
    return render(
        request,
        "voice_review.html",
        member=member,
        circle=circle,
        note=note,
        suggestions=suggestions_from(note, doses, zone),
        feeling=Feeling(feeling) if feeling else None,
    )


@router.post("/save")
def save_voice_note(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    dose: Annotated[list[str], Form(max_length=20)] = [],  # noqa: B006 - FastAPI form default
    feeling: Annotated[str, Form(max_length=10)] = "",
    note: Annotated[str, Form(max_length=280)] = "",
    tell: Annotated[str, Form(max_length=3)] = "",
) -> RedirectResponse:
    """Save only what the person ticked, through the normal permission checks."""
    care = services.care
    saved: list[str] = []
    for value in dose:
        try:
            medication_id, when, status = value.split("|")
            log = care.record_dose(
                member, medication_id, datetime.fromisoformat(when), DoseStatus(status)
            )
        except PermissionDenied as exc:
            flash(request, str(exc), "error")
            return redirect("/today")
        except (ValueError, NotFound):
            continue
        med = care.get_medication(member.circle_id, log.medication_id)
        saved.append(f"{med.name} {'taken' if log.status == DoseStatus.TAKEN else 'skipped'}")
    message = f"Saved: {', '.join(saved)}." if saved else ""
    if feeling in {f.value for f in Feeling}:
        circle = care.get_circle(member.circle_id)
        now = datetime.now(UTC)
        try:
            entry = care.record_check_in(
                member,
                now.astimezone(ZoneInfo(circle.timezone)).date(),
                Feeling(feeling),
                note,
                now,
            )
        except PermissionDenied as exc:
            flash(request, str(exc), "error")
            return redirect("/today")
        message += f" Today's feeling: {entry.feeling.label}."
        if tell and entry.feeling == Feeling.UNWELL:
            message += share_bad_day(request, services, circle, member, entry, now)
    flash(request, message.strip() or "Nothing was ticked, so nothing was saved.", "success")
    return redirect("/today")
