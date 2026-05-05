---
status: partial
phase: 03-camera-i-o
source: [03-VERIFICATION.md]
started: 2026-05-05
updated: 2026-05-05
---

## Current Test

[smokes 1 + 3 run on real OBS @ 1080p25 PAL host; smoke 2 deferred to QA-04 (Windows DirectShow filter persistence)]

## Tests

### 1. Real OBS Virtual Camera end-to-end smoke (real cv2 + DirectShow path through OpenCvVideoSource)
expected: With OBS Studio running and 'Start Virtual Camera' toggled on, ObsCamera enumerates the live DirectShow registry, opens device via cv2.VideoCapture(idx, CAP_DSHOW), and delivers a steady stream of Frame DTOs at the configured fps with monotonic perf_counter_ns timestamps for >= 30 s with no stalls and no fallback to 720p
result: PASS — 2026-05-05 09:56-09:57 on real host with OBS at PAL 1080p25 (PTS_CAPTURE_FPS=25). 751 frames in 30.04 s = exactly 25.00 fps @ (1920, 1080). camera_discovered fired with available_count=2 index=1. camera_started fired with fps=25 height=1080 width=1920. Monotonic timestamp assertion held across all 751 frames. Clean thread exit (camera_thread_exited clean=True). last_error=None. final_state=CLOSED after stop().

### 2. Real OBS-not-running hard-fail (real DirectShow enumeration without OBS)
expected: With OBS Studio NOT running (no virtual camera registered), ObsCamera.start() raises OBSCameraNotFoundError carrying expected='OBS Virtual Camera' and the actual operator-machine device list (FaceTime HD / Logitech / etc.), and exits the application loudly without falling back to a webcam
result: DEFERRED to Phase 8 QA-04 — closing OBS Studio does NOT unregister the OBS Virtual Camera DirectShow filter on Windows; the filter is registered at OBS install time and persists across OBS process lifecycle. Smoke 2 cannot be run on a host with OBS installed. The logic is fully covered by `test_discover_missing_with_empty_devices`, `test_discover_missing_raises_with_device_list`, and `test_discover_case_sensitive_match` against `FakeFilterGraph` stubs. QA-04 stage smoke must run on a separate Windows host without OBS installed (or by uninstalling OBS for the test).

### 3. Real warmup-window 1080p->720p fallback against an actual slow camera (or load-induced grab budget breach)
expected: With a real device that cannot sustain 1080p @ 30 fps (or with simulated CPU load forcing >40 ms inter-grab p95), capture transitions to 1280x720 inside the 2 s warmup with one camera_resolution_fallback WARN and continues running
result: INCONCLUSIVE on host — 2026-05-05 10:13 ran smoke 3 against real OBS at 1080p25 for 6 s; host had sufficient headroom, p95 budget never breached, no fallback fired. Final resolution stayed (1920, 1080) at 24.92 fps across 152 frames. This is the expected outcome on an unloaded host. Real-load harness (stress-ng pinned CPU + competing OBS render budget) deferred to Phase 8 QA-04. Fallback decision logic is fully covered in CI by `test_warmup_breach_triggers_fallback`, `test_post_warmup_breach_inhibited`, `test_720p_breach_logs_error_continues`, `test_fallback_one_shot`, and `test_fallback_does_not_trip_stall_on_slow_first_frame` against short-fuse-monkeypatched FakeVideoSource scripts.

## Summary

total: 3
passed: 1
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps

None. Smoke 2 + smoke 3 are environmental constraints (DirectShow filter persistence, no real load harness) — both are pre-deferred to Phase 8 QA-04 by `03-CONTEXT.md` and Plan 03-01 acceptance criteria. The phase orchestrator works correctly on the real cv2 + pygrabber DirectShow path as proven by smoke 1.

## Smoke Harness

`smoke_real_obs.py` lives at `.planning/phases/03-camera-i-o/smoke_real_obs.py`. Re-run with:

```powershell
$env:PTS_CAPTURE_FPS = "25"
$env:PYTHONPATH = "pastor_tracker/src"
.\pastor_tracker\.venv\Scripts\python.exe .planning\phases\03-camera-i-o\smoke_real_obs.py 1   # e2e at 25 fps
.\pastor_tracker\.venv\Scripts\python.exe .planning\phases\03-camera-i-o\smoke_real_obs.py 2   # no-OBS hard-fail (needs host with OBS uninstalled)
.\pastor_tracker\.venv\Scripts\python.exe .planning\phases\03-camera-i-o\smoke_real_obs.py 3   # fallback under load (run alongside stress harness)
```
