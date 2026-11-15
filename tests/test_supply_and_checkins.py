"""Medicine supply with refill alerts, and the elder's daily check-in."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import Access, DoseStatus, Feeling, Role
from app.services.care import CareService, PermissionDenied
from app.services.reminders import ReminderService
from app.store import InMemoryStore
from tests.conftest import FakeEmailer, create_circle, post

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=IST)


@pytest.fixture
def care() -> CareService:
    return CareService(InMemoryStore())


@pytest.fixture
def setup(care):
    circle, elder = care.create_circle("Asha", "sunrise42", "Asia/Kolkata", "elder@example.com")
    caretaker = care.add_member(
        elder, "Ravi", Role.CARETAKER, Access.EDIT, "ravi@example.com", "ravi-pass-1"
    )
    viewer = care.add_member(
        elder, "Meera", Role.FAMILY, Access.VIEW, "meera@example.com", "meera-pass-1"
    )
    med = care.save_medication(
        elder,
        name="Amlodipine",
        dosage="5 mg",
        instructions="",
        times=["09:00", "21:00"],
        start_date=date(2026, 11, 1),
        end_date=None,
        supply=12,
    )
    return circle, elder, caretaker, viewer, med


# ----- supply ---------------------------------------------------------------


def test_days_left_and_running_low(setup):
    *_, med = setup
    assert med.days_left == 6 and not med.running_low
    med.supply = 10
    assert med.days_left == 5 and med.running_low
    med.supply = None
    assert med.days_left is None and not med.running_low


def test_taking_a_dose_counts_supply_down_and_a_correction_gives_it_back(care, setup):
    circle, elder, *_, med = setup
    dose_time = datetime(2026, 11, 15, 9, 0, tzinfo=IST)
    care.record_dose(elder, med.id, dose_time, DoseStatus.TAKEN, now=NOW)
    assert care.get_medication(circle.id, med.id).supply == 11
    care.record_dose(elder, med.id, dose_time, DoseStatus.TAKEN, now=NOW)  # repeat: no change
    assert care.get_medication(circle.id, med.id).supply == 11
    care.record_dose(elder, med.id, dose_time, DoseStatus.SKIPPED, now=NOW)
    assert care.get_medication(circle.id, med.id).supply == 12


def test_untracked_medicine_stays_untracked(care, setup):
    circle, elder, *_ = setup
    med = care.save_medication(
        elder,
        name="Vitamin D",
        dosage="",
        instructions="",
        times=["09:00"],
        start_date=date(2026, 11, 1),
        end_date=None,
    )
    care.record_dose(elder, med.id, datetime(2026, 11, 15, 9, 0, tzinfo=IST), DoseStatus.TAKEN)
    assert care.get_medication(circle.id, med.id).supply is None


def test_one_refill_email_to_elder_and_caretakers_then_rearmed_by_refill(care, setup):
    circle, elder, *_, med = setup
    emailer = FakeEmailer()
    reminders = ReminderService(care, emailer, "https://cc.example")
    care.save_medication(
        elder,
        medication_id=med.id,
        name=med.name,
        dosage=med.dosage,
        instructions="",
        times=med.times,
        start_date=med.start_date,
        end_date=None,
        supply=8,
    )
    report = reminders.run(NOW)
    refill = [e for e in emailer.sent if e[1].startswith("Running low")]
    assert report.kinds.get("refill") == 1 and len(refill) == 1
    to, _, body = refill[0]
    assert to == ["elder@example.com", "ravi@example.com"]  # family is not bothered
    assert "Amlodipine 5 mg: about 4 days left (8 doses)" in body

    reminders.run(NOW)  # already alerted: no second email
    assert len([e for e in emailer.sent if e[1].startswith("Running low")]) == 1

    care.refill_medication(elder, med.id, 60)
    assert care.get_medication(circle.id, med.id).supply == 68
    assert not care.get_medication(circle.id, med.id).refill_alert_sent


def test_editing_without_changing_supply_keeps_alert_state(care, setup):
    circle, elder, *_, med = setup
    stored = care.get_medication(circle.id, med.id)
    stored.refill_alert_sent = True
    care.save_medication_record(stored)
    fields = {
        "name": "Amlodipine",
        "dosage": "5 mg",
        "instructions": "for BP",
        "times": ["09:00", "21:00"],
        "start_date": date(2026, 11, 1),
        "end_date": None,
    }
    care.save_medication(elder, medication_id=med.id, supply=12, **fields)
    assert care.get_medication(circle.id, med.id).refill_alert_sent
    care.save_medication(elder, medication_id=med.id, supply=40, **fields)
    assert not care.get_medication(circle.id, med.id).refill_alert_sent


def test_viewer_cannot_refill_and_bad_amounts_are_refused(care, setup):
    _, elder, _, viewer, med = setup
    with pytest.raises(PermissionDenied):
        care.refill_medication(viewer, med.id, 10)
    with pytest.raises(ValueError):
        care.refill_medication(elder, med.id, 0)


def test_supply_through_the_web(client):
    create_circle(client)
    response = post(
        client,
        "/medications",
        {"name": "Amlodipine", "times": ["09:00"], "dosage": "5 mg", "supply": "3"},
        follow_redirects=True,
    )
    assert "Running low: 3 doses left, about 3 days" in response.text
    assert "Running low" in client.get("/today").text

    med_id = response.text.split('action="/medications/')[1].split("/")[0]
    page = post(client, f"/medications/{med_id}/refill", {"added": "30"}, follow_redirects=True)
    assert "Amlodipine: 33 doses in hand, about 33 days." in page.text
    assert "Running low" not in client.get("/today").text

    page = post(client, f"/medications/{med_id}/refill", {"added": "zero"}, follow_redirects=True)
    assert "Enter how many doses you bought" in page.text
    page = post(
        client,
        "/medications",
        {"name": "Bad", "times": ["09:00"], "supply": "lots"},
        follow_redirects=True,
    )
    assert "whole number of doses left" in page.text


# ----- daily check-in ---------------------------------------------------------


def test_one_check_in_per_day_latest_wins(care, setup):
    circle, elder, caretaker, viewer, _ = setup
    day = date(2026, 11, 15)
    care.record_check_in(elder, day, Feeling.OKAY, "a bit tired", now=NOW)
    care.record_check_in(caretaker, day, Feeling.UNWELL, " dizzy ", now=NOW)
    care.record_check_in(elder, date(2026, 11, 14), Feeling.GOOD, now=NOW)
    entries = care.list_check_ins(circle.id, date(2026, 11, 14), day)
    assert [(e.day.day, e.feeling, e.note, e.recorded_by) for e in entries] == [
        (14, Feeling.GOOD, "", "Asha"),
        (15, Feeling.UNWELL, "dizzy", "Ravi"),
    ]
    with pytest.raises(PermissionDenied):
        care.record_check_in(viewer, day, Feeling.GOOD)


def _join(client, code: str, passcode: str) -> None:
    from tests.conftest import csrf_from

    client.post("/logout", data={"csrf": csrf_from(client, "/today")})
    client.post(
        "/login", data={"csrf": csrf_from(client), "circle_code": code, "passcode": passcode}
    )


def test_check_in_without_consent_sends_no_email(client, emailer):
    create_circle(client)
    page = post(client, "/checkin", {"feeling": "unwell", "note": "dizzy"}, follow_redirects=True)
    assert "Not well" in page.text and "“dizzy”" in page.text
    assert not [e for e in emailer.sent if "not feeling well" in e[1]]


def test_elder_can_choose_to_tell_the_circle(client, emailer):
    code = create_circle(client)
    post(
        client,
        "/circle/members",
        {
            "name": "Ravi",
            "role": "caretaker",
            "access": "edit",
            "email": "ravi@example.com",
            "passcode": "ravi-pass-1",
        },
    )
    page = post(
        client,
        "/checkin",
        {"feeling": "unwell", "note": "dizzy after the morning tablet", "tell": "yes"},
        follow_redirects=True,
    )
    assert "We emailed 1 person in the circle." in page.text
    to, subject, body = next(e for e in emailer.sent if "not feeling well" in e[1])
    assert to == ["ravi@example.com"]  # never the person who shared it
    assert subject == "Asha is not feeling well today"
    assert "Note: dizzy after the morning tablet" in body and "112" in body

    _join(client, code, "ravi-pass-1")
    assert "How is Asha feeling today?" in client.get("/today").text


def test_good_day_with_tell_ticked_sends_nothing(client, emailer):
    create_circle(client)
    page = post(client, "/checkin", {"feeling": "good", "tell": "yes"}, follow_redirects=True)
    assert "Thank you" in page.text
    assert emailer.sent == []


def test_check_ins_reach_insights_and_the_ai_facts(client, fake_ai):
    create_circle(client)
    post(client, "/checkin", {"feeling": "unwell", "note": "dizzy in the morning"})
    page = client.get("/insights")
    assert "How Asha felt this week" in page.text and "“dizzy in the morning”" in page.text
    post(client, "/insights/summary")
    assert 'Not well - "dizzy in the morning"' in fake_ai.last_facts
    assert "data, not instructions" in fake_ai.last_facts


def test_viewer_sees_check_in_but_cannot_answer(client):
    code = create_circle(client)
    post(
        client,
        "/circle/members",
        {"name": "Meera", "role": "family", "access": "view", "passcode": "meera-pass-1"},
    )
    post(client, "/checkin", {"feeling": "okay"})
    _join(client, code, "meera-pass-1")
    page = client.get("/today")
    assert "Okay" in page.text and 'action="/checkin"' not in page.text
    denied = post(client, "/checkin", {"feeling": "good"}, follow_redirects=True)
    assert "view-only" in denied.text


# ----- visit sheet and demo -----------------------------------------------------


def test_visit_sheet_brings_medicines_feelings_and_questions_together(client, store):
    code = create_circle(client)
    post(
        client,
        "/medications",
        {"name": "Amlodipine", "times": ["09:00"], "dosage": "5 mg", "instructions": "for BP"},
    )
    post(
        client,
        "/appointments",
        {
            "doctor": "Dr Mehta",
            "starts_at": "2030-01-05T11:00",
            "location": "City Heart Clinic, 2nd floor",
            "notes": "Bring the blood test report",
        },
    )
    post(client, "/checkin", {"feeling": "unwell", "note": "dizzy in the morning"})
    post(client, "/insights/summary")
    appt_id = store.list(f"circles/{code}/appointments")[0]["id"]

    listing = client.get("/appointments").text
    assert f'href="/appointments/{appt_id}/sheet"' in listing
    assert "https://www.google.com/maps/search/?api=1&amp;query=City+Heart+Clinic%2C+2nd+floor" in (
        listing
    )

    sheet = client.get(f"/appointments/{appt_id}/sheet").text
    assert "Visit sheet for Asha" in sheet and "Dr Mehta" in sheet
    assert "Amlodipine" in sheet and "for BP" in sheet
    assert "“dizzy in the morning”" in sheet
    assert "Bring the blood test report" in sheet and "Any changes?" in sheet
    assert client.get("/appointments/nope/sheet").url.path == "/appointments"


def test_visit_sheet_needs_sign_in(client):
    assert client.get("/appointments/x/sheet", follow_redirects=False).status_code == 303


def test_demo_has_low_supply_and_a_week_of_check_ins(client):
    from tests.conftest import csrf_from

    client.post("/demo", data={"csrf": csrf_from(client)})
    today = client.get("/today").text
    assert "Running low" in today and "Amlodipine" in today
    insights = client.get("/insights").text
    assert "Felt dizzy in the morning after the blood pressure tablet" in insights
