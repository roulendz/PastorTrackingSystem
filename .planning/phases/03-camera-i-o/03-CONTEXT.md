# Phase 3: Camera I/O - Context

**Gathered:** 2026-05-05
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

An async OBS Virtual Camera frame source that fails loudly when OBS is not running, hits the 1080p30 target with auto-fallback to 720p, drops stale frames, and timestamps every frame with `perf_counter_ns()`.

**In scope:**
- `pastor_tracker/io/obs_camera.py` — async frame source built on `cv2.VideoCapture(idx, CAP_DSHOW)` + `pygrabber.dshow_graph.FilterGraph` device enumeration
- DirectShow device enumeration at boot (one-shot); exact-match resolution of `config.obs_camera_name` against the device list
- Hard-fail with the available device list when OBS Virtual Camera is not found (typed `OBSCameraNotFoundError`)
- Target capture at `config.capture_width × config.capture_height @ config.capture_fps` (default 1920×1080@30); auto-fallback to 1280×720 once during a 2s warmup window if frame-time budget is breached
- `time.perf_counter_ns()` timestamp stamped on every `Frame` at grab time; `Frame` DTO assembled in the capture thread (off the asyncio hot path) per `core/types.py:Frame` contract (BGR uint8, HxWx3, width/height match)
- Stale-frame drop policy — frames older than 100 ms (`now_ns - frame.timestamp_ns`) discarded by the async consumer at retrieval
- Stall detection — capture thread compares per-grab `perf_counter_ns()` deltas; > 200 ms = stall; up to 3 reopen attempts with linear backoff (200/500/1000 ms) before raising typed `CameraStallError`
- Lifecycle — `async def start()` returns once first frame arrives; `async def stop()` cancels heartbeat tasks (if any) and joins the capture thread with timeout
- Test strategy — `VideoSource` `Protocol` + `FakeVideoSource` (zero-deps) producing canned ndarray sequences; covers discovery, exact-match resolution, missing-device error, fallback trigger, stall + reopen, stale-frame drop; no hardware in CI

**Out of scope (later phases):**
- Perception / YOLO11-pose / BoT-SORT / Kalman (Phase 4)
- Motion intent + framer + pan controller + dispatcher (Phase 5)
- Pipeline orchestrator (Phase 6) — `obs_camera.py` exposes the producer surface only
- DearPyGui dashboard (Phase 7)
- On-stage smoke test against a real OBS install (Phase 8 / QA-04)
- Continuous fallback evaluation outside the warmup window
- Hot-plug device-change detection / re-enumeration

**Requirements covered:** IO-CAM-01..04.

</domain>

<decisions>
## Implementation Decisions

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config
- Backend = OpenCV `CAP_DSHOW`; device enumeration via `pygrabber.dshow_graph.FilterGraph` (IO-CAM-01)
- Target 1920×1080@30, fallback 1280×720 (IO-CAM-03)
- Frames stamped with `time.perf_counter_ns()` at grab time (IO-CAM-04)
- Stale frames > 100 ms dropped; stall > 200 ms = ERROR + restart capture (IO-CAM-04)
- Hard-fail with device list when OBS VCam not found (IO-CAM-02)
- Threading for blocking I/O only; asyncio for lifecycle/consumer (PROJECT.md)
- Tiger-style fail-fast: typed exceptions, no silent fallback (CLAUDE.md rule 1)
- `structlog` JSON logging only; no `print()`; bare `except` forbidden
- All public functions annotated, no `Any`, no magic numbers (`config` is authoritative for every tunable)
- ≤ 2-level conditional nesting; guard clauses + early returns
- Conventional Commits, one logical change per commit
- `Frame` DTO contract — BGR uint8, HxWx3, width/height agreement, `timestamp_ns >= 0` (per `core/types.py`)
- Config fields consumed (no new fields needed for v1): `obs_camera_name`, `capture_width`, `capture_height`, `capture_fps`

### Async/Threading Architecture (Area 1, all accepted)
- Dedicated capture thread runs the blocking `cv2.VideoCapture.read()` loop and pushes built `Frame` DTOs into a bounded `asyncio.Queue` via `loop.call_soon_threadsafe(queue.put_nowait, ...)` — same pattern as Phase 2 RX thread
- Producer/consumer topology = SPSC (one capture thread, one async consumer / next pipeline stage)
- `Frame` is constructed in the capture thread (timestamp + width/height/dtype validation) — keeps ndarray work off the asyncio hot path
- Lifecycle — `async def start()` opens the capture, spawns the thread, awaits first frame (or fails with timeout); `async def stop()` signals shutdown event, joins thread with bounded timeout, releases `VideoCapture`

