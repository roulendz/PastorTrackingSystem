---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 05-04-PLAN.md
last_updated: "2026-05-10T09:44:08.167Z"
last_activity: 2026-05-10 -- Phase 6 planning complete
progress:
  total_phases: 8
  completed_phases: 5
  total_plans: 21
  completed_plans: 17
  percent: 81
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-03)

**Core value:** Cinematic, jitter-free auto-tracking of a single primary speaker — no overshoot, no oscillation, no lock-loss, no audible motor jerk.
**Current focus:** Phase 05 — Intent and Control

## Current Position

Phase: 6
Plan: Not started
Status: Ready to execute
Last activity: 2026-05-10 -- Phase 6 planning complete

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**

- Total plans completed: 12
- Average duration: 9 min
- Total execution time: 9 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 1 | 9 min | 9 min |
| 03 | 2 | - | - |
| 05 | 6 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 01 P02 | 7 | 2 tasks | 4 files |
| Phase 01 P03 | 5 | 3 tasks | 6 files |
| Phase 02 P01 | 577 | 3 tasks | 2 files |
| Phase 02 P03 | 12 | 3 tasks | 8 files |
| Phase 03 P02 | 80m | 3 tasks | 6 files |
| Phase 05 P01 | 4 min | 2 tasks | 2 files |
| Phase 05 P02 | 5min | 2 tasks | 3 files |
| Phase 05 P03 | 5min | 2 tasks | 3 files |
| Phase 05 P04 | 13min | 2 tasks | 5 files |
| Phase 05 P05 | 7min | 2 tasks tasks | 3 files files |
| Phase 05 P06 | 12min | 1 tasks | 1 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Phase 1: Pure-core/dirty-edges architecture — `core/` is side-effect-free, fully unit-testable, lands before any I/O
- Phase 1: Critically-damped 2nd-order follower over PID — overshoot/oscillation is incompatible with cinematic feel
- Phase 1: Pydantic v2 frozen `Config` with fail-fast validation — no silent fallbacks
- Phase 2: VID:PID auto-detect over fixed COM port — USB-jack-dependent enumeration on dev machine; `COM6` is fallback only
- Phase 1 / Plan 01: uv hosted in repo-root `.venv` (pip install uv) — pipx unavailable on dev box; bypasses PATH pollution
- Phase 1 / Plan 01: pre-commit `commit-msg` hook installed at parent-repo `.git/hooks/` with `--config pastor_tracker/.pre-commit-config.yaml` — pastor_tracker is a sub-directory, not nested git repo
- Phase 1 / Plan 01: structlog 25.5.0 resolved within `>=24.4,<26.0` — JSON API + format_exc_info processor identical to 24.4
- [Phase ?]: Phase 1 / Plan 02: Tightened [tool.pydantic-mypy] init_forbid_extra+init_typed in pyproject.toml so mypy disallow_any_explicit stays clean for every Pydantic class
- [Phase ?]: Phase 1 / Plan 02: Config has 25 fields (NOT 24) — PROMPT.md ## Config block already includes arduino_ready_timeout_sec; plan narrative arithmetic was stale
- [Phase ?]: Phase 1 / Plan 02: Cross-field invariants enforced via @model_validator(mode='after'); source precedence locked init > env > .env > config.json > defaults via settings_customise_sources
- [Phase ?]: Plan 01-03: ImageArray = npt.NDArray[np.uint8] alias mitigates Pitfall 2 (bare ndarray expands to Any under disallow_any_explicit)
- [Phase ?]: Plan 01-03: Damper uses Holden exact closed-form (pos_new = target + exp(-y*dt)*(j0+j1*dt)), NOT semi-implicit Euler — unconditionally stable for any dt
- [Phase ?]: Plan 01-03: Mixed DTO containers — Frame as dataclass(frozen,slots) for ndarray; other 5 DTOs as Pydantic BaseModel(frozen,extra=forbid) via _FrozenModel base (Pattern 8)
- [Phase ?]: Plan 02-01: ProtocolEvent uses PEP 695 type syntax (ruff UP040)
- [Phase ?]: Plan 02-01: pure parser layer at 100% line+branch coverage; zero serial/threading/asyncio imports in arduino_protocol.py
- [Phase ?]: Plan 02-01: Error.code modelled as ErrorCode | int union for forward-compat (Pitfall 9)
- [Phase ?]: Plan 02-03: latched-error gate runs BEFORE _dispatch_paused gate in send_motor_angle so a faulted+paused state still raises the typed exception (BLOCKER 3)
- [Phase ?]: Plan 02-03: heartbeat task fault-halts on ArduinoError; link is dead, firmware watchdog moot once link faulted
- [Phase ?]: Plan 02-03: _recover catches (asyncio.TimeoutError, asyncio.QueueEmpty), latches WatchdogResetError + FAULTED, RETURNS without re-raising; deterministic surface = next send_*
- [Phase ?]: Plan 02-03: USB-disconnect latches LinkLostError, NOT FirmwareErrorReceived(ErrorCode.NONE); ErrorCode.NONE is firmware-only sentinel per protocol.h:68
- [Phase ?]: ObsCamera mirrors ArduinoMotor: single producer thread + bounded asyncio.Queue + cross-thread bridge via call_soon_threadsafe
- [Phase ?]: Pitfall 7 close-order: stop_event -> thread.join -> source.release -> CLOSED
- [Phase ?]: T-03-03: start() releases source on first-frame timeout BEFORE raising CameraOpenError
- [Phase ?]: Resolution fallback is one-shot inside 2 s warmup window only; never re-promote, never re-fallback
- [Phase ?]: Plan 05-01: Trajectory fixtures parametrized via D-12 (helpers never hardcode Phase-5 thresholds)
- [Phase ?]: Plan 05-01: itertools.pairwise replaces zip with strict=True for monotonicity (RUF007 + length-mismatch fix)
- [Phase ?]: Plan 05-02: MotionAnalyzer single class (162 lines) — no helper module split since CONTEXT.md threshold of ~250 lines not approached
- [Phase ?]: Plan 05-02: intent_change log fields {old, new, vx}; reason=upstream_none on D-04 None-upstream branch only
- [Phase ?]: Plan 05-02: consume() return type MotionState | None mirrors SubjectTracker.consume for Pipeline orchestrator symmetry (D-01)
- [Phase ?]: Plan 05-03: Framer stage-1 damping in normalized-x domain only; degree conversion deferred to PanController (D-05)
- [Phase ?]: Plan 05-03: At-target seed (FollowerState(position=current_target, velocity=0.0)) — no warmup transient toward zero (D-06)
- [Phase ?]: Plan 05-03: Hold-on-None / indeterminate clears full Framer state — clean re-seed at next non-indeterminate motion (D-07 + Pitfall 6)
- [Phase ?]: Plan 05-03: framing_target_change INFO log gated on _last_discrete_target_x_normalized (NOT continuous damper position) — bounded by Plan 02 hysteresis
- [Phase ?]: Plan 05-04: PanController stage-2 damper in DEGREE DOMAIN (D-05); FOV bridge first via core.geometry.normalized_x_to_angle_deg before damping (D-08)
- [Phase ?]: Plan 05-04: Anti-windup via FollowerState.position OVERWRITE using dataclasses.replace (D-09) — NOT PID; no integral/accumulator state. Proven observably: 3 successive over-velocity emitted-deltas equal vmax*dt within machine epsilon (~1e-15)
- [Phase ?]: Plan 05-04: Hold-on-None preserves FollowerState in place (D-07) — contrast with Plan 03 Framer which clears state on indeterminate; degree damper holds across upstream gaps for smooth resume
- [Phase ?]: Plan 05-04: Fixed pre-existing structlog test pollution — configure_logging non-idempotent INFO filter leaked into subsequent log-event tests; fixed via autouse structlog.reset_defaults() teardown in test_logging.py
- [Phase 05]: Plan 05-05: CommandDispatcher synchronous emit gate (D-01) -- only sync stage in Phase 5; orchestrator calls dispatcher.decide(angle, now_ns) directly after await controller.consume(...)
- [Phase 05]: Plan 05-05: Two-gate emission split into two distinct if-blocks (delta then interval) instead of if A and B -- mandatory for 100% branch coverage AND for distinguishable command_suppressed_delta vs command_suppressed_interval Pattern-9 logs
- [Phase 05]: Plan 05-05: Pitfall 7 None-upstream non-update verified across multi-tick gap -- 3 None ticks at 1/2/3s after seed produce no state change; subsequent real call's interval is measured from the original seed timestamp, not the latest None tick
- [Phase 05]: Plan 05-05: Issue 9 ramp upper bound formula floor(T*1000/min_interval_ms) + 2 (NOT + 1) -- +1 first-call seed + +1 boundary-frame slack at gate quantization; Issue 12 defensive assert min_interval_ms > 0 before division
- [Phase 05]: Plan 05-05: Coverage CLI uses --cov=src/pastor_tracker/control (directory form) not module-dotted -- pytest-cov 6.3 + numpy 2.4 hits cannot-load-module-twice on dotted form; directory form matches Plan 04 working invocation
- [Phase ?]: Plan 05-06: dwell_then_walk over ramp for the velocity-clamp test -- D-06 at-target seed makes pure-ramp clamp tests vacuously pass; dwell_then_walk forces a real center-then-third target transition that exercises both dampers
- [Phase ?]: Plan 05-06: ramp/dwell_then_walk constant-vx dilution -- helpers produce vx = (x_end - x_start) / total_sec; cap total_sec at min(time_budget, span_max/walk_vx - margin) so analyzer flips

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Test flake | `test_geometry::test_inverse_map_output_in_unit_interval` -- pre-existing full-suite-only failure (unraisable asyncio event-loop warning); details in `.planning/phases/05-intent-and-control/deferred-items.md` | Open | Plan 05-04 |

## Session Continuity

Last session: 2026-05-06T10:08:34.718Z
Stopped at: Completed 05-04-PLAN.md
Resume file: None
