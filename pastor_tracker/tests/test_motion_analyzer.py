"""INTENT-01 / INTENT-02 / TEST-03 hysteresis-classifier tests.

Drives real MotionAnalyzer with synthetic trajectories from
``tests.fixtures.trajectories``. No mocks (CLAUDE.md TEST-05 + D-12). All
numerics flow from the ``valid_config_dict`` fixture; no hardcoded thresholds.

Time source: every consume() call uses the upstream subject's ``timestamp_ns``;
D-02 prohibits the analyzer from reading a wall clock and
``test_no_emission_reads_wall_clock`` asserts that contractually.
"""
from __future__ import annotations

import asyncio
import time
from typing import Final

import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotionState, TrackedSubject
from pastor_tracker.intent import MotionAnalyzer
from tests.fixtures.trajectories import (
    borderline_chatter,
    ramp,
)

# Module-level test constants -- local to this file; NOT Config-owned.
# (CLAUDE.md rule 6 -- every threshold the analyzer reads flows in via
# valid_config_dict; these are test-tuning multipliers / sample rates.)
_DT_30HZ_SEC: Final[float] = 1.0 / 30.0
_DT_60HZ_SEC: Final[float] = 1.0 / 60.0
_T0_NS: Final[int] = 1_000_000_000
_NS_PER_SEC: Final[float] = 1_000_000_000.0
_ABOVE_THRESHOLD_FACTOR: Final[float] = 2.0  # vx = 2x threshold = clearly sustained
_BORDERLINE_FACTOR: Final[float] = 1.05  # 5% above threshold; chatter case
_HYSTERESIS_MULTIPLIER: Final[float] = 2.0  # run for 2x the hysteresis window
_DWELL_MULTIPLIER: Final[float] = 2.0  # run for 2x the dwell duration
_DWELL_X_PIN: Final[float] = 0.5  # ramp endpoints equal => vx == 0 => dwell band
_RAMP_X_START_RIGHT: Final[float] = 0.25
_RAMP_X_START_LEFT: Final[float] = 0.75
_RAMP_X_MAX: Final[float] = 0.99  # cap to keep within [0,1] when summing displacement
_RAMP_X_MIN: Final[float] = 0.01
_FLIP_HEADROOM_FRAMES: Final[int] = 5  # margin past the theoretical flip frame
_TRACK_ID: Final[int] = 1


def _make_analyzer(valid_config_dict: dict[str, object]) -> MotionAnalyzer:
    return MotionAnalyzer(Config(**valid_config_dict))  # type: ignore[arg-type]


def _build_subject(
    *, x: float, vx: float, ts_ns: int
) -> TrackedSubject:
    """Construct a TrackedSubject for hand-rolled un-cross sequences."""
    return TrackedSubject(
        track_id=_TRACK_ID,
        subject_center_x_normalized=x,
        subject_center_y_normalized=_DWELL_X_PIN,
        velocity_x_norm_per_sec=vx,
        velocity_y_norm_per_sec=0.0,
        timestamp_ns=ts_ns,
    )


def _drive(
    analyzer: MotionAnalyzer, subjects: list[TrackedSubject]
) -> list[MotionState]:
    """Iterate a TrackedSubject list through consume() and collect MotionStates."""
    out: list[MotionState] = []
    for subj in subjects:
        result = asyncio.run(analyzer.consume(subj, now_ns=subj.timestamp_ns))
        assert result is not None
        out.append(result)
    return out


# --------------------------------------------------------------------------
# Initial state + None-upstream
# --------------------------------------------------------------------------


