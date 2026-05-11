---
phase: 08-end-to-end-and-ship-gates
reviewed: 2026-05-11T00:00:00Z
depth: standard
files_reviewed: 1
files_reviewed_list:
  - pastor_tracker/README.md
findings:
  critical: 0
  warning: 6
  info: 2
  total: 8
status: findings_present
---

# Phase 8: Code Review Report

**Reviewed:** 2026-05-11
**Depth:** standard
**Files Reviewed:** 1 (`pastor_tracker/README.md`, 232 lines)
**Status:** findings_present

## Summary

Phase 8 is documentation-only: `pastor_tracker/README.md` was expanded from an 11-line stub to a 232-line operator manual. No source code changed. Review scope: factual accuracy of the README against the source of truth (`config.py`, `__main__.py`, `arduino_transport.py`, `obs_camera.py`, `subject_tracker.py`, `arduino_motor.py`, `ui/dashboard.py`).

Headline finding: the README cites six concrete **structured-log event names** as operator-actionable triage signals (`obs_camera_opened`, `obs_camera_not_found`, `arduino_port_not_found`, `firmware_error code=11`, `subject_lock_lost`, `subject_lock_acquired`). Only one of the six (`lock_acquired`, cited without prefix) exists in the codebase. The other five will never appear in stdout, directly defeating the troubleshooting playbook that tells operators to grep for them. These are doc bugs, but they are load-bearing doc bugs: an operator who follows the README during a stage rehearsal will not find the strings the README promised them. Classified WARNING (not BLOCKER) because the underlying behaviour is correct — the README mislabels it. No security issue, no source-code change required, but the doc must be reconciled to the actual event names before the README is treated as authoritative.

Other findings: one self-acknowledged TODO without a tracking issue (BLOCKER per CLAUDE.md "TODO without issue number" rule, downgraded to WARNING because the README is the artifact under review and the TODO is dated to v2 supply-chain hardening), several minor inaccuracies in the FOV calibration math wording, and two info-level cleanups.

## Warnings

### WR-01: Five fabricated structured-log event names

**File:** `pastor_tracker/README.md:53, 180, 189, 207, 216`
**Issue:** The README instructs the operator to look for specific structured-log event names that do not exist in the codebase. Verified by grepping `pastor_tracker/src/` for each string:

| README claim | README line | Actual event in source | Source location |
|-|-|-|-|
| `obs_camera_opened` | 53 (Verification section) | `camera_started` | `io/obs_camera.py:573` |
| `obs_camera_not_found` | 180 | (no such log event — `OBSCameraNotFoundError` is raised; closest log lines are `camera_discovered` / `camera_multiple_matches` on success only) | `io/obs_camera.py:260-307` |
| `arduino_port_not_found` | 189 | (no such log event — `ArduinoPortNotFoundError` is raised; success-side events are `port_discovered`, `port_multiple_matches`, `port_manual_override`) | `io/arduino_transport.py:201-230` |
| `firmware_error code=11` | 207 | `error_received code=11 code_name=HEARTBEAT_TIMEOUT` | `io/arduino_motor.py:463-468` |
| `subject_lock_lost` | 216 | `lock_loss` | `perception/subject_tracker.py:327` |
| `subject_lock_acquired` | 216 | `lock_acquired` | `perception/subject_tracker.py:244` |

This is a load-bearing inaccuracy: the README's Troubleshooting section tells operators that specific log lines confirm specific failure modes. An operator running `Select-String -Path stdout.log -Pattern 'obs_camera_not_found'` will get zero hits even when OBS is missing, then conclude the README is broken (or worse, that the system is fine when it is not).

**Fix:** Reconcile the README to the actual event names. Suggested replacements (verbatim):
```diff
-the structured log line **obs_camera_opened** appears on stdout
+the structured log line **camera_started** appears on stdout

-structured log line **obs_camera_not_found** lists the available DirectShow devices
+the OBSCameraNotFoundError message lists the available DirectShow devices (the camera_discovered / camera_multiple_matches success events do not fire)

-structured log line **arduino_port_not_found**
+ArduinoPortNotFoundError (raised before any log event fires; the port_discovered / port_multiple_matches / port_manual_override success events do not fire)

-Structured log line **firmware_error code=11**
+Structured log line **error_received code=11 code_name=HEARTBEAT_TIMEOUT**

-log lines **subject_lock_lost** then **subject_lock_acquired**
+log lines **lock_loss** then **lock_acquired** (or **lock_reacquired** if re-acquire happens on a different track_id)
```

### WR-02: TODO without tracking-issue number violates CLAUDE.md rule

**File:** `pastor_tracker/README.md:41`
**Issue:** `<!-- TODO(v2-supply-chain): pin yolo11n-pose.pt SHA256 once weights file is in repo -->` is a TODO without a referenced issue number. CLAUDE.md "Forbidden in app code" explicitly lists "TODO without issue number" as a violation. The README is not "app code" per the literal rule, but the spirit of the rule (every TODO must be tracked) applies — this TODO is a v2 supply-chain commitment that needs an issue to land against. Without a tracking number, the commitment dissolves the moment the README ships.

**Fix:** Either (a) file a v2 supply-chain issue and replace the TODO with `<!-- TODO(#NNN): pin yolo11n-pose.pt SHA256 once weights file is in repo -->`, or (b) move the v2 deferral into `.planning/ROADMAP.md` "Deferred to v2" and delete the inline TODO. Option (b) is preferred since ROADMAP.md is already the canonical deferred-work registry.

