"""Mask sensitive values from log records and uncaught tracebacks."""

import logging
import sys
import traceback as traceback_module
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, cast

from telescreen.masking.redact import Redactor

_active_redactor: Redactor | None = None
_NON_TEXT_RECORD_FIELDS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "levelno",
        "lineno",
        "msecs",
        "process",
        "relativeCreated",
        "stack_info",
        "thread",
        "msg",
    }
)


class MaskingLogger(logging.Logger):
    """Mask structured extra fields before named logger handlers run."""

    def callHandlers(self, record: logging.LogRecord) -> None:
        """Redact custom record attributes before dispatching to handlers."""
        active_redactor: Redactor | None = _active_redactor
        if active_redactor is not None:
            attribute_name: str
            for attribute_name in record.__dict__:
                if attribute_name not in _NON_TEXT_RECORD_FIELDS:
                    value_text: str = str(getattr(record, attribute_name))
                    safe_value: str = active_redactor.redact(value_text).text
                    if safe_value != value_text:
                        setattr(record, attribute_name, safe_value)
        super().callHandlers(record)


def _logger_class_with_masking(logger_class: type[logging.Logger]) -> type[logging.Logger]:
    """Keep a registered logger subclass while putting masking first in its MRO."""
    return cast(
        type[logging.Logger],
        type(f"Masking{logger_class.__name__}", (MaskingLogger, logger_class), {}),
    )


class RecordFactory(Protocol):
    """Create log records using the standard logging factory call shape."""

    def __call__(
        self,
        name: str,
        level: int,
        pathname: str,
        lineno: int,
        msg: str,
        args: tuple[str | int | float | bool | None, ...],
        exc_info: tuple[type[BaseException], BaseException, TracebackType | None] | None,
        func: str | None = None,
        sinfo: str | None = None,
    ) -> logging.LogRecord: ...


@dataclass(frozen=True)
class MaskingRecordFactory:
    """Redact each record before any logger handler can render it."""

    original: RecordFactory
    redactor: Redactor

    def __call__(
        self,
        name: str,
        level: int,
        pathname: str,
        lineno: int,
        msg: str,
        args: tuple[str | int | float | bool | None, ...],
        exc_info: tuple[type[BaseException], BaseException, TracebackType | None] | None,
        func: str | None = None,
        sinfo: str | None = None,
    ) -> logging.LogRecord:
        """Return a record whose message, exception and stack text are safe."""
        record: logging.LogRecord = self.original(
            name, level, pathname, lineno, msg, args, exc_info, func, sinfo
        )
        try:
            rendered: str = record.getMessage()
        except (TypeError, ValueError):
            rendered = "[REDACTED]"
        if record.exc_info is not None:
            rendered += "\n" + "".join(traceback_module.format_exception(*record.exc_info))
        elif record.exc_text is not None:
            rendered += "\n" + record.exc_text
        if record.stack_info is not None:
            rendered += "\n" + record.stack_info
        record.msg = self.redactor.redact(rendered).text
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record


@dataclass(eq=False)
class MaskingFormatter(logging.Formatter):
    """Format a log record, then redact the complete rendered message."""

    redactor: Redactor

    def __post_init__(self) -> None:
        """Initialize the stdlib formatter state."""
        super().__init__(fmt="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Redact the message, exception and stack text as one string."""
        formatted: str = super().format(record)
        return self.redactor.redact(formatted).text


def configure_logging(level: str, redactor: Redactor | None = None) -> None:
    """Install one masking stream handler and normalize noisy library loggers."""
    global _active_redactor
    active_redactor: Redactor = redactor if redactor is not None else Redactor.default()
    _active_redactor = active_redactor
    logging.setLoggerClass(MaskingLogger)
    registered_logger: logging.Logger | logging.PlaceHolder
    for registered_logger in logging.root.manager.loggerDict.values():
        if isinstance(registered_logger, logging.Logger) and not isinstance(
            registered_logger, MaskingLogger
        ):
            logger_class: type[logging.Logger] = type(registered_logger)
            registered_logger.__class__ = _logger_class_with_masking(logger_class)
    existing_factory: RecordFactory = cast(RecordFactory, logging.getLogRecordFactory())
    original_factory: RecordFactory = (
        existing_factory.original
        if isinstance(existing_factory, MaskingRecordFactory)
        else existing_factory
    )
    logging.setLogRecordFactory(MaskingRecordFactory(original_factory, active_redactor))
    root: logging.Logger = logging.getLogger()
    root.setLevel(level.upper())
    handler: logging.Handler | None = next(
        (candidate for candidate in root.handlers if isinstance(candidate, logging.StreamHandler)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
    root.handlers[:] = [handler]
    handler.setFormatter(MaskingFormatter(active_redactor))
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "sqlalchemy"):
        logger: logging.Logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
    for logger_name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def install_excepthook(redactor: Redactor) -> None:
    """Route uncaught exceptions through the root logger's masking formatter."""

    def handle_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        tb: TracebackType | None,
    ) -> None:
        logger: logging.Logger = logging.getLogger()
        rendered: str = "".join(traceback_module.format_exception(exception_type, exception, tb))
        safe_rendered: str = redactor.redact(rendered).text
        logger.error("Uncaught exception: %s", safe_rendered)

    sys.excepthook = handle_exception
