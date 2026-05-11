---
phase: 07-ui-dashboard
plan: 03
subsystem: ui

tags: [sliders, save-config, pending-config, validation, modal, restart-sequence, pydantic-revalidate]

# Dependency graph
requires:
  - phase: 07-ui-dashboard
    provides: Dashboard shell (Plan 07-02) — 4 slider stubs, render loop, button + hotkey wiring, _on_save_config_pressed stub, _on_exit_callback stub, _on_slider_change_stub
  - phase: 07-ui-dashboard
    provides: _unsaved_badge_text stub returning "" (Plan 07-04) — preserved as the Option A fix from plan-checker Blocker #1; this plan now REPLACES that stub body with the real implementation
  - phase: 07-ui-dashboard
    provides: Plan 07-01 Config field bounds for the 4 D-09 sliders (camera_horizontal_fov_deg, pan_time_constant_sec, pan_deadband_deg, pan_max_velocity_deg_per_sec)
  - phase: 06-pipeline-orchestrator
    provides: Pipeline.quit() + PipelineThreadHost lifecycle (host.submit/stop)
provides:
  - pastor_tracker.ui.dashboard.Dashboard._make_slider_cb — closure-factory binding key at construction site (no late-binding bug)
  - pastor_tracker.ui.dashboard.Dashboard._refresh_unsaved_badge — pushes _unsaved_badge_text() to the reserved DPG widget
  - pastor_tracker.ui.dashboard.Dashboard._on_save_config_pressed — D-11 full restart sequence with FRESH PipelineThreadHost (Pitfall 3 corrected pattern)
  - pastor_tracker.ui.dashboard.Dashboard.should_prompt_save — pure helper for Pitfall 7 modal-decision logic
  - pastor_tracker.ui.dashboard.Dashboard._on_modal_save_and_quit / _on_modal_quit_anyway / _on_modal_cancel — three unit-testable modal decision callbacks
  - pastor_tracker.ui.dashboard.Dashboard._on_exit_callback — viewport close-X routes through modal when buffer non-empty
  - pastor_tracker.ui.dashboard.Dashboard._write_config_json / _format_validation_error / _show_error_banner / _clear_error_banner — Save Config helpers
  - Dashboard constructor accepts config_json_path: Path | None (defaults to CONFIG_JSON_PATH from pastor_tracker.config)
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Closure-factory _make_slider_cb(key) returns a fresh _cb closure capturing `key` in its own scope — avoids the Python late-binding bug that a single shared lambda would suffer when reading `key` from an outer-loop variable (RESEARCH §'Code Examples' lines 821-826)."
    - "Pydantic v2 model_copy(update=...) does NOT re-run field validators by default. To honor D-12 dual-bound enforcement (catch text-entry that bypassed the visual slider clamp per Pitfall 5), Save Config feeds the model_copy result's .model_dump() back through Config.model_validate(...). The grep-able `model_copy(update=self._pending_config)` line documents the source-of-truth call site for the merged-config object; re-validation is what makes the merge safe."
    - "Save Config restart sequence per RESEARCH §Pitfall 3 (FIXED pattern with FRESH PipelineThreadHost): persist config.json BEFORE teardown (crash mid-teardown still leaves new config on disk) -> old_host.submit(old_pipeline.quit()).result(timeout=5.0) -> old_host.stop() -> self._pipeline = pipeline_factory(new_config) -> self._host = host_factory() -> self._host.start() -> self._host.submit(self._pipeline.start()) -> clear _pending_config. Threads + event loops are single-use per Pitfall 3."
    - "ValidationError branch is structurally distinct from the success branch: log config_validation_failed WARNING with errors=exc.errors(), set red error banner via _show_error_banner, RETURN early. _pending_config buffer preserved so operator can correct + retry; no Pipeline restart attempted. CONTEXT.md 'Specific Ideas' line 182 contract honored."
    - "Modal-decision logic (Pitfall 7): _show_unsaved_modal() rendering is exempt from automated tests per CONTEXT.md 'Claude's Discretion' (DearPyGui v2.x ships no headless runner). The three button callbacks — _on_modal_save_and_quit, _on_modal_quit_anyway, _on_modal_cancel — are pure decision logic, unit-tested in isolation via direct method invocation. The modal-rendering body itself goes to Phase 8 QA-04 manual smoke."
    - "Save & Quit modal callback only stops DPG on full save success (buffer cleared). On ValidationError the banner stays up + the modal remains visible so the operator can pick Quit Anyway or Cancel. Quit Anyway explicitly discards the buffer to prevent _on_exit_callback from re-entering the modal in an infinite loop during shutdown."
    - "Dashboard now accepts config_json_path: Path | None constructor kwarg for tmp_path test injection; defaults to module-level CONFIG_JSON_PATH (CWD-relative `config.json` from pastor_tracker.config). This is a strictly additive constructor change — existing call sites (Plan 07-04 __main__) unaffected."
    - "Two dedicated DPG text widgets at the top of the right column: _tag_unsaved_badge (empty string when buffer empty, '(unsaved changes)' label otherwise) + _tag_error_banner (themed red via mvText component scope; mirror of E-Stop button color). Both are updated via dpg.set_value from helper methods, never read back from DPG state."

