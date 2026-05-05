"""PERC-02 weighted-keypoint-mean centroid helper (Phase 4 perception).

Pure function. No I/O. No state. Imported by both ``_pose_worker._translate``
(spawn worker, B1 fix) and ``subject_tracker.SubjectTracker`` (loop thread)
so the centroid math has exactly ONE implementation.

COCO-17 keypoint indices (verified in ultralytics):
    0  = nose
    5  = left shoulder
    6  = right shoulder
    11 = left hip
    12 = right hip

Weights (locked by CONTEXT.md / PERC-02): nose 0.4, shoulder mid 0.4, hip mid 0.2.
"""
from __future__ import annotations

from typing import Final

import numpy as np
import numpy.typing as npt

_NOSE_KP_INDEX: Final[int] = 0
_LEFT_SHOULDER_KP_INDEX: Final[int] = 5
_RIGHT_SHOULDER_KP_INDEX: Final[int] = 6
_LEFT_HIP_KP_INDEX: Final[int] = 11
_RIGHT_HIP_KP_INDEX: Final[int] = 12
_WEIGHT_NOSE: Final[float] = 0.4
_WEIGHT_SHOULDER_MID: Final[float] = 0.4
_WEIGHT_HIP_MID: Final[float] = 0.2
_REFERENCED_KP_INDICES: Final[tuple[int, ...]] = (
    _NOSE_KP_INDEX,
    _LEFT_SHOULDER_KP_INDEX,
    _RIGHT_SHOULDER_KP_INDEX,
    _LEFT_HIP_KP_INDEX,
    _RIGHT_HIP_KP_INDEX,
)

__all__ = ["weighted_keypoint_centroid"]


def weighted_keypoint_centroid(
    kp_xyn: npt.NDArray[np.float32],
    kp_conf: npt.NDArray[np.float32],
) -> tuple[float, float, float]:
    """PERC-02 weighted mean centroid + mean kp confidence.

    Args:
        kp_xyn:  shape ``(17, 2)`` -- per-keypoint normalized (x, y) for ONE person.
        kp_conf: shape ``(17,)``   -- per-keypoint confidence for the same person.

    Returns:
        ``(cx, cy, mean_kp_conf)`` -- ``cx`` and ``cy`` are the weighted mean
        of the 5 referenced keypoints; ``mean_kp_conf`` is the mean confidence
        across the SAME 5 referenced keypoints (NOT the full 17). SubjectTracker
        applies the conf < 0.55 floor on this scalar (PERC-02).
    """
    if kp_xyn.shape[0] < max(_REFERENCED_KP_INDICES) + 1:
        raise ValueError(
            f"kp_xyn must have at least {max(_REFERENCED_KP_INDICES) + 1} rows, "
            f"got shape {kp_xyn.shape}"
        )
    nose = kp_xyn[_NOSE_KP_INDEX]
    shoulder_mid = (
        kp_xyn[_LEFT_SHOULDER_KP_INDEX] + kp_xyn[_RIGHT_SHOULDER_KP_INDEX]
    ) * 0.5
    hip_mid = (
        kp_xyn[_LEFT_HIP_KP_INDEX] + kp_xyn[_RIGHT_HIP_KP_INDEX]
    ) * 0.5
    cx = (
        _WEIGHT_NOSE * nose[0]
        + _WEIGHT_SHOULDER_MID * shoulder_mid[0]
        + _WEIGHT_HIP_MID * hip_mid[0]
    )
    cy = (
        _WEIGHT_NOSE * nose[1]
        + _WEIGHT_SHOULDER_MID * shoulder_mid[1]
        + _WEIGHT_HIP_MID * hip_mid[1]
    )
    mean_conf = float(np.mean([kp_conf[i] for i in _REFERENCED_KP_INDICES]))
    return float(cx), float(cy), mean_conf
