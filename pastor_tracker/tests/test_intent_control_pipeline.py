"""Phase 5 composition smoke tests (TEST-03 cross-stage portion).

Drives all four Phase 5 stages (MotionAnalyzer + Framer + PanController +
CommandDispatcher) end-to-end through scripted trajectories from
tests.fixtures.trajectories. Same-thread, no orchestrator (Phase 6 owns
that), no real Arduino motor.

Mirrors the Phase-6 orchestrator-style invocation pattern (per CONTEXT.md
Pipeline Composition Expectation):
    ms = await analyzer.consume(subject, now_ns)
    ft = await framer.consume(ms, now_ns)
    ang = await controller.consume(ft, now_ns)
    cmd = dispatcher.decide(ang, now_ns)

Cross-stage invariants asserted:
- borderline chatter -> ZERO dispatcher emissions (no thrash propagation)
- dwell_then_walk -> framer target progresses center -> third
- per-step controller delta <= pan_max_velocity_deg_per_sec * dt
- emission count <= analytic upper bound (Open Question 3 + Plan 05 Issue 9)
- None upstream propagates cleanly across all four stages

D-12: every numeric flows from valid_config_dict; no hardcoded thresholds
in test bodies. CLAUDE.md TEST-05: zero mocks; every stage runs real code.
"""
from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import Final

from pastor_tracker.config import Config
from pastor_tracker.control import CommandDispatcher, PanController
from pastor_tracker.core.types import (
    FramingTarget,
    MotionState,
    MotorCommand,
    TrackedSubject,
)
from pastor_tracker.intent import Framer, MotionAnalyzer
from tests.fixtures.trajectories import (
    borderline_chatter,
    dwell_then_walk,
    ramp,
)

# Module-level test constants (NOT Config-owned)
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_DT_30HZ_NS: Final[int] = int(_DT_30HZ_SEC * 1_000_000_000)
_T0_NS: Final[int] = 1_000_000_000
_CLAMP_VERIFICATION_TOL: Final[float] = 1e-9
_BORDERLINE_FACTOR: Final[float] = 1.05
_ABOVE_THRESHOLD_FACTOR: Final[float] = 2.0
_DWELL_MULTIPLIER: Final[float] = 2.0
_CHATTER_RUN_SEC: Final[float] = 5.0
_HYSTERESIS_MARGIN_FACTOR: Final[float] = 2.0
_FRAMING_SETTLE_FACTOR: Final[float] = 5.0
_PRE_SEED_VX_FACTOR: Final[float] = 3.0
_X_CENTER: Final[float] = 0.5
_X_UPPER_CAP: Final[float] = 0.99
_X_WALK_SAFETY_MARGIN: Final[float] = 0.1
_EMISSION_BOUND_SLACK: Final[int] = 2
_MS_PER_SEC: Final[int] = 1000


def _make_pipeline(
    valid_config_dict: dict[str, object],
) -> tuple[MotionAnalyzer, Framer, PanController, CommandDispatcher]:
    """Build all four Phase 5 stages from a SINGLE Config (T-05-06-04 mitigation)."""
    cfg = Config(**valid_config_dict)  # type: ignore[arg-type]
    return (
        MotionAnalyzer(cfg),
        Framer(cfg),
        PanController(cfg),
        CommandDispatcher(cfg),
    )


def _drive_pipeline(
    analyzer: MotionAnalyzer,
    framer: Framer,
    controller: PanController,
    dispatcher: CommandDispatcher,
    trajectory: list[TrackedSubject],
) -> tuple[list[float | None], list[MotorCommand | None]]:
    """Run a scripted trajectory through all four stages (Pitfall 5 parity).

    Returns (controller_angles_per_frame, dispatcher_cmds_per_frame).
    now_ns for each tick is the upstream subject's timestamp_ns -- the same
    value passed to all four stages on a single tick (T-05-06-01 mitigation).
    """
    controller_angles: list[float | None] = []
    cmds: list[MotorCommand | None] = []
    for subject in trajectory:
        now_ns = subject.timestamp_ns
        ms: MotionState | None = asyncio.run(
            analyzer.consume(subject, now_ns=now_ns),
        )
        ft: FramingTarget | None = asyncio.run(
            framer.consume(ms, now_ns=now_ns),
        )
        ang: float | None = asyncio.run(
            controller.consume(ft, now_ns=now_ns),
        )
        controller_angles.append(ang)
        cmd: MotorCommand | None = dispatcher.decide(ang, now_ns=now_ns)
        cmds.append(cmd)
    return controller_angles, cmds


