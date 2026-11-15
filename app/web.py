"""Shared web helpers: services container, session auth, CSRF, rendering."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.models import Circle, Member
from app.security import RateLimiter, csrf_matches, new_csrf_token
from app.services.ai import AiService
from app.services.care import CareService, NotFound
from app.services.reminders import ReminderService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@dataclass
class Services:
    """Everything a request handler needs, built once at startup."""

    settings: Settings
    care: CareService
    reminders: ReminderService
    ai: AiService
    login_limiter: RateLimiter
    ai_limiter: RateLimiter
    verify_task_token: Callable[[str], bool]


class LoginRequired(Exception):
    """Raised when a page needs a signed-in member."""


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return token


def require_csrf(request: Request, csrf: Annotated[str, Form()] = "") -> None:
    """Reject state-changing requests that lack the session's CSRF token."""
    if not csrf_matches(request.session.get("csrf"), csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your session expired. Please try again.")


CsrfDep = Annotated[None, Depends(require_csrf)]


def current_member(request: Request, services: ServicesDep) -> Member:
    """Load the signed-in member fresh from the store on every request.

    Removing someone or changing their access takes effect immediately,
    because nothing about permissions is trusted from the cookie.
    """
    circle_id = request.session.get("circle_id")
    member_id = request.session.get("member_id")
    if not circle_id or not member_id:
        raise LoginRequired
    try:
        return services.care.get_member(circle_id, member_id)
    except NotFound:
        request.session.clear()
        raise LoginRequired from None


MemberDep = Annotated[Member, Depends(current_member)]


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"message": message, "kind": kind})


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def render(
    request: Request,
    template: str,
    *,
    member: Member | None = None,
    circle: Circle | None = None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    messages = request.session.pop("flash", [])
    return TEMPLATES.TemplateResponse(
        request,
        template,
        {
            "member": member,
            "circle": circle,
            "csrf_token": csrf_token(request),
            "messages": messages,
            **context,
        },
        status_code=status_code,
    )


def client_ip(request: Request) -> str:
    """Best-effort client address.

    Cloud Run appends the real client address as the last X-Forwarded-For
    entry; earlier entries can be forged by the client, so they are ignored.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
