---
phase: 03-camera-i-o
reviewed: 2026-05-04T23:55:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/io/obs_camera.py
  - pastor_tracker/tests/fixtures/camera_traces.py
  - pastor_tracker/tests/test_obs_camera_discovery.py
  - pastor_tracker/tests/test_obs_camera_lifecycle.py
  - pastor_tracker/tests/test_obs_camera_stale_drop.py
  - pastor_tracker/tests/test_obs_camera_fallback.py
  - pastor_tracker/tests/test_obs_camera_stall.py
  - pastor_tracker/pyproject.toml
findings:
  critical: 0
  warning: 0
  info: 0
  total: 0
status: clean
---

# Phase 3: Code Review Report (Iteration 2)

**Reviewed:** 2026-05-04T23:55:00Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** clean

## Summary

Iteration 2 re-review of `pastor_tracker/io/obs_camera.py` and the
seven supporting test/fixture/config files after the iteration-1
fix loop closed CR-01..CR-03 and WR-01..WR-07 (per
`03-REVIEW-FIX.iter2.md`). All ten in-scope findings are resolved
and no new defects were introduced.

**CR-01 (fallback factory unwrapped):** `_maybe_fallback`
(`obs_camera.py:910-925`) now wraps the post-release factory call
in `try/except Exception # noqa: BLE001`, logs
`camera_fallback_factory_failed`, and routes through
`_fault_with_stall(exc)`. Pinned by
`test_fallback_factory_exception_latches_stall_error`, which
asserts `cam.last_error` is `CameraStallError`, `cam.state ==
FAULTED`, and the reopen-history reason cites the simulated
factory exception.

**CR-02 (fallback false-stall trip):** `_maybe_fallback` now
returns `bool` and `_capture_loop` (`obs_camera.py:683-695`)
resets `last_grab_ns = None; continue` on a successful fallback.
Mirrors the existing stall-recovery pattern at the reopen sites.
Pinned by `test_fallback_does_not_trip_stall_on_slow_first_frame`,
which uses a 400 ms post-fallback first-read against a 300 ms
compressed stall threshold.

**CR-03 (start swallows pre-thread errors / stop clobbers
FAULTED):** Three coupled fixes verified:
1. `start()` (`obs_camera.py:493-516`) latches `_state = FAULTED`
   + `_latched_error` for both `OBSCameraNotFoundError`
   (re-raised typed) and the factory `Exception` translator
   (wrapped in `CameraOpenError(...) from exc`).
2. `start()` (`obs_camera.py:556-557`) gates the final RUNNING
   promotion behind `if self._state is _CamState.OPENING`, so a
   capture-thread fault that races ahead of the loop-thread
   promotion is preserved.
3. `stop()` (`obs_camera.py:593-594`) gates the CLOSED
   transition behind `if self._state is not _CamState.FAULTED`,
   so terminal-fault diagnostics on `last_error` agree with
   `state` after a user-initiated cleanup.

**WR-01 (silent thread exit on source=None):** Replaced silent
`return` with `_fault_with_stall(RuntimeError(...))`, and
`_fault_with_stall` itself now sets `_stop_event`. The
self-terminating `_stop_event.set()` also closes the corner case
where a fault raised by `_maybe_fallback`'s factory translator
(which does NOT itself `return` from `_capture_loop`) would
otherwise re-fire `_fault_with_stall` on the WR-01 guard and
clobber the original latched error.

**WR-02 (final reopen iteration leaks handle):** Added the
symmetric `self._source.release(); self._source = None` after
the final iteration's failure log (`obs_camera.py:857-858`).

**WR-03 (start timeout path leaves capture_thread non-None):**
Added `self._capture_thread = None` after the join in the
timeout branch (`obs_camera.py:536`). A subsequent `stop()` for
cleanup no longer re-joins the same thread.

**WR-04 (REOPENING state declared but unused):** `_attempt_reopen`
(`obs_camera.py:790-792, 833-834`) now transitions RUNNING ->
REOPENING on entry and back to RUNNING on success, with guards
that never overwrite a terminal state that raced ahead via
`_on_capture_failed`. The pre-existing
`test_stall_reopen_succeeds_attempt_1` already hedges
`cam.state in (RUNNING, REOPENING)`.

**WR-05 (warmup window boundary off-by-one):** Gate now uses
strict `>` (`obs_camera.py:683`), pinned by the source-level
regression `test_warmup_window_is_half_open`.

**WR-06 (frames hangs forever if producer dies):**
`frames()` (`obs_camera.py:967-991`) wraps the queue get in
`asyncio.wait_for(..., timeout=_FIRST_FRAME_TIMEOUT_SEC)` and
synthesises a typed `CameraStallError` only when the capture
thread is dead AND no error is latched. The asymmetric "thread
alive OR latched-error race -> continue" branch is correct: a
latched error is raised by the next iteration's empty-check.

**WR-07 (unreachable QueueEmpty suppression):** Removed
`contextlib.suppress(asyncio.QueueEmpty)`; `_enqueue_frame`
(`obs_camera.py:706-721`) now relies on the `full()` guard alone
and the unused `import contextlib` is gone.

## Regression Hunt

Adversarial trace of all changed paths surfaced no new defects:

* **`_fault_with_stall` self-terminating set** -- confirmed safe
  for every caller. Capture-loop sites that already `return` are
  unaffected; the `_maybe_fallback` factory-exception caller now
  terminates on the next loop iteration via the `_stop_event`
  check rather than re-firing through the WR-01 source=None
  guard.
* **Single-consumer queue invariant** -- `_enqueue_frame` runs
  on the loop thread via `call_soon_threadsafe`; the only other
  queue accessors are in `frames()` (loop thread) and the
  test-only direct `cam._frames_queue.put_nowait` calls in
  `test_obs_camera_stale_drop.py` (also loop thread). The
  `full()`-then-`get_nowait()` sequence is safe.
* **CR-03 start translator scope** -- the `except Exception` on
  the factory call could in principle re-wrap a `CameraError`
  raised inside the factory, producing
  `CameraOpenError("VideoCapture open failed: <CameraOpenError>")`.
  This is documented as the typed translator at the boundary;
  not a defect.
* **WR-04 thread-safety** -- `_state` mutations from the
  capture thread (REOPENING / RUNNING) are unlocked but Python
  attribute assignment is atomic and the readers
  (`is_running`, dashboard) only observe a consistent snapshot.
  The capture thread never writes FAULTED; only
  `_on_capture_failed` (loop thread) does, so the "raced-ahead
  terminal state" guard at line 833 is sufficient.
* **WR-06 watchdog vs. fault latch race** -- the 3 s
  `_FIRST_FRAME_TIMEOUT_SEC` budget vastly exceeds the
  `call_soon_threadsafe` callback latency (microseconds), so
  the synthetic `CameraStallError` cannot in practice clobber a
  real latched error. The asymmetric "latched-error race ->
  continue" branch defers the raise to the next iteration's
  empty-check even under worst-case scheduling stalls.
* **`asyncio.wait_for` `TimeoutError` alias** -- Python 3.12
  unifies `asyncio.TimeoutError` and built-in `TimeoutError`;
  the bare `except TimeoutError` is correct.
* **`filterwarnings = ["error"]`** in pytest config -- no new
  `DeprecationWarning` paths introduced by this iteration; the
  iteration-1 240-test baseline confirms.

## Out-of-Scope Observations

The iteration-1 review contained four IN-level items
(IN-01..IN-04) that were explicitly out of `fix_scope:
critical_warning`. They remain unaddressed but are non-blocking
and were not in scope for this re-review.

---

_Reviewed: 2026-05-04T23:55:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
