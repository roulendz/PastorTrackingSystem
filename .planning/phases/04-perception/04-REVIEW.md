---
phase: 04-perception
reviewed: 2026-05-05T17:30:00Z
depth: standard
files_reviewed: 16
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/perception/__init__.py
  - pastor_tracker/src/pastor_tracker/perception/_keypoints.py
  - pastor_tracker/src/pastor_tracker/perception/_pose_worker.py
  - pastor_tracker/src/pastor_tracker/perception/pose_detector.py
  - pastor_tracker/src/pastor_tracker/perception/_kalman.py
  - pastor_tracker/src/pastor_tracker/perception/subject_tracker.py
  - pastor_tracker/src/pastor_tracker/config.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/tests/fixtures/pose_traces.py
  - pastor_tracker/tests/test_pose_detector.py
  - pastor_tracker/tests/test_subject_tracker_kalman.py
  - pastor_tracker/tests/test_subject_tracker_lock.py
  - pastor_tracker/tests/test_subject_tracker_hold.py
  - pastor_tracker/tests/test_perception_e2e.py
  - pastor_tracker/tests/test_config.py
  - pastor_tracker/tests/test_types.py
findings:
  blocker: 2
  warning: 8
  info: 5
  total: 15
status: clean
fix_applied_at: 2026-05-05T18:00:00Z
fix_scope: critical_warning
fixed: 10  # BL-01, BL-02, WR-01..08
skipped: 5  # IN-01..05 (Info findings out of scope per --fix critical_warning)
---

# Phase 4: Code Review Report

**Reviewed:** 2026-05-05T17:30:00Z
**Depth:** standard
**Files Reviewed:** 16
**Status:** issues_found

## Summary

Phase 4 ships a clean, well-structured perception layer that honors the
architectural locks from CONTEXT.md (single-worker process pool, long-lived
shared_memory, drop-oldest at ingress, real filterpy Kalman, BoT-SORT track-id
flow, no PID/MediaPipe/EMA, no mocks). The 6-state lock machine is flat,
guard-claused, and correctly exits via the W3 single-frame-gap path; the
PERC-02 weighted centroid lives in exactly one place (`_keypoints.py`); the
HOLDING posterior is genuinely frozen (covariance does not grow); the W4
invariant assertion replaces the previous `or 0` silent fallback.

However, two BLOCKER-class defects were found that contradict the documented
public contract:

1. **`SubjectTracker.tracked_subjects()` is a deadlock** — the async iterator
   awaits `_out_queue.get()` on a queue that nothing in the module ever
   populates. The CONTEXT.md-locked public API surface (`async def
   tracked_subjects(self) -> AsyncIterator[TrackedSubject]`) is therefore
   non-functional. Phase 6 wiring will hang the moment it tries the
   documented iterator pattern.

2. **`UltralyticsPoseEngine.detect` is `raise NotImplementedError`** — the
   production engine cannot run inference. The docstring still says "Plan 03
   implements..." but Plan 03 did not. Phase 4's full-suite tests pass only
   because every code path goes through `FakePoseEngine`; the real YOLO+
   BoT-SORT path is dead. This is documented as a deferral (`04-03-SUMMARY.md`
   line 240-253), but the deferral is to "Phase 8 QA-04" — meanwhile the orchestrator
   in Phases 5/6/7 has no executable production engine to wire to.

Eight WARNING-class defects cover stale documentation, missing `# noqa: BLE001`
on two `except Exception` blocks (which should fail ruff on the documented
config), missing defensive bounds checks at the ultralytics translation seam,
duplicated keypoint constants in `subject_tracker.py` versus `_keypoints.py`,
dead-effect dataclass fields on `_KalmanWrapper`, and a buffer-size invariant
on the shared_memory frame_view that is never validated.

No security or critical-correctness defects were found in the lock state
machine, the Kalman wiring, or the centroid math.

## Blocker Issues

### BL-01: `SubjectTracker.tracked_subjects()` is a deadlock — public contract API is non-functional

**File:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py:207-212`

**Issue:**
```python
async def tracked_subjects(self) -> AsyncIterator[TrackedSubject]:
    """Drain emitted subjects (orchestrator may push from `consume` then iterate)."""
    while True:
        self._raise_if_latched()
        ts = await self._out_queue.get()
        yield ts
