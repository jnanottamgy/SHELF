"""One dictConfig, applied once at startup. Log identifiers, never content."""

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any

from shelf.config import settings

# Per-task state: one webhook's ids are invisible to every concurrent one.
wa_message_id_var: ContextVar[str | None] = ContextVar("wa_message_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)


@contextmanager
def log_context(
    *, wa_message_id: str | None = None, user_id: int | None = None
) -> Generator[None]:
    """Tag every log line emitted inside the block. Nests; None leaves a var alone."""
    # Heterogeneous by design: a str-valued var and an int-valued one.
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []
    if wa_message_id is not None:
        tokens.append((wa_message_id_var, wa_message_id_var.set(wa_message_id)))
    if user_id is not None:
        tokens.append((user_id_var, user_id_var.set(user_id)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class RequestContextFilter(logging.Filter):
    """Stamps the ambient ids onto every record. An explicit extra= wins."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "wa_message_id"):
            wa_message_id = wa_message_id_var.get()
            record.wa_message_id = wa_message_id if wa_message_id is not None else "-"
        if not hasattr(record, "user_id"):
            user_id = user_id_var.get()
            record.user_id = user_id if user_id is not None else "-"
        return True


# Built from a throwaway record so it tracks whatever attrs this Python adds.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "color_message",  # uvicorn extra: same text with ANSI codes baked in
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with extra={...} merged in as real fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # default=str so a stray datetime can never crash the logger.
        return json.dumps(payload, default=str)


CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(wa_message_id)s] %(message)s"


def configure_logging() -> None:
    dictConfig(
        {
            "version": 1,
            # uvicorn built its loggers before importing us; True would mute them.
            "disable_existing_loggers": False,
            "filters": {"request_context": {"()": RequestContextFilter}},
            "formatters": {
                "console": {"format": CONSOLE_FORMAT, "datefmt": "%H:%M:%S"},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": settings.log_format,
                    "filters": ["request_context"],
                }
            },
            "loggers": {
                # Every shelf.* module logger inherits this by name.
                "shelf": {
                    "level": settings.log_level.upper(),
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "sqlalchemy.engine": {
                    "handlers": ["default"],
                    "level": "INFO" if settings.sql_echo else "WARNING",
                    "propagate": False,
                },
            },
            # Catch-all for third-party libraries.
            "root": {"handlers": ["default"], "level": "WARNING"},
        }
    )
