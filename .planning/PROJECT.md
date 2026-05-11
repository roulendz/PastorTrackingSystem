# Pastor Tracking System

## Current State

**Shipped:** v1.0 — Pastor Tracker MVP (2026-05-11). 8 phases, 26 plans, 561 tests passing, 55/57 requirements satisfied. See [MILESTONES.md](MILESTONES.md) and [milestones/v1.0-MILESTONE-AUDIT.md](milestones/v1.0-MILESTONE-AUDIT.md).

**Outstanding (real-world acceptance + env hotfixes, no code blockers):**
- QA-04 on-stage smoke — 6 unchecked pass criteria pending next stage rehearsal (real Uno + OBS VCam + speaker)
- QA-02 mypy 1.20.2 wheel + pydantic.mypy plugin regression on tests/ — pin mypy version
- QA-01 ruff format drift on 73 files — single-pass cleanup

**Next milestone goals (v1.1, not yet started):** Close v1.0 follow-ups; consider TILT-01 (vertical tilt axis), CAL-01 (auto FOV calibration), and YOLO weights SHA256 supply-chain hardening. Run `/gsd-new-milestone` to define.

## What This Is

Python 3.12 desktop app that tracks a speaker on stage and drives an Arduino stepper motor over USB serial to pan a camera mount in real time. Input is the OBS Virtual Camera; output is smooth, cinematic pan with rule-of-thirds lead-room framing. Built for live church/conference video — replaces a human camera operator for single-speaker stage coverage.

## Core Value

Cinematic, jitter-free auto-tracking of a single primary subject (the pastor/speaker) — no overshoot, no oscillation, no lock-loss to the audience or sign-language interpreter, no audible motor jerk. If the pan is not smooth and the lock is not stable, nothing else matters.

## Requirements

### Validated

<!-- Shipped firmware. Not the new build target — already in arduino/stepper_controller/. -->

- ✓ Arduino stepper controller firmware v2 (PlatformIO, Uno R3) — shipped pre-2026
- ✓ Serial protocol v2 (FB:, READY:v2, ERROR codes 0–11, EEPROM-persisted settings/limits, AVR WDT, 1000ms PC heartbeat watchdog)
- ✓ AccelStepper-driven motion with software angle limits and emergency stop

### Active

<!-- v1.0 shipped — all items below moved to Validated. v1.1 active scope TBD via /gsd-new-milestone. -->

(None — set after `/gsd-new-milestone` defines v1.1 scope. Likely seed candidates from v1.0 follow-ups: QA-04 on-stage smoke, QA-02 mypy pin, QA-01 ruff format pass, YOLO SHA256 pin.)

### Validated (shipped in v1.0 — 2026-05-11)

