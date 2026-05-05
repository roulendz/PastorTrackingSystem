---
phase: 03-camera-i-o
reviewed: 2026-05-04T00:00:00Z
depth: deep
files_reviewed: 18
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
  - pastor_tracker/tests/test_obs_camera_discovery.py
  - pastor_tracker/tests/test_obs_camera_lifecycle.py
  - pastor_tracker/tests/test_obs_camera_stale_drop.py
  - pastor_tracker/tests/test_obs_camera_fallback.py
  - pastor_tracker/tests/test_obs_camera_stall.py
  - pastor_tracker/pyproject.toml
findings:
  blocker: 4
  warning: 9
  total: 13
status: findings
---

# Phase 3 (Project-Wide Sweep): Code Review Report

**Reviewed:** 2026-05-04
**Depth:** deep (cross-phase, cross-file)
**Files Reviewed:** 18
**Status:** findings (4 BLOCKER + 9 WARNING)

## Summary

Project-wide adversarial sweep across Phases 1-3 production code before Phase 4
(Perception). Phase 3 itself was closed clean by the iter2 review; this sweep
widens the lens to cross-phase contracts, threading correctness, resource
lifecycles, and tiger-style adherence as Phase 6 orchestration approaches.

The Phase 3 camera module has internalised every Phase-2-grade lesson:
typed-error hierarchy mirrored, dedicated-thread + bounded-queue pattern
mirrored, `loop.call_soon_threadsafe` discipline mirrored, fail-loud
translators on every BLE001 catch.

What this sweep surfaces (not flagged in earlier reviews):

1. **Arduino recovery path masks firmware ERROR with WatchdogResetError**
   — a real fault classification bug.
2. **`ObsCamera.frames()` raises a synthetic `CameraStallError` after a clean
   `stop()`** — WR-06 watchdog cannot distinguish "stopped cleanly" from
   "thread crashed".
3. **`Frame.__post_init__` does not validate `image.dtype`** — a float32 array
   silently slips past the BGR-uint8 contract that all of Phase 4 will rely on.
4. **`ObsCamera.start()` does not catch graph factory failures** — DLL load
   error or COM init error escapes uncaught, leaving state stuck at OPENING.
5. Plus 9 quality / consistency / robustness warnings that are not blockers
   but should be tightened before Phase 6 wires the orchestrator.

No security vulnerabilities, no path traversal, no credential exposure
detected. No `print()` calls, no bare-except, no `Any` slipping in via casts.
Magic numbers are well-controlled in production code; one `0.05` hardcoded in
arduino_motor.py:311 is the only violation found.

---

## BLOCKER Issues