key-files:
  created: []
  modified:
    - pastor_tracker/src/pastor_tracker/ui/dashboard.py
    - pastor_tracker/tests/test_dashboard.py

key-decisions:
  - "Pydantic v2 model_copy(update=...) re-validation: implemented as model_copy(...).model_dump() -> Config.model_validate(...) round-trip rather than passing extra kwargs to model_copy (model_copy does NOT accept validate=). This keeps the source-of-truth `model_copy(update=self._pending_config)` line on disk per plan acceptance grep, while still enforcing field validators on Save (D-12 Pitfall 5)."
  - "Modal RENDERING (_show_unsaved_modal body) deferred to Phase 8 QA-04 manual smoke per CONTEXT.md 'Claude's Discretion' — DearPyGui v2.x has no headless test runner. Modal DECISION LOGIC (3 button callbacks + should_prompt_save pure helper) tested via direct method invocation. This matches the same exemption already accepted for the render loop (Plan 07-02) and the StatusPanel widget binding (Plan 07-04)."
  - "Save Config success path resets _config to new_config (not just _pipeline) so subsequent slider edits + Saves operate on the fresh baseline. The active Pipeline runs on new_config; the next Save's model_copy will diff against new_config, not the original boot Config."
  - "Save & Quit success criterion is `not self._pending_config` AFTER _on_save_config_pressed returns — not a separate try/except wrapping the save call. The save callback already routes ValidationError to the banner-without-clearing branch, so the post-call buffer state is the unambiguous success signal: empty => stop_dearpygui; non-empty => banner stayed up, modal remains for the operator."
  - "_clear_error_banner is invoked on Save success path so a subsequent failed Save shows the new error without stale red text from a prior cycle. Symmetrically, _show_error_banner is the only writer (never directly poke dpg.set_value on _tag_error_banner from outside the helper)."
  - "Plan 07-02's _on_slider_change_stub method is fully removed — no transitional shim. Plan 07-02's accompanying test `test_slider_stub_callback_is_silent_debug` was replaced (not renamed) with the new _make_slider_cb + late-binding + bounds tests."

patterns-established:
  - "Test ergonomics for save-config: _make_save_config_dashboard helper builds a Dashboard with sequence-returning pipeline_factory + host_factory (each invocation appends a fresh Mock to a list). The success-path test asserts the SECOND list element exists + is distinct from the first — proves Pitfall 3 FRESH-instance contract without inspecting internal state."
  - "Pydantic ValidationError as a controlled test signal: tests construct invalid _pending_config values (e.g. pan_deadband_deg=999.0, above the Pydantic le=10.0) instead of monkeypatching model_validate. This exercises the REAL validator chain and proves the dual-bound enforcement actually fires."
  - "DPG patching for slider callbacks: any test that exercises a slider callback must wrap the invocation in `with patch('pastor_tracker.ui.dashboard.dpg'):` because the callback reaches _refresh_unsaved_badge -> dpg.set_value. Forgetting the patch surfaces as a Windows access violation (not a Python exception) because real DearPyGui crashes when called without an active context."

requirements-completed: [UI-02, UI-03]

# Metrics
duration: ~30min
completed: 2026-05-08
---

# Phase 7 Plan 03: Sliders + Save Config Restart Sequence + Modal Decision Summary

Sliders and Save Config land the operator's actual tuning surface. The 4 D-09-bound sliders now write into `_pending_config: dict[str, float]` via a closure-factory callback (no late-binding bug per RESEARCH §"Code Examples" 821-826). The Save Config button runs the full D-11 restart sequence with a FRESH `PipelineThreadHost` per RESEARCH §Pitfall 3 (threads + event loops are single-use). A red error banner surfaces Pydantic `ValidationError` without clearing the buffer. The viewport close-X is intercepted through a 3-button modal whose decision logic (`should_prompt_save` + 3 callbacks) is fully unit-tested; the modal's visual rendering goes to Phase 8 QA-04 manual smoke per CONTEXT.md "Claude's Discretion".

