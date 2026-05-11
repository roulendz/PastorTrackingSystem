# Phase 7: UI Dashboard - Context

**Gathered:** 2026-05-11
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

A single-window DearPyGui operator dashboard with live preview, live tuning sliders, status panel, and hotkeys — so a human can run, tune, and emergency-stop the system on stage without touching code.

**In scope:**
- `pastor_tracker/ui/dashboard.py` — `Dashboard` class owning the DearPyGui main loop, the asyncio background thread, the bridge between them, the live preview drawlist + dynamic raw_texture, the 4 tuning sliders, 5 lifecycle buttons, status panel, and global hotkey registry.
- `pastor_tracker/ui/_pipeline_thread.py` — `PipelineThreadHost` helper: starts asyncio event loop in a `threading.Thread`, holds the `Pipeline` instance, exposes `submit(coro)` wrapping `asyncio.run_coroutine_threadsafe(coro, loop)`; owns cross-thread lifecycle (start/quit).
- `pastor_tracker/ui/_overlays.py` — pure draw helpers: `draw_skeleton`, `draw_subject_badge`, `draw_third_line`, `draw_angle_text`. Pure transforms on coords — no DearPyGui state mutation outside the drawlist handle.
- `pastor_tracker/ui/_status_panel.py` — read-only widget builder + 10 Hz refresh callback that reads `Pipeline.snapshot()` + the event-bus ring buffer.
- `pastor_tracker/ui/_event_bus.py` — `EventBusProcessor` for structlog: tap-and-pass processor that pushes WARN/ERROR events into a bounded `collections.deque(maxlen=64)` consumed by the status panel.
- `pastor_tracker/__main__.py` — UI entry mode: `--ui` flag (default) spawns `Dashboard()`; `--headless` keeps Phase 6 path for CI / Phase 8 smoke.
- `pastor_tracker/tests/test_dashboard.py` — non-rendering unit tests of dashboard internals.
- `pastor_tracker/tests/test_event_bus.py` — structlog tap processor unit tests.
- `pastor_tracker/tests/test_overlays.py` — overlay coord math.

**Out of scope (later phases / forbidden):**
- Multi-window / dockable layout — single window per ROADMAP UI-01
- Tabs, settings dialog, theme picker — out of scope (v2)
- On-stage smoke against real Uno + real OBS + real speaker (Phase 8 / QA-04)
- Recording / replay buttons — v2 telemetry
- Hot-reload of Config without restart — Phase 6 D-18 forbids; "Save Config" is restart-only
- Tilt-axis sliders or framing controls beyond the 4 specified — pan-only mount in v1
- PID / EMA toggles — forbidden by PROJECT.md and CLAUDE.md

**Requirements covered:** UI-01, UI-02, UI-03, UI-04, UI-05.

</domain>

<decisions>
## Implementation Decisions

### Locked Decisions (D-01..D-16) — Stable IDs for Plan Citation

| ID | Source Area | Decision |
|----|-------------|----------|
| D-01 | Area 1 | Pipeline runs in `threading.Thread(target=lambda: asyncio.run(_pipeline_main(...)))`. DearPyGui owns main thread + render loop. |
| D-02 | Area 1 | UI to Pipeline bridge is `loop.call_soon_threadsafe(asyncio.ensure_future, pipeline.<cmd>())`; UI thread never awaits a coroutine. |
| D-03 | Area 1 | UI render tick reads `pipeline.latest_frame` and `pipeline.snapshot()` as atomic CPython attribute reads at 30 Hz; no locks. |
| D-04 | Area 1 | Quit order: UI Quit -> `loop.call_soon_threadsafe(pipeline.quit())` -> `thread.join()` -> `dpg.stop_dearpygui()`. |
| D-05 | Area 2 | Live preview uses one `dpg.add_raw_texture(W, H, format=RGBA)` updated each tick via `dpg.set_value(tex, flat_buf)`. |
| D-06 | Area 2 | Frame bytes: `cv2.cvtColor(frame.image, COLOR_BGR2RGBA)`, optional `cv2.resize` to 960x540 if input larger, normalize float32/255 for DearPyGui. |
| D-07 | Area 2 | Overlays drawn via DearPyGui `draw_line`/`draw_circle`/`draw_text` over the image — NOT burned into BGR. Pure helpers in `_overlays.py`. |
| D-08 | Area 2 | Render tick = 30 Hz; skip render when `Pipeline.latest_frame.timestamp_ns` matches the last rendered value. |
| D-09 | Area 3 | Exactly 4 sliders: `pan_time_constant_sec` [0.2..2.0], `pan_deadband_deg` [0.05..2.0], `pan_max_velocity_deg_per_sec` [5..120], `camera_horizontal_fov_deg` [40..120]. |
| D-10 | Area 3 | Slider edits update UI-side `_pending_config: dict[str, float]`. Pipeline keeps running on active Config. Status badge shows "(unsaved changes)" when buffer non-empty. |
| D-11 | Area 3 | "Save Config": `new_config = current_config.model_copy(update=_pending_config)`, write JSON, `await pipeline.quit()` -> reconstruct Pipeline -> `await pipeline.start()`. Pydantic `ValidationError` -> red error banner; `_pending_config` cleared only on success. (Phase 6 D-18 strict restart.) |
| D-12 | Area 3 | Bounds enforcement is dual: DearPyGui `min_value`/`max_value` + Pydantic validation on Save. |
| D-13 | Area 4 | Single `dpg.window(no_close=True)`. Horizontal split: left = preview drawlist (960x540), right column = sliders (top) + buttons (mid) + status (bottom). |
| D-14 | Area 4 | `dpg.add_handler_registry()` with global `add_key_press_handler` for S/P/H/E/Q -> start/pause(toggle resume)/home/e_stop/quit. Fires regardless of focus per UI-05. |
| D-15 | Area 4 | Status fields refreshed at 10 Hz (every 3rd 30 Hz render frame): pipeline state, motor link state, camera FPS (rolling 30-frame avg), last detection confidence, ID lock state, last_error+timestamp. |
| D-16 | Area 4 | `EventBusProcessor` inserted into structlog chain by `configure_logging()` — taps WARN/ERROR into `deque(maxlen=64)`; status panel reads head. Tap-and-pass; logging output unchanged. |

