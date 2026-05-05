"""Sequence-gap / regression detection tests for ArduinoMotor (W-04).

``_check_seq_gap`` runs sync on the loop thread inside ``_on_rx_event``.
We exercise it directly rather than through the full RX bridge -- it is
a pure function on ``self._last_seq`` and the incoming :class:`Feedback`
DTO. No threading involved.
"""
from __future__ import annotations

import structlog

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import ArduinoMotor, _MotorState
from pastor_tracker.io.arduino_protocol import SEQ_MODULUS, Feedback
from pastor_tracker.io.arduino_transport import FakeSerialTransport


def _make_fb(sequence: int) -> Feedback:
    """Minimal valid Feedback DTO with a chosen ``sequence``."""
    return Feedback(
        current_angle_deg=0.0,
        target_angle_deg=0.0,
        speed_steps_per_sec=0.0,
        is_running=False,
        timestamp_micros=0,
        sequence=sequence,
        accel_phase=0,
    )


def test_seq_regression_logs_regression_event_not_gap(
    valid_config_dict: dict[str, object],
) -> None:
    """W-04: a backwards seq jump logs feedback_seq_regression, NOT a 4.29e9 gap."""
    motor = ArduinoMotor(FakeSerialTransport(), Config(**valid_config_dict))
    # Seed: last_seq=10, then receive seq=0 (firmware reset). Modular delta
    # is (0 - 10) % 2^32 = 4_294_967_286 -- a regression, not a forward gap.
    motor._last_seq = 10
    with structlog.testing.capture_logs() as caplog:
        motor._check_seq_gap(_make_fb(0))
    regression = [
        r for r in caplog if r.get("event") == "feedback_seq_regression"
    ]
    assert len(regression) == 1, (
        f"expected exactly one feedback_seq_regression event, got {caplog!r}"
    )
    assert regression[0]["from_seq"] == 10
    assert regression[0]["to_seq"] == 0
    # Must NOT have logged a forward-gap event.
    assert not any(r.get("event") == "feedback_seq_gap" for r in caplog), (
        f"regression mis-classified as forward gap: {caplog!r}"
    )
    # Last seq must be reset to the regressed value so subsequent
    # contiguous feedback does not log spurious gaps.
    assert motor._last_seq == 0


def test_seq_forward_gap_above_threshold_logs_gap(
    valid_config_dict: dict[str, object],
) -> None:
    """Sanity: a small forward gap above the threshold still logs feedback_seq_gap."""
    motor = ArduinoMotor(FakeSerialTransport(), Config(**valid_config_dict))
    motor._last_seq = 100
    with structlog.testing.capture_logs() as caplog:
        # Threshold is 5; jump of 10 is above it.
        motor._check_seq_gap(_make_fb(110))
    gaps = [r for r in caplog if r.get("event") == "feedback_seq_gap"]
    assert len(gaps) == 1, f"expected exactly one gap event, got {caplog!r}"
    assert gaps[0]["gap"] == 10
    assert motor._last_seq == 110


def test_seq_forward_gap_within_threshold_silent(
    valid_config_dict: dict[str, object],
) -> None:
    """Within-threshold contiguous forward seq does NOT log a gap event."""
    motor = ArduinoMotor(FakeSerialTransport(), Config(**valid_config_dict))
    motor._last_seq = 100
    with structlog.testing.capture_logs() as caplog:
        motor._check_seq_gap(_make_fb(101))
        motor._check_seq_gap(_make_fb(102))
    assert not any(
        r.get("event") in ("feedback_seq_gap", "feedback_seq_regression")
        for r in caplog
    ), f"unexpected log on contiguous seq: {caplog!r}"
    assert motor._last_seq == 102


def test_seq_regression_records_after_recovery_flag(
    valid_config_dict: dict[str, object],
) -> None:
    """W-04: feedback_seq_regression carries after_recovery flag.

    Phase 7 dashboard distinguishes "regression we expected because the
    orchestrator already spawned _recover" from "regression we did not
    expect" without timestamp correlation across log events.
    """
    motor = ArduinoMotor(FakeSerialTransport(), Config(**valid_config_dict))
    motor._last_seq = 10

    # Case 1: state=RUNNING -- after_recovery is False.
    with structlog.testing.capture_logs() as caplog_running:
        motor._check_seq_gap(_make_fb(0))
    regressions_running = [
        r for r in caplog_running if r.get("event") == "feedback_seq_regression"
    ]
    assert len(regressions_running) == 1
    assert regressions_running[0]["after_recovery"] is False

    # Case 2: state=RECOVERING -- after_recovery is True.
    motor._last_seq = 20
    motor._state = _MotorState.RECOVERING
    with structlog.testing.capture_logs() as caplog_recovering:
        motor._check_seq_gap(_make_fb(0))
    regressions_recovering = [
        r for r in caplog_recovering if r.get("event") == "feedback_seq_regression"
    ]
    assert len(regressions_recovering) == 1
    assert regressions_recovering[0]["after_recovery"] is True


def test_seq_rollover_at_modulus_boundary_is_forward_not_regression(
    valid_config_dict: dict[str, object],
) -> None:
    """uint32 rollover (0xFFFFFFFE -> 0) is a small forward gap, not a regression."""
    motor = ArduinoMotor(FakeSerialTransport(), Config(**valid_config_dict))
    motor._last_seq = SEQ_MODULUS - 2  # 0xFFFFFFFE
    with structlog.testing.capture_logs() as caplog:
        motor._check_seq_gap(_make_fb(0))
    # gap = (0 - (2^32 - 2)) % 2^32 = 2 -- small forward gap, NOT regression.
    assert not any(
        r.get("event") == "feedback_seq_regression" for r in caplog
    ), f"rollover mis-classified as regression: {caplog!r}"
    assert motor._last_seq == 0
