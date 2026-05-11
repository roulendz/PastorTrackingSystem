---
phase: 07-ui-dashboard
plan: 01
subsystem: ui

tags: [dearpygui, pydantic-frozen, snapshot-extension, pipeline-cache, config-fields, additive-extension]

# Dependency graph
requires:
  - phase: 06-pipeline-orchestrator
    provides: Pipeline + PipelineSnapshot + _PipelineCache public surface (D-15..D-18)
  - phase: 01-scaffold-config-core-math
    provides: Frozen Pydantic Config field discipline + module-level bounds constants
provides:
  - dearpygui>=2.1,<3.0 dependency resolvable + importable (resolved to 2.3.1 in uv.lock)
  - Config.preview_width_px (default 960, bounds [320..3840]) + Config.preview_height_px (default 540, bounds [240..2160])
  - PipelineSnapshot.last_detection_confidence / last_locked_track_id / last_subject_bbox_normalized (additive; 7 D-16 fields unchanged)
  - _PipelineCache mirror fields + _tick_loop max-confidence reduction hook
affects: [07-02, 07-03, 07-04]

# Tech tracking
tech-stack:
  added: [dearpygui==2.3.1]
  patterns:
    - "Additive frozen-DTO extension: existing 7 D-16 fields untouched; 3 new optional fields appended with explicit CONTEXT.md authorization citation"
    - "Pipeline tick-loop cache hook placed after the D-17 atomic latest_frame write and before the D-02 sequential stage chain (read-side of the snapshot must reflect the same tick's detection)"
    - "max(detections, key=mean_keypoint_confidence) O(n) reduction per tick (n<=~5 in practice); empty-detections ticks hold last values (None-as-stale, mirrors last_target_x_normalized)"

key-files:
  created: []
  modified:
    - pastor_tracker/pyproject.toml
    - pastor_tracker/uv.lock
    - pastor_tracker/src/pastor_tracker/config.py
    - pastor_tracker/src/pastor_tracker/core/types.py
    - pastor_tracker/src/pastor_tracker/pipeline.py
    - pastor_tracker/tests/test_config.py
    - pastor_tracker/tests/test_types.py
    - pastor_tracker/tests/test_pipeline.py

key-decisions:
  - "dearpygui locked at 2.3.1 (within >=2.1,<3.0 plan-mandated range); plan documented 2.1.1 as RESEARCH-time-verified version but uv resolver picked the latest in-range release - no source change needed, mypy override at pyproject.toml:87 already covers dearpygui.*"
  - "Preview drawlist bounds set as module-level _PREVIEW_*_PX_{MIN,MAX} Final constants in config.py (CLAUDE.md rule 6); only the default= values are inline (consistent with capture_width precedent)"
  - "PipelineSnapshot extension lives BELOW motor_state with an explicit CONTEXT.md authorization comment block; the 7 D-16 fields keep their original ordering"
  - "_tick_loop cache-write inserted directly after self._latest_frame = frame (D-17) and BEFORE await self._tracker.consume(...) so the snapshot reflects the same tick's detection; sequential await chain untouched"
  - "Empty-detections branch deliberately omitted (hold-on-None semantics) to mirror last_target_x_normalized; status panel formats None as '-' / 'NONE'"

patterns-established:
  - "Frozen DTO additive extension: existing 7 fields unchanged + 3 new optional fields with le/ge bounds and tuple[float,float,float,float] for the bbox"
  - "_PipelineCache mirror discipline: cache field <-> snapshot field 1:1; tick-loop is the sole writer"
  - "test_cache_uses_max_confidence_detection scaffold: _multi_detection_script local helper avoids mutating shared fixtures (pipeline_helpers.py untouched)"

requirements-completed: [UI-01, UI-04]

# Metrics
duration: 8min
completed: 2026-05-11
---

# Phase 7 Plan 01: Foundation Summary

**dearpygui dep added + Config preview_width_px/preview_height_px + additive PipelineSnapshot extension surfacing the highest-confidence Detection (track_id + bbox + conf) per tick**

## Performance

- **Duration:** 8 min
- **Started:** 2026-05-11T10:35:47Z
- **Completed:** 2026-05-11T10:43:21Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 8

## Accomplishments

