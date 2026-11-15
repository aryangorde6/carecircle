"""Logging setup: structured JSON on Cloud Run, plain text locally.

Cloud Logging parses one JSON object per line on stdout, so ``severity``
becomes the log level in the console and errors with a ``stack_trace``
are picked up by Error Reporting. Messages never include personal data.
"""

from __future__ import annotations

import json
import logging
import os


class CloudLoggingFormatter(logging.Formatter):
    """Formats a record as a Cloud Logging structured entry."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if os.environ.get("K_SERVICE"):  # set by Cloud Run
        handler.setFormatter(CloudLoggingFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
