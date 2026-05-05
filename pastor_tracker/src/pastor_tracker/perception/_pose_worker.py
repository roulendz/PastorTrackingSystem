"""Worker target for ProcessPoolExecutor (Phase 4 perception).

This module is imported by the spawned child interpreter (Windows: spawn-only).
Top-level functions only -- no closures, no class instances bound at import time.

Invariants (RESEARCH 04 Pitfalls 3 + 4):
    * Worker process loads YOLO model EXACTLY ONCE on first call;
      reuse keeps BoT-SORT track-id continuity (`persist=True` semantics
      live on the YOLO instance, not on disk).
    * Never re-instantiate `YOLO(...)` mid-run -- track-id state is bound
      to the model instance.
    * Top-level module functions only -- closures don't pickle across spawn.

`model_path` and `botsort_yaml_path` are passed as `str` (NOT `Path`) across
the pickle boundary -- both ultralytics' `YOLO(...)` and the `tracker=` kwarg
accept strings, and strings pickle without filesystem-handle baggage on Windows
spawn.
"""
from __future__ import annotations

import logging
from multiprocessing import shared_memory
from typing import Final

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

# Lazy module globals -- one model + one attached shm block per worker process.
_model: object | None = None  # ultralytics.YOLO instance after first call
_shm: shared_memory.SharedMemory | None = None
_shm_name: str | None = None

# Pitfall 10: warm with a 640x640 zero-image; matches YOLO's default imgsz.
_WARMUP_IMG_SIZE: Final[int] = 640


class _PoseDetection(BaseModel):
    """Picklable per-detection payload returned by ``infer``.

    Mirrors the field names of ``core.types.Detection`` so the orchestrator
    can construct ``Detection`` instances directly via ``model_validate``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_center_x_normalized: float = Field(ge=0.0, le=1.0)
    subject_center_y_normalized: float = Field(ge=0.0, le=1.0)
    mean_keypoint_confidence: float = Field(ge=0.0, le=1.0)
    bbox_x1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y1_normalized: float = Field(ge=0.0, le=1.0)
    bbox_x2_normalized: float = Field(ge=0.0, le=1.0)
    bbox_y2_normalized: float = Field(ge=0.0, le=1.0)
    timestamp_ns: int = Field(ge=0)
    track_id: int | None = Field(ge=0, default=None)


class PoseEngineResult(BaseModel):
    """Picklable per-frame worker result. Returned by ``infer``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detections: list[_PoseDetection] = Field(default_factory=list)


def _silence_ultralytics_stdout() -> None:
    """Pitfall 6: ultralytics chatter pollutes structlog JSON. Silence at WARN."""
    logging.getLogger("ultralytics").setLevel(logging.WARNING)


def _ensure_model(model_path: str, device: str) -> object:
    """Lazy-load YOLO once per worker. Pitfall 3: NEVER re-instantiate."""
    global _model
    if _model is None:
        _silence_ultralytics_stdout()
        from ultralytics import YOLO  # type: ignore[attr-defined]  # heavy import deferred

        _model = YOLO(model_path)
        # Pitfall 10: spend cold-start (CUDA kernel JIT) cost here, not on the first real frame.
        _model.predict(
            np.zeros((_WARMUP_IMG_SIZE, _WARMUP_IMG_SIZE, 3), dtype=np.uint8),
            device=device,
            verbose=False,
        )
    return _model


def warmup(device: str, model_path: str) -> None:
    """Public worker entry point called once by ``PoseDetector.start()``."""
    _ensure_model(model_path, device)


def infer(
    shm_name: str,
    shape: tuple[int, int, int],
    timestamp_ns: int,
    device: str,
    model_path: str,
    botsort_yaml_path: str | None,
) -> PoseEngineResult:
    """Read frame from shared block, run YOLO11-pose track, return picklable result.

    Args:
        shm_name: name of the long-lived ``SharedMemory`` block created by parent.
        shape: ``(height, width, 3)`` of the BGR uint8 frame.
        timestamp_ns: frame timestamp from the original ``Frame`` (Pitfall 11 -- forward only).
        device: ``"cuda"`` or ``"cpu"`` (already resolved by parent).
        model_path: path to YOLO weights (``.pt``).
        botsort_yaml_path: custom tracker config; ``None`` uses bundled ``"botsort.yaml"`` string.
    """
    global _shm, _shm_name
    if _shm is None or _shm_name != shm_name:
        if _shm is not None:
            _shm.close()
        _shm = shared_memory.SharedMemory(name=shm_name)  # attach existing
        _shm_name = shm_name
    frame_view: npt.NDArray[np.uint8] = np.ndarray(shape, dtype=np.uint8, buffer=_shm.buf)
    model = _ensure_model(model_path, device)
    tracker_arg = botsort_yaml_path if botsort_yaml_path is not None else "botsort.yaml"
    results = model.track(  # type: ignore[attr-defined]
        frame_view,
        persist=True,           # Pitfall 3 invariant -- ID continuity across calls
        tracker=tracker_arg,
        classes=[0],            # COCO 0 = person; suppress audience non-persons (Pitfall 8)
        device=device,
        verbose=False,
    )
    return _translate(results[0], timestamp_ns)


