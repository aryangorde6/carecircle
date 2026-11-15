"""Passcode access, CSRF, rate limiting, roles and cross-circle isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import create_circle, csrf_from, post


def login(client: TestClient, code: str, passcode: str):
    return client.post(
        "/login", data={"csrf": csrf_from(client), "circle_code": code, "passcode": passcode}
    )


def add_member(client: TestClient, name: str, role: str, access: str, passcode: str):
    return post(
        client,
        "/circle/members",
        {
            "name": name,
            "role": role,
            "access": access,
            "passcode": passcode,
            "email": f"{name.lower()}@example.com",
        },
    )


def test_home_page_has_accessible_landmarks(client):
    html = client.get("/").text
    assert '<html lang="en">' in html
    assert 'href="#main"' in html
    assert '<main id="main"' in html
    assert 'for="circle_code"' in html


def test_security_headers_present(client):
    response = client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_create_circle_signs_in_elder_and_shows_code(client):
    code = create_circle(client)
    assert len(code) == 8
    assert client.get("/today").status_code == 200


def test_pages_require_login(client):
    response = client.get("/today", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_without_csrf_is_rejected(client):
    create_circle(client)
    response = client.post(
        "/appointments", data={"doctor": "Dr X", "starts_at": "2030-01-01T10:00"}
    )
    assert response.status_code == 403


def test_weak_or_mismatched_passcode_rejected(client):
    token = csrf_from(client)
    response = client.post(
        "/circles",
        data={"csrf": token, "elder_name": "A", "passcode": "aaaaaa", "passcode_confirm": "aaaaaa"},
    )
    assert "too easy" in response.text
    response = client.post(
        "/circles",
        data={
            "csrf": csrf_from(client),
            "elder_name": "A",
            "passcode": "abc12345",
            "passcode_confirm": "abc12399",
        },
    )
    assert "do not match" in response.text


def test_login_with_right_and_wrong_passcode(client):
    code = create_circle(client)
    post(client, "/logout")
    assert "did not match" in login(client, code, "wrong-pass").text
    assert "Welcome, Asha" in login(client, code.lower(), "sunrise42").text


def test_login_is_rate_limited(client):
    code = create_circle(client)
    post(client, "/logout")
    for _ in range(8):
        login(client, code, "wrong-pass")
    response = login(client, code, "sunrise42")
    assert "Too many sign-in attempts" in response.text


def test_passcodes_are_hashed(client, store):
    code = create_circle(client)
    members = store.list(f"circles/{code}/members")
    assert members[0]["passcode_hash"].startswith("$argon2")
    assert "sunrise42" not in str(members)


def test_only_elder_manages_access(client, app):
    code = create_circle(client)
    assert "was added" in add_member(client, "Ravi", "caretaker", "edit", "ravi-pass-1").text
    assert "was added" in add_member(client, "Meera", "family", "view", "meera-pass-1").text

    family = TestClient(app)
    login(family, code, "meera-pass-1")
    page = family.get("/circle").text
    assert "Only Asha can add people" in page
    response = add_member(family, "Intruder", "caretaker", "edit", "intruder-1")
    assert "Only the elder can add people" in response.text


def test_view_only_member_cannot_edit(client, app):
    code = create_circle(client)
    add_member(client, "Meera", "family", "view", "meera-pass-1")
    family = TestClient(app)
    login(family, code, "meera-pass-1")
    response = post(
        family,
        "/medications",
        {"name": "Aspirin", "times": ["08:00"], "start_date": "2030-01-01"},
    )
    assert "view-only access" in response.text


def test_elder_can_upgrade_access_and_remove(client, app, store):
    code = create_circle(client)
    add_member(client, "Meera", "family", "view", "meera-pass-1")
    meera = next(m for m in store.list(f"circles/{code}/members") if m["name"] == "Meera")
    assert (
        "can now view and edit"
        in post(client, f"/circle/members/{meera['id']}/access", {"access": "edit"}).text
    )
    family = TestClient(app)
    login(family, code, "meera-pass-1")
    assert family.get("/today").status_code == 200
    post(client, f"/circle/members/{meera['id']}/remove")
    # Removed members lose access immediately, even with a live session.
    assert family.get("/today", follow_redirects=False).status_code == 303


def test_duplicate_passcode_rejected(client):
    create_circle(client)
    response = add_member(client, "Ravi", "caretaker", "edit", "sunrise42")
    assert "different passcode" in response.text


def test_records_from_another_circle_are_not_reachable(app, store):
    owner = TestClient(app)
    create_circle(owner, "Asha", "sunrise42")
    post(owner, "/medications", {"name": "Aspirin", "times": ["08:00"], "start_date": "2030-01-01"})
    code_a = next(c["id"] for c in store.list("circles"))
    med_id = store.list(f"circles/{code_a}/medications")[0]["id"]

    other = TestClient(app)
    create_circle(other, "Bala", "moonlight7")
    # Bala's circle cannot see, edit or stop Asha's medicine by guessing its id.
    assert "not found" in other.get(f"/medications/{med_id}/edit").text
    assert "not found" in post(other, f"/medications/{med_id}/stop").text
    assert store.list(f"circles/{code_a}/medications")[0]["active"] is True


def test_docs_hidden_in_production(store):
    from app.config import Settings
    from app.main import create_app

    prod = create_app(
        Settings(environment="production", session_secret="s" * 40, secure_cookies=True),
        store=store,
        ai_model=None,
    )
    client = TestClient(prod)
    assert client.get("/docs").status_code == 404
    assert "strict-transport-security" in client.get("/").headers
    # With no audience configured, production refuses scheduler calls.
    assert client.post("/tasks/reminders").status_code == 401


def test_head_requests_supported_for_uptime_checks(client):
    assert client.head("/").status_code == 200
    assert client.head("/healthz").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
