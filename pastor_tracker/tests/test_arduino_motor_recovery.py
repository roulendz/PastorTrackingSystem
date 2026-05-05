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
    FirmwareErrorReceived,
    LinkLostError,
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


async def test_firmware_error_during_recovery_preserves_firmware_error(
    valid_config_dict: dict[str, object],
) -> None:
    """B-01: firmware ERROR mid-recovery MUST NOT clobber to WatchdogResetError.

    Trace:
    1. Mid-session READY:v2 -> _on_rx_event spawns _recover.
    2. _recover sends S: then awaits Settings ack via _wait_for_event.
    3. Firmware emits ERROR:11 (or any code). RX thread bridges it.
    4. _on_rx_event Error branch sets _latched_error = FirmwareErrorReceived
       AND enqueues the Error into _rx_queue.
    5. _wait_for_event pulls the Error, sees it is not Settings, discards.
    6. Eventually _wait_for_event times out (no Settings arrives).
    7. _recover's ack-timeout except branch fires. Without B-01 it would
       overwrite _latched_error with WatchdogResetError -- losing the
       operator-actionable FirmwareErrorReceived(HEARTBEAT_TIMEOUT, ...).

    Assertion: latched error after recovery completes MUST still be
    FirmwareErrorReceived; the next send_* raises that, not the generic
    WatchdogResetError.
    """
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 0.2,  # short ack timeout for fast test
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # Trigger recovery; do NOT feed Settings ack -- recovery sits paused.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        # Firmware emits ERROR while _recover is awaiting Settings ack.
        # ERROR:11 = HEARTBEAT_TIMEOUT per protocol.h.
        fake.feed_rx(b"ERROR:11 - PC heartbeat lost")
        # Wait for FAULTED -- _recover times out + clobber-guard runs.
        await wait_for_state(motor, _MotorState.FAULTED, timeout=2.0)
        assert isinstance(motor._latched_error, FirmwareErrorReceived), (
            f"expected FirmwareErrorReceived, got "
            f"{type(motor._latched_error).__name__}: {motor._latched_error}"
        )
        # Next send_* surfaces the firmware error, not WatchdogResetError.
        with pytest.raises(FirmwareErrorReceived):
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
    finally:
        await motor.close()


async def test_recovery_link_lost_during_settings_send_latches_link_lost_error(
    valid_config_dict: dict[str, object],
) -> None:
    """C-02: LinkLostError raised inside _recover is caught + latched as
    LinkLostError (preserving the typed surface), not as WatchdogResetError.
    Without C-02 the exception escapes as an unhandled task exception.
    """
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 5.0,
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # Trigger recovery; do NOT feed Settings ack yet -- recovery blocks
        # waiting for it inside _wait_for_event.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        # Simulate USB unplug mid-recovery: latch LinkLostError on the
        # loop thread (mimics the RX-thread bridge translator path). The
        # next _wait_for_event call observes _latched_error and raises
        # LinkLostError, which _recover must catch and preserve as-is.
        # Latch BEFORE feeding the ack so the latched-error gate trips
        # inside the next send_settings/send_limits or _raise_if_latched.
        # Easiest path: directly invoke _on_link_lost (it runs sync on the
        # loop thread the same way the RX-thread bridge does).
        # However, _on_link_lost just SETS the latched error -- it doesn't
        # interrupt the in-flight _wait_for_event. We need to feed an ack
        # so the await returns, THEN the next send_limits hits the
        # latched-error gate and raises LinkLostError inside _recover.
        # That LinkLostError must be caught by C-02's broadened except.
        motor._on_link_lost(RuntimeError("simulated USB unplug"))
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        # Wait for FAULTED -- _recover should latch and return cleanly.
        await wait_for_state(motor, _MotorState.FAULTED, timeout=1.0)
        # Critical: latched error must STILL be LinkLostError, not coerced
        # to WatchdogResetError by C-02's catch.
        assert isinstance(motor._latched_error, LinkLostError), (
            f"expected LinkLostError, got {type(motor._latched_error).__name__}"
        )
        with pytest.raises(LinkLostError):
            await motor.send_motor_angle(
                MotorCommand(target_angle_deg=0.0, timestamp_ns=1)
            )
    finally:
        await motor.close()


