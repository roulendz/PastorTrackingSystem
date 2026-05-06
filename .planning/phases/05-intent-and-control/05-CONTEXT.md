# Phase 5: Intent and Control - Context

**Gathered:** 2026-05-06
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

Convert the Kalman trajectory into a rule-of-thirds framing target with hysteresis, then drive a two-stage critically-damped pan that produces no jerk, no overshoot, no oscillation — and emit motor commands only when they actually change the picture.

**In scope:**
- `pastor_tracker/intent/motion_analyzer.py` — sustained-velocity + dwell hysteresis classifier; consumes `TrackedSubject | None`, emits `MotionState | None` with `MotionIntent ∈ {moving_left, moving_right, dwelling, indeterminate}`
- `pastor_tracker/intent/framer.py` — rule-of-thirds intent → `target_x_normalized` (0.333 / 0.500 / 0.667), smoothed by stage-1 `CriticallyDampedFollower(framing_time_constant_sec)` in normalized-x domain; emits `FramingTarget | None`
- `pastor_tracker/control/pan_controller.py` — converts `target_x_normalized → angle_deg` via `core.geometry.normalized_x_to_angle_deg(...)`, smoothed by stage-2 `CriticallyDampedFollower(pan_time_constant_sec)` in degree domain, then velocity-clamped (`pan_max_velocity_deg_per_sec` × dt) and deadband-suppressed (`pan_deadband_deg`); emits `float | None`
- `pastor_tracker/control/command_dispatcher.py` — pure transform `decide(angle_deg | None, now_ns) -> MotorCommand | None`; gates on `command_min_delta_deg` + `command_min_interval_ms`
- All four stages expose the **`async def consume(upstream, now_ns) -> downstream | None`** surface (mirrors Phase 4 BL-01) — orchestrator owns the outer loop, no internal `AsyncIterator`
- Public read-only state for Phase 7 dashboard: `MotionAnalyzer.current_intent`, `Framer.current_target_x_normalized`, `PanController.current_angle_deg`, `CommandDispatcher.last_emitted_angle_deg`, `CommandDispatcher.last_emit_ts_ns`
- Test fixtures `tests/fixtures/trajectories.py` — pure helpers (`step`, `ramp`, `dwell_then_walk`, `borderline_chatter`) producing `list[TrackedSubject]` at configurable dt, fed into analyzer/framer/controller/dispatcher unit tests with real damping math

**Out of scope (later phases):**
- Pipeline orchestrator wiring (Phase 6) — Phase 5 ships pure stages; Phase 6 wires them
- DearPyGui dashboard (Phase 7)
- On-stage smoke against real Uno + real OBS + real speaker (Phase 8 / QA-04)
- Tilt axis / vertical motion (v2)
- PID, EMA, MediaPipe — explicitly forbidden per CLAUDE.md / PROJECT.md

**Requirements covered:** INTENT-01..04, CTRL-01..04, TEST-03.

</domain>

<decisions>
## Implementation Decisions

### Locked Decisions (D-01..D-13) — Stable IDs for Plan Citation

