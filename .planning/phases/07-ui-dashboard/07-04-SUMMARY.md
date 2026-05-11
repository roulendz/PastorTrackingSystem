---
phase: 07-ui-dashboard
plan: 04
subsystem: ui

tags: [structlog, event-bus, status-panel, logging-extension, main-entry, headless-flag, dearpygui]

# Dependency graph
requires:
  - phase: 07-ui-dashboard
    provides: PipelineSnapshot extension (last_detection_confidence, last_locked_track_id, last_subject_bbox_normalized) from Plan 07-01
  - phase: 07-ui-dashboard
    provides: Dashboard shell + _build_status_slots placeholders + _STATUS_REFRESH_DIVISOR + _unsaved_badge_text stub from Plan 07-02
  - phase: 01-scaffold-and-tooling
    provides: configure_logging Phase 1 contract (6-processor JSON chain)
  - phase: 06-pipeline-orchestrator
    provides: 8-stage Pipeline construction (lifted into _default_pipeline_factory)
provides:
  - pastor_tracker.ui._event_bus.{EventBusProcessor, EventBuffer, EventTriple, make_event_bus, EVENT_BUS_MAXLEN}
  - pastor_tracker.ui._status_panel.{StatusPanel, compute_fps, _format_state_line, _format_motor, _format_fps, _format_confidence, _format_id_lock, _format_last_error}
  - pastor_tracker.logging_config.configure_logging extended with optional event_bus_buffer kwarg (insertion BEFORE JSONRenderer per RESEARCH §5)
  - pastor_tracker.ui.dashboard.Dashboard.event_bus parameter + _default_pipeline_factory real 8-stage construction
  - pastor_tracker.__main__.main(argv) with mutually-exclusive --ui (default) / --headless flag
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Tap-and-pass structlog processor: EventBusProcessor returns event_dict identity-unchanged so the chain continues to the terminal JSONRenderer; deque.appendleft places newest at index 0 for trivial 'most recent error' lookups."
    - "Insertion point: BEFORE JSONRenderer (chain index len-2). Asserted in test_configure_logging_buffer_chain_position via isinstance(processors[-1], JSONRenderer) + isinstance(processors[-2], EventBusProcessor). RESEARCH §5 documents why the CONTEXT.md D-16 wording ('AFTER JSON renderer') is wrong: JSONRenderer is terminal and returns str -- a processor placed after it would crash on event_dict.get('level')."
    - "Lazy UI import in configure_logging: when event_bus_buffer is None (Phase 1 / --headless default), no pastor_tracker.ui.* import is pulled. The DearPyGui-dependent path stays off the --headless boot."
    - "Pure formatters at module level: 6 _format_* helpers + compute_fps are NOT methods on StatusPanel -- they take primitives, return strings, and have no DPG dependency, so the test surface is trivial (19/19 status-panel tests pass without ever touching dpg.create_context)."
    - "FPS rolling avg: collections.deque(maxlen=30) of (timestamp_ns, frame_count). compute_fps reads window[0] and window[-1] (oldest/newest) for an N-sample sliding window. Defensive: returns 0.0 on <2 samples or dt<=0 (avoids ZeroDivisionError on first-tick race)."
    - "Shared event-bus deque: __main__ constructs one make_event_bus() per --ui boot and threads it through configure_logging(event_bus_buffer=...) AND Dashboard(event_bus=...). The structlog tap and the StatusPanel read the SAME deque instance -- test_main_ui_passes_event_bus_to_dashboard asserts identity (`is`)."
    - "mypy --strict + structlog signature: EventBusProcessor.__call__ takes (logger: Any, method_name: str, event_dict: MutableMapping[str, Any]); the file-level `# mypy: disable-error-code='explicit-any'` directive bounds the Any escape to one auditable file (vs. weakening the project-wide rule)."
    - "Mutually-exclusive flag group in argparse: --ui / --headless via add_mutually_exclusive_group; both unset -> UI default per CONTEXT.md 'Specific Ideas' line 191; both set -> SystemExit code=2 from argparse usage-error path (test_main_ui_and_headless_mutually_exclusive)."
    - "main(argv=None) -> int signature: defaults to None so argparse falls back to sys.argv (the bottom-of-module SystemExit(main()) call still works for `python -m pastor_tracker`); tests pass explicit argv lists to drive the flag matrix without subprocess overhead."

