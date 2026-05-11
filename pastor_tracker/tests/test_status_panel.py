"""Unit tests for :mod:`pastor_tracker.ui._status_panel` (Plan 07-04 Task 2).

Coverage:
    * 6 pure formatters (state/motor/fps/conf/id_lock/last_error) -- 8 tests.
    * ``compute_fps`` rolling-avg behaviour -- 3 tests (zero/one-sample,
      two-sample one-second-apart, bounded window).
    * ``StatusPanel.refresh`` issues exactly 6 ``dpg.set_value`` calls.

DPG is monkey-patched on the module rather than mocked-per-test so that
no real DearPyGui context is constructed (the tests never call
``dpg.create_context``). The pure formatters are tested DIRECTLY -- they
take no DPG state.
"""
from __future__ import annotations

import collections
from typing import cast
from unittest.mock import MagicMock

import pytest

from pastor_tracker.core.types import PipelineSnapshot
from pastor_tracker.ui._event_bus import EventBuffer, make_event_bus
from pastor_tracker.ui._status_panel import (
    StatusPanel,
    _format_confidence,
    _format_fps,
    _format_id_lock,
    _format_last_error,
    _format_motor,
    _format_state_line,
    compute_fps,
)

# ---- pure formatters ----------------------------------------------------


def test_format_state_line_running() -> None:
    line = _format_state_line(snap_state="running", unsaved_badge="")
    assert "Pipeline:" in line
    assert "running" in line


def test_format_state_line_with_badge() -> None:
    line = _format_state_line(
        snap_state="running", unsaved_badge="(unsaved changes)"
    )
    assert "running" in line
    assert "(unsaved changes)" in line


def test_format_motor_renders_label_and_value() -> None:
    line = _format_motor("running")
    assert "Motor link:" in line
    assert "running" in line


def test_format_fps_pads_to_one_decimal() -> None:
    line = _format_fps(29.7)
    assert "Camera FPS:" in line
    assert "29.7" in line


def test_format_confidence_none() -> None:
    line = _format_confidence(None)
    assert "Confidence:" in line
    assert "—" in line


def test_format_confidence_value() -> None:
    line = _format_confidence(0.82)
    assert "Confidence:" in line
    assert "0.82" in line


def test_format_id_lock_none() -> None:
    line = _format_id_lock(None)
    assert "ID lock:" in line
    assert "NONE" in line


def test_format_id_lock_value() -> None:
    line = _format_id_lock(3)
    assert "ID lock:" in line
    assert "3" in line


def test_format_last_error_empty_deque() -> None:
    line = _format_last_error(make_event_bus())
    assert "Last error:" in line
    assert "—" in line


def test_format_last_error_with_entry() -> None:
    buf: EventBuffer = collections.deque(maxlen=64)
    buf.appendleft(("warning", "test_event", "2026-05-08T12:00:00Z"))
    line = _format_last_error(buf)
    assert "warning" in line
    assert "test_event" in line
    assert "2026-05-08T12:00:00Z" in line


# ---- compute_fps rolling avg --------------------------------------------


def test_fps_rolling_avg_zero_window() -> None:
    """Empty window -> 0.0 (cannot compute rate with no samples)."""
    assert compute_fps(collections.deque(), now_ns=0) == 0.0


def test_fps_rolling_avg_one_sample_returns_zero() -> None:
    """Single-sample window -> 0.0 (need two samples to compute a rate)."""
    window: collections.deque[tuple[int, int]] = collections.deque()
    window.append((1_000_000_000, 1))
    assert compute_fps(window, now_ns=1_000_000_000) == 0.0


def test_fps_rolling_avg_two_frames_one_second_apart() -> None:
    """Two samples 1 s apart with 1 frame elapsed -> 1.0 fps."""
    window: collections.deque[tuple[int, int]] = collections.deque(
        [(0, 0), (1_000_000_000, 1)]
    )
    assert compute_fps(window, now_ns=1_000_000_000) == pytest.approx(1.0)


