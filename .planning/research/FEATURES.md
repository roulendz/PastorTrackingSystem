# Feature Research

**Domain:** Automated single-person camera tracking for church/live event production
**Researched:** 2026-02-15
**Confidence:** MEDIUM-HIGH (verified against multiple commercial systems and open-source implementations)

## Context: What Makes This System Unique

Before mapping features, it is critical to understand the constraints that make this system different from a typical PTZ auto-tracker:

- **Extremely narrow FOV (~6.8 degrees):** The Sony AX700 at max optical zoom yields ~0.0053 degrees/pixel. At 1280px width, even a 1-pixel detection jitter causes ~0.005 degrees of error. This is 10-100x narrower than typical PTZ cameras (50-70 degrees wide).
- **External motor + separate camera:** Unlike integrated PTZ cameras where pan/tilt/zoom are unified, this system has a separate stepper motor and camera, creating synchronization challenges.
- **Single-person, single-stage:** Always tracking one known person (the pastor) on a defined stage area. Not a security/surveillance multi-target system.
- **Broadcast-quality output required:** The camera output goes to a live stream or recording. Jerky tracking is immediately visible to viewers.

These constraints make certain features critical here that would be optional in wider-FOV systems.

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete or unusable.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Deadzone around virtual home** | Without it, the camera jitters constantly when the pastor stands near the center. Every commercial system has this. Currently partially implemented. | LOW | Already exists as `flControlDeadbandDegrees`. Needs to be relative to home position (0 degrees), not camera center. Currently uses person-angle-relative-to-home which is correct. |
| **Smooth return to home when idle** | When the pastor is within the deadzone, the camera should drift gently back to home (0 degrees), not snap. Prevents visible correction. All professional systems do this. | MEDIUM | Currently implemented as `_compute_home_return_target_angle()` with velocity-limited return. Needs polish -- should use S-curve easing, not linear velocity limit. |
| **Configurable tracking speed/sensitivity** | Operators must tune how aggressively the camera follows. Too fast = jerky. Too slow = person leaves frame. Panasonic AW-SF100, PTZOptics Move 4K, and Frigate all expose this. | LOW | Partially exists via `flVelocityGain`, `flMaxVelocityDegreesPerSecond`, `flVelocitySmoothingAlpha`. These are the right parameters but need better UI exposure and per-scenario presets. |
| **Loss-of-detection hold + timeout** | When pose detection fails (person turns, occlusion), the camera must hold its last position for a configurable timeout, then gently return home. Snapping home immediately on a single dropped frame is catastrophic on live broadcast. | MEDIUM | Not implemented. Currently the system just skips the control tick when no person is detected. Needs: hold position for N seconds, then gradual return. Frigate uses 10-second default timeout. |
| **Motor angle limits (soft stops)** | Camera must not pan beyond safe mechanical/optical limits. All systems have this. | LOW | Already implemented: `flMotorMinAngleDegrees` / `flMotorMaxAngleDegrees` with clamping in `_execute_centering_control_algorithm`. |
| **Start/stop tracking controls** | Operator must be able to enable/disable tracking instantly. | LOW | Already implemented: S key starts, P key pauses. |
| **Visual feedback overlay** | Operator needs to see: person detection circle, home line, deadzone boundaries, motor angle, tracking state. | MEDIUM | Already implemented in `draw_visualization_overlay()` and `DeadzoneUIController`. Includes home line, deadzone edges, person marker, stats. Well done. |
| **Emergency stop** | Instant motor halt for safety. | LOW | Already implemented: `send_emergency_stop_command()`. |

### Differentiators (Competitive Advantage)

