"""Tests for structlog JSON configuration."""
from __future__ import annotations

import json

import pytest
import structlog

from pastor_tracker.logging_config import configure_logging


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
