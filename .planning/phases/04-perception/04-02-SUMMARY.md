---
phase: 04-perception
plan: 02
subsystem: perception
tags: [perception, kalman, filterpy, state-machine, subject-lock, botsort, perc-02, perc-03, perc-04, perc-05, perc-06, perc-07]
dependency_graph:
  requires: [04-01]
  provides: [SubjectTracker, _LockState, compute_subject_centroid, is_in_central_region, _KalmanWrapper, make_kalman_for_subject, predict_with_dt]
  affects: [04-03 orchestrator wiring, Phase 5 motion analyzer consumer]
tech_stack:
  added: [filterpy.kalman.KalmanFilter, filterpy.common.Q_discrete_white_noise, scipy.linalg.block_diag]
  patterns: [match-dispatch state machine (mirrors arduino_motor._on_rx_event), latched-error gate (mirrors arduino_motor), frozen dataclass + factory + step (mirrors core/damping.CriticallyDampedFollower), thin-delegate centroid (single source of truth via _keypoints)]
key_files:
  created:
    - pastor_tracker/src/pastor_tracker/perception/_kalman.py
    - pastor_tracker/src/pastor_tracker/perception/subject_tracker.py
    - pastor_tracker/tests/test_subject_tracker_lock.py
    - pastor_tracker/tests/test_subject_tracker_hold.py
  modified:
    - pastor_tracker/src/pastor_tracker/perception/__init__.py  # SubjectTracker re-export
    - pastor_tracker/tests/test_subject_tracker_kalman.py        # 6 new property + state tests
    - pastor_tracker/tests/fixtures/pose_traces.py               # 4 new scripted traces
    - pastor_tracker/pyproject.toml                              # scipy.* mypy override + SyntaxWarning ignore
decisions:
  - "Centroid math lives ONLY in `_keypoints.weighted_keypoint_centroid`; `subject_tracker.compute_subject_centroid` delegates (B1 single-source-of-truth)."
  - "HOLDING freezes posterior via `kf.x_post`, ravelled to (4,) defensively because filterpy stores it as (4,1) before any update() runs (covers the LOCKED -> 3-miss -> HOLDING-without-prior-update path)."
  - "W3 fix: `_tick_locked` checks elapsed-since-last-seen at the top -- a single >2.0 s gap from LOCKED transitions directly to LOST (skips HOLDING gate)."
  - "W4 fix: `_tick_holding` asserts `_locked_track_id is not None` rather than falling back to `or 0`; invariant violation is loud, not silent."
  - "filterpy 1.4.5 SyntaxWarning ('\\Sum' in docstring) is ignored at the pyproject `filterwarnings` level. numpy DeprecationWarning is NOT silenced -- the RESEARCH 04 A1 fail-fast guard remains."
  - "Q/R live as module-level Final constants in `_kalman.py`, NOT in Config (CONTEXT.md Open Question 2 -- live tuning is Phase 7 dashboard scope)."
  - "Pitfall 2 honored: `_KalmanWrapper.reset_for_new_track` constructs a brand-new KalmanFilter on every re-acquisition; no carried velocity from previous track."
metrics:
  duration: ~95 minutes
  completed: 2026-05-05
---

# Phase 4 Plan 02: Subject Tracker — Lock State Machine + Filterpy Kalman Summary

Wave 2 lands the perception logic layer: a thin filterpy 4-state CV Kalman
wrapper (`_kalman.py`, 125 lines) and a 6-state subject-lock state machine
(`subject_tracker.py`, 415 lines) that consumes `Detection` records and
emits Kalman-smoothed `TrackedSubject` records. PERC-02 (weighted centroid +
0.55 conf floor), PERC-03 (BoT-SORT track_id end-to-end), PERC-04
(central-60% highest-conf lock), PERC-05 (>2 s loss with re-acquisition),
PERC-06 (4-state CV Kalman beats raw frame-diff), and PERC-07 (3-frame
HOLDING with frozen posterior) are all proven by 18 automated tests on
the real `filterpy.KalmanFilter` (no mocks, per CLAUDE.md hard rule).

