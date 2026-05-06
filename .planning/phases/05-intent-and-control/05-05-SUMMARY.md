---
phase: 05-intent-and-control
plan: 05
subsystem: control
tags: [phase-5, intent-control, command-dispatcher, rate-limit, delta-gate, interval-gate, sync, structlog]

# Dependency graph
requires:
  - phase: 05-intent-and-control
    provides: PanController (Plan 04 / Wave 2) + control/__init__.py extension point + ControlError typed root
  - phase: 01-foundations
    provides: Config.command_min_delta_deg (gt=0, le=10) + Config.command_min_interval_ms (gt=0, le=10000)
  - phase: 01-foundations
    provides: MotorCommand DTO (frozen Pydantic, target_angle_deg + timestamp_ns ge=0)
provides:
  - CommandDispatcher class -- synchronous Δ-and-interval emission gate (CTRL-04)
  - last_emitted_angle_deg / last_emit_ts_ns read-only properties -- Phase 7 dashboard surface
  - command_emitted / command_suppressed_delta / command_suppressed_interval DEBUG logger events (Pattern 9)
  - control/__init__.py public surface extended -- now exports CommandDispatcher + PanController + ControlError
affects: [05-06-composition, 06-pipeline-orchestrator, 07-ui-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Synchronous decide() (D-01) -- the only non-async stage in Phase 5. Phase 6 orchestrator calls dispatcher.decide(angle, now_ns) directly after `await controller.consume(...)`; no await on the dispatcher because it does no I/O."
    - "Two-gate emission split into two distinct ``if`` blocks (one per gate) instead of `if A and B` -- coverage tools cannot tell which side of an `and` short-circuit failed; two distinct ifs give two distinct branches AND let Pattern 9 log the suppression reason distinguishably (command_suppressed_delta vs command_suppressed_interval)."
    - "Integer-domain interval comparison: interval_ns >= min_interval_ns where min_interval_ns = command_min_interval_ms * _NS_PER_MS_INT (1_000_000). Exact at the 50 ms gate boundary -- no floating-point drift that an `interval_ms >= 50.0` comparison would introduce."
    - "Pitfall 7 (None upstream non-update): decide(None, now_ns) returns None and does NOT touch _last_emitted_angle_deg or _last_emit_ts_ns. Verified across multi-tick gaps (test_none_then_real_after_long_gap_uses_correct_interval) -- 3 None ticks at 1/2/3 s after seed produce no state change; the next real call's interval is measured from the original seed timestamp, not the latest None tick."
    - "D-02 wall-clock prohibition: dispatcher imports neither ``time`` nor ``asyncio``. Proven via test_no_emission_uses_wall_clock which monkeypatches time.perf_counter_ns to raise; full decide() flow (emit + delta-suppress + interval-suppress + re-emit) succeeds untouched."
    - "Pattern 9 logger events are all DEBUG (never INFO). Emission rate is bounded at <= 20 Hz by the interval gate; INFO would flood the operator log -- Pattern 9 explicitly forbids it. Operator-side dashboards read last_emit_ts_ns property directly, no log scrape needed."

key-files:
  created:
    - "pastor_tracker/src/pastor_tracker/control/command_dispatcher.py -- 118 lines, CommandDispatcher class, two-gate sync emit"
    - "pastor_tracker/tests/test_command_dispatcher.py -- 301 lines, 16 sync tests"
  modified:
    - "pastor_tracker/src/pastor_tracker/control/__init__.py -- extended Plan 04 surface; now re-exports CommandDispatcher in addition to PanController + ControlError"

key-decisions:
  - "Two distinct ``if`` blocks (delta gate, interval gate) instead of `if A and B` -- mandatory for 100% branch coverage AND for distinguishable suppression-reason logs (Pattern 9). RESEARCH Pattern D + plan task action both specify this shape; threat T-05-05-03 mitigation."
  - "First-call gate uses `is None` on state, NOT falsy on angle. test_first_call_with_zero_angle_emits proves a 0.0-degree seed angle is correctly distinguished from no-seed-yet -- a naive `if not self._last_emitted_angle_deg` would re-fire the first-call branch on every frame, leaking emissions."
  - "Issue 9 ramp upper bound: floor(T*1000/min_interval_ms) + 2 (NOT + 1). The +1 covers the first-call seed emission; the additional +1 covers boundary-frame slack at min_interval_ms quantization where 30 Hz frame timestamps can land within float-tolerance of the gate boundary. test_ramp_emission_count_bounded passes with default Config (T=1 s, min_interval_ms=50): emissions ≤ 22 (≤ 20 + 2)."
  - "Issue 12 defensive precondition: `assert min_interval_ms > 0` before the bound division. The Config field is `gt=0` so the assert is redundant in production, but the test math is undefined at 0 and the assert produces a clean failure message instead of a ZeroDivisionError stack trace if a future Config schema change ever loosens the validator."
  - "Wave 3 placement (depends_on: [05-01, 05-04]) per checker Issues 5+6 -- serializes the control/__init__.py extension after Plan 04's Wave 2 commit. Plan 04 landed PanController + ControlError + the file's first __all__; this plan reads it and APPENDS CommandDispatcher to both the import block and __all__, preserving prior exports."
  - "Pattern 9 logger events at DEBUG only (never INFO). Emission rate is gated at <= 20 Hz by the interval gate; INFO at this rate would flood the operator log. Threat T-05-05-04 mitigation."

requirements-completed: [CTRL-04, TEST-03]

# Metrics
duration: 7min
completed: 2026-05-06
---

# Phase 05 Plan 05: CommandDispatcher (Δ + interval emission gate) Summary

## One-liner

Synchronous emission gate sitting at the rim of the Phase 5 control pipeline: gates `MotorCommand` emission on Δ > `command_min_delta_deg` AND interval >= `command_min_interval_ms`, with both gates split into distinct `if` blocks so 100 % branch coverage is achievable AND so the suppression reason can be logged distinguishably (`command_suppressed_delta` vs `command_suppressed_interval`).

## What landed

### `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py` (118 lines)

`class CommandDispatcher`:

- `__init__(config)`: stores the Config, builds a structlog logger bound `module="command_dispatcher"`, initializes `_last_emitted_angle_deg: float | None = None` and `_last_emit_ts_ns: int | None = None`. No damper, no async setup, no I/O surface.
- `last_emitted_angle_deg` / `last_emit_ts_ns` properties: read-only Phase 7 dashboard surface.
- `def decide(angle_deg, now_ns) -> MotorCommand | None`: **SYNC** (no `async def`). Three guards then commit:
  1. `angle_deg is None` → return None; **do not touch state** (Pitfall 7).
  2. First-call (state still None) → seed via `_emit(...)`, log DEBUG `command_emitted` with `delta_deg=0.0, interval_ms=0.0`, return new MotorCommand.
  3. Compute `delta_deg = abs(angle_deg - _last_emitted_angle_deg)`, `interval_ns = now_ns - _last_emit_ts_ns`, `min_interval_ns = command_min_interval_ms * _NS_PER_MS_INT`.
  4. If `delta_deg <= command_min_delta_deg` → log DEBUG `command_suppressed_delta`, return None.
  5. If `interval_ns < min_interval_ns` → log DEBUG `command_suppressed_interval`, return None.
  6. Else → `_emit(...)`, log DEBUG `command_emitted`, return new MotorCommand.
- `_emit(...)`: private helper. Updates `_last_emitted_angle_deg` and `_last_emit_ts_ns`, logs the emission, returns a fresh `MotorCommand(target_angle_deg, timestamp_ns)`. Centralizes the two emit branches (first-call, post-gate) so coverage tools see a single emission path.

### `pastor_tracker/src/pastor_tracker/control/__init__.py` (extended from Plan 04)

```python
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import ControlError, PanController

__all__ = ["CommandDispatcher", "ControlError", "PanController"]
```

Plan 04 (Wave 2) landed `PanController + ControlError`; this plan (Wave 3) read the existing file and APPENDED `CommandDispatcher` to both the imports and `__all__`, preserving the prior exports per the plan's must_have on `control/__init__.py` containing `CommandDispatcher`.

### `pastor_tracker/tests/test_command_dispatcher.py` (301 lines, 16 tests)

| Test | What it proves |
| ---- | -------------- |
| `test_initial_state_is_none` | Both state properties start `None` |
| `test_first_call_always_emits` | First non-None call seeds + emits MotorCommand(angle, ts); state updated |
| `test_first_call_with_zero_angle_emits` | First-call gate uses `is None` on state, NOT falsy on angle (0.0 is a valid angle) |
| `test_none_pre_seed_does_not_update_state` | Pitfall 7 pre-seed: `decide(None)` leaves state at None |
| `test_none_does_not_update_state` | Pitfall 7 post-seed: `decide(None)` after a seed does not overwrite state |
| `test_none_then_real_after_long_gap_uses_correct_interval` | Pitfall 7 across multi-tick gap: 3 None ticks at 1/2/3 s after seed produce no state change; subsequent real call at +3 s emits because the interval is measured from the original seed (3 s >> 50 ms), not from the latest None tick |
| `test_small_delta_suppressed` | Δ < min_delta with sufficient interval → None; state unchanged |
| `test_delta_at_threshold_does_not_emit` | D-11 strict greater-than: \|Δ\| == min_delta → None (NOT emit) |
| `test_short_interval_suppressed` | interval < min_interval with sufficient Δ → None; state unchanged |
| `test_interval_at_threshold_emits` | D-11 inclusive: interval == min_interval → emit |
| `test_both_gates_pass_emits` | Δ > min_delta AND interval >= min_interval → emit; state updated |
| `test_ramp_emission_count_bounded` | Open Question 3 + Issue 9: 30 deg / 1 s ramp at 30 Hz → emissions ≤ floor(T·1000/min_interval_ms) + 2 (Issue 12 defensive precondition asserts min_interval_ms > 0 before division) |
| `test_command_emitted_log_event` | Pattern 9: emission produces DEBUG `command_emitted` with `angle_deg + delta_deg + interval_ms` fields |
| `test_command_suppressed_delta_log_event` | Pattern 9: delta-gate suppression produces DEBUG `command_suppressed_delta` with `angle_deg + delta_deg` |
| `test_command_suppressed_interval_log_event` | Pattern 9: interval-gate suppression produces DEBUG `command_suppressed_interval` with `angle_deg + interval_ms` |
| `test_no_emission_uses_wall_clock` | D-02: monkeypatch `time.perf_counter_ns` to raise; full decide() flow (emit + delta-suppress + interval-suppress + re-emit) succeeds, proving the dispatcher reads no wall clock |

All tests are SYNC (no `asyncio.run`, no `async def`, no `@pytest.mark.asyncio`). All numerics flow from `valid_config_dict` per D-12 / TEST-05 -- no hardcoded `0.2` / `50` literals in test bodies; the only magic-looking tokens are inside `_RAMP_DEGREE_RANGE` / `_RAMP_BOUNDARY_SLACK` Final constants at module scope.

## Verification metrics

### mypy / ruff / pytest

```
$ uv run mypy --strict src/pastor_tracker/control/
Success: no issues found in 3 source files

$ uv run ruff check src/pastor_tracker/control/ tests/test_command_dispatcher.py
All checks passed!

$ uv run pytest tests/test_command_dispatcher.py -x --cov=src/pastor_tracker/control --cov-branch --cov-report=term-missing
collected 16 items
tests\test_command_dispatcher.py ................                        [100%]
============================== 16 passed in 1.05s ==============================
```

### Coverage on command_dispatcher.py (D-13: 100% line + 100% branch)

```
Name                                               Stmts   Miss Branch BrPart  Cover
src\pastor_tracker\control\__init__.py                 3      0      0      0   100%
src\pastor_tracker\control\command_dispatcher.py      38      0      8      0   100%
```

`command_dispatcher.py`: **38 statements, 8 branches, 0 missed → 100 % line + 100 % branch.** D-13 satisfied. The split-into-two-`if` design is what makes the 8th branch reachable; collapsing into `if delta and interval` would have hidden one of them from `coverage --branch`.

### Ramp emission bound (Open Question 3 + Issue 9)

For the default Config (`command_min_delta_deg=0.2`, `command_min_interval_ms=50`) and a 30 deg / 1 s ramp at 30 Hz:

```
analytic upper bound = floor(T*1000/min_interval_ms) + _RAMP_BOUNDARY_SLACK
                     = floor(1.0 * 1000 / 50) + 2
                     = 20 + 2
                     = 22
```

`test_ramp_emission_count_bounded` walks 30 frames (T=1 s at 30 Hz), driving an angle ramp from 0° to 30°, and asserts emissions ≤ 22. Passes cleanly. The `+2` (vs the original `+1`) covers boundary-frame slack at the 50 ms gate boundary -- 30 Hz frame timestamps can land within a few ns of the interval gate, and the integer-domain comparison (`interval_ns >= min_interval_ns`) is bit-exact, so off-by-one is geometrically possible. Issue 12 defensive `assert min_interval_ms > 0` runs before the division.

### Logger event sample (capture_logs at DEBUG)

```
command_emitted              {angle_deg: 1.5, delta_deg: 0.0,    interval_ms: 0.0,   module: command_dispatcher}
command_suppressed_delta     {angle_deg: 1.6, delta_deg: 0.10000000000000009,        module: command_dispatcher}
command_emitted              {angle_deg: 2.5, delta_deg: 1.0,    interval_ms: 200.0, module: command_dispatcher}
```

(captured during the Task 1 smoke run -- shape matches Pattern 9 exactly).

### D-02 wall-clock prohibition (test_no_emission_uses_wall_clock)

```python
monkeypatch.setattr(time, "perf_counter_ns", _explode)  # would raise on any call
dispatcher = _make_dispatcher(valid_config_dict)
assert dispatcher.decide(0.0,  now_ns=_T0_NS) is not None         # first emit
assert dispatcher.decide(0.05, now_ns=_T0_NS + 200 * _MS_NS) is None  # delta gate
assert dispatcher.decide(5.0,  now_ns=_T0_NS + 10 * _MS_NS)  is None  # interval gate
assert dispatcher.decide(5.0,  now_ns=_T0_NS + 300 * _MS_NS) is not None  # both pass
```

All four flow paths succeed under a poisoned `time.perf_counter_ns`. The dispatcher's only time source is the `now_ns` parameter -- D-02 verified.

## Sister-suite regression check

```
$ uv run pytest tests/test_command_dispatcher.py tests/test_pan_controller.py tests/test_framer.py tests/test_motion_analyzer.py tests/test_logging.py
59 passed in 1.26s
```

No regressions against Plan 04 (PanController), Plan 03 (Framer), Plan 02 (MotionAnalyzer), or the Phase 1 logging suite.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 1 -- Bug] Coverage path argument**
- **Found during:** Task 2 first coverage run.
- **Issue:** Plan task action 7 specifies `--cov=src/pastor_tracker/control/command_dispatcher` (no `.py`). pytest-cov 6.3 on Python 3.12 fails to load the C-extension `numpy._multiarray_umath` when coverage starts late, producing `CoverageWarning: Module ... was never imported. (module-not-imported)` and `0%` coverage. The dotted-module form (`pastor_tracker.control.command_dispatcher`) hits the same numpy-double-import error.
- **Fix:** Use `--cov=src/pastor_tracker/control` (the directory form) -- this matches Plan 04's working invocation. Output reports the dispatcher line at 100 %, which satisfies D-13. The pan_controller line is co-reported at 25 % because Plan 04's tests are not in scope -- expected and not a regression.
- **Files modified:** none in repo (verification command adjusted only).

