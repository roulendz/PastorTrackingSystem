# Pastor Tracking System

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

<!-- v1 scope for the Python pastor_tracker/ app. -->

- [ ] **CORE-01**: Pure-functional core types, geometry math, and critically-damped 2nd-order follower (no PID)
- [ ] **IO-01**: Async OBS Virtual Camera frame source with DirectShow backend, frame-staleness drop, perf-counter timestamps
- [ ] **IO-02**: Async Arduino serial driver — VID:PID auto-detect, boot handshake `READY:v2`, 200 ms heartbeat, FB parser, watchdog-reset recovery
- [ ] **PERC-01**: YOLO11-pose detector (ultralytics, GPU/CPU) with confidence gate ≥ 0.55 and weighted-keypoint subject centroid
- [ ] **PERC-02**: BoT-SORT ID lock on primary subject (highest-conf person in central 60% at start), re-acquire after >2 s loss
- [ ] **PERC-03**: 4-state Kalman filter (x, y, vx, vy) on normalized frame coords for prediction during gaps
- [ ] **INTENT-01**: Motion analyzer — sustained-velocity + dwell hysteresis from Kalman vx
- [ ] **INTENT-02**: Rule-of-thirds framer — left/center/right third selection based on motion intent
- [ ] **CTRL-01**: Pan controller with deadband (0.4°), velocity clamp (30°/s), critically-damped follower
- [ ] **CTRL-02**: Command dispatcher — rate-limit `M:` emission (Δ > 0.2°, ≥ 50 ms gap)
- [ ] **PIPE-01**: Asyncio orchestrator wiring camera → detector → tracker → analyzer → framer → controller → motor
- [ ] **UI-01**: DearPyGui dashboard — live preview with skeleton/badge/third overlay, tuning sliders, status, hotkeys (S/P/H/E/Q)
- [ ] **CFG-01**: Pydantic v2 frozen `Config` with env+JSON loading, range validation, fail-fast on invalid
- [ ] **TEST-01**: Property tests for geometry + damping (hypothesis), unit tests for analyzer/framer/controller, protocol parser test against canned FB lines + heartbeat
- [ ] **DOC-01**: README — `uv sync` install, OBS VCam setup, FOV calibration procedure, run command, hotkey table
- [ ] **QA-01**: `ruff check` + `mypy --strict` clean, one Conventional Commit per module

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
| YOLO11-pose over MediaPipe | Faster + more accurate in 2026; native BoT-SORT integration via ultralytics | — Pending |
| Critically-damped 2nd-order follower over PID | PID overshoots/oscillates; bad for cinematography | — Pending |
| Kalman over EMA smoothing | EMA adds lag; Kalman predicts during detection gaps without lag | — Pending |
| BoT-SORT ID persistence | Locks primary subject across occlusions; ignores audience/interpreter | — Pending |
| Two-stage damping (framing + motor) | Eliminates jerk and overshoot at both intent and actuation layers | — Pending |
| VID:PID auto-detect over fixed COM port | USB-jack-dependent enumeration on dev machine — fixed port unreliable | — Pending |
| Pure core / dirty edges architecture | Side effects only at rim (serial, camera, GUI); core stays unit-testable | — Pending |
| `dearpygui` over Qt/Tk | Immediate-mode rendering = low-latency live preview; minimal boilerplate | — Pending |
| Boot handshake required (`READY:v<N>`) | Version mismatch detected at startup, not mid-run | — Pending |
| AVR WDT + PC heartbeat dual watchdog | Hardware lockup recovery + PC-process-death detection | — Pending |
| `mypy --strict` no `Any` | Sharpen contracts via `Literal`/`NewType`/`TypeAlias`; catch errors at type-check time | — Pending |

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
*Last updated: 2026-05-03 after initialization from PROMPT.md*
