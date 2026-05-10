# Roadmap: Pastor Tracking System (PTS)

## Overview

The journey is bottom-up across the pure-core/dirty-edges boundary. Phase 1 lands the scaffold, frozen `Config`, and the pure math (`core/types.py`, `core/geometry.py`, `core/damping.py`) under property tests — nothing depends on hardware yet, and nothing else can ship until the math is provably damped and overshoot-free. Phase 2 brings up the Arduino serial driver and verifies it against the real Uno (auto-detected via VID:PID, fallback `COM6`) — this is the first hardware-in-the-loop checkpoint and gates everything downstream. Phase 3 brings up the OBS Virtual Camera frame source. Phase 4 layers perception on top: YOLO11-pose, BoT-SORT ID lock, and the 4-state Kalman filter. Phase 5 fuses motion intent and pan control — the two-stage damping (framer + pan controller) lives here together so integration is provable. Phase 6 wires everything through the asyncio pipeline orchestrator with start/pause/home/e-stop/quit lifecycle. Phase 7 adds the DearPyGui dashboard. Phase 8 is the on-stage end-to-end smoke test plus README, FOV calibration docs, and final `ruff` / `mypy --strict` ship gates.

Engineering culture (tiger-style, DRY, SRP, ≤2-level nesting, `mypy --strict` no `Any`, `structlog` JSON, no `print`, Conventional Commits) is a per-phase quality gate, not a separate phase.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Scaffold, Config, Core Math** - Repo scaffold, frozen Pydantic `Config`, pure DTOs/geometry/damping with property tests
- [ ] **Phase 2: Arduino I/O** - Async serial driver with VID:PID auto-detect, boot handshake, heartbeat, RX parser, watchdog-reset recovery — verified against real Uno
- [ ] **Phase 3: Camera I/O** - Async OBS Virtual Camera frame source with DirectShow, frame-staleness drop, perf-counter timestamps
- [ ] **Phase 4: Perception** - YOLO11-pose detection, BoT-SORT primary-subject ID lock, 4-state Kalman smoothing
- [x] **Phase 5: Intent and Control** - Motion analyzer with hysteresis, rule-of-thirds framer, two-stage damped pan controller, rate-limited command dispatcher
 (completed 2026-05-06)
- [ ] **Phase 6: Pipeline Orchestrator** - Asyncio orchestrator wiring all stages, lifecycle (start/pause/home/e-stop/quit)
- [ ] **Phase 7: UI Dashboard** - DearPyGui live preview, tuning sliders, status panel, hotkeys
- [ ] **Phase 8: End-to-End and Ship Gates** - On-stage smoke test, README + FOV calibration, final lint/type/commit gates

## Phase Details

### Phase 1: Scaffold, Config, Core Math
**Goal**: Establish the project skeleton, fail-fast frozen `Config`, and the pure-core DTOs and math (geometry + critically-damped follower) under property tests — the unit-testable foundation everything else builds on.
**Depends on**: Nothing (first phase)
**Requirements**: SCAF-01, SCAF-02, SCAF-03, SCAF-04, SCAF-05, CFG-01, CFG-02, CFG-03, CFG-04, CORE-01, CORE-02, CORE-03, CORE-04, TEST-01, TEST-02, TEST-05
**Success Criteria** (what must be TRUE):
  1. `uv sync` resolves a locked dependency tree and `uv run pytest` runs the test suite green from a clean clone
  2. `ruff check` and `mypy --strict` exit zero on the entire `src/pastor_tracker/` tree with no `Any`, no `print`, no bare `except`
  3. Loading `Config` with an out-of-range field (e.g. `pan_max_velocity_deg_per_sec=-1` or `motor_max_speed_steps_per_sec=99`) raises a Pydantic validation error at startup — no silent fallback
  4. `core/geometry.py` round-trips normalized↔angle conversions for any FOV in (0, 180) under hypothesis property tests
  5. `core/damping.py` step-response test asserts the follower converges to target with zero overshoot for time constants in [0.1, 2.0] s (real math, no mocks)
**Plans**: 3 plans
- [x] 01-01-PLAN.md — Scaffold uv project + ruff/mypy/pytest config + structlog + lint canary + commitizen (Wave 0)
- [x] 01-02-PLAN.md — Frozen Pydantic Config (24 fields, env+JSON, fail-fast) + tests (Wave 1)
- [x] 01-03-PLAN.md — core/types.py DTOs + core/geometry.py + core/damping.py (Holden) + property tests (Wave 2)
**UI hint**: no