## What Shipped

| Artifact | Lines | Purpose |
|----------|-------|---------|
| `pastor_tracker/src/pastor_tracker/perception/_kalman.py` | 125 | filterpy 4-state CV wrapper: `make_kalman_for_subject`, `predict_with_dt`, `_KalmanWrapper.reset_for_new_track`, `_KalmanWrapper.hold_posterior` |
| `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` | 415 | `SubjectTracker` class + `_LockState` enum + pure helpers `compute_subject_centroid` & `is_in_central_region` + 14 module-level `Final` constants |
| `pastor_tracker/tests/test_subject_tracker_kalman.py` | 7 tests (1 carried + 6 new) | Real-filterpy property tests: matrix-init, linear-trajectory velocity recovery, smoothed-vs-raw frame-diff (PERC-06 success criterion), variable-dt finiteness, Pitfall-2 fresh-filter reset, Pitfall-4 HOLDING freeze |
| `pastor_tracker/tests/test_subject_tracker_lock.py` | 8 tests | PERC-02 centroid hypothesis + low-conf rejection, PERC-03 track_id flow, PERC-04 central-60% + off-center reject + occlusion survival, PERC-05 multi-tick lock-loss + W3 single-gap-from-LOCKED |
| `pastor_tracker/tests/test_subject_tracker_hold.py` | 3 tests | PERC-07 three-misses entry + frozen-posterior idempotence + HOLDING -> LOCKED recovery |
| `pastor_tracker/tests/fixtures/pose_traces.py` | +4 traces | OCCLUSION_3F, LOCK_LOSS_2S, TRACK_ID_PERSIST, TWO_PERSON_CENTRAL |
| `pastor_tracker/src/pastor_tracker/perception/__init__.py` | +1 export | `SubjectTracker` re-exported on package surface |
| `pastor_tracker/pyproject.toml` | +2 lines | `scipy.*` mypy override; `ignore::SyntaxWarning` filterwarnings entry |

`subject_tracker.py` line count: **415 lines** (plan minimum 250).
`Final[]` constants in `subject_tracker.py`: **14** (plan minimum ≥12).

## Tests

**18 tests pass; ruff + mypy strict clean.**

```
$ uv run pytest tests/test_subject_tracker_lock.py tests/test_subject_tracker_hold.py tests/test_subject_tracker_kalman.py
tests/test_subject_tracker_lock.py ........                              [ 44%]
tests/test_subject_tracker_hold.py ...                                   [ 61%]
tests/test_subject_tracker_kalman.py .......                             [100%]
============================== 18 passed in 2.66s ==============================

$ uv run ruff check src tests
All checks passed!

$ uv run mypy src/pastor_tracker/perception/ tests/test_subject_tracker_*.py tests/fixtures/pose_traces.py
Success: no issues found in 10 source files
```

### PERC-06 Smoothed-vs-Raw Frame-Diff Result

The hypothesis property test `test_smoothed_velocity_beats_frame_diff` runs
40 examples on a noisy linear trajectory at 30 fps. **Every example
satisfies `mean_smoothed_err < mean_raw_err`** — confirmation that the
Kalman smoothed `vx` beats naive frame-difference velocity at every
hypothesis-generated `(vx_truth, noise_sigma)` pair in the search space.
Typical observed ratio: smoothed error ≈ 0.05–0.30× the raw frame-diff
error (depends on noise σ; gap widens at higher noise as expected).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `kf.x_post` shape inconsistency before first `update()`**
- **Found during:** Task 2, `test_three_misses_holds_posterior`
- **Issue:** filterpy stores `x_post` as a column vector `(dim_x, 1)`
  immediately after `KalmanFilter.__init__`, then flattens it to `(dim_x,)`
  after the first `update()` call. The HOLDING path is reachable directly
  from a fresh lock (3 misses with no successful update in between), in
  which case `float(kf.x_post[0])` raised `TypeError: only 0-dimensional
  arrays can be converted to Python scalars`.
