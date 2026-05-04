---
status: partial
phase: 03-camera-i-o
source: [03-VERIFICATION.md]
started: 2026-05-05
updated: 2026-05-05
---

## Current Test

[awaiting human testing — deferred to Phase 8 QA-04 stage smoke]

## Tests

### 1. Real OBS Virtual Camera end-to-end smoke (real cv2 + DirectShow path through OpenCvVideoSource)
expected: With OBS Studio running and 'Start Virtual Camera' toggled on, ObsCamera enumerates the live DirectShow registry, opens device via cv2.VideoCapture(idx, CAP_DSHOW), and delivers a steady 1920x1080 @ 30 fps stream of Frame DTOs with monotonic perf_counter_ns timestamps for >= 30 s with no stalls and no fallback to 720p
why_human: FakeVideoSource exercises every orchestrator branch but OpenCvVideoSource real-cv2 paths and the FilterGraph DirectShow enumeration cannot run in CI without a connected camera + running OBS Virtual Camera. Plan 03-01 acceptance and 03-CONTEXT.md explicitly defer to Phase 8 / QA-04 stage smoke.
result: [pending]

### 2. Real OBS-not-running hard-fail (real DirectShow enumeration without OBS)
expected: With OBS Studio NOT running (no virtual camera registered), ObsCamera.start() raises OBSCameraNotFoundError carrying expected='OBS Virtual Camera' and the actual operator-machine device list (FaceTime HD / Logitech / etc.), and exits the application loudly without falling back to a webcam
why_human: Discovery matrix is fully covered by FakeFilterGraph stubs but the real DirectShow registry path requires a Windows host without OBS VCam installed. Same QA-04 deferral applies.
result: [pending]

### 3. Real warmup-window 1080p->720p fallback against an actual slow camera (or load-induced grab budget breach)
expected: With a real device that cannot sustain 1080p @ 30 fps (or with simulated CPU load forcing >40 ms inter-grab p95), capture transitions to 1280x720 inside the 2 s warmup with one camera_resolution_fallback WARN and continues running
why_human: Fallback decision logic is fully covered by short-fuse-monkeypatched FakeVideoSource scripts but the real-cv2 release+reopen at 1280x720 requires hardware. QA-04 deferral.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
