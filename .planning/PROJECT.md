# Pastor Tracking Camera System

## What This Is

An automated person-tracking camera system for church services that follows the pastor using MediaPipe pose detection, OpenCV, and Arduino stepper motor control. The camera (Sony AX700 at max optical zoom, ~6.8° FOV) must track the pastor smoothly while maintaining a "virtual center" line locked to the physical home position (talking stand with V marker). The system runs on Windows with Python 3.10.

## Core Value

The virtual center line must stay perfectly locked to the physical background (talking stand) when the motor moves the camera — at any speed, in any control mode. If the virtual center drifts, the entire tracking system's spatial awareness breaks down.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. Inferred from existing codebase. -->

- ✓ Camera capture at 30 FPS with precise timestamps — existing
- ✓ MediaPipe Pose single-person detection with confidence scoring — existing
- ✓ Stepper motor control via Arduino serial protocol (DM542 driver, 180:1 gearbox, 8 microstep) — existing
- ✓ Swappable control algorithms (P, PID, Velocity) via strategy pattern — existing
- ✓ TrackingSample atomic measurements pairing frame + actual motor angle — existing
- ✓ Configuration layering (default_config.json + user_config.json) — existing
- ✓ Live settings panel (Dear ImGui) for real-time parameter tuning — existing
- ✓ Virtual center line visualization on camera feed — existing
- ✓ Angular deadzone around home position to prevent tracking jitter — existing
- ✓ Home return behavior when pastor is within deadzone — existing
- ✓ NullMotorInterface for running without hardware — existing
- ✓ Arduino feedback parsing with microsecond timestamps and sequence numbers — existing
- ✓ Motor angle interpolation from state history for frame synchronization — existing
- ✓ Emergency stop via keyboard (Q to quit, E-stop command) — existing

### Active

<!-- Current scope. Building toward these. All hypotheses until shipped and validated. -->

- [ ] Virtual center stays locked to background at all motor velocities (fix sync bug)
- [ ] Human-like camera motion with smooth ease-in/out curves (no robotic snapping)
- [ ] Smooth home return (gentle easing when pastor enters safe zone, not abrupt centering)
- [ ] Synthetic test harness with scripted pastor paths for deterministic testing
- [ ] Dead code and legacy code removed (clean, maintainable codebase)
- [ ] Correct FOV calculation for Sony AX700 at max optical zoom (~6.8°)
- [ ] Velocity control mode works correctly without introducing timing drift
- [ ] NullMotorInterface simulates realistic motor physics (acceleration, velocity limits)

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Multi-camera support — single camera setup is the use case
- Network/remote control — local operation only for now
- Multi-person tracking — single pastor on stage
- Recording/playback of real sessions — synthetic testing covers iteration needs
- OAuth/authentication — desktop application, not networked
- Mobile app — desktop-only

## Context

The system tracks a pastor during church services. The camera is a Sony AX700 zoomed to max optical (111.6mm focal length, 1" sensor → ~6.8° horizontal FOV at 1280px = ~0.0053°/pixel). At this narrow FOV, even 0.1° of motor sync error = ~19 pixels of visible drift in the virtual center line.

The talking stand at center stage has a distinct V shape, making it easy to visually verify whether the virtual center line stays locked to the physical home position.

Two safe zones exist:
1. **Home safe zone** — angular range around 0° where camera prefers returning to home
2. **Shoulder deadzone** — tolerance around detected person position to prevent jitter from pose estimation noise

The system has been broken for ~20-30 commits. The virtual center line drifts/slides when the motor moves, especially at higher velocities in Velocity control mode. When the camera returns to home (0°), the virtual center re-aligns to frame center — confirming the bug is in the dynamic sync during movement, not the static calculation.

**Suspected root causes:**
- Velocity controller uses `time.time()` for delta calculation (not synced with frame timestamps)
- Motor angle interpolation may lag behind actual position during acceleration phases
- Smoothing alpha and velocity gain may amplify timing jitter at narrow FOV

Hardware: Arduino Uno/Nano with DM542 stepper driver, 200 steps/rev motor with 180:1 gear ratio, 8 microsteps = 288,000 steps/revolution. Serial at 115200 baud, feedback every 20ms.

Testing is currently only possible during weekly church services. Synthetic testing with scripted pastor movement paths is essential for daily iteration.

## Constraints

- **Platform**: Windows 10, Python 3.10 — must remain compatible
- **Hardware**: Arduino + DM542 + 180:1 gearbox stepper — firmware can be rewritten
- **Camera**: Sony AX700 at max optical zoom (~6.8° FOV) — cannot change
- **Real-time**: Must maintain 30 FPS processing loop — no frame drops
- **Testing**: Must be testable without physical hardware or camera (synthetic/simulated)
- **Motion quality**: Camera movements must look human-operated, not robotic

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Full rewrite allowed (clean slate) | ~20-30 commits of accumulated bugs, legacy code making debugging harder | — Pending |
| Velocity control mode as primary | Provides smoother motion than P/PID for human-like camera operation | — Pending |
| Synthetic testing over recording/playback | Scripted paths are more controllable and repeatable than real recordings | — Pending |
| Smooth ease-in/out for v1, other motion profiles for v2 | Start with the most universally "human" feel, add options later | — Pending |
| Arduino firmware can be rewritten | User explicitly approved overwriting all code including firmware | — Pending |
| FOV fixed at ~6.8° for Sony AX700 max zoom | Calculated from sensor specs: 2*atan(13.2/(2*111.6)) = 6.77° | — Pending |

---
*Last updated: 2026-02-15 after initialization*