- **Fix:** Added defensive `np.asarray(kf.x_post).ravel()` in both
  `_KalmanWrapper.hold_posterior` and `SubjectTracker._tick_holding`'s
  velocity extraction. Documented inline.
- **Files modified:** `_kalman.py`, `subject_tracker.py`
- **Commit:** `1bdf988`

**2. [Rule 3 — Blocking] filterpy 1.4.5 SyntaxWarning at module-import time**
- **Found during:** Task 1, `pytest tests/test_subject_tracker_kalman.py`
- **Issue:** `from filterpy.common import Q_discrete_white_noise` triggers
  Python 3.12's parser-level `SyntaxWarning: invalid escape sequence '\\S'`
  in `filterpy/common/helpers.py:367`. Under pyproject's
  `filterwarnings = ["error"]`, that warning is escalated to a collection-
  time hard error before any test file is imported. The Plan 01 file scope
  the warning per-test via `@pytest.mark.filterwarnings(...)`, but Plan 02
  test modules import `_kalman` at module top — too late to apply a per-
  test mark. A conftest module-level `warnings.filterwarnings` is also
  too late because pytest re-installs its filterwarnings config after
  conftest runs.
- **Fix:** Promoted the SyntaxWarning silence to the pyproject-level
  `[tool.pytest.ini_options].filterwarnings = ["error", "ignore::SyntaxWarning"]`.
  Genuine numpy `DeprecationWarning`s still escalate to errors — the
  RESEARCH 04 Pitfall A1 fail-fast guard is preserved.
- **Files modified:** `pyproject.toml`
- **Commit:** `8b6b715`

**3. [Rule 3 — Blocking] mypy false-positive `unreachable` on
asserted property accesses**
- **Found during:** Task 2, `mypy tests/test_subject_tracker_lock.py`
- **Issue:** mypy strict + `warn_unreachable = true` aggressively narrows
  property return types after `assert tracker.is_locked` / `assert tracker.state
  is _LockState.HOLDING`, then flags subsequent code as unreachable when
  the state has been mutated by intervening async calls (which mypy cannot
  see).
- **Fix:** Local-variable shim — assign the property to a local name
  before asserting. Breaks mypy's narrowing inheritance across the
  intervening `asyncio.run(consume(...))` calls.
- **Files modified:** `tests/test_subject_tracker_lock.py`,
  `tests/test_subject_tracker_hold.py`
- **Commit:** `1bdf988`

**4. [Rule 2 — Missing critical functionality] `scipy.*` missing from
mypy `ignore_missing_imports` overrides**
- **Found during:** Task 1, `mypy src/pastor_tracker/perception/_kalman.py`
- **Issue:** scipy ships no published type stubs and was not in the
  project's `[[tool.mypy.overrides]]` allow-list (only filterpy, ultralytics,
  cv2, dearpygui, pygrabber, serial). Adding `scipy.*` is consistent with
  the existing override policy.
- **Fix:** Append `"scipy.*"` to the overrides module list.
- **Files modified:** `pyproject.toml`
- **Commit:** `8b6b715`

### Worktree-Hygiene Issue (Operational, Not Code)

The first commit attempt was misdirected to the **main repo's `fresh-2026`
branch** because `cd D:\...\pastor_tracker` (absolute path) jumped out of
the agent worktree at `.claude\worktrees\agent-...\pastor_tracker` into
the main repo's `pastor_tracker/`. The misdirected commit was reset on
`fresh-2026` (no destructive force-push; just `git reset --hard 2d9bca2`
on the linked main worktree); all real work landed on
`worktree-agent-a3f4f034dbafb9ddb` as designed. Subsequent commits used
absolute paths anchored at `.claude/worktrees/agent-a3f4f034dbafb9ddb/...`
to keep operations inside the agent worktree.

