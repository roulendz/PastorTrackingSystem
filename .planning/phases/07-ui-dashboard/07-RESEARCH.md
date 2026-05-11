# Phase 7: UI Dashboard - Research

**Researched:** 2026-05-08
**Domain:** DearPyGui v2.x operator dashboard wiring an asyncio Pipeline (Phase 6) into a single-window GUI with live preview overlays, 4 tuning sliders, 5 lifecycle buttons, status panel, and global hotkeys. Concurrency-and-wiring phase, not an algorithm phase.
**Confidence:** HIGH on Pipeline public surface and CONTEXT.md D-01..D-16 (CITED to in-tree `pipeline.py`, `core/types.py`, `config.py`, CONTEXT.md); HIGH on Windows asyncio-in-thread + main-thread-DPG split (CITED to dearpygui docs + Phase 6 SIGINT precedent); HIGH on raw_texture / set_value lifecycle (CITED to dearpygui readthedocs); HIGH on structlog processor-chain ordering (CITED to structlog docs — corrective finding vs CONTEXT.md D-16 wording, see §5); MEDIUM on `_pending_config` restart-sequence timing (no prior in-repo asyncio-loop-recreate precedent; design is hand-derived from Phase 6 D-09 idempotence); LOW on DearPyGui Windows multi-monitor DPI scaling (no in-tree precedent; flagged as Pitfall).

## Summary

Phase 7 is **wiring + UI-shell**. Every algorithm is already locked behind `Pipeline.{start, pause, resume, home, e_stop, quit, snapshot, latest_frame}` (Phase 6). The dashboard's only job is to (a) keep DearPyGui on the main thread, (b) run the asyncio Pipeline in a background thread, (c) bridge UI button/hotkey events into the loop via `loop.call_soon_threadsafe(asyncio.ensure_future, pipeline.<cmd>())` per D-02, (d) read `pipeline.latest_frame` + `pipeline.snapshot()` as atomic CPython attribute reads at 30 Hz per D-03, and (e) survive the deterministic D-04 quit ordering (`pipeline.quit()` -> `thread.join()` -> `dpg.stop_dearpygui()` -> `dpg.destroy_context()`).

The risk surface is **concurrency between three event loops** — DearPyGui's render loop on the main thread, the asyncio Pipeline tick task in a background thread, and the Save Config restart sequence which must tear down the asyncio loop, construct a fresh Pipeline + new event loop, and resume — all without dropping the main-thread render contract. Two corrective findings vs CONTEXT.md surface below: (1) §5 — `EventBusProcessor` must be inserted **before** `JSONRenderer` (not after as D-16 states verbatim), because `JSONRenderer` is terminal and returns a string, not an `event_dict`. (2) §11 — `dearpygui` is not in `pyproject.toml` dependencies and must be added in Plan 07-01.

**Primary recommendation:** Adopt the 5-file split CONTEXT.md sketches (`dashboard.py` + `_pipeline_thread.py` + `_overlays.py` + `_status_panel.py` + `_event_bus.py`). Manual frame loop (`while dpg.is_dearpygui_running(): dpg.render_dearpygui_frame()`) on the main thread so the 30 Hz timestamp-equality skip (D-08) is implementable. `PipelineThreadHost` synchronises loop-startup via `threading.Event` so the first `host.submit(...)` cannot race a not-yet-running loop. `EventBusProcessor` inserts at processor-chain index `len(chain)-1` (just before `JSONRenderer`). All non-rendering tests use `Mock(spec=Pipeline)`; render loop is acknowledged manual-only per CONTEXT.md "Claude's Discretion" — DearPyGui v2.x ships no headless runner.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (D-01..D-16, verbatim per CONTEXT.md)

| ID | Decision (1-line reference) |
|----|------------------------------|
| D-01 | Pipeline runs in `threading.Thread(target=lambda: asyncio.run(_pipeline_main(...)))`. DearPyGui owns main thread + render loop. |
| D-02 | UI to Pipeline bridge is `loop.call_soon_threadsafe(asyncio.ensure_future, pipeline.<cmd>())`; UI thread never awaits a coroutine. |
| D-03 | UI render tick reads `pipeline.latest_frame` and `pipeline.snapshot()` as atomic CPython attribute reads at 30 Hz; no locks. |
| D-04 | Quit order: UI Quit -> `loop.call_soon_threadsafe(pipeline.quit())` -> `thread.join()` -> `dpg.stop_dearpygui()`. |
| D-05 | Live preview uses one `dpg.add_raw_texture(W, H, format=RGBA)` updated each tick via `dpg.set_value(tex, flat_buf)`. |
| D-06 | Frame bytes: `cv2.cvtColor(frame.image, COLOR_BGR2RGBA)`, optional `cv2.resize` to 960x540 if input larger, normalize float32/255 for DearPyGui. |
| D-07 | Overlays drawn via DearPyGui `draw_line`/`draw_circle`/`draw_text` over the image — NOT burned into BGR. Pure helpers in `_overlays.py`. |
| D-08 | Render tick = 30 Hz; skip render when `Pipeline.latest_frame.timestamp_ns` matches the last rendered value. |
| D-09 | Exactly 4 sliders: `pan_time_constant_sec` [0.2..2.0], `pan_deadband_deg` [0.05..2.0], `pan_max_velocity_deg_per_sec` [5..120], `camera_horizontal_fov_deg` [40..120]. |
| D-10 | Slider edits update UI-side `_pending_config: dict[str, float]`. Pipeline keeps running on active Config. Status badge shows "(unsaved changes)" when buffer non-empty. |
| D-11 | "Save Config": `new_config = current_config.model_copy(update=_pending_config)`, write JSON, `await pipeline.quit()` -> reconstruct Pipeline -> `await pipeline.start()`. `ValidationError` -> red error banner; `_pending_config` cleared only on success. |
| D-12 | Bounds enforcement is dual: DearPyGui `min_value`/`max_value` + Pydantic validation on Save. |
| D-13 | Single `dpg.window(no_close=True)`. Horizontal split: left = preview drawlist (960x540), right column = sliders (top) + buttons (mid) + status (bottom). |
| D-14 | `dpg.add_handler_registry()` with global `add_key_press_handler` for S/P/H/E/Q -> start/pause(toggle resume)/home/e_stop/quit. Fires regardless of focus per UI-05. |
| D-15 | Status fields refreshed at 10 Hz (every 3rd 30 Hz render frame): pipeline state, motor link state, camera FPS (rolling 30-frame avg), last detection confidence, ID lock state, last_error+timestamp. |
| D-16 | `EventBusProcessor` inserted into structlog chain by `configure_logging()` — taps WARN/ERROR into `deque(maxlen=64)`; status panel reads head. Tap-and-pass; logging output unchanged. **CORRECTIVE: insertion point is BEFORE `JSONRenderer`, not after — see §5.** |

### Locked by PROJECT.md / CLAUDE.md / Phase 6 contracts
- Forbidden: PID, EMA, mocked Kalman/damping math in tests, `print()`, bare `except:`, magic numbers (Config is authoritative), nested conditionals > 2 levels
- Pure-core / dirty-edges — `ui/` is rim code; cannot import `intent/` or `control/` directly; only the `Pipeline` public surface
- Pydantic v2 frozen `Config`; mutate via `.model_copy(update=...)` (D-11)
- `mypy --strict` no `Any`; `structlog` JSON logging only; Conventional Commits
- Phase 6 D-15..D-18 — Phase 7 talks to Pipeline ONLY through `start/pause/resume/home/e_stop/quit/snapshot/latest_frame`
- Phase 6 D-07 — e-stop within 200 ms; UI button must dispatch via `loop.call_soon_threadsafe(...)` synchronously
- Phase 6 D-18 — Config reload is restart; never hot-reload frozen fields

### Claude's Discretion
- Internal helper / private method names beyond the public `Dashboard` methods
- Widget tags (`dpg.generate_uuid()` recommended over string tags)
- Pixel positions / colors / font sizes — DearPyGui dark theme defaults
- Whether `_pipeline_thread.py` / `_overlays.py` / `_status_panel.py` / `_event_bus.py` split out vs inlined — **split is recommended** (see §1)
- Internal docstrings — only where WHY non-obvious
- Render-loop unit tests beyond the listed coverage — **manual smoke acceptable** (DearPyGui has no headless runner in v2.x)
- `Config.preview_width_px` / `Config.preview_height_px` defaults — recommend 960x540 with bounds

### Deferred Ideas (OUT OF SCOPE)
- Multi-window dockable layout / tabs / panels — v2
- Recording / replay of framing decisions — v2 telemetry
- Theme picker — v2
- Settings dialog with all Config knobs beyond D-09 — v2
- Tilt-axis sliders — pan-only mount in v1
- PID / EMA toggles — forbidden by PROJECT.md / CLAUDE.md
- On-screen overlay of hysteresis state machine — operator dashboard for tuning, not debugging
- Per-stage timing histogram in status panel — v2 telemetry
- Hot-reload of subset of Config fields without restart — rejected by Phase 6 D-18
- Headless render-loop integration tests — DearPyGui 2.x has no headless runner; deferred
- Auto-save Config on every slider edit — rejected; pending-buffer + explicit Save Config is discipline
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **UI-01** | DearPyGui single window — live preview with skeleton overlay, subject ID badge, framing-target vertical line, current/target angle text | §1 Module Layout (`dashboard.py` + `_overlays.py`); §2 DearPyGui API (raw_texture + drawlist); §6 Snapshot DTO Extensions; D-05..D-08, D-13 |
| **UI-02** | Live tuning sliders — pan time-constant, deadband, max velocity, FOV | §1 Module Layout (`dashboard.py` slider region); §4 asyncio bridge for slider callbacks; D-09..D-12 |
| **UI-03** | Buttons — Start, Pause, Home, E-Stop, Save Config | §4 asyncio bridge (`host.submit(...)`); §7 Save Config restart sequence; D-11, D-13 |
| **UI-04** | Status panel — motor link state, camera FPS, detection conf, ID lock state, last error | §1 (`_status_panel.py`); §5 EventBusProcessor; §6 Snapshot DTO Extensions; D-15, D-16 |
| **UI-05** | Hotkeys — S start, P pause, H home, E estop, Q quit | §2 (handler_registry); §4 (host.submit dispatch); D-14 |
</phase_requirements>

