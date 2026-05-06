---
phase: 05-intent-and-control
plan: 01
subsystem: testing
tags: [phase-5, intent-control, test-fixtures, trajectories, hysteresis, pydantic, pytest]

# Dependency graph
requires:
  - phase: 04-perception
    provides: TrackedSubject frozen Pydantic DTO (track_id, x/y normalized, vx/vy, timestamp_ns)
provides:
  - Deterministic TrackedSubject sequence generators consumed by every Phase-5 unit test
  - Pinned-contract self-tests that fail fast if a future edit silently breaks the helper invariants
  - Parametrized fixtures that decouple Phase-5 thresholds (motion/dwell/hysteresis) from fixture bodies (D-12)
affects: [05-02-motion-analyzer, 05-03-framer, 05-04-pan-controller, 05-05-command-dispatcher, 05-06-composition]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single-source-of-truth DTO factory (_make_subject) — DRY across all four public helpers"
    - "Tiger-style parameter-named ValueError boundary guards (_validate_common, _validate_x_range)"
    - "itertools.pairwise for monotonicity assertions (RUF007 compliance)"
    - "Parametrized rejection tests via @pytest.mark.parametrize over helper-name string"

key-files:
  created:
    - "pastor_tracker/tests/fixtures/trajectories.py — four helpers (step, ramp, dwell_then_walk, borderline_chatter)"
    - "pastor_tracker/tests/fixtures/test_trajectories.py — 18 self-tests pinning every helper contract"
  modified: []

key-decisions:
  - "Helpers take keyword-only parameters with defaults for track_id/t0_ns; every Phase-5 numeric flows in via caller (D-12)"
  - "borderline_chatter pins x=0.5 and only oscillates vx — RESEARCH Pitfall 1: classifier reads vx, not integrated x"
  - "_make_subject single construction site — DRY: y/vy pinning + Pydantic kw construction live in one place"
  - "ValueError messages name the parameter (x_before, dt_sec) before Pydantic field error fires — friendlier traceback"

patterns-established:
  - "tests/fixtures/trajectories.py — pure-sync deterministic DTO emitters mirror tests/fixtures/pose_traces.make_detection idiom"
  - "Self-test sibling for fixture modules where the fixture is the source-of-truth for downstream numerical assertions"

requirements-completed: [TEST-03]

# Metrics
duration: 4min
completed: 2026-05-06
---

# Phase 05 Plan 01: Trajectory Fixtures for Intent-and-Control Tests Summary

**Four pure deterministic TrackedSubject sequence generators (step, ramp, dwell_then_walk, borderline_chatter) plus 18 self-tests that pin every helper's length / x / vx / timestamp contract.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-06T09:06:03Z
- **Completed:** 2026-05-06T09:09:56Z
- **Tasks:** 2
- **Files modified:** 2 (both created)

## Accomplishments
- `tests/fixtures/trajectories.py` lands four locked-signature helpers matching RESEARCH Pattern 8 line 486-528 exactly — keyword-only, parametrized, never Config-coupled
- `_validate_common` + `_validate_x_range` provide Tiger-style boundary checks with parameter-named error messages (defense-in-depth before Pydantic Field validation)
- `_make_subject` is the single TrackedSubject construction site — DRY across all four helpers, pinning `subject_center_y_normalized=0.5` and `velocity_y_norm_per_sec=0.0` for pan-only tests
- 18 self-tests (`test_trajectories.py`) pin contract invariants: x/vx sequence shapes, monotonic timestamps, ValueError boundary rejections (parametrized across all four helpers)
- Magic-number grep against {0.08, 0.3, 0.03, 1.5, 0.4, 30.0, 0.2, 50} returns 0 in both files — D-12 enforced

## Task Commits

Each task was committed atomically:

1. **Task 1: Create tests/fixtures/__init__.py + trajectories.py with four pure helpers** — `ca61040` (test)
2. **Task 2: Self-test trajectories.py — protect against silent contract drift** — `c8c7d76` (test)

**Plan metadata:** to be created next (docs: complete plan)

_Note: `tests/fixtures/__init__.py` already existed from a prior phase, so Task 1 created only `trajectories.py`. The `__init__.py` requirement of the plan is satisfied by the pre-existing marker, verified at execution start._

## Files Created/Modified
- `pastor_tracker/tests/fixtures/trajectories.py` (255 lines) — four pure helpers + private validators + DTO factory
- `pastor_tracker/tests/fixtures/test_trajectories.py` (260 lines) — 18 self-tests covering all helpers, all rejections, and timestamp monotonicity

