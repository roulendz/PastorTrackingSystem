# Pastor Tracking System

<!-- GSD:project-start source:PROJECT.md -->
## Project

Python 3.12 desktop app that tracks a single primary speaker on stage and drives an Arduino stepper motor over USB serial to pan a camera mount. Input: OBS Virtual Camera. Output: cinematic, jitter-free pan with rule-of-thirds lead-room framing.

**Core value:** No overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk. If pan smoothness or lock stability fails, nothing else matters.

See `.planning/PROJECT.md` for full context, constraints, and key decisions.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:PROMPT.md -->
## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Python 3.12 | latest stable |
| Package manager | `uv` | fast, lockfile-driven |
| Lint/format | `ruff` | replaces black + flake8 + isort |
| Type check | `mypy --strict` | every function annotated, no `Any` |
| Validation | `pydantic` v2 (`frozen=True`) | config + DTOs |
| Logging | `structlog` JSON | no `print()` allowed |
| Testing | `pytest` + `hypothesis` | property tests for math |
| Detection | `ultralytics` YOLO11-pose | faster + more accurate than MediaPipe in 2026 |
| ID persistence | BoT-SORT (built into ultralytics) | track across occlusions |
| State estimation | `filterpy` Kalman 4-state | predict during gaps, no EMA lag |
| Camera I/O | `opencv-python` + DirectShow + `pygrabber` | OBS VCam enumeration |
| Serial | `pyserial` (async wrapper) | threading for blocking RX only |
| GUI | `dearpygui` | immediate-mode, low-latency live preview |
| Concurrency | `asyncio` for I/O, `threading` for blocking serial RX | |

**Hardware:** Arduino Uno R3/R4, AccelStepper firmware v2 (shipped, in `arduino/stepper_controller/`), 115 200 baud serial, 200 steps × 180:1 gear × 8 microsteps = 288 000 steps/rev.

**Forbidden libraries / patterns (never reintroduce):** PID, MediaPipe, EMA on detection stream, mocked Kalman/damping in tests.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:PROMPT.md -->
## Conventions

**Engineering culture — enforced strictly:**

1. **Tiger-style:** fail fast, fail loud. Validate inputs at boundaries. Crash on contract violation. No silent excepts. No `except Exception: pass`.
2. **SRP:** one class = one reason to change. One function = one verb.
3. **DRY:** zero duplicated logic. Shared math goes in `core/`.
4. **Pure core, dirty edges:** detection/framing/control logic = pure functions on dataclasses. Side effects (serial, camera, GUI) at the rim.
5. **No nested conditionals deeper than 2 levels.** Use guard clauses + early returns. No spaghetti, no nested if-in-ifs.
6. **No magic numbers in code** — everything tunable lives in `Config`.
7. **PEP8 strict naming:** `snake_case` functions/vars, `PascalCase` classes, `SCREAMING_SNAKE` constants. Descriptive names — no abbreviations (`subject_center_x_normalized`, not `cx`).
8. **Type hints everywhere.** No `Any`. Use `Literal`, `NewType`, `TypeAlias` to sharpen contracts.
9. **Immutable data.** Pydantic `frozen=True`, dataclasses `frozen=True`. Mutate via `.model_copy(update=...)`.
10. **Conventional Commits.** One logical change per commit.
11. **Functional code.** Pure transforms on typed DTOs.

**Forbidden in app code:**
- `print()` (use `structlog`)
- Bare `except:` or `except Exception: pass`
- Globals (except module-level constants)
- `time.sleep()` in main loop
- Magic numbers
- Commented-out code
- TODO without issue number
- Mocked Kalman/damping math in tests — test real implementations
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:PROMPT.md -->
## Architecture

Unidirectional pipeline. Each stage = pure transform on typed DTO. Orchestrator wires stages. No stage knows another stage's internals.

```
OBS VCam ─► FrameSource ─► PoseDetector ─► SubjectTracker ─► MotionAnalyzer
                                                                   │
                            ArduinoMotor ◄── PanController ◄── Framer
```

```
repo_root/
├── arduino/stepper_controller/         # PlatformIO firmware v2 (shipped)
│   ├── platformio.ini                  # Uno + native test envs
│   ├── include/protocol.h              # constants, enums, EEPROM layout
│   ├── src/main.cpp                    # firmware entry
│   └── test/                           # Unity unit tests
└── pastor_tracker/                     # Python tracking app (build target)
    ├── pyproject.toml
    ├── src/pastor_tracker/
    │   ├── __main__.py                 # entry, CLI args, async event loop
    │   ├── config.py                   # Pydantic Settings, env + JSON
    │   ├── core/
    │   │   ├── types.py                # Frame, Detection, TrackedSubject, MotionState, FramingTarget, MotorCommand
    │   │   ├── geometry.py             # FOV math, normalized↔angle conversions
    │   │   └── damping.py              # critically-damped 2nd-order follower
    │   ├── io/
    │   │   ├── obs_camera.py           # async frame source, drops stale frames
    │   │   └── arduino_motor.py        # async serial, RX thread, FB parser, heartbeat task
    │   ├── perception/
    │   │   ├── pose_detector.py        # YOLO11-pose wrapper
    │   │   └── subject_tracker.py      # BoT-SORT ID lock, Kalman smoothing
    │   ├── intent/
    │   │   ├── motion_analyzer.py      # velocity, direction, dwell detection
    │   │   └── framer.py               # rule-of-thirds intent + hysteresis
    │   ├── control/
    │   │   ├── pan_controller.py       # damped follower, deadband, vel-clamp
    │   │   └── command_dispatcher.py   # rate-limited M: emitter
    │   ├── pipeline.py                 # asyncio orchestrator
    │   └── ui/
    │       └── dashboard.py            # DearPyGui live view + tuning sliders
    └── tests/
        ├── test_geometry.py            # property tests
        ├── test_damping.py             # step response, no overshoot
        ├── test_motion_analyzer.py     # hysteresis, dwell
        ├── test_framer.py              # third selection logic
        ├── test_pan_controller.py      # deadband, clamp
        └── test_arduino_protocol.py    # parser against canned FB lines + heartbeat
```

**Two-stage damping (NOT PID):** framing target (≈ 0.8 s) → motor angle (≈ 0.6 s). PID overshoots and oscillates — explicitly forbidden.

**Watchdogs:** AVR `WDTO_500MS` hardware watchdog + 1000 ms PC-heartbeat firmware watchdog. PC sends 200 ms heartbeat. Re-receipt of `READY:v2` mid-session = MCU reset → re-issue settings + limits.

**Arduino port discovery:** VID:PID auto-detect over `serial.tools.list_ports.comports()` matching Uno R3 (`2341:0043`), R4 (`2341:0069`), CH340 (`1A86:7523`), FTDI (`0403:6001`). Fall back to `arduino_port` from config if no match. Fixed COM port hardcoding is forbidden — port number changes per USB jack.

See `.planning/PROJECT.md` and `PROMPT.md` for full architecture, protocol v2 contract, error codes, and failure-mode table.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work
- `/gsd-plan-phase <N>` to plan the next phase

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.

**Phase 1 entry:** `/gsd-discuss-phase 1` (recommended) or `/gsd-plan-phase 1` (skip discussion).
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` — do not edit manually.
<!-- GSD:profile-end -->