Features that set this system apart from off-the-shelf PTZ auto-tracking cameras.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **S-curve motion profiles** | Commercial broadcast systems (Shotoku AutoFrame, Maxicrane) use S-curve acceleration/deceleration to eliminate the mechanical, robotic feel. Trapezoidal profiles cause visible jerk at start/stop. S-curves spread jerk over time, making motion feel natural and broadcast-ready. This is the single biggest quality differentiator. | HIGH | Requires changes at both Arduino firmware level (AccelStepper supports trapezoidal only, need custom S-curve or jerk-limited profile) AND Python control level (velocity commands must be shaped). The VelocityController's `flVelocitySmoothingAlpha` EMA filter is a crude approximation -- real S-curves need 7-phase profiling. |
| **Shoulder-center tracking with jitter suppression** | Using shoulder midpoint (already implemented) is more stable than face tracking for a speaker who turns their head. Adding a nonlinear latching filter (per NSF research) on top would suppress sub-pixel jitter without adding latency to real movements. Better than simple low-pass which slows everything. | MEDIUM | Current implementation uses raw shoulder midpoint. Need to add: (1) temporal smoothing of detected center point (EMA or latching filter), (2) minimum movement threshold below which corrections are suppressed. The latching filter approach is specifically designed for this problem -- smooth small jitter, pass large movements. |
| **Synthetic video testing mode** | Replace live camera with a pre-recorded or procedurally generated video showing a person walking across a stage. Allows development, regression testing, and parameter tuning without physical hardware. No commercial system offers this because they sell hardware. For a DIY system, this is transformative for development velocity. | MEDIUM | The architecture already supports this via dependency injection -- `CameraInterface` can be replaced with a `VideoFileCameraInterface` that reads from MP4/AVI. OpenCV `VideoCapture` accepts both device indices and file paths. Combined with `NullMotorInterface`, enables fully hardware-free testing. |
| **Recording and playback of tracking sessions** | Record TrackingSamples (timestamp, motor angle, person position, detection confidence) to CSV/JSON for post-hoc analysis. Enables: comparing algorithm changes, identifying failure modes, tuning parameters offline. | MEDIUM | TrackingSample dataclass already has all needed fields. Need: (1) session recorder that writes samples to file, (2) session player that can replay through the visualization, (3) metrics computation (tracking error histogram, jitter frequency analysis). |
| **Framing presets (composition modes)** | Like PTZOptics Move 4K's left/center/right framing options. The "center" of tracking does not have to be the physical center of the frame -- the pastor could be framed in the left third (rule of thirds) while still being tracked. This is an offset applied to the target pixel position. | LOW | Simple constant offset added to `get_pixel_offset_from_center()`. Need: configurable `flFramingOffsetPixels` that shifts the "target" left or right of frame center. Three presets: center (0), left-third (+offset), right-third (-offset). |
| **Adaptive deadzone based on stage position** | When the pastor is far from home (large motor angle), the deadzone should be tighter because they are actively moving. When near home, the deadzone should be wider because they are likely stationary (at the podium). This prevents the common problem where the tracking is too loose when they start walking and too tight when they stand still. | MEDIUM | Requires a function: `flEffectiveDeadzone = f(flMotorAngle)`. Simple approach: linear interpolation between `flDeadzoneAtHomeDegrees` (wide) and `flDeadzoneAtEdgeDegrees` (tight) based on absolute motor angle. |
| **Confidence-weighted control gain** | When pose detection confidence is low (partially occluded, bad lighting), reduce control aggressiveness rather than tracking at full gain with noisy data. High confidence = full tracking speed, low confidence = gentle/slow corrections. | LOW | `flPersonConfidenceScore` already flows through `TrackingSample`. Multiply the control correction by `clamp(confidence, 0.3, 1.0)` or similar. Simple but effective at reducing jitter from marginal detections. |
| **Per-phase calibration wizard** | Guided setup: (1) set home position, (2) calibrate FOV, (3) walk the stage to set angle limits, (4) adjust deadzone live. The current 'C' key calibration is functional but not discoverable. | MEDIUM | UI work mostly. The 2-point FOV calibration already exists. Need to wrap it in a step-by-step wizard with visual prompts. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems in this specific context.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Multi-person tracking** | "What if there are multiple people on stage?" | Exponentially increases complexity. Requires: person identification/re-identification, target selection logic, handoff algorithms. MediaPipe Pose only tracks one person reliably. The system is designed for tracking one speaker -- this is the right scope. | Use multiple camera instances, one per person. Or accept that this system tracks the primary speaker only. |
| **Zoom control (auto-zoom)** | "Camera should zoom in when the pastor is far away" | The Sony AX700's optical zoom changes FOV, which invalidates the angle-per-pixel calibration. Auto-zoom creates a feedback loop: zoom in -> FOV narrows -> small movements cause large pixel offsets -> camera overcorrects -> zoom out -> repeat. Also, zoom changes are slow and visible. | Fix zoom at max optical. The narrow FOV IS the feature -- it provides the tight framing that makes the system valuable. If wider shots are needed, use a second camera. |
| **Tilt axis tracking** | "Camera should also tilt up/down as the pastor moves" | Adds mechanical complexity (2-axis gimbal), doubles motor control complexity, and vertical movement is minimal for a standing speaker. Tilt is only needed if the stage has significant elevation changes. | Fixed tilt angle set during installation. If elevation changes exist, handle with a manual tilt preset, not auto-tracking. |
| **Face detection mode** | "Track the face instead of shoulders" | Faces are smaller targets, more easily occluded (looking down at notes, turning to the screen), and the detection is less stable than shoulder tracking. At 6.8 degree FOV, the face occupies very few pixels. Shoulders are wider, more stable landmarks. | Keep shoulder-midpoint tracking. It is the right choice for this application as the codebase already implements. |
| **Predictive motion (Kalman filter for person position)** | "Predict where the person will be to reduce lag" | At 30 FPS with a person walking at normal speed (~1.4 m/s), the per-frame displacement is tiny relative to the FOV. Prediction adds complexity and can cause overshoot when the person stops or changes direction. The real latency problem is motor response time, not detection lag. | Focus on reducing motor command latency (faster serial, predictive motor positioning) rather than predicting person position. The motor angle interpolation already partially addresses this via `get_estimated_motor_angle_degrees()`. |
| **Network/ONVIF PTZ control** | "Control a real PTZ camera instead of a stepper motor" | Completely different latency profile (ONVIF commands have 100-500ms round-trip), no precise angle feedback, camera-dependent behavior. Would require rewriting the entire motor interface. This system's strength is the tight feedback loop with the Arduino stepper. | If ONVIF control is ever needed, it should be a separate project, not bolted onto this one. |
| **AI-powered shot composition** | "AI should automatically frame the shot cinematically" | Black box behavior is unacceptable for live broadcast. The operator needs predictable, deterministic behavior. "AI decided to reframe" during a sermon is a disaster. | Provide manual composition presets (rule of thirds offsets) that the operator selects. Deterministic, predictable, controllable. |

