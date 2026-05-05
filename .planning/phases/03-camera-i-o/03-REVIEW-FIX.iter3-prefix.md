---
phase: 03-camera-i-o
fixed_at: 2026-05-04T23:43:00Z
review_path: .planning/phases/03-camera-i-o/03-REVIEW.md
iteration: 1
findings_in_scope: 10
fixed: 10
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-05-04T23:43:00Z
**Source review:** `.planning/phases/03-camera-i-o/03-REVIEW.md`
**Iteration:** 1
**Fix scope:** critical_warning (CR-01..CR-03 + WR-01..WR-07)

**Summary:**
- Findings in scope: 10 (3 critical + 7 warning)
- Fixed: 10
- Skipped: 0

## Verification Snapshot

| Gate | Status |
|---|---|
| pytest (full suite, 240 tests) | passed |
| ruff check src tests | clean |
| mypy --strict src | clean |
| New regression tests added | 3 (CR-01, CR-02, WR-05) |
| Pre-existing test fixed by CR-03 | 1 (test_capture_thread_exception_latches_stall_error -- was timing-flaky against the FAULTED-vs-RUNNING race that CR-03 closes) |

The 237-test baseline grew to 240 with the new regressions; all
240 pass on every run since CR-03 landed.

## Fixed Issues

### CR-01: `_maybe_fallback` factory call not wrapped -- silent thread death on cv2 open error

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`
- `pastor_tracker/tests/test_obs_camera_fallback.py`

**Commit:** `70ed036`

**Applied fix:** Wrapped the post-release fallback factory call in
a `try/except Exception` translator with `# noqa: BLE001`,
mirroring the existing translators in `_capture_loop` and
`_attempt_reopen`. On factory failure, log
`camera_fallback_factory_failed` and route through
`_fault_with_stall(exc)` so the typed `CameraStallError` surfaces
on `cam.last_error` and `cam.state` transitions to FAULTED.
Added regression test `test_fallback_factory_exception_latches_stall_error`
modelled on the existing `test_reopen_factory_exception_recorded_in_history`.

---

### CR-02: Fallback transition deterministically trips false stall detection in production

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`
- `pastor_tracker/tests/test_obs_camera_fallback.py`

**Commit:** `a7f0d31`

**Applied fix:** Took the reviewer's preferred Option A. Changed
`_maybe_fallback` to return `bool` (True iff the source was
actually swapped). `_capture_loop` now branches on that signal:
when fallback fires, `last_grab_ns = None; continue` -- mirroring
the stall-recovery pattern at the existing reopen sites
(lines 597-598 / 617-618). This prevents the new 720p source's
CAP_DSHOW first-frame latency (documented as up to 3 s) from
being charged against the pre-fallback grab timestamp and
tripping the 200 ms stall detector.

Added regression test `test_fallback_does_not_trip_stall_on_slow_first_frame`
that pins a 400 ms simulated first-frame latency on the post-
fallback source. Verified by stashing the fix: the regression
test correctly fires `camera_stall_detected` with `delta_ms=400`,
so the test exercises the buggy code path.

---

### CR-03: `start()` swallows pre-thread errors and `stop()` clobbers `_CamState.FAULTED`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `33d2ea2`

**Applied fix:** Three coupled changes:

1. **start() pre-thread errors:** Wrapped `discover_obs_camera_index`
   in `try/except OBSCameraNotFoundError` and the initial
   `_video_source_factory` call in `try/except Exception`. Both
   handlers latch `_state = _CamState.FAULTED` and `_latched_error`
   before re-raising, so the dashboard can distinguish "failed at
   discovery" from "still opening". The factory translator uses
   `raise CameraOpenError(...) from exc` (BLE001 is exempt because
   the typed re-raise IS the translation).

2. **start() RUNNING transition guard:** The final
   `self._state = _CamState.RUNNING` is now gated by
   `if self._state is _CamState.OPENING`. This prevents the loop
   thread from clobbering a FAULTED state that the capture thread
   raced ahead to latch (e.g. a `read()` that raises immediately
   after the first successful frame). This race was previously
   surfaced by `test_capture_thread_exception_latches_stall_error`
   failing intermittently on the worktree (1/3 runs).

3. **stop() FAULTED preservation:** The unconditional
   `self._state = _CamState.CLOSED` is now gated by
   `if self._state is not _CamState.FAULTED`. Terminal fault
   diagnostics no longer disagree with `last_error` after the
   user-initiated `stop()` for cleanup.

**Note (logic / state-machine):** Per `<verification_strategy>` in
the agent contract, this fix touches the lifecycle state machine
and state-transition guards. Tier 1 (re-read) and Tier 2 (mypy
+ ruff + 240-test suite) all pass and the previously-flaky test
`test_capture_thread_exception_latches_stall_error` now passes
deterministically. Logic correctness is well-pinned by the
existing test surface, but **flagging for human verification**
of the FAULTED-preservation contract: any future test that
asserts `state == CLOSED` after a fault path must be updated
(none currently exist; `test_start_then_stop_clean` only asserts
CLOSED on the success path).

---

### WR-01: Silent thread exit when `self._source is None`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `9f544f0`

**Applied fix:** Replaced the silent `return` on `self._source is None`
with `_fault_with_stall(RuntimeError("capture loop observed source=None; recovery path bug"))`
+ `return`. Per CLAUDE.md rule 1 (tiger-style: fail fast, fail
loud), defensive silent suppression is forbidden.

Also hardened `_fault_with_stall` to set `_stop_event` after
dispatch -- without this, a fault raised mid-loop (e.g.
`_maybe_fallback`'s factory-exception path which does NOT itself
return from `_capture_loop`) lets the loop continue, hit the new
`source=None` guard, and re-fire `_fault_with_stall`, clobbering
the original latched error with a generic 'source=None'
RuntimeError. This was caught when `test_fallback_factory_exception_latches_stall_error`
(the CR-01 regression) failed after introducing the WR-01 guard.
The `_stop_event.set()` makes `_fault_with_stall` self-terminating
across all call sites.

No new test added -- the WR-01 path is unreachable today (CR-01
closed the only known route), so a test would have to manufacture
an unreachable state.

---

### WR-02: `_attempt_reopen` does not release the final failed source

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `70bb64d`

**Applied fix:** Appended the symmetric `self._source.release(); self._source = None`
after the FINAL iteration's `camera_reopen_attempt_failed` log,
matching the release pattern at the top of each loop body
(line 697-699). The 3rd-attempt failure no longer leaks the
VideoCapture handle until `stop()` runs.

---

### WR-03: `start()` first-frame timeout path does not null `self._capture_thread`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `dd852f2`

**Applied fix:** Added `self._capture_thread = None` after the
join in the timeout branch, mirroring stop()'s symmetric
join-then-null block. A subsequent `stop()` for cleanup no
longer logs a redundant `camera_thread_exited` for the already-
joined thread.

---

### WR-04: `_CamState.REOPENING` declared but never assigned

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `68f9a84`

**Applied fix:** Took the wire-it-in option (the plan documents
lock all 6 states in `03-01-PLAN.md` and `03-PATTERNS.md`).
`_attempt_reopen` now sets `_state = _CamState.REOPENING` on
entry (only when prior state is RUNNING -- never clobber a
terminal state) and back to RUNNING on success (only when state
is still REOPENING). On failure the loop returns False and the
caller calls `_fault_with_stall`, which transitions
REOPENING -> FAULTED via `_on_capture_failed`.

`is_running` (which only checks `state is RUNNING`) correctly
returns False during REOPENING -- consumers querying
"should I read a frame now?" get the right answer. Python
attribute writes are atomic so no lock is needed for the
daemon-thread mutation.

The pre-existing `test_stall_reopen_succeeds_attempt_1` line 141
already hedges with `cam.state in (_CamState.RUNNING, _CamState.REOPENING)`
anticipating this transition; that test still passes.

---

### WR-05: Warmup-window boundary uses `>= 0` instead of `> 0`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`
- `pastor_tracker/tests/test_obs_camera_fallback.py`