# ---------- tests ----------


def test_pipeline_constructible_from_single_config(
    valid_config_dict: dict[str, object],
) -> None:
    """All four stages build from one Config and expose initial-state properties."""
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    assert analyzer.current_intent == "indeterminate"
    assert framer.current_target_x_normalized is None
    assert controller.current_angle_deg is None
    assert dispatcher.last_emitted_angle_deg is None
    assert dispatcher.last_emit_ts_ns is None


def test_borderline_chatter_produces_no_emissions_after_seed(
    valid_config_dict: dict[str, object],
) -> None:
    """Borderline-vx chatter must NOT produce dispatcher emissions (T-05-06-02)."""
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    trajectory = borderline_chatter(
        vx_amplitude=threshold * _BORDERLINE_FACTOR,
        dt_sec=_DT_30HZ_SEC,
        total_sec=_CHATTER_RUN_SEC,
    )
    _, cmds = _drive_pipeline(analyzer, framer, controller, dispatcher, trajectory)
    # Analyzer never flipped -> framer never seeded -> controller never seeded
    # -> dispatcher saw only None angles -> zero emissions ever.
    assert all(c is None for c in cmds)
    assert analyzer.current_intent == "indeterminate"
    assert framer.current_target_x_normalized is None
    assert controller.current_angle_deg is None
    assert dispatcher.last_emitted_angle_deg is None


