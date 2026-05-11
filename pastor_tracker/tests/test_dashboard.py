"""Dashboard non-rendering tests (Plan 07-02 Task 2).

Coverage:
    * 5 button callbacks dispatch to the correct Pipeline method via
      ``host.submit`` (mocked).
    * P button / hotkey toggles pause <-> resume based on snapshot state.
    * Save Config button logs a "not_implemented" WARN (Plan 07-03 lands
      the restart sequence).
    * 5 hotkeys dispatch identically to the buttons.
    * Home done-callback swallows ``OrchestratorRejected`` (Pitfall 9)
      without logging a ui_command_failed WARN.
    * D-04 quit sequence runs in the exact order:
      ``host.submit(pipeline.quit()).result() -> host.stop()
      -> dpg.stop_dearpygui()``.
    * ``_initiate_quit`` is idempotent (second call short-circuits).
    * Render-tick skip on unchanged ``Frame.timestamp_ns`` (D-08).

Render-loop visual rendering itself is exempt per CONTEXT.md "Claude's
Discretion" (DearPyGui v2.x ships no headless runner); tests cover the
PURE callback wiring + the texture-upload call-count assertion.
"""
from __future__ import annotations

from concurrent.futures import Future
from typing import cast
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest
from structlog.testing import capture_logs

from pastor_tracker.config import Config
from pastor_tracker.core.types import Frame, PipelineSnapshot
from pastor_tracker.pipeline import OrchestratorRejected, Pipeline
from pastor_tracker.ui._pipeline_thread import PipelineThreadHost
from pastor_tracker.ui.dashboard import Dashboard


def _make_snapshot(state: str = "running") -> PipelineSnapshot:
    return PipelineSnapshot(
        state=cast("PipelineSnapshot.__fields__['state'].annotation", state),  # type: ignore[name-defined]
        last_intent="dwelling",
        motor_state="running",
    )


def _snap(state: str) -> PipelineSnapshot:
    # Mypy-friendly snapshot factory (the cast above is annoying; this is
    # the actual helper used in tests).
    return PipelineSnapshot(  # type: ignore[arg-type]
        state=state,
        last_intent="dwelling",
        motor_state="running",
    )


def _make_resolved_future(value: object | None = None) -> Future[object]:
    fut: Future[object] = Future()
    fut.set_result(value)
    return fut


def _make_exception_future(exc: BaseException) -> Future[object]:
    fut: Future[object] = Future()
    fut.set_exception(exc)
    return fut


def _make_mock_pipeline(state: str = "running") -> Mock:
    pipeline = Mock(spec=Pipeline)
    pipeline.snapshot.return_value = _snap(state)
    pipeline.latest_frame = None
    return pipeline


def _make_mock_host() -> Mock:
    host = Mock(spec=PipelineThreadHost)

    # AsyncMock spec methods on Pipeline return un-awaited coroutines when
    # called; the real PipelineThreadHost would schedule them on its loop.
    # In tests we never run a loop, so we must close the coroutine here to
    # prevent the pytest unraisable-exception hook from firing on GC.
    _default_future = _make_resolved_future(None)

    def _submit_closing_coros(coro: object) -> Future[object]:
        # AsyncMock-produced coroutines have .close(); plain objects do not.
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return _default_future

    host.submit.side_effect = _submit_closing_coros
    return host


def _make_dashboard(
    *, mock_pipeline: Mock | None = None, mock_host: Mock | None = None
) -> tuple[Dashboard, Mock, Mock]:
    pipeline = mock_pipeline or _make_mock_pipeline()
    host = mock_host or _make_mock_host()
    config = Config()
    dashboard = Dashboard(
        config,
        pipeline_factory=lambda _cfg: pipeline,
        host_factory=lambda: host,
    )
    # Inject the pre-built instances directly so tests do not call run().
    dashboard._pipeline = pipeline
    dashboard._host = host
    return dashboard, pipeline, host


# ---- button dispatch ---------------------------------------------------


def test_button_start_dispatches_pipeline_start() -> None:
    dashboard, pipeline, host = _make_dashboard()
    dashboard._on_start_pressed(0, None, None)
    pipeline.start.assert_called_once()
    host.submit.assert_called_once()


