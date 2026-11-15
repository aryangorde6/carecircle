"""Read aloud with Google Cloud Text-to-Speech.

Many laptops and some phones have no speech voices installed, so the browser's
own speechSynthesis stays silent. The server turns short on-screen text into
MP3 instead, in the elder's language; the browser's voices remain the fallback.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.store import DocumentStore

logger = logging.getLogger(__name__)

MAX_SPEECH_CHARS = 600
TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Two-letter codes the pages use (HTML lang attributes) mapped to Cloud TTS voices.
SPEECH_LANGUAGES = {
    "en": "en-IN",
    "hi": "hi-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "kn": "kn-IN",
    "gu": "gu-IN",
    "ml": "ml-IN",
    "pa": "pa-IN",
}


class SpeechUnavailable(Exception):
    """Server read-aloud is off, over its daily limit, or failed."""


class SpeechModel(Protocol):
    def synthesize(self, text: str, language_code: str) -> bytes: ...


@dataclass
class SpeechBudget:
    """Hard daily spending stop for Text-to-Speech, separate from the Gemini cap."""

    store: DocumentStore
    daily_cap: int

    def consume(self, now: datetime | None = None) -> None:
        day = (now or datetime.now(UTC)).strftime("%Y%m%d")
        used = self.store.increment("tts_usage", day, "calls")
        if used > self.daily_cap:
            raise SpeechUnavailable("The daily read-aloud limit has been reached.")


def voice_for(language: str) -> str:
    """Unknown or empty language codes read in Indian English."""
    return SPEECH_LANGUAGES.get(language.strip().lower(), "en-IN")


class SpeechService:
    """Cleans and clips the text and applies the budget before every call."""

    def __init__(self, model: SpeechModel | None, budget: SpeechBudget) -> None:
        self._model = model
        self._budget = budget

    @property
    def enabled(self) -> bool:
        return self._model is not None

    def speak(self, text: str, language: str = "en") -> bytes:
        if self._model is None:
            raise SpeechUnavailable("Reading aloud on the server is turned off.")
        cleaned = " ".join(text.split())[:MAX_SPEECH_CHARS]
        if not cleaned:
            raise SpeechUnavailable("There is nothing to read.")
        self._budget.consume()
        return self._model.synthesize(cleaned, voice_for(language))


class GoogleSpeech:  # pragma: no cover - needs Google credentials; faked in tests
    """Calls the Cloud Text-to-Speech REST API as the Cloud Run service account."""

    def __init__(self) -> None:
        self._session: Any = None

    def _authorized_session(self) -> Any:
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._session = AuthorizedSession(credentials)
        return self._session

    def synthesize(self, text: str, language_code: str) -> bytes:
        response = self._authorized_session().post(
            TTS_URL,
            json={
                "input": {"text": text},
                "voice": {"languageCode": language_code},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.9},
            },
            timeout=15,
        )
        if response.status_code != 200:
            # Log the status and sizes only, never the text being read.
            logger.warning(
                "text-to-speech failed: status=%s language=%s chars=%d",
                response.status_code,
                language_code,
                len(text),
            )
            raise SpeechUnavailable("Reading aloud is not available right now.")
        return base64.b64decode(response.json()["audioContent"])
