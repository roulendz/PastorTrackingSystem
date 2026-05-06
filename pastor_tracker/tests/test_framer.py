"""INTENT-03 / INTENT-04 / TEST-03 framer tests.

Drives real Framer with synthetic MotionState lists. Real
CriticallyDampedFollower (no mocks per CLAUDE.md TEST-05).
Step-response invariants mirror tests/test_damping.py:42-68.

D-12: every numeric flows from ``valid_config_dict`` (framing_time_constant_sec,
capture_fps); the third constants 1/3, 0.5, 2/3 are MATH (not Config-tunables)
and appear as ``1.0 / 3.0`` etc. in expected-value computations.
"""
from __future__ import annotations

import asyncio
import time
from itertools import pairwise
from typing import Final

import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import FramingTarget, MotionIntent, MotionState
from pastor_tracker.intent import Framer

# Step-response invariant tolerances -- mirrors tests/test_damping.py
_OVERSHOOT_TOL: Final[float] = 1e-9
_MONOTONIC_TOL: Final[float] = 1e-9
_SETTLED_TOL: Final[float] = 0.05
_SETTLE_TAU_MULTIPLE: Final[int] = 5
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_T0_NS: Final[int] = 1_000_000_000

# Mathematical thirds (NOT Config-owned; standard rule-of-thirds constants)
_THIRD_LEFT: Final[float] = 1.0 / 3.0
_THIRD_CENTER: Final[float] = 0.5
_THIRD_RIGHT: Final[float] = 2.0 / 3.0
_APPROX_TOL: Final[float] = 1e-9
_NS_PER_SEC: Final[int] = 1_000_000_000


def _make_framer(valid_config_dict: dict[str, object]) -> Framer:
    """Build a Framer from the conftest-owned config dict.

    ``valid_config_dict`` is a SHARED pytest fixture (conftest.py); tests
    that need overrides MUST ``dict(valid_config_dict)``-copy first and
    mutate the copy. NEVER mutate the fixture in place (WR-06)."""
    return Framer(Config(**valid_config_dict))  # type: ignore[arg-type]


def _ms(intent: MotionIntent, ts_ns: int, vx: float = 0.0) -> MotionState:
    return MotionState(
        intent=intent,
        sustained_velocity_x_norm_per_sec=vx,
        timestamp_ns=ts_ns,
    )


# ---------------------------------------------------------------------------
# Initial / hold-on-None / indeterminate
# ---------------------------------------------------------------------------


def test_initial_target_is_none(valid_config_dict: dict[str, object]) -> None:
    framer = _make_framer(valid_config_dict)
    assert framer.current_target_x_normalized is None