```

`_out_queue` is declared at line 149 with `maxsize=_OUT_QUEUE_MAX (256)` but
**nothing in the module ever calls `_out_queue.put()` or `_out_queue.put_nowait()`**.
Confirmed via grep: only the constructor (line 149) and the `get()` on line 211
reference the queue. Calling `async for ts in tracker.tracked_subjects()` will
block forever on `get()`.

This contradicts CONTEXT.md's locked public-API surface ("Public API = `async def
tracked_subjects(self) -> AsyncIterator[TrackedSubject]:` (mirrors Phase 2
`motor.events()` and Phase 3 `camera.frames()`)") and `04-02-SUMMARY.md` line
192-194 which says the queue is "reserved for an `async for ts in
tracker.tracked_subjects()` consumer loop in Plan 03". Plan 03 did not wire
producer-side `put()` calls, so the iterator is dead code that *looks* like
the public surface.

Phase 6's orchestrator will either:
- Call `consume()` and use the returned `TrackedSubject | None` (works) — making
  `tracked_subjects()` redundant; or
- Call `tracked_subjects()` per the CONTEXT lock and hang forever.

**Fix:** Either (a) wire `consume()` to also `await self._out_queue.put(ts)` for
non-`None` emits and drop-oldest on full queue, OR (b) delete
`tracked_subjects()` and `_out_queue` entirely and update the public-API
contract to reflect that `consume()` returns the emit synchronously.

If (a):

```python
async def consume(
    self, detections: list[Detection], now_ns: int,
) -> TrackedSubject | None:
    # ... existing dispatch ...
    result = ...  # current return value
    if result is not None:
        if self._out_queue.full():
            try:
                _ = self._out_queue.get_nowait()
                self._logger.warning(
                    "subject_tracker_output_drop_oldest",
                    queue_size=self._out_queue.maxsize,
                )
            except asyncio.QueueEmpty:
                pass
        await self._out_queue.put(result)
    return result
```

Add a regression test that exercises the iterator on a non-empty stream.

---

### BL-02: `UltralyticsPoseEngine.detect` is `NotImplementedError` — production engine cannot run

**File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:183-187`

**Issue:**
```python
async def detect(self, frame: Frame) -> list[Detection]:
    """Plan 03 implements drop-oldest + executor.submit. Plan 01 NotImplemented."""
    raise NotImplementedError(
        "UltralyticsPoseEngine.detect lands in Plan 04-03 (Wave 3)"
    )
```

The docstring is stale: it claims Plan 03 implements this method, but Plan 03
did not. `04-03-SUMMARY.md` lines 240-253 acknowledge the deferral but route
it to "Phase 8 QA-04". Meanwhile:

- `UltralyticsPoseEngine` is exported at the package surface (`__init__.py:16`,
  `pose_detector.py:44`).
