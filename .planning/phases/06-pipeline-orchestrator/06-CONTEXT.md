# Phase 6: Pipeline Orchestrator - Context

**Gathered:** 2026-05-10
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

Wire `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor` into a single asyncio pipeline with a clean lifecycle — every stage stays a pure transform on a typed DTO; no stage knows another's internals.

**In scope:**
- `pastor_tracker/pipeline.py` — `Pipeline` class: composes existing stages, owns the tick loop, owns lifecycle state (`STOPPED / RUNNING / PAUSED / HOMING / E_STOPPED / QUITTING`), exposes `start() / pause() / resume() / home() / e_stop() / quit()` async methods, exposes read-only `snapshot() -> PipelineSnapshot` and `latest_frame -> Frame | None`
- `pastor_tracker/__main__.py` — replace Phase-1 boot stub with: load Config → instantiate `ArduinoMotor` + `ObsCamera` + `PoseDetector` + `SubjectTracker` + `MotionAnalyzer` + `Framer` + `PanController` + `CommandDispatcher` → instantiate `Pipeline(...)` → run `pipeline.start()` → wait on Ctrl-C / SIGINT → `pipeline.quit()`
- `pastor_tracker/core/types.py` — add `PipelineSnapshot` frozen DTO and `PipelineState` Literal alias
- `pastor_tracker/pipeline.py` — `OrchestratorRejected(Exception)` for invalid lifecycle transitions and out-of-limits `home()` calls
- `tests/test_pipeline.py` — wires fakes (`FakeVideoSource` from Phase 3, `FakePoseEngine` from Phase 4, `FakeSerialTransport` from Phase 2) into `Pipeline`, drives a scripted trajectory, asserts: tick loop progresses, dispatched `MotorCommand` count matches expectation, lifecycle transitions, e-stop halts within one heartbeat interval, home rejection when 0° outside limits, snapshot DTO populated
- README pointer in Phase 8 — operator runs `python -m pastor_tracker`

**Out of scope (later phases):**
- DearPyGui dashboard / hotkeys / live preview rendering (Phase 7) — Phase 6 ships the headless API surface only; Phase 7 wires UI to it
- On-stage smoke test against real Uno + real OBS + real speaker (Phase 8 / QA-04)
- Telemetry / recording / replay (deferred v2)
- PID, EMA, MediaPipe — explicitly forbidden per CLAUDE.md / PROJECT.md

**Requirements covered:** PIPE-01, PIPE-02, PIPE-03.

</domain>

<decisions>
## Implementation Decisions

### Locked Decisions (D-01..D-18) — Stable IDs for Plan Citation

