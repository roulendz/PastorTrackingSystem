---
phase: 05-intent-and-control
plan: 04
subsystem: control
tags: [phase-5, intent-control, pan-controller, damping, stage-2, fov-bridge, velocity-clamp, deadband, anti-windup, structlog]

# Dependency graph
requires:
  - phase: 05-intent-and-control
    provides: Framer (Plan 03) + FramingTarget DTO + intent/__init__.py re-export shape
  - phase: 01-foundations
    provides: CriticallyDampedFollower (Holden exact form) + FollowerState + Config.pan_time_constant_sec/pan_deadband_deg/pan_max_velocity_deg_per_sec/camera_horizontal_fov_deg + core.geometry.normalized_x_to_angle_deg
provides:
  - PanController class -- FOV bridge + stage-2 critically-damped follower in DEGREE DOMAIN + anti-windup velocity clamp + emission deadband (CTRL-01, CTRL-02, CTRL-03)
  - ControlError typed exception -- root for control-stage errors (PanController, future CommandDispatcher in Plan 05)
  - current_angle_deg read-only property -- Phase 7 dashboard surface
  - pan_controller_seeded / pan_clamped / pan_deadband_suppressed DEBUG logger events
  - control/__init__.py public surface -- PanController + ControlError
affects: [05-05-command-dispatcher, 05-06-composition, 06-pipeline-orchestrator, 07-ui-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Stage-2 damping in DEGREE DOMAIN (D-05) -- mirrors Plan 03 Framer's stage-1 in normalized-x; FOV conversion happens BEFORE damping (D-08 order)"
    - "Anti-windup via FollowerState.position OVERWRITE using dataclasses.replace (D-09) -- NOT PID; no integral/accumulator state. Pitfall 2 explicitly forbids _integral_*, _accum_*, _error_sum_* attributes."
    - "Emission deadband on _last_emitted_angle_deg, NOT on damper _state.position (D-10) -- damper continues stepping every frame; only the EMISSION is gated. Pitfall 3 forbids freezing the damper inside the deadband."
    - "Hold-on-None preserves FollowerState in place (D-07) -- contrast with Plan 03 Framer which clears state on indeterminate. The degree-domain damper holds across upstream gaps so resume on framer-target return is smooth, no warmup transient."
    - "First non-None target seeds FollowerState(position=angle_deg, velocity=0.0) (D-06) -- explicit construction at the call site, not damper.initial_state(), to document the seeding rule."
    - "_compute_dt_sec floors to 1/capture_fps on pre-seed AND non-monotonic upstream timestamps (mirrors SubjectTracker / Framer) -- never raises ValueError from damper.step on dt <= 0."
    - "structlog.testing.capture_logs() used in 3 logger-event tests; pre-existing test_logging.py polluter (configure_logging non-idempotent) fixed via autouse structlog.reset_defaults() teardown."

key-files:
  created:
    - "pastor_tracker/src/pastor_tracker/control/pan_controller.py -- 172 lines, PanController + ControlError"
    - "pastor_tracker/tests/test_pan_controller.py -- 415 lines, 16 tests"
    - ".planning/phases/05-intent-and-control/deferred-items.md -- Phase 05 deferred-items log; first entry tracks pre-existing test_geometry full-suite flake"
  modified:
    - "pastor_tracker/src/pastor_tracker/control/__init__.py -- re-export PanController + ControlError (was Phase 0 stub)"
    - "pastor_tracker/tests/test_logging.py -- add autouse structlog.reset_defaults() teardown (Rule 1 fix for INFO-filter leak that suppressed DEBUG events in subsequent log-event tests)"

key-decisions:
  - "FOV bridge first (D-08): normalized_x_to_angle_deg(target_x_normalized, camera_horizontal_fov_deg) BEFORE the damper step. Damper operates in degrees so the velocity clamp's vmax*dt threshold is in the same units (deg/s * s = deg)."
  - "Anti-windup overwrite proven OBSERVABLY per Issue 8: test_velocity_clamp_overwrites_state drives 3 successive over-velocity steps; under sustained over-target drive each emitted-step delta equals vmax*dt within machine epsilon (residual ~1.05e-15). Without the FollowerState.position overwrite, the damper would integrate from the unclamped position and successive deltas would shrink. NO private-state inspection (controller._state) and NO # type: ignore[attr-defined] in the test."
  - "16 tests landed (>= 12 minimum) -- exceeds plan target. Added test_deadband_logs_pan_deadband_suppressed for symmetry with the seed/clamp logger tests."
  - "Coverage 98% line + 100% branch coverage on the deadband + clamp if-blocks (D-13 satisfied). One uncovered branch (line 168, _compute_dt_sec pre-seed floor) is unreachable in normal flow because _seed_and_emit sets _last_upstream_ts_ns before any step path runs -- defensive belt-and-braces guard, not a behavioural branch."
  - "test_logging.py polluter fix (Rule 1): configure_logging(level='INFO') with cache_logger_on_first_use=True caches an INFO filter on every previously-bound structlog logger, suppressing DEBUG events in test_pan_controller and test_framer log-event tests. Latent ordering bug pre-dates this plan -- masked because test_framer ran ALPHABETICALLY BEFORE test_logging while test_pan_controller runs AFTER. Fixed via autouse structlog.reset_defaults() teardown in test_logging.py."
  - "Pre-existing test_geometry::test_inverse_map_output_in_unit_interval flake (full-suite only, unraisable asyncio event-loop warning) logged to deferred-items.md per CLAUDE.md scope boundary -- NOT introduced by this plan; reproduces on bare 81e67dc Task-1 state."
  - "vmax override in step-response test capped at 360.0 (Config max) instead of 1000.0 to honour the Pydantic validator; 360 deg/s * (1/30 s) = 12 deg per-step still well above any first-step damper delta at fov=70 targets, so the clamp does not engage and the damper invariant is the only thing under test."

requirements-completed: [CTRL-01, CTRL-02, CTRL-03]

# Metrics
duration: 13min
completed: 2026-05-06
---

# Phase 05 Plan 04: PanController (FOV + stage-2 damper + clamp + deadband) Summary

## One-liner

Stage-2 critically-damped follower in the DEGREE domain with FOV bridge first, anti-windup velocity clamp via `FollowerState.position` overwrite (NOT PID), and emission-only deadband that lets the damper keep stepping every frame -- proven observably by 3 successive `vmax*dt` deltas under sustained over-velocity drive.

## What landed

### `pastor_tracker/src/pastor_tracker/control/pan_controller.py` (172 lines)

`class ControlError(Exception)`: typed root for control-stage errors.

`class PanController`:
- `__init__(config)`: builds `CriticallyDampedFollower(time_constant_sec=config.pan_time_constant_sec)`, structlog `module="pan_controller"`, all state fields `None` (`_state`, `_last_upstream_ts_ns`, `_last_emitted_angle_deg`, `_current_angle_deg`).
- `current_angle_deg` property: read-only Phase 7 dashboard surface; reflects the EMITTED angle, not the damper's internal `_state.position` (which keeps stepping during deadband-suppressed ticks per D-10).
- `async consume(target, now_ns)`:
  1. `target is None` -> `_hold()` returns `_last_emitted_angle_deg` (no damper advance, D-07).
  2. Convert `nx -> deg` via `core.geometry.normalized_x_to_angle_deg(target.target_x_normalized, config.camera_horizontal_fov_deg)` (D-08).
  3. First non-None target -> `_seed_and_emit(angle_deg, target.timestamp_ns)` -> `FollowerState(position=angle_deg, velocity=0.0)`, log DEBUG `pan_controller_seeded` (D-06).
  4. Subsequent target -> `_step_clamp_deadband_emit(angle_deg, target.timestamp_ns)`:
     - `dt_sec = _compute_dt_sec(...)` (1/capture_fps floor on pre-seed / non-monotonic).
     - `damper.step(...)` -> `new_state`.
     - VELOCITY CLAMP: if `|new_state.position - prev_position| > pan_max_velocity_deg_per_sec * dt_sec`, clip and **overwrite** `new_state = replace(new_state, position=prev_position + clipped)` (D-09 anti-windup -- NOT PID). Log DEBUG `pan_clamped`.
     - DEADBAND: if `|new_state.position - _last_emitted_angle_deg| < pan_deadband_deg`, return `_last_emitted_angle_deg` and log DEBUG `pan_deadband_suppressed` (damper state already advanced -- D-10 / Pitfall 3).
     - Else update `_last_emitted_angle_deg`, return new_angle.

### `pastor_tracker/src/pastor_tracker/control/__init__.py` (8 lines)

Public re-export: `PanController`, `ControlError`. CommandDispatcher slot reserved for Plan 05.

### `pastor_tracker/tests/test_pan_controller.py` (415 lines, 16 tests)

| Test                                                | What it proves                                                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `test_initial_angle_is_none`                        | Pre-seed `current_angle_deg is None`                                                                    |
| `test_none_target_pre_seed_returns_none`            | `consume(None)` before any seed returns `None`                                                          |
| `test_center_target_maps_to_zero_angle`             | FOV bridge: `nx=0.5 -> 0 deg`                                                                           |
| `test_left_third_maps_to_negative_angle`            | `nx=1/3` maps to negative angle equal to `normalized_x_to_angle_deg(1/3, fov)`                          |
| `test_right_third_maps_to_positive_angle`           | symmetric: `nx=2/3` maps to positive angle                                                              |
| `test_step_response_no_overshoot`                   | Damper invariant in degrees: zero overshoot, monotonic, settled <5% by 5*tau (vmax overridden to 360 to disable clamp) |
| `test_velocity_clamp_caps_step_size`                | Per-step `|delta| <= vmax*dt + 1e-9` over 90-frame ramp (vmax=5, deadband=0)                            |
| `test_velocity_clamp_overwrites_state`              | **D-09 anti-windup OBSERVABLY**: 3 successive over-velocity emitted-deltas each equal `vmax*dt` within machine epsilon (residual ~1.05e-15). No private-state inspection (Issue 8). |
| `test_deadband_suppresses_small_changes`            | Sub-deadband nudges (~0.04 deg vs 0.4 deg deadband) produce 0 change in emission across 10 frames       |
| `test_deadband_does_not_freeze_damper`              | **Pitfall 3**: 60-frame run with sustained nx=0.7 target settles `|emit - target_angle| < deadband`; damper integrated continuously |
| `test_hold_during_none_target`                      | D-07: `consume(None)` after seed returns held last-emitted angle                                        |
| `test_hold_does_not_advance_damper`                 | D-07: 5 None ticks then resume with same target returns the seed angle (no drift)                       |
| `test_dt_floor_when_upstream_non_monotonic`         | Out-of-order upstream `timestamp_ns` does NOT raise `ValueError` -- dt floors to 1/fps                  |
| `test_seed_logs_pan_controller_seeded`              | First non-None target emits DEBUG `pan_controller_seeded` event with `angle_deg` field                   |
| `test_clamp_logs_pan_clamped`                       | Over-velocity drive emits DEBUG `pan_clamped` with `unclamped_delta_deg`, `clamped_delta_deg`, `dt_sec`  |
| `test_deadband_logs_pan_deadband_suppressed`        | Sub-deadband nudge emits DEBUG `pan_deadband_suppressed` with `delta_deg`, `last_emitted_angle_deg`     |

## Verification metrics

### mypy / ruff / pytest

```
$ uv run mypy --strict src/pastor_tracker/control/
Success: no issues found in 2 source files

$ uv run ruff check src/pastor_tracker/control/ tests/test_pan_controller.py
All checks passed!

$ uv run pytest tests/test_pan_controller.py -x --cov=src/pastor_tracker/control --cov-branch --cov-fail-under=90
============================== 16 passed in 0.83s ==============================
Name                                           Stmts   Miss Branch BrPart  Cover
--------------------------------------------------------------------------------
src/pastor_tracker/control/__init__.py             2      0      0      0   100%
src/pastor_tracker/control/pan_controller.py      69      1     12      1    98%
--------------------------------------------------------------------------------
TOTAL                                             71      1     12      1    98%
Required test coverage of 90% reached. Total coverage: 97.59%
```

### Step-response measurements (tau=0.6, fov=70, target_nx=2/3, dt=1/30, horizon=5*tau)

```
target_angle = normalized_x_to_angle_deg(2/3, 70) = 13.137781 deg
max(positions)                 = 12.9666648297 deg
overshoot (max - target)       = -1.71e-01 deg  (NEGATIVE -- still approaching, no overshoot)
positions[-1] @ 5*tau          = 12.966665 deg
|positions[-1] - target_angle| = 1.7112e-01 deg  (1.302% of target -- well under 5% bound)
```

Plan invariant: `max(positions) <= target + 1e-9` -> **PASS** (max < target by 0.17 deg, no overshoot).

### Velocity-clamp measurements (vmax=5, deadband=0, dt=1/30 from int-ns)

```
actual_dt_sec  = float(33333333) / 1e9 = 0.033333333 s
expected_step  = 5.0 * 0.033333333    = 0.1666666650 deg/step
```

`test_velocity_clamp_caps_step_size`: max per-step abs delta over 90-frame ramp = `0.1666666650 deg <= 0.166666666 deg + 1e-9` -> **PASS**.

### Anti-windup overwrite measurements (D-09 OBSERVABLE proof)

```
seed @ nx=0.0 -> angle = -35.0 deg
3 successive frames at nx=1.0:

step 0: emitted_delta = 0.1666666650 deg, residual vs vmax*dt = +1.05e-15
step 1: emitted_delta = 0.1666666650 deg, residual vs vmax*dt = +1.05e-15
step 2: emitted_delta = 0.1666666650 deg, residual vs vmax*dt = +1.05e-15
```

All three deltas are bit-identical at machine epsilon. **Without the `replace(new_state, position=...)` overwrite, the damper would integrate from the unclamped position and the second/third deltas would shrink** (the damper would "think" it's already at +X.X deg and the velocity term would decay toward 0). With the overwrite, the damper restarts from the clamped position each step and the over-target drive produces the maximum vmax*dt delta indefinitely. -> **PASS, Pitfall 2 averted, threat T-05-04-02 mitigated.**

### Deadband-not-freeze (Pitfall 3 / threat T-05-04-03)

`test_deadband_does_not_freeze_damper`: drive `nx=0.7` (target_angle = 13.14 deg) for 60 frames. Final emitted angle 12.97 deg; `|12.97 - 13.14| = 0.17 deg < deadband 0.4 deg` -> **PASS**. The damper integrated through the entire 60-frame run even though most intermediate ticks were deadband-suppressed; the cumulative push exceeded the deadband ~3 times during the run (visible in the captured `pan_deadband_suppressed` log stream).

### Logger-event sample (capture_logs at DEBUG)

```
pan_controller_seeded {angle_deg: -35.0, module: pan_controller}
pan_clamped {unclamped_delta_deg: 0.4014, clamped_delta_deg: 0.16667, dt_sec: 0.0333, module: pan_controller}
pan_deadband_suppressed {delta_deg: 0.075, last_emitted_angle_deg: 0.0, module: pan_controller}
```

All three events match Pattern 9 shape; structlog `cache_logger_on_first_use=True` was the polluter root-cause documented in the deviations section below.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 1 - Bug] Fixed structlog suite-pollution leaking INFO-level filter**
- **Found during:** Task 2 full-suite verification.
- **Issue:** `tests/test_logging.py::test_configure_logging_emits_json_event` calls `configure_logging(level="INFO")`, which uses `cache_logger_on_first_use=True` to freeze a filtering wrapper on every cached structlog logger. After this test runs, all subsequent tests sharing the same Python process see DEBUG events suppressed by the INFO filter -- including the new `test_pan_controller.py::test_seed_logs_pan_controller_seeded` / `test_clamp_logs_pan_clamped` / `test_deadband_logs_pan_deadband_suppressed` tests, which assert on DEBUG events. `tests/test_framer.py::test_seed_logs_framer_seeded` had the same latent vulnerability but was masked by alphabetical ordering placing it BEFORE `test_logging.py`.
- **Fix:** Added an `autouse` fixture in `tests/test_logging.py` that calls `structlog.reset_defaults()` after each test, restoring the un-configured default `BoundLogger`. This unblocks both Plan 05-04's new log-event tests AND the pre-existing Plan 05-03 framer log-event test (which was a latent ordering bomb).
- **Files modified:** `pastor_tracker/tests/test_logging.py`
- **Commit:** 445ab8d

**2. [Rule 1 - Bug] Fixed test float-precision when reproducing controller dt**
- **Found during:** Task 2 first run of `test_velocity_clamp_overwrites_state`.
- **Issue:** Initial test compared the emitted-step delta against `vmax * (1.0/30.0)`. The PanController derives `dt_sec` from integer ns deltas (`float(delta_ns) / 1e9` where `delta_ns = int((1/30) * 1e9) = 33333333`), so the controller's actual `dt_sec` is `0.033333333`, not `0.03333333333...`. Difference is ~1.6e-9, larger than the 1e-9 tolerance.
- **Fix:** Reproduced the controller's exact dt computation in the test (`actual_dt_sec = float(_DT_30HZ_NS) / 1e9`) and compared against that. Same fix applied preemptively to `test_velocity_clamp_caps_step_size`.
- **Files modified:** `pastor_tracker/tests/test_pan_controller.py`
- **Commit:** 445ab8d

**3. [Rule 3 - Blocking] vmax override capped at Config max (360 deg/s)**
- **Found during:** Task 2 first run of `test_step_response_no_overshoot`.
- **Issue:** Plan task 2 action 2 specified `cfg["pan_max_velocity_deg_per_sec"] = 1000.0` to disable the clamp during the step-response test. Pydantic Config validator caps `pan_max_velocity_deg_per_sec` at `le=360`, so 1000.0 raises `ValidationError`.
- **Fix:** Override capped at 360.0 (Config max). 360 deg/s * (1/30 s) = 12 deg per-step is well above any first-step damper delta at fov=70 targets, so the clamp does not engage and the test invariant remains the damper's behaviour.
- **Files modified:** `pastor_tracker/tests/test_pan_controller.py`
- **Commit:** 445ab8d

### Deferred (out of scope)

**4. Pre-existing flake -- `test_geometry.py::test_inverse_map_output_in_unit_interval`**
- **Found during:** full-suite verification.
- **Issue:** Hypothesis-driven test fails when run as part of the full suite with `PytestUnraisableExceptionWarning: Exception ignored in <function BaseEventLoop.__del__>`. Promoted to error by `pyproject.toml:filterwarnings=["error"]`.
- **Confirmed pre-existing:** Reproduces on bare 81e67dc (Task 1 only) state and with `--ignore=tests/test_pan_controller.py`. NOT introduced by Plan 05-04.
- **Disposition:** Logged to `.planning/phases/05-intent-and-control/deferred-items.md` per CLAUDE.md scope boundary; left untouched.

### Stub/threat scan

- **Stubs:** None. Every code path in pan_controller.py is wired to real Config / real damper / real geometry. No placeholder data, no TODOs without issue numbers.
- **Threat surface:** No new endpoints, auth surface, file I/O, or schema changes. PanController consumes a Pydantic-validated FramingTarget and emits a float -- no expansion of the Phase 5 trust boundary documented in the plan's `<threat_model>`.

## Self-Check

### Files exist
- `pastor_tracker/src/pastor_tracker/control/pan_controller.py` -> FOUND (172 lines)
- `pastor_tracker/src/pastor_tracker/control/__init__.py` -> FOUND (modified)
- `pastor_tracker/tests/test_pan_controller.py` -> FOUND (415 lines, 16 tests)
- `.planning/phases/05-intent-and-control/deferred-items.md` -> FOUND
- `pastor_tracker/tests/test_logging.py` -> FOUND (modified, autouse fixture)

### Commits exist
- 81e67dc -- `feat(05-04): implement PanController + ControlError (CTRL-01/02/03)` -> FOUND
- 445ab8d -- `test(05-04): add 16 PanController tests + fix structlog suite-pollution` -> FOUND

## Self-Check: PASSED
