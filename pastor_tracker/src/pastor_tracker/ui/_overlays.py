"""Pure coordinate helpers for the Phase 7 dashboard overlay layer.

This module is the pure-core boundary of the UI subsystem: every function
is a typed transform from PipelineSnapshot / Frame + dimensions to either
a tuple of pixel coordinates (which the dashboard then feeds into
dpg.draw_*) or a flat float32 numpy buffer (texture upload payload).

No DearPyGui calls. No state. Unit-testable in isolation under pytest
without spawning a DPG context (CLAUDE.md "pure-core / dirty-edges").
"""
from __future__ import annotations

from typing import Final

import cv2
import numpy as np
import numpy.typing as npt

from pastor_tracker.core.types import Frame, PipelineSnapshot

# Em-dash sentinel for "value not yet known" in angle / lock formatting.
# Kept as a named module-level Final to satisfy CLAUDE.md rule 6 and to
# make the rendering contract grep-discoverable.
_UNKNOWN_VALUE_GLYPH: Final[str] = "—"

# RGBA pixel byte width -- documented constant for the flat-buffer
# texture upload helper. cv2.COLOR_BGR2RGBA produces 4-channel output.
_BYTES_PER_PIXEL_RGBA: Final[int] = 4

# Per-channel normalization divisor: uint8 -> float32 in [0.0, 1.0].
_UINT8_NORMALIZATION_DIVISOR: Final[float] = 255.0


def third_line_pixels(
    snap: PipelineSnapshot,
    image_w_px: int,
    image_h_px: int,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Vertical line at the framing target's pixel x.

    Returns (top_point, bottom_point) tuple of pixel coords, or None when
    the snapshot has no target yet (``last_target_x_normalized is None``).
    """
    if snap.last_target_x_normalized is None:
        return None
    x_px = round(snap.last_target_x_normalized * image_w_px)
    return ((x_px, 0), (x_px, image_h_px))


def bbox_rect_pixels(
    snap: PipelineSnapshot,
    image_w_px: int,
    image_h_px: int,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Pixel-coord bbox (pmin, pmax) from the snapshot's normalized bbox.

    Returns None when ``snap.last_subject_bbox_normalized is None``.
    Coordinates are integer-rounded toward nearest.
    """
    bbox = snap.last_subject_bbox_normalized
    if bbox is None:
        return None
    x1_norm, y1_norm, x2_norm, y2_norm = bbox
    pmin = (round(x1_norm * image_w_px), round(y1_norm * image_h_px))
    pmax = (round(x2_norm * image_w_px), round(y2_norm * image_h_px))
    return (pmin, pmax)


def angle_text(snap: PipelineSnapshot) -> str:
    """Formatted ``"pan: ±NN.NN°  emit: ±NN.NN°"`` string for the drawlist.

    Em-dash sentinel for None per RESEARCH §Pattern 4.
    """
    cur = (
        _UNKNOWN_VALUE_GLYPH
        if snap.last_pan_angle_deg is None
        else f"{snap.last_pan_angle_deg:+.2f}"
    )
    emit = (
        _UNKNOWN_VALUE_GLYPH
        if snap.last_emitted_angle_deg is None
        else f"{snap.last_emitted_angle_deg:+.2f}"
    )
    return f"pan: {cur}°  emit: {emit}°"


def id_lock_text(snap: PipelineSnapshot) -> str:
    """Operator-facing "ID: <id>" badge string; "ID: NONE" when no lock."""
    if snap.last_locked_track_id is None:
        return "ID: NONE"
    return f"ID: {snap.last_locked_track_id}"


def bgr_frame_to_rgba_float_flat(
    frame: Frame,
    preview_w_px: int,
    preview_h_px: int,
) -> npt.NDArray[np.float32]:
    """Convert a BGR uint8 Frame to a DearPyGui raw_texture-ready flat buffer.

    Pipeline (RESEARCH §Pattern 3 + D-06):
        1. ``cv2.cvtColor(BGR -> RGBA)``
        2. ``cv2.resize`` to (preview_w, preview_h) when the input is larger
           in either dimension (``INTER_AREA`` -- the documented quality
           choice for downscale).
        3. ``.astype(float32) / 255.0`` -- normalize to [0.0, 1.0].
        4. ``.flatten()`` -- 1-D buffer of length W*H*4.

    Used by Dashboard._upload_texture per render tick.
    """
    rgba = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGBA)
    if frame.width > preview_w_px or frame.height > preview_h_px:
        rgba = cv2.resize(
            rgba,
            (preview_w_px, preview_h_px),
            interpolation=cv2.INTER_AREA,
        )
    normalized = rgba.astype(np.float32) / _UINT8_NORMALIZATION_DIVISOR
    return normalized.flatten()


__all__ = [
    "angle_text",
    "bbox_rect_pixels",
    "bgr_frame_to_rgba_float_flat",
    "id_lock_text",
    "third_line_pixels",
]
