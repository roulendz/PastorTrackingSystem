"""Pure FOV math: normalized image-x ↔ off-axis horizontal angle (degrees).

Pinhole camera model. No state, no I/O. All inputs are floats in well-defined
ranges; out-of-range inputs raise ``ValueError`` (tiger-style fail-fast).

Sources:
- scratchapixel.com — Perspective Projection (pinhole derivation)
- commonlands.com — FOV calculator (HFOV = 2 * arctan(w / 2f))
"""
from __future__ import annotations

import math

# Named constants (CLAUDE.md rule 6 — no magic numbers).
NORMALIZED_X_MIN: float = 0.0
NORMALIZED_X_MAX: float = 1.0
FOV_DEG_MIN_EXCLUSIVE: float = 0.0
FOV_DEG_MAX_EXCLUSIVE: float = 180.0
HALF: float = 0.5
NORMALIZED_RANGE: float = NORMALIZED_X_MAX - NORMALIZED_X_MIN  # 1.0
TWO: float = 2.0


def normalized_x_to_angle_deg(
    normalized_x: float, horizontal_fov_deg: float
) -> float:
    """Map normalized image x in [0, 1] to off-axis horizontal angle in degrees.

    nx = 0.0   -> angle = -fov/2   (left edge)
    nx = 0.5   -> angle = 0        (optical centre)
    nx = 1.0   -> angle = +fov/2   (right edge)
    """
    if not NORMALIZED_X_MIN <= normalized_x <= NORMALIZED_X_MAX:
        raise ValueError(
            f"normalized_x out of [{NORMALIZED_X_MIN}, {NORMALIZED_X_MAX}]: "
            f"{normalized_x}"
        )
    if not FOV_DEG_MIN_EXCLUSIVE < horizontal_fov_deg < FOV_DEG_MAX_EXCLUSIVE:
        raise ValueError(
            f"horizontal_fov_deg out of "
            f"({FOV_DEG_MIN_EXCLUSIVE}, {FOV_DEG_MAX_EXCLUSIVE}): "
            f"{horizontal_fov_deg}"
        )
    half_fov_rad = math.radians(horizontal_fov_deg * HALF)
    offset = (TWO * normalized_x) - NORMALIZED_RANGE  # in [-1, +1]
    return math.degrees(math.atan(offset * math.tan(half_fov_rad)))


def angle_deg_to_normalized_x(
    angle_deg: float, horizontal_fov_deg: float
) -> float:
    """Inverse of :func:`normalized_x_to_angle_deg`."""
    if not FOV_DEG_MIN_EXCLUSIVE < horizontal_fov_deg < FOV_DEG_MAX_EXCLUSIVE:
        raise ValueError(
            f"horizontal_fov_deg out of "
            f"({FOV_DEG_MIN_EXCLUSIVE}, {FOV_DEG_MAX_EXCLUSIVE}): "
            f"{horizontal_fov_deg}"
        )
    half_fov_rad = math.radians(horizontal_fov_deg * HALF)
    offset = math.tan(math.radians(angle_deg)) / math.tan(half_fov_rad)
    return (offset + NORMALIZED_RANGE) * HALF
