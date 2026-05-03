---
phase: 2
slug: arduino-i-o
status: findings
critical_count: 2
warning_count: 5
info_count: 4
reviewed_at: 2026-05-03
---

# Phase 02 Code Review

## Summary

Protocol fidelity is excellent (every constant cited to `protocol.h` line; ErrorCode enum matches 0..11; FB/SETTINGS/LIMITS field shapes match `main.cpp`). The pure parser is well-guarded against DoS (256 B cap), NaN/inf, and non-ASCII bytes. Concurrency invariants (single writer behind `_tx_lock`; single reader on RX thread; bounded queue with drop-oldest) are correctly implemented in the happy path. However, **two ship-blocking defects** exist in lifecycle and exception handling around the watchdog-recovery state machine: the recovery `asyncio.Task` leaks at `close()` and is never cancelled or awaited, and exceptions raised by `send_settings` / `send_limits` *inside* `_recover` (e.g., a `LinkLostError` that latches mid-recovery) escape the narrow `except (TimeoutError, QueueEmpty)` and become unhandled task exceptions — under the project's `filterwarnings=["error"]` policy these become non-deterministic test failures and, in production, a swallowed crash. Five warnings around state-mutation race in mid-session `Ready` handling, a stale-Ready-leak path, an unused-result idiom, and one buffer-overflow risk on the `send_settings` API surface should be fixed before merge.

## Critical Findings (2)

- **C-01** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:235-251` — `close()` never cancels or awaits `self._recover_task`. If `close()` is called while `_recover` is mid-flight (e.g., test cleanup after triggering a watchdog reset, or production shutdown during recovery), the task continues running on the loop and may call `send_settings` / `send_limits` against a now-closed transport, leading to either `FakeSerialClosedError` (in tests) or `serial.SerialException` (in prod) inside the orphan task — surfaced as a `Task exception was never retrieved` warning. With `[tool.pytest.ini_options] filterwarnings = ["error"]`, this is a non-deterministic test failure. **Fix:** mirror the heartbeat-task shutdown at top of `close()`:
  ```python
  if self._recover_task is not None:
      self._recover_task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
          await self._recover_task
      self._recover_task = None
  ```
  Place this BEFORE setting `_stop_event` so the recover task does not race the transport close. **Why critical:** orphan async task + closed transport = data corruption window and warnings-as-errors flake.

- **C-02** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:489` — `_recover` only catches `(asyncio.TimeoutError, asyncio.QueueEmpty)`. The body calls `self.send_settings(...)` and `self.send_limits(...)`, both of which call `_raise_if_latched()` and may also propagate any error from `_send_raw` (e.g., `serial.SerialException` translated to `LinkLostError` by the RX bridge while recovery is in flight; or `ValueError` from the 47-byte buffer guard if config drifts; or `asyncio.CancelledError` if `close()` ever does cancel the task per C-01). None of those are caught, so the exception escapes to the asyncio loop as an unhandled-task exception. **Fix:** broaden the catch to `(TimeoutError, asyncio.QueueEmpty, ArduinoError, ValueError)` and on any of those, latch a `WatchdogResetError(f"recovery aborted: {exc}")` (preserving the original via `__cause__`), log `watchdog_recovery_failed` with the concrete exception type, and return. Re-raise `asyncio.CancelledError` unchanged. **Why critical:** the documented contract is "deterministic surface = next `send_*` raises latched error"; an unhandled task exception breaks that contract and produces warnings-as-errors flake under the project pytest config.

## Warning Findings (5)

- **W-01** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:351-357` — Race between `_on_rx_event` and `_recover` on `_state`. The mid-session-`Ready` detection reads `self._state is _MotorState.RUNNING` synchronously, but the transition to `RECOVERING` happens asynchronously inside `_recover` (line 464). If two `Ready` lines are bridged via `call_soon_threadsafe` before `_recover` runs even one statement, both callbacks see `RUNNING` and spawn duplicate `_recover` tasks — `self._recover_task` is overwritten and the first task leaks. **Fix:** flip state to `RECOVERING` synchronously inside `_on_rx_event` BEFORE scheduling the task; remove the redundant `self._state = _MotorState.RECOVERING` from line 464.
  ```python
  if isinstance(event, Ready) and self._state is _MotorState.RUNNING:
      self._state = _MotorState.RECOVERING
      self._dispatch_paused = True
      self._recover_task = asyncio.create_task(self._recover())
      return
  ```

- **W-02** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:351,377` — During `_state == _MotorState.RECOVERING`, an additional mid-session `Ready` (a second MCU reset during recovery) does NOT match the guard at line 351 and falls through to the unconditional `self._enqueue(event)` at line 377, leaking the `Ready` into the public events queue. The class docstring (line 352-354) explicitly states "Don't enqueue this Ready into the public events queue — it is a control signal, not a payload event." **Fix:** broaden the predicate to `self._state in (_MotorState.RUNNING, _MotorState.RECOVERING)` — re-trigger the recovery flow if already in flight, or at minimum drop the duplicate Ready (do not leak to events queue). Same for `_state == _MotorState.FAULTED` — Ready post-fault should not appear as a payload event.

