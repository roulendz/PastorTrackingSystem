---
status: partial
phase: 08-end-to-end-and-ship-gates
source: [08-VERIFICATION.md]
started: 2026-05-11
updated: 2026-05-11
---

## Current Test

[awaiting human testing on next stage rehearsal — requires real Uno + real OBS VCam + real speaker]

## Tests

### 1. Zero overshoot

expected: Motor never overshoots framing target and recoils. Operator observes camera glide to target and stop cleanly — no visible past-the-mark correction.
result: [pending]

### 2. Zero audible motor jerk

expected: Pan is silent or smooth-hum. No clack, no chatter from stepper. Operator listens during the entire 5-minute run.
result: [pending]

### 3. Zero lock-loss to audience or interpreter

expected: Camera does not jump to a non-primary subject for more than 2 s. Speaker remains framed throughout (audience/interpreter walk-throughs trigger at most a 2 s re-acquire that returns to the stage center).
result: [pending]

### 4. Rule-of-thirds framing holds

expected: Moving-right subject sits in the left third (≈ 0.333). Moving-left subject sits in the right third (≈ 0.667). Dwelling subject sits at center (≈ 0.500). Matches INTENT-03.
result: [pending]

### 5. E-stop works

expected: Pressing `E` halts the motor within one tick — perceived latency < 50 ms. Motor stays halted until operator restarts the app (e-stop latches faulted state).
result: [pending]

### 6. ≥ 5 minutes continuous tracking

expected: No crashes, no `ERROR:` log lines, no `firmware_error code=11` heartbeat events. Tracking remains active for the full duration.
result: [pending]

## Summary

total: 6
passed: 0
issues: 0
pending: 6
skipped: 0
blocked: 0

## Gaps

(None — all items pending hardware availability. To complete: schedule on next stage rehearsal, run pastor-tracker for ≥ 5 minutes with a real speaker, record results above, then re-run /gsd-verify-work 8.)

## Related Ship-Gate Deferrals

The phase verification also flagged two environmental gate regressions deferred to follow-up hotfix plans:

- **QA-02 (mypy --strict)** — installed mypy 1.20.2 wheel has a broken `mypy.expandtype.ExpandTypeVisitor` symbol that crashes the `pydantic.mypy` plugin at import. Affects 925 `[arg-type]` errors on test fixtures. Not a Phase 8 code regression; environment-level. Fix path: pin a known-good mypy version or wait for upstream wheel repair.
- **QA-01 (ruff format --check)** — 73 files exceed line-length / format drift accumulated through Phases 1-7. `.pre-commit-config.yaml` never gated `ruff format --check`. Fix path: run `.venv\Scripts\ruff.exe format src tests` and commit as `style(08): apply ruff format across src + tests`.

Both are separate from the QA-04 on-stage smoke and do not require human-on-stage validation.