def _translate(result: object, timestamp_ns: int) -> PoseEngineResult:
    """Translate ultralytics Result (boxes + keypoints) into the picklable DTO.

    Pitfall 12: ALWAYS use ``boxes.xyxyn`` (normalized) -- never ``boxes.xyxy``.
    Pitfall 11: ``timestamp_ns`` is forwarded from caller -- NOT recomputed here.
    Open Question 6: ``boxes.id`` may be ``None`` on the very first frame.

    PERC-02 (B1 fix): subject_center_*_normalized is the weighted-keypoint mean
    (0.4 * nose + 0.4 * shoulder_mid + 0.2 * hip_mid) computed from
    ``result.keypoints.xyn`` and ``result.keypoints.conf``. The math is shared
    with SubjectTracker via :func:`pastor_tracker.perception._keypoints.weighted_keypoint_centroid`
    so both the worker output AND the loop-thread helper agree byte-for-byte.
    Bbox midpoint is NEVER used as the centroid -- detections without enough
    valid keypoints (mean conf < 0.55 across the 5 referenced kp) still emit a
    _PoseDetection so SubjectTracker can apply the conf-floor rejection per its
    existing PERC-02 logic; the worker does not silently drop them.
    """
    from pastor_tracker.perception._keypoints import weighted_keypoint_centroid

    boxes = getattr(result, "boxes", None)
    keypoints = getattr(result, "keypoints", None)
    if boxes is None or keypoints is None:
        return PoseEngineResult(detections=[])
    xyxyn = boxes.xyxyn.cpu().numpy() if boxes.xyxyn is not None else None
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else None
    kp_xyn = keypoints.xyn.cpu().numpy() if keypoints.xyn is not None else None
    kp_conf = keypoints.conf.cpu().numpy() if keypoints.conf is not None else None
    track_ids_t = boxes.id
    track_ids: list[int] | None = (
        track_ids_t.int().cpu().tolist() if track_ids_t is not None else None
    )
    if xyxyn is None or confs is None or kp_xyn is None or kp_conf is None:
        return PoseEngineResult(detections=[])
    detections: list[_PoseDetection] = []
    for idx in range(int(xyxyn.shape[0])):
        x1, y1, x2, y2 = (
            float(xyxyn[idx, 0]),
            float(xyxyn[idx, 1]),
            float(xyxyn[idx, 2]),
            float(xyxyn[idx, 3]),
        )
        if x2 <= x1 or y2 <= y1:
            continue  # degenerate bbox -- Detection validator would reject
        # PERC-02 (B1): weighted-keypoint mean centroid + mean kp conf over the
        # 5 referenced keypoints (nose, both shoulders, both hips). Math lives
        # in _keypoints.py so SubjectTracker can call the same function.
        cx, cy, mean_kp_conf = weighted_keypoint_centroid(kp_xyn[idx], kp_conf[idx])
        # Clamp into [0, 1] to honour Detection validators if the weighted mean
        # marginally exits the unit square due to fp rounding (Pitfall 12).
        cx = float(min(max(cx, 0.0), 1.0))
        cy = float(min(max(cy, 0.0), 1.0))
        det = _PoseDetection(
            subject_center_x_normalized=cx,
            subject_center_y_normalized=cy,
            mean_keypoint_confidence=float(mean_kp_conf),
            bbox_x1_normalized=x1,
            bbox_y1_normalized=y1,
            bbox_x2_normalized=x2,
            bbox_y2_normalized=y2,
            timestamp_ns=timestamp_ns,
            track_id=track_ids[idx] if track_ids is not None else None,
        )
        detections.append(det)
    return PoseEngineResult(detections=detections)
