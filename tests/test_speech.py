"""Read aloud through Google Cloud Text-to-Speech (faked here)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.speech import (
    MAX_SPEECH_CHARS,
    SpeechBudget,
    SpeechService,
    SpeechUnavailable,
    voice_for,
)
from app.store import InMemoryStore
from tests.conftest import FakeSpeech, create_circle, csrf_from, post


def test_member_gets_mp3_in_the_right_language(client, fake_speech):
    create_circle(client)
    response = post(client, "/speak", {"text": "  सुबह की दवाई   ले ली ", "lang": "hi"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3-fake-mp3"
    assert fake_speech.calls == [("सुबह की दवाई ले ली", "hi-IN")]


def test_today_offers_server_read_aloud(client):
    create_circle(client)
    page = client.get("/today").text
    if 'data-speak="#speak-text"' in page:
        assert 'data-speak-url="/speak"' in page


def test_text_is_clipped_and_unknown_language_reads_in_english(client, fake_speech):
    create_circle(client)
    response = post(client, "/speak", {"text": "word " * 500, "lang": "xx"})
    assert response.status_code == 200
    text, language = fake_speech.calls[0]
    assert len(text) <= MAX_SPEECH_CHARS
    assert language == "en-IN"


def test_signed_out_visitor_cannot_use_it(client, fake_speech):
    token = csrf_from(client)
    response = client.post("/speak", data={"csrf": token, "text": "hello"}, follow_redirects=False)
    assert response.status_code == 303
    assert fake_speech.calls == []


def test_missing_csrf_is_refused(client, fake_speech):
    create_circle(client)
    response = client.post("/speak", data={"text": "hello"})
    assert response.status_code == 403
    assert fake_speech.calls == []


def test_empty_text_and_rate_limit(client, app, fake_speech):
    create_circle(client)
    assert post(client, "/speak", {"text": "   "}).status_code == 503
    app.state.services.speech_limiter.max_events = 2  # the empty request counted too
    assert post(client, "/speak", {"text": "one"}).status_code == 200
    assert post(client, "/speak", {"text": "two"}).status_code == 429
    assert [call[0] for call in fake_speech.calls] == ["one"]


def test_turned_off_without_google_project(settings, store, emailer):
    app = create_app(settings, store=store, emailer=emailer)
    client = TestClient(app)
    create_circle(client)
    assert "data-speak-url" not in client.get("/today").text
    assert post(client, "/speak", {"text": "hello"}).status_code == 503


def test_daily_budget_is_a_hard_stop():
    speech = FakeSpeech()
    service = SpeechService(speech, SpeechBudget(InMemoryStore(), daily_cap=2))
    service.speak("one")
    service.speak("two")
    with pytest.raises(SpeechUnavailable):
        service.speak("three")
    assert len(speech.calls) == 2


@pytest.mark.parametrize(("code", "voice"), [("hi", "hi-IN"), ("TA", "ta-IN"), ("", "en-IN")])
def test_voice_for(code, voice):
    assert voice_for(code) == voice

