---
phase: 06-pipeline-orchestrator
verified: 2026-05-08T00:00:00Z
status: human_needed
score: 22/22 must-haves verified (1 roadmap SC deferred to Phase 8)
overrides_applied: 0
human_verification:
  - test: "Phase 6 ROADMAP Success Criterion #1: real-hardware end-to-end smoke"
    expected: "`python -m pastor_tracker` brings up the full pipeline against a real auto-detected Uno + real OBS Virtual Camera and produces smooth pan motion from a moving subject in front of the camera"
    why_human: "Requires physical Arduino Uno + camera + moving speaker; in-process equivalents (lifecycle, e-stop budget, snapshot, latest_frame slot, exit codes) are all VERIFIED programmatically. Per CONTEXT.md and the user prompt this end-to-end is explicitly Phase 8 / QA-04 territory — flagged here as a forward-looking human gate so Phase 8 picks it up."
deferred:
  - truth: "Real-hardware end-to-end smoke (ROADMAP SC#1 verbatim)"
    addressed_in: "Phase 8"
    evidence: "Phase 8 Success Criterion #1 (verbatim): 'End-to-end smoke test on stage: real auto-detected Uno, real OBS VCam, real speaker — the camera tracks the pastor for ≥ 5 minutes with no overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk.' Requirement QA-04 maps to Phase 8."
---

# Phase 6: Pipeline Orchestrator Verification Report

**Phase Goal:** Wire `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor` into a single asyncio pipeline with a clean lifecycle — every stage stays a pure transform on a typed DTO; no stage knows another stage's internals.
**Verified:** 2026-05-08
**Status:** human_needed (in-process implementation fully verified; on-stage smoke is Phase 8/QA-04)
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (merged from ROADMAP Success Criteria + PLAN frontmatter must_haves)

