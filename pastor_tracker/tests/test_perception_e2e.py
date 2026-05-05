"""End-to-end Phase 4 integration: FakePoseEngine -> PoseDetector -> SubjectTracker.

Validates the seam Phase 4 ships. Phase 6 will own the full pipeline; this
test exercises the contract between the two Phase-4 modules at the
orchestrator boundary using zero ML deps (the FakePoseEngine produces
real :class:`Detection` records driven by scripted traces).

Tests:
    1. ``test_e2e_track_id_persist_to_tracked_subject_stream`` -- 10-frame
       rightward-walk trace; assert the SubjectTracker stays locked on
       track_id=42 and the Kalman-smoothed cx converges to the most
       recent measurement (W7 bound: 0.55 <= cx <= 0.60).
    2. ``test_e2e_lock_acquire_emits_log`` -- two-person scene; central
       (cx=0.5) wins over higher-conf-off-center (cx=0.05) end-to-end
       and ``lock_acquired`` log fires with track_id=1.
    3. ``test_e2e_low_conf_no_lock`` -- mean-kp-conf 0.40 trace; tracker
       stays unlocked across the entire stream.
    4. ``test_e2e_perc02_weighted_centroid_in_emit`` (B1 contract) --
       construct a synthetic ultralytics-shaped result with bbox center
       deliberately offset from the keypoints; assert the emitted
       Detection.subject_center_*_normalized comes from the PERC-02
       weighted-keypoint mean, NOT the bbox midpoint.
"""
from __future__ import annotations

import asyncio
import builtins

import numpy as np
import numpy.typing as npt
import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, Frame, TrackedSubject
from pastor_tracker.perception.pose_detector import PoseDetector
from pastor_tracker.perception.subject_tracker import SubjectTracker, _LockState
from tests.fixtures.pose_traces import (
    POSE_TRACE_LOW_CONF_REJECT,
    POSE_TRACE_TRACK_ID_PERSIST,
    POSE_TRACE_TWO_PERSON_CENTRAL,
    FakePoseEngine,
)


def _make_frames(scripted: list[list[Detection]], cfg: Config) -> list[Frame]:
    """Build a synthetic Frame stream timestamped to match the scripted detections."""
    img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
    out: list[Frame] = []
    for k, frame_dets in enumerate(scripted):
        ts_ns = frame_dets[0].timestamp_ns if frame_dets else (k + 1) * 33_000_000
        out.append(
            Frame(
                image=img,
                width=cfg.capture_width,
                height=cfg.capture_height,
                timestamp_ns=ts_ns,
            )
        )
    return out


async def _drive_pipeline(
    cfg: Config,
    scripted: list[list[Detection]],
) -> tuple[SubjectTracker, list[TrackedSubject | None]]:
    """Wire FakePoseEngine -> PoseDetector -> SubjectTracker; drive ``scripted`` end-to-end."""
    engine = FakePoseEngine(script=scripted)
    detector = PoseDetector(config=cfg, engine=engine)
    tracker = SubjectTracker(cfg)
    await detector.start()
    emits: list[TrackedSubject | None] = []
    try:
        frames = _make_frames(scripted, cfg)
        for f in frames:
            await detector.consume(f)
            await asyncio.sleep(0.005)  # let each inference finish
        det_iter = detector.detections()
        for _ in range(len(scripted)):
            dets = await asyncio.wait_for(anext(det_iter), timeout=1.0)
            ts_ns = dets[0].timestamp_ns if dets else 0
            ts = await tracker.consume(dets, now_ns=ts_ns)
            emits.append(ts)
    finally:
        await detector.stop()
    return tracker, emits


@pytest.mark.asyncio
async def test_e2e_track_id_persist_to_tracked_subject_stream(
    valid_config_dict: dict[str, object],
) -> None:
    """PERC-03 + PERC-06: track_id=42 stable across 10 frames; Kalman smooths rightward motion."""
    cfg = Config(**valid_config_dict)
    tracker, emits = await _drive_pipeline(cfg, POSE_TRACE_TRACK_ID_PERSIST)
    is_locked = tracker.is_locked
    assert is_locked is True
    assert tracker.current_track_id == 42
    last_emit = emits[-1]
    assert last_emit is not None
    assert last_emit.track_id == 42
    # Trajectory: cx steps 0.5, 0.51, 0.52, ..., 0.59 over 10 frames; the
    # smoothed cx at the end should converge close to the most recent
    # raw measurement (R is small in _kalman.py so the filter trusts the
    # observation). W7 fix: tighten lower bound 0.50 -> 0.55 (the loose
    # 0.50 was unverifiable -- it would pass even if the filter never
    # tracked the rightward motion at all).
    assert 0.55 <= last_emit.subject_center_x_normalized <= 0.60


@pytest.mark.asyncio
async def test_e2e_lock_acquire_emits_log(valid_config_dict: dict[str, object]) -> None:
    """PERC-04 end-to-end: central beats higher-conf-off-center; lock_acquired fires."""
    cfg = Config(**valid_config_dict)
    with structlog.testing.capture_logs() as caplog:
        tracker, _ = await _drive_pipeline(cfg, POSE_TRACE_TWO_PERSON_CENTRAL)
    events = [e for e in caplog if e.get("event") == "lock_acquired"]
    assert len(events) >= 1
    assert events[0]["track_id"] == 1
    current_track = tracker.current_track_id
    assert current_track == 1


