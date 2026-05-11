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
from pydantic import BaseModel, ConfigDict, Field, model_validator

# BGR uint8 image buffer (OpenCV convention). Parameterised to keep mypy
# ``disallow_any_explicit`` happy — bare ``np.ndarray`` expands to
# ``np.ndarray[Any, Any]`` (Pitfall 2 in 01-RESEARCH.md).
ImageArray = npt.NDArray[np.uint8]

# Sentinel for the "no decision yet" intent, used by MotionAnalyzer in Phase 5.
MotionIntent = Literal["moving_left", "moving_right", "dwelling", "indeterminate"]

# Frame.image contract: BGR uint8 of shape (H, W, 3). Named constants per
# CLAUDE.md rule 6 — no magic numbers in validation paths.
_IMAGE_NDIM_EXPECTED: int = 3  # H, W, channels
_IMAGE_CHANNELS_EXPECTED: int = 3  # B, G, R
_IMAGE_HEIGHT_AXIS: int = 0
_IMAGE_WIDTH_AXIS: int = 1
_IMAGE_CHANNEL_AXIS: int = 2
_TIMESTAMP_NS_MIN: int = 0
# B-03: Phase 4 (perception/YOLO11-pose via ultralytics) requires uint8
# BGR. The static ``ImageArray = NDArray[np.uint8]`` alias is purely
# compile-time -- runtime numpy arrays carry an arbitrary dtype, so the
# tiger-style boundary check belongs here in __post_init__ (CLAUDE.md
# rule 1: fail-fast at boundaries).
_IMAGE_DTYPE_EXPECTED: np.dtype[np.uint8] = np.dtype(np.uint8)


@dataclass(frozen=True, slots=True)
class Frame:
    """Captured camera frame. Side-effect-free DTO.

    ``image`` is BGR uint8 (OpenCV convention) of shape ``(height, width, 3)``.
    ``timestamp_ns`` is ``time.perf_counter_ns()`` at grab time.

    Tiger-style invariants enforced in ``__post_init__`` (CLAUDE.md rule 1):
    - ``image`` must be HxWx3 BGR (ndim==3, last axis size 3)
    - ``width`` / ``height`` must agree with ``image.shape``
    - ``timestamp_ns`` must be ``>= 0`` (matches sibling Pydantic DTOs)
    """

    image: ImageArray
    width: int
    height: int
    timestamp_ns: int

    def __post_init__(self) -> None:
        if self.image.ndim != _IMAGE_NDIM_EXPECTED:
            raise ValueError(
                f"Frame.image must be {_IMAGE_NDIM_EXPECTED}-D (HxWx3 BGR), "
                f"got ndim={self.image.ndim} shape={self.image.shape}"
            )
        if self.image.shape[_IMAGE_CHANNEL_AXIS] != _IMAGE_CHANNELS_EXPECTED:
            raise ValueError(
                f"Frame.image must have {_IMAGE_CHANNELS_EXPECTED} channels "
                f"(BGR), got shape {self.image.shape}"
            )
        if self.image.dtype != _IMAGE_DTYPE_EXPECTED:
            # B-03: Phase 4 perception (YOLO11-pose / ultralytics) expects
            # uint8 BGR. A float32 / int16 array would either crash the
            # detector or trigger a silent dtype copy on the GPU hot path.
            raise ValueError(
                f"Frame.image must have dtype {_IMAGE_DTYPE_EXPECTED}, "
                f"got dtype={self.image.dtype}"
            )
        if not self.image.flags["C_CONTIGUOUS"]:
            # W-08: Phase 4 YOLO11-pose / ultralytics requires C-contiguous
            # arrays for zero-copy GPU upload. cv2.VideoCapture.read() always
            # returns C-contiguous BGR uint8, so production is fine -- but a
            # FakeVideoSource that builds a frame via slicing (e.g.
            # ``frame[..., ::-1]`` for RGB->BGR) produces a non-contiguous
            # view. Catch at the boundary; let callers ``ascontiguousarray``
            # explicitly if they need to (CLAUDE.md rule 1).
            raise ValueError(
                f"Frame.image must be C-contiguous; got "
                f"strides={self.image.strides}, shape={self.image.shape}"
            )
        image_height = int(self.image.shape[_IMAGE_HEIGHT_AXIS])
        image_width = int(self.image.shape[_IMAGE_WIDTH_AXIS])
        if image_width != self.width or image_height != self.height:
            raise ValueError(
                f"Frame width/height ({self.width}x{self.height}) "
                f"disagree with image.shape ({image_width}x{image_height})"
            )
        if self.timestamp_ns < _TIMESTAMP_NS_MIN:
            raise ValueError(
                f"timestamp_ns must be >= {_TIMESTAMP_NS_MIN}, "
                f"got {self.timestamp_ns}"
            )