### Device Discovery & Failure (Area 2, all accepted)
- Boot-only one-shot enumeration via `pygrabber.dshow_graph.FilterGraph().get_input_devices()`; no background re-poll
- Exact case-sensitive equality match against `config.obs_camera_name` (default `"OBS Virtual Camera"`)
- No match → raise typed `OBSCameraNotFoundError` carrying the full enumerated device list; structlog ERROR with `expected=...`, `available=[...]`; halt boot
- Multiple matches with the same name → pick first hit, structlog INFO listing all matched indexes (mirror Phase 2 multi-match rule)

### Resolution Fallback (Area 3, all accepted)
- "Frame-time budget breached" = rolling **p95** of inter-grab `perf_counter_ns()` deltas over last 30 frames exceeds 1.2 × (1 / `capture_fps`) seconds; breach must persist ≥ 1 s
- Fallback evaluation is **one-shot**, gated to a 2 s warmup window after first frame; never re-promote
- Apply fallback by releasing the current `VideoCapture` and reopening at 1280×720 against the same matched DirectShow index (avoids cross-vendor `cap.set` quirks); structlog WARN with from/to resolution
- Already-720p case + budget still breached → structlog ERROR (surface to UI in Phase 7), continue capturing at 720p; halting kills the only camera path

### Stall Recovery (Area 4, all accepted)
- Stall detection lives in the capture thread — per-grab `perf_counter_ns()` delta compared against 200 ms ceiling (`stall_threshold_ms`)
- Restart policy — up to 3 reopen attempts with linear backoff `200 ms → 500 ms → 1000 ms`; each attempt re-issues `VideoCapture(idx, CAP_DSHOW)` + property set + first-frame wait
- After 3 failed reopens → raise typed `CameraStallError` (carries restart attempt history); structlog ERROR; capture thread exits and async consumer surfaces the error
- Stale-frame drop (> 100 ms) handled by the async consumer at retrieval (`now_ns - frame.timestamp_ns > 100_000_000`); capture thread never decides — it just pushes (drop-oldest semantics on bounded queue with WARN log on drop, mirror Phase 2)

