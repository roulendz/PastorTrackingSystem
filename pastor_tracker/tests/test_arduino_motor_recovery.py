"""Watchdog-recovery tests for ArduinoMotor (IO-ARD-06).

Covers:
* Mid-session ``Ready`` event triggers re-issue of ``S:`` + ``L:``.
* Recovery ack-watcher waits for STRUCTURED ``Settings`` (5-tuple), NOT
  textual ``SettingsInfo`` (RESEARCH lines 504-506).
* Trailing ``SettingsInfo("saved to EEPROM")`` is drained from the
  public events queue (WARN 6).
* Ack timeout latches :class:`WatchdogResetError` + state=FAULTED;
  the next ``send_*`` raises -- ``_recover`` itself does NOT re-raise
  (BLOCKER 3 -- deterministic surface).
* ``send_motor_angle`` is paused during the recovery window.
* Heartbeat continues throughout recovery (firmware watchdog stays happy).

WARN 9: every test bounded < 1 s; cross-thread synchronisation uses
``wait_for_state`` / ``wait_for_pause_cleared`` polling helpers, never
``asyncio.sleep(0.05)``.
"""
from __future__ import annotations

import asyncio

import pytest

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_motor import (
    ArduinoMotor,
    WatchdogResetError,
    _MotorState,
)
from pastor_tracker.io.arduino_protocol import ProtocolEvent, SettingsInfo
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from tests.fixtures.arduino_traces import (
    _started_motor,
    wait_for_pause_cleared,
    wait_for_state,
)


async def _drain_public_events(
    motor: ArduinoMotor, max_wait: float = 0.05
) -> list[ProtocolEvent]:
    """Pull every event currently in the public RX queue (best-effort)."""
    drained: list[ProtocolEvent] = []
    deadline = asyncio.get_running_loop().time() + max_wait
    while asyncio.get_running_loop().time() < deadline:
        try:
            ev = await asyncio.wait_for(
                motor._rx_queue.get(), timeout=0.01
            )
        except TimeoutError:
            break
        drained.append(ev)
    return drained


async def test_recovery_reissues_settings_and_limits(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-06: mid-session Ready -> re-issue S:+L:; trailing SettingsInfo drained."""
    motor, fake = await _started_motor(valid_config_dict)
    try:
        # Let the RX thread quiesce on the boot preamble before we measure TX.
        await asyncio.sleep(0.02)
        fake.captured_writes.clear()
        # Mid-session preamble (3 lines) -> recovery state machine fires.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        # Wait for the recovery state machine to enter RECOVERING (this is
        # the proof that mid-session Ready was detected). Without this, a
        # quick wait_for_pause_cleared could return immediately because
        # the recovery hasn't even started yet.
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        # Recovery sends S: then waits for structured Settings ack; feed it.
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        # Recovery sends L: then waits for Limits ack.
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        # Trailing textual SettingsInfo -- _recover drains exactly one (WARN 6).
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
        assert motor.state == _MotorState.RUNNING
        assert b"S:25000.000,12500.000,0.000,0.000,0.000\n" in fake.captured_writes
        assert b"L:-90.000,90.000\n" in fake.captured_writes
        # Public events queue should NOT contain a SettingsInfo from recovery
        # noise -- the trailing "SETTINGS: saved to EEPROM" was drained.
        public_events = await _drain_public_events(motor)
        assert not any(
            isinstance(ev, SettingsInfo) for ev in public_events
        ), "recovery SettingsInfo leaked into public events queue (WARN 6)"
    finally:
        await motor.close()


async def test_recovery_settings_ack_timeout_raises(
    valid_config_dict: dict[str, object],
) -> None:
    """BLOCKER 3: ack timeout latches WatchdogResetError + FAULTED; next send_* raises."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 0.1,
    }
    fake = FakeSerialTransport()
    # Boot preamble.
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # Mid-session preamble triggers recovery; feed NO structured Settings ack.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        assert motor.state == _MotorState.FAULTED
        assert motor._latched_error is not None
        assert isinstance(motor._latched_error, WatchdogResetError)
        with pytest.raises(WatchdogResetError):
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
    finally:
        await motor.close()


async def test_recovery_skips_textual_settings_info(
    valid_config_dict: dict[str, object],
) -> None:
    """RESEARCH 504-506: ack-watcher discriminates structured Settings from textual SettingsInfo."""
    motor, fake = await _started_motor(valid_config_dict)
    try:
        await asyncio.sleep(0.02)
        fake.captured_writes.clear()
        # Mid-session preamble.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        # Textual SettingsInfo arrives BEFORE the structured ack -- the watcher
        # must NOT treat this as the Settings ack (it would prematurely clear
        # _dispatch_paused if it did).
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        # Now the structured ack proper.
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        # Trailing drain target.
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
        assert motor.state == _MotorState.RUNNING
        assert b"S:25000.000,12500.000,0.000,0.000,0.000\n" in fake.captured_writes
        assert b"L:-90.000,90.000\n" in fake.captured_writes
    finally:
        await motor.close()


async def test_motor_angle_paused_during_recovery(
    valid_config_dict: dict[str, object],
) -> None:
    """During recovery (RECOVERING), send_motor_angle is a silent no-op."""
    motor, fake = await _started_motor(valid_config_dict)
    try:
        await asyncio.sleep(0.02)
        fake.captured_writes.clear()
        # Trigger recovery; do NOT feed acks yet -- recovery sits paused.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        assert motor.is_dispatch_paused is True
        m_count_before = sum(1 for w in fake.captured_writes if w.startswith(b"M:"))
        await motor.send_motor_angle(
            MotorCommand(target_angle_deg=10.0, timestamp_ns=1)
        )
        m_count_during = sum(1 for w in fake.captured_writes if w.startswith(b"M:"))
        assert m_count_during == m_count_before, "M: emitted during recovery"
        # Now feed the acks + trailing drain so recovery completes.
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
        await motor.send_motor_angle(
            MotorCommand(target_angle_deg=10.0, timestamp_ns=2)
        )
        m_count_after = sum(1 for w in fake.captured_writes if w.startswith(b"M:"))
        assert m_count_after == m_count_during + 1
    finally:
        await motor.close()


async def test_heartbeat_continues_during_recovery(
    valid_config_dict: dict[str, object],
) -> None:
    """Heartbeat task is independent of recovery state."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_heartbeat_interval_ms": 50,
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        await asyncio.sleep(0.060)
        q_pre = sum(1 for w in fake.captured_writes if w == b"Q\n")
        # Trigger recovery; let it sit paused (no acks).
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        await asyncio.sleep(0.2)
        q_post = sum(1 for w in fake.captured_writes if w == b"Q\n")
        # 200 ms / 50 ms = 4 intervals; tolerate jitter to floor at >= 3.
        assert q_post >= q_pre + 3, (
            f"heartbeat stalled during recovery: q_pre={q_pre}, q_post={q_post}"
        )
        # Cleanup: feed acks so close() proceeds cleanly.
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
    finally:
        await motor.close()