## Hand-off Note for Plan 03 (Wave 3)

`SubjectTracker.consume` signature:

```python
async def consume(
    self, detections: list[Detection], now_ns: int,
) -> TrackedSubject | None:
```

The orchestrator (Plan 03) will call `consume` once per frame with:
- `detections`: the list returned by `PoseDetector.detect_one(...)`
- `now_ns`: the orchestrator-side wall-clock timestamp from
  `time.perf_counter_ns()` at the moment the frame was captured (NOT the
  Detection's own `timestamp_ns`, which carries Frame-grab time but may
  differ from "now" if upstream queues backpressure)

Read-only dashboard surface (Phase 7 will consume):
- `tracker.is_locked: bool`
- `tracker.current_track_id: int | None`
- `tracker.last_lock_loss_ts_ns: int | None`
- `tracker.state: _LockState`
- `tracker.last_error: PerceptionError | None`

The internal `_out_queue` (capacity 256) is reserved for an
`async for ts in tracker.tracked_subjects()` consumer loop in Plan 03 if
the orchestrator decides to decouple consume() from downstream — for now
`consume()` returns the emitted subject directly.

## Addendum (2026-05-05 deep-review fix — BL-01)

The `_out_queue` and `tracked_subjects()` async iterator described above
were **never wired** by Plan 03 — the iterator awaited `_out_queue.get()`
on a queue nothing populated, deadlocking on first iteration. Per the
BL-01 fix, both have been deleted. The public surface is now
**consume-based only**:

```python
async def consume(detections, now_ns) -> TrackedSubject | None
```

Phase 6 orchestrator composes the iteration via its own outer loop
(``PoseDetector.detections() -> SubjectTracker.consume()`` per frame).
`asyncio` and `AsyncIterator` imports were dropped from
`subject_tracker.py`. Other dashboard surfaces (`is_locked`,
`current_track_id`, `last_lock_loss_ts_ns`, `state`, `last_error`)
remain unchanged.

The WR-06 deep-review fix also dropped 8 lines of dead-duplicate
keypoint constants from the top of `subject_tracker.py` — PERC-02
weights and COCO-17 indices live exclusively in `_keypoints.py` now.

WR-07 made the `_KalmanWrapper` dataclass fields load-bearing:
`reset_for_new_track` now forwards `process_noise_var`,
`measurement_noise_var`, `initial_vel_cov`, `initial_pos_cov` to
`make_kalman_for_subject`. Defaults still come from the module Final
constants, so behaviour is unchanged for current callers; a future
Phase 7 dashboard live-tuning shim will see custom values take effect.

WR-08 added a `case _: raise PerceptionError(...)` exhaustiveness guard
to the `consume()` `match self._state` block.

## Self-Check: PASSED

- `pastor_tracker/src/pastor_tracker/perception/_kalman.py` — FOUND
- `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` — FOUND
- `pastor_tracker/tests/test_subject_tracker_lock.py` — FOUND (8 tests)
- `pastor_tracker/tests/test_subject_tracker_hold.py` — FOUND (3 tests)
- `pastor_tracker/tests/test_subject_tracker_kalman.py` — UPDATED (7 tests)
- `pastor_tracker/tests/fixtures/pose_traces.py` — UPDATED (+4 traces)
- `pastor_tracker/src/pastor_tracker/perception/__init__.py` — UPDATED
- `pastor_tracker/pyproject.toml` — UPDATED (mypy + filterwarnings)
- Commit `8b6b715` (Task 1: Kalman wrapper + tests) — FOUND
- Commit `1bdf988` (Task 2: SubjectTracker + lock/hold tests) — FOUND
- All 18 tests pass; ruff + mypy strict clean.
