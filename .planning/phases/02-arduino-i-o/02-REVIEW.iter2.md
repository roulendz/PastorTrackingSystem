---
phase: 2
slug: arduino-i-o
iteration: 2
status: clean
critical_count: 0
warning_count: 0
info_count: 1
reviewed_at: 2026-05-03
---

# Phase 02 Code Review — Iteration 2

## Summary

All 7 in-scope iter-1 findings (2 Critical + 5 Warning) verified closed. Each fix landed at the documented site with the documented mechanism, and is exercised by a new pytest case that asserts the behavioural delta (not just the line edit). No regressions detected: emergency-stop bypass still bypasses `_dispatch_paused` AND the latched-error gate in FAULTED state (line 856); recovery's trailing `SettingsInfo` drain still runs unconditionally inside the `finally` precondition (line 654); the FB header / settings-info preamble skip in `_wait_for_ready` is unchanged. Cross-cutting concerns checked clean — no new `print()`, no bare `except`, no `Any`, all literals continue to live as module-level `Final` constants, all DTOs remain Pydantic `frozen=True`, all log event names stable. One residual iter-1 Info item carried forward (I-01 — `ErrorCode._value2member_map_` private-dunder access in `arduino_protocol.py:343` is unchanged); not in scope for `--auto`. Verdict: **clean**.

## Iter-1 Closure Status

| Finding | Status | Evidence |
|---------|--------|----------|
| C-01 (close orphans recover_task) | closed | `arduino_motor.py:264-268` — cancel + suppress(CancelledError, Exception) await + `_recover_task = None`, BEFORE heartbeat teardown. Test `test_close_during_recovery_cancels_cleanly` (`test_arduino_motor_recovery.py:368-395`) asserts `_recover_task is None` and `state == CLOSED` after close-during-RECOVERING. |
| C-02 (recover swallows only TimeoutError/QueueEmpty) | closed | `arduino_motor.py:609-650` — broadened to `(TimeoutError, QueueEmpty)` + dedicated `LinkLostError` (preserves typed surface) + `ValueError` (wrapped as `WatchdogResetError`) + `CancelledError` (re-raised). All non-cancel paths set `_state=FAULTED`, log distinct `reason=`, return. Tests `test_recovery_link_lost_during_settings_send_latches_link_lost_error` (line 193-244) and `test_recovery_cancelled_propagates` (line 247-279) assert each exception class is handled correctly. |
| W-01 (race on _state spawning duplicate _recover) | closed | `arduino_motor.py:393-411` — sync state flip to RECOVERING + `_dispatch_paused=True` BEFORE `create_task`, plus in-flight guard `if self._recover_task is not None and not self._recover_task.done(): return`. `_recover` clears `self._recover_task = None` in `finally:` (line 668-671) so post-recovery a fresh Ready can spawn cleanly. Test `test_burst_ready_does_not_double_recover` (line 282-325) asserts identity equality of `_recover_task` after a back-to-back Ready burst. |
| W-02 (mid-session Ready leaks to public events queue) | closed | `arduino_motor.py:383-417` — every Ready outside `_state == RUNNING` returns BEFORE `_enqueue`. RUNNING path also returns. FAULTED/RECOVERING/HANDSHAKING/DISCONNECTED/CLOSED hit the `_logger.debug("ready_ignored")` branch and return. Test `test_mid_session_ready_not_enqueued_to_public_events` (line 328-365) feeds a second Ready while RECOVERING and asserts no `Ready` instance in the drained public queue. |
| W-03 (send_settings/send_limits lack range validation) | closed | `arduino_protocol.py:74-84` declares clamp `Final[float]` constants citing `protocol.h:27-30` and `:23-24`. `arduino_motor.py:756-804` (send_settings) rejects out-of-clamp `max_speed`/`max_accel` with cited `ValueError`, rejects non-finite PID via `math.isfinite`. `arduino_motor.py:806-835` (send_limits) rejects non-finite, inverted (`min_deg >= max_deg`), and out-of-envelope. Tests `test_send_settings_rejects_out_of_range` (4 cases), `test_send_settings_rejects_non_finite_pid` (3 cases), `test_send_limits_rejects_out_of_range` (6 cases) cover the boundary surface. |
| W-04 (seq regression mis-classified as 4.29e9 forward gap) | closed | `arduino_motor.py:449-479` — gap > `SEQ_MODULUS // 2` classified as `feedback_seq_regression` (logs `from_seq` + `to_seq`, resets `_last_seq`); only smaller forward gaps log `feedback_seq_gap`. New test file `test_arduino_motor_seq_gap.py` exercises 4 cases including the rollover boundary `0xFFFFFFFE -> 0` confirming it stays a forward gap. |
| W-05 (heartbeat task crash silently swallowed) | closed | `arduino_motor.py:498-533` — new `_on_task_done` callback latches non-cancel/non-`ArduinoError` exceptions as `LinkLostError(__cause__=exc)`, sets `_state=FAULTED`, logs `task_crashed`. Wired to heartbeat at start (line 241) and to recover-task at spawn (line 410). Test `test_heartbeat_task_unexpected_exception_latches` (line 45-81) monkey-patches `send_query` to raise `RuntimeError`, asserts the next `send_motor_angle` raises `LinkLostError`. |

