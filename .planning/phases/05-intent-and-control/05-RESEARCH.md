# Phase 5: Intent and Control - Research

**Researched:** 2026-05-05
**Domain:** Hysteresis classification (Schmitt-trigger / sustained-crossing) on a continuous velocity signal + two-stage critically-damped follower (closed-form Holden) with anti-windup velocity clamp + emission-deadband + rate-limited dispatcher (Δ-and-interval gate). All code lives in pure-core / dirty-edges layer — no I/O, no async transport, mirroring Phase 4 BL-01 `consume()` shape.
**Confidence:** HIGH on damping invariants and consume-shape (CITED to existing repo code); HIGH on Schmitt-trigger / hysteresis idiom (VERIFIED against control-engineering canon); HIGH on anti-windup overwrite pattern (CITED to existing CONTEXT.md decision and game-loop best practice for clamped follower output); MEDIUM on hypothesis strategy shapes (CITED to Phase 1 test_damping precedent).

## Summary

Phase 5 fuses four pure transforms into a single per-frame tick: `MotionAnalyzer` classifies intent from `TrackedSubject.velocity_x_norm_per_sec` using a sustained-crossing Schmitt-style classifier with separate move/dwell timers; `Framer` damps the rule-of-thirds target in normalized-x space (stage-1, τ ≈ 0.8 s); `PanController` converts to degrees, damps in motor space (stage-2, τ ≈ 0.6 s), velocity-clamps and emission-deadbands; `CommandDispatcher` gates emission on Δ > 0.2° AND ≥ 50 ms. Every stage exposes `async def consume(upstream, now_ns) -> downstream | None` (mirrors Phase 4 BL-01) except the dispatcher, which is `def decide(angle | None, now_ns) -> MotorCommand | None`.

Every design knob is already locked in CONTEXT.md (4 areas, 13 binding decisions); every numeric tunable is already in `Config` (no new fields); every DTO is already in `core/types.py` (no new fields); the damper and FOV math are already implemented and property-tested in Phase 1. **Phase 5 is therefore not a "what library do we use" research phase — it is a "how do we wire CONTEXT-locked transforms together without violating Tiger-style / mypy --strict / ≤2-nesting / no-PID / no-EMA constraints" research phase.** The high-value research output is: (1) the canonical dt rule across None / non-None upstream interleaving, (2) the anti-windup pattern that doesn't violate "no PID", (3) deadband-vs-damper interaction semantics, (4) hypothesis strategies for the four trajectory fixtures, (5) per-stage seeding behavior on re-acquisition, (6) structlog event-name conventions consistent with Phase 2-4, (7) `match`-statement exhaustiveness pattern that survives WR-08-style review, (8) analytic upper bound for the dispatcher emission-count assertion.

**Primary recommendation:** Implement the four stages as four standalone classes with the locked `consume()` / `decide()` surfaces, drive each test from a `Config` fixture (no hardcoded thresholds), reuse `CriticallyDampedFollower` unchanged (instantiated twice, one per stage), and treat the dt rule (§Code Examples, Pattern 1) as the single source of truth for time-derivative computation across all four stages. Do not add files beyond `motion_analyzer.py`, `framer.py`, `pan_controller.py`, `command_dispatcher.py`, and `tests/fixtures/trajectories.py` unless an individual file balloons past ~250 lines (Phase 2/3 split discipline).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts:**
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

**Stage Interface Shape (Area 1):**
- `MotionAnalyzer.consume(subject: TrackedSubject | None, now_ns: int) -> MotionState | None`
- `Framer.consume(motion: MotionState | None, now_ns: int) -> FramingTarget | None`
- `PanController.consume(target: FramingTarget | None, now_ns: int) -> float | None` (damped angle in degrees, or None)
- `CommandDispatcher.decide(angle_deg: float | None, now_ns: int) -> MotorCommand | None` (sync, pure transform)

**Hysteresis State Machine (Area 2):**
- Time source = `TrackedSubject.timestamp_ns` upstream + caller's `now_ns` on None ticks; analyzer NEVER reads a wall clock
- Sustained-velocity = per-direction `_first_crossing_ts_ns`; flip on continuous duration ≥ `motion_hysteresis_sec`; reset on un-cross
- Dwell = `_dwell_start_ts_ns` while `|vx| < dwell_threshold_norm_per_sec`; flip to `dwelling` once continuous ≥ `dwell_duration_sec`
- None-upstream → reset all timers, emit `MotionState(intent="indeterminate", sustained_velocity_x_norm_per_sec=0.0, timestamp_ns=now_ns)`
- Initial intent = `"indeterminate"`

**Two-Stage Damping (Area 3):**
- Stage-1 damper held by `Framer`, stepped in normalized-x domain
- Stage-2 damper held by `PanController`, stepped in degree domain
- Initial state on first non-None upstream = `FollowerState(position=current_target, velocity=0.0)` — no warmup transient
- Hold-on-None / hold-on-`indeterminate` = damper holds its current `FollowerState`, emits held position; no advance, no reset
- Domain conversion order in PanController: `target_x_normalized → angle_deg via normalized_x_to_angle_deg(target_x, fov_deg)` FIRST, then damp in angle domain, then velocity-clamp, then deadband

**Pan Limits, Dispatcher, Tests (Area 4):**
- Velocity clamp: after `damper.step()`, compute `delta_deg = new_pos - prev_pos`, clamp to `±pan_max_velocity_deg_per_sec * dt_sec`, **overwrite** the damper's `FollowerState.position` with the clamped value
- Deadband: applied inside `PanController.consume()` against `_last_emitted_angle_deg`; if `|new - last_emitted| < pan_deadband_deg` emit the previous angle (damper continues stepping under the hood)
- Dispatcher state: `_last_emitted_angle_deg`, `_last_emit_ts_ns`; emit when first call OR (Δ > min_delta AND interval ≥ min_interval); on emit, update both fields
- Dispatcher None-upstream: returns None, does NOT update state
- Test fixtures: new `tests/fixtures/trajectories.py` (`step`, `ramp`, `dwell_then_walk`, `borderline_chatter`) returning `list[TrackedSubject]`
- Coverage: 100% line+branch on hysteresis classifier + deadband+clamp branches + dispatcher gate; ≥90% line on framer + pan controller
- All test parameters flow through `Config` fixtures — no hardcoded numeric literals in test bodies

### Claude's Discretion

All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal helper / private method names beyond `consume()` / `decide()`
- Error class hierarchy under a single `IntentError` / `ControlError` root if needed (analogous to Phase 4 `PerceptionError`)
- Logging key names — keep consistent with `event="..."` shape established in Phases 2–4 structlog conventions
- Whether the hysteresis crossing tracker is a small dataclass or stateful method on `MotionAnalyzer`
- File granularity inside `intent/` and `control/` — `motion_analyzer.py + framer.py` and `pan_controller.py + command_dispatcher.py` is the default; small private helper modules (e.g. `_hysteresis.py`) permitted if a single file balloons past ~250 lines
- Internal docstring depth and inline comments — only where the WHY is non-obvious

### Deferred Ideas (OUT OF SCOPE)

- Adaptive hysteresis (tighten thresholds when speaker is dwelling, loosen during rapid motion)
- Vertical / tilt axis framing target (rule-of-thirds in y too) — v2 TILT-01
- Multi-subject framer (audience cutaway, interpreter framing) — v2 MULTI-01
- Lookahead via Kalman velocity prediction (anticipate motion)
- Live config reload during pipeline run — Phase 7 dashboard concern
- Recording / replay of framing decisions for post-mortem — v2 telemetry
- PID toggle / fallback for "experimentation" — explicitly forbidden by PROJECT.md and CLAUDE.md
- Real-stage smoke test against live Uno + OBS + speaker — Phase 8 QA-04
- On-screen visualization of the hysteresis state machine — Phase 7 dashboard
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **INTENT-01** | Sustained `vx > 0.08` for ≥ 0.3 s = right-bound; `vx < -0.08` ≥ 0.3 s = left-bound; `|vx| < 0.03` ≥ 1.5 s = dwell | Pattern 1 (Hysteresis classifier with per-direction crossing timers) + Pitfall 1 (sample-time aliasing) |
| **INTENT-02** | Hysteresis prevents thrash; thresholds + dwell duration in Config | Pattern 1 (Schmitt-style sustained-crossing predicate) + Code Example 1 + `borderline_chatter` fixture (no flip until ≥ hysteresis duration) |
| **INTENT-03** | Framer maps intent → rule-of-thirds: moving-right → 0.333; moving-left → 0.667; dwelling → 0.500 | Pattern 2 (Framer intent→target mapping) + Code Example 2 + exhaustive `match` over `MotionIntent` Literal (Pattern 6) |
| **INTENT-04** | Framing target smoothed with damping `framing_time_constant_sec ≈ 0.8 s` | Pattern 2 (Stage-1 damper in normalized-x domain) + reuse of `core/damping.CriticallyDampedFollower` (Pattern 3) |
| **CTRL-01** | Pan controller — second damping stage `pan_time_constant_sec ≈ 0.6 s` on motor angle delta | Pattern 3 (Stage-2 damper in degree domain after normalized→angle conversion) |
| **CTRL-02** | Deadband — suppress motor command if `|angle_delta| < 0.4°` | Pattern 4 (Emission-deadband: damper continues stepping, only emission held to last_emitted_angle) + Pitfall 3 (deadband-vs-damper anti-windup) |
| **CTRL-03** | Velocity clamp — max pan rate `30°/s` | Pattern 5 (Anti-windup velocity clamp by overwriting FollowerState.position) + Pitfall 2 (no integral windup since no integrator) |
| **CTRL-04** | Command dispatcher — emit `M:<deg>` only when delta > 0.2° AND ≥ 50 ms since last command | Pattern 7 (Δ-and-interval gate) + Code Example 4 + Open Question 4 (analytic emission-count upper bound) |
| **TEST-03** | Unit tests for `motion_analyzer.py`, `framer.py`, `pan_controller.py` — real damping math, no mocks | Validation Architecture section + `tests/fixtures/trajectories.py` (Pattern 8) + `Config` fixture parametrization (no hardcoded thresholds) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

