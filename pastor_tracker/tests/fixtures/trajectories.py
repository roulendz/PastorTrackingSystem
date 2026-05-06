"""Pure deterministic TrackedSubject sequence generators for Phase 5 unit tests.

Every Phase 5 unit test (motion_analyzer, framer, pan_controller, dispatcher,
composition smoke) consumes one of the four helpers in this module. Helpers are
parametrized — they never hardcode a Phase-5 numeric threshold (motion_threshold,
hysteresis, dwell, pan, command_*); callers pass the values pulled from
``valid_config_dict`` (or directly from a Config instance) so the same fixture
serves every threshold-sensitivity test.

Import path is ``tests.fixtures.trajectories`` -- pytest ``rootdir`` is
``pastor_tracker/`` (testpaths=["tests"], packages=["src/pastor_tracker"]).
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package. Mirrors the pose_traces.py convention.

CONTEXT D-12 (locked): helpers are parameter-driven, never Config-coupled.
"""
from __future__ import annotations

from typing import Final

from pastor_tracker.core.types import TrackedSubject

__all__ = [
    "borderline_chatter",
    "dwell_then_walk",
    "ramp",
    "step",
]

# CLAUDE.md rule 6 — only literals allowed are the unit conversions and the
# pan-only y-axis pinning. Every other numeric flows in via parameters.
_NS_PER_SEC: Final[float] = 1_000_000_000.0
_Y_CENTER: Final[float] = 0.5  # pan-only — y unused by the analyzer
_VY_ZERO: Final[float] = 0.0
_NORM_MIN: Final[float] = 0.0
_NORM_MAX: Final[float] = 1.0
_DEFAULT_T0_NS: Final[int] = 1_000_000_000
_DEFAULT_TRACK_ID: Final[int] = 1


def _validate_common(
    dt_sec: float, total_sec: float, track_id: int, t0_ns: int
) -> None:
    """Fail-loud guard for the four parameters every helper shares.

    Tiger-style (CLAUDE.md rule 1): raise ValueError naming the offending
    parameter so a caller reading the traceback knows exactly which knob was
    wrong, no silent empty-list returns.
    """
    if dt_sec <= 0.0:
        raise ValueError(f"dt_sec must be > 0.0, got dt_sec={dt_sec}")
    if total_sec <= 0.0:
        raise ValueError(f"total_sec must be > 0.0, got total_sec={total_sec}")
    if track_id < 0:
        raise ValueError(f"track_id must be >= 0, got track_id={track_id}")
    if t0_ns < 0:
        raise ValueError(f"t0_ns must be >= 0, got t0_ns={t0_ns}")


def _validate_x_range(name: str, value: float) -> None:
    """Reject normalized-x values outside [0.0, 1.0] before Pydantic does.

    Pydantic also raises (Field(ge=0.0, le=1.0)), but failing here lets the
    error message name the parameter ("x_before"), not the field
    ("subject_center_x_normalized"), which is friendlier for test authors.
    """
    if value < _NORM_MIN or value > _NORM_MAX:
        raise ValueError(
            f"{name} must be within [{_NORM_MIN}, {_NORM_MAX}], got {name}={value}"
        )


def _make_subject(
    *, x: float, vx: float, ts_ns: int, track_id: int
) -> TrackedSubject:
    """Single source of truth for TrackedSubject construction (DRY).

    Every public helper funnels through here so y/vy pinning, kw-only
    Pydantic construction, and field-name conventions live in one place.
    """
    return TrackedSubject(
        track_id=track_id,
        subject_center_x_normalized=x,
        subject_center_y_normalized=_Y_CENTER,
        velocity_x_norm_per_sec=vx,
        velocity_y_norm_per_sec=_VY_ZERO,
        timestamp_ns=ts_ns,
    )


def _clamp_norm(value: float) -> float:
    """Clamp a raw x value to the normalized [0, 1] domain."""
    return min(_NORM_MAX, max(_NORM_MIN, value))


def step(
    *,
    t_step_sec: float,
    dt_sec: float,
    total_sec: float,
    x_before: float,
    x_after: float,
    track_id: int = _DEFAULT_TRACK_ID,
    t0_ns: int = _DEFAULT_T0_NS,
) -> list[TrackedSubject]:
    """Produce a TrackedSubject sequence with a single step in x at t_step_sec.

    Frames before ``t_step_sec`` carry x=x_before, vx=0. The step frame carries
    x=x_after and vx=(x_after-x_before)/dt_sec (one-frame delta). All frames
    after the step carry x=x_after, vx=0.
    """
    _validate_common(dt_sec, total_sec, track_id, t0_ns)
    _validate_x_range("x_before", x_before)
    _validate_x_range("x_after", x_after)
    if t_step_sec < 0.0:
        raise ValueError(f"t_step_sec must be >= 0.0, got t_step_sec={t_step_sec}")
    if t_step_sec > total_sec:
        raise ValueError(
            f"t_step_sec ({t_step_sec}) must be <= total_sec ({total_sec})"
        )

    n_frames = int(total_sec / dt_sec)
    step_frame_idx = int(t_step_sec / dt_sec)
    dt_ns = int(dt_sec * _NS_PER_SEC)
    vx_step = (x_after - x_before) / dt_sec

    subjects: list[TrackedSubject] = []
    for frame_idx in range(n_frames):
        ts_ns = t0_ns + frame_idx * dt_ns
        if frame_idx < step_frame_idx:
            x_curr = x_before
            vx = 0.0
        elif frame_idx == step_frame_idx:
            x_curr = x_after
            vx = vx_step
        else:
            x_curr = x_after
            vx = 0.0
        subjects.append(
            _make_subject(x=x_curr, vx=vx, ts_ns=ts_ns, track_id=track_id)
        )
    return subjects


