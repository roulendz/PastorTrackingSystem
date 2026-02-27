# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-15)

**Core value:** Virtual center line stays perfectly locked to physical background at all motor velocities
**Current focus:** Phase 3 - Motion Smoothing

## Current Position

Phase: 3 of 5 (Motion Smoothing)
Plan: 1 of 2 in current phase (03-01 complete)
Status: In Progress
Last activity: 2026-02-27 -- Completed 03-01 (Core motion smoothing modules)

Progress: [########..] 80%

## Performance Metrics

**Velocity:**
- Total plans completed: 8
- Average duration: 5 min
- Total execution time: 0.72 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-test-foundation | 3 | 15 min | 5 min |
| 02-time-synchronization | 4 | 21 min | 5 min |
| 03-motion-smoothing | 1 | 8 min | 8 min |

**Recent Trend:**
- Last 5 plans: 02-01 (5 min), 02-02 (3 min), 02-03 (4 min), 02-04 (9 min), 03-01 (8 min)
- Trend: Consistent

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Test infrastructure comes before timing fix -- enables daily iteration vs weekly church testing
- [Roadmap]: Time sync fix before motion smoothing -- smoothing on broken timing produces smooth-but-wrong output
- [Roadmap]: Code cleanup bundled with test foundation -- clean codebase makes all later phases easier
- [01-01]: SimulatedMotorInterface replaces NullMotorInterface in all fallback paths (motor-less mode now uses physics simulation)
- [01-01]: Background simulation at ~1000 Hz for interactive mode; explicit dt for deterministic tests
- [01-01]: Gear ratio constant 360/288000 deg/step for step-to-degree conversion
- [01-01]: dataclasses-json removed as dead dependency
- [01-02]: Log level convention: debug for expected failures, warning for unexpected-but-recoverable, error for attention-required
- [01-02]: UI/destructor broad catches kept (Exception) but always log at debug level
- [01-02]: Critical path catches narrowed to specific types (ValueError, TypeError, AttributeError, KeyError)
- [01-03]: Mock PoseTracker via unittest.mock rather than importing MediaPipe in tests
- [01-03]: Explicit advance_simulation(dt) in tests for deterministic motor physics
- [01-03]: Synthetic video generation (90 frames, moving red circle) for camera tests
- [02-01]: Clock ABC with abc.ABC (not Protocol) for explicit interface contract
- [02-01]: RealClock wraps time.perf_counter() exclusively (monotonic, high-resolution)
- [02-01]: Background simulation loop keeps raw time.perf_counter() for real-time sleep delta
- [02-01]: Optional[Clock] = None defaulting to RealClock() preserves backward compatibility
- [02-01]: VelocityController.reset_controller() resets _flPreviousVelocity after time state removal
- [02-02]: Timestamp placed between grab() and retrieve() -- closest proxy to true capture moment
- [02-02]: dt > flDtMaxSeconds skips control update and updates previous timestamp to prevent cascade
- [02-02]: dt < flDtMinSeconds clamped up to minimum to avoid division issues
- [02-02]: First frame uses nominal 1/30s interval rather than zero dt
- [02-03]: Module-level _hermite_interpolate_from_history() shared by MotorInterface and SimulatedMotorInterface
- [02-03]: Velocity direction inferred from target-current delta; iAccelerationState=0 forces zero velocity
- [02-03]: Module-level _FL_DEGREES_PER_STEP constant for velocity conversion in helper functions
- [02-04]: Clock must advance before simulation step for timestamp alignment in deterministic tests
- [02-04]: Interpolation accuracy measured at midpoints between coarsely-spaced history entries
- [02-04]: SYNC-06 validated with synchronized clock+sim producing zero interpolation error at frame times
- [03-01]: OneEuroFilter tuned to minCutoff=0.01Hz, derivativeCutoff=0.1Hz (plan spec 1.0Hz could not satisfy <1px jitter suppression)
- [03-01]: HomeReturnController resets profiler velocity on overshoot clamp to prevent oscillation around home
- [03-01]: Overshoot detection uses sign-flip of target angle rather than distance-to-home comparison

### Pending Todos

None yet.

### Blockers/Concerns

- [Research]: DearPyGui Python 3.12 compatibility not verified -- may need version update or replacement
- [Research]: Ruckig + AccelStepper interaction (two motion planners in series) needs empirical testing in Phase 3
- [Research]: Python 3.10 EOL in October 2026 -- upgrade deferred but should happen eventually

## Session Continuity

Last session: 2026-02-27
Stopped at: Completed 03-01-PLAN.md (Core motion smoothing modules)
Resume file: None
