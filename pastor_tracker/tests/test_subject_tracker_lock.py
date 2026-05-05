"""PERC-02 / PERC-03 / PERC-04 / PERC-05 lock-state-machine tests.

Drives real SubjectTracker + real filterpy.KalmanFilter (CLAUDE.md hard rule:
no mocks). Detection sequences via FakePoseEngine fixtures.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
import structlog
from hypothesis import given, settings
from hypothesis import strategies as st

from pastor_tracker.config import Config
from pastor_tracker.core.types import TrackedSubject
from pastor_tracker.perception.subject_tracker import (
    SubjectTracker,
    _LockState,
    compute_subject_centroid,
)
from tests.fixtures.pose_traces import (
    POSE_TRACE_OCCLUSION_3F,
    POSE_TRACE_TRACK_ID_PERSIST,
    POSE_TRACE_TWO_PERSON_CENTRAL,
    make_detection,
)


def _make_tracker(valid_config_dict: dict[str, object]) -> SubjectTracker:
    return SubjectTracker(Config(**valid_config_dict))  # type: ignore[arg-type]


@given(
    kp=st.lists(
        st.tuples(
            st.floats(min_value=0.0, max_value=1.0),
            st.floats(min_value=0.0, max_value=1.0),
        ),
        min_size=17, max_size=17,
    ),
)
@settings(deadline=None, max_examples=40)
def test_centroid_weighted_mean_property(kp: list[tuple[float, float]]) -> None:
    """PERC-02: centroid = 0.4*nose + 0.4*shoulder_mid + 0.2*hip_mid."""
    kp_arr = np.array(kp, dtype=np.float32)
    cx, cy = compute_subject_centroid(kp_arr)
    expected_cx = (
        0.4 * kp_arr[0, 0]
        + 0.4 * (kp_arr[5, 0] + kp_arr[6, 0]) * 0.5
        + 0.2 * (kp_arr[11, 0] + kp_arr[12, 0]) * 0.5
    )
    expected_cy = (
        0.4 * kp_arr[0, 1]
        + 0.4 * (kp_arr[5, 1] + kp_arr[6, 1]) * 0.5
        + 0.2 * (kp_arr[11, 1] + kp_arr[12, 1]) * 0.5
    )
    assert cx == pytest.approx(float(expected_cx), abs=1e-5)
    assert cy == pytest.approx(float(expected_cy), abs=1e-5)


def test_low_conf_detection_rejected(valid_config_dict: dict[str, object]) -> None:
    """PERC-02: mean kp conf < 0.55 floor -> no lock."""
    tracker = _make_tracker(valid_config_dict)
    low_conf = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.40, timestamp_ns=1_000_000)
    result = asyncio.run(tracker.consume([low_conf], now_ns=1_000_000))
    assert result is None
    assert tracker.is_locked is False


def test_track_id_persists_across_frames(valid_config_dict: dict[str, object]) -> None:
    """PERC-03: BoT-SORT track_id flows from Detection to TrackedSubject."""
    tracker = _make_tracker(valid_config_dict)
    last: TrackedSubject | None = None
    for frame_dets in POSE_TRACE_TRACK_ID_PERSIST:
        last = asyncio.run(tracker.consume(frame_dets, now_ns=frame_dets[0].timestamp_ns))
    assert tracker.current_track_id == 42
    assert last is not None
    assert last.track_id == 42


def test_initial_lock_central_60pct_highest_conf(
    valid_config_dict: dict[str, object],
) -> None:
    """PERC-04: central-60% wins over higher-conf-off-center."""
    tracker = _make_tracker(valid_config_dict)
    frame = POSE_TRACE_TWO_PERSON_CENTRAL[0]
    ts = asyncio.run(tracker.consume(frame, now_ns=frame[0].timestamp_ns))
    assert tracker.is_locked is True
    assert tracker.current_track_id == 1
    assert ts is not None
    assert ts.track_id == 1


def test_no_lock_when_only_off_center_person(
    valid_config_dict: dict[str, object],
) -> None:
    """PERC-04: sole off-center person never locks."""
    tracker = _make_tracker(valid_config_dict)
    off_center = make_detection(
        cx=0.05, cy=0.5, track_id=1, conf=0.95, timestamp_ns=1_000_000
    )
    result = asyncio.run(tracker.consume([off_center], now_ns=1_000_000))
    assert result is None
    assert tracker.is_locked is False
    assert tracker.state in (_LockState.SEEKING, _LockState.UNLOCKED)


def test_lock_survives_brief_occlusion(valid_config_dict: dict[str, object]) -> None:
    """PERC-04: ID continuity through occlusion (track_id=1 returns after brief miss).

    POSE_TRACE_OCCLUSION_3F starts with 5 locked frames at ts 0..4*33ms, then 3 empty
    frames, then 5 more locked frames at ts 8*33ms..12*33ms. We synthesize empty-frame
    timestamps from the surrounding locked-frame timestamps so the time-based lock
    loss check operates on plausible monotonic ts.
    """
    tracker = _make_tracker(valid_config_dict)
    last_seen_ts = 0
    for k, frame in enumerate(POSE_TRACE_OCCLUSION_3F):
        if frame:
            ts_ns = frame[0].timestamp_ns
            last_seen_ts = ts_ns
            asyncio.run(tracker.consume(frame, now_ns=ts_ns))
        else:
            # Empty frame; advance time by ~33 ms from previous reference.
            last_seen_ts += 33_000_000
            asyncio.run(tracker.consume([], now_ns=last_seen_ts))
        del k
    assert tracker.current_track_id == 1


def test_lock_loss_2s_reacquires(valid_config_dict: dict[str, object]) -> None:
    """PERC-05: > 2.0 s gap -> LOST + WARN; next central candidate -> LOCKED with new id."""
    tracker = _make_tracker(valid_config_dict)
    with structlog.testing.capture_logs() as caplog:
        # Frame 0: lock track_id=1
        d1 = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000_000_000)
        asyncio.run(tracker.consume([d1], now_ns=1_000_000_000))
        locked_after_acq = tracker.is_locked
        assert locked_after_acq
        # Empty frames advancing past 2 s threshold
        asyncio.run(tracker.consume([], now_ns=2_000_000_000))
        asyncio.run(tracker.consume([], now_ns=3_000_000_000))
        asyncio.run(tracker.consume([], now_ns=3_500_000_000))  # past 2 s threshold
        # Tracker should have either entered LOST or HOLDING by now and is no longer LOCKED
        locked_after_gap = tracker.is_locked
        assert not locked_after_gap
        # New central detection with new track_id triggers re-acquire
        d2 = make_detection(cx=0.5, cy=0.5, track_id=2, conf=0.9, timestamp_ns=4_000_000_000)
        asyncio.run(tracker.consume([d2], now_ns=4_000_000_000))
        locked_after_reacq = tracker.is_locked
        assert locked_after_reacq
        assert tracker.current_track_id == 2
    events = [e["event"] for e in caplog]
    assert "lock_loss" in events
    assert "lock_reacquired" in events


def test_single_gap_over_2s_triggers_lost_from_locked(
    valid_config_dict: dict[str, object],
) -> None:
    """W3 fix (PERC-05): one frame arriving 2.5 s after the lock-acquire frame
    must transition LOCKED -> LOST directly, skipping HOLDING (single-gap path)."""
    tracker = _make_tracker(valid_config_dict)
    with structlog.testing.capture_logs() as caplog:
        d_lock = make_detection(
            cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000_000_000
        )
        asyncio.run(tracker.consume([d_lock], now_ns=1_000_000_000))
        assert tracker.is_locked is True
        # Single big gap: 2.5 s later, NO detections.
        asyncio.run(tracker.consume([], now_ns=3_500_000_000))
    assert tracker.state is _LockState.LOST
    events = [(e["event"], e.get("via")) for e in caplog]
    assert ("lock_loss", "locked_single_frame_gap") in events, events
