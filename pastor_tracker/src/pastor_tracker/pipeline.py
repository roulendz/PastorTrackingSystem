"""Asyncio pipeline orchestrator wiring 8 stages with a 6-state lifecycle.

Phase 6 entry point. Composes ObsCamera (Phase 3), PoseDetector (Phase 4),
SubjectTracker (Phase 4), MotionAnalyzer/Framer (Phase 5),
PanController/CommandDispatcher (Phase 5), and ArduinoMotor (Phase 2)
into a single ``asyncio.Task`` per CONTEXT.md D-01..D-04.

Public surface (D-15..D-18):
    * ``Pipeline`` class -- async lifecycle methods (start/pause/resume/home/
      e_stop/quit), ``snapshot()``, ``latest_frame`` slot.
    * ``OrchestratorRejected`` -- single typed exception raised by lifecycle
      methods on invalid state-transition or out-of-limits home.
    * ``PipelineSnapshot``, ``PipelineState`` -- re-exported from ``core.types``
      so callers have a single import surface.

Plan 06-01 ships only this module skeleton + ``OrchestratorRejected``. The
full ``Pipeline`` class lands in Plan 06-02 (Wave 1). The ``__main__`` boot
refactor lands in Plan 06-03 (Wave 1). Integration tests land in Plan
06-04 (Wave 2).
"""
from __future__ import annotations

from pastor_tracker.core.types import PipelineSnapshot, PipelineState

__all__ = [
    "OrchestratorRejected",
    "PipelineSnapshot",
    "PipelineState",
]


class OrchestratorRejected(Exception):
    """Invalid lifecycle transition or out-of-limits ``home()`` call (D-15, D-08).

    Single typed surface raised by ``Pipeline`` lifecycle methods. Phase 7
    dashboard catches this and surfaces a status-bar message; tests assert
    on ``pytest.raises(OrchestratorRejected)``.

    Examples that raise:
        * ``await pipeline.pause()`` while state is STOPPED (no tick task to pause)
        * ``await pipeline.home()`` when 0.0 is outside [pan_min_deg, pan_max_deg]
        * ``await pipeline.resume()`` while state is STOPPED (no paused tick)
    """
