"""Gemini on Vertex AI via the Google Gen AI SDK.

Authenticates with the Cloud Run service account (Application Default
Credentials), so there is no API key to leak.
"""

from __future__ import annotations

from google import genai
from google.genai import types

from app.models import ExtractedMedication
from app.services.ai import PRESCRIPTION_PROMPT, SUMMARY_PROMPT, WeeklySummary


class GeminiModel:
    """Implements :class:`app.services.ai.AiModel` with Gemini."""

    def __init__(self, project: str, location: str, model: str) -> None:
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def extract_medications(self, image: bytes, mime_type: str) -> list[ExtractedMedication]:
        response = self._client.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=image, mime_type=mime_type),
                "Extract the medicines from this image.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=PRESCRIPTION_PROMPT,
                response_mime_type="application/json",
                response_schema=list[ExtractedMedication],
                temperature=0.1,
                max_output_tokens=2048,
            ),
        )
        parsed = response.parsed
        if not isinstance(parsed, list):
            return []
        return [ExtractedMedication.model_validate(item) for item in parsed]

    def weekly_summary(self, facts: str) -> WeeklySummary:
        response = self._client.models.generate_content(
            model=self._model,
            contents=[f"<data>\n{facts}\n</data>"],
            config=types.GenerateContentConfig(
                system_instruction=SUMMARY_PROMPT,
                response_mime_type="application/json",
                response_schema=WeeklySummary,
                temperature=0.4,
                max_output_tokens=1024,
            ),
        )
        parsed = response.parsed
        if isinstance(parsed, WeeklySummary):
            return parsed
        return WeeklySummary.model_validate_json(response.text or "{}")
