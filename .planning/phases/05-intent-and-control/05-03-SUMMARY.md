---
phase: 05-intent-and-control
plan: 03
subsystem: intent
tags: [phase-5, intent-control, framer, rule-of-thirds, damping, stage-1, exhaustive-match, structlog]

# Dependency graph
requires:
  - phase: 05-intent-and-control
    provides: MotionAnalyzer (Plan 02) + MotionState DTO + intent/__init__.py re-export shape
  - phase: 01-foundations
    provides: CriticallyDampedFollower (Holden exact form) + FollowerState + Config.framing_time_constant_sec
provides:
  - Framer class — rule-of-thirds target selection + stage-1 critically-damped smoothing in normalized-x domain (INTENT-03, INTENT-04)
  - IntentError typed exception for unhandled MotionIntent dispatch (WR-08 fix discipline)
  - framing_target_change INFO logger event (gated on discrete intent shift, NOT per damper step)
  - framer_seeded DEBUG logger event on first non-indeterminate motion
  - intent/__init__.py extended public surface — Framer + IntentError alongside MotionAnalyzer
affects: [05-04-pan-controller, 05-06-composition, 06-pipeline-orchestrator, 07-ui-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Stage-1 damping in NORMALIZED-X DOMAIN (D-05) — degree conversion deferred to PanController (Plan 04)"
    - "At-target seeding with FollowerState(position=current_target, velocity=0.0) — no warmup transient toward zero (D-06)"
    - "Hold-on-None / indeterminate clears state so next non-indeterminate motion re-seeds at the new target (D-07 + Pitfall 6)"
    - "Exhaustive match-over-Literal for MotionIntent with case _ raising IntentError — Pattern 6 / WR-08 fix discipline; mypy --strict + runtime guard double layer"
    - "framing_target_change INFO log gated on _last_discrete_target_x_normalized (NOT continuous _current_target_x_normalized) — bounded at <= 1 / motion_hysteresis_sec by Plan 02 hysteresis"
    - "Time source exclusively motion.timestamp_ns / now_ns parameters (D-02) — Framer never reads time.time / time.perf_counter_ns"
    - "_compute_dt_sec floors to 1/capture_fps when upstream ts absent or non-monotonic — protects damper.step from dt <= 0 ValueError (T-05-03-06)"

key-files:
  created:
    - "pastor_tracker/src/pastor_tracker/intent/framer.py — 156 lines, Framer + IntentError"
    - "pastor_tracker/tests/test_framer.py — 361 lines, 13 tests"
  modified:
    - "pastor_tracker/src/pastor_tracker/intent/__init__.py — re-export Framer + IntentError alongside Plan 02's MotionAnalyzer"

key-decisions:
  - "current_target_x_normalized property exposes continuous clamped damper position (Phase 7 dashboard surface); _last_discrete_target_x_normalized is the private gate for the framing_target_change INFO log (one log per intent flip, not per frame)"
  - "Re-acquisition strategy: clear ALL Framer state on indeterminate / None ticks (Pitfall 6 recommended implementation) — _state, _last_upstream_ts_ns, _current_target_x_normalized, _last_discrete_target_x_normalized all null. Re-arms log gate cleanly on re-acquire."
  - "test_consume_does_not_read_wall_clock patches only time.perf_counter_ns + time.time (Plan 02 precedent) — NOT time.monotonic, which the Windows asyncio proactor loop calls internally on close (asyncio infrastructure, not Framer code)"
  - "13 tests landed (>= 11 minimum) — added test_step_response_no_overshoot_left_to_right (symmetric to right-to-left) for full directional coverage of the monotonic-from-rest invariant"
  - "Coverage 91% on framer.py vs >= 90% target — uncovered lines are case _ raise IntentError (mypy-unreachable per Issue 7) and the first-call branch of _compute_dt_sec (seed path returns before _compute_dt_sec is called, so _last_upstream_ts_ns is set whenever _compute_dt_sec runs)"

requirements-completed: [INTENT-03, INTENT-04, TEST-03]

# Metrics
duration: 5min
completed: 2026-05-06
---

# Phase 05 Plan 03: Framer (stage-1 rule-of-thirds + damping) Summary

## One-liner

Rule-of-thirds framer mapping `MotionIntent` to a normalized-x target in `{1/3, 0.5, 2/3}` and smoothing the discrete transitions through `CriticallyDampedFollower(time_constant_sec=config.framing_time_constant_sec)` — at-target seeding (D-06), state-cleared hold-on-None (D-07), exhaustive match with typed `IntentError` raise.

## What landed

- `pastor_tracker/src/pastor_tracker/intent/framer.py` (156 lines) — `Framer` class + `IntentError` typed exception. Public async `consume(motion: MotionState | None, now_ns: int) -> FramingTarget | None` mirrors `MotionAnalyzer.consume` and `SubjectTracker.consume` for orchestrator symmetry (D-01).
- `pastor_tracker/src/pastor_tracker/intent/__init__.py` — extended (NOT clobbered) Plan 02's `MotionAnalyzer` re-export to also surface `Framer` + `IntentError`. `__all__ = ["Framer", "IntentError", "MotionAnalyzer"]`.
- `pastor_tracker/tests/test_framer.py` (361 lines, 13 tests) — 91% line + branch coverage on framer.py:
  - `tests/test_framer.py::test_initial_target_is_none`
  - `tests/test_framer.py::test_none_motion_returns_none_and_clears_state`
  - `tests/test_framer.py::test_indeterminate_motion_returns_none_and_clears_state`
  - `tests/test_framer.py::test_moving_right_yields_left_third`
  - `tests/test_framer.py::test_moving_left_yields_right_third`
  - `tests/test_framer.py::test_dwelling_yields_center`
  - `tests/test_framer.py::test_step_response_no_overshoot_right_to_left`
  - `tests/test_framer.py::test_step_response_no_overshoot_left_to_right`
  - `tests/test_framer.py::test_hold_during_none_upstream_then_reseed_at_new_target`
  - `tests/test_framer.py::test_non_monotonic_upstream_uses_dt_floor`
  - `tests/test_framer.py::test_seed_logs_framer_seeded`
  - `tests/test_framer.py::test_framing_target_change_logged_on_discrete_shift`
  - `tests/test_framer.py::test_consume_does_not_read_wall_clock`

## Verification snapshot

```
$ uv run mypy --strict src/pastor_tracker/intent/framer.py
Success: no issues found in 1 source file

$ uv run mypy --strict src/pastor_tracker/intent/
Success: no issues found in 4 source files

$ uv run ruff check src/pastor_tracker/intent/ tests/test_framer.py
All checks passed!

$ uv run pytest tests/test_framer.py -x --cov=src/pastor_tracker/intent --cov-branch --cov-report=term-missing
13 passed in 0.90s

src\pastor_tracker\intent\framer.py    78    5    18    2   91%   143-146, 152
```

Step-response measurements at `tau = config.framing_time_constant_sec = 0.8 s`, `dt = 1/30 s`, horizon = 120 steps (`5 * tau / dt`):

| Metric                    | Value         | Bound (plan)         |
|---------------------------|---------------|----------------------|
| `max(positions) - target` | `-1.665e-04` | `<= 1e-9` (no overshoot, since position approaches but never reaches target — overshoot would be a positive max-minus-target) |
| `positions[5*tau] - target` | `-1.665e-04` | `< 0.0333` (5% of `2/3`) |
| `min(step_delta)`         | `+1.310e-05` | `>= -1e-9` (monotonic) |

`framing_target_change` INFO emission count over the suite:
- `test_framing_target_change_logged_on_discrete_shift` -> 2 events (left-third -> center, center -> right-third)
- `test_step_response_no_overshoot_right_to_left` -> 1 event (left-third -> right-third on the post-seed step)
- `test_step_response_no_overshoot_left_to_right` -> 1 event (right-third -> left-third on the post-seed step)
- `test_hold_during_none_upstream_then_reseed_at_new_target` -> 0 (re-seed path does not log change; only step-path does)
- `test_non_monotonic_upstream_uses_dt_floor` -> 1 event (post-seed flip moving_right -> moving_left)
- `test_consume_does_not_read_wall_clock` -> 2 events (moving_right -> dwelling, dwelling -> moving_left)
- Total: ~7 INFO emissions across 13 tests, all bounded by exactly one event per discrete intent flip per Pattern 9.

## Acceptance checklist

- [x] `framer.py` exists, 156 lines (>= 110)
- [x] `class Framer` count = 1, `class IntentError` count = 1
- [x] `async def consume` count = 1
- [x] `match intent` + `case _:` + `raise IntentError` all present
- [x] `framing_target_change` referenced in source (>= 1)
- [x] `_last_discrete_target_x_normalized` count = 6 (>= 4: init + reset + seed-set + step-compare + step-update + step-set)
- [x] No `pass` smell, no `time.time/time.perf_counter/datetime.now/asyncio.sleep` in framer.py
- [x] Magic-number leak (`0.8`) absent from non-comment code in both source and tests
- [x] `mypy --strict src/pastor_tracker/intent/framer.py` exits 0
- [x] `ruff check src/pastor_tracker/intent/framer.py` exits 0
- [x] `from pastor_tracker.intent import Framer, IntentError, MotionAnalyzer` succeeds
- [x] `test_framer.py` exists, 361 lines (>= 230)
- [x] 13 `def test_` (>= 11)
- [x] `pytest tests/test_framer.py -x` reports 13 passed, 0 failed
- [x] >= 90% line coverage on framer.py (actual 91%)
- [x] `unittest.mock` / `MagicMock` / `AsyncMock` count = 0 (TEST-05)
- [x] `structlog.testing.capture_logs` count = 2
- [x] `framing_target_change` count in tests = 6 (Pattern 9 reinforcement)
- [x] `pytest.raises(IntentError` count = 0 (Issue 7 — removed; mypy + runtime guard cover the invariant)
- [x] `no overshoot|MONOTONIC|SETTLED` count in tests = 8

## Threat surface scan

No new security-relevant surface introduced in this plan. The Framer is a pure transform on validated DTOs (Pydantic frozen models with Literal/Field validation upstream); no network endpoints, no file access, no auth paths. Threat register T-05-03-01 .. T-05-03-08 are all addressed by mitigations or accepted with documented bounded cost — see plan `<threat_model>` block.

## Deviations from Plan

**1. [Rule 1 - Bug] test_consume_does_not_read_wall_clock — over-zealous monkeypatch surface**

- **Found during:** Task 2 first test run.
- **Issue:** Initial implementation patched the entire wall-clock surface (`time.perf_counter_ns`, `time.perf_counter`, `time.monotonic_ns`, `time.monotonic`, `time.time`, `time.time_ns`). Test failed because `asyncio.run(...)` on Windows calls `time.monotonic()` internally during proactor-loop close — not Framer code.
- **Fix:** Reduced the patched surface to `time.perf_counter_ns` + `time.time` to match the Plan 02 D-02 precedent (`tests/test_motion_analyzer.py::test_no_emission_reads_wall_clock`). Added explicit per-call assertions on the consume() return values inside the test body so accidental short-circuit returns can't mask a regression.
- **Files modified:** `pastor_tracker/tests/test_framer.py` (only — pre-commit, before Task 2 commit landed).
- **Commit:** `57412b9` (Task 2 — fix folded into the initial commit, no separate fix commit).

**2. [Rule 3 - Build environment] uv not on PATH**

- **Found during:** First mypy / ruff invocation.
- **Issue:** `uv` not found via `where uv`; PATH does not include the venv `Scripts/` dir.
- **Fix:** Prepended `D:/System/Documents/PastorTrackingSystem/.venv/Scripts` to PATH for each Bash invocation. No source changes.
- **Files modified:** none.
- **Commit:** none (process workaround only).

## TDD Gate Compliance

- Task 1 plan-frontmatter `tdd="true"`: implementation landed in commit `e33137d` (`feat(05-03): implement Framer + IntentError`).
- Task 2 plan-frontmatter `tdd="true"`: tests landed in commit `57412b9` (`test(05-03): add 13 framer tests`).

The plan structures Task 1 as the implementation (smoke-import gate) and Task 2 as the property/invariant test suite. The full RED -> GREEN gate sequence is therefore composite: Task 2 contains the failing-test-first invariant work that Task 1's implementation must satisfy. All 13 Task 2 tests passed against the Task 1 implementation on first execution (after the wall-clock-test patch-surface fix noted in Deviations §1) — the mathematical invariants (no overshoot, monotonic, settled-by-5*tau) are inherited from Phase 1's `test_damping.py` discipline and are wired through to the Framer's emitted `FramingTarget` sequence.

## Self-Check: PASSED

Created files (verified `[ -f ... ]`):

- `pastor_tracker/src/pastor_tracker/intent/framer.py` -> FOUND (156 lines)
- `pastor_tracker/tests/test_framer.py` -> FOUND (361 lines)

Modified files (verified via `git diff --stat`):

- `pastor_tracker/src/pastor_tracker/intent/__init__.py` -> FOUND (extended, not clobbered)

Commits (verified via `git log --oneline`):

- `e33137d` feat(05-03): implement Framer + IntentError -> FOUND
- `57412b9` test(05-03): add 13 framer tests -> FOUND
