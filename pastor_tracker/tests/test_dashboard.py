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


def test_button_save_config_noop_when_buffer_empty() -> None:
    """Plan 07-03: pressing Save with no pending changes is a logged no-op."""
    dashboard, _, host = _make_dashboard()
    with capture_logs() as logs:
        dashboard._on_save_config_pressed(0, None, None)
    host.submit.assert_not_called()
    assert any(entry.get("event") == "ui_save_config_noop" for entry in logs)


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
    from unittest.mock import patch

    dashboard, _, _ = _make_dashboard()
    cb = dashboard._make_slider_cb("pan_time_constant_sec")
    # Patch dpg so _refresh_unsaved_badge does not poke a real viewport.
    with patch("pastor_tracker.ui.dashboard.dpg"):
        cb(0, 1.2, None)
    assert dashboard._pending_config == {"pan_time_constant_sec": 1.2}


def test_slider_callback_factory_no_late_binding() -> None:
    """RESEARCH 'Code Examples' lines 821-826: closure binds key at definition."""
    from unittest.mock import patch

    dashboard, _, _ = _make_dashboard()
    keys = [
        "pan_time_constant_sec",
        "pan_deadband_deg",
        "pan_max_velocity_deg_per_sec",
        "camera_horizontal_fov_deg",
    ]
    values = [0.5, 0.3, 25.0, 80.0]
    cbs = [dashboard._make_slider_cb(k) for k in keys]
    with patch("pastor_tracker.ui.dashboard.dpg"):
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


# ---- Task 2: Save Config + ValidationError + exit-callback modal ------


def _make_save_config_dashboard(
    *,
    tmp_config_path: object,
    initial_pending: dict[str, float] | None = None,
) -> tuple[Dashboard, list[Mock], list[Mock]]:
    """Build a Dashboard with sequence-returning host_factory + pipeline_factory.

    Each invocation returns a fresh Mock so Save Config can rebuild a
    second host + pipeline without re-using the first instance (RESEARCH
    Pitfall 3).
    """
    from pathlib import Path as _Path

    pipelines: list[Mock] = []
    hosts: list[Mock] = []

    def _pipeline_factory(_cfg: Config) -> Mock:
        p = _make_mock_pipeline()
        pipelines.append(p)
        return p

    def _host_factory() -> Mock:
        h = _make_mock_host()
        hosts.append(h)
        return h

    dashboard = Dashboard(
        Config(),
        pipeline_factory=_pipeline_factory,
        host_factory=_host_factory,
        config_json_path=_Path(str(tmp_config_path)) / "config.json",
    )
    # Inject the first pair (mirrors what run() would do).
    dashboard._pipeline = _pipeline_factory(dashboard._config)
    dashboard._host = _host_factory()
    if initial_pending:
        dashboard._pending_config.update(initial_pending)
    return dashboard, pipelines, hosts


def test_save_config_success_writes_json(tmp_path: object) -> None:
    """D-11 success path: writes new config.json with merged values."""
    import json
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    with patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)
    data = json.loads(dashboard._config_json_path.read_text())
    assert data["pan_deadband_deg"] == 0.6


def test_save_config_success_clears_pending_buffer(tmp_path: object) -> None:
    """D-11: _pending_config cleared ONLY on full success."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    with patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)
    assert dashboard._pending_config == {}


def test_save_config_success_swaps_host_and_pipeline(tmp_path: object) -> None:
    """Pitfall 3: fresh PipelineThreadHost + fresh Pipeline post-Save."""
    from unittest.mock import patch

    dashboard, pipelines, hosts = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    old_host = dashboard._host
    assert old_host is not None
    with patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)
    # The old host was quit + stopped; a fresh host + pipeline are
    # constructed; the new host has start + submit called.
    old_host.stop.assert_called_once()
    assert len(hosts) >= 2  # old + new
    assert len(pipelines) >= 2  # old + new (factory called for new_config)
    new_host = dashboard._host
    assert new_host is not None
    assert new_host is not old_host
    new_host.start.assert_called_once()
    # new_host.submit was invoked at least once (with the new pipeline.start()).
    new_host.submit.assert_called()


def test_save_config_validation_error_shows_banner_keeps_buffer(
    tmp_path: object,
) -> None:
    """D-11 ValidationError branch: banner up, buffer unchanged, no restart."""
    from unittest.mock import patch

    # pan_deadband_deg has Pydantic le=10.0; 999.0 violates -> ValidationError.
    dashboard, _, hosts = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 999.0},
    )
    with patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)
    # Buffer unchanged.
    assert dashboard._pending_config == {"pan_deadband_deg": 999.0}
    # Banner text non-empty.
    assert dashboard._error_banner_text != ""
    # No fresh host constructed (only the initial host_factory call).
    assert len(hosts) == 1


def test_save_config_validation_error_logs_config_validation_failed(
    tmp_path: object,
) -> None:
    """D-11 ValidationError emits WARNING with 'errors' field."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 999.0},
    )
    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)
    matches = [e for e in logs if e.get("event") == "config_validation_failed"]
    assert len(matches) >= 1
    assert "errors" in matches[0]


