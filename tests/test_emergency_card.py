"""The emergency card: elder-controlled public QR link, health details and open alerts."""

from __future__ import annotations

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.models import Access, Role
from app.services.care import CareService, PermissionDenied
from app.services.demo import create_demo_circle
from app.store import InMemoryStore
from tests.conftest import create_circle, post

CARD_LINK = re.compile(r'href="http://localhost:8080(/card/[A-Za-z0-9_-]+)"')


@pytest.fixture
def care() -> CareService:
    return CareService(InMemoryStore())


def _setup(care: CareService):
    circle, elder = care.create_circle("Asha", "sunrise42", "Asia/Kolkata", "elder@example.com")
    editor = care.add_member(elder, "Ravi", Role.CARETAKER, Access.EDIT, None, "ravi-pass-1")
    viewer = care.add_member(elder, "Meera", Role.FAMILY, Access.VIEW, None, "meera-pass-1")
    care.save_medication(
        elder,
        name="Amlodipine",
        dosage="5 mg",
        instructions="for blood pressure",
        times=["09:00"],
        start_date=date(2026, 1, 1),
        end_date=None,
    )
    return circle, elder, editor, viewer


def test_only_the_elder_turns_the_card_on_or_off(care):
    _, elder, editor, _ = _setup(care)
    with pytest.raises(PermissionDenied):
        care.enable_card(editor)
    circle = care.enable_card(elder)
    assert circle.card_id and care.open_card(circle.card_id) is not None
    with pytest.raises(PermissionDenied):
        care.disable_card(editor)
    care.disable_card(elder)
    assert care.open_card(circle.card_id) is None


def test_a_new_link_retires_the_old_one(care):
    _, elder, _, _ = _setup(care)
    old = care.enable_card(elder).card_id
    new = care.enable_card(elder).card_id
    assert old != new
    assert old and care.open_card(old) is None
    assert new and care.open_card(new) is not None


def test_editors_can_edit_health_details_but_viewers_cannot(care):
    _, _, editor, viewer = _setup(care)
    details = {
        "blood_group": "O+",
        "allergies": " Penicillin ",
        "conditions": "Diabetes",
        "emergency_contacts": "Ravi +91 55500 01234",
    }
    circle = care.update_health_info(editor, **details)
    assert circle.allergies == "Penicillin" and circle.blood_group == "O+"
    with pytest.raises(PermissionDenied):
        care.update_health_info(viewer, **details)


def test_opens_are_recorded_newest_first(care):
    _, elder, _, _ = _setup(care)
    circle = care.enable_card(elder)
    assert circle.card_id
    care.open_card(circle.card_id)
    care.open_card(circle.card_id)
    opens = care.card_opens(care.get_circle(circle.id))
    assert len(opens) == 2 and opens[0] >= opens[1]


def test_public_card_needs_no_sign_in_and_emails_the_circle_once(client: TestClient, emailer):
    create_circle(client)
    post(
        client,
        "/circle/health",
        {"blood_group": "B+", "allergies": "Sulfa drugs", "emergency_contacts": "Ravi +91 555"},
    )
    post(client, "/circle/card/on")
    page = client.get("/circle")
    assert "Print the fridge card" in page.text and "data:image/svg+xml" in page.text
    match = CARD_LINK.search(page.text)
    assert match
    path = match.group(1)

    stranger = TestClient(client.app)
    response = stranger.get(path)
    assert response.status_code == 200
    assert "Sulfa drugs" in response.text and "B+" in response.text
    assert "tel:112" in response.text and "Ravi +91 555" in response.text
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert len(emailer.sent) == 1
    to, subject, body = emailer.sent[0]
    assert to == ["elder@example.com"] and "emergency card was just opened" in subject
    assert "112" in body

    stranger.get(path)  # a second scan within 30 minutes sends no second email
    assert len(emailer.sent) == 1
    assert "Opened 2 times" in client.get("/circle").text


def test_turned_off_or_malformed_cards_are_not_found(client: TestClient):
    create_circle(client)
    post(client, "/circle/card/on")
    match = CARD_LINK.search(client.get("/circle").text)
    assert match
    post(client, "/circle/card/off")
    stranger = TestClient(client.app)
    assert stranger.get(match.group(1)).status_code == 404
    assert stranger.get("/card/../../etc").status_code == 404
    assert stranger.get("/card/short").status_code == 404


def test_print_page_shows_qr_and_needs_the_card_on(client: TestClient):
    create_circle(client)
    assert client.get("/circle/card/print", follow_redirects=False).status_code == 303
    post(client, "/circle/card/on")
    page = client.get("/circle/card/print")
    assert page.status_code == 200 and "data:image/svg+xml" in page.text
    assert "Call 112" in page.text


def test_unknown_blood_group_is_refused(client: TestClient):
    create_circle(client)
    response = post(client, "/circle/health", {"blood_group": "Z9"})
    assert "Please choose a blood group" in response.text


def test_public_card_is_rate_limited(client: TestClient):
    create_circle(client)
    post(client, "/circle/card/on")
    match = CARD_LINK.search(client.get("/circle").text)
    assert match
    stranger = TestClient(client.app)
    codes = {stranger.get(match.group(1)).status_code for _ in range(31)}
    assert 429 in codes


def test_demo_circle_has_its_card_on(care):
    demo = create_demo_circle(care)
    assert demo.circle.card_id and demo.circle.allergies
    card = care.open_card(demo.circle.card_id)
    assert card is not None and card.demo