| Directive | Source | Phase 5 Application |
|-----------|--------|---------------------|
| `mypy --strict`, no `Any`, every function annotated | CLAUDE.md §8 + pyproject.toml `disallow_any_explicit = true` | Stage classes use only `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand`, `int`, `float`, `bool`, `MotionIntent` — no `Any`. `match` over `MotionIntent` Literal must include exhaustiveness `case _` (Pattern 6, Phase 4 WR-08) |
| Tiger-style fail-fast — no silent except, no bare except, `except Exception: pass` forbidden | CLAUDE.md §1 | `IntentError` / `ControlError` root if exceptions are needed; pure transforms should mostly fail at construction (e.g., `time_constant_sec <= 0` from `core/damping.py`) — Phase 5 itself is unlikely to need new exception types |
| ≤ 2-level conditional nesting; guard clauses + early returns | CLAUDE.md §5 | Hysteresis classifier MUST be flat — early-return on None upstream; flat dispatch over `vx` regions; no `if moving_right and sustained and not dwell:` ladders. The crossing logic decomposes into "update timers, then check thresholds" two-pass flow |
| No magic numbers — `Config` is authoritative | CLAUDE.md §6 | Zero hardcoded `0.08`, `0.3`, `0.03`, `1.5`, `0.8`, `0.6`, `0.4`, `30.0`, `0.2`, `50` in production OR test bodies; tests fetch from `Config` fixture. Module-level `Final` constants only for unit-conversion factors (e.g., `_NS_PER_SEC = 1_000_000_000.0`, mirroring Phase 4) |
| Immutable data — Pydantic `frozen=True`, dataclass `frozen=True` | CLAUDE.md §9 | All emitted DTOs (`MotionState`, `FramingTarget`, `MotorCommand`) are already frozen Pydantic models; `FollowerState` is already a frozen dataclass. Internal stage state (`_first_crossing_ts_ns`, `_last_emitted_angle_deg`, etc.) is mutable instance state — that's allowed, but only via `self._x = ...` rebind, never as model fields |
| `structlog` JSON only, no `print()` | CLAUDE.md + ruff `T20` | Logger per stage with `module="motion_analyzer"` / `"framer"` / `"pan_controller"` / `"command_dispatcher"` bind. Event names: see Pattern 9 |
| Conventional Commits, one logical change per commit | CLAUDE.md §10 | Wave-by-wave commit discipline matches Phase 4 (3 plans expected: Plan 05-01 = MotionAnalyzer + trajectories.py fixture + tests; Plan 05-02 = Framer + tests; Plan 05-03 = PanController + CommandDispatcher + tests) — split TBD by planner |
| **Forbidden**: PID, MediaPipe, EMA on detection, mocked Kalman/damping in tests | CLAUDE.md "Forbidden libraries / patterns" | Phase 5 has the highest temptation to violate this — "anti-windup" is canonically a PID concept. Pattern 5 explicitly explains why overwriting FollowerState.position is NOT introducing an integrator and therefore not PID-by-the-back-door |
| `time.sleep()` in main loop forbidden | CLAUDE.md "Forbidden in app code" | Stages are pure transforms — they neither sleep nor poll. Time always flows in via `now_ns` parameter |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Velocity hysteresis classification (vx → MotionIntent) | Loop thread (pure logic in `motion_analyzer.py`) | — | No I/O, no async transport — pure transform on `TrackedSubject` |
| Per-direction crossing timer state | Loop thread (instance state on `MotionAnalyzer`) | — | Owned by single instance; mutated on each `consume()` call |
| Intent → rule-of-thirds normalized target mapping | Loop thread (pure logic in `framer.py`) | — | Static `match`-over-Literal lookup; no state |
| Stage-1 damping (target_x_normalized smoothing) | Loop thread (`framer.py` holds the damper) | — | `CriticallyDampedFollower` is pure math; framer owns one instance + a `FollowerState` |
| Normalized-x → angle-deg conversion | Loop thread (`pan_controller.py` calls `geometry.normalized_x_to_angle_deg`) | — | Pure pinhole math, already property-tested in Phase 1 |
| Stage-2 damping (angle_deg smoothing) | Loop thread (`pan_controller.py` holds the second damper) | — | Same — second `CriticallyDampedFollower` instance, separate `FollowerState` |
| Velocity clamp anti-windup (overwrite damper state) | Loop thread (`pan_controller.py`) | — | After damper step, mutate the about-to-be-stored FollowerState; pure sequential transform |
| Emission deadband (suppress small-delta emissions) | Loop thread (`pan_controller.py` holds `_last_emitted_angle_deg`) | — | Single instance variable, no concurrency |
| Δ-and-interval rate-limit gate | Loop thread (`command_dispatcher.py` holds `_last_emitted_angle_deg`, `_last_emit_ts_ns`) | — | Synchronous decide(); orchestrator owns the per-frame call |
| Read-only dashboard surface | Loop thread (properties read by Phase 7) | — | Phase 7 dashboard reads `current_intent`, `current_target_x_normalized`, `current_angle_deg`, `last_emitted_angle_deg` from main thread; atomicity via Python attribute writes |
| Motor command transmission | (NOT Phase 5) — Phase 6 orchestrator only | — | Phase 5 emits `MotorCommand` DTO; Phase 6 calls `motor.send_motor_angle(cmd.target_angle_deg)`. Phase 5 must NOT import `arduino_motor` |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pastor_tracker.core.damping` (in-repo) | shipped Phase 1 | Critically-damped 2nd-order follower (Holden exact closed form) | The only damper in the project per CLAUDE.md; reused unchanged for both stages [VERIFIED: `pastor_tracker/src/pastor_tracker/core/damping.py` exists, property-tested via `tests/test_damping.py`] |
| `pastor_tracker.core.geometry` (in-repo) | shipped Phase 1 | `normalized_x_to_angle_deg(nx, fov)` pinhole conversion | The only normalized↔angle bridge per CONTEXT lock; property-tested via `tests/test_geometry.py` [VERIFIED: `pastor_tracker/src/pastor_tracker/core/geometry.py`] |
| `pastor_tracker.core.types` (in-repo) | shipped Phase 1 | `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand`, `MotionIntent` Literal | DTOs already frozen and validated; `MotionIntent` already includes `"indeterminate"` sentinel [VERIFIED: `core/types.py:32` has `MotionIntent = Literal["moving_left", "moving_right", "dwelling", "indeterminate"]`] |
| `pastor_tracker.config.Config` (in-repo) | shipped Phase 1 | All 11 numeric knobs Phase 5 needs | All fields exist with range validation [VERIFIED: `config.py` lines 160-206] |
| `structlog` | `>=24.4,<26.0` (locked in pyproject) | JSON logging only — no `print()` | Phase 1-4 precedent; bind `module="..."` per stage |
| `pytest` | `>=8.4,<9.0` | Test runner with `asyncio_mode = "auto"` | pyproject.toml line 99 |
| `pytest-asyncio` | `>=0.26,<2.0` | `async def test_*` works without explicit decorator | pyproject.toml line 24 |
| `hypothesis` | `>=6.152,<7.0` | Property tests for hysteresis edge cases (borderline-chatter strategy) | Phase 1 precedent in `test_damping.py` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| stdlib `dataclasses` | py3.12 | `frozen=True, slots=True` for any internal records (e.g., crossing-timer struct if extracted) | Only if a dataclass clarifies — instance attributes are fine |
| stdlib `enum` | py3.12 | Internal state enums if needed (e.g., `_HysteresisRegion`) — NOT exposed in DTOs | Mirrors Phase 4 `_LockState` pattern |
| stdlib `typing.Final` | py3.12 | Module-level constants for `_NS_PER_SEC`, `_NS_PER_MS` only | All tunables go in Config |
| stdlib `math` | py3.12 | Already used by `core.damping` and `core.geometry` | Inherit; no direct math in Phase 5 stages |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Sustained-crossing per-direction timer | Schmitt trigger with two thresholds (asymmetric upper/lower) | Schmitt is the canonical hysteresis idiom but adds two new Config fields (upper/lower threshold). CONTEXT.md locks single-threshold + sustained-duration — equivalent guarantee against thrash, fewer knobs. **Reject** [CITED: Wikipedia "Schmitt trigger" — both formulations achieve hysteresis; sustained-duration is just the time-domain dual] |
| Leaky integrator on velocity | First-order IIR / EMA | EMA is forbidden by PROJECT.md / CLAUDE.md. **Reject** |
| Debouncer (count consecutive frames above threshold) | Frame-count instead of duration-based | Frame-count couples threshold to frame rate; CONTEXT-locked decision is duration-based (`motion_hysteresis_sec`). **Reject** |
| Separate framer + controller dampers | Single damper at the boundary | The two-stage damping decision is locked; PROJECT.md "Two-stage damping (framing + motor)" is the architectural rationale (separates intent smoothing from actuation smoothing). **Reject merger** |
| Per-call damper instantiation | Long-lived damper instance per stage | `CriticallyDampedFollower` is a frozen dataclass with no per-call cost; `FollowerState` is the only mutable bit and it's threaded explicitly. CONTEXT-locked. **No-op** |

**Installation:** No new dependencies needed. All five Phase 5 modules live within the existing `pastor_tracker` package. **Verify by running `uv pip list | grep -E "filterpy|pydantic|structlog|hypothesis"` — all expected pinned versions already in lockfile from Phase 1-4.**

**Version verification (Wave 0 must run):**
```bash
cd pastor_tracker
uv run python -c "from pastor_tracker.core.damping import CriticallyDampedFollower; print('damping ok')"
uv run python -c "from pastor_tracker.core.geometry import normalized_x_to_angle_deg; print(normalized_x_to_angle_deg(0.5, 70.0))"
uv run python -c "from pastor_tracker.core.types import TrackedSubject, MotionState, FramingTarget, MotorCommand, MotionIntent; print('types ok')"
uv run python -c "from pastor_tracker.config import Config; c = Config(arduino_port='COM6'); print(c.motion_threshold_norm_per_sec, c.framing_time_constant_sec)"
```
[VERIFIED: All four imports succeed against the current repo (read 2026-05-05); commit `32fea4d` is current HEAD on `fresh-2026`.]

## Architecture Patterns

### System Architecture Diagram

```
              now_ns (perf_counter_ns())
                       │
                       ▼
TrackedSubject | None ─┐                                 (per-frame tick from Phase 6 orchestrator)
                       │
                       ▼
                  MotionAnalyzer.consume()
                  ┌──────────────────────────────┐
                  │ guard: None upstream         │
                  │   ├─ reset all timers        │
                  │   └─ emit MotionState(       │
                  │       "indeterminate", 0.0)  │
                  │ guard: |vx| > move_thresh    │
                  │   ├─ start/keep crossing tmr │
                  │   └─ flip on duration ≥ tau  │
                  │ guard: |vx| < dwell_thresh   │
                  │   ├─ start/keep dwell tmr    │
                  │   └─ flip on duration ≥ tau  │
                  └─────────────┬────────────────┘
                       │
                       ▼
MotionState | None ────┐
                       │
                       ▼
                  Framer.consume()
                  ┌──────────────────────────────┐
                  │ guard: None / "indeterminate"│
                  │   └─ hold damper state, emit │
                  │      held position (or None) │
                  │ intent → target lookup:      │
                  │   moving_right → 0.333       │
                  │   moving_left  → 0.667       │
                  │   dwelling     → 0.500       │
                  │ damper.step(target,dt)       │
                  │   in normalized-x domain     │
                  │   τ = framing_time_const     │
                  └─────────────┬────────────────┘
                       │
                       ▼