def test_should_prompt_save_returns_true_with_pending(tmp_path: object) -> None:
    """Pure helper -- unit-testable in isolation; empty buffer => False."""
    dashboard, _, _ = _make_save_config_dashboard(tmp_config_path=tmp_path)
    assert dashboard.should_prompt_save() is False
    dashboard._pending_config["pan_deadband_deg"] = 0.6
    assert dashboard.should_prompt_save() is True


def test_exit_callback_with_pending_blocks_close(tmp_path: object) -> None:
    """Pitfall 7: non-empty buffer routes through _show_unsaved_modal."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    dashboard._show_unsaved_modal = Mock()  # type: ignore[method-assign]
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_exit_callback()
    dashboard._show_unsaved_modal.assert_called_once()
    mock_dpg.stop_dearpygui.assert_not_called()


def test_exit_callback_empty_pending_calls_stop_dearpygui(tmp_path: object) -> None:
    """Empty buffer => _on_exit_callback exits directly via dpg.stop_dearpygui."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(tmp_config_path=tmp_path)
    dashboard._show_unsaved_modal = Mock()  # type: ignore[method-assign]
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_exit_callback()
    dashboard._show_unsaved_modal.assert_not_called()
    mock_dpg.stop_dearpygui.assert_called_once()


def test_modal_quit_anyway_discards_buffer_and_stops_dpg(tmp_path: object) -> None:
    """Modal-decision logic: Quit Anyway clears buffer + stop_dearpygui."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_quit_anyway(0, None, None)
    assert dashboard._pending_config == {}
    mock_dpg.stop_dearpygui.assert_called_once()


def test_modal_save_and_quit_stops_dpg_on_success(tmp_path: object) -> None:
    """Modal-decision: Save & Quit => Save success path then stop_dearpygui."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_save_and_quit(0, None, None)
    # Save succeeded => buffer cleared, stop_dearpygui called.
    assert dashboard._pending_config == {}
    mock_dpg.stop_dearpygui.assert_called_once()


def test_modal_save_and_quit_keeps_running_on_validation_error(
    tmp_path: object,
) -> None:
    """Save & Quit must NOT stop_dearpygui if Save fails (banner stays up)."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 999.0},  # out-of-bounds
    )
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_save_and_quit(0, None, None)
    # Save failed: buffer unchanged, stop_dearpygui NOT called.
    assert dashboard._pending_config == {"pan_deadband_deg": 999.0}
    mock_dpg.stop_dearpygui.assert_not_called()


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


# ---- CR-02 rollback regression -----------------------------------------


# ---- WR-01 modal cleanup regression ------------------------------------


def test_modal_cancel_deletes_modal_widget(tmp_path: object) -> None:
    """WR-01: Cancel callback must dpg.delete_item the modal window tag."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    # Simulate _show_unsaved_modal having opened a modal (captured tag).
    dashboard._tag_modal = 7777
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_cancel(0, None, None)
    mock_dpg.delete_item.assert_called_once_with(7777)
    assert dashboard._tag_modal == 0


