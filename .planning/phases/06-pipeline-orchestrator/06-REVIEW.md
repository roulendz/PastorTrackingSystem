---
status: clean
phase: 06-pipeline-orchestrator
reviewed: 2026-05-08T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/pipeline.py
  - pastor_tracker/src/pastor_tracker/__main__.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/src/pastor_tracker/perception/pose_detector.py
  - pastor_tracker/tests/test_pipeline.py
  - pastor_tracker/tests/fixtures/pipeline_helpers.py
  - pastor_tracker/tests/test_pose_detector.py
  - pastor_tracker/tests/test_types.py
findings:
  critical: 0
  warning: 6
  info: 5
  total: 11
warnings_resolved: 6
info_deferred: 5
---

# Phase 6 Pipeline Orchestrator — Code Review

Adversarial review of the 8-stage asyncio orchestrator (pipeline.py + `__main__.py`
refactor + supporting types/fixtures/tests). Focus per scope: concurrency,
lifecycle table coverage, CLAUDE.md compliance, tiger-style boundaries, pure-core
discipline, resource cleanup, test integrity.

The implementation is generally tight: the 18-cell `_LIFECYCLE_TABLE` is
table-driven, `_HOME_TARGET_DEG` and the firmware-PID defaults are named
constants (rule 6), structlog is the only logging surface, no `print()` / bare
`except:` / `Any` leaked into production code, `_dispatch_enabled` gating is
a single bool with one writer, and the `e_stop` inline-send pattern correctly
bypasses the tick task. The `__main__` SIGINT branch handles the
ProactorEventLoop pitfall correctly. Findings below are real defects worth
fixing before Phase 7 integration; none are immediate ship-blockers because
the surface is private-by-default behind a single Pipeline class.

## Warnings

### WR-01: `home()` leaves state stuck in HOMING + dispatch silenced if `motor.send_home()` raises
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:447-455`
- **Problem:** `self._state = HOMING` and `self._dispatch_enabled = False` are
  set BEFORE `await self._motor.send_home()`. If the motor raises (e.g.
  `LinkLostError` from a USB unplug between line 451 and the await), the
  exception propagates and the back-edge `self._state = PAUSED` at line 454
  never runs. Pipeline is now wedged in HOMING — the only valid public
  transitions out are `e_stop` and `quit`; the operator cannot `resume()` or
  re-`start()`. Worse, `_dispatch_enabled` stays False indefinitely.
- **Fix:** Wrap the await in try/finally that always restores PAUSED on
  success and routes failure through the existing fail-fast path
  (transition STOPPED + log + re-raise), or send_home BEFORE the state
  mutation so a raise leaves state unchanged at the source state.
  ```python
  try:
      await self._motor.send_home()
  except Exception:
      # tiger-style: collapse to STOPPED so done-callback path
      # produces a single recovery contract.
      self._state = _PipelineState.STOPPED
      self._dispatch_enabled = False
      raise
  self._state = _PipelineState.PAUSED
  ```

### WR-02: `e_stop()` partial-failure leaves state unchanged but `send_emergency_stop` may have already silenced the wire
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:469-479`
- **Problem:** Inverse of WR-01 but the asymmetry matters: line 471 awaits
  `motor.send_emergency_stop()` BEFORE setting `_dispatch_enabled = False`
  and `_state = E_STOPPED`. If the send raises (transport error, link lost
  mid-call), the firmware-side e-stop semantics are indeterminate (the byte
  may or may not have flushed) but the orchestrator state still reads
  RUNNING and dispatch is still enabled — the next tick will happily issue
  another `M:` line on the same potentially-degraded wire. Operator clicks
  e-stop, sees no state change, the tick keeps driving the motor.
- **Fix:** After any exception from `send_emergency_stop`, force
  `_state = E_STOPPED` and `_dispatch_enabled = False` regardless, then
  re-raise. Safety: e-stop intent was issued; orchestrator must reflect
  that intent even when the wire failed.

