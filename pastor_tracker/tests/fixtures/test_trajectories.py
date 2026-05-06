"""Self-tests for tests.fixtures.trajectories.

The Phase 5 unit tests (motion_analyzer, framer, pan_controller, dispatcher,
composition smoke) all consume these helpers; if a helper silently breaks
contract, every downstream test rots. These tests pin the contract.
"""
from __future__ import annotations

from itertools import pairwise
from typing import Final

import pytest

from tests.fixtures.trajectories import (
    borderline_chatter,
    dwell_then_walk,
    ramp,
    step,
)

# Module-level test constants — local to this self-test; NOT Config-owned.
# These are fixture-input parameters, deliberately distinct from any Phase-5
# Config threshold (motion_threshold=0.08, hysteresis=0.3, dwell=1.5, etc.).
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_DT_20HZ_SEC: Final[float] = 0.05  # 20 Hz, exact for asserting frame counts
_TOTAL_SEC_1S: Final[float] = 1.0
_TOTAL_SEC_1P5S: Final[float] = 1.5
_T_STEP_HALF_SEC: Final[float] = 0.5
_X_BEFORE_LEFT: Final[float] = 0.3
_X_AFTER_RIGHT: Final[float] = 0.7
_DWELL_HALF_SEC: Final[float] = 0.5
_WALK_VX_TENTH: Final[float] = 0.1
_CHATTER_AMPLITUDE: Final[float] = 0.09
_X_CENTER: Final[float] = 0.5
_ZERO_TOL: Final[float] = 1e-12
_SETTLE_TOL: Final[float] = 1e-9
_EXPECTED_FRAMES_1S_AT_20HZ: Final[int] = 20
_EXPECTED_DWELL_FRAMES: Final[int] = 10  # 0.5s / 0.05s


def test_step_yields_two_phase_x_sequence() -> None:
    subjects = step(
        t_step_sec=_T_STEP_HALF_SEC,
        dt_sec=_DT_20HZ_SEC,
        total_sec=_TOTAL_SEC_1S,
        x_before=_X_BEFORE_LEFT,
        x_after=_X_AFTER_RIGHT,
    )
    assert len(subjects) == _EXPECTED_FRAMES_1S_AT_20HZ
    step_frame = int(_T_STEP_HALF_SEC / _DT_20HZ_SEC)
    # Pre-step frames: x = x_before, vx = 0
    for s in subjects[:step_frame]:
        assert s.subject_center_x_normalized == _X_BEFORE_LEFT
        assert s.velocity_x_norm_per_sec == 0.0
    # Step frame: x = x_after, vx = (x_after - x_before) / dt_sec
    expected_vx = (_X_AFTER_RIGHT - _X_BEFORE_LEFT) / _DT_20HZ_SEC
    assert subjects[step_frame].subject_center_x_normalized == _X_AFTER_RIGHT
    assert abs(subjects[step_frame].velocity_x_norm_per_sec - expected_vx) < _ZERO_TOL
    # Post-step frames: x = x_after, vx = 0
    for s in subjects[step_frame + 1:]:
        assert s.subject_center_x_normalized == _X_AFTER_RIGHT
        assert s.velocity_x_norm_per_sec == 0.0


def test_ramp_yields_constant_vx() -> None:
    subjects = ramp(
        x_start=_X_BEFORE_LEFT,
        x_end=_X_AFTER_RIGHT,
        dt_sec=_DT_20HZ_SEC,
        total_sec=_TOTAL_SEC_1S,
    )
    assert len(subjects) == _EXPECTED_FRAMES_1S_AT_20HZ
    expected_vx = (_X_AFTER_RIGHT - _X_BEFORE_LEFT) / _TOTAL_SEC_1S
    for s in subjects:
        assert abs(s.velocity_x_norm_per_sec - expected_vx) < _ZERO_TOL
    assert abs(subjects[0].subject_center_x_normalized - _X_BEFORE_LEFT) < _SETTLE_TOL
    # Last emitted x is at frame_idx=19, t=0.95s, so x = 0.3 + 0.4*0.95 = 0.68
    expected_last_x = _X_BEFORE_LEFT + expected_vx * (
        (_EXPECTED_FRAMES_1S_AT_20HZ - 1) * _DT_20HZ_SEC
    )
    assert abs(subjects[-1].subject_center_x_normalized - expected_last_x) < _SETTLE_TOL


def test_dwell_then_walk_holds_then_walks() -> None:
    subjects = dwell_then_walk(
        dwell_sec=_DWELL_HALF_SEC,
        walk_vx=_WALK_VX_TENTH,
        dt_sec=_DT_20HZ_SEC,
        total_sec=_TOTAL_SEC_1P5S,
    )
    expected_total_frames = int(_TOTAL_SEC_1P5S / _DT_20HZ_SEC)
    assert len(subjects) == expected_total_frames
    # Dwell phase: x=0.5, vx=0.0
    for s in subjects[:_EXPECTED_DWELL_FRAMES]:
        assert s.subject_center_x_normalized == _X_CENTER
        assert s.velocity_x_norm_per_sec == 0.0
    # Walk phase: vx=walk_vx, x increases monotonically
    walk_frames = subjects[_EXPECTED_DWELL_FRAMES:]
    for s in walk_frames:
        assert s.velocity_x_norm_per_sec == _WALK_VX_TENTH
    walk_x = [s.subject_center_x_normalized for s in walk_frames]
    assert all(b > a for a, b in pairwise(walk_x))


