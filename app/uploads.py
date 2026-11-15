"""Validation for uploaded prescription photos and voice notes.

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


class AudioRejected(ValueError):
    """The upload is not an allowed voice recording or is too large."""


def detect_audio_type(data: bytes) -> str | None:
    """Return the MIME type of common voice-note formats, otherwise None.

    Covers what browsers record (WebM/Opus, Ogg/Opus, MP4/AAC) and what phone
    recorder apps save (M4A, MP3, WAV).
    """
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


def validate_audio(data: bytes, max_bytes: int) -> str:
    """Return the detected MIME type or raise :class:`AudioRejected`."""
    if not data:
        raise AudioRejected("Please record or choose a voice note first.")
    if len(data) > max_bytes:
        raise AudioRejected("The voice note is too long. Please keep it under a minute.")
    mime = detect_audio_type(data)
    if mime is None:
        raise AudioRejected("That file is not a voice recording we can read.")
    return mime
