"""Application settings, read once from environment variables.

Secrets (session key, SMTP password) are injected at runtime by Cloud Run from
Secret Manager and are never committed to the repository.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value and value.strip().isdigit() else default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the app."""

    environment: str = "development"
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    secure_cookies: bool = False

    # Storage: "memory" for local development and tests, "firestore" in production.
    store_backend: str = "memory"
    gcp_project: str = ""
    gcp_location: str = "global"

    # Gemini on Vertex AI. Disabled when no project is configured.
    gemini_model: str = "gemini-3.5-flash"
    ai_daily_call_cap: int = 300
    ai_session_call_cap: int = 15

    # Email: "console" logs a redacted line instead of sending; "smtp" sends.
    email_backend: str = "console"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    public_base_url: str = "http://localhost:8080"

    # Cloud Scheduler calls /tasks/* with a Google-signed OIDC token.
    tasks_audience: str = ""
    tasks_service_account: str = ""

    default_timezone: str = "Asia/Kolkata"
    max_upload_bytes: int = 5 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gcp_project)


def load_settings() -> Settings:
    """Build settings from the process environment."""
    environment = os.environ.get("APP_ENV", "development")
    is_prod = environment == "production"
    session_secret = os.environ.get("SESSION_SECRET", "")
    if is_prod and len(session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be set to at least 32 characters in production")

    return Settings(
        environment=environment,
        session_secret=session_secret or secrets.token_urlsafe(48),
        secure_cookies=_env_bool("SECURE_COOKIES", is_prod),
        store_backend=os.environ.get("STORE_BACKEND", "firestore" if is_prod else "memory"),
        gcp_project=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        gcp_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        ai_daily_call_cap=_env_int("AI_DAILY_CALL_CAP", 300),
        ai_session_call_cap=_env_int("AI_SESSION_CALL_CAP", 15),
        email_backend=os.environ.get("EMAIL_BACKEND", "console"),
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        email_from=os.environ.get("EMAIL_FROM", os.environ.get("SMTP_USER", "")),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
        tasks_audience=os.environ.get("TASKS_AUDIENCE", ""),
        tasks_service_account=os.environ.get("TASKS_SERVICE_ACCOUNT", ""),
        default_timezone=os.environ.get("DEFAULT_TIMEZONE", "Asia/Kolkata"),
    )