| ID | Source Area | Decision (1-line reference) |
|----|-------------|------------------------------|
| D-01 | Area 1 — Stage Interface | All three async stages expose `async def consume(upstream, now_ns) -> downstream \| None`; CommandDispatcher is sync `def decide(angle_deg \| None, now_ns) -> MotorCommand \| None`. |
| D-02 | Area 2 — Hysteresis | Time source = `TrackedSubject.timestamp_ns` (frame-derived); analyzer NEVER reads a wall clock. |
| D-03 | Area 2 — Hysteresis | Sustained-velocity tracking via per-direction `_first_crossing_ts_ns`; flip on continuous duration ≥ `motion_hysteresis_sec`; reset crossing timer on un-cross. Dwell tracked via `_dwell_start_ts_ns` + `dwell_duration_sec`. |
| D-04 | Area 2 — Hysteresis | None-upstream → reset all timers, emit `MotionState(intent="indeterminate", sustained_velocity_x_norm_per_sec=0.0, timestamp_ns=now_ns)`. |
| D-05 | Area 3 — Damping | Stage-1 damper held by Framer (normalized-x domain); stage-2 damper held by PanController (degree domain). |
| D-06 | Area 3 — Damping | First non-None upstream seeds `FollowerState(position=current_target, velocity=0.0)` — no warmup transient. |
| D-07 | Area 3 — Damping | Hold-on-None / hold-on-indeterminate: damper holds current FollowerState; **no advance, no reset**. |
| D-08 | Area 3 — Damping | PanController order: `target_x_normalized → angle_deg via normalized_x_to_angle_deg(target_x, fov_deg)` FIRST, then damp in angle domain, then velocity-clamp, then deadband. |
| D-09 | Area 4 — Pan Limits | Velocity clamp: after `damper.step()`, compute `delta = new_pos - prev_pos`, clamp to `±pan_max_velocity_deg_per_sec * dt`, **overwrite** the damper's `FollowerState.position` with the clamped value (anti-windup; not PID). |
| D-10 | Area 4 — Pan Limits | Deadband applied inside `PanController.consume()` against `_last_emitted_angle_deg`; if `\|new - last_emitted\| < pan_deadband_deg` → return last_emitted. Damper continues stepping under the hood. |
| D-11 | Area 4 — Dispatcher | Dispatcher state = `_last_emitted_angle_deg`, `_last_emit_ts_ns`; emit when first call OR (Δ > `command_min_delta_deg` AND interval ≥ `command_min_interval_ms`); on emit, update both fields. None-upstream returns None and does **not** update state. |
| D-12 | Area 4 — Tests | Test fixtures in `pastor_tracker/tests/fixtures/trajectories.py` — `step` / `ramp` / `dwell_then_walk` / `borderline_chatter` returning `list[TrackedSubject]`. All numeric thresholds flow through `Config` — no hardcoded literals in test bodies. |
| D-13 | Area 4 — Coverage | Coverage targets: 100% line+branch on hysteresis classifier + deadband+clamp branches + dispatcher gate; ≥ 90% line on `framer.py` and `pan_controller.py`. |

These IDs are the canonical reference downstream PLAN.md files MUST cite when invoking a locked decision.

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts
- Forbidden: PID, EMA on detection stream, mocked Kalman / damping math in tests, `print()`, bare `except`, magic numbers (Config is authoritative), nested conditionals > 2 levels
- Stage-1 damping `framing_time_constant_sec ≈ 0.8 s`, stage-2 damping `pan_time_constant_sec ≈ 0.6 s` (Config)
- Motion thresholds: sustained `|vx| > 0.08` for ≥ 0.3 s = moving; `|vx| < 0.03` for ≥ 1.5 s = dwell (Config: `motion_threshold_norm_per_sec`, `motion_hysteresis_sec`, `dwell_threshold_norm_per_sec`, `dwell_duration_sec`)
- Rule-of-thirds: moving-right → left third 0.333; moving-left → right third 0.667; dwelling → center 0.500
- Pan deadband 0.4°, pan velocity clamp 30°/s (Config: `pan_deadband_deg`, `pan_max_velocity_deg_per_sec`)
- Dispatcher gate: emit when `Δ > 0.2°` AND `≥ 50 ms` since last emit (Config: `command_min_delta_deg`, `command_min_interval_ms`)
- Pure-core / dirty-edges layering — `intent/` and `control/` are pure transforms on typed DTOs; only the orchestrator (Phase 6) talks to `ArduinoMotor`
- DTO contracts already frozen in `core/types.py`: `TrackedSubject`, `MotionState` (with `intent: MotionIntent`, `sustained_velocity_x_norm_per_sec`, `timestamp_ns`), `FramingTarget` (with `target_x_normalized: float ∈ [0,1]`, `timestamp_ns`), `MotorCommand` (with `target_angle_deg`, `timestamp_ns`)
- `CriticallyDampedFollower` reuse — `core/damping.py` Holden-exact closed form is the ONLY damper; no PID, no EMA, no per-stage variant
- FOV math reuse — `core/geometry.normalized_x_to_angle_deg(...)` is the ONLY normalized↔angle bridge
- Pydantic v2 frozen DTOs, dataclass `frozen=True, slots=True`; mutate via `.model_copy(update=...)` / `dataclasses.replace(...)`
- Tiger-style fail-fast on contract violation; `mypy --strict` no `Any`; `structlog` JSON logging only
- Conventional Commits, one logical change per commit

