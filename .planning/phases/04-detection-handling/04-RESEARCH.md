# Phase 4: Detection Handling - Research

**Researched:** 2026-03-01
**Domain:** Detection state management and graceful degradation in real-time tracking pipeline
**Confidence:** HIGH

## Summary

Phase 4 is a pure software-logic phase with no new external dependencies. The implementation adds a detection state machine (TRACKING / HOLDING / RETURNING_HOME) to the existing TrackerController pipeline, unifying detection loss and low-confidence handling into a single code path. The vast majority of infrastructure already exists: the HomeReturnController provides S-curve return motion, the low-confidence timer path already accumulates time and triggers home return, and the OneEuroFilter already has a `reset()` method for reinitialization.

The primary engineering challenge is dropout filtering -- distinguishing between a single dropped frame (noise) and genuine detection loss. The CONTEXT.md leaves the approach (frame-counter vs time-based) and threshold values to Claude's discretion. Research indicates a frame-counter approach is simpler, more deterministic, and better suited to the 30 FPS fixed-rate pipeline. Recovery easing (ramping corrections back up after reappearance) requires a short multiplicative ramp on the confidence scale factor.

**Primary recommendation:** Add a `DetectionState` enum and frame-counter dropout filter to `_execute_centering_control_algorithm()`, merging detection loss into the existing low-confidence timer path. No new modules or files needed -- this is modification of `tracker_controller.py` plus new config fields and tests.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Detection loss (bPersonWasDetected=False) shares the same path as low confidence -- feeds into the existing low-confidence timer
- Hard freeze when detection drops: camera stops immediately, any in-progress S-curve or correction halts
- Same timeout for both detection loss and low confidence (flConfidenceLowTimeoutSeconds, default 5s)
- Same HomeReturnController S-curve parameters for detection-loss home return as safe-zone home return
- Stop sending motor commands during hold -- stepper motor holds position naturally via holding torque
- Brief detection gaps (single or few frames) must cause zero visible camera movement
- Hold position during brief gaps, resume normal tracking when detection returns
- OneEuroFilter state preserved across brief gaps for smoother re-acquisition
- Detection state (TRACKING / HOLDING / RETURNING_HOME) shown in debug overlay only, no indicator in normal mode
- Ease back into tracking gradually when pastor reappears (ramp corrections over ~0.3-0.5s window)
- Cancel home return immediately if person reappears mid-return (matches existing HomeReturnController cancel-on-exit behavior)
- Same S-curve speed limits for recovery as normal tracking -- no special catch-up mode
- Freeze-in-place only when detection lost -- no tracking of last known direction or predictive behavior

### Claude's Discretion
- Dropout filtering approach (frame-counter vs time-based threshold for declaring detection "lost")
- Exact dropout threshold value (number of frames or duration)
- OneEuroFilter reset policy: when to reset vs preserve state after longer gaps
- Settings panel organization -- whether to add a dedicated "Detection Handling" section or reuse existing confidence controls
- Recovery easing implementation (ramp function shape and exact duration)

### Deferred Ideas (OUT OF SCOPE)
- Predictive tracking using last-known direction of travel -- could be its own enhancement phase
- Different timeout/S-curve parameters for detection loss vs low confidence -- premature until real-world testing reveals need
- Visual indicator for camera operators showing detection state -- could be added later if operators need it
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DTCT-01 | When person detection is lost, camera holds its current position for a configurable timeout (default 5 seconds) | Merge detection-loss into existing low-confidence timer path. Hard freeze (stop sending commands). Timer uses existing `flConfidenceLowTimeoutSeconds`. Frame-counter dropout filter prevents premature timer start on single dropped frames. |
| DTCT-02 | After detection loss timeout expires, camera returns to home with smooth easing (same S-curve as MOTN-02) | Existing HomeReturnController already handles this via the low-confidence path at lines 320-338 of tracker_controller.py. Detection loss shares this exact path -- no new return logic needed. |
| DTCT-03 | Single dropped detection frames do not cause any visible camera movement -- requires consecutive lost frames before hold mode activates | Frame-counter dropout filter: count consecutive no-detection frames; only declare "detection lost" after N consecutive misses (recommend N=3, ~100ms at 30 FPS). During counted frames, hold position and preserve filter state. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib (enum) | 3.10+ | DetectionState enum | Already used for TrackerState, HomeReturnState |