FramingTarget | None ──┐
                       │
                       ▼
                  PanController.consume()
                  ┌──────────────────────────────┐
                  │ guard: None upstream         │
                  │   └─ hold damper state, emit │
                  │      last_emitted (or None)  │
                  │ angle = normalized_x_to_     │
                  │   angle_deg(target.x, fov)   │
                  │ damper.step(angle, dt)       │
                  │   in degree domain           │
                  │   τ = pan_time_constant      │
                  │ velocity clamp:              │
                  │   delta = new - prev         │
                  │   clamped = clip(delta, ±vmax│
                  │             *dt)             │
                  │   OVERWRITE FollowerState    │
                  │     .position with clamped   │
                  │ deadband:                    │
                  │   if |new - last_emitted|    │
                  │       < pan_deadband_deg     │
                  │     emit last_emitted        │
                  │   else                       │
                  │     emit new + update        │
                  │     last_emitted             │
                  └─────────────┬────────────────┘
                       │
                       ▼
float | None ──────────┐
                       │
                       ▼
                  CommandDispatcher.decide()  (sync, pure)
                  ┌──────────────────────────────┐
                  │ guard: None angle            │
                  │   └─ return None             │
                  │      (do NOT update state)   │
                  │ guard: first emit            │
                  │   └─ emit MotorCommand,      │
                  │      update last_*           │
                  │ guard: |Δ| > min_delta AND   │
                  │       interval ≥ min_int     │
                  │   └─ emit MotorCommand,      │
                  │      update last_*           │
                  │ else                         │
                  │   return None                │
                  └─────────────┬────────────────┘
                       │
                       ▼
                MotorCommand | None
                       │
                       ▼
              (Phase 6 orchestrator routes to ArduinoMotor)
```

Data flow: every per-frame tick the orchestrator passes whatever upstream emitted (subject or None) along with `now_ns`. Each stage either emits a downstream DTO or None; "None" propagates correctly through the chain because every stage has `consume(... | None, ...)` semantics. The dispatcher is the only **sync** stage because it needs no I/O wait.

### Recommended Project Structure

```
pastor_tracker/src/pastor_tracker/
├── intent/
│   ├── __init__.py             # public exports: MotionAnalyzer, Framer, IntentError (if any)
│   ├── motion_analyzer.py      # MotionAnalyzer class (hysteresis state machine)
│   └── framer.py               # Framer class (intent→target + stage-1 damper)
├── control/
│   ├── __init__.py             # public exports: PanController, CommandDispatcher, ControlError (if any)
│   ├── pan_controller.py       # PanController class (FOV conversion + stage-2 damper + clamp + deadband)
│   └── command_dispatcher.py   # CommandDispatcher class (Δ-and-interval gate)