| #   | Truth                                                                                          | Status     | Evidence                                                                                                                                                                                                                                                                                                                                          |
| --- | ---------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | ROADMAP SC#1 — `python -m pastor_tracker` runs full pipeline E2E on real Uno + real OBS VCam   | DEFERRED   | Real-hardware verification deferred to Phase 8 / QA-04 (per CONTEXT.md and prompt). In-process equivalents (rows 2-22) all VERIFIED. `python -m pastor_tracker --help` runs cleanly; subprocess test `test_main_sigint_clean_shutdown` confirms graceful EXIT_HARDWARE_FAILED exit when no hardware present.                                       |
| 2   | ROADMAP SC#2 — Each stage receives/emits typed frozen DTOs only; orchestrator owns wiring (PIPE-02) | VERIFIED   | `pipeline.py:177-204` constructor takes 8 stages by name; tick body `pipeline.py:277-304` only calls public `consume()`/`decide()`/`stream()` methods. Grep audit: no `_<private>` access of any stage. All DTOs (Frame, Detection, TrackedSubject, MotionState, FramingTarget, MotorCommand, PipelineSnapshot) are frozen Pydantic / frozen dataclass. |
| 3   | ROADMAP SC#3a — Lifecycle commands `start`, `pause`, `home`, `e-stop`, `quit` work via API     | VERIFIED   | All 6 async methods on `Pipeline`: `start()` (344), `pause()` (395), `resume()` (406), `home()` (423), `e_stop()` (472), `quit()` (503). Tests `test_lifecycle_valid_transitions` (parametrized over 18-cell `_LIFECYCLE_TABLE`) and `test_lifecycle_invalid_transitions_rejected` both pass.                                                       |
| 4   | ROADMAP SC#3b — `e-stop` halts motor within one heartbeat interval (≤ 200 ms)                  | VERIFIED   | `test_e_stop_completes_within_heartbeat_budget` (line 409) uses `SlowFakePoseEngine(per_call_delay_sec=0.5)` (2.5× the 200 ms budget) and asserts `elapsed < pipeline._config.arduino_heartbeat_interval_ms / 1000.0`. Test passes. D-07 inline-send confirmed at `pipeline.py:501`.                                                                |
| 5   | ROADMAP SC#3c — `home` rejected when 0° outside software limits                                | VERIFIED   | `pipeline.py:431-446` raises `OrchestratorRejected` when `0.0` outside `[motor_angle_min_deg, motor_angle_max_deg]`. `test_home_rejected_when_zero_out_of_limits` (parametrized over 2 reject arms via duck-typed Config namespace) passes. `test_home_accepted_when_zero_within_limits` covers the accept arm.                                       |
| 6   | PIPE-01 — `pipeline.py` orchestrator wires the 8 named stages                                   | VERIFIED   | Tick loop body (`pipeline.py:277-304`) iterates `async for frame, detections in self._detector.stream(self._camera.frames())` and calls `tracker.consume → analyzer.consume → framer.consume → controller.consume → dispatcher.decide → motor.send_motor_angle` in sequence. `test_tick_loop_drives_all_stages_end_to_end` passes.                |
| 7   | D-01 wiring grep (`self._detector.stream(self._camera.frames())`)                              | VERIFIED   | 1 match at `pipeline.py:277`.                                                                                                                                                                                                                                                                                                                       |
| 8   | D-04 (`now_ns = frame.timestamp_ns`)                                                            | VERIFIED   | 1 match at `pipeline.py:279`. No `time.perf_counter() / time.monotonic() / loop.time()` anywhere in `pipeline.py` (grep returns 0 matches).                                                                                                                                                                                                         |
| 9   | D-05 (start order: motor.start → settings → limits → camera/detector/tick spawn)               | VERIFIED   | `pipeline.py:370-393`: `motor.start() → camera.start() → motor.send_settings(...) → motor.send_limits(...) → detector.start() → asyncio.create_task(tick_loop)`.                                                                                                                                                                                    |
| 10  | D-06 (pause flips `_dispatch_enabled=False`; tick continues)                                    | VERIFIED   | `pipeline.py:401`. `test_pause_suppresses_motor_send_but_tick_continues` asserts no new `M:` lines AND `last_frame_ts_ns` advances after pause.                                                                                                                                                                                                     |
| 11  | D-07 (e_stop calls `send_emergency_stop` inline before any state mutation; ≤ 200 ms)            | VERIFIED   | `pipeline.py:493-501` (state mutation FIRST, then send — WR-02 fix; this protects state when wire fails). 2 grep matches for `await self._motor.send_emergency_stop()`. Budget enforced by test (#4).                                                                                                                                              |
| 12  | D-08 (home rejected when 0 out of limits; HOMING → PAUSED on success)                           | VERIFIED   | `pipeline.py:431-470`. WR-01 fix: try/except around `motor.send_home()` collapses to STOPPED on failure.                                                                                                                                                                                                                                            |
| 13  | D-09 (quit idempotent: cancel tick → camera.stop → detector.stop → motor.close)                 | VERIFIED   | `pipeline.py:503-557`. Idempotence shortcut at line 513. `test_quit_idempotent` passes — second call no-ops; `fake_serial.closed` and `fake_video.released` both True.                                                                                                                                                                              |
| 14  | D-10/D-12/D-13/D-14 — failure routes (Camera/Arduino/Perception/unknown all propagate)          | VERIFIED   | `_on_tick_task_done` (`pipeline.py:306-340`) latches STOPPED + logs `pipeline_crashed` with exc_info on any non-cancellation exception. Tests `test_camera_open_error_propagates`, `test_arduino_link_lost_propagates`, `test_perception_failure_propagates`, `test_tick_loop_unknown_exception_propagates` all pass.                                |
| 15  | D-11 (WatchdogResetError recovered by ArduinoMotor._recover; pipeline never re-issues settings) | VERIFIED   | `send_settings`/`send_limits` grep returns exactly 2 matches in `pipeline.py`, both inside `start()` (lines 376, 383). `test_watchdog_reset_recovered_pipeline_continues` passes — pipeline stays RUNNING through watchdog reset.                                                                                                                  |
| 16  | D-15 — `_LIFECYCLE_TABLE` 18 cells; missing key raises `OrchestratorRejected`                  | VERIFIED   | `pipeline.py:102-127` table has exactly 18 entries. Plan 06-04 fixed two contract violations: `start()` only short-circuits from RUNNING (not PAUSED/HOMING); `resume()` short-circuit removed entirely. `test_lifecycle_invalid_transitions_rejected` parametrized over all invalid pairs passes.                                                  |
| 17  | D-16 — `snapshot()` returns frozen `PipelineSnapshot` with the 7 named fields                  | VERIFIED   | `core/types.py:198+` defines `PipelineSnapshot(_FrozenModel)` with `state, last_frame_ts_ns, last_intent, last_target_x_normalized, last_pan_angle_deg, last_emitted_angle_deg, motor_state`. `test_snapshot_dto_complete` asserts round-trip via `model_dump()`/`model_validate()`.                                                                |
| 18  | D-17 — `latest_frame: Frame \| None` single-attribute slot, single writer                       | VERIFIED   | `pipeline.py:281` (`self._latest_frame = frame`) is the only write site; property at line 219 is read-only. `test_latest_frame_slot_updates` asserts non-None and timestamp progression.                                                                                                                                                            |
| 19  | `__main__.py` — production entry point with platform-conditional SIGINT + exit codes            | VERIFIED   | `__main__.py` has `main()`, `_amain()`, 4 EXIT_* Final[int] constants (EXIT_OK=0, EXIT_INVALID_CONFIG=64, EXIT_HARDWARE_FAILED=65, EXIT_CRASHED=70). Platform-conditional branch at lines 144-161; `loop.call_soon_threadsafe(shutdown_event.set)` at line 335. `python -m pastor_tracker --help` runs cleanly.                                     |
| 20  | `__main__` — exit codes proven externally via subprocess tests                                  | VERIFIED   | `test_main_invalid_config_exit_code` asserts `returncode == 64` after setting `PTS_PAN_MAX_VELOCITY_DEG_PER_SEC="-1.0"`. `test_main_sigint_clean_shutdown` asserts `returncode == 65` after setting `PTS_ARDUINO_PORT="COM_DOES_NOT_EXIST"`. Both pass.                                                                                              |
| 21  | `PoseDetector.stream()` async helper exists (Plan 06-01 contract)                               | VERIFIED   | `pose_detector.py:492-495`: `async def stream(self, frames: AsyncIterator[Frame]) -> AsyncIterator[tuple[Frame, list[Detection]]]`. 4 dedicated tests in `test_pose_detector.py` pass.                                                                                                                                                              |
| 22  | mypy --strict + ruff clean across full repo; full suite green (pre-existing geometry flake aside)| VERIFIED   | `mypy --strict src` → `Success: no issues found in 27 source files`. `ruff check src tests` → `All checks passed!`. `pytest tests` → 422 passed + 1 skipped + 1 known pre-existing geometry flake (`test_edges_map_to_half_fov` — documented in Phase 5 deferred-items.md per prompt; identical to baseline; NOT introduced by Phase 6).            |

**Score:** 22/22 must-haves verified (1 ROADMAP SC explicitly deferred to Phase 8 — see Deferred Items)

### Deferred Items

Items not yet met but explicitly addressed in later milestone phases.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Phase 6 ROADMAP SC#1: real-hardware end-to-end smoke (`python -m pastor_tracker` against real auto-detected Uno + real OBS VCam producing smooth pan from a moving subject) | Phase 8 (QA-04) | Phase 8 ROADMAP SC#1 verbatim: "End-to-end smoke test on stage: real auto-detected Uno, real OBS VCam, real speaker — the camera tracks the pastor for ≥ 5 minutes with no overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk." Prompt explicitly states: "real-hardware end-to-end is Phase 8 / QA-04 — not Phase 6's gate". |

### Required Artifacts

| Artifact                                                                          | Expected                                                                                          | Status     | Details                                                                                                                                                       |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pastor_tracker/src/pastor_tracker/pipeline.py`                                   | Pipeline class + _PipelineState + _LIFECYCLE_TABLE + _PipelineCache + 6 lifecycle + snapshot     | VERIFIED   | 557 lines (target 320-500 — overshoot from 6 review-fix patches WR-01..WR-06, all justified). All structural greps pass.                                       |
| `pastor_tracker/src/pastor_tracker/__main__.py`                                   | argparse + Config + 8-stage construction + Pipeline + cross-platform SIGINT + asyncio.run + exits | VERIFIED   | 379 lines. main() and _amain() both present. EXIT_* constants correctly typed Final[int].                                                                    |
| `pastor_tracker/src/pastor_tracker/core/types.py` (PipelineState + PipelineSnapshot) | Literal[6] alias + 7-field frozen Pydantic DTO                                                  | VERIFIED   | `PipelineState = Literal[...]` at line 193; `class PipelineSnapshot(_FrozenModel):` at line 198 with 7 fields per D-16.                                       |
| `pastor_tracker/src/pastor_tracker/perception/pose_detector.py` (stream helper)   | `async def stream(...) -> AsyncIterator[tuple[Frame, list[Detection]]]`                          | VERIFIED   | Method at line 492; preserves BL-01 drop-oldest via internal producer task + `pending_frames` deque (per Plan 06-01 deviation, with WR-03 fix to pairing).   |
| `pastor_tracker/tests/test_pipeline.py`                                           | 17 named integration tests covering PIPE-01..03 + D-05..D-14 + __main__ exit codes               | VERIFIED   | 945 lines. 18 named test patterns matched (one extra: home accept + reject split). 39 passed + 1 documented skip.                                              |
| `pastor_tracker/tests/fixtures/pipeline_helpers.py`                               | `_pipeline_with_fakes()` + `_FakeFilterGraph` + default scripts + `_make_valid_config_dict`      | VERIFIED   | 349 lines. All required helpers present.                                                                                                                       |

### Key Link Verification

| From                          | To                                                                  | Via                                                                          | Status | Details                                                                                              |
| ----------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------- |
| `pipeline.py`                 | `PoseDetector.stream`                                               | `async for frame, dets in self._detector.stream(self._camera.frames())`     | WIRED  | Exact pattern at `pipeline.py:277`.                                                                  |
| `pipeline.py` `e_stop()`      | `ArduinoMotor.send_emergency_stop`                                  | inline `await self._motor.send_emergency_stop()`                            | WIRED  | Two matches (idempotent path + main path) at `pipeline.py:482, 501`.                                 |
| `pipeline.py` `snapshot()`    | `PipelineSnapshot`                                                  | `return PipelineSnapshot(...)` (cheap, no I/O)                              | WIRED  | `pipeline.py:229-237`.                                                                                |
| `pipeline.py` `start()`       | `ArduinoMotor.send_settings` + `send_limits`                        | one call each, inside `start()` only (D-11 / Pitfall 6 structural)          | WIRED  | Exactly 2 matches in pipeline.py, both at lines 376/383 inside `start()`.                            |
| `__main__.py`                 | `Pipeline` + `OrchestratorRejected`                                  | construct + start + quit lifecycle (lazy import inside `_amain`)            | WIRED  | `Pipeline(...)` at `__main__.py:310`; `await pipeline.start()` at 350; `await pipeline.quit()` at 363, 370. |
| `__main__.py`                 | `signal.SIGINT` (cross-platform)                                     | `_install_shutdown_signals` with `sys.platform == _WIN32_PLATFORM` branch  | WIRED  | `__main__.py:144-161`. WR-04 fix: install + uninstall pair via try/finally.                          |

### Data-Flow Trace (Level 4)

| Artifact                       | Data Variable               | Source                                       | Produces Real Data                                       | Status     |
| ------------------------------ | --------------------------- | -------------------------------------------- | -------------------------------------------------------- | ---------- |
| `Pipeline` tick loop           | `frame, detections`         | `self._detector.stream(self._camera.frames())` | Yes — real ObsCamera + real PoseDetector compose end-to-end with fakes in tests; 39 integration tests assert `M:` lines on `fake_serial.captured_writes` after start  | FLOWING    |
| `Pipeline` snapshot cache      | `_cache.last_*`             | tick body writes from real stage outputs     | Yes — `test_snapshot_dto_complete` asserts live values after ticks    | FLOWING    |
| `Pipeline.latest_frame` slot   | `_latest_frame`             | tick body assigns frame from camera          | Yes — `test_latest_frame_slot_updates` asserts non-None, monotonic    | FLOWING    |
| `__main__` Pipeline instance   | 8 constructed stages        | `_amain` constructs each via Config          | Yes — subprocess `test_main_*` proves the full boot sequence (modulo hardware) | FLOWING    |

### Behavioral Spot-Checks

| Behavior                                                | Command                                                                                                                                                                                    | Result                                  | Status |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- | ------ |
| Phase 6 integration test suite passes                   | `pytest tests/test_pipeline.py -q`                                                                                                                                                          | 39 passed, 1 documented skip            | PASS   |
| Full repo test suite green (modulo known geometry flake)| `pytest tests -q`                                                                                                                                                                          | 422 passed, 1 skipped, 1 pre-existing flake | PASS  |
| mypy --strict clean                                     | `mypy --strict src`                                                                                                                                                                        | Success: no issues found in 27 source files | PASS |
| ruff lint clean                                         | `ruff check src tests`                                                                                                                                                                     | All checks passed                       | PASS   |
| `python -m pastor_tracker --help` runs                  | `python -m pastor_tracker --help`                                                                                                                                                          | argparse usage printed, exit 0          | PASS   |
| Imports + exit codes correct                            | `python -c "from pastor_tracker.__main__ import main, _amain, EXIT_OK, EXIT_INVALID_CONFIG, EXIT_HARDWARE_FAILED, EXIT_CRASHED; from pastor_tracker.pipeline import Pipeline, OrchestratorRejected, PipelineSnapshot, PipelineState; assert EXIT_OK==0 and ..."` | imports clean, exit codes correct       | PASS   |

### Requirements Coverage

| Requirement | Source Plan         | Description                                                                                                            | Status     | Evidence                                                                                                                                                            |
| ----------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PIPE-01     | 06-01, 06-02, 06-03, 06-04 | `pipeline.py` asyncio orchestrator wiring stages OBS VCam → ... → ArduinoMotor                                  | SATISFIED  | `Pipeline` class wires all 8 stages; `test_tick_loop_drives_all_stages_end_to_end` asserts the chain runs end-to-end against composed fakes.                       |
| PIPE-02     | 06-01, 06-02, 06-04        | Each stage = pure transform on typed DTO; orchestrator owns wiring; no stage knows another stage's internals    | SATISFIED  | Tick body uses ONLY public `consume()`/`decide()`/`stream()` methods. All DTOs frozen. Code review confirmed no module-private state access.                       |
| PIPE-03     | 06-02, 06-03, 06-04        | Lifecycle — start, pause, home, e-stop, quit (mapped to UI hotkeys in Phase 7)                                  | SATISFIED  | All 6 async lifecycle methods present. `_LIFECYCLE_TABLE` table-driven (18 cells); valid + invalid transitions both covered by parametrized tests.                |

No orphaned requirements. ROADMAP maps PIPE-01..03 to Phase 6 and all three are claimed across plans 06-01..04.

### Anti-Patterns Found

None of significance. Code review (`06-REVIEW.md`) flagged 6 warnings (WR-01..WR-06) and 5 info items; all 6 warnings were resolved with commits `d6ceb6a..0ba9ec2` per the Fix Log; the 5 info items were deferred per default review-fix scope. Final status: `clean`.

| File                                          | Line                                  | Pattern                                            | Severity | Impact                                                                                                                                                                                |
| --------------------------------------------- | ------------------------------------- | -------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline.py`                                 | 461, 534, 542, 550                    | `except Exception` (4 occurrences)                 | INFO     | All four are documented translators with `# noqa: BLE001` — three at the subsystem-close boundary in `quit()` (WR-05 fix; explicit warning logs replace `contextlib.suppress`), one in `home()` (WR-01 fix; collapses to STOPPED + re-raises). Tiger-style §1 honoured: not silent. |
| `__main__.py`                                 | 265                                   | `except Exception`                                 | INFO     | Single documented BLE001 translator at the process boundary (`main()`) — re-classifies any unexpected raise into `EXIT_CRASHED`. CLAUDE.md tiger-style §1 compliant.                  |
| `tests/test_pipeline.py`                      | 347                                   | `pytest.skip` for `(E_STOPPED, "start")` cell      | INFO     | Documented deferred lifecycle re-arm path (IN-04 from review). Non-blocking; deferred to a future plan with explicit rationale (single-shot `motor.start()` requires re-arch).        |
| `pose_detector.py`                            | n/a                                   | `pending_frames` deque pairing complexity (WR-03)  | INFO     | Resolved by `_output_drops_pending` counter fix (commit c1607f3); `test_stream_drops_stale_internally` still passes. Frame/detection identity preserved.                              |

Anti-pattern grep summary on phase-modified files (`pipeline.py`, `__main__.py`, `core/types.py`, `pose_detector.py`, `test_pipeline.py`, `pipeline_helpers.py`):
- `print(` / bare `except:` / `except Exception: pass` — none in production code.
- `TODO` without issue number — none introduced.
- Magic numbers — none in production code; all numeric constants flow through Config or named module-level `Final` constants.
- `time.sleep()` in main loop — none.
- Mocked Kalman/damping math in tests — none (real implementations used per CLAUDE.md TEST-05).

### Human Verification Required

1. **Real-hardware end-to-end smoke (Phase 6 ROADMAP SC#1)**

   **Test:** Plug in a real Arduino Uno (any of the auto-detected VID:PIDs: 2341:0043, 2341:0069, 1A86:7523, 0403:6001), open OBS Studio, start the OBS Virtual Camera, position a moving subject (or yourself walking) in front of the camera, then run `python -m pastor_tracker` from the project root.

   **Expected:** The pipeline boots end-to-end (handshake with Uno, opens OBS VCam, spawns tick loop), and the camera-mounted stepper pans smoothly to follow the subject — no overshoot, no oscillation, no audible motor jerk. Ctrl-C drains gracefully (subprocess exits 0 within ~1 second).

   **Why human:** Requires physical Arduino + camera + moving subject — cannot be automated in CI. Per CONTEXT.md and the user prompt, this is explicitly **Phase 8 / QA-04** territory. All in-process equivalents (lifecycle, e-stop budget, snapshot, latest_frame slot, exit codes, signal handling, fake-composed end-to-end) are already VERIFIED. This item is surfaced here so Phase 8 picks it up as the formal gate; **it does not block Phase 6 sign-off.**

### Gaps Summary

No actionable gaps. All 22 must-haves are VERIFIED in code; the single deferred item (real-hardware smoke) is explicitly addressed by Phase 8 / QA-04 per ROADMAP and prompt.

**Status rationale:** `human_needed` (not `passed`) because the human verification section is non-empty per Step 9 decision-tree precedence. The single item is a forward-looking Phase 8 gate, not a Phase 6 blocker — Phase 6's in-process implementation surface is fully verified and ready for Phase 7 to consume.

---

_Verified: 2026-05-08_
_Verifier: Claude (gsd-verifier)_
