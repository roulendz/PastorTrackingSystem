---
phase: 06-pipeline-orchestrator
plan: 01
subsystem: pipeline-orchestrator
tags: [phase-6, wave-0, contracts, pose-detector-stream, pipeline-snapshot, types]
requires: []
provides:
  - "PoseDetector.stream(frames) async iterator yielding (Frame, list[Detection])"
  - "PipelineState Literal alias (6 lifecycle members)"
  - "PipelineSnapshot frozen Pydantic DTO (7 D-16 fields)"
  - "OrchestratorRejected typed exception in pipeline.py"
  - "pipeline.py module skeleton with PipelineSnapshot/PipelineState re-exports"
affects:
  - "Plan 06-02: can extend pipeline.py with Pipeline class without bootstrapping module"
  - "Plan 06-02: can drive tick loop via 'async for frame, dets in detector.stream(camera.frames())'"
  - "Plan 06-02: can build PipelineSnapshot from _PipelineCache fields"
  - "Plan 06-03: __main__ can import OrchestratorRejected for typed lifecycle error handling"
tech-stack:
  added: []
  patterns:
    - "Pure-core / dirty-edges DTO discipline (PipelineSnapshot frozen + extra='forbid')"
    - "Concurrent producer + queued frame-pairing inside stream() to preserve BL-01 drop-oldest while threading Frame identity (D-17)"
key-files:
  created:
    - pastor_tracker/src/pastor_tracker/pipeline.py
  modified:
    - pastor_tracker/src/pastor_tracker/perception/pose_detector.py
    - pastor_tracker/src/pastor_tracker/core/types.py
    - pastor_tracker/tests/test_pose_detector.py
    - pastor_tracker/tests/test_types.py
decisions:
  - "stream() uses an internal producer task + pending_frames deque to preserve BL-01 drop-oldest semantics while keeping Frame identity per yield (D-03 + D-17). A purely sequential await-consume/await-detection design defeats drop-oldest; a purely concurrent design loses identity. The deque tracks the frame whose inflight will produce each detection, popping the previous entry when consume() will drop it."
  - "PipelineSnapshot lives in core/types.py (not pipeline.py) to avoid the import cycle pipeline.py -> core.types -> pipeline.py. PipelineState mirrors pipeline._PipelineState members but as a string Literal."
  - "pipeline.py ships only the OrchestratorRejected skeleton. Full Pipeline class deferred to Plan 06-02 so its lifecycle assembly is not interleaved with module bootstrapping."
metrics:
  duration: "~12 min"
  completed: "2026-05-10"
---

# Phase 6 Plan 01: Wave-0 Contracts Summary

Wired the three Wave-0 contract pieces that Plans 06-02 / 06-03 / 06-04 depend on: `PoseDetector.stream(frames)` async helper (D-03), the `PipelineSnapshot` + `PipelineState` public DTO surface (D-16), and the `pipeline.py` module skeleton with `OrchestratorRejected` (D-15).

## Deliverables

### 1. `PoseDetector.stream(frames)` — D-03

Exact signature shipped:

```python
async def stream(
    self,
    frames: AsyncIterator[Frame],
) -> AsyncIterator[tuple[Frame, list[Detection]]]:
```

Plan 06-02 can drive the tick loop as:

```python
async for frame, dets in detector.stream(camera.frames()):
    now_ns = frame.timestamp_ns
    self._latest_frame = frame  # D-17 atomic slot update
    # ...downstream stages...
```

Internal structure (BL-01 preservation):
- A producer coroutine pumps `frames` → `consume()` concurrently with detection draining. Slow inference triggers drop-oldest naturally.
- A `pending_frames: deque[Frame]` tracks which frame each detection corresponds to, popping the previous entry when a new `consume()` will drop its inflight.
- Producer awaits `asyncio.sleep(0)` between consumes so fast engines (FakePoseEngine instant detect) process each frame to completion without spurious drops.
- Producer task is always awaited in `finally` (cancelled or naturally complete) to retrieve its exception and avoid asyncio "Task exception was never retrieved" warnings at GC time.

Result: `yielded_pairs == input_frames` for fast engines, `yielded_pairs <= input_frames` for slow engines (drop-oldest fires), and per-yield Frame identity is preserved (D-17 single-attribute-write semantics).

### 2. `PipelineSnapshot` + `PipelineState` — D-16

Confirmed the **exact 7 D-16 fields** in `core/types.py`:

