"""Golden-trace replay test for ArduinoMotor (TEST-04)."""
from __future__ import annotations

import asyncio

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import ArduinoMotor, _MotorState
from pastor_tracker.io.arduino_protocol import (
    Error,
    ErrorCode,
    Feedback,
    ProtocolEvent,
)
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import ARDUINO_TRACE_GOLDEN


async def test_golden_trace_replay(
    valid_config_dict: dict[str, object],
) -> None:
    """TEST-04: full golden trace handshake -> 3 FBs -> ERROR:11 halt."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_heartbeat_interval_ms": 50,
    }
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_GOLDEN:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        events: list[ProtocolEvent] = []

        async def _drain() -> None:
            async for ev in motor.events():
                events.append(ev)
                if isinstance(ev, Error):
                    return

        await asyncio.wait_for(_drain(), timeout=2.0)
        assert any(isinstance(e, Feedback) for e in events), (
            "no Feedback events captured"
        )
        last = events[-1]
        assert isinstance(last, Error)
        assert last.code == ErrorCode.HEARTBEAT_TIMEOUT
        assert any(w == b"Q\n" for w in fake.captured_writes), (
            "heartbeat never fired"
        )
        assert motor.state == _MotorState.FAULTED
    finally:
        await motor.close()
