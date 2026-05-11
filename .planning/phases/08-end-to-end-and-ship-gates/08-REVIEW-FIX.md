---
phase: 08-end-to-end-and-ship-gates
fixed_at: 2026-05-11T19:45:00Z
review_path: .planning/phases/08-end-to-end-and-ship-gates/08-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 8: Code Review Fix Report

**Fixed at:** 2026-05-11T19:45:00Z
**Source review:** .planning/phases/08-end-to-end-and-ship-gates/08-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (all WARNING; IN-01/IN-02 out of scope per `fix_scope: critical_warning`)
- Fixed: 6
- Skipped: 0

All six warnings are documentation reconciliations against `pastor_tracker/README.md`. Each fix was verified by re-reading the affected README section (Tier 1) and confirming the inserted text exactly matches the source-of-truth event names grepped from `pastor_tracker/src/`. README is markdown, so no syntax checker applies (Tier 3 fallback).

## Fixed Issues

### WR-01: Five fabricated structured-log event names

**Files modified:** `pastor_tracker/README.md`
**Commit:** `8b01cc1`
**Applied fix:** Reconciled five operator-facing log-event citations in README to the actual `structlog` event names emitted by the source. Specifically:
- `obs_camera_opened` -> `camera_started` (per `io/obs_camera.py:573`)
- `obs_camera_not_found` -> replaced with `OBSCameraNotFoundError` description noting the success-side events (`camera_discovered` / `camera_multiple_matches`) do not fire on the failure path (per `io/obs_camera.py:260-307`)
- `arduino_port_not_found` -> replaced with `ArduinoPortNotFoundError` description noting success-side events (`port_discovered` / `port_multiple_matches` / `port_manual_override`) do not fire on the failure path (per `io/arduino_transport.py:201-230`)
- `firmware_error code=11` -> `error_received code=11 code_name=HEARTBEAT_TIMEOUT` (per `io/arduino_motor.py:463-468`)
- `subject_lock_lost` / `subject_lock_acquired` -> `lock_loss` / `lock_acquired` (with `lock_reacquired` noted for the new-track-id re-acquire path, per `perception/subject_tracker.py:236-247, 320-332`)

### WR-02: TODO without tracking-issue number

**Files modified:** `pastor_tracker/README.md`
**Commit:** `3fc7ccf`
**Applied fix:** Removed the `<!-- TODO(v2-supply-chain): ... -->` HTML comment from line 41. The two options in REVIEW.md were (a) replace with `#NNN` issue reference and (b) move to ROADMAP.md "Deferred to v2". The repo has no live issue tracker (no `#NNN` references found anywhere) and ROADMAP.md has no "Deferred to v2" section. Chose the minimum-change reconciliation: delete the inline TODO marker. The prose paragraph on the next line (now line 41) already states the v2-supply-chain deferral explicitly to the operator -- the commitment is preserved without violating CLAUDE.md "TODO without issue number" since there is no longer a `TODO` token.

### WR-03: FOV calibration formula edge case (f=0) not guarded

**Files modified:** `pastor_tracker/README.md`
**Commit:** `4fa336a`
**Applied fix:** Appended the suggested guard sentence to step 6 of the FOV calibration procedure: "If you cannot see the bar at all (f -> 0), re-aim the camera before recording a measurement; the formula is undefined at f = 0."

### WR-04: "About 1/7 of the frame at 70 deg FOV" rounding is sloppy

**Files modified:** `pastor_tracker/README.md`
**Commit:** `410a403`
**Applied fix:** Replaced the magic-number "1/7 of the frame at 70 deg FOV" parenthetical with the generalized phrasing "about 14% at 70 deg FOV, 19% at 53 deg FOV", which carries both example points and matches the d=1.0 m / d=0.7 m examples earlier in the section. An operator who calibrates to a non-default FOV no longer gets a false negative from the 1/7 heuristic.

### WR-05: Pre-flight checklist item 7 assumes overlays draw before pipeline starts

**Files modified:** `pastor_tracker/README.md`
**Commit:** `5fe7d34`
**Applied fix:** Re-split steps 7 and 8 of the pre-flight checklist so the framing-target line and subject bbox checks happen AFTER pressing `S`. This matches the actual pipeline behaviour: `third_line_pixels()` and `bbox_rect_pixels()` return `None` until the tick task populates `snap.last_target_x_normalized` / `snap.last_subject_bbox_normalized`, which only happens after the `S` hotkey starts the pipeline.

### WR-06: `--ui` default-detection wording is ambiguous

**Files modified:** `pastor_tracker/README.md`
**Commit:** `fcddd15`
**Applied fix:** Reworded the Run section "default mode" sentence to "If neither flag is passed, UI mode is selected ...; this matches passing `--ui` explicitly." Removes the misleading "the default mode is `--ui`" phrasing that contradicts `__main__.py:236-244` where both `--ui` and `--headless` default to `False` and UI selection is decided by `headless_mode = bool(args.headless)` on line 250.

## Skipped Issues

None.

---

_Fixed: 2026-05-11T19:45:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