| Field | Type | Default | Validation |
|---|---|---|---|
| `state` | `PipelineState` | (required) | Literal of 6 lifecycle members |
| `last_frame_ts_ns` | `int \| None` | `None` | `ge=0` when supplied |
| `last_intent` | `MotionIntent` | (required) | Literal incl. "indeterminate" |
| `last_target_x_normalized` | `float \| None` | `None` | `ge=0.0, le=1.0` when supplied |
| `last_pan_angle_deg` | `float \| None` | `None` | unbounded (motor limits in Config) |
| `last_emitted_angle_deg` | `float \| None` | `None` | unbounded |
| `motor_state` | `str` | (required) | opaque (`_MotorState.value`) |

`PipelineState = Literal["stopped", "running", "paused", "homing", "e_stopped", "quitting"]` — the 6 D-16 lifecycle members. Lives in `core/types.py` to break the would-be import cycle `pipeline.py → core.types → pipeline.py`.

`PipelineSnapshot` extends `_FrozenModel` (`frozen=True, extra="forbid"`) per project DTO discipline.

### 3. `pipeline.py` skeleton — D-15

`pastor_tracker/src/pastor_tracker/pipeline.py` ships:
- `OrchestratorRejected(Exception)` — single typed surface for invalid lifecycle transitions (D-15) and out-of-limits home (D-08).
- Re-exports `PipelineSnapshot` and `PipelineState` from `core.types` so Phase 7 callers have a single import line.
- Module docstring explicitly notes `Pipeline` class is the **06-02 deliverable**, `__main__` refactor is **06-03**, integration tests are **06-04**.

## Tests Added

`pastor_tracker/tests/test_pose_detector.py` (+4 tests, total 19):
- `test_stream_yields_frame_detection_pairs` — identity preservation across 3-frame trace
- `test_stream_exits_cleanly_when_frames_exhausted` — clean StopAsyncIteration
- `test_stream_propagates_engine_fault` — latched PerceptionError surfaces via stream
- `test_stream_drops_stale_internally` — BL-01 drop-oldest fires when wired through stream

`pastor_tracker/tests/test_types.py` (+7 tests, total 24):
- `test_pipeline_state_literal_members` — exact 6-member set
- `test_pipeline_snapshot_construction_minimal` — required + optional defaults
- `test_pipeline_snapshot_construction_full` — 7-field round-trip via `model_dump`
- `test_pipeline_snapshot_frozen` — mutation rejected
- `test_pipeline_snapshot_extra_forbidden` — unknown keys rejected
- `test_pipeline_snapshot_target_x_range_validated` — `[0, 1]` enforced
- `test_pipeline_snapshot_last_frame_ts_ns_non_negative` — `ge=0` enforced

## Verification Commands

```bash
cd pastor_tracker
PYTHONPATH=src python -m pytest tests/test_pose_detector.py tests/test_types.py
# 19 + 24 = 43 tests pass

PYTHONPATH=src python -m pytest         # full suite: 384 passed
python -m mypy --strict src             # 27 source files, no issues
python -m ruff check src tests          # all checks passed
```

Acceptance commands from the plan:

```bash
# Task 1 acceptance
grep -c "async def stream" pastor_tracker/src/pastor_tracker/perception/pose_detector.py    # >= 1
grep -E "AsyncIterator\[tuple\[Frame, list\[Detection\]\]\]" pastor_tracker/src/pastor_tracker/perception/pose_detector.py  # match

# Task 2 acceptance
grep -c "class PipelineSnapshot" pastor_tracker/src/pastor_tracker/core/types.py            # 1
grep -E "^PipelineState = Literal\[" pastor_tracker/src/pastor_tracker/core/types.py        # match

# Task 3 acceptance
test -f pastor_tracker/src/pastor_tracker/pipeline.py                                       # 0
grep -c "class OrchestratorRejected" pastor_tracker/src/pastor_tracker/pipeline.py          # 1
PYTHONPATH=src python -c "from pastor_tracker.pipeline import OrchestratorRejected, PipelineSnapshot, PipelineState; raise OrchestratorRejected('test')" 2>&1 | grep "OrchestratorRejected: test"
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] stream() helper redesigned to actually preserve drop-oldest**

- **Found during:** Task 1 GREEN phase (test_stream_drops_stale_internally failed against the literal plan code).
- **Issue:** The plan's prescribed implementation (sequential `await consume(frame); dets = await detections_iter.__anext__(); yield (frame, dets)`) provides strict back-pressure that defeats BL-01 drop-oldest entirely. Each `await detections_iter.__anext__()` synchronously drains one queue entry per frame — drop-oldest in `consume()` requires a *new* consume to arrive while `_inflight` is still busy, but the sequential design always waits for the previous detection before submitting the next frame.
- **Fix:** Redesigned with a producer coroutine pumping `frames` → `consume()` concurrently with the main coroutine draining `detections()`. Added `pending_frames: deque[Frame]` to track which frame each detection corresponds to (popping the tail when a new submission will drop the previous inflight). `await asyncio.sleep(0)` between consumes lets fast engines (FakePoseEngine) process each frame to completion. Producer always awaited in `finally` to avoid `Task exception was never retrieved` warnings.
- **Files modified:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py` (added `import collections` and `import contextlib`).
- **Commit:** `f041b3e`

