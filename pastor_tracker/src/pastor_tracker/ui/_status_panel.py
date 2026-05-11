"""10 Hz status-panel widget for the Phase 7 dashboard (UI-04, D-15).

The status panel renders 6 operator-facing fields:
    1. Pipeline state  (running / paused / homing / e_stopped / stopped /
       quitting) + optional ``(unsaved changes)`` badge from D-12.
    2. Motor link state (Phase 2 ``ArduinoMotor.state.value``).
    3. Camera FPS (UI-side rolling avg over the last 30 rendered frames).
    4. Last detection confidence (mean keypoint conf of the most recent
       non-empty Detection batch).
    5. ID lock (BoT-SORT track id of the locked subject).
    6. Last error (most recent WARN/ERROR/CRITICAL event from the
       :class:`pastor_tracker.ui._event_bus.EventBusProcessor` tap).

Refresh cadence (D-15): the dashboard's render tick (~30 Hz) invokes
:meth:`StatusPanel.refresh` every 3rd frame (``_STATUS_REFRESH_DIVISOR``
in :mod:`pastor_tracker.ui.dashboard`), giving ~10 Hz updates -- fast
enough to feel live, slow enough that the operator's eye can track the
numbers. ``record_frame`` is called on EVERY rendered frame so the FPS
rolling avg samples the actual render rate, not the refresh rate.

All formatters are module-level pure functions on primitive types: this
keeps unit-testing trivial (no DPG context required) and avoids the
"hidden coupling" trap where status display drifts from snapshot field
semantics across plans.
"""
from __future__ import annotations

import collections
from typing import Final

import dearpygui.dearpygui as dpg

from pastor_tracker.core.types import PipelineSnapshot
from pastor_tracker.ui._event_bus import EventBuffer, make_event_bus

# ---------------------------------------------------------------------------
# Module-level constants (CLAUDE.md rule 6 -- no magic numbers).
# ---------------------------------------------------------------------------

_FPS_WINDOW_FRAMES: Final[int] = 30
"""Rolling-avg window: 30 samples ~ 1 s at the nominal 30 Hz render rate."""

_NS_PER_SECOND: Final[float] = 1_000_000_000.0
"""``time.perf_counter_ns`` denominator."""

_FPS_MIN_SAMPLES_FOR_AVG: Final[int] = 2
"""Need at least two samples (start + end) to compute a rate."""

_UNKNOWN_VALUE_GLYPH: Final[str] = "—"
"""Em-dash sentinel for missing scalar values; matches Plan 07-02 ``_build_status_slots`` text."""

_STATE_LABEL: Final[str] = "Pipeline:"
_MOTOR_LABEL: Final[str] = "Motor link:"
_FPS_LABEL: Final[str] = "Camera FPS:"
_CONF_LABEL: Final[str] = "Confidence:"
_LOCK_LABEL: Final[str] = "ID lock:"
_ERR_LABEL: Final[str] = "Last error:"


# ---------------------------------------------------------------------------
# Pure formatters -- unit-testable without a DPG context.
# ---------------------------------------------------------------------------


def _format_state_line(snap_state: str, unsaved_badge: str) -> str:
    """``Pipeline:   running   (unsaved changes)``  or just ``Pipeline:   running``."""
    return f"{_STATE_LABEL}   {snap_state}   {unsaved_badge}".rstrip()


def _format_motor(motor_state: str) -> str:
    return f"{_MOTOR_LABEL}  {motor_state}"


def _format_fps(fps: float) -> str:
    return f"{_FPS_LABEL}  {fps:5.1f}"


def _format_confidence(conf: float | None) -> str:
    rendered = _UNKNOWN_VALUE_GLYPH if conf is None else f"{conf:.2f}"
    return f"{_CONF_LABEL}  {rendered}"


def _format_id_lock(track_id: int | None) -> str:
    rendered = "NONE" if track_id is None else str(track_id)
    return f"{_LOCK_LABEL}     {rendered}"


def _format_last_error(event_bus: EventBuffer) -> str:
    if not event_bus:
        return f"{_ERR_LABEL}  {_UNKNOWN_VALUE_GLYPH}"
    level, event, timestamp = event_bus[0]
    return f"{_ERR_LABEL}  [{level}] {event} @ {timestamp}"