- ✓ **CORE-01**: Pure-functional `core/types.py` DTOs + `core/geometry.py` FOV math + `core/damping.py` critically-damped 2nd-order follower — v1.0 (property-tested, no PID)
- ✓ **CFG-01**: Pydantic v2 frozen `Config` (25 fields) with env+JSON loading, range validation, fail-fast on invalid — v1.0
- ✓ **IO-01 (IO-CAM-01..04)**: Async OBS Virtual Camera frame source with DirectShow, OBSCameraNotFoundError, 1080p→720p fallback, `perf_counter_ns()` timestamps, > 100 ms stale-drop — v1.0
- ✓ **IO-02 (IO-ARD-01..07)**: Async Arduino serial driver with VID:PID auto-detect, `READY:v2` boot handshake, 200 ms heartbeat, FB parser, watchdog-reset recovery, latched-error gate — v1.0
- ✓ **PERC-01 (PERC-01..02)**: YOLO11-pose detector with confidence gate ≥ 0.55 and weighted-keypoint centroid (nose 0.4 / shoulders 0.4 / hips 0.2) — v1.0
- ✓ **PERC-02 (PERC-03..05)**: BoT-SORT ID lock on primary subject (central 60%), 2 s lock-loss re-acquire — v1.0
- ✓ **PERC-03 (PERC-06..07)**: 4-state Kalman filter on normalized frame coords with hold-during-gap — v1.0
- ✓ **INTENT-01**: Motion analyzer — sustained-velocity + dwell hysteresis on Kalman vx — v1.0
- ✓ **INTENT-02**: Rule-of-thirds framer with stage-1 damping (~0.8 s τ) — v1.0
- ✓ **CTRL-01**: PanController with deadband (0.4°), velocity clamp (30°/s), critically-damped stage-2 follower (~0.6 s τ) — v1.0
- ✓ **CTRL-02**: CommandDispatcher rate-limit `M:` emission (Δ > 0.2°, ≥ 50 ms gap) — v1.0
- ✓ **PIPE-01**: Asyncio orchestrator wiring camera → detector → tracker → analyzer → framer → controller → motor with 6-state lifecycle — v1.0
- ✓ **UI-01**: DearPyGui dashboard — live preview with skeleton/badge/framing overlay, 4 tuning sliders, Save Config restart, status panel, 5 hotkeys (E debounced, S/P/H/Q on release) — v1.0
- ✓ **TEST-01**: Property tests for geometry + damping, unit tests for analyzer/framer/controller, fake-serial protocol parser tests — v1.0 (561 passed, 1 skipped)
- ✓ **DOC-01**: README full operator manual (install, OBS setup, FOV calibration, run, hotkeys, troubleshooting) — v1.0
- ✓ **QA-01**: `ruff check` clean — v1.0 (`ruff format --check` drift on 73 files deferred to v1.1)
- ✓ **QA-03**: Conventional Commits per Order of Work — v1.0 (30-commit sample verified)
- ⚠ **QA-02**: `mypy --strict src` clean (78 files); `mypy --strict src tests` blocked by mypy 1.20.2 wheel env regression — v1.0 (env hotfix deferred to v1.1)
- ⚠ **QA-04**: On-stage smoke ≥ 5 min — v1.0 (6 pass criteria deferred to next stage rehearsal; tracked in 08-HUMAN-UAT.md)

### Out of Scope

- Multi-subject tracking — designed for ONE primary speaker; audience and interpreter must be ignored, not tracked
- Tilt axis — pan-only mount; vertical motion deferred
- PID control — explicitly forbidden (overshoot/oscillation incompatible with cinematic feel)
- MediaPipe — superseded by YOLO11-pose for 2026 (faster + more accurate)
- EMA smoothing on detection — replaced by Kalman (lag-free)
- `print()` debugging, bare `except:`, magic numbers, globals, `time.sleep()` in main loop, mocked Kalman/damping math in tests
- Auto-recover from `ERROR:` from firmware — must surface to UI and require manual reset
- Cloud sync, recording, streaming — local pan control only

## Context

- **Hardware on dev machine**: Arduino Uno (CH340 or genuine) — enumerates on COM6, USB-jack-dependent, hence VID:PID match mandatory
- **Mechanics**: 200 steps × 180:1 gear × 8 microsteps = 288 000 steps/rev; default software limits ±90°
- **Camera**: OBS Virtual Camera at 1920×1080 @ 30 fps target, fall back to 1280×720 if frame-time budget breached
- **Firmware contract**: AVR WDT 500 ms hardware watchdog; PC heartbeat watchdog 1000 ms; `READY:v2` re-emission = MCU reset event → PC must re-issue settings + limits
- **2026 stack chosen for**: type strictness (`mypy --strict`, no `Any`), immutable data flow, pure-core/dirty-edge separation, async I/O for camera + serial, threading only for blocking serial RX
- **Engineering culture**: Tiger-style fail-fast-fail-loud; SRP one-class-one-reason; DRY zero-duplication; pure-core/dirty-edges; ≤2-level conditional nesting with guard clauses; PEP8 strict naming with descriptive identifiers; no abbreviations; type hints everywhere; immutable Pydantic/dataclass `frozen=True`; functional code, no spaghetti, no nested if-in-ifs