## Feature Dependencies

```
Motor Angle Limits
    (no dependencies, already implemented)

Deadzone Around Home
    (no dependencies, already implemented)

Loss-of-Detection Hold + Timeout
    (no dependencies, currently missing)

Smooth Home Return
    requires: Deadzone Around Home
    requires: Loss-of-Detection Hold (to know when to start returning)

S-Curve Motion Profiles
    requires: Arduino firmware changes (jerk-limited stepping)
    requires: Motor Angle Limits (S-curve must respect limits)
    enhances: Smooth Home Return (home return uses S-curve too)
    enhances: All tracking motion quality

Shoulder Jitter Suppression (Latching Filter)
    requires: Shoulder Center Tracking (already implemented)
    enhances: All control algorithms (cleaner input signal)

Confidence-Weighted Control Gain
    requires: Confidence score in TrackingSample (already implemented)
    enhances: Loss-of-Detection Hold (graceful degradation before full loss)

Framing Presets
    requires: Deadzone Around Home (offset interacts with deadzone)

Adaptive Deadzone
    requires: Deadzone Around Home (extends it)
    enhances: Tracking quality across full stage range

Synthetic Video Testing
    requires: CameraInterface abstraction (already exists via DI)
    requires: NullMotorInterface (already exists)
    enhances: All future development (enables testing without hardware)

Session Recording/Playback
    requires: TrackingSample data structure (already exists)
    enhances: Synthetic Video Testing (recorded sessions become test data)
    enhances: Parameter tuning (offline analysis)

Calibration Wizard
    requires: FOV Calibration (already exists)
    requires: Motor Angle Limits (already exists)
    enhances: Initial setup experience
```

### Dependency Notes