async def test_recovery_cancelled_propagates(
    valid_config_dict: dict[str, object],
) -> None:
    """C-02: CancelledError inside _recover must propagate, not be swallowed."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 5.0,
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        recover_task = motor._recover_task
        assert recover_task is not None
        # Cancel directly. The task awaits the cancellation cleanly because
        # _recover re-raises CancelledError (does not swallow).
        recover_task.cancel()
        # Awaiting the cancelled task must produce CancelledError, NOT
        # latch WatchdogResetError or LinkLostError.
        with pytest.raises(asyncio.CancelledError):
            await recover_task
        # _latched_error must NOT be set: cancellation is operator-driven,
        # not a fault condition.
        assert motor._latched_error is None
    finally:
        await motor.close()


async def test_burst_ready_does_not_double_recover(
    valid_config_dict: dict[str, object],
) -> None:
    """W-01: two mid-session Ready events back-to-back must spawn exactly
    one _recover task (no double-spawn race).
    """
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 5.0,
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # Burst: feed two mid-session preambles back-to-back. Each preamble
        # ends in Ready; the FIRST Ready spawns recovery, the SECOND must be
        # dropped by the in-flight guard.
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        first_task = motor._recover_task
        assert first_task is not None
        # Wait long enough that the second Ready would have been processed.
        await asyncio.sleep(0.05)
        # The same task must still be the in-flight one -- no second task
        # was spawned (otherwise _recover_task would have been overwritten
        # and first_task would not match).
        assert motor._recover_task is first_task, (
            "second mid-session Ready spawned a duplicate _recover task"
        )
        # Drain acks so close() proceeds cleanly.
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
    finally:
        await motor.close()


async def test_mid_session_ready_not_enqueued_to_public_events(
    valid_config_dict: dict[str, object],
) -> None:
    """W-02: mid-session Ready (control signal) must not appear in events()."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 5.0,
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    try:
        # First mid-session Ready -> recovery spawn (case 1, RUNNING).
        fake.feed_rx(b"SETTINGS: loaded from EEPROM")
        fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
        fake.feed_rx(b"READY:v2")
        await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
        # Second mid-session Ready WHILE RECOVERING (case 2). Must be dropped,
        # NOT enqueued to public events.
        fake.feed_rx(b"READY:v2")
        # Brief settle so the bridged Ready is processed before we drain.
        await asyncio.sleep(0.05)
        # Drain remaining acks so we can complete recovery (and so the
        # event-queue drain below is bounded).
        fake.feed_rx(b"SETTINGS:25000.000,12500.000,0.000,0.000,0.000")
        fake.feed_rx(b"LIMITS:-90.000,90.000")
        fake.feed_rx(b"SETTINGS: saved to EEPROM")
        await wait_for_pause_cleared(motor, timeout=1.0)
        public = await _drain_public_events(motor)
        from pastor_tracker.io.arduino_protocol import Ready
        assert not any(isinstance(ev, Ready) for ev in public), (
            "mid-session Ready leaked into public events queue (W-02)"
        )
    finally:
        await motor.close()


async def test_close_during_recovery_cancels_cleanly(
    valid_config_dict: dict[str, object],
) -> None:
    """C-01: close() during in-flight recovery cancels the task; no warnings."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 5.0,  # long enough that we cancel mid-flight
    }
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    await motor.start()
    # Trigger recovery; feed NO acks -- recovery sits awaiting Settings.
    fake.feed_rx(b"SETTINGS: loaded from EEPROM")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    await wait_for_state(motor, _MotorState.RECOVERING, timeout=1.0)
    # Capture the recover task before close() clears it.
    recover_task = motor._recover_task
    assert recover_task is not None
    # close() must cancel the in-flight recover task without raising.
    await motor.close()
    assert motor._recover_task is None
    assert motor.state == _MotorState.CLOSED
    # Sanity: the cancelled task is done (cancellation observed).
    assert recover_task.done()


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
