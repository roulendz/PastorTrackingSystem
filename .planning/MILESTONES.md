# Milestones

## v1.0 — Pastor Tracker MVP (Shipped: 2026-05-11)

**Status:** Shipped with tech debt (no code-level blockers; on-stage smoke deferred to next stage rehearsal).
**Audit:** [.planning/milestones/v1.0-MILESTONE-AUDIT.md](milestones/v1.0-MILESTONE-AUDIT.md)
**Roadmap archive:** [.planning/milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)
**Requirements archive:** [.planning/milestones/v1.0-REQUIREMENTS.md](milestones/v1.0-REQUIREMENTS.md)

**Delivered:** Python 3.12 desktop app that tracks a single primary speaker on stage via OBS Virtual Camera and drives an Arduino stepper over USB serial for cinematic, jitter-free auto-pan. Pure-core / dirty-edges architecture, immutable DTOs, two-stage critically-damped follower (no PID), YOLO11-pose + BoT-SORT + 4-state Kalman, DearPyGui dashboard.

**Phases:** 8 (Scaffold + Core Math → Arduino I/O → Camera I/O → Perception → Intent + Control → Pipeline Orchestrator → UI Dashboard → End-to-End + Ship Gates)
**Plans:** 26 across 8 phases
**Tests:** 561 passed, 1 skipped
**Requirements:** 55 of 57 satisfied (98% — 2 deferred per Known Gaps below)
**LOC:** ~32 source files in `src/pastor_tracker/`

**Key accomplishments:**

1. **Pure-core architecture** — `core/types.py` frozen DTOs (Frame, Detection, TrackedSubject, MotionState, FramingTarget, MotorCommand), `core/geometry.py` FOV math, `core/damping.py` critically-damped 2nd-order follower (Holden exact closed-form). Property-tested. No PID. No EMA. No mocked math in tests.
2. **Arduino driver** — VID:PID auto-detect (Uno R3/R4/CH340/FTDI), `READY:v2` boot handshake, 200 ms heartbeat, threaded RX parser for FB/ERROR/RESET lines, watchdog-reset recovery, latched-error gate prioritising over pause.
3. **OBS Virtual Camera I/O** — async DirectShow capture, OBSCameraNotFoundError with available-device list, 1920×1080@30 → 720p auto-fallback, `perf_counter_ns()` frame timestamps, > 100 ms stale-frame drop.
4. **Perception pipeline** — YOLO11-pose via process pool, BoT-SORT ID persistence, central-60% primary lock, 2 s lock-loss re-acquire, weighted-keypoint centroid (nose 0.4 / shoulders 0.4 / hips 0.2), 4-state Kalman with hold-during-gap.
5. **Two-stage damping (no PID)** — `Framer` smooths rule-of-thirds target (~0.8 s τ); `PanController` smooths motor angle (~0.6 s τ), deadband 0.4°, velocity clamp 30°/s, command dispatcher rate-limits `M:` to ≥ 50 ms gaps + > 0.2° deltas. Step response asserts no overshoot.
6. **Pipeline orchestrator** — single asyncio tick loop wires the eight stages with typed DTOs; pure transforms only; 6-state lifecycle (idle/tracking/paused/homing/e_stopped/closed) accessible via UI dispatch.
7. **DearPyGui dashboard** — 960×540 live preview with skeleton + framing-target overlays, 4 live tuning sliders (`_pending_config` + Save Config restart), 5 buttons + 5 hotkeys (E debounced on press, S/P/H/Q on release), status panel + event-bus "Last error" widget.
8. **Operator README** — full install / OBS setup / FOV calibration / run / hotkey / troubleshooting manual; doc-grep validated against `config.py` field names and `__main__.py` argparse flags.

**Quality gates:**

| Gate | Status |
|------|--------|
| `ruff check src tests` | ✓ Clean |
| `mypy --strict src` | ✓ Clean (78 source files) |
| `pytest tests` | ✓ 561 passed, 1 skipped |
| Conventional Commits | ✓ Clean (sampled 30 commits) |
| `mypy --strict src tests` | ✗ Env regression (mypy 1.20.2 wheel + pydantic.mypy plugin — see Known Gaps) |
| `ruff format --check` | ⚠ 73-file drift (optional gate, never pre-commit-enforced) |

**Known deferred items at close:** 12 (see [STATE.md `## Deferred Items`](STATE.md)). Three real follow-ups:
- **QA-04 on-stage smoke** — 6 unchecked pass criteria in `08-HUMAN-UAT.md`; schedule on next stage rehearsal with real Uno + OBS VCam + speaker.
- **QA-02 mypy env regression** — pin `mypy = "1.19.x"` (or wait for upstream fix) to repair pydantic.mypy plugin crash on test fixtures.
- **QA-01 ruff format drift** — single-commit cleanup pass.

The 7 `human_needed` phase verifications (Phases 2, 3, 4, 6, 7, 8 + Phase 8 itself) all reduce to the same QA-04 on-stage gate.

**Key decisions** (full log in PROJECT.md):
- Pure core / dirty edges separation (no side effects in `core/`)
- Critically-damped 2nd-order follower over PID (overshoot incompatible with cinematic feel)
- YOLO11-pose over MediaPipe (2026 faster + more accurate)
- BoT-SORT ID persistence over per-frame matching
- Kalman over EMA (lag-free prediction during gaps)
- VID:PID auto-detect over fixed COM port
- DearPyGui over Qt/Tk (immediate-mode low-latency)
- Two-stage damping (framing + motor) eliminates jerk at both layers
- `mypy --strict` with no `Any` — sharpens contracts via `Literal` / `NewType` / `TypeAlias`

---
