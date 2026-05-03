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
    """``dataclasses.replace`` is the documented mutation path."""
    frame = Frame(image=_make_image(), width=4, height=4, timestamp_ns=1)
    bigger = dataclasses.replace(frame, width=8)
    assert frame.width == 4
    assert bigger.width == 8


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