pastor_tracker/tests/
├── fixtures/
│   └── trajectories.py         # step / ramp / dwell_then_walk / borderline_chatter helpers
├── test_motion_analyzer.py     # hysteresis (move + dwell) + None-upstream + initial_state + borderline_chatter
├── test_framer.py              # third selection over MotionIntent + step-response on damper + hold-on-None
├── test_pan_controller.py      # FOV conversion + step-response + velocity clamp + deadband + hold-on-None
└── test_command_dispatcher.py  # Δ gate + interval gate + first-call + None-upstream non-update
```

**File-split rationale:**
- Two top-level packages (`intent/`, `control/`) match PROJECT.md `Architecture` block and the existing repo layout; `pastor_tracker/intent/` and `pastor_tracker/control/` are new directories per CLAUDE.md `## Architecture` block (lines 24-26 of CLAUDE.md).
- One class per file — Phase 2/3/4 precedent: `arduino_protocol.py` was an exception (parser is many small types), but orchestrator-style classes are one-per-file (`arduino_motor.py`, `obs_camera.py`, `pose_detector.py`, `subject_tracker.py`).
- Tests split mirrors classes 1:1 — Phase 4 chose to split tests by *concern* (lock / kalman / hold), but Phase 5's stages each have a single concern, so 1:1 is correct.
- `tests/fixtures/trajectories.py` matches `tests/fixtures/pose_traces.py`, `arduino_traces.py`, `camera_traces.py` precedent — pure helpers + scripted sequences.
- A future `_hysteresis.py` private helper module is permitted IF `motion_analyzer.py` exceeds ~250 lines (it shouldn't — the classifier is small).

### Pattern 1: Hysteresis classifier with sustained per-direction crossing timers

**What:** Each stage frame checks `vx` against `motion_threshold_norm_per_sec`; if currently crossed in a direction, the timer for that direction starts (or persists); when the duration ≥ `motion_hysteresis_sec`, the intent flips. Crossing the threshold back resets the timer for that direction.
**When to use:** This phase, INTENT-01/02. Replaces a Schmitt trigger's two-threshold formulation with a one-threshold + sustained-duration formulation. Equivalent anti-thrash guarantee.
**Why not Schmitt:** Schmitt requires two thresholds (upper / lower); CONTEXT-locked decision is one threshold + sustained duration. Both prevent borderline thrash; the duration-based form is preferred here because it composes naturally with the dwell timer (both are "continuous duration ≥ X" predicates).

**Sketch:**
```python
# motion_analyzer.py — internal state (instance attributes on MotionAnalyzer)
self._first_right_crossing_ts_ns: int | None = None
self._first_left_crossing_ts_ns: int | None = None
self._dwell_start_ts_ns: int | None = None
self._current_intent: MotionIntent = "indeterminate"
```

**Sources:**
- Wikipedia "Schmitt trigger" — hysteresis can be expressed as two thresholds or as one threshold + duration (time-domain dual). [CITED: en.wikipedia.org/wiki/Schmitt_trigger]
- Phase 4 `_LockState` machine uses the same "continuous duration ≥ timeout" idiom (lock-loss = `> 2.0 s` since last seen). [VERIFIED: `subject_tracker.py:262`]

### Pattern 2: Intent → rule-of-thirds target with exhaustive `match`

**What:** A `match` over `MotionIntent` Literal that maps to a numeric target. Must include `case _` exhaustiveness guard (Phase 4 WR-08 lesson).
**When to use:** Inside `Framer.consume()` to translate the analyzer's intent to the framer's target.

```python
# framer.py — exhaustive dispatch (CLAUDE.md ≤2-nesting + WR-08 exhaustiveness)
_TARGET_LEFT_THIRD: Final[float] = 1.0 / 3.0    # 0.333…
_TARGET_RIGHT_THIRD: Final[float] = 2.0 / 3.0   # 0.667…
_TARGET_CENTER: Final[float] = 0.5

def _intent_to_target(intent: MotionIntent) -> float | None:
    """Returns None for 'indeterminate' (caller holds damper state)."""
    match intent:
        case "moving_right":
            return _TARGET_LEFT_THIRD     # subject moving right → frame him on left third
        case "moving_left":
            return _TARGET_RIGHT_THIRD
        case "dwelling":
            return _TARGET_CENTER
        case "indeterminate":
            return None                    # no target yet; hold damper
        case _:
            raise IntentError(             # WR-08: exhaustiveness guard
                f"unhandled MotionIntent in _intent_to_target: {intent!r}"
            )
```

### Pattern 3: Two damping stages, separate `FollowerState` per stage

**What:** Each of `Framer` and `PanController` owns one `CriticallyDampedFollower` and one `FollowerState`. The damper instance is constructed once in `__init__` from `Config`; the state is mutated (rebound) each `consume()` call by `damper.step()`.
**When to use:** Always — this is the architectural locked decision.

```python
# framer.py — sketch
class Framer:
    def __init__(self, config: Config) -> None:
        self._damper = CriticallyDampedFollower(
            time_constant_sec=config.framing_time_constant_sec,
        )
        self._state: FollowerState | None = None  # seeded on first non-None upstream
        self._last_upstream_ts_ns: int | None = None
        # ...

    async def consume(
        self, motion: MotionState | None, now_ns: int,
    ) -> FramingTarget | None:
        if motion is None or motion.intent == "indeterminate":
            return self._emit_held(now_ns)
        target = _intent_to_target(motion.intent)
        # target is non-None here because we filtered "indeterminate" above
        assert target is not None
        if self._state is None:
            self._state = FollowerState(position=target, velocity=0.0)
            self._last_upstream_ts_ns = motion.timestamp_ns
            return FramingTarget(target_x_normalized=target, timestamp_ns=now_ns)
        dt_sec = self._compute_dt_sec(motion.timestamp_ns)
        self._state = self._damper.step(self._state, target=target, dt=dt_sec)
        self._last_upstream_ts_ns = motion.timestamp_ns
        # Clamp to [0,1] -- the damper math is monotonic when seeded at-target,
        # but on re-acquisition with a new target the transient could overshoot
        # the [0,1] FramingTarget validator; clamp for safety.
        clamped = min(1.0, max(0.0, self._state.position))
        return FramingTarget(target_x_normalized=clamped, timestamp_ns=now_ns)
```

**Note on the clamp:** Critically-damped follower is monotonic from-rest (Phase 1 property test), so on a within-domain step (0.333 ↔ 0.667 ↔ 0.5) the trajectory stays in `[0.333, 0.667]`. The seam-clamp to `[0, 1]` exists only to satisfy the Pydantic `Field(ge=0.0, le=1.0)` validator in case of a future configuration mistake.

### Pattern 4: Emission deadband (NOT damper-state freeze)

**What:** `PanController` keeps stepping its damper every frame, but only **emits** the new angle if `|new_angle - last_emitted_angle| ≥ pan_deadband_deg`. The damper is not frozen during the deadband; only the emission is.
**Why this and not "freeze damper":** Freezing the damper would create a discontinuity at the deadband edge — when we eventually emit again, the damper would jump from a stale state. Continuing to step keeps the internal trajectory smooth; emission deadband alone produces the same operational behavior (motor doesn't move) without introducing damper windup.
**Side effect:** When a small target update accumulates damper motion until it exceeds the deadband, the emission catches up smoothly because the damper has been faithfully integrating the target the whole time.

```python
# pan_controller.py — sketch
if self._last_emitted_angle_deg is None:
    self._last_emitted_angle_deg = new_angle_deg
    return new_angle_deg
if abs(new_angle_deg - self._last_emitted_angle_deg) < self._config.pan_deadband_deg:
    return self._last_emitted_angle_deg  # damper still stepped above; only emission frozen
self._last_emitted_angle_deg = new_angle_deg
return new_angle_deg
```

### Pattern 5: Velocity clamp by overwriting `FollowerState.position` (anti-windup, NOT PID)

**What:** After `state = damper.step(state, target, dt)`, compute `delta = state.position - prev_position`. If `|delta| > vmax * dt`, clip `delta` to `±vmax * dt`, recompute `clamped_position = prev_position + clipped_delta`, and **rebind** `self._state = replace(state, position=clamped_position)`. Future damper steps therefore integrate from the clamped position, not the unclamped one.
**Why this is not PID:** A PID integrates an error term; "anti-windup" in PID parlance is suppressing integral accumulation against a saturated actuator. The Holden critically-damped follower has NO integral term — it has a position state and a velocity state. "Overwriting position" is just constraining the position trajectory to match physical actuator limits; the next step's `j0 = position - target` and `j1 = velocity + j0*y` recompute from the constrained position, so the damper naturally re-converges to the (still-pursued) target without overshoot. The damper's velocity is **not** re-clamped — the trajectory remains critically damped in the abstract; what we constrain is the position the motor can physically execute. [CITED: Daniel Holden "Spring-It-On" — the closed-form follower is unconditionally stable for any valid (position, velocity, target) tuple, including a sequence that has been externally constrained between steps.]

```python
# pan_controller.py — sketch (after damper step)
new_state = self._damper.step(self._state, target=angle_deg, dt=dt_sec)
delta = new_state.position - self._state.position
max_delta = self._config.pan_max_velocity_deg_per_sec * dt_sec
if abs(delta) > max_delta:
    clamped_delta = max(-max_delta, min(max_delta, delta))
    new_state = replace(new_state, position=self._state.position + clamped_delta)
    self._logger.debug(
        "pan_clamped",
        unclamped_delta_deg=delta,
        clamped_delta_deg=clamped_delta,
        dt_sec=dt_sec,
    )
self._state = new_state
```

### Pattern 6: `match`-statement exhaustiveness over `MotionIntent` Literal (Phase 4 WR-08 lesson)

**What:** Every `match` over a `Literal` type must include a `case _:` arm that raises a typed exception, even when all current Literal members are covered. This prevents silent breakage when a future Literal member is added.
**When to use:** `Framer._intent_to_target()`, anywhere else `MotionIntent` is dispatched.

```python
match intent:
    case "moving_right": ...
    case "moving_left": ...
    case "dwelling": ...
    case "indeterminate": ...
    case _:
        raise IntentError(f"unhandled MotionIntent: {intent!r}")
```

[VERIFIED: Phase 4 `subject_tracker.py:191-198` `case _:` raises `PerceptionError` for unhandled `_LockState` — the canonical Tiger-style fail-loud pattern.]

### Pattern 7: Δ-and-interval emission gate

**What:** Emit when `last is None` (first call) OR `(|angle - last_angle| > min_delta AND now_ns - last_ns ≥ min_interval_ns)`. On emit, update both `last_angle` and `last_ns`. None upstream → return None, do NOT update state.

```python
# command_dispatcher.py — sketch
class CommandDispatcher:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._last_emitted_angle_deg: float | None = None
        self._last_emit_ts_ns: int | None = None
        # ...

    def decide(self, angle_deg: float | None, now_ns: int) -> MotorCommand | None:
        if angle_deg is None:
            return None
        if self._last_emitted_angle_deg is None or self._last_emit_ts_ns is None:
            self._last_emitted_angle_deg = angle_deg
            self._last_emit_ts_ns = now_ns
            return MotorCommand(target_angle_deg=angle_deg, timestamp_ns=now_ns)
        delta = abs(angle_deg - self._last_emitted_angle_deg)
        interval_ns = now_ns - self._last_emit_ts_ns
        min_interval_ns = self._config.command_min_interval_ms * _NS_PER_MS_INT
        if delta > self._config.command_min_delta_deg and interval_ns >= min_interval_ns:
            self._last_emitted_angle_deg = angle_deg
            self._last_emit_ts_ns = now_ns
            return MotorCommand(target_angle_deg=angle_deg, timestamp_ns=now_ns)
        return None
```

### Pattern 8: Trajectory fixture helpers

**What:** Pure module-level helpers in `tests/fixtures/trajectories.py` returning `list[TrackedSubject]`. Drives every Phase 5 unit test without per-test boilerplate.

```python
# tests/fixtures/trajectories.py
def step(
    *,
    t_step_sec: float,
    dt_sec: float,
    total_sec: float,
    x_before: float,
    x_after: float,
    track_id: int = 1,
    t0_ns: int = 1_000_000_000,
) -> list[TrackedSubject]:
    """Position step at t_step_sec; velocity computed by finite-difference."""

def ramp(
    *,
    x_start: float, x_end: float,
    dt_sec: float, total_sec: float,
    track_id: int = 1, t0_ns: int = 1_000_000_000,
) -> list[TrackedSubject]:
    """Linear ramp; constant vx = (x_end - x_start) / total_sec."""

def dwell_then_walk(
    *,
    dwell_sec: float, walk_vx: float,
    dt_sec: float, total_sec: float,
    track_id: int = 1, t0_ns: int = 1_000_000_000,
) -> list[TrackedSubject]:
    """Stationary at x=0.5 for dwell_sec, then linear motion at walk_vx norm/sec."""

def borderline_chatter(
    *,
    vx_amplitude: float,
    dt_sec: float, total_sec: float,
    track_id: int = 1, t0_ns: int = 1_000_000_000,
) -> list[TrackedSubject]:
    """vx oscillates between +vx_amplitude and -vx_amplitude every frame.

    Set vx_amplitude slightly above motion_threshold_norm_per_sec so each
    direction crosses the threshold but never sustains for hysteresis_sec.
    Asserts that intent NEVER flips out of 'indeterminate' over the whole run.
    """
```

### Pattern 9: structlog event names per stage

| Stage | Event | Level | Fields | Frequency |
|-------|-------|-------|--------|-----------|
| MotionAnalyzer | `intent_change` | INFO | `old`, `new`, `vx`, `sustained_for_sec` | rare (intent transitions) |
| MotionAnalyzer | `motion_analyzer_reset` | DEBUG | `reason="upstream_none"` | once per upstream gap |
| Framer | `framing_target_change` | INFO | `old`, `new`, `intent` | rare (intent transitions translate to target changes) |
| Framer | `framer_seeded` | DEBUG | `position`, `intent` | once per re-acquisition |
| PanController | `pan_clamped` | DEBUG | `unclamped_delta_deg`, `clamped_delta_deg`, `dt_sec` | per clamping frame |
| PanController | `pan_deadband_suppressed` | DEBUG | `delta_deg`, `last_emitted_angle_deg` | per suppressed frame (high-frequency — debug only) |
| PanController | `pan_controller_seeded` | DEBUG | `angle_deg` | once per re-acquisition |
| CommandDispatcher | `command_emitted` | DEBUG | `angle_deg`, `delta_deg`, `interval_ms` | per emission (~ ≤ 20 Hz) |
| CommandDispatcher | `command_suppressed_delta` | DEBUG | `angle_deg`, `delta_deg` | per suppressed frame |
| CommandDispatcher | `command_suppressed_interval` | DEBUG | `angle_deg`, `interval_ms` | per suppressed frame |

**INFO-vs-DEBUG choice:** intent transitions are operator-meaningful (an INFO line per pastor's pace change is fine, ~few per minute). Pan-clamp / deadband / dispatcher fire at frame rate — DEBUG only, otherwise structlog floods at 30 Hz.

### Anti-Patterns to Avoid

- **Reading wall clock inside any stage:** Every stage takes `now_ns` from the caller. NEVER `time.perf_counter_ns()` or `time.time()` inside `consume()` / `decide()`. Tests must be deterministic.
- **AsyncIterator surface:** Phase 4 BL-01 lesson — an iterator backed by a queue deadlocks when nothing pumps. `consume()` returns directly per call.
- **EMA on velocity to smooth chatter:** Forbidden by PROJECT.md. Use sustained-crossing duration instead.
- **PID toggle "for experimentation":** Forbidden — even commented-out PID code violates CLAUDE.md "no commented-out code".
- **Adding new Config fields:** Phase 5 introduces zero new Config fields. Every threshold already exists.
- **Adding new DTO fields:** `MotionState`, `FramingTarget`, `MotorCommand` are frozen with the necessary fields. Don't add `confidence` or `intent_changed_this_frame` etc.
- **Mocking the damper or `core.geometry`:** CLAUDE.md TEST-05 + CONTEXT lock — tests drive the real `CriticallyDampedFollower` and the real `normalized_x_to_angle_deg`.
- **Using frame-count as the hysteresis unit:** CONTEXT-locked: duration-based, not frame-count-based.
- **Freezing the damper during deadband:** Pattern 4 — only the emission is frozen.
- **Updating dispatcher state on None upstream:** CONTEXT-locked area 4 — `decide(None, ts)` returns None and does NOT touch `_last_*`. This keeps the Δ-and-interval gate meaningful across upstream gaps.
- **Per-direction thresholds:** Use a single `motion_threshold_norm_per_sec`; the sign of `vx` tells you the direction. Don't accidentally split the threshold into "right" and "left" Config fields.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Critically-damped follower | numpy IIR or hand-rolled spring math | `pastor_tracker.core.damping.CriticallyDampedFollower` | Already shipped, property-tested, Holden-exact-stable; CONTEXT-locked. Re-implementing introduces overshoot bugs. |
| Normalized↔angle conversion | inline `(x - 0.5) * fov` linear approximation | `pastor_tracker.core.geometry.normalized_x_to_angle_deg` | Pinhole model is non-linear (atan-shaped); the linear approximation is wrong at the FOV edges. Already property-tested in Phase 1. |
| Hysteresis classifier | A finite state machine library (`transitions`, `python-statemachine`) | A few `if`-guarded crossing timers | The hysteresis logic is ~30 lines; pulling in a state-machine library adds dependency weight + obscures Tiger-style intent. |
| Test trajectory generators | hypothesis-only or random walk | Pure helpers in `tests/fixtures/trajectories.py` (Pattern 8) | Deterministic helpers compose with hypothesis (`@given(strategy)`) when needed; pure helpers alone make assertions traceable. |
| Time arithmetic (`now - last >= duration`) | manual ns/sec/ms juggling | Module-level `_NS_PER_SEC`, `_NS_PER_MS` Final constants + a single `_compute_dt_sec()` helper per stage | Phase 4 `subject_tracker.py:60` already uses this idiom; mirror exactly. |
| Async transport / queue between stages | `asyncio.Queue` + producer task per stage | Direct `await stage.consume(upstream, now_ns)` per frame in orchestrator | Phase 4 BL-01 lesson; queues deadlock on empty upstream. |

**Key insight:** Phase 5 has the smallest surface area of any phase — four small classes, ~150-200 lines each. The risk is in *strict* adherence to CONTEXT decisions and CLAUDE.md culture (no PID-by-the-back-door, no EMA-by-the-back-door, no magic numbers in tests). Don't add abstraction; the four classes are simple enough to read top-to-bottom.

## Common Pitfalls

### Pitfall 1: Sample-time aliasing at the hysteresis threshold boundary
**What goes wrong:** `vx` floats just above `motion_threshold_norm_per_sec` due to Kalman jitter; on some frames it crosses, on others it un-crosses; the per-direction crossing timer keeps resetting; intent never flips even though motion is sustained.
**Why it happens:** The threshold is a hard boundary; numerical noise in the Kalman posterior crosses it both ways within a single sample interval.
**How to avoid:** This is by design — `motion_hysteresis_sec` is exactly the antidote. The fixture `borderline_chatter` exercises this: vx oscillates around the threshold each frame, and the assertion is that intent **stays** "indeterminate" (correct anti-thrash behavior). If real-stage tuning shows aliasing-induced thrash, raise `motion_threshold_norm_per_sec` or `motion_hysteresis_sec` in Config — do NOT add Schmitt's two-threshold formulation to code (Config is authoritative).
**Warning signs:** `intent_change` events fire several times per second in normal operation (should be at most a handful per minute).

### Pitfall 2: PID-by-the-back-door via velocity clamp
**What goes wrong:** Engineer writes "anti-windup" code that accumulates an integral of the clamped error (a la true PID anti-windup) — quietly introducing an integrator and therefore a PID variant.
**Why it happens:** "Anti-windup" is a PID term; reaching for the canonical pattern brings the integrator with it.
**How to avoid:** Pattern 5 — overwrite `FollowerState.position`, do NOT introduce any new state variable that accumulates error over time. The damper has only `position` and `velocity`; no integral.
**Warning signs:** Any new field on Phase 5 stages that looks like `_integral_*`, `_accum_*`, `_error_sum_*` — fail the review.

### Pitfall 3: Damper-vs-deadband interaction creating a discontinuity
**What goes wrong:** Engineer freezes the damper during deadband (`if deadband_active: return self._last_emitted` BEFORE damper.step). When the deadband eventually exits, the damper has stale state and jumps.
**Why it happens:** Misreading "deadband suppresses motor commands" as "deadband freezes the controller".
**How to avoid:** Pattern 4 — `damper.step()` is ALWAYS called; only the emission is gated. The damper continues integrating the target.
**Warning signs:** Visible camera jerk at deadband exit; integration test shows angle "leap" after a long deadband window.

### Pitfall 4: Wrong dt source on None-upstream / re-acquire
**What goes wrong:** After a None-upstream gap, the next non-None tick computes `dt = now_ns - self._last_upstream_ts_ns` where `_last_upstream_ts_ns` is from before the gap — `dt` becomes huge (e.g. 5 s). The damper takes one step toward target with dt=5s, which (per Holden closed form) reaches target in one frame, defeating the smoothing.
**Why it happens:** The dt rule is ambiguous if not specified.
**How to avoid:** **Canonical dt rule (locked here):**
- On the **first non-None upstream after a None gap**, the damper is RE-SEEDED at the new target with velocity 0.0; dt is irrelevant for the seed step. Subsequent steps compute dt from the CURRENT upstream's `timestamp_ns` minus the PREVIOUS upstream's `timestamp_ns` (NOT `now_ns`).
- During None ticks, no damper step is taken; the held FollowerState is emitted.
- The previous-upstream timestamp is updated only on non-None ticks.
**Warning signs:** Step-response test shows non-monotonic position right after a re-acquisition gap; framing target snaps instead of easing in.

### Pitfall 5: `now_ns` vs `timestamp_ns` confusion
**What goes wrong:** Stage uses `now_ns` for some math and `subject.timestamp_ns` for others. The two diverge slightly because `now_ns` is when the orchestrator called `consume()`, while `subject.timestamp_ns` is when the frame was captured.
**Why it happens:** Two sources of "time" in the API.
**How to avoid:** **Locked rule:**
- `dt_sec` between non-None upstreams: use `current.timestamp_ns - previous.timestamp_ns` (frame-derived; deterministic across pipeline reorderings).
- `timestamp_ns` field on emitted DTO: use `now_ns` (the tick time; lets downstream stages compute their own dt with the same convention).
- Hysteresis duration check: use `now_ns - first_crossing_ts_ns` where `first_crossing_ts_ns` was captured FROM `now_ns` at the crossing frame.
- All MotionState / FramingTarget / MotorCommand `timestamp_ns` = the `now_ns` argument of the call that emitted them.
**Warning signs:** Tests that pass on a fixed `now_ns == subject.timestamp_ns` schedule but fail when the test injects a small clock skew between the two.

### Pitfall 6: Initial-state seeding semantics on re-acquisition
**What goes wrong:** Subject lock loss → 5 s of None-upstream (HOLDING + LOST in Phase 4) → re-acquire at a position different from where the framer's damper held. If the framer/controller seeds with **zero velocity** but at the **held position**, the next damper step sweeps from held → new_target slowly (correct). If the framer carries forward the **last-known velocity** from before the gap, the damper accelerates wrong way for one step.
**How to avoid:** **Locked rule:** on re-acquisition (first non-None upstream after a None gap), seed `FollowerState(position=current_target, velocity=0.0)` — **carry forward neither velocity nor position from before the gap**. CONTEXT.md Area 3: "Initial state seeded on first non-None upstream — `FollowerState(position=current_target, velocity=0.0)`; **no warmup transient toward zero**". The "no warmup" clause means we seed at-target, not at zero.
**Subtlety:** "First non-None upstream" must be detected by `self._state is None`. After a None-gap, `self._state` was held (NOT cleared) — so detection of "re-acquisition" is by `motion.intent != "indeterminate"` AND the previous tick was indeterminate. **Recommended:** track a `_was_indeterminate_last_tick: bool` or check `self._last_upstream_ts_ns is None` post-reset.
**Cleanest:** clear `self._state = None` on every "indeterminate" or None-upstream tick. Then "first non-None" is exactly `self._state is None`. This costs nothing because the state is just a 16-byte frozen dataclass.

### Pitfall 7: dispatcher state update on a None upstream
**What goes wrong:** `decide(None, now_ns)` updates `_last_emit_ts_ns` to `now_ns`. After a long upstream gap, the next real angle arrives and the interval-since-last is suddenly very small, gating the emission.
**How to avoid:** CONTEXT-locked Area 4: None upstream → return None and DO NOT update state. Pattern 7 sketch shows the early-return.
**Warning signs:** Test that interleaves None and non-None ticks shows the first post-None emission being suppressed by the interval gate.

### Pitfall 8: `await asyncio.sleep(0)` cargo-cult inside consume()
**What goes wrong:** Engineer adds `await asyncio.sleep(0)` to "yield to the event loop" inside an `async def consume()`. Phase 5 stages do no I/O — adding a sleep adds a context switch with zero benefit.
**How to avoid:** `async def consume()` is async only because Phase 4 BL-01 made every consume async for orchestrator uniformity. Don't await anything inside Phase 5 stages — they're synchronous transforms wearing async clothing.
**Warning signs:** Any `await` keyword inside a Phase 5 `consume()` body.

### Pitfall 9: hypothesis strategy too broad → discovers unrelated invariants
**What goes wrong:** A hypothesis strategy that generates `vx` floats from `-1.0` to `1.0` finds borderline cases far outside the configured `motion_threshold_norm_per_sec`, causing tests to assert behavior that depends on the threshold value (which the strategy can't know).
**How to avoid:** Constrain strategies to ranges around the Config thresholds:
```python
@given(vx=st.floats(
    min_value=config.motion_threshold_norm_per_sec * 0.9,
    max_value=config.motion_threshold_norm_per_sec * 1.1,
))
```
The "borderline" generator should chatter around the threshold, not span [-1, 1].
**Warning signs:** Hypothesis shrinks to a value that's clearly outside the system's design range and you find yourself special-casing it.

### Pitfall 10: dt from `now_ns - last_now_ns` instead of from the frame timestamp
**What goes wrong:** Stage uses `now_ns - self._last_now_ns` for damper dt. If the orchestrator's `now_ns` clock is `perf_counter_ns()` and the upstream subject's `timestamp_ns` is also `perf_counter_ns()` (Phase 3 uses it for camera grab), then for a normal pipeline they're equal. But for a test that injects synthetic timestamps (e.g., `timestamp_ns = step_index * 33_333_333`), `now_ns` and `subject.timestamp_ns` may diverge — using `now_ns` makes the test's notion of dt different from production's.
**How to avoid:** Pitfall 5's "Locked rule" — dt comes from upstream timestamps when both are non-None; never from `now_ns` differences across calls.
**Warning signs:** Production behavior diverges from test fixture behavior on the same scripted trajectory.

## Runtime State Inventory

> Phase 5 is greenfield (new modules, no rename / refactor / migration). Section omitted.

## Code Examples

### Pattern A: MotionAnalyzer skeleton (verified against existing `consume()` shape from Phase 4)

```python
# Source: synthesizes Phase 4 subject_tracker.py:161 consume() signature with CONTEXT.md Area 1 + 2.
"""pastor_tracker/intent/motion_analyzer.py"""
from __future__ import annotations

from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotionIntent, MotionState, TrackedSubject

_NS_PER_SEC: Final[float] = 1_000_000_000.0


class MotionAnalyzer:
    """Sustained-velocity + dwell hysteresis classifier (INTENT-01, INTENT-02)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="motion_analyzer")
        self._first_right_crossing_ts_ns: int | None = None
        self._first_left_crossing_ts_ns: int | None = None
        self._dwell_start_ts_ns: int | None = None
        self._current_intent: MotionIntent = "indeterminate"

    @property
    def current_intent(self) -> MotionIntent:
        return self._current_intent

    async def consume(
        self, subject: TrackedSubject | None, now_ns: int,
    ) -> MotionState | None:
        if subject is None:
            return self._handle_none(now_ns)
        vx = subject.velocity_x_norm_per_sec
        intent = self._classify(vx, subject.timestamp_ns)
        if intent != self._current_intent:
            self._logger.info(
                "intent_change",
                old=self._current_intent,
                new=intent,
                vx=vx,
            )
            self._current_intent = intent
        return MotionState(
            intent=intent,
            sustained_velocity_x_norm_per_sec=vx,
            timestamp_ns=now_ns,
        )

    def _handle_none(self, now_ns: int) -> MotionState:
        self._first_right_crossing_ts_ns = None
        self._first_left_crossing_ts_ns = None
        self._dwell_start_ts_ns = None
        if self._current_intent != "indeterminate":
            self._logger.info(
                "intent_change",
                old=self._current_intent,
                new="indeterminate",
                reason="upstream_none",
            )
        self._current_intent = "indeterminate"
        return MotionState(
            intent="indeterminate",
            sustained_velocity_x_norm_per_sec=0.0,
            timestamp_ns=now_ns,
        )

    def _classify(self, vx: float, now_ns: int) -> MotionIntent:
        move_thr = self._config.motion_threshold_norm_per_sec
        dwell_thr = self._config.dwell_threshold_norm_per_sec
        # Update right-crossing timer
        if vx > move_thr:
            if self._first_right_crossing_ts_ns is None:
                self._first_right_crossing_ts_ns = now_ns
        else:
            self._first_right_crossing_ts_ns = None
        # Update left-crossing timer
        if vx < -move_thr:
            if self._first_left_crossing_ts_ns is None:
                self._first_left_crossing_ts_ns = now_ns
        else:
            self._first_left_crossing_ts_ns = None
        # Update dwell timer
        if abs(vx) < dwell_thr:
            if self._dwell_start_ts_ns is None:
                self._dwell_start_ts_ns = now_ns
        else:
            self._dwell_start_ts_ns = None
        # Sustained checks (move overrides dwell — by physical priority)
        hyst_ns = int(self._config.motion_hysteresis_sec * _NS_PER_SEC)
        if (
            self._first_right_crossing_ts_ns is not None
            and now_ns - self._first_right_crossing_ts_ns >= hyst_ns
        ):
            return "moving_right"
        if (
            self._first_left_crossing_ts_ns is not None
            and now_ns - self._first_left_crossing_ts_ns >= hyst_ns
        ):
            return "moving_left"
        dwell_ns = int(self._config.dwell_duration_sec * _NS_PER_SEC)
        if (
            self._dwell_start_ts_ns is not None
            and now_ns - self._dwell_start_ts_ns >= dwell_ns
        ):
            return "dwelling"
        # Sustained crossing not yet achieved — hold previous intent
        return self._current_intent
```

### Pattern B: Framer skeleton

```python
# Source: applies Pattern 2 + Pattern 3 + Pitfall 6 (re-acquire seeding).
"""pastor_tracker/intent/framer.py"""
from __future__ import annotations

from dataclasses import replace
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.damping import CriticallyDampedFollower, FollowerState
from pastor_tracker.core.types import FramingTarget, MotionIntent, MotionState

_NS_PER_SEC: Final[float] = 1_000_000_000.0
_TARGET_LEFT_THIRD: Final[float] = 1.0 / 3.0
_TARGET_RIGHT_THIRD: Final[float] = 2.0 / 3.0
_TARGET_CENTER: Final[float] = 0.5
_NORM_MIN: Final[float] = 0.0
_NORM_MAX: Final[float] = 1.0


class IntentError(Exception):
    """Raised when an unhandled MotionIntent reaches the dispatch."""


class Framer:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="framer")
        self._damper = CriticallyDampedFollower(
            time_constant_sec=config.framing_time_constant_sec,
        )
        self._state: FollowerState | None = None
        self._last_upstream_ts_ns: int | None = None
        self._current_target_x_normalized: float | None = None

    @property
    def current_target_x_normalized(self) -> float | None:
        return self._current_target_x_normalized

    async def consume(
        self, motion: MotionState | None, now_ns: int,
    ) -> FramingTarget | None:
        if motion is None or motion.intent == "indeterminate":
            return self._hold(now_ns)
        target = self._intent_to_target(motion.intent)
        # _intent_to_target only returns None for "indeterminate", filtered above
        assert target is not None, "unreachable: indeterminate filtered above"
        # Re-acquisition path — seed at-target (Pitfall 6)
        if self._state is None:
            self._state = FollowerState(position=target, velocity=0.0)
            self._last_upstream_ts_ns = motion.timestamp_ns
            self._current_target_x_normalized = target
            self._logger.debug("framer_seeded", position=target, intent=motion.intent)
            return FramingTarget(
                target_x_normalized=target,
                timestamp_ns=now_ns,
            )
        dt_sec = self._compute_dt_sec(motion.timestamp_ns)
        self._state = self._damper.step(self._state, target=target, dt=dt_sec)
        self._last_upstream_ts_ns = motion.timestamp_ns
        clamped = min(_NORM_MAX, max(_NORM_MIN, self._state.position))
        self._current_target_x_normalized = clamped
        return FramingTarget(target_x_normalized=clamped, timestamp_ns=now_ns)

    def _hold(self, now_ns: int) -> FramingTarget | None:
        if self._state is None:
            return None
        return FramingTarget(
            target_x_normalized=min(_NORM_MAX, max(_NORM_MIN, self._state.position)),
            timestamp_ns=now_ns,
        )

    @staticmethod
    def _intent_to_target(intent: MotionIntent) -> float | None:
        match intent:
            case "moving_right":
                return _TARGET_LEFT_THIRD
            case "moving_left":
                return _TARGET_RIGHT_THIRD
            case "dwelling":
                return _TARGET_CENTER
            case "indeterminate":
                return None
            case _:
                raise IntentError(
                    f"unhandled MotionIntent: {intent!r}"
                )

    def _compute_dt_sec(self, current_upstream_ts_ns: int) -> float:
        if self._last_upstream_ts_ns is None:
            # Should not happen after first seed; safety floor
            return 1.0 / max(self._config.capture_fps, 1)
        delta_ns = current_upstream_ts_ns - self._last_upstream_ts_ns
        if delta_ns <= 0:
            return 1.0 / max(self._config.capture_fps, 1)
        return float(delta_ns) / _NS_PER_SEC
```

### Pattern C: PanController skeleton (clamp + deadband)

```python
# Source: applies Pattern 3, 4, 5; reuses core/geometry.
"""pastor_tracker/control/pan_controller.py"""
from __future__ import annotations

from dataclasses import replace
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.damping import CriticallyDampedFollower, FollowerState
from pastor_tracker.core.geometry import normalized_x_to_angle_deg
from pastor_tracker.core.types import FramingTarget

_NS_PER_SEC: Final[float] = 1_000_000_000.0


class ControlError(Exception):
    pass


class PanController:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="pan_controller")
        self._damper = CriticallyDampedFollower(
            time_constant_sec=config.pan_time_constant_sec,
        )
        self._state: FollowerState | None = None
        self._last_upstream_ts_ns: int | None = None
        self._last_emitted_angle_deg: float | None = None
        self._current_angle_deg: float | None = None

    @property
    def current_angle_deg(self) -> float | None:
        return self._current_angle_deg

    async def consume(
        self, target: FramingTarget | None, now_ns: int,
    ) -> float | None:
        if target is None:
            return self._hold()
        angle_deg = normalized_x_to_angle_deg(
            target.target_x_normalized,
            self._config.camera_horizontal_fov_deg,
        )
        if self._state is None:
            # Re-acquire seed (Pitfall 6)
            self._state = FollowerState(position=angle_deg, velocity=0.0)
            self._last_upstream_ts_ns = target.timestamp_ns
            self._last_emitted_angle_deg = angle_deg
            self._current_angle_deg = angle_deg
            self._logger.debug("pan_controller_seeded", angle_deg=angle_deg)
            return angle_deg
        dt_sec = self._compute_dt_sec(target.timestamp_ns)
        new_state = self._damper.step(self._state, target=angle_deg, dt=dt_sec)
        delta = new_state.position - self._state.position
        max_delta = self._config.pan_max_velocity_deg_per_sec * dt_sec
        if abs(delta) > max_delta:
            clipped = max(-max_delta, min(max_delta, delta))
            new_state = replace(new_state, position=self._state.position + clipped)
            self._logger.debug(
                "pan_clamped",
                unclamped_delta_deg=delta,
                clamped_delta_deg=clipped,
                dt_sec=dt_sec,
            )
        self._state = new_state
        self._last_upstream_ts_ns = target.timestamp_ns
        new_angle = new_state.position
        # Emission deadband (Pattern 4)
        assert self._last_emitted_angle_deg is not None
        if abs(new_angle - self._last_emitted_angle_deg) < self._config.pan_deadband_deg:
            self._current_angle_deg = self._last_emitted_angle_deg
            return self._last_emitted_angle_deg
        self._last_emitted_angle_deg = new_angle
        self._current_angle_deg = new_angle
        return new_angle

    def _hold(self) -> float | None:
        return self._last_emitted_angle_deg

    def _compute_dt_sec(self, current_upstream_ts_ns: int) -> float:
        if self._last_upstream_ts_ns is None:
            return 1.0 / max(self._config.capture_fps, 1)
        delta_ns = current_upstream_ts_ns - self._last_upstream_ts_ns
        if delta_ns <= 0:
            return 1.0 / max(self._config.capture_fps, 1)
        return float(delta_ns) / _NS_PER_SEC
```

### Pattern D: CommandDispatcher (synchronous decide)

```python
# Source: Pattern 7 + CONTEXT.md Area 4.
"""pastor_tracker/control/command_dispatcher.py"""
from __future__ import annotations

from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand

_NS_PER_MS_INT: Final[int] = 1_000_000


class CommandDispatcher:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="command_dispatcher")
        self._last_emitted_angle_deg: float | None = None
        self._last_emit_ts_ns: int | None = None

    @property
    def last_emitted_angle_deg(self) -> float | None:
        return self._last_emitted_angle_deg

    @property
    def last_emit_ts_ns(self) -> int | None:
        return self._last_emit_ts_ns

    def decide(
        self, angle_deg: float | None, now_ns: int,
    ) -> MotorCommand | None:
        if angle_deg is None:
            return None
        if self._last_emitted_angle_deg is None or self._last_emit_ts_ns is None:
            self._last_emitted_angle_deg = angle_deg
            self._last_emit_ts_ns = now_ns
            self._logger.debug(
                "command_emitted", angle_deg=angle_deg, delta_deg=0.0, interval_ms=0.0,
            )
            return MotorCommand(target_angle_deg=angle_deg, timestamp_ns=now_ns)
        delta_deg = abs(angle_deg - self._last_emitted_angle_deg)
        interval_ns = now_ns - self._last_emit_ts_ns
        min_interval_ns = self._config.command_min_interval_ms * _NS_PER_MS_INT
        if delta_deg > self._config.command_min_delta_deg and interval_ns >= min_interval_ns:
            self._last_emitted_angle_deg = angle_deg
            self._last_emit_ts_ns = now_ns
            self._logger.debug(
                "command_emitted",
                angle_deg=angle_deg,
                delta_deg=delta_deg,
                interval_ms=interval_ns / _NS_PER_MS_INT,
            )
            return MotorCommand(target_angle_deg=angle_deg, timestamp_ns=now_ns)
        return None
```

### Pattern E: Hypothesis strategies for trajectory tests

```python
# Source: synthesizes test_damping.py:32 hypothesis usage with Pitfall 9 constraint.
import hypothesis.strategies as st
from hypothesis import given, settings

# A scalar vx around the move threshold
def vx_borderline_strategy(threshold: float) -> st.SearchStrategy[float]:
    return st.floats(
        min_value=threshold * 0.5,
        max_value=threshold * 1.5,
        allow_nan=False,
        allow_infinity=False,
    )

# A short ramp across the threshold (used for sustained-crossing tests)
def ramp_strategy(threshold: float, total_sec: float, dt_sec: float) -> st.SearchStrategy[list[float]]:
    n = int(total_sec / dt_sec)
    return st.lists(
        st.floats(min_value=0.0, max_value=threshold * 2.0),
        min_size=n, max_size=n,
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| PID with integral anti-windup clamping | Critically-damped 2nd-order follower with output position clamp | Phase 1 architectural decision (PROJECT.md Key Decisions) | No overshoot, no oscillation, no integral windup possible |
| EMA on velocity for hysteresis | Sustained-crossing duration timer | Phase 1 / PROJECT.md "EMA on detection stream" forbidden | Lag-free; thrash-free at borderline thresholds |
| AsyncIterator stage surface | `consume(upstream, now_ns)` per-tick call | Phase 4 BL-01 (commit b9fe091 referenced in git log) | No queue deadlocks; deterministic test surface |
| Frame-count-based hysteresis | Duration-based (motion_hysteresis_sec, dwell_duration_sec) | CONTEXT.md Area 2 | Decouples hysteresis from frame rate |
| Per-call SharedMemory or other heavy IPC | (N/A — Phase 5 is pure-core only, no IPC) | — | — |

**Deprecated/outdated:**
- PID variants — explicitly forbidden by PROJECT.md and CLAUDE.md
- MediaPipe — superseded by YOLO11-pose in Phase 4
- EMA smoothing on detection stream — replaced by Kalman in Phase 4
- AsyncIterator surfaces between stages — Phase 4 BL-01 simplification removed them

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Critically-damped follower's `step()` is correct under sequential FollowerState replacement (i.e., overwriting `position` mid-trajectory keeps subsequent steps stable) | Pattern 5 | LOW — Holden's closed form is unconditionally stable for any (position, velocity, target) tuple per `core/damping.py` docstring; the property test in Phase 1 doesn't explicitly exercise externally-mutated state, but the formula has no path-dependent term. If a future stage shows oscillation post-clamp, add a property test |
| A2 | The "no warmup transient" rule in CONTEXT Area 3 ("seed at current_target with velocity 0.0") combined with the deadband emission keeps the first-frame emitted angle equal to the converted target angle | Pitfall 6, Pattern C | LOW — direct algebraic consequence; the emission-deadband sees `|angle - last_emitted| = 0` on the seed step (since `last_emitted = angle`), so no jitter |
| A3 | Hypothesis 6.152 + asyncio_mode=auto + pytest-asyncio is happy with `@given` on async test functions for the borderline-chatter property | Pattern 8, Validation Architecture | LOW — Phase 4 `test_subject_tracker_lock.py:36-58` already uses `@given(...)` on a sync function in an asyncio-flavored test file; sync property tests are the safer pattern. Phase 5 should keep hypothesis tests sync (driving the analyzer via `asyncio.run(tracker.consume(...))` in the body) — not async |
| A4 | structlog event names `intent_change`, `framing_target_change`, `pan_clamped`, `command_emitted` are not already used by Phase 1-4 stages with conflicting field shapes | Pattern 9 | LOW — Phase 4 uses `lock_acquired`, `lock_loss`, `lock_holding`, `lock_recovered_from_hold`, `low_conf_detection_rejected`. No collision |
| A5 | `motion_threshold_norm_per_sec` and `dwell_threshold_norm_per_sec` are independent (a moment can satisfy `|vx| > motion_thr` without satisfying `|vx| > dwell_thr` since `0.08 > 0.03`) AND the move-priority over dwell rule (move beats dwell when both timers are running) is the intended tiebreaker | Pattern A | LOW — config defaults: `0.08 > 0.03`, so a `vx` between them satisfies neither move nor dwell threshold (both timers run independently; whichever sustains first "wins"); `vx > 0.08` sets the move timer AND fails the dwell threshold (`|vx| > 0.03` resets dwell timer); `vx ≈ 0.05` runs the dwell timer alone. The classifier in Pattern A correctly handles all three regions |
| A6 | The damper's `FollowerState` clamp to `[0, 1]` in `Framer` does not cause divergence in subsequent steps | Pattern B note | LOW — same argument as A1; clamping a position is functionally equivalent to a momentary external constraint, which the closed form handles naturally |

**If this table is empty:** N/A — six low-risk assumptions documented for plan-checker visibility.

## Open Questions

### 1. Should the damper state be cleared on every "indeterminate" tick, or held?

CONTEXT.md says: "Hold-on-None semantics — when upstream is None or `MotionIntent='indeterminate'`, the damper holds its current `FollowerState` and emits the held position; **no advance, no reset**." That answers the immediate question (hold).

But subtle re-acquisition behavior depends on whether `Framer._state` is **cleared** on indeterminate to make `_state is None` the seed-condition gate. Two designs:

| Design | Behavior on re-acquire after indeterminate gap |
|--------|------------------------------------------------|
| Hold state but re-seed when intent changes | New target requires the seed; need a second flag (e.g. `_was_indeterminate_last_tick`) |
| Clear state on indeterminate, seed on first non-None | `_state is None` IS the seed gate. Cleaner but loses the "emit held position" info during a long indeterminate gap |

**Recommendation:** Hold the state, AND emit the held position during indeterminate (Pattern B `_hold()`). On the FIRST tick where `motion.intent != "indeterminate"` after a sustained indeterminate run, re-seed at the new target — this requires checking `motion.intent != _last_intent` or maintaining a small flag `_was_indeterminate_last_tick: bool`. **Decision is at Claude's discretion (CONTEXT.md says discretion on internal helpers).** The cleanest implementation is to use `self._state is None` as the seed gate AND clear state on indeterminate ticks, accepting that during long indeterminate gaps the framer emits None (no held position). Plan should make this explicit.

### 2. Should hysteresis sustain duration be checked at the **threshold-crossing instant** or **continuously each frame**?

A subject crosses `vx > 0.08` for 290 ms, drops below for one frame, crosses back. With a "reset on un-cross" rule, the timer restarts; with a "lenient" rule, the timer keeps running. CONTEXT-locked: **"reset the corresponding crossing timer the moment the threshold is uncrossed"** (Area 2). Strict reset is therefore the rule. This is the right behavior — borderline chatter reproduces this exact pattern and we want the chatter case to NEVER flip intent.

### 3. What's the analytic upper bound on dispatcher emissions over a synthetic ramp?

Given a ramp of duration `T` at framerate `F` (so `N = T*F` frames), and dispatcher gates `min_delta_deg = 0.2°` AND `min_interval_ms = 50 ms`:
- **Interval gate alone:** at most `floor(T * 1000 / min_interval_ms) = T * 20` emissions.
- **Delta gate alone:** at most `floor(angular_range_traveled / min_delta_deg)` emissions.
- **Combined (AND):** at most `min(T * 1000 / min_interval_ms, angular_range / min_delta_deg)` emissions.

For a ramp of `30°/s` for `1 s` (full velocity-clamp ride), angular range = 30°, so delta-gate bound = `30 / 0.2 = 150`; interval-gate bound = `1000 / 50 = 20`. The AND of the two = `min(20, 150) = 20`.

This is exactly the **success-criterion #4** assertion: `count <= total_sec * 1000 / command_min_interval_ms`. Test should compute the bound from `Config` (no hardcoded 20).

### 4. How do we handle the Framer/PanController state interaction when the damper has reached the deadband-floor — does it ever escape?

Scenario: framer's target has been stable at 0.5 for many ticks; pan controller's damped angle has converged to within 0.1° of target; emission stays at the last_emitted forever. This is correct — the deadband is exactly the "don't twitch the motor for sub-pixel framing changes" guarantee. The damper's velocity is approaching zero, so the position approaches a fixed point; the deadband just stops emitting.

The ONLY concern is if something nudges the angle JUST enough to exit the deadband — and that's by design (any sustained intent change exceeds the deadband within ~1 time-constant).

### 5. Should we test the Framer + PanController as an integrated unit (composition test) or only in isolation?

CONTEXT-locked TEST-03 lists three test files: `motion_analyzer.py`, `framer.py`, `pan_controller.py`. The dispatcher gets a fourth file. **An integration test (analyzer→framer→controller→dispatcher composition over a scripted trajectory) is NOT required by TEST-03 but is highly recommended** as a sanity check (Phase 4 has `test_perception_e2e.py`; Phase 5 should have `test_intent_control_e2e.py` or similar). Phase 6 will own the orchestrator-driven E2E test, but a same-thread, no-real-Arduino composition test in Phase 5 catches integration bugs early.

**Recommendation to planner:** Add a 5th test file `test_intent_control_pipeline.py` running the four stages back-to-back over the `dwell_then_walk` and `borderline_chatter` fixtures. Sub-100-line file; high ROI.

### 6. Does pytest-asyncio play well with `asyncio.run(...)` calls inside non-async test functions?

Phase 4 tests use this pattern: `asyncio.run(tracker.consume(...))` from a sync test body (verified at `test_subject_tracker_lock.py:67`). pytest-asyncio's `asyncio_mode = "auto"` (pyproject.toml line 99) doesn't interfere with explicit `asyncio.run()` calls in sync tests. Phase 5 should mirror exactly. **No new investigation needed.**

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All Phase 5 modules | ✓ | 3.12 (pyproject `requires-python = ">=3.12,<3.13"`) | — |
| `pydantic` | DTOs, Config | ✓ | `>=2.13,<3.0` (pyproject) | — |
| `structlog` | Logging | ✓ | `>=24.4,<26.0` | — |
| `pytest` | Test runner | ✓ (dev group) | `>=8.4,<9.0` | — |
| `pytest-asyncio` | Async tests | ✓ (dev group) | `>=0.26,<2.0` | — |
| `hypothesis` | Property tests | ✓ (dev group) | `>=6.152,<7.0` | — |
| `pastor_tracker.core.damping` | Both stages | ✓ shipped Phase 1 | — | — |
| `pastor_tracker.core.geometry` | PanController | ✓ shipped Phase 1 | — | — |
| `pastor_tracker.core.types` | All stages | ✓ shipped Phase 1 | — | — |
| `pastor_tracker.config` | All stages | ✓ shipped Phase 1 | — | — |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

[VERIFIED: pyproject.toml read 2026-05-05; all dependencies present in lockfile via `uv lock` per Phase 1 Plan 01-01.]

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest >=8.4,<9.0` + `pytest-asyncio >=0.26,<2.0` + `hypothesis >=6.152,<7.0` |
| Config file | `pyproject.toml [tool.pytest.ini_options]` (testpaths=["tests"], asyncio_mode="auto", strict-markers, strict-config, filterwarnings=error) |
| Quick run command | `cd pastor_tracker && uv run pytest tests/test_motion_analyzer.py tests/test_framer.py tests/test_pan_controller.py tests/test_command_dispatcher.py -x` |
| Full suite command | `cd pastor_tracker && uv run pytest -x --strict-markers` |
| Coverage command | `cd pastor_tracker && uv run pytest --cov=src/pastor_tracker/intent --cov=src/pastor_tracker/control --cov-branch --cov-report=term-missing` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INTENT-01 | Sustained `vx > +motion_thr` for ≥ `motion_hysteresis_sec` flips to `moving_right` | unit | `pytest tests/test_motion_analyzer.py::test_sustained_right_flips_intent -x` | Wave 0 (new file) |
| INTENT-01 | Sustained `vx < -motion_thr` for ≥ `motion_hysteresis_sec` flips to `moving_left` | unit | `pytest tests/test_motion_analyzer.py::test_sustained_left_flips_intent -x` | Wave 0 |
| INTENT-01 | Sustained `|vx| < dwell_thr` for ≥ `dwell_duration_sec` flips to `dwelling` | unit | `pytest tests/test_motion_analyzer.py::test_sustained_dwell_flips_intent -x` | Wave 0 |
| INTENT-02 | Borderline chatter NEVER flips intent | unit + hypothesis | `pytest tests/test_motion_analyzer.py::test_borderline_chatter_no_thrash -x` | Wave 0 |
| INTENT-02 | Threshold un-cross resets the crossing timer | unit | `pytest tests/test_motion_analyzer.py::test_un_cross_resets_timer -x` | Wave 0 |
| INTENT-02 | None-upstream resets ALL timers + emits indeterminate | unit | `pytest tests/test_motion_analyzer.py::test_none_upstream_emits_indeterminate -x` | Wave 0 |
| INTENT-03 | `moving_right` → target 0.333 (1/3) | unit | `pytest tests/test_framer.py::test_moving_right_yields_left_third -x` | Wave 0 |
| INTENT-03 | `moving_left` → target 0.667 (2/3) | unit | `pytest tests/test_framer.py::test_moving_left_yields_right_third -x` | Wave 0 |
| INTENT-03 | `dwelling` → target 0.5 | unit | `pytest tests/test_framer.py::test_dwelling_yields_center -x` | Wave 0 |
| INTENT-03 | `indeterminate` → no target (None or held) | unit | `pytest tests/test_framer.py::test_indeterminate_holds_or_returns_none -x` | Wave 0 |
| INTENT-04 | Step-response (left→right third) damped by stage-1 τ; no overshoot, monotonic | unit | `pytest tests/test_framer.py::test_step_response_no_overshoot -x` | Wave 0 |
| INTENT-04 | Damper state held during None upstream | unit | `pytest tests/test_framer.py::test_hold_during_none_upstream -x` | Wave 0 |
| CTRL-01 | Step-response (full FOV ramp) damped by stage-2 τ; no overshoot | unit | `pytest tests/test_pan_controller.py::test_step_response_no_overshoot -x` | Wave 0 |
| CTRL-01 | FOV conversion: target=0.5 maps to angle=0.0 | unit | `pytest tests/test_pan_controller.py::test_center_target_maps_to_zero_angle -x` | Wave 0 |
| CTRL-02 | `|angle - last_emitted| < pan_deadband_deg` returns previous angle | unit | `pytest tests/test_pan_controller.py::test_deadband_suppresses_small_changes -x` | Wave 0 |
| CTRL-02 | Damper continues stepping during deadband (verified by deadband-then-exit emission catches up smoothly) | unit | `pytest tests/test_pan_controller.py::test_deadband_does_not_freeze_damper -x` | Wave 0 |
| CTRL-03 | Per-step `|delta_deg| <= pan_max_velocity_deg_per_sec * dt_sec` | unit | `pytest tests/test_pan_controller.py::test_velocity_clamp_caps_step_size -x` | Wave 0 |
| CTRL-03 | After clamp, damper FollowerState.position == clamped position (anti-windup) | unit | `pytest tests/test_pan_controller.py::test_velocity_clamp_overwrites_state -x` | Wave 0 |
| CTRL-04 | Δ ≤ command_min_delta_deg → no emission | unit | `pytest tests/test_command_dispatcher.py::test_small_delta_suppressed -x` | Wave 0 |
| CTRL-04 | interval < command_min_interval_ms → no emission even if Δ large | unit | `pytest tests/test_command_dispatcher.py::test_short_interval_suppressed -x` | Wave 0 |
| CTRL-04 | First call always emits | unit | `pytest tests/test_command_dispatcher.py::test_first_call_always_emits -x` | Wave 0 |
| CTRL-04 | None upstream returns None and does NOT update state | unit | `pytest tests/test_command_dispatcher.py::test_none_does_not_update_state -x` | Wave 0 |
| CTRL-04 | Emission count over synthetic ramp ≤ analytic bound (Open Q 3) | unit | `pytest tests/test_command_dispatcher.py::test_ramp_emission_count_bounded -x` | Wave 0 |
| TEST-03 | All Phase 5 tests use real `CriticallyDampedFollower` and real `normalized_x_to_angle_deg` | repo policy | `pytest -x` runs cleanly with no `unittest.mock` import in `tests/test_motion_analyzer.py`, `test_framer.py`, `test_pan_controller.py`, `test_command_dispatcher.py` | Wave 0 enforces |

### Sampling Rate
- **Per task commit:** `cd pastor_tracker && uv run pytest tests/test_motion_analyzer.py tests/test_framer.py tests/test_pan_controller.py tests/test_command_dispatcher.py -x`
- **Per wave merge:** `cd pastor_tracker && uv run pytest -x` (full suite)
- **Phase gate:** Full suite green AND `uv run pytest --cov=src/pastor_tracker/intent --cov=src/pastor_tracker/control --cov-branch --cov-report=term-missing` shows 100% line+branch on hysteresis classifier + deadband+clamp branches + dispatcher gate; ≥90% line on framer.py + pan_controller.py

### Wave 0 Gaps
- [ ] `pastor_tracker/tests/test_motion_analyzer.py` — covers INTENT-01, INTENT-02
- [ ] `pastor_tracker/tests/test_framer.py` — covers INTENT-03, INTENT-04
- [ ] `pastor_tracker/tests/test_pan_controller.py` — covers CTRL-01, CTRL-02, CTRL-03
- [ ] `pastor_tracker/tests/test_command_dispatcher.py` — covers CTRL-04
- [ ] (recommended, per Open Question 5) `pastor_tracker/tests/test_intent_control_pipeline.py` — composition smoke test
- [ ] `pastor_tracker/tests/fixtures/trajectories.py` — pure helpers (`step`, `ramp`, `dwell_then_walk`, `borderline_chatter`)
- Framework install: not needed (all deps already in pyproject.toml; `uv sync` fetches them)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A — no user-facing auth in pure-core stages |
| V3 Session Management | no | N/A |
| V4 Access Control | no | N/A |
| V5 Input Validation | yes | Pydantic v2 frozen DTOs already validate every numeric range at construction (CORE-01); `Config` validates Phase 5 thresholds at startup (CFG-02). The DTOs received from Phase 4 (`TrackedSubject`) are already validated by `core/types.py:_FrozenModel`; Phase 5 stages receive validated input by contract |
| V6 Cryptography | no | N/A — no secrets, no signing in Phase 5 |
| V7 Error Handling and Logging | yes | structlog JSON only; no PII (the only fields logged are angles and intents, not user-identifying data); ruff `T20` blocks `print()` |
| V8 Data Protection | no | N/A — no persistent storage written by Phase 5 |
| V11 Business Logic | partially | The hysteresis + deadband + dispatcher gate IS the business logic protecting against camera oscillation; tests assert these gates |
| V14 Configuration | yes | All thresholds in `Config` with range validation; out-of-range values fail-fast at startup |

### Known Threat Patterns for {pure-core math + frame-rate-driven controller}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Numerical instability (NaN propagation through damper) | Tampering | `core/damping.py` rejects `dt <= 0.0` and `time_constant_sec <= 0.0`; Phase 5 stages should never feed those values. Add an early `assert dt > 0` guard if the dt computation is non-trivial |
| Deadband-bypass DoS (rapid intent flips trigger emission flood) | Denial of Service | Hysteresis (`motion_hysteresis_sec`, `dwell_duration_sec`) PLUS dispatcher rate limit (`command_min_interval_ms`) cap emission frequency at ≤20 Hz regardless of upstream intent oscillation rate |
| Infinite-recursion / unbounded loop in classifier | DoS | Classifier is straight-line code: timer updates + 3 threshold checks + return; no loops, no recursion |
| Magic-number drift (someone hardcodes 0.08 instead of reading config) | Tampering / supply chain | ruff `PLR2004` rule rejects magic-value-comparison in app code (test files have a per-file ignore, BUT CONTEXT-locked rule says no hardcoded thresholds in test bodies — this must be enforced by code review, not lint). Plan-level pre-merge check: grep test files for the literal `0.08` / `0.3` / `0.03` / `1.5` / `0.4` / `30.0` / `0.2` / `50` outside parameter passing |
| Logger spam saturating disk / observability | DoS | `pan_clamped`, `pan_deadband_suppressed`, `command_emitted`, `command_suppressed_*` events are DEBUG-level only — production runs at INFO and these don't fire (Pattern 9) |

**Threat assessment summary:** Phase 5 has minimal attack surface — pure functions on validated DTOs. The main "security" axis here is **operational integrity** (the damper invariants), which is enforced by Phase 1's property tests and the new Phase 5 tests asserting the same invariants under composition.

## Sources

### Primary (HIGH confidence)
- `D:\System\Documents\PastorTrackingSystem\.planning\phases\05-intent-and-control\05-CONTEXT.md` — locked decisions, all four areas
- `D:\System\Documents\PastorTrackingSystem\.planning\REQUIREMENTS.md` — INTENT-01..04, CTRL-01..04, TEST-03
- `D:\System\Documents\PastorTrackingSystem\.planning\PROJECT.md` — vision, key decisions, forbidden libs
- `D:\System\Documents\PastorTrackingSystem\.planning\ROADMAP.md` — Phase 5 success criteria
- `D:\System\Documents\PastorTrackingSystem\CLAUDE.md` — engineering culture, forbidden patterns
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\core\damping.py` — Holden closed-form follower
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\core\geometry.py` — pinhole conversion
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\core\types.py` — DTOs + `MotionIntent` Literal
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\config.py` — all 11 Phase 5 knobs
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\perception\subject_tracker.py` — Phase 4 BL-01 `consume()` pattern + WR-08 exhaustiveness pattern
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\pyproject.toml` — dependency versions, ruff/mypy/pytest config
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\tests\conftest.py` — `valid_config_dict` fixture
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\tests\test_damping.py` — hypothesis idiom (`@given`/`@settings`/`max_examples`/`deadline=None`)
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\tests\test_subject_tracker_lock.py` — `asyncio.run(consume(...))` from sync test pattern
- `D:\System\Documents\PastorTrackingSystem\.planning\phases\04-perception\04-RESEARCH.md` — research-format precedent + Pitfall structure precedent

### Secondary (MEDIUM confidence)
- Daniel Holden — "Spring-It-On" (theorangeduck.com/page/spring-roll-call) — closed-form critically-damped follower stability properties (cross-referenced with `core/damping.py` docstring)
- Wikipedia "Schmitt trigger" (en.wikipedia.org/wiki/Schmitt_trigger) — hysteresis formulations: dual-threshold OR single-threshold + sustained-duration

### Tertiary (LOW confidence)
- None — all claims in this research are either CITED to in-repo files or to well-known control-engineering canon. The single `[ASSUMED]`-flagged area is "no warmup transient on re-acquisition" semantics interpretation in Pitfall 6, which is a CONTEXT-decision interpretation question, not a fact

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every dependency is already in pyproject.toml and verified imported in current repo HEAD
- Architecture: HIGH — every architectural decision is locked in CONTEXT.md (4 areas, 13 binding decisions); research role is interpretation + edge-case analysis, not exploration
- Pitfalls: HIGH — Pitfalls 1, 4, 5, 6, 7 are direct extrapolations of CONTEXT.md edge-case clauses; Pitfalls 2, 3, 8, 9, 10 are general-engineering or Phase-4-precedent issues
- Test architecture: HIGH — mirrors Phase 4 test discipline 1:1; trajectory fixtures are simple deterministic helpers
- The dt rule (Pitfall 4 + Pitfall 5): MEDIUM — CONTEXT.md does not explicitly choose between `now_ns - last_now_ns` and `current.timestamp_ns - previous.timestamp_ns`; this research recommends the latter for production-test parity, but the planner should confirm or surface to discuss-phase if a different rule is preferred

**Research date:** 2026-05-05
**Valid until:** 2026-06-05 (30 days — Phase 5 design is stable; underlying math is decades-old)

## RESEARCH COMPLETE
