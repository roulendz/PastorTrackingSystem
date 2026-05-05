"""Subject-lock state machine + Kalman wiring (Phase 4 perception).

Architecture (RESEARCH 04 Pattern 5 + CONTEXT.md Subject Lock Lifecycle):

    Six-state machine driven by per-frame Detection arrivals + a 2.0 s
    lock-loss timer:

        UNLOCKED       -- pipeline just started; no subject ever locked.
        SEEKING        -- alias of UNLOCKED post first lookup; kept distinct
                           for log clarity (PATTERNS.md note).
        LOCKED         -- track_id is locked; emit TrackedSubject from
                           KalmanFilter on every matching detection.
        HOLDING        -- locked, but >= 3 consecutive frames without a
                           matching detection (PERC-07); emit FROZEN
                           posterior (no predict, no update -- Pitfall 4).
        LOST           -- > 2.0 s since last_seen; waiting for re-acq.
        RE_ACQUIRING   -- new central-60% candidate present post-LOST;
                           transition to LOCKED with FRESH KalmanFilter
                           (Pitfall 2).

    Pure helpers:
        compute_subject_centroid: PERC-02 weighted mean (delegates to
                                  ``_keypoints.weighted_keypoint_centroid``
                                  -- single source of truth, B1 fix).
        is_in_central_region:     PERC-04 60%-of-frame predicate.

    Test contract (CLAUDE.md): no mocks of KalmanFilter. ``FakePoseEngine``
    in ``tests.fixtures.pose_traces`` produces real Detection records;
    ``SubjectTracker`` consumes them and drives a real
    ``filterpy.kalman.KalmanFilter`` via :mod:`._kalman`.
"""
from __future__ import annotations

import enum
from typing import Final

import numpy as np
import numpy.typing as npt
import structlog
from filterpy.kalman import KalmanFilter

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, TrackedSubject
from pastor_tracker.perception._kalman import (
    _KalmanWrapper,
    predict_with_dt,
)
from pastor_tracker.perception._keypoints import weighted_keypoint_centroid
from pastor_tracker.perception.pose_detector import PerceptionError

# --- Lock heuristics (PERC-04) ---
_CENTRAL_REGION_FRACTION: Final[float] = 0.6
_CENTRAL_HALF: Final[float] = (1.0 - _CENTRAL_REGION_FRACTION) / 2.0  # 0.2

# --- Lifecycle thresholds ---
_LOCK_LOSS_TIMEOUT_SEC: Final[float] = 2.0  # PERC-05
_HOLD_POSTERIOR_FRAME_THRESHOLD: Final[int] = 3  # PERC-07
_NS_PER_SEC: Final[float] = 1_000_000_000.0
_NS_PER_MS: Final[float] = 1_000_000.0

# --- Frame coordinate clip range ---
_NORM_MIN: Final[float] = 0.0
_NORM_MAX: Final[float] = 1.0

# --- Floor for capture_fps when computing default dt ---
_CAPTURE_FPS_FLOOR: Final[int] = 1


__all__ = [
    "SubjectTracker",
    "compute_subject_centroid",
    "is_in_central_region",
]


class _LockState(enum.Enum):
    UNLOCKED = "unlocked"
    SEEKING = "seeking"
    LOCKED = "locked"
    HOLDING = "holding"
    LOST = "lost"
    RE_ACQUIRING = "re_acquiring"


def compute_subject_centroid(kp_xyn: npt.NDArray[np.float32]) -> tuple[float, float]:
    """PERC-02 weighted-mean centroid (B1: thin delegate to shared helper).

    The math lives in :mod:`pastor_tracker.perception._keypoints` so that
    ``_pose_worker._translate`` (worker tier) and this loop-thread call site
    cannot diverge. ``kp_conf`` is fabricated as ones because legacy callers
    of this helper (Plan 02 unit tests) only feed positions; production code
    paths use the worker translation which calls ``weighted_keypoint_centroid``
    directly with real per-keypoint confidence.
    """
    cx, cy, _ = weighted_keypoint_centroid(
        kp_xyn, np.ones((kp_xyn.shape[0],), dtype=np.float32)
    )
    return cx, cy


