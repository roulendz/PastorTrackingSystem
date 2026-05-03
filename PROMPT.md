# Pastor Tracking System — Greenfield 2026

Build Python 3.12 desktop app. Track speaker on stage. Drive Arduino stepper via COM3. Input = OBS Virtual Camera. Smooth cinematic pan with rule-of-thirds lead-room.

## Stack (2026 SOTA)

- **Python 3.12**, package manager: `uv`
- **Lint/format:** `ruff` (replaces black + flake8 + isort)
- **Type check:** `mypy --strict` — every function annotated, no `Any`
- **Validation:** `pydantic` v2 for config + DTOs (frozen models)
- **Logging:** `structlog` JSON output, no `print`
- **Testing:** `pytest` + `hypothesis` for property tests
- **Detection:** `ultralytics` YOLO11-pose (faster + more accurate than MediaPipe in 2026)
- **Tracking ID persistence:** BoT-SORT (built into ultralytics)
- **State estimation:** Kalman filter on subject centroid (`filterpy`)
- **Camera I/O:** OpenCV with DirectShow backend
- **Serial:** `pyserial` with async wrapper
- **GUI:** `dearpygui` (immediate-mode, low-latency live preview)
- **Concurrency:** `asyncio` for I/O, `threading` only for blocking serial RX

## Engineering Principles (enforce strictly)

1. **Tiger-style:** fail fast, fail loud. Validate inputs at boundaries. Crash on contract violation. No silent excepts. No `except Exception: pass`.
2. **SRP:** one class = one reason to change. One function = one verb.
3. **DRY:** zero duplicated logic. Shared math goes in `core/`.
4. **Pure core, dirty edges:** detection/framing/control logic = pure functions on dataclasses. Side effects (serial, camera, GUI) at the rim.
5. **No nested conditionals deeper than 2 levels.** Use guard clauses + early returns.
6. **No magic numbers in code** — everything tunable in `Config`.
7. **Standard PEP8 naming:** `snake_case` functions/vars, `PascalCase` classes, `SCREAMING_SNAKE` constants. Descriptive names, no abbreviations (`subject_center_x_normalized`, not `cx`).
8. **Type hints everywhere.** No `Any`. Use `Literal`, `NewType`, `TypeAlias` where it sharpens contracts.
9. **Immutable data.** Pydantic `frozen=True`, dataclasses `frozen=True`. Mutate via `.model_copy(update=...)`.
10. **Structured commits.** Conventional Commits, one logical change per commit.

## Architecture

Unidirectional pipeline:

```
OBS VCam ─► FrameSource ─► PoseDetector ─► SubjectTracker ─► MotionAnalyzer
                                                                   │
                            ArduinoMotor ◄── PanController ◄── Framer
```

Each stage = pure transform on typed DTO. Orchestrator wires stages. No stage knows another stage's internals.

```
pastor_tracker/
├── pyproject.toml
├── src/pastor_tracker/
│   ├── __main__.py                   # entry, CLI args, async event loop
│   ├── config.py                     # Pydantic Settings, env + JSON
│   ├── core/
│   │   ├── types.py                  # Frame, Detection, TrackedSubject, MotionState, FramingTarget, MotorCommand
│   │   ├── geometry.py               # FOV math, normalized↔angle conversions
│   │   └── damping.py                # critically-damped 2nd-order follower
│   ├── io/
│   │   ├── obs_camera.py             # async frame source, drops stale frames
│   │   └── arduino_motor.py          # async serial, RX thread, FB parser
│   ├── perception/
│   │   ├── pose_detector.py          # YOLO11-pose wrapper
│   │   └── subject_tracker.py        # BoT-SORT ID lock, Kalman smoothing
│   ├── intent/
│   │   ├── motion_analyzer.py        # velocity, direction, dwell detection
│   │   └── framer.py                 # rule-of-thirds intent + hysteresis
│   ├── control/
│   │   ├── pan_controller.py         # damped follower, deadband, vel-clamp
│   │   └── command_dispatcher.py     # rate-limited M: emitter
│   ├── pipeline.py                   # asyncio orchestrator
│   └── ui/
│       └── dashboard.py              # DearPyGui live view + tuning sliders
└── tests/
    ├── test_geometry.py              # property tests
    ├── test_damping.py               # step response, no overshoot
    ├── test_motion_analyzer.py       # hysteresis, dwell
    ├── test_framer.py                # third selection logic
    ├── test_pan_controller.py        # deadband, clamp
    └── test_arduino_protocol.py      # parser against canned FB lines
```

