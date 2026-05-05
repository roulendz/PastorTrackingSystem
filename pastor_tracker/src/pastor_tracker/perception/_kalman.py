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
    initial_x: float,
    initial_y: float,
    dt: float,
    *,
    process_noise_var: float = _KALMAN_PROCESS_NOISE_VAR,
    measurement_noise_var: float = _KALMAN_MEASUREMENT_NOISE_VAR,
    initial_vel_cov: float = _KALMAN_INITIAL_VEL_COV,
    initial_pos_cov: float = _KALMAN_INITIAL_POS_COV,
) -> KalmanFilter:
    """Construct a fresh 4-state CV Kalman filter (RESEARCH 04 Code Example 1).

    State vector: [x, y, vx, vy] in normalized frame coords.
    Measurement: [x, y] (centroid from PERC-02 weighted keypoint mean).

    WR-07: tuning constants are now keyword-only parameters with module-level
    Final defaults. ``_KalmanWrapper.reset_for_new_track`` forwards its
    instance fields here so a future Phase 7 dashboard live-tuning shim that
    constructs a ``_KalmanWrapper(process_noise_var=..., ...)`` actually
    takes effect (previously the dataclass fields were dead -- silently
    ignored because this factory read module constants directly).
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
    q_axis = Q_discrete_white_noise(dim=2, dt=dt, var=process_noise_var)
    kf.Q = block_diag(q_axis, q_axis)
    kf.R = np.eye(_KALMAN_DIM_Z, dtype=np.float64) * measurement_noise_var
    kf.x = np.array([initial_x, initial_y, 0.0, 0.0], dtype=np.float64)
    kf.P = np.diag(
        [
            initial_pos_cov,
            initial_pos_cov,
            initial_vel_cov,
            initial_vel_cov,
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

    WR-07: the dataclass fields below are now load-bearing -- they are
    forwarded into ``make_kalman_for_subject`` by ``reset_for_new_track``.
    Defaults come from the module-level ``Final`` constants so no behaviour
    change for existing callers (``SubjectTracker(_KalmanWrapper())``);
    a Phase 7 dashboard live-tuning shim that constructs
    ``_KalmanWrapper(process_noise_var=...)`` will see those values
    actually take effect.
    """

    process_noise_var: float = _KALMAN_PROCESS_NOISE_VAR
    measurement_noise_var: float = _KALMAN_MEASUREMENT_NOISE_VAR
    initial_vel_cov: float = _KALMAN_INITIAL_VEL_COV
    initial_pos_cov: float = _KALMAN_INITIAL_POS_COV

    def reset_for_new_track(
        self, initial_x: float, initial_y: float, dt: float,
    ) -> KalmanFilter:
        """Pitfall 2: brand-new KalmanFilter -- no carried state from previous track."""
        return make_kalman_for_subject(
            initial_x,
            initial_y,
            dt,
            process_noise_var=self.process_noise_var,
            measurement_noise_var=self.measurement_noise_var,
            initial_vel_cov=self.initial_vel_cov,
            initial_pos_cov=self.initial_pos_cov,
        )

    @staticmethod
    def hold_posterior(kf: KalmanFilter) -> tuple[float, float]:
        """Pitfall 4: return frozen posterior position (no predict, no update).

        Repeated calls return the same value -- covariance does NOT grow.

        Note: filterpy initializes ``kf.x_post`` as a column vector ``(dim_x, 1)``
        but flattens it to ``(dim_x,)`` after the first ``update()`` call. We
        flatten defensively so callers see a consistent scalar regardless of
        whether HOLDING is reached before any measurement was applied.
        """
        x_post = np.asarray(kf.x_post).ravel()
        return float(x_post[0]), float(x_post[1])