### Locked by PROJECT.md / CLAUDE.md / Phase 6 contracts
- Forbidden: PID, EMA, mocked Kalman / damping math in tests, `print()`, bare `except:`, magic numbers, nested > 2 conditionals
- Pure-core / dirty-edges — `ui/` is rim; cannot import `intent/` or `control/` except via `Pipeline.snapshot()` / `Pipeline.latest_frame`
- Pydantic v2 frozen DTOs; `Config` frozen — mutate via `.model_copy(update=...)` (D-11)
- `mypy --strict` no `Any`; `structlog` JSON logging only
- Conventional Commits, one logical change per commit
- Phase 6 D-15..D-18 — Phase 7 talks to Pipeline ONLY through the public surface (`start/pause/resume/home/e_stop/quit/snapshot/latest_frame`)
- Phase 6 D-07 — e-stop within 200 ms; UI button must call `loop.call_soon_threadsafe(...)` synchronously
- Phase 6 D-18 — Config reload is restart; never hot-reload frozen fields
- DearPyGui v2.x — manual frame loop pattern (`dpg.is_dearpygui_running` + `dpg.render_dearpygui_frame`) so per-frame skip logic works (D-08)

### Threading & Pipeline Integration (Area 1, all accepted) — D-01..D-04
- asyncio Pipeline lives in background thread spawned by `Dashboard.__init__`. Event loop reference captured at thread startup via `threading.Event` sync point.
- All UI to asyncio calls go through `host.submit(pipeline.<cmd>())` -> `asyncio.run_coroutine_threadsafe(coro, self._loop)`. Returned `concurrent.futures.Future` fire-and-forget for lifecycle buttons; UI never blocks.
- UI render tick reads `pipeline.latest_frame` and `pipeline.snapshot()`. Atomic CPython reads; no locking.
- Quit order deterministic: UI Quit / Q hotkey / window close -> `host.submit(pipeline.quit())` -> `host.thread.join(timeout=5.0)` -> `dpg.stop_dearpygui()` -> `dpg.destroy_context()`. Idempotent (Phase 6 D-09).

### Live Preview Rendering (Area 2, all accepted) — D-05..D-08
- Texture created once at startup with `Config.preview_width_px` x `Config.preview_height_px` (default 960x540). `dpg.set_value(tex_id, flat_buf)` per tick.
- Frame conversion UI-side: `cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGBA)` -> `cv2.resize(...)` if larger -> `.astype(np.float32) / 255.0` -> flatten 1-D. Pipeline never sees the conversion.
- Overlays use `dpg.draw_line`, `dpg.draw_circle`, `dpg.draw_text` against drawlist. Coords from pure helpers in `_overlays.py` (normalized + image dims -> pixels). Drawlist rebuilt each tick.
- Render skip: compare `pipeline.latest_frame.timestamp_ns` to last rendered ts; if equal, skip texture upload + overlay rebuild but still pump `dpg.render_dearpygui_frame()`.

