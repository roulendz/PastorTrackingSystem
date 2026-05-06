"""CTRL-01 / CTRL-02 / CTRL-03 / TEST-03 PanController tests.

Drives real PanController with FramingTarget sequences. Real
CriticallyDampedFollower + real ``core.geometry.normalized_x_to_angle_deg``
(no mocks per CLAUDE.md TEST-05).

Step-response invariants mirror tests/test_damping.py:42-68.
D-12: every numeric flows from ``valid_config_dict``; no hardcoded
Phase-5 thresholds in test bodies.

D-09 anti-windup proven OBSERVABLY (no private-state inspection):
three successive over-velocity emitted-angle deltas under sustained
over-target drive each equal ``pan_max_velocity_deg_per_sec * dt_sec``
within float tolerance. Without the FollowerState.position overwrite
the second/third deltas would shrink because the damper would
integrate from the unclamped position.
"""
from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.control import PanController
from pastor_tracker.core.geometry import normalized_x_to_angle_deg
from pastor_tracker.core.types import FramingTarget

_OVERSHOOT_TOL: Final[float] = 1e-9
_MONOTONIC_TOL: Final[float] = 1e-9
_SETTLED_TOL_FRACTION: Final[float] = 0.05
_SETTLE_TAU_MULTIPLE: Final[int] = 5
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_DT_30HZ_NS: Final[int] = int(_DT_30HZ_SEC * 1_000_000_000)
_T0_NS: Final[int] = 1_000_000_000
_APPROX_TOL: Final[float] = 1e-9
_CLAMP_VERIFICATION_TOL: Final[float] = 1e-9
# 0.001 nx at fov=70 -> ~0.04 deg < default deadband (0.4 deg)
_TINY_NX_NUDGE: Final[float] = 0.001
# Override values used to isolate clamp / deadband behaviour.
# Values are TEST-LOCAL knobs (not Config defaults) -- explicit so the
# anti-windup proof is trivially reproducible by hand:
#   max per-step delta = 5 deg/s * (1/30) s = 0.1666... deg
_CLAMP_VMAX_OVERRIDE: Final[float] = 5.0
_CLAMP_DEADBAND_OVERRIDE: Final[float] = 0.0
# Config caps pan_max_velocity_deg_per_sec at 360 deg/s; this maximum
# disables the clamp at fov-scale targets (~16 deg) for the step-response test.
_STEP_RESPONSE_VMAX_OVERRIDE: Final[float] = 360.0
_OVER_VELOCITY_STEP_COUNT: Final[int] = 3
_DEADBAND_HORIZON_FRAMES: Final[int] = 10
_DEADBAND_DAMPER_HORIZON_FRAMES: Final[int] = 60
_VELOCITY_CLAMP_HORIZON_FRAMES: Final[int] = 90  # 3 s @ 30 Hz
_DEADBAND_TARGET_NX: Final[float] = 0.7  # ~13 deg @ fov=70 -- well above deadband


def _make_pan_controller(valid_config_dict: dict[str, object]) -> PanController:
    return PanController(Config(**valid_config_dict))  # type: ignore[arg-type]


def _ft(nx: float, ts_ns: int) -> FramingTarget:
    return FramingTarget(target_x_normalized=nx, timestamp_ns=ts_ns)


# ---------------------------------------------------------------------------
# Initial / FOV bridge / hold-on-None
# ---------------------------------------------------------------------------


def test_initial_angle_is_none(valid_config_dict: dict[str, object]) -> None:
    controller = _make_pan_controller(valid_config_dict)
    assert controller.current_angle_deg is None


def test_none_target_pre_seed_returns_none(
    valid_config_dict: dict[str, object],
) -> None:
    controller = _make_pan_controller(valid_config_dict)
    result = asyncio.run(controller.consume(None, now_ns=_T0_NS))
    assert result is None
    assert controller.current_angle_deg is None


def test_center_target_maps_to_zero_angle(
    valid_config_dict: dict[str, object],
) -> None:
    """FOV bridge: nx=0.5 -> 0 deg (D-08 conversion before damping)."""
    controller = _make_pan_controller(valid_config_dict)
    result = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert result is not None
    assert abs(result) < _APPROX_TOL