key-files:
  created:
    - pastor_tracker/src/pastor_tracker/ui/_event_bus.py
    - pastor_tracker/src/pastor_tracker/ui/_status_panel.py
    - pastor_tracker/tests/test_event_bus.py
    - pastor_tracker/tests/test_status_panel.py
    - pastor_tracker/tests/test_main.py
  modified:
    - pastor_tracker/src/pastor_tracker/logging_config.py
    - pastor_tracker/src/pastor_tracker/__main__.py
    - pastor_tracker/src/pastor_tracker/ui/dashboard.py
    - pastor_tracker/tests/test_logging.py
    - pastor_tracker/tests/test_dashboard.py

key-decisions:
  - "EventBusProcessor inserted BEFORE JSONRenderer (RESEARCH §5 corrective finding) — the CONTEXT.md D-16 wording 'AFTER JSON renderer' is unimplementable because JSONRenderer is terminal. The corrective ordering is contractually captured by test_configure_logging_buffer_chain_position."
  - "_unsaved_badge_text kept as a stub returning '' per the Option A fix from plan-checker Blocker #1: Plan 07-03 REPLACES this stub with the real ``\"(unsaved changes)\" if self._pending_config else \"\"`` impl. Without the stub, _render_tick would AttributeError on every _STATUS_REFRESH_DIVISOR tick at the end of 07-04 — defeating the test suite."
  - "Dashboard constructs its own event bus when none is injected (test ergonomics): unit tests that pass mock_pipeline / mock_host but don't care about the event bus get a private deque per Dashboard instance. __main__ injects a SHARED deque so the structlog tap and StatusPanel see the same events."
  - "_default_pipeline_factory replaced with real 8-stage construction (mirrors __main__._amain); hardware-stack modules (pyserial, opencv, ultralytics, pygrabber, filterpy) are LAZY-imported inside the function so the --headless boot does not pay their import cost. ArduinoPortNotFoundError surfaces to the __main__ exit-translator ladder unchanged."
  - "argparse default=False on both --ui and --headless lets `args.headless` be the single dispatch discriminant (`headless_mode = bool(args.headless)`); --ui flag is informational only (and required for the mutually-exclusive group to reject --ui --headless), with the actual 'UI is default' policy living in the `if headless_mode: ... else: ...` ladder."
  - "Pre-existing test_default_pipeline_factory_raises_not_implemented (from Plan 07-02) was updated to tolerate either ArduinoPortNotFoundError (CI/no-Uno) OR a real Pipeline (developer bench) — the only assertion the test still makes is that the Plan 07-02 NotImplementedError stub is gone."

patterns-established:
  - "Per-file mypy escape hatch: `# mypy: disable-error-code='explicit-any'` directive at the top of _event_bus.py to allow structlog's Any-typed processor signature without weakening pyproject.toml's project-wide disallow_any_explicit setting."
  - "Coroutine close on stub asyncio.run: tests that stub asyncio.run must call coro.close() on the AsyncMock-produced coroutine to suppress 'coroutine was never awaited' RuntimeWarning under filterwarnings=error."
  - "sys.modules injection for lazy imports: monkeypatch.setitem(sys.modules, 'pastor_tracker.ui.dashboard', fake_module) intercepts the lazy `from pastor_tracker.ui.dashboard import Dashboard` inside the --ui branch -- avoids importing real DearPyGui in unit tests."
  - "DPG patch on BOTH modules in render-tick tests: the Plan 07-02 test_render_tick_skips_when_timestamp_unchanged now patches `pastor_tracker.ui.dashboard.dpg` AND `pastor_tracker.ui._status_panel.dpg` so neither path reaches real DPG; the texture-upload count is read off the dashboard mock alone."

requirements-completed: [UI-01, UI-04]

# Metrics
duration: ~25min
completed: 2026-05-11
---

# Phase 7 Plan 04: Status Panel + Event Bus + Headless Flag Summary

Phase 7 status surface is feature-complete: the structlog `EventBusProcessor`
(inserted BEFORE `JSONRenderer` per RESEARCH §5 correction), the `StatusPanel`
with 6-field 10 Hz refresh and 30-sample FPS rolling avg, the
`configure_logging` extension accepting an optional event-bus buffer, and the
`__main__.py` `--ui` (default) / `--headless` mutually-exclusive flag pair
that lazy-imports the Dashboard and threads the same event bus into both
the structlog chain and the status panel.

## Corrective EventBusProcessor Insertion Point

CONTEXT.md D-16 wording specified the processor "AFTER JSON renderer". This
ordering is unimplementable: `JSONRenderer` is the **terminal** processor
in a structlog chain — it returns a `str`, not a `dict`. A processor placed
after it would crash on `event_dict.get("level")` with
`AttributeError: 'str' object has no attribute 'get'`.

