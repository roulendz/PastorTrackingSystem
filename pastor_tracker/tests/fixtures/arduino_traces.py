"""Canned byte traces and async helpers for Arduino I/O integration tests.

Drive the traces via :meth:`FakeSerialTransport.feed_rx` one line at a time
(without trailing ``\\n`` -- parser strips terminators per the contract).

Import path is ``tests.fixtures.arduino_traces`` -- pytest ``rootdir`` is
``pastor_tracker/`` (``testpaths=["tests"]``, packages=``["src/pastor_tracker"]``);
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package. Importing as ``from pastor_tracker.tests.fixtures.arduino_traces``
raises :class:`ModuleNotFoundError`.
"""
from __future__ import annotations

import asyncio
from typing import Final

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import ArduinoMotor, _MotorState
from pastor_tracker.io.arduino_transport import FakeSerialTransport

ARDUINO_TRACE_BOOT_ONLY: Final[list[bytes]] = [
    b"SETTINGS: defaults (no valid EEPROM)",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
]

ARDUINO_TRACE_GOLDEN: Final[list[bytes]] = [
    b"SETTINGS: defaults (no valid EEPROM)",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    b"FB:0.00,0.00,0.00,0,1000,0,0",
    b"FB:0.50,1.00,500.00,1,21000,1,1",
    b"FB:1.00,1.00,0.00,0,41000,2,0",
    b"ERROR:11 - PC heartbeat lost",
]

ARDUINO_TRACE_WATCHDOG_RESET: Final[list[bytes]] = [
    b"SETTINGS: loaded from EEPROM",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    b"FB:0.00,0.00,0.00,0,1000,0,0",
    # mid-session watchdog reset:
    b"SETTINGS: loaded from EEPROM",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    # recovery acks (structured):
    b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000",
    b"SETTINGS: saved to EEPROM",
    b"LIMITS:-90.000,90.000",
    b"SETTINGS: saved to EEPROM",
]


async def _started_motor(
    valid_config_dict: dict[str, object],
    transport: FakeSerialTransport | None = None,
) -> tuple[ArduinoMotor, FakeSerialTransport]:
    """Feed the boot preamble, start the motor, return ``(motor, fake)``.

    Used by handshake-success tests AND every downstream test that needs a
    motor in :attr:`_MotorState.RUNNING`. Caller is responsible for
    ``await motor.close()``.
    """
    fake = transport if transport is not None else FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    return motor, fake


async def wait_for_state(
    motor: ArduinoMotor,
    target_state: _MotorState,
    timeout: float = 1.0,
) -> None:
    """Poll ``motor.state`` at 10 ms cadence until ``target_state`` or ``timeout``.

    Replaces brittle ``asyncio.sleep(0.05)`` cross-thread bridge waits in
    recovery/error tests. 10 ms is faster than the RX thread's
    ``_RX_THREAD_READ_TIMEOUT_SEC=0.1`` s loop tick AND faster than typical
    scheduler jitter, so polling overhead is bounded by ``timeout``.

    Raises :class:`asyncio.TimeoutError` if state not reached within
    ``timeout``. Tests using this helper MUST complete in < 1 s wall clock.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if motor.state == target_state:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"motor.state did not reach {target_state} within {timeout}s "
        f"(current: {motor.state})"
    )


async def wait_for_pause_cleared(
    motor: ArduinoMotor, timeout: float = 1.0
) -> None:
    """Poll ``motor.is_dispatch_paused`` at 10 ms cadence until ``False``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if motor.is_dispatch_paused is False:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"motor.is_dispatch_paused did not clear within {timeout}s"
    )