### B-01: Watchdog recovery overwrites firmware ERROR latch with WatchdogResetError

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:567-619`
**Severity:** BLOCKER
**Category:** Bug — fault classification correctness

**Issue:**
If the firmware emits an `ERROR:<code>` line **while `_recover` is waiting for
`Settings`/`Limits` ack**, the typed `FirmwareErrorReceived` latch is
silently overwritten with a generic `WatchdogResetError("recovery ack
timeout: ...")`.

**Trace:**
1. Mid-session `READY:v2` arrives → `_on_rx_event` spawns `_recover` task.
2. `_recover` calls `send_settings`, then awaits `_wait_for_event(Settings, ...)`.
3. Firmware emits `ERROR:11 - PC heartbeat lost` (or any ErrorCode).
4. RX thread parses, schedules `_on_rx_event(Error)` on loop thread.
5. `_on_rx_event` Error branch sets:
   ```
   self._latched_error = FirmwareErrorReceived(event.code, event.message)
   self._state = _MotorState.FAULTED
   ```
   then **enqueues the Error event** into `_rx_queue` (line 435).
6. `_recover._wait_for_event` pulls the Error from the queue, sees
   `isinstance(ev, Settings)` is False, **discards** it (line 701-705 logs
   `recovery_discarded_event`).
7. Eventually `_wait_for_event` times out → `_recover` enters the
   `except (asyncio.TimeoutError, asyncio.QueueEmpty)` branch (line 609).
8. **Line 610 unconditionally re-assigns `self._latched_error =
   WatchdogResetError(...)`**, clobbering the more specific
   `FirmwareErrorReceived`.

The next `send_*` call will raise `WatchdogResetError` instead of the
operator-actionable `FirmwareErrorReceived(ErrorCode.HEARTBEAT_TIMEOUT, ...)`.
Operator triage and any Phase 7 dashboard logic that branches on error
subclass type will see the wrong cause.

This breaks tiger-style "fail loud, fail accurate". The typed surface is
the whole point of the 5-subclass `ArduinoError` hierarchy.

**Fix sketch:**
```python
# arduino_motor.py:609 — promote the existing latched error if a more
# specific one was set by _on_rx_event while _recover was waiting.
except (asyncio.TimeoutError, asyncio.QueueEmpty) as exc:  # noqa: UP041
    if not isinstance(self._latched_error, (FirmwareErrorReceived, LinkLostError)):
        self._latched_error = WatchdogResetError(
            f"recovery ack timeout: {exc}"
        )
    self._state = _MotorState.FAULTED
    self._logger.error(
        "watchdog_recovery_failed",
        reason="ack_timeout",
        stage_exc=str(exc),
        latched_type=type(self._latched_error).__name__,
    )
    return
```
Apply the same guard to the `ValueError` branch (line 632) — if a
firmware ERROR has already latched a more specific error, do not
downgrade it to `WatchdogResetError("recovery write error: ...")`.

Add a regression test in `tests/test_arduino_motor_recovery.py` that
feeds RX:
1. mid-session `READY:v2`
2. `ERROR:11 - PC heartbeat lost` (immediately, before recovery ack)
3. assert `motor.last_error` is `FirmwareErrorReceived` after stop, NOT
   `WatchdogResetError`.

---

### B-02: `ObsCamera.frames()` raises synthetic `CameraStallError` after clean `stop()`

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:943-1002`
**Severity:** BLOCKER
**Category:** Bug — clean-shutdown surface

**Issue:**
`frames()` is an async generator. Phase 6's pipeline orchestrator will
consume it via `async for frame in camera.frames():`. When `stop()` runs
(orchestrator shutdown, normal lifecycle event), the consumer's next
iteration must terminate cleanly — either by `StopAsyncIteration`
(generator returns) or by raising a typed CLOSED-state error. Today it
synthesises a fake `CameraStallError`:

```python
# obs_camera.py:978-991
thread_dead = (
    self._capture_thread is None
    or not self._capture_thread.is_alive()
)
if thread_dead and self._latched_error is None:
    raise CameraStallError(
        attempts=[(0, 0, "capture thread dead, no error latched")],
    ) from None
continue
```

After a clean `stop()`:
* `self._capture_thread` is `None` (set on line 589) → `thread_dead` is True.
* `self._state` is `_CamState.CLOSED`.
* `self._latched_error` is `None`.

The `wait_for(_FIRST_FRAME_TIMEOUT_SEC=3.0)` will time out within 3s after
stop (the queue stops being filled), and the watchdog raises a synthetic
`CameraStallError` even though the camera was stopped cleanly. The Phase 6
orchestrator will see "camera crashed" when it actually stopped on demand.

**Fix sketch:**
```python
# obs_camera.py:978
if self._state is _CamState.CLOSED:
    return  # generator-clean exit; consumer's `async for` loop ends.
thread_dead = (
    self._capture_thread is None
    or not self._capture_thread.is_alive()
)
if thread_dead and self._latched_error is None:
    raise CameraStallError(
        attempts=[(0, 0, "capture thread dead, no error latched")],
    ) from None
continue
```
Add a regression test:
```python
async def test_frames_returns_cleanly_after_stop(valid_config_dict):
    cam, _ = await _started_camera(valid_config_dict)
    await cam.stop()
    yielded = []
    async for f in cam.frames():
        yielded.append(f)  # should never run
    assert yielded == []   # generator returned cleanly, no exception
```

---

### B-03: `Frame.__post_init__` does not validate `image.dtype` — Phase 4 contract gap

**File:** `pastor_tracker/src/pastor_tracker/core/types.py:62-84`
**Severity:** BLOCKER
**Category:** Cross-phase contract drift

**Issue:**
`Frame` is the DTO Phase 4 (perception) consumes. The docstring (line 50)
and `ImageArray = npt.NDArray[np.uint8]` type alias declare the contract as
"BGR uint8 HxWx3". `__post_init__` validates ndim, channel count,
width/height agreement, and `timestamp_ns >= 0` — but **does not validate
`image.dtype == np.uint8`**.

Python's type-alias narrowing is purely static; at runtime, `Frame(image=
some_float32_array, ...)` constructs successfully. The OpenCvVideoSource
real implementation always returns uint8 (cv2 contract), so production
today is fine — but:
* Phase 4 will pass `Frame.image` to YOLO11-pose; ultralytics expects uint8.
* A future test or a swapped-in `VideoSource` (e.g. simulated input from a
  numpy generator) returning float32 would silently produce a Frame that
  poisons every downstream stage.
* CLAUDE.md rule 1 (tiger-style fail-fast at boundaries) — the boundary IS
  `Frame.__post_init__`; passing dtype validation through is an unenforced
  invariant.

**Fix sketch:**
```python
# core/types.py — add a constant + a check in __post_init__.
_IMAGE_DTYPE_EXPECTED: np.dtype[np.uint8] = np.dtype(np.uint8)
# inside __post_init__, after ndim check:
if self.image.dtype != _IMAGE_DTYPE_EXPECTED:
    raise ValueError(
        f"Frame.image must have dtype {_IMAGE_DTYPE_EXPECTED}, "
        f"got {self.image.dtype}"
    )
```
Add a regression test in `tests/test_types.py`:
```python
def test_frame_rejects_non_uint8_dtype():
    bad = np.zeros((1080, 1920, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="dtype"):
        Frame(image=bad, width=1920, height=1080, timestamp_ns=0)
```

---

### B-04: `ObsCamera.start()` does not catch graph-factory failures

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:486-498`
**Severity:** BLOCKER
**Category:** Bug — fail-loud gap

**Issue:**
```python
# obs_camera.py:487-498
try:
    self._device_index = discover_obs_camera_index(
        self._config.obs_camera_name,
        factory=self._filter_graph_factory,
        logger=self._logger,
    )
except OBSCameraNotFoundError as exc:
    self._state = _CamState.FAULTED
    self._latched_error = exc
    raise
```

`discover_obs_camera_index` calls `factory()` (line 286 of obs_camera.py).
Production wires `factory=lambda: FilterGraph()`. The `FilterGraph`
constructor calls `CoInitialize` and links DirectShow DLLs. **Any of the
following propagate as untyped exceptions**:
* `OSError` — `quartz.dll` / `mfplat.dll` missing
* `pythoncom.com_error` / `pywintypes.error` — `RPC_E_CHANGED_MODE`,
  `CO_E_NOTINITIALIZED`
* Any `RuntimeError` from pygrabber

When any of these fire, `start()` propagates the raw error AND leaves
`self._state == _CamState.OPENING` AND `self._latched_error == None`. The
dashboard reads "still opening" forever, Phase 6 orchestrator sees a raw
`OSError` instead of the typed `CameraError` family.

This violates the same "translate to typed terminal state" pattern that
CR-03 fixed for the `OBSCameraNotFoundError` path. The factory-failure
path was overlooked.

**Fix sketch:**
```python
# obs_camera.py:487 — broaden the catch to translate any factory failure.
try:
    self._device_index = discover_obs_camera_index(
        self._config.obs_camera_name,
        factory=self._filter_graph_factory,
        logger=self._logger,
    )
except OBSCameraNotFoundError as exc:
    self._state = _CamState.FAULTED
    self._latched_error = exc
    raise
except Exception as exc:  # noqa: BLE001 -- documented translator
    self._state = _CamState.FAULTED
    self._latched_error = CameraOpenError(
        f"DirectShow enumeration failed: {exc!r}"
    )
    raise self._latched_error from exc
```
Add a regression test:
```python
async def test_start_translates_graph_factory_failure(valid_config_dict):
    def _bad_factory():
        raise OSError("quartz.dll not found")
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *a, **kw: ...,  # never called
        filter_graph_factory=_bad_factory,
    )
    with pytest.raises(CameraOpenError, match="DirectShow"):
        await cam.start()
    assert cam.state is _CamState.FAULTED
    assert isinstance(cam.last_error, CameraOpenError)
```

---

## WARNING Issues

### W-01: `geometry.angle_deg_to_normalized_x` can return values slightly outside [0, 1]

**File:** `pastor_tracker/src/pastor_tracker/core/geometry.py:57-82`
**Severity:** WARNING
**Category:** Bug — boundary correctness for downstream consumers

**Issue:**
`_HALF_FOV_BOUNDARY_TOL_DEG = 1e-9` lets the input `angle_deg` exceed
`±half_fov_deg` by ~1e-9 to absorb roundtrip drift. But the function does
NOT clamp the **output** `normalized_x` — when the input is at
`half_fov_deg + 1e-10`, the math yields `normalized_x ~= 1.0 + epsilon`.
Downstream `Detection`/`TrackedSubject`/`FramingTarget` Pydantic models all
declare `Field(ge=0.0, le=1.0)` and will raise `ValidationError`.

This is asymmetric: the function tolerates input drift but propagates the
drift to its output, then the next DTO crashes on it.

**Fix sketch:**
```python
# geometry.py:81 — clamp at the seam.
offset = math.tan(math.radians(angle_deg)) / math.tan(half_fov_rad)
normalized = (offset + NORMALIZED_RANGE) * HALF
return min(NORMALIZED_X_MAX, max(NORMALIZED_X_MIN, normalized))
```
Add a roundtrip property test in `tests/test_geometry.py` confirming the
inverse map's output is always in `[0, 1]`.

---

### W-02: `arduino_motor._wait_for_ready` magic number `0.05`

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:309-312`
**Severity:** WARNING
**Category:** CLAUDE.md rule 6 violation

**Issue:**
```python
line = await asyncio.wait_for(
    asyncio.to_thread(self._transport.read_line, remaining),
    timeout=remaining + 0.05,
)
```
`0.05` is a magic number for "scheduler-jitter slack on top of the
transport's own timeout". Every other tunable lives in module-level
`Final` constants per CLAUDE.md rule 6.

**Fix sketch:**
```python
# arduino_motor.py:97 — alongside _RECOVERY_TRAILING_DRAIN_SEC.
_HANDSHAKE_WAIT_FOR_SLACK_SEC: Final[float] = 0.05  # scheduler-jitter slack
# arduino_motor.py:311 — use it.
timeout=remaining + _HANDSHAKE_WAIT_FOR_SLACK_SEC,
```

---

### W-03: `_enqueue` inconsistency between arduino_motor and obs_camera

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:439-447`
                vs `pastor_tracker/src/pastor_tracker/io/obs_camera.py:704-721`
**Severity:** WARNING
**Category:** DRY / pattern consistency

**Issue:**
`obs_camera._enqueue_frame` removed the `contextlib.suppress(QueueEmpty)`
in WR-07 fix, citing CLAUDE.md tiger-style rule 1: "unreachable
error-suppression hides real bugs". `arduino_motor._enqueue` (line 442)
still has it:

```python
def _enqueue(self, event: ProtocolEvent) -> None:
    if self._rx_queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):  # <-- still here
            self._rx_queue.get_nowait()
        ...
```

Same logical structure (`full()` then `get_nowait()`), same unreachable
suppress. Either both modules should keep the defensive suppress, or
neither should — the inconsistency means the next reader has to ask
"which is right?".

**Fix sketch:**
Apply the WR-07 fix from obs_camera to arduino_motor — drop the
`contextlib.suppress` block:
```python
def _enqueue(self, event: ProtocolEvent) -> None:
    if self._rx_queue.full():
        self._rx_queue.get_nowait()  # full() guarantees non-empty
        self._logger.warning(
            "rx_queue_full", dropped_event_type=type(event).__name__
        )
    self._rx_queue.put_nowait(event)
```

---

### W-04: `_check_seq_gap` does not record watchdog-reset signal in regression branch

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:449-479`
**Severity:** WARNING
**Category:** Diagnostic richness

**Issue:**
After firmware watchdog reset, `sequence` resets to 0. The host detects
this as a "regression" and updates `_last_seq` (lines 462-471). The log
event is `feedback_seq_regression` — but the orchestrator already
**knows** a watchdog reset occurred (it spawned `_recover`). A
`feedback_seq_regression` log emitted right after the recovery would
duplicate the diagnostic, but distinguishing "regression we expected
because of recovery" from "regression we did not expect" requires
correlating timestamps across log events.

**Fix sketch:**
Pass an `expected_after_recovery` flag in the log:
```python
def _check_seq_gap(self, fb: Feedback) -> None:
    if self._last_seq is None: ...
    gap = (fb.sequence - self._last_seq) % SEQ_MODULUS
    if gap > SEQ_MODULUS // 2:
        self._logger.warning(
            "feedback_seq_regression",
            from_seq=self._last_seq,
            to_seq=fb.sequence,
            after_recovery=(self._state is _MotorState.RECOVERING),
        )
        self._last_seq = fb.sequence
        return
    ...
```
Low priority — purely diagnostic clarity for Phase 7 dashboard.

---

### W-05: `ObsCamera._first_frame_event` cleared during reopen but never waited on after start()

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:793`
**Severity:** WARNING
**Category:** Dead code / misleading state

**Issue:**
`_attempt_reopen` calls `self._first_frame_event.clear()` at line 793.
After `start()` returns, no code path waits on `_first_frame_event` —
`_capture_loop` line 671-672 only sets it (idempotently). Clearing it
during reopen is operationally a no-op but reads as "we are about to
re-do the first-frame handshake", which is misleading.

**Fix sketch:**
Either:
1. Remove the `clear()` (it has no effect, and removing it documents
   that the event is single-use after start);
2. Or document the intent explicitly:
   ```python
   # _first_frame_event: set once by capture loop on first successful
   # frame; cleared here on reopen so a future caller of start() (after
   # a full lifecycle restart) still observes single-shot semantics.
   self._first_frame_event.clear()
   ```
Lean toward option 1 — `start()` is documented single-shot ("calling
twice raises CameraError"), so reopen-after-restart is not a real path.

---

### W-06: `ObsCamera.stop()` releases source even if a concurrent `_attempt_reopen` is mid-factory

**File:** `pastor_tracker/src/pastor_tracker/io/obs_camera.py:566-594`
**Severity:** WARNING
**Category:** Threading correctness — race window

**Issue:**
The close-order is documented as Pitfall-7-correct: `_stop_event.set()` →
join → release. The 1s join timeout (`_CAPTURE_JOIN_TIMEOUT_SEC`) bounds
how long stop will wait. If the capture thread is currently inside
`self._video_source_factory(...)` (line 803) AND that factory is
`OpenCvVideoSource.__init__` doing a real `cv2.VideoCapture(idx,
CAP_DSHOW)`, **the OS-level open call can take 2-3 seconds on cold
DirectShow** [CITED OpenCV forum]. The 1s join times out, `stop()`
proceeds to `self._source.release()` (line 591) while the capture thread
is still about to assign the freshly-opened `_source` and call
`self._source.read()`.

Race outcomes:
* Best: `release()` on a not-yet-assigned handle (the local in factory
  hasn't been written to `self._source` yet) — no-op, capture thread
  later writes a new `_source` AFTER stop has already nulled the field
  → `_source` survives the stop and leaks until process exit.
* Worst: capture thread assigns `_source`, stop reads it, releases it,
  then capture thread calls `read()` on a released handle — driver
  behaviour undefined; on Windows DirectShow this can crash the process.

This race already existed in Phase 2 (`PySerialTransport.__init__` is
also blocking and has the same timeout) — Phase 3 inherits it.

**Fix sketch:**
Two complementary fixes:
1. **Cap `_CAPTURE_JOIN_TIMEOUT_SEC`** at a value larger than the
   factory's worst-case open time — e.g. raise from 1.0s to 4.0s
   (`_FIRST_FRAME_TIMEOUT_SEC + 1s` slack). The capture thread's wait
   inside `_attempt_reopen.stop_event.wait(backoff_ms / 1000)` returns
   immediately on `set()`, so the only blocking call left is the
   factory itself.
2. **Log a structured WARN when join times out** so the operator at
   least sees the leak:
   ```python
   if rx_thread.is_alive():
       self._logger.warning(
           "camera_thread_join_timeout",
           timeout_sec=_CAPTURE_JOIN_TIMEOUT_SEC,
           note="release proceeding; potential handle race",
       )
   ```
Apply the same fix to `arduino_motor.close()` for the parallel
`_RX_JOIN_TIMEOUT_SEC` race.

---

### W-07: `arduino_motor.close()` Exception suppression breadth

**File:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:264-273`
**Severity:** WARNING
**Category:** Tiger-style — broad except

**Issue:**
```python
with contextlib.suppress(asyncio.CancelledError, Exception):
    await self._recover_task
...
with contextlib.suppress(asyncio.CancelledError, Exception):
    await self._heartbeat_task
```
Suppressing `Exception` at shutdown is documented (W-05 done-callback
already retrieved + latched the exception, so re-raising on `await` is
redundant) — but `contextlib.suppress(Exception)` is wider than needed.
A `KeyError` or `RuntimeError` from cancellation cleanup would be hidden
even if it's a legitimate bug in the recovery code.

**Fix sketch:**
Narrow the suppression to the specific exception classes that the
done-callback contract acknowledges:
```python
# arduino_motor.py:266 — narrow to documented types.
with contextlib.suppress(asyncio.CancelledError, ArduinoError):
    await self._recover_task
```
This still tolerates the cancellation/graceful-error pair, but a
`RuntimeError` or `AssertionError` will surface — exactly what
tiger-style demands.

---

### W-08: `Frame` does not assert `image.flags['C_CONTIGUOUS']`

**File:** `pastor_tracker/src/pastor_tracker/core/types.py:62-84`
**Severity:** WARNING
**Category:** Cross-phase contract — Phase 4 perception

**Issue:**
YOLO11-pose / ultralytics requires C-contiguous arrays for zero-copy GPU
upload. `cv2.VideoCapture.read()` returns C-contiguous BGR uint8 always,
so production is fine. But a `FakeVideoSource` test that builds a frame
via slicing (e.g. `frame[..., ::-1]` for RGB→BGR) would produce a
non-contiguous view; passing it to YOLO would either crash or trigger an
implicit `np.ascontiguousarray` copy on the hot path.

**Fix sketch:**
```python
# core/types.py — add to __post_init__.
if not self.image.flags["C_CONTIGUOUS"]:
    raise ValueError(
        f"Frame.image must be C-contiguous; got strides={self.image.strides}"
    )
```
Pair with a test asserting that a non-contiguous BGR view raises.

---

### W-09: `Config.arduino_protocol_version` `Literal[2]` does not feed `arduino_motor` runtime check

**File:** `pastor_tracker/src/pastor_tracker/config.py:98`
                vs `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:296-297`
**Severity:** WARNING
**Category:** Config drift — unread tunable

**Issue:**
`Config.arduino_protocol_version: Literal[2] = 2` is pinned at the type
system. `arduino_motor._wait_for_ready` uses
`expected = PROTOCOL_VERSION_MAJOR` (the module constant from
arduino_protocol.py:62) — it does NOT read `self._config.arduino_protocol_version`.

If a future protocol bump goes from `Literal[2]` to `Literal[2, 3]` the
config field becomes a real runtime tunable — but the orchestrator
ignores it. CFG-04's traceability comment claims "pinned to v2 by
Literal" yet the value is never consulted. This is an unread tunable
that masquerades as enforced.

**Fix sketch:**
Either:
1. Drop `arduino_protocol_version` from Config entirely — the firmware
   constant is the source of truth; or
2. Read it in `_wait_for_ready`:
   ```python
   expected = self._config.arduino_protocol_version
   if expected != PROTOCOL_VERSION_MAJOR:
       raise ProtocolVersionMismatchError(
           f"config asks for v{expected}, host wired for "
           f"v{PROTOCOL_VERSION_MAJOR}"
       )
   ```
Recommend option 1 for v1 to keep the config surface honest.

---

## Threats to Phase 6 Orchestration

These are not BLOCKERs in their own right but compound the above
findings when wired into the pipeline:

* **B-02 + B-04** together mean the Phase 6 pipeline cannot reliably
  `try: ... except CameraError` to discriminate "camera stopped on demand"
  from "camera died" from "camera failed to start". Fix both before Phase 6.
* **W-06** + arduino_motor's parallel race mean a coordinated shutdown
  (Ctrl-C → cancel orchestrator → camera.stop() + motor.close()) can leak
  one handle from each I/O module. Bound the join timeouts at a value
  larger than the worst-case factory open before Phase 6.
* **B-03** + **W-08** together are the Phase 4 boundary — every Frame
  flowing into perception must be uint8 + C-contiguous, otherwise YOLO
  silently copies on every frame and burns 30%+ of the GPU budget.

---

## Notes on what is NOT a defect (intentionally hardened)

* `arduino_motor.send_emergency_stop` bypasses both pause and latched
  error — that's the documented safety contract, NOT a tiger-style
  violation.
* `_capture_loop`'s BLE001 catches are documented translators; both have
  comments justifying the broad catch and translate to typed errors.
* `ObsCamera`'s `_fault_with_stall` calls `_stop_event.set()` after
  scheduling `_on_capture_failed` — this is correct (prevents the WR-01
  guard from re-firing a duplicate fault) and is well-commented.
* `_RX_JOIN_TIMEOUT_SEC=1.0` and `_CAPTURE_JOIN_TIMEOUT_SEC=1.0` are
  symmetrically documented; the race window noted in W-06 is a
  pre-existing inheritance from Phase 2, not a Phase 3 regression.
* `Frame.image` non-readonly mutation is explicitly called out in the
  module docstring (line 12-15) — convention, not enforced by code,
  but documented.

---

_Reviewed: 2026-05-04_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep (project-wide cross-phase sweep)_
