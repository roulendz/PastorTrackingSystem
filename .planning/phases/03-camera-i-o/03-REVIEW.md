---
phase: 03-camera-i-o
reviewed: 2026-05-04T18:00:00Z
depth: deep
iteration: 4
files_reviewed: 12
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/io/obs_camera.py
  - pastor_tracker/src/pastor_tracker/io/arduino_motor.py
  - pastor_tracker/src/pastor_tracker/io/arduino_protocol.py
  - pastor_tracker/src/pastor_tracker/io/arduino_transport.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/src/pastor_tracker/core/geometry.py
  - pastor_tracker/src/pastor_tracker/core/damping.py
  - pastor_tracker/src/pastor_tracker/config.py
  - pastor_tracker/src/pastor_tracker/__main__.py
  - pastor_tracker/tests/fixtures/camera_traces.py
  - pastor_tracker/tests/fixtures/arduino_traces.py
  - pastor_tracker/pyproject.toml
findings:
  blocker: 0
  warning: 0
  info: 2
  total: 2
status: clean
---

# Phase 3 (Iteration 4 Verification): Code Review Report

**Reviewed:** 2026-05-04
**Depth:** deep (cross-phase, cross-file, cross-thread)
**Iteration:** 4 (verification of iter-3 fixes)
**Files Reviewed:** 12
**Status:** clean (no BLOCKER or WARNING findings; 2 INFO observations)

## Summary

Iteration-4 verification pass over the 12/13 fixes applied in iteration-3
(`03-REVIEW-FIX.md` commit chain `8b47694`..`88cb5d0`) plus a fresh adversarial
sweep against the wider source set. The fix audit and the new sweep both come
back clean: no new BLOCKER, no new WARNING.

### Fix verification (iter-3 closed findings)

Every closed finding was re-traced from the fix-report claim down to the live
source line. Each fix is correctly applied, the pinned regression test
exercises the failure mode, and no fix introduced a regression on adjacent
code paths.