**Commit:** `eba7978`

**Applied fix:** Changed the gate from `warmup_remaining_ns >= 0`
to `warmup_remaining_ns > 0`. Half-open semantics `[0, W)` --
the exact boundary instant `now_ns - warmup_started_ns ==
_WARMUP_WINDOW_SEC * _NS_PER_SEC` now counts as 'window expired'
rather than 'last-chance fire'. CONTEXT.md "2 s warmup window"
reads as 'inside warmup'.

Added unit-level regression `test_warmup_window_is_half_open`
that uses `inspect.getsource(ObsCamera._capture_loop)` to assert
`"warmup_remaining_ns > 0"` is present and `"warmup_remaining_ns >= 0"`
is absent. This is a string-level guard rather than a fragile
timing-sensitive integration test pinned to the boundary.

---

### WR-06: `frames()` raises latched error after queue drain -- but the queue may NEVER drain if producer dies

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `696d0fa`

**Applied fix:** Wrapped `_frames_queue.get()` in
`asyncio.wait_for(..., timeout=_FIRST_FRAME_TIMEOUT_SEC)`. On
`TimeoutError`: if `self._capture_thread is None or not is_alive()`
AND `self._latched_error is None`, synthesize a typed
`CameraStallError(attempts=[(0, 0, "capture thread dead, no error latched")])`.
Otherwise (thread alive, OR error latched and racing the next
iteration's empty-check), continue waiting. The synthetic stall
exposes the silent-death symptom on the consumer side rather
than hanging the event loop.

Today the path is unreachable (CR-01 + WR-01 closed every known
route); the watchdog is defense-in-depth against future
regressions.

---

### WR-07: `_enqueue_frame` `contextlib.suppress(asyncio.QueueEmpty)` is unreachable

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py`

**Commit:** `5d578e5`

**Applied fix:** Removed the `with contextlib.suppress(asyncio.QueueEmpty):`
wrapper around `self._frames_queue.get_nowait()`. The block is
gated by `self._frames_queue.full()`, so the queue is non-empty
by definition; `get_nowait()` cannot raise `QueueEmpty`. Per
CLAUDE.md rule 1 (tiger-style: fail fast, fail loud), the
suppression was masking a contract bug. If a future refactor
drops the `full()` guard, an unexpected `QueueEmpty` will now
propagate.

Also dropped the now-unused `import contextlib`.

## Skipped Issues

None -- all 10 in-scope findings (3 critical + 7 warning) were
fixed and committed atomically. Info-level findings (IN-01..IN-04)
were out of scope per `fix_scope: critical_warning`.

---

_Fixed: 2026-05-04T23:43:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
