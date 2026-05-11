"""DearPyGui operator dashboard for Phase 7 (UI-01..05).

Composition:
    main thread owns DearPyGui render loop + viewport + drawlist + widgets
        + handler_registry (CONTEXT.md D-01).
    background thread owns asyncio Pipeline tick task
        (PipelineThreadHost, CONTEXT.md D-01..D-02).
    cross-thread bridge: ``host.submit(coro)`` wraps
        ``asyncio.run_coroutine_threadsafe`` (D-02). UI never awaits.

Plan 07-02 ships the shell:
    render loop + texture + drawlist + 5 buttons + 5 hotkeys + D-04 quit
    sequence + render-skip (D-08).
Sliders are STUBS (``add_slider_float`` is added but the callback only
    logs a placeholder); Plan 07-03 wires them into ``_pending_config``
    and the Save Config restart sequence.

Plan 07-04 adds the 10 Hz :class:`StatusPanel` (UI-04, D-15) into the
render tick + wires the real 8-stage :func:`_default_pipeline_factory`
(replaces the Plan 07-02 ``NotImplementedError`` stub). The 6 reserved
status widget slots from ``_build_status_slots`` are now bound to the
panel via ``attach_widgets``. Slider Save-Config wiring still lands in
Plan 07-03 (``_unsaved_badge_text`` is a stub that returns ``""`` until
Plan 07-03 replaces it).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, Final

import dearpygui.dearpygui as dpg
import numpy as np
import structlog
from pydantic import ValidationError

from pastor_tracker.config import CONFIG_JSON_PATH, Config
from pastor_tracker.core.types import Frame, PipelineSnapshot
from pastor_tracker.io.arduino_motor import ArduinoError
from pastor_tracker.io.arduino_transport import ArduinoPortNotFoundError
from pastor_tracker.io.obs_camera import CameraError
from pastor_tracker.perception.pose_detector import PerceptionError
from pastor_tracker.pipeline import OrchestratorRejected, Pipeline
from pastor_tracker.ui._event_bus import EventBuffer, make_event_bus
from pastor_tracker.ui._overlays import (
    angle_text,
    bbox_rect_pixels,
    bgr_frame_to_rgba_float_flat,
    id_lock_text,
    third_line_pixels,
)
from pastor_tracker.ui._pipeline_thread import PipelineThreadHost
from pastor_tracker.ui._status_panel import StatusPanel

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Module-level constants (CLAUDE.md rule 6 -- no magic numbers in code).
# ---------------------------------------------------------------------------

_VIEWPORT_TITLE: Final[str] = "Pastor Tracker — Dashboard"
# Default: 960 preview + 480 controls per D-13.
_VIEWPORT_WIDTH_DEFAULT: Final[int] = 1_440
_VIEWPORT_HEIGHT_DEFAULT: Final[int] = 640
# Every 3rd 30 Hz frame ~ 10 Hz refresh per D-15. Used by Plan 07-04.
_STATUS_REFRESH_DIVISOR: Final[int] = 3
# D-04 quit-drain budget; mirrors PipelineThreadHost join budget.
_PIPELINE_QUIT_TIMEOUT_SEC: Final[float] = 5.0
# Hardware-boot (motor handshake + camera open) is generous on Windows.
_PIPELINE_START_TIMEOUT_SEC: Final[float] = 10.0
# Sentinel "never rendered a frame" timestamp.
_UNINITIALIZED_TS_SENTINEL: Final[int] = -1
# Local mirror of __main__.EXIT_OK; avoids the import cycle that arrives
# in Plan 07-04 when ``__main__.py`` learns to construct Dashboard.
# Both values are sysexits.h-flavoured (0 = clean shutdown).
_EXIT_OK_LOCAL: Final[int] = 0

# Slider bounds from CONTEXT.md D-09. Plan 07-03 wires the callbacks; this
# plan only constructs the widgets (stub callback logs DEBUG).
_PAN_TIME_CONSTANT_MIN_SEC: Final[float] = 0.2
_PAN_TIME_CONSTANT_MAX_SEC: Final[float] = 2.0
_PAN_DEADBAND_MIN_DEG: Final[float] = 0.05
_PAN_DEADBAND_MAX_DEG: Final[float] = 2.0
_PAN_VELOCITY_MIN_DEG_PER_SEC: Final[float] = 5.0
_PAN_VELOCITY_MAX_DEG_PER_SEC: Final[float] = 120.0
_FOV_MIN_DEG: Final[float] = 40.0
_FOV_MAX_DEG: Final[float] = 120.0

# E-Stop color theme (RGB tuple). Red text per D-13.
_ESTOP_TEXT_COLOR_RGB: Final[tuple[int, int, int]] = (255, 0, 0)

# Drawlist text overlay positions. Anchored to top-left and bottom-left of
# the preview drawlist so they read with the live image.
_ID_LOCK_TEXT_POS: Final[tuple[int, int]] = (10, 10)
_ANGLE_TEXT_BOTTOM_OFFSET_PX: Final[int] = 24

# Unsaved-changes badge text (CONTEXT.md "Specific Ideas" line 183 wording).
# Empty string when buffer is empty; this label otherwise.
_UNSAVED_BADGE_LABEL: Final[str] = "(unsaved changes)"

# Save Config: budget for the old Pipeline.quit() Future.result(...) wait
# (RESEARCH §Pitfall 3 + A6 -- UI freezes during this window; 5 s mirrors
# the D-04 quit-drain budget).
_SAVE_QUIT_TIMEOUT_SEC: Final[float] = 5.0

# Save Config error-banner prefix. Operator-facing one-liner; the
# ValidationError per-field details are appended.
_ERROR_BANNER_PREFIX: Final[str] = "Save failed: "


def _default_pipeline_factory(config: Config) -> Pipeline:
    """Default 8-stage Pipeline construction -- mirrors ``__main__._amain``.

    Plan 07-04 wires the dashboard into the real boot path. The
    construction order (D-05) and the factory shape are identical to the
    Phase 6 ``_amain`` body; the lazy import keeps DearPyGui's import
    cost off the ``--headless`` path (this function is only called from
    the ``--ui`` branch).

    Hardware failures during construction (``ArduinoPortNotFoundError``)
    propagate to ``__main__.main``'s exit-translator ladder, where they
    are classified as ``EXIT_HARDWARE_FAILED``.
    """
    # Lazy imports: hardware-stack modules are heavy (pyserial, opencv,
    # ultralytics) and only the --ui path needs them; --headless reaches
    # the same stages through __main__._amain.
    from pygrabber.dshow_graph import FilterGraph

    from pastor_tracker.control.command_dispatcher import CommandDispatcher
    from pastor_tracker.control.pan_controller import PanController
    from pastor_tracker.intent.framer import Framer
    from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
    from pastor_tracker.io.arduino_motor import ArduinoMotor
    from pastor_tracker.io.arduino_transport import (
        PySerialTransport,
        discover_arduino_port,
    )
    from pastor_tracker.io.obs_camera import (
        ObsCamera,
        OpenCvVideoSource,
        VideoSource,
    )
    from pastor_tracker.perception.pose_detector import (
        PoseDetector,
        UltralyticsPoseEngine,
    )
    from pastor_tracker.perception.subject_tracker import SubjectTracker

    def _build_video_source(
        index: int, width: int, height: int, fps: int
    ) -> VideoSource:
        return OpenCvVideoSource(index, width, height, fps)

    def _build_filter_graph() -> FilterGraph:
        return FilterGraph()  # type: ignore[no-untyped-call]

    arduino_port = discover_arduino_port(config.arduino_port)
    transport = PySerialTransport(port=arduino_port, baud=config.arduino_baud)
    motor = ArduinoMotor(transport, config)
    camera = ObsCamera(
        config,
        video_source_factory=_build_video_source,
        filter_graph_factory=_build_filter_graph,
    )
    pose_engine = UltralyticsPoseEngine(config)
    detector = PoseDetector(config=config, engine=pose_engine)
    tracker = SubjectTracker(config)
    analyzer = MotionAnalyzer(config)
    framer = Framer(config)
    controller = PanController(config)
    dispatcher = CommandDispatcher(config)
    return Pipeline(
        config,
        camera=camera,
        motor=motor,
        detector=detector,
        tracker=tracker,
        analyzer=analyzer,
        framer=framer,
        controller=controller,
        dispatcher=dispatcher,
    )


class Dashboard:
    """DearPyGui dashboard owning the main-thread render loop.

    Args:
        config: Validated frozen Config.
        pipeline_factory: DI seam -- builds a Pipeline from Config. Default
            raises NotImplementedError; tests inject a Mock(spec=Pipeline).
        host_factory: DI seam -- builds a PipelineThreadHost. Default is
            the real PipelineThreadHost class; tests inject a
            Mock(spec=PipelineThreadHost).
    """

    def __init__(
        self,
        config: Config,
        *,
        pipeline_factory: Callable[[Config], Pipeline] | None = None,
        host_factory: Callable[[], PipelineThreadHost] | None = None,
        event_bus: EventBuffer | None = None,
        config_json_path: Path | None = None,
    ) -> None:
        self._config = config
        self._pipeline_factory: Callable[[Config], Pipeline] = (
            pipeline_factory or _default_pipeline_factory
        )
        self._host_factory: Callable[[], PipelineThreadHost] = (
            host_factory or PipelineThreadHost
        )
        self._pipeline: Pipeline | None = None
        self._host: PipelineThreadHost | None = None
        self._logger = structlog.get_logger(module="ui.dashboard")
        # Plan 07-03 fills _pending_config from slider callbacks.
        self._pending_config: dict[str, float] = {}
        self._last_rendered_ts_ns: int = _UNINITIALIZED_TS_SENTINEL
        self._frame_count: int = 0
        self._quit_initiated: bool = False
        # Widget tags assigned in _build_ui (after dpg.create_context).
        self._tag_texture: int = 0
        self._tag_drawlist: int = 0
        # WR-03: stable overlay-item tags allocated ONCE in _build_ui;
        # _redraw_overlays updates them via configure_item + show/hide
        # instead of delete_item(children_only=True) + re-add per frame.
        # Avoids ~150 widget allocs/sec at 30 Hz over multi-hour shoots.
        self._tag_overlay_third_line: int = 0
        self._tag_overlay_bbox: int = 0
        self._tag_overlay_id_text: int = 0
        self._tag_overlay_angle_text: int = 0
        # Plan 07-03: unsaved-changes badge widget tag. Set in
        # _build_unsaved_badge(); used by _refresh_unsaved_badge() to
        # push the text via dpg.set_value().
        self._tag_unsaved_badge: int = 0
        # Plan 07-03: red error-banner widget tag + text state. The text
        # is held on the Dashboard so unit tests can read it without
        # introspecting DPG widget state.
        self._tag_error_banner: int = 0
        self._error_banner_text: str = ""
        # WR-01: unsaved-changes modal window tag. ``0`` means "no modal
        # currently open". Captured by ``_show_unsaved_modal`` and
        # cleared by every modal callback so the widget tree does not
        # accumulate a stale modal per Cancel/X cycle.
        self._tag_modal: int = 0
        # Save Config target path. Defaults to the module-level
        # ``CONFIG_JSON_PATH`` (CWD-relative ``config.json``); tests
        # inject a ``tmp_path`` for isolation.
        self._config_json_path: Path = (
            config_json_path if config_json_path is not None else CONFIG_JSON_PATH
        )
        # Plan 07-04 status panel. Dashboard constructs its own event bus
        # when running standalone (e.g. tests); ``__main__.py`` injects a
        # shared one so structlog taps land in the SAME deque the panel
        # reads.
        self._event_bus: EventBuffer = (
            event_bus if event_bus is not None else make_event_bus()
        )
        self._status_panel: StatusPanel = StatusPanel(self._event_bus)

    # ----- public surface ------------------------------------------------

    def run(self) -> int:
        """Main-thread entry. Returns sysexits.h-flavoured exit code.

        Boot order (RESEARCH §"Threading & Quit-Sequence Concrete Walkthrough"):
            1. Construct Pipeline + PipelineThreadHost.
            2. Start host (blocks on loop_ready barrier).
            3. host.submit(pipeline.start()).result(timeout=...).
            4. dpg.create_context() + _build_ui() + setup + viewport show.
            5. Manual render loop with D-08 timestamp-skip.
            6. finally: _initiate_quit() + dpg.destroy_context().
        """
        self._pipeline = self._pipeline_factory(self._config)
        self._host = self._host_factory()
        self._host.start()
        self._host.submit(self._pipeline.start()).result(
            timeout=_PIPELINE_START_TIMEOUT_SEC
        )
        dpg.create_context()
        try:
            self._build_ui()
            dpg.create_viewport(
                title=_VIEWPORT_TITLE,
                width=_VIEWPORT_WIDTH_DEFAULT,
                height=_VIEWPORT_HEIGHT_DEFAULT,
            )
            dpg.setup_dearpygui()
            dpg.set_exit_callback(self._on_exit_callback)
            dpg.show_viewport()
            while dpg.is_dearpygui_running():
                self._render_tick()
                dpg.render_dearpygui_frame()
            return _EXIT_OK_LOCAL
        finally:
            self._initiate_quit()
            dpg.destroy_context()

    # ----- UI build ------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the single window with preview, sliders, buttons, status.

        Layout (D-13): horizontal split. Left = preview drawlist. Right =
        sliders (top, STUB callback this plan) + buttons (mid) + status
        slots (bottom, reserved for Plan 07-04).
        """
        preview_w = self._config.preview_width_px
        preview_h = self._config.preview_height_px

        # Initial float32 RGBA buffer (Pitfall 4: must be float32 for
        # mvFormat_Float_rgba).
        initial_buffer = np.zeros(preview_w * preview_h * 4, dtype=np.float32)
        with dpg.texture_registry():
            self._tag_texture = dpg.add_raw_texture(
                width=preview_w,
                height=preview_h,
                default_value=initial_buffer,
                format=dpg.mvFormat_Float_rgba,
            )

        # E-Stop red theme -- one theme item, bound to the e-stop button.
        with dpg.theme() as estop_theme, dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Text, _ESTOP_TEXT_COLOR_RGB)

        with dpg.window(
            label="Pastor Tracker",
            no_close=True,
            no_collapse=True,
            width=_VIEWPORT_WIDTH_DEFAULT,
            height=_VIEWPORT_HEIGHT_DEFAULT,
        ), dpg.group(horizontal=True):
            # --- LEFT: preview drawlist ---
            with dpg.group():
                self._tag_drawlist = dpg.add_drawlist(
                    width=preview_w, height=preview_h
                )
                dpg.draw_image(
                    self._tag_texture,
                    (0, 0),
                    (preview_w, preview_h),
                    parent=self._tag_drawlist,
                )
                # WR-03: pre-allocate stable overlay items. The
                # third-line + bbox start hidden (no snapshot data
                # yet); the two text items start with placeholder
                # strings. _redraw_overlays mutates them via
                # configure_item / show_item / hide_item per tick.
                self._tag_overlay_third_line = dpg.draw_line(
                    (0, 0),
                    (0, preview_h),
                    parent=self._tag_drawlist,
                    show=False,
                )
                self._tag_overlay_bbox = dpg.draw_rectangle(
                    (0, 0),
                    (0, 0),
                    parent=self._tag_drawlist,
                    show=False,
                )
                self._tag_overlay_id_text = dpg.draw_text(
                    _ID_LOCK_TEXT_POS,
                    "",
                    parent=self._tag_drawlist,
                )
                self._tag_overlay_angle_text = dpg.draw_text(
                    (10, preview_h - _ANGLE_TEXT_BOTTOM_OFFSET_PX),
                    "",
                    parent=self._tag_drawlist,
                )
            # --- RIGHT: sliders + buttons + status ---
            with dpg.group():
                self._build_unsaved_badge()
                self._build_sliders()
                self._build_buttons(estop_theme)
                self._build_status_slots()
        self._build_hotkeys()

    def _build_unsaved_badge(self) -> None:
        """Plan 07-03: dedicated text widget for the unsaved-changes badge.

        Also constructs the Save-Config error banner widget (red text,
        empty until a ValidationError surfaces). Both widgets sit above
        the slider row so the operator sees them next to the controls
        they tune.
        """
        self._tag_unsaved_badge = dpg.add_text("")
        self._tag_error_banner = dpg.add_text("")
        # Red theme for the error banner -- reuse the E-Stop color tuple
        # (CLAUDE.md DRY rule 3) but apply it lazily here since the
        # E-Stop theme is button-scoped (mvThemeCol_Text on mvButton).
        with dpg.theme() as banner_theme, dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, _ESTOP_TEXT_COLOR_RGB)
        dpg.bind_item_theme(self._tag_error_banner, banner_theme)

    def _build_sliders(self) -> None:
        """Plan 07-03: 4 sliders with D-09 bounds + closure-factory callbacks.

        The closure factory (:meth:`_make_slider_cb`) binds the field key
        at slider-construction time -- this avoids the late-binding
        gotcha that a single shared lambda would suffer (RESEARCH §"Code
        Examples" lines 821-826).
        """
        dpg.add_slider_float(
            label="pan_time_constant_sec",
            default_value=self._config.pan_time_constant_sec,
            min_value=_PAN_TIME_CONSTANT_MIN_SEC,
            max_value=_PAN_TIME_CONSTANT_MAX_SEC,
            format="%.2f",
            callback=self._make_slider_cb("pan_time_constant_sec"),
        )
        dpg.add_slider_float(
            label="pan_deadband_deg",
            default_value=self._config.pan_deadband_deg,
            min_value=_PAN_DEADBAND_MIN_DEG,
            max_value=_PAN_DEADBAND_MAX_DEG,
            format="%.2f",
            callback=self._make_slider_cb("pan_deadband_deg"),
        )
        dpg.add_slider_float(
            label="pan_max_velocity_deg_per_sec",
            default_value=self._config.pan_max_velocity_deg_per_sec,
            min_value=_PAN_VELOCITY_MIN_DEG_PER_SEC,
            max_value=_PAN_VELOCITY_MAX_DEG_PER_SEC,
            format="%.1f",
            callback=self._make_slider_cb("pan_max_velocity_deg_per_sec"),
        )
        dpg.add_slider_float(
            label="camera_horizontal_fov_deg",
            default_value=self._config.camera_horizontal_fov_deg,
            min_value=_FOV_MIN_DEG,
            max_value=_FOV_MAX_DEG,
            format="%.1f",
            callback=self._make_slider_cb("camera_horizontal_fov_deg"),
        )

    def _make_slider_cb(
        self, key: str
    ) -> Callable[[int, float, object], None]:
        """Closure factory: binds ``key`` at definition site.

        Per RESEARCH §"Code Examples" lines 821-826: a single shared
        callback that read ``key`` from a loop variable would suffer the
        Python late-binding bug (all callbacks would write to the LAST
        key). The factory returns a fresh closure that captures ``key``
        in its own scope.
        """
        def _cb(sender: int, app_data: float, user_data: object) -> None:
            del sender, user_data
            self._pending_config[key] = app_data
            self._refresh_unsaved_badge()
        return _cb

    def _refresh_unsaved_badge(self) -> None:
        """Push the current badge text to the reserved widget."""
        dpg.set_value(self._tag_unsaved_badge, self._unsaved_badge_text())

    def _build_buttons(self, estop_theme: int) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start", callback=self._on_start_pressed)
            dpg.add_button(label="Pause", callback=self._on_pause_pressed)
            dpg.add_button(label="Home", callback=self._on_home_pressed)
            estop_btn = dpg.add_button(label="E-Stop", callback=self._on_estop_pressed)
            dpg.bind_item_theme(estop_btn, estop_theme)
            dpg.add_button(label="Save Config", callback=self._on_save_config_pressed)

    def _build_status_slots(self) -> None:
        """6 reserved text widgets bound to the Plan 07-04 ``StatusPanel``.

        Tags are captured here and forwarded via ``attach_widgets`` so the
        panel can ``dpg.set_value`` them at 10 Hz from ``_render_tick``.
        Initial values match the Plan 07-02 placeholder text so the layout
        size is stable on first paint.
        """
        tag_state = dpg.add_text("Pipeline:    —")
        tag_motor = dpg.add_text("Motor link:  —")
        tag_fps = dpg.add_text("Camera FPS:  —")
        tag_conf = dpg.add_text("Confidence:  —")
        tag_lock = dpg.add_text("ID lock:     —")
        tag_err = dpg.add_text("Last error:  —")
        self._status_panel.attach_widgets(
            tag_state, tag_motor, tag_fps, tag_conf, tag_lock, tag_err
        )

    def _build_hotkeys(self) -> None:
        with dpg.handler_registry():
            dpg.add_key_press_handler(
                key=dpg.mvKey_S, callback=self._on_start_pressed
            )
            dpg.add_key_press_handler(
                key=dpg.mvKey_P, callback=self._on_pause_pressed
            )
            dpg.add_key_press_handler(
                key=dpg.mvKey_H, callback=self._on_home_pressed
            )
            dpg.add_key_press_handler(
                key=dpg.mvKey_E, callback=self._on_estop_pressed
            )
            dpg.add_key_press_handler(
                key=dpg.mvKey_Q, callback=self._on_quit_pressed
            )

    # ----- Plan 07-03: unsaved-changes badge helpers --------------------

    def _unsaved_badge_text(self) -> str:
        """Returns ``_UNSAVED_BADGE_LABEL`` when ``_pending_config`` non-empty.

        Pure helper -- also read by the Plan 07-04 :class:`StatusPanel`
        each ``_STATUS_REFRESH_DIVISOR`` tick to render the "Pipeline:"
        status line with an optional trailing "(unsaved changes)".
        """
        if not self._pending_config:
            return ""
        return _UNSAVED_BADGE_LABEL

    # ----- button + hotkey callbacks ------------------------------------

    def _require_initialized(self) -> tuple[Pipeline, PipelineThreadHost]:
        """WR-02 tiger-style guard: explicit raise over ``assert`` for ``python -O``.

        Returns the non-None ``(pipeline, host)`` pair after asserting the
        run-time invariant set by :meth:`run` (``self._pipeline`` and
        ``self._host`` populated before any button / render callback can
        fire). The ``assert`` form was stripped under ``python -O`` and a
        future None-deref would surface as ``AttributeError`` from a deep
        DPG callback frame -- this helper mirrors
        :class:`PipelineThreadHost.submit`'s explicit-raise contract (see
        ``_pipeline_thread.py:111-116``).
        """
        if self._pipeline is None or self._host is None:
            raise RuntimeError(
                "Dashboard callback fired before run() initialized pipeline/host"
            )
        return self._pipeline, self._host

    def _on_start_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        pipeline, host = self._require_initialized()
        fut = host.submit(pipeline.start())
        fut.add_done_callback(self._log_command_completion)

    def _on_pause_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        pipeline, host = self._require_initialized()
        snap = pipeline.snapshot()
        if snap.state == "running":
            fut = host.submit(pipeline.pause())
        elif snap.state == "paused":
            fut = host.submit(pipeline.resume())
        else:
            self._logger.debug("ui_pause_skipped", reason=f"state={snap.state}")
            return
        fut.add_done_callback(self._log_command_completion)

    def _on_home_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        pipeline, host = self._require_initialized()
        fut = host.submit(pipeline.home())
        fut.add_done_callback(self._handle_home_done)

    def _on_estop_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        pipeline, host = self._require_initialized()
        fut = host.submit(pipeline.e_stop())
        fut.add_done_callback(self._log_command_completion)

    def _on_save_config_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        """D-11 Save Config: validate -> persist -> teardown -> rebuild -> resume.

        Restart sequence per RESEARCH §Pitfall 3 (fresh PipelineThreadHost
        + fresh Pipeline; the old host is ``stop()``-ed and discarded).
        On ``ValidationError`` the operator-facing banner is set, the
        ``_pending_config`` buffer is preserved, and no Pipeline restart
        is attempted (CONTEXT.md "Specific Ideas" line 182).

        CR-02 rollback discipline: the new Pipeline + PipelineThreadHost
        are constructed + started in locals BEFORE the old host is torn
        down. If ``_pipeline_factory`` / ``_host_factory`` / new
        ``host.start()`` raises, ``self._config`` / ``self._pipeline`` /
        ``self._host`` retain the OLD values (no swap), the
        ``_pending_config`` buffer is preserved so the operator can retry
        or revert sliders, and a red banner surfaces the failure. The old
        host is teardown-eligible only once the new host is fully up.
        """
        del sender, app_data, user_data
        if not self._pending_config:
            self._logger.info("ui_save_config_noop", reason="no_pending_changes")
            return
        keys = list(self._pending_config.keys())
        self._logger.info("ui_save_config_attempted", keys=keys)
        # 1. Re-validate. Pydantic v2 ``model_copy(update=...)`` does
        #    NOT re-run field validators by default -- we feed the
        #    copy's dump back through ``Config.model_validate(...)`` so
        #    D-12 (dual-bound enforcement) catches Pitfall 5 (text-entry
        #    that bypassed the slider visual clamp).
        try:
            unvalidated = self._config.model_copy(update=self._pending_config)
            new_config = Config.model_validate(unvalidated.model_dump())
        except ValidationError as exc:
            self._logger.warning(
                "config_validation_failed", errors=exc.errors()
            )
            self._show_error_banner(self._format_validation_error(exc))
            return  # do NOT clear _pending_config
        # WR-02 tiger-style guard (shared helper) -- explicit raise, not
        # ``assert``, so the invariant holds under ``python -O``.
        self._require_initialized()
        # 2. Persist BEFORE any teardown -- a crash during the teardown
        #    chain still leaves the new config on disk for the next boot.
        self._write_config_json(new_config)
        # 3. CR-02 + CR-01: Build the new pipeline + host EAGERLY in
        #    locals AND block on the new pipeline's ``start()`` result
        #    BEFORE tearing down the old host. Failures here (factory
        #    raise, host.start() barrier timeout, OR hardware-rejection
        #    of the new config inside ``pipeline.start()``) leave
        #    ``self._*`` pointing at the still-alive old pair and
        #    ``_pending_config`` preserved so the operator can retry or
        #    revert sliders. Mirrors ``run()``'s blocking-start contract
        #    (line 263-265) which is the documented hardware-failure
        #    surface for ``__main__.main``'s ``EXIT_HARDWARE_FAILED``
        #    translator ladder.
        try:
            new_pipeline = self._pipeline_factory(new_config)
            new_host = self._host_factory()
            new_host.start()
            new_host.submit(new_pipeline.start()).result(
                timeout=_PIPELINE_START_TIMEOUT_SEC
            )
        except (
            CameraError,
            ArduinoError,
            ArduinoPortNotFoundError,
            PerceptionError,
            OrchestratorRejected,
            TimeoutError,
            RuntimeError,
        ) as exc:
            self._logger.error(
                "ui_save_config_restart_failed",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            self._show_error_banner(
                f"{_ERROR_BANNER_PREFIX}restart failed: {exc}"
            )
            # CR-02 rollback: do NOT swap. Old host + pipeline are still
            # the canonical pair; _pending_config is preserved so the
            # operator can retry or revert sliders. Best-effort cleanup
            # of the partially-built new_host if it managed to start
            # (so the orphan thread does not survive the failed restart).
            new_host_local = locals().get("new_host")
            if new_host_local is not None:
                try:
                    new_host_local.stop()
                except Exception as cleanup_exc:  # noqa: BLE001 -- best-effort orphan cleanup
                    self._logger.warning(
                        "ui_save_config_orphan_host_stop_failed",
                        exc_type=type(cleanup_exc).__name__,
                        exc_msg=str(cleanup_exc),
                    )
            return
        # 4. New pipeline is up + running. Tear down the old host
        #    (Pitfall 3 -- threads + event loops are single-use).
        old_host = self._host
        old_pipeline = self._pipeline
        try:
            old_host.submit(old_pipeline.quit()).result(
                timeout=_SAVE_QUIT_TIMEOUT_SEC
            )
        except (OrchestratorRejected, TimeoutError) as exc:
            self._logger.warning(
                "ui_save_quit_failed",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
        old_host.stop()
        # 5. Commit the swap. ``self._*`` now references the new pair;
        #    the new pipeline is already running.
        self._config = new_config
        self._pipeline = new_pipeline
        self._host = new_host
        # 6. Clear buffer ONLY on full success.
        self._pending_config.clear()
        self._refresh_unsaved_badge()
        self._clear_error_banner()
        self._logger.info("ui_pipeline_restart_complete", keys=keys)

    # ----- Save Config helpers ------------------------------------------

    def _write_config_json(self, new_config: Config) -> None:
        """Write the merged Config to ``self._config_json_path`` as JSON.

        ``model_dump(mode='json')`` coerces ``Path`` -> ``str`` per the
        pydantic v2 contract (RESEARCH §"Don't Hand-Roll" row 10).
        """
        payload = new_config.model_dump(mode="json")
        self._config_json_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _format_validation_error(self, exc: ValidationError) -> str:
        """One-line operator-facing banner text from a ValidationError."""
        details = "; ".join(
            f"{err['loc'][0] if err['loc'] else '?'}: {err['msg']}"
            for err in exc.errors()
        )
        return f"{_ERROR_BANNER_PREFIX}{details}"

    def _show_error_banner(self, text: str) -> None:
        """Set the red error banner text + widget value."""
        self._error_banner_text = text
        if self._tag_error_banner:
            dpg.set_value(self._tag_error_banner, text)

    def _clear_error_banner(self) -> None:
        """Reset the error banner to empty (called on Save success)."""
        self._error_banner_text = ""
        if self._tag_error_banner:
            dpg.set_value(self._tag_error_banner, "")

    # ----- Modal-decision logic (Pitfall 7) -----------------------------

    def should_prompt_save(self) -> bool:
        """Pure helper -- True when quit has unsaved edits in the buffer.

        Modal *rendering* is exempt from automated tests (DearPyGui v2.x
        has no headless runner); the *decision* is unit-tested by
        invoking this helper + the three ``_on_modal_*`` callbacks
        directly.
        """
        return bool(self._pending_config)

    def _show_unsaved_modal(self) -> None:
        """Lazy-construct the 3-button "unsaved changes" modal (Pitfall 7).

        Modal rendering itself is verified manually (Phase 8 QA-04 smoke).
        Tests stub this method via ``Mock``; the decision logic lives in
        :meth:`_on_modal_save_and_quit` / ``_on_modal_quit_anyway`` /
        ``_on_modal_cancel`` which are unit-testable in isolation.

        WR-01: the modal window tag is captured into ``self._tag_modal``
        so every callback exit can delete the widget. Re-entry while a
        modal is already open is a no-op (prevents a second X-press from
        layering a fresh modal over the live one).
        """
        # Modal UI construction is rendered only -- behavior is covered
        # by the three callbacks below. This method intentionally has no
        # automated test (CONTEXT.md "Claude's Discretion": render-loop
        # coverage is exempt).
        if self._tag_modal:
            # WR-01: modal already up; no-op so we do not stack widgets.
            return
        with dpg.window(
            label="Unsaved changes",
            modal=True,
            no_close=True,
        ) as modal_tag:
            self._tag_modal = modal_tag
            dpg.add_text("You have unsaved changes. What would you like to do?")
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save & Quit", callback=self._on_modal_save_and_quit
                )
                dpg.add_button(
                    label="Quit Anyway", callback=self._on_modal_quit_anyway
                )
                dpg.add_button(label="Cancel", callback=self._on_modal_cancel)

    def _delete_modal_if_open(self) -> None:
        """WR-01: idempotent modal-widget cleanup.

        Called from every ``_on_modal_*`` exit branch so the DPG widget
        tree does not accumulate a stale modal per Cancel/X cycle. Safe
        to call when ``self._tag_modal == 0`` (no-op).
        """
        if not self._tag_modal:
            return
        dpg.delete_item(self._tag_modal)
        self._tag_modal = 0

    def _on_modal_save_and_quit(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        """Save & Quit: run Save Config; only stop DPG on full success."""
        self._on_save_config_pressed(sender, app_data, user_data)
        if not self._pending_config:
            # Save cleared the buffer -> success path. WR-01: tear down
            # the modal before stop_dearpygui (otherwise the widget
            # registry leaks on the path where DPG re-fires the exit
            # callback during shutdown).
            self._delete_modal_if_open()
            dpg.stop_dearpygui()
        # else: Save failed; banner is up; modal stays open (operator
        # can pick Quit Anyway or Cancel).

    def _on_modal_quit_anyway(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        """Quit Anyway: discard the buffer + stop DPG.

        Discarding is required so re-entry of :meth:`_on_exit_callback`
        (which DPG may fire again during shutdown) does NOT re-prompt
        the modal in an infinite loop.
        """
        del sender, app_data, user_data
        self._logger.warning(
            "ui_quit_with_unsaved_changes", count=len(self._pending_config)
        )
        self._pending_config.clear()
        # WR-01: process is exiting anyway, but consistency keeps the
        # widget tree clean if a future change inserts a teardown step
        # between here and dpg.destroy_context().
        self._delete_modal_if_open()
        dpg.stop_dearpygui()

    def _on_modal_cancel(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        """Cancel: dismiss the modal; leave buffer + pipeline untouched."""
        del sender, app_data, user_data
        self._logger.debug("ui_modal_cancel")
        # WR-01: dismissing the modal must actually delete the widget;
        # the ``modal=True`` flag visually hides on Cancel-button click
        # only when DPG renders a close-X (we set ``no_close=True``), so
        # without explicit deletion the window persists in the tree.
        self._delete_modal_if_open()

    def _on_quit_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        self._logger.info("ui_quit_initiated")
        dpg.stop_dearpygui()

    # ----- done-callbacks ------------------------------------------------

    def _handle_home_done(self, fut: Future[None]) -> None:
        """Pitfall 9: home is NOT idempotent on HOMING; catch the typed exception.

        WR-04: ``Future.exception()`` itself raises ``CancelledError`` when
        the future was cancelled (e.g. ``host.stop()`` during Save Config
        restart or quit drains in-flight futures). Guard with
        ``fut.cancelled()`` before reading the exception so the
        callback returns cleanly instead of letting ``CancelledError``
        escape into asyncio's loop callback machinery (which would log
        it as an unhandled callback exception and risk tripping
        ``filterwarnings=['error']`` policies per Phase 2 W-05 precedent).
        """
        if fut.cancelled():
            return
        exc = fut.exception()
        if isinstance(exc, OrchestratorRejected):
            self._logger.debug("ui_home_skipped", reason=str(exc))
            return
        if exc is not None:
            self._logger.warning(
                "ui_command_failed",
                command="home",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )

    def _log_command_completion(self, fut: Future[None]) -> None:
        """WR-04: cancellation-safe done-callback for lifecycle commands."""
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is None:
            return
        self._logger.warning(
            "ui_command_failed",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )

    # ----- render tick (D-03 atomic reads, D-08 skip) -------------------

    def _render_tick(self) -> None:
        """Per-frame render. Atomic reads; skip when timestamp unchanged.

        Render-loop visual rendering itself is verified manually (DPG has
        no headless runner per CONTEXT.md). The skip branch (D-08) is
        unit-tested via the dpg.set_value mock.

        Plan 07-04 wires the 10 Hz status refresh (D-15): the FPS rolling
        window samples EVERY rendered frame via ``record_frame`` (so FPS
        reflects the actual render rate, not the refresh rate), while
        ``refresh`` fires once per ``_STATUS_REFRESH_DIVISOR`` frames.
        """
        # WR-02 tiger-style guard inlined (render-tick hot path -- avoids
        # the tuple alloc the shared :meth:`_require_initialized` helper
        # would do; only the pipeline ref is read in this method).
        if self._pipeline is None:
            raise RuntimeError(
                "Dashboard render tick fired before run() initialized pipeline"
            )
        frame = self._pipeline.latest_frame  # atomic CPython attr read (D-03)
        if frame is not None and frame.timestamp_ns != self._last_rendered_ts_ns:
            self._upload_texture(frame)
            self._redraw_overlays(self._pipeline.snapshot())
            self._status_panel.record_frame(frame.timestamp_ns)
            self._last_rendered_ts_ns = frame.timestamp_ns
        if self._frame_count % _STATUS_REFRESH_DIVISOR == 0:
            self._status_panel.refresh(
                self._pipeline.snapshot(), self._unsaved_badge_text()
            )
        self._frame_count += 1

    def _upload_texture(self, frame: Frame) -> None:
        buf = bgr_frame_to_rgba_float_flat(
            frame,
            self._config.preview_width_px,
            self._config.preview_height_px,
        )
        dpg.set_value(self._tag_texture, buf)

    def _redraw_overlays(self, snap: PipelineSnapshot) -> None:
        """WR-03: mutate the pre-allocated overlay items, no per-tick churn.

        Uses ``dpg.configure_item`` for endpoint updates and
        ``dpg.show_item`` / ``dpg.hide_item`` for the third-line + bbox
        when the snapshot has no target / no bbox. The drawlist itself
        retains a stable set of 5 children for the life of the
        dashboard, so the DPG widget hash-map grows by 0 per render tick
        instead of ~150/sec.
        """
        preview_w = self._config.preview_width_px
        preview_h = self._config.preview_height_px
        third = third_line_pixels(snap, preview_w, preview_h)
        if third is not None:
            dpg.configure_item(
                self._tag_overlay_third_line, p1=third[0], p2=third[1]
            )
            dpg.show_item(self._tag_overlay_third_line)
        else:
            dpg.hide_item(self._tag_overlay_third_line)
        bbox = bbox_rect_pixels(snap, preview_w, preview_h)
        if bbox is not None:
            dpg.configure_item(
                self._tag_overlay_bbox, pmin=bbox[0], pmax=bbox[1]
            )
            dpg.show_item(self._tag_overlay_bbox)
        else:
            dpg.hide_item(self._tag_overlay_bbox)
        dpg.configure_item(self._tag_overlay_id_text, text=id_lock_text(snap))
        dpg.configure_item(self._tag_overlay_angle_text, text=angle_text(snap))

    # ----- D-04 quit sequence -------------------------------------------

    def _initiate_quit(self) -> None:
        """D-04 quit sequence -- idempotent (Pitfall 7).

        Order: ``host.submit(pipeline.quit()).result(timeout=...)``
            -> ``host.stop()`` -> ``dpg.stop_dearpygui()``.
        ``dpg.destroy_context()`` is called by ``run()``'s finally AFTER
        this returns.
        """
        if self._quit_initiated:
            return
        self._quit_initiated = True
        if self._host is not None and self._pipeline is not None:
            try:
                self._host.submit(self._pipeline.quit()).result(
                    timeout=_PIPELINE_QUIT_TIMEOUT_SEC
                )
            except (OrchestratorRejected, TimeoutError) as exc:
                self._logger.warning(
                    "ui_quit_failed",
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 -- documented translator at process boundary (mirrors __main__.py)
                self._logger.warning(
                    "ui_quit_failed",
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
            self._host.stop()
        dpg.stop_dearpygui()

    def _on_exit_callback(self) -> None:
        """Pitfall 7 hook for the viewport close-X.

        With unsaved edits in ``_pending_config`` the close intent is
        routed through the 3-button modal (Save & Quit / Quit Anyway /
        Cancel). With an empty buffer the close happens immediately via
        ``dpg.stop_dearpygui()`` -- ``run()``'s ``finally`` then drives
        the D-04 quit sequence.
        """
        if self.should_prompt_save():
            self._show_unsaved_modal()
            return
        dpg.stop_dearpygui()


__all__ = ["Dashboard"]
