"""The care circle: who is in it and what each person may do.

Only the elder can add or remove people and change their access, which
keeps the elder at the centre of their own healthcare decisions.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.models import Access, Role
from app.security import validate_passcode
from app.services.care import NotFound, PasscodeInUse, PermissionDenied
from app.web import CsrfDep, MemberDep, ServicesDep, flash, redirect, render

router = APIRouter(prefix="/circle")


@router.get("", response_class=HTMLResponse)
def circle_page(request: Request, member: MemberDep, services: ServicesDep) -> HTMLResponse:
    circle = services.care.get_circle(member.circle_id)
    return render(
        request,
        "circle.html",
        member=member,
        circle=circle,
        members=services.care.list_members(circle.id),
        new_circle=request.session.pop("new_circle", False),
    )


@router.post("/members")
def add_member(
    request: Request,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    name: Annotated[str, Form(max_length=80)],
    role: Annotated[Role, Form()],
    access: Annotated[Access, Form()],
    passcode: Annotated[str, Form(max_length=64)],
    email: Annotated[str, Form(max_length=254)] = "",
) -> RedirectResponse:
    error = validate_passcode(passcode)
    if not name.strip():
        error = "Please enter a name."
    if error:
        flash(request, error, "error")
        return redirect("/circle")
    try:
        added = services.care.add_member(
            member, name.strip(), role, access, email.strip() or None, passcode
        )
    except (PermissionDenied, PasscodeInUse) as exc:
        flash(request, str(exc), "error")
    except ValidationError:
        flash(request, "Please check the email address.", "error")
    else:
        flash(
            request,
            f"{added.name} was added. Share the circle code and their passcode with them.",
            "success",
        )
    return redirect("/circle")


@router.post("/members/{member_id}/access")
def change_access(
    request: Request,
    member_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
    access: Annotated[Access, Form()],
) -> RedirectResponse:
    try:
        changed = services.care.set_member_access(member, member_id, access)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except NotFound:
        flash(request, "That person was not found.", "error")
    else:
        label = "can now view and edit" if access == Access.EDIT else "can now only view"
        flash(request, f"{changed.name} {label}.", "success")
    return redirect("/circle")


@router.post("/members/{member_id}/remove")
def remove_member(
    request: Request,
    member_id: str,
    member: MemberDep,
    services: ServicesDep,
    _csrf: CsrfDep,
) -> RedirectResponse:
    try:
        services.care.remove_member(member, member_id)
    except PermissionDenied as exc:
        flash(request, str(exc), "error")
    except NotFound:
        flash(request, "That person was not found.", "error")
    else:
        flash(request, "Removed from the circle. Their passcode no longer works.", "success")
    return redirect("/circle")