| ID | Claim | Verified live | Regression test verified | Verdict |
|----|-------|---------------|--------------------------|---------|
| B-01 | Symmetric `isinstance(_latched_error, (FirmwareErrorReceived, LinkLostError))` guard before `WatchdogResetError` overwrite in BOTH timeout and ValueError branches of `_recover` | `arduino_motor.py:669-672` (timeout branch) + `arduino_motor.py:703-706` (ValueError branch) — both branches present and symmetric, both log `latched_type` | `test_firmware_error_during_recovery_preserves_firmware_error` cited in fix report | PASS |
| B-02 | `frames()` returns cleanly when `state is _CamState.CLOSED` after a `TimeoutError` | `obs_camera.py:1013-1014` — `if self._state is _CamState.CLOSED: return` correctly placed *before* the WR-06 thread-dead synthetic fault path | `test_frames_returns_cleanly_after_stop` | PASS |
| B-03 | `Frame.__post_init__` rejects non-`uint8` `image.dtype` | `core/types.py:79-86` — dtype check sits between channel-count and contiguity checks; `_IMAGE_DTYPE_EXPECTED = np.dtype(np.uint8)` lifted to module-level Final | `test_frame_rejects_non_uint8_dtype` | PASS |
| B-04 | `start()` catches graph-factory exceptions and translates to typed `CameraOpenError` | `obs_camera.py:499-513` — second `except Exception` clause after `OBSCameraNotFoundError`; latches `_state=FAULTED` + `_latched_error` BEFORE the `raise ... from exc` (symmetric with the cv2-open path at `:521-531`) | `test_start_translates_graph_factory_failure` | PASS |
| W-01 | Output-side clamp on `angle_deg_to_normalized_x` to `[NORMALIZED_X_MIN, NORMALIZED_X_MAX]` | `core/geometry.py:91` — `min(NORMALIZED_X_MAX, max(NORMALIZED_X_MIN, normalized))` returned | `test_inverse_map_output_in_unit_interval` (hypothesis property) | PASS |
| W-02 | `_HANDSHAKE_WAIT_FOR_SLACK_SEC: Final[float] = 0.05` lifted to module constant | `arduino_motor.py:102` — Final lifted; call site `:345` uses the constant | n/a (constant rename) | PASS |
| W-03 | `arduino_motor._enqueue` no longer wraps `get_nowait()` in `contextlib.suppress(asyncio.QueueEmpty)` | `arduino_motor.py:484-489` — bare `get_nowait()` after `full()` guard, mirrors `obs_camera._enqueue_frame` exactly | n/a (mirrors WR-07 fix) | PASS |
| W-04 | `feedback_seq_regression` log carries `after_recovery=(self._state is _MotorState.RECOVERING)` | `arduino_motor.py:512-517` — `after_recovery` keyword present | `test_seq_regression_records_after_recovery_flag` (RUNNING→False, RECOVERING→True) | PASS |
| W-05 | Removed no-op `self._first_frame_event.clear()` from `_attempt_reopen` | `obs_camera.py:821-826` — replaced with documenting comment, no `.clear()` call | n/a (no-op removal) | PASS |
| W-06 | Structured WARN logs `camera_thread_join_timeout` and `rx_thread_join_timeout` on join timeout | `obs_camera.py:600-612` + `arduino_motor.py:283-294` — both surface a `WARN` log via `is_alive()` post-join check | n/a (diagnostic only) | PASS |
| W-08 | `Frame.__post_init__` rejects non-C-contiguous images | `core/types.py:87-98` — `if not self.image.flags["C_CONTIGUOUS"]: raise ValueError(...)` | `test_frame_rejects_non_contiguous_image` | PASS |
| W-09 | `_wait_for_ready` reads `self._config.arduino_protocol_version` and runs a host-vs-config consistency guard | `arduino_motor.py:322-331` — `expected = self._config.arduino_protocol_version`; `if expected != PROTOCOL_VERSION_MAJOR: raise ProtocolVersionMismatchError`; downstream `expected` flows into both the `Ready.version != expected` check (`:361`) and the timeout/error messages (`:340-341`, `:368`) | covered by `test_protocol_version_default_is_2` (existing) | PASS |

### Skipped finding (W-07)

W-07 was skipped in iter-3 with documented rationale in `03-REVIEW-FIX.md`
under `## Skipped Issues`. The skip is **acceptable**:

* The current `contextlib.suppress(asyncio.CancelledError, Exception)` in
  `arduino_motor.close()` (`:271`, `:276`) is paired with the W-05
  done-callback (`_on_task_done` at `:546-581`) which retrieves any
  unexpected task exception **before** `close()` runs and translates it to
  a typed `LinkLostError` latched on `self._latched_error` (with the
  original exception preserved via `__cause__`).
* The pinned test `test_heartbeat_task_unexpected_exception_latches`
  (`tests/test_arduino_motor_heartbeat.py:45-81`) does exercise the
  latching path: it injects a `RuntimeError` via monkey-patched
  `send_query`, asserts `isinstance(motor._latched_error, LinkLostError)`
  AND that `close()` is a clean no-throw drain. Narrowing the suppression
  to `ArduinoError` would let the original `RuntimeError` escape `close()`,
  breaking the no-throw close contract that the test pins.
* The fix-report explicitly documents that re-opening W-07 requires
  refactoring `_on_task_done` to track exception-already-retrieved state
  OR loosening the test contract — both genuine design changes outside
  "narrow the exception class".

### New project-wide sweep — no BLOCKER, no WARNING

The deep adversarial sweep over the 12 listed source files found no new
correctness, security, or threading defects. Specifically verified:

* **Concurrency invariants hold.** Single-producer / single-consumer on both
  bounded queues (`_rx_queue`, `_frames_queue`); `loop.call_soon_threadsafe`
  is the only thread→loop bridge in both modules; `_stop_event` is the only
  loop→thread signal. No direct cross-thread queue access. The single-writer
  rule on `transport.write` is enforced via `self._tx_lock` + the
  single-RX-thread invariant on `read_line`.