RESEARCH §5 documents the corrected position (second-to-last, just before
`JSONRenderer`) and Plan 07-04 implements it. The contract is enforced by
`test_configure_logging_buffer_chain_position`, which introspects
`structlog.get_config()["processors"]` and asserts:
- `processors[-1]` is `JSONRenderer` (Phase 1 invariant)
- `processors[-2]` is `EventBusProcessor` (RESEARCH §5 correction)

Phase 1 callers that pass no `event_bus_buffer` see the original 6-processor
chain — `configure_logging()` without args is byte-identical to the Phase 1
contract.

## Status Panel: 6 Fields, 10 Hz, FPS Rolling Avg

D-15 field set (rendered by `StatusPanel.refresh(snap, unsaved_badge)`):

| Field        | Source                                       | None-sentinel |
| ------------ | -------------------------------------------- | ------------- |
| Pipeline     | `snap.state` + unsaved-badge text            | n/a           |
| Motor link   | `snap.motor_state`                           | n/a           |
| Camera FPS   | `compute_fps(self._fps_window, now_ns)`      | `0.0`         |
| Confidence   | `snap.last_detection_confidence`             | `—`           |
| ID lock      | `snap.last_locked_track_id`                  | `NONE`        |
| Last error   | `event_bus[0]` (newest WARN/ERROR/CRITICAL)  | `—`           |

The render tick (`Dashboard._render_tick`) calls:
- `self._status_panel.record_frame(frame.timestamp_ns)` on EVERY rendered
  frame — keeps the FPS measurement honest (samples actual render rate,
  not refresh rate);
- `self._status_panel.refresh(snap, unsaved_badge)` every
  `_STATUS_REFRESH_DIVISOR == 3` frames, giving ~10 Hz at the nominal 30
  Hz render rate.

FPS rolling avg: `collections.deque(maxlen=30)` of `(timestamp_ns,
frame_count)`. `compute_fps` reads `window[0]` (oldest) and `window[-1]`
(newest), elapsed seconds = `(newest_ns - oldest_ns) / 1e9`, FPS =
`(newest_count - oldest_count) / elapsed_sec`. Defensive returns of `0.0`
on `<2` samples or `dt<=0` avoid a `ZeroDivisionError` on first-tick race.

## `--ui` / `--headless` Contract

Argparse mutually-exclusive group; defaults route to UI mode (CONTEXT.md
"Specific Ideas" line 191). The dispatch ladder in `main`:

| Branch                            | Logging                                           | Lifecycle entry                                   |
| --------------------------------- | ------------------------------------------------- | ------------------------------------------------- |
| `--headless`                      | `configure_logging()` (no buffer)                 | `asyncio.run(_amain(config))` — Phase 6 path      |
| no flag / `--ui`                  | `configure_logging(event_bus_buffer=event_bus)`   | `Dashboard(config, event_bus=event_bus).run()`    |
| `--ui --headless`                 | n/a                                               | argparse `SystemExit(code=2)`                     |

The UI branch lazy-imports `pastor_tracker.ui.dashboard.Dashboard`, so the
`--headless` path never pays DearPyGui's import cost. The same
`make_event_bus()` deque flows into `configure_logging` (the structlog tap
appendleft-pushes into it) AND into `Dashboard.__init__` (the status panel
reads index 0 for the "Last error" field) — identity asserted by
`test_main_ui_passes_event_bus_to_dashboard`.

## Manual Smoke Checklist (deferred to Phase 8 QA-04)

Plan 07-04 lands feature-complete code; the visual / DearPyGui-rendered
smoke pass needs a real OBS VCam + Arduino and lives in Phase 8 QA-04. The
DearPyGui v2.x library ships no headless runner, so the in-process tests
cover every NON-rendering path (callback wiring, dispatch, formatter
output, chain composition); the rendered output is exempt per CONTEXT.md
"Claude's Discretion".

Phase 8 QA-04 checklist:
- [ ] `python -m pastor_tracker` opens window with preview drawlist + 4
  sliders + 5 buttons + 6 status fields.
- [ ] FPS field reads ~30 once OBS VCam is feeding frames.
- [ ] Slider drag updates `(unsaved changes)` badge once Plan 07-03 lands.
- [ ] Save Config writes new `config.json` on disk and pipeline visibly
  restarts (state badge cycles `stopped` → `running`) once Plan 07-03
  lands.
- [ ] S / P / H / E / Q hotkeys dispatch from any focus.
- [ ] Hold-H during HOMING does NOT spam logs with
  `pipeline_home_rejected` (Pitfall 9 catch).
- [ ] Last-error field updates on any WARN/ERROR (force one via
  `--config-json /nonexistent.json` then start).
