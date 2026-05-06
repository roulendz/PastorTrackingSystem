---
phase: 05-intent-and-control
plan: 02
subsystem: intent
tags: [phase-5, intent-control, motion-analyzer, hysteresis, schmitt-trigger, sustained-crossing, structlog]

# Dependency graph
requires:
  - phase: 04-perception
    provides: TrackedSubject frozen Pydantic DTO (track_id, x/y normalized, vx/vy, timestamp_ns)
  - phase: 05-intent-and-control
    provides: tests/fixtures/trajectories.py (step, ramp, dwell_then_walk, borderline_chatter)
provides:
  - MotionAnalyzer class — Phase 5 sustained-velocity + dwell hysteresis classifier
  - MotionState emission contract for Plan 03 (Framer) and Plan 06 (orchestrator)
  - intent_change INFO logger event with old/new/vx (and reason on None-upstream)
affects: [05-03-framer, 05-06-composition, 06-pipeline-orchestrator, 07-ui-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure async transform mirrors Phase 4 SubjectTracker.consume() shape (D-01)"
    - "Per-direction crossing-timer + dwell-timer state machine; un-cross resets timer (D-03)"
    - "Move-priority-over-dwell tiebreak in _classify resolution order"
    - "Time source exclusively subject.timestamp_ns / now_ns parameters (D-02) — analyzer never reads time.time / time.perf_counter_ns"
    - "structlog.testing.capture_logs for log-event assertion in tests"

key-files:
  created:
    - "pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py — 162 lines, MotionAnalyzer class"
    - "pastor_tracker/tests/test_motion_analyzer.py — 448 lines, 13 tests"
  modified:
    - "pastor_tracker/src/pastor_tracker/intent/__init__.py — re-export MotionAnalyzer"

key-decisions:
  - "intent_change log fields: old, new, vx — Pattern 9 baseline (sustained_for_sec deferred; not requested by Phase 7 dashboard yet)"
  - "None-upstream branch logs intent_change with reason=upstream_none INSTEAD of vx — disambiguates the path source for the dashboard"
  - "Test ramp start coords moved to 0.25 / 0.75 (not 0.3 / 0.7) to avoid magic-number grep collision with motion_hysteresis_sec default of 0.3 — semantic identity preserved"
  - "consume() returns MotionState | None (not MotionState) to mirror Phase 4 SubjectTracker.consume signature for orchestrator symmetry; in Phase 5 the None branch is unreachable but the type matches"
  - "13 tests landed (one above the ≥10 minimum) — split the None-after-lock case into two: timer-reset semantics vs reason-field log-shape, since the two assertions have different failure modes"

requirements-completed: [INTENT-01, INTENT-02, TEST-03]

# Metrics
duration: 5min
completed: 2026-05-06
---

# Phase 05 Plan 02: MotionAnalyzer Hysteresis Classifier Summary

**Sustained-velocity + dwell hysteresis classifier (Phase 5 INTENT-01, INTENT-02) that turns Phase 4 TrackedSubject into Phase 5 MotionState, with per-direction crossing timers, move-priority-over-dwell tiebreak, and full wall-clock prohibition.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-06T09:12:42Z
- **Completed:** 2026-05-06T09:17:57Z
- **Tasks:** 2
- **Files created:** 2 (motion_analyzer.py, test_motion_analyzer.py)
- **Files modified:** 1 (intent/__init__.py)

## Accomplishments

- `intent/motion_analyzer.py` (162 lines, MotionAnalyzer class) — implements the locked D-01..D-04 contract:
  - `async def consume(subject, now_ns) -> MotionState | None` mirrors Phase 4 SubjectTracker.consume shape (D-01)
  - Per-direction crossing timers (`_first_right_crossing_ts_ns`, `_first_left_crossing_ts_ns`) with un-cross reset (D-03)
  - Dwell timer (`_dwell_start_ts_ns`); move-priority-over-dwell tiebreak in `_classify`
  - `consume(None, now_ns)` resets ALL three timers and emits `MotionState(intent="indeterminate", sustained_velocity_x_norm_per_sec=0.0, timestamp_ns=now_ns)` (D-04)
  - Time source is exclusively `subject.timestamp_ns` (timer arithmetic) and `now_ns` (emitted DTO timestamp); analyzer never reads `time.time()` or `time.perf_counter_ns()` (D-02)
  - Public read-only `current_intent` property exposes state for Phase 7 dashboard
  - Initial `_current_intent = "indeterminate"` before any sustained crossing
- `intent/__init__.py` updated to re-export `MotionAnalyzer` (mirrors `perception/__init__.py` pattern)
- `tests/test_motion_analyzer.py` (448 lines, 13 tests) — 100% line + 100% branch coverage on `motion_analyzer.py`:
  - `test_initial_intent_is_indeterminate` (initial state)
  - `test_none_upstream_emits_indeterminate` (D-04 happy path)
  - `test_sustained_right_flips_intent` (INTENT-01)
  - `test_sustained_left_flips_intent` (INTENT-01)
  - `test_sustained_dwell_flips_intent` (INTENT-01)
  - `test_borderline_chatter_no_thrash` (INTENT-02 firewall)
  - `test_un_cross_resets_timer` (D-03 reset semantics)
  - `test_threshold_un_cross_then_resustain` (D-03 fresh-timer-after-reset)
  - `test_intent_change_emits_log_event` (Pattern 9 logging)
  - `test_none_upstream_after_lock_logs_reason_field` (D-04 log shape)
  - `test_none_after_locked_resets_timers` (D-04 timer-reset semantics)
  - `test_dt_independence` (duration-based, not frame-based — sample-rate invariance)
  - `test_no_emission_reads_wall_clock` (D-02 monkeypatch assertion)

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement MotionAnalyzer + intent/__init__.py re-export** — `1ffdd76` (feat)
2. **Task 2: Unit tests for MotionAnalyzer (INTENT-01, INTENT-02, TEST-03)** — `21634e3` (test)

**Plan metadata commit:** to be created next (docs: complete plan).

## Files Created/Modified

- `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py` — 162 lines, single class, no helper module split
- `pastor_tracker/src/pastor_tracker/intent/__init__.py` — re-exports `MotionAnalyzer`
- `pastor_tracker/tests/test_motion_analyzer.py` — 448 lines, 13 tests

## Verification Results

**Task 1 acceptance:**
- `wc -l src/pastor_tracker/intent/motion_analyzer.py` = 162 (≥ 90 required)
- `grep -c "class MotionAnalyzer"` = 1
- `grep -c "async def consume"` = 1 (after removing one docstring duplicate)
- Wall-clock-read grep `(time\.time|time\.perf_counter|datetime\.now|asyncio\.sleep)` = 0
- Magic-number grep `{0.08, 0.3, 0.03, 1.5}` non-comment lines = 0
- `uv run mypy --strict src/pastor_tracker/intent/motion_analyzer.py` → Success: no issues found in 1 source file
- `uv run ruff check src/pastor_tracker/intent/` → All checks passed!
- Smoke import + None-upstream invocation → prints `ok`

**Task 2 acceptance:**
- `wc -l tests/test_motion_analyzer.py` = 448 (≥ 200 required)
- `grep -c "def test_"` = 13 (≥ 10 required)
- `uv run pytest tests/test_motion_analyzer.py -x` → **13 passed in 0.96s**
- Coverage: `src\pastor_tracker\intent\motion_analyzer.py 59 stmts 0 miss 24 branch 0 brpart 100%`
- `uv run ruff check tests/test_motion_analyzer.py` → All checks passed!
- Magic-number grep `{0.08, 0.3, 0.03, 1.5}` non-comment lines = 0 (after the 0.3→0.25 / 0.7→0.75 refactor)
- `unittest.mock | MagicMock | AsyncMock` import grep = 0
- `valid_config_dict: dict` parameter count = 14 (≥ 8 required; covers all 13 tests + helper)
- `structlog.testing.capture_logs` occurrences = 2 (transition test + reason-field test)

**Final verification gates (plan ``<verification>`` block):**
- `pytest tests/test_motion_analyzer.py -x --cov=src/pastor_tracker/intent --cov-branch --cov-report=term-missing` → 13 passed, 100% line + 100% branch on motion_analyzer.py
- `mypy --strict src/pastor_tracker/intent/` → Success: no issues found in 2 source files
- `ruff check src/pastor_tracker/intent/ tests/test_motion_analyzer.py` → All checks passed!
- `python -c "from pastor_tracker.intent import MotionAnalyzer"` → exits 0

## Decisions Made

- **`intent_change` log fields baseline:** kept Pattern 9's minimal `{old, new, vx}` for the normal flow and added `reason="upstream_none"` only on the D-04 None-upstream branch (where `vx` would be a synthetic 0.0 unrelated to the actual transition trigger). Did NOT add the optional `sustained_for_sec` field — Phase 7 dashboard hasn't requested it; can be retrofitted as a non-breaking key when a UI panel needs it.
- **Test ramp start-x adjusted from 0.3/0.7 → 0.25/0.75:** the bare literal `0.3` collided with `motion_hysteresis_sec=0.3` in the magic-number guard grep. Position-coord `0.3` is a fixture parameter (not a Phase-5 threshold), but the grep can't distinguish; changing the start coord preserves semantic identity and keeps the guard signal trustworthy.
- **No helper module split:** analyzer landed at 162 lines; CONTEXT.md "Claude's Discretion" allowed splitting a `_hysteresis.py` only past ~250 lines. Single class is more readable.
- **No `match` statement in `_classify`:** the dispatch is by threshold region (numeric ranges), not by enum value — straight-line `if` blocks are clearer and avoid the Pattern 6 exhaustiveness boilerplate. Phase 4 `SubjectTracker._tick_*` uses `match` because the dispatch IS by `_LockState` enum; that's the right tool for that situation, not this one.
- **`consume()` return type stayed `MotionState | None`** (not just `MotionState`): mirrors Phase 4 `SubjectTracker.consume` for orchestrator symmetry (D-01). In Phase 5 the None branch is unreachable, but the type alignment matters for `Pipeline` composition in Plan 06.
- **Test count: 13 (one over the ≥10 minimum)** — split "None-after-lock" into two tests: `test_none_upstream_after_lock_logs_reason_field` (log shape) and `test_none_after_locked_resets_timers` (timer-reset semantics). The two assertions have different failure modes; combining them would mask one failure behind the other.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Acceptance grep `async def consume` returned 2 instead of 1**
- **Found during:** Task 1 acceptance verification.
- **Issue:** The class docstring had a literal reference to `async def consume(...)` describing the public surface, which the strict acceptance grep counted as a definition site (returns must be 1).
- **Fix:** Replaced the docstring's `async def consume` reference with `consume`, preserving the description. Definition count dropped to 1.
- **Files modified:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py`
- **Verification:** mypy + ruff still green; smoke import still works.
- **Committed in:** `1ffdd76` (Task 1 commit — fix landed before commit).

**2. [Rule 1 — Bug] Magic-number grep collision: ramp start coord 0.3 collided with motion_hysteresis_sec default 0.3**
- **Found during:** Task 2 acceptance verification.
- **Issue:** `_RAMP_X_START_RIGHT = 0.3` (a position coordinate, not a threshold) made the magic-number guard grep fire on Phase-5 threshold value `motion_hysteresis_sec=0.3`.
- **Fix:** Changed start coords to `_RAMP_X_START_RIGHT = 0.25` / `_RAMP_X_START_LEFT = 0.75`. Semantic identity preserved (any value in `(0, 0.5)` works for "left of midline"). Tests still pass; coverage still 100%.
- **Files modified:** `pastor_tracker/tests/test_motion_analyzer.py`
- **Verification:** 13/13 pytest pass; 100% line + 100% branch coverage; magic-number grep returns 0.
- **Committed in:** `21634e3` (Task 2 commit — fix landed before commit).

---

**Total deviations:** 2 auto-fixed (both Rule 1 grep-acceptance bugs in pre-commit verification; no semantics changed).
**Impact on plan:** None — both fixes landed inside their respective tasks before commit.

## Issues Encountered

- `uv run pytest --cov=src/pastor_tracker/intent/motion_analyzer` (the plan's literal verify command) failed with `coverage: Module src/pastor_tracker/intent/motion_analyzer was never imported` because pytest-cov treats the value as both a path AND a module name — the file-path resolution succeeds but coverage attaches to the module-name slot. Switching to `--cov=src/pastor_tracker/intent` (the directory) made coverage attach correctly and report 100% on `motion_analyzer.py`. Documenting here for the verifier; the plan's verify command should be updated in a follow-up.
- `uv` not on global PATH on this Windows machine (same as Plan 01); resolved by invoking `D:\System\Documents\PastorTrackingSystem\.venv\Scripts\uv.exe` directly.

## TDD Gate Compliance

Plan frontmatter type is `execute` (not `tdd`), so plan-level RED→GREEN→REFACTOR gate enforcement does not apply. Per-task `tdd="true"` flags in the plan describe per-task discipline; both task commits follow the plan-prescribed order (Task 1 = `feat` for production code, Task 2 = `test` for tests). The implementation landed before the test commit because both tasks share a single class as the contract under test, and the plan explicitly orders them this way (Task 1's verify already includes a smoke-test for None-upstream — a minimum vital sign — before the full test suite arrives in Task 2).

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Plan 03 (Framer) can `from pastor_tracker.intent import MotionAnalyzer` — public surface is stable.
- Plan 03 will add `Framer` to `intent/__init__.py.__all__` per the comment already in the source.
- The `intent_change` INFO log event is wired and visible in `structlog.testing.capture_logs` — Phase 7 dashboard panels can subscribe.
- Pipeline (Plan 06) can compose `MotionAnalyzer.consume(...)` after `SubjectTracker.consume(...)` with a one-line glue in the orchestrator — both share the `consume(x, now_ns)` shape.

## Self-Check: PASSED

- `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py` — FOUND
- `pastor_tracker/src/pastor_tracker/intent/__init__.py` — FOUND (modified)
- `pastor_tracker/tests/test_motion_analyzer.py` — FOUND
- Commit `1ffdd76` — FOUND in `git log` (`feat(05-02): implement MotionAnalyzer hysteresis classifier`)
- Commit `21634e3` — FOUND in `git log` (`test(05-02): unit tests for MotionAnalyzer (INTENT-01, INTENT-02, TEST-03)`)

---
*Phase: 05-intent-and-control*
*Completed: 2026-05-06*
