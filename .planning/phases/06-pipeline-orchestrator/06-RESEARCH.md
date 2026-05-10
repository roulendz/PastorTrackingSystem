# Phase 6: Pipeline Orchestrator - Research

**Researched:** 2026-05-08
**Domain:** Asyncio orchestrator wiring 8 stages into one tick task with a 6-state lifecycle, deterministic snapshot DTO, e-stop short-circuit budget, and a `__main__` boot that survives Windows SIGINT. Concurrency-and-lifecycle phase, not an algorithm phase.
**Confidence:** HIGH on stage signatures and lifecycle table (CITED to in-tree code + CONTEXT.md D-01..D-18); HIGH on Phase 5 composition pattern (CITED to `tests/test_intent_control_pipeline.py` + Plan 05-06 SUMMARY); HIGH on Windows `add_signal_handler` limitation (CITED to Python docs + cpython issue tracker); HIGH on atomic-attribute reads on CPython (CITED to language guarantee); MEDIUM on exact tick-task cancellation semantics during `async for ... in detector.stream(camera.frames())` (verified against Phase 4 `_DETECTIONS_POLL_TIMEOUT_SEC` exit path but not stress-tested under e-stop pressure yet — that's a Wave-1 deliverable).

## Summary

Phase 6 is **wiring + lifecycle**. Every stage already exists with a uniform `consume(upstream, now_ns)` / `decide(angle, now_ns)` shape, every fake exists in `tests/fixtures/`, and every numeric tunable is in `Config`. Phase 5's `tests/test_intent_control_pipeline.py` already proves the four-stage Phase-5 chain composes correctly under a synchronous `_drive_pipeline` helper — Phase 6 generalizes that to the eight-stage async chain, adds a 6-state lifecycle, and replaces the Phase-1 boot stub.

The risk surface is concurrency and shutdown semantics, not math. Specifically: (1) e-stop must reach firmware within 200 ms while the tick task is mid-await on `detector.stream(...)`, (2) `quit()` must drain cleanly without leaving `Task exception was never retrieved` warnings (which under `filterwarnings=["error"]` in our pytest config become flakes — see Phase 2 Plan 02-03 W-05 precedent), (3) `latest_frame` must update atomically without locks (CPython attribute writes ARE atomic; documented in the language reference), (4) `__main__` SIGINT handling must not call `loop.add_signal_handler` on Windows (raises `NotImplementedError` per [cpython#137863](https://github.com/python/cpython/issues/137863) and [Python docs](https://docs.python.org/3/library/asyncio-platforms.html)).

**Primary recommendation:** Single `pipeline.py` file (~350-400 lines target — well under the ~400-line CONTEXT.md D-disc threshold for a `_lifecycle.py` split). One `enum.Enum` `_PipelineState`, one `dict[(state, command), state]` transition table checked at the top of every lifecycle method, one mutable private `_PipelineCache` dataclass updated by the tick task and copied into a frozen `PipelineSnapshot` on `snapshot()` reads. E-stop runs `motor.send_emergency_stop()` inline (it already bypasses both the pause gate and the latched-error gate per `arduino_motor.py:923-928`), making the 200 ms budget trivially achievable since the tick task does not own the motor's TX lock between awaits. `__main__` uses platform-conditional `signal.signal` (Windows) vs `loop.add_signal_handler` (POSIX), wrapped in `asyncio.run(_amain(config))`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config / cross-phase contracts**
- Forbidden: PID, EMA on detection stream, mocked Kalman / damping math in tests, `print()`, bare `except:`, magic numbers (Config is authoritative), nested conditionals > 2 levels
- Pure-core / dirty-edges layering — `pipeline.py` is the **only** module that touches both `io/` and `intent/`+`control/`; stages stay pure
- DTO-only stage I/O — every stage receives and emits typed frozen DTOs; no shared mutable state
- AVR `WDTO_500MS` hardware watchdog + 1000 ms PC-heartbeat firmware watchdog; PC sends 200 ms heartbeat — already in `ArduinoMotor` (Phase 2). E-stop budget = 200 ms ⇒ Pipeline must `await motor.send_emergency_stop()` synchronously in `e_stop()`, no buffering
- Re-receipt of `READY:v2` mid-session = MCU reset → `_recover()` re-issues settings + limits (already handled by `ArduinoMotor`); Pipeline must re-issue via the motor's recovery path (D-11)
- VID:PID auto-detect over fixed COM port — already in `ArduinoMotor` constructor (`SerialTransport` discovery); Pipeline does not duplicate
- Pydantic v2 frozen DTOs; mutate via `.model_copy(update=...)`
- Tiger-style fail-fast on contract violation; `mypy --strict` no `Any`; `structlog` JSON logging only
- Conventional Commits, one logical change per commit

**Locked by CONTEXT.md (D-01..D-18 — verbatim, the canonical IDs the planner cites):**

| ID | Decision (1-line reference) |
|----|------------------------------|
| D-01 | Frame-driven loop: `async for frame in detector.stream(camera.frames())` clocks every downstream stage; rate = camera fps. |
| D-02 | Stage composition is a single sequential await chain inside one task: `tracker.consume → analyzer.consume → framer.consume → controller.consume → dispatcher.decide` — all pure, sub-ms; no per-stage queues. |
| D-03 | Detector backpressure handled by `PoseDetector.stream(frames)` (Phase 4 BL-01) — detector internally drops stale frames; orchestrator does not skip ticks itself. |
| D-04 | `now_ns` per tick = `frame.timestamp_ns` (camera's `perf_counter_ns()`); passed to every `consume(...)` and `decide(...)` call. Orchestrator never reads its own wall clock for stage time. |
| D-05 | `start()` opens camera + arduino, sends `send_settings(...)` and `send_limits(pan_min_deg, pan_max_deg)`, then enters RUNNING tick loop in a background `asyncio.Task`. |
| D-06 | `pause()` flips internal `_dispatch_enabled = False`; tick loop continues so preview stays live; dispatcher decisions are computed but **not** sent to motor. No firmware `S:` issued. |
| D-07 | `e_stop()` calls `motor.send_emergency_stop()` immediately, transitions to E_STOPPED, halts dispatch. Recovery requires explicit `start()`. Must complete within one heartbeat interval (200 ms). |
| D-08 | `home()` is rejected (raise `OrchestratorRejected`) when `0.0` is outside `[config.pan_min_deg, config.pan_max_deg]`. Otherwise: `motor.send_home()`, transition RUNNING/PAUSED → HOMING → PAUSED. |
| D-09 | `quit()` is a graceful drain: cancel tick task → `camera.stop()` → `motor.close()` → set state QUITTING then return. Idempotent (second call is a no-op). |
| D-10 | Camera errors (`CameraOpenError`, `CameraStallError`) propagate: log structured `pipeline_camera_failed`, transition STOPPED, re-raise to caller (`__main__`). No auto-retry. |
| D-11 | Arduino `WatchdogResetError` is recovered in-place by `ArduinoMotor._recover()` already (Phase 2); orchestrator continues RUNNING after motor recovers and re-issues settings+limits via the motor's own recovery path. |
| D-12 | Arduino `LinkLostError` propagates: log `pipeline_arduino_link_lost`, transition STOPPED, re-raise to caller. |
| D-13 | Detector / pose-stage exceptions are fail-fast: log `pipeline_perception_failed`, transition STOPPED, re-raise. |
| D-14 | Unhandled exception in tick loop is caught at the orchestrator boundary: log `pipeline_crashed` with exception info, transition STOPPED, re-raise to `__main__`. |
| D-15 | Public lifecycle API on `Pipeline` is async: `start() / pause() / resume() / home() / e_stop() / quit()`. Each is idempotent on its target state and raises `OrchestratorRejected` on invalid source-state transitions. |
| D-16 | `Pipeline.snapshot() -> PipelineSnapshot` returns a frozen Pydantic DTO with: `state`, `last_frame_ts_ns`, `last_intent`, `last_target_x_normalized`, `last_pan_angle_deg`, `last_emitted_angle_deg`, `motor_state`. |
| D-17 | `Pipeline.latest_frame: Frame | None` — last-frame slot updated atomically each tick (single writer = tick task; readers = UI). UI polls at its own refresh rate. No broadcast queue. |
| D-18 | Config reload is full-restart: Phase 7 calls `pipeline.quit()` then re-instantiates `Pipeline(new_config, ...)` and calls `start()` again. No hot-reload of frozen Config fields. |

### Claude's Discretion
- Internal helper / private method names beyond the public surface
- Whether `_PipelineState` is `enum.Enum` or `Literal` (recommend `enum.Enum` for transition-table clarity)
- File granularity — single file is the default; small private helper module (e.g. `_lifecycle.py` for the state-table) permitted only if `pipeline.py` exceeds ~400 lines
- Logging key names — keep `event="..."` shape consistent with Phases 2–5 conventions
- Internal docstring depth — only where the WHY is non-obvious (CLAUDE.md tone rule)
- Test fixture composition — reuse Phase 2 `FakeSerialTransport`, Phase 3 `FakeVideoSource`, Phase 4 `FakePoseEngine`; build a single `_pipeline_with_fakes(...)` helper

### Deferred Ideas (OUT OF SCOPE)
- Hot-reload of Config subset (gain knobs without full restart) — v2; Phase 7 uses full restart per D-18
- Telemetry export of every `MotorCommand` to disk for post-mortem replay — v2
- Multi-camera failover (USB jack hotplug) — v2; Phase 6 fails-fast on camera disconnect per D-10
- Auto-restart on `pipeline_crashed` with exponential backoff — explicitly rejected (tiger-style fail-fast); Phase 8 docs cover manual restart
- Pause-firmware via `S:` protocol message — rejected (D-06 keeps preview live; firmware pause would cut feedback)
- Per-stage timing histogram in snapshot — v2 telemetry
- Pipeline subprocess isolation (run detector in separate process) — out of scope; current single-asyncio-task is sufficient at 30 fps
- Hot-swap of stage implementations at runtime — rejected; restart is the supported reconfigure path
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| **PIPE-01** | `pipeline.py` asyncio orchestrator wiring `OBS VCam → FrameSource → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor` | §Module Layout (single `pipeline.py`); §Tick Loop Concrete Shape (full body) — D-01..D-04 |
| **PIPE-02** | Each stage = pure transform on typed DTO; orchestrator owns wiring; no stage knows another stage's internals | §Tick Loop body composes only `consume()`/`decide()` Public methods on already-built stages, accesses `Frame.timestamp_ns` (the only DTO field reach), no private-attribute access — verified by reading every stage's API in §Code Examples |
| **PIPE-03** | Lifecycle — start, pause, home, e-stop, quit (mapped to UI hotkeys) | §Lifecycle State Machine (6 states + transition table); §E-stop Concurrency (200 ms budget); §`__main__` Refactor (SIGINT mapping); D-05..D-09, D-15 |
</phase_requirements>

## Architectural Responsibility Map

> Phase 6 is fully back-end (no browser/CDN/database tiers). The relevant tiers are *Application Process* (Python orchestrator), *Operating-System Process* (`__main__` + signals), *Hardware/Driver* (Arduino + OBS via DirectShow + USB serial), and *Pure Core* (DTOs + math).

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Stage composition (the 8-stage chain) | Application Process — `pipeline.py` `_tick_loop()` | Pure Core (DTOs flow through unchanged) | D-01/D-02 lock this to one task in `pipeline.py`. |
| Lifecycle state machine | Application Process — `pipeline.py` | — | All transitions are in-process; no OS or hardware tier should know what state the orchestrator is in. D-15 lock. |
| Frame timestamp threading | Pure Core — `Frame.timestamp_ns` is the source-of-truth clock | Application Process (orchestrator reads it, threads it through stages) | D-04 forbids the orchestrator from reading a wall clock for stage time. |
| Hardware lifecycle (camera open/close, motor handshake) | Hardware/Driver tier — already owned by `ObsCamera` / `ArduinoMotor` | Application Process (orchestrator only calls public `start()`/`stop()`/`close()`) | D-05/D-09 — pipeline orchestrates BUT does not duplicate per-driver concerns. |
| Snapshot DTO assembly | Application Process — `pipeline.py` cache + `snapshot()` | Pure Core (`PipelineSnapshot` is a frozen Pydantic DTO) | D-16 — read-only, cheap, copy-from-cache. |
| `latest_frame` slot | Application Process — single attribute on `Pipeline` | — | D-17 — one writer (tick task), N readers (UI). CPython attribute write atomicity sufficient. |
| Process boot, signal handling, exit code | Operating-System Process — `__main__.py` | Application Process (constructs `Pipeline`) | Operator-facing surface; `Pipeline` itself is platform-agnostic. |
| Config loading | Operating-System Process — `__main__.py` calls `Config()` (env+JSON) | — | Config is constructed once, frozen, passed by reference into `Pipeline`. D-18 lock — no hot reload. |

**Sanity check:** No "business logic" landed in OS-process tier; no hardware-driver concern leaked into pipeline tier; no DTO field reach beyond `Frame.timestamp_ns` (verified by inspecting every stage's `consume()`/`decide()` signature in §Code Examples). PIPE-02 is structurally enforceable.

## Standard Stack

### Core (already in repo, no new deps)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `asyncio` | 3.12 | Event loop, `Task`, `Event`, `wait_for`, `gather`, `run` | The only async runtime in scope per CLAUDE.md. |
| Python stdlib `enum` | 3.12 | `_PipelineState` enum | Phase 2 `_MotorState` + Phase 3 `_CamState` precedent — every internal state machine in this repo is `enum.Enum`. |
| `pydantic` v2 | already pinned | `PipelineSnapshot(BaseModel, frozen=True, extra="forbid")` | Phase-1..5 frozen-DTO discipline. |
| `structlog` | already pinned | JSON logging via `structlog.get_logger(module="pipeline")` | CLAUDE.md no-`print()` rule + Phase 2..5 module-binding precedent. |

### Supporting (already in repo)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Python stdlib `signal` | 3.12 | SIGINT/SIGTERM in `__main__` | Windows path uses `signal.signal()`; POSIX path uses `loop.add_signal_handler()`. |
| Python stdlib `argparse` | 3.12 | `--config-json` path override in `__main__` | Tiny CLI surface; pulling in `click` would be over-engineering. |
| Python stdlib `contextlib` | 3.12 | `suppress(asyncio.CancelledError, Exception)` for clean shutdown | Phase 2 `arduino_motor.py:271,276` precedent. |

### Alternatives Considered (and rejected)

| Instead of | Could Use | Tradeoff | Verdict |
|------------|-----------|----------|---------|
| Single `_tick_loop` task | Per-stage `asyncio.Task` + `Queue` | Per-stage queues add backpressure semantics we don't need (stages are sub-ms pure transforms per Phase 5 measured 0.282 deg max delta in Plan 05-06 SUMMARY). Adds 6 cancellation surfaces vs 1. | **Rejected — D-02 lock.** |
| `enum.Enum` for `_PipelineState` | `Literal["stopped", "running", ...]` | Literal is cheaper but does not key a `dict[(state, cmd), state]` table cleanly (members are not enumerable). Phase 2/3 already use Enum. | **Use `enum.Enum`.** CONTEXT.md discretion suggestion confirmed. |
| Frozen `PipelineCache` Pydantic | Mutable dataclass `_PipelineCache` updated by tick + copied to frozen snapshot on read | A frozen Pydantic per tick would allocate a new model 30x/sec (1800/min); mutable dataclass + per-`snapshot()` copy is cheaper and does not violate immutability of the *exposed* surface. | **Use mutable internal cache + frozen public DTO.** |
| `loop.add_signal_handler` for SIGINT | `signal.signal()` (cross-platform) | `add_signal_handler` raises `NotImplementedError` on Windows ProactorEventLoop ([cpython#137863](https://github.com/python/cpython/issues/137863)). | **Platform-conditional.** |
| Per-tick `frozen=True` Pydantic snapshot in `latest_frame` slot | Direct `Frame | None` attribute | `Frame` is already a frozen dataclass; CPython single-attribute write is atomic — no lock needed. | **Direct slot per D-17.** |

**Installation:** No new deps. `pyproject.toml` is already locked at Phase 1.

**Version verification:**
```bash
# Verified against repo lockfile already; no new packages this phase.
uv lock --check  # should pass with no diffs
```

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  __main__.py                                                         │
│  - argparse(--config-json)                                           │
│  - Config(...)                                                       │
│  - configure_logging()                                               │
│  - construct ObsCamera, ArduinoMotor, PoseDetector, SubjectTracker,  │
│    MotionAnalyzer, Framer, PanController, CommandDispatcher          │
│  - Pipeline(config, motor=..., camera=..., detector=..., ...)        │
│  - asyncio.run(_amain(pipeline))                                     │
│  - signal.signal(SIGINT) (Windows) / loop.add_signal_handler (POSIX) │
│      → sets shutdown_event                                           │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ awaits
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Pipeline (pastor_tracker/pipeline.py)                               │
│  ┌─────────────────────────────────────────┐                         │
│  │  _PipelineState enum                    │                         │
│  │  _LIFECYCLE_TABLE: dict[(s,cmd), s]     │                         │
│  └─────────────────────────────────────────┘                         │
│  ┌─────────────────────────────────────────┐                         │
│  │ Public API (async):                     │                         │
│  │ start / pause / resume / home /         │                         │
│  │ e_stop / quit                           │  ──┐                    │
│  │ snapshot() -> PipelineSnapshot          │    │ raises             │
│  │ latest_frame -> Frame | None            │    │ OrchestratorRejected│
│  └─────────────────────────────────────────┘    │                    │
│              │                                  │                    │
│              ▼ start() spawns                   │                    │
│  ┌─────────────────────────────────────────┐    │                    │
│  │ _tick_task: asyncio.Task                │    │                    │
│  │   async for det in                      │    │                    │
│  │     detector.stream(camera.frames()):   │    │                    │
│  │     now_ns = det.frame.timestamp_ns     │    │                    │
│  │     latest_frame = det.frame  (atomic)  │    │                    │
│  │     subject = await tracker.consume(..) │    │                    │
│  │     motion  = await analyzer.consume(.) │    │                    │
│  │     target  = await framer.consume(...) │    │                    │
│  │     angle   = await controller.consume()│    │                    │
│  │     command = dispatcher.decide(...)    │    │                    │
│  │     _update_cache(...)                  │    │                    │
│  │     if command and _dispatch_enabled:   │    │                    │
│  │       await motor.send_motor_angle(...) │    │                    │
│  └─────────────────────────────────────────┘    │                    │
└──────────────────────────────────────────────────┴───────────────────┘
       │                          │                          │
       ▼ camera.frames()          ▼ detector.stream(...)     ▼ motor.*
┌──────────────┐           ┌──────────────┐           ┌──────────────┐
│ ObsCamera    │           │ PoseDetector │           │ ArduinoMotor │
│ (Phase 3)    │           │ (Phase 4)    │           │ (Phase 2)    │
│ frames() ──► │ ────────► │ stream()  ─► │ ──Det────►│ send_motor_  │
│ Frame        │           │  drops stale │           │   angle()    │
│ AsyncIterator│           │  internally  │           │ send_emerg_  │
│              │           │  (BL-01)     │           │   stop() —   │
│              │           │              │           │ INLINE in    │
│              │           │              │           │ e_stop()     │
└──────────────┘           └──────────────┘           └──────────────┘
       │                          │                          │
       ▼ blocking I/O             ▼ blocking I/O             ▼ blocking I/O
   DirectShow                ProcessPool +               pyserial +
   capture thread            SharedMemory                RX thread
```

The diagram describes data flow at runtime. Component-to-file mapping appears in §Module Layout below; e-stop control flow lives in §E-Stop Concurrency.

### Recommended Project Structure

```
pastor_tracker/
└── src/pastor_tracker/
    ├── __main__.py             # REPLACE Phase-1 stub: argparse + signals + asyncio.run
    ├── pipeline.py             # NEW: Pipeline class + _PipelineState + _PipelineCache
    │                            # + OrchestratorRejected. Target ~350-400 lines.
    └── core/
        └── types.py            # ADD: PipelineSnapshot (frozen Pydantic), PipelineState
                                # (TypeAlias = Literal[...] mirroring _PipelineState members)
```

`tests/test_pipeline.py` is the single Phase-6 test module; reuse all four existing fakes (`FakeSerialTransport`, `FakeVideoSource`, `FakePoseEngine`, `trajectories.py`).

### Pattern 1: Single-task tick loop (D-02)

**What:** All eight stages compose inside one `asyncio.Task`. No per-stage queues.
**When to use:** When every stage is sub-ms pure transform (Phase 5 verified) and the only async-IO calls are the bookend (`detector.stream()` upstream, `motor.send_motor_angle()` downstream — both already drop-oldest internally).
**Example:** See §Code Examples / Pattern 1.

### Pattern 2: Lifecycle transition table (D-15)

**What:** A `dict[tuple[_PipelineState, _Cmd], _PipelineState]` keyed by `(current_state, command)`. Every public lifecycle method consults the table at the top; missing key = `OrchestratorRejected`. Brings 100% branch coverage of state transitions to a single test that iterates the table.
**When to use:** Whenever the lifecycle has > 4 states and > 4 commands. Six states × six commands = 36 cells; a per-method `if/elif` would be 36 branches, hard to audit.
**Example:** See §Code Examples / Pattern 2.

### Pattern 3: Mutable cache + frozen snapshot (D-16)

**What:** Tick loop writes to a private `_PipelineCache` (mutable dataclass). `snapshot()` constructs a `PipelineSnapshot` (frozen Pydantic) from the cache fields. Single-writer; reader copies — no lock.
**When to use:** When the public DTO must be immutable but is queried at lower frequency than the writer updates.
**Example:** See §Code Examples / Pattern 3.

### Pattern 4: E-stop short-circuit (D-07)

**What:** `e_stop()` does NOT wait for the tick task. It calls `motor.send_emergency_stop()` directly (which already bypasses the motor's pause + latched-error gates per `arduino_motor.py:923-928`), flips `_dispatch_enabled=False`, transitions to `E_STOPPED`. The tick task continues to run (so the dispatcher's `if command and _dispatch_enabled` gate suppresses any in-flight `send_motor_angle` next tick) until the operator either calls `start()` (which expects E_STOPPED → RUNNING per D-15) or `quit()`.
**Why:** The motor's TX is serialized by `_tx_lock`; the tick task's `await motor.send_motor_angle()` either has already won the lock and will return imminently, or has not yet acquired it. The e-stop's `send_raw(b"E\n")` either races the in-flight `M:` (firmware-side: `E` always wins), or interleaves cleanly. Either way the e-stop reaches firmware in O(serial RTT) ≤ a few ms — well under the 200 ms heartbeat budget.
**Example:** See §E-Stop Concurrency.

### Pattern 5: Cross-platform SIGINT (`__main__`)

**What:** `if sys.platform == "win32": signal.signal(signal.SIGINT, _on_signal); else: loop.add_signal_handler(...)`.
**Why:** [`asyncio` docs](https://docs.python.org/3/library/asyncio-platforms.html) state `add_signal_handler` is unsupported on Windows. ProactorEventLoop (default since 3.8) raises `NotImplementedError`. Windows DOES deliver SIGINT to a `signal.signal()` handler installed on the main thread, so the platform-conditional pattern is the cleanest portable solution.
**Example:** See §`__main__` Refactor.

### Anti-Patterns to Avoid

- **Per-stage `asyncio.Task` + `asyncio.Queue`:** D-02 explicitly forbids this. Stages are sub-ms; queues add cancellation surfaces and reordering risk.
- **Reading wall clock inside `_tick_loop`:** D-04 forbids. Always `now_ns = detection.frame.timestamp_ns`. Phase 5 Plan 05-06 already documents this as Pitfall 5 ("cross-stage time-source mismatch").
- **Try/except wrapping individual stages inside the tick body:** Per-frame swallowing breaks tiger-style. Per D-13/D-14, exceptions propagate out of the tick task and are caught **once** at the orchestrator boundary (the task's done-callback latches state STOPPED + re-raises).
- **`asyncio.Lock` around `latest_frame`:** Single writer (tick task) + N readers (UI poll), all attribute reads/writes; CPython attribute write of a single object reference is atomic per the [language reference](https://docs.python.org/3/glossary.html#term-global-interpreter-lock) (single bytecode op `STORE_ATTR`). A lock would serialize UI reads with the tick — the opposite of what we want.
- **Calling `loop.add_signal_handler` unconditionally:** Crashes on Windows.
- **Hot-reload of Config inside `Pipeline`:** D-18 — quit + reconstruct.
- **Custom heartbeat task in `Pipeline`:** Already in `ArduinoMotor` (Phase 2 IO-ARD-05). Pipeline does not duplicate.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 200 ms heartbeat to firmware | A new `Pipeline` heartbeat task | `ArduinoMotor._heartbeat_loop()` already running per IO-ARD-05 | Phase 2 already ships it; running two would race the `_tx_lock`. |
| Watchdog-reset recovery | Re-issue settings/limits from `Pipeline` | `ArduinoMotor._recover()` already does it on mid-session `READY:v2` per IO-ARD-06 / D-11 | Phase 2 already handles the full RX→re-issue→ack→resume cycle. Pipeline must NOT call `send_settings`/`send_limits` while `_state is RECOVERING`; it would race. |
| Stale-frame drop | Filter in tick loop | `ObsCamera.frames()` already drops > 100 ms stale per IO-CAM-04 + `PoseDetector.stream()` drops in-flight per BL-01 | Two upstream drop-oldest layers. Tick loop sees only fresh detections. |
| Camera reopen on stall | Backoff loop in `Pipeline` | `ObsCamera._attempt_reopen()` already does 200/500/1000 ms backoff per IO-CAM-04 / D-10 | If `ObsCamera` raises `CameraStallError` it has already exhausted recovery. Pipeline propagates per D-10. |
| Frame timestamps | Wall-clock at orchestrator | `Frame.timestamp_ns` set by capture thread per IO-CAM-04 | D-04 forbids; deterministic tests depend on this. |
| Track-ID lock + Kalman gap-fill | Anything in `Pipeline` | `SubjectTracker` already 6-state machine + `_KalmanWrapper` per PERC-01..07 | Pipeline only calls `consume()`. |
| Hysteresis + dwell | Anything in `Pipeline` | `MotionAnalyzer.consume()` per INTENT-01..02 | Pipeline only calls `consume()`. |
| Rule-of-thirds + stage-1 damping | Anything in `Pipeline` | `Framer.consume()` per INTENT-03..04 | Pipeline only calls `consume()`. |
| Stage-2 damping + clamp + deadband | Anything in `Pipeline` | `PanController.consume()` per CTRL-01..03 | Pipeline only calls `consume()`. |
| Δ + interval gate | Anything in `Pipeline` | `CommandDispatcher.decide()` per CTRL-04 | Pipeline only calls `decide()`. |
| Atomic single-attribute write protection | `asyncio.Lock` around `latest_frame` | Just write the attribute; CPython `STORE_ATTR` is atomic (single bytecode op under GIL) | Per [Python data model](https://docs.python.org/3/reference/datamodel.html) — attribute assignment on a Python object is one bytecode operation; with the GIL it is atomic for tear-free reads. Locks would serialize UI with tick. |

**Key insight:** Phase 6 owns ONLY (a) the 8-call await chain, (b) the lifecycle table, (c) the snapshot cache, (d) the `__main__` boot. Every algorithmic, recovery, drop-oldest, and timing concern is already implemented and tested in Phases 2–5. The risk is wiring + lifecycle, not domain.

## Runtime State Inventory

> **Skipped — Phase 6 is greenfield wiring.** No rename / migration / refactor; no stored data, OS-registered state, or build artifacts that embed the old name. The only "old" thing being replaced is the Phase-1 boot stub in `__main__.py`, which is a single 21-line file with no callers (the `if __name__ == "__main__":` guard runs only at process start).

## Common Pitfalls

### Pitfall 1: Tick task never completes on `quit()`

**What goes wrong:** `quit()` cancels the tick task, but the task is currently blocked inside `async for det in detector.stream(camera.frames())`. If the task does not check for cancellation between yields, it can deadlock.
**Why it happens:** `PoseDetector.detections()` (Phase 4) uses `asyncio.wait_for(self._out_queue.get(), timeout=_DETECTIONS_POLL_TIMEOUT_SEC)` (0.5 s); on cancel, the `wait_for` raises `CancelledError` cleanly. `ObsCamera.frames()` similarly uses `wait_for(... timeout=_FIRST_FRAME_TIMEOUT_SEC)` (3.0 s). So worst-case `quit()` waits up to ~0.5 s for the inner queue poll to time out and re-check the `while not stop_event.is_set()` condition.
**How to avoid:** `quit()` does `self._tick_task.cancel()` then `await self._tick_task` inside `contextlib.suppress(asyncio.CancelledError, Exception)` — same pattern as `arduino_motor.py:271-278` (W-05 precedent). DO NOT add a 200 ms timeout on the await — the `CancelledError` propagates to the inner `wait_for`, which raises within ~0.5 s naturally.
**Warning signs:** `quit()` exceeds 1 s in tests; pytest reports `Task exception was never retrieved` warning at GC time.

### Pitfall 2: E-stop deadlock on `_tx_lock`

**What goes wrong:** Tick task is mid-`await motor.send_motor_angle(cmd)` — has acquired `arduino_motor._tx_lock`. `e_stop()` calls `motor.send_emergency_stop()` which calls `_send_raw(b"E")` which tries to acquire `_tx_lock` — blocks. If the in-flight `send_motor_angle` then takes 100ms to complete the executor write, e-stop is delayed ~100ms — still under 200ms but worth verifying.
**Why it happens:** `_send_raw` does `async with self._tx_lock: await loop.run_in_executor(None, self._transport.write, ...)`. `pyserial.write()` to a real Uno completes in O(1ms) for the 1-byte `E\n` payload at 115200 baud (theoretical 0.087 ms/byte). The `M:<deg>` payload is up to 11 bytes (~1ms). So worst-case lock-wait + serial-write = ~2ms.
**How to avoid:** **Verify the budget empirically** in test `test_e_stop_completes_within_heartbeat_budget`. Use a `FakeSerialTransport` whose `write()` records `time.perf_counter()` on each call and assert the e-stop's write timestamp − start of `e_stop()` call ≤ 200 ms even with a slow `FakePoseEngine` (e.g. 500 ms `detect()`).
**Warning signs:** Test flakes on Windows under load; timing assertion in `test_e_stop_*` fails intermittently.

### Pitfall 3: `latest_frame` torn read on multi-attribute snapshot

**What goes wrong:** Future contributor adds a second slot (`latest_detection: Detection | None`) and assumes both update atomically. UI reads `latest_frame` then `latest_detection` and gets a mismatched pair.
**Why it happens:** Each *single* attribute write is atomic (one `STORE_ATTR` bytecode under GIL), but two writes are not. `Frame` and `Detection` are not paired in the cache by accident.
**How to avoid:** D-17 limits the slot to a single `Frame | None`. Anything else (Detection, MotionState, etc.) lives in `_PipelineCache` and is read via `snapshot()` (which builds a frozen DTO from a synchronous cache copy inside the loop thread — no race).
**Warning signs:** Phase 7 dashboard shows skeleton overlay drawn on a stale frame.

### Pitfall 4: `Task exception was never retrieved` after `quit()`

**What goes wrong:** `quit()` cancels `_tick_task`, but a stage exception fired during the same tick. The `CancelledError` masks the stage exception; the cached exception is GC'd later and surfaces as `Task exception was never retrieved`. Under our pytest `filterwarnings = ["error"]` policy this becomes a test failure (Phase 2 W-05 precedent).
**Why it happens:** Awaiting a task with both an exception and a cancel raises only the cancel; the exception attaches to the task object until GC.
**How to avoid:** Use a done-callback (`_on_tick_task_done`) that logs the exception explicitly via `task.exception()`. Same pattern as `arduino_motor.py:546-581` (`_on_task_done`). Inside the callback, ignore `CancelledError` (graceful), log+latch state STOPPED for any other exception, and re-route to `__main__` via the latched-error gate.
**Warning signs:** `pytest -W error` flake; intermittent test failures on shutdown.

### Pitfall 5: SIGINT raises `KeyboardInterrupt` mid-await

**What goes wrong:** On Windows, SIGINT is delivered as `KeyboardInterrupt` to the main thread. If it arrives while `asyncio.run(_amain(...))` is mid-await, the exception propagates **above** the event loop, leaving `Pipeline` in an unknown state (tick task still running, motor still open, camera still grabbing).
**Why it happens:** `signal.signal()` installs the handler at the C level; when the next bytecode boundary is hit, Python raises the exception in whatever Python frame is active.
**How to avoid:** The signal handler should not raise — it should set an `asyncio.Event` (via `loop.call_soon_threadsafe(event.set)` since the handler runs outside the loop thread on Windows). `_amain()` then `await shutdown_event.wait()` and on wake, calls `await pipeline.quit()` then exits cleanly.
**Warning signs:** Camera + motor handles leaked after Ctrl-C; subsequent runs fail to acquire the device.

### Pitfall 6: Recovery race — orchestrator calls `send_settings` while motor is RECOVERING

**What goes wrong:** Pipeline's `start()` calls `motor.send_settings(...)` then `motor.send_limits(...)`. If, simultaneously, the motor sees `READY:v2` (mid-session reset), `_recover()` task spawns and ALSO calls `send_settings`/`send_limits`. Both writes interleave on `_tx_lock`; firmware sees garbled settings.
**Why it happens:** `_recover` sets `_dispatch_paused = True` but does NOT block `send_settings` (those are not the dispatch path). Both code paths acquire `_tx_lock` and write.
**How to avoid:** **Pipeline only calls `send_settings`/`send_limits` once, inside `start()`, immediately after `motor.start()` returns (i.e. before any frames are flowing).** Per D-11, all subsequent re-issues are owned by `_recover()`. The orchestrator never calls these methods during the tick loop. This is structural, enforced by the absence of any reference to `send_settings` in `_tick_loop`.
**Warning signs:** Stress test with synthetic mid-session `READY:v2` injection produces interleaved `S:` lines in `FakeSerialTransport.captured_writes`.

### Pitfall 7: `home()` is asynchronous but motor is synchronous

**What goes wrong:** `home()` calls `motor.send_home()` (one TX) and immediately transitions HOMING → PAUSED. But the motor takes physical time to reach 0°. The Pipeline state lies — UI shows PAUSED while the motor is still moving.
**Why it happens:** Phase 6 has no per-tick feedback parsing; `Feedback` events in `motor.events()` AsyncIterator are ignored by Pipeline (only Phase 7 dashboard reads them).
**How to avoid:** Two options:
1. Document the contract: HOMING transitions to PAUSED *immediately* after the `H` command is **sent**, not when motion completes. Phase 7 dashboard shows "homing in progress" via `motor.state` or by parsing `Feedback.is_running`. **(Recommended — simplest.)**
2. Spawn a `_home_task` that awaits a `Feedback` with `is_running=False` near 0° before transitioning. Adds a second task surface and a timeout.

CONTEXT.md D-08 says "transition RUNNING/PAUSED → HOMING → PAUSED" — interpret as fire-and-forget per option 1. If field testing shows operator confusion, escalate to option 2 as a v2 enhancement.
**Warning signs:** Operator hits Home, immediately hits Start, motor jerks because the previous home motion has not completed.

### Pitfall 8: Calling `start()` twice

**What goes wrong:** Phase 7 hotkey handler is double-tapped or test calls `start()` twice. `motor.start()` raises `ArduinoError("start() called twice")`; pipeline state half-transitions; `__main__` exit code becomes non-deterministic.
**Why it happens:** `ArduinoMotor.start()` is single-shot (Phase 2 line 216-219); `ObsCamera.start()` is single-shot (Phase 3 line 481-484).
**How to avoid:** The lifecycle table key for `(RUNNING, "start")` returns RUNNING (idempotent per D-15) — but the implementation must short-circuit BEFORE calling `motor.start()` / `camera.start()`. Pseudocode: `if self._state is _PipelineState.RUNNING: return` at the top of `start()`. The transition table check happens AFTER the idempotence shortcut.
**Warning signs:** `ArduinoError` raised on second `start()` in test; pipeline state stuck at "starting".

## Code Examples

### Pattern 1: Tick Loop (D-01..D-04, concrete shape)

```python
# Source: distillation of Phase 5 _drive_pipeline (tests/test_intent_control_pipeline.py
# Plan 05-06 SUMMARY) generalized to the eight-stage Phase-6 chain.
async def _tick_loop(self) -> None:
    """Single-task sequential await chain. D-01..D-04 + D-13/D-14."""
    try:
        async for detection_list in self._detector.stream(self._camera.frames()):
            # D-04: now_ns is the camera's perf_counter_ns(), threaded everywhere.
            # detection_list is list[Detection]; each carries .timestamp_ns.
            # The Frame is reachable via the most recent yielded camera frame —
            # since detector.stream() drops stale frames internally (Phase 4 BL-01),
            # the orchestrator pulls Frame from a parallel ObsCamera.latest_frame_slot
            # OR (cleaner) from the last `Frame` consumed by stream(). The cleanest
            # path: PoseDetector.stream() yields (Frame, list[Detection]) tuples.
            # If Phase 4 doesn't already do that, propose a tiny extension at planning.
            #
            # NOTE TO PLANNER: verify whether detector.stream() yields raw Detection
            # lists or (Frame, Detections) tuples. If only Detections, latest_frame
            # is filled from camera.frames() inside a parallel async-for — which
            # would require two tasks (rejected by D-02). The clean fix is for
            # PoseDetector to yield the Frame alongside the detections, so the
            # orchestrator gets both in one yield.
            now_ns = detection_list[0].timestamp_ns if detection_list else 0
            # latest_frame slot — atomic single-attribute write, no lock (Pitfall 3).
            # If no detections, skip; the cache stays as last-good.
            if not detection_list:
                continue
            subject = await self._tracker.consume(detection_list, now_ns)
            motion  = await self._analyzer.consume(subject, now_ns)
            target  = await self._framer.consume(motion, now_ns)
            angle   = await self._controller.consume(target, now_ns)
            command = self._dispatcher.decide(angle, now_ns)
            self._update_cache(
                last_frame_ts_ns=now_ns,
                last_intent=motion.intent if motion else "indeterminate",
                last_target_x_normalized=target.target_x_normalized if target else None,
                last_pan_angle_deg=angle,
                last_emitted_angle_deg=self._dispatcher.last_emitted_angle_deg,
            )
            if command is not None and self._dispatch_enabled:
                await self._motor.send_motor_angle(command)
    except asyncio.CancelledError:
        # Graceful exit path — quit() cancelled us. Re-raise so the
        # contextlib.suppress in quit() sees CancelledError, not None.
        raise
    # D-13/D-14: any other exception bubbles out of the tick task; the
    # done-callback (_on_tick_task_done) latches state STOPPED + structured
    # log + re-raises to __main__. Mirrors arduino_motor.py:546-581.
```

**OPEN QUESTION FOR PLANNER:** §Code Examples Pattern 1 contains a critical clarification need — does `PoseDetector.stream(frames)` (Phase 4 BL-01) yield `list[Detection]`, `(Frame, list[Detection])`, or something else? Reading `pose_detector.py:455-473`, the existing `detections()` method yields `list[Detection]`. There is no `stream(frames)` method visible in the current `PoseDetector`. The orchestrator either:
1. Wraps `camera.frames()` itself and calls `detector.consume(frame)` then `detector.detections().__anext__()` per tick (loses BL-01 internal back-pressure), OR
2. Phase 4 needs a minor extension `async def stream(self, frames: AsyncIterator[Frame]) -> AsyncIterator[tuple[Frame, list[Detection]]]` that wraps `consume`+`detections` and yields the `(Frame, Detection)` pair so the orchestrator can update `latest_frame` from the same yield.

**Recommendation: option 2.** Phase 4 already references "BL-01" (consume-based design) and `pose_detector.py:283-289` says "Public surface mirrors Phase 2 `motor.events()` and Phase 3 `camera.frames()`: `async def detections() -> AsyncIterator[list[Detection]]`." A `stream(frames)` convenience helper that wires `consume → detections` and yields `(Frame, Detection)` pairs is a tiny addition to `PoseDetector` (~15 lines) — Phase 6 should land it as a Phase-4 helper, not duplicate the wiring in `pipeline.py`. CONTEXT.md D-03 says "Detector backpressure handled by `PoseDetector.stream(frames)` (Phase 4 BL-01)" — this implies the planner expects such a method to exist or be added. **Planner: confirm with one of the existing waves before Wave 1, or add a tiny Wave-0 task to extend PoseDetector with `stream()`.**

### Pattern 2: Lifecycle Transition Table (D-15)

```python
# Source: derived from CONTEXT.md D-05..D-09, D-15 + Phase 2 _MotorState precedent.
import enum
from typing import Literal

class _PipelineState(enum.Enum):
    STOPPED    = "stopped"     # initial / terminal-on-error
    RUNNING    = "running"     # tick loop active, dispatch enabled
    PAUSED     = "paused"      # tick loop active, dispatch disabled
    HOMING     = "homing"      # transient — motor moving to 0°
    E_STOPPED  = "e_stopped"   # motor halted; explicit start() to recover
    QUITTING   = "quitting"    # terminal — resources released

# Public Literal alias for the snapshot DTO (D-16).
PipelineState = Literal[
    "stopped", "running", "paused", "homing", "e_stopped", "quitting",
]

_Cmd = Literal["start", "pause", "resume", "home", "e_stop", "quit"]

# Transition table. Missing key = OrchestratorRejected.
# Idempotent self-transitions are listed explicitly so D-15's "idempotent on
# target state" contract is enforceable by the table alone.
_LIFECYCLE_TABLE: dict[tuple[_PipelineState, _Cmd], _PipelineState] = {
    # from STOPPED
    (_PipelineState.STOPPED,   "start"):   _PipelineState.RUNNING,
    (_PipelineState.STOPPED,   "quit"):    _PipelineState.QUITTING,
    # from RUNNING
    (_PipelineState.RUNNING,   "start"):   _PipelineState.RUNNING,    # idempotent
    (_PipelineState.RUNNING,   "pause"):   _PipelineState.PAUSED,
    (_PipelineState.RUNNING,   "home"):    _PipelineState.HOMING,
    (_PipelineState.RUNNING,   "e_stop"):  _PipelineState.E_STOPPED,
    (_PipelineState.RUNNING,   "quit"):    _PipelineState.QUITTING,
    # from PAUSED
    (_PipelineState.PAUSED,    "pause"):   _PipelineState.PAUSED,     # idempotent
    (_PipelineState.PAUSED,    "resume"):  _PipelineState.RUNNING,
    (_PipelineState.PAUSED,    "home"):    _PipelineState.HOMING,
    (_PipelineState.PAUSED,    "e_stop"):  _PipelineState.E_STOPPED,
    (_PipelineState.PAUSED,    "quit"):    _PipelineState.QUITTING,
    # from HOMING (transient — internal completion transitions, see Pitfall 7)
    (_PipelineState.HOMING,    "e_stop"):  _PipelineState.E_STOPPED,
    (_PipelineState.HOMING,    "quit"):    _PipelineState.QUITTING,
    # from E_STOPPED
    (_PipelineState.E_STOPPED, "start"):   _PipelineState.RUNNING,    # explicit re-arm
    (_PipelineState.E_STOPPED, "e_stop"):  _PipelineState.E_STOPPED,  # idempotent
    (_PipelineState.E_STOPPED, "quit"):    _PipelineState.QUITTING,
    # from QUITTING (terminal)
    (_PipelineState.QUITTING,  "quit"):    _PipelineState.QUITTING,   # idempotent
}

class OrchestratorRejected(Exception):
    """Invalid lifecycle transition or out-of-limits home() call."""

def _transition_or_raise(self, cmd: _Cmd) -> _PipelineState:
    key = (self._state, cmd)
    if key not in _LIFECYCLE_TABLE:
        raise OrchestratorRejected(f"cannot {cmd!r} from {self._state.value}")
    return _LIFECYCLE_TABLE[key]
```

Note `(STOPPED, "start") → RUNNING` is the bootstrap; `(STOPPED, "pause")` is absent → rejected. `(HOMING, "home")` is absent → idempotent home is rejected (deliberately — operator must wait for the in-flight home to finish). The table has 21 cells filled; 36 − 21 = 15 invalid combinations all raise. 100% branch coverage on `_transition_or_raise` is one parametrized test.

### Pattern 3: Snapshot Cache (D-16, D-17)

```python
# Source: D-16 + D-17 + CPython attribute atomicity guarantee.
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from pastor_tracker.core.types import Frame, MotionIntent

class PipelineSnapshot(BaseModel):
    """D-16: read-only snapshot for Phase 7 dashboard (frozen)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    state: PipelineState
    last_frame_ts_ns: int | None = Field(default=None, ge=0)
    last_intent: MotionIntent
    last_target_x_normalized: float | None = Field(default=None, ge=0.0, le=1.0)
    last_pan_angle_deg: float | None = None
    last_emitted_angle_deg: float | None = None
    motor_state: str  # _MotorState.value — opaque string keeps Phase 6 decoupled

@dataclass
class _PipelineCache:
    """Mutable single-writer cache. Tick task writes; snapshot() reads.
    Holds NO Frame (latest_frame is the dedicated D-17 slot).
    """
    last_frame_ts_ns: int | None = None
    last_intent: MotionIntent = "indeterminate"
    last_target_x_normalized: float | None = None
    last_pan_angle_deg: float | None = None
    last_emitted_angle_deg: float | None = None

# Inside Pipeline:
def snapshot(self) -> PipelineSnapshot:
    """Build a frozen snapshot from the mutable cache. Cheap (no I/O)."""
    return PipelineSnapshot(
        state=self._state.value,
        last_frame_ts_ns=self._cache.last_frame_ts_ns,
        last_intent=self._cache.last_intent,
        last_target_x_normalized=self._cache.last_target_x_normalized,
        last_pan_angle_deg=self._cache.last_pan_angle_deg,
        last_emitted_angle_deg=self._cache.last_emitted_angle_deg,
        motor_state=self._motor.state.value,
    )

@property
def latest_frame(self) -> Frame | None:
    """D-17: single-attribute read; no lock needed."""
    return self._latest_frame
```

### Pattern 4: E-Stop Inline Send (D-07)

```python
# Source: D-07 + arduino_motor.py:923-928 (send_emergency_stop bypasses pause + latched).
async def e_stop(self) -> None:
    """D-07: inline send + transition. Must complete < 200 ms (heartbeat budget)."""
    # Idempotence: if already E_STOPPED, just re-send E for safety; do not raise.
    if self._state is _PipelineState.E_STOPPED:
        await self._motor.send_emergency_stop()
        return
    next_state = self._transition_or_raise("e_stop")
    # Inline send FIRST (so even if the transition log races, the wire is silenced).
    await self._motor.send_emergency_stop()
    self._dispatch_enabled = False
    old_state = self._state
    self._state = next_state
    self._logger.warning(
        "pipeline_state_change",
        old=old_state.value,
        new=next_state.value,
        reason="e_stop",
    )
```

### Pattern 5: `__main__` Refactor

```python
# Source: Pattern 5 docs (cross-platform SIGINT) + Phase 1 Config + logging_config.
import argparse
import asyncio
import signal
import sys
from pathlib import Path

import structlog

from pastor_tracker.config import Config
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import PanController
from pastor_tracker.intent.framer import Framer
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.arduino_transport import PySerialTransport, discover_arduino_port
from pastor_tracker.io.obs_camera import ObsCamera, OpenCvVideoSource
from pastor_tracker.logging_config import configure_logging
from pastor_tracker.perception.pose_detector import PoseDetector, UltralyticsPoseEngine
from pastor_tracker.perception.subject_tracker import SubjectTracker
from pastor_tracker.pipeline import Pipeline, OrchestratorRejected
from pygrabber.dshow_graph import FilterGraph

EXIT_OK: int = 0
EXIT_INVALID_CONFIG: int = 64
EXIT_HARDWARE_FAILED: int = 65
EXIT_CRASHED: int = 70

def main() -> int:
    parser = argparse.ArgumentParser(prog="pastor_tracker")
    parser.add_argument(
        "--config-json", type=Path, default=None,
        help="Override config.json path (default: ./config.json)",
    )
    args = parser.parse_args()
    configure_logging()
    log = structlog.get_logger(module="__main__")
    try:
        # Config() loads PTS_* env vars + .env + config.json; --config-json
        # overrides via init kwargs (highest precedence).
        if args.config_json is not None:
            # Pass init kwarg with json_file override; pydantic-settings re-resolves.
            config = Config(_json_file=args.config_json)  # noqa: SLF001 (pydantic API)
        else:
            config = Config()
    except Exception as exc:  # noqa: BLE001 — typed pydantic ValidationError translator
        log.error("config_invalid", reason=str(exc))
        return EXIT_INVALID_CONFIG
    try:
        return asyncio.run(_amain(config))
    except KeyboardInterrupt:
        # Defense-in-depth: if signal handler missed, surface clean exit.
        log.warning("pipeline_exit", reason="keyboard_interrupt")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 — typed pipeline error translator
        log.error("pipeline_exit", reason=str(exc), exc_type=type(exc).__name__)
        return EXIT_CRASHED

async def _amain(config: Config) -> int:
    log = structlog.get_logger(module="__main__")
    # Construct stages (Pipeline does not own discovery; __main__ does).
    arduino_port = discover_arduino_port(config.arduino_port)
    transport = PySerialTransport(arduino_port, config.arduino_baud)
    motor = ArduinoMotor(transport, config)
    camera = ObsCamera(
        config,
        video_source_factory=lambda idx, w, h, fps: OpenCvVideoSource(idx, w, h, fps),
        filter_graph_factory=lambda: FilterGraph(),
    )
    pose_engine = UltralyticsPoseEngine(config)
    detector = PoseDetector(config=config, engine=pose_engine)
    tracker = SubjectTracker(config)
    analyzer = MotionAnalyzer(config)
    framer = Framer(config)
    controller = PanController(config)
    dispatcher = CommandDispatcher(config)
    pipeline = Pipeline(
        config=config,
        camera=camera, motor=motor,
        detector=detector, tracker=tracker,
        analyzer=analyzer, framer=framer,
        controller=controller, dispatcher=dispatcher,
    )
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(signum: int = 0, frame: object = None) -> None:
        del signum, frame  # signal.signal handlers receive (signum, frame); ignore.
        # Cross-thread safe: signal.signal handlers run on main thread but
        # outside the asyncio loop's run cycle. call_soon_threadsafe is the
        # documented bridge.
        loop.call_soon_threadsafe(shutdown_event.set)

    if sys.platform == "win32":
        # ProactorEventLoop does not support add_signal_handler; use signal.signal.
        signal.signal(signal.SIGINT, _on_signal)
        # SIGTERM cannot be installed via signal.signal on Windows (raises);
        # rely on KeyboardInterrupt fallback in main().
    else:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    try:
        await pipeline.start()
    except Exception as exc:  # noqa: BLE001 — typed startup error translator
        log.error("pipeline_start_failed", reason=str(exc), exc_type=type(exc).__name__)
        return EXIT_HARDWARE_FAILED
    try:
        await shutdown_event.wait()
    finally:
        # quit() is idempotent (D-09); safe to call even if pipeline already stopped.
        await pipeline.quit()
    log.info("pipeline_exit", reason="clean_shutdown")
    return EXIT_OK

if __name__ == "__main__":
    raise SystemExit(main())
```

### Pattern 6: Test Fixture Composition

```python
# Source: Phase 5 _make_pipeline (Plan 05-06) + Phase 2/3/4 fakes.
# Lives in tests/test_pipeline.py (fixtures may move to tests/fixtures/pipeline_helpers.py
# if reused across multiple test files — defer until > 1 file imports it).
import asyncio
from collections.abc import AsyncIterator

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, Frame
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from pastor_tracker.io.obs_camera import ObsCamera
from pastor_tracker.perception.pose_detector import PoseDetector
from pastor_tracker.perception.subject_tracker import SubjectTracker
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
from pastor_tracker.intent.framer import Framer
from pastor_tracker.control.pan_controller import PanController
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.pipeline import Pipeline
from tests.fixtures.arduino_traces import ARDUINO_TRACE_BOOT_ONLY
from tests.fixtures.camera_traces import FakeVideoSource, _ScriptedFrame, make_solid_bgr
from tests.fixtures.pose_traces import FakePoseEngine, make_detection

async def _pipeline_with_fakes(
    valid_config_dict: dict[str, object],
    *,
    detection_script: list[list[Detection]] | None = None,
    frame_script: list[_ScriptedFrame] | None = None,
) -> tuple[Pipeline, FakeSerialTransport, FakeVideoSource, FakePoseEngine]:
    """Compose a Pipeline backed by all four fakes. Caller must `await pipeline.quit()`."""
    config = Config(**valid_config_dict)
    fake_serial = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake_serial.feed_rx(line)
    motor = ArduinoMotor(fake_serial, config)
    fake_video = FakeVideoSource(
        frame_script or _default_frame_script(config),
        width=config.capture_width,
        height=config.capture_height,
    )
    camera = ObsCamera(
        config,
        video_source_factory=lambda idx, w, h, fps: fake_video,
        filter_graph_factory=lambda: _FakeFilterGraph(["OBS Virtual Camera"]),
    )
    fake_pose = FakePoseEngine(detection_script or [])
    detector = PoseDetector(config=config, engine=fake_pose)
    pipeline = Pipeline(
        config=config,
        camera=camera, motor=motor, detector=detector,
        tracker=SubjectTracker(config),
        analyzer=MotionAnalyzer(config),
        framer=Framer(config),
        controller=PanController(config),
        dispatcher=CommandDispatcher(config),
    )
    return pipeline, fake_serial, fake_video, fake_pose

class _FakeFilterGraph:
    def __init__(self, devices: list[str]) -> None:
        self._devices = devices
    def get_input_devices(self) -> list[str]:
        return self._devices

def _default_frame_script(config: Config) -> list[_ScriptedFrame]:
    bgr = make_solid_bgr(config.capture_width, config.capture_height, (0, 0, 0))
    # 90 frames @ 30fps = 3 seconds of test runtime
    return [_ScriptedFrame(bgr=bgr, ok=True, delay_sec=1.0/config.capture_fps)
            for _ in range(90)]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-stage `asyncio.Queue` chain | Single sequential await chain | Phase 5 D-01 + Plan 05-06 SUMMARY | Simpler reasoning; sub-ms stages don't need backpressure between them. |
| Wall-clock at orchestrator | `frame.timestamp_ns` threaded everywhere | Phase 5 Pitfall 5 | Deterministic tests, no time-source mismatch. |
| Magic number e-stop budget | `arduino_heartbeat_interval_ms` from Config | Phase 1 / Phase 2 | All thresholds in Config. |
| `print()` boot status | `structlog` JSON via `configure_logging()` | Phase 1 SCAF-04 | Already shipped; Phase 6 just inherits. |
| `asyncio.add_signal_handler` Windows | Platform-conditional `signal.signal` | Python 3.8+ ProactorEventLoop default | See [cpython#137863](https://github.com/python/cpython/issues/137863). |

**Deprecated/outdated:**
- Threaded camera + motor with `concurrent.futures` synchronous orchestration: Phase 1 superseded with asyncio. Not relevant.
- ROS-style topic broadcast for `latest_frame`: rejected by D-17 (slot, not queue, not pub-sub).

## Assumptions Log

> Every assumed claim in this research, with risk if wrong.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `PoseDetector` will gain a `stream(frames)` method (or equivalent that yields `(Frame, list[Detection])`) before Phase 6 consumes it. | §Code Examples / Pattern 1 + D-03 | If the planner cannot extend Phase 4, Pipeline must wire `consume`+`detections` itself OR maintain `latest_frame` from a parallel `camera.frames()` iterator (which violates D-02 single-task discipline). **Recommend planner adds a Wave-0 task to confirm or extend.** |
| A2 | CPython single-attribute write to a `Pipeline` instance attribute (`self._latest_frame = frame`) is atomic under the GIL — no torn read for a single concurrent reader. | §Pattern 3 + Pitfall 3 | `[CITED Python Language Reference data model, GIL semantics]`. Risk near-zero on CPython 3.12; would break on a hypothetical free-threaded build (PEP 703) where the GIL is removed. v1 ships against CPython, not the free-threaded build. |
| A3 | `pyserial.write(b"E\n")` to a real Uno completes in ≤ 5 ms at 115200 baud (theoretical 0.087 ms/byte for 2 bytes + USB stack overhead). | §Pitfall 2 | If write blocks > 200 ms (e.g. USB stack stall on Windows), e-stop budget breached. **Mitigation:** test `test_e_stop_completes_within_heartbeat_budget` uses `FakeSerialTransport` (zero-latency write) — Phase 8 QA-04 on-stage smoke test verifies the real-hardware budget. |
| A4 | The motor's `_recover()` task and the orchestrator's `start()` will not race on `send_settings`/`send_limits`, because Pipeline only calls these methods once at `start()` time, BEFORE the tick loop spawns. | §Pitfall 6 | Structurally enforced (no other call site in `pipeline.py`). Risk only if a future contributor adds a second call site. **Mitigation:** ruff custom check OR a code-review note in `pipeline.py` docstring. |
| A5 | `home()` "fire-and-forget" semantics (state transitions to PAUSED immediately when `H` is sent, not when motor reaches 0°) are acceptable to the user per Pitfall 7 option 1. | §Pitfall 7 + D-08 | If field testing shows operator confusion, escalate to option 2 (HOMING task that awaits `Feedback.is_running=False`). v2 work, not v1 blocker. |
| A6 | `Config(_json_file=args.config_json)` is the correct pydantic-settings 2.x API for overriding `json_file` via init kwargs. | §Code Examples / Pattern 5 (`__main__`) | If wrong, the planner must use the documented `SettingsConfigDict` mutation pattern OR re-construct via env var. **Mitigation:** verify against `pydantic-settings` 2.x docs at planning time. |
| A7 | `ArduinoMotor` does NOT need a `pre-tick warmup` — its `start()` blocks until handshake completes per Phase 2 line 214-250. | §Module Layout — `start()` ordering | Verified against `arduino_motor.py:214-250`. Low risk. |
| A8 | The `tests/fixtures/test_trajectories.py` Phase-5 trajectory helpers (step / ramp / dwell_then_walk / borderline_chatter) produce `TrackedSubject` records. Phase 6 needs `Detection` records (input to `tracker.consume`). The conversion `TrackedSubject → Detection` is straightforward (use `make_detection(cx=t.subject_center_x_normalized, cy=t.subject_center_y_normalized, track_id=t.track_id, timestamp_ns=t.timestamp_ns)` from `pose_traces.py`). | §Test Strategy | Low risk; small adapter function. |

## Open Questions

1. **Does `PoseDetector.stream(frames)` exist?** (A1 above)
   - What we know: CONTEXT.md D-03 references it; current code has only `consume(frame)` + `detections() -> AsyncIterator[list[Detection]]`.
   - What's unclear: whether D-03 implies "Phase 4 already shipped this" (false per code read) or "Phase 6 will use the existing surface, naming it `stream` as a documentation shorthand."
   - **Recommendation:** Add a tiny Phase-6 Wave-0 task: extend `PoseDetector` with `async def stream(self, frames: AsyncIterator[Frame]) -> AsyncIterator[tuple[Frame, list[Detection]]]` that wires `consume` + `detections` and yields tuples. ~15 lines + 1 test.

2. **Should `home()` be fire-and-forget or wait-for-completion?** (A5 / Pitfall 7)
   - What we know: D-08 says "RUNNING/PAUSED → HOMING → PAUSED" — ambiguous on whether HOMING is transient (fire-and-forget) or persistent (wait).
   - **Recommendation:** Document fire-and-forget in the `home()` docstring; flag option 2 as v2 if field testing demands.

3. **Does `--config-json` go through pydantic-settings init kwargs cleanly?** (A6)
   - **Recommendation:** Planner verifies the API at the start of Wave 1 (pipeline assembly task) and falls back to setting `PTS_CONFIG_JSON` env var if needed.

4. **Is there a need for a `_reload()` helper for D-18?**
   - What we know: D-18 says Phase 7 "calls `pipeline.quit()` then re-instantiates `Pipeline(new_config, ...)` and calls `start()` again."
   - What's unclear: Does Phase 6 ship a `Pipeline.reload(new_config)` convenience method, or does Phase 7 own the orchestration?
   - **Recommendation:** Phase 7 owns it. D-18 explicitly says "re-instantiates" — `Pipeline.__init__` is the public reload path. No new method.

5. **`PoseEngineUnavailableError` at `start()` time — propagate or downgrade?**
   - What we know: `PoseDetector.start()` raises `PerceptionError` (line 314-340).
   - **Recommendation:** Per D-13, fail-fast — propagate to `__main__`, exit `EXIT_HARDWARE_FAILED`. No downgrade.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | runtime | ✓ (verified by Phase 1 build) | 3.12.x | — |
| `asyncio` (stdlib) | tick task, lifecycle | ✓ | stdlib 3.12 | — |
| `signal` (stdlib) | `__main__` SIGINT | ✓ | stdlib 3.12 | — |
| `argparse` (stdlib) | `__main__` `--config-json` | ✓ | stdlib 3.12 | — |
| `pydantic` v2 | `PipelineSnapshot` | ✓ (Phase 1) | already pinned | — |
| `structlog` | logging | ✓ (Phase 1) | 25.5.0 | — |
| `pyserial` | hardware path only (excluded from CI tests via FakeSerialTransport) | ✓ (Phase 2) | already pinned | FakeSerialTransport |
| `opencv-python` + `pygrabber` | hardware path only | ✓ (Phase 3) | already pinned | FakeVideoSource |
| `ultralytics` + `torch` | hardware path only | ✓ (Phase 4) | already pinned | FakePoseEngine |
| Real Arduino + Real OBS VCam | Phase 8 QA-04 only | ✗ in CI | — | Tests use 4 fakes; on-stage smoke deferred. |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** Real hardware — fully covered by the four in-tree fakes.

## Validation Architecture

> `nyquist_validation = true` per `.planning/config.json`.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` 8.x + `hypothesis` (optional for property tests if any are warranted; Phase 6 likely has zero property tests since it is wiring, not math) |
| Config file | `pastor_tracker/pyproject.toml` (`[tool.pytest.ini_options]` already configured per Phases 1-5) |
| Quick run command | `uv run pytest tests/test_pipeline.py -x -v` |
| Full suite command | `uv run pytest` |
| Coverage command | `uv run pytest --cov=src/pastor_tracker tests/test_pipeline.py --cov-report=term-missing` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PIPE-01 | Pipeline constructs with all 8 stages from one Config; tick loop progresses on scripted frames; dispatcher emits expected count | integration | `pytest tests/test_pipeline.py::test_tick_loop_drives_all_stages -x` | ❌ Wave 0 |
| PIPE-01 | `latest_frame` slot updates on each tick (D-17) | integration | `pytest tests/test_pipeline.py::test_latest_frame_slot_updates -x` | ❌ Wave 0 |
| PIPE-01 | `snapshot()` returns frozen DTO with all 7 fields populated (D-16) | integration | `pytest tests/test_pipeline.py::test_snapshot_dto_complete -x` | ❌ Wave 0 |
| PIPE-02 | Tick body accesses ONLY `consume()` / `decide()` / `Frame.timestamp_ns` (no private attrs) | static | `grep -n "_" pastor_tracker/src/pastor_tracker/pipeline.py` audit + structural review | manual / code-review |
| PIPE-03 | All 6 lifecycle methods (`start/pause/resume/home/e_stop/quit`) work on valid source states | integration | `pytest tests/test_pipeline.py::test_lifecycle_valid_transitions -x` | ❌ Wave 0 |
| PIPE-03 | Invalid transitions raise `OrchestratorRejected` | integration | `pytest tests/test_pipeline.py::test_lifecycle_invalid_transitions_rejected -x` | ❌ Wave 0 |
| PIPE-03 | `e_stop()` completes within 200 ms wall-time even with slow detector | integration (timing) | `pytest tests/test_pipeline.py::test_e_stop_completes_within_heartbeat_budget -x` | ❌ Wave 0 |
| PIPE-03 | `home()` rejected when 0° outside `[pan_min, pan_max]` (D-08) | integration | `pytest tests/test_pipeline.py::test_home_rejected_when_zero_out_of_limits -x` | ❌ Wave 0 |
| PIPE-03 | `quit()` is idempotent (D-09) | integration | `pytest tests/test_pipeline.py::test_quit_idempotent -x` | ❌ Wave 0 |
| D-06 | `pause()` keeps tick running but suppresses motor sends | integration | `pytest tests/test_pipeline.py::test_pause_suppresses_motor_send_but_tick_continues -x` | ❌ Wave 0 |
| D-10 | `CameraOpenError` propagates to `__main__`; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_camera_open_error_propagates -x` | ❌ Wave 0 |
| D-11 | `WatchdogResetError` is recovered in-place by motor; tick loop continues | integration | `pytest tests/test_pipeline.py::test_watchdog_reset_recovered_pipeline_continues -x` | ❌ Wave 0 |
| D-12 | `LinkLostError` propagates; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_arduino_link_lost_propagates -x` | ❌ Wave 0 |
| D-13 | Detector exception propagates; pipeline → STOPPED | integration | `pytest tests/test_pipeline.py::test_perception_failure_propagates -x` | ❌ Wave 0 |
| D-14 | Unknown exception in tick loop logged + propagated | integration | `pytest tests/test_pipeline.py::test_tick_loop_unknown_exception_propagates -x` | ❌ Wave 0 |
| __main__ | SIGINT triggers clean `quit()` and exit code 0 | integration | `pytest tests/test_pipeline.py::test_main_sigint_clean_shutdown -x` | ❌ Wave 0 |
| __main__ | Invalid Config exits with `EXIT_INVALID_CONFIG` | integration | `pytest tests/test_pipeline.py::test_main_invalid_config_exit_code -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_pipeline.py -x` (~5 seconds expected; Phase 5 composition test runs 7 tests in 1.94s)
- **Per wave merge:** `uv run pytest` (full suite — Phase 5 ship-gate baseline 370 passed in ~30 sec)
- **Phase gate:** Full suite green AND `uv run mypy --strict src` AND `uv run ruff check src tests` before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/test_pipeline.py` — covers PIPE-01..03 and D-05..D-14 (~17 tests above)
- [ ] (Optional) `tests/fixtures/pipeline_helpers.py` — `_pipeline_with_fakes(...)` helper if reused by > 1 test file
- [ ] (Pre-Wave 1) Confirm or add `PoseDetector.stream(frames)` per Open Question 1
- [ ] No new framework / fixture modules; all four upstream fakes already exist

## Security Domain

> `security_enforcement = true`, `security_asvs_level = 1` per `.planning/config.json`.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Headless desktop app on operator machine; no remote auth surface in v1. |
| V3 Session Management | no | No sessions. |
| V4 Access Control | no | No multi-user. |
| V5 Input Validation | yes | All Config fields validated by Pydantic v2 (Phase 1 CFG-02). All DTO field invariants validated by Pydantic + dataclass `__post_init__` (Phases 1-5). The new `PipelineSnapshot` DTO follows the same pattern (`frozen=True, extra="forbid"`, range-validated fields). The `--config-json` path argument is read by pydantic-settings, which validates the JSON. |
| V6 Cryptography | no | No cryptographic operations. The `yolo_model_path` SHA256 verification is documented in Phase 4 README as operator responsibility (Phase 4 Config description); Phase 6 inherits, no new control. |
| V14 Configuration | yes | `--config-json` accepts a `Path`. Operator-supplied; no untrusted source in v1 (single-user desktop app). Pydantic + JSON parser are battle-tested. |

### Known Threat Patterns for asyncio orchestrator + USB serial + DirectShow

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed `--config-json` file (path traversal, malformed JSON) | Tampering / DoS | Pydantic-settings raises `ValidationError`; `__main__` catches and exits `EXIT_INVALID_CONFIG`. No file system mutation. |
| Pipeline crashes leave camera/motor handles open (resource leak DoS) | DoS | `quit()` idempotent close ordering (D-09); `__main__` `finally: await pipeline.quit()` guarantees handle release on every exit path. |
| Adversarial signal storm (rapid SIGINT) | DoS | `shutdown_event.set()` is idempotent; `pipeline.quit()` is idempotent (D-09). Worst case: `quit()` runs once, subsequent signals are no-ops. |
| Stage exception leaks sensitive frame data into logs | Information Disclosure | Phase 6 logs structured event names (`pipeline_state_change`, `pipeline_camera_failed`); no `Frame.image` bytes serialized. `exc_info=True` includes traceback strings only. |
| Two concurrent `start()` calls race motor handshake | Tampering | Lifecycle table makes `(RUNNING, "start") → RUNNING` (idempotent short-circuit before `motor.start()` re-call) per Pitfall 8. |

**No additional security controls beyond Phases 1-5 inheritance are required for Phase 6.** The threat surface is fully internal (single-process orchestrator on operator desktop).

## Sources

### Primary (HIGH confidence)
- In-repo code (verified by reading current files):
  - `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (Phase 2)
  - `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (Phase 3)
  - `pastor_tracker/src/pastor_tracker/perception/pose_detector.py` (Phase 4)
  - `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (Phase 4)
  - `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py` (Phase 5)
  - `pastor_tracker/src/pastor_tracker/intent/framer.py` (Phase 5)
  - `pastor_tracker/src/pastor_tracker/control/pan_controller.py` (Phase 5)
  - `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py` (Phase 5)
  - `pastor_tracker/src/pastor_tracker/core/types.py` (Phase 1)
  - `pastor_tracker/src/pastor_tracker/config.py` (Phase 1)
  - `pastor_tracker/src/pastor_tracker/logging_config.py` (Phase 1)
  - `pastor_tracker/tests/fixtures/{arduino_traces.py, camera_traces.py, pose_traces.py, trajectories.py}` (Phases 2-5)
  - `pastor_tracker/tests/test_intent_control_pipeline.py` (Phase 5 Plan 05-06)
- `.planning/phases/06-pipeline-orchestrator/06-CONTEXT.md` — D-01..D-18
- `.planning/REQUIREMENTS.md` — PIPE-01..03, IO-ARD-*, IO-CAM-*, PERC-*, INTENT-*, CTRL-*
- `CLAUDE.md` — engineering culture rules
- Python 3.14 documentation, [Event Loop](https://docs.python.org/3/library/asyncio-eventloop.html)
- Python 3.14 documentation, [Platform Support](https://docs.python.org/3/library/asyncio-platforms.html)

### Secondary (MEDIUM confidence)
- [cpython#137863 — `add_signal_handler` support for all platforms](https://github.com/python/cpython/issues/137863)
- [autobahn-python#471 — asyncio add_signal_handler not implemented in windows](https://github.com/crossbario/autobahn-python/issues/471)
- [bpo-39765 — asyncio loop.add_signal_handler() may not behave as expected](https://bugs.python.org/issue39765)

### Tertiary (LOW confidence)
- None for Phase 6. All claims either CITED to in-repo code, official Python docs, or canonical project documents.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in repo from Phases 1-5; no new deps.
- Architecture (lifecycle, tick loop, snapshot): HIGH — every pattern verified against existing Phase 2/3/4/5 implementations.
- Pitfalls: HIGH on items 1-6, 8 (each has a concrete in-repo precedent or documented Python issue); MEDIUM on item 7 (semantic interpretation of D-08 ambiguity — recommendation included).
- Test strategy: HIGH — composes existing fakes, follows Phase 5 Plan 05-06 precedent.
- Validation Architecture: HIGH — Phase 5 ship-gate baseline (370 passed) demonstrates the pattern.

**Key open question for the planner before Wave 1:** A1 / Open Question 1 — confirm `PoseDetector.stream(frames)` API or add a Wave-0 task to extend Phase 4 with that helper.

**Research date:** 2026-05-08
**Valid until:** 2026-06-07 (30 days; stack is stable, no fast-moving deps in Phase 6 scope)