def test_left_third_maps_to_negative_angle(
    valid_config_dict: dict[str, object],
) -> None:
    """nx=1/3 maps to a negative angle whose magnitude matches the FOV bridge."""
    controller = _make_pan_controller(valid_config_dict)
    fov = float(valid_config_dict["camera_horizontal_fov_deg"])
    nx = 1.0 / 3.0
    expected = normalized_x_to_angle_deg(nx, fov)
    assert expected < 0.0
    result = asyncio.run(controller.consume(_ft(nx, _T0_NS), now_ns=_T0_NS))
    assert result is not None
    assert abs(result - expected) < _APPROX_TOL


def test_right_third_maps_to_positive_angle(
    valid_config_dict: dict[str, object],
) -> None:
    """nx=2/3 maps to a positive angle; symmetric to left-third test."""
    controller = _make_pan_controller(valid_config_dict)
    fov = float(valid_config_dict["camera_horizontal_fov_deg"])
    nx = 2.0 / 3.0
    expected = normalized_x_to_angle_deg(nx, fov)
    assert expected > 0.0
    result = asyncio.run(controller.consume(_ft(nx, _T0_NS), now_ns=_T0_NS))
    assert result is not None
    assert abs(result - expected) < _APPROX_TOL


# ---------------------------------------------------------------------------
# Step response (mirrors test_damping.py:42-68 in DEGREE DOMAIN)
# ---------------------------------------------------------------------------


def test_step_response_no_overshoot(
    valid_config_dict: dict[str, object],
) -> None:
    """OVERSHOOT / MONOTONIC / SETTLED: damper invariant in degree domain.

    Velocity clamp is disabled (vmax overridden to a huge value) so this
    proves the DAMPER's behaviour, not the clamp's.
    """
    cfg = dict(valid_config_dict)
    cfg["pan_max_velocity_deg_per_sec"] = _STEP_RESPONSE_VMAX_OVERRIDE
    controller = _make_pan_controller(cfg)
    tau = float(cfg["pan_time_constant_sec"])
    fov = float(cfg["camera_horizontal_fov_deg"])
    seed = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert seed is not None and abs(seed) < _APPROX_TOL
    target_nx = 2.0 / 3.0
    target_angle = normalized_x_to_angle_deg(target_nx, fov)
    horizon = int(_SETTLE_TAU_MULTIPLE * tau / _DT_30HZ_SEC)
    positions: list[float] = [seed]
    for k in range(1, horizon + 1):
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        result = asyncio.run(
            controller.consume(_ft(target_nx, ts_ns), now_ns=ts_ns),
        )
        assert result is not None
        positions.append(result)
    # 1) No overshoot.
    assert max(positions) <= target_angle + _OVERSHOOT_TOL, (
        f"overshoot: max={max(positions)} target={target_angle}"
    )
    # 2) Monotonic non-decreasing (critical damping from rest below target).
    diffs = [b - a for a, b in pairwise(positions)]
    assert all(d >= -_MONOTONIC_TOL for d in diffs), (
        f"non-monotonic: min_diff={min(diffs)}"
    )
    # 3) Settled to within 5% of target by 5*tau.
    assert abs(positions[-1] - target_angle) < (
        _SETTLED_TOL_FRACTION * abs(target_angle)
    ), f"slow convergence: pos@5tau={positions[-1]} target={target_angle}"


# ---------------------------------------------------------------------------
# Velocity clamp (CTRL-03 / D-09)
# ---------------------------------------------------------------------------


def test_velocity_clamp_caps_step_size(
    valid_config_dict: dict[str, object],
) -> None:
    """Per-step |delta| bounded by ``vmax * dt`` over a sustained ramp."""
    cfg = dict(valid_config_dict)
    cfg["pan_max_velocity_deg_per_sec"] = _CLAMP_VMAX_OVERRIDE
    cfg["pan_deadband_deg"] = _CLAMP_DEADBAND_OVERRIDE
    controller = _make_pan_controller(cfg)
    vmax = float(cfg["pan_max_velocity_deg_per_sec"])
    seed = asyncio.run(controller.consume(_ft(0.0, _T0_NS), now_ns=_T0_NS))
    assert seed is not None
    positions: list[float] = [seed]
    for k in range(1, _VELOCITY_CLAMP_HORIZON_FRAMES + 1):
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        result = asyncio.run(controller.consume(_ft(1.0, ts_ns), now_ns=ts_ns))
        assert result is not None
        positions.append(result)
    # The controller derives dt from int-ns deltas; reproduce exactly.
    actual_dt_sec = float(_DT_30HZ_NS) / 1_000_000_000.0
    max_per_step = vmax * actual_dt_sec + _CLAMP_VERIFICATION_TOL
    deltas = [abs(b - a) for a, b in pairwise(positions)]
    assert max(deltas) <= max_per_step, (
        f"clamp violated: max_delta={max(deltas)} > {max_per_step}"
    )


