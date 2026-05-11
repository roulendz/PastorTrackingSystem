---
phase: 07-ui-dashboard
plan: 02
subsystem: ui

tags: [dearpygui, asyncio-thread, render-loop, overlays, hotkeys, render-skip, quit-sequence]

# Dependency graph
requires:
  - phase: 07-ui-dashboard
    provides: dearpygui dependency + PipelineSnapshot extension + Config.preview_width_px/height_px (Plan 07-01)
  - phase: 06-pipeline-orchestrator
    provides: Pipeline public surface (start/pause/resume/home/e_stop/quit/snapshot/latest_frame) + OrchestratorRejected typed exception
provides:
  - pastor_tracker.ui._overlays.{third_line_pixels, bbox_rect_pixels, angle_text, id_lock_text, bgr_frame_to_rgba_float_flat}
  - pastor_tracker.ui._pipeline_thread.PipelineThreadHost (start / submit / stop; loop_ready barrier; double-start raise; explicit RuntimeError on submit-before-start)
  - pastor_tracker.ui.dashboard.Dashboard (main-thread render loop, 5 buttons, 5 hotkeys, D-04 quit sequence, D-08 render-skip)
affects: [07-03, 07-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-core boundary: _overlays.py has ZERO `import dearpygui` (grep gate enforced in test_overlays_module_has_no_dearpygui_dependency)"
    - "loop_ready barrier in PipelineThreadHost.start(): main thread blocks on threading.Event until the background loop has set_event_loop()'d, closing RESEARCH Pitfall 2 race"
    - "Explicit RuntimeError on submit-before-start (no bare assert) so the contract holds under `python -O` per CLAUDE.md tiger-style §1"
    - "OrchestratorRejected swallow in _handle_home_done (Pitfall 9): home is the only NON-idempotent lifecycle method; held-key repeat HOMING -> home would raise — we catch and log ui_home_skipped DEBUG, never ui_command_failed WARN"
    - "D-08 render-skip: Dashboard._last_rendered_ts_ns sentinel = -1; per-tick compares frame.timestamp_ns equality, skips both texture upload and overlay redraw on match (dpg.render_dearpygui_frame still pumps for vsync)"
    - "AsyncMock coroutine close-on-submit pattern in tests: Mock(spec=Pipeline) auto-creates AsyncMock for async lifecycle methods; calling these in tests returns an un-awaited coroutine that the pytest unraisable-exception hook trips on at GC; test's host.submit side_effect closes the coro before returning a pre-resolved Future"

key-files:
  created:
    - pastor_tracker/src/pastor_tracker/ui/_overlays.py
    - pastor_tracker/src/pastor_tracker/ui/_pipeline_thread.py
    - pastor_tracker/src/pastor_tracker/ui/dashboard.py
    - pastor_tracker/tests/test_overlays.py
    - pastor_tracker/tests/test_pipeline_thread.py
    - pastor_tracker/tests/test_dashboard.py
  modified:
    - pastor_tracker/src/pastor_tracker/ui/__init__.py

key-decisions:
  - "Dashboard accepts pipeline_factory + host_factory DI seams (default to a NotImplementedError stub + the real PipelineThreadHost class); Plan 07-04 wires the real factory from __main__"
  - "Save Config button + S/P/H/E/Q hotkey reuse the SAME callback methods (a hotkey is just a different fire-site for the same _on_*_pressed); per-key path divergence lives only inside _on_pause_pressed (toggle pause<->resume on snapshot.state)"
  - "EXIT_OK is mirrored as a local Final[int]=0 in dashboard.py (rather than importing from __main__) to avoid the Plan 07-04 circular import; the contract value is identical to __main__.EXIT_OK"
  - "RUF046 (int(round(...)) redundant) -- round(float) returns int per CPython since 3.0, so the inner int() cast was Ruff-flagged redundant; removed in _overlays.py"
  - "mypy Future[None] (not Future[object]) on done-callbacks -- Pipeline.{start,pause,...} all return Coroutine[..., None], so asyncio.run_coroutine_threadsafe produces Future[None]; this was the only mypy fix needed in dashboard.py"
  - "Render-loop / DPG-context lines (run / _build_ui / _build_*_stubs / _redraw_overlays partial) are exempt from coverage per CONTEXT.md 'Claude's Discretion: render-loop coverage is exempt -- DearPyGui has no headless runner in v2.x'; the testable callback / quit / render-skip / done-callback paths reach 100% coverage on every measurable branch"

patterns-established:
  - "DI-seam factory pattern for Dashboard tests: pipeline_factory=lambda c: mock_pipeline, host_factory=lambda: mock_host -- avoids touching dpg.create_context in test path"
  - "AsyncMock-aware mock host: side_effect closes any AsyncMock-produced coroutine to keep pytest's unraisable-exception hook silent"
  - "Mock parent + attach_mock(...) for call-order assertion: parent.attach_mock(quit_future, 'quit_future') / parent.attach_mock(mock_dpg, 'dpg') so mock_calls reports a single linear ordered list across the host + dpg + future surfaces"
  - "Render-skip test pattern: two _render_tick() calls with the same Frame.timestamp_ns assert dpg.set_value.call_count stays at 1; a third call with a bumped timestamp asserts it becomes 2 -- proves D-08 timestamp-equality skip both fires and lifts cleanly"

requirements-completed: [UI-05]

# Metrics
duration: 11min
completed: 2026-05-11
---

# Phase 7 Plan 02: UI Shell Summary

**3 new modules + 3 new test files; Dashboard shell now boots a DearPyGui main window with 960x540 preview drawlist, 5 buttons (Start/Pause/Home/E-Stop with red theme/Save Config), 5 hotkeys (S/P/H/E/Q), D-08 render-skip, and the D-04 quit sequence -- all wired through PipelineThreadHost.submit; OrchestratorRejected silently swallowed on H during HOMING per Pitfall 9.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-05-11T10:47:48Z
- **Completed:** 2026-05-11T10:58:52Z
- **Tasks:** 2 (both TDD)
- **Files created:** 6
- **Files modified:** 1

## Accomplishments

### Task 1 — Pure overlays + PipelineThreadHost (commit `240b664`)

- `ui/_overlays.py` (90 LOC + docstring): 5 pure functions wholly typed against `PipelineSnapshot` and `Frame`:
  - `third_line_pixels(snap, w, h) -> tuple[pmin, pmax] | None`
  - `bbox_rect_pixels(snap, w, h) -> tuple[pmin, pmax] | None`
  - `angle_text(snap) -> str` (em-dash U+2014 sentinel for None)
  - `id_lock_text(snap) -> str` ("ID: NONE" sentinel)
  - `bgr_frame_to_rgba_float_flat(frame, w, h) -> NDArray[float32]` (cv2.cvtColor + optional cv2.resize INTER_AREA + /255.0 normalize + flatten)
- ZERO `import dearpygui` / `from dearpygui` in `_overlays.py` — enforced by a runtime grep-gate test (`test_overlays_module_has_no_dearpygui_dependency`).
- `ui/_pipeline_thread.py` (~100 LOC): `PipelineThreadHost` with:
  - `start()` blocks on `threading.Event` until the background loop calls `asyncio.set_event_loop()` (closes RESEARCH Pitfall 2 race).
  - `start()` raises `RuntimeError("may not be called twice")` on a second call (single-use; Plan 07-03 Save Config will discard and reconstruct).
  - `submit()` raises `RuntimeError("called before start()")` when `_loop is None` — explicit raise per CLAUDE.md tiger-style §1 (the bare `assert` in RESEARCH §Pattern 1 would be stripped under `python -O`).
  - `submit(coro)` typed as `Coroutine[object, object, _T] -> Future[_T]` — generic TypeVar passthrough; no explicit `Any` (passes `disallow_any_explicit`).
  - `stop()` is idempotent (no-ops when `_loop is None`), drains via `loop.call_soon_threadsafe(loop.stop)` + `thread.join(timeout=5.0)`, logs `pipeline_thread_join_timeout` WARN if the join exceeds the budget.
- 20 tests: 14 overlay + 6 thread-host. Coverage 100% on `_overlays.py`, 94% on `_pipeline_thread.py` (the 3 uncovered lines are the WARN-log path on a thread that fails to join — exercised only on simulated stuck loops, which would race in CI).

### Task 2 — Dashboard shell (commit `d3ccd23`)

- `ui/dashboard.py` (~510 LOC including docstrings): `Dashboard` class owning the main-thread render loop. Composition matches CONTEXT.md D-01..D-08, D-13, D-14:
  - **`run()`**: constructs Pipeline + PipelineThreadHost via DI factories, starts host (blocks on loop_ready), submits `pipeline.start()` with `.result(timeout=10.0)`, then enters the manual `while dpg.is_dearpygui_running()` render loop per RESEARCH §Pattern 2.
  - **`_build_ui()`**: single `dpg.window(no_close=True, no_collapse=True)` (D-13) with horizontal split: left = 960x540 `add_drawlist` over a `add_raw_texture(..., format=dpg.mvFormat_Float_rgba)` (Pitfall 4 float32 contract), right = 4 slider STUBS + 5 buttons + 6 reserved status `add_text` slots.
  - **E-Stop red theme**: one-shot `dpg.theme()` + `dpg.theme_component(dpg.mvButton)` + `dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 0, 0))`, bound via `dpg.bind_item_theme` to the E-Stop button only (D-13).
  - **5 buttons**: `_on_start_pressed`, `_on_pause_pressed`, `_on_home_pressed`, `_on_estop_pressed`, `_on_save_config_pressed`.
  - **5 hotkeys**: `dpg.add_key_press_handler` for `mvKey_S/P/H/E/Q` — each maps to the SAME callback the button uses (hotkey + button are interchangeable fire-sites).
  - **`_on_pause_pressed` toggle**: reads `pipeline.snapshot().state`; running -> `pipeline.pause()`, paused -> `pipeline.resume()`, otherwise logs `ui_pause_skipped` DEBUG (the OrchestratorRejected path is therefore unreachable from this site).
  - **`_handle_home_done`**: inspects `fut.exception()`; `OrchestratorRejected` -> `ui_home_skipped` DEBUG (Pitfall 9), other exceptions -> `ui_command_failed` WARN.
  - **`_render_tick`**: D-03 atomic CPython read of `pipeline.latest_frame`; D-08 timestamp-equality check against `_last_rendered_ts_ns` sentinel `-1`; on match, the texture upload + overlay redraw are SKIPPED but `dpg.render_dearpygui_frame()` still pumps for vsync.
  - **`_initiate_quit`**: idempotent (`_quit_initiated: bool` guard); runs the exact D-04 order — `host.submit(pipeline.quit()).result(timeout=5.0)` -> `host.stop()` -> `dpg.stop_dearpygui()`. `dpg.destroy_context()` is called by `run()`'s `finally:` AFTER this returns. `(OrchestratorRejected, TimeoutError)` and bare `Exception` (with documented `# noqa: BLE001`) both translate to `ui_quit_failed` WARN.
  - **Save Config stub**: `_on_save_config_pressed` logs `ui_save_config_not_implemented` WARN — Plan 07-03 lands the real `model_copy(update=...) -> write JSON -> host.submit(quit) -> rebuild host -> host.submit(start)` restart sequence.
  - **`_unsaved_badge_text()`** forward-compat hook returns `""` for Plan 07-02; Plan 07-03 swaps in `"(unsaved changes)" if self._pending_config else ""`.
- `ui/__init__.py` re-exports `Dashboard`.
- 27 tests; UI package coverage 79% overall (uncovered: render-loop body + DPG context-building lines, exempt per CONTEXT.md).

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Pure overlays + PipelineThreadHost | `240b664` | `ui/_overlays.py`, `ui/_pipeline_thread.py`, `tests/test_overlays.py`, `tests/test_pipeline_thread.py` |
| 2 | Dashboard shell (render loop / buttons / hotkeys / quit) | `d3ccd23` | `ui/dashboard.py`, `ui/__init__.py`, `tests/test_dashboard.py` |

Both tasks `tdd="true"`. Per-task RED -> GREEN cycle followed; tests + impl committed together as a single `feat(...)` commit per task, matching the Phase 5 / Plan 07-01 precedent for in-task TDD.

## Decisions Made

- **No `_default_pipeline_factory` real wiring this plan.** The factory raises `NotImplementedError("Plan 07-04 wires this; tests must inject a Mock(spec=Pipeline)")`. Plan 07-04 will land the same 8-stage construction that lives in `__main__._amain` today. This keeps Plan 07-02 importable + testable without ultralytics / dearpygui-runtime in the test process. Documented in the function's docstring + the SUMMARY.
- **`EXIT_OK` mirrored locally** (`_EXIT_OK_LOCAL: Final[int] = 0`) instead of imported from `pastor_tracker.__main__`. This avoids the circular-import edge case that Plan 07-04 introduces when `__main__` learns to construct `Dashboard`. The value matches `__main__.EXIT_OK` exactly.
- **`Future[None]` on done-callback signatures** (not `Future[object]`). Mypy correctly infers `Coroutine[..., None]` for Pipeline lifecycle methods, so `asyncio.run_coroutine_threadsafe(pipeline.start(), loop)` returns `Future[None]`. Initially typed as `Future[object]`; mypy `arg-type` error flagged on `fut.add_done_callback(self._log_command_completion)` — fix was to narrow the parameter type to `Future[None]`.
- **AsyncMock coroutine close-on-submit** in tests. `Mock(spec=Pipeline)` auto-creates AsyncMock for the 6 async lifecycle methods; calling them in tests returns an un-awaited coroutine. Without intervention, pytest's unraisable-exception hook trips a `PytestUnraisableExceptionWarning` at GC. The mock `host.submit` `side_effect` closes the coroutine before returning the test's pre-resolved Future — pattern documented in the test file's `_make_mock_host` docstring.

## Reserved Swap-in Points for Plan 07-03 / 07-04

Plan 07-03 (sliders + Save Config restart sequence) can land WITHOUT modifying Plan 07-02 code:

| Hook | File:Line | Plan 07-03 swap |
|------|-----------|------------------|
| `_on_slider_change_stub` | `dashboard.py` `_build_slider_stubs` callback | Replace with `_make_slider_cb(key) -> _cb(...)` closure that writes `_pending_config[key] = value` + calls `self._unsaved_badge_text` refresh hook |
| `_on_save_config_pressed` | `dashboard.py` | Replace WARN log with the real `model_copy(update=...) -> ValidationError catch -> JSON write -> host.submit(quit).result() -> host.stop() -> rebuild Pipeline + new host -> host.submit(start) -> _pending_config.clear()` sequence |
| `_unsaved_badge_text` | `dashboard.py` | Replace `return ""` with `return "(unsaved changes)" if self._pending_config else ""` |
| `_on_exit_callback` | `dashboard.py` | Replace `dpg.stop_dearpygui()` with modal-decision logic: Save & Quit / Quit Anyway / Cancel when `_pending_config` non-empty |

Plan 07-04 (status panel + EventBus) can land WITHOUT modifying Plan 07-02 code:

| Hook | File:Line | Plan 07-04 swap |
|------|-----------|------------------|
| 6 reserved status `dpg.add_text` slots | `dashboard.py` `_build_status_slots` | Replace placeholders with tagged widgets; status_panel.refresh() writes via `dpg.set_value(tag, ...)` |
| 10 Hz refresh hook | `dashboard.py` `_render_tick` (comment-blocked) | Uncomment `if self._frame_count % _STATUS_REFRESH_DIVISOR == 0: self._status_panel.refresh(self._pipeline.snapshot())` |
| `_STATUS_REFRESH_DIVISOR` | `dashboard.py` module-level Final[int]=3 | Already in place; no change needed |
| EventBus deque (UI side) | NEW `ui/_event_bus.py` | structlog processor BEFORE JSONRenderer per RESEARCH §5 corrective; `_status_panel` reads `deque[0]` for `Last error: ...` line |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Test expectation for all-zero BGR -> RGBA float buffer**
- **Found during:** Task 1 RED phase, first pytest run.
- **Issue:** Test `test_bgr_frame_to_rgba_float_flat_native_size` asserted `buf.max() == 0.0` on an all-zero BGR frame. Wrong: `cv2.COLOR_BGR2RGBA` synthesizes an opaque alpha channel = 255, so the float32 buffer's max is `1.0` (any alpha pixel).
- **Fix:** Updated the assertion to `buf.max() == 1.0` with a docstring comment explaining the alpha-channel synthesis. The implementation is correct; the test expectation was wrong.
- **Files modified:** `pastor_tracker/tests/test_overlays.py`
- **Commit:** `240b664` (rolled into the Task 1 commit before separating).

**2. [Rule 3 — Blocking] Un-consumed coroutine warnings under pytest filterwarnings=["error"]**
- **Found during:** Task 1 + Task 2 first pytest runs.
- **Issue:** Two distinct GC-time `PytestUnraisableExceptionWarning` sources:
  - `_noop()` coroutine built inline in `test_submit_before_start_raises` — submit refuses it, so the coroutine is never awaited and the unraisable hook fires at GC.
  - AsyncMock-produced coroutines from `Mock(spec=Pipeline)` calls in Task 2 dashboard tests — same root cause.
- **Fix:**
  - `test_submit_before_start_raises`: bind the coroutine to a local, call `coro.close()` in a `finally` block after the `pytest.raises(...)` context.
  - Task 2 mock-host helpers: `host.submit.side_effect = _submit_closing_coros`, where `_submit_closing_coros` calls `coro.close()` (if available) before returning the resolved test Future. Three test factories use this pattern (`_make_mock_host`, `_make_host_returning_exception_future`, the local builders in quit-sequence + initiate-quit tests).
- **Files modified:** `pastor_tracker/tests/test_pipeline_thread.py`, `pastor_tracker/tests/test_dashboard.py`
- **Commits:** `240b664` (Task 1) + `d3ccd23` (Task 2).

**3. [Rule 1 — Bug] mypy `Future[None]` vs `Future[object]` parameterization**
- **Found during:** Task 2 mypy run.
- **Issue:** Initial done-callback signatures were typed `def _handle_home_done(self, fut: Future[object]) -> None:`. Mypy correctly inferred `Future[None]` for `host.submit(pipeline.start())` (Pipeline lifecycle methods return `Coroutine[..., None]`) and rejected the broader parameter as `arg-type` mismatch.
- **Fix:** Narrowed both done-callback signatures to `Future[None]`. Test files keep `Future[object]` locally since they're constructed with `set_result(None)` / `set_exception(...)` — that's still assignable.
- **Files modified:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py`
- **Commit:** `d3ccd23`.

**4. [Rule 1 — Bug] Ruff RUF046 `int(round(...))` redundancy**
- **Found during:** Task 1 ruff run.
- **Issue:** `_overlays.py` had `int(round(x_norm * w_px))` — Ruff flagged the inner `int()` as redundant because `round(float)` already returns `int` per CPython since 3.0.
- **Fix:** Removed the inner `int()` cast in 3 spots. Behavior unchanged; types still flow correctly through mypy.
- **Files modified:** `pastor_tracker/src/pastor_tracker/ui/_overlays.py`
- **Commit:** `240b664`.

**5. [Rule 1 — Bug] Ruff SIM117 nested `with` statements**
- **Found during:** Task 2 ruff run.
- **Issue:** `_build_ui()` had two pairs of nested `with` contexts (theme + theme_component, window + horizontal group). Ruff SIM117 recommends combining.
- **Fix:** Combined into single `with A, B:` statements. Indentation flattens by one level; behavior unchanged.
- **Files modified:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py`
- **Commit:** `d3ccd23`.

Auth gates: none.

## Issues Encountered

- **`pytest --cov=pastor_tracker.ui.dashboard` collection error.** Module-dotted form trips an `ImportError: cannot load module more than once per process` from numpy under coverage's bytecode rewriting (Windows + numpy 2.4 + pytest-cov 6.x). Switched to package-form `--cov=pastor_tracker.ui` which collects cleanly. The Plan 05-05 STATE.md note already documents this CI pitfall — using the package form is the established workaround. Coverage numbers are accurate.
- **Pre-existing flake confirmed.** `test_geometry::test_normalized_angle_roundtrip` fails ONLY in the full-suite run (`pytest tests`); passes in isolation (`pytest tests/test_geometry.py`). This is the documented asyncio-event-loop-bleed flake family carried since Plan 05-04 — explicitly listed in the deferred-items table on STATE.md and called out in this plan's objective as "do NOT chase". Total: 481 passed, 1 skipped, 1 pre-existing flake. Baseline 434 + 47 new tests = 481, expected.

## Manual Smoke Procedure (deferred to Phase 8 QA-04)

The DearPyGui render loop has no headless runner in v2.x (CONTEXT.md "Claude's Discretion"). Visual verification of the operator surface is Phase 8 QA-04 manual smoke; until then, this procedure is the minimum operator-facing test plan that Plan 07-04 will hook into a `--ui` flag.

```pwsh
# From repo root, with venv activated and Arduino + OBS Virtual Camera attached:
$env:PYTHONPATH = "pastor_tracker/src"
D:/System/Documents/PastorTrackingSystem/pastor_tracker/.venv/Scripts/python.exe -c "
from pastor_tracker.config import Config
from pastor_tracker.ui.dashboard import Dashboard
# Plan 07-04 will replace this lambda with the real factory from __main__._amain.
from unittest.mock import Mock
from pastor_tracker.pipeline import Pipeline
mock = Mock(spec=Pipeline)
mock.snapshot.return_value = ...  # see test_dashboard for a real PipelineSnapshot
mock.latest_frame = None
dash = Dashboard(Config(), pipeline_factory=lambda c: mock)
dash.run()
"
```

Plan 07-04 will swap the Mock factory with the real `__main__._amain` stage construction; smoke procedure then becomes `python -m pastor_tracker --ui`.

## Threat Flags

None — Plan 07-02 introduces no new I/O surface (the dashboard only consumes the Phase 6 Pipeline public surface), no new auth boundary, no new schema, no new file access. ASVS V5 (Input Validation) is preserved by Pydantic on the Config seam (dual-bounded sliders are Plan 07-03's surface). The only writes the dashboard does are to its own DearPyGui drawlist; the Pipeline's frozen `latest_frame` / `snapshot()` are read-only via atomic CPython attribute reads.

## TDD Gate Compliance

This plan is not a plan-level `type: tdd` plan (`type: execute`). Both tasks declared `tdd="true"` per task; the per-task RED -> GREEN cycle was followed (RED: write failing tests; GREEN: implement to pass) and committed as a single `feat(...)` commit per task — matching the Plan 07-01 precedent in this phase and the Phase 5 precedent across the repo.

## Next Phase Readiness

- Plan 07-03 (sliders + Save Config restart sequence) can land entirely additive against the swap-in points documented above.
- Plan 07-04 (status panel + EventBus + `--ui` flag in `__main__`) can land additive against the 10 Hz refresh hook + the 6 reserved status `add_text` slots.
- Pipeline contracts unchanged.
- Phase 6 + Phase 7 Plan 07-01 invariants preserved (440 pre-Plan-07-02 tests + 47 new = 487 passing tests under isolation; full-suite run lands at 481 + 1 known flake + 1 skip per pre-existing baseline).
- `mypy --strict` clean across `src/pastor_tracker/ui/`.
- `ruff check src tests` clean.

## Self-Check: PASSED

- `pastor_tracker/src/pastor_tracker/ui/_overlays.py` — FOUND, 100% coverage
- `pastor_tracker/src/pastor_tracker/ui/_pipeline_thread.py` — FOUND, 94% coverage
- `pastor_tracker/src/pastor_tracker/ui/dashboard.py` — FOUND, 72% coverage (uncovered = render-loop body + DPG context construction, exempt per CONTEXT.md)
- `pastor_tracker/tests/test_overlays.py` — FOUND, 14 tests passing
- `pastor_tracker/tests/test_pipeline_thread.py` — FOUND, 6 tests passing
- `pastor_tracker/tests/test_dashboard.py` — FOUND, 27 tests passing
- `pastor_tracker/src/pastor_tracker/ui/__init__.py` — `from pastor_tracker.ui.dashboard import Dashboard` PRESENT
- `grep "import dearpygui" pastor_tracker/src/pastor_tracker/ui/_overlays.py` — 0 matches (pure-core boundary preserved)
- `grep "import dearpygui" pastor_tracker/src/pastor_tracker/ui/_pipeline_thread.py` — 0 matches
- `grep "OrchestratorRejected" pastor_tracker/src/pastor_tracker/ui/dashboard.py` — 3 matches (import + isinstance check + except clause in _initiate_quit)
- `grep "no_close=True" pastor_tracker/src/pastor_tracker/ui/dashboard.py` — 1 match (D-13)
- `grep "mvFormat_Float_rgba" pastor_tracker/src/pastor_tracker/ui/dashboard.py` — 2 matches (Pitfall 4)
- `grep "ui_save_config_not_implemented" pastor_tracker/src/pastor_tracker/ui/dashboard.py` — 1 match (Plan 07-02 stub marker)
- `mvKey_S/P/H/E/Q` all present in dashboard.py — VERIFIED
- Commit `240b664` exists in git log — FOUND
- Commit `d3ccd23` exists in git log — FOUND
- `mypy src/pastor_tracker/ui/` — Success
- `ruff check src/pastor_tracker/ui/ tests/test_overlays.py tests/test_pipeline_thread.py tests/test_dashboard.py` — All checks passed
- `pytest tests/test_overlays.py tests/test_pipeline_thread.py tests/test_dashboard.py` — 47 passed
- Full suite `pytest tests` — 481 passed, 1 skipped, 1 pre-existing flake (`test_geometry::test_normalized_angle_roundtrip` documented asyncio-bleed family — passes in isolation)

---
*Phase: 07-ui-dashboard*
*Completed: 2026-05-11*