### Supporting
No new dependencies. Phase 4 uses only existing project modules:

| Module | Path | Purpose | When to Use |
|--------|------|---------|-------------|
| HomeReturnController | `src/control/home_return_controller.py` | S-curve home return after timeout | Already integrated at tracker_controller.py:320-338 |
| OneEuroFilter | `src/tracking/pose_filter.py` | Pose smoothing, has reset() for reinitialization | Preserve state across short gaps, reset after long gaps |
| MotionProfiler | `src/control/motion_profiler.py` | S-curve velocity profiling | Reset on hard freeze to prevent stale velocity carryover |
| compute_confidence_scale_factor | `src/tracking/pose_filter.py` | Smoothstep scale factor | Used in recovery easing ramp multiplication |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Frame-counter dropout filter | Time-based threshold (accumulate dt) | Time-based handles variable frame rates, but this system runs at fixed 30 FPS so frame counting is simpler and more deterministic. Frame counter also avoids floating-point accumulation errors. |
| Linear recovery ramp | Smoothstep recovery ramp | Smoothstep provides C1 continuity (no kink at start/end of ramp), consistent with existing confidence scaling. Slightly more code but visually smoother. |
| Dedicated DetectionState enum | Reuse TrackerState | TrackerState is too coarse (IDLE/TRACKING/ERROR). Detection state is a sub-state of TRACKING and should be modeled separately. |

## Architecture Patterns

### Recommended Project Structure
No new files needed. All changes go in existing files:
```
src/
├── control/
│   └── tracker_controller.py    # DetectionState enum, dropout filter, recovery ramp
├── tracking/
│   └── pose_filter.py           # No changes needed (reset() already exists)
├── utilities/
│   └── config_manager.py        # New config fields for dropout and recovery
├── ui/
│   └── live_settings_panel.py   # New sliders for detection handling params
└── main.py                      # Debug overlay additions
tests/
└── test_detection_handling.py   # New test file for Phase 4 behaviors
```

### Pattern 1: Detection State Machine (Sub-state of TRACKING)

**What:** A `DetectionState` enum (TRACKING, HOLDING, RETURNING_HOME) that represents the detection-handling sub-state within the overall TRACKING state. This sub-state governs behavior in `_execute_centering_control_algorithm()`.

**When to use:** Whenever the tracking pipeline needs to decide between normal tracking, holding position, or returning home based on detection status.

**Implementation approach:**

```python
class DetectionState(Enum):
    """Sub-states of the TRACKING state for detection handling."""
    TRACKING = "TRACKING"           # Person detected, normal control active
    HOLDING = "HOLDING"             # Detection lost, holding position, timer running
    RETURNING_HOME = "RETURNING_HOME"  # Timeout expired, S-curve return in progress
```

State transitions:
- TRACKING -> HOLDING: Detection lost (after dropout filter threshold)
- HOLDING -> TRACKING: Detection recovered (with recovery easing ramp)
- HOLDING -> RETURNING_HOME: Hold timeout expired (feeds into existing HomeReturnController)
- RETURNING_HOME -> TRACKING: Detection recovered mid-return (cancel HomeReturnController)
- RETURNING_HOME -> AT_HOME: Return completes (existing HomeReturnController behavior)

### Pattern 2: Frame-Counter Dropout Filter

**What:** Count consecutive frames with `bPersonWasDetected=False`. Only transition to HOLDING after N consecutive misses. During the count-up, the pipeline holds position (no commands sent) but does not start the hold timer.

**When to use:** At the top of `_execute_centering_control_algorithm()`, replacing the current simple `if not obCurrentSample.bPersonWasDetected: return`.

**Implementation approach:**

