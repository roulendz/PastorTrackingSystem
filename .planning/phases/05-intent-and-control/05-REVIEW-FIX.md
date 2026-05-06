---
phase: 05-intent-and-control
fixed_at: 2026-05-05T00:00:00Z
review_path: .planning/phases/05-intent-and-control/05-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 6
skipped: 1
status: partial
---

# Phase 5: Code Review Fix Report

**Fixed at:** 2026-05-05
**Source review:** `.planning/phases/05-intent-and-control/05-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (1 blocker + 6 warnings)
- Fixed: 6
- Skipped: 1 (WR-07 — explicitly marked "no fix required" by reviewer)

## Fixed Issues

### BL-01: PanController emits unclamped multi-degree jump after a long None-upstream gap

**Files modified:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py`, `pastor_tracker/tests/test_pan_controller.py`
**Commit:** `b3988f2`
**Applied fix:**
- Cleared `self._last_upstream_ts_ns = None` inside `_hold()` so the next real frame falls through to the `1 / capture_fps` `dt` floor rather than computing `dt = T_gap` (which collapsed Holden damper decay AND inflated the velocity-clamp ceiling beyond the FOV).
- `_state` is intentionally retained — the damper FollowerState is correct across the gap; only the upstream-timestamp witness needed clearing.
- Added regression test `test_long_none_gap_then_new_target_does_not_snap`: seeds at nx=0.5, drives a 5-second None gap, resumes at nx=1.0, asserts the resume-frame jump is bounded by `vmax * dt_floor`.
- Verified by running the test on the broken version (35° jump observed) and the fixed version (~0° jump observed).
- All 17 pan_controller tests pass.

### WR-01: `Framer._intent_to_target("indeterminate")` branch is unreachable yet retained

**Files modified:** `pastor_tracker/src/pastor_tracker/intent/framer.py`
**Commit:** `bd0a831`
**Applied fix:**
- Removed the `case "indeterminate": return None` arm (dead code; `consume()` filters indeterminate before the call).
- Tightened return type from `float | None` to `float`.
- Removed the now-unnecessary `assert target is not None` at the call site.
- Retained the `case _: raise IntentError(...)` exhaustiveness guard for any future MotionIntent literal extension.
- All 13 framer tests pass.

### WR-02: `MotionAnalyzer._classify` returns stale intent in the dead-band

**Files modified:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py`, `pastor_tracker/tests/test_motion_analyzer.py`
**Commit:** `8343259`
**Applied fix:**
- Reviewer's option (a): documented the sticky semantics in the module docstring under a new "Sticky-intent dead band (WR-02)" section. Captured the rationale (no chatter), the failure mode the design avoids, and the future-escalation path (Config-owned opposite-condition release timer) if field testing reveals ghost moves.
- Added regression test `test_sustained_move_persists_through_dead_band`: matures `moving_right`, runs vx in the dwell..move mid-band for >= 2x dwell_duration_sec, asserts intent persists on every dead-band frame.
- All 14 motion_analyzer tests pass.
- **Status:** fixed (documentation + behavioural test pin; option (b) re-design deferred until field testing demands it).

### WR-03: `MotionAnalyzer._classify` does not clamp negative `dwell_threshold` semantics

**Files modified:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py`
**Commit:** `3a1d3f4`
**Applied fix:**
- Added two tiger-style fail-loud guards in `__init__`:
  - `dwell_threshold_norm_per_sec > 0` (defense-in-depth — Config already enforces `gt=0.0`, but the analyzer is the boundary that uses the threshold).
  - `motion_threshold_norm_per_sec > dwell_threshold_norm_per_sec` (keeps the dead band non-empty; a single vx value otherwise satisfies both move and dwell, making classification non-deterministic).
- Both guards raise `ValueError` with the offending values in the message.
- All 14 motion_analyzer tests pass (the default Config satisfies both invariants).

### WR-04: PanController velocity clamp uses asymmetric clip without preserving damper velocity sign

**Files modified:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py`
**Commit:** `69e51ec`
**Applied fix:**
- Reviewer's option (b): documented the position-only anti-windup contract.
- Added an in-source rationale at the clamp site explaining: (a) the Holden update bleeds residual velocity across subsequent ticks, so the position-only overwrite is enough to bound per-step deltas (proven by `test_velocity_clamp_caps_step_size`), and (b) re-deriving velocity from the clipped delta was rejected because it breaks continuous-time damper physics and would invalidate the existing observable invariant test.
- Updated the module-level D-09 line to cite the in-source comment.
- All 17 pan_controller tests pass.

### WR-05: `# pragma: no cover` masks coverage of dispatcher misbehaviour safety net

**Files modified:** `pastor_tracker/tests/test_command_dispatcher.py`
**Commit:** `9e7f130`
**Applied fix:**
- Replaced `raise AssertionError(...)` with `pytest.fail(...)` (clean failure mode).
- Removed `# pragma: no cover` so coverage truthfully reports the branch (un-hit is the success state).
- Added an in-line comment citing D-13 100% coverage target and explaining why the function being un-hit is correct.
- All 16 dispatcher tests pass.

### WR-06: `valid_config_dict` referenced but not declared as a fixture in this phase

**Files modified:** `pastor_tracker/tests/test_command_dispatcher.py`, `pastor_tracker/tests/test_framer.py`, `pastor_tracker/tests/test_motion_analyzer.py`, `pastor_tracker/tests/test_pan_controller.py`
**Commit:** `dac982e`
**Applied fix:**
- Added a docstring at each of the four Phase-5 single-stage `_make_*` factories citing:
  - the conftest.py-owned shared fixture contract,
  - the dict()-copy-then-override pattern for per-test overrides,
  - the explicit prohibition on in-place mutation (would silently leak state across tests).
- The Phase-5 `_make_pipeline` already had a docstring (out of scope).
- Phase-4 `test_subject_tracker_*` tests are out of Phase-5 scope.
- All 60 affected tests pass.

## Skipped Issues

### WR-07: Unused import in test_command_dispatcher

**File:** `pastor_tracker/tests/test_command_dispatcher.py:18`
**Reason:** Reviewer explicitly states `**Fix:** None required — note for future reference.` The import IS used (by `monkeypatch.setattr(time, "perf_counter_ns", _explode)` inside `test_no_emission_uses_wall_clock`); the warning is a hypothetical false-positive note for any future `ruff --select F401` tightening. No code change is appropriate at this time.
**Original issue:** `import time` at line 18 is used only inside one test; benign, but flagged in case ruff later surfaces a false-positive.

---

_Fixed: 2026-05-05_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