- **W-03** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:601-617` — `send_settings` 47-byte buffer-guard is an `assert`-style `ValueError` after-the-fact rather than a domain validation. A caller passing `pid_p=99999.999` produces `S:25000.000,12500.000,99999.999,0.000,0.000` = 41 bytes payload, fits. But `max_speed=99999.999` produces `S:99999.999,...` = also fine at 41 bytes. The realistic max under firmware clamps is `50000.000` / `30000.000` so total ≤ 39 bytes payload — safe in practice. However, the public API does not enforce firmware clamp ranges (`MIN/MAX_MAX_SPEED_STEPS_PER_SEC` from `protocol.h:27-30`), so a caller can submit an out-of-firmware-range value that the firmware will clamp silently. **Fix:** validate inputs against the firmware clamp ranges in `send_settings` / `send_limits` and raise a typed `ValueError` with the firmware-citation in the message. Tiger-style fail-loud at the host boundary instead of silent firmware clamping.

- **W-04** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:393-402` — `_check_seq_gap` modular-arithmetic gap will report a huge spurious gap (~2^32) on any out-of-order or regressed `sequence` field. While serial protocols rarely re-order, a buggy firmware emission or a multiple-Ready/post-reset sequence resetting to 0 would log `feedback_seq_gap` with `gap=4294967295` and silently update `_last_seq` to the regressed value, masking subsequent real gaps. **Fix:** detect a regression (`fb.sequence < self._last_seq` AND `(self._last_seq - fb.sequence) < SEQ_MODULUS // 2`) and log `feedback_seq_regressed` instead of `feedback_seq_gap`; reset `_last_seq` if the regression is small (likely a watchdog reset's fresh seq=0).

- **W-05** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:202-233` — `start()` does not register an exception-handler-on-task-failure for `_heartbeat_task`. If `_heartbeat_loop` raises anything other than `ArduinoError` or `CancelledError` (e.g., a programmer error in `send_query` after future refactor), the exception is silently dropped until the task is GC'd, then surfaces as `Task exception was never retrieved`. **Fix:** add a `task.add_done_callback` that re-raises any non-cancelled, non-`ArduinoError` exception via `loop.call_exception_handler`, or wrap the loop in a `try/except Exception` that latches `LinkLostError` for unknown failures.

## Info Findings (4)

- **I-01** `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py:328` — Uses `ErrorCode._value2member_map_` (a private dunder-adjacent attribute of `enum.IntEnum`). Works today on CPython but is implementation-specific. Replace with the public idiom `code_int in {c.value for c in ErrorCode}` or `try: ErrorCode(code_int) except ValueError: code = code_int`.

- **I-02** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:509` — `if isinstance(trailing, SettingsInfo):` is a tautology — `_wait_for_event(SettingsInfo, ...)` only returns when `isinstance(ev, event_type)` matches (line 544), so `trailing` is always `SettingsInfo` here. The check is dead code. Remove the `isinstance` and assign the type narrowly via assertion or use `cast(SettingsInfo, trailing)`.

- **I-03** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:265` — Handshake checks `event.version != PROTOCOL_VERSION_MAJOR` (the module-level constant from `arduino_protocol.py`) rather than `self._config.arduino_protocol_version`. While `Config` pins the value to `Literal[2]`, the parser-side constant is the authority — but the operator-set Config should be the one consulted at the orchestrator boundary, with the parser constant treated as a fallback / sanity check. Trivially fix by reading `expected = self._config.arduino_protocol_version` and asserting `expected == PROTOCOL_VERSION_MAJOR` once at construction.

- **I-04** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py:667-675` — `events()` is documented as an async iterator but returns one shared `_rx_queue.get()`-driven generator. Calling `events()` from two consumers splits events between them non-deterministically. Either document the single-consumer contract explicitly in the docstring or expose a typed `subscribe()` that fan-outs events. Phase 6 (pipeline) will be the first consumer; clarify before then.

## Verdict
fix-blockers-then-ship — C-01 and C-02 must be resolved before merge; W-01..W-05 should be folded in but do not block on their own.

## CODE REVIEW COMPLETE