- [ ] Window close with pending changes shows modal (Plan 07-03);
  Save & Quit / Quit Anyway / Cancel all work.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Dashboard render-tick test broke when status panel was wired**

- **Found during:** Task 2 end-of-task pytest run.
- **Issue:** `test_render_tick_skips_when_timestamp_unchanged` (from
  Plan 07-02) patched only `pastor_tracker.ui.dashboard.dpg`; the new
  Plan 07-04 wiring also reaches `pastor_tracker.ui._status_panel.dpg`
  via `self._status_panel.refresh(...)`. The unpatched call crashed
  with a Windows access violation (real DearPyGui called without an
  active DPG context).
- **Fix:** Updated the test to patch BOTH module symbols
  (`with patch("pastor_tracker.ui.dashboard.dpg") as mock_dpg, patch("pastor_tracker.ui._status_panel.dpg")`).
  The texture-upload count assertion still reads off the dashboard
  mock alone; the status-panel mock's calls are ignored.
- **Files modified:** `pastor_tracker/tests/test_dashboard.py`
- **Commit:** `8849786`

**2. [Rule 1 - Bug] _default_pipeline_factory test asserted the Plan 07-02 stub**

- **Found during:** Task 2 (factory replacement).
- **Issue:** Plan 07-02 shipped a `NotImplementedError` stub for
  `_default_pipeline_factory` and a test
  (`test_default_pipeline_factory_raises_not_implemented`) that
  asserted that exception. Plan 07-04 replaces the stub with the
  real 8-stage construction, which now either raises
  `ArduinoPortNotFoundError` (no Uno on CI) or returns a real
  Pipeline (developer bench with hardware) — never
  `NotImplementedError`.
- **Fix:** Renamed the test to
  `test_default_pipeline_factory_constructs_pipeline_or_raises_hardware`
  and tolerate either hardware-not-found OR a Pipeline; the only
  assertion that remains is "the Plan 07-02 `NotImplementedError`
  stub is gone".
- **Files modified:** `pastor_tracker/tests/test_dashboard.py`
- **Commit:** `8849786`

**3. [Rule 2 - Critical] ValidationError translator must still emit JSON log**

- **Found during:** Task 2 dispatch-ladder refactor.
- **Issue:** Moved `configure_logging()` from "always before
  `Config()`" to "after mode dispatch" so the `--ui` branch can
  inject the event bus. But the existing `ValidationError` translator
  expects structlog to be configured (else `log.error(...)` is a
  no-op) — the `test_main_invalid_config_exit_code` subprocess test
  asserts `"config_invalid" in stdout`.
- **Fix:** Inside the `except ValidationError` block, call
  `configure_logging()` (no buffer, cheap) BEFORE the
  `log.error(...)` call. Preserves the Phase 6 subprocess
  contract.
- **Files modified:** `pastor_tracker/src/pastor_tracker/__main__.py`
- **Commit:** `8849786`

### Authentication Gates

None.

### Architectural Decisions Carried Forward

None — every Task 1/2 change followed the plan as-written modulo the
above bug fixes.

## Test Results

| File                              | Tests | Status |
| --------------------------------- | ----- | ------ |
| `tests/test_event_bus.py`         | 14    | pass   |
| `tests/test_logging.py`           | 4     | pass (1 baseline + 3 new) |
| `tests/test_status_panel.py`      | 19    | pass   |
| `tests/test_main.py`              | 5     | pass   |
| `tests/test_dashboard.py`         | 27    | pass (existing) |
| **Full suite**                    | 522 + 1 skip | pass (1 known baseline flake: `test_arduino_motor_replay::test_golden_trace_replay` heartbeat race, unrelated) |

Linting + typing: `ruff check src/ tests/...` clean; `mypy src/` clean
(32 source files).

## Self-Check: PASSED

- `pastor_tracker/src/pastor_tracker/ui/_event_bus.py`: FOUND
- `pastor_tracker/src/pastor_tracker/ui/_status_panel.py`: FOUND
- `pastor_tracker/tests/test_event_bus.py`: FOUND
- `pastor_tracker/tests/test_status_panel.py`: FOUND
- `pastor_tracker/tests/test_main.py`: FOUND
- Commit `e6cb57a` (Task 1): FOUND
- Commit `8849786` (Task 2): FOUND
- Import smoke: PASSED (`Dashboard`, `StatusPanel`, `EventBusProcessor` all import).
- EventBusProcessor placement: VERIFIED — `test_configure_logging_buffer_chain_position` asserts `processors[-2]` is `EventBusProcessor` and `processors[-1]` is `JSONRenderer`.
