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


def test_frame_rejects_non_contiguous_image() -> None:
    """W-08: ``Frame.image`` must be C-contiguous -- Phase 4 zero-copy GPU.

    cv2.VideoCapture.read() always returns C-contiguous BGR uint8, so
    production is fine -- but a fake source that builds a frame via
    slicing (e.g. RGB->BGR view) produces a non-contiguous view. YOLO
    would either crash or trigger an implicit ascontiguousarray copy
    on the hot path.
    """
    base = np.zeros((4, 4, 6), dtype=np.uint8)
    view = base[:, :, ::2]  # non-contiguous view, shape (4, 4, 3)
    assert not view.flags["C_CONTIGUOUS"]
    with pytest.raises(ValueError, match="C-contiguous"):
        Frame(image=view, width=4, height=4, timestamp_ns=1)


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


# --- Phase 6 Wave-0 (Plan 06-01): PipelineSnapshot + PipelineState --------


def test_pipeline_state_literal_members() -> None:
    """PipelineState Literal must include exactly the 6 D-16 lifecycle members."""
    import typing

    from pastor_tracker.core.types import PipelineState

    expected = {"stopped", "running", "paused", "homing", "e_stopped", "quitting"}
    assert set(typing.get_args(PipelineState)) == expected


def test_pipeline_snapshot_construction_minimal() -> None:
    """Minimal construction: only required fields; optionals default to None."""
    from pastor_tracker.core.types import PipelineSnapshot

    snap = PipelineSnapshot(
        state="stopped",
        last_intent="indeterminate",
        motor_state="disconnected",
    )
    assert snap.state == "stopped"
    assert snap.last_intent == "indeterminate"
    assert snap.motor_state == "disconnected"
    assert snap.last_frame_ts_ns is None
    assert snap.last_target_x_normalized is None
    assert snap.last_pan_angle_deg is None
    assert snap.last_emitted_angle_deg is None


def test_pipeline_snapshot_construction_full() -> None:
    """All seven D-16 fields populated; round-trip through model_dump preserves values.

    Phase 7 Plan 01 additively extends the snapshot with three optional UI
    fields (``last_detection_confidence``, ``last_locked_track_id``,
    ``last_subject_bbox_normalized``); when unspecified they default to None
    and surface in the dump alongside the seven D-16 fields.
    """
    from pastor_tracker.core.types import PipelineSnapshot

    snap = PipelineSnapshot(
        state="running",
        last_frame_ts_ns=1_000_000_000,
        last_intent="moving_right",
        last_target_x_normalized=0.333,
        last_pan_angle_deg=12.5,
        last_emitted_angle_deg=12.3,
        motor_state="running",
    )
    dumped = snap.model_dump()
    assert dumped == {
        "state": "running",
        "last_frame_ts_ns": 1_000_000_000,
        "last_intent": "moving_right",
        "last_target_x_normalized": 0.333,
        "last_pan_angle_deg": 12.5,
        "last_emitted_angle_deg": 12.3,
        "motor_state": "running",
        # Phase 7 Plan 01 additions default to None when not supplied.
        "last_detection_confidence": None,
        "last_locked_track_id": None,
        "last_subject_bbox_normalized": None,
    }


def test_pipeline_snapshot_frozen() -> None:
    """frozen=True must reject post-construction mutation."""
    from pastor_tracker.core.types import PipelineSnapshot

    snap = PipelineSnapshot(
        state="stopped", last_intent="indeterminate", motor_state="disconnected",
    )
    with pytest.raises(ValidationError):
        snap.state = "paused"  # type: ignore[misc]


