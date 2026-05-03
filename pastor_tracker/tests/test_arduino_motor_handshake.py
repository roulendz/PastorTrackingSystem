"""Handshake tests for ArduinoMotor (IO-ARD-02)."""
from __future__ import annotations

import pytest

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import (
    ArduinoMotor,
    HandshakeTimeoutError,
    ProtocolVersionMismatchError,
    _MotorState,
)
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import ARDUINO_TRACE_BOOT_ONLY


async def test_handshake_succeeds_with_full_preamble(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02 + Pitfall 1: 3-line preamble succeeds."""
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    try:
        assert motor.state == _MotorState.RUNNING
        assert motor.is_dispatch_paused is False
    finally:
        await motor.close()


async def test_handshake_rejects_version_mismatch(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02 + CFG-04: READY:v3 raises ProtocolVersionMismatchError."""
    fake = FakeSerialTransport()
    fake.feed_rx(b"READY:v3")
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    with pytest.raises(ProtocolVersionMismatchError, match="expected READY:v2"):
        await motor.start()


async def test_handshake_times_out_when_no_ready(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02: silent serial -> HandshakeTimeoutError within ready window."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 0.05,
    }
    fake = FakeSerialTransport()
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    with pytest.raises(HandshakeTimeoutError):
        await motor.start()
