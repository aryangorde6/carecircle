"""Prescription scanning, upload validation, AI budget, summaries, appointments, XSS."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.routes.appointments import google_calendar_url
from app.services.ai import AiBudget, AiService, AiUnavailable
from app.store import InMemoryStore
from app.uploads import UploadRejected, detect_image_type, validate_image
from tests.conftest import create_circle, csrf_from, post

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def test_detects_real_image_types():
    assert detect_image_type(PNG) == "image/png"
    assert detect_image_type(JPEG) == "image/jpeg"
    assert detect_image_type(WEBP) == "image/webp"
    assert detect_image_type(b"<script>alert(1)</script>") is None
    assert detect_image_type(b"XXXX\x00\x00\x00\x00WEBP") is None


def test_validate_image_limits():
    with pytest.raises(UploadRejected):
        validate_image(b"", 1000)
    with pytest.raises(UploadRejected):
        validate_image(PNG, 10)
    with pytest.raises(UploadRejected):
        validate_image(b"%PDF-1.7" + b"\x00" * 10, 1000)


def test_scan_shows_review_form_without_saving(client, store):
    code = create_circle(client)
    response = client.post(
        "/medications/scan",
        data={"csrf": csrf_from(client, "/medications/scan")},
        files={"photo": ("rx.png", PNG, "image/png")},
    )
    assert "Check what Gemini found" in response.text
    assert 'value="Metformin"' in response.text
    assert store.list(f"circles/{code}/medications") == []


def test_scan_rejects_disguised_file(client):
    create_circle(client)
    response = client.post(
        "/medications/scan",
        data={"csrf": csrf_from(client, "/medications/scan")},
        files={"photo": ("rx.png", b"MZ\x90\x00 not an image", "image/png")},
    )
    assert "Only JPEG, PNG or WebP" in response.text


def test_ai_budget_is_a_hard_stop():
    store = InMemoryStore()
    budget = AiBudget(store, daily_cap=2)
    now = datetime(2026, 11, 15, tzinfo=UTC)
    budget.consume(now)
    budget.consume(now)
    with pytest.raises(AiUnavailable):
        budget.consume(now)


def test_ai_disabled_without_model():
    service = AiService(None, AiBudget(InMemoryStore(), 10))
    assert not service.enabled
    with pytest.raises(AiUnavailable):
        service.summarise_week("facts")


def test_weekly_summary_generate_and_email(client, emailer, fake_ai):
    create_circle(client)
    post(
        client, "/medications", {"name": "Aspirin", "times": ["08:00"], "start_date": "2026-11-01"}
    )
    response = post(client, "/insights/summary")
    assert "A good week overall." in response.text
    assert "Any changes?" in response.text
    assert "Aspirin" in fake_ai.last_facts
    response = post(client, "/insights/summary/email")
    assert "Summary emailed to 1 people" in response.text
    assert emailer.sent[-1][0] == ["elder@example.com"]


def test_appointment_crud_and_calendar_link(client, store):
    code = create_circle(client)
    response = post(
        client,
        "/appointments",
        {
            "doctor": "Dr Rao",
            "starts_at": "2030-01-05T10:30",
            "purpose": "Eye check",
            "location": "City Clinic",
        },
    )
    assert "Appointment saved" in response.text
    assert "calendar.google.com" in response.text
    appt = store.list(f"circles/{code}/appointments")[0]
    assert appt["starts_at"].startswith("2030-01-05T10:30:00+05:30")
    post(
        client, f"/appointments/{appt['id']}", {"doctor": "Dr Rao", "starts_at": "2030-01-06T11:00"}
    )
    assert store.list(f"circles/{code}/appointments")[0]["starts_at"].startswith("2030-01-06")
    assert "Appointment removed" in post(client, f"/appointments/{appt['id']}/delete").text


def test_calendar_url_has_no_notes():
    from app.models import Appointment

    appt = Appointment(
        id="a",
        circle_id="c",
        doctor="Dr Rao",
        purpose="Check-up",
        location="Clinic",
        starts_at=datetime(2030, 1, 5, 5, 0, tzinfo=UTC),
        notes="private note",
    )
    url = google_calendar_url(appt, "Asha")
    assert "20300105T050000Z%2F20300105T060000Z" in url
    assert "private" not in url


def test_user_input_is_escaped(client):
    create_circle(client, name="<script>alert(1)</script>")
    page = client.get("/today").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_medication_edit_and_validation(client, store):
    code = create_circle(client)
    post(
        client, "/medications", {"name": "Aspirin", "times": ["08:00"], "start_date": "2026-11-01"}
    )
    med = store.list(f"circles/{code}/medications")[0]
    assert "Edit Aspirin" in client.get(f"/medications/{med['id']}/edit").text
    bad = post(client, f"/medications/{med['id']}", {"name": "Aspirin", "times": ["99:99"]})
    assert "Please check the form" in bad.text
    good = post(
        client,
        f"/medications/{med['id']}",
        {"name": "Aspirin", "times": ["08:00", "20:00"], "start_date": "2026-11-01"},
    )
    assert "Saved Aspirin" in good.text
    assert store.list(f"circles/{code}/medications")[0]["times"] == ["08:00", "20:00"]
    assert "Medicine stopped" in post(client, f"/medications/{med['id']}/stop").text