def test_pipeline_snapshot_extra_forbidden() -> None:
    """extra='forbid' must reject unknown keys."""
    from pastor_tracker.core.types import PipelineSnapshot

    with pytest.raises(ValidationError):
        PipelineSnapshot(
            state="stopped",
            last_intent="indeterminate",
            motor_state="disconnected",
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_pipeline_snapshot_target_x_range_validated() -> None:
    """last_target_x_normalized must be in [0.0, 1.0]; reject 1.5 and -0.1."""
    from pastor_tracker.core.types import PipelineSnapshot

    with pytest.raises(ValidationError):
        PipelineSnapshot(
            state="running",
            last_intent="moving_right",
            motor_state="running",
            last_target_x_normalized=1.5,
        )
    with pytest.raises(ValidationError):
        PipelineSnapshot(
            state="running",
            last_intent="moving_right",
            motor_state="running",
            last_target_x_normalized=-0.1,
        )


def test_pipeline_snapshot_last_frame_ts_ns_non_negative() -> None:
    """last_frame_ts_ns must be >= 0 when supplied."""
    from pastor_tracker.core.types import PipelineSnapshot

    with pytest.raises(ValidationError):
        PipelineSnapshot(
            state="running",
            last_intent="moving_right",
            motor_state="running",
            last_frame_ts_ns=-1,
        )


def test_detection_track_id_optional() -> None:
    """Phase 4 Plan 01: Detection.track_id is optional; None default; ge=0 rejects negatives."""
    det_default = Detection(
        subject_center_x_normalized=0.5,
        subject_center_y_normalized=0.5,
        mean_keypoint_confidence=0.9,
        bbox_x1_normalized=0.4,
        bbox_y1_normalized=0.4,
        bbox_x2_normalized=0.6,
        bbox_y2_normalized=0.6,
        timestamp_ns=1,
    )
    assert det_default.track_id is None
    det_with_id = Detection(
        subject_center_x_normalized=0.5,
        subject_center_y_normalized=0.5,
        mean_keypoint_confidence=0.9,
        bbox_x1_normalized=0.4,
        bbox_y1_normalized=0.4,
        bbox_x2_normalized=0.6,
        bbox_y2_normalized=0.6,
        timestamp_ns=1,
        track_id=42,
    )
    assert det_with_id.track_id == 42
    with pytest.raises(ValidationError):
        Detection(
            subject_center_x_normalized=0.5,
            subject_center_y_normalized=0.5,
            mean_keypoint_confidence=0.9,
            bbox_x1_normalized=0.4,
            bbox_y1_normalized=0.4,
            bbox_x2_normalized=0.6,
            bbox_y2_normalized=0.6,
            timestamp_ns=1,
            track_id=-1,
        )


# --- Phase 7 / Plan 01: PipelineSnapshot extensions (D-15 status + UI-01) ----


def test_pipeline_snapshot_phase7_fields_default_none() -> None:
    """The three Phase 7 additions default to None when unspecified."""
    from pastor_tracker.core.types import PipelineSnapshot

    snap = PipelineSnapshot(
        state="stopped",
        last_intent="indeterminate",
        motor_state="closed",
    )
    assert snap.last_detection_confidence is None
    assert snap.last_locked_track_id is None
    assert snap.last_subject_bbox_normalized is None


def test_pipeline_snapshot_phase7_fields_roundtrip() -> None:
    """All three additions round-trip through model_dump/model_validate."""
    from pastor_tracker.core.types import PipelineSnapshot

    bbox = (0.1, 0.2, 0.3, 0.4)
    snap = PipelineSnapshot(
        state="running",
        last_intent="moving_right",
        motor_state="running",
        last_detection_confidence=0.85,
        last_locked_track_id=3,
        last_subject_bbox_normalized=bbox,
    )
    dumped = snap.model_dump()
    roundtripped = PipelineSnapshot.model_validate(dumped)
    assert roundtripped == snap
    assert roundtripped.last_detection_confidence == pytest.approx(0.85)
    assert roundtripped.last_locked_track_id == 3
    assert roundtripped.last_subject_bbox_normalized == bbox


def test_pipeline_snapshot_phase7_confidence_upper_bound() -> None:
    """last_detection_confidence > 1.0 raises ValidationError (le=1.0)."""
    from pastor_tracker.core.types import PipelineSnapshot

    with pytest.raises(ValidationError, match="last_detection_confidence"):
        PipelineSnapshot(
            state="running",
            last_intent="moving_right",
            motor_state="running",
            last_detection_confidence=1.5,
        )


def test_pipeline_snapshot_phase7_track_id_non_negative() -> None:
    """last_locked_track_id < 0 raises ValidationError (ge=0)."""
    from pastor_tracker.core.types import PipelineSnapshot

    with pytest.raises(ValidationError, match="last_locked_track_id"):
        PipelineSnapshot(
            state="running",
            last_intent="moving_right",
            motor_state="running",
            last_locked_track_id=-1,
        )