def test_modal_save_and_quit_deletes_modal_on_success(tmp_path: object) -> None:
    """WR-01: Save & Quit success branch must delete the modal widget."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    dashboard._tag_modal = 7777
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_save_and_quit(0, None, None)
    # Save succeeded -> buffer cleared, modal deleted, dpg stopped.
    assert dashboard._pending_config == {}
    mock_dpg.delete_item.assert_any_call(7777)
    assert dashboard._tag_modal == 0
    mock_dpg.stop_dearpygui.assert_called_once()


def test_modal_save_and_quit_keeps_modal_on_validation_failure(
    tmp_path: object,
) -> None:
    """WR-01: Save & Quit failure branch must NOT delete the modal."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 999.0},  # out-of-bounds
    )
    dashboard._tag_modal = 7777
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_save_and_quit(0, None, None)
    # Save failed: modal stays so operator can pick another action.
    mock_dpg.delete_item.assert_not_called()
    assert dashboard._tag_modal == 7777
    mock_dpg.stop_dearpygui.assert_not_called()


def test_modal_quit_anyway_deletes_modal(tmp_path: object) -> None:
    """WR-01: Quit Anyway must delete the modal widget on its way out."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    dashboard._tag_modal = 7777
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._on_modal_quit_anyway(0, None, None)
    mock_dpg.delete_item.assert_called_once_with(7777)
    assert dashboard._tag_modal == 0


def test_show_unsaved_modal_is_no_op_when_already_open(tmp_path: object) -> None:
    """WR-01: re-entry while a modal is open must not layer a fresh widget."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(
        tmp_config_path=tmp_path,
        initial_pending={"pan_deadband_deg": 0.6},
    )
    dashboard._tag_modal = 7777  # pretend a modal is already up
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._show_unsaved_modal()
    # No new window constructed, no new buttons added.
    mock_dpg.window.assert_not_called()
    mock_dpg.add_button.assert_not_called()
    assert dashboard._tag_modal == 7777  # tag unchanged


def test_delete_modal_if_open_is_idempotent(tmp_path: object) -> None:
    """WR-01: cleanup helper must be safe to call when no modal is open."""
    from unittest.mock import patch

    dashboard, _, _ = _make_save_config_dashboard(tmp_config_path=tmp_path)
    assert dashboard._tag_modal == 0
    with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg:
        dashboard._delete_modal_if_open()
    mock_dpg.delete_item.assert_not_called()


def test_save_config_rollback_on_pipeline_factory_failure(tmp_path: object) -> None:
    """CR-02: pipeline_factory raise leaves old self._* intact, preserves buffer."""
    from pathlib import Path as _Path
    from unittest.mock import patch

    from pastor_tracker.io.arduino_transport import ArduinoPortNotFoundError

    # First factory call (run() init mirror) succeeds; second call (Save)
    # raises -- simulating USB unplug between launch and Save.
    pipelines: list[Mock] = []
    factory_calls: list[int] = [0]

    def _pipeline_factory(_cfg: Config) -> Mock:
        factory_calls[0] += 1
        if factory_calls[0] == 1:
            p = _make_mock_pipeline()
            pipelines.append(p)
            return p
        raise ArduinoPortNotFoundError("USB device vanished mid-session")

    hosts: list[Mock] = []

    def _host_factory() -> Mock:
        h = _make_mock_host()
        hosts.append(h)
        return h

    dashboard = Dashboard(
        Config(),
        pipeline_factory=_pipeline_factory,
        host_factory=_host_factory,
        config_json_path=_Path(str(tmp_path)) / "config.json",
    )
    dashboard._pipeline = _pipeline_factory(dashboard._config)
    dashboard._host = _host_factory()
    dashboard._pending_config["pan_deadband_deg"] = 0.6
    old_host = dashboard._host
    old_pipeline = dashboard._pipeline
    old_config = dashboard._config

    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)

    # Old state intact -- no swap occurred.
    assert dashboard._host is old_host
    assert dashboard._pipeline is old_pipeline
    assert dashboard._config is old_config
    # Old host NOT torn down (still alive for next retry).
    old_host.stop.assert_not_called()
    # Pending buffer preserved so operator can retry / revert sliders.
    assert dashboard._pending_config == {"pan_deadband_deg": 0.6}
    # Operator-visible error banner up.
    assert "restart failed" in dashboard._error_banner_text
    # Structured log event surfaced (CR-02 + CR-01 share this event name).
    assert any(
        entry.get("event") == "ui_save_config_restart_failed" for entry in logs
    )