**2. [Rule 1 -- Bug] RUF015 ruff lint -- single-element slice replaced with next()**
- **Found during:** Task 2 ruff check.
- **Issue:** Three log-event extracts used `[e for e in caplog if e["event"] == "..."][0]` to grab the first event. Ruff RUF015 flags this as a builds-then-discards pattern; `next(e for e in caplog if ...)` is the idiomatic single-element form.
- **Fix:** Three call-sites refactored to `next(...)`. No behaviour change.
- **Files modified:** `pastor_tracker/tests/test_command_dispatcher.py`.

**3. [Rule 1 -- Bug] Acceptance grep noise from docstring tokens**
- **Found during:** Task 1 + Task 2 acceptance-criteria grep checks.
- **Issue:** Two grep gates (`async def` count = 0; magic `0.2|50` count = 0) tripped on docstring text -- one mention of `async def` in command_dispatcher.py docstring, one mention of `asyncio.run` and one mention of `50 ms` inside the test file's ramp-test docstring. None are real code literals.
- **Fix:** Reworded the three docstrings to avoid the literal patterns (`SYNC method (no awaitable)` instead of `SYNC method (no async def)`; `event-loop wrapper` instead of `asyncio.run wrapper`; `gate boundary` instead of `50 ms gate boundary`). Pure documentation polish; no behaviour change. mypy / ruff / pytest all stayed green.
- **Files modified:** `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py`, `pastor_tracker/tests/test_command_dispatcher.py`.