def test_borderline_chatter_alternates_sign() -> None:
    subjects = borderline_chatter(
        vx_amplitude=_CHATTER_AMPLITUDE,
        dt_sec=_DT_30HZ_SEC,
        total_sec=_TOTAL_SEC_1S,
    )
    expected_n = int(_TOTAL_SEC_1S / _DT_30HZ_SEC)
    assert len(subjects) == expected_n
    # All |vx| == amplitude
    for s in subjects:
        assert abs(abs(s.velocity_x_norm_per_sec) - _CHATTER_AMPLITUDE) < _ZERO_TOL
        assert abs(s.subject_center_x_normalized - _X_CENTER) < _ZERO_TOL
    # Strict alternation: even indices positive, odd indices negative
    for idx, s in enumerate(subjects):
        if idx % 2 == 0:
            assert s.velocity_x_norm_per_sec > 0.0
        else:
            assert s.velocity_x_norm_per_sec < 0.0


@pytest.mark.parametrize(
    "helper_name",
    ["step", "ramp", "dwell_then_walk", "borderline_chatter"],
)
def test_helpers_reject_invalid_dt(helper_name: str) -> None:
    """Every helper raises ValueError on dt_sec=0.0."""
    if helper_name == "step":
        with pytest.raises(ValueError, match="dt_sec"):
            step(
                t_step_sec=0.5,
                dt_sec=0.0,
                total_sec=_TOTAL_SEC_1S,
                x_before=_X_BEFORE_LEFT,
                x_after=_X_AFTER_RIGHT,
            )
    elif helper_name == "ramp":
        with pytest.raises(ValueError, match="dt_sec"):
            ramp(
                x_start=_X_BEFORE_LEFT,
                x_end=_X_AFTER_RIGHT,
                dt_sec=0.0,
                total_sec=_TOTAL_SEC_1S,
            )
    elif helper_name == "dwell_then_walk":
        with pytest.raises(ValueError, match="dt_sec"):
            dwell_then_walk(
                dwell_sec=_DWELL_HALF_SEC,
                walk_vx=_WALK_VX_TENTH,
                dt_sec=0.0,
                total_sec=_TOTAL_SEC_1P5S,
            )
    else:
        with pytest.raises(ValueError, match="dt_sec"):
            borderline_chatter(
                vx_amplitude=_CHATTER_AMPLITUDE,
                dt_sec=0.0,
                total_sec=_TOTAL_SEC_1S,
            )


@pytest.mark.parametrize(
    "helper_name",
    ["step", "ramp", "dwell_then_walk", "borderline_chatter"],
)
def test_helpers_reject_invalid_total(helper_name: str) -> None:
    """Every helper raises ValueError on total_sec=-1.0."""
    if helper_name == "step":
        with pytest.raises(ValueError, match="total_sec"):
            step(
                t_step_sec=0.5,
                dt_sec=_DT_20HZ_SEC,
                total_sec=-1.0,
                x_before=_X_BEFORE_LEFT,
                x_after=_X_AFTER_RIGHT,
            )
    elif helper_name == "ramp":
        with pytest.raises(ValueError, match="total_sec"):
            ramp(
                x_start=_X_BEFORE_LEFT,
                x_end=_X_AFTER_RIGHT,
                dt_sec=_DT_20HZ_SEC,
                total_sec=-1.0,
            )
    elif helper_name == "dwell_then_walk":
        with pytest.raises(ValueError, match="total_sec"):
            dwell_then_walk(
                dwell_sec=_DWELL_HALF_SEC,
                walk_vx=_WALK_VX_TENTH,
                dt_sec=_DT_20HZ_SEC,
                total_sec=-1.0,
            )
    else:
        with pytest.raises(ValueError, match="total_sec"):
            borderline_chatter(
                vx_amplitude=_CHATTER_AMPLITUDE,
                dt_sec=_DT_20HZ_SEC,
                total_sec=-1.0,
            )


def test_step_rejects_out_of_range_x() -> None:
    with pytest.raises(ValueError, match="x_before"):
        step(
            t_step_sec=_T_STEP_HALF_SEC,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1S,
            x_before=-0.1,
            x_after=_X_AFTER_RIGHT,
        )


def test_borderline_chatter_rejects_zero_amplitude() -> None:
    with pytest.raises(ValueError, match="vx_amplitude"):
        borderline_chatter(
            vx_amplitude=0.0,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1S,
        )


@pytest.mark.parametrize(
    "helper_name",
    ["step", "ramp", "dwell_then_walk", "borderline_chatter"],
)
def test_monotonic_timestamps(helper_name: str) -> None:
    """Every helper emits strictly monotonically increasing timestamp_ns."""
    if helper_name == "step":
        subjects = step(
            t_step_sec=_T_STEP_HALF_SEC,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1S,
            x_before=_X_BEFORE_LEFT,
            x_after=_X_AFTER_RIGHT,
        )
    elif helper_name == "ramp":
        subjects = ramp(
            x_start=_X_BEFORE_LEFT,
            x_end=_X_AFTER_RIGHT,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1S,
        )
    elif helper_name == "dwell_then_walk":
        subjects = dwell_then_walk(
            dwell_sec=_DWELL_HALF_SEC,
            walk_vx=_WALK_VX_TENTH,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1P5S,
        )
    else:
        subjects = borderline_chatter(
            vx_amplitude=_CHATTER_AMPLITUDE,
            dt_sec=_DT_20HZ_SEC,
            total_sec=_TOTAL_SEC_1S,
        )
    timestamps = [s.timestamp_ns for s in subjects]
    assert all(b > a for a, b in pairwise(timestamps))
    assert timestamps[0] >= 1_000_000_000  # default t0_ns
