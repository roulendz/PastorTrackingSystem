"""Frozen DTOs flowing between pipeline stages.

``Frame`` is a dataclass because it carries a ``numpy.ndarray`` (Pydantic v2
needs ``arbitrary_types_allowed=True`` for ndarrays and gives no real
validation; dataclass is honest). The remaining five DTOs are Pydantic v2
models so range validation can shape downstream contracts.

Mutation pattern (CLAUDE.md rule 9):
- Pydantic models: ``new = obj.model_copy(update={...})``
- ``Frame``: ``new = dataclasses.replace(obj, **kw)``

Note on ``Frame.image``: ``frozen=True`` on the dataclass prevents reassigning
the ``image`` attribute, but it cannot prevent in-place mutation of the
underlying numpy buffer. Callers MUST treat ``Frame.image`` as read-only;
later perception stages copy when they need to write.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

# BGR uint8 image buffer (OpenCV convention). Parameterised to keep mypy
# ``disallow_any_explicit`` happy — bare ``np.ndarray`` expands to
# ``np.ndarray[Any, Any]`` (Pitfall 2 in 01-RESEARCH.md).
ImageArray = npt.NDArray[np.uint8]

# Sentinel for the "no decision yet" intent, used by MotionAnalyzer in Phase 5.
MotionIntent = Literal["moving_left", "moving_right", "dwelling", "indeterminate"]


@dataclass(frozen=True, slots=True)
class Frame:
    """Captured camera frame. Side-effect-free DTO.

    ``image`` is BGR uint8 (OpenCV convention) of shape ``(height, width, 3)``.
    ``timestamp_ns`` is ``time.perf_counter_ns()`` at grab time.
    """

    image: ImageArray
    width: int
    height: int
    timestamp_ns: int


class _FrozenModel(BaseModel):
    """Base for all Pydantic DTOs in this module — frozen + extra-forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Detection(_FrozenModel):
    """A single person detection from YOLO11-pose, in normalized image coords."""

    subject_center_x_normalized: float = Field(ge=0.0, le=1.0)
    subject_center_y_normalized: float = Field(ge=0.0, le=1.0)
    mean_keypoint_confidence: float = Field(ge=0.0, le=1.0)
    bbox_x1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_x2_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y2_normalized: float = Field(ge=0.0, le=1.0)
    timestamp_ns: int = Field(ge=0)


class TrackedSubject(_FrozenModel):
    """Kalman-smoothed primary subject (Phase 4 produces; Phase 5 consumes)."""

    track_id: int = Field(ge=0)
    subject_center_x_normalized: float = Field(ge=0.0, le=1.0)
    subject_center_y_normalized: float = Field(ge=0.0, le=1.0)
    velocity_x_norm_per_sec: float
    velocity_y_norm_per_sec: float
    timestamp_ns: int = Field(ge=0)


class MotionState(_FrozenModel):
    """Output of the motion analyzer (Phase 5)."""

    intent: MotionIntent
    sustained_velocity_x_norm_per_sec: float
    timestamp_ns: int = Field(ge=0)


class FramingTarget(_FrozenModel):
    """Rule-of-thirds framing target in normalized x (Phase 5)."""

    target_x_normalized: float = Field(ge=0.0, le=1.0)
    timestamp_ns: int = Field(ge=0)


class MotorCommand(_FrozenModel):
    """Absolute-angle motor target. Construction-site applies hardware bounds."""

    target_angle_deg: float
    timestamp_ns: int = Field(ge=0)