def test_velocity_clamp_overwrites_state(
    valid_config_dict: dict[str, object],
) -> None:
    """Anti-windup proven OBSERVABLY (Issue 8 / D-09).

    Drive 3 successive over-velocity steps with deadband=0. Without the
    anti-windup overwrite, the damper would integrate from the unclamped
    position and the second/third deltas would shrink as the damper
    "thinks" it is closer to the target than it really is. With the
    overwrite, the damper restarts each step from the CLAMPED position,
    so under sustained over-target drive every emitted-step delta equals
    ``pan_max_velocity_deg_per_sec * dt_sec`` within float tolerance.
    """
    cfg = dict(valid_config_dict)
    cfg["pan_max_velocity_deg_per_sec"] = _CLAMP_VMAX_OVERRIDE
    cfg["pan_deadband_deg"] = _CLAMP_DEADBAND_OVERRIDE
    controller = _make_pan_controller(cfg)
    vmax = float(cfg["pan_max_velocity_deg_per_sec"])
    # Seed at nx=0.0 (= -fov/2). Drive 3 frames at nx=1.0 (= +fov/2).
    # Span = fov degrees, far enough that vmax*dt clamp engages every step.
    seed = asyncio.run(controller.consume(_ft(0.0, _T0_NS), now_ns=_T0_NS))
    assert seed is not None
    emitted: list[float] = [seed]
    for k in range(1, _OVER_VELOCITY_STEP_COUNT + 1):
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        result = asyncio.run(controller.consume(_ft(1.0, ts_ns), now_ns=ts_ns))
        assert result is not None
        emitted.append(result)
    # Reproduce the controller's internal dt -- it is derived from integer ns
    # deltas (``float(delta_ns) / 1e9``), which is not exactly equal to 1/30.
    # Comparing against vmax * (1/30) would fail by ~1.6e-9 of float drift.
    actual_dt_sec = float(_DT_30HZ_NS) / 1_000_000_000.0
    expected_step = vmax * actual_dt_sec
    deltas = [b - a for a, b in pairwise(emitted)]
    assert len(deltas) == _OVER_VELOCITY_STEP_COUNT
    for i, d in enumerate(deltas):
        assert abs(d - expected_step) < _CLAMP_VERIFICATION_TOL, (
            f"step {i} delta {d} != expected {expected_step} (within "
            f"{_CLAMP_VERIFICATION_TOL}); without anti-windup overwrite, "
            f"successive deltas would shrink as damper integrates from "
            f"unclamped position"
        )


# ---------------------------------------------------------------------------
# Emission deadband (CTRL-02 / D-10 / Pitfall 3)
# ---------------------------------------------------------------------------


def test_deadband_suppresses_small_changes(
    valid_config_dict: dict[str, object],
) -> None:
    """Sub-deadband nudges produce no change in emitted angle."""
    controller = _make_pan_controller(valid_config_dict)
    deadband = float(valid_config_dict["pan_deadband_deg"])
    fov = float(valid_config_dict["camera_horizontal_fov_deg"])
    first = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert first is not None
    tiny_nx = 0.5 + _TINY_NX_NUDGE
    tiny_angle_delta = abs(
        normalized_x_to_angle_deg(tiny_nx, fov)
        - normalized_x_to_angle_deg(0.5, fov)
    )
    # Sanity: the chosen nudge must be inside deadband to exercise the suppress branch.
    assert tiny_angle_delta < deadband
    for k in range(1, _DEADBAND_HORIZON_FRAMES + 1):
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        result = asyncio.run(controller.consume(_ft(tiny_nx, ts_ns), now_ns=ts_ns))
        assert result is not None
        assert abs(result - first) < _APPROX_TOL


