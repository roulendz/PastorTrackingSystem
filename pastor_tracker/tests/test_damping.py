"""Step-response test for ``pastor_tracker.core.damping`` (CORE-03 / TEST-02).

TEST-05: Real math only. No ``unittest.mock``, no fakes. This file imports
the real ``CriticallyDampedFollower`` and exercises it.
"""
from __future__ import annotations

import math
from itertools import pairwise

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pastor_tracker.core.damping import CriticallyDampedFollower

OVERSHOOT_TOL: float = 1e-9
MONOTONIC_TOL: float = 1e-9
SETTLED_TOL: float = 0.05
TARGET_UNIT_STEP: float = 1.0
DT_60HZ: float = 1.0 / 60.0
HORIZON_MULTIPLE: int = 10
MIN_HORIZON_STEPS: int = 600
SETTLE_TAU_MULTIPLE: int = 5
HYP_MAX_EXAMPLES: int = 40
TWO_SECONDS_AT_60HZ: int = 120
KNOWN_TAU_SEC: float = 0.6
KNOWN_HORIZON_SEC: float = 3.0
KNOWN_SETTLE_FRACTION: float = 0.95


@given(
    tau=st.floats(
        min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False
    )
)
@settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    max_examples=HYP_MAX_EXAMPLES,
)
def test_critically_damped_no_overshoot(tau: float) -> None:
    """Unit step response: zero overshoot, monotonic, settled within 5% by 5*tau.

    Sweeps tau in [0.1, 2.0] s — covers Config.framing_time_constant_sec (0.8 s)
    and Config.pan_time_constant_sec (0.6 s) defaults plus 6x headroom.
    """
    follower = CriticallyDampedFollower(time_constant_sec=tau)
    state = follower.initial_state(position=0.0)
    horizon = max(int(HORIZON_MULTIPLE * tau / DT_60HZ), MIN_HORIZON_STEPS)
    positions: list[float] = [state.position]
    for _ in range(horizon):
        state = follower.step(state, target=TARGET_UNIT_STEP, dt=DT_60HZ)
        positions.append(state.position)
    # 1) No overshoot.
    assert max(positions) <= TARGET_UNIT_STEP + OVERSHOOT_TOL, (
        f"overshoot tau={tau}: max={max(positions)}"
    )
    # 2) Monotonic non-decreasing (critical damping from rest below target).
    diffs = [b - a for a, b in pairwise(positions)]
    assert all(d >= -MONOTONIC_TOL for d in diffs), (
        f"non-monotonic tau={tau}: min_diff={min(diffs)}"
    )
    # 3) Settled to within 5% by 5*tau.
    idx_5tau = int(SETTLE_TAU_MULTIPLE * tau / DT_60HZ)
    assert abs(positions[idx_5tau] - TARGET_UNIT_STEP) < SETTLED_TOL, (
        f"slow convergence tau={tau}: pos@5tau={positions[idx_5tau]}"
    )


def test_zero_or_negative_time_constant_rejected() -> None:
    with pytest.raises(ValueError, match="time_constant_sec"):
        CriticallyDampedFollower(time_constant_sec=0.0)
    with pytest.raises(ValueError, match="time_constant_sec"):
        CriticallyDampedFollower(time_constant_sec=-0.5)


def test_zero_or_negative_dt_rejected() -> None:
    follower = CriticallyDampedFollower(time_constant_sec=0.5)
    state = follower.initial_state(position=0.0)
    with pytest.raises(ValueError, match="dt"):
        follower.step(state, target=1.0, dt=0.0)
    with pytest.raises(ValueError, match="dt"):
        follower.step(state, target=1.0, dt=-0.01)


def test_at_target_stays_at_target() -> None:
    """If position == target and velocity == 0, the follower does not drift."""
    follower = CriticallyDampedFollower(time_constant_sec=KNOWN_TAU_SEC)
    state = follower.initial_state(position=1.0)
    for _ in range(TWO_SECONDS_AT_60HZ):
        state = follower.step(state, target=1.0, dt=DT_60HZ)
    assert math.isclose(state.position, 1.0, abs_tol=1e-12)
    assert math.isclose(state.velocity, 0.0, abs_tol=1e-12)


def test_known_tau_06_settles_within_5pct_by_3sec() -> None:
    """Sanity: tau=0.6 s reaches 95% of target in ~3 s (5*tau); deterministic."""
    follower = CriticallyDampedFollower(time_constant_sec=KNOWN_TAU_SEC)
    state = follower.initial_state(position=0.0)
    steps = int(KNOWN_HORIZON_SEC / DT_60HZ)
    for _ in range(steps):
        state = follower.step(state, target=1.0, dt=DT_60HZ)
    assert state.position > KNOWN_SETTLE_FRACTION
    assert state.position <= 1.0 + OVERSHOOT_TOL