def ramp(
    *,
    x_start: float,
    x_end: float,
    dt_sec: float,
    total_sec: float,
    track_id: int = _DEFAULT_TRACK_ID,
    t0_ns: int = _DEFAULT_T0_NS,
) -> list[TrackedSubject]:
    """Produce a constant-velocity ramp from x_start to x_end over total_sec.

    Every emitted subject carries the same vx = (x_end - x_start) / total_sec.
    x is linearly interpolated and clamped to [0, 1].
    """
    _validate_common(dt_sec, total_sec, track_id, t0_ns)
    _validate_x_range("x_start", x_start)
    _validate_x_range("x_end", x_end)

    n_frames = int(total_sec / dt_sec)
    dt_ns = int(dt_sec * _NS_PER_SEC)
    vx_const = (x_end - x_start) / total_sec

    subjects: list[TrackedSubject] = []
    for frame_idx in range(n_frames):
        ts_ns = t0_ns + frame_idx * dt_ns
        x_raw = x_start + vx_const * (frame_idx * dt_sec)
        x_curr = _clamp_norm(x_raw)
        subjects.append(
            _make_subject(x=x_curr, vx=vx_const, ts_ns=ts_ns, track_id=track_id)
        )
    return subjects


def dwell_then_walk(
    *,
    dwell_sec: float,
    walk_vx: float,
    dt_sec: float,
    total_sec: float,
    track_id: int = _DEFAULT_TRACK_ID,
    t0_ns: int = _DEFAULT_T0_NS,
) -> list[TrackedSubject]:
    """Hold x=0.5, vx=0 for ``dwell_sec``, then walk at constant ``walk_vx``.

    Models the dwell-then-resume pattern that exercises the dwell hysteresis
    path in MotionAnalyzer.
    """
    _validate_common(dt_sec, total_sec, track_id, t0_ns)
    if dwell_sec <= 0.0:
        raise ValueError(f"dwell_sec must be > 0.0, got dwell_sec={dwell_sec}")
    if dwell_sec >= total_sec:
        raise ValueError(
            f"dwell_sec ({dwell_sec}) must be < total_sec ({total_sec}) "
            f"so the walk phase is non-empty"
        )
    if walk_vx == 0.0:
        raise ValueError("walk_vx must be != 0.0; use ramp(...) for a static fixture")

    n_frames = int(total_sec / dt_sec)
    dt_ns = int(dt_sec * _NS_PER_SEC)
    dwell_frame_count = int(dwell_sec / dt_sec)

    subjects: list[TrackedSubject] = []
    for frame_idx in range(n_frames):
        ts_ns = t0_ns + frame_idx * dt_ns
        if frame_idx < dwell_frame_count:
            x_curr = _Y_CENTER  # 0.5 — same numeric, semantically "centered"
            vx = 0.0
        else:
            time_since_walk_sec = (frame_idx - dwell_frame_count) * dt_sec
            x_raw = _Y_CENTER + walk_vx * time_since_walk_sec
            x_curr = _clamp_norm(x_raw)
            vx = walk_vx
        subjects.append(
            _make_subject(x=x_curr, vx=vx, ts_ns=ts_ns, track_id=track_id)
        )
    return subjects


def borderline_chatter(
    *,
    vx_amplitude: float,
    dt_sec: float,
    total_sec: float,
    track_id: int = _DEFAULT_TRACK_ID,
    t0_ns: int = _DEFAULT_T0_NS,
) -> list[TrackedSubject]:
    """Alternate vx between +amplitude / -amplitude on consecutive frames.

    Position stays pinned at 0.5 — chatter is purely a vx-sign-flipping
    fixture used to exercise the hysteresis classifier. Per RESEARCH Pitfall 1
    + Pattern 8, we do not integrate position because the consumer
    (MotionAnalyzer) only reads vx.
    """
    _validate_common(dt_sec, total_sec, track_id, t0_ns)
    if vx_amplitude <= 0.0:
        raise ValueError(
            f"vx_amplitude must be > 0.0, got vx_amplitude={vx_amplitude}"
        )

    n_frames = int(total_sec / dt_sec)
    dt_ns = int(dt_sec * _NS_PER_SEC)

    subjects: list[TrackedSubject] = []
    for frame_idx in range(n_frames):
        ts_ns = t0_ns + frame_idx * dt_ns
        vx = vx_amplitude if frame_idx % 2 == 0 else -vx_amplitude
        subjects.append(
            _make_subject(x=_Y_CENTER, vx=vx, ts_ns=ts_ns, track_id=track_id)
        )
    return subjects