## Subject Tracking — 2026 Best Practices

### Detection: YOLO11-pose
- 17 COCO keypoints. Single-class person.
- Run on GPU if available (CUDA), CPU fallback.
- Async inference: detection runs in process pool, doesn't block capture.
- Subject centroid = weighted mean of nose (0.4) + shoulder midpoint (0.4) + hip midpoint (0.2). Robust to head turns and arm gestures.
- Reject detection if mean keypoint confidence < 0.55.

### ID persistence: BoT-SORT
- Built into ultralytics `model.track(persist=True, tracker="botsort.yaml")`.
- Lock onto **primary subject** = highest-confidence person in central 60% of frame at start.
- Persist that track ID across occlusions. Ignore other detections (audience, sign-language interpreter).
- If lock lost > 2.0s → re-acquire via central-frame heuristic, log WARN.

### State estimation: Kalman filter
- 4-state: `[x, y, vx, vy]` in normalized frame coords.
- Predict during detection gaps. Update on each detection.
- Output smoothed `subject_state` to motion analyzer. Eliminates per-frame noise without lag of EMA.

### Motion intent (rule-of-thirds lead-room)
Camera framing follows speaker's intent, not raw position. From Kalman velocity:

| Sustained `vx` (normalized/sec) | Framing target |
|---|---|
| `> +0.08` for ≥ 0.3s (moving frame-right) | place subject at **left third** (0.333) |
| `< -0.08` for ≥ 0.3s (moving frame-left) | place subject at **right third** (0.667) |
| `\|vx\| < 0.03` for ≥ 1.5s (dwelling) | place subject at **center** (0.500) |

Hysteresis prevents thrash. Threshold values in config.

### Smoothing: critically-damped second-order follower
NOT a PID. PID overshoots and oscillates — bad for cinematography.

```
state: position p, velocity v
target: x_target
omega = 2*pi / time_constant
v += omega^2 * (x_target - p) * dt - 2*omega*v*dt
p += v * dt
```

Time constant `pan_response_time_seconds` ≈ 0.6s (speaker movement), framer time constant ≈ 0.8s (third transitions).

Apply to:
1. Framing target (third → smoothed third)
2. Motor angle (normalized error → angle delta → smoothed motor target)

Two-stage damping = no jerk, no overshoot, cinematic feel.

### Anti-jitter
- **Deadband:** suppress motor command if `|angle_delta| < 0.4°`.
- **Velocity clamp:** max pan rate `30°/s` (config).
- **Command throttle:** emit `M:` only if delta > 0.2° AND ≥ 50ms since last command.
- **Frame staleness:** drop frames older than 100ms in capture queue.

## Arduino Protocol

Firmware fixed. Use as-is.

**TX (newline-terminated, 115200 baud):**
| Cmd | Meaning |
|---|---|
| `M:<deg>` | move to absolute angle |
| `S:<maxSpd>,<maxAcc>,<p>,<i>,<d>` | settings |
| `R` | reset position to 0 |
| `Q` | query state (immediate FB) |
| `E` | emergency stop |
| `H` | home (move to 0) |
| `X:0\|1` | driver disable/enable |

**RX:**
```
FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState
FB:<curAng>,<tgtAng>,<speed>,<isRun 0|1>,<microsTs>,<seq>,<accState 0..3>
READY
ERROR:<code> - <message>
```