def test_save_config_rollback_on_pipeline_start_hardware_failure(
    tmp_path: object,
) -> None:
    """CR-01: new_pipeline.start() hardware failure surfaces + rolls back state.

    The new pipeline's ``start()`` is mirrored against ``run()``'s blocking
    contract: ``submit(pipeline.start()).result(timeout=...)``. A
    ``CameraError`` raised inside the new pipeline's start (e.g. the new
    Config moved camera resolution to a value the device rejects) must be
    classified, banner-surfaced, and rolled back to the old host.
    """
    from pathlib import Path as _Path
    from unittest.mock import patch

    from pastor_tracker.io.obs_camera import CameraError

    pipelines: list[Mock] = []
    factory_calls: list[int] = [0]

    def _pipeline_factory(_cfg: Config) -> Mock:
        factory_calls[0] += 1
        p = _make_mock_pipeline()
        pipelines.append(p)
        return p

    host_calls: list[int] = [0]
    hosts: list[Mock] = []

    def _host_factory() -> Mock:
        host_calls[0] += 1
        h = _make_mock_host()
        if host_calls[0] == 2:
            # Second host: submit(new_pipeline.start()) returns a Future
            # that raises CameraError when .result() is awaited.
            failed = _make_exception_future(
                CameraError("new resolution rejected by VCam")
            )

            def _submit(coro: object) -> Future[object]:
                close = getattr(coro, "close", None)
                if callable(close):
                    close()
                return failed

            h.submit.side_effect = _submit
        hosts.append(h)
        return h

    dashboard = Dashboard(
        Config(),
        pipeline_factory=_pipeline_factory,
        host_factory=_host_factory,
        config_json_path=_Path(str(tmp_path)) / "config.json",
    )
    dashboard._pipeline = _pipeline_factory(dashboard._config)
    dashboard._host = _host_factory()
    dashboard._pending_config["pan_deadband_deg"] = 0.6
    old_host = dashboard._host
    old_pipeline = dashboard._pipeline

    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)

    # Old state intact; rollback succeeded.
    assert dashboard._host is old_host
    assert dashboard._pipeline is old_pipeline
    old_host.stop.assert_not_called()
    assert dashboard._pending_config == {"pan_deadband_deg": 0.6}
    assert "restart failed" in dashboard._error_banner_text
    matches = [
        e for e in logs if e.get("event") == "ui_save_config_restart_failed"
    ]
    assert len(matches) == 1
    assert matches[0].get("exc_type") == "CameraError"
    # Best-effort orphan-host cleanup: the new host that DID start gets
    # stop()-ed so we don't leak a background thread.
    new_host = hosts[1]
    new_host.stop.assert_called_once()


def test_save_config_rollback_on_host_start_failure(tmp_path: object) -> None:
    """CR-02: new host.start() raise leaves old self._* intact, preserves buffer."""
    from pathlib import Path as _Path
    from unittest.mock import patch

    pipelines: list[Mock] = []

    def _pipeline_factory(_cfg: Config) -> Mock:
        p = _make_mock_pipeline()
        pipelines.append(p)
        return p

    host_calls: list[int] = [0]
    hosts: list[Mock] = []

    def _host_factory() -> Mock:
        host_calls[0] += 1
        h = _make_mock_host()
        if host_calls[0] == 2:
            # Second host (rebuild during Save) fails to start.
            h.start.side_effect = RuntimeError("loop_ready barrier missed")
        hosts.append(h)
        return h

    dashboard = Dashboard(
        Config(),
        pipeline_factory=_pipeline_factory,
        host_factory=_host_factory,
        config_json_path=_Path(str(tmp_path)) / "config.json",
    )
    dashboard._pipeline = _pipeline_factory(dashboard._config)
    dashboard._host = _host_factory()
    dashboard._pending_config["pan_deadband_deg"] = 0.6
    old_host = dashboard._host
    old_pipeline = dashboard._pipeline

    with capture_logs() as logs, patch("pastor_tracker.ui.dashboard.dpg"):
        dashboard._on_save_config_pressed(0, None, None)

    # Old state intact.
    assert dashboard._host is old_host
    assert dashboard._pipeline is old_pipeline
    old_host.stop.assert_not_called()
    assert dashboard._pending_config == {"pan_deadband_deg": 0.6}
    assert "restart failed" in dashboard._error_banner_text
    assert any(
        entry.get("event") == "ui_save_config_restart_failed" for entry in logs
    )
