---
phase: 05-intent-and-control
reviewed: 2026-05-05T00:00:00Z
depth: standard
iteration: 2
files_reviewed: 14
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/control/__init__.py
  - pastor_tracker/src/pastor_tracker/control/command_dispatcher.py
  - pastor_tracker/src/pastor_tracker/control/pan_controller.py
  - pastor_tracker/src/pastor_tracker/intent/__init__.py
  - pastor_tracker/src/pastor_tracker/intent/framer.py
  - pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py
  - pastor_tracker/tests/fixtures/test_trajectories.py
  - pastor_tracker/tests/fixtures/trajectories.py
  - pastor_tracker/tests/test_command_dispatcher.py
  - pastor_tracker/tests/test_framer.py
  - pastor_tracker/tests/test_intent_control_pipeline.py
  - pastor_tracker/tests/test_logging.py
  - pastor_tracker/tests/test_motion_analyzer.py
  - pastor_tracker/tests/test_pan_controller.py
findings:
  blocker: 0
  warning: 0
  total: 0
status: clean
---

# Phase 5: Code Review Report (Iteration 2)

**Reviewed:** 2026-05-05
**Depth:** standard
**Iteration:** 2 (re-review of iteration-1 fixes)
**Files Reviewed:** 14
**Status:** clean

## Summary

All seven iteration-1 findings (BL-01, WR-01..WR-06, WR-07) have been correctly
resolved. A fresh adversarial pass over the changed files surfaced no new
defects. Engineering hygiene continues to be strong: every threshold flows
through `Config`, no wall-clock reads exist outside the orchestrator-supplied
`now_ns`, no mocks of damping/Kalman math, and Tiger-style fail-loud guards
have been added at the boundary that previously degraded silently.

The Phase-5 code path is ready to ship.

## Verification of Iteration-1 Fixes

### BL-01 -- PanController hold-on-None dt blowup -- RESOLVED

**File:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py:167-188`

`_hold()` now sets `self._last_upstream_ts_ns = None` and returns the held
emission. The docstring captures the precise rationale (decay collapse +
clamp ceiling inflation) and explicitly contrasts the cleared upstream
witness against the intentionally retained `_state` damper field.

Regression test landed at `tests/test_pan_controller.py:348-381`
(`test_long_none_gap_then_new_target_does_not_snap`): seeds at nx=0.5
(0 deg), feeds a 5 s None gap, then resumes at nx=1.0 (+35 deg) and asserts
`abs(resumed - seed) <= vmax * (1/capture_fps) + tol`. With default config
this gives a 1.0 deg upper bound; the actual one-step delta integrates to
~0.19 deg through the damper -- a comfortable margin. The pre-fix code path
would have produced ~35 deg in one frame.

I traced the fix end-to-end: after `_hold()` clears the timestamp, the
resume call enters `_step_clamp_deadband_emit` (because `_state` is
preserved), which calls `_compute_dt_sec`. With `_last_upstream_ts_ns=None`
the function returns `1.0 / max(capture_fps, 1) = 1/30`, exactly the
expected behaviour. After the resume tick, line 152 re-arms
`_last_upstream_ts_ns` from the actual resume timestamp, so subsequent
frames compute dt normally.

### WR-01 -- Dead `case "indeterminate"` arm -- RESOLVED

**File:** `pastor_tracker/src/pastor_tracker/intent/framer.py:132-148`

The unreachable arm has been removed. `_intent_to_target` now returns
`float` (no Optional), and the `case _: raise IntentError(...)` provides
the Tiger-style fail-loud guard for any future `MotionIntent` literal that
isn't propagated. The `assert target is not None` at the old line 83 was
correctly removed (the new return type makes the assert tautological).
`IntentError` is exported from `intent/__init__.py:8,11`.

The `consume()` filter at framer.py:78 still short-circuits
`indeterminate`, so `case _:` is only reachable if a future contributor
adds a fifth `MotionIntent` literal without updating the dispatcher --
exactly the regression this guard is designed to surface.

### WR-02 -- Sticky-intent dead-band documentation + regression test -- RESOLVED

**File:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py:17-28`,
`pastor_tracker/tests/test_motion_analyzer.py:296-354`

A dedicated module-docstring section ("Sticky-intent dead band (WR-02)")
now documents the persistence semantics in the `dwell_thr <= |vx| <=
move_thr` band, including the conditions under which the intent flips back
(opposite-direction or dwell timer maturing). The test file's
`test_sustained_move_persists_through_dead_band` matures `moving_right`,
then drives the arithmetic-midpoint dead-band vx for `2 * dwell_duration`
frames and asserts every emitted intent stays `moving_right`. The
docstring reference back to the test ensures any future change to the
release path is forced to update both sides.

### WR-03 -- Threshold-validity guards in `__init__` -- RESOLVED

**File:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py:73-97`

Two fail-loud constructor guards now reject (a) `dwell_threshold <= 0`
(would silently disable dwell classification) and (b)
`motion_threshold <= dwell_threshold` (would collapse the dead band and
make a single vx satisfy both `> move_thr` and `< dwell_thr`,
non-deterministic). Error messages name both fields with the offending
values. The guards are defensive duplicates of Pydantic `gt=0` constraints
at the analyzer boundary, exactly the Tiger-style discipline CLAUDE.md
rule 1 prescribes.

### WR-04 -- Velocity-clamp velocity-state semantics -- RESOLVED

**File:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py:14-18,124-139`

Two complementary anchors now make the design choice explicit:
1. Module docstring D-09 expanded: "FollowerState.velocity is NOT
   overwritten -- the Holden update bleeds residual energy across
   subsequent steps".