def test_button_pause_dispatches_pipeline_pause_when_running() -> None:
    pipeline = _make_mock_pipeline(state="running")
    dashboard, _, host = _make_dashboard(mock_pipeline=pipeline)
    dashboard._on_pause_pressed(0, None, None)
    pipeline.pause.assert_called_once()
    pipeline.resume.assert_not_called()
    host.submit.assert_called_once()


def test_button_pause_dispatches_pipeline_resume_when_paused() -> None:
    pipeline = _make_mock_pipeline(state="paused")
    dashboard, _, host = _make_dashboard(mock_pipeline=pipeline)
    dashboard._on_pause_pressed(0, None, None)
    pipeline.resume.assert_called_once()
    pipeline.pause.assert_not_called()
    host.submit.assert_called_once()


def test_button_pause_skipped_when_not_running_or_paused() -> None:
    pipeline = _make_mock_pipeline(state="stopped")
    dashboard, _, host = _make_dashboard(mock_pipeline=pipeline)
    dashboard._on_pause_pressed(0, None, None)
    pipeline.pause.assert_not_called()
    pipeline.resume.assert_not_called()
    host.submit.assert_not_called()


def test_button_home_dispatches_pipeline_home() -> None:
    dashboard, pipeline, host = _make_dashboard()
    dashboard._on_home_pressed(0, None, None)
    pipeline.home.assert_called_once()
    host.submit.assert_called_once()


def test_button_estop_dispatches_pipeline_e_stop() -> None:
    dashboard, pipeline, host = _make_dashboard()
    dashboard._on_estop_pressed(0, None, None)
    pipeline.e_stop.assert_called_once()
    host.submit.assert_called_once()


def test_button_save_config_logs_not_implemented() -> None:
    dashboard, _, host = _make_dashboard()
    with capture_logs() as logs:
        dashboard._on_save_config_pressed(0, None, None)
    # Save Config is a stub in Plan 07-02; no submit fires.
    host.submit.assert_not_called()
    assert any(entry.get("event") == "ui_save_config_not_implemented" for entry in logs)


# ---- hotkey dispatch ---------------------------------------------------


@pytest.mark.parametrize(
    ("callback_attr", "pipeline_method"),
    [
        ("_on_start_pressed", "start"),
        ("_on_home_pressed", "home"),
        ("_on_estop_pressed", "e_stop"),
    ],
)
def test_hotkey_dispatches_correctly(
    callback_attr: str, pipeline_method: str
) -> None:
    dashboard, pipeline, _ = _make_dashboard()
    getattr(dashboard, callback_attr)(0, None, None)
    getattr(pipeline, pipeline_method).assert_called_once()


def test_hotkey_p_toggles_pause_resume() -> None:
    # running -> pause
    pipeline = _make_mock_pipeline(state="running")
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline)
    dashboard._on_pause_pressed(0, None, None)
    pipeline.pause.assert_called_once()

    # paused -> resume
    pipeline2 = _make_mock_pipeline(state="paused")
    dashboard2, _, _ = _make_dashboard(mock_pipeline=pipeline2)
    dashboard2._on_pause_pressed(0, None, None)
    pipeline2.resume.assert_called_once()


def test_hotkey_q_initiates_quit_sequence() -> None:
    dashboard, _, _ = _make_dashboard()
    # _on_quit_pressed calls dpg.stop_dearpygui under the hood; patch the
    # module-level dpg symbol to avoid attempting to talk to a real viewport.
    from unittest.mock import patch

    with capture_logs() as logs, patch(
        "pastor_tracker.ui.dashboard.dpg"
    ) as mock_dpg:
        dashboard._on_quit_pressed(0, None, None)
    mock_dpg.stop_dearpygui.assert_called_once()
    assert any(entry.get("event") == "ui_quit_initiated" for entry in logs)


# ---- Pitfall 9: home + OrchestratorRejected -----------------------------