* **Close ordering is correct.** Both `ArduinoMotor.close()` and
  `ObsCamera.stop()` follow the Pitfall 7 sequence: cancel async tasks →
  set stop_event → join thread → release/close OS handle → flip terminal
  state. FAULTED is preserved across stop in both modules (so dashboards
  can distinguish "stopped after fault" from "stopped cleanly").
* **Latched-error gate is consistently applied.** `_raise_if_latched()` is
  called at the top of every public `send_*` except `send_emergency_stop`
  (the documented safety bypass).
* **Pure-math layer is clean.** `geometry.py` and `damping.py` validate at
  boundaries, raise `ValueError` on out-of-domain input, are stateless and
  deterministic. `damping.step()` uses Holden's exact closed form; no
  PID, no EMA — CLAUDE.md "forbidden libraries" intact.
* **Frame DTO contract is now exhaustive.** `Frame.__post_init__` validates
  ndim, channel count, dtype (uint8), C-contiguity, width/height
  agreement, and timestamp ≥ 0 — every invariant Phase 4 perception will
  rely on.
* **Protocol parser hardening intact.** `arduino_protocol.parse_line` rejects
  empty input, oversized input (T-02-04 DoS guard at 256 bytes), non-ASCII
  bytes, non-finite floats (T-02-04b), and unknown prefixes; never crashes
  on malformed input — always raises `ProtocolParseError` for the
  orchestrator to log and skip. `Limits` enforces strictly-greater
  `max_deg > min_deg` via `model_validator`.
* **No `print()`, no bare `except:`, no `except Exception: pass`, no
  globals, no magic numbers** in production code. Every `# noqa: BLE001`
  is paired with a translator that surfaces a typed error.
* **No security findings.** No SQL/command/path-traversal vectors (no DB,
  no shell-out, no user-controlled paths beyond Pydantic-validated config
  fields). No hardcoded secrets. `eval`/`exec`/`pickle` not present.
  Serial/camera input is parsed by typed parsers with strict validation.
  `pyproject.toml` enables `bandit (S)` linting; the `S101` exclusion is
  scoped to test files only.
* **Test fixtures are sound.** `FakeVideoSource` and `FakeSerialTransport`
  honour the same threading contracts the real implementations do; both
  fail loud on closed-write (`FakeSerialClosedError`) and never silently
  drop bytes/frames. `wait_for_state` / `wait_for_pause_cleared` polling
  helpers replace brittle `asyncio.sleep` cross-thread bridge waits.

The two items below are pure observational — neither rises to WARNING.

---

## Info

### IN-01: `frames()` clean-shutdown latency is bounded by `_FIRST_FRAME_TIMEOUT_SEC` (3.0 s)

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:1000-1014`
**Issue:** The B-02 clean-shutdown branch correctly returns when state is
`CLOSED`, but the per-iteration `asyncio.wait_for(..., timeout=_FIRST_FRAME_TIMEOUT_SEC)`
means a consumer iterating `async for frame in cam.frames():` after `cam.stop()`
will wait up to 3 seconds before seeing `StopAsyncIteration`. This is
imperceptible during operator-driven shutdown but adds 3 s to test-suite
shutdown when full-resolution timeouts are not monkey-patched. Phase 6's
pipeline-shutdown ergonomics may want a separate, smaller timeout for the
"queue empty + state CLOSED" path.

**Fix sketch (only if Phase 6 surfaces this as a real shutdown lag):**
Add a separate `_SHUTDOWN_DRAIN_TIMEOUT_SEC: Final[float] = 0.1` and use
the smaller value when `self._state is _CamState.CLOSED`:
```python
timeout = (
    _SHUTDOWN_DRAIN_TIMEOUT_SEC
    if self._state is _CamState.CLOSED
    else _FIRST_FRAME_TIMEOUT_SEC
)
frame = await asyncio.wait_for(self._frames_queue.get(), timeout=timeout)
```

### IN-02: `feedback_seq_regression.after_recovery` is False once `_recover` has flipped state back to RUNNING

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:511-517`
**Issue:** The W-04 fix correctly emits `after_recovery=True` when a
regressed Feedback arrives **while** the orchestrator is in
`_MotorState.RECOVERING`. After `_recover` completes successfully it sets
`_state = _MotorState.RUNNING` (`:738`) BEFORE clearing `_recover_task` in
the `finally` block. The firmware reset its `sequence` counter to 0, but
the next post-recovery Feedback line may arrive *after* state has flipped
back to RUNNING — at which point `_check_seq_gap` records
`after_recovery=False` even though the regression is logically caused by
the just-completed recovery. Phase 7 dashboard logic will see
false-negative "unexpected regression" rows in this narrow window. The
W-04 fix is still strictly better than the previous behaviour (always
False); refining the signal would require tracking "first FB after
recovery completes" via a short-lived flag rather than reading state.

