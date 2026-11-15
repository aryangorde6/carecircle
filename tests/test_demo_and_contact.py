"""Demo circles, self-service reminder settings and test emails."""

from __future__ import annotations

import smtplib
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.services.care import CareService
from app.services.demo import create_demo_circle
from app.services.emailer import EmailFailed, SmtpEmailer
from app.store import InMemoryStore
from tests.conftest import csrf_from, post


def test_demo_circle_has_members_medicines_history_and_appointments():
    care = CareService(InMemoryStore())
    now = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)  # 10:30 IST
    demo = create_demo_circle(care, now)
    circle = demo.circle
    assert circle.demo
    assert {m.role.value for m in care.list_members(circle.id)} == {"elder", "caretaker", "family"}
    assert len(care.list_medications(circle.id)) == 3
    assert len(care.list_appointments(circle.id, upcoming_from=now)) == 2
    week, per_med = care.adherence(circle, date(2026, 11, 9), date(2026, 11, 15), now)
    assert week.missed >= 3 and week.skipped == 1 and week.taken > 10
    assert per_med["Atorvastatin"].missed >= 3
    # Today's most recent due dose (09:00) is left unmarked to show the reminder ladder.
    today = care.schedule_for_day(circle, date(2026, 11, 15))
    assert [d.status for d in today if d.medication.name == "Amlodipine"] == [None]
    assert all(m.email is None for m in care.list_members(circle.id))


def test_demo_passcodes_sign_in_as_each_role(client, app):
    response = client.post("/demo", data={"csrf": csrf_from(client)})
    assert "Today for Kamala Iyer" in response.text
    page = client.get("/circle").text
    assert "Try the other roles" in page
    code = page.split("Circle code: <strong>")[1][:8]
    passcode = page.split("<code>")[3].split("</code>")[0]  # Priya, family, view only
    other = TestClient(app)
    other.post("/login", data={"csrf": csrf_from(other), "circle_code": code, "passcode": passcode})
    assert "(view only)" in other.get("/today").text


def test_demo_requires_csrf(client):
    assert client.post("/demo").status_code == 403


def test_member_updates_own_email_and_gets_test_email(client, emailer):
    client.post("/demo", data={"csrf": csrf_from(client)})
    response = post(client, "/circle/test-email")
    assert "Add your email address first" in response.text
    response = post(client, "/circle/me", {"email": "judge@example.com", "notify": "on"})
    assert "reminder settings were saved" in response.text
    response = post(client, "/circle/test-email")
    assert "Test email sent" in response.text
    assert emailer.sent[-1][0] == ["judge@example.com"]


def test_bad_email_rejected(client):
    client.post("/demo", data={"csrf": csrf_from(client)})
    response = post(client, "/circle/me", {"email": "not-an-email"})
    assert "check the email address" in response.text


def test_failed_email_shows_a_friendly_message(client, emailer):
    client.post("/demo", data={"csrf": csrf_from(client)})
    post(client, "/circle/me", {"email": "judge@example.com", "notify": "on"})
    emailer.fail = True
    response = post(client, "/circle/test-email")
    assert response.status_code == 200
    assert "could not be sent" in response.text


def test_smtp_errors_become_email_failed(monkeypatch):
    def refuse(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    settings = Settings(smtp_user="u@example.com", smtp_password="x", email_from="u@example.com")
    with pytest.raises(EmailFailed):
        SmtpEmailer(settings).send(["a@example.com"], "Subject", "Body")
