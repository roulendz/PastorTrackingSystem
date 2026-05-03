"""structlog JSON logging — call ``configure_logging()`` once at module entry.

Phase 1 contract: structured JSON to stdout, ISO-8601 UTC timestamps,
exception traceback rendered into the JSON payload via ``format_exc_info``.
"""
from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging for JSON output.

    CALL ONCE at process startup (e.g. from ``__main__``). NOT idempotent:
    ``logging.basicConfig`` is a no-op once the root logger has handlers, and
    ``cache_logger_on_first_use=True`` (below) freezes the filtering wrapper
    on every previously-bound ``structlog.get_logger`` instance. Phase 7's
    runtime level slider will need a proper reconfigure path; until then,
    treat this function as one-shot and crash loudly on second invocation
    upstream rather than relying on silent re-application here.
    """
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