### Stage Interface Shape (Area 1, all accepted)
- `MotionAnalyzer.consume(subject: TrackedSubject | None, now_ns: int) -> MotionState | None` — async, mirrors Phase 4 `SubjectTracker.consume()` (BL-01 simplification)
- `Framer.consume(motion: MotionState | None, now_ns: int) -> FramingTarget | None` — async, same shape
- `PanController.consume(target: FramingTarget | None, now_ns: int) -> float | None` — async, returns damped absolute pan angle in degrees (None when no target / no upstream)
- `CommandDispatcher` is a **pure transform**: `def decide(angle_deg: float | None, now_ns: int) -> MotorCommand | None`; orchestrator (Phase 6) is the one that calls `motor.send_motor_angle(...)` on the emitted command
- Rationale — keeps every stage unit-testable without an event loop or transport, matches Phase 4 BL-01 lesson that iterator surfaces deadlock on empty queues

### Hysteresis State Machine (Area 2, all accepted)
- Time source = `TrackedSubject.timestamp_ns` (frame-derived, `perf_counter_ns()` upstream); when upstream is None, the analyzer's caller passes the current `now_ns` of the tick — analyzer never reads a wall clock itself (deterministic in tests)
- Sustained-velocity tracking — track per-direction `_first_crossing_ts_ns`; flip `intent` to `moving_right` when `vx > +motion_threshold_norm_per_sec` continuously and `now_ns - first_crossing >= motion_hysteresis_sec * 1e9`; flip to `moving_left` symmetrically; reset the corresponding crossing timer the moment the threshold is uncrossed (no integration / no leaky-bucket — pure crossing predicate per CLAUDE.md "no spaghetti")
- Dwell — track `_dwell_start_ts_ns` while `|vx| < dwell_threshold_norm_per_sec`; flip to `dwelling` once continuous duration ≥ `dwell_duration_sec`
- None-upstream semantics — when `consume(None, now_ns)` is called (UNLOCKED / SEEKING / LOST upstream), the analyzer **resets all hysteresis timers** and emits `MotionState(intent="indeterminate", sustained_velocity_x_norm_per_sec=0.0, timestamp_ns=now_ns)` so the framer can choose the centering policy explicitly; HOLDING upstream emits a real `TrackedSubject` (frozen posterior) and is processed normally
- Initial intent before any sustained crossing = `"indeterminate"` (already a member of `MotionIntent` Literal in `core/types.py`)

### Two-Stage Damping (Area 3, all accepted)
- Stage-1 damper held by `Framer` — `CriticallyDampedFollower(time_constant_sec=config.framing_time_constant_sec)`, stepped in **normalized-x domain** with `FollowerState(position=target_x_normalized)`
- Stage-2 damper held by `PanController` — `CriticallyDampedFollower(time_constant_sec=config.pan_time_constant_sec)`, stepped in **degree domain** with `FollowerState(position=angle_deg)`
- Initial state seeded on first non-None upstream — `FollowerState(position=current_target, velocity=0.0)`; **no warmup transient toward zero**
- Hold-on-None semantics — when upstream is None or `MotionIntent="indeterminate"`, the damper holds its current `FollowerState` and emits the held position; **no advance, no reset**. On re-acquire, the seeded state from the new first-non-None upstream replaces the held state (avoids snapping from stale/very old position)
- Domain conversion order in `PanController` — `target_x_normalized → angle_deg via normalized_x_to_angle_deg(target_x, config.camera_horizontal_fov_deg)` **first**, then damp in angle-domain, then velocity-clamp, then deadband suppression. Damping in motor-space keeps the motion mathematically linear and the velocity clamp acts on the same units the motor consumes

