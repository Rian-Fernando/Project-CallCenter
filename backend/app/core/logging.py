"""Developer logging setup (§24).

Logs are for developers and are deliberately verbose about *why* something
failed. They never contain resident audio, and transcripts are logged only at
DEBUG level so a normal INFO-level run records no conversation content.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-34s  %(message)s"
_DATEFMT = "%H:%M:%S"


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[41m\033[97m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        out = super().format(record)
        if sys.stderr.isatty():
            color = self.COLORS.get(record.levelname, "")
            if color:
                return f"{color}{out}{self.RESET}"
        return out


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColorFormatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # These libraries are extremely chatty at INFO and drown out our own logs.
    for noisy in ("httpx", "httpcore", "urllib3", "qdrant_client",
                  "faster_whisper", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