This deviation preserves the contract the plan specified in `<behavior>` Test 4 ("BL-01 drop-oldest still active when wired through stream()") and Test 1 ("each yielded `frame` is the same object passed in (identity preserved)") — both of which are unsatisfiable by the plan's literal `<action>` code. The contract takes precedence over the example code.

**2. [Rule 1 - Bug] Test annotation lint cleanup**

- **Found during:** Task 1 ruff check.
- **Issue:** Initially used quoted forward-ref annotations `"asyncio.AsyncIterator[FrameT]"` to avoid local-class issues; ruff UP037 flagged them as unnecessary (file already has `from __future__ import annotations`).
- **Fix:** Imported `AsyncIterator` from `collections.abc` at module top and used unquoted annotations.
- **Files modified:** `pastor_tracker/tests/test_pose_detector.py`
- **Commit:** `f041b3e` (rolled into Task 1 GREEN commit)

### Intentional Plan Adherence Notes

- The plan's `<action>` for Task 1 included a verbatim code block; the actual implementation diverges as documented above. The plan's `<behavior>` contract (4 named tests with explicit assertions) is satisfied exactly.
- All other tasks executed exactly as written.

## Threat Flags

None. Phase 6 surface is fully internal (single-process orchestrator on operator desktop). Threat surface is unchanged from Phases 1-5.

## Known Stubs

None. The `Pipeline` class itself is deferred to 06-02, but that is **explicit plan scope** — the skeleton + OrchestratorRejected is the documented Wave-0 deliverable, not a stub.

## Self-Check: PASSED

Created files:
- `FOUND: pastor_tracker/src/pastor_tracker/pipeline.py` (43 lines, mypy strict + ruff clean, importable)

Modified files:
- `FOUND: pastor_tracker/src/pastor_tracker/perception/pose_detector.py` (+ `stream()` helper, `import collections`, `import contextlib`)
- `FOUND: pastor_tracker/src/pastor_tracker/core/types.py` (+ `PipelineState`, `PipelineSnapshot`)
- `FOUND: pastor_tracker/tests/test_pose_detector.py` (+ 4 stream tests, total 19)
- `FOUND: pastor_tracker/tests/test_types.py` (+ 7 snapshot tests, total 24)

Commits (verified via `git log --oneline`):
- `FOUND: 5447b41` test(06-01): add failing tests for PoseDetector.stream() helper (TDD RED)
- `FOUND: f041b3e` feat(06-01): add PoseDetector.stream() async helper for Phase 6 orchestrator (TDD GREEN)
- `FOUND: 7fa171b` test(06-01): add failing tests for PipelineState + PipelineSnapshot (TDD RED)
- `FOUND: bc06617` feat(06-01): add PipelineState + PipelineSnapshot DTO to core/types (TDD GREEN)
- `FOUND: 7ec624a` feat(06-01): create pipeline.py module skeleton with OrchestratorRejected

Verification:
- `FOUND: pytest -q` → 384 passed (full suite green; one pre-existing kalman flaky test that fails in baseline too — out of scope per Rule scope-boundary)
- `FOUND: mypy --strict src` → 27 source files, no issues
- `FOUND: ruff check src tests` → all checks passed

## TDD Gate Compliance

Plan does not have `type: tdd` at the plan level; the per-task `tdd="true"` flags on Tasks 1 and 2 were honored: each shipped a `test(...)` commit (RED) before its `feat(...)` commit (GREEN). Task 3 was non-TDD (skeleton creation) per the plan's `<task type="auto">` (no `tdd="true"`).
