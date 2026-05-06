"""CTRL-04 / TEST-03 CommandDispatcher tests (synchronous).

Drives real CommandDispatcher with manual angle sequences. No mocks
(TEST-05 + D-12). All numerics flow from ``valid_config_dict``.

NB: dispatcher tests are SYNC (no event-loop wrapper, no awaitable) --
``decide()`` is the only synchronous Phase 5 stage (D-01 + Pattern D).
Forcing an event-loop wrapper would only add overhead without exercising
any real concurrency surface.

Coverage target (D-13): 100% line + 100% branch on command_dispatcher.py.
The split-into-two-if design in Task 1 is what makes 100% branch coverage
achievable -- coverage tools cannot tell which side of an ``A and B`` short
circuit failed; two distinct ``if`` blocks give two distinct branches.
"""
from __future__ import annotations

import time
from typing import Final

import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.control import CommandDispatcher
from pastor_tracker.core.types import MotorCommand

_T0_NS: Final[int] = 1_000_000_000
_MS_NS: Final[int] = 1_000_000
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_DT_30HZ_NS: Final[int] = int(_DT_30HZ_SEC * 1_000_000_000)
_RAMP_DEGREE_RANGE: Final[float] = 30.0  # angular range of synthetic ramp test
_RAMP_DURATION_SEC: Final[float] = 1.0
_RAMP_BOUNDARY_SLACK: Final[int] = 2  # +1 first-call seed, +1 boundary-frame slack


def _make_dispatcher(valid_config_dict: dict[str, object]) -> CommandDispatcher:
    return CommandDispatcher(Config(**valid_config_dict))  # type: ignore[arg-type]


# ---------- initial / first-call ----------