### WR-03: FOV calibration formula edge case (f=0) not guarded

**File:** `pastor_tracker/README.md:82`
**Issue:** Step 6 gives the FOV formula `h_fov_deg = 2 * atan(0.5 / (d * f)) * (180 / π)` for partial-fill fraction `f`, with the constraint `f ∈ (0, 1]`. The text below the formula does not warn the operator that `f = 0` (bar invisible in frame, e.g. wrong aim) divides by zero. This is unlikely to be hit in practice (an operator with f=0 will re-aim), but the formula is given as the v1 manual procedure without a guard rail. Minor doc-robustness issue; not a correctness defect in code.

**Fix:** Add one sentence after the formula:
```
If you cannot see the bar at all (f → 0), re-aim the camera before recording a measurement; the formula is undefined at f = 0.
```

### WR-04: "About 1/7 of the frame at 70° FOV" rounding is sloppy

**File:** `pastor_tracker/README.md:93`
**Issue:** The Verification step says "a 10° camera-mount pan should move the subject roughly `10 / h_fov` of the frame width (about 1/7 of the frame at 70° FOV)". 10 / 70 = 0.1428... which is 1/7 only by coincidence; the eyeball heuristic is roughly correct but `1/7` is a magic-number approximation that does not survive any FOV other than 70° (which is itself only the default). At the documented 53.13° example (line 79), the same 10° pan moves the subject 10/53.13 ≈ 18.8% of the frame width — about 1/5, not 1/7. An operator who calibrates to a different FOV and then uses the 1/7 heuristic gets a false negative.

**Fix:** Drop the parenthetical, or generalize it:
```
a 10° camera-mount pan should move the subject roughly 10 / h_fov of the frame width (about 14% at 70° FOV, 19% at 53° FOV).
```

### WR-05: Pre-flight checklist item 7 assumes overlays draw before pipeline starts

**File:** `pastor_tracker/README.md:159`
**Issue:** Pre-flight step 7 says "Confirm the preview window opens, the framing-target vertical line is drawn, and the subject bounding box overlays the speaker" — *before* step 8's "Press `S` to start tracking". This contradicts the pipeline's actual behaviour: `third_line_pixels()` returns `None` when `snap.last_target_x_normalized is None` (`ui/_overlays.py:44`) and `bbox_rect_pixels()` returns `None` when `snap.last_subject_bbox_normalized is None` (`ui/_overlays.py:61`). Both snapshot fields are populated by the pipeline tick task, which is paused until `S` is pressed. So at step 7, before `S`, the operator will see the preview window but no third-line and no bbox — and the README will appear wrong.

**Fix:** Move the overlay checks into step 8 (post-`S`):
```diff
-7. Confirm the preview window opens, the framing-target vertical line is drawn, and the subject bounding box overlays the speaker.
-8. Press `S` to start tracking. The motor should begin tracking after a < 1 s damping ramp-up.
+7. Confirm the preview window opens and shows live frames from the OBS Virtual Camera.
+8. Press `S` to start tracking. The framing-target vertical line is drawn, the subject bounding box overlays the speaker, and the motor begins tracking after a < 1 s damping ramp-up.
```

### WR-06: `--ui` default-detection wording is ambiguous

**File:** `pastor_tracker/README.md:108`
**Issue:** The Run section says "The default mode is `--ui`" but `__main__.py:236-244` shows `--ui` defaults to `False` and `--headless` defaults to `False`, with the actual default determined by `headless_mode = bool(args.headless)` on line 250 (i.e. UI wins when neither flag is set, not "default is `--ui`"). The README's phrasing implies that passing `--ui` and passing nothing are identical — which is true — but a careful operator reading `--help` will see `default=False` on both flags and wonder which is really the default. Pure clarity issue.

**Fix:** Reword line 108:
```diff
-The default mode is `--ui` (DearPyGui dashboard with live preview, sliders, status panel, and hotkeys).
+If neither flag is passed, UI mode is selected (DearPyGui dashboard with live preview, sliders, status panel, and hotkeys); this matches passing `--ui` explicitly.
```

## Info

### IN-01: "Boot banner must be `READY:v2`" repeats but never cross-links to firmware

**File:** `pastor_tracker/README.md:14, 193`
**Issue:** `READY:v2` is mentioned twice without a path reference to `arduino/stepper_controller/include/protocol.h` (where `PROTOCOL_VERSION_MAJOR = 2`) or to PROMPT.md ## Arduino Protocol. Operators flashing the firmware for the first time will not know where the source of truth lives.
**Fix:** Add one cross-reference, e.g. "(see `arduino/stepper_controller/include/protocol.h`)".

### IN-02: Project-layout one-liner buries the Python source tree

**File:** `pastor_tracker/README.md:229-231`
**Issue:** The "Project layout" section is one sentence pointing at `.planning/PROJECT.md`. Given the README is the operator manual (not the developer guide), this is fine, but a single bullet listing the four top-level src/ subdirs (`io/`, `perception/`, `intent/`, `control/`) would help operators map the troubleshooting log lines back to source files without a roundtrip to PROJECT.md.
**Fix:** Optional. Add a 4-line bullet list of the src subdirectories with one-line descriptions.

---

_Reviewed: 2026-05-11_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
