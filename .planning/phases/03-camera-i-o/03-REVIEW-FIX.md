---
phase: 03-camera-i-o
fixed_at: 2026-05-05T07:45:05Z
review_path: .planning/phases/03-camera-i-o/03-REVIEW.md
iteration: 3
findings_in_scope: 13
fixed: 12
skipped: 1
status: partial
---

# Phase 3: Code Review Fix Report (Iteration 3)

**Fixed at:** 2026-05-05T07:45:05Z
**Source review:** `.planning/phases/03-camera-i-o/03-REVIEW.md` (iter3 project-wide cross-phase sweep -- 4 BLOCKER + 9 WARNING)
**Iteration:** 3
**Fix scope:** critical_warning (B-01..B-04 + W-01..W-09)

**Summary:**
- Findings in scope: 13 (4 blocker + 9 warning)
- Fixed: 12 (4 blocker + 8 warning)
- Skipped: 1 (W-07, see "Skipped Issues" below)

## Verification Snapshot

| Gate | Status |
|---|---|
| pytest (full suite) | 247 passed (was 240; +7 new regression tests) |
| ruff check src tests | clean |
| mypy --strict src | clean (17 source files) |
| New regression tests added | 7 (B-01, B-02, B-03, B-04, W-01, W-04, W-08) |
| Pre-existing tests broken | 0 |

