"""Tests for structlog JSON configuration.

NOTE on test isolation: ``configure_logging`` is documented one-shot
(cache_logger_on_first_use=True freezes filtering on cached loggers).
Without explicit teardown, this test leaks an INFO-level filter into
subsequent tests in the same process, suppressing DEBUG events that
downstream log-event tests rely on (e.g. ``pan_controller_seeded``,
``framer_seeded``). The autouse fixture below resets structlog defaults
after this module so the rest of the suite sees the un-configured
default behaviour again.

Plan 07-04 extension: tests 2-4 cover the new optional ``event_bus_buffer``
parameter. The corrective insertion point (BEFORE ``JSONRenderer`` per
RESEARCH §5) is asserted in :func:`test_configure_logging_buffer_chain_position`.
"""
from __future__ import annotations

import collections
import json
from collections.abc import Iterator

import pytest
import structlog

from pastor_tracker.logging_config import configure_logging
from pastor_tracker.ui._event_bus import EVENT_BUS_MAXLEN, EventBusProcessor


@pytest.fixture(autouse=True)
def _reset_structlog_after_test() -> Iterator[None]:
    """Restore structlog defaults so the cached INFO filter does not leak."""
    yield
    structlog.reset_defaults()


def test_configure_logging_emits_json_event(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    log = structlog.get_logger("pastor_tracker.test")
    log.info("phase1_boot", port="auto")
    captured = capsys.readouterr().out.strip().splitlines()
    assert captured, "structlog produced no output"
    payload = json.loads(captured[-1])
    assert payload["event"] == "phase1_boot"
    assert payload["port"] == "auto"
    assert payload["level"] == "info"
    assert "timestamp" in payload


# ---- Plan 07-04 additions: event_bus_buffer parameter ---------------------


def test_configure_logging_default_no_buffer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Calling configure_logging() with no kwarg matches Phase 1 behaviour."""
    configure_logging()
    log = structlog.get_logger("pastor_tracker.test")
    log.info("phase1_compat", note="default args")
    captured = capsys.readouterr().out.strip().splitlines()
    assert captured, "structlog produced no output"
    payload = json.loads(captured[-1])
    assert payload["event"] == "phase1_compat"
    assert payload["note"] == "default args"
    # The Phase 1 chain has 6 processors; without buffer, no EventBusProcessor.
    processors = structlog.get_config()["processors"]
    assert not any(isinstance(p, EventBusProcessor) for p in processors)


def test_configure_logging_with_buffer_inserts_event_bus(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With buffer, WARN populates deque AND JSON still emits on stdout."""
    buf: collections.deque[tuple[str, str, str]] = collections.deque(
        maxlen=EVENT_BUS_MAXLEN
    )
    configure_logging(event_bus_buffer=buf)
    log = structlog.get_logger("pastor_tracker.test")
    log.warning("ui_event_bus_test", reason="injected")
    # Side effect 1: deque captured the warning.
    assert len(buf) == 1
    level, event, _ts = buf[0]
    assert level == "warning"
    assert event == "ui_event_bus_test"
    # Side effect 2: JSON still emitted on stdout.
    captured = capsys.readouterr().out.strip().splitlines()
    assert captured, "JSON sink was bypassed -- chain is broken"
    payload = json.loads(captured[-1])
    assert payload["event"] == "ui_event_bus_test"
    assert payload["level"] == "warning"


def test_configure_logging_buffer_chain_position() -> None:
    """EventBusProcessor sits at index ``len(processors) - 2`` (just before JSONRenderer)."""
    buf: collections.deque[tuple[str, str, str]] = collections.deque(
        maxlen=EVENT_BUS_MAXLEN
    )
    configure_logging(event_bus_buffer=buf)
    processors = structlog.get_config()["processors"]
    # The terminal processor must be JSONRenderer (Phase 1 invariant).
    assert isinstance(processors[-1], structlog.processors.JSONRenderer)
    # The EventBusProcessor must sit IMMEDIATELY BEFORE the renderer
    # (RESEARCH §5 corrective insertion point).
    assert isinstance(processors[-2], EventBusProcessor)
    # And exactly once -- not duplicated by a re-call.
    bus_count = sum(1 for p in processors if isinstance(p, EventBusProcessor))
    assert bus_count == 1
