# Phase 3: Motion Smoothing - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Camera movements look human-operated with smooth starts, smooth stops, and no jitter from pose detection noise. S-curve motion profiles replace any linear/abrupt velocity changes. Pose filtering suppresses detection noise on stationary subjects. Confidence-based response adjusts tracking behavior based on detection quality. Detection loss handling (no person detected at all) belongs in Phase 4.

</domain>

<decisions>
## Implementation Decisions

### Motion profile feel
- Slow human pan style — gentle ease-in like a skilled camera operator smoothly picking up the shot
- Gentle coast to stop — visible deceleration with momentum feel, takes a beat to settle
- Symmetric S-curves — same curve shape for acceleration and deceleration
- Direction reversals: smooth decel through zero, brief pause, smooth accel in new direction (no instant flick)
- Camera matches pastor's pace — pastor stays centered at all times, no intentional lag
- Camera speed adapts smoothly to pastor speed changes — walks faster, camera speeds up (with S-curve smoothing applied)
- Church broadcast style feel — smooth, deliberate camera work, no flashy moves
- Dead zone for micro-movements — existing adjustable dead zone (shoulder center) is preserved; S-curve smoothing works in addition to it, not replacing it
- S-curve parameters exposed in DearPyGui settings panel for runtime tuning

### Home return behavior
- Short delay (~1-2 seconds) before starting home return — confirms pastor is staying in safe zone, not just passing through
- Home return delay is runtime-tunable via settings panel
- S-curve return at same speed as normal tracking — consistent motion feel
- If pastor leaves safe zone during mid-return: immediately cancel return and resume tracking (smooth transition, no completing return first)
- Zero overshoot at home position — camera lands exactly at home with zero velocity
- Safe zone definition unchanged — already configurable, this phase just adds S-curve return within it
- Camera completely locked at home when pastor is in safe zone — zero movement from gestures or swaying at lectern
- Slightly faster S-curve re-engagement when leaving home — tighter ease-in since pastor has clearly committed to walking
- Claude's discretion: whether to track small movements within safe zone during return, and whether to use velocity-based pass-through detection beyond the delay timer

### Jitter filtering
- Absolutely zero visible camera movement when pastor is stationary — any wiggle is a bug
- Filter placement (pose input vs motor output) is Claude's discretion based on architecture fit
- Jitter-vs-real-movement detection approach is Claude's discretion (sustained direction, magnitude threshold, or hybrid)
- Jitter filter parameters exposed in DearPyGui settings panel for runtime tuning

### Confidence-based response
- Gradual blend: tracking strength scales smoothly with confidence — high confidence = full correction, low confidence = reduced correction
- Below a hold threshold: camera holds current position entirely (freeze in place)
- Sustained low confidence triggers a home return timer — treat it like soft detection loss (hold for timeout, then S-curve home)
- Confidence threshold and blend curve exposed in DearPyGui settings panel for runtime tuning

### Claude's Discretion
- Filter placement in the pipeline (pre-control vs post-control)
- Jitter detection algorithm (sustained direction, magnitude, or hybrid approach)
- Whether to blend tracking during safe-zone return or suspend it
- Whether to add velocity-based pass-through detection on top of the delay timer
- S-curve mathematical implementation details
- Exact default values for all tunable parameters

</decisions>

<specifics>
## Specific Ideas

- Church broadcast reference — smooth, deliberate, professional livestream camera work
- "Smooth but sticky to pastor" carries forward from Phase 2 — no jitter, but responsive to real movement
- Symmetric curves ensure direction reversals look natural (decel-pause-accel through zero)
- All new parameters should be runtime-tunable in the settings panel — this is a feel-intensive phase that needs live dialing-in
- Existing dead zone and safe zone logic preserved — this phase layers S-curve motion and filtering on top

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-motion-smoothing*
*Context gathered: 2026-02-26*