def test_initial_intent_is_indeterminate(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    assert analyzer.current_intent == "indeterminate"


def test_none_upstream_emits_indeterminate(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    result = asyncio.run(analyzer.consume(None, now_ns=_T0_NS))
    assert result is not None
    assert result.intent == "indeterminate"
    assert result.sustained_velocity_x_norm_per_sec == 0.0
    assert result.timestamp_ns == _T0_NS
    assert analyzer.current_intent == "indeterminate"


# --------------------------------------------------------------------------
# Sustained move flips
# --------------------------------------------------------------------------


def test_sustained_right_flips_intent(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    # vx_const = (x_end - x_start) / total_sec; pick x_end so vx_const = 2*threshold.
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    x_end = min(_RAMP_X_MAX, _RAMP_X_START_RIGHT + target_vx * total_sec)
    subjects = ramp(
        x_start=_RAMP_X_START_RIGHT,
        x_end=x_end,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    # Sanity: every emitted subject has a vx clearly above threshold.
    assert all(s.velocity_x_norm_per_sec > threshold for s in subjects)
    emitted = _drive(analyzer, subjects)
    # By end-of-run, sustained-right is satisfied.
    assert analyzer.current_intent == "moving_right"
    # Pre-window frames must remain "indeterminate" (timer not yet matured).
    pre_window_count = int(hysteresis_sec / _DT_30HZ_SEC) - 1
    assert pre_window_count >= 1
    pre_window_intents = [m.intent for m in emitted[:pre_window_count]]
    assert all(intent == "indeterminate" for intent in pre_window_intents)
    # Post-window frame must show "moving_right" in the emitted MotionState.
    post_window_idx = int(hysteresis_sec / _DT_30HZ_SEC) + _FLIP_HEADROOM_FRAMES
    assert emitted[post_window_idx].intent == "moving_right"


def test_sustained_left_flips_intent(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    x_end = max(_RAMP_X_MIN, _RAMP_X_START_LEFT - target_vx * total_sec)
    subjects = ramp(
        x_start=_RAMP_X_START_LEFT,
        x_end=x_end,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    assert all(s.velocity_x_norm_per_sec < -threshold for s in subjects)
    emitted = _drive(analyzer, subjects)
    assert analyzer.current_intent == "moving_left"
    post_window_idx = int(hysteresis_sec / _DT_30HZ_SEC) + _FLIP_HEADROOM_FRAMES
    assert emitted[post_window_idx].intent == "moving_left"


# --------------------------------------------------------------------------
# Sustained dwell flip
# --------------------------------------------------------------------------


def test_sustained_dwell_flips_intent(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    dwell_threshold = float(valid_config_dict["dwell_threshold_norm_per_sec"])
    dwell_duration_sec = float(valid_config_dict["dwell_duration_sec"])
    total_sec = _DWELL_MULTIPLIER * dwell_duration_sec
    # ramp(x_start=x_end=0.5) yields vx_const = 0.0 -- always within the
    # dwell band (|0.0| < dwell_threshold) so the dwell timer captures
    # at frame 0 and matures at frame ceil(dwell_duration_sec / dt).
    subjects = ramp(
        x_start=_DWELL_X_PIN,
        x_end=_DWELL_X_PIN,
        dt_sec=_DT_30HZ_SEC,
        total_sec=total_sec,
    )
    assert all(abs(s.velocity_x_norm_per_sec) < dwell_threshold for s in subjects)
    emitted = _drive(analyzer, subjects)
    assert analyzer.current_intent == "dwelling"
    pre_window_count = int(dwell_duration_sec / _DT_30HZ_SEC) - 1
    pre_window_intents = [m.intent for m in emitted[:pre_window_count]]
    assert all(intent == "indeterminate" for intent in pre_window_intents)
    post_window_idx = (
        int(dwell_duration_sec / _DT_30HZ_SEC) + _FLIP_HEADROOM_FRAMES
    )
    assert emitted[post_window_idx].intent == "dwelling"


# --------------------------------------------------------------------------
# Borderline chatter / un-cross resets
# --------------------------------------------------------------------------


def test_borderline_chatter_no_thrash(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    # Chatter @ 5% above threshold for 5x the hysteresis window: every
    # other frame un-crosses, so neither right nor left timer ever sustains.
    subjects = borderline_chatter(
        vx_amplitude=_BORDERLINE_FACTOR * threshold,
        dt_sec=_DT_30HZ_SEC,
        total_sec=5.0 * hysteresis_sec,
    )
    emitted = _drive(analyzer, subjects)
    assert analyzer.current_intent == "indeterminate"
    assert all(m.intent == "indeterminate" for m in emitted)


def test_un_cross_resets_timer(
    valid_config_dict: dict[str, object],
) -> None:
    """vx-above-threshold for half the window, ONE un-cross frame, then
    vx-above for half the window must NOT flip intent: the un-cross resets
    the right-crossing timer."""
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    full_window_frames = int(hysteresis_sec / _DT_30HZ_SEC)
    half_window_frames = full_window_frames // 2
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    above_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    subjects: list[TrackedSubject] = []
    frame_idx = 0
    # Phase 1: half window above threshold.
    for _ in range(half_window_frames):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=above_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    # Phase 2: ONE frame at vx=0 (below threshold) -- timer resets.
    subjects.append(_build_subject(
        x=_DWELL_X_PIN, vx=0.0, ts_ns=_T0_NS + frame_idx * dt_ns,
    ))
    frame_idx += 1
    # Phase 3: half window above threshold again. Total above-frames =
    # full_window_frames but split by a reset, so timer never matures.
    for _ in range(half_window_frames):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=above_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    _drive(analyzer, subjects)
    assert analyzer.current_intent == "indeterminate"


def test_threshold_un_cross_then_resustain(
    valid_config_dict: dict[str, object],
) -> None:
    """After an un-cross, sustaining ABOVE threshold for the FULL hysteresis
    window MUST flip intent: the new timer was started fresh from the re-cross."""
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    full_window_frames = int(hysteresis_sec / _DT_30HZ_SEC)
    half_window_frames = full_window_frames // 2
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    above_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    subjects: list[TrackedSubject] = []
    frame_idx = 0
    for _ in range(half_window_frames):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=above_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    subjects.append(_build_subject(
        x=_DWELL_X_PIN, vx=0.0, ts_ns=_T0_NS + frame_idx * dt_ns,
    ))
    frame_idx += 1
    # Now sustain ABOVE threshold for full_window_frames + headroom.
    for _ in range(full_window_frames + _FLIP_HEADROOM_FRAMES):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=above_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    _drive(analyzer, subjects)
    assert analyzer.current_intent == "moving_right"


# --------------------------------------------------------------------------
# Sticky-intent dead band (WR-02)
# --------------------------------------------------------------------------


def test_sustained_move_persists_through_dead_band(
    valid_config_dict: dict[str, object],
) -> None:
    """WR-02: once ``moving_right`` matures, vx in the dwell..move dead band
    must NOT release back to ``indeterminate``.

    Sequence:
        1. Drive vx clearly above ``motion_threshold_norm_per_sec`` for >=
           ``motion_hysteresis_sec`` so ``moving_right`` is sustained.
        2. Drive vx in the dead band ``[dwell_thr, move_thr]`` (specifically
           the midpoint, e.g. 0.055 with the default config) for many frames.
           In this band every timer resets every frame, no condition is
           sustained, and ``_classify`` returns ``self._current_intent``.
        3. Assert ``moving_right`` persists.

    This is the documented behaviour (see module docstring "Sticky-intent
    dead band"); the test pins it so a future change that introduces a
    release path is forced to update both the docstring and this test.
    """
    analyzer = _make_analyzer(valid_config_dict)
    move_thr = float(valid_config_dict["motion_threshold_norm_per_sec"])
    dwell_thr = float(valid_config_dict["dwell_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    dwell_duration_sec = float(valid_config_dict["dwell_duration_sec"])
    dt_ns = int(_DT_30HZ_SEC * _NS_PER_SEC)
    above_vx = _ABOVE_THRESHOLD_FACTOR * move_thr
    # Mid-band: |vx| > dwell_thr (no dwell timer) AND |vx| <= move_thr (no
    # move timer). Use the arithmetic midpoint to stay clearly in-band.
    dead_band_vx = (dwell_thr + move_thr) / 2.0
    assert dwell_thr < dead_band_vx <= move_thr
    subjects: list[TrackedSubject] = []
    frame_idx = 0
    # Phase 1: mature ``moving_right``.
    move_frames = int(_HYSTERESIS_MULTIPLIER * hysteresis_sec / _DT_30HZ_SEC)
    for _ in range(move_frames):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=above_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    # Phase 2: dead band for >= dwell_duration_sec to prove dwell does NOT
    # release the sticky intent (dwell timer resets every frame because
    # |vx| > dwell_thr).
    dead_band_frames = int(
        _DWELL_MULTIPLIER * dwell_duration_sec / _DT_30HZ_SEC
    )
    for _ in range(dead_band_frames):
        subjects.append(_build_subject(
            x=_DWELL_X_PIN, vx=dead_band_vx, ts_ns=_T0_NS + frame_idx * dt_ns,
        ))
        frame_idx += 1
    emitted = _drive(analyzer, subjects)
    # Final intent persists as moving_right.
    assert analyzer.current_intent == "moving_right"
    # Every dead-band frame emits moving_right (sticky semantics).
    dead_band_emissions = emitted[move_frames:]
    assert all(m.intent == "moving_right" for m in dead_band_emissions), (
        "sticky-intent semantics regressed: dead-band frames flipped to "
        f"{set(m.intent for m in dead_band_emissions) - {'moving_right'}}"
    )


# --------------------------------------------------------------------------
# Logging discipline
# --------------------------------------------------------------------------


def test_intent_change_emits_log_event(
    valid_config_dict: dict[str, object],
) -> None:
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    x_end = min(_RAMP_X_MAX, _RAMP_X_START_RIGHT + target_vx * total_sec)
    subjects = ramp(
        x_start=_RAMP_X_START_RIGHT, x_end=x_end,
        dt_sec=_DT_30HZ_SEC, total_sec=total_sec,
    )
    with structlog.testing.capture_logs() as caplog:
        for subj in subjects:
            asyncio.run(analyzer.consume(subj, now_ns=subj.timestamp_ns))
    transitions = [e for e in caplog if e.get("event") == "intent_change"]
    # At least one transition: indeterminate -> moving_right
    assert any(
        t.get("old") == "indeterminate" and t.get("new") == "moving_right"
        for t in transitions
    )


def test_none_upstream_after_lock_logs_reason_field(
    valid_config_dict: dict[str, object],
) -> None:
    """consume(None) AFTER a real flip must emit intent_change with reason
    field (D-04 -- the only branch in _handle_none that logs)."""
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    x_end = min(_RAMP_X_MAX, _RAMP_X_START_RIGHT + target_vx * total_sec)
    subjects = ramp(
        x_start=_RAMP_X_START_RIGHT, x_end=x_end,
        dt_sec=_DT_30HZ_SEC, total_sec=total_sec,
    )
    _drive(analyzer, subjects)
    assert analyzer.current_intent == "moving_right"
    with structlog.testing.capture_logs() as caplog:
        result = asyncio.run(analyzer.consume(None, now_ns=_T0_NS))
    assert result is not None
    assert result.intent == "indeterminate"
    reasoned = [
        e for e in caplog
        if e.get("event") == "intent_change" and e.get("reason") == "upstream_none"
    ]
    assert len(reasoned) == 1
    assert reasoned[0]["old"] == "moving_right"
    assert reasoned[0]["new"] == "indeterminate"


# --------------------------------------------------------------------------
# None resets timers across runs
# --------------------------------------------------------------------------


def test_none_after_locked_resets_timers(
    valid_config_dict: dict[str, object],
) -> None:
    """Drive a sustained right-ramp until intent flips; consume(None); then
    drive a sustained left-ramp -- it must take the FULL hysteresis window
    to flip again, proving the right-timer state was discarded."""
    analyzer = _make_analyzer(valid_config_dict)
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    # Phase 1: sustained right -> moving_right.
    x_end_right = min(_RAMP_X_MAX, _RAMP_X_START_RIGHT + target_vx * total_sec)
    subjects_right = ramp(
        x_start=_RAMP_X_START_RIGHT, x_end=x_end_right,
        dt_sec=_DT_30HZ_SEC, total_sec=total_sec,
    )
    _drive(analyzer, subjects_right)
    assert analyzer.current_intent == "moving_right"
    # Phase 2: consume None -> indeterminate + all timers reset.
    asyncio.run(analyzer.consume(None, now_ns=_T0_NS + 10 * 10**9))
    assert analyzer.current_intent == "indeterminate"
    # Phase 3: sustained left, starting at a fresh timestamp epoch.
    x_end_left = max(_RAMP_X_MIN, _RAMP_X_START_LEFT - target_vx * total_sec)
    subjects_left = ramp(
        x_start=_RAMP_X_START_LEFT, x_end=x_end_left,
        dt_sec=_DT_30HZ_SEC, total_sec=total_sec,
        t0_ns=_T0_NS + 100 * 10**9,
    )
    flip_frame = int(hysteresis_sec / _DT_30HZ_SEC)
    # Frame just before maturity: still indeterminate.
    pre_window_subjects = subjects_left[:flip_frame - 1]
    _drive(analyzer, pre_window_subjects)
    assert analyzer.current_intent == "indeterminate"
    # Drive remainder; must end up moving_left.
    _drive(analyzer, subjects_left[flip_frame - 1:])
    assert analyzer.current_intent == "moving_left"


# --------------------------------------------------------------------------
# Sample-rate independence
# --------------------------------------------------------------------------


def test_dt_independence(
    valid_config_dict: dict[str, object],
) -> None:
    """The hysteresis is duration-based, not frame-count-based: the same
    sustained-vx ramp must flip at the same wall-time mark at 30 Hz vs 60 Hz."""
    threshold = float(valid_config_dict["motion_threshold_norm_per_sec"])
    hysteresis_sec = float(valid_config_dict["motion_hysteresis_sec"])
    total_sec = _HYSTERESIS_MULTIPLIER * hysteresis_sec
    target_vx = _ABOVE_THRESHOLD_FACTOR * threshold
    x_end = min(_RAMP_X_MAX, _RAMP_X_START_RIGHT + target_vx * total_sec)

    for dt_sec in (_DT_30HZ_SEC, _DT_60HZ_SEC):
        analyzer = _make_analyzer(valid_config_dict)
        subjects = ramp(
            x_start=_RAMP_X_START_RIGHT, x_end=x_end,
            dt_sec=dt_sec, total_sec=total_sec,
        )
        flip_frame = int(hysteresis_sec / dt_sec)
        # Drive frames [0 .. flip_frame - 2]: still indeterminate.
        _drive(analyzer, subjects[:flip_frame - 1])
        assert analyzer.current_intent == "indeterminate", (
            f"dt={dt_sec}: flipped too early at frame {flip_frame - 1}"
        )
        # Drive remaining frames; must reach moving_right by flip_frame +
        # headroom.
        _drive(analyzer, subjects[flip_frame - 1:flip_frame + _FLIP_HEADROOM_FRAMES])
        assert analyzer.current_intent == "moving_right", (
            f"dt={dt_sec}: did not flip by frame {flip_frame + _FLIP_HEADROOM_FRAMES}"
        )


# --------------------------------------------------------------------------
# Wall-clock prohibition (D-02)
# --------------------------------------------------------------------------


def test_no_emission_reads_wall_clock(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02: MotionAnalyzer.consume(None, ...) must run with time.time and
    time.perf_counter_ns disabled. Asserts the analyzer does not call them
    in the None-upstream path (the only path tested here -- log emission
    avoided to keep structlog from reading time.time itself)."""
    def _raise(*args: object, **kwargs: object) -> int:
        raise AssertionError(
            "MotionAnalyzer must not read a wall clock (D-02)"
        )
    monkeypatch.setattr(time, "perf_counter_ns", _raise)
    monkeypatch.setattr(time, "time", _raise)
    analyzer = _make_analyzer(valid_config_dict)
    result = asyncio.run(analyzer.consume(None, now_ns=_T0_NS))
    assert result is not None
    assert result.intent == "indeterminate"
    assert result.timestamp_ns == _T0_NS
