---
plan: 06-02
phase: 06-pipeline-orchestrator
status: complete
wave: 1
completed: 2026-05-10
requirements: [PIPE-01, PIPE-02, PIPE-03]
---

# Plan 06-02 — Pipeline Class

## Outcome

`pastor_tracker/src/pastor_tracker/pipeline.py` extended from the 06-01 skeleton to a 496-line production `Pipeline` class wiring all 8 stages (`OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor`) into a single asyncio orchestrator with a 6-state lifecycle, 18-cell transition table, frame-driven tick loop, and snapshot/latest_frame surface for Phase 7.

## Decisions Honored

| Decision | Implementation |
|----------|----------------|
| D-01..D-04 | Frame-driven `async for frame, dets in self._detector.stream(self._camera.frames())`; sequential await chain in single Task; `now_ns = frame.timestamp_ns` threaded through every consume/decide; no per-stage queues, no wall clock reads |
| D-05 | `start()` order: `motor.start()` → `motor.send_settings(...)` → `motor.send_limits(pan_min_deg, pan_max_deg)` → `camera.start()` → spawn tick task |
| D-06 | `pause()` flips `_dispatch_enabled = False`; tick continues; only `motor.send_motor_angle(cmd)` is gated; no firmware `S:` |
| D-07 | `e_stop()` calls `motor.send_emergency_stop()` inline (not via tick task); short-circuits even when tick is mid-`detector.stream()` await |
| D-08 | `home()` raises `OrchestratorRejected("home_rejected_zero_outside_limits")` when `0.0` outside `[pan_min_deg, pan_max_deg]`; otherwise `motor.send_home()` and transition through HOMING → PAUSED |
| D-09 | `quit()` cancel tick task → `camera.stop()` → `motor.close()` → state QUITTING; idempotent (second call no-op) |
| D-10..D-14 | Tiger-style fail-fast: tick body has no try/except; orchestrator-level done-callback catches `CameraError`, `LinkLostError`, perception exceptions, and unknown exceptions, logs structured `pipeline_*_failed` event, transitions STOPPED, propagates to caller. `WatchdogResetError` self-recovers in motor (Phase 2 `_recover()`); orchestrator does NOT re-issue settings |
| D-15 | `_LIFECYCLE_TABLE: dict[tuple[_PipelineState, _Cmd], _PipelineState]` exact-18 entries; `_transition_or_raise(cmd)` raises `OrchestratorRejected` on missing key |
| D-16 | `snapshot() -> PipelineSnapshot` reads `_PipelineCache` and returns frozen Pydantic DTO with 7 fields |
| D-17 | `latest_frame: Frame \| None` attribute updated atomically each tick (single writer = tick task; CPython STORE_ATTR atomic under GIL) |

## Key Files

- `pastor_tracker/src/pastor_tracker/pipeline.py` — Pipeline class (+ existing skeleton from 06-01) — 496 lines

## Verification Gates

| Gate | Result |
|------|--------|
| `mypy --strict src` | 27 files clean |
| `ruff check src tests` | All checks passed |
| `grep -c "send_settings\|send_limits" pipeline.py` | 2 (both in start()) |
| `grep -cE "time\.perf_counter\|time\.monotonic\|loop\.time\(\)" pipeline.py` | 0 (D-04 enforced) |
| `grep -c "now_ns = frame.timestamp_ns" pipeline.py` | 1 |
| Pipeline + OrchestratorRejected import test | OK |

## Self-Check: PASSED

## Notes

The original executor agent for this plan ran in a worktree mistakenly based off `branch-tracking` (incompatible architecture — no `pastor_tracker/` package). Agent recovered by writing files via absolute paths into the parent repo's `fresh-2026` working tree (uncommitted on agent return). Orchestrator subsequently:
1. Discarded the leaked partial `__main__.py` mod (06-03 had its own canonical version in correctly-based worktree).
2. Stashed the leaked `pipeline.py` work, merged 06-03's worktree first, restored the stash.
3. Cleaned 2 unused `# type: ignore[attr-defined]` comments in `__main__.py` post-merge (Pipeline now resolvable).
4. Re-ran all gates GREEN, commits this plan directly on `fresh-2026`.

The Pipeline implementation itself is unchanged from the agent's verified output.
