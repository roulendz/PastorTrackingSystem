"""Sustained-velocity + dwell hysteresis classifier (Phase 5 INTENT-01, INTENT-02).

Mirrors Phase 4 ``SubjectTracker.consume(...)`` shape (D-01 / BL-01 simplification):
per-frame async transform with no internal queue / iterator. Time source is
exclusively the upstream ``TrackedSubject.timestamp_ns`` (D-02) -- the analyzer
never reads a wall clock, so tests are fully deterministic.

Hysteresis (D-03):
    - Per-direction crossing timer: ``_first_right_crossing_ts_ns`` /
      ``_first_left_crossing_ts_ns``.
    - Dwell timer: ``_dwell_start_ts_ns``.
    - Each timer captures the timestamp at first cross; resets on un-cross
      (Pitfall 1: borderline-vx chatter must not flip intent).
    - Intent flips when continuous duration >= configured hysteresis /
      dwell-duration window.

None-upstream (D-04): reset all timers, emit MotionState("indeterminate", 0.0,
now_ns). The analyzer always returns a MotionState (no None emissions); the
Optional return shape mirrors Phase 4 ``SubjectTracker.consume`` for symmetry.

Move-priority-over-dwell tiebreak: a sustained right- or left-cross resolves
before dwell, so an operator entering motion from a long stillness flips
``moving_*`` immediately when the crossing timer matures, without waiting for
dwell to release.

Logging (RESEARCH 05 Pattern 9):
    - ``intent_change`` INFO: ``old``, ``new``, ``vx`` (or ``reason`` on the
      None-upstream path).
"""
from __future__ import annotations

from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotionIntent, MotionState, TrackedSubject

# Only literal allowed (CLAUDE.md rule 6) -- unit conversion. Every threshold
# below reads from ``self._config.*``.
_NS_PER_SEC: Final[float] = 1_000_000_000.0


__all__ = ["MotionAnalyzer"]


class MotionAnalyzer:
    """Phase 5 motion classifier. Pure transform on TrackedSubject.

    Public surface (consume-based, mirrors Phase 4 ``SubjectTracker``):
        ``consume(subject, now_ns) -> MotionState | None``:
            single-frame async tick; returns the classified MotionState. Always
            non-None in Phase 5 -- the Optional return type matches
            ``SubjectTracker.consume`` for orchestrator symmetry (D-01).

    Read-only dashboard surface (CONTEXT.md Phase Boundary):
        ``current_intent`` -- last classified intent (Phase 7 dashboard).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="motion_analyzer")
        self._first_right_crossing_ts_ns: int | None = None
        self._first_left_crossing_ts_ns: int | None = None
        self._dwell_start_ts_ns: int | None = None
        self._current_intent: MotionIntent = "indeterminate"

    # ---------- read-only dashboard surface ----------

    @property
    def current_intent(self) -> MotionIntent:
        """Last classified intent. Initialized to ``"indeterminate"``."""
        return self._current_intent

    # ---------- per-frame consume ----------

    async def consume(
        self,
        subject: TrackedSubject | None,
        now_ns: int,
    ) -> MotionState | None:
        """Single per-frame tick. Returns MotionState (always non-None for Phase 5)."""
        if subject is None:
            return self._handle_none(now_ns)
        vx = subject.velocity_x_norm_per_sec
        classified = self._classify(vx, subject.timestamp_ns)
        if classified != self._current_intent:
            self._logger.info(
                "intent_change",
                old=self._current_intent,
                new=classified,
                vx=vx,
            )
            self._current_intent = classified
        return MotionState(
            intent=classified,
            sustained_velocity_x_norm_per_sec=vx,
            timestamp_ns=now_ns,
        )

    # ---------- internals ----------

    def _handle_none(self, now_ns: int) -> MotionState:
        """D-04: reset all timers, emit indeterminate. Logs only on real transition."""
        self._first_right_crossing_ts_ns = None
        self._first_left_crossing_ts_ns = None
        self._dwell_start_ts_ns = None
        if self._current_intent != "indeterminate":
            self._logger.info(
                "intent_change",
                old=self._current_intent,
                new="indeterminate",
                reason="upstream_none",
            )
        self._current_intent = "indeterminate"
        return MotionState(
            intent="indeterminate",
            sustained_velocity_x_norm_per_sec=0.0,
            timestamp_ns=now_ns,
        )

    def _classify(self, vx: float, now_ns: int) -> MotionIntent:
        """Update timers and resolve sustained intent (move-priority-over-dwell)."""
        move_thr = self._config.motion_threshold_norm_per_sec
        dwell_thr = self._config.dwell_threshold_norm_per_sec
        # Right crossing timer: capture first cross; reset on un-cross.
        if vx > move_thr:
            if self._first_right_crossing_ts_ns is None:
                self._first_right_crossing_ts_ns = now_ns
        else:
            self._first_right_crossing_ts_ns = None
        # Left crossing timer (symmetric).
        if vx < -move_thr:
            if self._first_left_crossing_ts_ns is None:
                self._first_left_crossing_ts_ns = now_ns
        else:
            self._first_left_crossing_ts_ns = None
        # Dwell timer.
        if abs(vx) < dwell_thr:
            if self._dwell_start_ts_ns is None:
                self._dwell_start_ts_ns = now_ns
        else:
            self._dwell_start_ts_ns = None
        # Sustain checks (move overrides dwell -- see module docstring).
        hyst_ns = int(self._config.motion_hysteresis_sec * _NS_PER_SEC)
        if (
            self._first_right_crossing_ts_ns is not None
            and now_ns - self._first_right_crossing_ts_ns >= hyst_ns
        ):
            return "moving_right"
        if (
            self._first_left_crossing_ts_ns is not None
            and now_ns - self._first_left_crossing_ts_ns >= hyst_ns
        ):
            return "moving_left"
        dwell_ns = int(self._config.dwell_duration_sec * _NS_PER_SEC)
        if (
            self._dwell_start_ts_ns is not None
            and now_ns - self._dwell_start_ts_ns >= dwell_ns
        ):
            return "dwelling"
        return self._current_intent
