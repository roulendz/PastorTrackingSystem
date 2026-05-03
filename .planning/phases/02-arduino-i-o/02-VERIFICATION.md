---
phase: 2
slug: arduino-i-o
status: human_needed
must_haves_met: 8
must_haves_total: 8
coverage_arduino_protocol: 100%/100%
coverage_arduino_motor: 93%
coverage_arduino_transport: 88%
verified_at: 2026-05-03
flake_observed: test_golden_trace_replay (1/3 full-suite runs; 5/5 isolated runs)
---

# Phase 02 Verification

**Phase Goal:** A standalone, fully-tested async Arduino serial driver that auto-detects the Uno, completes the v2 boot handshake, runs the heartbeat, parses every RX line type, and recovers from MCU watchdog resets — proven against canned `FB:`/`READY:`/`ERROR:` replay before any other I/O exists.

## Goal Coverage

| Must-Have | Verified | Evidence |
|-----------|----------|----------|
| **IO-ARD-01** Auto-detect Uno (4 VID:PIDs + override + no-match crash) | VERIFIED | `arduino_transport.py:46-52` defines `SUPPORTED_VID_PIDS` frozenset of all 4 pairs (`0x2341:0x0043` Uno R3, `0x2341:0x0069` Uno R4, `0x1A86:0x7523` CH340, `0x0403:0x6001` FTDI). `discover_arduino_port` at `:176` returns first match, raises `ArduinoPortNotFoundError(:61)` on miss. `tests/test_arduino_transport.py` 16 tests cover all 4 VID:PIDs, multi-match (logs `port_multiple_matches`, picks first), single-match (logs `port_discovered`), configured-port override, configured-port-absent crash. All green. |
| **IO-ARD-02** v2 boot handshake | VERIFIED | `arduino_motor.py:209` `start()` invokes `_wait_for_ready` which skips `SettingsInfo` + `FeedbackHeader` per Pitfall 1, expects `Ready(version=2)`, raises `HandshakeTimeoutError` (`:305`) and `ProtocolVersionMismatchError` (`:333`). 3 tests in `test_arduino_motor_handshake.py` (handshake-success / mismatch / timeout) all green. |
| **IO-ARD-03** Async TX wrapper | VERIFIED | `arduino_motor.py:173` `self._tx_lock = asyncio.Lock()`. `_send_raw:719` uses `loop.run_in_executor` for blocking `transport.write`. `:713` enforces `len(line)+1 > FIRMWARE_INPUT_BUFFER_USABLE` (47-byte) pre-send guard. `test_arduino_motor_tx.py` 32 tests (10 named + 22 parametrized) cover M/S/L/R/Q/E/H/X/D byte format, lock serialization, oversize rejection, send_settings/send_limits range validation. All green. |
| **IO-ARD-04** Parser handles 9 RX line types + seq-gap WARN | VERIFIED | `arduino_protocol.py:414` `parse_line` dispatches FB / FB_HEADER / READY / ERROR / SETTINGS / LIMITS / DRIVER / RESET / STOP / DIAG (10 prefixes — 9 firmware-spec + structured/textual SETTINGS variants). 11 frozen Pydantic DTOs at `:147-235` plus `ProtocolParseError(:262)`. `_check_seq_gap` in `arduino_motor.py:449-479` distinguishes forward-gap (`feedback_seq_gap`) from regression (`feedback_seq_regression`) and uint32-rollover. **100% line + 100% branch coverage** (72 parser tests + 4 seq-gap tests, all green). |
| **IO-ARD-05** Heartbeat | VERIFIED | `arduino_motor.py:539` `_heartbeat_loop` emits `b"Q\n"` every `self._config.arduino_heartbeat_interval_ms / 1000` seconds. `close()` at `:247` cancels heartbeat task before RX-thread teardown. `test_arduino_motor_heartbeat.py` 3 tests including W-05 unexpected-exception latching (RuntimeError → LinkLostError via `_on_task_done`). All green. |
| **IO-ARD-06** Watchdog reset recovery | VERIFIED | `arduino_motor.py:393-411` `_on_rx_event` flips state to RECOVERING + pauses dispatch + spawns `_recover` (with in-flight guard for burst-Ready). `_recover` at `:567` re-issues settings → limits from Config (PC-authoritative), with structured-`Settings` vs textual-`SettingsInfo` ack discrimination, drains trailing `SettingsInfo`, latches `WatchdogResetError`+FAULTED on ack timeout (deterministic next-`send_*`-raises contract). 10 tests in `test_arduino_motor_recovery.py` including burst-Ready dedup, Ready-not-leaked-to-events, link-lost-during-recovery preserves typed surface, cancelled-propagates, close-during-recovery cleanup. All green. |
| **IO-ARD-07** ERROR halt | VERIFIED | `arduino_motor.py:424` enqueues `FirmwareErrorReceived` and latches it on any `Error(code=1..11)`. `_on_link_lost:495` latches `LinkLostError` (NOT `FirmwareErrorReceived(NONE)` — `ErrorCode.NONE` reserved for firmware sentinel per BLOCKER 2). `send_emergency_stop:851` bypasses both pause AND latched-error gates. `test_arduino_motor_error.py` 13 parametrized cases covering all 11 firmware error codes + emergency-stop bypass + link-lost typed surface. All green. |
| **TEST-04** Canned-line replay | PASSED (with documented flake) | `test_arduino_motor_replay.py:test_golden_trace_replay` runs ARDUINO_TRACE_GOLDEN through full pipeline (handshake → 3 FB → ERROR:11 halt) and asserts FAULTED state + heartbeat fired. Passes 5/5 in isolation; observed to fail 1/3 full-suite runs due to a 2s drain timeout under timing pressure (matches FIX report's documented pre-existing flake). |

**Score: 8/8 must-haves verified.**

## Test Suite

- `pytest -ra` (full Phase 1 + Phase 2 suite): **193 collected, 192 passed in clean isolation; one timing-sensitive flake observed (see Flake Analysis).**
- New tests in Phase 2: 121 (72 protocol + 16 transport + 33 motor + golden-trace replay).
- Wall clock: ~8.5 s.

## Quality Gates

| Gate | Result |
|------|--------|
| `ruff check pastor_tracker` | All checks passed |
| `mypy --strict pastor_tracker/src` | Success: no issues found in 16 source files |
| `arduino_protocol.py` line + branch coverage | 100% / 100% (gate ≥ 100%, met) |
| `arduino_motor.py` line coverage | 93% (gate ≥ 90%, met) |
| `arduino_transport.py` line coverage | 88% (gate ≥ 90% — **9 lines uncovered are all `PySerialTransport` real-hardware paths**, documented in `deferred-items.md`, deferred to Phase 8 QA-04 per CONTEXT.md) |
| `print()` / bare `except` / `time.sleep()` in app code | 0 hits (the one `# noqa: BLE001` in `_rx_loop` is the documented serial-error translator) |
| Forbidden libraries (PID / MediaPipe / EMA) | 0 hits |

## Flake Analysis

**Test:** `tests/test_arduino_motor_replay.py::test_golden_trace_replay`

**Reproduction:**
- Isolated: 5/5 PASS (`pytest tests/test_arduino_motor_replay.py`)
- Full suite under coverage: 1 failure observed in 3 consecutive `pytest -ra` runs
- FIX report's note: "1 of ~10 full-suite runs"

**Root cause (per FIX report and test source):** the test feeds the entire trace into `FakeSerialTransport` up-front and waits up to 2 seconds for `_drain` to consume the events. The 50 ms heartbeat interval shares the same RX queue and bounded-queue plumbing; under load (full suite running in parallel test infrastructure), the RX-thread enqueueing of 5 trace lines + ongoing heartbeat-`Q`/`FB` interplay can exceed the 2 s drain budget. **The behavior under test is correct;** the test budget is too tight for the full-suite-under-coverage workload.

**Classification:** This is a **test-infrastructure flake**, not a code bug. The behavior covered is also covered deterministically by the dedicated `test_arduino_motor_handshake.py`, `test_arduino_motor_heartbeat.py`, and `test_arduino_motor_error.py` suites. The TEST-04 contract (golden trace traverses the pipeline and ends FAULTED on ERROR:11) is verified — the timing margin is just thin.

**Recommendation (Warning, not Blocker):** Phase 2 ships, but a follow-up is warranted. Either:
- Raise the 2 s drain timeout to 5 s, or
- Pump trace lines on-demand instead of front-loading (mirrors firmware emission cadence), or
- Mark the test `@pytest.mark.flaky(reruns=2)` if pytest-rerunfailures is acceptable.

This does not block Phase 3 — Phase 3 (Camera I/O) does not depend on the replay test.

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| IO-ARD-01 | 02-02 | VID:PID auto-detect (4 supported pairs) + manual override + fail-loud | SATISFIED | `arduino_transport.py:46-52, 176`; 16 tests green |
| IO-ARD-02 | 02-03 | Boot handshake `READY:v2`; abort on mismatch / timeout | SATISFIED | `arduino_motor.py:209, 305, 333`; 3 tests green |
| IO-ARD-03 | 02-03 | Async TX wrapper, lock-serialised, 47-byte guard | SATISFIED | `arduino_motor.py:173, 713, 719`; 32 tests green |
| IO-ARD-04 | 02-01 | Parser for all 9 RX line types + seq-gap WARN | SATISFIED | `arduino_protocol.py:414`; 76 tests at 100% line+branch |
| IO-ARD-05 | 02-03 | 200ms heartbeat (or `arduino_heartbeat_interval_ms`) | SATISFIED | `arduino_motor.py:539`; 3 tests green |
| IO-ARD-06 | 02-03 | Mid-session `READY:v2` triggers settings+limits re-issue, paused `M:`, ack-timeout latches `WatchdogResetError` | SATISFIED | `arduino_motor.py:393, 567`; 10 tests green |
| IO-ARD-07 | 02-03 | `ERROR:N` halt + `LinkLostError` distinct from `FirmwareErrorReceived(NONE)` | SATISFIED | `arduino_motor.py:424, 495`; 13 tests green |
| TEST-04 | 02-03 | Canned-line replay through full pipeline | SATISFIED (flaky test budget — see Flake Analysis) | `test_arduino_motor_replay.py`; passes deterministically in isolation |

## Anti-Patterns Found

None. The single `# noqa: BLE001` in `_rx_loop` is documented as the deliberate serial-error → `LinkLostError` translator (cannot let daemon-thread exception escape silently); accepted in REVIEW iter-2.

## Human Verification Required

**Hardware-bound, deferred to Phase 8 QA-04 per CONTEXT.md / VALIDATION.md** — these are not Phase 2 gaps:

1. **Real Uno R3/R4 USB enumeration** — different hosts expose different VID:PID pairs; verify on production stage hardware.
   - Test: plug Uno, run `python -m pastor_tracker --probe-arduino`, confirm `port_discovered` log + correct port.
2. **Real AVR `WDTO_500MS` watchdog firing under load** — requires physical lockup conditions.
   - Test: command burst until firmware watchdog triggers; assert PC observes mid-session `READY:v2`, re-issues settings + limits, resumes within `arduino_ready_timeout_sec`.
3. **Real serial signal integrity at 115 200 baud over 3m USB cable** — venue cable / EMI conditions vary.
   - Test: on-stage smoke with production cable; observe no parser dropouts over a 30-minute session.
4. **Real DTR-toggle-on-port-open MCU reset** — requires real MCU + driver chip.
   - Test: open port, observe `READY:v2` within `arduino_ready_timeout_sec`.
5. **Real multi-Arduino-attached host (two devices matching VID:PID simultaneously)** — requires two physical devices.
   - Test: plug two boards, confirm INFO log lists both, first wins (no crash).

**Newly identified (recommend Phase 8 closure):**

6. **Replay test flake under load** — verify the timing fix lands cleanly.
   - Test: after the 2s → 5s drain bump (or equivalent), run `pytest -ra` 30 consecutive times under coverage; confirm 0 failures.

## Gaps

None blocking Phase 2 closure. The `arduino_transport.py` 88% coverage gap (target 90%) is a pre-existing, intentional deferral documented in `deferred-items.md` — the 9 uncovered lines are all `PySerialTransport` real-hardware paths covered by Phase 8 QA-04. CONTEXT.md and VALIDATION.md both acknowledge this.

## Verdict

**human_needed** — All 8 must-haves are programmatically verified against actual source + tests. Quality gates green (ruff, mypy strict, parser at 100%/100%, motor at 93%, transport at 88% with documented hardware-only deferral). One pre-existing test-infrastructure flake (`test_golden_trace_replay`) confirmed by reproduction (1/3 full-suite runs) — the underlying behavior is correct, the test budget is thin. Five hardware-only verifications are properly deferred to Phase 8 QA-04 per CONTEXT.md.

Ship-recommendation: **proceed to Phase 3** (camera I/O has no dependency on the replay test). File a low-priority follow-up to widen the replay test's drain budget; close the residual hardware verifications in Phase 8.

## VERIFICATION COMPLETE