### Sliders, Save Config, Hot-reload (Area 3, all accepted) — D-09..D-12
- 4 sliders bound to keys `pan_time_constant_sec`, `pan_deadband_deg`, `pan_max_velocity_deg_per_sec`, `camera_horizontal_fov_deg`.
- Slider `callback` stores new value into `Dashboard._pending_config[key]`; repaints "(unsaved changes)" badge.
- "Save Config" callback:
  1. `try: new_config = self._config.model_copy(update=self._pending_config)` — Pydantic re-validates.
  2. On `ValidationError` -> bus event `"config_validation_failed"` -> red error banner; abort save (do NOT clear buffer).
  3. On success -> write JSON to `self._config_path`, `host.submit(pipeline.quit())`, `host.join(5.0)`, fresh Pipeline from `new_config`, new `PipelineThreadHost`, `host.submit(pipeline.start())`, clear `_pending_config`.
- Bounds: DearPyGui `min_value`/`max_value` clamps drag; Pydantic still validates on Save (text-entry on slider bypasses visual clamp).

### Layout, Hotkeys, Status Panel (Area 4, all accepted) — D-13..D-16
- Single window, initial 1440x640 (960 preview + 480 controls). User can resize.
- Buttons: Start, Pause, Home, E-Stop, Save Config — 5 buttons in horizontal row mid-right. Each dispatches via `host.submit(...)`. E-Stop has red text.
- Hotkeys: `dpg.add_key_press_handler(key=dpg.mvKey_S, callback=lambda s,a: host.submit(pipeline.start()))` etc. for P/H/E/Q. Fire regardless of focus.
- Status panel: vertical list of 6 `dpg.add_text` widgets updated by 10 Hz callback. FPS rolling avg uses `collections.deque(maxlen=30)` of `(now_ns, frame_count)`.
- `EventBusProcessor` inserted in `configure_logging()` AFTER JSON renderer — observes rendered events, pushes `(level, event, timestamp)` into deque. Output unchanged (tap-and-pass).