`accelState`: 0=stopped, 1=accel, 2=cruise, 3=decel. `seq` monotonic — gap > 5 = WARN.

**Mechanics:** 200 steps × 180:1 gear × 8 microsteps = 288000 steps/rev.

## OBS Virtual Camera

```python
cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
```

Enumerate devices via `pygrabber.dshow_graph.FilterGraph`, match `"OBS Virtual Camera"`. Hard-fail with device list if not found. Target 1920×1080 @ 30fps, fall back to 1280×720 if frame-time budget breached.

Each frame stamped with `time.perf_counter_ns()` at grab time.

## Config (Pydantic v2, frozen, env+JSON)

```python
class Config(BaseSettings, frozen=True):
    arduino_port: str = "COM3"
    arduino_baud: int = 115200
    obs_camera_name: str = "OBS Virtual Camera"
    capture_width: int = 1920
    capture_height: int = 1080
    capture_fps: int = 30
    camera_horizontal_fov_deg: float = 70.0
    detection_confidence_min: float = 0.55
    motion_threshold_norm_per_sec: float = 0.08
    motion_hysteresis_sec: float = 0.3
    dwell_threshold_norm_per_sec: float = 0.03
    dwell_duration_sec: float = 1.5
    framing_time_constant_sec: float = 0.8
    pan_time_constant_sec: float = 0.6
    pan_deadband_deg: float = 0.4
    pan_max_velocity_deg_per_sec: float = 30.0
    motor_max_speed_steps_per_sec: float = 25000.0
    motor_max_accel_steps_per_sec2: float = 12500.0
    command_min_delta_deg: float = 0.2
    command_min_interval_ms: int = 50
```

Validation: ranges, port format, FOV positive. Crash at startup on invalid.

## GUI

DearPyGui single window:
- Live preview with skeleton overlay, subject ID badge, framing-target vertical line, current/target angle text
- Live sliders: pan time-constant, deadband, max velocity, FOV
- Buttons: `Start` `Pause` `Home` `E-Stop` `Save Config`
- Status: motor link, camera FPS, detection conf, ID lock state, last error
- Hotkeys: `S` start, `P` pause, `H` home, `E` estop, `Q` quit

## Failure Modes (loud crash, not silent fallback)

| Condition | Action |
|---|---|
| Serial open fails | exit, print error |
| OBS VCam missing | exit, list available cameras |
| 3 consecutive frames no detection | hold position, log WARN |
| Lock lost > 2.0s | re-acquire, log WARN |
| `ERROR:` from Arduino | halt tracking, surface in UI, require manual reset |
| `seq` gap > 5 | log WARN |
| Frame queue stall > 200ms | log ERROR, restart capture |

## Forbidden

- `print()` in app code
- Bare `except:` or `except Exception: pass`
- Globals (except module-level constants)
- `time.sleep()` in main loop
- Magic numbers
- Commented-out code
- TODO without issue number
- Mocked Kalman/damping math in tests — test real implementations

## Deliverables

1. `pyproject.toml` with pinned deps (`uv lock`)
2. Full `src/pastor_tracker/` tree, real implementations
3. `tests/` with property tests for math, integration test for protocol parser using fake serial replay
4. `README.md`: install (`uv sync`), OBS VCam setup, FOV calibration procedure, run command, hotkey table
5. `ruff check` + `mypy --strict` clean
6. One commit per module, Conventional Commits

## Order of work

1. Scaffold tree + `pyproject.toml` + lint config
2. `core/types.py` + `core/geometry.py` + `core/damping.py` with full tests
3. `io/arduino_motor.py` — verify against real COM3
4. `io/obs_camera.py` — verify frame grab
5. `perception/pose_detector.py` + `subject_tracker.py`
6. `intent/motion_analyzer.py` + `framer.py`
7. `control/pan_controller.py` + `command_dispatcher.py`
8. `pipeline.py` orchestrator
9. `ui/dashboard.py` last
10. End-to-end smoke test on stage

Begin.
