"""Adherence insights and the AI-written weekly family summary."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models import Circle
from app.services.ai import AiUnavailable, WeeklySummary
from app.services.care import CareService
from app.services.emailer import EmailFailed
from app.web import CsrfDep, MemberDep, ServicesDep, flash, redirect, render

router = APIRouter(prefix="/insights")


def _summaries(circle_id: str) -> str:
    return f"circles/{circle_id}/summaries"


def build_week_facts(care: CareService, circle: Circle, today: date, now: datetime) -> str:
    """Plain-text facts about the last 7 days, used as the model's only input."""
    start = today - timedelta(days=6)
    overall, per_med = care.adherence(circle, start, today, now)
    zone = ZoneInfo(circle.timezone)
    missed_by_hour: dict[str, int] = {}
    day = start
    while day <= today:
        for dose in care.schedule_for_day(circle, day):
            if dose.status is None and dose.scheduled_for < now - timedelta(hours=1):
                slot = dose.scheduled_for.astimezone(zone).strftime("%H:%M")
                missed_by_hour[slot] = missed_by_hour.get(slot, 0) + 1
        day += timedelta(days=1)
    percent = overall.percent if overall.percent is not None else "n/a"
    lines = [
        f"Person: {circle.elder_name}",
        f"Period: {start.isoformat()} to {today.isoformat()}",
        f"Overall: {overall.taken} taken, {overall.skipped} skipped, {overall.missed} missed "
        f"of {overall.expected} doses ({percent}%)",
        "Per medicine:",
    ]
    for name, stats in sorted(per_med.items()):
        lines.append(
            f"- {name}: {stats.taken} taken, {stats.skipped} skipped, {stats.missed} missed"
        )
    if missed_by_hour:
        lines.append(
            "Missed doses by time of day: "
            + ", ".join(f"{slot} x{count}" for slot, count in sorted(missed_by_hour.items()))
        )
    upcoming = care.list_appointments(circle.id, upcoming_from=now)[:2]
    for appt in upcoming:
        lines.append(f"Next appointment: {appt.doctor} ({appt.purpose or 'check-up'})")
    return "\n".join(lines)


@router.get("", response_class=HTMLResponse)
def insights(request: Request, member: MemberDep, services: ServicesDep) -> HTMLResponse:
    care = services.care
    circle = care.get_circle(member.circle_id)
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo(circle.timezone)).date()
    week, week_per_med = care.adherence(circle, today - timedelta(days=6), today, now)
    month, _ = care.adherence(circle, today - timedelta(days=29), today, now)
    saved = care.store.get(_summaries(circle.id), "latest")
    return render(
        request,
        "insights.html",
        member=member,
        circle=circle,
        week=week,
        month=month,
        week_per_med=sorted(week_per_med.items()),
        summary=WeeklySummary.model_validate(saved["summary"]) if saved else None,
        summary_at=saved.get("created_at") if saved else None,
        ai_enabled=services.ai.enabled,
    )


@router.post("/summary")
def generate_summary(
    request: Request, member: MemberDep, services: ServicesDep, _csrf: CsrfDep
) -> RedirectResponse:
    if not services.ai_limiter.hit(f"ai:{member.circle_id}:{member.id}"):
        flash(request, "You have used the AI summary a lot just now. Try again later.", "error")
        return redirect("/insights")
    care = services.care
    circle = care.get_circle(member.circle_id)
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo(circle.timezone)).date()
    try:
        result = services.ai.summarise_week(build_week_facts(care, circle, today, now))
    except AiUnavailable as exc:
        flash(request, str(exc), "error")
        return redirect("/insights")
    except Exception:
        flash(request, "The summary could not be written right now. Please try again.", "error")
        return redirect("/insights")
    care.store.put(
        _summaries(circle.id),
        "latest",
        {"summary": result.model_dump(), "created_at": now.isoformat()},
    )
    flash(request, "Weekly summary ready.", "success")
    return redirect("/insights#summary")


@router.post("/summary/email")
def email_summary(
    request: Request, member: MemberDep, services: ServicesDep, _csrf: CsrfDep
) -> RedirectResponse:
    if not member.can_edit:
        flash(request, "You have view-only access.", "error")
        return redirect("/insights")
    if not services.login_limiter.hit(f"summary-email:{member.circle_id}"):
        flash(request, "The summary was emailed recently. Please wait a few minutes.", "error")
        return redirect("/insights")
    care = services.care
    circle = care.get_circle(member.circle_id)
    saved = care.store.get(_summaries(circle.id), "latest")
    if not saved:
        flash(request, "Generate a summary first.", "error")
        return redirect("/insights")
    summary = WeeklySummary.model_validate(saved["summary"])
    to = sorted({str(m.email) for m in care.list_members(circle.id) if m.email and m.notify})
    if not to:
        flash(request, "Nobody in the circle has an email address yet.", "error")
        return redirect("/insights")
    questions = "\n".join(f"  - {q}" for q in summary.doctor_questions)
    body = (
        f"Weekly update for {circle.elder_name}\n\n{summary.summary}\n\n"
        f"Questions for the next doctor's visit:\n{questions}\n\n"
        f"See details: {services.settings.public_base_url}/insights"
    )
    try:
        services.reminders.emailer.send(
            to, f"This week's care update for {circle.elder_name}", body
        )
    except EmailFailed:
        flash(request, "The email could not be sent just now. Please try again later.", "error")
        return redirect("/insights")
    flash(request, f"Summary emailed to {len(to)} people in the circle.", "success")
    return redirect("/insights")