| ID | Source Area | Decision (1-line reference) |
|----|-------------|------------------------------|
| D-01 | Area 1 — Tick Loop | Frame-driven loop: `async for frame in detector.stream(camera.frames())` clocks every downstream stage; rate = camera fps. |
| D-02 | Area 1 — Tick Loop | Stage composition is a single sequential await chain inside one task: `tracker.consume → analyzer.consume → framer.consume → controller.consume → dispatcher.decide` — all pure, sub-ms; no per-stage queues. |
| D-03 | Area 1 — Tick Loop | Detector backpressure handled by `PoseDetector.stream(frames)` (Phase 4 BL-01) — detector internally drops stale frames; orchestrator does not skip ticks itself. |
| D-04 | Area 1 — Tick Loop | `now_ns` per tick = `frame.timestamp_ns` (camera's `perf_counter_ns()`); passed to every `consume(...)` and `decide(...)` call. Orchestrator never reads its own wall clock for stage time. |
| D-05 | Area 2 — Lifecycle | `start()` opens camera + arduino, sends `send_settings(...)` and `send_limits(pan_min_deg, pan_max_deg)`, then enters RUNNING tick loop in a background `asyncio.Task`. |
| D-06 | Area 2 — Lifecycle | `pause()` flips internal `_dispatch_enabled = False`; tick loop continues so preview stays live; dispatcher decisions are computed but **not** sent to motor. No firmware `S:` issued. |
| D-07 | Area 2 — Lifecycle | `e_stop()` calls `motor.send_emergency_stop()` immediately, transitions to E_STOPPED, halts dispatch. Recovery requires explicit `start()`. Must complete within one heartbeat interval (200 ms). |
| D-08 | Area 2 — Lifecycle | `home()` is rejected (raise `OrchestratorRejected`) when `0.0` is outside `[config.pan_min_deg, config.pan_max_deg]`. Otherwise: `motor.send_home()`, transition RUNNING/PAUSED → HOMING → PAUSED. |
| D-09 | Area 2 — Lifecycle | `quit()` is a graceful drain: cancel tick task → `camera.stop()` → `motor.close()` → set state QUITTING then return. Idempotent (second call is a no-op). |
| D-10 | Area 3 — Failure | Camera errors (`CameraOpenError`, `CameraStallError`) propagate: log structured `pipeline_camera_failed`, transition STOPPED, re-raise to caller (`__main__`). No auto-retry. |
| D-11 | Area 3 — Failure | Arduino `WatchdogResetError` is recovered in-place by `ArduinoMotor._recover()` already (Phase 2); orchestrator continues RUNNING after motor recovers and re-issues settings+limits via the motor's own recovery path. |
| D-12 | Area 3 — Failure | Arduino `LinkLostError` propagates: log `pipeline_arduino_link_lost`, transition STOPPED, re-raise to caller. |
| D-13 | Area 3 — Failure | Detector / pose-stage exceptions are fail-fast: log `pipeline_perception_failed`, transition STOPPED, re-raise. |
| D-14 | Area 3 — Failure | Unhandled exception in tick loop is caught at the orchestrator boundary: log `pipeline_crashed` with exception info, transition STOPPED, re-raise to `__main__`. |
| D-15 | Area 4 — Surface | Public lifecycle API on `Pipeline` is async: `start() / pause() / resume() / home() / e_stop() / quit()`. Each is idempotent on its target state and raises `OrchestratorRejected` on invalid source-state transitions. |
| D-16 | Area 4 — Surface | `Pipeline.snapshot() -> PipelineSnapshot` returns a frozen Pydantic DTO with: `state: PipelineState`, `last_frame_ts_ns: int \| None`, `last_intent: MotionIntent`, `last_target_x_normalized: float \| None`, `last_pan_angle_deg: float \| None`, `last_emitted_angle_deg: float \| None`, `motor_state: str`. |
| D-17 | Area 4 — Surface | `Pipeline.latest_frame: Frame \| None` — last-frame slot updated atomically each tick (single writer = tick task; readers = UI). UI polls at its own refresh rate. No broadcast queue. |
| D-18 | Area 4 — Surface | Config reload is full-restart: Phase 7 calls `pipeline.quit()` then re-instantiates `Pipeline(new_config, ...)` and calls `start()` again. No hot-reload of frozen Config fields. |

These IDs are the canonical reference downstream PLAN.md files MUST cite when invoking a locked decision.

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts
- Forbidden: PID, EMA on detection stream, mocked Kalman / damping math in tests, `print()`, bare `except:`, magic numbers (Config is authoritative), nested conditionals > 2 levels
- Pure-core / dirty-edges layering — `pipeline.py` is the **only** module that touches both `io/` and `intent/`+`control/`; stages stay pure
- DTO-only stage I/O — every stage receives and emits typed frozen DTOs; no shared mutable state
- AVR `WDTO_500MS` hardware watchdog + 1000 ms PC-heartbeat firmware watchdog; PC sends 200 ms heartbeat — already in `ArduinoMotor` (Phase 2). E-stop budget = 200 ms ⇒ Pipeline must `await motor.send_emergency_stop()` synchronously in `e_stop()`, no buffering
- Re-receipt of `READY:v2` mid-session = MCU reset → `_recover()` re-issues settings + limits (already handled by `ArduinoMotor`); Pipeline must re-issue via the motor's recovery path (D-11)
- VID:PID auto-detect over fixed COM port — already in `ArduinoMotor` constructor (`SerialTransport` discovery); Pipeline does not duplicate
- Pydantic v2 frozen DTOs; mutate via `.model_copy(update=...)`
- Tiger-style fail-fast on contract violation; `mypy --strict` no `Any`; `structlog` JSON logging only
- Conventional Commits, one logical change per commit

### Tick Loop & Stage Composition (Area 1, all accepted) — D-01..D-04
- Frame-driven loop: `async for frame in detector.stream(camera.frames()): ...` — detector consumes camera frames internally, drops stale ones (Phase 4 BL-01), yields `Detection` items; orchestrator advances stages on each yielded detection.
- Sequential await chain inside the tick body — no per-stage `asyncio.Queue`, no per-stage Task. All Phase-5 stages are sub-millisecond pure transforms; sequential composition keeps reasoning trivial and avoids inter-stage backpressure complexity.
- `now_ns = frame.timestamp_ns` is the canonical tick clock; threaded through `tracker.consume(detection, now_ns) → analyzer.consume(subject, now_ns) → framer.consume(motion, now_ns) → controller.consume(target, now_ns) → dispatcher.decide(angle, now_ns)`. Deterministic in tests.
- When upstream stage emits `None`, downstream stages still get called with `None` and `now_ns` — each stage's hold-on-None semantics (Phase 4/5 D-04/D-07) handles the transient.

### Lifecycle State Machine (Area 2, all accepted) — D-05..D-09
- States: `STOPPED` (initial / terminal), `RUNNING` (tick loop active, dispatch enabled), `PAUSED` (tick loop active, dispatch disabled), `HOMING` (transient, motor moving to 0°), `E_STOPPED` (motor halted, requires `start()` to recover), `QUITTING` (terminal, resources released).
- Transitions: `STOPPED →start→ RUNNING ⇄ PAUSED`; `RUNNING/PAUSED →home→ HOMING →done→ PAUSED`; any `→e_stop→ E_STOPPED →start→ RUNNING`; any `→quit→ QUITTING` (terminal).
- `OrchestratorRejected` raised on invalid source state (e.g. `pause()` from STOPPED) and on out-of-limits `home()`.
- `start()` performs `motor.start()` (Phase 2 handshake) BEFORE `camera.start()` — fail-fast on hardware before opening DirectShow; on success calls `motor.send_settings(...)` then `motor.send_limits(pan_min_deg, pan_max_deg)`, then spawns the tick task.
- Dispatch enable/disable is a single `_dispatch_enabled: bool` flag checked **after** `dispatcher.decide(...)` — the dispatcher's gate state still progresses (so resume is seamless), only the `motor.send_motor_angle(cmd)` call is gated.
- `e_stop()` runs the motor call inline (not via the tick task) so even if the tick is mid-detector-await, the e-stop reaches firmware within 200 ms heartbeat budget.
- `quit()` is the only path that closes hardware; `STOPPED` does not close serial/camera (allows `start()` to be re-called cheaply for Phase 7 reload pattern when not desired). Re-evaluate if Phase 7 wants stop-also-closes — current default keeps resources hot for retry.

### Failure & Recovery (Area 3, all accepted) — D-10..D-14
- Tiger-style fail-fast everywhere except the Arduino watchdog reset path (already self-healing in Phase 2 `ArduinoMotor._recover()`).
- All failures log a structured event (`pipeline_camera_failed`, `pipeline_arduino_link_lost`, `pipeline_perception_failed`, `pipeline_crashed`) with `exc_info=True` then transition STOPPED and re-raise to the caller.
- `__main__` catches the re-raised exception, exits non-zero with a final `pipeline_exit` event — operator sees the failure in logs and decides whether to restart.
- No silent retry, no per-frame skip-on-exception, no swallowed exceptions.

### Phase 7 Public Surface (Area 4, all accepted) — D-15..D-18
- All lifecycle methods are async coroutines — Phase 7 hotkey handlers `await pipeline.start()` etc.
- `OrchestratorRejected` is the single public exception raised by lifecycle methods on invalid transitions and out-of-limits home; Phase 7 catches it and surfaces a status-bar message.
- `snapshot()` is the **only** read-path the dashboard uses for the status panel — it does NOT touch private stage attributes. Snapshot is cheap (frozen DTO from cached fields). UI calls it on its render tick (e.g. 30 Hz).
- `latest_frame` slot is `Frame | None`; the tick task overwrites it after each successful detector iteration. UI reads it without locking — `Frame` is frozen and the slot is a single attribute write (atomic on CPython). Live-preview overlay drawing happens entirely in Phase 7 (Phase 6 ships the slot only).
- Config reload pattern (Phase 7 "Save Config") is documented in Phase 6 README pointer / Phase 7 spec: `await pipeline.quit()` then construct a fresh `Pipeline` from the new Config and `await pipeline.start()`. No hot-reload of frozen Config fields.

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal helper / private method names beyond the public surface
- Whether `_PipelineState` is `enum.Enum` or `Literal` (recommend `enum.Enum` for transition-table clarity)
- File granularity inside `pipeline.py` — single file is the default; small private helper module (e.g. `_lifecycle.py` for the state-table) permitted only if `pipeline.py` exceeds ~400 lines
- Logging key names — keep `event="..."` shape consistent with Phases 2–5 conventions
- Internal docstring depth — only where the WHY is non-obvious (CLAUDE.md tone rule)
- Test fixture composition — reuse Phase 2 `FakeSerialTransport`, Phase 3 `FakeVideoSource`, Phase 4 `FakePoseEngine`; build a single `_pipeline_with_fakes(...)` helper

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/io/obs_camera.py` — `ObsCamera.start()`, `ObsCamera.stop()`, `ObsCamera.frames() -> AsyncIterator[Frame]`. `frames()` exits cleanly via `StopAsyncIteration` after `stop()` (Phase 3 B-02 fix). `start()` translates ALL DirectShow / DLL load failures to typed `CameraOpenError` (Phase 3 B-04 fix).
- `pastor_tracker/io/arduino_motor.py` — `ArduinoMotor.start()` (handshake), `close()`, `send_settings(...)`, `send_limits(min, max)`, `send_motor_angle(MotorCommand)`, `send_emergency_stop()`, `send_home()`, `send_reset()`, `events() -> AsyncIterator[ProtocolEvent]`. `_recover()` already re-issues settings on watchdog reset; `state` property exposes `_MotorState` enum.
- `pastor_tracker/perception/pose_detector.py` — `PoseDetector.stream(frames: AsyncIterator[Frame]) -> AsyncIterator[Detection]` (Phase 4 BL-01) — drops stale frames internally.
- `pastor_tracker/perception/subject_tracker.py` — `SubjectTracker.consume(detection, now_ns) -> TrackedSubject | None` (BL-01 shape).
- `pastor_tracker/intent/motion_analyzer.py` — `MotionAnalyzer.consume(subject, now_ns) -> MotionState | None` (Phase 5 D-01).
- `pastor_tracker/intent/framer.py` — `Framer.consume(motion, now_ns) -> FramingTarget | None` (Phase 5 D-01).
- `pastor_tracker/control/pan_controller.py` — `PanController.consume(target, now_ns) -> float | None` (Phase 5 D-01).
- `pastor_tracker/control/command_dispatcher.py` — `CommandDispatcher.decide(angle_deg, now_ns) -> MotorCommand | None` (Phase 5 D-01, sync).
- `pastor_tracker/core/types.py` — `Frame`, `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand`, `MotionIntent`. Phase 6 ADDS `PipelineSnapshot` and `PipelineState` aliases here.
- `pastor_tracker/config.py` — frozen `Config`; Phase 6 needs `pan_min_deg`, `pan_max_deg`, `arduino_*`, `obs_*` knobs (all already present).
- `pastor_tracker/logging_config.py` — `configure_logging()`; bind `module="pipeline"`.
- Test fakes: `pastor_tracker/tests/fixtures/fake_serial_transport.py` (Phase 2), `pastor_tracker/tests/fixtures/fake_video_source.py` (Phase 3), `pastor_tracker/tests/fixtures/fake_pose_engine.py` (Phase 4), `pastor_tracker/tests/fixtures/trajectories.py` (Phase 5). Phase 6 composes all four into `_pipeline_with_fakes(...)`.

### Established Patterns (Phases 1–5)
- Pure-core / dirty-edges — Phase 6 is the **only** module that legally crosses both sides
- `consume(upstream, now_ns)` / `decide(angle_deg, now_ns)` — uniform per-frame stage shape from Perception onward
- Pydantic `frozen=True, extra='forbid'` for DTOs; `enum.Enum` for transport / lifecycle state machines (Phase 2 `_MotorState` precedent)
- Lint policy: `ruff` rules T20 / BLE001 / E722 / RUF; `mypy --strict` no `Any`
- Test fakes live under `tests/fixtures/`; integration tests compose fakes
- Coverage policy: ≥ 90% line on orchestrator file; lifecycle transition table at 100% line+branch

### Integration Points
- Upstream of pipeline: `Config` (loaded by `__main__`); `ObsCamera`, `ArduinoMotor`, `PoseDetector`, `SubjectTracker`, `MotionAnalyzer`, `Framer`, `PanController`, `CommandDispatcher` constructed by `__main__`
- Downstream consumer: Phase 7 `dashboard.py` instantiates `Pipeline(...)` and calls its async lifecycle methods + `snapshot()` + `latest_frame`
- `__main__` orchestrates: `asyncio.run(_amain(config))` where `_amain` constructs everything, calls `pipeline.start()`, awaits SIGINT, calls `pipeline.quit()`

</code_context>

<specifics>
## Specific Ideas

- Tick loop body (concrete shape):

  ```python
  async for detection in self._detector.stream(self._camera.frames()):
      now_ns = detection.frame.timestamp_ns
      self._latest_frame = detection.frame
      subject = await self._tracker.consume(detection, now_ns)
      motion  = await self._analyzer.consume(subject, now_ns)
      target  = await self._framer.consume(motion, now_ns)
      angle   = await self._controller.consume(target, now_ns)
      command = self._dispatcher.decide(angle, now_ns)
      self._update_snapshot_cache(motion, target, angle, command)
      if command is not None and self._dispatch_enabled:
          await self._motor.send_motor_angle(command)
  ```

  The `if command is not None and self._dispatch_enabled` guard is the **only** dispatch gate. `pause()` flips `_dispatch_enabled = False`; tick loop keeps going so preview stays live.

- `_PipelineState` is `enum.Enum` with members `{STOPPED, RUNNING, PAUSED, HOMING, E_STOPPED, QUITTING}`. Transition table is a small `dict[(state, command), state]` checked before each lifecycle method body — invalid combination raises `OrchestratorRejected("cannot {command} from {state}")`.

- E-stop test: assert `pipeline.e_stop()` returns within 200 ms wall time even when tick task is mid-`detector.stream(...)` — uses a `FakePoseEngine` that sleeps 500 ms per detection to simulate slow detector; e-stop must short-circuit the dispatch path independent of the tick loop.

- Home rejection test: parametrize over Config with `pan_min_deg=-30, pan_max_deg=+30` (0° in range — accept) and `pan_min_deg=+10, pan_max_deg=+50` (0° out of range — reject); assert `OrchestratorRejected` raised in the latter, `motor.send_home` called once in the former.

- Snapshot test: drive a scripted trajectory through the fakes, assert `snapshot.last_intent` cycles `indeterminate → moving_right → dwelling`, `snapshot.last_target_x_normalized` matches Phase 5 framer output, `snapshot.last_emitted_angle_deg` matches dispatcher emission count.

- `__main__` test: assert SIGINT triggers `pipeline.quit()` and process exits 0; assert mid-run `CameraOpenError` exits non-zero with `pipeline_exit` log event.

- Logging shapes: `event="pipeline_state_change" old=... new=... reason=...` (every transition); `event="pipeline_dispatch_disabled" reason="paused"` / `reason="e_stop"`; `event="pipeline_home_rejected" reason="0_outside_limits" pan_min_deg=... pan_max_deg=...`; `event="pipeline_tick_started" frame_ts_ns=...` (DEBUG only — would spam); `event="pipeline_quit" duration_sec=...`.

- All numeric thresholds (heartbeat budget, pan limits, etc.) flow through Config — no hardcoded literals in `pipeline.py` or test bodies.

</specifics>

<deferred>
## Deferred Ideas

- Hot-reload of Config subset (gain knobs without full restart) — v2; Phase 7 uses full restart per D-18
- Telemetry export of every `MotorCommand` to disk for post-mortem replay — v2
- Multi-camera failover (USB jack hotplug) — v2; Phase 6 fails-fast on camera disconnect per D-10
- Auto-restart on `pipeline_crashed` with exponential backoff — explicitly rejected (tiger-style fail-fast); Phase 8 docs cover manual restart
- Pause-firmware via `S:` protocol message — rejected (D-06 keeps preview live; firmware pause would cut feedback)
- Per-stage timing histogram in snapshot — v2 telemetry
- Pipeline subprocess isolation (run detector in separate process) — out of scope; current single-asyncio-task is sufficient at 30 fps
- Hot-swap of stage implementations at runtime — rejected; restart is the supported reconfigure path

</deferred>
