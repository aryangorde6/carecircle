"""Endpoints called by Cloud Scheduler, authenticated with Google-signed OIDC tokens."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.config import Settings
from app.web import ServicesDep

router = APIRouter(prefix="/tasks", include_in_schema=False)
logger = logging.getLogger(__name__)


def build_task_verifier(settings: Settings):
    """Return a function that checks a bearer token from Cloud Scheduler.

    In production the token must be a valid Google OIDC token for our audience,
    issued to the scheduler's service account. Locally (no audience set) tasks
    are allowed so reminders can be tried by hand; production refuses that.
    """

    def verify(token: str) -> bool:
        if not settings.tasks_audience:
            return not settings.is_production
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_oauth2_token(
                token, google_requests.Request(), audience=settings.tasks_audience
            )
        except ValueError:
            return False
        return bool(
            claims.get("email_verified") and claims.get("email") == settings.tasks_service_account
        )

    return verify


@router.post("/reminders")
def run_reminders(request: Request, services: ServicesDep) -> dict[str, object]:
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not services.verify_task_token(token):
        logger.warning("rejected unauthenticated task call")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorised")
    report = services.reminders.run()
    return {"circles": report.circles, "emails": report.emails, "kinds": report.kinds}