### Phase 2: Arduino I/O
**Goal**: A standalone, fully-tested async Arduino serial driver that auto-detects the Uno, completes the v2 boot handshake, runs the heartbeat, parses every RX line type, and recovers from MCU watchdog resets — proven against the real device before any other I/O exists.
**Depends on**: Phase 1
**Requirements**: IO-ARD-01, IO-ARD-02, IO-ARD-03, IO-ARD-04, IO-ARD-05, IO-ARD-06, IO-ARD-07, TEST-04
**Success Criteria** (what must be TRUE):
  1. With a real Uno connected, the driver auto-detects the port via VID:PID (`2341:0043` / `2341:0069` / `1A86:7523` / `0403:6001`) and receives `READY:v2` within 2 s; falls back to `COM6` from config when auto-detect finds no match
  2. Boot handshake aborts loudly with an expected-vs-received log when firmware emits `READY:v1` (or any non-`v2`); aborts loudly on 2 s timeout
  3. While tracking, a `Q` heartbeat is emitted every 200 ms and the firmware never raises `ERROR:11` under normal operation; mid-session `READY:v2` is treated as MCU reset → settings + limits are re-issued and a WARN is logged
  4. Protocol parser test (fake-serial replay) decodes canned `FB:` / `READY:` / `SETTINGS:` / `LIMITS:` / `DRIVER:` / `RESET:` / `STOP:` / `DIAG:` / `ERROR:` lines into typed DTOs and detects `seq` gaps > 5 with a WARN
  5. `M:` dispatch enforces Δ > 0.2° and ≥ 50 ms gap; receiving `ERROR:<code>` halts tracking and surfaces a typed error event (no auto-recover)
**Plans**: 3 plans
- [x] 02-01-PLAN.md — Pure protocol parser + DTOs + ErrorCode enum + pyserial/pytest-cov deps (Wave 1)
- [x] 02-02-PLAN.md — SerialTransport Protocol + PySerial impl + FakeSerial impl + VID:PID discovery (Wave 2)
- [x] 02-03-PLAN.md — ArduinoMotor orchestrator (handshake, RX thread, heartbeat, watchdog recovery, ERROR halt) + 6 motor tests + golden-trace replay (Wave 3)
**UI hint**: no

### Phase 3: Camera I/O
**Goal**: An async OBS Virtual Camera frame source that fails loudly when OBS is not running, hits the 1080p30 target with auto-fallback to 720p, drops stale frames, and timestamps every frame with `perf_counter_ns()`.
**Depends on**: Phase 1
**Requirements**: IO-CAM-01, IO-CAM-02, IO-CAM-03, IO-CAM-04
**Success Criteria** (what must be TRUE):
  1. With OBS Virtual Camera running, the source enumerates DirectShow devices via `pygrabber`, matches `"OBS Virtual Camera"`, and delivers a steady stream of 1920×1080 frames at the configured 30 fps
  2. With OBS Virtual Camera not running, capture exits loudly with the available device list — no silent fallback to a webcam
  3. When the per-frame budget is breached, capture auto-falls to 1280×720 and logs the resolution change once
  4. Every delivered frame carries a `perf_counter_ns()` timestamp; frames older than 100 ms are dropped at the queue boundary; a stall > 200 ms logs ERROR and restarts capture
**Plans**: 2 plans
- [x] 03-01-PLAN.md — opencv-python + pygrabber deps + obs_camera transport seam (VideoSource Protocol + OpenCvVideoSource + discover_obs_camera_index + CameraError hierarchy + _P95Detector helper + Final constants) + FakeVideoSource fixture + discovery test matrix (Wave 1)
- [x] 03-02-PLAN.md — ObsCamera orchestrator (start/stop/frames + capture thread + asyncio bridge + bounded queue + fallback + stall recovery + status surface) + lifecycle/fallback/stall/stale-drop tests (Wave 2)
**UI hint**: no

### Phase 4: Perception
**Goal**: Detect the pastor on every frame with YOLO11-pose, lock onto them as the primary subject via BoT-SORT, and smooth their trajectory with a 4-state Kalman filter that predicts cleanly through occlusions and detection gaps.
**Depends on**: Phase 3
**Requirements**: PERC-01, PERC-02, PERC-03, PERC-04, PERC-05, PERC-06, PERC-07
**Success Criteria** (what must be TRUE):
  1. YOLO11-pose runs in a process pool and emits per-frame detections without blocking the capture loop on either GPU or CPU
  2. The subject centroid is the weighted mean (nose 0.4, shoulder mid 0.4, hip mid 0.2); detections with mean keypoint confidence < 0.55 are rejected
  3. At pipeline start, the highest-confidence person in the central 60% of the frame is locked as the primary subject; the BoT-SORT track ID persists across occlusions and other people (audience, interpreter) are ignored
  4. Lock loss > 2.0 s triggers re-acquisition via the central-frame heuristic and logs WARN; 3 consecutive frames with no detection holds the last position and logs WARN
  5. The 4-state Kalman filter `[x, y, vx, vy]` in normalized coords predicts during gaps and updates on detection — output is lag-free relative to a raw EMA baseline
