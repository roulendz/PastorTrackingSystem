# Phase 1: Scaffold, Config, Core Math - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning
**Mode:** Auto-generated (smart-discuss infrastructure detection)

<domain>
## Phase Boundary

Establish the project skeleton, fail-fast frozen `Config`, and the pure-core DTOs and math (geometry + critically-damped follower) under property tests — the unit-testable foundation everything else builds on.

**In scope:**
- `pastor_tracker/` package layout (`pyproject.toml`, `uv lock`, pinned deps)
- `ruff` + `mypy --strict` config
- `pytest` + `hypothesis` test wiring
- `structlog` JSON logging at module entry
- Pydantic v2 frozen `Config` with all 23 fields, env+JSON loading, range validation, fail-fast
- `core/types.py` frozen DTOs (`Frame`, `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand`)
- `core/geometry.py` FOV math + normalized↔angle conversions
- `core/damping.py` critically-damped 2nd-order follower
- Property tests on geometry, step-response test on damping (real math, no mocks)

**Out of scope (later phases):**
- Arduino serial driver (Phase 2)
- OBS camera I/O (Phase 3)
- Perception / Kalman / BoT-SORT (Phase 4)
- Motion intent + pan controller + dispatcher (Phase 5)
- Pipeline orchestrator (Phase 6)
- DearPyGui dashboard (Phase 7)
- E2E + ship gates (Phase 8)

**Requirements covered:** SCAF-01..05, CFG-01..04, CORE-01..04, TEST-01, TEST-02, TEST-05.

</domain>

<decisions>
## Implementation Decisions

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md
- Python 3.12, `uv` package manager, `ruff` lint/format, `mypy --strict` — non-negotiable
- Pydantic v2 `frozen=True` for `Config` and DTOs; `BaseSettings` for env+JSON loading
- `structlog` JSON output only — `print()` and bare `except` forbidden by lint policy
- `pytest` + `hypothesis` for tests; mock-free for math
- Critically-damped 2nd-order follower (NOT PID — explicitly forbidden)
- Tiger-style fail-fast: validation errors raised at startup, no silent fallback
- File layout per CLAUDE.md `pastor_tracker/src/pastor_tracker/{core,io,perception,intent,control,ui}` skeleton; only `core/` populated this phase
- Conventional Commits, one logical change per commit
- All public functions annotated, no `Any`, no magic numbers (everything tunable in `Config`)
- ≤2-level conditional nesting; guard clauses + early returns

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion — pure infrastructure phase. Reasonable defaults expected:
- Field ranges for `Config` derived from PROMPT.md and PROJECT.md constraint table
- Geometry/damping module signatures shaped to the typed DTOs in `core/types.py`
- Test file granularity (one `test_*.py` per module under test)
- Logging schema fields chosen to support later debug surface (timestamp, module, level, event, kv pairs)

</decisions>

<code_context>
## Existing Code Insights

### Repo state
- `arduino/stepper_controller/` — shipped PlatformIO firmware v2 (read-only this phase)
- `pastor_tracker/` — does not yet exist; this phase creates it
- `.planning/` — PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md present
- No prior CONTEXT.md (Phase 1 = first phase)

### Reusable assets
None — greenfield Python tree.

### Established patterns
None Python-side. Conventions enforced by CLAUDE.md will be the patterns this phase establishes.

### Integration points
- `Config` field set + ranges must align with PROMPT.md so later phases (heartbeat 200 ms, command throttle 50 ms, FOV calibration, motor clamps `[100..50000]` speed `[50..30000]` accel, etc.) consume them without redefining.
- DTO shapes must match downstream stage signatures: `FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor`.

</code_context>

<specifics>
## Specific Ideas

- `core/geometry.py` round-trips `normalized↔angle` for any FOV in `(0, 180)` — hypothesis property test mandatory.
- `core/damping.py` step response: zero overshoot for time constants `[0.1, 2.0] s` — real math, no mocks (TEST-05).
- `Config` invalid sample (e.g. `pan_max_velocity_deg_per_sec=-1`, `motor_max_speed_steps_per_sec=99`) MUST raise Pydantic `ValidationError` at startup — covered by a dedicated test.
- Lint policy must reject `print(` and bare `except:` / `except Exception: pass` (`ruff` rules: `T20`, `BLE001`, `E722`).

</specifics>

<deferred>
## Deferred Ideas

None — discuss skipped (infrastructure phase). Any non-core surface (serial, camera, perception, control, GUI, pipeline, docs, ship gates) belongs to its own later phase per ROADMAP.

</deferred>