## Architectural Responsibility Map

> Phase 7 is fully application-tier + OS-process-tier. Hardware/driver and pure-core tiers are read-only (consumed via `Pipeline` surface).

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| DearPyGui render loop (main thread) | OS-Process Tier — `dashboard.py:Dashboard.run()` | — | DearPyGui requires the main thread per upstream docs (see §1); cannot be a worker thread. |
| asyncio Pipeline tick task | Application Tier — `_pipeline_thread.py:PipelineThreadHost` | Pipeline (Phase 6) | D-01 — runs in a background `threading.Thread`; the host owns the loop reference + submit shim. |
| Cross-thread command dispatch | Application Tier — `host.submit(coro)` -> `asyncio.run_coroutine_threadsafe` | OS-Process Tier (button callback fires on main thread) | D-02 — UI thread never awaits; fire-and-forget. |
| Live frame upload (BGR -> RGBA -> raw_texture) | Application Tier — `dashboard.py:_render_tick()` | Pure Core (`Frame.image` is read-only) | D-05..D-08; CPU-side cv2 conversion, GPU upload via `dpg.set_value`. |
| Overlay drawing (skeleton, third, angle) | Pure Core — `_overlays.py` (pure functions on coords) | Application Tier (drawlist mutation lives in `dashboard.py`) | D-07 — overlays are not burned into BGR; drawn over via DearPyGui drawlist. |
| Status panel polling | Application Tier — `_status_panel.py:refresh()` | Pure Core (`PipelineSnapshot` read) | D-15 — 10 Hz callback reads atomic `pipeline.snapshot()`. |
| Slider tuning -> pending buffer | Application Tier — `dashboard.py:_on_slider_change()` | — | D-10 — UI-side dict; never crosses thread boundary until Save Config. |
| Save Config restart sequence | OS-Process Tier — orchestrates teardown + reconstruct | Application Tier (new PipelineThreadHost) | D-11 — full quit-and-rebuild; not a hot-reload (Phase 6 D-18 lock). |
| structlog tap (`EventBusProcessor`) | Application Tier — inserted by `configure_logging()` extension | Pure Core (deque) | D-16 — processor sits before `JSONRenderer` (corrective §5). |
| Global hotkey dispatch | Application Tier — `dpg.handler_registry` | — | D-14 — handler registry callbacks fire regardless of focused widget. |

**Sanity check:** No algorithmic logic landed in Phase 7. The dashboard is a pure consumer of `Pipeline.snapshot()` + `Pipeline.latest_frame`; the `_overlays.py` module is pure coordinate math (no DearPyGui calls inside the helpers — they return tuples of (px_x, px_y) that the dashboard then passes to `dpg.draw_*`). Tier responsibility map matches CONTEXT.md "pure-core / dirty-edges" lock.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `dearpygui` | **2.1.1** (verified PyPI 2026-05-08) | Immediate-mode GUI; main-thread render loop | CLAUDE.md "GUI" row; PROJECT.md "dearpygui (immediate-mode, low-latency live preview)" |
| Python stdlib `threading` | 3.12 | `Thread` + `Event` for PipelineThreadHost | Phase 2 RX-thread precedent (arduino_motor.py); standard pattern |
| Python stdlib `asyncio` | 3.12 | `run_coroutine_threadsafe`, `new_event_loop`, `set_event_loop` | Phase 6 D-09 lifecycle; cross-thread bridge per D-02 |
| Python stdlib `collections.deque` | 3.12 | Event-bus bounded ring buffer (maxlen=64); FPS rolling avg (maxlen=30) | Stdlib; thread-safe `append`/`appendleft`/`pop` per CPython docs |
| `opencv-python` | already pinned (>=4.10,<5.0) | `cv2.cvtColor(BGR2RGBA)` + `cv2.resize` per D-06 | Already in repo (Phase 3); no new dep |
| `pydantic` v2 | already pinned (>=2.13,<3.0) | `Config.model_copy(update=...)` per D-11 | Already in repo (Phase 1) |
| `structlog` | already pinned (>=24.4,<26.0) | `EventBusProcessor` insertion into chain | Already in repo (Phase 1 logging_config.py) |
| `numpy` | already pinned (>=2.4,<3.0) | `.astype(np.float32) / 255.0` + `.flatten()` for set_value | Already in repo |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Python stdlib `argparse` | 3.12 | `--ui` / `--headless` flag in `__main__.py` | Tiny CLI surface; already used in Phase 6 `__main__` |
| Python stdlib `pathlib.Path` | 3.12 | `config.json` write path for D-11 Save | Already imported in `__main__.py` |
| Python stdlib `json` | 3.12 | `json.dump(new_config.model_dump(...), f)` on Save | Stdlib; pydantic v2 `model_dump(mode="json")` handles `Path` -> str |
| `unittest.mock.Mock(spec=Pipeline)` | stdlib | Non-rendering dashboard tests | CONTEXT.md "Specific Ideas" — Mock(spec=...) gives static-typed surface |

