"""ERROR halt + link-lost tests for ArduinoMotor (IO-ARD-07).

Covers:
* Every firmware ``ERROR:N`` (codes 1..11) latches
  :class:`FirmwareErrorReceived`, sets state=FAULTED, pauses dispatch.
  The next ``send_motor_angle`` raises; ``send_emergency_stop`` STILL
  works (safety bypass).
* ``ERROR:11`` logs at error level with ``code_name="HEARTBEAT_TIMEOUT"``.
* USB-disconnect (RX-thread serial exception) latches
  :class:`LinkLostError`, NOT :class:`FirmwareErrorReceived` -- BLOCKER 2.

WARN 9: every test bounded < 1 s; cross-thread sync uses
``wait_for_state`` polling helpers, never ``asyncio.sleep(0.05)``.
"""
from __future__ import annotations

import pytest
import serial
import structlog

from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_motor import (
    FirmwareErrorReceived,
    LinkLostError,
    _MotorState,
)
from pastor_tracker.io.arduino_protocol import ErrorCode
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import _started_motor, wait_for_state


@pytest.mark.parametrize(
    "code",
    [c.value for c in ErrorCode if c.value != 0],
)
async def test_error_line_halts_dispatch(
    code: int,
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-07: ERROR:N halts dispatch and raises FirmwareErrorReceived."""
    motor, fake = await _started_motor(valid_config_dict)
    try:
        fake.feed_rx(f"ERROR:{code} - test message {code}".encode("ascii"))
        await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        assert motor.state == _MotorState.FAULTED
        assert motor.is_dispatch_paused is True
        with pytest.raises(FirmwareErrorReceived) as exc_info:
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
        assert int(exc_info.value.code) == code
        # E-stop bypass: must still work.
        pre_count = sum(1 for w in fake.captured_writes if w == b"E\n")
        await motor.send_emergency_stop()
        post_count = sum(1 for w in fake.captured_writes if w == b"E\n")
        assert post_count == pre_count + 1
    finally:
        await motor.close()


async def test_error_11_logs_at_error_level(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-07: ERROR:11 emits structured 'error_received' record at error level."""
    with structlog.testing.capture_logs() as caplog:
        motor, fake = await _started_motor(valid_config_dict)
        try:
            fake.feed_rx(b"ERROR:11 - PC heartbeat lost")
            await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        finally:
            await motor.close()
    error_records = [r for r in caplog if r.get("event") == "error_received"]
    assert len(error_records) >= 1, (
        f"expected >=1 'error_received' log, got {caplog!r}"
    )
    record = error_records[0]
    assert record["code"] == 11
    assert record["code_name"] == "HEARTBEAT_TIMEOUT"
    assert record.get("log_level") == "error"


class _DroppingTransport(FakeSerialTransport):
    """FakeSerialTransport that raises serial.SerialException after the boot preamble.

    The RX thread's ``except Exception`` branch catches it and bridges into
    ``_on_link_lost`` on the loop thread, latching :class:`LinkLostError`.
    """

    def __init__(self) -> None:
        super().__init__()
        self._dropped = False

    def read_line(self, timeout: float) -> bytes | None:
        if self._rx_lines:
            return super().read_line(timeout)
        if self._dropped:
            raise serial.SerialException("usb unplug")
        # First time the queue is empty after the preamble drained, simulate
        # the USB cable being unplugged. The RX thread's _RX_THREAD_READ_TIMEOUT_SEC
        # tick = 0.1 s, so we transition deterministically.
        self._dropped = True
        raise serial.SerialException("usb unplug")


async def test_link_lost_raises_link_lost_error(
    valid_config_dict: dict[str, object],
) -> None:
    """BLOCKER 2: USB disconnect latches LinkLostError, NOT FirmwareErrorReceived."""
    transport = _DroppingTransport()
    motor, _fake = await _started_motor(valid_config_dict, transport=transport)
    try:
        await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        assert motor.state == _MotorState.FAULTED
        assert motor._latched_error is not None
        assert isinstance(motor._latched_error, LinkLostError)
        assert not isinstance(motor._latched_error, FirmwareErrorReceived)
        with pytest.raises(LinkLostError):
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
        # Safety bypass: send_emergency_stop still writes E\n.
        pre_count = sum(1 for w in transport.captured_writes if w == b"E\n")
        await motor.send_emergency_stop()
        post_count = sum(1 for w in transport.captured_writes if w == b"E\n")
        assert post_count == pre_count + 1
    finally:
        await motor.close()