def test_deadband_does_not_freeze_damper(
    valid_config_dict: dict[str, object],
) -> None:
    """Pitfall 3: damper continues stepping even while emission is gated.

    Drive a moderate target step (well above deadband). After enough frames
    for the damper to settle, emission should track the damper position --
    proving the damper integrated through the entire run.
    """
    controller = _make_pan_controller(valid_config_dict)
    deadband = float(valid_config_dict["pan_deadband_deg"])
    fov = float(valid_config_dict["camera_horizontal_fov_deg"])
    asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    target_angle = normalized_x_to_angle_deg(_DEADBAND_TARGET_NX, fov)
    last_emit = 0.0
    for k in range(1, _DEADBAND_DAMPER_HORIZON_FRAMES + 1):
        ts_ns = _T0_NS + k * _DT_30HZ_NS
        result = asyncio.run(
            controller.consume(_ft(_DEADBAND_TARGET_NX, ts_ns), now_ns=ts_ns),
        )
        assert result is not None
        last_emit = result
    # Settled emission should be within deadband of target -- proves damper
    # integrated cumulatively even though many intermediate emits were gated.
    assert abs(last_emit - target_angle) < deadband, (
        f"damper failed to track: last_emit={last_emit} target={target_angle} "
        f"(diff {abs(last_emit - target_angle)} >= deadband {deadband})"
    )


# ---------------------------------------------------------------------------
# Hold-on-None (D-07 -- preserves state, no damper drift)
# ---------------------------------------------------------------------------


def test_hold_during_none_target(
    valid_config_dict: dict[str, object],
) -> None:
    """target=None returns the held last-emitted angle (D-07 truth)."""
    controller = _make_pan_controller(valid_config_dict)
    seed = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert seed is not None and abs(seed) < _APPROX_TOL
    held = asyncio.run(controller.consume(None, now_ns=_T0_NS + _DT_30HZ_NS))
    assert held is not None
    assert abs(held - seed) < _APPROX_TOL
    assert controller.current_angle_deg is not None
    assert abs(controller.current_angle_deg - seed) < _APPROX_TOL


def test_hold_does_not_advance_damper(
    valid_config_dict: dict[str, object],
) -> None:
    """D-07: hold ticks must NOT drift the damper FollowerState.

    After several None-upstream ticks, feeding the same FramingTarget back
    should return an emission essentially identical to the seed -- proving
    the damper FollowerState was held in place across the gap.
    """
    controller = _make_pan_controller(valid_config_dict)
    seed = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert seed is not None
    # 5 None-upstream ticks
    for k in range(1, 6):
        asyncio.run(controller.consume(None, now_ns=_T0_NS + k * _DT_30HZ_NS))
    # Resume with the SAME target -- emission should still be at the seed angle.
    resume_ts = _T0_NS + 6 * _DT_30HZ_NS
    resumed = asyncio.run(controller.consume(_ft(0.5, resume_ts), now_ns=resume_ts))
    assert resumed is not None
    assert abs(resumed - seed) < _APPROX_TOL


def test_long_none_gap_then_new_target_does_not_snap(
    valid_config_dict: dict[str, object],
) -> None:
    """BL-01 regression: long None gap must not produce a single-frame FOV jump.

    Before the fix, ``_hold()`` retained ``_last_upstream_ts_ns`` across the
    gap; the next real frame computed ``dt = T_gap`` (~5 s here), which both
    collapsed the damper decay (snap to target) AND inflated the velocity
    clamp ceiling beyond the FOV (clamp never engages). Result: a one-frame
    full-FOV jump from the seed angle to ~+fov/2.

    With the fix, ``_hold()`` clears ``_last_upstream_ts_ns``; the resume
    frame uses the ``1 / capture_fps`` floor for ``dt``, so the per-step
    angle delta is bounded by ``vmax * dt_floor``.
    """
    controller = _make_pan_controller(valid_config_dict)
    seed = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert seed is not None
    # 5-second None gap -- before the fix the dt on resume would be ~5 s.
    long_gap_ts = _T0_NS + 5 * 1_000_000_000
    asyncio.run(controller.consume(None, now_ns=long_gap_ts))
    # New target at the opposite edge of the normalized range.
    resume_ts = long_gap_ts + _DT_30HZ_NS
    resumed = asyncio.run(controller.consume(_ft(1.0, resume_ts), now_ns=resume_ts))
    assert resumed is not None
    vmax = float(valid_config_dict["pan_max_velocity_deg_per_sec"])
    capture_fps = float(valid_config_dict["capture_fps"])
    # Floor dt is exactly 1 / capture_fps (see _compute_dt_sec).
    dt_floor_sec = 1.0 / capture_fps
    max_one_step_jump = vmax * dt_floor_sec + _CLAMP_VERIFICATION_TOL
    assert abs(resumed - seed) <= max_one_step_jump, (
        f"jump {abs(resumed - seed)} after 5s None gap exceeds vmax*dt_floor "
        f"{max_one_step_jump} -- BL-01 fix regressed"
    )


