---
phase: 2
fixed_at: 2026-05-03
review_path: .planning/phases/02-arduino-i-o/02-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-05-03
**Source review:** `.planning/phases/02-arduino-i-o/02-REVIEW.md`
**Iteration:** 1
**Scope:** Critical + Warning only (4 Info findings out of scope per `--auto` default).

**Summary:**
- Findings in scope: 7 (2 Critical + 5 Warning)
- Fixed: 7
- Skipped: 0

## Fixed Issues

### C-01: `close()` orphans `_recover_task`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_recovery.py`

**Commit:** `a5a535c`

**Applied fix:** `close()` now cancels and awaits the in-flight `_recover_task` BEFORE the heartbeat / RX-thread teardown, mirroring the heartbeat-task shutdown pattern. Sets `_recover_task = None` after the await. Without this, calling `close()` mid-recovery left the recover task running and writing into a closing transport, surfacing as `Task exception was never retrieved` -- a non-deterministic flake under `filterwarnings=["error"]`.

**Test added:** `test_close_during_recovery_cancels_cleanly` -- starts motor, triggers recovery (no acks fed), calls `close()`, asserts `_recover_task is None`, no warnings raised.

---

### C-02: `_recover` only catches `(TimeoutError, QueueEmpty)`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_recovery.py`

**Commit:** `a8938bc`

**Applied fix:** Broadened the `_recover` exception handlers:
- `LinkLostError` is preserved AS-IS (typed surface intact -- callers see `LinkLostError`, not `WatchdogResetError`).
- `ValueError` (e.g. 47-byte TX guard tripping during config drift) is wrapped in `WatchdogResetError("recovery write error: ...")`.
- `asyncio.CancelledError` is re-raised unchanged so `close()` cancellation semantics stay correct.

All non-cancellation paths log `watchdog_recovery_failed` with a distinct `reason=` and set `_state = FAULTED` before returning, preserving the documented "deterministic next-`send_*`-raises" contract.

**Tests added:**
- `test_recovery_link_lost_during_settings_send_latches_link_lost_error` -- triggers recovery, latches LinkLostError mid-recovery via `_on_link_lost`, asserts next `send_motor_angle` raises `LinkLostError` (not `WatchdogResetError`).
- `test_recovery_cancelled_propagates` -- cancels recover task directly, asserts `CancelledError` propagates and `_latched_error` stays `None`.

---

### W-01: Race on `_state` between `_on_rx_event` and `_recover`

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_recovery.py`

**Commit:** `6b17fed`

**Applied fix:** `_on_rx_event` now flips `_state = RECOVERING` and `_dispatch_paused = True` synchronously BEFORE `asyncio.create_task(self._recover())`. A second mid-session Ready bridged in the same tick batch hits an in-flight guard (`if self._recover_task is not None and not self._recover_task.done(): return`) and is dropped with `recovery_already_in_flight` log. `_recover` clears `self._recover_task = None` in a `finally` block so a future fault-and-recover cycle can re-spawn cleanly.

**Test added:** `test_burst_ready_does_not_double_recover` -- feeds two mid-session preambles back-to-back, asserts the same `_recover_task` reference holds throughout (no overwrite).

---

### W-02: Mid-session `Ready` while RECOVERING/FAULTED leaks into public events queue

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (same edit as W-01)
- `pastor_tracker/tests/test_arduino_motor_recovery.py`

**Commit:** `6b17fed` (combined with W-01)

**Applied fix:** The W-01 restructure folds W-02's contract in: any mid-session `Ready` outside `_state == RUNNING` (RECOVERING / FAULTED / DISCONNECTED / HANDSHAKING / CLOSED) is dropped at `_on_rx_event` without enqueuing to the public events queue. Logs `ready_ignored` at DEBUG with `state=...` so an operator can still trace the dropped lines.

**Test added:** `test_mid_session_ready_not_enqueued_to_public_events` -- triggers recovery, feeds an extra `Ready` while RECOVERING, drains the public events queue after recovery completes, asserts no `Ready` event appears.

---

### W-03: `send_settings` / `send_limits` lack input-range validation

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py`
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_tx.py`

**Commits:** `f7ba074`, `7b9b451` (lint follow-up: `math.isfinite` + sorted imports)

**Applied fix:**
- Mirrored firmware constants in `arduino_protocol.py` as `Final[float]`: `MIN/MAX_MAX_SPEED_STEPS_PER_SEC` (protocol.h:27-28), `MIN/MAX_MAX_ACCEL_STEPS_PER_SEC2` (protocol.h:29-30), and `LIMIT_ANGLE_MIN/MAX_DEG` (protocol.h:23-24, +/- 180 widened envelope superset).
- `send_settings` rejects out-of-firmware-clamp `max_speed` / `max_accel` and non-finite PID gains with descriptive `ValueError` messages including the firmware-citation.
- `send_limits` rejects NaN/inf, inverted/zero-area limits, and out-of-envelope magnitudes.

Tiger-style fail-loud at the host boundary instead of letting the firmware silently clamp.

**Tests added (parametrized):**
- `test_send_settings_rejects_out_of_range` -- 4 cases (under-min and over-max for both speed and accel).
- `test_send_settings_rejects_non_finite_pid` -- 3 cases (NaN/inf/-inf for each PID gain).
- `test_send_limits_rejects_out_of_range` -- 6 cases (inverted, zero-area, out-of-envelope min, out-of-envelope max, NaN min, inf max).

---

### W-04: `_check_seq_gap` modular arithmetic logs spurious 4.29e9 gap on regressed seq

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_seq_gap.py` (new file)