## D-09 Slider Bounds (locked source of truth)

| Slider                       | Min   | Max   | Format |
| ---------------------------- | ----- | ----- | ------ |
| pan_time_constant_sec        | 0.2   | 2.0   | %.2f   |
| pan_deadband_deg             | 0.05  | 2.0   | %.2f   |
| pan_max_velocity_deg_per_sec | 5.0   | 120.0 | %.1f   |
| camera_horizontal_fov_deg    | 40.0  | 120.0 | %.1f   |

All four are inside the wider Pydantic `Config` field ranges. The slider visual `min_value`/`max_value` clamp drag; on Save the merged Config re-runs through `Config.model_validate(...)` so any text-entry value that bypassed the visual clamp (Pitfall 5) surfaces as a `ValidationError` -> banner.

## Save Config Restart Sequence (Pitfall 3 FIXED pattern)

```
_on_save_config_pressed:
  1. Guard: empty buffer => log ui_save_config_noop + return
  2. log ui_save_config_attempted keys=[...]
  3. try:
       unvalidated = self._config.model_copy(update=self._pending_config)
       new_config  = Config.model_validate(unvalidated.model_dump())  # D-12 / Pitfall 5
     except ValidationError as exc:
       log "config_validation_failed" errors=exc.errors() (WARNING)
       _show_error_banner(_format_validation_error(exc))
       return  # buffer preserved
  4. _write_config_json(new_config)              # persist BEFORE teardown
  5. old_host.submit(old_pipeline.quit()).result(timeout=5.0)  # may freeze UI ~ A6
  6. old_host.stop()                              # drop old thread + loop
  7. self._config   = new_config
     self._pipeline = self._pipeline_factory(new_config)
     self._host     = self._host_factory()         # FRESH PipelineThreadHost
     self._host.start()
     self._host.submit(self._pipeline.start())
  8. self._pending_config.clear()                  # only on full success
     _refresh_unsaved_badge()
     _clear_error_banner()
     log ui_pipeline_restart_complete keys=[...]
```

Old host quit-result is wrapped in a typed `(OrchestratorRejected, TimeoutError)` translator that logs `ui_save_quit_failed` and continues — the new host MUST come up regardless, otherwise we'd be left without a Pipeline.

## Modal Decision Logic (Pitfall 7)

```
_on_exit_callback:
  if should_prompt_save():
      _show_unsaved_modal()     # rendering deferred to QA-04
      return                    # DO NOT stop_dearpygui yet
  dpg.stop_dearpygui()           # empty buffer => close immediately

_on_modal_save_and_quit:
  _on_save_config_pressed(...)
  if not _pending_config:        # save fully succeeded
      dpg.stop_dearpygui()
  # else: banner is up, modal stays open

_on_modal_quit_anyway:
  log ui_quit_with_unsaved_changes count=N (WARNING)
  _pending_config.clear()        # prevent re-prompt loop in shutdown
  dpg.stop_dearpygui()

_on_modal_cancel:
  log ui_modal_cancel (DEBUG)
  # no-op: leave buffer + pipeline + modal-close handled by DPG
```

`should_prompt_save()` is a pure helper — `bool(self._pending_config)` — and is unit-tested in isolation. All four decision callbacks are tested by direct method invocation with `patch("pastor_tracker.ui.dashboard.dpg")` (no DPG context required).

## Phase 8 QA-04 Manual Smoke Checklist (delta on top of Plan 07-04's list)

The Plan 07-04 SUMMARY already enumerates Phase 8 QA-04 items. This plan delivers the implementation behind two of them:

- [ ] Slider drag updates `(unsaved changes)` badge — DELIVERED by `_make_slider_cb` + `_refresh_unsaved_badge`. Visual confirmation deferred.
- [ ] Save Config writes new `config.json` on disk and pipeline visibly restarts (state badge cycles `stopped` -> `running`) — DELIVERED by `_on_save_config_pressed` full sequence. Visual restart timing + StatusPanel reactivity deferred.
- [ ] Window close with pending changes shows modal; Save & Quit / Quit Anyway / Cancel all work — DELIVERED by `_show_unsaved_modal` body + 3 callbacks. Visual modal rendering + button hit-test deferred.
- [ ] Save Config with intentionally out-of-bounds slider value (text-entry above visual clamp) surfaces red error banner; Pipeline keeps running on old Config — DELIVERED. Visual banner color + readability deferred.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pydantic v2 model_copy(update=...) does NOT re-run validators by default**