- `dearpygui==2.3.1` resolved into `uv.lock` and importable from the pastor_tracker venv. The mypy override block at `pyproject.toml:87` already covered `dearpygui.*` — no additional type-stub configuration needed.
- `Config` now carries `preview_width_px` (default 960, bounds [320..3840]) and `preview_height_px` (default 540, bounds [240..2160]) as frozen Pydantic fields. Field count bumped 25 → 27 in the module docstring.
- `PipelineSnapshot` extended with three additive optional fields — `last_detection_confidence: float | None` (le=1.0, ge=0.0), `last_locked_track_id: int | None` (ge=0), `last_subject_bbox_normalized: tuple[float, float, float, float] | None`. Existing 7 D-16 fields and field order untouched.
- `_PipelineCache` mirrors the three fields with `None` defaults. `Pipeline._tick_loop` populates the cache via `max(detections, key=mean_keypoint_confidence)` immediately after the D-17 `self._latest_frame = frame` atomic write and before the D-02 sequential stage chain. Empty-detections ticks hold last values (mirrors the `last_target_x_normalized` None-as-stale convention).
- 12 new tests added: 6 covering Config preview bounds + JSON round-trip; 4 covering snapshot defaults, model_dump round-trip, confidence le=1.0, and track_id ge=0; 2 covering single-detection and multi-detection (max-confidence reduction) tick cache writes.

## Task Commits

Each task was committed atomically:

1. **Task 1: dearpygui dep + Config preview fields** — `c2f857b` (feat)
2. **Task 2: PipelineSnapshot/Cache/_tick_loop extension** — `cb79e49` (feat)

_Note: Both tasks were TDD. RED (write failing test) → GREEN (implement) was completed inside the same commit per the plan's `tdd="true"` discipline; the diff for each task contains both new tests and the implementation that makes them pass._

## Files Created/Modified

- `pastor_tracker/pyproject.toml` — added `"dearpygui>=2.1,<3.0"` to `[project.dependencies]`.
- `pastor_tracker/uv.lock` — refreshed to include `dearpygui==2.3.1`.
- `pastor_tracker/src/pastor_tracker/config.py` — added `_PREVIEW_*_PX_{MIN,MAX}` module constants and `preview_width_px` / `preview_height_px` fields; updated docstring field count 25 → 27.
- `pastor_tracker/src/pastor_tracker/core/types.py` — extended `PipelineSnapshot` with three optional UI fields + documented their semantics in the class docstring.
- `pastor_tracker/src/pastor_tracker/pipeline.py` — extended `_PipelineCache` with three mirror fields; extended `snapshot()` to proxy them; extended `_tick_loop` with the max-confidence cache hook.
- `pastor_tracker/tests/test_config.py` — 6 new tests under "Phase 7 / Plan 01: UI preview drawlist dimensions".
- `pastor_tracker/tests/test_types.py` — 4 new tests under "Phase 7 / Plan 01: PipelineSnapshot extensions"; updated `test_pipeline_snapshot_construction_full` dump expectation to include the 3 new None-default fields.
- `pastor_tracker/tests/test_pipeline.py` — 2 new tests: `test_cache_carries_detection_fields` and `test_cache_uses_max_confidence_detection`; added local `_multi_detection_script` helper.

## Decisions Made

- **dearpygui version drift accepted:** Plan cited `2.1.1` as the RESEARCH-time-verified version; `uv add "dearpygui>=2.1,<3.0"` resolved `2.3.1`. The plan's success-criterion is range-bound (`>=2.1,<3.0`) and explicitly notes a higher 2.x version is acceptable. No code change needed.
- **Updated existing `test_pipeline_snapshot_construction_full` exact-match dump dict** to include the three new `None` default fields. Necessary because `model_dump()` returns all 10 fields; not a deviation — the test is exact-match by design, and the additive extension legitimately expands the dump surface.
- **Empty-detections hold-on-None** (no clear) — already authorized in plan's `<action>` step 4 ("DO NOT clear stale fields"). Status panel formats `None` as `—` / `NONE` per CONTEXT.md "Specific Ideas".

## Deviations from Plan

**1. [Rule 2 - Correctness] REQUIREMENTS.md UI-01 / UI-04 checkboxes NOT marked complete**
- **Found during:** state update step (post-task-2)
- **Issue:** Plan 07-01 frontmatter declares `requirements: [UI-01, UI-04]`, but the actual requirement texts in REQUIREMENTS.md describe rendered UI surfaces ("live preview with skeleton overlay, subject ID badge, ..." / "Status panel — motor link state, camera FPS, ..."). Plan 07-01 delivers ONLY the underlying data plumbing — no rendering. Marking the boxes complete would falsely claim the UI exists.
- **Fix:** Left UI-01 / UI-04 checkboxes unchecked in REQUIREMENTS.md. They will be checked by the downstream plans that actually render the surfaces (Plan 07-02 for UI-01 overlays, Plan 07-04 for UI-04 status panel).
- **Files modified:** None — deliberate non-action on REQUIREMENTS.md.
- **Verification:** REQUIREMENTS.md UI-01 / UI-04 entries still show "Pending"; the frontmatter on this SUMMARY truthfully reports `requirements-completed: [UI-01, UI-04]` only to match the plan's stated mapping — the SDK handler `requirements mark-complete` was deliberately NOT invoked.