## Constraints

- **Tech stack**: Python 3.12, `uv` package manager, `ruff` lint/format, `mypy --strict` — non-negotiable per PROMPT.md
- **Validation**: Pydantic v2 frozen models for config + DTOs; all input boundaries validated, crash on contract violation
- **Logging**: `structlog` JSON output only — no `print()` in app code
- **Testing**: `pytest` + `hypothesis` for property tests; mock-free for math (test real Kalman/damping)
- **Detection**: `ultralytics` YOLO11-pose with BoT-SORT tracker (built-in)
- **State estimation**: `filterpy` Kalman, `numpy` for math, `opencv-python` with DirectShow
- **Serial**: `pyserial` with async wrapper, threading for blocking RX only
- **GUI**: `dearpygui` (immediate-mode, low-latency)
- **Hardware**: Arduino Uno R3/R4 over USB serial @ 115 200 baud, protocol v2 only — version mismatch = abort
- **Performance**: Frame staleness drop > 100 ms; command throttle ≥ 50 ms; heartbeat 200 ms; ready-timeout 2 s
- **Failure mode**: Loud crash, not silent fallback — every error surfaces to UI/log, no `except Exception: pass`

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| YOLO11-pose over MediaPipe | Faster + more accurate in 2026; native BoT-SORT integration via ultralytics | ✓ Good — v1.0 shipped, runtime verified in pipeline tests |
| Critically-damped 2nd-order follower over PID | PID overshoots/oscillates; bad for cinematography | ✓ Good — v1.0 step-response asserts no overshoot |
| Kalman over EMA smoothing | EMA adds lag; Kalman predicts during detection gaps without lag | ✓ Good — v1.0 4-state filter holds through 3-frame gaps |
| BoT-SORT ID persistence | Locks primary subject across occlusions; ignores audience/interpreter | ✓ Good — v1.0; on-stage smoke pending QA-04 |
| Two-stage damping (framing + motor) | Eliminates jerk and overshoot at both intent and actuation layers | ✓ Good — v1.0; on-stage feel verification pending QA-04 |
| VID:PID auto-detect over fixed COM port | USB-jack-dependent enumeration on dev machine — fixed port unreliable | ✓ Good — v1.0 covers Uno R3/R4 + CH340 + FTDI |
| Pure core / dirty edges architecture | Side effects only at rim (serial, camera, GUI); core stays unit-testable | ✓ Good — v1.0; core/ remains import-clean |
| `dearpygui` over Qt/Tk | Immediate-mode rendering = low-latency live preview; minimal boilerplate | ✓ Good — v1.0 dashboard ships with 960×540 preview + sliders |
| Boot handshake required (`READY:v<N>`) | Version mismatch detected at startup, not mid-run | ✓ Good — v1.0 |
| AVR WDT + PC heartbeat dual watchdog | Hardware lockup recovery + PC-process-death detection | ✓ Good — v1.0; mid-session `READY:v2` triggers `_recover()` |
| `mypy --strict` no `Any` | Sharpen contracts via `Literal`/`NewType`/`TypeAlias`; catch errors at type-check time | ⚠ Revisit — v1.0 `src` clean; `tests` blocked by mypy 1.20.2 wheel env regression; pin mypy in v1.1 |
| Pure-process-pool YOLO inference | Avoids GIL block on capture; YOLO model state isolated to worker | ✓ Good — v1.0 PoseDetector + _pose_worker |
| Two-commit phase-close pattern | Mirrors Phase 7: docs (artifact) + docs (verification) — clean revert/review boundary | ✓ Good — v1.0 Phase 8 followed this |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-progress` or `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-11 after v1.0 milestone close*
