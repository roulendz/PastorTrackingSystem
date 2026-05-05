"""PERC-07 HOLDING-state tests (3-frame freeze + recovery)."""
from __future__ import annotations

import asyncio

import structlog

from pastor_tracker.config import Config
from pastor_tracker.perception.subject_tracker import SubjectTracker, _LockState
from tests.fixtures.pose_traces import make_detection


def _make_tracker(valid_config_dict: dict[str, object]) -> SubjectTracker:
    return SubjectTracker(Config(**valid_config_dict))  # type: ignore[arg-type]


def test_three_misses_holds_posterior(valid_config_dict: dict[str, object]) -> None:
    """PERC-07: 3 consecutive empty frames trigger HOLDING + WARN."""
    tracker = _make_tracker(valid_config_dict)
    d1 = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=0)
    asyncio.run(tracker.consume([d1], now_ns=0))
    with structlog.testing.capture_logs() as caplog:
        asyncio.run(tracker.consume([], now_ns=33_000_000))   # miss 1
        asyncio.run(tracker.consume([], now_ns=66_000_000))   # miss 2
        asyncio.run(tracker.consume([], now_ns=99_000_000))   # miss 3 -> HOLDING
    assert tracker.state is _LockState.HOLDING
    events = [e["event"] for e in caplog]
    assert "lock_holding" in events


def test_holding_freezes_posterior(valid_config_dict: dict[str, object]) -> None:
    """PERC-07: HOLDING emits identical frozen posterior across multiple frames."""
    tracker = _make_tracker(valid_config_dict)
    d1 = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=0)
    asyncio.run(tracker.consume([d1], now_ns=0))
    asyncio.run(tracker.consume([], now_ns=33_000_000))
    asyncio.run(tracker.consume([], now_ns=66_000_000))
    asyncio.run(tracker.consume([], now_ns=99_000_000))  # enter HOLDING
    held_emits = []
    for k in range(5):
        ts = asyncio.run(tracker.consume([], now_ns=132_000_000 + k * 33_000_000))
        held_emits.append(ts)
    # All 5 holding emits must be identical x/y (frozen posterior).
    assert all(ts is not None for ts in held_emits)
    first = held_emits[0]
    assert first is not None
    assert all(
        ts is not None
        and abs(ts.subject_center_x_normalized - first.subject_center_x_normalized) < 1e-9
        and abs(ts.subject_center_y_normalized - first.subject_center_y_normalized) < 1e-9
        for ts in held_emits
    )


def test_holding_recovers_to_locked(valid_config_dict: dict[str, object]) -> None:
    """PERC-07: HOLDING -> LOCKED on next matching detection within 2 s."""
    tracker = _make_tracker(valid_config_dict)
    d_lock = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=0)
    asyncio.run(tracker.consume([d_lock], now_ns=0))
    asyncio.run(tracker.consume([], now_ns=33_000_000))
    asyncio.run(tracker.consume([], now_ns=66_000_000))
    asyncio.run(tracker.consume([], now_ns=99_000_000))
    state_after_misses = tracker.state
    assert state_after_misses == _LockState.HOLDING
    d_recover = make_detection(
        cx=0.55, cy=0.5, track_id=1, conf=0.9, timestamp_ns=132_000_000
    )
    ts = asyncio.run(tracker.consume([d_recover], now_ns=132_000_000))
    state_after_recovery = tracker.state
    assert state_after_recovery == _LockState.LOCKED
    assert ts is not None
    assert tracker.current_track_id == 1