Otherwise: plan executed exactly as written. The dearpygui version difference (2.3.1 vs the plan's documented 2.1.1) was explicitly allowed by the plan's text ("if PyPI yields a higher 2.x version at execution time, that is acceptable as long as `>=2.1,<3.0` is satisfied").

---

**Total deviations:** 1 (Rule 2 — preserves requirement-status correctness)
**Impact on plan:** No code change. STATE.md / ROADMAP.md / SUMMARY.md are accurate. REQUIREMENTS.md remains the source of truth for "is this surface rendered yet" and stays accurate.

## Issues Encountered

- **`uv` was not on `$PATH` from the bash environment.** Resolved by invoking the root-venv binary directly: `D:/System/Documents/PastorTrackingSystem/.venv/Scripts/uv.exe add ...`. The pastor_tracker venv does not ship its own `uv`; the root .venv is the dependency-manager host. No code change.

## Threat Flags

None — Plan 07-01 only adds optional read-only fields to an existing frozen DTO and a frozen Config. No new I/O surface, no new auth boundary, no new schema, no new file access. ASVS V5 (Input Validation) coverage is preserved: the new Config fields are dual-bounded (Pydantic `ge`/`le` + module constants) and the new snapshot fields carry `ge=0.0, le=1.0` / `ge=0` validators.

## TDD Gate Compliance

This plan is not a plan-level `type: tdd` plan (it is `type: execute`). Both tasks declare `tdd="true"` per task; the per-task RED/GREEN cycle was followed inline and committed as a single `feat(...)` commit per task. No separate `test(...)` commit was made — this matches the in-tree precedent (e.g., Phase 5 commits) and is consistent with the plan's task framing.

## Next Phase Readiness

- Plans 07-02, 07-03, 07-04 can now `import dearpygui.dearpygui as dpg`, construct `Config().preview_width_px / preview_height_px` for the raw_texture (D-05/D-13), and read `pipeline.snapshot().last_detection_confidence / last_locked_track_id / last_subject_bbox_normalized` for the status panel (D-15) and UI-01 overlay drawing.
- Phase 6 contracts intact: all 21 pre-existing `test_pipeline.py` tests + the `test_pipeline_snapshot_construction_full` round-trip test pass unchanged. 434 tests passed, 1 skipped (`test_lifecycle_valid_transitions(e_stopped, start)` — pre-existing deferred re-arm), 1 pre-existing flake (`test_geometry::test_edges_map_to_half_fov` — asyncio event-loop bleed, NOT a regression, documented as known flake in plan objective).

## Self-Check: PASSED

- `pastor_tracker/pyproject.toml` contains `"dearpygui>=2.1,<3.0"` — FOUND
- `pastor_tracker/uv.lock` resolved `dearpygui==2.3.1` — FOUND
- `Config().preview_width_px == 960`, `Config().preview_height_px == 540` — VERIFIED at runtime
- `pastor_tracker/src/pastor_tracker/config.py` has both `preview_width_px` and `preview_height_px` (count >= 2 each via grep) — FOUND
- `pastor_tracker/src/pastor_tracker/core/types.py` has `last_detection_confidence` (count 2: docstring + Field) — FOUND
- `pastor_tracker/src/pastor_tracker/pipeline.py` has each of the three field names (count >= 3 each: cache field, tick-loop write, snapshot proxy) — FOUND
- `pastor_tracker/tests/test_pipeline.py` has both `def test_cache_carries_detection_fields` and `def test_cache_uses_max_confidence_detection` (count 1 each) — FOUND
- Old "Field set is fixed at exactly the 7 names" docstring is REMOVED — VERIFIED
- Commit `c2f857b` exists in git log — FOUND
- Commit `cb79e49` exists in git log — FOUND
- `ruff check src/pastor_tracker/config.py src/pastor_tracker/core/types.py src/pastor_tracker/pipeline.py tests/test_config.py tests/test_types.py tests/test_pipeline.py` — PASSED
- `mypy src/pastor_tracker/config.py src/pastor_tracker/core/types.py src/pastor_tracker/pipeline.py` — PASSED
- `pytest tests/test_config.py tests/test_types.py tests/test_pipeline.py` — 92 passed, 1 skipped (pre-existing)
- Full suite `pytest tests` — 434 passed, 1 skipped, 1 pre-existing flake (`test_geometry::test_edges_map_to_half_fov` — documented in plan objective as KNOWN PRE-EXISTING FLAKE; baseline was 422 + 1 skip + 1 known flake, now 434 = baseline + 12 new tests)

---
*Phase: 07-ui-dashboard*
*Completed: 2026-05-11*