def _make_host_returning_exception_future(exc: BaseException) -> Mock:
    """Build a host mock whose submit() returns a Future already in error state."""
    host = Mock(spec=PipelineThreadHost)
    failed_future = _make_exception_future(exc)

    def _submit(coro: object) -> Future[object]:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return failed_future

    host.submit.side_effect = _submit
    return host


def test_hotkey_h_swallows_orchestrator_rejected() -> None:
    pipeline = _make_mock_pipeline()
    host = _make_host_returning_exception_future(
        OrchestratorRejected("home rejected from HOMING")
    )
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline, mock_host=host)
    with capture_logs() as logs:
        dashboard._on_home_pressed(0, None, None)
    # Pitfall 9: NO ui_command_failed WARN -- only ui_home_skipped DEBUG.
    assert not any(entry.get("event") == "ui_command_failed" for entry in logs)
    assert any(entry.get("event") == "ui_home_skipped" for entry in logs)


def test_home_done_callback_logs_warn_on_other_exception() -> None:
    pipeline = _make_mock_pipeline()
    host = _make_host_returning_exception_future(RuntimeError("USB lost"))
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline, mock_host=host)
    with capture_logs() as logs:
        dashboard._on_home_pressed(0, None, None)
    assert any(
        entry.get("event") == "ui_command_failed"
        and entry.get("command") == "home"
        for entry in logs
    )


# ---- D-04 quit sequence -------------------------------------------------


def test_quit_sequence_order() -> None:
    """D-04 + Pitfall 7: pipeline.quit().result() -> host.stop() -> dpg.stop_dearpygui()."""
    dashboard, _pipeline, host = _make_dashboard()

    # Build an instrumented Future whose .result() records its position.
    parent = MagicMock()
    parent.host = host
    quit_future: MagicMock = MagicMock(spec=Future)
    parent.attach_mock(quit_future, "quit_future")

    def _submit_returns_instrumented(coro: object) -> MagicMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return quit_future

    host.submit.side_effect = _submit_returns_instrumented
    quit_future.result.return_value = None

    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        parent.attach_mock(mock_dpg, "dpg")
        dashboard._initiate_quit()

    # Expected order:
    #   1. host.submit(pipeline.quit())
    #   2. quit_future.result(timeout=...)
    #   3. host.stop()
    #   4. dpg.stop_dearpygui()
    method_call_order = [
        name
        for name, _, _ in parent.mock_calls
        if name in {"host.submit", "quit_future.result", "host.stop", "dpg.stop_dearpygui"}
    ]
    assert method_call_order == [
        "host.submit",
        "quit_future.result",
        "host.stop",
        "dpg.stop_dearpygui",
    ]


def test_quit_sequence_idempotent() -> None:
    dashboard, _, host = _make_dashboard()
    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._initiate_quit()
        dashboard._initiate_quit()
    # submit only called once even though _initiate_quit was called twice.
    assert host.submit.call_count == 1


# ---- D-08 render-tick skip ---------------------------------------------


def test_render_tick_skips_when_timestamp_unchanged() -> None:
    """D-08 render-tick skip: texture upload + overlays only fire on new ts.

    Plan 07-04 added a 10 Hz StatusPanel refresh into ``_render_tick`` --
    the panel's ``dpg.set_value`` calls live in ``_status_panel.dpg``,
    so both modules' ``dpg`` symbol must be patched to keep the test
    isolated from a real DPG context. The texture-upload count is read
    off the ``dashboard.dpg`` mock alone; the status-panel mock's calls
    are ignored here.
    """
    pipeline = _make_mock_pipeline()
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    frame = Frame(image=image, width=960, height=540, timestamp_ns=1_000_000_000)
    pipeline.latest_frame = frame
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline)
    # Texture + drawlist tags need a non-zero sentinel so the inner upload
    # logic doesn't short-circuit on an uninitialized widget id.
    dashboard._tag_texture = 1001
    dashboard._tag_drawlist = 1002

    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg, patch(
        "pastor_tracker.ui._status_panel.dpg"
    ):
        # First tick: timestamp differs from sentinel -> upload happens.
        dashboard._render_tick()
        assert mock_dpg.set_value.call_count == 1

        # Second tick: same frame, same timestamp -> upload SKIPPED.
        dashboard._render_tick()
        assert mock_dpg.set_value.call_count == 1

        # Third tick: bump timestamp -> upload happens again.
        new_frame = Frame(
            image=image, width=960, height=540, timestamp_ns=2_000_000_000
        )
        pipeline.latest_frame = new_frame
        dashboard._render_tick()
        assert mock_dpg.set_value.call_count == 2