def test_fps_rolling_avg_bounded_at_30() -> None:
    """StatusPanel.record_frame is bounded by ``_FPS_WINDOW_FRAMES`` (=30)."""
    panel = StatusPanel(make_event_bus())
    for i in range(100):
        panel.record_frame(i * 33_000_000)  # ~30 fps spacing
    # We cannot inspect the private deque directly without touching the
    # private attribute; rely on the documented bound via the constant.
    from pastor_tracker.ui._status_panel import _FPS_WINDOW_FRAMES

    assert panel._fps_window.maxlen == _FPS_WINDOW_FRAMES
    assert len(panel._fps_window) == _FPS_WINDOW_FRAMES


def test_fps_rolling_avg_same_timestamp_returns_zero() -> None:
    """Defensive: dt <= 0 returns 0.0 (avoids ZeroDivisionError on boot)."""
    window: collections.deque[tuple[int, int]] = collections.deque(
        [(1_000, 0), (1_000, 1)]
    )
    assert compute_fps(window, now_ns=1_000) == 0.0


# ---- StatusPanel.refresh writes 6 widgets -------------------------------


def _make_snap(
    *,
    state: str = "running",
    motor_state: str = "running",
    last_frame_ts_ns: int | None = 0,
    last_detection_confidence: float | None = 0.82,
    last_locked_track_id: int | None = 3,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        state=cast("PipelineSnapshot.__fields__['state'].annotation", state),  # type: ignore[name-defined,arg-type]
        last_intent="dwelling",
        motor_state=motor_state,
        last_frame_ts_ns=last_frame_ts_ns,
        last_detection_confidence=last_detection_confidence,
        last_locked_track_id=last_locked_track_id,
    )


def test_refresh_calls_set_value_six_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pastor_tracker.ui._status_panel as panel_mod

    mock_dpg = MagicMock()
    monkeypatch.setattr(panel_mod, "dpg", mock_dpg)
    panel = StatusPanel(make_event_bus())
    panel.attach_widgets(1, 2, 3, 4, 5, 6)
    panel.refresh(_make_snap(), "")
    assert mock_dpg.set_value.call_count == 6


def test_refresh_writes_to_attached_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each ``set_value`` call targets the corresponding ``attach_widgets`` tag."""
    import pastor_tracker.ui._status_panel as panel_mod

    mock_dpg = MagicMock()
    monkeypatch.setattr(panel_mod, "dpg", mock_dpg)
    panel = StatusPanel(make_event_bus())
    panel.attach_widgets(10, 20, 30, 40, 50, 60)
    panel.refresh(_make_snap(), "")
    invoked_tags = {call.args[0] for call in mock_dpg.set_value.call_args_list}
    assert invoked_tags == {10, 20, 30, 40, 50, 60}


def test_refresh_propagates_unsaved_badge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pastor_tracker.ui._status_panel as panel_mod

    mock_dpg = MagicMock()
    monkeypatch.setattr(panel_mod, "dpg", mock_dpg)
    panel = StatusPanel(make_event_bus())
    panel.attach_widgets(1, 2, 3, 4, 5, 6)
    panel.refresh(_make_snap(state="running"), "(unsaved changes)")
    # The state field (tag=1) must carry the badge text.
    state_call = next(
        c for c in mock_dpg.set_value.call_args_list if c.args[0] == 1
    )
    assert "(unsaved changes)" in state_call.args[1]


def test_refresh_renders_last_error_from_event_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pastor_tracker.ui._status_panel as panel_mod

    mock_dpg = MagicMock()
    monkeypatch.setattr(panel_mod, "dpg", mock_dpg)
    buf = make_event_bus()
    buf.appendleft(("error", "wire_lost", "2026-05-08T12:00:00Z"))
    panel = StatusPanel(buf)
    panel.attach_widgets(1, 2, 3, 4, 5, 6)
    panel.refresh(_make_snap(), "")
    # The error field (tag=6) must carry the captured event.
    err_call = next(c for c in mock_dpg.set_value.call_args_list if c.args[0] == 6)
    assert "wire_lost" in err_call.args[1]
    assert "error" in err_call.args[1]