## Verification Results

**Task 1 acceptance:**
- `wc -l trajectories.py` = 255 (≥ 80 required)
- `grep -E "^def (step|ramp|dwell_then_walk|borderline_chatter)"` returns 4 matches
- `uv run python -c "from tests.fixtures.trajectories import …"` exits 0 (smoke import + step() check prints `ok`)
- `uv run ruff check tests/fixtures/trajectories.py` → All checks passed!
- `uv run mypy --strict tests/fixtures/trajectories.py` → Success: no issues found in 1 source file
- Magic-number grep `{0.08, 0.3, 0.03, 1.5, 0.4, 30.0, 0.2, 50}` returns **0**
- File contains `from pastor_tracker.core.types import TrackedSubject`
- File does NOT contain `import asyncio`, `print(`, or `from pastor_tracker.config`

**Task 2 acceptance:**
- `wc -l test_trajectories.py` = 260 (≥ 40 required)
- `grep -c "def test_"` = 9 (with parametrize, **18 collected tests**)
- `uv run pytest tests/fixtures/test_trajectories.py -x` → **18 passed in 0.48s**
- `uv run ruff check tests/fixtures/test_trajectories.py` → All checks passed!
- `grep -c "from tests.fixtures.trajectories import"` = 1
- No `from pastor_tracker.intent` or `from pastor_tracker.control` imports

## Decisions Made
- Helper signatures match the locked Pattern 8 skeletons exactly — no deviation from `step / ramp / dwell_then_walk / borderline_chatter` shapes documented in 05-RESEARCH.md
- `borderline_chatter` does not integrate position (x stays at 0.5) — Pitfall 1 says the consumer (MotionAnalyzer) only reads vx, so integrating would add cycles without test value and could drift x out of [0,1]
- `_clamp_norm` helper centralizes the [0,1] clamp logic for `ramp` and `dwell_then_walk` — DRY
- Test file uses `itertools.pairwise` (not `zip(a[:-1], a[1:])`) to satisfy RUF007 — landed during Task 2 verification

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] zip(walk_x, walk_x[1:], strict=True) raises ValueError on length mismatch**
- **Found during:** Task 2 first pytest run
- **Issue:** Plan's reference snippet for monotonicity used `zip(timestamps, timestamps[1:], strict=True)` — but with `strict=True` this raises ValueError because `timestamps` is one element longer than `timestamps[1:]`. The first run of `test_dwell_then_walk_holds_then_walks` failed with `ValueError: zip() argument 2 is shorter than argument 1`.
- **Fix:** Switched to `itertools.pairwise(...)` which is also the ruff-preferred form (RUF007). Single import + two call sites.
- **Files modified:** `pastor_tracker/tests/fixtures/test_trajectories.py`
- **Verification:** 18/18 pytest pass; ruff clean
- **Committed in:** `c8c7d76` (Task 2 commit — fix landed before commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix in test scaffolding, no production code changed)
**Impact on plan:** None — the fix was inside Task 2 before commit, so all acceptance gates remained green.

## Issues Encountered
- `uv` not on global PATH on this Windows machine; resolved by invoking `D:\System\Documents\PastorTrackingSystem\.venv\Scripts\uv.exe` directly. Did not modify any pyproject / config — the host environment quirk is recorded here for the verifier and future executors.

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
- Plan 02 (MotionAnalyzer) can `from tests.fixtures.trajectories import step, ramp, dwell_then_walk, borderline_chatter` immediately
- All four helpers parametrized so MotionAnalyzer tests can pull thresholds from `valid_config_dict` (see conftest.py line 27-29: `motion_threshold_norm_per_sec`, `motion_hysteresis_sec`, `dwell_threshold_norm_per_sec`)
- The 18 self-tests will fail loudly in CI on any future silent contract drift in trajectories.py — every Phase-5 plan inherits this safety net

## Self-Check: PASSED

- `pastor_tracker/tests/fixtures/trajectories.py` — FOUND
- `pastor_tracker/tests/fixtures/test_trajectories.py` — FOUND
- Commit `ca61040` — FOUND in `git log`
- Commit `c8c7d76` — FOUND in `git log`
- `pastor_tracker/tests/fixtures/__init__.py` — pre-existing FOUND (no commit needed)

---
*Phase: 05-intent-and-control*
*Completed: 2026-05-06*
