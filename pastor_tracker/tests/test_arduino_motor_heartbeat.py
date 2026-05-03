"""Heartbeat cadence test for ArduinoMotor (IO-ARD-05)."""
from __future__ import annotations

import asyncio

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import ARDUINO_TRACE_BOOT_ONLY


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