@pytest.mark.asyncio
async def test_e2e_low_conf_no_lock(valid_config_dict: dict[str, object]) -> None:
    """PERC-02 end-to-end: low-conf detection never promotes the tracker."""
    cfg = Config(**valid_config_dict)
    tracker, emits = await _drive_pipeline(cfg, POSE_TRACE_LOW_CONF_REJECT)
    is_locked = tracker.is_locked
    assert is_locked is False
    assert all(e is None for e in emits)
    state = tracker.state
    assert state in (_LockState.UNLOCKED, _LockState.SEEKING)


# ---------------------------------------------------------------------------
# B1 contract test: PERC-02 weighted-keypoint centroid feeds the emit.
# ---------------------------------------------------------------------------


class _NumpyHandle:
    """Mimics ultralytics' tensor-handle ``.cpu().numpy()`` shape."""

    def __init__(self, arr: npt.NDArray[np.float32]) -> None:
        self._arr = arr

    def cpu(self) -> _NumpyHandle:
        return self

    def numpy(self) -> npt.NDArray[np.float32]:
        return self._arr


class _IntHandle:
    """Mimics ultralytics' id-tensor ``.int().cpu().tolist()`` shape."""

    def __init__(self, arr: npt.NDArray[np.int64]) -> None:
        self._arr = arr

    # ``int`` mirrors the ultralytics tensor API; the name is fixed by the
    # external library contract that ``_pose_worker._translate`` consumes
    # (``boxes.id.int().cpu().tolist()``). Returning the same handle keeps
    # the chain valid without producing a real torch tensor.
    def int(self) -> _IntHandle:
        return self

    def cpu(self) -> _IntHandle:
        return self

    def tolist(self) -> list[builtins.int]:
        return [builtins.int(x) for x in self._arr.tolist()]


class _SyntheticBoxes:
    """Mimics ultralytics ``boxes`` enough for ``_pose_worker._translate``."""

    def __init__(
        self,
        xyxyn: npt.NDArray[np.float32],
        conf: npt.NDArray[np.float32],
        ids: list[int] | None,
    ) -> None:
        self.xyxyn = _NumpyHandle(xyxyn)
        self.conf = _NumpyHandle(conf)
        self.id: _IntHandle | None
        if ids is None:
            self.id = None
        else:
            self.id = _IntHandle(np.asarray(ids, dtype=np.int64))


class _SyntheticKeypoints:
    def __init__(
        self,
        xyn: npt.NDArray[np.float32],
        conf: npt.NDArray[np.float32],
    ) -> None:
        self.xyn = _NumpyHandle(xyn)
        self.conf = _NumpyHandle(conf)


class _SyntheticResult:
    def __init__(
        self,
        boxes: _SyntheticBoxes,
        keypoints: _SyntheticKeypoints,
    ) -> None:
        self.boxes = boxes
        self.keypoints = keypoints


def test_e2e_perc02_weighted_centroid_in_emit() -> None:
    """B1 contract test: emitted Detection.subject_center_*_normalized comes
    from the PERC-02 weighted-keypoint mean -- NOT the bbox midpoint.

    Construct a synthetic ultralytics-shaped result with bbox center
    deliberately OFFSET from the keypoints (bbox center cx=0.50;
    keypoints all at cx=0.70). Assert the emitted
    Detection.subject_center_x_normalized matches 0.70 +/- 1e-4, proving
    the worker uses ``weighted_keypoint_centroid``.
    """
    from pastor_tracker.perception._pose_worker import _translate

    # Bbox midpoint at cx=0.50 (would be wrong centroid).
    xyxyn = np.array([[0.40, 0.40, 0.60, 0.80]], dtype=np.float32)
    confs = np.array([0.95], dtype=np.float32)
    # 17 keypoints, all at cx=0.70 (the WEIGHTED mean must equal 0.70).
    kp_xyn = np.zeros((1, 17, 2), dtype=np.float32)
    kp_xyn[0, :, 0] = 0.70  # all x = 0.70
    kp_xyn[0, :, 1] = 0.50  # all y = 0.50
    kp_conf = np.ones((1, 17), dtype=np.float32) * 0.9

    result = _SyntheticResult(
        boxes=_SyntheticBoxes(xyxyn=xyxyn, conf=confs, ids=[42]),
        keypoints=_SyntheticKeypoints(xyn=kp_xyn, conf=kp_conf),
    )
    out = _translate(result, timestamp_ns=12345)
    assert len(out.detections) == 1
    det = out.detections[0]
    # CRITICAL B1 ASSERTION: weighted-keypoint mean (0.70), NOT bbox midpoint (0.50).
    assert abs(det.subject_center_x_normalized - 0.70) < 1e-4, det
    assert abs(det.subject_center_y_normalized - 0.50) < 1e-4, det
    assert abs(det.mean_keypoint_confidence - 0.9) < 1e-4, det
    assert det.track_id == 42
