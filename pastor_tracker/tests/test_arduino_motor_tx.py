"""TX byte-format tests for ArduinoMotor (IO-ARD-03).

Asserts every send_* method produces the exact ASCII bytes the firmware
parser expects (per ``arduino/stepper_controller/src/main.cpp``), the
``asyncio.Lock`` serialises concurrent writers atomically, and the
:data:`FIRMWARE_INPUT_BUFFER_USABLE` pre-send guard rejects oversized lines.
"""
from __future__ import annotations

import asyncio

import pytest

from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import _started_motor


async def _fresh_motor(
    valid_config_dict: dict[str, object],
) -> tuple[ArduinoMotor, FakeSerialTransport]:
    """Boot motor and clear preamble-era writes so this test sees only its own TX."""
    motor, fake = await _started_motor(valid_config_dict)
    fake.captured_writes.clear()
    return motor, fake


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (12.345, b"M:12.345\n"),
        (-1.5, b"M:-1.500\n"),
        (0.0, b"M:0.000\n"),
        (90.0, b"M:90.000\n"),
    ],
)
async def test_send_motor_angle_byte_format(
    angle: float,
    expected: bytes,
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: M:<deg> emitted with .3f precision, ASCII, trailing \\n."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await motor.send_motor_angle(
            MotorCommand(target_angle_deg=angle, timestamp_ns=1)
        )
        assert expected in fake.captured_writes
    finally:
        await motor.close()


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("send_query", b"Q\n"),
        ("send_emergency_stop", b"E\n"),
        ("send_reset", b"R\n"),
        ("send_home", b"H\n"),
    ],
)
async def test_send_simple_commands_byte_format(
    method: str,
    expected: bytes,
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: zero-argument commands emit exactly one ASCII tag + \\n."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await getattr(motor, method)()
        assert expected in fake.captured_writes
    finally:
        await motor.close()


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, b"X:1\n"), (False, b"X:0\n")],
)
async def test_send_driver_byte_format(
    enabled: bool,
    expected: bytes,
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: send_driver(True) -> X:1, send_driver(False) -> X:0."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await motor.send_driver(enabled)
        assert expected in fake.captured_writes
    finally:
        await motor.close()


@pytest.mark.parametrize(
    ("steps", "expected"),
    [(1000, b"D:1000\n"), (-500, b"D:-500\n"), (0, b"D:0\n")],
)
async def test_send_diagnostic_steps_byte_format(
    steps: int,
    expected: bytes,
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: D:<steps> emits signed integer literal + \\n."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await motor.send_diagnostic_steps(steps)
        assert expected in fake.captured_writes
    finally:
        await motor.close()


async def test_send_settings_byte_format(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: S:<spd>,<acc>,<p>,<i>,<d> uses .3f precision, fits 47 bytes."""
    expected = b"S:25000.000,12500.000,0.000,0.000,0.000\n"
    # Pre-flight: total length (incl. trailing \n that _send_raw appends) <= 48.
    assert len(expected) - 1 <= 47  # the payload without \n must fit firmware INPUT_BUFFER_USABLE
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await motor.send_settings(25000.0, 12500.0, 0.0, 0.0, 0.0)
        assert expected in fake.captured_writes
    finally:
        await motor.close()


async def test_send_limits_byte_format(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-03: L:<min>,<max> uses .3f precision."""
    expected = b"L:-90.000,90.000\n"
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await motor.send_limits(-90.0, 90.0)
        assert expected in fake.captured_writes
    finally:
        await motor.close()


async def test_send_buffer_overflow_raises_pre_send(
    valid_config_dict: dict[str, object],
) -> None:
    """Pitfall 5: TX line longer than FIRMWARE_INPUT_BUFFER_USABLE raises ValueError."""
    motor, _fake = await _fresh_motor(valid_config_dict)
    try:
        with pytest.raises(ValueError, match="TX line"):
            await motor._send_raw(b"X" * 60)
    finally:
        await motor.close()


async def test_dispatch_paused_silences_motor_angle(
    valid_config_dict: dict[str, object],
) -> None:
    """During recovery (_dispatch_paused=True), send_motor_angle is a no-op."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        motor._dispatch_paused = True
        m_count_before = sum(1 for w in fake.captured_writes if w.startswith(b"M:"))
        await motor.send_motor_angle(
            MotorCommand(target_angle_deg=42.0, timestamp_ns=1)
        )
        m_count_after = sum(1 for w in fake.captured_writes if w.startswith(b"M:"))
        assert m_count_after == m_count_before
    finally:
        motor._dispatch_paused = False
        await motor.close()


async def test_emergency_stop_bypasses_pause(
    valid_config_dict: dict[str, object],
) -> None:
    """Safety: send_emergency_stop ALWAYS writes E\\n even when paused."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        motor._dispatch_paused = True
        await motor.send_emergency_stop()
        assert b"E\n" in fake.captured_writes
    finally:
        motor._dispatch_paused = False
        await motor.close()


async def test_concurrent_motor_angle_serialized(
    valid_config_dict: dict[str, object],
) -> None:
    """asyncio.Lock guarantees per-call atomic writes under contention."""
    motor, fake = await _fresh_motor(valid_config_dict)
    try:
        await asyncio.gather(
            *[
                motor.send_motor_angle(
                    MotorCommand(target_angle_deg=float(i), timestamp_ns=i + 1)
                )
                for i in range(5)
            ]
        )
        m_writes = [w for w in fake.captured_writes if w.startswith(b"M:")]
        assert len(m_writes) == 5
        expected_set = {f"M:{float(i):.3f}\n".encode("ascii") for i in range(5)}
        assert set(m_writes) == expected_set
    finally:
        await motor.close()


