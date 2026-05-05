"""Phase 4 perception: pose detection, BoT-SORT tracking, Kalman smoothing.

Public surface:
    * ``PoseEngine`` Protocol -- DI seam (production: ``UltralyticsPoseEngine``;
      tests: ``FakePoseEngine`` in ``tests.fixtures.pose_traces``).
    * ``PoseDetector`` -- orchestrator (Plan 04-03, Wave 3).
    * ``UltralyticsPoseEngine`` -- production engine.
    * ``PerceptionError``, ``PoseEngineUnavailableError`` -- typed errors.
    * ``SubjectTracker`` lands in Plan 04-02 (Wave 2).
"""
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseDetector,
    PoseEngine,
    PoseEngineUnavailableError,
    UltralyticsPoseEngine,
)

__all__ = [
    "PerceptionError",
    "PoseDetector",
    "PoseEngine",
    "PoseEngineUnavailableError",
    "UltralyticsPoseEngine",
]