### Pan Limits, Dispatcher, Tests (Area 4, all accepted)
- Velocity clamp implementation — after the damper step, compute `delta_deg = new_pos - prev_pos`; clamp to `±pan_max_velocity_deg_per_sec * dt_sec`; **overwrite the damper's `FollowerState.position` with the clamped value** so future steps integrate from the clamped position (avoids damper windup against a target the motor cannot reach in time)
- Deadband application — applied **inside `PanController.consume()`** against the controller's own `_last_emitted_angle_deg`. If `|new_angle - last_emitted| < pan_deadband_deg`, emit the previous angle (stable; dispatcher sees no delta and naturally suppresses). Damper continues stepping under the hood — only the *emission* is suppressed
- Dispatcher state — `_last_emitted_angle_deg: float | None`, `_last_emit_ts_ns: int | None`; emit when `_last_emitted_angle_deg is None` (first call) OR (`|angle - last| > command_min_delta_deg` AND `now_ns - _last_emit_ts_ns >= command_min_interval_ms * 1_000_000`); on emit, update both fields
- Dispatcher None-upstream semantics — `decide(None, now_ns)` returns None, does **not** update state (so the Δ-and-interval gate stays meaningful across upstream gaps)
- Test fixture organization — new `tests/fixtures/trajectories.py` providing pure helpers `step(t_step_sec, dt_sec, total_sec, x_before, x_after)`, `ramp(start, end, dt_sec, total_sec)`, `dwell_then_walk(dwell_sec, dt_sec, ...)`, `borderline_chatter(dt_sec, total_sec, vx_amplitude)` — each returns `list[TrackedSubject]`. Drives every Phase 5 unit test
- Coverage target — **100% line + branch** on hysteresis classifier, deadband+clamp branches, dispatcher gate; **≥ 90% line** on framer + pan controller (matches Phase 2 pure-parser-layer + Phase 4 lock-acquisition rules)
- No `print()`, bare `except:`, mocked damping math, or hardcoded Config numbers in tests — all parameters flow through `Config` fixtures

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal helper / private method names beyond the public `consume()` / `decide()` surface
- Error class hierarchy under a single `IntentError` / `ControlError` root if needed (analogous to Phase 4 `PerceptionError`)
- Logging key names — keep consistent with `event="..."` shape established in Phases 2–4 structlog conventions
- Whether the hysteresis crossing tracker is a small dataclass or stateful method on `MotionAnalyzer`
- File granularity inside `intent/` and `control/` — `motion_analyzer.py + framer.py` and `pan_controller.py + command_dispatcher.py` is the default; small private helper modules (e.g. `_hysteresis.py`) permitted if a single file balloons
- Internal docstring depth and inline comments — only where the WHY is non-obvious (CLAUDE.md tone rule)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/core/damping.py` — `CriticallyDampedFollower(time_constant_sec)` + `FollowerState(position, velocity)`; `step(state, target, dt) -> FollowerState`. Holden-exact closed form, unconditionally stable, zero overshoot. Phase 5 instantiates one per stage
- `pastor_tracker/core/geometry.py` — `normalized_x_to_angle_deg(normalized_x, horizontal_fov_deg)` for stage-2 input conversion (degree-domain damping). Already property-tested in Phase 1
- `pastor_tracker/core/types.py` — `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand` already defined and frozen; `MotionIntent` Literal already includes `"indeterminate"` sentinel for the no-decision-yet state (line 32). Phase 5 produces these directly — no DTO additions expected
- `pastor_tracker/config.py` — frozen `Config` exposes every Phase 5 knob: `motion_threshold_norm_per_sec`, `motion_hysteresis_sec`, `dwell_threshold_norm_per_sec`, `dwell_duration_sec`, `framing_time_constant_sec`, `pan_time_constant_sec`, `pan_deadband_deg`, `pan_max_velocity_deg_per_sec`, `command_min_delta_deg`, `command_min_interval_ms`, `camera_horizontal_fov_deg`. No new Config fields needed
- `pastor_tracker/perception/subject_tracker.py` — Phase 4 BL-01 `consume()` shape. Phase 5 stages mirror it 1:1 for orchestrator composability and uniform testability
- `pastor_tracker/io/arduino_motor.py` — provides `send_motor_angle(deg)` async API; Phase 6 (NOT Phase 5) wires the dispatcher's `MotorCommand.target_angle_deg` into this call
- `structlog` logger — bind `module="motion_analyzer" / "framer" / "pan_controller" / "command_dispatcher"`

### Established Patterns (Phases 1–4)
- Pure-core / dirty-edges — Phase 5 lives entirely on the pure side; only the orchestrator crosses to `io/arduino_motor.py`
- `consume(upstream, now_ns)` async surface (Phase 4 BL-01) is the canonical per-frame stage shape from Perception onward
- Pydantic `frozen=True, extra='forbid'` for DTOs; mutation via `.model_copy(update=...)`; dataclass `frozen=True, slots=True` only for ndarray-bearing types (none in Phase 5)
- Lint policy rejects `print(`, bare `except:`, `except Exception: pass` (`ruff` rules T20 / BLE001 / E722); `mypy --strict` no `Any`
- Test fixtures live under `tests/fixtures/` (Phase 2 `FakeSerialTransport`, Phase 3 `FakeVideoSource`, Phase 4 `FakePoseEngine`); Phase 5 adds `trajectories.py` for `TrackedSubject` sequence generation
- Pure-parser-layer 100% line+branch coverage rule (Phase 2) applies equivalently to hysteresis classifier + deadband/clamp branches
- Phase 4 deep-review fix discipline (W3, W4, BL-01, WR-08 exhaustiveness `case _`) — Phase 5 `consume()` dispatch should likewise have an exhaustiveness guard if a `match` is used

### Integration Points
- Upstream — `SubjectTracker.consume()` (Phase 4) emits `TrackedSubject | None` to `MotionAnalyzer.consume()`
- Downstream — `CommandDispatcher.decide()` emits `MotorCommand | None`; the **orchestrator (Phase 6)** is responsible for `await motor.send_motor_angle(cmd.target_angle_deg)`. Phase 5 must not import `arduino_motor`
- Phase 6 (Pipeline Orchestrator) composes: `tracker.consume(...) → analyzer.consume(...) → framer.consume(...) → controller.consume(...) → dispatcher.decide(...) → motor.send_motor_angle(...)` — every stage on the same per-frame tick
- Phase 7 (Dashboard) reads — `analyzer.current_intent`, `framer.current_target_x_normalized`, `controller.current_angle_deg`, `dispatcher.last_emitted_angle_deg` (status panel + framing-target overlay + current-vs-target text)

</code_context>

<specifics>
## Specific Ideas

- Mirror the Phase 4 `consume()` test pattern: feed scripted `list[TrackedSubject]` → assert intent transition timing, framing target value, pan angle, dispatcher emission count over a synthetic trajectory
- Hysteresis test must include **borderline chatter**: vx oscillating around `±motion_threshold_norm_per_sec` — assert intent does NOT flip until sustained ≥ hysteresis duration (this is the regression test against thrash)
- Damping test must include **step-response on framer** and **step-response on controller**: feed a target step (left third → right third), assert position monotonic toward target with no overshoot (re-uses the Phase 1 step-response invariant on a fresh damper instance)
- Velocity-clamp test: feed a target far enough to require `> 30°/s` to track at the test dt; assert the per-step delta never exceeds `pan_max_velocity_deg_per_sec * dt`; assert damper state position == clamped position (not the unclamped one)
- Deadband test: feed a target requiring < 0.4° change; assert PanController emits the previous angle (no movement) and dispatcher emits no `MotorCommand`
- Dispatcher test counts emissions over a synthetic ramp trajectory at known dt — assert `count <= total_sec * 1000 / command_min_interval_ms` (matches success criterion #4 in ROADMAP)
- All tests parametrized via `Config` fixtures — no hardcoded `0.08`, `0.4`, `30.0`, etc. in test bodies
- Logging shapes (Phase 2/3/4 precedent): `event="intent_change" old=… new=… vx=… sustained_for_sec=…` (analyzer); `event="framing_target_change" old=… new=…` (framer); `event="pan_clamped" delta_deg=… clamp_deg=…` (controller, debug-level only); `event="command_emitted" angle_deg=… delta_deg=… interval_ms=…` (dispatcher, debug-level only — INFO would spam at 20 Hz)

</specifics>

<deferred>
## Deferred Ideas

- Adaptive hysteresis (tighten thresholds when speaker is dwelling, loosen during rapid motion) — out of scope; v1 uses fixed Config thresholds
- Vertical / tilt axis framing target (rule-of-thirds in y too) — pan-only mount in v1 per PROJECT.md (v2 TILT-01)
- Multi-subject framer (audience cutaway, interpreter framing) — out of scope (v2 MULTI-01)
- Lookahead via Kalman velocity prediction (anticipate motion) — deferred; the two-stage damper + hysteresis already meet the no-overshoot/no-jerk bar without prediction lead
- Live config reload during pipeline run — Phase 7 dashboard "Save Config" reloads + restarts pipeline; not Phase 5's concern
- Recording / replay of the framing decisions for post-mortem — deferred to v2 telemetry tooling
- PID toggle / fallback for "experimentation" — explicitly forbidden by PROJECT.md and CLAUDE.md
- Real-stage smoke test against the live Uno + OBS + speaker — Phase 8 QA-04
- On-screen visualization of the hysteresis state machine — Phase 7 dashboard concern, not analyzer's

</deferred>