### Alternatives Considered (and rejected)
| Instead of | Could Use | Tradeoff | Verdict |
|------------|-----------|----------|---------|
| Background `threading.Thread` + main-thread DPG | `asyncio.run_in_executor` from inside DPG render loop | DPG render loop is synchronous; would need to call `loop.run_until_complete(...)` between frames -> blocks render | **Rejected — D-01 lock; main thread = DPG only.** |
| Pipeline tick task in main thread (asyncio) + DPG in worker thread | `start_dearpygui()` in `threading.Thread` | DearPyGui docs: works best ONLY on main thread + callbacks thread; deadlocks reported on heavy-loaded user threads ([issue #2053](https://github.com/hoffstadt/DearPyGui/issues/2053)) | **Rejected — main thread MUST be DPG.** |
| `dpg.set_frame_callback(N, cb)` for 30 Hz tick | Manual `while dpg.is_dearpygui_running()` + `dpg.render_dearpygui_frame()` | `set_frame_callback` doesn't expose timestamp-equality skip cleanly; manual loop is the documented pattern when you want render skip control | **Manual loop per CONTEXT.md "DearPyGui v2.x — manual frame loop pattern".** |
| Pydantic `BaseModel(frozen=False)` for `_pending_config` | `dict[str, float]` UI-side buffer | A whole pydantic model per slider edit is over-engineering; dict is the minimum that round-trips through `Config.model_copy(update=...)` | **Use dict per CONTEXT.md "Specific Ideas".** |
| `EventBusProcessor` AFTER `JSONRenderer` (literal CONTEXT.md D-16 wording) | `EventBusProcessor` BEFORE `JSONRenderer` | `JSONRenderer` is the terminal processor and returns a `str`, not an `event_dict`. A processor placed after it would receive a string and could not call `event_dict.get(...)` ([structlog docs](https://www.structlog.org/en/stable/processors.html)) | **Insert BEFORE — see §5; this is a corrective finding vs CONTEXT.md.** |

**Installation:**
```bash
# Add to pyproject.toml [project.dependencies] before lock-refresh:
#     "dearpygui>=2.1,<3.0",
uv add "dearpygui>=2.1,<3.0"
uv lock --check
```

**Version verification:**
```bash
# Performed at research time 2026-05-08:
# pip index versions dearpygui  -> 2.1.1 (latest)
# Release notes: https://github.com/hoffstadt/DearPyGui/releases
```
[VERIFIED: PyPI] Latest is `2.1.1` (publish date pre-2026-05-08). [CITED: https://pypi.org/project/dearpygui/]

## Architecture Patterns

### System Architecture Diagram

```
                    ┌──────────────────────────────────────────┐
                    │            MAIN THREAD                   │
                    │                                          │
                    │  __main__.py  --ui (default)             │
                    │     │                                    │
                    │     ▼                                    │
                    │  Dashboard(config, config_path).run()    │
                    │     │                                    │
                    │     ├── configure_logging(+EventBus)     │
                    │     ├── PipelineThreadHost(config)       │
                    │     │       │ (Pipeline runs in BG)      │
                    │     ├── dpg.create_context()             │
                    │     ├── dpg.add_raw_texture(960,540,RGBA)│
                    │     ├── dpg.window(no_close=True)        │
                    │     │     ├── drawlist (preview)         │
                    │     │     ├── 4x slider_float            │
                    │     │     ├── 5x button                  │
                    │     │     └── 6x text (status)           │
                    │     ├── dpg.handler_registry             │
                    │     │     └── add_key_press_handler S/P/H/E/Q
                    │     ├── dpg.show_viewport()              │
                    │     └── while dpg.is_dearpygui_running():│
                    │           render_tick():                 │
                    │             frame = pipeline.latest_frame│ ◄─── ATOMIC read
                    │             snap  = pipeline.snapshot()  │ ◄─── ATOMIC read
                    │             if frame.ts_ns != last_ts:   │
                    │               upload_texture(frame)      │
                    │               redraw_overlays(snap)      │
                    │             if frame_count % 3 == 0:     │
                    │               status_panel.refresh(snap) │
                    │             dpg.render_dearpygui_frame() │
                    │     finally:                             │
                    │           host.submit(pipeline.quit())   │ ─┐
                    │           host.join(timeout=5.0)         │  │
                    │           dpg.stop_dearpygui()           │  │
                    │           dpg.destroy_context()          │  │
                    └──────────────────────────────────────────┘  │
                                  │                                │
                                  │ host.submit(coro) ─────────────┼──► call_soon_threadsafe
                                  │                                │      asyncio.ensure_future
                                  ▼                                │
                    ┌──────────────────────────────────────────┐  │
                    │       PIPELINE THREAD (background)       │◄─┘
                    │                                          │
                    │  asyncio.new_event_loop()                │
                    │  asyncio.set_event_loop(loop)            │
                    │  loop_ready.set()  # threading.Event    │
                    │                                          │
                    │  loop.run_forever()                      │
                    │     │                                    │
                    │     ├── pipeline.start() task            │
                    │     │     └── _tick_loop (Phase 6)       │
                    │     │           writes pipeline._latest_ │
                    │     │             frame + _cache         │
                    │     ├── pipeline.pause/resume/home/      │
                    │     │     e_stop/quit (submitted)        │
                    │     └── on quit: loop.stop()             │
                    └──────────────────────────────────────────┘
                                  │
                                  │ structlog events
                                  ▼
                          ┌────────────────────────┐
                          │ EventBusProcessor      │ (inserted in chain
                          │   bounded deque(64)    │  BEFORE JSONRenderer)
                          └────────────────────────┘
                                  │
                                  ▼ status panel reads deque[0]
```

### Recommended Project Structure

```
pastor_tracker/src/pastor_tracker/ui/
├── __init__.py                    # already exists; bumps to re-export Dashboard
├── dashboard.py                   # ~280-340 lines: Dashboard class + render tick + button/slider/hotkey wiring
├── _pipeline_thread.py            # ~80-110 lines: PipelineThreadHost (start/submit/join/loop-ready Event)
├── _overlays.py                   # ~80-120 lines: pure coord helpers (no DPG state mutation)
├── _status_panel.py               # ~110-150 lines: 10 Hz refresh + FPS rolling avg + (unsaved) badge
└── _event_bus.py                  # ~40-60 lines: EventBusProcessor + deque buffer

pastor_tracker/tests/
├── test_dashboard.py              # ~250 lines: Mock(spec=Pipeline) for command dispatch + slider buffer + quit ordering
├── test_pipeline_thread.py        # ~150 lines: PipelineThreadHost lifecycle (start->submit->join), loop_ready race
├── test_overlays.py               # ~180 lines: pure coord math (skeleton, badge, third-line, angle-text)
├── test_status_panel.py           # ~150 lines: FPS rolling avg, status string formatting, unsaved-badge
└── test_event_bus.py              # ~100 lines: structlog tap processor + WARN/ERROR filter + maxlen
```

**Line-budget rationale:** CONTEXT.md notes ~250 lines as the threshold above which a module should split. The 5-file split keeps every file well under that. `dashboard.py` is borderline — if it crosses 350 lines during execution, extract the button/slider wiring into `_widgets.py` (deferrable; flag for plan-time decision).

### Pattern 1: PipelineThreadHost — asyncio loop in background thread

**What:** Owns an asyncio event loop running in `threading.Thread`. Exposes synchronous `submit(coro) -> Future` and `join()` to the main thread.

**When to use:** Whenever DearPyGui main-thread render loop needs to dispatch into asyncio (every button, slider-bound restart, hotkey, quit).

**Example:**
```python
# Source: derived from CPython asyncio docs + Phase 6 D-01..D-04
# https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe
from __future__ import annotations
import asyncio
import threading
from concurrent.futures import Future
from collections.abc import Coroutine
from typing import Any

class PipelineThreadHost:
    """Owns an asyncio event loop running in a background thread.

    Main thread submits coroutines via `submit(coro)` -- which calls
    `asyncio.run_coroutine_threadsafe`. The host blocks `submit` callers
    until the loop is running (via `loop_ready` Event), eliminating the
    "loop not yet running" race during startup.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pipeline-loop", daemon=False)
        self._thread.start()
        # Block until the loop is actually running -- otherwise the first
        # submit() races against an uninitialized self._loop attribute.
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("Pipeline loop failed to start within 5s")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            # Drain pending tasks then close. Mirrors Phase 6 quit() drain.
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        assert self._loop is not None  # _loop_ready.wait() enforces this
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
```
[VERIFIED: in-tree Phase 6 D-01..D-04 + Python asyncio docs] [CITED: https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe]

### Pattern 2: Manual frame loop with timestamp-skip (D-08)

**What:** Replace `dpg.start_dearpygui()` with explicit `while dpg.is_dearpygui_running(): dpg.render_dearpygui_frame()` so the dashboard can skip the per-tick `cv2.cvtColor` + `set_value` upload when the frame timestamp is unchanged.

**When to use:** Always for this dashboard — D-08 lock requires it. The auto-loop hides the per-frame hook needed for skip logic.

**Example:**
```python
# Source: dearpygui readthedocs Render Loop page
# https://dearpygui.readthedocs.io/en/latest/documentation/render-loop.html
def run(self) -> None:
    dpg.create_context()
    self._build_ui()
    dpg.create_viewport(title="Pastor Tracker", width=1440, height=640)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    try:
        last_ts_ns = -1  # sentinel: never seen a frame
        frame_count = 0
        while dpg.is_dearpygui_running():
            frame = self._pipeline.latest_frame  # atomic read (D-03)
            if frame is not None and frame.timestamp_ns != last_ts_ns:
                self._upload_texture(frame)
                self._redraw_overlays(self._pipeline.snapshot())
                last_ts_ns = frame.timestamp_ns
            if frame_count % 3 == 0:  # 10 Hz status refresh (D-15)
                self._status_panel.refresh(self._pipeline.snapshot())
            dpg.render_dearpygui_frame()
            frame_count += 1
    finally:
        self._initiate_quit()  # D-04 sequence
        dpg.destroy_context()
```
[VERIFIED: dearpygui docs] [CITED: https://dearpygui.readthedocs.io/en/latest/documentation/render-loop.html]

### Pattern 3: cv2 BGR -> RGBA float32 -> flat raw_texture upload

**What:** Per-tick conversion: BGR uint8 -> RGBA uint8 -> downscale if > 960x540 -> float32/255 -> flatten 1-D.

**When to use:** Every render tick where `frame.timestamp_ns` changed (skip path otherwise).

**Example:**
```python
# Source: cv2 docs + dearpygui textures.rst
# https://dearpygui.readthedocs.io/en/latest/documentation/textures.html
import cv2
import numpy as np
from pastor_tracker.core.types import Frame

def bgr_frame_to_rgba_float_flat(frame: Frame, preview_w: int, preview_h: int) -> np.ndarray:
    """Convert one Frame to a dearpygui raw_texture-ready flat float32 buffer.

    Pure transform; no DPG state mutation. Defined in _overlays.py? No --
    this is dashboard.py's per-tick helper; _overlays.py holds DRAW-coord
    pure functions only.
    """
    rgba = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGBA)
    if frame.width > preview_w or frame.height > preview_h:
        rgba = cv2.resize(rgba, (preview_w, preview_h), interpolation=cv2.INTER_AREA)
    return (rgba.astype(np.float32) / 255.0).flatten()
```
[VERIFIED: dearpygui textures docs] [CITED: https://dearpygui.readthedocs.io/en/latest/documentation/textures.html]

### Pattern 4: Pure overlay coord helpers (`_overlays.py`)

**What:** Pure functions that take a snapshot DTO + image dims and return tuples of `(x_px, y_px)` for the dashboard to feed into `dpg.draw_line` / `dpg.draw_circle` / `dpg.draw_text`. Zero DPG calls inside.

**When to use:** Every render tick for the overlay layer; testable in isolation under pytest with no DPG context.

**Example:**
```python
# Pure-core / dirty-edges: _overlays.py has zero `import dearpygui`.
from pastor_tracker.core.types import PipelineSnapshot

def third_line_pixels(
    snap: PipelineSnapshot, image_w_px: int, image_h_px: int
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Vertical line at the framing target's pixel x. None if no target yet."""
    if snap.last_target_x_normalized is None:
        return None
    x_px = int(round(snap.last_target_x_normalized * image_w_px))
    return ((x_px, 0), (x_px, image_h_px))

def angle_text(
    snap: PipelineSnapshot,
) -> str:
    """Formatted current/target angle string. Used by drawlist as draw_text."""
    cur = "—" if snap.last_pan_angle_deg is None else f"{snap.last_pan_angle_deg:+6.2f}"
    emit = "—" if snap.last_emitted_angle_deg is None else f"{snap.last_emitted_angle_deg:+6.2f}"
    return f"pan: {cur}°  emit: {emit}°"
```
[ASSUMED] Skeleton overlay (per-keypoint pose lines) is NOT in `PipelineSnapshot` and would require either (a) extending the snapshot to include `last_detection: Detection | None` + a keypoints field on `Detection`, or (b) caching the last pose engine output via a dedicated `latest_pose` slot on Pipeline. **See §6 for the decision tree.**

### Anti-Patterns to Avoid
- **DPG calls from the background thread** — deadlock per [hoffstadt/DearPyGui#2053](https://github.com/hoffstadt/DearPyGui/issues/2053). All slider callbacks fire on the DPG main thread; only `host.submit(...)` crosses the boundary.
- **`asyncio.new_event_loop()` from the main thread** — would conflict with whatever the background thread's loop set. The main thread NEVER touches asyncio directly in this phase.
- **Locks around `pipeline.latest_frame` / `pipeline.snapshot()`** — atomic CPython attribute read is sufficient per D-03 + Phase 6 D-17. Adding a lock would re-introduce the deadlock surface.
- **Slider `callback` that calls `host.submit(...)`** — slider edits stay in `_pending_config` only (D-10); only Save Config crosses the thread.
- **`time.sleep()` in render loop** — forbidden by CLAUDE.md. DPG's render frame already paces via vsync.
- **Burning overlays into `frame.image`** — D-07 forbids; overlays live in drawlist, not in the BGR buffer.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-thread coroutine submission | Hand-rolled `queue.Queue` + dispatcher loop | `asyncio.run_coroutine_threadsafe` | Stdlib, handles cancellation + return values cleanly |
| BGR -> RGBA conversion | `numpy` channel-swap loop | `cv2.cvtColor(img, COLOR_BGR2RGBA)` | C-level SIMD; ~10x faster than numpy slicing |
| Image resize | PIL or manual stride math | `cv2.resize(img, (W, H), interpolation=INTER_AREA)` | INTER_AREA is the documented quality choice for downscale |
| 30-frame FPS rolling avg | Custom ring-buffer class | `collections.deque(maxlen=30)` | Stdlib, `append` is O(1) thread-safe |
| Bounded event-bus ring buffer | Custom list-with-pop | `collections.deque(maxlen=64)` | Same; `appendleft` + `[0]` gives newest-first |
| Threaded event loop startup race | Custom poll loop ("is loop ready yet?") | `threading.Event` + `loop_ready.wait(timeout)` | Stdlib; deterministic, no busy-wait |
| Pydantic re-validation on Save | Manual range checks | `Config.model_copy(update=_pending)` raises `ValidationError` | Pydantic v2 re-validates the merged model in `model_copy` per [docs](https://docs.pydantic.dev/latest/concepts/models/#model_copy) |
| structlog event tap | Custom logger subclass | structlog processor in chain (returns dict unchanged) | Idiomatic per [structlog Processors](https://www.structlog.org/en/stable/processors.html) |
| DPG widget tags | String tags (`"pan_slider"`) globally | `dpg.generate_uuid()` returned ints | CONTEXT.md "Claude's Discretion" recommends; collision-free; mypy-int-typed |
| JSON write of new Config | Manual `f.write(json.dumps(...))` | `json.dump(new_config.model_dump(mode="json"), f, indent=2)` | `model_dump(mode="json")` handles `Path -> str` coercion |

**Key insight:** Phase 7 is wiring around already-built primitives. Every reach for a custom implementation should trigger "is there a stdlib / pydantic / dearpygui / cv2 / structlog primitive that does this?" before writing code.

## Common Pitfalls

### Pitfall 1: structlog `EventBusProcessor` after `JSONRenderer` would receive a string

**What goes wrong:** CONTEXT.md D-16 ("Specific Ideas") states "inserted AFTER JSON renderer". If taken literally, the processor would be appended at the end of the chain — but `JSONRenderer` is the **terminal** processor and returns a `str`, not a dict. The next processor receives a string and `event_dict.get("level")` raises `AttributeError`.
**Why it happens:** The structlog processor chain transforms `event_dict` left-to-right; only the last processor (the renderer) emits a non-dict value. Documented at [structlog Processors](https://www.structlog.org/en/stable/processors.html).
**How to avoid:** Insert `EventBusProcessor` **before** `JSONRenderer` (i.e. at chain position `len(chain)-1`, which puts it second-to-last, just before `JSONRenderer`).
**Warning signs:** `test_event_bus.py` fixture that uses real `configure_logging()` + asserts on deque contents will raise `AttributeError: 'str' object has no attribute 'get'` if the order is wrong.

**Corrective `configure_logging()` shape (for the planner to land in Plan 07-04):**
```python
def configure_logging(level: str = "INFO", event_bus_buffer: deque | None = None) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if event_bus_buffer is not None:
        processors.append(EventBusProcessor(event_bus_buffer))  # BEFORE renderer
    processors.append(structlog.processors.JSONRenderer())        # terminal
    structlog.configure(processors=processors, ...)
```

### Pitfall 2: `loop_ready` race — first `host.submit()` before loop runs

**What goes wrong:** `Dashboard.__init__` creates `PipelineThreadHost` and immediately calls `host.start()`. Without a sync barrier, the main thread proceeds to `host.submit(pipeline.start())` while the background thread has not yet executed `asyncio.set_event_loop` — `self._loop` is still `None`.
**Why it happens:** `threading.Thread.start()` returns as soon as the OS schedules the thread; the body has not run yet.
**How to avoid:** `threading.Event` set inside `_run` immediately after `asyncio.set_event_loop`. Main thread blocks on `loop_ready.wait(timeout=5.0)` before returning from `host.start()`. Pattern in §Pattern 1 above.
**Warning signs:** `AssertionError: self._loop is not None` or `AttributeError: 'NoneType' object has no attribute 'call_soon_threadsafe'` on the very first button press.

### Pitfall 3: Save Config restart leaks the old asyncio loop

**What goes wrong:** D-11 says "quit Pipeline, reconstruct, start fresh Pipeline". A naive implementation calls `host.stop()` then `host.start()` again — but `host._thread` is dead, so a second `start()` on the same `PipelineThreadHost` instance creates a new thread that tries to `set_event_loop` on a loop the old thread already closed. Even worse: if the same `PipelineThreadHost` instance is reused, `asyncio.new_event_loop()` succeeds but the second `loop_ready.set()` short-circuits (Event is already set).
**Why it happens:** `threading.Thread` and `asyncio.AbstractEventLoop` are both single-use objects. `Thread.start()` raises `RuntimeError` on a second call; `loop.close()` invalidates the loop.
**How to avoid:** Save Config creates a **fresh** `PipelineThreadHost` instance. The old one is `stop()`-ed and discarded. Pattern:
```python
def _on_save_config(self) -> None:
    try:
        new_config = self._config.model_copy(update=self._pending_config)
    except ValidationError as exc:
        self._show_error_banner(str(exc))
        return  # do NOT clear _pending_config
    # 1. Persist BEFORE tearing down (so a crash during teardown still has new config on disk)
    self._write_config_json(new_config)
    # 2. Tear down old host
    self._host.submit(self._pipeline.quit()).result(timeout=5.0)
    self._host.stop()
    # 3. Construct fresh Pipeline + fresh host
    self._config = new_config
    self._pipeline = _build_pipeline(new_config)
    self._host = PipelineThreadHost()
    self._host.start()
    self._host.submit(self._pipeline.start())
    # 4. Clear buffer ONLY on full success
    self._pending_config.clear()
    self._refresh_unsaved_badge()
```
**Warning signs:** `RuntimeError: threads can only be started once` or `RuntimeError: Event loop is closed` on the second Save Config.

### Pitfall 4: DearPyGui texture format mismatch — uint8 vs float32

**What goes wrong:** `dpg.add_raw_texture(W, H, default_value, format=dpg.mvFormat_Float_rgba)` expects `float32` values in `[0.0, 1.0]`. Passing uint8 in `[0, 255]` either silently displays a fully-saturated white image (uint8 reinterpreted as huge floats) or raises a type error depending on DPG version. CONTEXT.md D-06 explicitly mandates `.astype(np.float32) / 255.0`.
**Why it happens:** DPG's `mvFormat_Float_rgba` is the documented format for raw_textures per [readthedocs/textures.html](https://dearpygui.readthedocs.io/en/latest/documentation/textures.html); uint8 raw textures use `mvFormat_Int_rgba` or `mvFormat_Byte_rgba` (renamed across versions).
**How to avoid:** Always `.astype(np.float32) / 255.0` per D-06. Document the format in the `dpg.add_raw_texture` call so a future refactor doesn't silently strip the normalization.
**Warning signs:** Preview is solid white or solid black; no error, just visually wrong.

### Pitfall 5: Slider text-entry bypasses visual clamp

**What goes wrong:** DearPyGui sliders have a "double-click to type" mode (`no_input=False` is the default). Typed values bypass `min_value`/`max_value` visual clamps and reach the callback unchecked. A user types `pan_max_velocity_deg_per_sec = 999` and on Save Config, pydantic `_PAN_VELOCITY_MAX_DEG_PER_SEC = 360.0` rejects — but only on Save, not during edit.
**Why it happens:** Documented DPG behaviour; `min_value`/`max_value` are visual-only constraints on the slider track.
**How to avoid:** Dual-bound enforcement per D-12. DPG bounds clamp the visual; Pydantic re-validates on Save. The `_pending_config` buffer holds out-of-bound values until Save fails them. The error banner explains the bound to the user. Optionally set `no_input=True` on sliders to disable text entry entirely (CLAUDE-discretion call; recommend leaving text-entry on for precise tuning, with Pydantic as the gate).
**Warning signs:** User reports "I typed 999 but the Save button does nothing visible" — error banner must surface.

### Pitfall 6: `pipeline.latest_frame.image` invalidated after `pipeline.quit()`

**What goes wrong:** Phase 6's `Pipeline.quit()` calls `camera.stop()`, which releases the OpenCV `VideoCapture` handle. The last frame's `image` ndarray is held by a separate `Frame` dataclass instance — but if any pose-engine stage held a view into the camera's internal buffer (some `cv2` backends recycle frame memory), the buffer could be mid-write when the dashboard's render tick reads it during quit.
**Why it happens:** `Frame.__post_init__` validates C-contiguous + uint8, but cannot prevent later in-place buffer reuse by the driver. In practice, `cv2.VideoCapture.read()` returns a fresh ndarray copy per [cv2 docs] so this is unlikely — but the worst case during quit is a torn read.
**How to avoid:** Render tick has a `try/except (RuntimeError, ValueError, AttributeError)` around the `bgr_frame_to_rgba_float_flat(frame)` call during quit (only — not in steady-state, per tiger-style). Cleaner: gate render-tick on `pipeline.state != "quitting"`. The D-04 quit sequence does `host.submit(pipeline.quit())` then `host.join()`, so the dashboard's render loop must already be exiting by the time `quit()` runs — the `dpg.is_dearpygui_running()` flag flips to False on viewport close, naturally draining the loop before the join.
**Warning signs:** Crash during quit; "torn frame" preview frame in last shown image; rare.

### Pitfall 7: Quit / window-close race

**What goes wrong:** User clicks the OS window's close button → `dpg.is_dearpygui_running()` returns False on next check → main loop exits → `finally:` runs `host.submit(pipeline.quit())` → but `dpg.destroy_context()` runs before the asyncio task finishes the quit.
**Why it happens:** D-04 says quit order is `pipeline.quit() -> thread.join() -> dpg.stop_dearpygui()`. The window-close path skips the explicit Q hotkey; the loop exits naturally and `finally` is the only quit hook.
**How to avoid:** In the `finally` block, do `host.submit(pipeline.quit()).result(timeout=5.0)` (blocking on the Future from the main thread) BEFORE `host.stop()` BEFORE `dpg.destroy_context()`. Note D-13 mandates `dpg.window(no_close=True)` so the *window* cannot be closed independently — but the *viewport* close-X is still a user gesture.

**Modal "unsaved changes" prompt:** When `_pending_config` is non-empty at quit time, present a modal `[Save & Quit] / [Quit Anyway] / [Cancel]`. Implementation: catch the viewport-close intent via `dpg.set_exit_callback(callback)`, show modal, only call `dpg.stop_dearpygui()` after the user picks Save or Quit Anyway. CONTEXT.md "Specific Ideas" mandates this.

**Warning signs:** "Task was destroyed but it is pending" warning at shutdown; under `filterwarnings=["error"]` (pyproject.toml line 102), this is a hard fail.

### Pitfall 8: Windows multi-monitor DPI scaling

**What goes wrong:** Operator drags the window from a 100%-scale monitor to a 150%-scale monitor; DPG widgets re-layout, slider hit-targets shift, the 960x540 preview drawlist becomes blurry or scrolls out of view.
**Why it happens:** Windows per-monitor DPI awareness; DPG 2.x supports it but layout is in absolute pixels by default.
**How to avoid:** `dpg.set_viewport_dpi_awareness(...)` — not strictly required for v1 since the on-stage rig uses a single fixed-DPI monitor (LOW risk for our deploy). Flag for Phase 8 manual smoke; do not over-engineer in v1.
**Warning signs:** Operator reports "widgets look tiny" or "preview clips off the right edge" on second monitor.

### Pitfall 9: `dpg.add_key_press_handler` repeats while key is held

**What goes wrong:** Operator holds `S` for half a second waiting for visual feedback; key-press handler fires N times → `host.submit(pipeline.start())` is submitted N times.
**Why it happens:** DPG `key_press_handler` invokes repeatedly on held keys per [issue #2547](https://github.com/hoffstadt/DearPyGui/discussions/2547).
**How to avoid:** Pipeline.start() is **idempotent on (RUNNING, "start")** per Phase 6 D-15 (`pipeline.py:364`: `if self._state is _PipelineState.RUNNING: return`). Pause is idempotent on PAUSED; e-stop is idempotent on E_STOPPED. Quit is idempotent on QUITTING. So repeat-fire is harmless **for these four**. **Home is NOT idempotent** — repeated H presses while HOMING will raise `OrchestratorRejected` (HOMING -> home is not in the lifecycle table). Wrap the home dispatch in a `try/except OrchestratorRejected: pass` (this is the documented exception per Phase 6 `pipeline.py:50-60`) OR debounce on the UI side. **Recommend the catch** — the Pipeline contract already names the typed exception for "wrong state".
**Warning signs:** Spurious `pipeline_home_rejected` WARN logs while user is just being patient.

## Runtime State Inventory

Phase 7 is a fresh module addition; no rename / refactor. Skipping per CONTEXT.md ("In scope" is new code under `pastor_tracker/ui/`).

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — no DB, no on-disk state aside from `config.json` which already exists and is the explicit target of D-11 Save | none |
| Live service config | None — Phase 7 has no external services | none |
| OS-registered state | None — no Task Scheduler / launchd entries (this is a desktop app) | none |
| Secrets/env vars | None new — Phase 7 reads existing `PTS_*` env vars via Config | none |
| Build artifacts | `dearpygui` is added to pyproject.toml — `uv lock` regenerates `uv.lock` | run `uv lock` once |

## Snapshot DTO Extensions — answer to research question 6

**Question (from research brief):** Does Phase 7 need `last_detection`/`last_tracked_subject`/`last_motor_link_state`/`camera_fps`/`last_error_event` added to `PipelineSnapshot`? Or compute UI-side from `latest_frame` + structlog tap?

**Current `PipelineSnapshot` fields (Phase 6, `core/types.py:198`):**
- `state: PipelineState`
- `last_frame_ts_ns: int | None`
- `last_intent: MotionIntent`
- `last_target_x_normalized: float | None`
- `last_pan_angle_deg: float | None`
- `last_emitted_angle_deg: float | None`
- `motor_state: str`

**Phase 7 needs (from D-15 status panel + UI-01 overlays):**

| Need | Field exists? | Decision |
|------|--------------|----------|
| Pipeline state | ✓ `state` | use directly |
| Motor link state | ✓ `motor_state` | use directly; render as "Motor link: {motor_state}" |
| Camera FPS rolling avg | ✗ | **UI-side compute** — `deque((now_ns, frame_count_at_now), maxlen=30)`; FPS = `(count_window) / ((now - oldest_now) / 1e9)`. No snapshot extension needed. |
| Last detection confidence | ✗ | **Snapshot extension** — add `last_detection_confidence: float | None`. UI-side compute is impossible because Pipeline does not expose `Detection`; only `MotionIntent` (which is derived later). |
| ID lock state (track_id) | ✗ | **Snapshot extension** — add `last_locked_track_id: int | None`. Same reason as above. |
| Last error event + timestamp | ✗ | **UI-side compute** — `EventBusProcessor` deque already captures this (D-16). Status panel reads `deque[0]` if non-empty. No snapshot extension. |
| Skeleton overlay keypoints | ✗ | **Snapshot extension OR deferred** — `Detection` doesn't currently carry keypoints (`core/types.py:119`); adding them would expand both Pipeline cache + DTO. **Recommend: deferral.** UI-01 "skeleton overlay" is achievable in v1 via bbox-only rendering (the `Detection` DTO has `bbox_x1/y1/x2/y2_normalized` fields); full keypoint skeleton can be a v2 telemetry upgrade. Document explicitly in plan. |

**Recommended Pipeline cache / snapshot extension (planner: Plan 07-01 deliverable):**

```python
# core/types.py — PipelineSnapshot extension (additive; existing 7 fields unchanged)
class PipelineSnapshot(_FrozenModel):
    state: PipelineState
    last_frame_ts_ns: int | None = Field(default=None, ge=0)
    last_intent: MotionIntent
    last_target_x_normalized: float | None = Field(default=None, ge=0.0, le=1.0)
    last_pan_angle_deg: float | None = None
    last_emitted_angle_deg: float | None = None
    motor_state: str
    # --- Phase 7 additions (D-15 status panel + UI-01 overlays) ---
    last_detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    last_locked_track_id: int | None = Field(default=None, ge=0)
    last_subject_bbox_normalized: tuple[float, float, float, float] | None = None
```

**Cache + tick-loop hook (pipeline.py extension):**

```python
@dataclass
class _PipelineCache:
    # ... existing fields ...
    last_detection_confidence: float | None = None
    last_locked_track_id: int | None = None
    last_subject_bbox_normalized: tuple[float, float, float, float] | None = None

# In _tick_loop:
if detections:
    primary = max(detections, key=lambda d: d.mean_keypoint_confidence)
    self._cache.last_detection_confidence = primary.mean_keypoint_confidence
    self._cache.last_locked_track_id = primary.track_id
    self._cache.last_subject_bbox_normalized = (
        primary.bbox_x1_normalized, primary.bbox_y1_normalized,
        primary.bbox_x2_normalized, primary.bbox_y2_normalized,
    )
```

**Risk:** Phase 6 docs are written; extending the snapshot touches a frozen contract. Per CONTEXT.md "Specific Ideas" — *"Detection / TrackedSubject — read via snapshot() proxies OR by extending PipelineSnapshot with last_detection / last_tracked_subject (open to plan; no D-XX touch)."* — this extension is explicitly authorized by CONTEXT.md and does not require revisiting Phase 6.

**Cost:** 1 frozen-DTO extension + 3 cache fields + 1 max-conf reduce per tick (O(n) on detection count, n ≤ ~5 typically). Negligible.

[VERIFIED: in-tree pipeline.py + core/types.py + CONTEXT.md "Specific Ideas"]

## Threading & Quit-Sequence Concrete Walkthrough — answer to research question 4

**Q: How does Pipeline's existing SIGINT handling interact with the background thread (Phase 6 D-04 + Phase 7 D-04)?**

Phase 6's `__main__.py:_amain` installs SIGINT via `signal.signal()` on Windows / `loop.add_signal_handler()` on POSIX (lines 122-178). The handler sets an `asyncio.Event` (`shutdown_event`) that `_amain` awaits.

**Phase 7's change to `__main__.py`:** When `--ui` (default), `_amain` is NOT called. Instead:

```python
def main() -> int:
    # ... argparse + configure_logging() unchanged ...
    if args.headless:
        return asyncio.run(_amain(config))  # Phase 6 path unchanged
    # Phase 7 path:
    dashboard = Dashboard(config, args.config_json or CONFIG_JSON_PATH)
    return dashboard.run()  # synchronous; runs on main thread

class Dashboard:
    def run(self) -> int:
        self._host = PipelineThreadHost()
        self._host.start()  # blocks on loop_ready Event
        self._host.submit(self._pipeline.start()).result(timeout=10.0)
        # ... DPG context + render loop ...
        try:
            # Manual render loop (Pattern 2)
            while dpg.is_dearpygui_running():
                self._render_tick()
                dpg.render_dearpygui_frame()
            return EXIT_OK
        finally:
            self._initiate_quit()
```

**SIGINT in --ui mode:** Phase 6's signal handler is no longer installed (we are not inside `asyncio.run(_amain(...))`). Python's default SIGINT raises `KeyboardInterrupt` on the **main thread** — which is the DPG render loop. The `while dpg.is_dearpygui_running()` loop will be interrupted; the `finally` block runs `_initiate_quit()` which does:

```python
def _initiate_quit(self) -> None:
    if self._host is not None and self._pipeline is not None:
        try:
            self._host.submit(self._pipeline.quit()).result(timeout=5.0)
        except (OrchestratorRejected, asyncio.TimeoutError, Exception) as exc:  # documented translator
            self._logger.warning("ui_quit_failed", exc_info=exc)
        self._host.stop()  # joins background thread
    dpg.stop_dearpygui()
    dpg.destroy_context()
```

This is the **D-04 sequence**. Order: `pipeline.quit()` (asyncio task on background thread, drained via `.result(timeout=5.0)` on main thread) → `host.stop()` (which calls `loop.stop()` + `thread.join()`) → `dpg.stop_dearpygui()` → `dpg.destroy_context()`.

[VERIFIED: in-tree __main__.py + Phase 6 D-04 + Phase 7 D-04]

## structlog Processor Insertion — answer to research question 5

**The CORRECTIVE finding.** CONTEXT.md D-16 / "Specific Ideas" literally state:

> `EventBusProcessor` inserted in `configure_logging()` AFTER JSON renderer — observes rendered events, pushes `(level, event, timestamp)` into deque.

**This is wrong as stated.** `structlog.processors.JSONRenderer` is the **terminal** processor: it returns a `str`, not a dict. Per [structlog Processors docs](https://www.structlog.org/en/stable/processors.html):

> The last processor plays an important role because its duty is to adapt the event_dict into something the logging methods of the wrapped logger understand, and it's the only processor that needs to know anything about the underlying system.

A processor placed AFTER `JSONRenderer` would receive a string. `event_dict.get("level")` would raise `AttributeError`.

**Correct insertion:** Before `JSONRenderer`. The processor sees the structured dict (`add_log_level` has already added `"level"`, `TimeStamper` has added `"timestamp"`), taps it, returns it unchanged. The next call is `JSONRenderer` which renders to JSON. Output is unchanged for the JSON sink; the side effect is the deque append.

**Final corrected chain (Phase 7's `configure_logging` extension):**

```python
# logging_config.py — Phase 7 extension; default behavior unchanged when event_bus_buffer=None
def configure_logging(level: str = "INFO", event_bus_buffer: deque | None = None) -> None:
    """Phase 7 extension: optionally tap WARN/ERROR into a UI deque BEFORE JSONRenderer.

    Default (event_bus_buffer=None) preserves Phase 1 behavior exactly: 6 processors
    ending in JSONRenderer. Phase 7 dashboard boot passes a deque from EventBus.
    """
    logging.basicConfig(level=level.upper(), format="%(message)s")
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if event_bus_buffer is not None:
        from pastor_tracker.ui._event_bus import EventBusProcessor
        processors.append(EventBusProcessor(event_bus_buffer))
    processors.append(structlog.processors.JSONRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

**WHY** the WARN/ERROR filter must be inside the processor (not via wrapper_class filtering):

```python
# _event_bus.py
class EventBusProcessor:
    """Tap-and-pass structlog processor. Captures WARN/ERROR events into a bounded deque."""
    _CAPTURED_LEVELS = frozenset({"warning", "error", "critical"})

    def __init__(self, buffer: deque) -> None:
        self._buf = buffer

    def __call__(self, logger, method_name, event_dict):
        # The "level" key is set by structlog.processors.add_log_level
        # earlier in the chain. method_name is also reliable.
        if event_dict.get("level") in self._CAPTURED_LEVELS:
            self._buf.appendleft((
                event_dict["level"],
                event_dict.get("event", ""),
                event_dict.get("timestamp", ""),  # ISO string from TimeStamper
            ))
        return event_dict  # MUST return; chain continues to JSONRenderer
```

**The `logging_config.py` docstring already flags non-idempotence (lines 18-22).** Phase 7 boot path must:
1. Headless mode: call `configure_logging()` as before — single call, no event bus.
2. UI mode: call `configure_logging(event_bus_buffer=ui_buffer)` — single call, with event bus.

Never reconfigure mid-run (the docstring warns it's frozen by `cache_logger_on_first_use=True`).

[VERIFIED: structlog docs] [CITED: https://www.structlog.org/en/stable/processors.html]
[CORRECTIVE: vs CONTEXT.md D-16 / "Specific Ideas" wording]

## Code Examples

### Pipeline command dispatch (UI button)

```python
# Source: derived from CONTEXT.md D-02 + asyncio docs
# https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe
def _on_start_pressed(self, sender, app_data, user_data) -> None:
    """Fired from DPG main thread; dispatches into asyncio loop fire-and-forget."""
    fut = self._host.submit(self._pipeline.start())
    # Fire-and-forget: do NOT call fut.result() -- UI thread never blocks.
    # If start() raises, the done-callback path inside Pipeline logs structurally;
    # the EventBusProcessor surfaces it to the status panel.
    fut.add_done_callback(self._log_command_completion)

def _log_command_completion(self, fut: Future) -> None:
    exc = fut.exception()  # non-blocking; fut is already done
    if exc is not None:
        self._logger.warning(
            "ui_command_failed",
            command="start",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
```

### Hotkey handler (UI-05 / D-14)

```python
# Source: dearpygui handler_registry docs
# https://dearpygui.readthedocs.io/en/latest/documentation/io-handlers-state.html
def _build_hotkeys(self) -> None:
    with dpg.handler_registry():
        dpg.add_key_press_handler(key=dpg.mvKey_S, callback=self._on_start_pressed)
        dpg.add_key_press_handler(key=dpg.mvKey_P, callback=self._on_pause_pressed)
        dpg.add_key_press_handler(key=dpg.mvKey_H, callback=self._on_home_pressed)
        dpg.add_key_press_handler(key=dpg.mvKey_E, callback=self._on_estop_pressed)
        dpg.add_key_press_handler(key=dpg.mvKey_Q, callback=self._on_quit_pressed)

def _on_pause_pressed(self, sender, app_data, user_data) -> None:
    """P toggles pause <-> resume per Specific Ideas (CONTEXT.md)."""
    snap = self._pipeline.snapshot()  # atomic read
    if snap.state == "running":
        fut = self._host.submit(self._pipeline.pause())
    elif snap.state == "paused":
        fut = self._host.submit(self._pipeline.resume())
    else:
        # OrchestratorRejected would fire if we forced it; skip cleanly.
        self._logger.debug("ui_pause_skipped", reason=f"state={snap.state}")
        return
    fut.add_done_callback(self._log_command_completion)

def _on_home_pressed(self, sender, app_data, user_data) -> None:
    """Pitfall 9: home is NOT idempotent on HOMING; catch the typed exception."""
    def _on_done(fut: Future) -> None:
        exc = fut.exception()
        if isinstance(exc, OrchestratorRejected):
            self._logger.debug("ui_home_skipped", reason=str(exc))
            return
        if exc is not None:
            self._logger.warning("ui_command_failed", command="home", exc_msg=str(exc))
    self._host.submit(self._pipeline.home()).add_done_callback(_on_done)
```

### Slider with `_pending_config` buffer

```python
# Source: CONTEXT.md "Specific Ideas" + dearpygui slider docs
def _build_sliders(self) -> None:
    dpg.add_slider_float(
        label="pan_time_constant_sec",
        default_value=self._config.pan_time_constant_sec,
        min_value=0.2, max_value=2.0, format="%.2f",
        callback=self._make_slider_cb("pan_time_constant_sec"),
    )
    # ... 3 more sliders identical shape ...

def _make_slider_cb(self, key: str):
    """Closure factory: avoids late-binding gotcha on `key` in a lambda."""
    def _cb(sender, value: float, user_data) -> None:
        self._pending_config[key] = value
        self._refresh_unsaved_badge()
    return _cb
```

### Status panel 10 Hz refresh

```python
# Source: D-15
def refresh(self, snap: PipelineSnapshot) -> None:
    """Called every 3rd render frame (~10 Hz at 30 fps)."""
    dpg.set_value(self._tag_state, f"Pipeline:    {snap.state} {self._unsaved_badge()}")
    dpg.set_value(self._tag_motor, f"Motor link:  {snap.motor_state}")
    dpg.set_value(self._tag_fps,   f"Camera FPS:  {self._compute_fps():.1f}")
    conf = "—" if snap.last_detection_confidence is None else f"{snap.last_detection_confidence:.2f}"
    dpg.set_value(self._tag_conf, f"Confidence:  {conf}")
    lock = "NONE" if snap.last_locked_track_id is None else str(snap.last_locked_track_id)
    dpg.set_value(self._tag_lock, f"ID lock:     {lock}")
    if self._event_buffer:
        level, event, ts = self._event_buffer[0]
        dpg.set_value(self._tag_err, f"Last error:  [{level}] {event} @ {ts}")
    else:
        dpg.set_value(self._tag_err, "Last error:  —")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `dpg.start_dearpygui()` auto-loop | Manual `while dpg.is_dearpygui_running(): dpg.render_dearpygui_frame()` | DPG 0.8+ documented as preferred when you need per-frame logic | Required for D-08 timestamp-skip |
| String widget tags (`"my_tag"`) | `dpg.generate_uuid()` int tags | DPG 1.x stabilization | Collision-free, mypy-typed |
| `add_static_texture` for video | `add_raw_texture` + `set_value` per tick | DPG 0.7+ | Raw is "high performance and the preferred method when updating large textures every frame" per [docs](https://dearpygui.readthedocs.io/en/latest/documentation/textures.html) |
| `time.sleep` between frames | DPG render-frame vsync pacing | always | CLAUDE.md forbids `time.sleep` in main loop |

**Deprecated/outdated:**
- DPG 1.x's `dpg.set_primary_window` is still in v2 but layout via `dpg.window(no_close=True)` + horizontal_group is the recommended pattern.
- DPG `mvFormat_Int_rgba` is the integer-uint8 raw-texture format; CONTEXT.md D-06 commits us to `mvFormat_Float_rgba` (float32) — this is the canonical pattern.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Skeleton overlay for UI-01 can be deferred to v2; v1 renders bbox-only + framing-third + angle-text | §6 Snapshot DTO Extensions | LOW — bbox is the standard "I locked someone" affordance; full keypoints are gravy. If the user wants keypoints in v1, Pipeline cache + `Detection.keypoints` field must be added. |
| A2 | `dearpygui` 2.1.1 supports Python 3.12 wheels on Windows x64 | §Standard Stack | LOW — PyPI shows wheels through 3.12; verified at lock time. |
| A3 | `pipeline.latest_frame.image` will not be mid-write at quit time because OpenCV `cap.read()` returns a fresh copy | §Pitfall 6 | MEDIUM — depends on cv2 backend; DirectShow on Windows is documented to copy. If a torn read surfaces, gate render-tick on `pipeline.state != "quitting"`. |
| A4 | Operator's deploy monitor is single-DPI; multi-monitor drag is rare | §Pitfall 8 | LOW — for on-stage rig; flag for Phase 8 QA-04 if multi-monitor surfaces. |
| A5 | `Mock(spec=Pipeline)` provides enough typed-attribute fidelity that the dashboard tests don't need a real Pipeline | §Test Strategy | LOW — `spec=...` reflects the attribute surface; the async methods become AsyncMocks automatically (mock 3.8+). |
| A6 | Save Config can synchronously `.result(timeout=5.0)` on `pipeline.quit()` from the main thread without UI hang | §Pitfall 3 | MEDIUM — 5 s is long for a frozen UI. Acceptable for a deliberate operator action; DPG will not redraw during the wait. If the wait is unacceptable, add a "saving..." modal. Recommend documenting the freeze in plan. |
| A7 | `_pending_config[key] = value` from DPG slider callback is thread-safe (same thread as render tick — DPG main thread) | §Pattern slider | HIGH-confidence — DPG callbacks fire on the main thread per docs; no cross-thread mutation. |
| A8 | DearPyGui v2.1.1 supports `dpg.set_exit_callback(...)` for the unsaved-changes modal hook | §Pitfall 7 | LOW — present since DPG 1.x; verified in API docs. If the API name has shifted, alternate: poll `dpg.is_dearpygui_running()` and check `_pending_config` before allowing the loop to exit. |

## Open Questions (RESOLVED)

1. **Should `_pipeline_thread.py` test the full asyncio-Pipeline-construction path, or use a `Mock(spec=Pipeline)`?**
   - What we know: CONTEXT.md "Specific Ideas" recommends `Mock(spec=Pipeline)` for `test_dashboard.py`.
   - What's unclear: `test_pipeline_thread.py` tests the HOST, not the Pipeline. It should use a trivial `async def noop(): pass` coroutine + a real `PipelineThreadHost`. Phase 6 integration tests already cover full Pipeline construction.
   - Recommendation: Real `PipelineThreadHost` + trivial async no-op for host lifecycle tests; `Mock(spec=Pipeline)` for dashboard wiring tests.

2. **Should `dashboard.py` expose a public `render_tick()` for testability, or keep it private?**
   - What we know: Render-loop coverage is exempt (CONTEXT.md). But the *pure* parts of the tick (texture-build helper, snapshot-to-status-string) are testable.
   - Recommendation: Extract `bgr_frame_to_rgba_float_flat(frame, w, h)` to a module-level function (testable in isolation); keep `_render_tick` private (orchestration only — uncoverable).

3. **What is the right default `preview_width_px` / `preview_height_px`?**
   - What we know: CONTEXT.md "Claude's Discretion" recommends 960x540 with bounds.
   - What's unclear: Whether to add these as `Config` fields (then validate range) or hardcode as module constants in `dashboard.py`.
   - Recommendation: Add as Config fields (consistent with CFG-01 discipline; one source of truth) with bounds `[320..3840]` x `[240..2160]`. Default 960, 540.

4. **Does the unsaved-changes modal need its own test?**
   - What we know: DPG modal dialog construction is renderless / hard to test.
   - Recommendation: Unit-test the **decision logic** (`should_prompt_save() -> bool` based on `_pending_config` emptiness), not the modal rendering. Modal behavior goes to Phase 8 QA-04 manual smoke.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All | ✓ | 3.12.x | — |
| `uv` | dependency mgmt | ✓ (Phase 1) | latest | — |
| `dearpygui` package | UI-01..05 | ✗ — **NOT in pyproject.toml** | needs 2.1.1 | none — must add |
| `opencv-python` | D-06 (cv2.cvtColor) | ✓ | >=4.10,<5.0 | — |
| `numpy` | D-06 (.astype/float32) | ✓ | >=2.4,<3.0 | — |
| `pydantic` v2 | D-11 (model_copy) | ✓ | >=2.13,<3.0 | — |
| `structlog` | D-16 (EventBus processor) | ✓ | >=24.4,<26.0 | — |
| OBS Virtual Camera | runtime (live preview) | runtime-detected (Phase 3) | — | headless mode `--headless` for CI |
| Arduino on USB | runtime (motor) | runtime-detected (Phase 2) | — | none — `--headless` does not avoid; the Pipeline still wires through ArduinoMotor |

**Missing dependencies with no fallback:**
- `dearpygui` — **must be added to `pyproject.toml`** as part of Plan 07-01. Without it, `import dearpygui.dearpygui as dpg` fails at module-import time.

**Missing dependencies with fallback:**
- None — the dashboard cannot be partially built.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` 8.4+ + `pytest-asyncio` 0.26+ |
| Config file | `pyproject.toml [tool.pytest.ini_options]` (asyncio_mode = "auto", filterwarnings = ["error", "ignore::SyntaxWarning"]) |
| Quick run command | `cd pastor_tracker && pytest tests/test_dashboard.py tests/test_overlays.py tests/test_event_bus.py tests/test_status_panel.py tests/test_pipeline_thread.py -x` |
| Full suite command | `cd pastor_tracker && pytest --cov=src/pastor_tracker/ui` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| **UI-01** | Live preview drawlist creation + texture upload helper | unit (pure helper) | `pytest tests/test_dashboard.py::test_bgr_frame_to_rgba_float_flat_shape -x` | ❌ Wave 0 |
| **UI-01** | Skeleton/bbox/third-line/angle-text overlay coord math | unit | `pytest tests/test_overlays.py -x` | ❌ Wave 0 |
| **UI-01** | Snapshot extension fields populated by tick (`last_detection_confidence`, `last_locked_track_id`, `last_subject_bbox_normalized`) | unit (extend Phase 6 test_pipeline.py) | `pytest tests/test_pipeline.py::test_cache_carries_detection_fields -x` | ❌ Wave 0 (Phase 6 test exists; extend) |
| **UI-01** | Render loop end-to-end with viewport open | **manual** | n/a — DPG no headless | n/a |
| **UI-02** | 4 sliders constructed with correct bounds + key names | unit (introspect dashboard.run() partial setup via Mock context) | `pytest tests/test_dashboard.py::test_sliders_have_correct_bounds -x` | ❌ Wave 0 |
| **UI-02** | Slider callback writes to `_pending_config[key]` | unit | `pytest tests/test_dashboard.py::test_slider_callback_updates_pending -x` | ❌ Wave 0 |
| **UI-02** | Unsaved badge toggles on/off based on `_pending_config` non-empty | unit | `pytest tests/test_status_panel.py::test_unsaved_badge -x` | ❌ Wave 0 |
| **UI-03** | All 5 button callbacks dispatch via `host.submit` to correct Pipeline method | unit (Mock(spec=Pipeline)) | `pytest tests/test_dashboard.py::test_button_dispatches_pipeline_command -x` | ❌ Wave 0 |
| **UI-03** | Save Config success: writes JSON, tears down host, builds fresh host, clears `_pending_config` | unit | `pytest tests/test_dashboard.py::test_save_config_success_flow -x` | ❌ Wave 0 |
| **UI-03** | Save Config ValidationError: surfaces banner, does NOT clear buffer, does NOT restart Pipeline | unit | `pytest tests/test_dashboard.py::test_save_config_validation_failure -x` | ❌ Wave 0 |
| **UI-04** | EventBusProcessor filters WARN/ERROR into deque; DEBUG/INFO skipped | unit | `pytest tests/test_event_bus.py::test_warn_error_captured_info_skipped -x` | ❌ Wave 0 |
| **UI-04** | EventBusProcessor returns event_dict unchanged (pass-through) | unit | `pytest tests/test_event_bus.py::test_pass_through_returns_dict -x` | ❌ Wave 0 |
| **UI-04** | EventBus deque is bounded at maxlen=64 | unit | `pytest tests/test_event_bus.py::test_deque_bounded -x` | ❌ Wave 0 |
| **UI-04** | Status panel formats fields for known + missing values | unit | `pytest tests/test_status_panel.py -x` | ❌ Wave 0 |
| **UI-04** | FPS rolling avg over 30-frame window | unit (deque-driven, no DPG) | `pytest tests/test_status_panel.py::test_fps_rolling_avg -x` | ❌ Wave 0 |
| **UI-05** | Hotkey S/P/H/E/Q dispatches correct Pipeline command | unit (handler-callback invocation through Mock(spec=Pipeline)) | `pytest tests/test_dashboard.py::test_hotkey_dispatch -x` | ❌ Wave 0 |
| **UI-05** | Hotkey P toggles pause<->resume based on snapshot state | unit | `pytest tests/test_dashboard.py::test_hotkey_p_toggles_pause_resume -x` | ❌ Wave 0 |
| **UI-05** | Hotkey H catches `OrchestratorRejected` on idempotent re-fire (Pitfall 9) | unit | `pytest tests/test_dashboard.py::test_hotkey_h_swallows_rejected_on_homing -x` | ❌ Wave 0 |
| **all** | Window-close + Q hotkey trigger D-04 quit sequence in order | unit (instrumented Mock host) | `pytest tests/test_dashboard.py::test_quit_sequence_order -x` | ❌ Wave 0 |
| **all** | `PipelineThreadHost` start/submit/stop lifecycle | unit (real host + trivial async no-op) | `pytest tests/test_pipeline_thread.py -x` | ❌ Wave 0 |
| **all** | `PipelineThreadHost.submit` blocks until `loop_ready.set()` (Pitfall 2) | unit (timing-based; race injection via slow `_run`) | `pytest tests/test_pipeline_thread.py::test_submit_waits_for_loop_ready -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_dashboard.py tests/test_overlays.py tests/test_event_bus.py tests/test_status_panel.py tests/test_pipeline_thread.py -x` (target < 10 s wall clock)
- **Per wave merge:** `pytest --cov=src/pastor_tracker/ui` (target < 30 s)
- **Phase gate:** Full suite green + manual render-loop smoke per CONTEXT.md "Specific Ideas" before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_dashboard.py` — covers UI-01..05 dispatch + slider + save-config + quit-sequence
- [ ] `tests/test_overlays.py` — covers UI-01 coord math (pure)
- [ ] `tests/test_event_bus.py` — covers UI-04 structlog tap
- [ ] `tests/test_status_panel.py` — covers UI-04 formatting + FPS rolling avg
- [ ] `tests/test_pipeline_thread.py` — covers PipelineThreadHost lifecycle
- [ ] Framework install: `uv add "dearpygui>=2.1,<3.0"` then `uv lock` (Plan 07-01 deliverable)

**Note on `tests/conftest.py`:** No new fixtures needed; `Mock(spec=Pipeline)` is constructed inline per test. If a shared `fake_pipeline()` factory emerges from Plan 07-02 wave, extract then.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Desktop app; no users / sessions |
| V3 Session Management | no | — |
| V4 Access Control | no | Operator has physical access to the rig; no remote surface |
| **V5 Input Validation** | **yes** | Pydantic `Config.model_copy(update=...)` re-validates all slider buffer values on Save (D-12). Slider bounds dual-enforced (DPG `min_value` + Pydantic). |
| **V6 Cryptography** | no | No secrets, no crypto in UI; YOLO weights SHA256 is Phase 4's concern |

### Known Threat Patterns for `dearpygui` + `cv2` + asyncio dashboard

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path-traversal via `--config-json` arg | Tampering | argparse type=Path; pydantic `JsonConfigSettingsSource` resolves but does not execute paths; ensure path is not user-controlled if Phase 8 deploy hardens config-json discovery |
| Malicious `config.json` content | Tampering | Pydantic `Config(extra="forbid", frozen=True)` rejects unknown fields + invalid ranges; CFG-03 fail-fast |
| Slider buffer holds out-of-range value until Save | Tampering | Dual-bound D-12 enforces; out-of-range surfaces via `ValidationError` banner |
| Image frame contains crafted pixels causing cv2.cvtColor crash | DoS | `Frame.__post_init__` validates dtype/shape/contiguity at the boundary (Phase 1 / 3 already); cv2 itself is hardened against malformed input |
| Long-running DPG render-thread hang triggers PC heartbeat timeout in firmware | DoS | Render loop is independent of Pipeline tick; firmware heartbeat is owned by `ArduinoMotor` (Phase 2 IO-ARD-05) on the background thread |
| User keystroke pumps `H` causing repeated home commands | DoS (self-inflicted) | Pipeline.home() raises `OrchestratorRejected` on HOMING; UI catches per Pitfall 9 |

**Verdict:** Phase 7 surface is operator-local. V5 (Input Validation) is the only relevant ASVS category and is fully covered by Pydantic re-validation + dual-bound sliders. No new crypto, no new auth surface.

## Coverage Targets

| Module | Target | Notes |
|--------|--------|-------|
| `_overlays.py` | ≥ 90% line + branch | Pure coord math; trivially coverable |
| `_event_bus.py` | ≥ 90% line + branch | WARN/ERROR filter + pass-through + maxlen behavior |
| `_status_panel.py` | ≥ 90% line | Field formatting + FPS rolling avg + unsaved badge |
| `_pipeline_thread.py` | ≥ 90% line | Lifecycle + loop_ready race + stop() drain |
| `dashboard.py` pure helpers | ≥ 90% line | Slider callback closures, button dispatch, Save Config flow (Mock(spec=Pipeline)), hotkey toggle logic |
| `dashboard.py` render-loop body | exempt | DPG render loop is not coverable without a viewport; documented manual-only |
| Pipeline cache extension (snapshot fields) | ≥ 90% line + branch (extend Phase 6) | New cache fields populated each tick; new snapshot fields surface them |

**Reporter:** existing `pytest-cov` config (pyproject.toml). Phase 7 coverage runs via `pytest --cov=src/pastor_tracker/ui --cov-report=term-missing`. Extension lines in `pipeline.py` / `core/types.py` covered via existing Phase 6 test suite (extend; do not duplicate).

## Project Constraints (from CLAUDE.md)

- **Tiger-style:** fail-fast at boundaries. Validate every input. `Pipeline.snapshot()` is trusted (it's our code); `_pending_config` slider input is trusted only inside DPG bounds — Pydantic re-validates on Save (D-12).
- **No `print()`:** all logging is `structlog`. Phase 7 introduces `ui_command_dispatched`, `ui_command_failed`, `ui_config_pending_changes`, `ui_config_save_attempted`, `config_validation_failed`, `ui_pipeline_restart_complete`, `ui_quit_initiated`, `ui_quit_failed`, `ui_home_skipped`, `ui_pause_skipped` — all per CONTEXT.md "Specific Ideas".
- **No bare `except:` / `except Exception: pass`:** every translator boundary uses `# noqa: BLE001` with a one-line WHY comment (mirrors Phase 6 `__main__.py` `# noqa: BLE001 -- typed exit-code translator`).
- **No magic numbers:** `_PREVIEW_WIDTH_DEFAULT = 960`, `_PREVIEW_HEIGHT_DEFAULT = 540`, `_STATUS_REFRESH_DIVISOR = 3` (every 3rd of 30 Hz = 10 Hz), `_FPS_WINDOW_FRAMES = 30`, `_EVENT_BUS_MAXLEN = 64`, `_HOST_JOIN_TIMEOUT_SEC = 5.0`, `_LOOP_READY_TIMEOUT_SEC = 5.0`. All module-level `Final[int|float]` in `dashboard.py` or hoisted to `Config`.
- **No globals:** all state lives on `Dashboard` instance attributes.
- **No `time.sleep()` in main loop:** DPG render-frame vsync paces.
- **PEP8 strict naming:** `Dashboard`, `PipelineThreadHost`, `EventBusProcessor` (PascalCase classes); `_pending_config`, `_on_start_pressed`, `_build_sliders`, `_render_tick`, `_initiate_quit` (snake_case methods). No abbreviations.
- **Type hints everywhere:** `mypy --strict` no `Any`. Slider callbacks typed as `def _cb(sender: int, app_data: float, user_data: object) -> None` (DPG callback signature).
- **Immutable data:** `_pending_config` is the ONLY mutable state on Dashboard; `Config` is frozen; `PipelineSnapshot` is frozen; the `deque` ring buffer is mutable but bounded.
- **Conventional Commits:** one logical change per commit (Plan 07-01 = pyproject + Config preview fields + types extension; Plan 07-02 = ui shell + overlays; Plan 07-03 = sliders + Save Config; Plan 07-04 = status panel + event bus + tests).

**`dearpygui.*` is already in the `[[tool.mypy.overrides]]` block of `pyproject.toml` (line 86) with `ignore_missing_imports = true`** — no new mypy configuration needed.

## Sources

### Primary (HIGH confidence)
- **CONTEXT.md** `.planning/phases/07-ui-dashboard/07-CONTEXT.md` — D-01..D-16 locked decisions
- **Phase 6 RESEARCH** `.planning/phases/06-pipeline-orchestrator/06-RESEARCH.md` — async patterns, Windows SIGINT
- **Phase 6 source** `pastor_tracker/src/pastor_tracker/pipeline.py` — Pipeline public surface (lines 161-557)
- **Phase 6 source** `pastor_tracker/src/pastor_tracker/__main__.py` — Phase 6 entry, signal handling pattern
- **Core types** `pastor_tracker/src/pastor_tracker/core/types.py` — PipelineSnapshot field set (lines 198-227)
- **Config** `pastor_tracker/src/pastor_tracker/config.py` — 4 slider field bounds (lines 123, 173-181)
- **structlog Processors** https://www.structlog.org/en/stable/processors.html — chain-order semantics (corrective finding §5)
- **DearPyGui Textures** https://dearpygui.readthedocs.io/en/latest/documentation/textures.html — raw_texture lifecycle
- **DearPyGui Render Loop** https://dearpygui.readthedocs.io/en/latest/documentation/render-loop.html — manual loop pattern
- **DearPyGui IO/Handlers** https://dearpygui.readthedocs.io/en/latest/documentation/io-handlers-state.html — handler_registry global hotkey
- **asyncio docs** https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe — cross-thread submission
- **Pydantic v2 model_copy** https://docs.pydantic.dev/latest/concepts/models/#model_copy — re-validation on update

### Secondary (MEDIUM confidence)
- **PyPI dearpygui** https://pypi.org/project/dearpygui/ — version 2.1.1 latest (verified 2026-05-08)
- **DearPyGui GitHub Releases** https://github.com/hoffstadt/DearPyGui/releases — release cadence

### Tertiary (LOW confidence)
- DearPyGui issue [#2053](https://github.com/hoffstadt/DearPyGui/issues/2053) — main-thread deadlock report (single anecdote; documented behavior)
- DearPyGui discussion [#2547](https://github.com/hoffstadt/DearPyGui/discussions/2547) — key_press_handler held-key repeat (mitigation: Pipeline idempotence + OrchestratorRejected catch)
- DearPyGui issue [#2619](https://github.com/hoffstadt/DearPyGui/issues/2619) — `is_dearpygui_running()` post-close behavior (not load-bearing for our quit-sequence; finally-block runs deterministically)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every library is already in repo or is a single PyPI add; dearpygui 2.1.1 verified
- Architecture: HIGH — pattern is one of two documented options (main-thread DPG vs. main-thread asyncio); CONTEXT.md D-01 locks the former; all Phase 6 invariants stand unchanged
- Pitfalls: HIGH on Pitfalls 1-5, 7, 9 (in-tree code or documented behavior); MEDIUM on Pitfall 6 (depends on cv2 backend timing); LOW on Pitfall 8 (multi-DPI is deploy-specific)
- Snapshot DTO extension: HIGH — CONTEXT.md "Specific Ideas" explicitly authorizes
- structlog corrective (§5): HIGH — structlog docs are unambiguous; CONTEXT.md wording is in error
- Test strategy: HIGH on automated unit tests; documented exemption for render loop

**Research date:** 2026-05-08
**Valid until:** 2026-06-08 (30 days; stable phase — DPG release cadence is slow, structlog API is mature)