- Phase 4 ships a `PoseDetector` orchestrator that calls `engine.detect(frame)`
  on line 353 of `_infer_one`. Wiring `UltralyticsPoseEngine` into the
  orchestrator (which Phase 6 must do per CONTEXT.md "Pipeline orchestrator
  (Phase 6) wires OBS VCam → FrameSource → PoseDetector → SubjectTracker →
  ...") will translate every frame to `engine detect failed: NotImplementedError`,
  latch FAULTED on the first frame, and stop the pipeline.

The "PERC-01..07 are all green" claim in `04-03-SUMMARY.md` is therefore
predicated on tests that never call the production engine — only `FakePoseEngine`.
PERC-01 ("YOLO11-pose runs in process pool, doesn't block capture") is not
demonstrably satisfied by ANY automated test.

This is a contract gap, not just a coverage gap. The orchestrator-side
plumbing (executor.submit, shm copy-in, future await) was specified in
`04-RESEARCH.md` Pattern 4 + `04-PATTERNS.md` and was the central deliverable
of Plan 04-03 per the plan name ("Wave 3 — PoseDetector Orchestrator").
Plan 04-03 wired the *orchestrator-side* drop-oldest + queue, but skipped the
engine-side submit + shm-copy that closes the loop.

**Fix:** Implement `UltralyticsPoseEngine.detect` per the documented pattern:

```python
async def detect(self, frame: Frame) -> list[Detection]:
    if self._state is not _DetectorState.RUNNING:
        raise PerceptionError(
            f"detect() while state={self._state.value}; expected RUNNING"
        )
    assert self._shm is not None
    assert self._executor is not None
    assert self._loop is not None
    assert self._resolved_device is not None
    # Copy frame bytes into the long-lived shm block (the only copy in the pipeline).
    view = np.ndarray(
        frame.image.shape, dtype=np.uint8, buffer=self._shm.buf
    )
    view[:] = frame.image
    botsort_arg = (
        str(self._config.botsort_yaml_path)
        if self._config.botsort_yaml_path is not None
        else None
    )
    result = await self._loop.run_in_executor(
        self._executor,
        _pose_worker.infer,
        self._shm.name,
        frame.image.shape,
        frame.timestamp_ns,
        self._resolved_device,
        str(self._config.yolo_model_path),
        botsort_arg,
    )
    return [
        Detection.model_validate(d.model_dump()) for d in result.detections
    ]
```

Add a hardware-gated integration test (`pytest --hardware`) that drives a
synthetic 1-frame inference through a real ultralytics model on CPU; even
without a GPU on the dev box, ultralytics + torch CPU is enough to prove
the seam is wired. The CUDA path remains a Phase 8 QA-04 deferral, but the
contract (`detect` actually executes) ships in Phase 4.

## Warnings

### WR-01: Two `except Exception` blocks lack the `# noqa: BLE001` exemption — should fail ruff

**File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:172, 268`

**Issue:** `pyproject.toml` line 55 enables ruff rule `BLE` (`flake8-blind-except`,
which includes BLE001 "Do not catch blind exception: `Exception`"). The four
other `except Exception` sites in this file (lines 287, 295, 339, 365) all
carry `# noqa: BLE001` with documented translator rationale. Lines 172 and 268
do not:

```python
# Line 172 (start, _resolve_device translator):
except Exception as exc:  # documented translator (cross-process boundary)

# Line 268 (PoseDetector.start engine_start translator):
except Exception as exc:
```

The `04-03-SUMMARY.md` claim "ruff src+tests clean" should be re-verified —
either ruff is not catching these (project config oddity) or the summary is
inaccurate. Per CLAUDE.md "Tiger-style fail-fast — no silent except, no bare
except", every `except Exception` must be a documented translator.

**Fix:**

```python
# Line 172:
except Exception as exc:  # noqa: BLE001 -- documented translator (cross-process boundary)

# Line 268:
except Exception as exc:  # noqa: BLE001 -- documented translator (engine.start boundary)
```

Re-run `uv run ruff check src tests` to confirm the rule actually fires before
landing the fix; if ruff was already clean, document why (e.g., a ruff version
or config quirk) so the next reviewer doesn't re-flag it.

---

### WR-02: `_translate` does not bounds-check `track_ids[idx]` against `xyxyn.shape[0]`

**File:** `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py:159-191`

**Issue:** `track_ids` is a `list[int] | None` derived from `boxes.id.int().cpu().tolist()`
when present. The translation loop (line 165) iterates over `range(int(xyxyn.shape[0]))`
and indexes `track_ids[idx]` on line 191:

```python
for idx in range(int(xyxyn.shape[0])):
    ...
    track_id=track_ids[idx] if track_ids is not None else None,
```

Ultralytics is documented to keep `boxes.id`, `boxes.xyxyn`, and
`boxes.conf` aligned in length, but a ReID/track-buffer mismatch in a future
ultralytics version (or a corner case where some detections lack ids) would
raise `IndexError` from inside the worker process. The exception would
propagate back through the `Future`, get caught by `_infer_one`'s generic
`except Exception` translator (good), and latch FAULTED — which is heavy-handed
for what is recoverable. Defensive guard at the seam mirrors the Pitfall 12
clamp pattern the worker already applies for centroid coords.

**Fix:**

```python
track_id_for_idx: int | None = None
if track_ids is not None and idx < len(track_ids):
    track_id_for_idx = track_ids[idx]
det = _PoseDetection(
    ...
    track_id=track_id_for_idx,
)
```

---

### WR-03: `_translate` indexes `results[0]` without bounds check

**File:** `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py:128`

**Issue:** `infer` ends with `return _translate(results[0], timestamp_ns)`.
If `results` is empty (an empty list from ultralytics — rare but
contractually possible, e.g., when a future version returns `[]` for a
zero-detection frame instead of a single-element result), the worker raises
`IndexError`, propagates to `_infer_one`'s generic Exception translator,
and latches FAULTED on a recoverable empty-result frame.

**Fix:**

```python
if not results:
    return PoseEngineResult(detections=[])
return _translate(results[0], timestamp_ns)
```

---

### WR-04: bbox coords from `xyxyn` are NOT clamped to `[0, 1]` even though centroid is

**File:** `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py:166-181`

**Issue:** Lines 178-181 explicitly clamp `cx`/`cy` to `[0, 1]` to honor the
Pydantic `Field(ge=0.0, le=1.0)` validator on `_PoseDetection` ("if the
weighted mean marginally exits the unit square due to fp rounding"). The
same risk applies verbatim to `x1, y1, x2, y2` from `xyxyn` (line 167-170),
which are likewise normalized by ultralytics with floating-point arithmetic
that can yield values like `1.0000001`. No clamp is applied to the bbox
fields, so a marginal out-of-range bbox raises `ValidationError` inside
the worker, propagates as `IndexError`/`ValidationError` through the Future,
and latches FAULTED.

This is the same pitfall Pitfall 12 documents.

**Fix:**

```python
def _clamp01(v: float) -> float:
    return float(min(max(v, 0.0), 1.0))

x1 = _clamp01(float(xyxyn[idx, 0]))
y1 = _clamp01(float(xyxyn[idx, 1]))
x2 = _clamp01(float(xyxyn[idx, 2]))
y2 = _clamp01(float(xyxyn[idx, 3]))
if x2 <= x1 or y2 <= y1:
    continue
```

---

### WR-05: `_pose_worker.infer` does not validate that `shape` fits inside the shared_memory block

**File:** `pastor_tracker/src/pastor_tracker/perception/_pose_worker.py:117`

**Issue:**
```python
frame_view: npt.NDArray[np.uint8] = np.ndarray(shape, dtype=np.uint8, buffer=_shm.buf)
```

The parent allocates the shm block as `capture_width * capture_height * 3` at
`PoseDetector.start()` (`pose_detector.py:150-155`). The worker constructs a
view of arbitrary `shape` against that block. If a caller submits a frame
larger than the allocated block (e.g., camera reconfigured mid-run, or a test
forgets to pass capture-resolution-matching frames), numpy creates a view
that overruns the underlying buffer — undefined behavior, possible segfault,
or silent garbage in the view. The Frame `__post_init__` validates contiguity
and dtype but cannot validate against the shm allocation size from the
opposite process.

**Fix:** Validate at the worker seam before constructing the view.

```python
required_bytes = int(np.prod(shape)) * np.dtype(np.uint8).itemsize
if required_bytes > _shm.size:
    raise ValueError(
        f"frame shape {shape} requires {required_bytes} bytes; "
        f"shm block has only {_shm.size}"
    )
frame_view: npt.NDArray[np.uint8] = np.ndarray(
    shape, dtype=np.uint8, buffer=_shm.buf
)
```

The matching parent-side guard belongs in
`UltralyticsPoseEngine.detect` (currently NotImplementedError, see BL-02);
when that lands, refuse to copy a frame whose `image.nbytes > self._shm.size`.

---

### WR-06: `subject_tracker.py` duplicates COCO keypoint indices and PERC-02 weights from `_keypoints.py`

**File:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py:57-65`

**Issue:** CONTEXT.md locks "single source of truth" for the centroid math in
`_keypoints.weighted_keypoint_centroid`. The math itself is correctly
delegated (`subject_tracker.compute_subject_centroid` calls into `_keypoints`
on line 110-112). BUT `subject_tracker.py` lines 57-65 *also* declare
`_NOSE_KP_INDEX`, `_LEFT_SHOULDER_KP_INDEX`, `_RIGHT_SHOULDER_KP_INDEX`,
`_LEFT_HIP_KP_INDEX`, `_RIGHT_HIP_KP_INDEX`, `_WEIGHT_NOSE`,
`_WEIGHT_SHOULDER_MID`, `_WEIGHT_HIP_MID` as module-level `Final` constants —
all of which are duplicates of the same names in `_keypoints.py`. None of
them are *used* in `subject_tracker.py` (verified — no other reference),
so they are dead duplicate constants.

If PERC-02 weights ever change (e.g., on-stage tuning), only `_keypoints.py`
needs editing. The duplicate constants in `subject_tracker.py` will silently
go stale, presenting a misleading impression that two impls exist.

**Fix:** Delete lines 57-65 from `subject_tracker.py`. The constants live
exclusively in `_keypoints.py`.

---

### WR-07: `_KalmanWrapper` instance fields are never read — silent contract trap for future tuners

**File:** `pastor_tracker/src/pastor_tracker/perception/_kalman.py:96-111`

**Issue:**
```python
@dataclass(frozen=True, slots=True)
class _KalmanWrapper:
    process_noise_var: float = _KALMAN_PROCESS_NOISE_VAR
    measurement_noise_var: float = _KALMAN_MEASUREMENT_NOISE_VAR
    initial_vel_cov: float = _KALMAN_INITIAL_VEL_COV

    def reset_for_new_track(
        self, initial_x: float, initial_y: float, dt: float,
    ) -> KalmanFilter:
        return make_kalman_for_subject(initial_x, initial_y, dt)
```

`reset_for_new_track` does NOT forward `self.process_noise_var`,
`self.measurement_noise_var`, or `self.initial_vel_cov` to
`make_kalman_for_subject`; the factory uses module-level constants directly
(line 68, 70, 76-78). Future code that constructs a `_KalmanWrapper` with
custom values (e.g., a Phase 7 dashboard live-tuning shim) will see those
values silently ignored.

This couples poorly with Open Question 2's resolution ("v1 = module Final
constants; live tuning is Phase 7 dashboard scope"). The dataclass fields
suggest configurability that does not exist.

**Fix:** Either (a) delete the three dataclass fields and make `_KalmanWrapper`
stateless (it's a `@staticmethod` carrier), or (b) thread the fields through
`make_kalman_for_subject` so they actually take effect.

(a) is simpler and matches the current intent:

```python
class _KalmanWrapper:
    """Stateless namespace for Kalman lifecycle helpers."""

    @staticmethod
    def reset_for_new_track(
        initial_x: float, initial_y: float, dt: float,
    ) -> KalmanFilter:
        return make_kalman_for_subject(initial_x, initial_y, dt)

    @staticmethod
    def hold_posterior(kf: KalmanFilter) -> tuple[float, float]:
        x_post = np.asarray(kf.x_post).ravel()
        return float(x_post[0]), float(x_post[1])
```

`SubjectTracker` then calls `_KalmanWrapper.reset_for_new_track(...)` directly
(no instance), matching the existing static call to `hold_posterior` on line
364 of `subject_tracker.py`.

---

### WR-08: `consume` `match` statement has no `case _:` exhaustiveness guard

**File:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py:197-205`

**Issue:**
```python
match self._state:
    case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
        return self._try_lock(eligible, now_ns)
    case _LockState.LOCKED:
        return self._tick_locked(eligible, now_ns)
    case _LockState.HOLDING:
        return self._tick_holding(eligible, now_ns)
    case _LockState.LOST:
        return self._try_reacquire(eligible, now_ns)
```

All six members of `_LockState` are covered — but if the enum gains a member
in a future change (e.g., `_LockState.ERROR` for an explicit fault state), the
`match` falls through and `consume()` returns `None` implicitly without any
state mutation or log. The function-level signature says `-> TrackedSubject | None`,
so mypy stays silent. Tiger-style says fail loudly on contract violations.

**Fix:**

```python
match self._state:
    case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
        return self._try_lock(eligible, now_ns)
    case _LockState.LOCKED:
        return self._tick_locked(eligible, now_ns)
    case _LockState.HOLDING:
        return self._tick_holding(eligible, now_ns)
    case _LockState.LOST:
        return self._try_reacquire(eligible, now_ns)
    case _:
        raise PerceptionError(
            f"unexpected _LockState in consume: {self._state!r}"
        )
```

## Info

### IN-01: `UltralyticsPoseEngine.detect` docstring is stale — claims Plan 03 implements it

**File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:184`

**Issue:** Even if BL-02 is deferred to Phase 8, the docstring "Plan 03
implements drop-oldest + executor.submit. Plan 01 NotImplemented." is
historically inaccurate now that Plan 03 has shipped without doing so.

**Fix:** Update the docstring to point at the actual deferral target and
the SUMMARY entry that records the decision:

```python
async def detect(self, frame: Frame) -> list[Detection]:
    """Production inference path — DEFERRED to Phase 8 QA-04 hardware run.

    See ``.planning/phases/04-perception/04-03-SUMMARY.md`` "Out-of-Scope
    Deferrals" for the deferral rationale and the Plan 04-01 + Plan 04-03
    threat model dispositions for what's required to lift this guard.
    """
    raise NotImplementedError(
        "UltralyticsPoseEngine.detect requires real torch + ultralytics + "
        "GPU/model weights; deferred to Phase 8 QA-04"
    )
```

---

### IN-02: `PoseDetector.start` "called twice" error message is misleading after FAULTED/CLOSED

**File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:251-254`

**Issue:**
```python
if self._state is not _DetectorState.DISCONNECTED:
    raise PerceptionError(
        f"start() called twice (state={self._state.value})"
    )
```

A caller who restarted after a fault sees `state=faulted` paired with the
text "start() called twice" — the message implies a double-call when the
real situation is "single-shot lifecycle, cannot resume after fault".

**Fix:**

```python
if self._state is not _DetectorState.DISCONNECTED:
    raise PerceptionError(
        f"start() called on non-DISCONNECTED state={self._state.value}; "
        f"PoseDetector is single-shot — construct a fresh instance"
    )
```

Same wording applies to `UltralyticsPoseEngine.start` at line 137-140.

---

### IN-03: `_resolve_device("auto")` does not log the resolution path

**File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:79-101`

**Issue:** `UltralyticsPoseEngine.start` line 145-149 logs
`pose_engine_device_resolved` after calling `_resolve_device`. That covers
the production engine. But `_resolve_device` itself is a module-level helper
that future callers (Phase 7 dashboard, debugging tools) might invoke
directly; it silently returns `"cpu"` when `"auto"` is requested but CUDA
is missing, with no operator-visible signal. Mirroring the documented
"WARN if cuda was implicit-fallback" pattern at the helper level is cheap
defensive logging.

**Fix:** Move the resolution log into `_resolve_device` itself, or document
that callers must log the resolution. Minor — current single call site is
fine.

---

### IN-04: `_kalman.py` constants `_KALMAN_DIM_X` and `_KALMAN_DIM_Z` shadow named values used as args

**File:** `pastor_tracker/src/pastor_tracker/perception/_kalman.py:36-37, 51`

**Issue:** Module-level constants are good. But the literal `2` is also
used (line 68 in `Q_discrete_white_noise(dim=2, ...)`) — the per-axis dim,
which is distinct from `_KALMAN_DIM_Z`. Two different `2`s with
different meaning sit unannotated.

**Fix:** Add `_KALMAN_PER_AXIS_DIM: Final[int] = 2  # x and vx per axis` and
use it in `Q_discrete_white_noise(dim=_KALMAN_PER_AXIS_DIM, ...)`.

---

### IN-05: `pose_traces.FailingPoseEngine.detect` imports `PerceptionError` per call

**File:** `pastor_tracker/tests/fixtures/pose_traces.py:124-128`

**Issue:** The local import inside `detect` is documented as cycle-avoidance
("avoids a fixtures->perception import cycle at collect-time"). On the call
hot path this re-imports the module on every call — Python caches the
import after the first hit so the cost is minimal, but the fixtures-side
collect-time cycle is best resolved by importing inside `__init__` (still
post-collect) rather than inside the hot method.

**Fix:**

```python
class FailingPoseEngine:
    def __init__(self, *, error_message: str = "simulated engine fault") -> None:
        from pastor_tracker.perception.pose_detector import PerceptionError
        self._error_cls = PerceptionError
        self._closed = False
        self._msg = error_message

    async def detect(self, frame: Frame) -> list[Detection]:
        raise self._error_cls(self._msg)
```

---

_Reviewed: 2026-05-05T17:30:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
