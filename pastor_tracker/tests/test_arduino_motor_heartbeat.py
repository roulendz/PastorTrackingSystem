"""Heartbeat cadence test for ArduinoMotor (IO-ARD-05)."""
from __future__ import annotations

import asyncio

import pytest

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_motor import (
    ArduinoMotor,
    LinkLostError,
    _MotorState,
)
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import (
    ARDUINO_TRACE_BOOT_ONLY,
    wait_for_state,
)


async def test_heartbeat_emits_q_at_interval(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-05: heartbeat task emits b'Q\\n' every arduino_heartbeat_interval_ms."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_heartbeat_interval_ms": 50,
    }
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    # 5 intervals at 50 ms = 250 ms; 10 ms cushion for scheduler jitter.
    await asyncio.sleep(0.260)
    await motor.close()
    q_writes = [w for w in fake.captured_writes if w == b"Q\n"]
    # Tolerate +/-1 for asyncio scheduler jitter (RESEARCH line 603).
    assert 4 <= len(q_writes) <= 6, (
        f"expected 4-6 heartbeat Q\\n, got {len(q_writes)}"
    )


async def test_heartbeat_task_unexpected_exception_latches(
    valid_config_dict: dict[str, object],
) -> None:
    """W-05: a non-ArduinoError, non-CancelledError raised inside the
    heartbeat task is surfaced via the latched-error gate (LinkLostError)
    instead of dying silently as a "Task exception was never retrieved"
    GC warning -- which under filterwarnings=["error"] is a flake.
    """
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_heartbeat_interval_ms": 50,
    }
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # Monkeypatch send_query to raise a programmer-error class. The
        # heartbeat task currently only catches ArduinoError / CancelledError
        # so RuntimeError escapes -- the done-callback installed by start()
        # must catch it and latch a LinkLostError.
        async def _boom() -> None:
            raise RuntimeError("simulated programmer bug")

        motor.send_query = _boom  # type: ignore[method-assign]
        # Wait for the next heartbeat tick -> task crashes -> done-callback fires.
        await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        assert isinstance(motor._latched_error, LinkLostError)
        assert "RuntimeError" in str(motor._latched_error)
        # Next public send_* must surface the latched typed error.
        with pytest.raises(LinkLostError):
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
    finally:
        await motor.close()


async def test_heartbeat_cancels_on_close(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-05: close() cancels the heartbeat task cleanly (no leaks)."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_heartbeat_interval_ms": 50,
    }
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    await asyncio.sleep(0.060)  # let one heartbeat fire
    await motor.close()
    count_at_close = len([w for w in fake.captured_writes if w == b"Q\n"])
    await asyncio.sleep(0.060)
    count_after = len([w for w in fake.captured_writes if w == b"Q\n"])
    assert count_after == count_at_close, "heartbeat continued after close()"
