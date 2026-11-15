"""CareCircle application factory."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, load_settings
from app.routes import appointments, auth, circle, insights, medications, tasks, today
from app.security import SECURITY_HEADERS, RateLimiter
from app.services.ai import AiBudget, AiModel, AiService
from app.services.care import CareService
from app.services.emailer import Emailer, build_emailer
from app.services.reminders import ReminderService
from app.store import DocumentStore, InMemoryStore
from app.web import TEMPLATES, LoginRequired, Services

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
SESSION_MAX_AGE = 12 * 60 * 60


def _default_store(settings: Settings) -> DocumentStore:
    if settings.store_backend == "firestore":
        from app.store_firestore import FirestoreStore

        return FirestoreStore(settings.gcp_project or None)
    return InMemoryStore()


def _default_ai_model(settings: Settings) -> AiModel | None:
    if not settings.ai_enabled:
        return None
    from app.services.gemini_client import GeminiModel

    return GeminiModel(settings.gcp_project, settings.gcp_location, settings.gemini_model)


def create_app(
    settings: Settings | None = None,
    *,
    store: DocumentStore | None = None,
    emailer: Emailer | None = None,
    ai_model: AiModel | None = None,
    verify_task_token: Callable[[str], bool] | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes; production builds real Google clients."""
    settings = settings or load_settings()
    store = store if store is not None else _default_store(settings)
    care = CareService(store)
    app = FastAPI(
        title="CareCircle",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.services = Services(
        settings=settings,
        care=care,
        reminders=ReminderService(
            care, emailer or build_emailer(settings), settings.public_base_url
        ),
        ai=AiService(
            ai_model if ai_model is not None else _default_ai_model(settings),
            AiBudget(store, settings.ai_daily_call_cap),
        ),
        login_limiter=RateLimiter(max_events=8, window_seconds=300),
        ai_limiter=RateLimiter(max_events=settings.ai_session_call_cap, window_seconds=3600),
        verify_task_token=verify_task_token or tasks.build_task_verifier(settings),
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.secure_cookies:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="carecircle_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/tasks/"):
            return Response(status_code=exc.status_code)
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {
                "status_code": exc.status_code,
                "detail": exc.detail,
                "member": None,
                "circle": None,
                "messages": [],
                "csrf_token": "",
            },
            status_code=exc.status_code,
        )

    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static"
    )
    for module in (auth, today, medications, appointments, circle, insights, tasks):
        app.include_router(module.router)
    return app


def get_app() -> FastAPI:  # pragma: no cover - used by uvicorn --factory
    return create_app()


__all__ = ["create_app", "get_app"]
