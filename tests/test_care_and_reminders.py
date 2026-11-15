"""Medication schedules, adherence and the escalating reminder ladder."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Access, DoseStatus, Role
from app.services.care import CareService, PermissionDenied
from app.services.reminders import ReminderService, dose_stage
from app.store import InMemoryStore
from tests.conftest import FakeEmailer

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def care() -> CareService:
    return CareService(InMemoryStore())


@pytest.fixture
def circle_setup(care):
    circle, elder = care.create_circle("Asha", "sunrise42", "Asia/Kolkata", "elder@example.com")
    caretaker = care.add_member(
        elder, "Ravi", Role.CARETAKER, Access.EDIT, "ravi@example.com", "ravi-pass-1"
    )
    family = care.add_member(
        elder, "Meera", Role.FAMILY, Access.VIEW, "meera@example.com", "meera-pass-1"
    )
    med = care.save_medication(
        elder,
        name="Metformin",
        dosage="500 mg",
        instructions="after food",
        times=["09:00", "21:00"],
        start_date=date(2026, 11, 1),
        end_date=None,
    )
    return circle, elder, caretaker, family, med


def at(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


def test_schedule_lists_each_dose_time(care, circle_setup):
    circle, *_ = circle_setup
    doses = care.schedule_for_day(circle, date(2026, 11, 15))
    assert [d.scheduled_for.hour for d in doses] == [9, 21]
    assert care.schedule_for_day(circle, date(2026, 10, 31)) == []


def test_invalid_time_rejected(care, circle_setup):
    _, elder, *_ = circle_setup
    with pytest.raises(ValueError):
        care.save_medication(
            elder,
            name="X",
            dosage="",
            instructions="",
            times=["25:00"],
            start_date=date(2026, 11, 1),
            end_date=None,
        )


def test_view_only_cannot_record_dose(care, circle_setup):
    _, _, _, family, med = circle_setup
    with pytest.raises(PermissionDenied):
        care.record_dose(family, med.id, at(9), DoseStatus.TAKEN)


def test_adherence_counts_taken_and_missed(care, circle_setup):
    circle, elder, _, _, med = circle_setup
    care.record_dose(elder, med.id, at(9, day=14), DoseStatus.TAKEN)
    care.record_dose(elder, med.id, at(21, day=14), DoseStatus.SKIPPED)
    care.record_dose(elder, med.id, at(9), DoseStatus.TAKEN)
    overall, per_med = care.adherence(circle, date(2026, 11, 14), date(2026, 11, 15), at(23))
    # 15 Nov 21:00 is only 2 h old at 23:00, so it is counted as missed.
    assert (overall.taken, overall.skipped, overall.missed) == (2, 1, 1)
    assert overall.percent == 50
    assert per_med["Metformin"].expected == 4


def test_dose_stage_ladder(care, circle_setup):
    circle, *_ = circle_setup
    dose = care.schedule_for_day(circle, date(2026, 11, 15))[0]  # 09:00
    assert dose_stage(dose, at(8, 30)) is None
    assert dose_stage(dose, at(8, 50)) == "upcoming"
    assert dose_stage(dose, at(9, 10)) is None
    assert dose_stage(dose, at(9, 35)) == "missed"
    assert dose_stage(dose, at(10, 5)) == "escalated"
    assert dose_stage(dose, at(16, 0)) is None


def test_reminders_escalate_without_duplicates(care, circle_setup):
    _circle, *_ = circle_setup
    emailer = FakeEmailer()
    service = ReminderService(care, emailer, "https://app.example")

    service.run(at(8, 50))
    assert emailer.sent[-1][0] == ["elder@example.com"]
    assert "Medicine time soon" in emailer.sent[-1][1]

    service.run(at(8, 55))  # same window: nothing new
    assert len(emailer.sent) == 1

    service.run(at(9, 35))
    assert emailer.sent[-1][0] == ["elder@example.com", "ravi@example.com"]

    service.run(at(10, 5))
    assert "meera@example.com" in emailer.sent[-1][0]
    assert "check on Asha" in emailer.sent[-1][1]
    assert len(emailer.sent) == 3


def test_taken_dose_gets_no_missed_reminder(care, circle_setup):
    _circle, elder, _, _, med = circle_setup
    emailer = FakeEmailer()
    care.record_dose(elder, med.id, at(9), DoseStatus.TAKEN)
    ReminderService(care, emailer, "https://app.example").run(at(10, 5))
    assert emailer.sent == []


def appt_emails(emailer: FakeEmailer) -> list:
    return [e for e in emailer.sent if e[1].startswith("Appointment reminder")]


def test_appointment_reminders(care, circle_setup):
    _circle, elder, *_ = circle_setup
    care.save_appointment(
        elder,
        doctor="Dr Rao",
        purpose="Diabetes review",
        location="City Clinic",
        starts_at=at(11, day=16),
        notes="",
    )
    emailer = FakeEmailer()
    service = ReminderService(care, emailer, "https://app.example")
    service.run(at(10, day=15).astimezone(UTC))  # 25 h before: nothing
    assert appt_emails(emailer) == []
    service.run(at(12, day=15))  # 23 h before
    sent = appt_emails(emailer)
    assert len(sent) == 1 and len(sent[0][0]) == 3 and "Dr Rao" in sent[0][1]
    service.run(at(9, 30, day=16))  # 1.5 h before
    service.run(at(9, 40, day=16))
    assert len(appt_emails(emailer)) == 2


def test_late_added_appointment_sends_single_email(care, circle_setup):
    _, elder, *_ = circle_setup
    care.save_appointment(
        elder, doctor="Dr Rao", purpose="", location="", starts_at=at(11), notes=""
    )
    emailer = FakeEmailer()
    service = ReminderService(care, emailer, "https://app.example")
    service.run(at(10))
    service.run(at(10, 10))
    assert len(appt_emails(emailer)) == 1


def test_stopped_medication_not_scheduled(care, circle_setup):
    circle, elder, _, _, med = circle_setup
    care.stop_medication(elder, med.id)
    assert care.schedule_for_day(circle, date(2026, 11, 15)) == []


def test_reminder_task_requires_token(client):
    assert client.post("/tasks/reminders").status_code == 401
    bad = client.post("/tasks/reminders", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    ok = client.post("/tasks/reminders", headers={"Authorization": "Bearer good-token"})
    assert ok.status_code == 200
    assert ok.json()["circles"] == 0


def test_today_page_marks_dose(client, store):
    from tests.conftest import create_circle, post

    code = create_circle(client)
    today = datetime.now(IST).date().isoformat()
    post(
        client,
        "/medications",
        {"name": "Aspirin", "dosage": "75 mg", "times": ["00:00", "23:59"], "start_date": today},
    )
    page = client.get("/today").text
    assert "Aspirin" in page and "I took it" in page
    med_id = store.list(f"circles/{code}/medications")[0]["id"]
    when = datetime.combine(datetime.now(IST).date(), datetime.min.time(), tzinfo=IST)
    response = post(
        client,
        "/doses",
        {"medication_id": med_id, "scheduled_for": when.isoformat(), "status": "taken"},
    )
    assert "Aspirin marked as taken" in response.text
    # A forged time that is not on the schedule is refused.
    forged = post(
        client,
        "/doses",
        {
            "medication_id": med_id,
            "scheduled_for": (when + timedelta(minutes=7)).isoformat(),
            "status": "taken",
        },
    )
    assert "could not be found" in forged.text
