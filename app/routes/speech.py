"""Read aloud: on-screen text becomes speech with Google Cloud Text-to-Speech."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.services.speech import SpeechUnavailable
from app.web import CsrfDep, MemberDep, ServicesDep

router = APIRouter()


@router.post("/speak")
async def speak(
    member: MemberDep,
    services: ServicesDep,
    _: CsrfDep,
    text: Annotated[str, Form(max_length=4000)] = "",
    lang: Annotated[str, Form(max_length=8)] = "en",
) -> Response:
    """Members only, CSRF-checked and rate-limited; the browser falls back to its own voice."""
    if not services.speech_limiter.hit(f"speak:{member.circle_id}:{member.id}"):
        return Response(status_code=429)
    try:
        audio = await run_in_threadpool(services.speech.speak, text, lang)
    except SpeechUnavailable:
        return Response(status_code=503)
    return Response(audio, media_type="audio/mpeg")
