# Requirements: Pastor Tracking System

**Defined:** 2026-05-03
**Core Value:** Cinematic, jitter-free auto-tracking of a single primary speaker — no overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk.

## v1 Requirements

### Scaffold

- [x] **SCAF-01**: `pastor_tracker/` package with `pyproject.toml`, `uv lock`, pinned deps for the 2026 stack
- [x] **SCAF-02**: `ruff` lint/format config + `mypy --strict` config (no `Any`, all functions annotated)
- [x] **SCAF-03**: `tests/` directory wired to `pytest` + `hypothesis`
- [x] **SCAF-04**: `structlog` JSON logging configured at module entry; `print()` and bare `except` forbidden by lint policy
- [x] **SCAF-05**: Conventional Commits enforced — one logical change per commit

### Configuration

- [x] **CFG-01**: `Config(BaseSettings, frozen=True)` with all 23 PROMPT.md fields, env+JSON loading
- [x] **CFG-02**: Range validation on every field — port format, FOV positive, motor clamps `[100..50000]` speed `[50..30000]` accel
- [x] **CFG-03**: Crash at startup on invalid config (no silent fallback, no defaults that hide errors)
- [x] **CFG-04**: `arduino_protocol_version` defaults to `2`; mismatch with firmware `READY:v<N>` aborts boot

### Core (pure, side-effect-free)

- [x] **CORE-01**: `core/types.py` — frozen DTOs: `Frame`, `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand`
- [x] **CORE-02**: `core/geometry.py` — FOV math, normalized↔angle conversions; property-tested (hypothesis)
- [x] **CORE-03**: `core/damping.py` — critically-damped 2nd-order follower; step-response test asserts no overshoot
- [x] **CORE-04**: 100% type coverage on core; no `Any`; no `print`; ≤2-level conditional nesting

### Arduino I/O

- [ ] **IO-ARD-01**: VID:PID auto-detect over `serial.tools.list_ports.comports()` matching genuine Uno R3 (`2341:0043`), R4 (`2341:0069`), CH340 (`1A86:7523`), FTDI (`0403:6001`)
- [x] **IO-ARD-02**: Boot handshake — read up to `arduino_ready_timeout_sec`, expect `READY:v2`, abort on version mismatch with expected-vs-received log
- [x] **IO-ARD-03**: Async TX wrapper for `M:` `S:` `L:` `R` `Q` `E` `H` `X:` `D:` commands; rate-limited dispatcher (Δ > 0.2°, ≥ 50 ms gap)
- [x] **IO-ARD-04**: Threaded RX with parser for `FB:` `READY:` `SETTINGS:` `LIMITS:` `DRIVER:` `RESET:` `STOP:` `DIAG:` `ERROR:` lines; `seq` gap > 5 → WARN
- [x] **IO-ARD-05**: 200 ms heartbeat task while tracking — sends `Q` (or other) so firmware never hits 1000 ms PC-heartbeat timeout
- [x] **IO-ARD-06**: Watchdog-reset recovery — re-receipt of `READY:v2` mid-session = MCU reset → re-issue settings + limits + WARN log
- [x] **IO-ARD-07**: `ERROR:` from firmware halts tracking, surfaces to UI, requires manual reset; `ERROR:11` (heartbeat lost) logs ERROR for investigation

### Camera I/O

- [x] **IO-CAM-01**: Async OBS Virtual Camera frame source via `cv2.VideoCapture(idx, CAP_DSHOW)`; enumerate via `pygrabber.dshow_graph.FilterGraph`, match `"OBS Virtual Camera"`
- [ ] **IO-CAM-02**: Hard-fail with available device list if OBS VCam not found
- [x] **IO-CAM-03**: Target 1920×1080 @ 30 fps; auto-fall to 1280×720 if frame-time budget breached
- [x] **IO-CAM-04**: Each frame stamped with `time.perf_counter_ns()` at grab time; queue drops frames older than 100 ms; stall > 200 ms logs ERROR + restarts capture

### Perception

- [ ] **PERC-01**: YOLO11-pose wrapper (ultralytics) — GPU/CPU, async inference in process pool, doesn't block capture
- [ ] **PERC-02**: Subject centroid = weighted mean — nose 0.4, shoulder mid 0.4, hip mid 0.2; reject if mean keypoint conf < 0.55
- [ ] **PERC-03**: BoT-SORT ID persistence via `model.track(persist=True, tracker="botsort.yaml")`
- [ ] **PERC-04**: Primary-subject lock — highest-conf person in central 60% of frame at start; persist ID across occlusions
- [ ] **PERC-05**: Lock loss > 2.0 s → re-acquire via central-frame heuristic, log WARN
- [ ] **PERC-06**: 4-state Kalman filter `[x, y, vx, vy]` in normalized frame coords; predict during gaps, update on detection
- [ ] **PERC-07**: 3 consecutive frames with no detection → hold position, log WARN