def test_initial_state_is_none(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    assert dispatcher.last_emitted_angle_deg is None
    assert dispatcher.last_emit_ts_ns is None


def test_first_call_always_emits(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    cmd = dispatcher.decide(1.5, now_ns=_T0_NS)
    assert isinstance(cmd, MotorCommand)
    assert cmd.target_angle_deg == 1.5
    assert cmd.timestamp_ns == _T0_NS
    assert dispatcher.last_emitted_angle_deg == 1.5
    assert dispatcher.last_emit_ts_ns == _T0_NS


def test_first_call_with_zero_angle_emits(
    valid_config_dict: dict[str, object],
) -> None:
    """Confirm the first-call gate uses ``is None`` on state, not falsy on angle.

    A naive ``if not self._last_emitted_angle_deg`` would treat a freshly seeded
    0.0 angle as "no seed yet" and re-fire the first-call branch on the next
    frame, leaking emissions.
    """
    dispatcher = _make_dispatcher(valid_config_dict)
    cmd = dispatcher.decide(0.0, now_ns=_T0_NS)
    assert isinstance(cmd, MotorCommand)
    assert cmd.target_angle_deg == 0.0
    assert dispatcher.last_emitted_angle_deg == 0.0
    assert dispatcher.last_emit_ts_ns == _T0_NS


# ---------- None upstream (Pitfall 7) ----------


def test_none_pre_seed_does_not_update_state(
    valid_config_dict: dict[str, object],
) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    assert dispatcher.decide(None, now_ns=_T0_NS) is None
    # State must remain None; a None upstream call must NOT seed.
    assert dispatcher.last_emitted_angle_deg is None
    assert dispatcher.last_emit_ts_ns is None


def test_none_does_not_update_state(valid_config_dict: dict[str, object]) -> None:
    """Pitfall 7: None upstream after a successful seed must not overwrite state."""
    dispatcher = _make_dispatcher(valid_config_dict)
    dispatcher.decide(1.0, now_ns=_T0_NS)
    # None upstream MUST NOT overwrite state.
    assert dispatcher.decide(None, now_ns=_T0_NS + 100 * _MS_NS) is None
    assert dispatcher.last_emitted_angle_deg == 1.0
    assert dispatcher.last_emit_ts_ns == _T0_NS


def test_none_then_real_after_long_gap_uses_correct_interval(
    valid_config_dict: dict[str, object],
) -> None:
    """Pitfall 7 across multi-tick gap: interval is measured from last EMITTED ts."""
    dispatcher = _make_dispatcher(valid_config_dict)
    dispatcher.decide(0.0, now_ns=_T0_NS)
    # Three None ticks at 1, 2, 3 seconds -- none update state.
    assert dispatcher.decide(None, now_ns=_T0_NS + 1_000_000_000) is None
    assert dispatcher.decide(None, now_ns=_T0_NS + 2_000_000_000) is None
    # Real call 3 s after seed: interval is 3 s (from _T0_NS), not 1 s
    # (from the most recent None tick). 3 s >> any min_interval; emit.
    cmd = dispatcher.decide(5.0, now_ns=_T0_NS + 3_000_000_000)
    assert isinstance(cmd, MotorCommand)
    assert cmd.target_angle_deg == 5.0


# ---------- delta gate (D-11 strict greater-than) ----------


def test_small_delta_suppressed(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    min_delta = float(valid_config_dict["command_min_delta_deg"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    # Tiny delta < min_delta, sufficient interval (well over min_interval).
    tiny = min_delta * 0.5
    assert dispatcher.decide(tiny, now_ns=_T0_NS + 200 * _MS_NS) is None
    # State unchanged on suppression.
    assert dispatcher.last_emitted_angle_deg == 0.0
    assert dispatcher.last_emit_ts_ns == _T0_NS


def test_delta_at_threshold_does_not_emit(
    valid_config_dict: dict[str, object],
) -> None:
    """D-11: emit iff |Δ| > min_delta. Strict greater-than, not >=."""
    dispatcher = _make_dispatcher(valid_config_dict)
    min_delta = float(valid_config_dict["command_min_delta_deg"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    # Exactly at threshold (|Δ| == min_delta, NOT > min_delta).
    assert dispatcher.decide(min_delta, now_ns=_T0_NS + 200 * _MS_NS) is None
    assert dispatcher.last_emitted_angle_deg == 0.0


# ---------- interval gate (D-11 >=) ----------


def test_short_interval_suppressed(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    min_interval_ms = int(valid_config_dict["command_min_interval_ms"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    # Big delta but short interval (half of min).
    short_interval_ns = (min_interval_ms // 2) * _MS_NS
    assert dispatcher.decide(5.0, now_ns=_T0_NS + short_interval_ns) is None
    # State unchanged on suppression.
    assert dispatcher.last_emitted_angle_deg == 0.0
    assert dispatcher.last_emit_ts_ns == _T0_NS


def test_interval_at_threshold_emits(valid_config_dict: dict[str, object]) -> None:
    """D-11: interval gate is >= min_interval (inclusive); contrast with delta's strict >."""
    dispatcher = _make_dispatcher(valid_config_dict)
    min_interval_ms = int(valid_config_dict["command_min_interval_ms"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    cmd = dispatcher.decide(5.0, now_ns=_T0_NS + min_interval_ms * _MS_NS)
    assert isinstance(cmd, MotorCommand)
    assert cmd.target_angle_deg == 5.0


# ---------- both gates pass ----------


def test_both_gates_pass_emits(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    dispatcher.decide(0.0, now_ns=_T0_NS)
    cmd = dispatcher.decide(5.0, now_ns=_T0_NS + 200 * _MS_NS)
    assert isinstance(cmd, MotorCommand)
    assert dispatcher.last_emitted_angle_deg == 5.0
    assert dispatcher.last_emit_ts_ns == _T0_NS + 200 * _MS_NS


# ---------- analytic emission upper bound (Open Question 3 + Issue 9) ----------


def test_ramp_emission_count_bounded(valid_config_dict: dict[str, object]) -> None:
    """Open Question 3: emissions over a synthetic ramp <= analytic bound.

    For a ramp of duration T s with a dispatcher gating min_delta_deg AND
    min_interval_ms, the upper bound is
    ``min(T*1000/min_interval_ms, range/min_delta_deg)``. For default Config
    the interval gate dominates over a 30 deg / 1 s ramp -- ~20 emissions
    in a perfect world.

    Bound formula (Issue 9 fix):
        bound = floor(T*1000/min_interval_ms) + 2
                                              ^^^
                +1 for first-call seed emission,
                +1 for boundary-frame slack at min_interval_ms quantization
                   (avoids off-by-one flake when 30 Hz frame timestamps
                    align with the gate boundary).

    Issue 12: defensive precondition asserts ``min_interval_ms > 0`` BEFORE
    the division -- guards against an accidental Config zero (Pydantic field
    is ``gt=0`` so the assert in production is redundant, but the test math
    is undefined at 0 and fails loudly with a clear message instead of
    ZeroDivisionError).
    """
    dispatcher = _make_dispatcher(valid_config_dict)
    min_interval_ms = int(valid_config_dict["command_min_interval_ms"])
    # Issue 12: defensive precondition before division.
    assert min_interval_ms > 0, (
        "ramp test requires positive interval -- check Config defaults"
    )
    n_frames = int(_RAMP_DURATION_SEC / _DT_30HZ_SEC)
    emissions = 0
    for k in range(n_frames):
        angle = _RAMP_DEGREE_RANGE * k / n_frames
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        if dispatcher.decide(angle, now_ns=ts_ns) is not None:
            emissions += 1
    # Issue 9 revised formula: + 2 (NOT + 1) accounts for first-call seed +
    # boundary-frame slack at min_interval_ms quantization.
    bound = int(_RAMP_DURATION_SEC * 1000 / min_interval_ms) + _RAMP_BOUNDARY_SLACK
    assert emissions <= bound, (
        f"emissions {emissions} exceeds analytic bound {bound}"
    )


# ---------- logger events (Pattern 9, all DEBUG) ----------


def test_command_emitted_log_event(valid_config_dict: dict[str, object]) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    dispatcher.decide(0.0, now_ns=_T0_NS)  # seed (also emits an event)
    with structlog.testing.capture_logs() as caplog:
        dispatcher.decide(5.0, now_ns=_T0_NS + 200 * _MS_NS)
    events = [e["event"] for e in caplog]
    assert "command_emitted" in events
    emitted = next(e for e in caplog if e["event"] == "command_emitted")
    assert "angle_deg" in emitted
    assert "delta_deg" in emitted
    assert "interval_ms" in emitted


def test_command_suppressed_delta_log_event(
    valid_config_dict: dict[str, object],
) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    min_delta = float(valid_config_dict["command_min_delta_deg"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    with structlog.testing.capture_logs() as caplog:
        dispatcher.decide(min_delta * 0.5, now_ns=_T0_NS + 200 * _MS_NS)
    events = [e["event"] for e in caplog]
    assert "command_suppressed_delta" in events
    suppressed = next(
        e for e in caplog if e["event"] == "command_suppressed_delta"
    )
    assert "angle_deg" in suppressed
    assert "delta_deg" in suppressed


def test_command_suppressed_interval_log_event(
    valid_config_dict: dict[str, object],
) -> None:
    dispatcher = _make_dispatcher(valid_config_dict)
    min_interval_ms = int(valid_config_dict["command_min_interval_ms"])
    dispatcher.decide(0.0, now_ns=_T0_NS)
    short_interval_ns = (min_interval_ms // 2) * _MS_NS
    with structlog.testing.capture_logs() as caplog:
        # Big delta, short interval -- delta gate passes, interval gate fails.
        dispatcher.decide(5.0, now_ns=_T0_NS + short_interval_ns)
    events = [e["event"] for e in caplog]
    assert "command_suppressed_interval" in events
    suppressed = next(
        e for e in caplog if e["event"] == "command_suppressed_interval"
    )
    assert "angle_deg" in suppressed
    assert "interval_ms" in suppressed


# ---------- D-02: dispatcher uses no wall clock ----------


def test_no_emission_uses_wall_clock(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02: dispatcher must not call ``time.perf_counter_ns`` (or any wall clock).

    Make ``time.perf_counter_ns`` raise; if dispatcher used it internally the
    test would surface the exception. ``now_ns`` is the only time source.
    """
    def _explode() -> int:  # pragma: no cover - only called if dispatcher misbehaves
        raise AssertionError("dispatcher must not read time.perf_counter_ns (D-02)")

    monkeypatch.setattr(time, "perf_counter_ns", _explode)
    dispatcher = _make_dispatcher(valid_config_dict)
    # First emit, suppression, re-emit -- exercises every code path that
    # might be tempted to read a wall clock.
    assert dispatcher.decide(0.0, now_ns=_T0_NS) is not None
    assert dispatcher.decide(0.05, now_ns=_T0_NS + 200 * _MS_NS) is None  # delta gate
    assert dispatcher.decide(5.0, now_ns=_T0_NS + 10 * _MS_NS) is None  # interval gate
    assert dispatcher.decide(5.0, now_ns=_T0_NS + 300 * _MS_NS) is not None  # both pass
