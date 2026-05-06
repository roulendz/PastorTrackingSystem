"""Phase 5 intent stages: motion analyzer + framer (D-01 consume() shape).

Public surface:
    * ``MotionAnalyzer`` -- sustained-velocity + dwell hysteresis classifier (INTENT-01, INTENT-02)
    * ``Framer`` -- rule-of-thirds target + stage-1 damping (INTENT-03, INTENT-04)
    * ``IntentError`` -- raised on unhandled MotionIntent dispatch (WR-08 guard)
"""
from pastor_tracker.intent.framer import Framer, IntentError
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer

__all__ = ["Framer", "IntentError", "MotionAnalyzer"]