2. Inline block comment at the clamp site cites the test that pins the
   observable invariant
   (`test_velocity_clamp_caps_step_size`/`test_velocity_clamp_overwrites_state`)
   and explains why the alternative (re-deriving velocity from clipped
   delta) was rejected (introduces a discontinuity at the clamp boundary).

A future maintainer cannot "fix" the apparent inconsistency without
invalidating both the docstring and the test -- the right tripwire.

### WR-05 -- `# pragma: no cover` replaced with `pytest.fail` -- RESOLVED

**File:** `pastor_tracker/tests/test_command_dispatcher.py:300-306`

The `_explode` body now calls `pytest.fail(...)`. The pragma is gone, so
coverage reports truthfully reflect that this branch is un-hit (success
state -- dispatcher never reads `time.perf_counter_ns`). The inline
comment reaffirms that an un-hit branch is the SUCCESS state for this
test, not a coverage gap.

### WR-06 -- `_make_*` factory contracts documented -- RESOLVED

**Files:** `tests/test_command_dispatcher.py:37-47`,
`tests/test_framer.py:41-47`,
`tests/test_motion_analyzer.py:48-54`,
`tests/test_pan_controller.py:58-64`

Every `_make_*` factory now carries a docstring spelling out:
1. `valid_config_dict` is a SHARED conftest fixture.
2. Tests requiring overrides MUST `dict(...)`-copy first.
3. NEVER mutate the fixture in place (would silently leak state across
   tests in the same module).

I scanned the four test modules and confirmed every override site
(e.g. `test_pan_controller.py:141-142`,`183-185`,`218-220`,`424-426`;
`test_intent_control_pipeline.py:195-196`) uses `dict(valid_config_dict)`
before mutating, never `valid_config_dict.update(...)`. The discipline
holds.

### WR-07 / WR-08 (deep-review addendum) -- RESOLVED

`test_command_dispatcher.py:18`'s `import time` is still legitimately used
(monkeypatch target). The `case _:` exhaustiveness guard added for WR-08
is in place at framer.py:141-148 and motion_analyzer.py validation
parallels the same fail-loud discipline.

## New Findings (Iteration 2)

None. The fixes did not introduce any new BLOCKER or WARNING-grade
defects.

I specifically traced the following risk surfaces for regressions:

1. **`PanController._hold()` interaction with `current_angle_deg`** --
   `_hold()` does not update `_current_angle_deg`, but the property still
   matches `_last_emitted_angle_deg` because both fields were synced in the
   prior `_step_clamp_deadband_emit` /  `_seed_and_emit` call. The
   pipeline test (`test_none_upstream_clean_propagation:312`) pins this
   equivalence. No defect.

2. **`MotionAnalyzer.__init__` ordering** -- validation raises BEFORE any
   instance fields are written, so a failed construction leaves no
   partially-initialized analyzer. Correct.

3. **`Framer._intent_to_target` mypy exhaustiveness** -- with the
   indeterminate arm removed, mypy may flag `case _:` as redundant under
   strict-Literal narrowing; however the runtime guard is intentional
   (Tiger-style fail-loud for future `MotionIntent` extensions). No
   `# type: ignore` was added, suggesting mypy --strict is happy. No
   defect.

4. **Dispatcher delta-gate boundary semantics** -- `<= min_delta`
   suppression preserves the docstring's "emit iff |delta| > min_delta"
   contract. Test `test_delta_at_threshold_does_not_emit` pins the strict
   `>` semantic. No defect.

5. **Damper position un-clamping in Framer** -- `_step_and_emit` clamps
   the EMITTED `target_x_normalized` to `[0,1]` but leaves
   `_state.position` unclamped. Critically-damped 2nd-order followers
   provably do not overshoot a target in `[1/3, 2/3]`, so the unclamped
   internal state stays bounded. No defect.

6. **`test_pre_seed_pipeline_emits_correctly_first_frame`** -- a single
   above-threshold frame on a fresh pipeline correctly produces no motor
   command (hysteresis duration > 0 forces multiple frames before the
   timer matures). Verified.

7. **`test_long_none_gap_then_new_target_does_not_snap` arithmetic** --
   default config: `vmax=30 deg/s`, `capture_fps=30`, `dt_floor=1/30 s`,
   `max_jump = 1.0 deg + tol`. Holden integrator one-step delta from
   position=0 toward target=+35 deg with tau=0.6 and dt=1/30 is
   ~0.19 deg, well under the bound. Test math is sound.

## Engineering Culture Audit (CLAUDE.md compliance)

- **No `print()`** -- all stages use `structlog.get_logger(...)`.
- **No bare `except:`** -- only typed `IntentError`/`ControlError` raised
  at boundaries; no swallowing.
- **No globals** -- only module-level `Final` constants in source.
- **No `time.sleep()` in main loop** -- N/A; Phase 5 is pure transforms.
- **No magic numbers** -- every threshold owned by `Config`; the few
  literals (`1/3`, `1/2`, `2/3`, `_NS_PER_SEC`) are mathematical, not
  tunable; `_CAPTURE_FPS_FLOOR=1` is a defensive lower bound.
- **No commented-out code** -- clean.
- **No TODO without issue number** -- none present.
- **No mocked Kalman/damping in tests** -- tests drive real
  `CriticallyDampedFollower` and real `normalized_x_to_angle_deg`.
- **Type hints everywhere** -- no `Any`; `MotionIntent` Literal sharpens
  contracts.
- **Immutable data** -- `FollowerState`/`MotionState`/`FramingTarget`/
  `MotorCommand` are frozen.
- **Functional code** -- every per-frame method is a pure transform on
  typed DTOs; the only state held is the per-stage damper / hysteresis
  bookkeeping, mutated only through deliberate writes.

---

_Reviewed: 2026-05-05_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Iteration: 2_
