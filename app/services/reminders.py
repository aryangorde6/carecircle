"""Proactive email reminders with an escalation ladder.

Cloud Scheduler calls ``/tasks/reminders`` every few minutes. Each run is
idempotent: every dose and appointment remembers which reminders it has
already triggered, so overlapping runs never send duplicates.

Ladder for a dose that has not been marked:
  * up to 15 min before   -> "upcoming" to the elder
  * 30 min after          -> "missed" to the elder and caretakers
  * 60 min after          -> "escalated" to family as well
Appointments get a reminder 24 hours and 2 hours before, to everyone.
A medicine with about five days of supply left triggers one refill email to
the elder and caretakers; buying more re-arms it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import REFILL_DAYS, Circle, DoseLog, Medication, Member, Role, ScheduledDose
from app.services.care import CareService, dose_log_id
from app.services.emailer import Emailer

logger = logging.getLogger(__name__)

UPCOMING_WINDOW = timedelta(minutes=15)
MISSED_AFTER = timedelta(minutes=30)
ESCALATE_AFTER = timedelta(minutes=60)
STALE_AFTER = timedelta(hours=6)
APPOINTMENT_STEPS = (("appt-24h", timedelta(hours=24)), ("appt-2h", timedelta(hours=2)))


@dataclass
class ReminderReport:
    """Counts from one run, safe to log (no personal data)."""

    circles: int = 0
    emails: int = 0
    kinds: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str) -> None:
        self.emails += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1


def _emails(members: Iterable[Member]) -> list[str]:
    return sorted({str(m.email) for m in members if m.email and m.notify})


def dose_stage(dose: ScheduledDose, now: datetime) -> str | None:
    """Which reminder stage applies to an unmarked dose right now."""
    if dose.status is not None:
        return None
    delta = now - dose.scheduled_for
    if delta >= STALE_AFTER:
        return None
    if delta >= ESCALATE_AFTER:
        return "escalated"
    if delta >= MISSED_AFTER:
        return "missed"
    if timedelta(0) <= -delta <= UPCOMING_WINDOW:
        return "upcoming"
    return None


class ReminderService:
    """Works out which reminders are due and sends them."""

    def __init__(self, care: CareService, emailer: Emailer, base_url: str) -> None:
        self.care = care
        self.emailer = emailer
        self.base_url = base_url

    def run(self, now: datetime | None = None) -> ReminderReport:
        current = now or datetime.now(UTC)
        report = ReminderReport()
        for circle in self.care.list_circles():
            report.circles += 1
            try:
                self._run_circle(circle, current, report)
            except Exception:  # one bad circle must not stop the others
                logger.exception("reminder run failed for one circle")
        logger.info("reminders: circles=%d emails=%d", report.circles, report.emails)
        return report

    def _run_circle(self, circle: Circle, now: datetime, report: ReminderReport) -> None:
        members = self.care.list_members(circle.id)
        elder = [m for m in members if m.role == Role.ELDER]
        caretakers = [m for m in members if m.role == Role.CARETAKER]
        family = [m for m in members if m.role == Role.FAMILY]
        recipients = {
            # If the elder has no email, caretakers get the gentle reminder too.
            "upcoming": elder if _emails(elder) else caretakers,
            "missed": elder + caretakers,
            "escalated": elder + caretakers + family,
        }

        local_today = now.astimezone(ZoneInfo(circle.timezone)).date()
        doses = self.care.schedule_for_days(circle, local_today - timedelta(days=1), local_today)
        due: dict[str, list[ScheduledDose]] = {}
        for dose in doses:
            stage = dose_stage(dose, now)
            if stage and stage not in (dose.log.reminders_sent if dose.log else []):
                due.setdefault(stage, []).append(dose)

        for stage, stage_doses in due.items():
            to = _emails(recipients[stage])
            if to:
                subject, body = self._dose_message(circle, stage, stage_doses)
                self.emailer.send(to, subject, body)
                report.add(stage)
            for dose in stage_doses:
                self._mark_dose(circle, dose, stage)

        # Refill alerts reuse the medicines already loaded with the schedule.
        meds = {dose.medication.id: dose.medication for dose in doses}
        low = sorted(
            (m for m in meds.values() if m.running_low and not m.refill_alert_sent),
            key=lambda m: m.name.lower(),
        )
        if low:
            to = _emails(elder + caretakers)
            if to:
                self.emailer.send(to, *self._refill_message(circle, low))
                report.add("refill")
            for med in low:
                med.refill_alert_sent = True
                self.care.save_medication_record(med)

        everyone = _emails(members)
        for appt in self.care.list_appointments(circle.id, upcoming_from=now):
            reached = [kind for kind, lead in APPOINTMENT_STEPS if appt.starts_at - now <= lead]
            new = [kind for kind in reached if kind not in appt.reminders_sent]
            if not new:
                continue
            # One email even if several steps were reached at once (late-added appointment).
            if everyone:
                local = appt.starts_at.astimezone(ZoneInfo(circle.timezone))
                self.emailer.send(
                    everyone,
                    f"Appointment reminder: {appt.doctor} at {local:%a %d %b, %I:%M %p}",
                    self._appointment_body(circle, appt.doctor, appt.purpose, appt.location, local),
                )
                report.add(new[-1])
            appt.reminders_sent = sorted({*appt.reminders_sent, *reached})
            self.care.save_appointment_record(appt)

    def _mark_dose(self, circle: Circle, dose: ScheduledDose, stage: str) -> None:
        log = dose.log or DoseLog(
            id=dose_log_id(dose.medication.id, dose.scheduled_for),
            circle_id=circle.id,
            medication_id=dose.medication.id,
            scheduled_for=dose.scheduled_for.astimezone(UTC),
        )
        # Reaching a later stage implies the earlier ones are done.
        order = ["upcoming", "missed", "escalated"]
        log.reminders_sent = sorted({*log.reminders_sent, *order[: order.index(stage) + 1]})
        self.care.save_dose_log(log)

    def _dose_message(
        self, circle: Circle, stage: str, doses: list[ScheduledDose]
    ) -> tuple[str, str]:
        zone = ZoneInfo(circle.timezone)
        lines = [
            f"  - {d.medication.name} {d.medication.dosage}".rstrip()
            + f" at {d.scheduled_for.astimezone(zone):%I:%M %p}"
            + (f" ({d.medication.instructions})" if d.medication.instructions else "")
            for d in doses
        ]
        name = circle.elder_name
        if stage == "upcoming":
            subject = f"Medicine time soon for {name}"
            intro = f"Hello! {name} has medicine due in the next few minutes:"
        elif stage == "missed":
            subject = f"Missed medicine: {name}"
            intro = f"These doses for {name} have not been marked as taken yet:"
        else:
            subject = f"Still not taken: please check on {name}"
            intro = (
                f"It has been over an hour and these doses for {name} are still not marked. "
                "Could someone in the family check in?"
            )
        body = "\n".join(
            [
                intro,
                "",
                *lines,
                "",
                f"Open CareCircle to mark them: {self.base_url}/today",
                "",
                "You get this email because you are in this CareCircle.",
            ]
        )
        return subject, body

    def _refill_message(self, circle: Circle, meds: list[Medication]) -> tuple[str, str]:
        lines = [
            f"  - {m.name} {m.dosage}".rstrip()
            + f": about {m.days_left} days left ({m.supply} doses)"
            for m in meds
        ]
        body = "\n".join(
            [
                f"Time to buy more medicine for {circle.elder_name}. "
                f"These have {REFILL_DAYS} days or less left:",
                "",
                *lines,
                "",
                f"After buying, press Refilled in CareCircle: {self.base_url}/medications",
                "",
                "You get this email because you are in this CareCircle.",
            ]
        )
        return f"Running low on medicine for {circle.elder_name}", body

    def _appointment_body(
        self, circle: Circle, doctor: str, purpose: str, location: str, local: datetime
    ) -> str:
        parts = [
            f"Upcoming appointment for {circle.elder_name}:",
            "",
            f"  Doctor: {doctor}",
            f"  When:   {local:%A %d %B, %I:%M %p}",
        ]
        if purpose:
            parts.append(f"  Why:    {purpose}")
        if location:
            parts.append(f"  Where:  {location}")
        parts += ["", f"Details and visit questions: {self.base_url}/appointments"]
        return "\n".join(parts)