### Claude's Discretion
- Internal helper / private method names beyond the public Dashboard methods
- Widget tags (`dpg.generate_uuid()` recommended over string tags)
- Pixel positions / colors / font sizes — DearPyGui dark theme defaults
- Whether `_pipeline_thread.py` / `_overlays.py` / `_status_panel.py` split out vs inlined — split is default
- Internal docstrings — only where WHY non-obvious
- Render-loop unit tests beyond the listed coverage — manual smoke acceptable (DearPyGui has no headless runner in v2.x)
- `Config.preview_width_px` / `Config.preview_height_px` defaults — recommend 960x540 with bounds

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/pipeline.Pipeline` — Phase 6 public surface (async lifecycle, `snapshot()`, `latest_frame`). Phase 7 talks ONLY to this.
- `pastor_tracker/core/types.PipelineSnapshot` — 7 fields read by status panel.
- `pastor_tracker/core/types.Frame` — `image: ndarray[uint8]` BGR + `timestamp_ns`. UI converts BGR -> RGBA per D-06.
- `pastor_tracker/core/types.Detection` / `TrackedSubject` — read via `snapshot()` proxies OR by extending PipelineSnapshot with `last_detection` / `last_tracked_subject` (open to plan; no D-XX touch).
- `pastor_tracker/__main__.py` — Phase 6 argparse + `_amain`. Phase 7 adds `--ui` (default) + `--headless` flags; `--ui` delegates to `Dashboard.run()`, `--headless` keeps existing path.
- `pastor_tracker/config.Config` — frozen Pydantic Settings. Sliders read/write 4 fields. May add `preview_width_px` / `preview_height_px` fields.
- `pastor_tracker/logging_config.configure_logging` — Phase 7 inserts `EventBusProcessor` into the chain at dashboard boot ONLY (NOT for headless).
- DearPyGui v2.x — `dpg.create_context`, `dpg.create_viewport`, `dpg.window`, `dpg.add_raw_texture`, `dpg.draw_*`, `dpg.set_value`, `dpg.add_slider_float`, `dpg.add_button`, `dpg.add_handler_registry`, `dpg.add_key_press_handler`, `dpg.set_frame_callback`, `dpg.render_dearpygui_frame`, `dpg.is_dearpygui_running`.

### Established Patterns (Phases 1-6)
- Pure-core / dirty-edges — `ui/` is rim code; only Pipeline public surface consumed
- Pydantic `frozen=True, extra='forbid'` for DTOs; `enum.Enum` for state types (Phase 6 `_PipelineState`)
- Lint: `ruff` T20 / BLE001 / E722 / RUF; `mypy --strict` no `Any`
- Test fakes under `tests/fixtures/`. Phase 7 dashboard tests unit-test pure functions; DearPyGui render-loop integration is manual / Phase 8 QA-04
- Coverage: >= 90% line on pure modules. Render loop coverage exempt — no headless runner in DearPyGui 2.x

### Integration Points
- Upstream: `__main__.py` (with `--ui` / `--headless`) constructs `Config`, then `Dashboard(config, config_path).run()`
- Downstream: none — dashboard is terminus
- Pipeline / Dashboard: `PipelineThreadHost` owns Pipeline + asyncio loop; Dashboard owns the host (constructs in `__init__`, destroys in `run()` finally)

</code_context>

<specifics>
## Specific Ideas

- Status panel field set (D-15):
  ```
  Pipeline:     {state}   {pending_changes_badge}
  Motor link:   {motor_state}
  Camera FPS:   {fps:5.1f}
  Confidence:   {last_conf:.2f}
  ID lock:      {locked_id or 'NONE'}
  Last error:   [{level}] {event}  @ {hh:mm:ss}
  ```

- Hotkey -> command mapping (D-14):
  | DearPyGui key | Command |
  |---------------|---------|
  | `dpg.mvKey_S` | `pipeline.start()` |
  | `dpg.mvKey_P` | `pipeline.pause()` if RUNNING else `pipeline.resume()` (toggle) |
  | `dpg.mvKey_H` | `pipeline.home()` |
  | `dpg.mvKey_E` | `pipeline.e_stop()` |
  | `dpg.mvKey_Q` | initiate full quit sequence (D-04) |

- Slider template (D-09):
  ```python
  dpg.add_slider_float(
      label="pan_time_constant_sec",
      default_value=config.pan_time_constant_sec,
      min_value=0.2, max_value=2.0, format="%.2f",
      callback=lambda s, v: self._pending_config.__setitem__("pan_time_constant_sec", v),
  )
  ```

- `EventBusProcessor` shape (D-16):
  ```python
  class EventBusProcessor:
      def __init__(self, buffer: collections.deque[tuple[str, str, int]]) -> None:
          self._buf = buffer
      def __call__(self, logger, method_name, event_dict):
          if event_dict.get("level") in {"warning", "error"}:
              self._buf.appendleft((event_dict["level"], event_dict.get("event", ""), event_dict.get("timestamp_ns", time.time_ns())))
          return event_dict  # pass-through
  ```

- `_pending_config` discipline:
  - Cleared only on successful Save Config.
  - On ValidationError, bus event pushed, badge stays "(unsaved changes — last save failed)".
  - On UI Quit / window close with pending changes — modal prompt: [Save & Quit] / [Quit Anyway] / [Cancel].

- Test fixtures Phase 7 needs (none new):
  - Mock `Pipeline` via `unittest.mock.Mock(spec=Pipeline)` for `test_dashboard.py`.
  - `_overlays.py` tests use plain math.
  - `_event_bus.py` tests use real `collections.deque` and fake `event_dict`.

- `__main__` adjustments:
  - `--ui / --headless` flag (default `--ui`).
  - `--ui`: lazy import `Dashboard` (DearPyGui heavy); call `Dashboard(config, config_path).run()`.
  - `--headless`: Phase 6 path unchanged.

- Logging shape:
  - `event="ui_command_dispatched" command="start" hotkey="S"` (DEBUG)
  - `event="ui_config_pending_changes" count=2` (DEBUG)
  - `event="ui_config_save_attempted" keys=["pan_deadband_deg"]` (INFO)
  - `event="config_validation_failed" errors=[...]` (WARNING)
  - `event="ui_pipeline_restart_complete" duration_sec=2.3` (INFO)
  - `event="ui_quit_initiated"` (INFO)

</specifics>

<deferred>
## Deferred Ideas

- Multi-window dockable layout / tabs / panels — v2
- Recording / replay of framing decisions — v2 telemetry
- Theme picker — v2
- Settings dialog with all Config knobs beyond D-09 — v2
- Tilt-axis sliders — pan-only mount in v1
- PID / EMA toggles — forbidden by PROJECT.md and CLAUDE.md
- On-screen overlay of hysteresis state machine — operator dashboard for tuning, not debugging
- Per-stage timing histogram in status panel — v2 telemetry
- Hot-reload of subset of Config fields without restart — rejected by Phase 6 D-18
- Headless render-loop integration tests — DearPyGui 2.x has no headless runner; deferred
- Auto-save Config on every slider edit — rejected; pending-buffer + explicit Save Config is discipline

</deferred>