class _FrozenModel(BaseModel):
    """Base for all Pydantic DTOs in this module — frozen + extra-forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Detection(_FrozenModel):
    """A single person detection from YOLO11-pose, in normalized image coords.

    Cross-field invariants (WR-03 — tiger-style fail-fast on degenerate bboxes):
    - ``bbox_x2_normalized > bbox_x1_normalized``
    - ``bbox_y2_normalized > bbox_y1_normalized``

    - ``track_id`` is the BoT-SORT track identifier when present; None on the
      very first frame before BoT-SORT initializes (RESEARCH 04 Pitfall 3,
      Open Question 6) and on synthetic/fake detections that bypass tracking.
    """

    subject_center_x_normalized: float = Field(ge=0.0, le=1.0)
    subject_center_y_normalized: float = Field(ge=0.0, le=1.0)
    mean_keypoint_confidence: float = Field(ge=0.0, le=1.0)
    bbox_x1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_x2_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y2_normalized: float = Field(ge=0.0, le=1.0)
    timestamp_ns: int = Field(ge=0)
    track_id: int | None = Field(ge=0, default=None)

    @model_validator(mode="after")
    def _bbox_well_ordered(self) -> Detection:
        if self.bbox_x2_normalized <= self.bbox_x1_normalized:
            raise ValueError(
                f"bbox_x2_normalized ({self.bbox_x2_normalized}) must exceed "
                f"bbox_x1_normalized ({self.bbox_x1_normalized})"
            )
        if self.bbox_y2_normalized <= self.bbox_y1_normalized:
            raise ValueError(
                f"bbox_y2_normalized ({self.bbox_y2_normalized}) must exceed "
                f"bbox_y1_normalized ({self.bbox_y1_normalized})"
            )
        return self


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


# Phase 6 -- pipeline orchestrator surface (D-16, D-17).
# PipelineState mirrors pastor_tracker.pipeline._PipelineState members but is
# a string Literal so PipelineSnapshot remains importable with no dependency
# on pipeline.py (avoids the import cycle: pipeline.py -> core.types -> pipeline.py).
PipelineState = Literal[
    "stopped", "running", "paused", "homing", "e_stopped", "quitting",
]


class PipelineSnapshot(_FrozenModel):
    """Read-only snapshot for the Phase 7 dashboard (D-16).

    Built cheaply from Pipeline._PipelineCache on every snapshot() call.
    Frozen + extra-forbidden per the project DTO discipline (CLAUDE.md
    rule 9). Field set is the 7 D-16 names + 3 Phase 7 additions
    (CONTEXT.md "Specific Ideas" authorizes; D-XX unchanged).

    Field semantics:
        * state: Literal -- one of the 6 valid lifecycle states.
        * last_frame_ts_ns: optional perf_counter_ns; None before first tick.
        * last_intent: always present; MotionIntent already includes
          "indeterminate" for the no-decision-yet case.
        * last_target_x_normalized: range-validated [0, 1]; None when no
          target yet.
        * last_pan_angle_deg, last_emitted_angle_deg: unbounded (motor angle
          limits live in Config; the snapshot just reflects what happened).
        * motor_state: opaque string -- the value of ArduinoMotor.state.value.
          Phase 6 keeps _MotorState private to the Arduino subsystem; the
          snapshot exposes its .value so Phase 7 can render text without
          importing _MotorState.
        * last_detection_confidence: highest mean_keypoint_confidence of the
          most recent non-empty detection batch; range [0, 1]. None until the
          first non-empty tick. Held across empty-detections ticks (mirror of
          the last_target_x_normalized None-as-stale convention).
        * last_locked_track_id: BoT-SORT track id of the same max-confidence
          Detection; None until first non-empty tick or when that detection
          carries no track id.
        * last_subject_bbox_normalized: (x1, y1, x2, y2) bbox of the same
          max-confidence Detection in normalized [0, 1] coords; None until
          first non-empty tick. Phase 7 UI-01 draws this as the "I locked
          someone" affordance.
    """

    state: PipelineState
    last_frame_ts_ns: int | None = Field(default=None, ge=0)
    last_intent: MotionIntent
    last_target_x_normalized: float | None = Field(default=None, ge=0.0, le=1.0)
    last_pan_angle_deg: float | None = None
    last_emitted_angle_deg: float | None = None
    motor_state: str
    # --- Plan 07-01 additions (Phase 7 D-15 status panel + UI-01 overlays).
    # Authorized by CONTEXT.md "Specific Ideas": "Detection / TrackedSubject
    # ... open to plan; no D-XX touch". RESEARCH §6 documents the rationale:
    # UI cannot compute these from latest_frame alone (Pipeline does not
    # surface Detection / TrackedSubject otherwise).
    last_detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    last_locked_track_id: int | None = Field(default=None, ge=0)
    last_subject_bbox_normalized: tuple[float, float, float, float] | None = None