```python
# At top of _execute_centering_control_algorithm():
if not obCurrentSample.bPersonWasDetected:
    self._iConsecutiveDroppedFrames += 1
    if self._iConsecutiveDroppedFrames < self._iDropoutFrameThreshold:
        # Brief dropout: hold position silently, don't start timer
        return
    # Genuine detection loss: enter/continue HOLDING state
    # ... (start or continue low-confidence timer)
    return
else:
    self._iConsecutiveDroppedFrames = 0
    # ... (handle recovery if was HOLDING or RETURNING_HOME)
```

**Recommended threshold:** `iDetectionDropoutFrameThreshold = 3` (configurable). At 30 FPS this is 100ms, long enough to absorb 1-2 dropped frames from MediaPipe, short enough that genuine departures are detected within 100ms.

### Pattern 3: Recovery Easing Ramp

**What:** When detection recovers after a hold period, corrections are multiplied by a ramp factor that goes from 0.0 to 1.0 over ~0.3-0.5 seconds. This prevents a snap-to-target when the pastor reappears.

**When to use:** When transitioning from HOLDING or RETURNING_HOME back to TRACKING.

**Implementation approach:**

```python
# On recovery (detection returns after hold):
self._dRecoveryStartTimestamp = dNow
self._eDetectionState = DetectionState.TRACKING

# In normal tracking path:
if self._dRecoveryStartTimestamp is not None:
    flRecoveryElapsed = dNow - self._dRecoveryStartTimestamp
    if flRecoveryElapsed < self.flRecoveryEasingDurationSeconds:
        flT = flRecoveryElapsed / self.flRecoveryEasingDurationSeconds
        flRecoveryScale = flT * flT * (3.0 - 2.0 * flT)  # smoothstep
    else:
        flRecoveryScale = 1.0
        self._dRecoveryStartTimestamp = None  # Ramp complete
    # Multiply into the existing confidence scale factor
    flConfidenceScale *= flRecoveryScale
```

### Pattern 4: Existing Low-Confidence Path Reuse

**What:** Detection loss feeds into the same `_flLowConfidenceTimer` and HomeReturnController path as low confidence. The CONTEXT.md explicitly states these share a single unified path.

**Current code at tracker_controller.py lines 315-341:** Already accumulates timer, triggers HomeReturnController with S-curve return, resets timer when confidence recovers. Detection loss simply forces `flConfidenceScale = 0.0` and enters this same path.

**Key insight:** The only new logic is: (a) the dropout filter before the confidence gate, (b) the hard-freeze behavior (reset MotionProfiler on entering HOLDING to prevent stale velocity), and (c) the recovery easing ramp on exiting HOLDING/RETURNING_HOME.

### Anti-Patterns to Avoid
- **Separate home return logic for detection loss:** CONTEXT.md explicitly says to reuse the same HomeReturnController and S-curve parameters. Do not create a parallel return path.
- **Sending zero-velocity commands during hold:** The user decided that NO commands should be sent during hold. The stepper motor maintains position via holding torque. Sending commands (even "hold at current position") creates unnecessary serial traffic.
- **Resetting OneEuroFilter on every detection gap:** Brief gaps (< dropout threshold) should preserve filter state entirely. Only reset after long gaps (> hold timeout) where the filter state is likely stale.
- **Predictive tracking during gaps:** Explicitly deferred. Do not track last-known direction or extrapolate position.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| S-curve home return | New return motion logic | Existing `HomeReturnController` + `_send_profiled_motor_command()` | Already tested, handles all edge cases (overshoot clamp, cancel-on-exit, AT_HOME locking) |
| Smoothstep easing | Custom interpolation | `flT * flT * (3.0 - 2.0 * flT)` (same formula as `compute_confidence_scale_factor`) | Proven C1-continuous, already used in codebase, trivial inline formula |
| Detection timer | New timer class | Existing `_flLowConfidenceTimer` pattern | Same accumulation + threshold + HomeReturnController trigger. Unifying keeps behavior consistent. |