def test_unsaved_badge_empty_when_no_pending() -> None:
    """Plan 07-03: empty buffer => empty badge text."""
    dashboard, _, _ = _make_dashboard()
    assert dashboard._pending_config == {}
    assert dashboard._unsaved_badge_text() == ""


def test_unsaved_badge_with_pending_contains_unsaved_changes() -> None:
    """Plan 07-03: non-empty buffer => '(unsaved changes)' text."""
    dashboard, _, _ = _make_dashboard()
    dashboard._pending_config["pan_deadband_deg"] = 0.6
    dashboard._pending_config["pan_time_constant_sec"] = 1.2
    assert "unsaved changes" in dashboard._unsaved_badge_text()


def test_command_completion_callback_logs_warn_on_exception() -> None:
    dashboard, _, _ = _make_dashboard()
    fut: Future[object] = Future()
    fut.set_exception(RuntimeError("downstream failure"))
    with capture_logs() as logs:
        dashboard._log_command_completion(fut)
    assert any(entry.get("event") == "ui_command_failed" for entry in logs)


def test_command_completion_callback_silent_on_success() -> None:
    dashboard, _, _ = _make_dashboard()
    fut: Future[object] = Future()
    fut.set_result(None)
    with capture_logs() as logs:
        dashboard._log_command_completion(fut)
    assert not any(entry.get("event") == "ui_command_failed" for entry in logs)


def test_redraw_overlays_draws_third_and_bbox_when_present() -> None:
    """When snapshot carries target + bbox, draw_line + draw_rectangle fire."""
    dashboard, _, _ = _make_dashboard()
    dashboard._tag_texture = 1001
    dashboard._tag_drawlist = 1002
    snap = PipelineSnapshot(  # type: ignore[arg-type]
        state="running",
        last_intent="dwelling",
        motor_state="running",
        last_target_x_normalized=0.5,
        last_subject_bbox_normalized=(0.1, 0.2, 0.3, 0.4),
    )
    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._redraw_overlays(snap)
    mock_dpg.draw_line.assert_called_once()
    mock_dpg.draw_rectangle.assert_called_once()


def test_redraw_overlays_skips_third_and_bbox_when_absent() -> None:
    dashboard, _, _ = _make_dashboard()
    dashboard._tag_texture = 1001
    dashboard._tag_drawlist = 1002
    snap = PipelineSnapshot(  # type: ignore[arg-type]
        state="running",
        last_intent="indeterminate",
        motor_state="running",
        last_target_x_normalized=None,
        last_subject_bbox_normalized=None,
    )
    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._redraw_overlays(snap)
    mock_dpg.draw_line.assert_not_called()
    mock_dpg.draw_rectangle.assert_not_called()


def test_initiate_quit_logs_warn_on_timeout() -> None:
    pipeline = _make_mock_pipeline()
    host = Mock(spec=PipelineThreadHost)
    failed = MagicMock(spec=Future)
    failed.result.side_effect = TimeoutError("quit took too long")

    def _submit(coro: object) -> MagicMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return failed

    host.submit.side_effect = _submit
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline, mock_host=host)
    from unittest.mock import patch

    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._initiate_quit()
    assert any(entry.get("event") == "ui_quit_failed" for entry in logs)


def test_initiate_quit_logs_warn_on_unexpected_exception() -> None:
    pipeline = _make_mock_pipeline()
    host = Mock(spec=PipelineThreadHost)
    failed = MagicMock(spec=Future)
    failed.result.side_effect = RuntimeError("hardware fell off the wire")

    def _submit(coro: object) -> MagicMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return failed

    host.submit.side_effect = _submit
    dashboard, _, _ = _make_dashboard(mock_pipeline=pipeline, mock_host=host)
    from unittest.mock import patch

    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._initiate_quit()
    assert any(entry.get("event") == "ui_quit_failed" for entry in logs)


