"""Phase 5 intent stages: motion analyzer + framer (D-01 consume() shape).

Public surface:
    * ``MotionAnalyzer`` -- sustained-velocity + dwell hysteresis classifier.
"""
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer

__all__ = ["MotionAnalyzer"]