def test_none_motion_returns_none_and_clears_state(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    result = asyncio.run(framer.consume(None, now_ns=_T0_NS))
    assert result is None
    assert framer.current_target_x_normalized is None


def test_indeterminate_motion_returns_none_and_clears_state(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    result = asyncio.run(
        framer.consume(_ms("indeterminate", _T0_NS), now_ns=_T0_NS),
    )
    assert result is None
    assert framer.current_target_x_normalized is None


# ---------------------------------------------------------------------------
# Intent dispatch (seed-emit yields the discrete third exactly)
# ---------------------------------------------------------------------------


def test_moving_right_yields_left_third(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    result = asyncio.run(
        framer.consume(_ms("moving_right", _T0_NS, vx=0.1), now_ns=_T0_NS),
    )
    assert result is not None
    assert abs(result.target_x_normalized - _THIRD_LEFT) < _APPROX_TOL
    assert framer.current_target_x_normalized is not None
    assert abs(framer.current_target_x_normalized - _THIRD_LEFT) < _APPROX_TOL


def test_moving_left_yields_right_third(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    result = asyncio.run(
        framer.consume(_ms("moving_left", _T0_NS, vx=-0.1), now_ns=_T0_NS),
    )
    assert result is not None
    assert abs(result.target_x_normalized - _THIRD_RIGHT) < _APPROX_TOL


def test_dwelling_yields_center(valid_config_dict: dict[str, object]) -> None:
    framer = _make_framer(valid_config_dict)
    result = asyncio.run(
        framer.consume(_ms("dwelling", _T0_NS), now_ns=_T0_NS),
    )
    assert result is not None
    assert abs(result.target_x_normalized - _THIRD_CENTER) < _APPROX_TOL


# ---------------------------------------------------------------------------
# Step response: real damper, no overshoot
# ---------------------------------------------------------------------------


def test_step_response_no_overshoot_right_to_left(
    valid_config_dict: dict[str, object],
) -> None:
    """Seed at left third, drive moving_left for 5*tau; assert no overshoot
    above right third, monotonic, settled within 5%."""
    framer = _make_framer(valid_config_dict)
    tau = float(valid_config_dict["framing_time_constant_sec"])
    seed_result = asyncio.run(
        framer.consume(_ms("moving_right", _T0_NS), now_ns=_T0_NS),
    )
    assert seed_result is not None
    assert abs(seed_result.target_x_normalized - _THIRD_LEFT) < _APPROX_TOL
    total_sec = _SETTLE_TAU_MULTIPLE * tau
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    horizon = int(total_sec / _DT_30HZ_SEC)
    positions: list[float] = [seed_result.target_x_normalized]
    for k in range(1, horizon + 1):
        ts_ns = _T0_NS + k * dt_ns
        result = asyncio.run(
            framer.consume(_ms("moving_left", ts_ns), now_ns=ts_ns),
        )
        assert isinstance(result, FramingTarget)
        positions.append(result.target_x_normalized)
    # 1) No overshoot above the right third.
    assert max(positions) <= _THIRD_RIGHT + _OVERSHOOT_TOL, (
        f"overshoot: max={max(positions)}"
    )
    # 2) Monotonic non-decreasing.
    diffs = [b - a for a, b in pairwise(positions)]
    assert all(d >= -_MONOTONIC_TOL for d in diffs), (
        f"non-monotonic: min_diff={min(diffs)}"
    )
    # 3) Settled to within 5% of the right third by 5*tau.
    assert abs(positions[-1] - _THIRD_RIGHT) < _SETTLED_TOL, (
        f"slow convergence: pos@5tau={positions[-1]}"
    )


def test_step_response_no_overshoot_left_to_right(
    valid_config_dict: dict[str, object],
) -> None:
    """Symmetric: seed at right third, drive moving_right for 5*tau;
    monotonic decreasing toward left third, no undershoot."""
    framer = _make_framer(valid_config_dict)
    tau = float(valid_config_dict["framing_time_constant_sec"])
    seed_result = asyncio.run(
        framer.consume(_ms("moving_left", _T0_NS), now_ns=_T0_NS),
    )
    assert seed_result is not None
    assert abs(seed_result.target_x_normalized - _THIRD_RIGHT) < _APPROX_TOL
    total_sec = _SETTLE_TAU_MULTIPLE * tau
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    horizon = int(total_sec / _DT_30HZ_SEC)
    positions: list[float] = [seed_result.target_x_normalized]
    for k in range(1, horizon + 1):
        ts_ns = _T0_NS + k * dt_ns
        result = asyncio.run(
            framer.consume(_ms("moving_right", ts_ns), now_ns=ts_ns),
        )
        assert isinstance(result, FramingTarget)
        positions.append(result.target_x_normalized)
    # 1) No undershoot below the left third.
    assert min(positions) >= _THIRD_LEFT - _OVERSHOOT_TOL, (
        f"undershoot: min={min(positions)}"
    )
    # 2) Monotonic non-increasing.
    diffs = [b - a for a, b in pairwise(positions)]
    assert all(d <= _MONOTONIC_TOL for d in diffs), (
        f"non-monotonic: max_diff={max(diffs)}"
    )
    # 3) Settled to within 5% of the left third by 5*tau.
    assert abs(positions[-1] - _THIRD_LEFT) < _SETTLED_TOL, (
        f"slow convergence: pos@5tau={positions[-1]}"
    )


# ---------------------------------------------------------------------------
# Hold-on-None + clean re-seed (D-07 + Pitfall 6)
# ---------------------------------------------------------------------------


def test_hold_during_none_upstream_then_reseed_at_new_target(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    # Seed at left third.
    result = asyncio.run(
        framer.consume(_ms("moving_right", _T0_NS), now_ns=_T0_NS),
    )
    assert result is not None
    assert abs(result.target_x_normalized - _THIRD_LEFT) < _APPROX_TOL
    # None upstream clears state.
    result = asyncio.run(framer.consume(None, now_ns=_T0_NS + _NS_PER_SEC))
    assert result is None
    assert framer.current_target_x_normalized is None
    # Fresh non-indeterminate re-seeds at the NEW target, not the prior one.
    ts2 = _T0_NS + 2 * _NS_PER_SEC
    result = asyncio.run(framer.consume(_ms("moving_left", ts2), now_ns=ts2))
    assert result is not None
    assert abs(result.target_x_normalized - _THIRD_RIGHT) < _APPROX_TOL


# ---------------------------------------------------------------------------
# dt floor protects damper from non-monotonic upstream (T-05-03-06)
# ---------------------------------------------------------------------------


def test_non_monotonic_upstream_uses_dt_floor(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    asyncio.run(framer.consume(_ms("moving_right", _T0_NS), now_ns=_T0_NS))
    # Backward-stepping upstream timestamp -- damper.step requires dt > 0;
    # framer's _compute_dt_sec floors to 1/capture_fps.
    result = asyncio.run(
        framer.consume(_ms("moving_left", _T0_NS - 1_000_000), now_ns=_T0_NS),
    )
    # No ValueError = success.
    assert result is not None


# ---------------------------------------------------------------------------
# Logger events (Pattern 9)
# ---------------------------------------------------------------------------


def test_seed_logs_framer_seeded(
    valid_config_dict: dict[str, object],
) -> None:
    framer = _make_framer(valid_config_dict)
    with structlog.testing.capture_logs() as caplog:
        asyncio.run(framer.consume(_ms("dwelling", _T0_NS), now_ns=_T0_NS))
    seed_events = [e for e in caplog if e.get("event") == "framer_seeded"]
    assert len(seed_events) == 1
    assert abs(seed_events[0]["position"] - _THIRD_CENTER) < _APPROX_TOL


def test_framing_target_change_logged_on_discrete_shift(
    valid_config_dict: dict[str, object],
) -> None:
    """Pattern 9 / CONTEXT <specifics> line 130 / Issue 3+4.

    The INFO ``framing_target_change`` event MUST fire exactly once per
    discrete intent-driven target shift, NOT per damper step. Verifies the
    seed -> step -> step (no log) -> intent flip (one log) -> step (no log)
    -> intent flip (second log) timeline.
    """
    framer = _make_framer(valid_config_dict)
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    with structlog.testing.capture_logs() as caplog:
        # Seed at left third (moving_right).
        asyncio.run(framer.consume(_ms("moving_right", _T0_NS), now_ns=_T0_NS))
        # A few more moving_right ticks -- discrete target unchanged, no log.
        for k in range(1, 4):
            ts = _T0_NS + k * dt_ns
            asyncio.run(framer.consume(_ms("moving_right", ts), now_ns=ts))
        # Flip to dwelling -- first framing_target_change INFO event expected.
        ts4 = _T0_NS + 4 * dt_ns
        asyncio.run(framer.consume(_ms("dwelling", ts4), now_ns=ts4))
        # Stay on dwelling for a few ticks -- no further log.
        for k in range(5, 8):
            ts = _T0_NS + k * dt_ns
            asyncio.run(framer.consume(_ms("dwelling", ts), now_ns=ts))
        # Flip to moving_left -- second framing_target_change INFO event.
        ts8 = _T0_NS + 8 * dt_ns
        asyncio.run(framer.consume(_ms("moving_left", ts8), now_ns=ts8))
    change_events = [
        e for e in caplog if e.get("event") == "framing_target_change"
    ]
    assert len(change_events) == 2, (
        f"expected exactly 2 framing_target_change events (one per discrete "
        f"shift); got {len(change_events)}: {change_events}"
    )
    # First flip: left third -> center.
    assert abs(change_events[0]["old"] - _THIRD_LEFT) < _APPROX_TOL
    assert abs(change_events[0]["new"] - _THIRD_CENTER) < _APPROX_TOL
    # Second flip: center -> right third.
    assert abs(change_events[1]["old"] - _THIRD_CENTER) < _APPROX_TOL
    assert abs(change_events[1]["new"] - _THIRD_RIGHT) < _APPROX_TOL


# ---------------------------------------------------------------------------
# D-02: never reads wall clock
# ---------------------------------------------------------------------------


def test_consume_does_not_read_wall_clock(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02 mirror of MotionAnalyzer: the Framer derives dt from the upstream
    motion.timestamp_ns delta and uses ``now_ns`` for the emitted DTO field
    only; it MUST NOT call ``time.perf_counter_ns`` / ``time.time`` directly.

    Patches the same surface as ``test_motion_analyzer.test_no_emission_reads_-
    wall_clock`` (D-02 precedent) -- not ``time.monotonic``, which the asyncio
    Windows proactor loop uses internally on close.
    """

    def _raise(*_args: object, **_kwargs: object) -> int:
        raise AssertionError(
            "Framer.consume must not read a wall clock (D-02)"
        )

    monkeypatch.setattr(time, "perf_counter_ns", _raise)
    monkeypatch.setattr(time, "time", _raise)

    framer = _make_framer(valid_config_dict)
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    # Drive a varied sequence: seed, step, indeterminate (reset), re-seed.
    # Each consume() call wrapped in its own asyncio.run is the precedent
    # used by the analyzer wall-clock test (Plan 02).
    result = asyncio.run(
        framer.consume(_ms("moving_right", _T0_NS), now_ns=_T0_NS),
    )
    assert result is not None
    result = asyncio.run(
        framer.consume(_ms("moving_right", _T0_NS + dt_ns), now_ns=_T0_NS + dt_ns),
    )
    assert result is not None
    result = asyncio.run(
        framer.consume(
            _ms("dwelling", _T0_NS + 2 * dt_ns), now_ns=_T0_NS + 2 * dt_ns,
        ),
    )
    assert result is not None
    none_result = asyncio.run(
        framer.consume(None, now_ns=_T0_NS + 3 * dt_ns),
    )
    assert none_result is None
    result = asyncio.run(
        framer.consume(
            _ms("moving_left", _T0_NS + 4 * dt_ns), now_ns=_T0_NS + 4 * dt_ns,
        ),
    )
    assert result is not None
