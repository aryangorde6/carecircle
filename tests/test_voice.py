"""Voice notes: Gemini suggests dose updates and a check-in; a person confirms before saving."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.models import ScheduledDose
from app.services.ai import VoiceDose, VoiceNote
from app.uploads import AudioRejected, detect_audio_type, validate_audio
from tests.conftest import create_circle, csrf_from, post

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (WEBM, "audio/webm"),
        (b"OggS" + b"\x00" * 20, "audio/ogg"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"),
        (b"\x00\x00\x00\x18ftypM4A ", "audio/mp4"),
        (b"ID3\x04" + b"\x00" * 10, "audio/mpeg"),
        (b"fLaC" + b"\x00" * 10, "audio/flac"),
        (b"\x89PNG\r\n\x1a\n", None),
        (b"<script>", None),
    ],
)
def test_audio_type_comes_from_magic_bytes(data, mime):
    assert detect_audio_type(data) == mime


def test_audio_validation_limits():
    with pytest.raises(AudioRejected):
        validate_audio(b"", 100)
    with pytest.raises(AudioRejected):
        validate_audio(WEBM * 10, 100)
    with pytest.raises(AudioRejected):
        validate_audio(b"not audio at all", 100)


def test_voice_note_cleans_model_output():
    note = VoiceNote.model_validate(
        {
            "heard": "x" * 900,
            "language_code": '"><script>',
            "doses": [{"number": 1, "status": "taken"}] * 40,
            "reply": "  ok  ",
        }
    )
    assert len(note.heard) == 600 and note.language_code == ""
    assert len(note.doses) == 20 and note.reply == "ok"


def _circle_with_medicine(client: TestClient) -> None:
    create_circle(client)
    post(
        client,
        "/medications",
        {
            "name": "Amlodipine",
            "dosage": "5 mg",
            "times": ["00:01", "23:59"],
            "start_date": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        },
    )


def _send_voice(client: TestClient, data: bytes = WEBM):
    return client.post(
        "/voice",
        data={"csrf": csrf_from(client, "/today")},
        files={"audio": ("voice.webm", data, "audio/webm")},
    )


def test_voice_note_is_only_a_suggestion_until_confirmed(client: TestClient, fake_ai):
    _circle_with_medicine(client)
    assert "Or just say it" in client.get("/today").text
    fake_ai.voice = VoiceNote(
        heard="सुबह की दवाई ले ली, थोड़ा चक्कर आ रहा है",
        heard_english="I took the morning medicine, feeling a bit dizzy",
        language_code="hi",
        doses=[VoiceDose(number=1, status="taken"), VoiceDose(number=9, status="taken")],
        feeling="unwell",
        note="थोड़ा चक्कर",
        reply="ठीक है, सुबह की दवाई दर्ज कर ली।",
    )
    review = _send_voice(client)
    assert review.status_code == 200
    assert 'lang="hi"' in review.text and "Amlodipine 5 mg" in review.text
    assert review.text.count('name="dose"') == 1  # dose 9 does not exist and is dropped
    assert "1. " in fake_ai.last_doses and "Amlodipine" in fake_ai.last_doses
    assert "not recorded yet" in fake_ai.last_doses  # nothing saved before confirming

    value = review.text.split('name="dose" value="')[1].split('"')[0]
    saved = post(client, "/voice/save", {"dose": value, "feeling": "unwell", "note": "dizzy"})
    assert "Saved: Amlodipine taken." in saved.text
    assert "Not well" in saved.text and "dizzy" in saved.text


def test_unclear_voice_note_saves_nothing(client: TestClient, fake_ai):
    _circle_with_medicine(client)
    fake_ai.voice = VoiceNote(heard="", reply="")
    review = _send_voice(client)
    assert "could not pick out" in review.text
    assert "Nothing was ticked" in post(client, "/voice/save").text


def test_wrong_file_type_is_refused_before_gemini(client: TestClient, fake_ai):
    _circle_with_medicine(client)
    response = _send_voice(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    assert "not a voice recording" in response.text
    assert fake_ai.calls == 0


def test_tampered_dose_values_are_ignored(client: TestClient):
    _circle_with_medicine(client)
    response = post(
        client,
        "/voice/save",
        {"dose": ["nope", "x|2026-01-01T09:00:00+05:30|taken", "a|b|c|d"]},
    )
    assert "Nothing was ticked" in response.text


def test_viewers_cannot_send_voice_notes(client: TestClient, fake_ai):
    _circle_with_medicine(client)
    post(
        client,
        "/circle/members",
        {"name": "Meera", "role": "family", "access": "view", "passcode": "meera-pass-1"},
    )
    code = client.get("/circle").text.split('<span aria-hidden="true">')[1][:8]
    post(client, "/logout")
    client.post(
        "/login",
        data={"csrf": csrf_from(client), "circle_code": code, "passcode": "meera-pass-1"},
    )
    assert "Or just say it" not in client.get("/today").text
    assert "view-only" in _send_voice(client).text
    assert fake_ai.calls == 0


def test_dose_list_for_prompt_marks_recorded_doses():
    from app.models import DoseLog, DoseStatus, Medication
    from app.routes.voice import dose_list_for_prompt

    zone = ZoneInfo("Asia/Kolkata")
    med = Medication(
        id="m1",
        circle_id="c",
        name="Metformin",
        dosage="500 mg",
        times=["08:30"],
        start_date=date(2026, 1, 1),
        instructions="after food",
    )
    when = datetime(2026, 11, 15, 8, 30, tzinfo=zone)
    log = DoseLog(
        id="l", circle_id="c", medication_id="m1", scheduled_for=when, status=DoseStatus.TAKEN
    )
    text = dose_list_for_prompt(
        [ScheduledDose(medication=med, scheduled_for=when, log=log)], zone, when
    )
    assert "1. 08:30 Metformin (500 mg; after food): taken" in text