def is_in_central_region(cx: float, cy: float) -> bool:
    """PERC-04: central 60% of frame in normalized coords (RESEARCH 04 Code Example 5)."""
    return (
        _CENTRAL_HALF <= cx <= 1.0 - _CENTRAL_HALF
        and _CENTRAL_HALF <= cy <= 1.0 - _CENTRAL_HALF
    )


class SubjectTracker:
    """Six-state lock + Kalman per-frame transformer.

    Public surface (consume-based, per BL-01 simplification 2026-05-05):
        ``async def consume(detections, now_ns) -> TrackedSubject | None``:
            single-frame tick; returns the smoothed subject or None when
            UNLOCKED / SEEKING / LOST. The Phase 6 orchestrator iterates
            by composing this call inside its own outer loop -- there is
            NO ``tracked_subjects()`` async iterator. The earlier BL-01
            blocker (a deadlock-on-empty-queue iterator backed by a queue
            nothing populated) was dropped in favour of consume()'s direct
            return semantics.

    Read-only dashboard surface (CONTEXT.md Area 4):
        is_locked, current_track_id, last_lock_loss_ts_ns
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="subject_tracker")
        self._kalman_wrapper = _KalmanWrapper()
        self._state: _LockState = _LockState.UNLOCKED
        self._kf: KalmanFilter | None = None
        self._locked_track_id: int | None = None
        self._last_seen_ts_ns: int | None = None
        self._last_lock_loss_ts_ns: int | None = None
        self._consecutive_misses: int = 0
        self._latched_error: PerceptionError | None = None
        self._holding_logged: bool = False  # one WARN per HOLDING entry

    # ---------- read-only dashboard surface ----------
    @property
    def is_locked(self) -> bool:
        return self._state is _LockState.LOCKED

    @property
    def current_track_id(self) -> int | None:
        return self._locked_track_id

    @property
    def last_lock_loss_ts_ns(self) -> int | None:
        return self._last_lock_loss_ts_ns

    @property
    def state(self) -> _LockState:
        return self._state

    @property
    def last_error(self) -> PerceptionError | None:
        return self._latched_error

    # ---------- core entry point ----------
    async def consume(
        self, detections: list[Detection], now_ns: int,
    ) -> TrackedSubject | None:
        """Single-frame tick. Returns emitted subject or None (UNLOCKED / SEEKING / LOST).

        The dispatch is flat (CLAUDE.md rule 5: <=2 nesting). Per-state handlers
        own their own guard logic.
        """
        self._raise_if_latched()
        # Filter detections by PERC-02 conf floor before any state processing.
        eligible = [
            d for d in detections
            if d.mean_keypoint_confidence >= self._config.detection_confidence_min
        ]
        rejected = len(detections) - len(eligible)
        if rejected > 0:
            self._logger.debug(
                "low_conf_detection_rejected",
                count=rejected,
                threshold=self._config.detection_confidence_min,
            )
        match self._state:
            case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
                return self._try_lock(eligible, now_ns)
            case _LockState.LOCKED:
                return self._tick_locked(eligible, now_ns)
            case _LockState.HOLDING:
                return self._tick_holding(eligible, now_ns)
            case _LockState.LOST:
                return self._try_reacquire(eligible, now_ns)
            case _:
                # WR-08 fix: exhaustiveness guard (Tiger-style fail-loud on
                # contract violation). All six current _LockState members are
                # covered above; this catches a future enum member added
                # without a matching dispatch arm.
                raise PerceptionError(
                    f"unhandled _LockState in consume: {self._state!r}"
                )

    # ---------- state-machine handlers ----------
    def _try_lock(
        self, eligible: list[Detection], now_ns: int,
    ) -> TrackedSubject | None:
        """UNLOCKED / SEEKING / RE_ACQUIRING: pick highest-conf central-60% candidate."""
        candidates = [
            d for d in eligible
            if is_in_central_region(
                d.subject_center_x_normalized,
                d.subject_center_y_normalized,
            )
            and d.track_id is not None
        ]
        if not candidates:
            self._state = _LockState.SEEKING  # explicit -- no central candidate this frame
            return None
        chosen = max(candidates, key=lambda d: d.mean_keypoint_confidence)
        old_state = self._state
        old_track_id = self._locked_track_id
        self._kf = self._kalman_wrapper.reset_for_new_track(
            initial_x=chosen.subject_center_x_normalized,
            initial_y=chosen.subject_center_y_normalized,
            dt=1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR),
        )
        assert chosen.track_id is not None  # filtered above
        self._locked_track_id = chosen.track_id
        self._last_seen_ts_ns = now_ns
        self._consecutive_misses = 0
        self._holding_logged = False
        self._state = _LockState.LOCKED
        if old_state is _LockState.RE_ACQUIRING or old_track_id is not None:
            gap_ms = (
                (now_ns - self._last_lock_loss_ts_ns) / _NS_PER_MS
                if self._last_lock_loss_ts_ns is not None
                else 0.0
            )
            self._logger.warning(
                "lock_reacquired",
                old_track_id=old_track_id,
                new_track_id=chosen.track_id,
                gap_ms=gap_ms,
            )
        else:
            self._logger.info(
                "lock_acquired", track_id=chosen.track_id,
                cx=chosen.subject_center_x_normalized,
                cy=chosen.subject_center_y_normalized,
            )
        return self._emit_from_kf(now_ns)

    def _tick_locked(
        self, eligible: list[Detection], now_ns: int,
    ) -> TrackedSubject | None:
        """LOCKED: predict + update on matching track_id, else miss-counter increment.

        W3 fix (PERC-05): A single large gap between consecutive frames (e.g. > 2.0 s
        with NO detection arrivals) MUST take LOCKED -> LOST directly, bypassing the
        3-miss HOLDING gate. Otherwise the consecutive-miss counter could keep state
        in LOCKED while real time has long since exceeded the lock-loss timeout.
        """
        if self._last_seen_ts_ns is not None:
            elapsed_sec = (now_ns - self._last_seen_ts_ns) / _NS_PER_SEC
            if elapsed_sec > _LOCK_LOSS_TIMEOUT_SEC:
                self._logger.warning(
                    "lock_loss",
                    track_id=self._locked_track_id,
                    last_seen_ts_ns=self._last_seen_ts_ns,
                    age_ms=elapsed_sec * 1000.0,
                    via="locked_single_frame_gap",  # W3 path (skips HOLDING)
                )
                self._last_lock_loss_ts_ns = now_ns
                self._locked_track_id = None
                self._consecutive_misses = 0
                self._kf = None
                self._state = _LockState.LOST
                return None
        match = self._find_match(eligible)
        if match is not None:
            self._consecutive_misses = 0
            self._last_seen_ts_ns = now_ns
            assert self._kf is not None
            dt_sec = self._compute_dt_sec(now_ns)
            predict_with_dt(self._kf, dt_sec)
            self._kf.update(np.array([
                match.subject_center_x_normalized,
                match.subject_center_y_normalized,
            ]))
            return self._emit_from_kf(now_ns)
        # miss
        self._consecutive_misses += 1
        if self._consecutive_misses >= _HOLD_POSTERIOR_FRAME_THRESHOLD:
            self._state = _LockState.HOLDING
            self._holding_logged = False
            return self._tick_holding(eligible, now_ns)
        # 1 or 2 misses while LOCKED: predict only, do NOT update
        assert self._kf is not None
        dt_sec = self._compute_dt_sec(now_ns)
        predict_with_dt(self._kf, dt_sec)
        return self._emit_from_kf(now_ns)

    def _tick_holding(
        self, eligible: list[Detection], now_ns: int,
    ) -> TrackedSubject | None:
        """HOLDING: emit frozen posterior; check for re-lock or LOST timeout."""
        # Did the locked id come back?
        match = self._find_match(eligible)
        if match is not None:
            self._consecutive_misses = 0
            self._last_seen_ts_ns = now_ns
            self._state = _LockState.LOCKED
            self._holding_logged = False
            assert self._kf is not None
            dt_sec = self._compute_dt_sec(now_ns)
            predict_with_dt(self._kf, dt_sec)
            self._kf.update(np.array([
                match.subject_center_x_normalized,
                match.subject_center_y_normalized,
            ]))
            self._logger.info(
                "lock_recovered_from_hold",
                track_id=self._locked_track_id,
            )
            return self._emit_from_kf(now_ns)
        # Did we exceed lock-loss timeout?
        if self._last_seen_ts_ns is not None and (
            (now_ns - self._last_seen_ts_ns) / _NS_PER_SEC > _LOCK_LOSS_TIMEOUT_SEC
        ):
            self._logger.warning(
                "lock_loss",
                track_id=self._locked_track_id,
                last_seen_ts_ns=self._last_seen_ts_ns,
                age_ms=(now_ns - self._last_seen_ts_ns) / _NS_PER_MS,
            )
            self._last_lock_loss_ts_ns = now_ns
            self._locked_track_id = None
            self._state = _LockState.LOST
            self._kf = None
            return None
        # still holding -- emit frozen posterior (Pitfall 4)
        if not self._holding_logged:
            self._logger.warning(
                "lock_holding",
                track_id=self._locked_track_id,
                consecutive_misses=self._consecutive_misses,
            )
            self._holding_logged = True
        assert self._kf is not None, "_tick_holding called without _kf -- invariant violated"
        assert (
            self._locked_track_id is not None
        ), "_tick_holding called without locked_track_id -- invariant violated (W4 fix)"
        cx, cy = _KalmanWrapper.hold_posterior(self._kf)
        # Velocities frozen at last update (don't grow covariance). x_post may be
        # a column vector (4,1) before any update() ran -- ravel to flatten.
        x_post_flat = np.asarray(self._kf.x_post).ravel()
        return TrackedSubject(
            track_id=self._locked_track_id,  # W4: no `or 0` fallback -- asserted above
            subject_center_x_normalized=cx,
            subject_center_y_normalized=cy,
            velocity_x_norm_per_sec=float(x_post_flat[2]),
            velocity_y_norm_per_sec=float(x_post_flat[3]),
            timestamp_ns=now_ns,
        )

    def _try_reacquire(
        self, eligible: list[Detection], now_ns: int,
    ) -> TrackedSubject | None:
        """LOST: same heuristic as initial lock; transition through RE_ACQUIRING."""
        self._state = _LockState.RE_ACQUIRING
        return self._try_lock(eligible, now_ns)

    # ---------- helpers ----------
    def _find_match(self, eligible: list[Detection]) -> Detection | None:
        if self._locked_track_id is None:
            return None
        for d in eligible:
            if d.track_id == self._locked_track_id:
                return d
        return None

    def _compute_dt_sec(self, now_ns: int) -> float:
        if self._last_seen_ts_ns is None:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        delta_ns = now_ns - self._last_seen_ts_ns
        if delta_ns <= 0:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        return float(delta_ns) / _NS_PER_SEC

    def _emit_from_kf(self, now_ns: int) -> TrackedSubject:
        assert self._kf is not None
        assert self._locked_track_id is not None
        return TrackedSubject(
            track_id=self._locked_track_id,
            subject_center_x_normalized=float(np.clip(self._kf.x[0], _NORM_MIN, _NORM_MAX)),
            subject_center_y_normalized=float(np.clip(self._kf.x[1], _NORM_MIN, _NORM_MAX)),
            velocity_x_norm_per_sec=float(self._kf.x[2]),
            velocity_y_norm_per_sec=float(self._kf.x[3]),
            timestamp_ns=now_ns,
        )

    def _raise_if_latched(self) -> None:
        if self._latched_error is not None:
            raise self._latched_error
