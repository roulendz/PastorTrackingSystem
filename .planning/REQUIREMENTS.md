# Requirements: Pastor Tracking Camera System

**Defined:** 2026-02-15
**Core Value:** Virtual center line stays perfectly locked to physical background at all motor velocities

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Time Synchronization

- [ ] **SYNC-01**: All timing throughout the system uses a single monotonic clock (time.perf_counter) — no time.time() anywhere
- [ ] **SYNC-02**: Camera frame timestamps are captured between grab()/retrieve() calls, not after blocking read()
- [ ] **SYNC-03**: Motor feedback angle interpolation uses quadratic interpolation during acceleration phases (not just linear)
- [ ] **SYNC-04**: Arduino-to-PC clock offset estimation uses linear regression over a sliding window instead of slow-converging EMA (98%/2%)
- [ ] **SYNC-05**: Control algorithms accept delta time as a parameter rather than computing it internally — makes algorithms deterministically testable
- [ ] **SYNC-06**: Virtual center line stays within 2 pixels of correct position at all motor velocities up to 45°/s

### Motion Quality

- [ ] **MOTN-01**: Camera movements use jerk-limited (S-curve) motion profiles instead of trapezoidal acceleration — no visible start/stop jerk
- [ ] **MOTN-02**: Home return uses S-curve easing — camera gently decelerates to home position, not linear velocity clamp
- [ ] **MOTN-03**: Pose detection noise is filtered before control input using adaptive filter (OneEuroFilter or latching) — suppresses jitter without adding lag on real movements
- [ ] **MOTN-04**: Control gain scales with detection confidence — low confidence produces gentle corrections, high confidence allows full tracking speed

### Detection Handling

- [ ] **DTCT-01**: When person detection is lost, camera holds its current position for a configurable timeout (default 5 seconds)
- [ ] **DTCT-02**: After detection loss timeout expires, camera returns to home with smooth easing (same S-curve as MOTN-02)
- [ ] **DTCT-03**: Single dropped detection frames do not cause any visible camera movement — requires consecutive lost frames before hold mode activates

### Testing Infrastructure

- [ ] **TEST-01**: Synthetic video source replaces live camera with scripted scenarios — CameraInterface accepts video file paths
- [ ] **TEST-02**: Scripted scenarios include: pastor walks left, walks right, pauses at lectern, walks outside FOV (±20°), varying walking speeds
- [ ] **TEST-03**: Synthetic video includes static lectern with V marker at center for visual verification of virtual center lock
- [ ] **TEST-04**: NullMotorInterface simulates realistic motor physics — acceleration limits, velocity limits, position ramping (not instant teleportation)
- [ ] **TEST-05**: Full tracking pipeline runs without any physical hardware (synthetic camera + simulated motor)
- [ ] **TEST-06**: Test harness validates virtual center stays within tolerance (SYNC-06) across all scripted scenarios

### Code Quality

- [ ] **CODE-01**: Dead code and legacy code removed — clean, minimal codebase
- [ ] **CODE-02**: Silent exception swallowing replaced with specific exception types and logging
- [ ] **CODE-03**: FOV correctly calculated for Sony AX700 at max optical zoom (~6.8° horizontal)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Motion Profiles

- **MOTN-05**: "Lag then catch-up" motion profile — camera reacts a beat late, then smoothly catches up (behind toggle)
- **MOTN-06**: "Anticipatory" motion profile — camera leads slightly based on movement direction (behind toggle)

### Advanced Features

- **ADVN-01**: Adaptive deadzone based on stage position — tighter when far from home, wider near home
- **ADVN-02**: Framing presets — rule of thirds offset for left/center/right composition
- **ADVN-03**: Property-based testing with Hypothesis for control algorithm edge cases
- **ADVN-04**: Session recording to CSV/JSON for post-hoc analysis and parameter tuning
- **ADVN-05**: Per-phase calibration wizard with guided step-by-step setup

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Multi-person tracking | Single pastor, single stage — system designed for one speaker |
| Auto-zoom control | Changes FOV, invalidates calibration, creates feedback loops |
| Tilt axis tracking | Minimal vertical movement for standing speaker, adds mechanical complexity |
| Face detection mode | Less stable than shoulder tracking at 6.8° FOV, easily occluded |
| Predictive person position (Kalman) | Frame-to-frame displacement is tiny at 30 FPS, prediction causes overshoot |
| Network/ONVIF PTZ control | Different latency profile (100-500ms), no precise angle feedback |
| AI shot composition | Unpredictable behavior unacceptable for live broadcast |
| Python 3.12 upgrade | Useful but not required for v1 — can be done incrementally |
| Recording/playback of real sessions | Synthetic testing covers iteration needs for v1 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SYNC-01 | Phase 2 | Pending |
| SYNC-02 | Phase 2 | Pending |
| SYNC-03 | Phase 2 | Pending |
| SYNC-04 | Phase 2 | Pending |
| SYNC-05 | Phase 2 | Pending |
| SYNC-06 | Phase 2 | Pending |
| MOTN-01 | Phase 3 | Pending |
| MOTN-02 | Phase 3 | Pending |
| MOTN-03 | Phase 3 | Pending |
| MOTN-04 | Phase 3 | Pending |
| DTCT-01 | Phase 4 | Pending |
| DTCT-02 | Phase 4 | Pending |
| DTCT-03 | Phase 4 | Pending |
| TEST-01 | Phase 1 | Pending |
| TEST-02 | Phase 5 | Pending |
| TEST-03 | Phase 5 | Pending |
| TEST-04 | Phase 1 | Pending |
| TEST-05 | Phase 1 | Pending |
| TEST-06 | Phase 5 | Pending |
| CODE-01 | Phase 1 | Pending |
| CODE-02 | Phase 1 | Pending |
| CODE-03 | Phase 1 | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 22
- Unmapped: 0

---
*Requirements defined: 2026-02-15*
*Last updated: 2026-02-15 after roadmap creation*
