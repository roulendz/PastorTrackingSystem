"""Pure coord helper tests for ``pastor_tracker.ui._overlays`` (Plan 07-02 Task 1).

These tests assert the pure-core boundary: every helper is a typed transform
from PipelineSnapshot / Frame + image dims to either a tuple of pixel
coordinates or a flat float32 numpy buffer. The module under test MUST
NOT import dearpygui; if a future refactor introduces such a dependency,
``test_overlays_module_has_no_dearpygui_dependency`` fails loudly.
"""
from __future__ import annotations

import numpy as np

from pastor_tracker.core.types import Frame, PipelineSnapshot
from pastor_tracker.ui._overlays import (
    angle_text,
    bbox_rect_pixels,
    bgr_frame_to_rgba_float_flat,
    id_lock_text,
    third_line_pixels,
)


def _make_snapshot(
    *,
    last_target_x_normalized: float | None = None,
    last_subject_bbox_normalized: tuple[float, float, float, float] | None = None,
    last_pan_angle_deg: float | None = None,
    last_emitted_angle_deg: float | None = None,
    last_locked_track_id: int | None = None,
) -> PipelineSnapshot:
    """Build a PipelineSnapshot with the fields under test; rest defaulted."""
    return PipelineSnapshot(
        state="running",
        last_intent="dwelling",
        motor_state="running",
        last_target_x_normalized=last_target_x_normalized,
        last_subject_bbox_normalized=last_subject_bbox_normalized,
        last_pan_angle_deg=last_pan_angle_deg,
        last_emitted_angle_deg=last_emitted_angle_deg,
        last_locked_track_id=last_locked_track_id,
    )


def _make_frame(width: int, height: int) -> Frame:
    """Construct a black BGR uint8 C-contiguous Frame at the requested dims."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    return Frame(image=image, width=width, height=height, timestamp_ns=1)


# ---- third_line_pixels --------------------------------------------------


def test_third_line_pixels_center() -> None:
    snap = _make_snapshot(last_target_x_normalized=0.5)
    assert third_line_pixels(snap, 960, 540) == ((480, 0), (480, 540))


def test_third_line_pixels_none_when_target_missing() -> None:
    snap = _make_snapshot(last_target_x_normalized=None)
    assert third_line_pixels(snap, 960, 540) is None


def test_third_line_pixels_left_edge() -> None:
    snap = _make_snapshot(last_target_x_normalized=0.0)
    assert third_line_pixels(snap, 960, 540) == ((0, 0), (0, 540))


def test_third_line_pixels_right_edge() -> None:
    snap = _make_snapshot(last_target_x_normalized=1.0)
    assert third_line_pixels(snap, 960, 540) == ((960, 0), (960, 540))


# ---- bbox_rect_pixels ---------------------------------------------------


def test_bbox_rect_pixels_basic() -> None:
    snap = _make_snapshot(last_subject_bbox_normalized=(0.1, 0.2, 0.3, 0.4))
    assert bbox_rect_pixels(snap, 1000, 500) == ((100, 100), (300, 200))


def test_bbox_rect_pixels_none_when_bbox_missing() -> None:
    snap = _make_snapshot(last_subject_bbox_normalized=None)
    assert bbox_rect_pixels(snap, 1000, 500) is None


# ---- angle_text ---------------------------------------------------------


def test_angle_text_formats_pan_and_emit() -> None:
    snap = _make_snapshot(last_pan_angle_deg=12.3, last_emitted_angle_deg=11.8)
    text = angle_text(snap)
    assert "12.30" in text
    assert "11.80" in text
    assert "pan:" in text
    assert "emit:" in text


def test_angle_text_em_dash_when_unknown() -> None:
    snap = _make_snapshot(last_pan_angle_deg=None, last_emitted_angle_deg=None)
    text = angle_text(snap)
    # U+2014 em dash sentinel per RESEARCH §Pattern 4.
    assert "—" in text


# ---- id_lock_text -------------------------------------------------------


def test_id_lock_text_none() -> None:
    snap = _make_snapshot(last_locked_track_id=None)
    assert id_lock_text(snap) == "ID: NONE"


def test_id_lock_text_value() -> None:
    snap = _make_snapshot(last_locked_track_id=42)
    assert id_lock_text(snap) == "ID: 42"


# ---- bgr_frame_to_rgba_float_flat --------------------------------------


def test_bgr_frame_to_rgba_float_flat_native_size() -> None:
    frame = _make_frame(960, 540)
    buf = bgr_frame_to_rgba_float_flat(frame, 960, 540)
    assert buf.dtype == np.float32
    assert buf.shape == (960 * 540 * 4,)
    # All-zero BGR with cv2.COLOR_BGR2RGBA -> RGB channels 0.0, alpha channel 1.0.
    # min = 0.0 (any RGB pixel); max = 1.0 (any alpha pixel).
    assert float(buf.min()) == 0.0
    assert float(buf.max()) == 1.0


def test_bgr_frame_to_rgba_float_flat_downscale() -> None:
    frame = _make_frame(1920, 1080)
    buf = bgr_frame_to_rgba_float_flat(frame, 960, 540)
    assert buf.dtype == np.float32
    assert buf.shape == (960 * 540 * 4,)


def test_bgr_frame_to_rgba_float_flat_values_in_unit_range() -> None:
    # Half-saturated pixels -> values ~0.5 in float32 range [0, 1].
    image = np.full((540, 960, 3), 128, dtype=np.uint8)
    frame = Frame(image=image, width=960, height=540, timestamp_ns=1)
    buf = bgr_frame_to_rgba_float_flat(frame, 960, 540)
    # RGBA: R, G, B come from BGR conversion (=128/255). Alpha=255/255=1.0.
    assert 0.0 <= float(buf.min()) <= 1.0
    assert 0.0 <= float(buf.max()) <= 1.0


# ---- pure-core boundary assertion --------------------------------------


def test_overlays_module_has_no_dearpygui_dependency() -> None:
    """Source-level grep gate: _overlays.py imports nothing from dearpygui."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "pastor_tracker" / "ui" / "_overlays.py"
    text = src.read_text(encoding="utf-8")
    assert "import dearpygui" not in text
    assert "from dearpygui" not in text
