# Phase 3: Camera I/O - Research

**Researched:** 2026-05-04
**Domain:** async OBS Virtual Camera frame source via OpenCV DirectShow + pygrabber enumeration; dedicated capture thread bridged to bounded `asyncio.Queue`
**Confidence:** HIGH (Phase 2 pattern is the template; OpenCV CAP_DSHOW + pygrabber behavior verified via official issues / docs; `core/types.py:Frame` contract authoritative)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md / Config**
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

**Async/Threading Architecture (Area 1, all accepted)**
- Dedicated capture thread runs blocking `cv2.VideoCapture.read()` and pushes built `Frame` DTOs into a bounded `asyncio.Queue` via `loop.call_soon_threadsafe(queue.put_nowait, ...)` — same pattern as Phase 2 RX thread
- Producer/consumer = SPSC (one capture thread, one async consumer / next pipeline stage)
- `Frame` is constructed in the capture thread (timestamp + width/height/dtype validation) — keeps ndarray work off the asyncio hot path
- Lifecycle — `async def start()` opens capture, spawns thread, awaits first frame (or fails with timeout); `async def stop()` signals shutdown event, joins thread with bounded timeout, releases `VideoCapture`

**Device Discovery & Failure (Area 2, all accepted)**
- Boot-only one-shot enumeration via `pygrabber.dshow_graph.FilterGraph().get_input_devices()`; no background re-poll
- Exact case-sensitive equality match against `config.obs_camera_name` (default `"OBS Virtual Camera"`)
- No match → raise typed `OBSCameraNotFoundError` carrying the full enumerated device list; structlog ERROR with `expected=...`, `available=[...]`; halt boot
- Multiple matches → pick first hit, structlog INFO listing all matched indexes (mirror Phase 2 multi-match rule)

**Resolution Fallback (Area 3, all accepted)**
- Frame-time budget breached = rolling **p95** of inter-grab `perf_counter_ns()` deltas over last 30 frames > 1.2 × (1 / `capture_fps`) seconds; persists ≥ 1 s
- Fallback evaluation is **one-shot**, gated to a 2 s warmup window after first frame; never re-promote
- Apply fallback by releasing the current `VideoCapture` and reopening at 1280×720 against the same matched DirectShow index
- Already-720p case + budget still breached → structlog ERROR (surface to UI in Phase 7), continue capturing at 720p

**Stall Recovery (Area 4, all accepted)**
- Stall detection lives in the capture thread — per-grab `perf_counter_ns()` delta vs 200 ms ceiling
- Up to 3 reopen attempts with linear backoff `200 ms → 500 ms → 1000 ms`; each attempt re-issues `VideoCapture(idx, CAP_DSHOW)` + property set + first-frame wait
- After 3 failed reopens → raise typed `CameraStallError` (carries restart attempt history); structlog ERROR; capture thread exits and async consumer surfaces the error
- Stale-frame drop (> 100 ms) handled by the async consumer at retrieval (`now_ns - frame.timestamp_ns > 100_000_000`); capture thread never decides — it just pushes (drop-oldest semantics on bounded queue with WARN log on drop, mirror Phase 2)

**Test Strategy**
- Public seam = `VideoSource` `Protocol` so production wires `cv2.VideoCapture` and tests wire `FakeVideoSource`
- `FakeVideoSource` is in-tree zero-deps — produces canned BGR ndarray sequences on demand; supports configurable per-frame delay, scripted stall/error injection, scripted index-mapping (for discovery tests)
- Test suite covers: device enumeration + exact-match, missing-device error path with device list, fallback trigger inside warmup, fallback inhibited outside warmup, stall + 3-attempt reopen + giveup, stale-frame drop on consumer, queue full → drop-oldest WARN, lifecycle start/stop joining
- No hardware-in-CI; real OBS device test deferred to Phase 8 QA-04 stage smoke
- Coverage target — ≥ 90 % line coverage on `obs_camera.py`; 100 % on the discovery/match branch

### Claude's Discretion
- Internal type names; new internal events/DTOs as needed
- Error class hierarchy under a single `CameraError` root (subclasses: `OBSCameraNotFoundError`, `CameraStallError`, `CameraOpenError`)
- Logging key names (consistent with Phases 1–2)
- File granularity inside `pastor_tracker/io/` — single `obs_camera.py` is fine; split permitted if line count balloons
- Whether the warmup-window p95 detector is a small helper class or a stateful function

### Deferred Ideas (OUT OF SCOPE)
- Continuous resolution-fallback evaluation outside the 2 s warmup window
- Hot-plug device-change detection (DirectShow `CM_*` callbacks / WMI events)
- `aioshutil`-style async camera lib adoption
- Recording a real `.mp4` golden capture for replay tests
- 4K / 60 fps capture targets
- Re-promotion from 720p back to 1080p when budget recovers
- Real-device pytest fixture (`pytest --hardware` marker) — Phase 8 QA-04
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IO-CAM-01 | Async OBS VCam frame source via `cv2.VideoCapture(idx, CAP_DSHOW)`; enumerate via `pygrabber.dshow_graph.FilterGraph`, match `"OBS Virtual Camera"` | Discovery & Boot — `FilterGraph().get_input_devices()` returns ordered list whose index is the cv2 device index when CAP_DSHOW backend is forced; verified via pygrabber source [CITED: github.com/bunkahle/pygrabber] |
| IO-CAM-02 | Hard-fail with available device list if OBS VCam not found | Discovery & Boot — `OBSCameraNotFoundError(expected=..., available=[...])` shape; halt boot |
| IO-CAM-03 | Target 1920×1080 @ 30 fps; auto-fall to 1280×720 if frame-time budget breached | Resolution Fallback section — rolling p95 detector + warmup gate + release+reopen strategy |
| IO-CAM-04 | Each frame stamped with `time.perf_counter_ns()` at grab time; queue drops frames older than 100 ms; stall > 200 ms logs ERROR + restarts capture | Frame Construction + Stall Recovery sections |
</phase_requirements>

## Summary

Phase 3 mirrors the Phase 2 dirty-edge skeleton almost line-for-line: a dedicated daemon thread runs the blocking call (`cap.read()` instead of `serial.read_until()`), constructs a typed frozen DTO inside the thread (the existing `core/types.py:Frame`, not a new event), and bridges to the asyncio event loop via `loop.call_soon_threadsafe(queue.put_nowait, frame)` against a bounded `asyncio.Queue` with drop-oldest semantics. The orchestrator's `start()` runs a synchronous boot phase (enumerate → match → open → first-frame wait) before spawning the thread; `stop()` reverses cleanly with a stop event + bounded join.

Three facts from primary sources sharpen the plan:

1. **The pygrabber index IS the cv2 index when both use DirectShow.** `FilterGraph.get_input_devices()` and `cv2.VideoCapture(idx, CAP_DSHOW)` both enumerate via the same `ICreateDevEnum` COM interface, in the same order. We can take the position of the matched friendly name in the pygrabber list and pass that integer directly to `VideoCapture`. [CITED: opencv source `cap_dshow.cpp` + pygrabber `dshow_graph.py`] No separate index-mapping step is needed.

