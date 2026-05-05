"""Phase 4 perception: pose detection, BoT-SORT tracking, Kalman smoothing.

Public surface:
    * ``PoseEngine`` Protocol -- DI seam (production: ``UltralyticsPoseEngine``;
      tests: ``FakePoseEngine`` in ``tests.fixtures.pose_traces``).
    * ``PoseDetector`` -- orchestrator (Plan 04-03, Wave 3).
    * ``UltralyticsPoseEngine`` -- production engine.
    * ``SubjectTracker`` -- 6-state lock + Kalman per-frame transformer.
    * ``PerceptionError``, ``PoseEngineUnavailableError`` -- typed errors.
"""
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseDetector,
    PoseEngine,
    PoseEngineUnavailableError,
    UltralyticsPoseEngine,
)
from pastor_tracker.perception.subject_tracker import SubjectTracker

__all__ = [
    "PerceptionError",
    "PoseDetector",
    "PoseEngine",
    "PoseEngineUnavailableError",
    "SubjectTracker",
    "UltralyticsPoseEngine",
]