### Test Strategy (Area 4, all accepted)
- Public seam = `VideoSource` `Protocol` (or `Callable`-style factory) so production code wires `cv2.VideoCapture` and tests wire `FakeVideoSource`
- `FakeVideoSource` is in-tree zero-deps — produces canned BGR ndarray sequences on demand, supports configurable per-frame delay, scripted stall/error injection, scripted index-mapping (for discovery tests)
- Test suite covers: device enumeration + exact-match, missing-device error path with device list, fallback trigger inside warmup, fallback inhibited outside warmup, stall + 3-attempt reopen + giveup, stale-frame drop on consumer, queue full → drop-oldest WARN, lifecycle start/stop joining
- No hardware-in-CI; real OBS device test deferred to Phase 8 QA-04 stage smoke
- Coverage target — ≥ 90 % line coverage on `obs_camera.py`; 100 % on the discovery/match branch (safety-critical for hard-fail behavior)

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal type names (`Frame` already exists; new internal events/DTOs as needed)
- Error class hierarchy under a single `CameraError` root (subclasses: `OBSCameraNotFoundError`, `CameraStallError`, `CameraOpenError`)
- Logging key names (kept consistent with structlog conventions established in Phases 1–2)
- File granularity inside `pastor_tracker/io/` — single `obs_camera.py` is fine; split is permitted if line count balloons
- Whether the warmup-window p95 detector is a small helper class or a stateful function

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/config.py` — frozen `Config` already exposes `obs_camera_name`, `capture_width`, `capture_height`, `capture_fps`, `camera_horizontal_fov_deg` with range validation; Phase 3 consumes these directly, no new fields required
- `pastor_tracker/core/types.py:Frame` — frozen dataclass DTO already enforces BGR uint8 / HxWx3 / width-height agreement / `timestamp_ns >= 0`; Phase 3 produces these and nothing else on the public surface
- `pastor_tracker/io/arduino_motor.py` + `arduino_protocol.py` + `arduino_transport.py` — Phase 2 establishes the dedicated-thread + bounded `asyncio.Queue` + typed-error pattern; Phase 3 mirrors it (transport seam, thread reads blocking source, parser/builder runs in the thread, queue carries typed events)
- `structlog` JSON logger configured in Phase 1 — reuse with `module="obs_camera"` binding

### Established Patterns (Phases 1–2)
- Pure-core / dirty-edges layering — Phase 3 is squarely a "dirty edge" (camera I/O), all impure code lives under `pastor_tracker/io/`
- Pydantic v2 `frozen=True` for DTOs; mutate via `.model_copy(update=...)`; dataclass `frozen=True, slots=True` for ndarray-bearing DTOs
- `pytest` + `hypothesis` for property tests; mock-free for math; in-tree fakes permitted for transport seams (Phase 2 used `FakeSerialTransport`)
- Lint policy rejects `print(`, bare `except:`, `except Exception: pass` (`ruff` rules T20 / BLE001 / E722); `mypy --strict` no `Any`
- Bounded `asyncio.Queue` (N=256 in Phase 2) with drop-oldest + WARN log on drop; same backpressure stance applies here

### Integration Points
- `Frame` producer surface consumed later by `pastor_tracker/perception/pose_detector.py` (Phase 4) — public API likely `async for frame in camera.frames():` (mirror Phase 2 `motor.events()`)
- Pipeline orchestrator (Phase 6) wires the camera into `OBS VCam → FrameSource → PoseDetector → ...`; lifecycle start/stop must compose with the orchestrator's overall lifecycle
- Dashboard (Phase 7) reads camera FPS + capture resolution + last-error from a status surface — Phase 3 should expose minimal observable state (`is_running`, `current_resolution`, `last_error`) without leaking implementation details
- `Config` is read once at construction; no live reconfig in Phase 3 (dashboard "Save Config" reloads + restarts pipeline, not Phase 3's concern)

</code_context>

<specifics>
## Specific Ideas

- Mirror Phase 2's `SerialTransport` seam: introduce `VideoSource` Protocol (or `Callable`) in `obs_camera.py` so production wires `cv2.VideoCapture` and tests wire `FakeVideoSource`
- The bounded `asyncio.Queue` size should match Phase 2's N=256 unless the camera's frame size makes that wasteful; 30 frames covers a full second at 30 fps → choose 64 or 128 to bound memory while still tolerating brief consumer pauses
- The "first frame arrived" gate inside `start()` should reuse `arduino_ready_timeout_sec`-style timing — propose a new config knob `camera_first_frame_timeout_sec` (default 2.0 s) only if the hard-fail path needs operator-tunable timing; otherwise hard-code under `_FIRST_FRAME_TIMEOUT_SEC` constant per CLAUDE.md rule 6
- Resolution fallback decision is logged as a single structured INFO event with from/to dims + p95 delta + budget — gives operator/dashboard a clean signal
- Stall + reopen attempt logs include `attempt={1..3}`, `backoff_ms`, `last_grab_age_ms` so the post-mortem is one structlog query
- `OBSCameraNotFoundError` message format: `expected camera_name='{config.obs_camera_name}', available={[...]}` so the operator sees both sides at once — same shape as Phase 2's READY-mismatch log

</specifics>

<deferred>
## Deferred Ideas

- Continuous resolution-fallback evaluation outside the 2 s warmup window — deferred; one-shot semantics are sufficient for live church/conference coverage where OBS configuration is static during a service
- Hot-plug device-change detection (DirectShow `CM_*` callbacks / WMI events) — deferred; manual restart is acceptable
- `aiohttp`-style or `aioshutil`-style async camera lib adoption — deferred; dedicated-thread approach matches Phase 2 and has no current evidence of being inadequate
- Recording a real `.mp4` golden capture for replay tests — deferred; `FakeVideoSource` ndarray sequences cover the same paths with zero deps
- 4K / 60 fps capture targets — out of scope for v1
- Re-promotion from 720p back to 1080p when budget recovers — out of scope for v1
- Real-device pytest fixture (`pytest --hardware` marker) for OBS — deferred to Phase 8 QA-04 stage smoke

</deferred>
