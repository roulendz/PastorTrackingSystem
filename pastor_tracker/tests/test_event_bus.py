"""Unit tests for :mod:`pastor_tracker.ui._event_bus` (Plan 07-04 Task 1).

Coverage:
    * WARN / ERROR / CRITICAL events are captured (3 tests).
    * INFO / DEBUG events are ignored (2 tests).
    * The processor returns ``event_dict`` identity-unchanged (pass-through).
    * The deque is bounded at ``EVENT_BUS_MAXLEN`` (overflow drops oldest).
    * Missing ``event`` / ``timestamp`` keys degrade to empty strings.

These tests construct the deque directly (rather than via the
``make_event_bus`` factory) so the bound is asserted explicitly in
:func:`test_deque_bounded_at_64`.
"""
from __future__ import annotations

import collections

import pytest

from pastor_tracker.ui._event_bus import (
    EVENT_BUS_MAXLEN,
    EventBuffer,
    EventBusProcessor,
    make_event_bus,
)


def _fresh_buf() -> EventBuffer:
    """Fresh deque sized at the canonical bound -- mirrors make_event_bus."""
    return collections.deque(maxlen=EVENT_BUS_MAXLEN)


def test_warn_captured() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "warning",
        {"level": "warning", "event": "test", "timestamp": "2026-05-08T12:00:00Z"},
    )
    assert buf[0] == ("warning", "test", "2026-05-08T12:00:00Z")


def test_error_captured() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "error",
        {"level": "error", "event": "boom", "timestamp": "2026-05-08T12:00:01Z"},
    )
    assert buf[0] == ("error", "boom", "2026-05-08T12:00:01Z")


def test_critical_captured() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "critical",
        {"level": "critical", "event": "panic", "timestamp": "2026-05-08T12:00:02Z"},
    )
    assert buf[0] == ("critical", "panic", "2026-05-08T12:00:02Z")


def test_info_not_captured() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "info",
        {"level": "info", "event": "boot", "timestamp": "2026-05-08T12:00:03Z"},
    )
    assert len(buf) == 0


def test_debug_not_captured() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "debug",
        {"level": "debug", "event": "tick", "timestamp": "2026-05-08T12:00:04Z"},
    )
    assert len(buf) == 0


def test_pass_through_returns_dict_unchanged() -> None:
    """Processor is tap-and-pass: chain receives the SAME dict it was given."""
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    event_dict = {
        "level": "warning",
        "event": "ident",
        "timestamp": "2026-05-08T12:00:05Z",
    }
    returned = processor(None, "warning", event_dict)
    assert returned is event_dict  # identity, not equality
    assert returned == {
        "level": "warning",
        "event": "ident",
        "timestamp": "2026-05-08T12:00:05Z",
    }


def test_deque_bounded_at_64() -> None:
    """Bound is 64. 100 appendlefts -> oldest 36 evicted; newest at index 0."""
    buf: EventBuffer = collections.deque(maxlen=EVENT_BUS_MAXLEN)
    processor = EventBusProcessor(buf)
    for i in range(100):
        processor(
            None,
            "warning",
            {
                "level": "warning",
                "event": f"evt_{i}",
                "timestamp": f"2026-05-08T12:00:{i:02d}Z",
            },
        )
    assert len(buf) == 64
    # Newest at index 0 (appendleft semantics): i=99 was the last write.
    assert buf[0][1] == "evt_99"
    # Oldest surviving entry is i=36 (100 - 64 = 36).
    assert buf[-1][1] == "evt_36"


def test_missing_event_key_uses_empty_string() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(
        None,
        "warning",
        {"level": "warning", "timestamp": "2026-05-08T12:00:06Z"},
    )
    assert buf[0] == ("warning", "", "2026-05-08T12:00:06Z")


def test_missing_timestamp_key_uses_empty_string() -> None:
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    processor(None, "warning", {"level": "warning", "event": "no_ts"})
    assert buf[0] == ("warning", "no_ts", "")


def test_make_event_bus_returns_bounded_deque() -> None:
    """The convenience factory returns a deque with the canonical bound."""
    buf = make_event_bus()
    assert isinstance(buf, collections.deque)
    assert buf.maxlen == EVENT_BUS_MAXLEN


@pytest.mark.parametrize("missing_level", [None, "notice", "TRACE", ""])
def test_unknown_level_not_captured(missing_level: str | None) -> None:
    """Anything outside {warning, error, critical} is ignored (defensive)."""
    buf = _fresh_buf()
    processor = EventBusProcessor(buf)
    payload: dict[str, object] = {"event": "x", "timestamp": "t"}
    if missing_level is not None:
        payload["level"] = missing_level
    processor(None, "info", payload)  # type: ignore[arg-type]
    assert len(buf) == 0
