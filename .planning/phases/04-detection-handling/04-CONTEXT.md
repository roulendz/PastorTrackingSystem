# Phase 4: Detection Handling - Context

**Gathered:** 2026-03-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Graceful camera behavior when person detection is lost, intermittent, or the pastor leaves frame. The camera holds position during gaps, returns home after timeout, and resumes tracking smoothly when detection returns. No new detection capabilities or multi-person tracking -- just robust handling of detection state transitions.

</domain>

<decisions>
## Implementation Decisions

### Hold behavior
- Detection loss (bPersonWasDetected=False) shares the same path as low confidence -- feeds into the existing low-confidence timer
- Hard freeze when detection drops: camera stops immediately, any in-progress S-curve or correction halts
- Same timeout for both detection loss and low confidence (flConfidenceLowTimeoutSeconds, default 5s)
- Same HomeReturnController S-curve parameters for detection-loss home return as safe-zone home return
- Stop sending motor commands during hold -- stepper motor holds position naturally via holding torque

### Dropout filtering
- Brief detection gaps (single or few frames) must cause zero visible camera movement
- Hold position during brief gaps, resume normal tracking when detection returns
- OneEuroFilter state preserved across brief gaps for smoother re-acquisition
- Detection state (TRACKING / HOLDING / RETURNING_HOME) shown in debug overlay only, no indicator in normal mode

### Recovery behavior
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

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `HomeReturnController` (control/home_return_controller.py): Full S-curve return state machine, already handles cancel-on-exit. Detection-loss home return feeds directly into this.
- Low-confidence timer path (tracker_controller.py:315-339): Already accumulates timer, triggers HomeReturnController, does S-curve return. Detection loss can share this path.
- `OneEuroFilter` (tracking/pose_filter.py): Currently lazy-initialized on first valid detection. Needs modification to preserve state across brief gaps.
- `compute_confidence_scale_factor()` (tracking/pose_filter.py): Existing smoothstep between hold/full thresholds.
- `flConfidenceLowTimeoutSeconds` config (default 5.0): Already configurable in settings panel.

### Established Patterns
- Pipeline steps in `_execute_control_algorithm()` are numbered and documented (STEP 1-9)
- Config fields use Hungarian notation with dataclass defaults in config_manager.py
- DearPyGui slider callbacks update both config and live module attributes
- Debug overlay gated behind debug flag

### Integration Points
- `tracker_controller.py:276-279`: The "Phase 4 will add hold logic here" comment -- primary insertion point
- Confidence gate at lines 309-341: Detection loss joins here, feeding the same timer path
- OneEuroFilter initialization at lines 347-354: Needs conditional reset logic for detection gaps
- Settings panel (live_settings_panel.py): New parameters need sliders if added

</code_context>

<specifics>
## Specific Ideas

- Detection loss is "the same thing as low confidence, just more extreme" -- unified path keeps behavior consistent
- Hard freeze prevents any unexpected camera motion during gaps -- the audience should never notice a brief detection drop
- Easing back into tracking after reappearance should feel like a human camera operator re-acquiring the target -- not a snap
- The stepper motor's natural holding torque means no commands needed during hold -- simpler and less serial traffic

</specifics>

<deferred>
## Deferred Ideas

- Predictive tracking using last-known direction of travel -- could be its own enhancement phase
- Different timeout/S-curve parameters for detection loss vs low confidence -- premature until real-world testing reveals need
- Visual indicator for camera operators showing detection state -- could be added later if operators need it

</deferred>

---

*Phase: 04-detection-handling*
*Context gathered: 2026-03-01*
