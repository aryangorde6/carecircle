"""Validation for uploaded prescription photos.

The file type is decided from the file's magic bytes, never from its name or
the browser-supplied content type. Files are kept in memory only and are
never written to disk or executed.
"""

from __future__ import annotations

_SIGNATURES: tuple[tuple[bytes, int, str], ...] = (
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"WEBP", 8, "image/webp"),
)


class UploadRejected(ValueError):
    """The upload is not an allowed image or is too large."""


def detect_image_type(data: bytes) -> str | None:
    """Return the MIME type for JPEG, PNG or WebP data, otherwise None."""
    for signature, offset, mime in _SIGNATURES:
        if data[offset : offset + len(signature)] == signature:
            if mime == "image/webp" and data[:4] != b"RIFF":
                continue
            return mime
    return None


def validate_image(data: bytes, max_bytes: int) -> str:
    """Return the detected MIME type or raise :class:`UploadRejected`."""
    if not data:
        raise UploadRejected("Please choose a photo to upload.")
    if len(data) > max_bytes:
        raise UploadRejected(
            f"The photo is too large. The limit is {max_bytes // (1024 * 1024)} MB."
        )
    mime = detect_image_type(data)
    if mime is None:
        raise UploadRejected("Only JPEG, PNG or WebP photos are accepted.")
    return mime