2. **OBS VCam is `read()`-False-when-stopped, not `open()`-False.** The DirectShow filter is registered by the OBS installer and stays enumerable even when OBS is closed. `VideoCapture(idx, CAP_DSHOW).isOpened()` returns `True` in this state, but `cap.read()` returns `(False, None)`. The correct failure model is therefore: open succeeds, **first-frame timeout** fires inside `start()`, raise `CameraOpenError("OBS not running or stream not started")`. This is distinct from `OBSCameraNotFoundError` (filter not even enumerated). [CITED: OBS Studio Issue #8057, OpenCV Issue #19746]

3. **CAP_DSHOW first-frame latency is real and large — up to ~3 s.** This is documented in OpenCV bug reports; the Windows DirectShow filter graph rendering takes time to settle. The `_FIRST_FRAME_TIMEOUT_SEC` constant must be ≥ 3.0 to avoid spurious `CameraOpenError` on cold boot. [CITED: OpenCV forum thread on slow camera init] The configured default in this RESEARCH = 3.0 s.

**Primary recommendation:** Single file `pastor_tracker/io/obs_camera.py` (~350 LOC budget) with three internal sections — (1) `VideoSource` Protocol + `OpenCvVideoSource` real impl + `FakeVideoSource` test fake, (2) discovery helper `discover_obs_camera_index()`, (3) `ObsCamera` orchestrator (`start`/`stop`/`frames()` async iterator). Mirrors Phase 2's three-file split conceptually but stays single-file because pygrabber's enumeration footprint is much smaller than pyserial's wire-protocol footprint — there is no pure-parser layer to isolate. Split if file exceeds ~400 LOC.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| DirectShow device enumeration (friendly-name → index) | Dirty edge (`obs_camera.py`) | — | OS-level COM call via pygrabber; hidden behind `discover_obs_camera_index()` so tests can monkeypatch the FilterGraph factory |
| Blocking `cv2.VideoCapture.read()` | Dirty edge (`obs_camera.py`) | — | Real I/O lives behind `VideoSource` Protocol; `FakeVideoSource` for tests |
| Capture thread + asyncio queue bridge | Orchestrator (`obs_camera.py`) | — | The only place threading touches asyncio; isolates the tricky bit; mirrors Phase 2 RX thread |
| `Frame` DTO construction (timestamp + shape validation) | Capture thread (orchestrator) | — | Keeps ndarray work off the asyncio hot path; `Frame.__post_init__` is the boundary contract |
| Stall detection + reopen state machine | Capture thread (orchestrator) | — | Lives where the `perf_counter_ns()` deltas are produced; thread restarts itself via internal helper, raises typed error after 3 strikes |
| Resolution-fallback p95 detector | Capture thread (orchestrator) | — | Stateful sliding window of 30 inter-grab deltas; one-shot decision inside 2 s warmup; release+reopen at 720p; never re-promote |
| Stale-frame drop (> 100 ms) | **Async consumer** (`async for frame in cam.frames():`) | — | Capture thread MUST NOT decide; it just pushes. Consumer-side drop because `now_ns` is best read at retrieval, not grab-time |
| Pipeline orchestration / wiring | **Phase 6** (`pipeline.py`) | — | `obs_camera.py` exposes producer surface only; never knows about pose detector etc. |
| Status surface (`is_running`, `current_resolution`, `last_error`) | Orchestrator (`obs_camera.py`) | UI (Phase 7) | Read-only properties on `ObsCamera`; Phase 7 dashboard reads them |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `opencv-python` | `>=4.10,<5.0` | `cv2.VideoCapture(idx, CAP_DSHOW)` for blocking frame grab | Blessed by PROJECT.md ## Constraints; CAP_DSHOW gives the fastest open path on Windows (~3 s vs 25 s for MSMF) [CITED: OpenCV Issue #17687] |
| `pygrabber` | `==0.2` | `FilterGraph().get_input_devices()` for DirectShow friendly-name enumeration | Blessed by PROJECT.md ## Constraints; only viable Python wrapper around `ICreateDevEnum`; cv2 alone cannot enumerate by name |
| `numpy` | `>=2.4,<3.0` (already pinned) | ndarray buffer for `Frame.image` | Phase 1 baseline |
| `structlog` | `>=24.4,<26.0` (already pinned) | JSON logging | Phase 1 baseline |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis` | (already in dev deps) | Test framework | Test files only |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `pygrabber` for enumeration | `comtypes` directly with `ICreateDevEnum` | More code (~150 LOC of COM boilerplate); pygrabber is 100 LOC of ctypes wrapper; same dependency surface; not worth the rewrite |
| `cv2.CAP_DSHOW` | `cv2.CAP_MSMF` (Media Foundation) | MSMF has 25 s open latency on Windows [CITED: OpenCV Issue #17687]; CAP_DSHOW takes ~3 s; CAP_MSMF gives no upside for our use |
| Dedicated thread + executor pattern | `aioshutil`/`aiocv2`-style async camera lib | CONTEXT.md explicitly defers; we copy Phase 2 idiom |
| Pydantic BaseModel for `Frame` | (already chosen — frozen dataclass) | `Frame` exists as `dataclass(frozen=True, slots=True)` because Pydantic v2 needs `arbitrary_types_allowed=True` for ndarrays and gives no real validation. Locked. |

**Installation:**
```bash
# Add to pastor_tracker/pyproject.toml [project.dependencies]
opencv-python = ">=4.10,<5.0"
pygrabber = "==0.2"
# Then:
uv sync
```

**Version verification (run before locking):**
```bash
uv pip index versions opencv-python  # confirm latest 4.x
uv pip index versions pygrabber       # confirm 0.2 still latest (it is — last release ~1 yr ago, "Inactive" on Snyk; acceptable since DirectShow API is stable)
```

[ASSUMED] `opencv-python>=4.10,<5.0` — opencv-python 4.x is the long-running stable line; `mypy --strict` is already configured to ignore `cv2.*` imports (`pyproject.toml:82`) so no stub package needed. Version pin is at researcher discretion; verify against PyPI at task time.
[VERIFIED: Snyk + PyPI] `pygrabber==0.2` is the only published version; project is "Inactive" but the wrapped API (`ICreateDevEnum`) is a stable Win32 COM interface — inactivity is non-blocking.

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────── ObsCamera (asyncio orchestrator) ────────────────────┐
│                                                                                │
│  start():                            stop():                                   │
│    discover_obs_camera_index()        stop_event.set()                         │
│    open VideoSource @ 1080p           join capture_thread (timeout)            │
│    spawn capture thread               source.release()                         │
│    await first frame                                                           │
│                                                                                │
│              ┌────────── frames_queue: asyncio.Queue(maxsize=64) ─────────┐    │
│              │                                                            │    │
│              ▼                                                            │    │
│  async for frame in cam.frames():                                         │    │
│      now_ns = time.perf_counter_ns()                                      │    │
│      if now_ns - frame.timestamp_ns > 100_000_000:                        │    │
│          continue   # stale-drop                                          │    │
│      yield frame                                                          │    │
│              ▲                                                            │    │
└──────────────┼────────────────────────────────────────────────────────────┘    │
               │                                                                 │
   loop.call_soon_threadsafe(_enqueue_frame, frame)                              │
               │                                                                 │
               │ (cross-thread bridge — Phase 2 idiom)                           │
               │                                                                 │
┌──────────────┼─────── capture_thread (daemon, blocking I/O) ──────────────────┐
│              │                                                                │
│  while not stop_event:                                                        │
│      ok, bgr = source.read()           ◄── blocking; ~33 ms typical at 30 fps │
│      now_ns = time.perf_counter_ns()                                          │
│      delta_ns = now_ns - last_grab_ns                                         │
│                                                                               │
│      if not ok or delta_ns > 200_000_000:                                     │
│          attempt_reopen()              ── 200/500/1000 ms backoff             │
│          continue                                                             │
│                                                                               │
│      frame = Frame(image=bgr, width=W, height=H, timestamp_ns=now_ns)         │
│                                                                               │
│      p95_window.observe(delta_ns)                                             │
│      if warmup_active and p95_window.budget_breached_persistent():            │
│          fallback_to_720p()            ── one-shot inside 2 s warmup          │
│                                                                               │
│      enqueue(frame)                    ── via call_soon_threadsafe            │
│              │                                                                │
└──────────────┼────────────────────────────────────────────────────────────────┘
               │
               ▼
   VideoSource (Protocol DI seam)
   ├── OpenCvVideoSource → cv2.VideoCapture(idx, CAP_DSHOW)
   └── FakeVideoSource   → scripted ndarray sequences for tests
```

Key invariants (mirror Phase 2):
1. **Single producer** — only the capture thread calls `source.read()`. Lifecycle reopens during `start()` / fallback / stall-recovery happen on threads that the loop currently controls (start: main thread inside `await asyncio.to_thread(...)`; reopens: capture thread itself).
2. **Single consumer** — only the asyncio loop drains `frames_queue` via `cam.frames()`.
3. **Bounded queue** — `asyncio.Queue(maxsize=64)` (sized below); drop-oldest + WARN on full.
4. **No shared mutable state across the bridge** — capture thread owns `VideoSource`; main loop owns `frames_queue`. Communication via `stop_event` (loop → thread, signal-only) and `call_soon_threadsafe` (thread → loop).

### Recommended Project Structure

```
src/pastor_tracker/io/
├── arduino_protocol.py       # Phase 2 (existing)
├── arduino_transport.py      # Phase 2 (existing)
├── arduino_motor.py          # Phase 2 (existing)
└── obs_camera.py             # Phase 3 — single file, ~350 LOC budget
                              #   §1 VideoSource Protocol + OpenCvVideoSource + FakeVideoSource
                              #   §2 discover_obs_camera_index() + OBSCameraNotFoundError
                              #   §3 ObsCamera orchestrator (start/stop/frames + capture thread)

tests/
├── test_obs_camera_discovery.py     # IO-CAM-01 / IO-CAM-02 — pygrabber monkeypatch
├── test_obs_camera_lifecycle.py     # start()/stop() with FakeVideoSource
├── test_obs_camera_fallback.py      # IO-CAM-03 — warmup-window fallback trigger
├── test_obs_camera_stall.py         # IO-CAM-04 — stall + 3-reopen + give-up
├── test_obs_camera_stale_drop.py    # IO-CAM-04 — consumer-side stale drop
└── fixtures/camera_traces.py        # canned ndarray sequences shared between tests
```

### Pattern 1: VideoSource Protocol DI (mirror Phase 2 SerialTransport)

**What:** A `runtime_checkable` `Protocol` that names the three operations the orchestrator depends on. Production wires a thin OpenCV wrapper; tests wire `FakeVideoSource`.
**When to use:** This is the only safe way to keep the orchestrator unit-testable without real hardware.
**Example:**
```python
# Source: extends Phase 2 idiom from arduino_transport.py
from typing import Protocol, runtime_checkable
import numpy.typing as npt
import numpy as np

_ImageArray = npt.NDArray[np.uint8]

@runtime_checkable
class VideoSource(Protocol):
    """Minimal DI surface — orchestrator imports only this contract."""

    def read(self) -> tuple[bool, _ImageArray | None]: ...
    def set_resolution(self, width: int, height: int, fps: int) -> None: ...
    def is_opened(self) -> bool: ...
    def release(self) -> None: ...
```

**Production impl:**
```python
class OpenCvVideoSource:
    """Thin cv2.VideoCapture wrapper. Single OS-level handle owner."""

    def __init__(self, device_index: int, width: int, height: int, fps: int) -> None:
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        # Property set AFTER open with CAP_DSHOW (verified via OpenCV docs);
        # set_resolution() is also called from reopen paths.
        self._apply_props(width, height, fps)

    def _apply_props(self, width: int, height: int, fps: int) -> None:
        # Order matters per kurokesu.com guide [CITED]: FOURCC first if used,
        # then width, then height, then FPS. We skip FOURCC because OBS VCam
        # advertises a single MJPG/YUY2 stream and lets DirectShow negotiate.
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  float(width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        self._cap.set(cv2.CAP_PROP_FPS,          float(fps))

    def read(self) -> tuple[bool, _ImageArray | None]:
        ok, frame = self._cap.read()
        return ok, frame  # cv2 returns BGR uint8 HxWx3 by default

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        # Phase 3 fallback path: prefer release+reopen over in-place set;
        # CAP_DSHOW in-place sets are flaky across vendors per OpenCV Issue
        # #23533 [CITED]. The orchestrator handles release+reopen explicitly;
        # this method exists for tests / future re-use.
        self._apply_props(width, height, fps)

    def is_opened(self) -> bool:
        return bool(self._cap.isOpened())

    def release(self) -> None:
        self._cap.release()
```

### Pattern 2: Capture-thread → asyncio bridge (copy of Phase 2 RX thread)

**What:** Daemon thread runs the blocking `read()` loop, builds a typed `Frame`, then crosses to the loop via `loop.call_soon_threadsafe(self._enqueue_frame, frame)`. The loop-side enqueuer applies drop-oldest.
**When to use:** Always — this is the only blocking-I/O→asyncio idiom CONTEXT.md authorizes.
**Example:**
```python
# Source: ports arduino_motor.py:_rx_loop almost verbatim
def _capture_loop(self) -> None:
    """Daemon-thread capture loop. Translates source errors to CameraStallError."""
    last_grab_ns: int | None = None
    while not self._stop_event.is_set():
        ok, bgr = self._source.read()
        now_ns = time.perf_counter_ns()
        if not ok or bgr is None:
            if not self._attempt_reopen():
                # 3 strikes — surface to consumer via the queue
                self._loop.call_soon_threadsafe(
                    self._on_capture_failed,
                    CameraStallError(attempts=self._reopen_history),
                )
                return
            last_grab_ns = None  # reset window after reopen
            continue
        if last_grab_ns is not None:
            delta_ns = now_ns - last_grab_ns
            if delta_ns > _STALL_THRESHOLD_NS:
                # Stall detected — same reopen path
                if not self._attempt_reopen():
                    self._loop.call_soon_threadsafe(
                        self._on_capture_failed,
                        CameraStallError(attempts=self._reopen_history),
                    )
                    return
                last_grab_ns = None
                continue
            self._p95_window.observe(delta_ns)
            self._maybe_fallback()  # one-shot inside warmup
        # Construct DTO inside the thread; Frame.__post_init__ validates shape.
        frame = Frame(
            image=bgr,
            width=self._current_width,
            height=self._current_height,
            timestamp_ns=now_ns,
        )
        last_grab_ns = now_ns
        self._loop.call_soon_threadsafe(self._enqueue_frame, frame)
```

### Pattern 3: Rolling p95 sliding window (no numpy on hot path)

**What:** A `collections.deque(maxlen=30)` of inter-grab deltas; p95 = `sorted(deque)[int(0.95 * len) - 1]` recomputed each frame. At 30 fps, sort cost on 30 ints is <1 µs — negligible vs. ~33 ms grab cost.
**When to use:** Inside the warmup window only; after first successful frame.
**Example:**
```python
import collections
from typing import Final

_P95_WINDOW_SIZE: Final[int] = 30           # 1 s of frames at 30 fps
_P95_INDEX: Final[int] = 28                 # int(0.95 * 30) - 1 = 28
_BUDGET_PERSIST_NS: Final[int] = 1_000_000_000  # 1 s sustained breach
_BUDGET_MULTIPLIER: Final[float] = 1.2

class _P95Detector:
    """Rolling p95 of inter-grab nanosecond deltas. Pure transform on a deque."""

    def __init__(self, target_fps: int) -> None:
        target_interval_ns = int(1_000_000_000 / target_fps)
        self._budget_ns = int(target_interval_ns * _BUDGET_MULTIPLIER)
        self._window: collections.deque[int] = collections.deque(maxlen=_P95_WINDOW_SIZE)
        self._first_breach_ns: int | None = None

    def observe(self, delta_ns: int) -> None:
        self._window.append(delta_ns)

    def budget_breached_persistent(self, now_ns: int) -> bool:
        if len(self._window) < _P95_WINDOW_SIZE:
            return False  # need full window before deciding
        sorted_window = sorted(self._window)
        p95 = sorted_window[_P95_INDEX]
        if p95 <= self._budget_ns:
            self._first_breach_ns = None
            return False
        if self._first_breach_ns is None:
            self._first_breach_ns = now_ns
            return False
        return (now_ns - self._first_breach_ns) >= _BUDGET_PERSIST_NS
```

### Anti-Patterns to Avoid

- **In-place `cap.set()` for resolution change.** Cross-vendor flaky on CAP_DSHOW [CITED: OpenCV Issue #23533]. Always release + reopen at the new resolution.
- **Using `asyncio.run_in_executor` for `read()`.** Works but pushes per-frame thread-pool overhead onto every grab. Phase 2's pattern (one dedicated thread that LIVES in the read loop) is cheaper at 30 Hz steady state.
- **Stamping `timestamp_ns` on the loop side.** Defeats `perf_counter_ns()` precision — adds queue-traversal jitter. Stamp at grab in the capture thread.
- **`time.time()` instead of `time.perf_counter_ns()`.** `time.time()` is wall-clock; subject to NTP slew and DST. `perf_counter_ns()` is monotonic with ns granularity — correct for inter-frame deltas. IO-CAM-04 names it specifically.
- **Mutating `Frame.image` in place after enqueue.** `Frame` is `frozen=True` but ndarrays are not immutable. Capture thread must NEVER reuse a buffer; cv2 always allocates a fresh ndarray per `read()` call (verified via cv2 source) so this is automatic — do not "optimize" by passing a preallocated buffer to `read(buffer)`.
- **Hot-promoting back from 720p to 1080p.** Out of scope; deferred. Once we drop, we stay dropped.
- **Letting `start()` return before first frame.** IO-CAM-04 implies grab-time invariants the consumer relies on; `start()` must block (await) until the first valid `Frame` is in the queue or the timeout fires.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| DirectShow device enumeration | Hand-rolled `comtypes` + `ICreateDevEnum` calls | `pygrabber.dshow_graph.FilterGraph().get_input_devices()` | 150 LOC of COM boilerplate vs one import; pygrabber wraps the same API |
| Frame buffering | Custom ring buffer of ndarrays | `asyncio.Queue(maxsize=64)` + drop-oldest | Standard library; bounded; the cross-thread bridge is `call_soon_threadsafe` (asyncio docs) |
| Resolution negotiation | Try-many-resolutions loop with `cap.set()` | Two pinned targets (1080p + 720p) + release+reopen | Trying random resolutions creates non-deterministic startup; spec only allows two |
| First-frame wait | `time.sleep(2.0)` after `cap.open()` | Loop on `cap.read()` until ok=True or timeout | The 3 s CAP_DSHOW startup is variable; polling the read is correct |
| FPS measurement | Compute average FPS over all frames | Rolling p95 over 30-frame window | Average hides tail-latency stalls; p95 is the standard percentile for jitter detection |
| Mock libraries for cv2 | `unittest.mock.patch("cv2.VideoCapture")` | DI Protocol + `FakeVideoSource` | CONTEXT.md locks DI fake; mocks fragile against cv2 internals (release/read sequencing) |

**Key insight:** This phase is small because OpenCV does the byte-level DirectShow work and pygrabber does the device enumeration. Phase 3's job is the *typed orchestration layer* — same shape as Phase 2 — and the only novel piece is the rolling-p95 detector, which is 20 LOC of pure stdlib.

## Common Pitfalls

### Pitfall 1: pygrabber index ≠ cv2 index when CAP_DSHOW is NOT forced
**What goes wrong:** Without explicit `cv2.CAP_DSHOW`, OpenCV may select MSMF on Windows and enumerate devices in a different order. Then the index returned by pygrabber points to a different camera in cv2.
**Why it happens:** OpenCV has multiple Windows backends (MSMF, DSHOW, V4L2-via-WSL, …); without a backend hint, the first available one wins, and the order varies.
**How to avoid:** ALWAYS pass `cv2.CAP_DSHOW` as the second argument to `VideoCapture`. Both pygrabber and `cv2.VideoCapture(idx, CAP_DSHOW)` use `ICreateDevEnum` underneath in the same order. [CITED: opencv `cap_dshow.cpp`]
**Warning sign:** Discovery test passes, but lifecycle test on real hardware reads from the laptop webcam instead of OBS VCam.

### Pitfall 2: OBS not running ⇒ `isOpened()` returns True, `read()` returns False
**What goes wrong:** A naive `if not cap.isOpened(): raise` check passes, but every `read()` returns `(False, None)`. The orchestrator interprets this as "stall" and burns through 3 reopen attempts before raising `CameraStallError` — but the right diagnosis is "OBS is not streaming".
**Why it happens:** OBS installs a DirectShow filter that is registered system-wide and remains enumerable when OBS is closed. The filter accepts open() but produces no frames.
**How to avoid:** Distinguish the failure mode in `start()`. If `attempt_first_frame()` times out, raise `CameraOpenError("first frame timeout — is OBS running and streaming the virtual camera?")`. Inside the steady-state capture loop, persistent `read() == False` over 3 attempts becomes `CameraStallError`. Two distinct error classes, two distinct hint strings.
**Warning sign:** [CITED: OBS Studio Issue #8057] User reports "OBS Virtual Camera output on Windows / DirectShow only delivers one initial frame" — root cause was OBS not in "Start Virtual Camera" mode.

### Pitfall 3: `cap.set()` after open is camera-dependent on CAP_DSHOW
**What goes wrong:** `cap.set(CAP_PROP_FRAME_WIDTH, 1920)` returns `True`, but the next `read()` returns a 640×480 frame because the driver silently snapped to its default.
**Why it happens:** [CITED: OpenCV docs videoio_flags_base — "depending on device hardware, driver and API backend"] DirectShow filters negotiate format with the source; the cv2-side property set is advisory.
**How to avoid:** After every property set, READ the property back via `cap.get(CAP_PROP_FRAME_WIDTH)` AND validate against the first `read()` result's `bgr.shape[1]`. If it doesn't match the request, log WARN with `requested=...` `actual=...` and propagate the actual width to `Frame.width` (so the DTO contract holds). This is a Phase 8 edge case — for v1, log and trust the actual shape.
**Warning sign:** Tests that write 1080p assertions fail on certain dev boxes despite the property set succeeding.

### Pitfall 4: 32-bit vs 64-bit DirectShow filter visibility
**What goes wrong:** Python is 64-bit, but a 32-bit-only DirectShow filter (rare; old webcam drivers) is invisible to `FilterGraph().get_input_devices()` from a 64-bit process.
**Why it happens:** Windows separates 32/64 COM registration; `ICreateDevEnum` honors the calling process's bitness.
**How to avoid:** Document that pastor_tracker is 64-bit (Python 3.12 from python.org is 64-bit by default). OBS itself is 64-bit on modern Windows; this is not a real risk for OBS VCam. Mention in README only.
**Warning sign:** Device list omits a camera that "Camera" app on Windows shows.

### Pitfall 5: `read()` return value is `(bool, ndarray | None)` — `None` allowed
**What goes wrong:** Pretending the second return is always an ndarray and indexing into it (`bgr.shape`) crashes with `AttributeError: 'NoneType' object has no attribute 'shape'`.
**Why it happens:** cv2 documents the contract; some forks return `(False, np.empty(0))` instead, but the pinned `opencv-python>=4.10` returns `(False, None)`.
**How to avoid:** Check `ok` first; only access `bgr` when `ok is True`. Type the `VideoSource.read()` return as `tuple[bool, _ImageArray | None]` so mypy enforces the guard.
**Warning sign:** `AttributeError` in stall-recovery test that simulates `(False, None)` returns.

### Pitfall 6: Capture thread closes `VideoCapture` while loop still has Frame references
**What goes wrong:** `stop()` calls `source.release()` while a `Frame` whose `image` ndarray points into a cv2-owned buffer is still being processed by the consumer.
**Why it happens:** OpenCV in Python returns a fresh ndarray per `read()` (the buffer is owned by the ndarray, not by the VideoCapture). Verified via cv2 Python bindings — `cv::Mat → np.ndarray` conversion copies ownership. So this is NOT a real risk for cv2 specifically.
**How to avoid:** Document the contract: `Frame.image` outlives the `VideoCapture`. No special handling needed; the fact that this isn't a problem deserves a comment so future contributors don't "fix" it.
**Warning sign:** N/A — this would manifest as use-after-free segfaults; verified absent in cv2.

### Pitfall 7: Closing the port while the capture thread is mid-`read()`
**What goes wrong:** `stop()` calls `source.release()` while the capture thread is blocked inside `read()`. The thread either hangs or returns `(False, None)` after a long delay.
**Why it happens:** Same as Phase 2 Pitfall 4 — order-of-shutdown matters.
**How to avoid:** Mirror Phase 2 close ordering: set `_stop_event` first → wait for capture thread to exit (it pops out via the next `read()` returning, or after the loop's natural ~33 ms tick) → THEN call `source.release()`. The thread checks `_stop_event` after each `read()` so the latest blocking call will resolve and the loop will exit.
**Warning sign:** Test teardown errors with "ClearCommError failed" or hangs > 5 s.

### Pitfall 8: First-frame timeout shorter than CAP_DSHOW cold-start latency
**What goes wrong:** Setting `_FIRST_FRAME_TIMEOUT_SEC = 1.0` gives spurious `CameraOpenError` on a perfectly healthy OBS that takes 2.5 s to render its first frame.
**Why it happens:** [CITED: OpenCV forum "Slow camera initialization"] CAP_DSHOW first-frame latency is 1–3 s on Windows due to filter graph build and frame allocator init.
**How to avoid:** `_FIRST_FRAME_TIMEOUT_SEC = 3.0` constant. Document the citation. If a future Windows release changes this, propose a Config knob.
**Warning sign:** Cold-start integration test fails on slower machines with "first frame timeout".

### Pitfall 9: Drop-oldest race between thread and consumer (RESOLVED — same as Phase 2)
**What goes wrong:** Naive worry: between `queue.get_nowait()` and `queue.put_nowait()` in `_enqueue_frame`, can another `_enqueue_frame` race? No, because `_enqueue_frame` runs on the loop via `call_soon_threadsafe` and asyncio is single-threaded.
**How to avoid:** Document `_enqueue_frame` runs only via `call_soon_threadsafe` and is non-async (synchronous callback).

### Pitfall 10: pygrabber FilterGraph instantiation has side effects
**What goes wrong:** Creating a `FilterGraph()` inside a unit test pulls in COM initialization (`CoInitialize`); if the test runs in a thread that already initialized COM with a different mode, it raises.
**Why it happens:** pygrabber calls `CoInitialize(NULL)` (apartment-threaded) on construction.
**How to avoid:** Tests NEVER construct a real `FilterGraph`. The discovery code uses a factory closure (`Callable[[], FilterGraph]`) so tests can monkeypatch the factory to return a stub. Production wires `lambda: FilterGraph()`.
**Warning sign:** `OSError: [WinError -2147417850]` (RPC_E_CHANGED_MODE) in pytest workers.

## Code Examples

### Discovery helper — exact-match by friendly name

```python
# Source: extends Phase 2 idiom from arduino_transport.py:discover_arduino_port
from typing import Callable, Final

_FilterGraphFactory = Callable[[], "FilterGraph"]   # avoid runtime import in tests

def discover_obs_camera_index(
    expected_name: str,
    *,
    factory: _FilterGraphFactory,
    logger: structlog.stdlib.BoundLogger,
) -> int:
    """Enumerate DirectShow devices; return index whose friendly name == expected_name.

    Raises:
        OBSCameraNotFoundError: no exact-match device found; carries available list.
    """
    graph = factory()
    devices: list[str] = list(graph.get_input_devices())  # FilterGraph returns list[str]
    matches = [
        (idx, name) for idx, name in enumerate(devices) if name == expected_name
    ]
    if not matches:
        # Tiger-style: no silent fallback. Carry full list for operator triage.
        raise OBSCameraNotFoundError(
            expected=expected_name,
            available=devices,
        )
    if len(matches) > 1:
        # Mirror Phase 2 multi-match log shape — chosen + all_indexes
        logger.info(
            "camera_multiple_matches",
            chosen_index=matches[0][0],
            all_indexes=[m[0] for m in matches],
            name=expected_name,
        )
    else:
        logger.info(
            "camera_discovered",
            index=matches[0][0],
            name=expected_name,
            available_count=len(devices),
        )
    return matches[0][0]
```

### Bounded-queue enqueue with drop-oldest (port of Phase 2 `_enqueue`)

```python
def _enqueue_frame(self, frame: Frame) -> None:
    """Loop-thread synchronous enqueuer. Drop-oldest semantics."""
    queue = self._frames_queue
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        self._logger.warning(
            "frames_queue_full",
            dropped_timestamp_ns=frame.timestamp_ns,  # log the one we just put for context
            queue_max=_FRAMES_QUEUE_MAX_SIZE,
        )
    queue.put_nowait(frame)
```

### Stale-frame drop on the consumer side

```python
async def frames(self) -> AsyncIterator[Frame]:
    """Async-iterable view of fresh Frames. Drops frames older than 100 ms."""
    while True:
        frame = await self._frames_queue.get()
        now_ns = time.perf_counter_ns()
        age_ns = now_ns - frame.timestamp_ns
        if age_ns > _STALE_FRAME_MAX_AGE_NS:
            self._logger.warning(
                "frame_stale_dropped",
                age_ms=age_ns / 1_000_000.0,
                threshold_ms=_STALE_FRAME_MAX_AGE_NS / 1_000_000.0,
            )
            continue
        yield frame
```

### Reopen with linear backoff (200 / 500 / 1000 ms)

```python
_REOPEN_BACKOFFS_MS: Final[tuple[int, int, int]] = (200, 500, 1000)

def _attempt_reopen(self) -> bool:
    """Try up to 3 reopens with linear backoff. Records attempts on self._reopen_history.

    Returns True on success, False after 3 failures (caller raises CameraStallError).
    """
    for attempt_index, backoff_ms in enumerate(_REOPEN_BACKOFFS_MS, start=1):
        self._source.release()
        self._stop_event.wait(backoff_ms / 1_000.0)  # responsive to shutdown
        if self._stop_event.is_set():
            return False
        try:
            self._source = self._video_source_factory(
                self._device_index,
                self._current_width,
                self._current_height,
                self._config.capture_fps,
            )
        except Exception as exc:  # noqa: BLE001 — documented translator
            self._reopen_history.append((attempt_index, backoff_ms, str(exc)))
            self._logger.warning(
                "camera_reopen_attempt_failed",
                attempt=attempt_index,
                backoff_ms=backoff_ms,
                reason=str(exc),
            )
            continue
        # Wait for first frame after reopen.
        if self._wait_first_frame_blocking():
            self._logger.warning(
                "camera_reopen_succeeded",
                attempt=attempt_index,
                backoff_ms=backoff_ms,
            )
            return True
        self._reopen_history.append((attempt_index, backoff_ms, "first_frame_timeout"))
    return False
```

### Frame DTO construction inside the capture thread (uses existing `core/types.py:Frame`)

```python
# `Frame` is the existing dataclass from core/types.py — no new DTO.
# Tiger-style validation in __post_init__ already enforces ndim==3, channels==3,
# width/height match, timestamp_ns >= 0. Capture thread just constructs.

def _build_frame(self, bgr: _ImageArray, now_ns: int) -> Frame:
    """Cheap DTO construction. Raises ValueError on shape mismatch (caught by caller)."""
    # No copy — Frame stores the cv2-owned ndarray reference. Per Pitfall 6,
    # cv2 allocates a fresh ndarray per read(); this is safe.
    return Frame(
        image=bgr,
        width=self._current_width,
        height=self._current_height,
        timestamp_ns=now_ns,
    )
```

## Sizing the bounded queue

At 30 fps, BGR uint8 1080p = 1920 × 1080 × 3 = **5 .93 MiB / frame**. Queue size tradeoff:

| `maxsize` | Memory ceiling (1080p) | Latency at full | Notes |
|-----------|----------------------|-----------------|-------|
| 32 | ≈ 190 MiB | ≈ 1.07 s | Tight; brief consumer pauses can drop |
| **64** | **≈ 380 MiB** | **≈ 2.13 s** | **Recommended — matches 2-second OBS scene-transition glitch envelope** |
| 128 | ≈ 760 MiB | ≈ 4.27 s | Excess; stale-drop kicks in at 100 ms long before this fills |
| 256 (Phase 2) | ≈ 1.52 GiB | ≈ 8.5 s | Phase 2 used 256 because each event is < 100 bytes; here events are MiB-scale |

**Recommended: `_FRAMES_QUEUE_MAX_SIZE: Final[int] = 64`.** Rationale:
- 1080p Frame = ~6 MiB; a queue of 64 caps memory at ~380 MiB which is acceptable on a 16 GB dev box.
- The consumer-side stale-drop kicks in at age > 100 ms; if the consumer is not draining within 100 ms, every queued frame is already stale, so a deeper queue gains nothing.
- 64 = 2.13 seconds of buffer. This tolerates a 1-second scene-transition glitch (OBS) plus a 1-second pose-detector hiccup (Phase 4 GPU re-init) without dropping.
- Drop-oldest WARN log shape: `frames_queue_full` with `dropped_timestamp_ns` + `queue_max=64`. Consumers and dashboard parse this for FPS-loss telemetry.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4 + pytest-asyncio 1.3.0 (verified installed in `pastor_tracker/.venv` per Phase 2) |
| Config file | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (asyncio_mode="auto", filterwarnings=error) — already set |
| Quick run command | `pastor_tracker\.venv\Scripts\pytest.exe tests/test_obs_camera_*.py -x` |
| Full suite command | `pastor_tracker\.venv\Scripts\pytest.exe -ra` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| IO-CAM-01 | Discover OBS VCam by exact-match name; index → `cv2.VideoCapture` mapping | unit (FilterGraph factory monkeypatched) | `pytest tests/test_obs_camera_discovery.py::test_exact_match -x` | ❌ Wave 0 |
| IO-CAM-01 | Multi-match — pick first, log INFO with all indexes | unit | `pytest tests/test_obs_camera_discovery.py::test_multi_match_picks_first_logs_all -x` | ❌ Wave 0 |
| IO-CAM-02 | Missing device raises `OBSCameraNotFoundError` carrying `available=[...]` | unit | `pytest tests/test_obs_camera_discovery.py::test_missing_raises_with_device_list -x` | ❌ Wave 0 |
| IO-CAM-03 | Warmup-window p95 budget breach triggers ONE-shot fallback to 720p; logs WARN | integration (FakeVideoSource with scripted slow grabs) | `pytest tests/test_obs_camera_fallback.py::test_warmup_breach_triggers_fallback -x` | ❌ Wave 0 |
| IO-CAM-03 | After warmup window, slow grabs do NOT trigger fallback (one-shot semantics) | integration | `pytest tests/test_obs_camera_fallback.py::test_post_warmup_breach_inhibited -x` | ❌ Wave 0 |
| IO-CAM-03 | Already-720p + still-breached → ERROR log + continue capture | integration | `pytest tests/test_obs_camera_fallback.py::test_720p_breach_logs_error_continues -x` | ❌ Wave 0 |
| IO-CAM-04 | `Frame.timestamp_ns` populated with `perf_counter_ns()` at grab; monotonic | unit | `pytest tests/test_obs_camera_lifecycle.py::test_frames_monotonic_timestamps -x` | ❌ Wave 0 |
| IO-CAM-04 | Stale frame (> 100 ms age) dropped on consumer side; WARN logged | unit | `pytest tests/test_obs_camera_stale_drop.py -x` | ❌ Wave 0 |
| IO-CAM-04 | Stall (> 200 ms inter-grab) → 3-attempt reopen; success on attempt N exits cleanly | integration | `pytest tests/test_obs_camera_stall.py::test_stall_reopen_succeeds -x` | ❌ Wave 0 |
| IO-CAM-04 | Stall + 3 failed reopens → `CameraStallError` raised; capture thread exits | integration | `pytest tests/test_obs_camera_stall.py::test_stall_3_failures_raises -x` | ❌ Wave 0 |
| (lifecycle) | `start()` blocks until first frame; `stop()` joins thread within bounded timeout | unit | `pytest tests/test_obs_camera_lifecycle.py::test_start_stop_clean -x` | ❌ Wave 0 |
| (lifecycle) | Queue full → drop-oldest + `frames_queue_full` WARN | unit | `pytest tests/test_obs_camera_lifecycle.py::test_queue_drop_oldest -x` | ❌ Wave 0 |
| (lifecycle) | First-frame timeout → `CameraOpenError` with hint about OBS not running | unit | `pytest tests/test_obs_camera_lifecycle.py::test_first_frame_timeout_obs_not_running -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_obs_camera_*.py -x` (≤ 5 s — all-fake, no hardware)
- **Per wave merge:** `pytest -ra` (full suite — Phase 1 + Phase 2 + Phase 3, estimated ≤ 15 s)
- **Phase gate:** Full suite green AND `coverage report --include="src/pastor_tracker/io/obs_camera.py"` shows ≥ 90% line coverage; 100% coverage on the discovery/match branch (safety-critical for hard-fail behavior)

### Wave 0 Gaps
- [ ] `tests/test_obs_camera_discovery.py` — covers IO-CAM-01, IO-CAM-02
- [ ] `tests/test_obs_camera_lifecycle.py` — covers start/stop, first-frame, queue-drop, timestamp semantics
- [ ] `tests/test_obs_camera_fallback.py` — covers IO-CAM-03 (warmup, post-warmup, 720p-still-breached)
- [ ] `tests/test_obs_camera_stall.py` — covers IO-CAM-04 stall + reopen
- [ ] `tests/test_obs_camera_stale_drop.py` — covers IO-CAM-04 consumer-side stale drop
- [ ] `tests/fixtures/camera_traces.py` — canned BGR ndarray sequences + scripted-delay generators shared between tests
- [ ] Add `opencv-python>=4.10,<5.0` to `[project.dependencies]` in `pastor_tracker/pyproject.toml`
- [ ] Add `pygrabber==0.2` to `[project.dependencies]` in `pastor_tracker/pyproject.toml`
- [ ] (Optional) Add `pytest --hardware` opt-in marker for Phase 8 real-OBS smoke (not enabled now)

### Intrinsically Untestable in CI (move to Phase 8 / QA-04)
- Real OBS Studio installed + DirectShow filter registered + "Start Virtual Camera" toggled on
- Real CAP_DSHOW first-frame latency variability across Windows builds
- Real 1080p → 720p fallback under genuine GPU contention
- Real OBS scene-transition glitch behavior (drops one frame? black frame? stale frame?)
- Real signed/unsigned DirectShow filter visibility differences

## FakeVideoSource design — scripted ndarray generator

```python
# tests/fixtures/camera_traces.py
import collections
import threading
import time
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import numpy.typing as npt

@dataclass(frozen=True)
class _ScriptedFrame:
    """One scripted output of FakeVideoSource.read()."""

    bgr: npt.NDArray[np.uint8] | None
    ok: bool
    delay_sec: float                # how long read() blocks before returning


class FakeVideoSource:
    """Bidirectional in-memory fake VideoSource.

    Tests pre-load a script of (ok, ndarray, delay) triples; read() pops the
    next one, sleeps for `delay`, and returns. After exhaustion, returns
    (False, None) until close.

    Mirrors FakeSerialTransport from Phase 2 in shape and threading model.
    """

    def __init__(
        self,
        script: Iterable[_ScriptedFrame],
        *,
        width: int,
        height: int,
    ) -> None:
        self._script: collections.deque[_ScriptedFrame] = collections.deque(script)
        self._opened = True
        self._lock = threading.Lock()
        self._width = width
        self._height = height
        self.set_resolution_calls: list[tuple[int, int, int]] = []   # for fallback assertions
        self.release_calls: int = 0

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        with self._lock:
            if not self._script:
                return False, None
            scripted = self._script.popleft()
        if scripted.delay_sec > 0:
            time.sleep(scripted.delay_sec)   # OK in test code; never in app code
        return scripted.ok, scripted.bgr

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        self._width, self._height = width, height
        self.set_resolution_calls.append((width, height, fps))

    def is_opened(self) -> bool:
        return self._opened

    def release(self) -> None:
        self._opened = False
        self.release_calls += 1


def make_solid_bgr(width: int, height: int, color: tuple[int, int, int]) -> npt.NDArray[np.uint8]:
    """Allocate a fresh BGR uint8 frame; matches cv2's per-read allocation contract."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame
```

Test patterns enabled by this fake:
- **Scripted stalls:** insert a `_ScriptedFrame(bgr=None, ok=False, delay_sec=0.25)` to simulate a 250 ms stall (> 200 ms threshold).
- **Scripted resolution mismatch:** insert frames whose `bgr.shape` disagrees with `width`/`height`; assert `Frame.__post_init__` raises and the orchestrator surfaces it correctly.
- **Scripted slow grabs in warmup:** prepend 30 frames at `delay_sec=0.040` (40 ms — above the 33.3 ms × 1.2 = 40 ms budget) to trigger fallback.
- **Scripted post-warmup slow grabs:** prepend 60 fast frames (0.030 s delay) then 30 slow frames (0.050 s); assert no fallback occurs.

## Lifecycle sketches

### `start()` — synchronous boot followed by thread spawn

```python
async def start(self) -> None:
    """Discover device → open VideoSource → spawn capture thread → await first frame."""
    if self._state is not _CamState.DISCONNECTED:
        raise CameraError(f"start() called twice (state={self._state.value})")
    self._loop = asyncio.get_running_loop()
    self._state = _CamState.OPENING
    # Step 1: Discovery (boot-only, one-shot).
    self._device_index = discover_obs_camera_index(
        self._config.obs_camera_name,
        factory=self._filter_graph_factory,
        logger=self._logger,
    )
    # Step 2: Open VideoSource at target resolution.
    self._current_width = self._config.capture_width
    self._current_height = self._config.capture_height
    self._source = self._video_source_factory(
        self._device_index,
        self._current_width,
        self._current_height,
        self._config.capture_fps,
    )
    # Step 3: First-frame wait. Tiger-style: hard-fail with hint about OBS.
    if not await asyncio.to_thread(self._wait_first_frame_blocking):
        self._source.release()
        raise CameraOpenError(
            "first frame timeout — is OBS running and 'Start Virtual Camera' toggled on?"
        )
    self._state = _CamState.RUNNING
    # Step 4: Spawn capture thread.
    self._capture_thread = threading.Thread(
        target=self._capture_loop, name="obs-camera-capture", daemon=True
    )
    self._capture_thread.start()
    self._logger.info(
        "camera_started",
        device_index=self._device_index,
        width=self._current_width,
        height=self._current_height,
        fps=self._config.capture_fps,
    )
```

### `stop()` — bounded shutdown (mirror Phase 2)

```python
async def stop(self) -> None:
    """Signal stop event, join capture thread with timeout, release source."""
    self._stop_event.set()
    capture_thread = self._capture_thread
    if capture_thread is not None:
        await asyncio.to_thread(capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC)
        self._logger.info(
            "camera_thread_exited",
            clean=not capture_thread.is_alive(),
        )
        self._capture_thread = None
    if self._source is not None:
        self._source.release()
        self._source = None
    self._state = _CamState.CLOSED
```

## Logging & Errors

### structlog event names (consistent with Phase 1 / 2 conventions)

```python
log = structlog.get_logger(module="obs_camera")
```

### Event catalog

| Event name | Level | Bound keys | When |
|------------|-------|------------|------|
| `camera_discovered` | INFO | `index`, `name`, `available_count` | Successful enumeration with single match |
| `camera_multiple_matches` | INFO | `chosen_index`, `all_indexes`, `name` | > 1 friendly-name match; first chosen |
| `camera_started` | INFO | `device_index`, `width`, `height`, `fps` | After first frame received |
| `camera_thread_exited` | INFO | `clean` (bool) | Capture thread `run()` returned |
| `camera_first_frame_timeout` | ERROR | `timeout_sec`, `device_index` | First frame not received within window — pre-abort |
| `camera_resolution_fallback` | WARN | `from`, `to`, `p95_ms`, `budget_ms`, `device_index` | One-shot 1080p → 720p inside warmup |
| `camera_resolution_breach_at_720p` | ERROR | `p95_ms`, `budget_ms` | Already at 720p, budget still breached |
| `camera_stall_detected` | WARN | `delta_ms`, `threshold_ms` | Per-grab delta > 200 ms |
| `camera_reopen_attempt_failed` | WARN | `attempt`, `backoff_ms`, `reason` | One reopen attempt failed |
| `camera_reopen_succeeded` | WARN | `attempt`, `backoff_ms` | Reopen succeeded after stall |
| `camera_stall_unrecoverable` | ERROR | `attempts`, `reopen_history` | 3 reopen failures → CameraStallError |
| `frame_stale_dropped` | WARN | `age_ms`, `threshold_ms` | Consumer dropped frame > 100 ms old |
| `frames_queue_full` | WARN | `dropped_timestamp_ns`, `queue_max` | Queue at 64; drop-oldest |

### Error class hierarchy

```python
class CameraError(Exception):
    """Root for all obs_camera-originated errors."""

class OBSCameraNotFoundError(CameraError):
    """OBS VCam friendly-name not found in DirectShow enumeration. — IO-CAM-02"""

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(
            f"expected camera_name={expected!r}, available={available}"
        )
        self.expected: str = expected
        self.available: list[str] = available

class CameraOpenError(CameraError):
    """VideoSource opened but no first frame within timeout. — IO-CAM-01

    Distinct from OBSCameraNotFoundError: the device IS enumerated; the most
    likely cause is OBS running but with 'Start Virtual Camera' not toggled.
    """

class CameraStallError(CameraError):
    """Steady-state stall: > 200 ms inter-grab delta, 3 reopen attempts failed. — IO-CAM-04"""

    def __init__(self, *, attempts: list[tuple[int, int, str]]) -> None:
        super().__init__(f"camera stall unrecoverable after {len(attempts)} attempts")
        self.attempts: list[tuple[int, int, str]] = attempts  # (attempt_idx, backoff_ms, reason)
```

These mirror Phase 2's `ArduinoError` root + 5 typed subclasses pattern. Single root means `pipeline.py` (Phase 6) can `except CameraError` to halt all camera-related faults uniformly while still allowing typed branches in tests.

## Constants table — what every magic number costs

Every literal called out below MUST be a module-level `Final` constant in `obs_camera.py`. Compliance with CLAUDE.md rule 6.

| Constant | Value | Source / Justification |
|----------|-------|------------------------|
| `_FRAMES_QUEUE_MAX_SIZE` | 64 | Sized above — ~380 MiB ceiling, 2.13 s buffer |
| `_FIRST_FRAME_TIMEOUT_SEC` | 3.0 | CAP_DSHOW first-frame latency [CITED: OpenCV forum] |
| `_CAPTURE_JOIN_TIMEOUT_SEC` | 1.0 | Mirrors Phase 2 `_RX_JOIN_TIMEOUT_SEC` |
| `_STALL_THRESHOLD_NS` | 200_000_000 | IO-CAM-04 spec |
| `_STALE_FRAME_MAX_AGE_NS` | 100_000_000 | IO-CAM-04 spec |
| `_REOPEN_BACKOFFS_MS` | (200, 500, 1000) | CONTEXT.md Area 4 lock |
| `_WARMUP_WINDOW_SEC` | 2.0 | CONTEXT.md Area 3 lock |
| `_BUDGET_MULTIPLIER` | 1.2 | CONTEXT.md Area 3 lock |
| `_BUDGET_PERSIST_NS` | 1_000_000_000 | CONTEXT.md Area 3 — 1 s persistence |
| `_P95_WINDOW_SIZE` | 30 | CONTEXT.md Area 3 — 30-frame window (1 s @ 30 fps) |
| `_P95_INDEX` | 28 | int(0.95 * 30) - 1 |
| `_FALLBACK_WIDTH` | 1280 | IO-CAM-03 spec |
| `_FALLBACK_HEIGHT` | 720 | IO-CAM-03 spec |
| `_NS_PER_MS` | 1_000_000 | unit conversion |

## Reference Projects

[ASSUMED — not used as load-bearing]

- **`bunkahle/pygrabber`** — primary library for this phase. Source confirms `FilterGraph().get_input_devices()` returns a Python `list[str]` of friendly names in `ICreateDevEnum` order; `add_input_device(idx)` uses the same ordering. Maintenance status "inactive" but Win32 COM API surface is stable; no open issues blocking our use case.
- **`andreaschiavinato/python_grabber`** — fork of pygrabber; same API. Demonstrates that the underlying COM enumeration is widely used.
- **OpenCV `samples/python/video.py`** — canonical CAP_DSHOW + property-set + read loop reference; the order of `cap.set()` calls used in `OpenCvVideoSource._apply_props` matches.

These are pointers, not deps — the phase still uses `opencv-python` + `pygrabber` directly.

## Project Constraints (from CLAUDE.md)

- Python 3.12 + `uv` + `ruff` + `mypy --strict` (`disallow_any_explicit`) — no `Any`
- `mypy.overrides` already pins `cv2.*` and `pygrabber.*` to `ignore_missing_imports = true` (`pyproject.toml:82`) — we are clear to import without stub packages
- `pydantic v2 frozen=True` for DTOs; `dataclass(frozen=True, slots=True)` for `Frame` (already established Phase 1)
- `structlog` JSON only; `print()` forbidden (ruff `T20`)
- Bare `except` / `except Exception: pass` forbidden (ruff `BLE001` / `E722`); the documented `# noqa: BLE001` translator comment from Phase 2 is the only allowed pattern
- `time.sleep()` in main loop forbidden — use `asyncio.sleep` in async code; `Event.wait(timeout)` is the threading-side equivalent
- No magic numbers — all tunables in `Config` or named module constants (table above)
- ≤ 2-level conditional nesting; guard clauses + early returns
- PEP8 strict naming; descriptive identifiers (no `cx`, `w`, `h` — use `subject_center_x_normalized`, `width`, `height`)
- Type hints everywhere; `Literal` / `NewType` / `TypeAlias` to sharpen contracts
- One Conventional Commit per logical change
- Forbidden: PID, MediaPipe, EMA, mocked Kalman/damping (none directly apply but the "test real implementations" ethic carries over — no `unittest.mock.patch("cv2.VideoCapture")`)
- ruff `S` (bandit), `PLR2004` (magic-value), `ANN`, `RUF` — Phase 3 must pass all

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All | ✓ | per Phase 1 venv | — |
| `opencv-python` | IO-CAM-01, capture | ✗ in venv | — | Add `>=4.10,<5.0` to `[project.dependencies]` Wave 0 |
| `pygrabber` | IO-CAM-01, discovery | ✗ in venv | — | Add `==0.2` to `[project.dependencies]` Wave 0 |
| `numpy` | Frame buffer | ✓ | 2.4 (per Phase 1) | — |
| `structlog` | Logging | ✓ | per Phase 1 | — |
| `pytest`, `pytest-asyncio`, `pytest-cov` | Tests | ✓ | per Phase 2 | — |
| Real OBS Studio installed | QA-04 only | ✓ on dev box (assumption) | — | — (deferred to Phase 8) |
| Real "Start Virtual Camera" toggle | QA-04 only | ✓ user-toggled | — | — (deferred to Phase 8) |

**Missing dependencies with no fallback:** none (CI runs purely on `FakeVideoSource`).

**Missing dependencies with fallback:**
- `opencv-python>=4.10,<5.0` — add to `[project.dependencies]` (production runtime dep)
- `pygrabber==0.2` — add to `[project.dependencies]` (production runtime dep)

Verified: `pyproject.toml` already declares `mypy.overrides` for `cv2.*` and `pygrabber.*` (lines 81-83) — Phase 1 anticipated these imports.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `cv2.VideoCapture(idx)` no backend hint | `cv2.VideoCapture(idx, cv2.CAP_DSHOW)` explicit | OpenCV 4.x stabilized backends | Reproducible Windows behavior; ~3 s open vs 25 s for MSMF |
| EMA smoothing of FPS | Rolling p95 over fixed window | always preferred for jitter detection | Tail-aware; survives single-frame anomalies |
| `unittest.mock.patch("cv2.VideoCapture")` | DI `Protocol` + `FakeVideoSource` | 2023+ industry shift | Decouples test from cv2 internals; matches Phase 2 fake idiom |
| `time.time()` for frame timestamps | `time.perf_counter_ns()` | Python 3.7+ standard | Monotonic; ns-precision; immune to wall-clock slew |
| MediaPipe-driven pipelines | OpenCV grab → YOLO11-pose | 2026 stack choice | Phase 3 is just the grab step; perception is Phase 4 |

**Deprecated/outdated:**
- `cv2.CAP_MSMF` on Windows — slow open, slow first-frame, no upside. Banned for this project.
- `cv2.VideoCapture(idx)` without backend hint — non-deterministic across OpenCV minor versions.
- `numpy.ndarray` without dtype/shape annotations — caught by mypy `disallow_any_explicit`; we use `npt.NDArray[np.uint8]` consistently with Phase 1.

## Security Domain

### Applicable ASVS Categories (Level 1)

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | Local DirectShow filter; no auth surface |
| V3 Session Management | no | No sessions |
| V4 Access Control | no | Desktop app, single user |
| V5 Input Validation | yes | Every captured ndarray validated against `Frame.__post_init__` (BGR uint8, HxWx3, w/h match); resolution mismatch raises `ValueError` |
| V6 Cryptography | no | No crypto needed |
| V7 Error Handling & Logging | yes | `structlog` JSON; no `print()`; `CameraError` hierarchy never silently swallowed |
| V8 Data Protection | no | No PII in frame buffers (just live video); no persistence |
| V12 Files and Resources | no | No file upload surface; no disk writes |
| V13 API and Web Services | no | No HTTP |
| V14 Configuration | yes | `Config` is frozen, range-validated at startup; no live reconfig |

### Known Threat Patterns for `cv2 + pygrabber + asyncio`

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious DirectShow filter spoofing OBS friendly name | Spoofing | Exact-name match is the gate; if a hostile filter registers with the exact same friendly name, the user has bigger problems (admin install required to register a filter) |
| Buffer-allocation DoS via gigantic frame dimensions | Tampering | `Config.capture_width <= 7680`, `capture_height <= 4320` already validated by Pydantic; `Frame.__post_init__` re-checks; cv2 itself caps at sensor max |
| Frame buffer poisoning (negative dims) | Tampering | numpy ndarray shape is unsigned by construction; cv2 cannot return negative dims |
| Malformed BGR layout (non-3-channel) from rogue filter | Tampering | `Frame.__post_init__` rejects ndim != 3 OR channels != 3; raises `ValueError` |
| Capture-thread starvation under GIL contention | DoS | Single capture thread; OS schedules independent of GIL during cv2's C-side blocking calls |
| Queue exhaustion (slow consumer) | DoS | Bounded queue (64) + drop-oldest + WARN |
| Closing source while thread mid-`read()` | DoS | Stop event first → join thread → release source (Pitfall 7) |
| Discovery COM init race in tests | DoS (test) | Factory closure; tests never construct real `FilterGraph` (Pitfall 10) |

The threat surface is small because video capture is local-only and the firmware/OS already validates DirectShow filter signing. Largest real risk: a buggy DirectShow filter producing malformed ndarrays that crash `Frame.__post_init__`. Mitigation: orchestrator catches the `ValueError`, logs ERROR, and treats it as a stall (next reopen attempt may succeed if it was transient).

## Open Questions (RESOLVED)

1. **Should `Frame` validation failure inside the capture thread be a stall trigger, or fatal?**
   - **RESOLVED**: Treat as stall — capture thread catches `ValueError` and routes through `_attempt_reopen` (3-strike eviction path); 3 consecutive shape mismatches surface `CameraStallError`.
   - What we know: `Frame.__post_init__` raises `ValueError` on shape mismatch; happens inside the capture thread.
   - What's unclear: Does a one-off shape mismatch (e.g., DirectShow renegotiating mid-stream) warrant a reopen, or is it always a sign of a broken filter that won't recover?
   - Recommendation: Treat as stall (continue with reopen). 3 consecutive shape mismatches → `CameraStallError` (path already exists). Add an INFO log `frame_shape_mismatch` distinct from the stall log.

2. **Does pygrabber's `get_input_devices()` return non-deterministic order across boots?**
   - **RESOLVED**: Don't assume index stability — re-enumerate on every `start()`; cache only for the `ObsCamera` instance lifetime.
   - What we know: It uses `ICreateDevEnum`; order depends on Windows enumeration which is FIFO-by-registration in practice.
   - What's unclear: If the user installs a new camera between two app launches, does OBS VCam's index stay stable?
   - Recommendation: Don't assume index stability; ALWAYS re-enumerate on every boot (one-shot at `start()`). Cache the index only for the lifetime of the `ObsCamera` instance.

3. **Should resolution-actually-applied disagree with config trigger an ERROR?**
   - **RESOLVED**: No ERROR — trust actual `bgr.shape`, propagate to `Frame.width`/`height`, emit single INFO `resolution_actual_differs` event at `start()` if mismatch.
   - What we know: Pitfall 3 — `cap.set()` may silently snap to a different supported resolution.
   - What's unclear: If we ask for 1080p and get 1280×720 (camera's actual max), is that a bug or a feature?
   - Recommendation: Trust the actual `bgr.shape` and propagate to `Frame.width`/`height`. Log a single INFO `resolution_actual_differs` event at `start()` if it differs. Operator can decide via dashboard. Don't ERROR.

4. **Heartbeat pattern from Phase 2 — does Phase 3 need a "camera health" tick?**
   - **RESOLVED**: No new heartbeat — frame arrival rate IS the liveness signal; dashboard derives staleness from `cam.last_frame_age_ns` (Phase 7 concern, not Phase 3).
   - What we know: Phase 2 has a 200 ms heartbeat for the firmware watchdog; Phase 3 has no analog because OBS doesn't watchdog the consumer.
   - What's unclear: Does the dashboard (Phase 7) need a "camera is alive" signal beyond just frame arrival rate?
   - Recommendation: NO new heartbeat. The frame arrival rate IS the liveness signal. Dashboard reads `cam.last_frame_age_ns` (a property derived from the most recent stamp) and renders "stale" if > 200 ms. Phase 7 concern.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `opencv-python>=4.10,<5.0` is the appropriate version pin | Standard Stack | Low — verify against PyPI at task time; opencv-python 4.x is the long-stable line |
| A2 | `pygrabber==0.2` is the only published version on PyPI as of 2026-05 | Standard Stack | Low — verified via PyPI search; if a 0.3 ships before task execution, re-evaluate |
| A3 | pygrabber's `FilterGraph().get_input_devices()` index matches `cv2.VideoCapture(idx, CAP_DSHOW)` index | Pitfall 1, Discovery | Medium — both wrap `ICreateDevEnum` but historic mismatches exist; the discovery test must verify this on the dev box during Wave 0 |
| A4 | `_FIRST_FRAME_TIMEOUT_SEC = 3.0` accommodates real-world CAP_DSHOW startup | Pitfall 8, Constants table | Low — based on OpenCV forum reports of 1–3 s latency; if dev-box test trips it, raise to 5.0 |
| A5 | Reference Projects section (pygrabber, python_grabber, OpenCV samples) — not load-bearing | Reference Projects | Low — section labeled `[ASSUMED]`; not used as evidence for any decision |
| A6 | OBS Studio installer registers DirectShow filter system-wide; admin not required at runtime | Pitfall 2 | Low — verified via OBS docs; admin only required at install time |
| A7 | `cv2` returns a fresh ndarray per `read()` call (no buffer reuse) | Pitfall 6 | Medium — verified via cv2 Python bindings source; if a future cv2 introduces buffer pooling, our drop-oldest queue logic still holds because each ndarray view is owned by its enqueued `Frame` |

All other claims in this RESEARCH.md are `[VERIFIED]` from the existing codebase (`config.py`, `core/types.py`, Phase 2 modules, `pyproject.toml`) or `[CITED]` from primary sources (OpenCV GitHub issues, OBS Studio GitHub issues, OpenCV documentation, pygrabber source).

## Sources

### Primary (HIGH confidence)
- `D:\System\Documents\PastorTrackingSystem\.planning\phases\03-camera-i-o\03-CONTEXT.md` — locked decisions (this Phase's user intent record)
- `D:\System\Documents\PastorTrackingSystem\.planning\phases\02-arduino-i-o\02-RESEARCH.md` — Phase 2 RESEARCH used as structural template
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\io\arduino_motor.py` — Phase 2 dirty-edge pattern (RX thread, queue bridge, lifecycle, error hierarchy) — VERBATIM port for capture thread
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\io\arduino_transport.py` — Phase 2 transport seam pattern (Protocol + production + fake)
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\core\types.py` — `Frame` DTO contract authoritative
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\config.py` — `obs_camera_name`, `capture_width`, `capture_height`, `capture_fps`, `camera_horizontal_fov_deg` field defs verified
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\pyproject.toml` — verified mypy `cv2.*` / `pygrabber.*` overrides at lines 81-83
- `D:\System\Documents\PastorTrackingSystem\.planning\REQUIREMENTS.md` — IO-CAM-01..04 wording
- `D:\System\Documents\PastorTrackingSystem\.planning\PROJECT.md` — camera stack pinned to opencv-python + DirectShow + pygrabber

### Secondary (MEDIUM confidence — verified via web)
- [OpenCV Issue #19746 — DirectShow VideoCapture returns blank frames for OBS Virtual Camera on Windows](https://github.com/opencv/opencv/issues/19746) — confirms OBS VCam compatibility quirks
- [OpenCV Issue #17687 — Camera is very slow to open when using the MSMF VideoCapture backend](https://github.com/opencv/opencv/issues/17687) — confirms CAP_DSHOW is the right backend choice
- [OpenCV Issue #23533 — opencv4 VideoCapture set width/height fail](https://github.com/opencv/opencv/issues/23533) — confirms in-place `cap.set()` quirks; justifies release+reopen for fallback
- [OpenCV Issue #27917 — MSMF Backend slow initial frame acquisition](https://github.com/opencv/opencv/issues/27917) — secondary confirmation of MSMF inadequacy
- [OBS Studio Issue #8057 — Virtual Camera output on Windows / DirectShow only delivers one initial frame](https://github.com/obsproject/obs-studio/issues/8057) — confirms OBS VCam state-dependent read behavior
- [bunkahle/pygrabber GitHub README](https://github.com/bunkahle/pygrabber) — confirms `FilterGraph().get_input_devices()` returns ordered device list
- [andreaschiavinato/python_grabber `dshow_graph.py`](https://github.com/andreaschiavinato/python_grabber/blob/master/pygrabber/dshow_graph.py) — fork showing same API; secondary verification
- [OpenCV docs — VideoCapture flags](https://docs.opencv.org/3.4/d4/d15/group__videoio__flags__base.html) — confirms property-set behavior is "depending on device hardware, driver and API backend"
- [Kurokesu — Pulling full resolution from a webcam with OpenCV (Windows)](https://www.kurokesu.com/main/2020/07/12/pulling-full-resolution-from-a-webcam-with-opencv-windows/) — practical guide; confirms FOURCC-then-WHF order
- [OpenCV forum — Slow camera initialization](https://forum.opencv.org/t/slow-camera-initialization/12179) — confirms 1–3 s CAP_DSHOW first-frame latency
- [Snyk Advisor — pygrabber package health](https://snyk.io/advisor/python/pygrabber) — confirms 0.2 is current; project status "Inactive"

### Tertiary (LOW confidence — assumed, marked)
- Reference Projects section — `[ASSUMED]`; not used as load-bearing evidence

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — opencv-python and pygrabber are the libraries PROJECT.md pins; no alternative considered
- Architecture & Concurrency: HIGH — pattern is a verbatim port of Phase 2's RX-thread + asyncio.Queue idiom, which already shipped and tested
- Discovery & First-Frame Wait: MEDIUM — pygrabber index ↔ cv2 index correspondence is well-documented in pygrabber source but Wave 0 dev-box test must verify on real hardware (Pitfall 1 is the gate)
- Resolution Fallback: HIGH — p95 detector is straightforward stdlib; release+reopen is the documented OpenCV workaround for `cap.set()` flakiness
- Stall Recovery: HIGH — same shape as Phase 2 watchdog recovery; backoffs are spec-locked in CONTEXT.md
- Stale-Frame Drop: HIGH — consumer-side check is one-line; no edge cases
- Test Strategy: HIGH — `FakeVideoSource` mirrors `FakeSerialTransport` from Phase 2; same DI seam approach
- Logging & Errors: HIGH — names and class hierarchy follow Phase 1/2 conventions
- Security Domain: HIGH — local-only USB capture has tiny attack surface; ASVS controls map cleanly

**Research date:** 2026-05-04
**Valid until:** 2026-06-04 (30 days — opencv-python 4.x and pygrabber 0.2 are stable; only the Phase 3 plan itself drives expiry)
