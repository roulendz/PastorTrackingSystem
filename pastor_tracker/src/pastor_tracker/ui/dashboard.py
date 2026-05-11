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
    and the Save Config restart sequence. Status panel + EventBus
    processor land in Plan 07-04 (the 6 reserved status ``add_text``
    widget slots and the ``_unsaved_badge_text`` hook are pre-wired).
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING, Final

import dearpygui.dearpygui as dpg
import numpy as np
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Frame, PipelineSnapshot
from pastor_tracker.pipeline import OrchestratorRejected, Pipeline
from pastor_tracker.ui._overlays import (
    angle_text,
    bbox_rect_pixels,
    bgr_frame_to_rgba_float_flat,
    id_lock_text,
    third_line_pixels,
)
from pastor_tracker.ui._pipeline_thread import PipelineThreadHost

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


def _default_pipeline_factory(config: Config) -> Pipeline:
    """Default lazy Pipeline construction.

    Plan 07-04 will replace this with the same factory ``__main__._amain``
    uses today (8-stage construction + discover_arduino_port). Plan 07-02
    only needs the surface; tests inject a Mock(spec=Pipeline) via the
    ``pipeline_factory`` parameter so this function is never called from
    a non-rendering test.
    """
    # The full construction lives in __main__._amain (Phase 6). Plan 07-04
    # wires the dashboard into the real boot path; until then this factory
    # exists only so the ``run()`` signature is complete.
    raise NotImplementedError(
        "_default_pipeline_factory is wired in Plan 07-04; tests must inject "
        "a Mock(spec=Pipeline) via pipeline_factory."
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
            # --- RIGHT: sliders + buttons + status ---
            with dpg.group():
                self._build_slider_stubs()
                self._build_buttons(estop_theme)
                self._build_status_slots()
        self._build_hotkeys()

    def _build_slider_stubs(self) -> None:
        """Plan 07-02 stub: 4 sliders with bounds; callback only logs DEBUG.

        Plan 07-03 swaps in the real ``_pending_config[key] = value``
        callback + unsaved-changes badge refresh.
        """
        dpg.add_slider_float(
            label="pan_time_constant_sec",
            default_value=self._config.pan_time_constant_sec,
            min_value=_PAN_TIME_CONSTANT_MIN_SEC,
            max_value=_PAN_TIME_CONSTANT_MAX_SEC,
            format="%.2f",
            callback=self._on_slider_change_stub,
        )
        dpg.add_slider_float(
            label="pan_deadband_deg",
            default_value=self._config.pan_deadband_deg,
            min_value=_PAN_DEADBAND_MIN_DEG,
            max_value=_PAN_DEADBAND_MAX_DEG,
            format="%.2f",
            callback=self._on_slider_change_stub,
        )
        dpg.add_slider_float(
            label="pan_max_velocity_deg_per_sec",
            default_value=self._config.pan_max_velocity_deg_per_sec,
            min_value=_PAN_VELOCITY_MIN_DEG_PER_SEC,
            max_value=_PAN_VELOCITY_MAX_DEG_PER_SEC,
            format="%.1f",
            callback=self._on_slider_change_stub,
        )
        dpg.add_slider_float(
            label="camera_horizontal_fov_deg",
            default_value=self._config.camera_horizontal_fov_deg,
            min_value=_FOV_MIN_DEG,
            max_value=_FOV_MAX_DEG,
            format="%.1f",
            callback=self._on_slider_change_stub,
        )

    def _build_buttons(self, estop_theme: int) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start", callback=self._on_start_pressed)
            dpg.add_button(label="Pause", callback=self._on_pause_pressed)
            dpg.add_button(label="Home", callback=self._on_home_pressed)
            estop_btn = dpg.add_button(label="E-Stop", callback=self._on_estop_pressed)
            dpg.bind_item_theme(estop_btn, estop_theme)
            dpg.add_button(label="Save Config", callback=self._on_save_config_pressed)

    def _build_status_slots(self) -> None:
        """Plan 07-04 wires the 10 Hz refresh into these 6 reserved slots."""
        dpg.add_text("Pipeline:    —")
        dpg.add_text("Motor link:  —")
        dpg.add_text("Camera FPS:  —")
        dpg.add_text("Confidence:  —")
        dpg.add_text("ID lock:     —")
        dpg.add_text("Last error:  —")

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

    # ----- Plan 07-03 / 07-04 forward-compat hooks ----------------------

    def _unsaved_badge_text(self) -> str:
        """Returns "(unsaved changes)" when _pending_config is non-empty.

        Plan 07-02 stub: always returns empty string. Plan 07-03 swaps in
        the real ``"(unsaved changes)" if self._pending_config else ""``.
        """
        return ""

    def _on_slider_change_stub(
        self, sender: int, app_data: float, user_data: object
    ) -> None:
        """Plan 07-02 stub. Plan 07-03 lands `_pending_config[key] = value`."""
        del sender, app_data, user_data
        self._logger.debug("ui_slider_stub_invoked")

    # ----- button + hotkey callbacks ------------------------------------

    def _on_start_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        assert self._pipeline is not None
        assert self._host is not None
        fut = self._host.submit(self._pipeline.start())
        fut.add_done_callback(self._log_command_completion)

    def _on_pause_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        assert self._pipeline is not None
        assert self._host is not None
        snap = self._pipeline.snapshot()
        if snap.state == "running":
            fut = self._host.submit(self._pipeline.pause())
        elif snap.state == "paused":
            fut = self._host.submit(self._pipeline.resume())
        else:
            self._logger.debug("ui_pause_skipped", reason=f"state={snap.state}")
            return
        fut.add_done_callback(self._log_command_completion)

    def _on_home_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        assert self._pipeline is not None
        assert self._host is not None
        fut = self._host.submit(self._pipeline.home())
        fut.add_done_callback(self._handle_home_done)

    def _on_estop_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        assert self._pipeline is not None
        assert self._host is not None
        fut = self._host.submit(self._pipeline.e_stop())
        fut.add_done_callback(self._log_command_completion)

    def _on_save_config_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        self._logger.warning(
            "ui_save_config_not_implemented",
            note="Plan 07-03 lands the restart sequence",
        )

    def _on_quit_pressed(
        self, sender: int, app_data: object, user_data: object
    ) -> None:
        del sender, app_data, user_data
        self._logger.info("ui_quit_initiated")
        dpg.stop_dearpygui()

    # ----- done-callbacks ------------------------------------------------

    def _handle_home_done(self, fut: Future[None]) -> None:
        """Pitfall 9: home is NOT idempotent on HOMING; catch the typed exception."""
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
        """
        assert self._pipeline is not None
        frame = self._pipeline.latest_frame  # atomic CPython attr read (D-03)
        if frame is not None and frame.timestamp_ns != self._last_rendered_ts_ns:
            self._upload_texture(frame)
            self._redraw_overlays(self._pipeline.snapshot())
            self._last_rendered_ts_ns = frame.timestamp_ns
        # Plan 07-04 inserts the 10 Hz status refresh here:
        #   if self._frame_count % _STATUS_REFRESH_DIVISOR == 0:
        #       self._status_panel.refresh(self._pipeline.snapshot())
        self._frame_count += 1

    def _upload_texture(self, frame: Frame) -> None:
        buf = bgr_frame_to_rgba_float_flat(
            frame,
            self._config.preview_width_px,
            self._config.preview_height_px,
        )
        dpg.set_value(self._tag_texture, buf)

    def _redraw_overlays(self, snap: PipelineSnapshot) -> None:
        preview_w = self._config.preview_width_px
        preview_h = self._config.preview_height_px
        dpg.delete_item(self._tag_drawlist, children_only=True)
        dpg.draw_image(
            self._tag_texture,
            (0, 0),
            (preview_w, preview_h),
            parent=self._tag_drawlist,
        )
        third = third_line_pixels(snap, preview_w, preview_h)
        if third is not None:
            dpg.draw_line(third[0], third[1], parent=self._tag_drawlist)
        bbox = bbox_rect_pixels(snap, preview_w, preview_h)
        if bbox is not None:
            dpg.draw_rectangle(bbox[0], bbox[1], parent=self._tag_drawlist)
        dpg.draw_text(
            _ID_LOCK_TEXT_POS,
            id_lock_text(snap),
            parent=self._tag_drawlist,
        )
        dpg.draw_text(
            (10, preview_h - _ANGLE_TEXT_BOTTOM_OFFSET_PX),
            angle_text(snap),
            parent=self._tag_drawlist,
        )

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

        Plan 07-02 stub: just call ``stop_dearpygui`` to break out of the
        render loop; the ``run()`` finally runs ``_initiate_quit`` after.
        Plan 07-03 swaps in the modal-decision logic for unsaved
        ``_pending_config`` changes.
        """
        # Plan 07-03: replace with modal-decision logic on _pending_config.
        dpg.stop_dearpygui()


__all__ = ["Dashboard"]