- **S-Curve Motion Profiles require Arduino firmware changes:** The AccelStepper library only supports trapezoidal profiles. S-curve requires either a custom firmware implementation or a different stepper library. This is the highest-risk dependency because it crosses the hardware/software boundary.
- **Synthetic Video Testing enables all other development:** Once video file input works, every other feature can be developed and tested without hardware. This should be implemented early to accelerate all subsequent work.
- **Loss-of-Detection Hold is a prerequisite for Smooth Home Return:** The system needs to know the difference between "person is in deadzone" (drift home gently) and "person is lost" (hold position, then timeout and return home). These are different behaviors that need different state management.
- **Shoulder Jitter Suppression enhances everything:** Cleaner input to the control algorithm means every control mode benefits. This is a force multiplier.

## MVP Definition

### Launch With (v1) -- Minimum for Usable Live Tracking

- [x] Motor angle limits -- already implemented
- [x] Start/stop tracking controls -- already implemented
- [x] Visual feedback overlay -- already implemented
- [x] Deadzone around home -- already implemented
- [x] Emergency stop -- already implemented
- [ ] **Loss-of-detection hold + timeout** -- critical gap, prevents embarrassing snaps during brief detection failures
- [ ] **Shoulder jitter suppression** -- at 6.8 degree FOV, raw detection jitter is visible in the output. Must smooth.

### Add After Validation (v1.x) -- Quality Polish

- [ ] **S-curve motion profiles** -- transforms tracking from "robotic" to "broadcast quality". This is the biggest quality leap but also the most complex (firmware + software).
- [ ] **Smooth home return with easing** -- upgrade current linear velocity limit to proper S-curve easing for return-to-home motion.
- [ ] **Confidence-weighted control gain** -- simple multiplier, big impact on marginal detections.
- [ ] **Synthetic video testing mode** -- enables development velocity for all future features.
- [ ] **Session recording** -- enables data-driven parameter tuning.

### Future Consideration (v2+)

- [ ] **Framing presets** -- nice to have for production variety, not critical for tracking quality.
- [ ] **Adaptive deadzone** -- optimization that requires real-world tuning data to calibrate properly.
- [ ] **Calibration wizard** -- UX improvement, not functional improvement.
- [ ] **Session playback with visualization** -- analysis tool for advanced users.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority | Status |
|---------|------------|---------------------|----------|--------|
| Loss-of-detection hold + timeout | HIGH | LOW | **P1** | Not implemented |
| Shoulder jitter suppression | HIGH | MEDIUM | **P1** | Not implemented |
| S-curve motion profiles | HIGH | HIGH | **P1** | Not implemented (requires firmware) |
| Smooth home return with easing | HIGH | MEDIUM | **P1** | Partially implemented (linear only) |
| Confidence-weighted gain | MEDIUM | LOW | **P2** | Not implemented |
| Synthetic video testing | MEDIUM | LOW | **P2** | Not implemented (architecture supports it) |
| Session recording | MEDIUM | LOW | **P2** | Not implemented |
| Framing presets | MEDIUM | LOW | **P2** | Not implemented |
| Adaptive deadzone | MEDIUM | MEDIUM | **P3** | Not implemented |
| Calibration wizard | LOW | MEDIUM | **P3** | Partially implemented (2-point FOV) |
| Session playback visualization | LOW | MEDIUM | **P3** | Not implemented |

**Priority key:**
- P1: Must have for broadcast-quality tracking
- P2: Should have, add when possible for polish and development efficiency
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Shotoku AutoFrame | PTZOptics Move 4K | Panasonic AW-SF100 | Frigate Autotracking | Our System |
|---------|-------------------|-------------------|-------------------|---------------------|------------|
| Motion smoothing | S-curve with response delay profiling | Basic smoothing | Adjustable sensitivity/speed | Basic ONVIF relative movement | EMA velocity smoothing (needs S-curve upgrade) |
| Deadzone / safe zone | Integrated framing presets | Auto-framing zones | Mask areas, tracking disable zones | Required zones for trigger only | Home-relative deadzone with draggable UI |
| Detection loss behavior | Holds with smooth transitions | Holds, returns to preset | Configurable lost-target behavior | Timeout + scan region + return to preset | **Missing -- needs implementation** |
| Framing composition | Framing presets (single/dual face) | Left/center/right composition | Full/upper body angle presets | N/A (security focus) | Not implemented |
| Testing without hardware | N/A (sells hardware) | N/A (sells hardware) | N/A (sells hardware) | N/A (requires ONVIF camera) | NullMotorInterface exists, needs VideoFileCameraInterface |
| Tracking target | Face(s) | Body + face pose detection | Template + face + deep learning | Object detection (YOLO-based) | Shoulder midpoint via MediaPipe Pose |
| Calibration | Factory + operator tuning | Auto-calibration on startup | GUI with masking/limiters | Startup calibration for movement weights | Manual 2-point FOV + config file |
| Narrow FOV handling | Designed for broadcast cameras | Integrated PTZ (wide FOV default) | Integrated PTZ | Integrated PTZ | **Unique strength** -- system designed specifically for telephoto tracking |
| Session recording | Via external systems | Via external NVR | Via external systems | Built-in recording | Not implemented |
| Price point | $10,000+ | $2,000-4,000 | $800 (software only, needs PTZ) | Free (open source, needs ONVIF PTZ) | DIY cost (~$200 hardware) |

