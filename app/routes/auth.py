"""Landing page, creating a circle, passcode sign-in and sign-out."""

from __future__ import annotations

from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.security import normalise_circle_code, validate_passcode
from app.services.demo import create_demo_circle
from app.web import CsrfDep, ServicesDep, client_ip, flash, redirect, render

router = APIRouter()

COMMON_TIMEZONES = (
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
    "UTC",
)


def _start_session(request: Request, circle_id: str, member_id: str) -> None:
    """Rotate the session on sign-in to prevent session fixation."""
    request.session.clear()
    request.session["circle_id"] = circle_id
    request.session["member_id"] = member_id


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    if request.session.get("member_id"):
        return redirect("/today")
    return render(request, "index.html", timezones=COMMON_TIMEZONES)


@router.post("/circles")
def create_circle(
    request: Request,
    services: ServicesDep,
    _csrf: CsrfDep,
    elder_name: Annotated[str, Form(max_length=80)],
    passcode: Annotated[str, Form(max_length=64)],
    passcode_confirm: Annotated[str, Form(max_length=64)],
    timezone: Annotated[str, Form(max_length=64)] = "Asia/Kolkata",
    email: Annotated[str, Form(max_length=254)] = "",
) -> RedirectResponse:
    if not services.login_limiter.hit(f"create:{client_ip(request)}"):
        flash(request, "Too many attempts. Please wait a few minutes.", "error")
        return redirect("/")
    error = validate_passcode(passcode)
    if passcode != passcode_confirm:
        error = "The two passcodes do not match."
    if timezone not in available_timezones():
        error = "Please choose a valid time zone."
    if not elder_name.strip():
        error = "Please enter the elder's name."
    if error:
        flash(request, error, "error")
        return redirect("/#create")
    try:
        circle, elder = services.care.create_circle(
            elder_name.strip(), passcode, timezone, email.strip() or None
        )
    except ValidationError:
        flash(request, "Please check the email address.", "error")
        return redirect("/#create")
    _start_session(request, circle.id, elder.id)
    request.session["new_circle"] = True
    return redirect("/circle")


@router.post("/demo")
def start_demo(request: Request, services: ServicesDep, _csrf: CsrfDep) -> RedirectResponse:
    """Create a fresh, private demo circle with sample data and sign in as the elder."""
    if not services.login_limiter.hit(f"demo:{client_ip(request)}"):
        flash(request, "Too many demo circles just now. Please wait a few minutes.", "error")
        return redirect("/")
    demo = create_demo_circle(services.care)
    _start_session(request, demo.circle.id, demo.elder.id)
    request.session["demo_passcodes"] = demo.passcodes
    flash(
        request,
        "Welcome to a demo circle with a week of sample data. You are signed in as Kamala, "
        "the elder. Try marking a dose, scanning a prescription or writing the weekly summary.",
        "success",
    )
    return redirect("/today")


@router.post("/login")
def login(
    request: Request,
    services: ServicesDep,
    _csrf: CsrfDep,
    circle_code: Annotated[str, Form(max_length=32)],
    passcode: Annotated[str, Form(max_length=64)],
) -> RedirectResponse:
    code = normalise_circle_code(circle_code)
    allowed_ip = services.login_limiter.hit(f"login-ip:{client_ip(request)}")
    allowed_circle = services.login_limiter.hit(f"login-circle:{code}")
    if not (allowed_ip and allowed_circle):
        flash(request, "Too many sign-in attempts. Please wait 5 minutes and try again.", "error")
        return redirect("/")
    member = services.care.authenticate(code, passcode)
    if member is None:
        flash(request, "That circle code and passcode did not match.", "error")
        return redirect("/")
    services.login_limiter.reset(f"login-circle:{code}")
    _start_session(request, member.circle_id, member.id)
    flash(request, f"Welcome, {member.name}.", "success")
    return redirect("/today")


@router.post("/logout")
def logout(request: Request, _csrf: CsrfDep) -> RedirectResponse:
    request.session.clear()
    flash(request, "You have signed out.", "info")
    return redirect("/")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    return RedirectResponse("/static/favicon.svg", status_code=301)


@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