# ---------------------------------------------------------------------------
# dt floor (non-monotonic upstream)
# ---------------------------------------------------------------------------


def test_dt_floor_when_upstream_non_monotonic(
    valid_config_dict: dict[str, object],
) -> None:
    """Out-of-order upstream timestamps must NOT raise -- dt floors to 1/fps."""
    controller = _make_pan_controller(valid_config_dict)
    asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    # Same-or-earlier timestamp (delta_ns <= 0). No ValueError = success.
    result = asyncio.run(
        controller.consume(_ft(0.6, _T0_NS - 1_000_000), now_ns=_T0_NS),
    )
    assert result is not None


# ---------------------------------------------------------------------------
# Logger events (Pattern 9)
# ---------------------------------------------------------------------------


def test_seed_logs_pan_controller_seeded(
    valid_config_dict: dict[str, object],
) -> None:
    controller = _make_pan_controller(valid_config_dict)
    fov = float(valid_config_dict["camera_horizontal_fov_deg"])
    expected_angle = normalized_x_to_angle_deg(0.5, fov)
    with structlog.testing.capture_logs() as caplog:
        asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    seed_events = [e for e in caplog if e.get("event") == "pan_controller_seeded"]
    assert len(seed_events) == 1
    assert abs(seed_events[0]["angle_deg"] - expected_angle) < _APPROX_TOL


def test_clamp_logs_pan_clamped(
    valid_config_dict: dict[str, object],
) -> None:
    """Over-velocity drive emits at least one ``pan_clamped`` DEBUG event with all fields."""
    cfg = dict(valid_config_dict)
    cfg["pan_max_velocity_deg_per_sec"] = _CLAMP_VMAX_OVERRIDE
    cfg["pan_deadband_deg"] = _CLAMP_DEADBAND_OVERRIDE
    controller = _make_pan_controller(cfg)
    asyncio.run(controller.consume(_ft(0.0, _T0_NS), now_ns=_T0_NS))
    with structlog.testing.capture_logs() as caplog:
        ts = _T0_NS + _DT_30HZ_NS
        asyncio.run(controller.consume(_ft(1.0, ts), now_ns=ts))
    clamp_events = [e for e in caplog if e.get("event") == "pan_clamped"]
    assert len(clamp_events) >= 1
    event = clamp_events[0]
    assert "unclamped_delta_deg" in event
    assert "clamped_delta_deg" in event
    assert "dt_sec" in event


def test_deadband_logs_pan_deadband_suppressed(
    valid_config_dict: dict[str, object],
) -> None:
    """Sub-deadband nudge emits a ``pan_deadband_suppressed`` DEBUG event."""
    controller = _make_pan_controller(valid_config_dict)
    asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    tiny_nx = 0.5 + _TINY_NX_NUDGE
    with structlog.testing.capture_logs() as caplog:
        ts = _T0_NS + _DT_30HZ_NS
        asyncio.run(controller.consume(_ft(tiny_nx, ts), now_ns=ts))
    suppress_events = [
        e for e in caplog if e.get("event") == "pan_deadband_suppressed"
    ]
    assert len(suppress_events) >= 1
    event = suppress_events[0]
    assert "delta_deg" in event
    assert "last_emitted_angle_deg" in event
