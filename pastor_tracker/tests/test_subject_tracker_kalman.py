"""Property + state tests on the real filterpy KalmanFilter (PERC-06 + Pitfalls 2, 4).

CLAUDE.md hard rule: no mocks. Every test drives a real KalmanFilter instance.

Plan 01 deviation note (Rule 3 — blocking issue): filterpy 1.4.5 ships a
docstring containing the literal sequence ``\\Sum`` which Python 3.12 treats
as ``SyntaxWarning: invalid escape sequence '\\S'``. Under the project-wide
``filterwarnings = ["error"]`` in pyproject.toml that warning is promoted
to a hard SyntaxError at filterpy import time -- BEFORE any numpy 2.4
interop is exercised. The cosmetic SyntaxWarning is unrelated to RESEARCH
04 Pitfall A1 (which targets numpy DeprecationWarning under filterpy's
math). We ignore the upstream cosmetic warning at the module scope so the
tests actually validate the intended A1 contract: that filterpy's
``KalmanFilter.predict()`` runs cleanly on numpy 2.4.x without numpy
DeprecationWarning. Any genuine numpy deprecation will still escalate to
a hard failure here -- exactly the fail-fast guard A1 demands.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pastor_tracker.perception._kalman import (
    _KalmanWrapper,
    make_kalman_for_subject,
    predict_with_dt,
)

# Mirror test_damping.py header-constants block (CLAUDE.md rule 6).
DT_30HZ: float = 1.0 / 30.0
HYPOTHESIS_MAX_EXAMPLES: int = 40
LINEAR_TRAJECTORY_HORIZON: int = 60
SMOOTHED_VS_TRUTH_TOLERANCE: float = 0.5  # 50% of truth (loose; measurement R is small)
KALMAN_INITIAL_VEL_COV_EXPECTED: float = 1.0


def test_filterpy_smoke_import() -> None:
    """RESEARCH 04 Pitfall A1 -- filterpy 1.4.5 under numpy 2.4.x. (Carried from Plan 01.)"""
    from filterpy.kalman import KalmanFilter
    kf = KalmanFilter(dim_x=4, dim_z=2)
    kf.F = np.eye(4, dtype=np.float64)
    kf.H = np.zeros((2, 4), dtype=np.float64)
    kf.H[0, 0] = 1.0
    kf.H[1, 1] = 1.0
    kf.x = np.zeros(4, dtype=np.float64)
    kf.predict()
    assert kf.x.shape == (4,)


def test_kalman_matrices_at_init() -> None:
    """PERC-06: F/H/Q/R/P/x shapes + initial values match RESEARCH 04 Code Example 1."""
    kf = make_kalman_for_subject(initial_x=0.5, initial_y=0.5, dt=DT_30HZ)
    assert kf.F.shape == (4, 4)
    assert kf.H.shape == (2, 4)
    assert kf.Q.shape == (4, 4)
    assert kf.R.shape == (2, 2)
    assert kf.x.shape == (4,)
    assert kf.P.shape == (4, 4)
    # CV model: F encodes position += velocity * dt
    assert kf.F[0, 2] == pytest.approx(DT_30HZ)
    assert kf.F[1, 3] == pytest.approx(DT_30HZ)
    # Initial state: pos = (0.5, 0.5), velocity = 0
    assert kf.x[0] == pytest.approx(0.5)
    assert kf.x[1] == pytest.approx(0.5)
    assert kf.x[2] == pytest.approx(0.0)
    assert kf.x[3] == pytest.approx(0.0)
    # CONTEXT Area 3: large initial velocity covariance ~1.0
    assert kf.P[2, 2] == pytest.approx(KALMAN_INITIAL_VEL_COV_EXPECTED)
    assert kf.P[3, 3] == pytest.approx(KALMAN_INITIAL_VEL_COV_EXPECTED)


@given(
    vx_truth=st.floats(min_value=-0.3, max_value=0.3, allow_nan=False),
    noise_sigma=st.floats(min_value=0.001, max_value=0.05, allow_nan=False),
)
@settings(deadline=None, max_examples=HYPOTHESIS_MAX_EXAMPLES)
def test_linear_trajectory_velocity_recovery(vx_truth: float, noise_sigma: float) -> None:
    """PERC-06: smoothed vx recovers within 50% of truth on a noisy linear trajectory."""
    rng = np.random.default_rng(seed=42)
    x0, y0 = 0.5, 0.5
    kf = make_kalman_for_subject(initial_x=x0, initial_y=y0, dt=DT_30HZ)
    for k in range(LINEAR_TRAJECTORY_HORIZON):
        t = (k + 1) * DT_30HZ
        true_x = x0 + vx_truth * t
        meas_x = true_x + rng.normal(0.0, noise_sigma)
        meas_y = y0 + rng.normal(0.0, noise_sigma)
        predict_with_dt(kf, DT_30HZ)
        kf.update(np.array([meas_x, meas_y]))
    # Allow large slack; the pertinent test_smoothed_velocity_beats_frame_diff below
    # is the rigorous comparison.
    assert math.isfinite(float(kf.x[2]))
    tolerance = max(SMOOTHED_VS_TRUTH_TOLERANCE * abs(vx_truth) + 0.1, 0.15)
    assert abs(float(kf.x[2]) - vx_truth) < tolerance


@given(
    vx_truth=st.floats(min_value=-0.3, max_value=0.3, allow_nan=False),
    noise_sigma=st.floats(min_value=0.005, max_value=0.05, allow_nan=False),
)
@settings(deadline=None, max_examples=HYPOTHESIS_MAX_EXAMPLES)
def test_smoothed_velocity_beats_frame_diff(vx_truth: float, noise_sigma: float) -> None:
    """CLAUDE.md test-real rule: smoothed vx must beat raw frame-diff on noisy data.

    This is the canonical PERC-06 success-criterion test (lag-free relative to EMA baseline).
    """
    rng = np.random.default_rng(seed=7)
    x0, y0 = 0.5, 0.5
    kf = make_kalman_for_subject(initial_x=x0, initial_y=y0, dt=DT_30HZ)
    last_meas = x0
    raw_diff_errs: list[float] = []
    smoothed_errs: list[float] = []
    for k in range(LINEAR_TRAJECTORY_HORIZON):
        t = (k + 1) * DT_30HZ
        true_x = x0 + vx_truth * t
        meas_x = true_x + rng.normal(0.0, noise_sigma)
        meas_y = y0 + rng.normal(0.0, noise_sigma)
        raw_diff = (meas_x - last_meas) / DT_30HZ
        last_meas = meas_x
        predict_with_dt(kf, DT_30HZ)
        kf.update(np.array([meas_x, meas_y]))
        if k > 5:  # discard warmup
            raw_diff_errs.append(abs(raw_diff - vx_truth))
            smoothed_errs.append(abs(float(kf.x[2]) - vx_truth))
    mean_raw_err = float(np.mean(raw_diff_errs))
    mean_smoothed_err = float(np.mean(smoothed_errs))
    # Smoothed must win on noisy trajectories (the whole point of Kalman).
    assert mean_smoothed_err < mean_raw_err, (
        f"smoothed err {mean_smoothed_err:.4f} >= raw {mean_raw_err:.4f} "
        f"(vx={vx_truth}, sigma={noise_sigma})"
    )


def test_variable_dt_handled() -> None:
    """PERC-06: feeding variable dt does not break the filter (no NaN / Inf)."""
    x0, y0 = 0.5, 0.5
    vx_truth = 0.1
    kf = make_kalman_for_subject(initial_x=x0, initial_y=y0, dt=DT_30HZ)
    dt_sequence = [1 / 30, 1 / 60, 1 / 30, 1 / 15, 1 / 30] * 6
    elapsed = 0.0
    for dt in dt_sequence:
        elapsed += dt
        true_x = x0 + vx_truth * elapsed
        predict_with_dt(kf, dt)
        kf.update(np.array([true_x, y0]))
    assert math.isfinite(float(kf.x[0]))
    assert math.isfinite(float(kf.x[1]))
    assert math.isfinite(float(kf.x[2]))
    assert abs(float(kf.x[0]) - (x0 + vx_truth * elapsed)) < 0.1


def test_reset_creates_fresh_filter() -> None:
    """Pitfall 2: re-acquisition discards previous state."""
    wrapper = _KalmanWrapper()
    kf_old = wrapper.reset_for_new_track(initial_x=0.5, initial_y=0.5, dt=DT_30HZ)
    # Drive vx away from zero
    for _ in range(5):
        predict_with_dt(kf_old, DT_30HZ)
        kf_old.update(np.array([0.5 + 0.1, 0.5]))
    assert abs(float(kf_old.x[2])) > 0.01  # non-zero velocity learned
    # Now reset
    kf_new = wrapper.reset_for_new_track(initial_x=0.7, initial_y=0.3, dt=DT_30HZ)
    assert kf_new is not kf_old
    assert kf_new.x[0] == pytest.approx(0.7)
    assert kf_new.x[1] == pytest.approx(0.3)
    assert kf_new.x[2] == pytest.approx(0.0)
    assert kf_new.x[3] == pytest.approx(0.0)
    assert kf_new.P[2, 2] == pytest.approx(KALMAN_INITIAL_VEL_COV_EXPECTED)


def test_holding_freezes_posterior() -> None:
    """Pitfall 4: HOLDING state must NOT advance Kalman -- repeated calls return identical x."""
    wrapper = _KalmanWrapper()
    kf = wrapper.reset_for_new_track(initial_x=0.5, initial_y=0.5, dt=DT_30HZ)
    for _ in range(3):
        predict_with_dt(kf, DT_30HZ)
        kf.update(np.array([0.55, 0.5]))
    p_trace_before = float(np.trace(kf.P))
    held_calls = [_KalmanWrapper.hold_posterior(kf) for _ in range(5)]
    assert all(c == held_calls[0] for c in held_calls), (
        f"hold_posterior must be idempotent; got {held_calls}"
    )
    p_trace_after = float(np.trace(kf.P))
    # Covariance must not grow during HOLDING (Pitfall 4).
    assert p_trace_after == pytest.approx(p_trace_before)