**Key insight:** Phase 4 is primarily about *connecting* existing components, not building new ones. The HomeReturnController, MotionProfiler, OneEuroFilter, and low-confidence timer are all already implemented and tested. The new logic is the dropout filter (a frame counter) and recovery easing (a timestamp + smoothstep).

## Common Pitfalls

### Pitfall 1: MotionProfiler Stale Velocity on Detection Loss
**What goes wrong:** When detection is lost and the camera should freeze, the MotionProfiler still holds its last velocity. If detection recovers, the profiler resumes from that velocity, causing an immediate motion burst.
**Why it happens:** The MotionProfiler is never explicitly reset during the hold period.
**How to avoid:** Call `self._obMotionProfiler.reset()` when entering HOLDING state (hard freeze). This zeros the profiler velocity. Combined with the recovery easing ramp, re-engagement will be smooth.
**Warning signs:** Camera lurches briefly when detection recovers after a long hold period.

### Pitfall 2: OneEuroFilter Stale State After Long Gap
**What goes wrong:** OneEuroFilter preserves state across brief gaps (correct for DTCT-03). But after a very long gap (e.g., 30+ seconds), the filter's previous timestamp is far in the past. The first call after recovery computes a huge `dt`, which causes the derivative estimate to spike, potentially producing erratic filtered output on the first frame back.
**Why it happens:** OneEuroFilter uses `(rawValue - previousRaw) / dt` for derivative. After a long gap, dt is large, but the person may have moved significantly, making the derivative reasonable. However, the smoothing factor `alpha = 2*pi*cutoff*dt / (2*pi*cutoff*dt + 1)` approaches 1.0 for large dt, so the filter essentially passes through the raw value. This is actually the correct behavior (trust the new measurement after a long gap).
**How to avoid:** For gaps shorter than the hold timeout (5s), preserve filter state (per CONTEXT.md). For gaps where the hold timeout expired and home return completed, reset the filter on recovery (the pastor is returning from offscreen, filter state is meaningless). The threshold for reset should be: reset if detection state was RETURNING_HOME, preserve if state was HOLDING.
**Warning signs:** Jittery first few frames after recovery from a long gap.

### Pitfall 3: Conflating Detection Loss with Low Confidence
**What goes wrong:** A frame where `bPersonWasDetected=True` but confidence is 0.05 should go through the confidence gate (existing behavior), while `bPersonWasDetected=False` should go through the detection dropout filter (new behavior). If detection loss is handled AFTER the confidence gate, the dropout filter never sees the `bPersonWasDetected=False` frames.
**Why it happens:** The current pipeline checks `bPersonWasDetected` at STEP 1 (line 278) with an early return before reaching the confidence gate at STEP 3 (line 315).
**How to avoid:** The dropout filter MUST be at STEP 1 (the current detection check location). After the dropout threshold is exceeded, detection loss enters the confidence gate path with an effective confidence of 0.0 (below hold threshold), which naturally triggers the hold/return behavior.
**Warning signs:** Dropdown filter appears to have no effect because detection-loss frames are handled before it runs.

### Pitfall 4: Recovery Ramp Interacting with Confidence Scale
**What goes wrong:** If the pastor reappears at the edge of the frame with low confidence (0.4), the confidence scale is already ~0.25 (smoothstep between 0.3 and 0.7). Multiplying by the recovery ramp (starting at 0.0) gives effectively zero correction. The camera appears frozen even though the person is detected.
**Why it happens:** Two multiplicative attenuation factors compounding to near-zero.
**How to avoid:** This is actually correct behavior -- the camera should ease back gently when the person reappears with low confidence. The recovery ramp is short (0.3-0.5s) and the person's confidence typically improves quickly as they move into clearer view. If this proves too conservative in practice, the ramp duration is configurable.
**Warning signs:** Camera seems slow to re-engage after detection recovery with marginal confidence. Tunable via `flRecoveryEasingDurationSeconds`.

