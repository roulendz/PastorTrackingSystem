---
status: partial
phase: 04-perception
source: [04-VERIFICATION.md]
started: 2026-05-05T14:30:00Z
updated: 2026-05-05T14:30:00Z
---

## Current Test

[awaiting Phase 8 QA-04 stage smoke]

## Tests

### 1. PERC-01 — UltralyticsPoseEngine.detect production path on real hardware
expected: `UltralyticsPoseEngine.detect(frame)` runs end-to-end against a real `yolo11n-pose.pt` model on a GPU/CPU, emits a non-empty `list[Detection]` with `track_id` populated by BoT-SORT, and respects the `<100ms` per-frame budget on the dev box.
result: [pending — hardware-bound; CI exercises the seam via FakePoseEngine]

### 2. PERC-04 / PERC-05 — BoT-SORT lock stability under real stage noise
expected: With OBS VCam pointed at a live speaker on stage (flash photography, audience motion, interpreter at podium edge), the central-60% + highest-conf heuristic locks the speaker at t=0, the BoT-SORT `track_id` survives ≥ 30s without flicker, and lock loss > 2.0s correctly re-acquires via the central-frame heuristic.
result: [pending — physical-world test; cannot reproduce in CI]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps

None — both items are documented deferrals to Phase 8 QA-04 per CONTEXT.md (Out of scope: "On-stage smoke test against a real OBS install + real speaker (Phase 8 / QA-04)"). They are not Phase 4 contract gaps.
