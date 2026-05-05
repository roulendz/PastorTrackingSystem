"""Canned Detection sequences and async helpers for perception integration tests.

Drive scripted traces via :class:`FakePoseEngine`; pre-load a list of
``list[Detection]`` per-frame outputs and hand the fake to ``SubjectTracker``
or ``PoseDetector`` via the ``PoseEngine`` Protocol injection seam.

Import path is ``tests.fixtures.pose_traces`` -- pytest ``rootdir`` is
``pastor_tracker/`` (testpaths=["tests"], packages=["src/pastor_tracker"]).
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package.
"""
from __future__ import annotations

import collections
from collections.abc import Iterable

from pastor_tracker.core.types import Detection, Frame


def make_detection(
    *,
    cx: float,
    cy: float,
    track_id: int | None = 1,
    conf: float = 0.9,
    timestamp_ns: int = 0,
    bbox_half_w: float = 0.05,
    bbox_half_h: float = 0.10,
) -> Detection:
    """Construct a synthetic Detection with a centered bbox around (cx, cy).

    Bbox is clamped to [0, 1] so the Detection cross-field validator
    (bbox_x2 > bbox_x1) holds for any cx in [bbox_half_w, 1 - bbox_half_w].
    """
    return Detection(
        subject_center_x_normalized=cx,
        subject_center_y_normalized=cy,
        mean_keypoint_confidence=conf,
        bbox_x1_normalized=max(0.0, cx - bbox_half_w),
        bbox_y1_normalized=max(0.0, cy - bbox_half_h),
        bbox_x2_normalized=min(1.0, cx + bbox_half_w),
        bbox_y2_normalized=min(1.0, cy + bbox_half_h),
        timestamp_ns=timestamp_ns,
        track_id=track_id,
    )


class FakePoseEngine:
    """Zero-deps PoseEngine fake. Pre-load a script of per-frame Detection lists.

    Satisfies the ``PoseEngine`` Protocol without ultralytics or torch in scope.
    """

    def __init__(self, script: Iterable[list[Detection]] | None = None) -> None:
        self._script: collections.deque[list[Detection]] = collections.deque(
            script if script is not None else []
        )
        self._closed: bool = False
        self.detect_calls: list[int] = []

    async def detect(self, frame: Frame) -> list[Detection]:
        self.detect_calls.append(frame.timestamp_ns)
        if not self._script:
            return []
        return self._script.popleft()

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


# --- Scripted traces (Plan 02 + Plan 03 will add more) ----------------------

POSE_TRACE_INITIAL_LOCK: list[list[Detection]] = [
    [make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000_000)],
]
"""One central-60% high-conf person at frame 0."""

POSE_TRACE_OFF_CENTER_NO_LOCK: list[list[Detection]] = [
    [make_detection(cx=0.05, cy=0.5, track_id=2, conf=0.95, timestamp_ns=1_000_000)],
]
"""Sole person sits outside central 60% (cx=0.05 < 0.2 threshold)."""

POSE_TRACE_LOW_CONF_REJECT: list[list[Detection]] = [
    [make_detection(cx=0.5, cy=0.5, track_id=3, conf=0.40, timestamp_ns=1_000_000)],
]
"""Central person with mean kp conf < 0.55 (PERC-02 floor)."""


# Locked subject for 5 frames, then 3 empty frames (HOLDING trigger), then 5 more.
POSE_TRACE_OCCLUSION_3F: list[list[Detection]] = (
    [
        [make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=k * 33_000_000)]
        for k in range(5)
    ]
    + [[] for _ in range(3)]
    + [
        [make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=k * 33_000_000)]
        for k in range(8, 13)
    ]
)
"""5 frames locked, 3 empty (HOLDING), 5 more locked. PERC-07 + recovery."""

POSE_TRACE_LOCK_LOSS_2S: list[list[Detection]] = (
    [
        [make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=k * 33_000_000)]
        for k in range(3)
    ]
    + [[] for _ in range(80)]  # ~2.6 s at 30 fps
    + [[make_detection(cx=0.5, cy=0.5, track_id=2, conf=0.9, timestamp_ns=83 * 33_000_000)]]
)
"""Lock track_id=1, then 2.6 s gap, then track_id=2 -- forces LOST -> RE_ACQUIRING."""

POSE_TRACE_TRACK_ID_PERSIST: list[list[Detection]] = [
    [
        make_detection(
            cx=0.5 + 0.01 * k, cy=0.5, track_id=42, conf=0.9, timestamp_ns=k * 33_000_000
        )
    ]
    for k in range(10)
]
"""Same physical subject, stable track_id=42 across 10 frames (rightward motion)."""

POSE_TRACE_TWO_PERSON_CENTRAL: list[list[Detection]] = [
    [
        make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000_000),
        make_detection(cx=0.05, cy=0.5, track_id=2, conf=0.95, timestamp_ns=1_000_000),
    ],
]
"""Two persons: track_id=1 central conf 0.9, track_id=2 off-center conf 0.95.

PERC-04 must lock track_id=1 (central beats higher-conf-but-off-center).
"""