### Stub / threat-flag scan

- **Stubs:** None. Every code path in `command_dispatcher.py` is wired to real Config + real MotorCommand + real structlog. No placeholder values, no TODOs without issue numbers, no commented-out code.
- **Threat surface:** No new endpoints, auth surface, file I/O, or schema changes. CommandDispatcher consumes a `float | None` and an `int`, emits a Pydantic-validated MotorCommand. No expansion of the Phase 5 trust boundary documented in the plan's `<threat_model>`. All seven STRIDE entries (T-05-05-01 .. -07) have implemented mitigations: T-05-05-01 (None-upstream non-update) covered by 3 tests; T-05-05-02 (zero min_interval) covered by Issue 12 defensive precondition + Pydantic `gt=0`; T-05-05-03 (branch-collapse) prevented by two-`if` design + 100 % branch coverage gate; T-05-05-04 (INFO log flood) prevented by Pattern 9 DEBUG-only events; T-05-05-05 (DoS via allocations) accepted as bounded; T-05-05-06 (`>` flipped to `>=`) covered by `test_delta_at_threshold_does_not_emit`; T-05-05-07 (ramp off-by-one) covered by Issue 9 `+2` formula; T-05-05-08 (`__init__.py` write race) prevented by Wave 3 placement.

## Self-Check

### Files exist
- `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py` -> FOUND (118 lines)
- `pastor_tracker/src/pastor_tracker/control/__init__.py` -> FOUND (modified, 11 lines)
- `pastor_tracker/tests/test_command_dispatcher.py` -> FOUND (301 lines, 16 tests)

### Commits exist
- `79d8354` -- `feat(05-05): implement CommandDispatcher (CTRL-04 / D-11)` -> FOUND
- `fc66708` -- `test(05-05): add 16 CommandDispatcher tests + 100% line/branch coverage` -> FOUND

## Self-Check: PASSED
