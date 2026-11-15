"""Nothing fails silently: friendly error pages and honest messages."""

from __future__ import annotations

from tests.conftest import create_circle, post


def test_unknown_url_gets_the_friendly_error_page(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_appointment_message_is_honest_when_nobody_has_email(client):
    create_circle(client)
    post(client, "/circle/me", {"email": ""})
    response = post(client, "/appointments", {"doctor": "Dr. Rao", "starts_at": "2026-12-01T10:00"})
    assert "Nobody in the circle has an email address yet" in response.text