### Pitfall 5: Timestamp Gap in dt Calculation After Hold
**What goes wrong:** During hold, `_dPreviousControlTimestampSeconds` is not updated (no control path runs). When tracking resumes, `dt = now - previousTimestamp` is the entire hold duration (potentially seconds). This dt exceeds `flDtMaxSeconds`, causing the first recovery frame to be skipped.
**Why it happens:** The current STEP 2 dt calculation skips frames with dt > flDtMaxSeconds.
**How to avoid:** When transitioning from HOLDING/RETURNING_HOME back to TRACKING, explicitly set `self._dPreviousControlTimestampSeconds = dNow` before the recovery frame's control path runs. This gives the recovery frame a nominal dt (similar to first-frame handling).
**Warning signs:** First frame after recovery is always skipped (logged as "Control update skipped: dt=... exceeds max").

## Code Examples

### Example 1: Detection State Machine Integration Point

The primary insertion point is tracker_controller.py at STEP 1 (line 276-279). The current code:

```python
# STEP 1: Skip if no person detected
# Phase 4 will add hold logic here
if not obCurrentSample.bPersonWasDetected:
    return
```

Becomes the dropout filter and detection state management:

```python
# STEP 1: Detection handling (Phase 4)
if not obCurrentSample.bPersonWasDetected:
    self._iConsecutiveDroppedFrames += 1
    if self._iConsecutiveDroppedFrames < self._iDropoutFrameThreshold:
        # Brief dropout: hold position silently, preserve filter state
        return
    # Genuine detection loss: hard freeze
    if self._eDetectionState == DetectionState.TRACKING:
        self._eDetectionState = DetectionState.HOLDING
        self._obMotionProfiler.reset()  # Prevent stale velocity on recovery
        logger.debug("Detection lost: entering HOLDING state")
    # Fall through to low-confidence timer path (flConfidenceScale = 0.0)
    # ... (dt calculation, then confidence gate with scale = 0.0)
else:
    # Detection present
    if self._iConsecutiveDroppedFrames > 0:
        self._iConsecutiveDroppedFrames = 0
    if self._eDetectionState in (DetectionState.HOLDING, DetectionState.RETURNING_HOME):
        # Recovery: ease back into tracking
        self._dRecoveryStartTimestamp = float(obCurrentSample.dSampleTimestampSeconds)
        self._dPreviousControlTimestampSeconds = float(obCurrentSample.dSampleTimestampSeconds)
        if self._eDetectionState == DetectionState.RETURNING_HOME:
            self._obHomeReturnController.reset()  # Cancel return
            self._obPoseFilter = None  # Reset filter (state too stale)
        self._eDetectionState = DetectionState.TRACKING
        self._flLowConfidenceTimer = 0.0
        logger.debug("Detection recovered: entering TRACKING state")
```

### Example 2: New Config Fields

```python
# In SystemConfiguration dataclass (config_manager.py):

# Detection handling (DTCT-01, DTCT-02, DTCT-03)
iDetectionDropoutFrameThreshold: int = 3       # Frames before declaring detection lost
flRecoveryEasingDurationSeconds: float = 0.4   # Ramp-up duration on recovery (seconds)
```

### Example 3: Recovery Easing Ramp Applied to Confidence Scale

```python
# In _execute_centering_control_algorithm(), after computing flConfidenceScale at STEP 3:

# Apply recovery easing ramp (Phase 4)
if self._dRecoveryStartTimestamp is not None:
    flRecoveryElapsed = dNow - self._dRecoveryStartTimestamp
    if flRecoveryElapsed < self.flRecoveryEasingDurationSeconds:
        flT = flRecoveryElapsed / self.flRecoveryEasingDurationSeconds
        flRecoveryScale = flT * flT * (3.0 - 2.0 * flT)  # smoothstep
        flConfidenceScale *= flRecoveryScale
    else:
        self._dRecoveryStartTimestamp = None  # Ramp complete
```

### Example 4: Debug Overlay State Display

```python
# In draw_visualization_overlay() in main.py, inside the bShowDebugInfo block:

# Detection state (Phase 4) -- debug overlay only
sDetectionState = "TRACKING"  # default
if hasattr(obTrackerController, '_eDetectionState'):
    sDetectionState = obTrackerController._eDetectionState.value
vInfoLines.append(f"Detection: {sDetectionState}")
```