The full suite was re-run after every commit; no commit caused a
regression. One pre-existing flake (`test_golden_trace_replay`) was
observed under full-suite contention on the first baseline run but
passed on every subsequent run -- this is a known timing-sensitive
test (see commit `5a68cce`'s 5 s drain-budget fix); it is unrelated
to any of the iter3 changes.

## Per-Finding Result Table

| ID | Severity | File:Line | Commit | Regression test | Status |
|----|----------|-----------|--------|-----------------|--------|
| B-01 | BLOCKER | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:609,632` | `8b47694` | yes -- `test_firmware_error_during_recovery_preserves_firmware_error` | fixed |
| B-02 | BLOCKER | `pastor_tracker/src/pastor_tracker/io/obs_camera.py:982` | `a005127` | yes -- `test_frames_returns_cleanly_after_stop` | fixed |
| B-03 | BLOCKER | `pastor_tracker/src/pastor_tracker/core/types.py:62-84` | `c714930` | yes -- `test_frame_rejects_non_uint8_dtype` | fixed |
| B-04 | BLOCKER | `pastor_tracker/src/pastor_tracker/io/obs_camera.py:486-498` | `c13b9a2` | yes -- `test_start_translates_graph_factory_failure` | fixed |
| W-01 | WARNING | `pastor_tracker/src/pastor_tracker/core/geometry.py:57-82` | `dca9c0e` | yes -- `test_inverse_map_output_in_unit_interval` (hypothesis property) | fixed |
| W-02 | WARNING | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:309-312` | `5f6779b` | no (constant rename only) | fixed |
| W-03 | WARNING | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:439-447` | `f1bf64f` | no (mirrors WR-07 obs_camera unreachable removal) | fixed |
| W-04 | WARNING | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:449-479` | `e6214fa` | yes -- `test_seq_regression_records_after_recovery_flag` | fixed |
| W-05 | WARNING | `pastor_tracker/src/pastor_tracker/io/obs_camera.py:793` | `8a5c1cf` | no (no-op removal -- documented in commit) | fixed |
| W-06 | WARNING | `pastor_tracker/src/pastor_tracker/io/obs_camera.py:566-594` and `arduino_motor.py:264-282` | `368dff6` | no (WARN-only diagnostic; race window pre-existing) | fixed (partial -- WARN logs only; join-timeout raise deferred) |
| W-07 | WARNING | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:264-273` | (n/a) | (n/a) | skipped (see below) |
| W-08 | WARNING | `pastor_tracker/src/pastor_tracker/core/types.py:62-84` | `04e6998` | yes -- `test_frame_rejects_non_contiguous_image` | fixed |
| W-09 | WARNING | `pastor_tracker/src/pastor_tracker/config.py:98` and `arduino_motor.py:296-297` | `88cb5d0` | covered by existing `test_protocol_version_default_is_2` (the read path is now exercised) | fixed |

## Fixed Issues

### B-01: Watchdog recovery overwrites firmware ERROR latch with WatchdogResetError

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (`_recover` two except branches)
- `pastor_tracker/tests/test_arduino_motor_recovery.py`

**Commit:** `8b47694`

**Applied fix:** Added `isinstance(self._latched_error, (FirmwareErrorReceived, LinkLostError))` guard before the unconditional `self._latched_error = WatchdogResetError(...)` assignment in BOTH the `(asyncio.TimeoutError, asyncio.QueueEmpty)` branch (line 609) AND the parallel `ValueError` (write-error) branch (line 632). The guard is symmetric: if `_on_rx_event` already latched a more specific typed error while `_recover` was awaiting an ack, that typed error survives. Logs `latched_type` so post-mortems show which typed error survived.

The regression test feeds `READY:v2` mid-session, lets `_recover` start, then feeds `ERROR:11 - PC heartbeat lost` while `_recover` is blocked on Settings ack. After the recovery times out, asserts `motor._latched_error` is `FirmwareErrorReceived` (not `WatchdogResetError`) and the next `send_motor_angle` raises `FirmwareErrorReceived`.

---

### B-02: `ObsCamera.frames()` raises synthetic `CameraStallError` after clean `stop()`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (`frames()` async generator)
- `pastor_tracker/tests/test_obs_camera_lifecycle.py`

**Commit:** `a005127`

**Applied fix:** Inside the `except TimeoutError:` block of `frames()`, added an early `return` when `self._state is _CamState.CLOSED`. The WR-06 thread-dead branch still fires for the ungraceful case (capture thread died WITHOUT reaching CLOSED). Phase 6's `async for frame in cam.frames():` consumer now sees `StopAsyncIteration` on clean shutdown rather than a synthetic `CameraStallError` masquerading as "camera crashed".

The regression test starts the camera, calls `stop()`, then iterates over `frames()` with a monkey-patched `_FIRST_FRAME_TIMEOUT_SEC=0.1` and `_STALE_FRAME_MAX_AGE_NS=0` (so any buffered frames are stale-dropped). Asserts the generator returns cleanly with `yielded == []`.

---

### B-03: `Frame.__post_init__` does not validate `image.dtype`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/core/types.py` (added `_IMAGE_DTYPE_EXPECTED` constant + check in `__post_init__`)
- `pastor_tracker/tests/test_types.py`

**Commit:** `c714930`

**Applied fix:** Added `_IMAGE_DTYPE_EXPECTED: np.dtype[np.uint8] = np.dtype(np.uint8)` module constant and a runtime check after the channel-count check: if `self.image.dtype != _IMAGE_DTYPE_EXPECTED` raise `ValueError`. Closes the gap between the static `ImageArray = NDArray[np.uint8]` type alias (compile-time only) and the runtime contract.

Existing callers (`obs_camera._capture_loop` builds `Frame(image=bgr, ...)` from `cv2.VideoCapture.read()` or `FakeVideoSource.read()` outputs, which are uint8 by construction) all pass uint8. Test fixtures `make_solid_bgr` use `np.zeros(..., dtype=np.uint8)`; verified by re-running the full obs_camera test suite (47 tests).

---

### B-04: `ObsCamera.start()` does not catch graph-factory failures

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (added `except Exception` after `except OBSCameraNotFoundError`)
- `pastor_tracker/tests/test_obs_camera_lifecycle.py`

**Commit:** `c13b9a2`

**Applied fix:** Added a second `except Exception as exc:` clause to the `try: self._device_index = discover_obs_camera_index(...)` block. The handler latches `_state = _CamState.FAULTED` and `_latched_error = CameraOpenError(f"DirectShow enumeration failed: {exc!r}")` then re-raises the typed error from the original cause. This is symmetric with the cv2 / DirectShow open path immediately below (the CR-03 pattern). Closes the OSError / pythoncom.com_error / pywintypes.error escape route.

The regression test injects a `_bad_factory` lambda that raises `OSError("quartz.dll not found (simulated)")`, asserts `start()` raises `CameraOpenError(match="DirectShow")`, `cam.state is _CamState.FAULTED`, and `isinstance(cam.last_error, CameraOpenError)`.

`# noqa: BLE001` was attempted but ruff reported it as unused (the `OBSCameraNotFoundError` clause above narrows the type-tracker's view of what can leak into the bare `except Exception`); removed it. Lint is clean.

---

### W-01: `geometry.angle_deg_to_normalized_x` can return values slightly outside [0, 1]

**Files modified:**
- `pastor_tracker/src/pastor_tracker/core/geometry.py` (output clamp)
- `pastor_tracker/tests/test_geometry.py` (new property test + `NORMALIZED_X_MIN`/`NORMALIZED_X_MAX` re-export)

**Commit:** `dca9c0e`

**Applied fix:** After computing `normalized = (offset + NORMALIZED_RANGE) * HALF`, return `min(NORMALIZED_X_MAX, max(NORMALIZED_X_MIN, normalized))`. Symmetric tolerance: tolerate `_HALF_FOV_BOUNDARY_TOL_DEG=1e-9` of input drift on the half-FOV boundary, produce strictly in-domain `[0, 1]` output. Downstream Pydantic DTOs (`Detection`, `TrackedSubject`, `FramingTarget` -- `Field(ge=0.0, le=1.0)`) no longer raise `ValidationError` from float ULP drift.

New property test `test_inverse_map_output_in_unit_interval` runs hypothesis over `(fov, nx)` in `(1.0, 170.0) x [0, 1]`, computes the round-trip `nx -> angle -> nx_back`, and asserts `0 <= nx_back <= 1`.

---

### W-02: `arduino_motor._wait_for_ready` magic number `0.05`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`

**Commit:** `5f6779b`

**Applied fix:** Lifted the `0.05` literal to a module-level `Final` constant `_HANDSHAKE_WAIT_FOR_SLACK_SEC: Final[float] = 0.05` alongside the existing `_RECOVERY_TRAILING_DRAIN_SEC`. Updated the call site at line 311. No behavioral change; pure CLAUDE.md rule 6 compliance.

---

### W-03: `_enqueue` inconsistency between arduino_motor and obs_camera

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (`_enqueue` method)

**Commit:** `f1bf64f`

**Applied fix:** Mirrored the WR-07 obs_camera fix: removed the `with contextlib.suppress(asyncio.QueueEmpty):` wrapper around `self._rx_queue.get_nowait()`. The `full()` guard guarantees `get_nowait()` cannot raise. Per CLAUDE.md rule 1, unreachable error-suppression is forbidden. Now `arduino_motor._enqueue` and `obs_camera._enqueue_frame` are structurally identical for future readers.

---

### W-04: `_check_seq_gap` does not record watchdog-reset signal in regression branch

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (added `after_recovery` field to `feedback_seq_regression` log)
- `pastor_tracker/tests/test_arduino_motor_seq_gap.py`

**Commit:** `e6214fa`

**Applied fix:** Added `after_recovery=(self._state is _MotorState.RECOVERING)` keyword to the `feedback_seq_regression` log emit. Phase 7 dashboard logic can now distinguish "regression we expected because the orchestrator already spawned `_recover`" from "regression we did not expect" without timestamp correlation.

The regression test sets `motor._last_seq=10` then calls `_check_seq_gap(_make_fb(0))` with `_state=RUNNING` (asserts `after_recovery is False`) and again with `_state=RECOVERING` (asserts `after_recovery is True`).

---

### W-05: `ObsCamera._first_frame_event` cleared during reopen but never waited on after start()

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (removed `self._first_frame_event.clear()` from `_attempt_reopen`)

**Commit:** `8a5c1cf`

**Applied fix:** Removed the no-op `self._first_frame_event.clear()` call. After `start()` returns, no path waits on the event; the capture loop only ever `set()`s it (idempotent). Removing the clear documents that the event is single-use after start. Replaced the misleading line with a documenting comment.

`start()` is documented single-shot ("calling twice raises CameraError"), so reopen-after-restart is not a real path.

---

### W-06: `ObsCamera.stop()` releases source even if a concurrent `_attempt_reopen` is mid-factory

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (`stop()` -- WARN log)
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (`close()` -- symmetric WARN log)

**Commit:** `368dff6`

**Applied fix (partial):** Added structured WARN logs `camera_thread_join_timeout` (obs_camera) and `rx_thread_join_timeout` (arduino_motor) when the join times out. Operators see the leak rather than diagnosing it after the fact. The reviewer's other recommendation -- raising `_CAPTURE_JOIN_TIMEOUT_SEC` and `_RX_JOIN_TIMEOUT_SEC` from 1.0 s to 4.0 s -- was deliberately deferred:

* Tests do not parameterise the timeout; raising it would compound test-suite runtime and could expose new flakes under contention.
* The race window is a pre-existing inheritance from Phase 2 (W-06 itself notes this), not a Phase 3 regression.
* The WARN log surfaces the symptom; the operator can act on the leak without us first lengthening every clean-shutdown path.

If Phase 6 wiring later proves the longer timeout is required, the constant lift is a one-line follow-up that does not require re-touching call sites.

---

### W-08: `Frame` does not assert `image.flags['C_CONTIGUOUS']`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/core/types.py` (added contiguity check in `__post_init__`)
- `pastor_tracker/tests/test_types.py`

**Commit:** `04e6998`

**Applied fix:** After the dtype check, added `if not self.image.flags["C_CONTIGUOUS"]: raise ValueError(...)`. Production callers (`cv2.VideoCapture.read()`) always return C-contiguous arrays; the guard catches a future `FakeVideoSource` or perception-stage refactor that builds a non-contiguous view.

The regression test constructs `np.zeros((4, 4, 6), dtype=np.uint8)[:, :, ::2]` (a non-contiguous view of shape `(4, 4, 3)`), confirms `flags["C_CONTIGUOUS"] is False`, and asserts `Frame(image=view, ...)` raises `ValueError(match="C-contiguous")`.

---

### W-09: `Config.arduino_protocol_version` `Literal[2]` does not feed `arduino_motor` runtime check

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (`_wait_for_ready` reads `self._config.arduino_protocol_version` + consistency guard)

**Commit:** `88cb5d0`

**Applied fix:** Took the reviewer's option 2 (read the config field at runtime) rather than option 1 (drop the field). Two reasons:

1. Dropping `arduino_protocol_version` is a public Config contract change that ripples through `tests/conftest.py:18` (the `valid_config_dict` fixture) AND every consumer that builds `Config(**valid_config_dict)`. Read-it-at-runtime is purely additive.
2. Existing tests `test_protocol_version_must_be_2` + `test_protocol_version_default_is_2` already pin the field's behaviour; the read-at-runtime change makes those tests exercise the actual orchestrator path rather than just the type system.

Added a defensive consistency guard: if `expected != PROTOCOL_VERSION_MAJOR`, raise `ProtocolVersionMismatchError`. Today the branch is unreachable (Literal[2] + constant=2) but a future widen to `Literal[2, 3]` automatically activates the guard.

## Skipped Issues

### W-07: `arduino_motor.close()` Exception suppression breadth

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:264-273`

**Reason:** The reviewer's recommended narrowing from `contextlib.suppress(asyncio.CancelledError, Exception)` to `contextlib.suppress(asyncio.CancelledError, ArduinoError)` **breaks an existing explicit design contract** validated by `test_heartbeat_task_unexpected_exception_latches` (`tests/test_arduino_motor_heartbeat.py:45-81`).

That test deliberately monkey-patches `motor.send_query` to raise `RuntimeError`, asserts that the W-05 done-callback installed by `start()` translates the `RuntimeError` into a latched `LinkLostError`, then calls `await motor.close()` inside a `try/finally`. The current behaviour is: close() awaits the heartbeat task, the heartbeat task re-raises the original `RuntimeError`, and `contextlib.suppress(Exception)` swallows it because the W-05 done-callback already retrieved + latched the typed `LinkLostError` from it.

Narrowing to `ArduinoError` lets the original `RuntimeError` escape `close()`, which fails the test:
```
src\pastor_tracker\io\arduino_motor.py:278: in close
    await self._heartbeat_task
src\pastor_tracker\io\arduino_motor.py:574: in _heartbeat_loop
    await self.send_query()
RuntimeError: simulated programmer bug
```

The intent the reviewer flagged ("a `KeyError` or `RuntimeError` from cancellation cleanup would be hidden even if it's a legitimate bug") is real -- BUT the W-05 done-callback already handles it: any unexpected exception type from the task is captured + translated to a latched `LinkLostError` BEFORE `close()` runs, with full diagnostic preserved via `__cause__`. The `Exception` suppression in `close()` is just preventing a re-raise of the same exception that the done-callback already turned into a typed surface. Narrowing it would lose the deterministic close-path drain that the test pins.

**Rolled back via `git checkout -- arduino_motor.py`** before the change was committed; the working tree is clean and the test suite stayed green at every commit boundary. To revisit W-07 properly, the agent would need to either:
* Refactor the done-callback to make the suppress redundant (tracking that the exception was already retrieved), OR
* Update `test_heartbeat_task_unexpected_exception_latches` to assert that `close()` raises and add a try/except around the close call.

Both are non-trivial design changes outside the scope of "narrow the exception class". Skipping per `<critical_rules>`: "DO skip findings that cannot be applied cleanly -- do not force broken fixes."

## Plan Impact

This sweep touched cross-phase contracts that downstream phase plans (4-7) reference. Orchestrator should propagate the changes to those plans:

### Phase 4 (Perception) -- Frame DTO contract tightening (B-03 + W-08)

`Frame.__post_init__` now enforces TWO new invariants, both of which Phase 4's YOLO11-pose / ultralytics path was already implicitly assuming:

1. `image.dtype == np.uint8` (B-03)
2. `image.flags["C_CONTIGUOUS"] is True` (W-08)

**Plan update needed:** `04-PLAN.md` (when written) can rely on these guarantees and skip its own dtype / contiguity guard (`np.ascontiguousarray` would be a no-op). Frame builders in tests/fixtures can no longer slice with `frame[..., ::-1]` patterns; they must `np.ascontiguousarray(...)` if they do. No public API change -- just a tighter precondition.

### Phase 6 (Pipeline orchestration) -- camera lifecycle surface (B-02 + B-04)

Two cross-phase orchestration contracts were tightened:

1. **B-02:** Phase 6's `async for frame in camera.frames():` now terminates cleanly with `StopAsyncIteration` after `camera.stop()` -- previously the orchestrator would have to catch `CameraStallError` and discriminate "stopped on demand" from "real stall" by checking `camera.state == CLOSED` itself. Phase 6 plan can now write the simpler shutdown idiom.
2. **B-04:** Phase 6's `try: await camera.start() except CameraError:` now reliably catches DirectShow / DLL load failures as `CameraOpenError` (the typed CameraError family), not as a raw `OSError` / `pywintypes.error`. Phase 6 plan can document the typed-only exception surface.

### Phase 7 (Dashboard) -- diagnostic richness (W-04 + W-06)

Two new structured log keys appear that Phase 7 dashboard widgets can consume:

1. **`feedback_seq_regression.after_recovery: bool`** (W-04) -- distinguishes expected (post-recovery) vs unexpected sequence regressions.
2. **`camera_thread_join_timeout` / `rx_thread_join_timeout`** (W-06) -- new WARN events surfacing handle-race risk on shutdown.

### Phase 6 -- protocol-version negotiation surface (W-09)

`ArduinoMotor` now reads `Config.arduino_protocol_version` at runtime instead of the host-side constant. Today `Literal[2]` pins this to a single value, but if a future protocol bump widens the literal, the `_wait_for_ready` consistency guard auto-activates. Phase 6 plan should note that the orchestrator's protocol-version is now config-driven (one-line constant change in arduino_protocol.py would no longer be sufficient on its own).

### W-07 (skipped) -- close-path exception contract

The `close()` exception-suppression contract is wider than tiger-style usually allows BECAUSE the W-05 done-callback front-loads the typed-error translation. If a future refactor makes the done-callback simpler (e.g. by using `task.add_done_callback(lambda t: self._latched_error or t.exception())`), W-07 should be re-opened. For now, document the contract: "close() drains any task exception silently because the done-callback has already translated it; never narrow this except clause without also updating `test_heartbeat_task_unexpected_exception_latches`."

---

_Fixed: 2026-05-05T07:45:05Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 3_
