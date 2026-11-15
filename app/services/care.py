"""Core care-circle operations: members, medications, doses and appointments.

Every read and write takes the circle id from the authenticated session and
looks records up *inside that circle's collections*, so a record id from one
circle can never reach another circle's data.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import (
    Access,
    Appointment,
    CheckIn,
    Circle,
    DoseLog,
    DoseStatus,
    Feeling,
    Medication,
    Member,
    Role,
    ScheduledDose,
)
from app.security import (
    hash_passcode,
    new_circle_code,
    new_id,
    normalise_circle_code,
    verify_passcode,
)
from app.store import DocumentStore

CIRCLES = "circles"
# Public emergency-card links: card id -> circle id, plus when it was last opened.
EMERGENCY_CARDS = "emergency_cards"
CARD_OPENS_KEPT = 20


class PermissionDenied(Exception):
    """The member is not allowed to perform this action."""


class NotFound(Exception):
    """The record does not exist in this circle."""


class PasscodeInUse(Exception):
    """Another member of the circle already uses this passcode."""


def _members(circle_id: str) -> str:
    return f"{CIRCLES}/{circle_id}/members"


def _medications(circle_id: str) -> str:
    return f"{CIRCLES}/{circle_id}/medications"


def _doses(circle_id: str) -> str:
    return f"{CIRCLES}/{circle_id}/doses"


def _appointments(circle_id: str) -> str:
    return f"{CIRCLES}/{circle_id}/appointments"


def _check_ins(circle_id: str) -> str:
    return f"{CIRCLES}/{circle_id}/checkins"


def dose_log_id(medication_id: str, scheduled_for: datetime) -> str:
    """Deterministic id so each scheduled dose has exactly one log."""
    return f"{medication_id}_{scheduled_for.astimezone(UTC).strftime('%Y%m%dT%H%M')}"


@dataclass(frozen=True)
class Adherence:
    """Adherence numbers for a period."""

    taken: int
    skipped: int
    missed: int

    @property
    def expected(self) -> int:
        return self.taken + self.skipped + self.missed

    @property
    def percent(self) -> int | None:
        if self.expected == 0:
            return None
        return round(100 * self.taken / self.expected)


def summarise_adherence(
    doses: Iterable[ScheduledDose], now: datetime, grace_minutes: int = 60
) -> tuple[Adherence, dict[str, Adherence]]:
    """Count taken, skipped and missed doses, overall and per medication.

    A dose with no log counts as missed once ``grace_minutes`` have passed;
    doses still inside that window are not counted yet.
    """
    cutoff = now - timedelta(minutes=grace_minutes)
    totals = {"taken": 0, "skipped": 0, "missed": 0}
    per_med: dict[str, dict[str, int]] = {}
    for dose in doses:
        if dose.status is None and dose.scheduled_for > cutoff:
            continue
        key = dose.status.value if dose.status else "missed"
        totals[key] += 1
        bucket = per_med.setdefault(dose.medication.name, {"taken": 0, "skipped": 0, "missed": 0})
        bucket[key] += 1
    return Adherence(**totals), {name: Adherence(**c) for name, c in per_med.items()}


class CareService:
    """Business logic for one deployment, backed by a document store."""

    def __init__(self, store: DocumentStore) -> None:
        self.store = store

    # ----- circles and members -------------------------------------------

    def create_circle(
        self,
        elder_name: str,
        passcode: str,
        timezone: str,
        email: str | None = None,
        now: datetime | None = None,
        demo: bool = False,
    ) -> tuple[Circle, Member]:
        """Create a circle with the elder as its first member and owner."""
        ZoneInfo(timezone)  # raises for unknown zones
        code = new_circle_code()
        while self.store.get(CIRCLES, code) is not None:
            code = new_circle_code()
        circle = Circle(
            id=code,
            elder_name=elder_name,
            timezone=timezone,
            created_at=now or datetime.now(UTC),
            demo=demo,
        )
        self.store.put(CIRCLES, circle.id, circle.model_dump(mode="json"))
        elder = self._save_member(circle.id, elder_name, Role.ELDER, Access.EDIT, email, passcode)
        return circle, elder

    def get_circle(self, circle_id: str) -> Circle:
        data = self.store.get(CIRCLES, circle_id)
        if data is None:
            raise NotFound("circle")
        return Circle.model_validate(data)

    def save_circle(self, circle: Circle) -> None:
        self.store.put(CIRCLES, circle.id, circle.model_dump(mode="json"))

    # ----- emergency card --------------------------------------------------

    def update_health_info(
        self,
        actor: Member,
        *,
        blood_group: str,
        allergies: str,
        conditions: str,
        emergency_contacts: str,
    ) -> Circle:
        """Edit what the emergency card shows. The elder and editors may do this."""
        self._require_edit(actor)
        circle = self.get_circle(actor.circle_id)
        updated = Circle.model_validate(
            {
                **circle.model_dump(),
                "blood_group": blood_group,
                "allergies": allergies.strip(),
                "conditions": conditions.strip(),
                "emergency_contacts": emergency_contacts.strip(),
            }
        )
        self.save_circle(updated)
        return updated

    def enable_card(self, actor: Member, now: datetime | None = None) -> Circle:
        """Turn the public emergency card on, or replace its link. Only the elder may."""
        if not actor.is_elder:
            raise PermissionDenied("Only the elder can turn the emergency card on.")
        circle = self.get_circle(actor.circle_id)
        if circle.card_id:
            self.store.delete(EMERGENCY_CARDS, circle.card_id)
        circle.card_id = secrets.token_urlsafe(16)
        self.store.put(
            EMERGENCY_CARDS,
            circle.card_id,
            {
                "circle_id": circle.id,
                "created_at": (now or datetime.now(UTC)).isoformat(),
                "opens": [],
            },
        )
        self.save_circle(circle)
        return circle

    def disable_card(self, actor: Member) -> Circle:
        """Turn the emergency card off; its link and QR code stop working at once."""
        if not actor.is_elder:
            raise PermissionDenied("Only the elder can turn the emergency card off.")
        circle = self.get_circle(actor.circle_id)
        if circle.card_id:
            self.store.delete(EMERGENCY_CARDS, circle.card_id)
        circle.card_id = None
        self.save_circle(circle)
        return circle

    def card_opens(self, circle: Circle) -> list[datetime]:
        """When the emergency card was opened recently, newest first."""
        data = self.store.get(EMERGENCY_CARDS, circle.card_id) if circle.card_id else None
        opens = (data or {}).get("opens", [])
        return sorted((datetime.fromisoformat(o) for o in opens), reverse=True)

    def open_card(self, card_id: str, now: datetime | None = None) -> Circle | None:
        """Resolve a public card link and note that it was opened. None if it is off."""
        data = self.store.get(EMERGENCY_CARDS, card_id)
        if data is None:
            return None
        try:
            circle = self.get_circle(str(data.get("circle_id", "")))
        except NotFound:
            return None
        if circle.card_id != card_id:  # replaced or turned off
            return None
        opens = [*data.get("opens", []), (now or datetime.now(UTC)).isoformat()]
        self.store.put(EMERGENCY_CARDS, card_id, {**data, "opens": opens[-CARD_OPENS_KEPT:]})
        return circle

    def list_circles(self) -> list[Circle]:
        return [Circle.model_validate(d) for d in self.store.list(CIRCLES)]

    def authenticate(self, raw_code: str, passcode: str) -> Member | None:
        """Return the member whose passcode matches, or None."""
        code = normalise_circle_code(raw_code)
        if not code or self.store.get(CIRCLES, code) is None:
            return None
        for member in self.list_members(code):
            if verify_passcode(member.passcode_hash, passcode):
                return member
        return None

    def list_members(self, circle_id: str) -> list[Member]:
        members = [Member.model_validate(d) for d in self.store.list(_members(circle_id))]
        order = {Role.ELDER: 0, Role.CARETAKER: 1, Role.FAMILY: 2}
        return sorted(members, key=lambda m: (order[m.role], m.name.lower()))

    def get_member(self, circle_id: str, member_id: str) -> Member:
        data = self.store.get(_members(circle_id), member_id)
        if data is None:
            raise NotFound("member")
        return Member.model_validate(data)

    def add_member(
        self,
        actor: Member,
        name: str,
        role: Role,
        access: Access,
        email: str | None,
        passcode: str,
    ) -> Member:
        """Only the elder decides who joins their circle."""
        if not actor.is_elder:
            raise PermissionDenied("Only the elder can add people to the circle.")
        if role == Role.ELDER:
            raise PermissionDenied("A circle has exactly one elder.")
        return self._save_member(actor.circle_id, name, role, access, email, passcode)

    def set_member_access(self, actor: Member, member_id: str, access: Access) -> Member:
        if not actor.is_elder:
            raise PermissionDenied("Only the elder can change access.")
        member = self.get_member(actor.circle_id, member_id)
        if member.is_elder:
            raise PermissionDenied("The elder always keeps full access.")
        member.access = access
        self.store.put(_members(actor.circle_id), member.id, member.model_dump(mode="json"))
        return member

    def update_own_contact(self, actor: Member, email: str | None, notify: bool) -> Member:
        """Any member may change their own reminder email and opt in or out."""
        member = self.get_member(actor.circle_id, actor.id)
        updated = Member.model_validate(
            {**member.model_dump(), "email": email or None, "notify": notify}
        )
        self.store.put(_members(actor.circle_id), updated.id, updated.model_dump(mode="json"))
        return updated

    def remove_member(self, actor: Member, member_id: str) -> None:
        if not actor.is_elder:
            raise PermissionDenied("Only the elder can remove people.")
        member = self.get_member(actor.circle_id, member_id)
        if member.is_elder:
            raise PermissionDenied("The elder cannot be removed.")
        self.store.delete(_members(actor.circle_id), member.id)

    def _save_member(
        self,
        circle_id: str,
        name: str,
        role: Role,
        access: Access,
        email: str | None,
        passcode: str,
    ) -> Member:
        for existing in self.list_members(circle_id):
            if verify_passcode(existing.passcode_hash, passcode):
                raise PasscodeInUse("Choose a different passcode.")
        member = Member(
            id=new_id(),
            circle_id=circle_id,
            name=name,
            role=role,
            access=access,
            email=email or None,
            passcode_hash=hash_passcode(passcode),
        )
        self.store.put(_members(circle_id), member.id, member.model_dump(mode="json"))
        return member

    # ----- medications -----------------------------------------------------

    def list_medications(self, circle_id: str, include_inactive: bool = False) -> list[Medication]:
        meds = [Medication.model_validate(d) for d in self.store.list(_medications(circle_id))]
        if not include_inactive:
            meds = [m for m in meds if m.active]
        return sorted(meds, key=lambda m: m.name.lower())

    def get_medication(self, circle_id: str, medication_id: str) -> Medication:
        data = self.store.get(_medications(circle_id), medication_id)
        if data is None:
            raise NotFound("medication")
        return Medication.model_validate(data)

    def save_medication(
        self,
        actor: Member,
        *,
        name: str,
        dosage: str,
        instructions: str,
        times: list[str],
        start_date: date,
        end_date: date | None,
        medication_id: str | None = None,
        supply: int | None = None,
    ) -> Medication:
        """Create or update a medication; requires edit access."""
        self._require_edit(actor)
        alert_sent = False
        if medication_id is not None:
            old = self.get_medication(actor.circle_id, medication_id)  # must exist in this circle
            # A changed count is a fresh start for the refill alert.
            alert_sent = old.refill_alert_sent and old.supply == supply
        med = Medication(
            id=medication_id or new_id(),
            circle_id=actor.circle_id,
            name=name,
            dosage=dosage,
            instructions=instructions,
            times=times,
            start_date=start_date,
            end_date=end_date,
            supply=supply,
            refill_alert_sent=alert_sent,
        )
        self.save_medication_record(med)
        return med

    def save_medication_record(self, med: Medication) -> None:
        self.store.put(_medications(med.circle_id), med.id, med.model_dump(mode="json"))

    def refill_medication(self, actor: Member, medication_id: str, added: int) -> Medication:
        """Add newly bought doses to the count and re-arm the low-supply alert."""
        self._require_edit(actor)
        if not 1 <= added <= 1000:
            raise ValueError("added must be between 1 and 1000")
        med = self.get_medication(actor.circle_id, medication_id)
        med.supply = min((med.supply or 0) + added, 9999)
        med.refill_alert_sent = False
        self.save_medication_record(med)
        return med

    def stop_medication(self, actor: Member, medication_id: str) -> None:
        self._require_edit(actor)
        med = self.get_medication(actor.circle_id, medication_id)
        med.active = False
        self.store.put(_medications(actor.circle_id), med.id, med.model_dump(mode="json"))

    # ----- doses and adherence --------------------------------------------

    def schedule_for_days(self, circle: Circle, start: date, end: date) -> list[ScheduledDose]:
        """All doses expected on the local days ``start``..``end``, with their logs.

        Medicines and dose logs are read once, however long the range is.
        """
        zone = ZoneInfo(circle.timezone)
        logs = {d["id"]: DoseLog.model_validate(d) for d in self.store.list(_doses(circle.id))}
        meds = self.list_medications(circle.id)
        doses: list[ScheduledDose] = []
        day = start
        while day <= end:
            for med in meds:
                if not med.is_scheduled_on(day):
                    continue
                for slot in med.times:
                    hour, minute = (int(part) for part in slot.split(":"))
                    when = datetime.combine(day, time(hour, minute), tzinfo=zone)
                    doses.append(
                        ScheduledDose(
                            medication=med,
                            scheduled_for=when,
                            log=logs.get(dose_log_id(med.id, when)),
                        )
                    )
            day += timedelta(days=1)
        return sorted(doses, key=lambda d: (d.scheduled_for, d.medication.name.lower()))

    def schedule_for_day(self, circle: Circle, day: date) -> list[ScheduledDose]:
        """All doses expected on one local calendar day, with their logs."""
        return self.schedule_for_days(circle, day, day)

    def record_dose(
        self,
        actor: Member,
        medication_id: str,
        scheduled_for: datetime,
        status: DoseStatus,
        now: datetime | None = None,
    ) -> DoseLog:
        """Mark a dose taken or skipped. The elder and editors may do this."""
        if not (actor.is_elder or actor.can_edit):
            raise PermissionDenied("You have view-only access.")
        med = self.get_medication(actor.circle_id, medication_id)
        if scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for must be timezone-aware")
        hhmm = scheduled_for.astimezone(ZoneInfo(self.get_circle(actor.circle_id).timezone))
        if hhmm.strftime("%H:%M") not in med.times:
            raise NotFound("dose")
        log_id = dose_log_id(med.id, scheduled_for)
        existing = self.store.get(_doses(actor.circle_id), log_id)
        log = (
            DoseLog.model_validate(existing)
            if existing
            else DoseLog(
                id=log_id,
                circle_id=actor.circle_id,
                medication_id=med.id,
                scheduled_for=scheduled_for.astimezone(UTC),
            )
        )
        previous = log.status
        log.status = status
        log.recorded_by = actor.name
        log.recorded_at = now or datetime.now(UTC)
        self.store.put(_doses(actor.circle_id), log.id, log.model_dump(mode="json"))
        if med.supply is not None and previous != status:
            if status == DoseStatus.TAKEN:
                med.supply = max(med.supply - 1, 0)
            elif previous == DoseStatus.TAKEN:
                med.supply += 1  # a correction gives the dose back
            self.save_medication_record(med)
        return log

    def save_dose_log(self, log: DoseLog) -> None:
        self.store.put(_doses(log.circle_id), log.id, log.model_dump(mode="json"))

    def save_dose_logs(self, logs: list[DoseLog]) -> None:
        """Save many dose logs of one circle in a single batch write."""
        if logs:
            self.store.put_many(
                _doses(logs[0].circle_id), {log.id: log.model_dump(mode="json") for log in logs}
            )

    def adherence(
        self, circle: Circle, start: date, end: date, now: datetime, grace_minutes: int = 60
    ) -> tuple[Adherence, dict[str, Adherence]]:
        """Overall and per-medication adherence for doses due before ``now``."""
        return summarise_adherence(self.schedule_for_days(circle, start, end), now, grace_minutes)

    # ----- daily check-ins -------------------------------------------------

    def record_check_in(
        self,
        actor: Member,
        day: date,
        feeling: Feeling,
        note: str = "",
        now: datetime | None = None,
    ) -> CheckIn:
        """Save how the elder feels today. The elder, or someone helping them, may record it."""
        self._require_edit(actor)
        check_in = CheckIn(
            id=day.isoformat(),
            circle_id=actor.circle_id,
            day=day,
            feeling=feeling,
            note=note.strip(),
            recorded_by=actor.name,
            recorded_at=now or datetime.now(UTC),
        )
        self.store.put(_check_ins(actor.circle_id), check_in.id, check_in.model_dump(mode="json"))
        return check_in

    def save_check_ins(self, circle_id: str, check_ins: list[CheckIn]) -> None:
        if check_ins:
            self.store.put_many(
                _check_ins(circle_id), {c.id: c.model_dump(mode="json") for c in check_ins}
            )

    def list_check_ins(self, circle_id: str, start: date, end: date) -> list[CheckIn]:
        """Check-ins for the local days ``start``..``end``, oldest first."""
        items = [CheckIn.model_validate(d) for d in self.store.list(_check_ins(circle_id))]
        return sorted((c for c in items if start <= c.day <= end), key=lambda c: c.day)

    # ----- appointments ----------------------------------------------------

    def list_appointments(
        self, circle_id: str, upcoming_from: datetime | None = None
    ) -> list[Appointment]:
        items = [Appointment.model_validate(d) for d in self.store.list(_appointments(circle_id))]
        if upcoming_from is not None:
            items = [a for a in items if a.starts_at >= upcoming_from]
        return sorted(items, key=lambda a: a.starts_at)

    def get_appointment(self, circle_id: str, appointment_id: str) -> Appointment:
        data = self.store.get(_appointments(circle_id), appointment_id)
        if data is None:
            raise NotFound("appointment")
        return Appointment.model_validate(data)

    def save_appointment(
        self,
        actor: Member,
        *,
        doctor: str,
        purpose: str,
        location: str,
        starts_at: datetime,
        notes: str,
        appointment_id: str | None = None,
    ) -> Appointment:
        self._require_edit(actor)
        reminders: list[str] = []
        if appointment_id is not None:
            old = self.get_appointment(actor.circle_id, appointment_id)
            if old.starts_at == starts_at:
                reminders = old.reminders_sent
        appt = Appointment(
            id=appointment_id or new_id(),
            circle_id=actor.circle_id,
            doctor=doctor,
            purpose=purpose,
            location=location,
            starts_at=starts_at,
            notes=notes,
            reminders_sent=reminders,
        )
        self.store.put(_appointments(actor.circle_id), appt.id, appt.model_dump(mode="json"))
        return appt

    def save_appointment_record(self, appt: Appointment) -> None:
        self.store.put(_appointments(appt.circle_id), appt.id, appt.model_dump(mode="json"))

    def delete_appointment(self, actor: Member, appointment_id: str) -> None:
        self._require_edit(actor)
        self.get_appointment(actor.circle_id, appointment_id)
        self.store.delete(_appointments(actor.circle_id), appointment_id)

    # ----- helpers ---------------------------------------------------------

    @staticmethod
    def _require_edit(actor: Member) -> None:
        if not actor.can_edit:
            raise PermissionDenied("You have view-only access to this circle.")
