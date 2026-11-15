"""Security helpers: passcode hashing, CSRF tokens, rate limiting, headers."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Unambiguous characters (no 0/O, 1/I/L) so codes are easy to read aloud.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CIRCLE_CODE_LENGTH = 8
PASSCODE_MIN_LENGTH = 6
PASSCODE_MAX_LENGTH = 64


def hash_passcode(passcode: str) -> str:
    """Hash a passcode with Argon2id."""
    return _hasher.hash(passcode)


def verify_passcode(passcode_hash: str, passcode: str) -> bool:
    """Return True when the passcode matches the stored Argon2 hash."""
    try:
        return _hasher.verify(passcode_hash, passcode)
    except (VerificationError, InvalidHashError):
        return False


def validate_passcode(passcode: str) -> str | None:
    """Return an error message, or None when the passcode is acceptable."""
    if len(passcode) < PASSCODE_MIN_LENGTH:
        return f"Passcode must be at least {PASSCODE_MIN_LENGTH} characters."
    if len(passcode) > PASSCODE_MAX_LENGTH:
        return f"Passcode must be at most {PASSCODE_MAX_LENGTH} characters."
    if len(set(passcode)) < 3:
        return "Passcode is too easy to guess. Use at least 3 different characters."
    return None


def new_circle_code() -> str:
    """Generate a random, readable circle code such as ``K7QM2XPA``."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CIRCLE_CODE_LENGTH))


def normalise_circle_code(raw: str) -> str:
    """Uppercase and strip spaces or dashes from a typed circle code."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())[:32]


def new_id() -> str:
    """Generate an unguessable document id."""
    return secrets.token_urlsafe(12)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(expected: str | None, received: str | None) -> bool:
    """Constant-time comparison of CSRF tokens."""
    if not expected or not received:
        return False
    return hmac.compare_digest(expected, received)


class RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary string.

    Kept in process memory; Cloud Run is deployed with a small max-instances
    count, so this is an effective first line of defence for a demo service.
    """

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        """Record one event; return False when the key is over its limit."""
        current = time.monotonic() if now is None else now
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and current - events[0] > self.window_seconds:
                events.popleft()
            if len(events) >= self.max_events:
                return False
            events.append(current)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self'; "
        "script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