### WR-03: Frame/Detection mis-pairing in `PoseDetector.stream` under output-queue overflow drops
- **File:** `pastor_tracker/src/pastor_tracker/perception/pose_detector.py:444-454, 510-562`
- **Problem:** `stream()` maintains a `pending_frames` deque under the
  invariant "head entry pairs with the next detection out of `_out_queue`".
  Two paths break that invariant:
  1. `_infer_one` (line 445) drops the OLDEST entry from `_out_queue` on
     output-queue overflow but does NOT pop from `pending_frames`. Next
     `popleft()` pairs detection N with the frame that produced N-1.
  2. The pump's `if self._inflight is not None and not self._inflight.done()`
     pop heuristic (line 529-534) only fires when the previous inflight is
     still in-flight at the moment of new-frame arrival. If the previous
     completed but the result is still queued (possible when the consumer
     side of `stream` is slow), the deque grows without bound and pairing
     drifts permanently.
- **Impact:** the orchestrator uses `frame.timestamp_ns` as `now_ns`
  (D-04). A drift of one frame = ~33ms of clock skew fed to every
  downstream `consume(...)`/`decide(...)` call — directly corrupts the
  Phase 5 dampers' dt math and the dispatcher's interval gate. Silent.
- **Fix:** Pop `pending_frames.popleft()` whenever `_out_queue.get_nowait()`
  drops a result (mirror the deque write where the queue is written), and
  add a `pending_frames.maxlen=_OUT_QUEUE_MAX` invariant assertion in the
  pump branch. Property test that `len(yielded) + drops == frames_in`.

### WR-04: Signal handler is never uninstalled; late SIGINT after loop close raises on `call_soon_threadsafe`
- **File:** `pastor_tracker/src/pastor_tracker/__main__.py:247-270`
- **Problem:** `signal.signal(SIGINT, _on_signal)` (Windows) and
  `loop.add_signal_handler(SIGINT, _on_signal)` (POSIX) are installed inside
  `_amain` but never restored. The handler captures `loop = asyncio.get_running_loop()`
  by closure. After `asyncio.run` returns (loop closed), a late SIGINT on
  Windows fires the handler, which calls `loop.call_soon_threadsafe(...)`
  on a closed loop → `RuntimeError: Event loop is closed`. Single-shot CLI
  mostly hides this, but pytest reuses the process and a stale handler
  surviving across `_amain` invocations is a real test-flake source.
- **Fix:** `try/finally` the signal install: capture `signal.signal(SIGINT, prev)`
  in finally on Windows; `loop.remove_signal_handler(SIGINT)` on POSIX.
  Alternatively guard the handler body: `if loop.is_closed(): return`.

