# Research Summary: Pastor Tracking System Improvements

**Domain:** Real-time camera tracking with motor synchronization
**Researched:** 2026-02-15
**Overall confidence:** MEDIUM-HIGH

## Executive Summary

The Pastor Tracking System's core timing bug -- virtual center line drift during motor movement -- is an architectural problem, not a library gap. The current system timestamps camera frames AFTER the blocking `VideoCapture.read()` call returns, creating ~33ms of uncertainty between when the frame was actually captured and when Python records the timestamp. Meanwhile, the Arduino-to-PC clock offset estimator uses a slow-converging exponential moving average (98%/2% weighting) that takes ~50 feedback cycles to stabilize. These two timing errors compound: the system calculates "where the person is" using a motor angle that is wrong by the amount the motor moved during the timestamp error window. At max zoom (6.8 degree FOV over 1920 pixels), even 1 degree of motor angle error shifts the virtual center by ~282 pixels -- nearly 15% of the frame width.

The motion smoothing problem is well-served by the ruckig library (MIT, v0.15.3), which provides real-time, jerk-limited trajectory generation. This is the industry standard for robotics motion planning and directly addresses the "human-like motion curves" requirement. For filtering MediaPipe pose detection noise, the One Euro Filter provides adaptive low-pass filtering that reduces jitter without adding lag during fast pastor movement -- exactly the tradeoff needed.

The testing gap is the most critical infrastructure debt. No test suite exists. The recommended approach combines pytest for structure, Hypothesis for property-based testing of control algorithms, and a custom deterministic simulation harness that replaces hardware interfaces (camera, motor, clock) with scriptable fakes. This allows running entire church-service-length scenarios in milliseconds without hardware. The python-control library (Caltech, v0.10.2) provides the mathematical plant model for the motor system.

A significant constraint is the Python 3.10 runtime. NumPy 2.3+, SciPy 1.16+, and InterpolatePy all require Python >= 3.11. Python 3.10 reaches end-of-life in October 2026. Upgrading to Python 3.12 should be an early roadmap item -- it unlocks better library compatibility and provides 15-60% performance improvements.

## Key Findings

**Stack:** Upgrade to Python 3.12; add ruckig for trajectory generation, OneEuroFilter for pose noise filtering, Hypothesis for property-based testing, python-control for simulation modeling. The time sync fix is code architecture, not a library.

**Architecture:** The timing pipeline needs restructuring: `grab()` -> `timestamp` -> `retrieve()` instead of `read()` -> `timestamp`. Motor angle interpolation should use quadratic (not linear) interpolation during acceleration phases. Clock offset estimation should use linear regression over a sliding window instead of EMA.

**Critical pitfall:** Attempting to fix the timing bug by adding more filtering will mask it, not fix it. The root cause is that camera frame timestamps and motor position timestamps are on different time bases with uncorrected systematic offset. Filtering smooths the symptom (jitter) while leaving the cause (drift) intact.

## Implications for Roadmap

Based on research, suggested phase structure:

1. **Python Upgrade + Test Infrastructure** - Foundation
   - Addresses: Python 3.10 EOL risk, zero test coverage
   - Avoids: Building on a platform that loses library support in 8 months
   - Contents: Upgrade to 3.12, add pytest + hypothesis, create simulation harness skeleton, write first property-based tests for existing control algorithms

2. **Time Synchronization Fix** - Core Bug Fix
   - Addresses: Virtual center line drift (the primary bug)
   - Avoids: Months of filter tuning that treats symptoms not causes
   - Contents: Refactor camera capture to grab/retrieve pattern, implement proper clock offset estimation, add camera latency calibration, validate with simulation harness

3. **Motion Smoothing** - Quality of Motion
   - Addresses: Jerky camera movement, non-human-like tracking
   - Avoids: Premature smoothing before timing is correct (would hide timing errors)
   - Contents: Integrate ruckig for trajectory generation, add OneEuroFilter for pose detection noise, implement velocity-aware deadband

4. **Simulation Test Coverage** - Regression Prevention
   - Addresses: "Can only test at Sunday service" problem
   - Avoids: Regression of fixed timing/smoothing issues
   - Contents: Scripted scenario library (pastor walks, stands, gestures), full-service replay testing, automated performance regression checks

**Phase ordering rationale:**
- Phase 1 before 2: Need test infrastructure to validate timing fix without manual testing
- Phase 2 before 3: Motion smoothing on broken timing produces smooth-but-wrong output
- Phase 3 before 4: Need the full system working correctly to define meaningful test scenarios
- Phase 4 uses all prior work: Simulation harness from Phase 1, timing from Phase 2, smoothing from Phase 3

**Research flags for phases:**
- Phase 2: Likely needs deeper research on camera-specific latency measurement and Arduino timestamp precision
- Phase 3: May need deeper research on ruckig integration with AccelStepper's trapezoidal profile (two motion planners in series)
- Phase 1, 4: Standard patterns, unlikely to need additional research

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified on PyPI with dates. Compatibility matrix built |
| Time Sync | HIGH | Root cause identified by reading actual codebase. Fix approach is well-established (grab/retrieve, clock offset estimation) |
| Motion Smoothing | MEDIUM-HIGH | ruckig is well-proven in robotics but untested on this specific AccelStepper + serial pipeline |
| Testing | MEDIUM | Hypothesis is proven for PBT. Custom simulation harness approach is standard but implementation effort is non-trivial |
| Python Upgrade | HIGH | Version compatibility fully mapped. MediaPipe 0.10.32 confirmed for 3.12 |

## Gaps to Address

- **Camera latency calibration method:** Need to measure actual frame-to-timestamp delay for the specific Sony AX700 capture pipeline. May vary by resolution/FPS setting.
- **Ruckig + AccelStepper interaction:** Two motion planners in series (ruckig generates target, AccelStepper follows with its own trapezoidal profile) may cause unexpected dynamics. Needs empirical testing.
- **DearPyGui Python 3.12 compatibility:** Not verified. May need version update or replacement.
- **Windows-specific ruckig build:** ruckig uses C++ bindings (nanobind). Need to verify pre-built Windows wheels exist for Python 3.12.