**Commit:** `531772f`

**Applied fix:** `_check_seq_gap` now classifies a delta exceeding `SEQ_MODULUS // 2` as a regression (logs `feedback_seq_regression` with `from_seq` / `to_seq`) and resets `_last_seq` to the new value. Small forward gaps still log `feedback_seq_gap` when above the warn threshold; uint32 rollover at the modulus boundary stays classified as a small forward gap (NOT a regression).

**Tests added (4 cases):**
- `test_seq_regression_logs_regression_event_not_gap` -- last_seq=10, receive seq=0; asserts `feedback_seq_regression` event with from_seq=10 / to_seq=0 and that no `feedback_seq_gap` event fires.
- `test_seq_forward_gap_above_threshold_logs_gap` -- 10-sequence forward gap, gap=10 in log.
- `test_seq_forward_gap_within_threshold_silent` -- contiguous +1 / +1 produces no log events.
- `test_seq_rollover_at_modulus_boundary_is_forward_not_regression` -- 0xFFFFFFFE -> 0 stays classified as forward gap of 2.

---

### W-05: `_heartbeat_task` lacks done-callback for non-`ArduinoError` exceptions

**Files modified:**
- `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
- `pastor_tracker/tests/test_arduino_motor_heartbeat.py`

**Commit:** `35c0dc6`

**Applied fix:**
- New `_on_task_done(task)` method ignores cancellation and `ArduinoError` (graceful exits), latches every other exception as `LinkLostError("<task_name> crashed: <exc>")` with the original preserved via `__cause__`, and transitions the motor to FAULTED.
- `start()` attaches `_on_task_done` as a done-callback to the heartbeat task.
- `_on_rx_event` attaches the same callback to the recover task on spawn.
- `close()` now suppresses `(CancelledError, Exception)` when awaiting heartbeat / recover tasks so a task that crashed mid-flight (and was already handled by the done-callback) does not re-raise during the drain. `BaseException` (KeyboardInterrupt / SystemExit) still propagates.

**Test added:** `test_heartbeat_task_unexpected_exception_latches` -- monkeypatches `motor.send_query` to raise `RuntimeError`, waits for FAULTED state, asserts `LinkLostError` is latched with `RuntimeError` referenced in the message, and that next `send_motor_angle` raises `LinkLostError`.

---

## Acceptance Gate Results

| Gate | Result |
|------|--------|
| `pytest -ra` (full suite) | **193 passed** (was 188 before fixes; 5 new tests) |
| `ruff check src tests` | **All checks passed** |
| `mypy --strict src` | **Success: no issues found in 16 source files** |
| `arduino_motor.py` line coverage | **93%** (acceptance: >=90%) |
| `arduino_protocol.py` line + branch coverage | **100% / 100%** (maintained) |
| Forbidden-pattern scan (`print\|bare except\|time.sleep`) | **0 matches** |

**Note on flakiness:** `test_golden_trace_replay` shows pre-existing flakiness under load (timing-sensitive 2s drain on a heartbeat-driven trace) -- it failed in 1 of ~10 full-suite runs, passes 100% in isolation. Not introduced by this fix series; the test file is unchanged.

## Commits

| ID | Hash | Subject |
|----|------|---------|
| C-01 | `a5a535c` | fix(02-CR): C-01 cancel _recover_task in close() to prevent orphan task |
| C-02 | `a8938bc` | fix(02-CR): C-02 broaden _recover except to latch LinkLost/ValueError |
| W-01/W-02 | `6b17fed` | fix(02-CR): W-01/W-02 race-free mid-session Ready dispatch |
| W-03 | `f7ba074` | fix(02-CR): W-03 host-boundary input validation for settings/limits |
| W-04 | `531772f` | fix(02-CR): W-04 distinguish seq regression from forward gap |
| W-05 | `35c0dc6` | fix(02-CR): W-05 done-callback latches unexpected task exceptions |
| W-03 lint | `7b9b451` | fix(02-CR): W-03 lint follow-up -- math.isfinite + sorted imports |

## FIX COMPLETE
