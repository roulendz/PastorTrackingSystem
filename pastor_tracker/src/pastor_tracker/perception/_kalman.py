"""filterpy 4-state CV Kalman wrapper (Phase 4 perception).

Pure-math helper used by ``SubjectTracker``. Mirrors the ``core/damping.py``
idiom: a frozen dataclass holding static knobs, a factory that constructs a
fresh ``filterpy.kalman.KalmanFilter`` per locked subject, and pure-step
methods.

CLAUDE.md hard rule: tests drive the real ``KalmanFilter`` -- no mocks.
RESEARCH 04 Pitfalls covered:
    * Pitfall 2: full reset on re-acquisition -- ``reset_for_new_track``
      constructs a brand-new ``KalmanFilter`` instance; no shared mutable
      state.
    * Pitfall 4: HOLDING state freezes the posterior -- ``hold_posterior``
      returns ``kf.x_post`` WITHOUT calling ``predict()`` (covariance must
      not grow during the gap).
    * Open Question 2: Q/R live as module-level ``Final`` constants here,
      NOT in ``Config`` (live tuning is Phase 7 dashboard scope).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from filterpy.common import Q_discrete_white_noise
from filterpy.kalman import KalmanFilter
from scipy.linalg import block_diag

# RESEARCH 04 Code Example 1 + Open Question 2: starting values for v1.
# Tune on stage during Phase 8 / QA-04 if visible artifacts appear.
_KALMAN_PROCESS_NOISE_VAR: Final[float] = 1e-3
_KALMAN_MEASUREMENT_NOISE_VAR: Final[float] = 1e-4
_KALMAN_INITIAL_VEL_COV: Final[float] = 1.0
_KALMAN_INITIAL_POS_COV: Final[float] = 1e-3

_KALMAN_DIM_X: Final[int] = 4   # [x, y, vx, vy]
_KALMAN_DIM_Z: Final[int] = 2   # observed [x, y]
_NS_PER_SEC: Final[float] = 1_000_000_000.0


def make_kalman_for_subject(
    initial_x: float, initial_y: float, dt: float,
) -> KalmanFilter:
    """Construct a fresh 4-state CV Kalman filter (RESEARCH 04 Code Example 1).

    State vector: [x, y, vx, vy] in normalized frame coords.
    Measurement: [x, y] (centroid from PERC-02 weighted keypoint mean).
    """
    if dt <= 0.0:
        raise ValueError(f"dt must be > 0, got {dt}")
    kf = KalmanFilter(dim_x=_KALMAN_DIM_X, dim_z=_KALMAN_DIM_Z)
    kf.F = np.array(
        [
            [1.0, 0.0, dt, 0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    kf.H = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    q_axis = Q_discrete_white_noise(dim=2, dt=dt, var=_KALMAN_PROCESS_NOISE_VAR)
    kf.Q = block_diag(q_axis, q_axis)
    kf.R = np.eye(_KALMAN_DIM_Z, dtype=np.float64) * _KALMAN_MEASUREMENT_NOISE_VAR
    kf.x = np.array([initial_x, initial_y, 0.0, 0.0], dtype=np.float64)
    kf.P = np.diag(
        [
            _KALMAN_INITIAL_POS_COV,
            _KALMAN_INITIAL_POS_COV,
            _KALMAN_INITIAL_VEL_COV,
            _KALMAN_INITIAL_VEL_COV,
        ]
    )
    return kf


def predict_with_dt(kf: KalmanFilter, dt: float) -> None:
    """Recompute F[0,2] = F[1,3] = dt then call kf.predict() (RESEARCH 04 Code Example 2).

    Note: Q technically also depends on dt; at 30 fps with dt jitter < 5 ms,
    recomputation is overkill (RESEARCH 04 Code Example 2 docstring). Skip.
    """
    if dt <= 0.0:
        raise ValueError(f"dt must be > 0, got {dt}")
    kf.F[0, 2] = dt
    kf.F[1, 3] = dt
    kf.predict()


@dataclass(frozen=True, slots=True)
class _KalmanWrapper:
    """Stateless config holder. Construct once; build fresh KalmanFilter per track.

    Mirrors ``CriticallyDampedFollower`` shape from ``core/damping.py``.
    """

    process_noise_var: float = _KALMAN_PROCESS_NOISE_VAR
    measurement_noise_var: float = _KALMAN_MEASUREMENT_NOISE_VAR
    initial_vel_cov: float = _KALMAN_INITIAL_VEL_COV

    def reset_for_new_track(
        self, initial_x: float, initial_y: float, dt: float,
    ) -> KalmanFilter:
        """Pitfall 2: brand-new KalmanFilter -- no carried state from previous track."""
        return make_kalman_for_subject(initial_x, initial_y, dt)

    @staticmethod
    def hold_posterior(kf: KalmanFilter) -> tuple[float, float]:
        """Pitfall 4: return frozen posterior position (no predict, no update).

        Repeated calls return the same value -- covariance does NOT grow.
        """
        return float(kf.x_post[0]), float(kf.x_post[1])