### Intent (motion → framing)

- [x] **INTENT-01**: Motion analyzer — sustained `vx > 0.08` for ≥ 0.3 s = right-bound; `vx < -0.08` for ≥ 0.3 s = left-bound; `|vx| < 0.03` for ≥ 1.5 s = dwell
- [x] **INTENT-02**: Hysteresis prevents thrash — thresholds + dwell duration in `Config`
- [x] **INTENT-03**: Framer maps intent → rule-of-thirds target — moving-right → left third (0.333); moving-left → right third (0.667); dwelling → center (0.500)
- [x] **INTENT-04**: Framing target smoothed with damping `framing_time_constant_sec ≈ 0.8 s`

### Control

- [x] **CTRL-01**: Pan controller — second damping stage `pan_time_constant_sec ≈ 0.6 s` on motor angle delta
- [x] **CTRL-02**: Deadband — suppress motor command if `|angle_delta| < 0.4°`
- [x] **CTRL-03**: Velocity clamp — max pan rate `30°/s`
- [x] **CTRL-04**: Command dispatcher — emit `M:<deg>` only when delta > 0.2° AND ≥ 50 ms since last command

### Pipeline

- [x] **PIPE-01**: `pipeline.py` asyncio orchestrator wiring stages: `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor`
- [x] **PIPE-02**: Each stage = pure transform on typed DTO; orchestrator owns wiring; no stage knows another stage's internals
- [x] **PIPE-03**: Lifecycle — start, pause, home, e-stop, quit (mapped to UI hotkeys)

### UI

- [ ] **UI-01**: DearPyGui single window — live preview with skeleton overlay, subject ID badge, framing-target vertical line, current/target angle text
- [ ] **UI-02**: Live tuning sliders — pan time-constant, deadband, max velocity, FOV
- [ ] **UI-03**: Buttons — `Start`, `Pause`, `Home`, `E-Stop`, `Save Config`
- [ ] **UI-04**: Status panel — motor link state, camera FPS, detection conf, ID lock state, last error
- [ ] **UI-05**: Hotkeys — `S` start, `P` pause, `H` home, `E` estop, `Q` quit

### Testing

- [x] **TEST-01**: Property tests for `core/geometry.py` (hypothesis)
- [x] **TEST-02**: Step-response test for `core/damping.py` — no overshoot
- [x] **TEST-03**: Unit tests for `motion_analyzer.py` (hysteresis + dwell), `framer.py` (third selection), `pan_controller.py` (deadband + clamp)
- [x] **TEST-04**: Integration test for Arduino protocol parser using fake-serial replay of canned `FB:` / `READY:` / `ERROR:` lines + heartbeat verification
- [x] **TEST-05**: No mocked Kalman/damping math — test real implementations

### Docs + Quality

- [ ] **DOC-01**: `README.md` — install (`uv sync`), OBS VCam setup, FOV calibration procedure, run command, hotkey table
- [ ] **QA-01**: `ruff check` clean
- [ ] **QA-02**: `mypy --strict` clean — every function annotated, no `Any`
- [ ] **QA-03**: One Conventional Commit per module (per Order of Work in PROMPT.md)
- [ ] **QA-04**: End-to-end smoke test on stage — real Uno (auto-detected port), real OBS VCam, real speaker

## v2 Requirements

Deferred. Not in current roadmap.

### Future scope

- **TILT-01**: Vertical tilt axis (second stepper)
- **MULTI-01**: Multi-subject coordination (camera switcher across speakers)
- **REC-01**: Local recording / livestream output
- **CAL-01**: Auto FOV calibration via known-distance reference

## Out of Scope

| Feature | Reason |
|---------|--------|
| PID controller | PROMPT.md forbids — overshoot/oscillation incompatible with cinematic feel |
| MediaPipe pose detection | Superseded by YOLO11-pose (faster + more accurate in 2026) |
| EMA on detection stream | Replaced by Kalman — EMA adds lag |
| Multi-subject tracking | One primary speaker only; audience and interpreter must be ignored |
| Tilt axis | Pan-only mount in v1 |
| Auto-recover from firmware `ERROR:` | Must surface to UI and require manual reset |
| Cloud sync / streaming / recording | Local pan control only |
| `print()` debugging | Forbidden — `structlog` JSON only |
| Magic numbers | All tunables live in `Config` |
| Bare `except:` / `except Exception: pass` | Forbidden — fail loud |
| Mocked Kalman/damping in tests | Forbidden — test real math |
| Fixed COM port hardcoded | VID:PID auto-detect mandatory; port number changes per USB jack |