def test_on_exit_callback_invokes_stop_dearpygui() -> None:
    dashboard, _, _ = _make_dashboard()
    from unittest.mock import patch

    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_exit_callback()
    mock_dpg.stop_dearpygui.assert_called_once()


def test_slider_callback_writes_to_pending_config() -> None:
    """Plan 07-03 Task 1: closure-bound slider callback stores into _pending_config."""
    dashboard, _, _ = _make_dashboard()
    cb = dashboard._make_slider_cb("pan_time_constant_sec")
    cb(0, 1.2, None)
    assert dashboard._pending_config == {"pan_time_constant_sec": 1.2}


def test_slider_callback_factory_no_late_binding() -> None:
    """RESEARCH 'Code Examples' lines 821-826: closure binds key at definition."""
    dashboard, _, _ = _make_dashboard()
    keys = [
        "pan_time_constant_sec",
        "pan_deadband_deg",
        "pan_max_velocity_deg_per_sec",
        "camera_horizontal_fov_deg",
    ]
    values = [0.5, 0.3, 25.0, 80.0]
    cbs = [dashboard._make_slider_cb(k) for k in keys]
    for cb, value in zip(cbs, values, strict=True):
        cb(0, value, None)
    assert dashboard._pending_config == dict(zip(keys, values, strict=True))


def test_sliders_have_correct_d09_bounds() -> None:
    """Introspect _build_sliders dpg.add_slider_float calls (D-09 bounds)."""
    from unittest.mock import patch

    dashboard, _, _ = _make_dashboard()
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._build_sliders()
    expected = [
        {"label": "pan_time_constant_sec", "min_value": 0.2, "max_value": 2.0},
        {"label": "pan_deadband_deg", "min_value": 0.05, "max_value": 2.0},
        {"label": "pan_max_velocity_deg_per_sec", "min_value": 5.0, "max_value": 120.0},
        {"label": "camera_horizontal_fov_deg", "min_value": 40.0, "max_value": 120.0},
    ]
    calls = mock_dpg.add_slider_float.call_args_list
    assert len(calls) == 4
    for call, exp in zip(calls, expected, strict=True):
        assert call.kwargs["label"] == exp["label"]
        assert call.kwargs["min_value"] == exp["min_value"]
        assert call.kwargs["max_value"] == exp["max_value"]


def test_slider_callback_refreshes_unsaved_badge_widget() -> None:
    """Plan 07-03: callback must call dpg.set_value on the badge widget tag."""
    from unittest.mock import patch

    dashboard, _, _ = _make_dashboard()
    dashboard._tag_unsaved_badge = 4242
    cb = dashboard._make_slider_cb("pan_deadband_deg")
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        cb(0, 0.9, None)
    mock_dpg.set_value.assert_called_once_with(4242, "(unsaved changes)")


def test_default_pipeline_factory_constructs_pipeline_or_raises_hardware() -> None:
    """Plan 07-04: real 8-stage construction.

    With no Arduino on the bus, ``discover_arduino_port`` raises
    ``ArduinoPortNotFoundError`` and the factory surfaces it (translated
    to ``EXIT_HARDWARE_FAILED`` by ``__main__.main``). When real hardware
    is attached the factory returns a Pipeline; we only need to assert
    that the Plan 07-02 ``NotImplementedError`` stub is gone.
    """
    from pastor_tracker.io.arduino_transport import ArduinoPortNotFoundError
    from pastor_tracker.ui.dashboard import _default_pipeline_factory

    try:
        result = _default_pipeline_factory(Config())
    except ArduinoPortNotFoundError:
        # No Uno on CI / test bench -- the factory fast-failed at port
        # discovery, which is the documented hardware-failure path.
        return
    except NotImplementedError:
        pytest.fail("Plan 07-04 must replace the Plan 07-02 stub")
    # Real hardware path: result must be a Pipeline.
    from pastor_tracker.pipeline import Pipeline

    assert isinstance(result, Pipeline)