def test_dwell_then_walk_produces_expected_target_progression(
    valid_config_dict: dict[str, object],
) -> None:
    """dwell_then_walk drives framer target from center toward left third."""
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    dwell_duration = float(valid_config_dict["dwell_duration_sec"])
    hysteresis = float(valid_config_dict["motion_hysteresis_sec"])
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    framing_tau = float(valid_config_dict["framing_time_constant_sec"])
    dwell_sec = _DWELL_MULTIPLIER * dwell_duration
    walk_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    # Walk just long enough for hysteresis to flip + framer damper to begin moving.
    # Cap walk so x stays in [0, 1]: max_walk_sec = (1 - 0.5) / walk_vx - margin.
    walk_sec = min(
        _HYSTERESIS_MARGIN_FACTOR * hysteresis + _FRAMING_SETTLE_FACTOR * framing_tau,
        (1.0 - _X_CENTER) / walk_vx - _X_WALK_SAFETY_MARGIN,
    )
    total_sec = dwell_sec + walk_sec
    trajectory = dwell_then_walk(
        dwell_sec=dwell_sec,
        walk_vx=walk_vx,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    _drive_pipeline(analyzer, framer, controller, dispatcher, trajectory)
    # After the walk window (post-hysteresis), intent should be moving_right
    # and the framer's current target should be approaching 1/3 (left third).
    assert analyzer.current_intent == "moving_right"
    assert framer.current_target_x_normalized is not None
    # Damped target moves from 0.5 toward 1/3; assert it has moved past center.
    assert framer.current_target_x_normalized < _X_CENTER


def test_velocity_clamp_holds_across_pipeline(
    valid_config_dict: dict[str, object],
) -> None:
    """Per-step controller delta never exceeds vmax * dt (T-05-06-03).

    Uses ``dwell_then_walk`` rather than ``ramp`` so the framer target
    actually MOVES during the test (dwell -> moving_right transitions the
    framer from center 0.5 to left-third 1/3 = ~11.66 deg on FOV=70). With
    a pure ``ramp`` the analyzer flips to moving_right on frame 0 and the
    framer's D-06 at-target seed pins the controller at the left-third
    angle for the entire run -- the invariant would hold vacuously. The
    dwell phase forces a center seed, then the walk phase drives a real
    target transition through the stage-1 + stage-2 dampers, exercising
    the controller's velocity clamp on every post-flip frame.
    """
    # Deadband disabled to isolate the clamp invariant from emission gating.
    cfg_dict = dict(valid_config_dict)
    cfg_dict["pan_deadband_deg"] = 0.0
    analyzer, framer, controller, dispatcher = _make_pipeline(cfg_dict)
    dwell_duration = float(cfg_dict["dwell_duration_sec"])
    threshold = float(cfg_dict["motion_threshold_norm_per_sec"])
    hysteresis = float(cfg_dict["motion_hysteresis_sec"])
    framing_tau = float(cfg_dict["framing_time_constant_sec"])
    pan_tau = float(cfg_dict["pan_time_constant_sec"])
    vmax = float(cfg_dict["pan_max_velocity_deg_per_sec"])
    dwell_sec = _DWELL_MULTIPLIER * dwell_duration
    walk_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    walk_sec = min(
        _HYSTERESIS_MARGIN_FACTOR * hysteresis + _FRAMING_SETTLE_FACTOR * (
            framing_tau + pan_tau
        ),
        (1.0 - _X_CENTER) / walk_vx - _X_WALK_SAFETY_MARGIN,
    )
    total_sec = dwell_sec + walk_sec
    trajectory = dwell_then_walk(
        dwell_sec=dwell_sec,
        walk_vx=walk_vx,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    controller_angles, _ = _drive_pipeline(
        analyzer, framer, controller, dispatcher, trajectory,
    )
    # Filter out None entries (frames before framer seeded).
    non_none_angles = [a for a in controller_angles if a is not None]
    assert len(non_none_angles) > 0
    max_per_step = vmax * _DT_30HZ_SEC + _CLAMP_VERIFICATION_TOL
    deltas = [abs(b - a) for a, b in pairwise(non_none_angles)]
    assert max(deltas) <= max_per_step, (
        f"max controller per-step delta {max(deltas)} exceeds "
        f"vmax*dt {max_per_step}"
    )


def test_emission_count_bounded_over_dwell_then_walk(
    valid_config_dict: dict[str, object],
) -> None:
    """Dispatcher emissions <= floor(T*1000/min_interval_ms) + 2 (Plan 05 Issue 9)."""
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    dwell_duration = float(valid_config_dict["dwell_duration_sec"])
    hysteresis = float(valid_config_dict["motion_hysteresis_sec"])
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    framing_tau = float(valid_config_dict["framing_time_constant_sec"])
    min_interval_ms = int(valid_config_dict["command_min_interval_ms"])
    assert min_interval_ms > 0  # Issue 12 defensive precondition
    dwell_sec = _DWELL_MULTIPLIER * dwell_duration
    walk_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    walk_sec = min(
        _HYSTERESIS_MARGIN_FACTOR * hysteresis + 3.0 * framing_tau,
        (1.0 - _X_CENTER) / walk_vx - _X_WALK_SAFETY_MARGIN,
    )
    total_sec = dwell_sec + walk_sec
    trajectory = dwell_then_walk(
        dwell_sec=dwell_sec,
        walk_vx=walk_vx,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    _, cmds = _drive_pipeline(analyzer, framer, controller, dispatcher, trajectory)
    emissions = sum(1 for c in cmds if c is not None)
    bound = int(total_sec * _MS_PER_SEC / min_interval_ms) + _EMISSION_BOUND_SLACK
    assert emissions <= bound, (
        f"emissions {emissions} exceeds analytic bound {bound} "
        f"(total_sec={total_sec}, min_interval_ms={min_interval_ms})"
    )


def test_none_upstream_clean_propagation(
    valid_config_dict: dict[str, object],
) -> None:
    """A None subject tick propagates cleanly across all four stages."""
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    hysteresis = float(valid_config_dict["motion_hysteresis_sec"])
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    framing_tau = float(valid_config_dict["framing_time_constant_sec"])
    pan_tau = float(valid_config_dict["pan_time_constant_sec"])
    # Drive enough sustained moving_right frames to seed analyzer + framer +
    # controller AND let the dispatcher emit at least once. The interval gate
    # (command_min_interval_ms) and the framer/pan time-constants jointly
    # require more than a single hysteresis window to observe a real emission.
    # ramp() produces CONSTANT vx = (x_end - x_start) / total_sec, so total_sec
    # must be CAPPED so the resulting ramp vx > threshold (above-threshold).
    walk_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    span_max = _X_UPPER_CAP - _X_CENTER
    seed_sec_unbounded = (
        _HYSTERESIS_MARGIN_FACTOR * hysteresis
        + _FRAMING_SETTLE_FACTOR * (framing_tau + pan_tau)
    )
    seed_sec = min(
        seed_sec_unbounded, (span_max / walk_vx) - _X_WALK_SAFETY_MARGIN,
    )
    sustained = ramp(
        x_start=_X_CENTER,
        x_end=min(_X_UPPER_CAP, _X_CENTER + walk_vx * seed_sec),
        dt_sec=_DT_30HZ_SEC,
        total_sec=seed_sec,
    )
    _drive_pipeline(analyzer, framer, controller, dispatcher, sustained)
    # Snapshot post-seed state.
    pre_none_controller_angle = controller.current_angle_deg
    pre_none_dispatcher_angle = dispatcher.last_emitted_angle_deg
    pre_none_dispatcher_ts = dispatcher.last_emit_ts_ns
    assert pre_none_controller_angle is not None
    # Now feed None for one tick.
    none_now_ns = sustained[-1].timestamp_ns + _DT_30HZ_NS
    ms = asyncio.run(analyzer.consume(None, now_ns=none_now_ns))
    assert ms is not None
    assert ms.intent == "indeterminate"
    ft = asyncio.run(framer.consume(ms, now_ns=none_now_ns))
    assert ft is None
    assert framer.current_target_x_normalized is None  # framer cleared (D-07)
    ang = asyncio.run(controller.consume(ft, now_ns=none_now_ns))
    # Controller HOLDS its last_emitted_angle_deg across None upstream (D-07)
    assert ang == pre_none_controller_angle
    # Dispatcher Pitfall 7: decide(held_angle, now_ns) -- if held_angle equals
    # last emitted (it does, since controller held), dispatcher's delta gate
    # suppresses; state must remain UNCHANGED.
    cmd = dispatcher.decide(ang, now_ns=none_now_ns)
    assert cmd is None
    assert dispatcher.last_emitted_angle_deg == pre_none_dispatcher_angle
    assert dispatcher.last_emit_ts_ns == pre_none_dispatcher_ts


def test_pre_seed_pipeline_emits_correctly_first_frame(
    valid_config_dict: dict[str, object],
) -> None:
    """A single moving_right frame on a fresh pipeline emits NO motor command.

    Hysteresis duration (>0) requires multiple frames before intent flips,
    so the pipeline must NOT issue a stray motor command on the very first
    frame. Proves the four-stage pre-seed gating composes correctly.
    """
    analyzer, framer, controller, dispatcher = _make_pipeline(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    subj = TrackedSubject(
        track_id=1,
        subject_center_x_normalized=_X_CENTER,
        subject_center_y_normalized=_X_CENTER,
        velocity_x_norm_per_sec=threshold * _PRE_SEED_VX_FACTOR,
        velocity_y_norm_per_sec=0.0,
        timestamp_ns=_T0_NS,
    )
    ms = asyncio.run(analyzer.consume(subj, now_ns=_T0_NS))
    ft = asyncio.run(framer.consume(ms, now_ns=_T0_NS))
    ang = asyncio.run(controller.consume(ft, now_ns=_T0_NS))
    cmd = dispatcher.decide(ang, now_ns=_T0_NS)
    # 1 frame is insufficient to flip intent (hysteresis duration > 0).
    assert analyzer.current_intent == "indeterminate"
    # Framer holds (no target yet because intent stays indeterminate).
    assert framer.current_target_x_normalized is None
    # Controller has no FramingTarget yet -> no seed yet.
    assert controller.current_angle_deg is None
    # Dispatcher has nothing to emit.
    assert dispatcher.last_emitted_angle_deg is None
    assert cmd is None
