# Roadmap: Pastor Tracking Camera System

## Overview

This roadmap fixes the core virtual-center-line drift bug, adds human-like motion quality, and builds the test infrastructure needed to iterate daily instead of weekly at church services. The work flows from a clean foundation (test harness + dead code removal) through the timing fix, motion smoothing, detection behavior, and finally full scenario validation -- each phase building on the last with verifiable results.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Test Foundation and Code Cleanup** - Synthetic test harness, motor simulation, dead code removal
- [x] **Phase 2: Time Synchronization** - Fix the core timing bug causing virtual center drift
- [ ] **Phase 3: Motion Smoothing** - Human-like camera motion with S-curve profiles and pose filtering
- [ ] **Phase 4: Detection Handling** - Graceful behavior when person detection is lost or intermittent
- [ ] **Phase 5: Scenario Validation** - Scripted test scenarios and automated tolerance verification

## Phase Details

### Phase 1: Test Foundation and Code Cleanup
**Goal**: The tracking pipeline runs end-to-end without any physical hardware, against a clean codebase with no dead code
**Depends on**: Nothing (first phase)
**Requirements**: TEST-01, TEST-04, TEST-05, CODE-01, CODE-02, CODE-03
**Success Criteria** (what must be TRUE):
  1. Running `python main.py --video` with a synthetic video source and SimulatedMotorInterface produces a working tracking loop with no hardware connected
  2. SimulatedMotorInterface accelerates, decelerates, and respects velocity limits like the real motor -- not instant teleportation to target angle
  3. No dead code remains -- every module and function is reachable from main.py or tests
  4. No silent exception swallowing -- all except blocks either handle specific exceptions or log with traceback
  5. FOV is correctly set to ~6.8 degrees for Sony AX700 at max optical zoom (validated by pixel-to-degree conversion matching expected value)
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md -- Core interface extensions (SimulatedMotorInterface, CameraInterface video support, FOV fix)
- [x] 01-02-PLAN.md -- Code cleanup (silent exception fixing, dead code removal)
- [x] 01-03-PLAN.md -- Test infrastructure (pytest setup, fixtures, motor/camera/FOV/pipeline tests)

### Phase 2: Time Synchronization
**Goal**: The virtual center line stays locked to the physical background at all motor velocities
**Depends on**: Phase 1 (needs test harness to validate without hardware)
**Requirements**: SYNC-01, SYNC-02, SYNC-03, SYNC-04, SYNC-05, SYNC-06
**Success Criteria** (what must be TRUE):
  1. Virtual center line stays within 2 pixels of correct position at motor velocities up to 45 degrees/s (verified by synthetic test harness)
  2. All timing in the codebase uses time.perf_counter -- grep for time.time() returns zero hits
  3. Camera frame timestamps are captured between grab() and retrieve() calls, not after blocking read()
  4. Control algorithms accept delta time as a parameter and produce identical output for identical inputs (deterministically testable)
  5. Motor angle interpolation uses quadratic interpolation during acceleration phases
**Plans**: 4 plans

Plans:
- [x] 02-01-PLAN.md -- Clock abstraction (RealClock/FakeClock DI), control algorithm dt parameter, time.time() purge
- [x] 02-02-PLAN.md -- Camera grab()/retrieve() split, TrackerController dt clamping, config parameters
- [x] 02-03-PLAN.md -- Motor interpolation upgrade (MotorState accelState, Hermite interpolation, linear regression clock sync)
- [x] 02-04-PLAN.md -- Validation test suite (clock, determinism, interpolation accuracy, SYNC-06 2-pixel RMS) + debug overlay

### Phase 3: Motion Smoothing
**Goal**: Camera movements look human-operated -- smooth starts, smooth stops, no jitter from pose detection noise
**Depends on**: Phase 2 (smoothing on broken timing produces smooth-but-wrong output)
**Requirements**: MOTN-01, MOTN-02, MOTN-03, MOTN-04
**Success Criteria** (what must be TRUE):
  1. Camera starts and stops moving with visible ease-in/out curves -- no abrupt velocity changes visible in the output
  2. When the pastor enters the home safe zone, the camera glides to home position with decelerating S-curve -- not a linear clamp or sudden stop
  3. When MediaPipe pose detection jitters by a few pixels frame-to-frame on a stationary person, the camera does not visibly move
  4. Low-confidence detections (partially occluded pastor) produce noticeably gentler camera corrections than high-confidence detections
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md -- Core motion smoothing modules (OneEuroFilter, MotionProfiler, HomeReturnController, confidence scaling) with TDD tests and config fields
- [x] 03-02-PLAN.md -- Integration into TrackerController pipeline and DearPyGui settings panel sliders for runtime tuning

### Phase 4: Detection Handling
**Goal**: Camera behaves gracefully when the pastor walks out of frame, is briefly occluded, or detection drops intermittently
**Depends on**: Phase 3 (detection-loss return uses S-curve motion from MOTN-02)
**Requirements**: DTCT-01, DTCT-02, DTCT-03
**Success Criteria** (what must be TRUE):
  1. When detection is lost, the camera holds its current position for 5 seconds (configurable) without any drift
  2. After the hold timeout, the camera returns to home using the same smooth S-curve easing as normal home return
  3. A single dropped detection frame (one frame with no person, next frame person is back) causes zero visible camera movement
**Plans**: TBD

Plans:
- [ ] 04-01: TBD

### Phase 5: Scenario Validation
**Goal**: The full tracking system passes automated tests across a library of realistic pastor movement scenarios
**Depends on**: Phase 4 (needs complete feature set to validate against)
**Requirements**: TEST-02, TEST-03, TEST-06
**Success Criteria** (what must be TRUE):
  1. Scripted test scenarios exist for: walk left, walk right, pause at lectern, walk outside FOV, varying speeds -- and all pass
  2. Synthetic video includes a visible V-marker lectern at center, and the virtual center line visually aligns with it when the motor is at home position
  3. Automated test suite validates that virtual center stays within 2-pixel tolerance (SYNC-06) across all scripted scenarios without manual inspection
**Plans**: TBD

Plans:
- [ ] 05-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Test Foundation and Code Cleanup | 3/3 | Complete | 2026-02-15 |
| 2. Time Synchronization | 4/4 | Complete | 2026-02-22 |
| 3. Motion Smoothing | 2/2 | Complete | 2026-02-27 |
| 4. Detection Handling | 0/TBD | Not started | - |
| 5. Scenario Validation | 0/TBD | Not started | - |