def compute_fps(window: collections.deque[tuple[int, int]], now_ns: int) -> float:
    """Rolling-avg FPS from a ``(timestamp_ns, frame_count)`` window.

    ``now_ns`` is currently unused but accepted on the call surface so a
    future "stale window" detection (e.g. if the render loop stalls, the
    last sample is seconds old -> FPS should read 0) can be added without
    breaking callers. Today the implementation derives FPS purely from
    the oldest/newest samples in the window, which is correct as long as
    the window is non-empty.

    Returns ``0.0`` when the window has fewer than 2 samples OR the
    elapsed time is zero (samples taken in the same instant -- rare but
    possible on first boot before perf_counter advances).
    """
    del now_ns  # reserved for stale-window detection (see docstring).
    if len(window) < _FPS_MIN_SAMPLES_FOR_AVG:
        return 0.0
    oldest_ns, oldest_count = window[0]
    newest_ns, newest_count = window[-1]
    dt_sec = (newest_ns - oldest_ns) / _NS_PER_SECOND
    if dt_sec <= 0:
        return 0.0
    return (newest_count - oldest_count) / dt_sec


# ---------------------------------------------------------------------------
# StatusPanel: holds widget tags + FPS window + event-bus reference.
# ---------------------------------------------------------------------------


class StatusPanel:
    """6-field status panel refreshed at ~10 Hz by the dashboard render tick.

    Args:
        event_bus: shared deque populated by the structlog
            :class:`pastor_tracker.ui._event_bus.EventBusProcessor`. The
            panel reads index 0 (newest) for the "Last error" field.
            Pass :func:`make_event_bus` output (or any deque with the same
            type) when constructing standalone for tests.
    """

    def __init__(self, event_bus: EventBuffer | None = None) -> None:
        self._event_bus: EventBuffer = (
            event_bus if event_bus is not None else make_event_bus()
        )
        self._fps_window: collections.deque[tuple[int, int]] = collections.deque(
            maxlen=_FPS_WINDOW_FRAMES
        )
        self._frame_counter: int = 0
        # Widget tags filled by attach_widgets() after DPG build.
        self._tag_state: int = 0
        self._tag_motor: int = 0
        self._tag_fps: int = 0
        self._tag_conf: int = 0
        self._tag_lock: int = 0
        self._tag_err: int = 0

    # ----- public surface ------------------------------------------------

    def attach_widgets(
        self,
        tag_state: int,
        tag_motor: int,
        tag_fps: int,
        tag_conf: int,
        tag_lock: int,
        tag_err: int,
    ) -> None:
        """Bind the 6 reserved ``dpg.add_text`` widget tags from Plan 07-02.

        Must be called once, after the dashboard ``_build_ui`` runs and
        before the first ``refresh`` call.
        """
        self._tag_state = tag_state
        self._tag_motor = tag_motor
        self._tag_fps = tag_fps
        self._tag_conf = tag_conf
        self._tag_lock = tag_lock
        self._tag_err = tag_err

    def record_frame(self, frame_timestamp_ns: int) -> None:
        """Append a sample to the FPS rolling window.

        Called from the dashboard render tick on EVERY rendered frame
        (NOT every status refresh) -- the FPS measurement must reflect
        the actual render rate, not the refresh divisor.
        """
        self._frame_counter += 1
        self._fps_window.append((frame_timestamp_ns, self._frame_counter))

    def refresh(self, snap: PipelineSnapshot, unsaved_badge: str) -> None:
        """10 Hz tick: push the 6 formatted strings to the reserved widgets.

        D-15 field set:
            state + unsaved-badge, motor link, FPS rolling avg, last
            detection confidence, ID lock, last error.
        """
        dpg.set_value(self._tag_state, _format_state_line(snap.state, unsaved_badge))
        dpg.set_value(self._tag_motor, _format_motor(snap.motor_state))
        now_ns = snap.last_frame_ts_ns if snap.last_frame_ts_ns is not None else 0
        dpg.set_value(self._tag_fps, _format_fps(compute_fps(self._fps_window, now_ns)))
        dpg.set_value(self._tag_conf, _format_confidence(snap.last_detection_confidence))
        dpg.set_value(self._tag_lock, _format_id_lock(snap.last_locked_track_id))
        dpg.set_value(self._tag_err, _format_last_error(self._event_bus))


__all__ = [
    "StatusPanel",
    "compute_fps",
]
