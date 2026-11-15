"""One-click demo circles with a realistic week of history.

Each visitor gets a fresh, isolated circle, so people trying the app never
see or change each other's data. Demo members have no email addresses, so no
email is sent unless the visitor adds their own address.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import Access, CheckIn, Circle, DoseLog, DoseStatus, Feeling, Member, Role
from app.services.care import CareService, dose_log_id

DEMO_TIMEZONE = "Asia/Kolkata"

# (name, dosage, instructions, times, doses in hand). Amlodipine is running low.
DEMO_MEDICINES = (
    ("Metformin", "500 mg, 1 tablet", "after food", ["08:30", "20:30"], 38),
    ("Amlodipine", "5 mg, 1 tablet", "for blood pressure", ["09:00"], 4),
    ("Atorvastatin", "10 mg, 1 tablet", "at bedtime", ["21:30"], 24),
)

# How Kamala said she felt (days ago -> feeling, note). Today is left for the visitor.
DEMO_FEELINGS = {
    6: (Feeling.GOOD, ""),
    5: (Feeling.GOOD, "Went for a walk with Priya"),
    4: (Feeling.OKAY, "A little tired"),
    3: (Feeling.UNWELL, "Felt dizzy in the morning after the blood pressure tablet"),
    2: (Feeling.GOOD, ""),
    1: (Feeling.OKAY, "Slept badly"),
}

# Doses missed or skipped in the past week: (days ago, medicine, time, status or None=missed).
# Evening statin doses are the weak spot, which the weekly summary can pick up.
DEMO_GAPS = {
    (1, "Atorvastatin", "21:30"): None,
    (3, "Atorvastatin", "21:30"): None,
    (4, "Metformin", "20:30"): DoseStatus.SKIPPED,
    (5, "Atorvastatin", "21:30"): None,
}


@dataclass(frozen=True)
class DemoCircle:
    circle: Circle
    elder: Member
    passcodes: dict[str, str]


def _passcode() -> str:
    return f"demo-{secrets.token_hex(3)}"


def create_demo_circle(care: CareService, now: datetime | None = None) -> DemoCircle:
    """Create a sample circle: elder, caretaker, family, medicines, history, appointments."""
    current = now or datetime.now(UTC)
    zone = ZoneInfo(DEMO_TIMEZONE)
    local_now = current.astimezone(zone)
    today = local_now.date()

    passcodes = {"Kamala Iyer": _passcode(), "Ravi Kumar": _passcode(), "Priya Iyer": _passcode()}
    circle, elder = care.create_circle(
        "Kamala Iyer", passcodes["Kamala Iyer"], DEMO_TIMEZONE, None, now=current, demo=True
    )
    caretaker = care.add_member(
        elder, "Ravi Kumar", Role.CARETAKER, Access.EDIT, None, passcodes["Ravi Kumar"]
    )
    care.add_member(elder, "Priya Iyer", Role.FAMILY, Access.VIEW, None, passcodes["Priya Iyer"])

    start = today - timedelta(days=7)
    meds = [
        care.save_medication(
            elder,
            name=name,
            dosage=dosage,
            instructions=instructions,
            times=times,
            start_date=start,
            end_date=None,
            supply=supply,
        )
        for name, dosage, instructions, times, supply in DEMO_MEDICINES
    ]

    # Leave today's most recent due dose unmarked so the reminder ladder is visible.
    due_today = [
        (datetime.combine(today, time.fromisoformat(slot), tzinfo=zone), med.id)
        for med in meds
        for slot in med.times
    ]
    past_today = sorted(item for item in due_today if item[0] <= local_now)
    left_open = past_today[-1] if past_today else None

    history: list[DoseLog] = []
    for days_ago in range(7, -1, -1):
        day = today - timedelta(days=days_ago)
        for med in meds:
            for slot in med.times:
                hour, minute = (int(p) for p in slot.split(":"))
                when = datetime.combine(day, time(hour, minute), tzinfo=zone)
                if when > local_now or (when, med.id) == left_open:
                    continue
                key = (days_ago, med.name, slot)
                if key in DEMO_GAPS and DEMO_GAPS[key] is None:
                    continue
                status = DEMO_GAPS.get(key) or DoseStatus.TAKEN
                marker = caretaker if (days_ago + hour) % 3 == 0 else elder
                history.append(
                    DoseLog(
                        id=dose_log_id(med.id, when),
                        circle_id=circle.id,
                        medication_id=med.id,
                        scheduled_for=when.astimezone(UTC),
                        status=status,
                        recorded_by=marker.name,
                        recorded_at=(when + timedelta(minutes=10)).astimezone(UTC),
                        # Past doses never trigger reminders.
                        reminders_sent=["upcoming", "missed", "escalated"],
                    )
                )
    care.save_dose_logs(history)  # one batch write instead of ~30 separate ones

    care.save_check_ins(
        circle.id,
        [
            CheckIn(
                id=(today - timedelta(days=days_ago)).isoformat(),
                circle_id=circle.id,
                day=today - timedelta(days=days_ago),
                feeling=feeling,
                note=note,
                recorded_by=elder.name,
                recorded_at=datetime.combine(
                    today - timedelta(days=days_ago), time(10, 0), tzinfo=zone
                ).astimezone(UTC),
            )
            for days_ago, (feeling, note) in DEMO_FEELINGS.items()
        ],
    )

    care.save_appointment(
        elder,
        doctor="Dr. Anil Mehta (Cardiologist)",
        purpose="Blood pressure and cholesterol review",
        location="City Heart Clinic, 2nd floor",
        starts_at=datetime.combine(today + timedelta(days=2), time(11, 0), tzinfo=zone),
        notes="Bring the last blood test report.",
    )
    care.save_appointment(
        elder,
        doctor="Dr. Sunita Rao (Diabetologist)",
        purpose="HbA1c follow-up",
        location="Sunrise Diabetes Centre",
        starts_at=datetime.combine(today + timedelta(days=9), time(10, 30), tzinfo=zone),
        notes="",
    )
    return DemoCircle(circle=circle, elder=elder, passcodes=passcodes)