**Plans**: 3 plans
- [x] 04-01-PLAN.md — Deps (ultralytics + filterpy) + Config fields (yolo_model_path / yolo_device / botsort_yaml_path) + Detection.track_id + PoseEngine Protocol + _pose_worker.py spawn-safe target + UltralyticsPoseEngine lifecycle skeleton + FakePoseEngine fixture + Wave-0 verifications (Wave 1)
- [x] 04-02-PLAN.md — _kalman.py (filterpy 4-state CV with variable-dt + posterior-freeze) + subject_tracker.py (6-state lock state machine + PERC-02 centroid + PERC-04 central-60% heuristic + PERC-05 lock-loss + PERC-07 HOLDING) + 17 unit tests on real filterpy (Wave 2)
- [x] 04-03-PLAN.md — PoseDetector orchestrator (drop-oldest at ingress + async iterator + FAULTED preserve) + e2e FakePoseEngine→PoseDetector→SubjectTracker integration test (Wave 3)
**UI hint**: no
**Cross-phase contract notes** (from Phase 3 deep review, commit aac3da1):
  - `Frame.__post_init__` now asserts `image.dtype == np.uint8` AND `image.flags["C_CONTIGUOUS"] is True`. YOLO/ultralytics path can rely on these — skip own dtype/contiguity guards. Test fixtures using `frame[..., ::-1]` views must wrap with `np.ascontiguousarray(...)`.

### Phase 5: Intent and Control
**Goal**: Convert the Kalman trajectory into a rule-of-thirds framing target with hysteresis, then drive a two-stage critically-damped pan that produces no jerk, no overshoot, no oscillation — and emit motor commands only when they actually change the picture.
**Depends on**: Phase 4
**Requirements**: INTENT-01, INTENT-02, INTENT-03, INTENT-04, CTRL-01, CTRL-02, CTRL-03, CTRL-04, TEST-03
**Success Criteria** (what must be TRUE):
  1. Motion analyzer classifies sustained `vx > +0.08` for ≥ 0.3 s as right-bound (target = left third 0.333), sustained `vx < -0.08` for ≥ 0.3 s as left-bound (target = right third 0.667), and `|vx| < 0.03` for ≥ 1.5 s as dwell (target = center 0.500); hysteresis prevents thrash on borderline velocities
  2. Two-stage damping is in place: framer smooths the third transition with `framing_time_constant_sec ≈ 0.8 s`, pan controller smooths the motor angle with `pan_time_constant_sec ≈ 0.6 s`
  3. Pan controller suppresses commands when `|angle_delta| < 0.4°` (deadband) and clamps pan rate to ≤ 30°/s
  4. Command dispatcher emits `M:<deg>` only when delta > 0.2° AND ≥ 50 ms since the last command — verified by counting emissions over a synthetic trajectory
  5. Unit tests cover hysteresis + dwell (motion_analyzer), third selection (framer), deadband + velocity clamp (pan_controller) — using real damping math, no mocks
**Plans**: 6 plans
- [x] 05-01-PLAN.md - Test fixture trajectories.py + self-tests (Wave 0)
- [x] 05-02-PLAN.md - MotionAnalyzer (hysteresis classifier) + tests (Wave 1)
- [x] 05-03-PLAN.md - Framer (rule-of-thirds + stage-1 damper) + tests (Wave 1)
- [x] 05-04-PLAN.md - PanController (FOV + stage-2 damper + clamp + deadband) + tests (Wave 2)
- [x] 05-05-PLAN.md - CommandDispatcher (sync delta+interval gate) + tests (Wave 2)
- [x] 05-06-PLAN.md - Composition smoke test across all four stages (Wave 3)
**UI hint**: no

### Phase 6: Pipeline Orchestrator
**Goal**: Wire `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor` into a single asyncio pipeline with a clean lifecycle — every stage stays a pure transform on a typed DTO; no stage knows another's internals.
**Depends on**: Phase 5
**Requirements**: PIPE-01, PIPE-02, PIPE-03
**Success Criteria** (what must be TRUE):
  1. Running `python -m pastor_tracker` brings up the full pipeline end-to-end against a real Uno + real OBS VCam and produces smooth pan motion from a moving subject in front of the camera
  2. Each stage receives and emits typed frozen DTOs only; the orchestrator owns wiring and no stage imports another stage's module-private state
  3. Lifecycle commands `start`, `pause`, `home`, `e-stop`, `quit` work via the orchestrator API (UI hotkey wiring lands in Phase 7) — `e-stop` halts motor within one heartbeat interval; `home` is rejected when 0° is outside software limits
