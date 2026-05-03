# Phase 2: Time Synchronization - Context

**Gathered:** 2026-02-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix the core timing bug causing virtual center line drift. All timing in the pipeline uses a single monotonic clock, camera frames are paired with interpolated motor angles at capture time, and control algorithms accept explicit delta-time for deterministic behavior. Runtime quality monitoring and degraded-mode responses belong in Phase 5.

</domain>

<decisions>
## Implementation Decisions

### Frame-motor pairing
- Interpolate motor angle from a timestamped history buffer — not nearest-report
- Best accuracy is the priority: interpolation from buffered motor reports at the frame's capture timestamp
- Arduino motor report frequency is unknown — researcher must investigate the firmware
- Frame timestamp placement (before/after grab()) is Claude's discretion based on OpenCV behavior research
- Camera-to-motor latency offset calibration is Claude's discretion based on whether the offset is significant

### Motor angle interpolation
- Physics-informed interpolation using known motor parameters (configured accel, max velocity from AccelStepper)
- Single smooth curve through direction reversals — not three discrete phases (decel/stop/accel)
- Interpolation always runs — no rest-detection bypass
- Operating angle range limited to ±25° for this setup (pastor motion envelope)
- When pastor enters home safe zone, motor smoothly returns to 0 degrees (home position for picture symmetry)
- Smooth but sticky: no jitter, but responsive tracking is the priority
- Constant-velocity segment interpolation method is Claude's discretion

### Timestamp propagation
- Single clock utility wrapping time.perf_counter() — all components call it, including the motor background thread
- Clock utility supports dependency injection: RealClock for production, FakeClock for deterministic tests
- TrackingSample carries an absolute timestamp (from clock utility), not delta-time — consumers compute dt
- Absolute perf_counter values as source of truth; derive relative/session-start values at boundaries if needed
- Control algorithms receive explicit dt parameter: calculate_correction_from_error(flError, flDeltaTime)
- dt computed at the pipeline boundary by the caller, not inside control algorithms
- dt clamped at the pipeline boundary (max ~2x frame interval, e.g., ~66ms for 30fps); skip control update if dt exceeds max
- Optional dt_min to avoid near-zero values
- On dropped frames: motor telemetry still recorded in history buffer, but control update skipped for that cycle
- Motor thread uses the same shared clock utility (not raw perf_counter) for consistency and testability

### Drift tolerance & measurement
- 2-pixel threshold measured as RMS (or MAE) over a configurable window (default ~1 second) — not per-frame instantaneous
- Transient exceedance during direction reversals is acceptable; must settle back within a configurable window (default ~0.75s)
- 45 deg/s is the validated operating limit — tolerance guaranteed up to this speed
- 2-pixel RMS is primarily a validation/acceptance metric for Phase 2
- Runtime quality monitoring and soft degradation responses deferred to Phase 5
- Debug overlay (gated behind debug flag) showing: pixel error (signed), raw motor angle, interpolated motor angle, delta between them
- RMS window length is a configurable parameter (default ~1s)
- Reversal settling window is a configurable parameter (default ~0.75s)

### Test validation approach
- Primary: synthetic known trajectories (constant velocity, acceleration, direction change) — deterministic ground truth
- Secondary: recorded real-world replays as a regression suite (Phase 5 scope for full scenario library)

### Claude's Discretion
- Frame timestamp placement relative to grab()/retrieve()
- Camera-to-motor latency offset calibration (whether needed and approach)
- Constant-velocity interpolation method (linear vs quadratic)
- Exact dt clamp values and skip-vs-clamp policy
- Clock utility API design details
- Debug overlay layout and formatting
- Exact RMS vs MAE choice for drift metric

</decisions>

<specifics>
## Specific Ideas

- "Smooth but sticky to pastor" — no jitter from noise, but responsive to real movement
- Motor always returns smoothly to 0 (home) when pastor is in center safe zone — preferred for picture symmetry
- Debug overlay should make interpolation benefit visible: show raw vs interpolated angle side-by-side
- dt policy enforced at pipeline boundary, not inside algorithms — separation of concerns
- Clock utility pattern: small Clock abstraction with now(), RealClock for production, FakeClock with manual advance for tests
- On dropped frames: log the drop, record motor telemetry, skip control — simple testable rule

</specifics>

<deferred>
## Deferred Ideas

- Runtime tracking quality degradation response (soft gain reduction, health signal) — Phase 5
- Runtime drift-exceeded escalation policy (pause correction after sustained threshold breach) — Phase 5
- UI indicator for tracking quality status — Phase 5
- Recorded real-world replay regression suite — Phase 5

</deferred>

---

*Phase: 02-time-synchronization*
*Context gathered: 2026-02-22*