## NEW Critical Findings (0)

None.

## NEW Warning Findings (0)

None.

## Residual Iter-1 Info (carried forward, not blocking)

| ID | Status | Note |
|----|--------|------|
| I-01 | unchanged | `arduino_protocol.py:343` still uses `ErrorCode._value2member_map_` (private dunder-adjacent attr). Out of `--auto` scope. |
| I-02 | unchanged | `arduino_motor.py:660` retains `if isinstance(trailing, SettingsInfo):` — tautology kept for type-narrowing readability. Out of scope. |
| I-03 | unchanged | Handshake still uses module-level `PROTOCOL_VERSION_MAJOR` rather than `self._config.arduino_protocol_version`. Out of scope. |
| I-04 | unchanged | `events()` async-iterator multi-consumer contract still implicit. Out of scope. |

## Cross-Cutting Quality Checks (depth=standard)

- `print()` / bare `except` / `time.sleep()` in app code: **0 hits** in `pastor_tracker/src/` for any of these patterns (the one `except Exception: # noqa: BLE001` in `_rx_loop:356` is the documented serial-error translator pre-dating iter-1).
- `Any` in source: **0** (mypy strict still passes per the FIX report's Acceptance Gate).
- Magic numbers in new edits: **0** — every constant introduced (`MIN_MAX_SPEED_STEPS_PER_SEC`, `MAX_MAX_ACCEL_STEPS_PER_SEC2`, `LIMIT_ANGLE_MIN_DEG`, `LIMIT_ANGLE_MAX_DEG`) is `Final[float]` with a `protocol.h:` line citation.
- Nesting depth in modified blocks: `_recover` body has nested `try/except` but no nested `if`s; `_on_rx_event` Ready arm uses guard-clause returns at depth 2; `send_settings`/`send_limits` validations use flat `if not (...): raise` ladders. All within the ≤ 2-level rule.
- Pydantic `frozen=True`: all DTOs in `arduino_protocol.py` still inherit from `_Event` with `model_config = ConfigDict(frozen=True, extra="forbid")` (line 144). No mutability regressions.
- Structlog event names are stable: `feedback_seq_gap` retained for forward gaps; `feedback_seq_regression` is the only new event name; `recovery_already_in_flight`, `ready_ignored`, `task_crashed`, `watchdog_recovery_failed` (with `reason=` field for sub-classification) are the only other additions. No renamed pre-existing events.
- Emergency-stop contract intact: `send_emergency_stop` (line 851-856) still bypasses `_raise_if_latched()` AND `_dispatch_paused` — verified by `test_emergency_stop_bypasses_pause`.
- Trailing `SettingsInfo` drain still runs on success path (line 654-664) AND inside `try`-block so a `TimeoutError` during the drain is suppressed and recovery completes; the surrounding `finally` still clears `_recover_task = None`.
- W-05 done-callback ordering: a task that crashes mid-flight has its exception retrieved by `task.exception()` inside the callback (line 513), so awaiting the same task in `close()` re-raises an already-retrieved exception — suppressed by `contextlib.suppress(CancelledError, Exception)` (line 266, 271). No `Task exception was never retrieved` warning surface remains.
- `_on_task_done` with `_state = CLOSED` race: if a task crashes BEFORE `close()` runs, the callback latches FAULTED + `_latched_error`. Then `close()` runs, eventually sets `_state = CLOSED`. The reverse order is impossible because a cancelled task hits `task.cancelled() -> return` early in the callback. No state-machine corruption.

## Verdict

**clean** — all 7 in-scope iter-1 findings closed with verified mechanisms and behavioural test coverage; no new Critical or Warning findings; no regressions to emergency-stop, trailing-drain, or queue-bounding contracts. Phase 02 is ship-ready.

## CODE REVIEW COMPLETE