### Example 5: Test Pattern for Single-Frame Dropout

```python
def test_single_dropped_frame_causes_no_motor_movement(self):
    """DTCT-03: One frame with no detection, next frame detection back.
    Motor commanded angle should not change."""
    obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

    # Establish baseline with person at 700px (off center)
    _run_frames(obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
                iFrameCount=10, flPersonX=700.0, flConfidence=0.9)
    flBaselineAngle = obTracker._flLastCommandedAngle

    # Single dropped frame
    _run_frames(obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
                iFrameCount=1, bDetected=False)

    # Detection back
    _run_frames(obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
                iFrameCount=1, flPersonX=700.0, flConfidence=0.9)

    # Commanded angle should resume from baseline (no drift during dropout)
    flPostDropoutAngle = obTracker._flLastCommandedAngle
    # Allow tiny delta from continued tracking, but no jump or reset
    assert abs(flPostDropoutAngle - flBaselineAngle) < 0.5
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Immediate return on detection loss | Hold + timeout + S-curve return | Phase 4 (this phase) | Camera no longer snaps to home on brief detection drops |
| No dropout filtering | Frame-counter filter (N consecutive misses required) | Phase 4 (this phase) | Single MediaPipe misses are invisible to the audience |
| Instant re-engagement after hold | Recovery easing ramp (smoothstep over 0.3-0.5s) | Phase 4 (this phase) | Camera re-acquires target like a human operator, not a robot |

**Deprecated/outdated:**
- Current STEP 1 `if not bPersonWasDetected: return` -- replaced by dropout filter + detection state machine

## Open Questions

1. **OneEuroFilter reset threshold**
   - What we know: Preserve across brief gaps (< dropout threshold). Reset when returning from RETURNING_HOME state (filter state is stale). The filter handles large dt correctly by passing raw value through (alpha approaches 1.0).
   - What's unclear: For long HOLDING periods (e.g., 5s hold + person reappears), should the filter be reset or preserved? The math suggests it works either way since large dt makes alpha ~1.0.
   - Recommendation: Preserve during HOLDING (simpler, math handles it), reset only on RETURNING_HOME exit. Can be revised if testing reveals jitter issues on recovery.

2. **Settings panel organization**
   - What we know: Two new config fields (iDetectionDropoutFrameThreshold, flRecoveryEasingDurationSeconds). Existing "Confidence" section already has the timeout slider.
   - What's unclear: Whether these are prominent enough to warrant their own section.
   - Recommendation: Add a "Detection Handling" section with just these two parameters, keeping it small. The existing flConfidenceLowTimeoutSeconds stays in "Confidence" since it serves both detection loss and low confidence.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of `tracker_controller.py`, `home_return_controller.py`, `pose_filter.py`, `motion_profiler.py`, `config_manager.py`, `live_settings_panel.py`, `main.py`
- Existing test patterns from `test_motion_integration.py`, `test_home_return.py`, `conftest.py`
- CONTEXT.md user decisions from `/gsd:discuss-phase`

### Secondary (MEDIUM confidence)
- OneEuroFilter behavior for large dt values -- verified by analyzing the mathematical formulas in `pose_filter.py` (alpha = 2*pi*fc*dt / (2*pi*fc*dt + 1) approaches 1.0 as dt increases)
- Frame-counter vs time-based dropout filter tradeoffs -- standard practice in real-time tracking systems

### Tertiary (LOW confidence)
- None. All findings are directly verifiable from the codebase.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - No new dependencies. All components exist and are tested.
- Architecture: HIGH - Direct codebase analysis. Integration points are explicitly marked with "Phase 4" comments. Existing patterns are well-established.
- Pitfalls: HIGH - Identified from code inspection (stale profiler velocity, timestamp gaps, filter reset policy). Each pitfall has a clear prevention strategy.

**Research date:** 2026-03-01
**Valid until:** 2026-04-01 (stable -- no external dependency changes expected)
