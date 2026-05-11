---
phase: 08-end-to-end-and-ship-gates
reviewed: 2026-05-11T00:00:00Z
depth: standard
files_reviewed: 1
files_reviewed_list:
  - pastor_tracker/README.md
findings:
  critical: 0
  warning: 0
  info: 0
  total: 0
status: clean
---

# Phase 8: Code Review Report (Iteration 2)

**Reviewed:** 2026-05-11
**Depth:** standard
**Files Reviewed:** 1 (`pastor_tracker/README.md`, 234 lines)
**Status:** clean

## Summary

Re-review of `pastor_tracker/README.md` after six WARNING findings from iteration 1 were addressed in commits `8b01cc1`..`fcddd15`. All six fixes verified against the source of truth (`io/obs_camera.py`, `io/arduino_transport.py`, `io/arduino_motor.py`, `io/arduino_protocol.py`, `perception/subject_tracker.py`, `__main__.py`, `ui/_overlays.py`, `ui/dashboard.py`). No new defects detected. The README is now an accurate operator manual; the auto-loop terminates here.

## Verification of prior findings

| ID | Issue | README line(s) now | Source-of-truth check | Status |
|-|-|-|-|-|
| WR-01 | Fabricated structured-log event names | 51, 178, 187, 205, 214 | `camera_started` (obs_camera.py:574), `camera_discovered` / `camera_multiple_matches` (obs_camera.py:295,302), `port_discovered` / `port_multiple_matches` / `port_manual_override` (arduino_transport.py:205,221,227), `error_received code=11 code_name=HEARTBEAT_TIMEOUT` (arduino_motor.py:464 + arduino_protocol.py:138), `lock_loss` / `lock_acquired` / `lock_reacquired` (subject_tracker.py:264,244,237) | Resolved |
| WR-02 | TODO without tracking issue number | 41 | HTML-comment TODO removed; v2 deferral remains in prose ("The pinned authoritative hash will be added to this README in v2 supply-chain hardening"). Repo-wide grep of `README.md` for TODO/FIXME/XXX/HACK returns zero hits. | Resolved |
| WR-03 | FOV formula edge case f=0 unguarded | 80 | Sentence added verbatim: "If you cannot see the bar at all (f → 0), re-aim the camera before recording a measurement; the formula is undefined at f = 0." | Resolved |
| WR-04 | 10° pan heuristic baked to 70° FOV | 91 | Generalized: "about 14% at 70° FOV, 19% at 53° FOV" — the per-FOV percentages match `10 / 70 = 14.3%` and `10 / 53.13 = 18.8%`. | Resolved |
| WR-05 | Overlay checks placed before `S` press | 157-158 | Step 7 now confirms preview frames only; step 8 ("Press `S` to start tracking") absorbs the framing-target / bbox / motor-ramp confirmations. Matches `ui/_overlays.py` snapshot-gated overlay behaviour. | Resolved |
| WR-06 | `--ui` "default mode" wording ambiguous | 106 | Reworded to "If neither flag is passed, UI mode is selected (DearPyGui dashboard with live preview, sliders, status panel, and hotkeys); this matches passing `--ui` explicitly." Mutual-exclusion claim verified against `__main__.py:232` (`add_mutually_exclusive_group`). | Resolved |

## New findings

None. Adversarial re-scan covered:

- All six fixed lines against source-of-truth files
- Surrounding paragraphs of each fix for collateral inaccuracies
- Exit-code table (matches `__main__.py:95-98`)
- Hotkey table (S/P/H/Q release + E press, matches dashboard.py wiring per Phase 7 reviews)
- Argparse mutual exclusion claim (matches `__main__.py:232`)
- VID:PID auto-detect table (matches CLAUDE.md auto-detect spec)
- FOV worked examples (`d=2.0m` → 28.07°, `d=1.0m` → 53.13°, `d=0.7m` → 71.08° all reproduce from `2 * atan(0.5/d) * 180/π`)
- Per-FOV pan heuristic percentages (`10/70 = 14.3%`, `10/53.13 = 18.8%` both correct)

No bugs, no security issues, no quality defects identified in this iteration.

---

_Reviewed: 2026-05-11_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
