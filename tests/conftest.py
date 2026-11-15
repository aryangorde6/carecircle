"""Shared fixtures: an app wired with in-memory storage and fake Google services."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ExtractedMedication
from app.services.ai import WeeklySummary
from app.store import InMemoryStore

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


@dataclass
class FakeEmailer:
    sent: list[tuple[list[str], str, str]] = field(default_factory=list)

    def send(self, to: list[str], subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@dataclass
class FakeAi:
    medications: list[ExtractedMedication] = field(default_factory=list)
    calls: int = 0
    last_facts: str = ""

    def extract_medications(self, image: bytes, mime_type: str) -> list[ExtractedMedication]:
        self.calls += 1
        return self.medications

    def weekly_summary(self, facts: str) -> WeeklySummary:
        self.calls += 1
        self.last_facts = facts
        return WeeklySummary(summary="A good week overall.", doctor_questions=["Any changes?"])


@pytest.fixture
def settings() -> Settings:
    return Settings(session_secret="test-secret-" + "x" * 40, ai_daily_call_cap=5)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def emailer() -> FakeEmailer:
    return FakeEmailer()


@pytest.fixture
def fake_ai() -> FakeAi:
    return FakeAi(
        medications=[
            ExtractedMedication(name="Metformin", dosage="500 mg", times=["09:00", "21:00"])
        ]
    )


@pytest.fixture
def app(settings, store, emailer, fake_ai):
    return create_app(
        settings,
        store=store,
        emailer=emailer,
        ai_model=fake_ai,
        verify_task_token=lambda token: token == "good-token",
    )


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def csrf_from(client: TestClient, path: str = "/") -> str:
    match = CSRF_RE.search(client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def create_circle(client: TestClient, name: str = "Asha", passcode: str = "sunrise42") -> str:
    """Create a circle as the elder and return its code."""
    token = csrf_from(client)
    response = client.post(
        "/circles",
        data={
            "csrf": token,
            "elder_name": name,
            "passcode": passcode,
            "passcode_confirm": passcode,
            "timezone": "Asia/Kolkata",
            "email": "elder@example.com",
        },
    )
    assert response.status_code == 200, response.text
    match = re.search(r'<span aria-hidden="true">([A-Z0-9]{8})</span>', response.text)
    assert match
    return match.group(1)


def post(client: TestClient, path: str, data: dict | None = None, **kwargs):
    """POST with the current session's CSRF token."""
    return client.post(path, data={"csrf": csrf_from(client, "/today"), **(data or {})}, **kwargs)
