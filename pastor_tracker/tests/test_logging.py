"""Tests for structlog JSON configuration.

NOTE on test isolation: ``configure_logging`` is documented one-shot
(cache_logger_on_first_use=True freezes filtering on cached loggers).
Without explicit teardown, this test leaks an INFO-level filter into
subsequent tests in the same process, suppressing DEBUG events that
downstream log-event tests rely on (e.g. ``pan_controller_seeded``,
``framer_seeded``). The autouse fixture below resets structlog defaults
after this module so the rest of the suite sees the un-configured
default behaviour again.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog

from pastor_tracker.logging_config import configure_logging


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