### WR-05: `quit()` swallows ALL exceptions from camera/detector/motor close — masks resource leaks
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:501-506`
- **Problem:** Three back-to-back `with contextlib.suppress(Exception):`
  blocks. CLAUDE.md tiger-style §1 forbids silent excepts. The intent
  (drain must not block on a close failure) is sound, but `suppress(Exception)`
  also eats `MemoryError`, `OSError`, `KeyboardInterrupt`-equivalent
  asyncio exceptions, and — crucially — it eats `PerceptionError` when
  `detector.stop()` latches FAULTED on engine-close failure (the path
  `pose_detector.py:362-375` already logs at WARN, but this layer drops
  the secondary fault entirely). No structured `pipeline_quit_close_failed`
  event is emitted. An operator post-mortem cannot tell whether the
  hardware went down clean or whether a close raised.
- **Fix:** Replace each `suppress(Exception)` with explicit
  `try/except Exception as exc: self._logger.warning("pipeline_quit_close_failed", subsystem=..., exc_info=True)`.
  Mirror the documented translator pattern used in `pose_detector.py:356-360`.

### WR-06: `_force_state(E_STOPPED)` issues real `E` byte every parametrized test → leaks into other-test wire-state assertions
- **File:** `pastor_tracker/tests/test_pipeline.py:173-176`
- **Problem:** `_force_state` for E_STOPPED calls `pipeline.e_stop()`, which
  sends a real `E` line into the shared `FakeSerialTransport.captured_writes`.
  Other tests that assert on captured-write counts (e.g.
  `test_pause_suppresses_motor_send_but_tick_continues`) get fresh
  pipelines per test so this does not currently cross-contaminate, but the
  `_VALID_LIFECYCLE_PAIRS` parametrize iterates all 18 cells and any
  future test that uses `_has_command(..., b"E")` as a diagnostic will
  see false-positives when the source state is E_STOPPED. There is no
  per-cell reset of the write buffer.
- **Fix:** In `test_lifecycle_*_transitions`, snapshot the buffer length
  AFTER `_force_state` and assert deltas against the snapshot — or
  document that the buffer is "post-force-state" and add a buffer-clear
  helper on FakeSerialTransport. The current pattern works only by
  accident of which assertions exist today.

## Info

### IN-01: `e_stop()` idempotent path re-issues `E` byte on every duplicate call
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:465-468`
- **Problem:** Comment "re-send E for safety" is reasonable, but a Phase 7
  hotkey hammered three times in a second writes `E\nE\nE\n` to the wire
  inside the 200 ms heartbeat window. Firmware accepts duplicate E but log
  noise from the structured event explosion (one log per call) is real.
- **Fix:** Either gate on a per-call "last_e_stop_ts_ns" within
  the heartbeat budget, or accept this as the documented contract and
  remove the "for safety" justification — the firmware has its own
  watchdog; second-byte safety is symbolic.

### IN-02: `test_stream_drops_stale_internally` assertion is trivially satisfied
- **File:** `pastor_tracker/tests/test_pose_detector.py:480-487` (relative to diff)
- **Problem:** The test asserts `yielded <= 5` where `5` is the input
  frame count — that bound is structurally enforced by the detector and
  cannot be exceeded regardless of drop-oldest behavior. The real
  invariant under test ("drops actually reduce yields") is checked only
  by the looser `len(drop_events) >= 1`. If the drop semantics regress
  to "queue all, drop nothing" the count assertion still passes.
- **Fix:** Assert `yielded < 5` (strict) AND `yielded + len(drop_events) <= 5`.

### IN-03: `_pipeline_with_fakes` has the test-only `pipeline._config = ...` patch contract — fragile
- **File:** `pastor_tracker/tests/test_pipeline.py:508-514`
- **Problem:** `cast(Config, _build_home_reject_config_namespace(...))` is
  honest about the type-system bypass, but the duck-typed
  SimpleNamespace only carries 2 fields. If `home()` ever reads a third
  Config field (e.g. for a future home-velocity check) the test will
  crash with `AttributeError` at the line `pipeline._config = cast(...)`.
  The fixture docstring says "verified against pipeline.py:419-423 on
  2026-05-08" — that timestamp will silently rot.
- **Fix:** Add an assertion in `_build_home_reject_config_namespace` that
  introspects `Pipeline.home`'s source for `self._config.<attr>` reads
  and fails fast if the attribute set drifted. Or, simpler: provide all
  Config fields the namespace might ever need, derived via
  `dataclasses.asdict(Config(**_make_valid_config_dict()).model_dump())`
  with the two limit fields overridden.