**Plans**: 4 plans
- [ ] 06-01-PLAN.md — Wave 0 contracts: PoseDetector.stream() helper + PipelineSnapshot/PipelineState in core.types + pipeline.py skeleton (OrchestratorRejected)
- [ ] 06-02-PLAN.md — Wave 1: Pipeline class with 6-state lifecycle table + 8-stage tick loop + snapshot cache + latest_frame slot + e-stop inline send
- [ ] 06-03-PLAN.md — Wave 1: __main__ refactor with argparse + cross-platform SIGINT handling + structured exit codes + 8-stage hardware wiring
- [ ] 06-04-PLAN.md — Wave 2: Pipeline integration tests with all 4 fakes + lifecycle table coverage + e-stop budget + __main__ exit-code subprocess tests
**UI hint**: no
**Cross-phase contract notes** (from Phase 3 deep review, commit aac3da1):
  - `ObsCamera.frames()` now exits cleanly via `StopAsyncIteration` after `stop()` (B-02 fix). Orchestrator can write `async for frame in camera.frames(): ...` without catching `CameraStallError` to discriminate clean shutdown.
  - `ObsCamera.start()` translates ALL DirectShow / DLL load failures (RPC_E_CHANGED_MODE, OSError, pywintypes.error) to typed `CameraOpenError` (B-04 fix). Orchestrator can use `try: await camera.start() except CameraError:` exclusively — no raw exception types leak.
  - `ArduinoMotor` now reads `Config.arduino_protocol_version` at runtime (W-09 fix) — protocol bump requires both Config Literal widening AND host-module constant change.

### Phase 7: UI Dashboard
**Goal**: A single-window DearPyGui operator dashboard with live preview, live tuning sliders, status panel, and hotkeys — so a human can run, tune, and emergency-stop the system on stage without touching code.
**Depends on**: Phase 6
**Requirements**: UI-01, UI-02, UI-03, UI-04, UI-05
**Success Criteria** (what must be TRUE):
  1. Live preview overlays the YOLO11 skeleton, the locked subject ID badge, the current framing-target vertical line, and current/target angle text — at the camera frame rate without blocking the pipeline
  2. Live tuning sliders adjust pan time-constant, deadband, max velocity, and FOV — values flow into the running `Config` view and take effect within one frame, with bounds enforced by Pydantic
  3. Buttons `Start`, `Pause`, `Home`, `E-Stop`, `Save Config` work and reflect their action in the status panel
  4. Status panel shows motor link state, camera FPS, detection confidence, ID lock state, and last error with timestamp
  5. Hotkeys `S` (start), `P` (pause), `H` (home), `E` (e-stop), `Q` (quit) work in any focus state of the window
**Plans**: TBD
**UI hint**: yes
**Cross-phase contract notes** (from Phase 3 deep review, commit aac3da1):
  - New structured log keys available for dashboard widgets: `feedback_seq_regression.after_recovery: bool` (W-04 — distinguishes expected post-recovery regressions from unexpected ones); `camera_thread_join_timeout` / `rx_thread_join_timeout` WARN events (W-06 — surfaces handle-race risk on shutdown for status panel).

### Phase 8: End-to-End and Ship Gates
**Goal**: Prove the whole system on a real stage with a real speaker, document install/setup/calibration/operation in the README, and pass the formal lint/type/commit ship gates.
**Depends on**: Phase 7
**Requirements**: DOC-01, QA-01, QA-02, QA-03, QA-04
**Success Criteria** (what must be TRUE):
  1. End-to-end smoke test on stage: real auto-detected Uno, real OBS VCam, real speaker — the camera tracks the pastor for ≥ 5 minutes with no overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk
  2. `README.md` covers `uv sync` install, OBS Virtual Camera setup, FOV calibration procedure, run command, and the hotkey table — a fresh operator can bring the system up from the README alone
  3. `ruff check` and `mypy --strict` exit zero across the full `src/pastor_tracker/` and `tests/` trees
  4. Git history is one Conventional Commit per module, matching the Order of Work in PROMPT.md (scaffold → core → arduino → camera → perception → intent → control → pipeline → ui → e2e/docs)
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Scaffold, Config, Core Math | 2/3 | In Progress|  |
| 2. Arduino I/O | 0/3 | Not started | - |
| 3. Camera I/O | 0/2 | Not started | - |
| 4. Perception | 0/3 | Not started | - |
| 5. Intent and Control | 6/6 | Complete   | 2026-05-06 |
| 6. Pipeline Orchestrator | 0/4 | Not started | - |
| 7. UI Dashboard | 0/TBD | Not started | - |
| 8. End-to-End and Ship Gates | 0/TBD | Not started | - |

---
*Roadmap created: 2026-05-03*
*Granularity: standard (8 phases) — derived from REQUIREMENTS.md categories and PROMPT.md Order of Work*
