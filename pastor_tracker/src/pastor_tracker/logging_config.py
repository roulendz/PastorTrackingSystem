"""structlog JSON logging -- call ``configure_logging()`` once at module entry.

Phase 1 contract: structured JSON to stdout, ISO-8601 UTC timestamps,
exception traceback rendered into the JSON payload via ``format_exc_info``.

Phase 7 extension (D-16, RESEARCH §5): an optional ``event_bus_buffer``
parameter inserts an :class:`pastor_tracker.ui._event_bus.EventBusProcessor`
into the chain BEFORE ``JSONRenderer``. The processor taps WARN/ERROR/
CRITICAL events into the supplied bounded deque so the Phase 7 status
panel can render the most recent operator-relevant event at 10 Hz (D-15).
JSON sink output is byte-identical with or without the tap.

Insertion-point correction: CONTEXT.md D-16 wording ("AFTER JSON renderer")
is wrong because ``JSONRenderer`` is the TERMINAL processor and returns a
``str``, not a ``dict``. A processor placed after it would crash on
``event_dict.get("level")`` (AttributeError on str). The corrected position
is second-to-last, immediately before the renderer.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pastor_tracker.ui._event_bus import EventBuffer


def configure_logging(
    level: str = "INFO",
    event_bus_buffer: EventBuffer | None = None,
) -> None:
    """Configure structlog + stdlib logging for JSON output.

    CALL ONCE at process startup (e.g. from ``__main__``). NOT idempotent:
    ``logging.basicConfig`` is a no-op once the root logger has handlers, and
    ``cache_logger_on_first_use=True`` (below) freezes the filtering wrapper
    on every previously-bound ``structlog.get_logger`` instance. Phase 7's
    runtime level slider will need a proper reconfigure path; until then,
    treat this function as one-shot and crash loudly on second invocation
    upstream rather than relying on silent re-application here.

    Args:
        level: stdlib log-level name (``"INFO"``, ``"DEBUG"``, ...). Case
            preserved by callers; this function uppercases internally.
        event_bus_buffer: optional bounded deque. When non-None, an
            :class:`EventBusProcessor` is inserted just BEFORE
            :class:`structlog.processors.JSONRenderer` (RESEARCH §5);
            WARN/ERROR/CRITICAL events are captured into the deque for the
            Phase 7 status panel. When None (Phase 1 default), the chain
            matches Phase 1 exactly -- no UI imports are pulled.
    """
    logging.basicConfig(level=level.upper(), format="%(message)s")
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if event_bus_buffer is not None:
        # Lazy import: keeps Phase 1 / --headless callers free of any
        # ``pastor_tracker.ui.*`` import (DearPyGui is a heavy dependency).
        from pastor_tracker.ui._event_bus import EventBusProcessor

        processors.append(EventBusProcessor(event_bus_buffer))
    processors.append(structlog.processors.JSONRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