### IN-04: `_DEFERRED_VALID_CELLS` documents `(E_STOPPED, "start")` as a known table-vs-impl gap — but the table still lists it as valid
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:122` and `tests/test_pipeline.py:145-149`
- **Problem:** The lifecycle table claims `(E_STOPPED, "start") → RUNNING`
  is valid (D-15 contract), but the implementation re-calls single-shot
  `motor.start()` which raises. A Phase 7 dashboard reading the public
  table to build its hotkey enable/disable matrix will offer "start" to
  the operator from E_STOPPED and crash on the first click. The
  test SKIPs the cell with a deferred reason — but the lie is in the
  table itself, not in the test.
- **Fix:** Either remove the cell from `_LIFECYCLE_TABLE` (and update
  CONTEXT.md D-15 to say re-arm requires `quit()` + fresh Pipeline per
  D-18), or fix `motor.start()` to be re-entrant. Do not ship a
  contradiction between table and impl.

### IN-05: `_on_tick_task_done` clean-drain path latches STOPPED but leaves `_tick_task` reference live
- **File:** `pastor_tracker/src/pastor_tracker/pipeline.py:322-329`
- **Problem:** When the camera iterator naturally exhausts (test fakes
  drained), the tick task completes cleanly, the callback latches STOPPED,
  but `self._tick_task` is not cleared. A subsequent `quit()` will hit
  the `if self._tick_task is not None and not self._tick_task.done():`
  guard at line 496 — the `not done()` check protects from re-cancel,
  so `quit()` works. But `start()` from STOPPED has no such guard: it
  will overwrite `self._tick_task` (line 391) without retrieving the
  old task's result, meaning if the prior drained with an exception
  hidden in the cache (it cannot today, but the path is structurally
  available) it would be lost.
- **Fix:** Set `self._tick_task = None` in both branches of
  `_on_tick_task_done` (clean-drain AND exception). Defensive only;
  no current bug fires.

---

## Fix Log

All 6 warnings resolved on 2026-05-08; Info findings (IN-01..IN-05) deferred
per default review-fix scope (`critical_warning`).

| Finding | Commit | Summary |
|---------|--------|---------|
| WR-01   | `d6ceb6a` | `home()`: try/except around `motor.send_home()`; collapse to STOPPED on failure so the done-callback recovery path (D-13/D-14) is the single recovery contract. |
| WR-02   | `2b2b845` | `e_stop()`: latch E_STOPPED + silence dispatch BEFORE the wire send; a partial-fail still locks operator-visible state and the next tick cannot fire another `M:` line. |
| WR-03   | `c1607f3` | `PoseDetector`: track output-queue head drops in `_output_drops_pending`; `stream()` drains the counter and pops `pending_frames` head entries to keep the frame<->detection pairing aligned (preserves D-04 tick-clock integrity). |
| WR-04   | `084a9d6` | `__main__._amain`: extract install + uninstall into `_install_shutdown_signals` / `_uninstall_shutdown_signals` helpers; outer `try/finally` guarantees uninstall so a late SIGINT cannot fire `call_soon_threadsafe` on a closed loop in pytest reuse. |
| WR-05   | `79b03ab` | `pipeline.quit()`: replace 3x `contextlib.suppress(Exception)` with explicit `try/except Exception: log.warning("pipeline_quit_close_failed", subsystem=..., exc_info=True)` per CLAUDE.md tiger-style §1; quit stays idempotent and reaches every close call. |
| WR-06   | `646957c` | `_force_state(E_STOPPED)`: directly mutate `pipeline._state = E_STOPPED` + `_dispatch_enabled = False` instead of calling `pipeline.e_stop()`; eliminates the leaked `E` byte in the shared FakeSerialTransport buffer. |

**Validation gates after all 6 fixes** (run inside isolated worktree):
- `mypy --strict src` -> clean (27 source files)
- `ruff check src tests` -> clean
- `pytest tests` -> 422 passed + 1 skipped + 1 known geometry flake
  (`test_edges_map_to_half_fov`); identical to pre-fix baseline counts
  (422 + 1 + 1 known flake). No regressions.

---

_Reviewed: 2026-05-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Fixed: 2026-05-08 (all 6 warnings; status -> clean)_
_Fixer: Claude (gsd-code-fixer)_
