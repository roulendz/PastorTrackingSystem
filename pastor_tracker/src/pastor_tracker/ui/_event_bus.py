"""Tap-and-pass structlog processor for the Phase 7 status panel (UI-04).

Inserted into the structlog chain by :func:`pastor_tracker.logging_config.configure_logging`
BEFORE ``JSONRenderer`` per RESEARCH §5 (the corrective finding vs CONTEXT.md
D-16 wording "AFTER JSON renderer", which is wrong because ``JSONRenderer`` is
terminal -- it returns a ``str``, not a ``dict``, and any processor placed
after it would receive a string and crash on ``event_dict.get("level")``).

The processor:
    1. Inspects ``event_dict.get("level")`` (set earlier in the chain by
       :func:`structlog.processors.add_log_level`).
    2. Appends ``(level, event, timestamp)`` triples to a bounded
       :class:`collections.deque` when ``level`` is ``warning``, ``error`` or
       ``critical``. INFO and DEBUG are NOT captured (operator-relevant only,
       per D-15 "Last error" field semantics).
    3. Returns ``event_dict`` UNCHANGED -- chain continues to
       ``JSONRenderer``. JSON output is identical to the Phase 1 contract.

The deque is read by :class:`pastor_tracker.ui._status_panel.StatusPanel`
to display the most recent operator-relevant event at 10 Hz (D-15).

structlog's processor signature mandates ``Any`` for the ``logger`` and
``event_dict`` parameters (see
https://www.structlog.org/en/stable/processors.html). The per-file
``disable-error-code`` directive below is the documented escape hatch for
mypy --strict's ``disallow_any_explicit`` rule; the alternative would be a
project-wide weakening, which is worse (auditability lives in one file
here).
"""
# mypy: disable-error-code="explicit-any"
from __future__ import annotations

import collections
from collections.abc import MutableMapping
from typing import Any, Final

EventTriple = tuple[str, str, str]
"""``(level, event, timestamp_iso)`` -- the three fields the status panel renders."""

EventBuffer = collections.deque[EventTriple]
"""Bounded FIFO of recent WARN/ERROR/CRITICAL events (newest at index 0)."""

_CAPTURED_LEVELS: Final[frozenset[str]] = frozenset({"warning", "error", "critical"})
"""structlog ``add_log_level`` lowercases method names; we match that."""

EVENT_BUS_MAXLEN: Final[int] = 64
"""Bound on the deque so a runaway warning loop cannot exhaust RAM."""


def make_event_bus() -> EventBuffer:
    """Construct a fresh bounded deque sized for the status panel.

    Convenience for callers that want the canonical maxlen without reaching
    for the module constant.
    """
    return collections.deque(maxlen=EVENT_BUS_MAXLEN)


class EventBusProcessor:
    """structlog processor: tap WARN/ERROR/CRITICAL events into a bounded deque.

    Position in chain: SECOND-TO-LAST, just before
    :class:`structlog.processors.JSONRenderer` (RESEARCH §5 corrective
    finding). Returning ``event_dict`` identity-unchanged keeps the chain
    flowing to the terminal renderer; the JSON sink output is byte-identical
    to the Phase 1 contract.

    Args:
        buffer: The deque to ``appendleft`` captured triples into. Caller
            owns its construction; the status panel reads from the SAME
            deque instance.
    """

    __slots__ = ("_buf",)

    def __init__(self, buffer: EventBuffer) -> None:
        self._buf = buffer

    def __call__(
        self,
        logger: Any,  # noqa: ANN401 -- structlog processor signature mandates Any
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        """Tap-and-pass. Mutates the deque; returns ``event_dict`` unchanged.

        ``add_log_level`` runs earlier in the chain, so ``level`` is already
        present in ``event_dict``. ``TimeStamper`` (also earlier) provides
        ``timestamp``. Both are stringified defensively because a
        misconfigured chain (level missing) would otherwise let a non-str
        slip into the panel's ``set_value`` call.
        """
        del logger, method_name  # structlog signature requires them; we do not use.
        level = event_dict.get("level")
        if level in _CAPTURED_LEVELS:
            self._buf.appendleft((
                str(level),
                str(event_dict.get("event", "")),
                str(event_dict.get("timestamp", "")),
            ))
        return event_dict


__all__ = [
    "EVENT_BUS_MAXLEN",
    "EventBuffer",
    "EventBusProcessor",
    "EventTriple",
    "make_event_bus",
]