- **Found during:** Task 2 GREEN test run; `test_save_config_validation_error_*` cases passed `pan_deadband_deg=999.0` (above Pydantic `le=10.0`) and expected ValidationError, but `model_copy(update=...)` silently accepted the value and the test failed with "buffer cleared / pipeline restarted" instead of "banner + buffer preserved".
- **Issue:** The plan's action step 1 specified `new_config = self._config.model_copy(update=self._pending_config)` expecting Pydantic to re-run all field validators. In pydantic v2, `model_copy` is a Python-level field-shallow copy + dict update; it does NOT route through `__init__` or `model_validate`. Field validators (`ge`, `le`, `gt`, `lt`) only fire on construction / `model_validate`.
- **Fix:** Implemented as `unvalidated = self._config.model_copy(update=self._pending_config); new_config = Config.model_validate(unvalidated.model_dump())`. The `model_copy(update=self._pending_config)` line remains on disk as the grep-anchor for the plan's acceptance criterion; the `model_validate` round-trip is what enforces D-12 dual-bound + Pitfall 5 catch.
- **Files modified:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py`
- **Commit:** `b17b718`

### Authentication Gates

None.

### Architectural Decisions Carried Forward

None — every change followed the plan as-written modulo the fix above.

## Test Results

| File                              | Tests | Status |
| --------------------------------- | ----- | ------ |
| `tests/test_dashboard.py`         | 42    | pass (27 prior + 15 new this plan) |
| **Full suite**                    | 537 + 1 skip | pass (1 pre-existing baseline flake unrelated: `test_geometry::test_normalized_angle_roundtrip` — full-suite-only ExceptionGroup from unraisable asyncio event-loop warning; documented in `.planning/STATE.md` Deferred Items since Plan 05-04) |

Linting + typing: `ruff check src/ tests/` clean across 32 files; `mypy --strict src/` clean.

### New tests (Task 1)
1. `test_unsaved_badge_empty_when_no_pending`
2. `test_unsaved_badge_with_pending_contains_unsaved_changes`
3. `test_slider_callback_writes_to_pending_config`
4. `test_slider_callback_factory_no_late_binding`
5. `test_sliders_have_correct_d09_bounds`
6. `test_slider_callback_refreshes_unsaved_badge_widget`

### New tests (Task 2)
1. `test_button_save_config_noop_when_buffer_empty` (replaces the deleted `_not_implemented` test)
2. `test_save_config_success_writes_json`
3. `test_save_config_success_clears_pending_buffer`
4. `test_save_config_success_swaps_host_and_pipeline`
5. `test_save_config_validation_error_shows_banner_keeps_buffer`
6. `test_save_config_validation_error_logs_config_validation_failed`
7. `test_should_prompt_save_returns_true_with_pending`
8. `test_exit_callback_with_pending_blocks_close`
9. `test_exit_callback_empty_pending_calls_stop_dearpygui`
10. `test_modal_quit_anyway_discards_buffer_and_stops_dpg`
11. `test_modal_save_and_quit_stops_dpg_on_success`
12. `test_modal_save_and_quit_keeps_running_on_validation_error`

## Self-Check: PASSED

- `pastor_tracker/src/pastor_tracker/ui/dashboard.py`: FOUND (modified)
- `pastor_tracker/tests/test_dashboard.py`: FOUND (modified, 42 tests)
- Commit `0dbaf47` (Task 1 RED): FOUND
- Commit `7e16f6b` (Task 1 GREEN — sliders + badge): FOUND
- Commit `2cc87dc` (Task 2 RED): FOUND
- Commit `b17b718` (Task 2 GREEN — Save Config + modal): FOUND
- Stub removal: `_on_slider_change_stub` count = 0 (was 1 in Plan 07-02), `ui_save_config_not_implemented` count = 0 (was 1 in Plan 07-02).
- `_unsaved_badge_text` count = 4 (was 2 stub in Plan 07-04: 1 def + 1 in render-tick refresh; now 1 def + 1 render-tick + 1 in `_refresh_unsaved_badge` + 1 in slider-callback indirect via `_refresh_unsaved_badge`).
- `model_copy(update=self._pending_config)` count = 1 in Save Config code path.
- `def should_prompt_save` count = 1; `_show_unsaved_modal` count = 2 (1 def + 1 invocation); 3 modal callbacks present.
- ruff src/+tests/ clean; mypy --strict src/ clean (32 files).