## Traceability

Populated by `gsd-roadmapper` 2026-05-03 from `.planning/ROADMAP.md` (8 phases).

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCAF-01 | Phase 1 | Complete |
| SCAF-02 | Phase 1 | Complete |
| SCAF-03 | Phase 1 | Complete |
| SCAF-04 | Phase 1 | Complete |
| SCAF-05 | Phase 1 | Complete |
| CFG-01 | Phase 1 | Complete |
| CFG-02 | Phase 1 | Complete |
| CFG-03 | Phase 1 | Complete |
| CFG-04 | Phase 1 | Complete |
| CORE-01 | Phase 1 | Complete |
| CORE-02 | Phase 1 | Complete |
| CORE-03 | Phase 1 | Complete |
| CORE-04 | Phase 1 | Complete |
| IO-ARD-01 | Phase 2 | Pending |
| IO-ARD-02 | Phase 2 | Complete |
| IO-ARD-03 | Phase 2 | Complete |
| IO-ARD-04 | Phase 2 | Complete |
| IO-ARD-05 | Phase 2 | Complete |
| IO-ARD-06 | Phase 2 | Complete |
| IO-ARD-07 | Phase 2 | Complete |
| IO-CAM-01 | Phase 3 | Complete |
| IO-CAM-02 | Phase 3 | Pending |
| IO-CAM-03 | Phase 3 | Complete |
| IO-CAM-04 | Phase 3 | Complete |
| PERC-01 | Phase 4 | Pending |
| PERC-02 | Phase 4 | Pending |
| PERC-03 | Phase 4 | Pending |
| PERC-04 | Phase 4 | Pending |
| PERC-05 | Phase 4 | Pending |
| PERC-06 | Phase 4 | Pending |
| PERC-07 | Phase 4 | Pending |
| INTENT-01 | Phase 5 | Complete |
| INTENT-02 | Phase 5 | Complete |
| INTENT-03 | Phase 5 | Complete |
| INTENT-04 | Phase 5 | Complete |
| CTRL-01 | Phase 5 | Complete |
| CTRL-02 | Phase 5 | Complete |
| CTRL-03 | Phase 5 | Complete |
| CTRL-04 | Phase 5 | Complete |
| PIPE-01 | Phase 6 | Complete |
| PIPE-02 | Phase 6 | Complete |
| PIPE-03 | Phase 6 | Complete |
| UI-01 | Phase 7 | Pending |
| UI-02 | Phase 7 | Pending |
| UI-03 | Phase 7 | Pending |
| UI-04 | Phase 7 | Pending |
| UI-05 | Phase 7 | Pending |
| TEST-01 | Phase 1 | Complete |
| TEST-02 | Phase 1 | Complete |
| TEST-03 | Phase 5 | Complete |
| TEST-04 | Phase 2 | Complete |
| TEST-05 | Phase 1 | Complete |
| DOC-01 | Phase 8 | Pending |
| QA-01 | Phase 8 | Pending |
| QA-02 | Phase 8 | Pending |
| QA-03 | Phase 8 | Pending |
| QA-04 | Phase 8 | Pending |

**Coverage:**
- v1 requirements: 57 total (initial doc miscount of 47 corrected during roadmap traceability)
- Mapped to phases: 57
- Unmapped: 0

**Per-phase distribution:**

| Phase | Count | Requirements |
|-------|-------|--------------|
| 1. Scaffold, Config, Core Math | 16 | SCAF-01..05, CFG-01..04, CORE-01..04, TEST-01, TEST-02, TEST-05 |
| 2. Arduino I/O | 8 | IO-ARD-01..07, TEST-04 |
| 3. Camera I/O | 4 | IO-CAM-01..04 |
| 4. Perception | 7 | PERC-01..07 |
| 5. Intent and Control | 9 | INTENT-01..04, CTRL-01..04, TEST-03 |
| 6. Pipeline Orchestrator | 3 | PIPE-01..03 |
| 7. UI Dashboard | 5 | UI-01..05 |
| 8. End-to-End and Ship Gates | 5 | DOC-01, QA-01..04 |

---
*Requirements defined: 2026-05-03*
*Last updated: 2026-05-03 — traceability populated by gsd-roadmapper*
