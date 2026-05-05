"""Tests for ``pastor_tracker.core.types`` — frozen DTOs reject mutation."""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from pydantic import ValidationError

from pastor_tracker.core.types import (
    Detection,
    Frame,
    FramingTarget,
    MotionState,
    MotorCommand,
    TrackedSubject,
)


def _make_image() -> np.ndarray:
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_frame_is_frozen_dataclass() -> None:
    frame = Frame(image=_make_image(), width=4, height=4, timestamp_ns=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.width = 8  # type: ignore[misc]


def test_frame_replace_returns_new_instance() -> None:
    """``dataclasses.replace`` is the documented mutation path.

    NOTE: ``replace`` re-runs ``__post_init__`` so we must keep image/width
    coherent (WR-02 guard rejects mismatched scalars).
    """
    frame = Frame(image=_make_image(), width=4, height=4, timestamp_ns=1)
    bigger_image = np.zeros((4, 8, 3), dtype=np.uint8)
    bigger = dataclasses.replace(frame, image=bigger_image, width=8)
    assert frame.width == 4
    assert bigger.width == 8


def test_frame_rejects_dimension_mismatch() -> None:
    """WR-02: ``width``/``height`` scalars MUST agree with ``image.shape``."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match=r"disagree with image\.shape"):
        Frame(image=img, width=1920, height=1080, timestamp_ns=1)


def test_frame_rejects_non_bgr_image() -> None:
    """WR-02: ``image`` must be HxWx3 BGR — reject 2-D or wrong channel count."""
    flat = np.zeros((4, 4), dtype=np.uint8)  # ndim==2
    with pytest.raises(ValueError, match="HxWx3 BGR"):
        Frame(image=flat, width=4, height=4, timestamp_ns=1)
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)  # 4 channels
    with pytest.raises(ValueError, match="channels"):
        Frame(image=rgba, width=4, height=4, timestamp_ns=1)


def test_frame_rejects_negative_timestamp() -> None:
    """WR-02: ``timestamp_ns`` must be ``>= 0`` — match sibling Pydantic DTOs."""
    with pytest.raises(ValueError, match="timestamp_ns"):
        Frame(image=_make_image(), width=4, height=4, timestamp_ns=-1)


def test_frame_rejects_non_uint8_dtype() -> None:
    """B-03: ``Frame.image`` must be ``np.uint8`` -- Phase 4 YOLO contract.

    A float32 / int16 array would either crash YOLO11-pose or trigger a
    silent dtype copy on the GPU hot path. Catch at the boundary.
    """
    bad = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="dtype"):
        Frame(
            image=bad,  # type: ignore[arg-type]
            width=4,
            height=4,
            timestamp_ns=1,
        )


def test_detection_rejects_mutation() -> None:
    det = Detection(
        subject_center_x_normalized=0.5,
        subject_center_y_normalized=0.5,
        mean_keypoint_confidence=0.9,
        bbox_x1_normalized=0.4,
        bbox_y1_normalized=0.4,
        bbox_x2_normalized=0.6,
        bbox_y2_normalized=0.6,
        timestamp_ns=1,
    )
    with pytest.raises(ValidationError):
        det.mean_keypoint_confidence = 0.1  # type: ignore[misc]


def test_detection_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Detection(
            subject_center_x_normalized=1.5,
            subject_center_y_normalized=0.5,
            mean_keypoint_confidence=0.9,
            bbox_x1_normalized=0.4,
            bbox_y1_normalized=0.4,
            bbox_x2_normalized=0.6,
            bbox_y2_normalized=0.6,
            timestamp_ns=1,
        )


def test_detection_rejects_inverted_bbox_x() -> None:
    """WR-03: ``bbox_x2`` must strictly exceed ``bbox_x1``."""
    with pytest.raises(ValidationError, match="bbox_x2_normalized"):
        Detection(
            subject_center_x_normalized=0.5,
            subject_center_y_normalized=0.5,
            mean_keypoint_confidence=0.9,
            bbox_x1_normalized=0.7,  # > x2 — inverted
            bbox_y1_normalized=0.4,
            bbox_x2_normalized=0.3,
            bbox_y2_normalized=0.6,
            timestamp_ns=1,
        )


def test_detection_rejects_zero_area_bbox() -> None:
    """WR-03: degenerate (x2 == x1 or y2 == y1) bboxes are rejected."""
    with pytest.raises(ValidationError, match="bbox_x2_normalized"):
        Detection(
            subject_center_x_normalized=0.5,
            subject_center_y_normalized=0.5,
            mean_keypoint_confidence=0.9,
            bbox_x1_normalized=0.5,  # == x2 — zero width
            bbox_y1_normalized=0.4,
            bbox_x2_normalized=0.5,
            bbox_y2_normalized=0.6,
            timestamp_ns=1,
        )


def test_detection_rejects_inverted_bbox_y() -> None:
    """WR-03: ``bbox_y2`` must strictly exceed ``bbox_y1``."""
    with pytest.raises(ValidationError, match="bbox_y2_normalized"):
        Detection(
            subject_center_x_normalized=0.5,
            subject_center_y_normalized=0.5,
            mean_keypoint_confidence=0.9,
            bbox_x1_normalized=0.3,
            bbox_y1_normalized=0.8,  # > y2 — inverted
            bbox_x2_normalized=0.6,
            bbox_y2_normalized=0.2,
            timestamp_ns=1,
        )


def test_tracked_subject_rejects_mutation() -> None:
    sub = TrackedSubject(
        track_id=7,
        subject_center_x_normalized=0.5,
        subject_center_y_normalized=0.5,
        velocity_x_norm_per_sec=0.0,
        velocity_y_norm_per_sec=0.0,
        timestamp_ns=1,
    )
    with pytest.raises(ValidationError):
        sub.track_id = 8  # type: ignore[misc]


def test_motion_state_rejects_mutation_and_bad_intent() -> None:
    ms = MotionState(
        intent="dwelling",
        sustained_velocity_x_norm_per_sec=0.0,
        timestamp_ns=1,
    )
    with pytest.raises(ValidationError):
        ms.intent = "moving_right"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        MotionState(
            intent="floating",  # type: ignore[arg-type]
            sustained_velocity_x_norm_per_sec=0.0,
            timestamp_ns=1,
        )


def test_framing_target_rejects_mutation() -> None:
    ft = FramingTarget(target_x_normalized=0.333, timestamp_ns=1)
    with pytest.raises(ValidationError):
        ft.target_x_normalized = 0.5  # type: ignore[misc]


def test_motor_command_rejects_mutation() -> None:
    mc = MotorCommand(target_angle_deg=12.5, timestamp_ns=1)
    with pytest.raises(ValidationError):
        mc.target_angle_deg = 0.0  # type: ignore[misc]