## Sources

### Commercial Systems Analyzed
- [Shotoku AutoFrame](https://www.shotoku.co.uk/products/autoframe/) -- broadcast-grade face tracking with S-curve motion profiles and framing presets (MEDIUM confidence, from official product pages and press releases)
- [PTZOptics Move 4K](https://ptzoptics.com/move-4k/) -- church-focused PTZ with auto-tracking, composition framing, and sensitivity controls (MEDIUM confidence, from official docs and ChurchFront review)
- [Panasonic AW-SF100](https://pro-av.panasonic.net/en/software/aw-sf100g/features.html) -- professional auto-tracking software with masking, limiters, sensitivity, and tracking disable zones (MEDIUM confidence, from official feature page)
- [Frigate Autotracking](https://docs.frigate.video/configuration/autotracking/) -- open-source NVR with PTZ autotracking, zones, calibration, timeout, return preset (HIGH confidence, verified from official docs)

### Motion Profile Research
- [Maxicrane Motion Profiles](https://maxicrane.com/blogs/news/understanding-motion-control-profiles-trapezoidal-and-s-curve-for-maxicrane-s-ostrich-and-scarab-systems) -- trapezoidal vs S-curve comparison for camera motion control (MEDIUM confidence)
- [PMD Corp S-Curve Deep Dive](https://www.pmdcorp.com/resources/type/articles/get/s-curve-profiles-deep-dive-article) -- 7-phase S-curve motion profiling mathematics (MEDIUM confidence)
- [Arduino Forum AccelStepper S-Curve](https://forum.arduino.cc/t/jerky-motion-in-stepper-using-s-curve-acceleration/174615) -- challenges with S-curve on AccelStepper (LOW confidence, forum discussion)

### Jitter Reduction Research
- [NSF Nonlinear Latching Filter](https://par.nsf.gov/servlets/purl/10233670) -- academic paper on jitter removal without adding latency, specifically designed for optical tracking (MEDIUM confidence, peer-reviewed)
- [Jitter Reduction Survey (SAE)](https://saemobilus.sae.org/articles/reduction-jitter-optical-tracking-data-depth-survey-smoothing-techniques-2008-01-2260) -- comparison of smoothing techniques for optical tracking data (MEDIUM confidence)

### Testing Approaches
- [OpenCV VideoCapture from File](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html) -- official OpenCV docs confirming VideoCapture accepts file paths (HIGH confidence, official docs)
- [Mock Camera in OpenCV Python](https://answers.opencv.org/question/229878/create-mock-opencv-camera-on-python/) -- community approach to replacing live camera with image sequences (LOW confidence, forum)

### Church/Worship Specific
- [ChurchFront PTZOptics Review](https://churchfront.com/2024/08/06/ptz-optics-move-4k-and-move-se-review-auto-tracking-cameras-for-churches/) -- real-world church usage review of auto-tracking cameras (MEDIUM confidence)
- [Reolink PTZ Camera for Church Guide](https://reolink.com/blog/ptz-camera-for-church/) -- general guide to PTZ features for worship settings (LOW confidence)

---
*Feature research for: Automated single-person camera tracking (church/live events)*
*Researched: 2026-02-15*