**Fix sketch (deferred to Phase 7 wiring or a follow-up):**
Add a `_post_recovery_grace: bool` flag set to `True` when `_recover`
flips state back to RUNNING and cleared on the first Feedback after
recovery; OR accept the current signal and document that
`after_recovery` is a state-snapshot, not a causal-attribution.
```python
# In _recover, on the success path:
self._state = _MotorState.RUNNING
self._post_recovery_grace = True
# In _check_seq_gap regression branch:
after_recovery = (
    self._state is _MotorState.RECOVERING
    or self._post_recovery_grace
)
if after_recovery:
    self._post_recovery_grace = False
```

---

## Cross-File Verification Notes

* **Frame DTO contract → Phase 4 implications.** `core/types.py:62-110`'s
  `__post_init__` now enforces `dtype == uint8`, `flags["C_CONTIGUOUS"]`,
  width/height agreement, and `timestamp_ns >= 0`. Production producers
  (`ObsCamera._capture_loop` at `:680-686` building `Frame(image=bgr,...)`
  from `cv2.VideoCapture.read()`) all satisfy these. Test producers
  (`make_solid_bgr` in `camera_traces.py:90-94`) build `np.zeros(...,
  dtype=np.uint8)` which is C-contiguous by construction. **Phase 4 can
  drop any defensive `np.ascontiguousarray` / dtype-cast it had planned.**
* **`CameraError` family → Phase 6 implications.** B-04 closed the last
  uncaught escape route from `start()`; the public lifecycle now raises
  ONLY typed `CameraError` subclasses. Phase 6 orchestrator can write
  `try: await camera.start() except CameraError:` without needing
  fallback `except Exception:`.
* **`ArduinoError` family → Phase 6 implications.** B-01 closed the
  fault-classification masking; the typed-error gate
  (`_raise_if_latched`) now surfaces the most-specific subclass
  (`FirmwareErrorReceived` for firmware-emitted ERRORs, `LinkLostError`
  for USB/serial death, `WatchdogResetError` for recovery ack timeout).
  Phase 6 / Phase 7 can branch on subclass type for triage UX.
* **Config field `arduino_protocol_version` is now load-bearing.** W-09
  closed the "literal-only-in-the-type-system" gap; `_wait_for_ready`
  reads the config field at runtime AND runs a host-vs-config consistency
  guard. Today's `Literal[2]` plus `PROTOCOL_VERSION_MAJOR=2` keeps the
  branch unreachable (mypy may flag it under `warn_unreachable` — verify
  if the iter-3 fix-report's "mypy clean" claim still holds; if not,
  guard with `# type: ignore[unreachable]`), but a future widen to
  `Literal[2, 3]` activates the guard with no further code change.

---

_Reviewed: 2026-05-04T18:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
_Iteration: 4 (verification)_
