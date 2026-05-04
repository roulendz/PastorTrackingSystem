# Phase 3: Camera I/O - Pattern Map

**Mapped:** 2026-05-05
**Files analyzed:** 5 (1 source + 3 test + 1 fixture + 1 build config)
**Analogs found:** 5 / 5 (Phase 2 establishes the dirty-edge dedicated-thread + asyncio.Queue + Protocol-DI idiom; Phase 3 is a near-verbatim port with one novel piece — the rolling-p95 detector — for which RESEARCH.md `Pattern 3` is the spec.)

## File Classification

| New / Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------------|------|-----------|----------------|---------------|
| `pastor_tracker/src/pastor_tracker/io/obs_camera.py` | service (orchestrator) + transport seam (Protocol + impls) + utility (`discover_obs_camera_index`) | streaming (frame producer, dedicated-thread → asyncio.Queue) | `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (orchestrator + RX thread + queue bridge + lifecycle + error hierarchy) AND `pastor_tracker/src/pastor_tracker/io/arduino_transport.py` (Protocol + Real impl + Fake + `discover_*`) | exact (verbatim port of Phase 2 idiom; Frame DTO already exists in `core/types.py`) |
| `pastor_tracker/tests/test_obs_camera.py` (or split into `_discovery.py` / `_lifecycle.py` / `_fallback.py` / `_stall.py` / `_stale_drop.py` per RESEARCH.md) | test (orchestrator + discovery, FakeVideoSource-driven) | n/a | `pastor_tracker/tests/test_arduino_transport.py` (monkeypatch + `SimpleNamespace` stubs + `structlog.testing.capture_logs` for log assertions) AND `pastor_tracker/tests/test_arduino_motor_handshake.py` (async + valid_config_dict + Fake transport + try/finally close) | exact |
| `pastor_tracker/tests/fakes/fake_video_source.py` (or `tests/fixtures/fake_video_source.py` — see Note A) | fixture (zero-deps Fake DI seam) | n/a | `pastor_tracker/src/pastor_tracker/io/arduino_transport.py:FakeSerialTransport` (in-tree fake, `threading.Lock`, scripted feed) | exact |
| `pastor_tracker/tests/fixtures/camera_traces.py` (helper module per RESEARCH.md) | fixture (canned ndarray sequences + scripted-delay generators + `_started_camera()` helper + `wait_for_state` poller) | n/a | `pastor_tracker/tests/fixtures/arduino_traces.py` (canned bytes + `_started_motor` async helper + `wait_for_state` poller) | exact |
| `pastor_tracker/pyproject.toml` (modify) | config | n/a | itself (Phase 2 added `pyserial>=3.5,<4.0` to `[project.dependencies]`; mypy `cv2.*` and `pygrabber.*` overrides at line 82 already shipped in Phase 1) | exact (self) |

> **Note A on test layout.** RESEARCH.md `Recommended Project Structure` (lines 218–235) splits into 5 test files under `tests/`, with shared `tests/fixtures/camera_traces.py`. This mirrors Phase 2's split (one test file per concern). The orchestrator's prompt mentions a single `tests/test_obs_camera.py` and `tests/fakes/fake_video_source.py`; reconcile by following the Phase 2 precedent: **split per concern** (`test_obs_camera_discovery.py`, `_lifecycle.py`, `_fallback.py`, `_stall.py`, `_stale_drop.py`) and place the fake in `tests/fixtures/camera_traces.py` alongside the canned ndarray helpers (Phase 2 path is `tests/fixtures/arduino_traces.py`, not `tests/fakes/`). The planner should pin the path; pattern-extraction is identical either way. RESEARCH.md is the spec; the prompt's `tests/fakes/` is illustrative.

---

## Pattern Assignments

### `pastor_tracker/src/pastor_tracker/io/obs_camera.py` (orchestrator + transport seam + discovery)

**Primary analog:** `pastor_tracker/src/pastor_tracker/io/arduino_motor.py`
**Secondary analog:** `pastor_tracker/src/pastor_tracker/io/arduino_transport.py`

The single `obs_camera.py` file collapses what Phase 2 split into three modules (transport / protocol / orchestrator) because there is no pure-parser layer to isolate (cv2 does the byte work; pygrabber does enumeration). Three internal sections per RESEARCH.md `Summary`:

1. §1 `VideoSource` Protocol + `OpenCvVideoSource` real impl + (Fake lives in tests).
2. §2 `discover_obs_camera_index()` + `OBSCameraNotFoundError`.
3. §3 `ObsCamera` orchestrator (`start` / `stop` / `frames` / capture thread / fallback / stall recovery) + `CameraError` hierarchy.

#### Module docstring + `__future__` import pattern

**Source:** `arduino_motor.py:1-29` and `arduino_transport.py:1-29` (both Phase 2 dirty-edge modules)

Both Phase 2 modules open with a multi-paragraph docstring that names: (a) what the module owns, (b) concurrency invariants enumerated as bullets, (c) tiger-style invariants enumerated as bullets, (d) the design split vs sibling modules. Mirror this. Concretely from `arduino_motor.py:9-28`:

```python
"""Asyncio orchestrator for the Arduino stepper firmware (v2).

Bridges the pure parser (arduino_protocol.py) and the transport
(arduino_transport.py) into a working motor link. Owns the only
threading <-> asyncio bridge in the project: a daemon RX thread reads
bytes, parses to ProtocolEvent, then crosses into the asyncio loop
via ``loop.call_soon_threadsafe`` to enqueue a bounded queue.

Concurrency invariants (CLAUDE.md rule 1, RESEARCH.md "Architecture
& Concurrency"):
    * Single writer: ...
    * Single reader: ...
    * Bounded RX queue: ``maxsize=256``, drop-oldest + WARN log on full.
    ...
"""
from __future__ import annotations
```

Apply with Phase 3-specific bullets (pulled from `03-CONTEXT.md` Decisions and `03-RESEARCH.md` System Architecture invariants):
- Single producer = capture thread; single consumer = asyncio loop.
- Bounded `asyncio.Queue(maxsize=64)` (RESEARCH.md `Sizing the bounded queue`).
- Boot-only one-shot DirectShow enumeration; no background re-poll.
- Stall detection at the capture thread; stale-drop at the consumer.
- One-shot resolution fallback gated to 2 s warmup window; never re-promote.

#### Imports pattern

**Source:** `arduino_motor.py:29-69`

```python
from __future__ import annotations

import asyncio
import contextlib
import enum
import math
import threading
from collections.abc import AsyncIterator
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_protocol import (
    FEEDBACK_SEQ_GAP_WARN_THRESHOLD,
    ...
)
from pastor_tracker.io.arduino_transport import (
    ArduinoPortNotFoundError,
    SerialTransport,
)
```

Apply for `obs_camera.py`:

```python
from __future__ import annotations

import asyncio
import collections
import contextlib
import enum
import threading
import time
from collections.abc import AsyncIterator
from typing import Callable, Final, Protocol, runtime_checkable

import cv2  # opencv-python — typed via mypy override (pyproject.toml:82)
import numpy as np
import numpy.typing as npt
import structlog
from pygrabber.dshow_graph import FilterGraph  # typed via mypy override

from pastor_tracker.config import Config
from pastor_tracker.core.types import Frame   # already shipped in Phase 1
```

Note: `mypy --strict disallow_any_explicit` is already configured to ignore `cv2.*` and `pygrabber.*` (`pyproject.toml:81-83`) — no stub packages required.

#### Module-level `Final` constants (CLAUDE.md rule 6 — no magic numbers)

**Source:** `arduino_motor.py:86-97` and `arduino_transport.py:40-58`

`arduino_motor.py:90-97` — every value `Final[…]`, every literal cited:

```python
_TX_NEWLINE: Final[bytes] = b"\n"
_MS_PER_SEC: Final[float] = 1_000.0
_RX_QUEUE_MAX_SIZE: Final[int] = 256                 # CONTEXT.md drop-oldest bound
_RX_JOIN_TIMEOUT_SEC: Final[float] = 1.0             # close() RX-thread join budget
_RX_THREAD_READ_TIMEOUT_SEC: Final[float] = 0.1      # short tick -> responsive stop_event
_DECIMAL_DEG_PRECISION: Final[int] = 3               # Pitfall 5 -- keep TX line <= 47 bytes
```

Apply verbatim from RESEARCH.md `Constants table` (lines 891–910):

```python
# Bounded queue + memory ceiling -- 1080p frame ~= 6 MiB; 64 frames -> ~380 MiB.
_FRAMES_QUEUE_MAX_SIZE: Final[int] = 64                      # RESEARCH `Sizing the bounded queue`
_FIRST_FRAME_TIMEOUT_SEC: Final[float] = 3.0                 # CAP_DSHOW first-frame latency [CITED OpenCV forum]
_CAPTURE_JOIN_TIMEOUT_SEC: Final[float] = 1.0                # mirrors arduino_motor _RX_JOIN_TIMEOUT_SEC
_STALL_THRESHOLD_NS: Final[int] = 200_000_000                # IO-CAM-04 spec -- 200 ms inter-grab ceiling
_STALE_FRAME_MAX_AGE_NS: Final[int] = 100_000_000            # IO-CAM-04 spec -- 100 ms consumer-side drop
_REOPEN_BACKOFFS_MS: Final[tuple[int, int, int]] = (200, 500, 1000)  # CONTEXT.md Area 4 lock
_WARMUP_WINDOW_SEC: Final[float] = 2.0                       # CONTEXT.md Area 3 lock
_BUDGET_MULTIPLIER: Final[float] = 1.2                       # CONTEXT.md Area 3 lock
_BUDGET_PERSIST_NS: Final[int] = 1_000_000_000               # CONTEXT.md Area 3 -- 1 s sustained breach
_P95_WINDOW_SIZE: Final[int] = 30                            # CONTEXT.md Area 3 -- 30-frame window @ 30 fps
_P95_INDEX: Final[int] = 28                                  # int(0.95 * 30) - 1
_FALLBACK_WIDTH: Final[int] = 1280                           # IO-CAM-03 spec
_FALLBACK_HEIGHT: Final[int] = 720                           # IO-CAM-03 spec
_NS_PER_MS: Final[int] = 1_000_000                           # unit conversion
_CAPTURE_THREAD_TICK_SEC: Final[float] = 0.1                 # short responsive tick (mirror arduino_motor _RX_THREAD_READ_TIMEOUT_SEC)
```

Citation-comment style mirrors `arduino_motor.py:92-97` and `arduino_protocol.py:62-69` (where every value carries a trailing `# protocol.h:NN` or `# CONTEXT.md ...` source pointer).

#### `__all__` re-export pattern

**Source:** `arduino_motor.py:74-84`

```python
__all__ = [
    "ArduinoError",
    "ArduinoMotor",
    "ArduinoPortNotFoundError",
    "FirmwareErrorReceived",
    "HandshakeTimeoutError",
    "LinkLostError",
    "ProtocolVersionMismatchError",
    "WatchdogResetError",
    "_MotorState",
]
```

Apply for `obs_camera.py` (one orchestrator-import surface for Phase 4 / Phase 6 consumers):

```python
__all__ = [
    "CameraError",
    "CameraOpenError",
    "CameraStallError",
    "FakeVideoSource",         # if Fake lives here; otherwise drop and import in tests
    "ObsCamera",
    "OBSCameraNotFoundError",
    "OpenCvVideoSource",
    "VideoSource",
    "_CamState",
    "discover_obs_camera_index",
]
```

#### `VideoSource` Protocol pattern (DI seam)

**Source:** `arduino_transport.py:77-89`

```python
@runtime_checkable
class SerialTransport(Protocol):
    """Minimal DI surface — the only contract orchestrator code depends on.

    ``read_line`` returns the line WITHOUT trailing ``\\r``/``\\n`` so Plan
    02-01's :func:`parse_line` accepts it directly. Returns ``None`` on timeout.
    """

    def write(self, data: bytes) -> int: ...

    def read_line(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...
```

Apply (per RESEARCH.md `Pattern 1`, lines 240–258):

```python
_ImageArray = npt.NDArray[np.uint8]


@runtime_checkable
class VideoSource(Protocol):
    """Minimal DI surface -- the only contract orchestrator code depends on.

    ``read`` returns ``(False, None)`` on driver-side failure; consumers MUST
    check ``ok`` before touching ``bgr`` (Pitfall 5 in 03-RESEARCH.md).
    """

    def read(self) -> tuple[bool, _ImageArray | None]: ...

    def set_resolution(self, width: int, height: int, fps: int) -> None: ...

    def is_opened(self) -> bool: ...

    def release(self) -> None: ...
```

Critical: `runtime_checkable` + minimal-method-set + return-type-annotated, exactly as `SerialTransport` does. mypy `--strict disallow_any_explicit` will catch any `Any` regression.

#### `OpenCvVideoSource` real impl pattern

**Source:** `arduino_transport.py:92-128` (`PySerialTransport` — the real-impl shape)

```python
class PySerialTransport:
    """Real ``pyserial`` wrapper. Single owner of the OS-level serial handle.

    ``__init__`` opens the port immediately (so a missing device fails at
    construction, not at first read). Constructor failure surfaces
    :class:`serial.SerialException` un-swallowed -- tiger-style.
    """

    def __init__(self, port: str, baud: int) -> None:
        self._serial = serial.Serial(port=port, baudrate=baud, timeout=_PYSERIAL_OPEN_TIMEOUT_SEC)

    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...
    def close(self) -> None: ...
```

Apply (per RESEARCH.md `Pattern 1`, lines 261–295):

```python
class OpenCvVideoSource:
    """Real cv2.VideoCapture wrapper. Single owner of the OS-level handle.

    Constructor opens immediately + applies properties so a missing device
    fails at construction. ``set_resolution`` is the documented release+reopen
    point per RESEARCH Pitfall (cap.set() is flaky on CAP_DSHOW
    [CITED OpenCV Issue #23533]).
    """

    def __init__(self, device_index: int, width: int, height: int, fps: int) -> None:
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        self._apply_props(width, height, fps)

    def _apply_props(self, width: int, height: int, fps: int) -> None:
        # Order per kurokesu.com guide: width, height, FPS. FOURCC skipped --
        # OBS VCam advertises a single stream and DirectShow negotiates.
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        self._cap.set(cv2.CAP_PROP_FPS, float(fps))

    def read(self) -> tuple[bool, _ImageArray | None]:
        ok, frame = self._cap.read()
        return ok, frame

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        self._apply_props(width, height, fps)

    def is_opened(self) -> bool:
        return bool(self._cap.isOpened())

    def release(self) -> None:
        self._cap.release()
```

#### Discovery helper pattern (`discover_obs_camera_index`)

**Source:** `arduino_transport.py:176-233` (`discover_arduino_port`)

```python
def discover_arduino_port(configured_port: str | None) -> str:
    """VID:PID auto-detect with manual-override fallback. -- IO-ARD-01.

    Resolution order:
        1. ``configured_port`` is not None AND present in ``comports()`` ->
           use it (logs ``port_manual_override``).
        2. ``configured_port`` is not None AND absent -> raise
           :class:`ArduinoPortNotFoundError`.
        3. ``configured_port`` is None -> first ``comports()`` entry whose
           ``(vid, pid)`` is in :data:`SUPPORTED_VID_PIDS`:
           * exactly 1 match -> log ``port_discovered``.
           * multiple matches -> log ``port_multiple_matches``.
        4. None match -> raise :class:`ArduinoPortNotFoundError`.

    Tiger-style: configured-but-absent raises rather than falling back to
    auto-detect -- a silent fallback would mask developer/operator misconfig.
    """
    log = structlog.get_logger(module="arduino_transport")
    ports = list(comports())
    if configured_port is not None:
        if any(p.device == configured_port for p in ports):
            log.info("port_manual_override", port=configured_port)
            return configured_port
        raise ArduinoPortNotFoundError(
            f"arduino_port={configured_port!r} not present in serial.tools.list_ports"
        )
    matches = [p for p in ports
               if p.vid is not None and p.pid is not None
               and (p.vid, p.pid) in SUPPORTED_VID_PIDS]
    if not matches:
        raise ArduinoPortNotFoundError("no Arduino USB device matched VID:PID set")
    if len(matches) > 1:
        log.info("port_multiple_matches", chosen=matches[0].device,
                 all=[(m.device, hex(m.vid), hex(m.pid)) for m in matches])
    else:
        log.info("port_discovered", port=matches[0].device,
                 vid=hex(matches[0].vid), pid=hex(matches[0].pid))
    chosen_device: str = matches[0].device
    return chosen_device
```

Apply (per RESEARCH.md `Discovery helper` lines 472–516, with the `factory` closure for test isolation per Pitfall 10):

```python
_FilterGraphFactory = Callable[[], FilterGraph]


def discover_obs_camera_index(
    expected_name: str,
    *,
    factory: _FilterGraphFactory,
    logger: structlog.stdlib.BoundLogger,
) -> int:
    """Enumerate DirectShow devices; return index whose friendly name == expected_name.

    Tiger-style: no exact match -> raise OBSCameraNotFoundError carrying full
    device list. Multiple matches with the same name -> first hit, log all
    (mirrors arduino_transport.py multi-match rule).

    The ``factory`` closure exists for Pitfall 10 (FilterGraph ctor calls
    CoInitialize -- tests monkeypatch the factory rather than constructing
    a real FilterGraph, which avoids RPC_E_CHANGED_MODE in pytest workers).
    """
    graph = factory()
    devices: list[str] = list(graph.get_input_devices())
    matches = [(idx, name) for idx, name in enumerate(devices) if name == expected_name]
    if not matches:
        # Tiger-style mirror of arduino_transport.discover_arduino_port:
        # carry the full enumerated list so operator triage sees both sides.
        raise OBSCameraNotFoundError(expected=expected_name, available=devices)
    if len(matches) > 1:
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

Note the analog symmetry with `arduino_transport.py`:
- `port_manual_override` (manual hit) ↔ no analog (camera has no equivalent — name match is the only path).
- `port_discovered` (single VID:PID match) ↔ `camera_discovered` (single name match).
- `port_multiple_matches` (>1 VID:PID match) ↔ `camera_multiple_matches` (>1 name match).
- `ArduinoPortNotFoundError("no Arduino USB device matched VID:PID set")` ↔ `OBSCameraNotFoundError(expected=..., available=...)` — the same `f"<bound> != <observed>"` shape.

#### Error class hierarchy pattern

**Source:** `arduino_motor.py:111-149`

```python
class ArduinoError(Exception):
    """Root for all arduino_motor-originated errors."""


class HandshakeTimeoutError(ArduinoError):
    """READY:v<N> not received within arduino_ready_timeout_sec. -- IO-ARD-02."""


class ProtocolVersionMismatchError(ArduinoError):
    """READY:v<N> received but N != arduino_protocol_version. -- IO-ARD-02 / CFG-04."""


class WatchdogResetError(ArduinoError):
    """Mid-session READY:v2 recovery failed (settings/limits ack timeout). -- IO-ARD-06."""


class FirmwareErrorReceived(ArduinoError):
    """ERROR:<code> from firmware -- halts tracking. -- IO-ARD-07."""

    def __init__(self, code: ErrorCode | int, message: str) -> None:
        super().__init__(f"firmware ERROR:{int(code)} -- {message}")
        self.code: ErrorCode | int = code
        self.message: str = message


class LinkLostError(ArduinoError):
    """USB disconnect mid-session. -- IO-ARD-07 (ERROR-class halt).

    Distinct surface from :class:`FirmwareErrorReceived`: the firmware did
    not emit an ERROR; the host's serial link itself died.
    """
```

Apply (per RESEARCH.md `Error class hierarchy` lines 860–887):

```python
class CameraError(Exception):
    """Root for all obs_camera-originated errors."""


class OBSCameraNotFoundError(CameraError):
    """OBS VCam friendly-name not found in DirectShow enumeration. -- IO-CAM-02.

    Distinct from CameraOpenError: the device is not even enumerated.
    """

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(f"expected camera_name={expected!r}, available={available}")
        self.expected: str = expected
        self.available: list[str] = available


class CameraOpenError(CameraError):
    """VideoSource opened but no first frame within timeout. -- IO-CAM-01.

    Distinct from OBSCameraNotFoundError: the device IS enumerated; the most
    likely cause is OBS running but with 'Start Virtual Camera' not toggled
    [CITED OBS Studio Issue #8057, OpenCV Issue #19746].
    """


class CameraStallError(CameraError):
    """Steady-state stall: > 200 ms inter-grab delta, 3 reopen attempts failed. -- IO-CAM-04."""

    def __init__(self, *, attempts: list[tuple[int, int, str]]) -> None:
        super().__init__(f"camera stall unrecoverable after {len(attempts)} attempts")
        self.attempts: list[tuple[int, int, str]] = attempts  # (attempt_idx, backoff_ms, reason)
```

The shape `__init__(*, expected, available)` + concrete `super().__init__(f"...")` + named instance attributes is identical to `FirmwareErrorReceived(arduino_motor.py:135-138)`.

#### Internal state-machine `enum.Enum` pattern

**Source:** `arduino_motor.py:100-108`

```python
class _MotorState(enum.Enum):
    """Internal state machine for :class:`ArduinoMotor`."""

    DISCONNECTED = "disconnected"
    HANDSHAKING = "handshaking"
    RUNNING = "running"
    RECOVERING = "recovering"
    FAULTED = "faulted"
    CLOSED = "closed"
```

Apply (per CONTEXT.md status surface — `is_running`, `current_resolution`, `last_error`):

```python
class _CamState(enum.Enum):
    """Internal state machine for :class:`ObsCamera`."""

    DISCONNECTED = "disconnected"
    OPENING = "opening"           # discovery + first-frame wait
    RUNNING = "running"
    REOPENING = "reopening"       # stall recovery in progress
    FAULTED = "faulted"
    CLOSED = "closed"
```

Use `enum.Enum` (not `IntEnum`) — these are not wire values; mypy `--strict` catches comparison typos.

#### `ObsCamera.__init__` pattern (Config-as-whole + asyncio.Queue + threading.Event)

**Source:** `arduino_motor.py:170-189`

```python
def __init__(self, transport: SerialTransport, config: Config) -> None:
    self._transport: SerialTransport = transport
    self._config: Config = config
    self._tx_lock = asyncio.Lock()
    self._rx_queue: asyncio.Queue[ProtocolEvent] = asyncio.Queue(
        maxsize=_RX_QUEUE_MAX_SIZE
    )
    self._stop_event: threading.Event = threading.Event()
    self._dispatch_paused: bool = False
    self._state: _MotorState = _MotorState.DISCONNECTED
    self._heartbeat_task: asyncio.Task[None] | None = None
    self._rx_thread: threading.Thread | None = None
    self._loop: asyncio.AbstractEventLoop | None = None
    ...
    self._latched_error: ArduinoError | None = None
    self._logger = structlog.get_logger(module="arduino_motor")
```

Apply:

```python
def __init__(
    self,
    config: Config,
    *,
    video_source_factory: Callable[[int, int, int, int], VideoSource],
    filter_graph_factory: _FilterGraphFactory,
) -> None:
    self._config: Config = config
    self._video_source_factory = video_source_factory
    self._filter_graph_factory = filter_graph_factory
    self._frames_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=_FRAMES_QUEUE_MAX_SIZE)
    self._stop_event: threading.Event = threading.Event()
    self._state: _CamState = _CamState.DISCONNECTED
    self._capture_thread: threading.Thread | None = None
    self._loop: asyncio.AbstractEventLoop | None = None
    self._source: VideoSource | None = None
    self._device_index: int | None = None
    self._current_width: int = config.capture_width
    self._current_height: int = config.capture_height
    self._reopen_history: list[tuple[int, int, str]] = []
    self._latched_error: CameraError | None = None
    self._fallback_consumed: bool = False                # one-shot flag
    self._first_frame_event: threading.Event = threading.Event()  # first-frame gate
    self._logger = structlog.get_logger(module="obs_camera")
```

Note the **two-factory injection pattern**: `video_source_factory` allows tests to wire `FakeVideoSource`; `filter_graph_factory` defends Pitfall 10 (CoInitialize race in pytest workers). Production wires `lambda idx, w, h, fps: OpenCvVideoSource(idx, w, h, fps)` and `lambda: FilterGraph()`.

#### `start()` lifecycle pattern (synchronous boot + thread spawn)

**Source:** `arduino_motor.py:209-245`

```python
async def start(self) -> None:
    """Run boot handshake then spawn RX thread + heartbeat task."""
    if self._state is not _MotorState.DISCONNECTED:
        raise ArduinoError(
            f"start() called twice (state={self._state.value})"
        )
    self._loop = asyncio.get_running_loop()
    self._state = _MotorState.HANDSHAKING
    self._logger.info("handshake_started", ...)
    start_time = self._loop.time()
    await self._wait_for_ready()
    elapsed_ms = (self._loop.time() - start_time) * _MS_PER_SEC
    self._logger.info("handshake_complete", ...)
    self._state = _MotorState.RUNNING
    ...
    self._rx_thread = threading.Thread(
        target=self._rx_loop, name="arduino-rx", daemon=True
    )
    self._rx_thread.start()
    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
    self._heartbeat_task.add_done_callback(self._on_task_done)
    self._logger.info("heartbeat_started", ...)
```

Apply (per RESEARCH.md `start()` sketch lines 770–810):

```python
async def start(self) -> None:
    """Discover -> open -> spawn capture thread -> await first frame."""
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
    self._source = self._video_source_factory(
        self._device_index,
        self._current_width,
        self._current_height,
        self._config.capture_fps,
    )
    # Step 3: Spawn capture thread BEFORE first-frame wait so the thread
    # populates the queue/event we await below.
    self._capture_thread = threading.Thread(
        target=self._capture_loop, name="obs-camera-capture", daemon=True
    )
    self._capture_thread.start()
    # Step 4: First-frame wait. Tiger-style: hard-fail with hint about OBS.
    first_frame_arrived = await asyncio.to_thread(
        self._first_frame_event.wait, _FIRST_FRAME_TIMEOUT_SEC
    )
    if not first_frame_arrived:
        self._stop_event.set()
        if self._capture_thread is not None:
            await asyncio.to_thread(self._capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC)
        if self._source is not None:
            self._source.release()
        self._logger.error(
            "camera_first_frame_timeout",
            timeout_sec=_FIRST_FRAME_TIMEOUT_SEC,
            device_index=self._device_index,
        )
        raise CameraOpenError(
            "first frame timeout -- is OBS running and 'Start Virtual Camera' toggled on?"
        )
    self._state = _CamState.RUNNING
    self._logger.info(
        "camera_started",
        device_index=self._device_index,
        width=self._current_width,
        height=self._current_height,
        fps=self._config.capture_fps,
    )
```

#### `stop()` lifecycle pattern (bounded shutdown order)

**Source:** `arduino_motor.py:247-283`

```python
async def close(self) -> None:
    """Cancel heartbeat + recover task, signal RX thread, close transport.

    Order matters: cancel any in-flight ``_recover`` task FIRST so it
    cannot race the transport close with a write.
    """
    if self._recover_task is not None:
        self._recover_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._recover_task
        self._recover_task = None
    if self._heartbeat_task is not None:
        self._heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._heartbeat_task
        self._heartbeat_task = None
    self._stop_event.set()
    rx_thread = self._rx_thread
    if rx_thread is not None:
        await asyncio.to_thread(rx_thread.join, _RX_JOIN_TIMEOUT_SEC)
        self._logger.info("rx_thread_exited", clean=not rx_thread.is_alive())
        self._rx_thread = None
    self._transport.close()
    self._state = _MotorState.CLOSED
```

Apply (per RESEARCH.md `stop()` sketch lines 815–830 and Pitfall 7 — set stop_event FIRST, join thread, THEN release source):

```python
async def stop(self) -> None:
    """Signal stop event, join capture thread with timeout, release source.

    Order matters per Pitfall 7: setting _stop_event BEFORE join lets the
    capture thread exit cleanly via the next read() returning. Releasing
    the source BEFORE the thread exits would race the in-flight read().
    """
    self._stop_event.set()
    capture_thread = self._capture_thread
    if capture_thread is not None:
        await asyncio.to_thread(capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC)
        self._logger.info(
            "camera_thread_exited", clean=not capture_thread.is_alive()
        )
        self._capture_thread = None
    if self._source is not None:
        self._source.release()
        self._source = None
    self._state = _CamState.CLOSED
```

#### Capture-thread + asyncio bridge pattern

**Source:** `arduino_motor.py:345-376` (`_rx_loop`)

```python
def _rx_loop(self) -> None:
    """Daemon-thread RX loop. Translates serial errors to LinkLostError."""
    while not self._stop_event.is_set():
        try:
            line = self._transport.read_line(_RX_THREAD_READ_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 -- documented translator
            if self._loop is not None and not self._stop_event.is_set():
                self._loop.call_soon_threadsafe(self._on_link_lost, exc)
            return
        if line is None:
            continue
        try:
            event = parse_line(line)
        except ProtocolParseError as exc:
            self._logger.warning("malformed_line", ...)
            continue
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_rx_event, event)
```

Apply (per RESEARCH.md `Pattern 2` lines 297–343):

```python
def _capture_loop(self) -> None:
    """Daemon-thread capture loop. Translates source errors to CameraStallError.

    The ``noqa: BLE001`` translator below mirrors arduino_motor._rx_loop:356 --
    cv2 driver errors (USB unplug, OBS process crash) MUST translate to a
    typed orchestrator-level error rather than crash the daemon thread silently.
    """
    last_grab_ns: int | None = None
    p95_detector = _P95Detector(self._config.capture_fps)
    warmup_started_ns = time.perf_counter_ns()
    while not self._stop_event.is_set():
        try:
            ok, bgr = self._source.read() if self._source is not None else (False, None)
        except Exception as exc:  # noqa: BLE001 -- documented translator
            if self._loop is not None and not self._stop_event.is_set():
                self._loop.call_soon_threadsafe(
                    self._on_capture_failed,
                    CameraStallError(attempts=list(self._reopen_history)),
                )
            return
        now_ns = time.perf_counter_ns()
        # Stall + read-failure share the same recovery path.
        if not ok or bgr is None or (
            last_grab_ns is not None and (now_ns - last_grab_ns) > _STALL_THRESHOLD_NS
        ):
            self._logger.warning(
                "camera_stall_detected",
                delta_ms=((now_ns - last_grab_ns) / _NS_PER_MS) if last_grab_ns else 0.0,
                threshold_ms=_STALL_THRESHOLD_NS / _NS_PER_MS,
            )
            if not self._attempt_reopen():
                if self._loop is not None and not self._stop_event.is_set():
                    self._loop.call_soon_threadsafe(
                        self._on_capture_failed,
                        CameraStallError(attempts=list(self._reopen_history)),
                    )
                return
            last_grab_ns = None
            continue
        # Build typed Frame inside the thread (validation lives in __post_init__).
        try:
            frame = Frame(
                image=bgr,
                width=self._current_width,
                height=self._current_height,
                timestamp_ns=now_ns,
            )
        except ValueError as exc:
            # Frame.__post_init__ shape mismatch -- treat as stall (Open Q1).
            self._logger.info(
                "frame_shape_mismatch",
                expected=f"{self._current_width}x{self._current_height}",
                observed=str(getattr(bgr, "shape", "n/a")),
                reason=str(exc),
            )
            if not self._attempt_reopen():
                if self._loop is not None and not self._stop_event.is_set():
                    self._loop.call_soon_threadsafe(
                        self._on_capture_failed,
                        CameraStallError(attempts=list(self._reopen_history)),
                    )
                return
            last_grab_ns = None
            continue
        # First-frame gate -- signal start() to return.
        if not self._first_frame_event.is_set():
            self._first_frame_event.set()
        # Warmup-window p95 detector (one-shot fallback).
        if last_grab_ns is not None and not self._fallback_consumed:
            p95_detector.observe(now_ns - last_grab_ns)
            if (now_ns - warmup_started_ns) <= int(_WARMUP_WINDOW_SEC * 1_000_000_000):
                self._maybe_fallback(p95_detector, now_ns)
        last_grab_ns = now_ns
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue_frame, frame)
```

Critical analog points to copy:
1. `noqa: BLE001 -- documented translator` comment style — exactly as `arduino_motor.py:356`.
2. Single guard at top of `while` (`stop_event.is_set()`) — exits cleanly.
3. `loop.call_soon_threadsafe(self._on_*, ...)` for every cross-thread bridge — never `loop.run_in_executor`, never raw `queue.put_nowait` from the thread.
4. Fail-cases short-circuit with `return` (mirror `_rx_loop`'s `return` at line 362).

#### Bounded-queue drop-oldest enqueuer pattern

**Source:** `arduino_motor.py:439-447` (`_enqueue`)

```python
def _enqueue(self, event: ProtocolEvent) -> None:
    """Drop-oldest semantics on a full bounded queue."""
    if self._rx_queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            self._rx_queue.get_nowait()
        self._logger.warning(
            "rx_queue_full", dropped_event_type=type(event).__name__
        )
    self._rx_queue.put_nowait(event)
```

Apply (per RESEARCH.md `Bounded-queue enqueue` lines 521–533):

```python
def _enqueue_frame(self, frame: Frame) -> None:
    """Loop-thread synchronous enqueuer. Drop-oldest semantics.

    Runs only via ``loop.call_soon_threadsafe`` from the capture thread;
    asyncio is single-threaded so no inter-callback race (Pitfall 9).
    """
    if self._frames_queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            self._frames_queue.get_nowait()
        self._logger.warning(
            "frames_queue_full",
            dropped_timestamp_ns=frame.timestamp_ns,
            queue_max=_FRAMES_QUEUE_MAX_SIZE,
        )
    self._frames_queue.put_nowait(frame)
```

Note the WARN-event-name shape: `<noun>_queue_full` with `dropped_<discriminator>` — exact mirror of `rx_queue_full / dropped_event_type`.

#### Async-iterator consumer surface (with consumer-side stale drop)

**Source:** `arduino_motor.py:879-887` (`events`)

```python
async def events(self) -> AsyncIterator[ProtocolEvent]:
    """Async-iterable view of the bounded RX queue."""
    while True:
        ev = await self._rx_queue.get()
        yield ev
```

Apply (per RESEARCH.md `Stale-frame drop` lines 537–552 — extra age check at the consumer side):

```python
async def frames(self) -> AsyncIterator[Frame]:
    """Async-iterable view of fresh Frames. Drops frames older than 100 ms.

    Stale-drop lives on the consumer (CONTEXT.md Area 4 lock) -- the capture
    thread MUST NOT decide age; ``now_ns`` is best read at retrieval.
    """
    while True:
        frame = await self._frames_queue.get()
        now_ns = time.perf_counter_ns()
        age_ns = now_ns - frame.timestamp_ns
        if age_ns > _STALE_FRAME_MAX_AGE_NS:
            self._logger.warning(
                "frame_stale_dropped",
                age_ms=age_ns / _NS_PER_MS,
                threshold_ms=_STALE_FRAME_MAX_AGE_NS / _NS_PER_MS,
            )
            continue
        yield frame
```

#### `_on_capture_failed` loop-thread latcher (mirrors `_on_link_lost`)

**Source:** `arduino_motor.py:481-496` (`_on_link_lost`)

```python
def _on_link_lost(self, exc: Exception) -> None:
    """Loop-thread latcher for RX-thread serial errors."""
    self._state = _MotorState.FAULTED
    self._dispatch_paused = True
    self._latched_error = LinkLostError(f"link_lost: {exc}")
    self._logger.error("link_lost", reason=str(exc))
```

Apply:

```python
def _on_capture_failed(self, error: CameraStallError) -> None:
    """Loop-thread latcher for capture-thread terminal errors.

    Surfaces the typed CameraStallError to the next ``async for frame in
    cam.frames():`` consumer via the latched-error gate.
    """
    self._state = _CamState.FAULTED
    self._latched_error = error
    self._logger.error(
        "camera_stall_unrecoverable",
        attempts=len(error.attempts),
        reopen_history=error.attempts,
    )
```

#### Reopen-with-backoff pattern (`_attempt_reopen`)

**Source:** No exact Phase 2 analog (Phase 2's mid-session recovery is `_recover` in `arduino_motor.py:567-671`, which has different semantics — it re-issues settings/limits, not reopens the link).

**Use RESEARCH.md `Reopen with linear backoff` lines 556–595 as the spec:**

```python
def _attempt_reopen(self) -> bool:
    """Try up to 3 reopens with linear backoff. Records attempts on _reopen_history.

    Returns True on success, False after 3 failures (caller raises CameraStallError).
    Mirrors arduino_motor.py recovery shape: log per-attempt WARN, latch history,
    check stop_event between attempts so close() interrupts cleanly.
    """
    for attempt_index, backoff_ms in enumerate(_REOPEN_BACKOFFS_MS, start=1):
        if self._source is not None:
            self._source.release()
        # responsive to shutdown -- mirror arduino_motor _stop_event.wait pattern
        if self._stop_event.wait(backoff_ms / _MS_PER_SEC):
            return False
        try:
            self._source = self._video_source_factory(
                self._device_index,  # type: ignore[arg-type]  -- non-None after start()
                self._current_width,
                self._current_height,
                self._config.capture_fps,
            )
        except Exception as exc:  # noqa: BLE001 -- documented translator
            self._reopen_history.append((attempt_index, backoff_ms, str(exc)))
            self._logger.warning(
                "camera_reopen_attempt_failed",
                attempt=attempt_index,
                backoff_ms=backoff_ms,
                reason=str(exc),
            )
            continue
        # Wait for first frame after reopen (uses a separate Event reset).
        if self._wait_first_frame_after_reopen():
            self._logger.warning(
                "camera_reopen_succeeded",
                attempt=attempt_index,
                backoff_ms=backoff_ms,
            )
            return True
        self._reopen_history.append((attempt_index, backoff_ms, "first_frame_timeout"))
    return False
```

Critical: the `noqa: BLE001 -- documented translator` style is a verbatim mirror of `arduino_motor.py:356, 524`. mypy + ruff have already accepted this exact comment shape.

#### `_P95Detector` rolling sliding-window pattern

**No Phase 2 analog.** Use RESEARCH.md `Pattern 3` lines 350–383 verbatim. This is a tiny pure helper class (~20 LOC) — self-contained, no external state. Place it ABOVE `ObsCamera` in the same file (similar layering to how `ErrorCode` enum is defined above the parser DTOs in `arduino_protocol.py:119-138`).

```python
class _P95Detector:
    """Rolling p95 of inter-grab nanosecond deltas. Pure transform on a deque.

    No Phase 2 analog -- see RESEARCH.md "Pattern 3: Rolling p95 sliding window"
    (lines 346-383). Sort cost on 30 ints is < 1 us; negligible vs ~33 ms grab.
    """

    def __init__(self, target_fps: int) -> None:
        target_interval_ns = int(1_000_000_000 / target_fps)
        self._budget_ns = int(target_interval_ns * _BUDGET_MULTIPLIER)
        self._window: collections.deque[int] = collections.deque(maxlen=_P95_WINDOW_SIZE)
        self._first_breach_ns: int | None = None

    def observe(self, delta_ns: int) -> None:
        self._window.append(delta_ns)

    def budget_breached_persistent(self, now_ns: int) -> bool:
        if len(self._window) < _P95_WINDOW_SIZE:
            return False
        sorted_window = sorted(self._window)
        p95 = sorted_window[_P95_INDEX]
        if p95 <= self._budget_ns:
            self._first_breach_ns = None
            return False
        if self._first_breach_ns is None:
            self._first_breach_ns = now_ns
            return False
        return (now_ns - self._first_breach_ns) >= _BUDGET_PERSIST_NS

    @property
    def current_p95_ns(self) -> int:
        return sorted(self._window)[_P95_INDEX] if len(self._window) >= _P95_WINDOW_SIZE else 0

    @property
    def budget_ns(self) -> int:
        return self._budget_ns
```

#### Read-only properties pattern (status surface for Phase 7 UI)

**Source:** `arduino_motor.py:195-203`

```python
@property
def state(self) -> _MotorState:
    """Current internal motor state. Read-only."""
    return self._state

@property
def is_dispatch_paused(self) -> bool:
    """``True`` while :meth:`send_motor_angle` is silenced (recovery)."""
    return self._dispatch_paused
```

Apply (CONTEXT.md `Integration Points` — minimal observable state for the Phase 7 dashboard):

```python
@property
def state(self) -> _CamState:
    """Current internal camera state. Read-only."""
    return self._state

@property
def is_running(self) -> bool:
    """``True`` between successful first-frame and stop()/fault."""
    return self._state is _CamState.RUNNING

@property
def current_resolution(self) -> tuple[int, int]:
    """``(width, height)`` currently in effect (1080p target or 720p fallback)."""
    return self._current_width, self._current_height

@property
def last_error(self) -> CameraError | None:
    """Latched terminal error if the camera faulted; None otherwise."""
    return self._latched_error
```

#### Tiger-style fail-fast pattern (`raise ValueError(f"...")`)

**Source:** `arduino_motor.py:711-716` (`_send_raw` buffer-size guard) and `arduino_motor.py:817-833` (`send_limits` finite/range guards) and `core/types.py:62-84` (`Frame.__post_init__`)

Every error message includes BOTH the violated bound AND the offending value:

```python
async def _send_raw(self, line: bytes) -> None:
    if len(line) + 1 > FIRMWARE_INPUT_BUFFER_USABLE:
        raise ValueError(
            f"TX line {len(line)} > {FIRMWARE_INPUT_BUFFER_USABLE}"
        )
    ...
```

Apply to every guard in `obs_camera.py` — RESEARCH.md sketches already comply; copy them.

---

### `pastor_tracker/tests/test_obs_camera_*.py` (split per concern, FakeVideoSource-driven)

**Primary analog (discovery tests):** `pastor_tracker/tests/test_arduino_transport.py`
**Primary analog (lifecycle / async tests):** `pastor_tracker/tests/test_arduino_motor_handshake.py`

#### Imports + module-docstring pattern

**Source:** `test_arduino_transport.py:1-26`

```python
"""Tests for ``pastor_tracker.io.arduino_transport`` (IO-ARD-01).

Covers the discover_arduino_port matrix (4 supported VID:PIDs x manual override
present/absent x multi-match x no-match) and FakeSerialTransport behaviour.
PySerialTransport is intentionally NOT exercised here -- it requires a real
device and is deferred to Phase 8 / QA-04 stage smoke.

Mocking discipline: ``monkeypatch.setattr`` on the module-level ``comports``
binding ... ``SimpleNamespace`` for ListPortInfo stubs -- never
``unittest.mock.MagicMock`` ... -- no mocking ``serial.Serial``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import structlog

from pastor_tracker.io.arduino_transport import (
    SUPPORTED_VID_PIDS,
    ArduinoPortNotFoundError,
    FakeSerialClosedError,
    FakeSerialTransport,
    discover_arduino_port,
)
```

Apply for `test_obs_camera_discovery.py`:

```python
"""Discovery tests for ``pastor_tracker.io.obs_camera`` (IO-CAM-01 / IO-CAM-02).

Covers the discover_obs_camera_index matrix (single match / multiple matches /
no match / configured-name absent). OpenCvVideoSource is intentionally NOT
exercised here -- it requires a real cv2 device and is deferred to Phase 8 /
QA-04 stage smoke.

Mocking discipline: monkeypatch the FilterGraph factory closure (Pitfall 10 --
constructing a real FilterGraph triggers CoInitialize and races pytest workers).
SimpleNamespace stubs for the FilterGraph stand-in -- never unittest.mock.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest
import structlog

from pastor_tracker.io.obs_camera import (
    OBSCameraNotFoundError,
    discover_obs_camera_index,
)
```

#### Helper-stub pattern

**Source:** `test_arduino_transport.py:33-37`

```python
def _fake_port(device: str, vid: int | None, pid: int | None) -> SimpleNamespace:
    return SimpleNamespace(device=device, vid=vid, pid=pid)


_COMPORTS_TARGET = "pastor_tracker.io.arduino_transport.comports"
```

Apply (the FilterGraph stub only needs a `get_input_devices()` method — POD-ish):

```python
def _fake_filter_graph(devices: list[str]) -> SimpleNamespace:
    """SimpleNamespace stub satisfying the FilterGraph.get_input_devices contract."""
    return SimpleNamespace(get_input_devices=lambda: list(devices))


def _factory_returning(devices: list[str]) -> Callable[[], SimpleNamespace]:
    return lambda: _fake_filter_graph(devices)
```

#### Discovery test pattern (parametrize + capture_logs)

**Source:** `test_arduino_transport.py:45-150`

The five discovery tests (`test_discover_returns_first_supported_match`, `test_discover_no_match_raises`, `test_discover_multiple_matches_picks_first`, `test_discover_manual_override_present`, `test_discover_manual_override_absent_raises`) provide the exact assertion shape — `structlog.testing.capture_logs()` for log verification, `pytest.raises(<TypedError>, match="<msg-fragment>")` for typed-error assertions.

Apply for Phase 3 (per RESEARCH.md test-map IO-CAM-01 / IO-CAM-02):

```python
def test_discover_returns_index_for_exact_match() -> None:
    """IO-CAM-01: exact friendly-name match returns the index."""
    factory = _factory_returning(["FaceTime HD", "OBS Virtual Camera", "Logitech BRIO"])
    log = structlog.get_logger(module="obs_camera")
    assert discover_obs_camera_index(
        "OBS Virtual Camera", factory=factory, logger=log
    ) == 1


def test_discover_missing_raises_with_device_list() -> None:
    """IO-CAM-02: no exact match -> OBSCameraNotFoundError carrying the available list."""
    factory = _factory_returning(["FaceTime HD", "Logitech BRIO"])
    log = structlog.get_logger(module="obs_camera")
    with pytest.raises(OBSCameraNotFoundError) as exc_info:
        discover_obs_camera_index("OBS Virtual Camera", factory=factory, logger=log)
    assert exc_info.value.expected == "OBS Virtual Camera"
    assert exc_info.value.available == ["FaceTime HD", "Logitech BRIO"]


def test_discover_multi_match_picks_first_logs_all() -> None:
    """IO-CAM-01: >1 same-name match -> first chosen, ``camera_multiple_matches`` log."""
    factory = _factory_returning([
        "OBS Virtual Camera",
        "Logitech BRIO",
        "OBS Virtual Camera",  # duplicate (rare but legal)
    ])
    log = structlog.get_logger(module="obs_camera")
    with structlog.testing.capture_logs() as caplog:
        result = discover_obs_camera_index("OBS Virtual Camera", factory=factory, logger=log)
    assert result == 0
    multi = [r for r in caplog if r.get("event") == "camera_multiple_matches"]
    assert len(multi) == 1
    assert multi[0]["chosen_index"] == 0
    assert multi[0]["all_indexes"] == [0, 2]
    # Single-match event must NOT have fired.
    assert not any(r.get("event") == "camera_discovered" for r in caplog)
```

Note the symmetry with `test_arduino_transport.py:test_discover_multiple_matches_picks_first` (lines 68–95) — same `[r for r in caplog if r.get("event") == ...]` filter pattern, same "multi-match exclusive of single-match" assertion shape.

#### Async lifecycle test pattern

**Source:** `test_arduino_motor_handshake.py:17-30`

```python
async def test_handshake_succeeds_with_full_preamble(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02 + Pitfall 1: 3-line preamble succeeds."""
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    try:
        assert motor.state == _MotorState.RUNNING
        assert motor.is_dispatch_paused is False
    finally:
        await motor.close()
```

Apply for `test_obs_camera_lifecycle.py`:

```python
async def test_start_returns_after_first_frame(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-CAM-04 + lifecycle: start() awaits first frame; state==RUNNING."""
    fake = FakeVideoSource(script=[
        _ScriptedFrame(bgr=make_solid_bgr(1920, 1080, (0, 0, 0)), ok=True, delay_sec=0.0),
        # ... more frames ...
    ], width=1920, height=1080)
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_args: fake,
        filter_graph_factory=_factory_returning(["OBS Virtual Camera"]),
    )
    await cam.start()
    try:
        assert cam.state == _CamState.RUNNING
        assert cam.is_running is True
        assert cam.current_resolution == (1920, 1080)
    finally:
        await cam.stop()
```

Critical: the `try / finally / await cam.stop()` pattern is verbatim from `test_arduino_motor_handshake.py:26-30`. asyncio_mode="auto" (`pyproject.toml:95`) auto-collects every `async def test_…`.

#### Negative-path test pattern

**Source:** `test_arduino_motor_handshake.py:33-55`

```python
async def test_handshake_rejects_version_mismatch(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02 + CFG-04: READY:v3 raises ProtocolVersionMismatchError."""
    fake = FakeSerialTransport()
    fake.feed_rx(b"READY:v3")
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    with pytest.raises(ProtocolVersionMismatchError, match="expected READY:v2"):
        await motor.start()


async def test_handshake_times_out_when_no_ready(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-ARD-02: silent serial -> HandshakeTimeoutError within ready window."""
    cfg_kwargs: dict[str, object] = {
        **valid_config_dict,
        "arduino_ready_timeout_sec": 0.05,
    }
    fake = FakeSerialTransport()
    motor = ArduinoMotor(fake, Config(**cfg_kwargs))
    with pytest.raises(HandshakeTimeoutError):
        await motor.start()
```

Apply for first-frame timeout (`test_obs_camera_lifecycle.py`):

```python
async def test_first_frame_timeout_obs_not_running(
    valid_config_dict: dict[str, object],
) -> None:
    """IO-CAM-01 + Pitfall 2: read() returns (False, None) -> CameraOpenError."""
    fake = FakeVideoSource(script=[
        _ScriptedFrame(bgr=None, ok=False, delay_sec=0.0)
        for _ in range(50)
    ], width=1920, height=1080)
    # We can't feasibly wait 3 s in a unit test; if the orchestrator exposes
    # a private timeout knob, a per-test override is acceptable. Otherwise
    # use a fixture that monkeypatches _FIRST_FRAME_TIMEOUT_SEC in the module
    # under test (mirror test_arduino_motor_handshake's
    # arduino_ready_timeout_sec=0.05 short-fuse pattern).
    cam = ObsCamera(...)
    with pytest.raises(CameraOpenError, match="OBS"):
        await cam.start()
```

Note: Phase 2 reduces `arduino_ready_timeout_sec` to 0.05 s via a Config override (`test_arduino_motor_handshake.py:48-51`). Phase 3's `_FIRST_FRAME_TIMEOUT_SEC` is a module-level Final constant, NOT a Config field. The planner has a choice: (a) introduce `Config.camera_first_frame_timeout_sec` field, or (b) use `monkeypatch.setattr("pastor_tracker.io.obs_camera._FIRST_FRAME_TIMEOUT_SEC", 0.05)` in tests. RESEARCH.md `Specific Ideas` line 124 names `(b)` as the preferred default unless operator-tunable timing is needed.

#### `valid_config_dict` reuse pattern

**Source:** `tests/conftest.py:7-41`

The fixture already includes `obs_camera_name`, `capture_width`, `capture_height`, `capture_fps` (lines 21–24 of conftest.py). **Reuse it as-is** — do NOT add a Phase 3-specific config fixture. Override per test via `{**valid_config_dict, "capture_fps": 30}` — exactly as `test_arduino_motor_handshake.py:48-51` overrides `arduino_ready_timeout_sec`.

#### Test coverage matrix (from RESEARCH.md `Phase Requirements -> Test Map`)

| Test file | Tests | Requirement |
|-----------|-------|-------------|
| `test_obs_camera_discovery.py` | exact-match / multi-match / missing-with-device-list | IO-CAM-01, IO-CAM-02 |
| `test_obs_camera_lifecycle.py` | start/stop / first-frame timeout / queue-drop / monotonic timestamps | IO-CAM-04 + lifecycle |
| `test_obs_camera_fallback.py` | warmup-breach trigger / post-warmup inhibited / 720p still-breached | IO-CAM-03 |
| `test_obs_camera_stall.py` | stall + reopen success / 3 reopen failures -> CameraStallError | IO-CAM-04 |
| `test_obs_camera_stale_drop.py` | consumer-side stale drop on 100 ms+ frames | IO-CAM-04 |

---

### `pastor_tracker/tests/fixtures/camera_traces.py` (FakeVideoSource + canned ndarrays + helpers)

**Primary analog:** `pastor_tracker/src/pastor_tracker/io/arduino_transport.py:FakeSerialTransport` (lines 131–173) for the Fake itself.
**Secondary analog:** `pastor_tracker/tests/fixtures/arduino_traces.py` (whole file) for canned-data + async helpers + import-path discipline.

#### Module-docstring + import-path discipline

**Source:** `tests/fixtures/arduino_traces.py:1-19`

```python
"""Canned byte traces and async helpers for Arduino I/O integration tests.

Drive the traces via :meth:`FakeSerialTransport.feed_rx` one line at a time
(without trailing ``\\n`` -- parser strips terminators per the contract).

Import path is ``tests.fixtures.arduino_traces`` -- pytest ``rootdir`` is
``pastor_tracker/`` (``testpaths=["tests"]``, packages=``["src/pastor_tracker"]``);
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package. Importing as ``from pastor_tracker.tests.fixtures.arduino_traces``
raises :class:`ModuleNotFoundError`.
"""
from __future__ import annotations

import asyncio
from typing import Final

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import ArduinoMotor, _MotorState
from pastor_tracker.io.arduino_transport import FakeSerialTransport
```

Apply identically — same `tests.fixtures.camera_traces` import path discipline; same "tests/ is NOT a sub-package" warning verbatim.

#### `FakeVideoSource` Fake-impl pattern

**Source:** `arduino_transport.py:131-173` (`FakeSerialTransport`)

```python
class FakeSerialTransport:
    """Bidirectional in-memory fake.

    Tests call :meth:`feed_rx` to enqueue inbound lines (without trailing
    ``\\n``) and assert against :attr:`captured_writes`. FIFO read order;
    :meth:`read_line` blocks on a :class:`threading.Event` until either a line
    is fed or the timeout elapses.
    """

    def __init__(self) -> None:
        self.captured_writes: list[bytes] = []
        self._rx_lines: collections.deque[bytes] = collections.deque()
        self._rx_event = threading.Event()
        self._closed = False

    def feed_rx(self, line: bytes) -> None: ...
    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...
    def close(self) -> None: ...
```

Apply (per RESEARCH.md `FakeVideoSource design` lines 686–757):

```python
@dataclass(frozen=True)
class _ScriptedFrame:
    """One scripted output of FakeVideoSource.read()."""

    bgr: npt.NDArray[np.uint8] | None
    ok: bool
    delay_sec: float                # how long read() blocks before returning


class FakeVideoSource:
    """Bidirectional in-memory fake VideoSource.

    Tests pre-load a script of ScriptedFrame triples; read() pops the next
    one, sleeps for ``delay_sec``, and returns. After exhaustion, returns
    (False, None) until close.

    Mirrors arduino_transport.FakeSerialTransport in shape and threading
    model: collections.deque + threading.Lock, fail-loud on close+write,
    captures observable side effects (set_resolution_calls, release_calls)
    so tests can assert against them.
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
        self.set_resolution_calls: list[tuple[int, int, int]] = []
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
```

Critical analog points:
- `collections.deque` for FIFO script — exact mirror of `FakeSerialTransport._rx_lines` (line 142).
- `threading.Lock` (Phase 3) vs `threading.Event` (Phase 2) — different sync primitives because Phase 2 needs a wakeup signal on `feed_rx`; Phase 3 doesn't (the script is preloaded).
- `release_calls`/`set_resolution_calls` lists for observable assertions — mirror of `captured_writes` (line 141).
- Production-rule comment `# OK in test code; never in app code` next to `time.sleep` — guards against a future refactor copying it into the orchestrator.

#### Helper-frame-builder pattern

**Source:** No Phase 2 analog; RESEARCH.md `make_solid_bgr` (lines 752–756) is the spec:

```python
def make_solid_bgr(width: int, height: int, color: tuple[int, int, int]) -> npt.NDArray[np.uint8]:
    """Allocate a fresh BGR uint8 frame; matches cv2's per-read allocation contract."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame
```

#### `_started_camera()` helper pattern

**Source:** `tests/fixtures/arduino_traces.py:54-69` (`_started_motor`)

```python
async def _started_motor(
    valid_config_dict: dict[str, object],
    transport: FakeSerialTransport | None = None,
) -> tuple[ArduinoMotor, FakeSerialTransport]:
    """Feed the boot preamble, start the motor, return ``(motor, fake)``.

    Caller is responsible for ``await motor.close()``.
    """
    fake = transport if transport is not None else FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    return motor, fake
```

Apply (Phase 3 version returns the camera + fake for downstream assertions):

```python
async def _started_camera(
    valid_config_dict: dict[str, object],
    *,
    script: Iterable[_ScriptedFrame] | None = None,
    devices: list[str] | None = None,
) -> tuple[ObsCamera, FakeVideoSource]:
    """Build a fake-backed ObsCamera, start it, return ``(camera, fake)``.

    Caller is responsible for ``await cam.stop()``.
    """
    if script is None:
        script = [
            _ScriptedFrame(
                bgr=make_solid_bgr(1920, 1080, (0, 0, 0)),
                ok=True,
                delay_sec=0.033,
            )
            for _ in range(60)
        ]
    fake = FakeVideoSource(script=script, width=1920, height=1080)
    devices = devices if devices is not None else ["OBS Virtual Camera"]
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_args, **_kw: fake,
        filter_graph_factory=lambda: SimpleNamespace(get_input_devices=lambda: devices),
    )
    await cam.start()
    return cam, fake
```

#### `wait_for_state` async-poller pattern

**Source:** `tests/fixtures/arduino_traces.py:72-95` (verbatim)

```python
async def wait_for_state(
    motor: ArduinoMotor,
    target_state: _MotorState,
    timeout: float = 1.0,
) -> None:
    """Poll ``motor.state`` at 10 ms cadence until ``target_state`` or ``timeout``.

    Replaces brittle ``asyncio.sleep(0.05)`` cross-thread bridge waits.
    10 ms is faster than the RX thread's ``_RX_THREAD_READ_TIMEOUT_SEC=0.1``
    s loop tick AND faster than typical scheduler jitter.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if motor.state == target_state:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(...)
```

Apply VERBATIM — substitute `ObsCamera` for `ArduinoMotor` and `_CamState` for `_MotorState`. The cross-thread-bridge timing concerns are identical (Phase 3's `_CAPTURE_THREAD_TICK_SEC` = 0.1 s mirrors Phase 2's `_RX_THREAD_READ_TIMEOUT_SEC` = 0.1 s).

---

### `pastor_tracker/pyproject.toml` (modify)

**Analog:** itself (Phase 2 added `pyserial>=3.5,<4.0` to `[project.dependencies]` at line 12)

**Action:** Add `opencv-python>=4.10,<5.0` and `pygrabber==0.2` to `[project.dependencies]` (currently lines 7–13). The `[[tool.mypy.overrides]]` block at line 81–83 already includes `cv2.*` and `pygrabber.*` in `ignore_missing_imports = true` — **do not modify it**, Phase 1 anticipated these imports.

**Diff sketch:**

```toml
dependencies = [
    "pydantic>=2.13,<3.0",
    "pydantic-settings>=2.14,<3.0",
    "structlog>=24.4,<26.0",
    "numpy>=2.4,<3.0",
    "pyserial>=3.5,<4.0",
+   "opencv-python>=4.10,<5.0",
+   "pygrabber==0.2",
]
```

**Verification:** RESEARCH.md `Standard Stack` (lines 117–123) pins these versions; `Environment Availability` (lines 944–945) confirms both are missing from the venv and must be added. No dev-deps changes — `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis` are already shipped (line 19–24).

---

## Shared Patterns

### Frozen dataclass DTO (ndarray-bearing)
**Source:** `pastor_tracker/src/pastor_tracker/core/types.py:44-84` (`Frame` — already shipped)
**Apply to:** Phase 3 produces `Frame` instances inside the capture thread; do NOT redefine. `Frame.__post_init__` is the boundary-validation contract — the capture thread MUST construct via the constructor (raises `ValueError` on shape mismatch); the orchestrator catches that `ValueError` in the capture loop and treats it as a stall (Open Question 1 in 03-RESEARCH.md).

### Tiger-style fail-fast guard with informative message
**Source:** `core/damping.py:47-51`, `core/geometry.py:32-54`, `core/types.py:62-84`, `arduino_motor.py:711-716`, `arduino_transport.py:200-218`
**Apply to:**
- `discover_obs_camera_index` → `OBSCameraNotFoundError(expected=..., available=...)` (mirror `discover_arduino_port:218`)
- `_attempt_reopen` → `CameraStallError(attempts=...)` after 3 strikes (mirror `_on_link_lost:495`)
- `start` first-frame wait → `CameraOpenError("first frame timeout -- is OBS running...")` (mirror `_wait_for_ready:305-308`)
- `Frame.__post_init__` → already enforces shape/timestamp invariants (no change)

**Format invariant:** every error message includes BOTH the violated bound AND the offending value, exactly as `core/damping.py:50` and `arduino_motor.py:715` do.

### Module-level named constants — no magic numbers
**Source:** `arduino_motor.py:90-97`, `arduino_protocol.py:62-112`, `arduino_transport.py:46-58`, `config.py:31-75`
**Apply to:** `obs_camera.py`. Every wire-format / firmware / Windows-DirectShow constant carries a trailing comment citing its source (`# IO-CAM-04 spec`, `# CONTEXT.md Area 4 lock`, `# CAP_DSHOW first-frame latency [CITED OpenCV forum]`). Tests under `tests/**/*.py` are exempt from `PLR2004` per `pyproject.toml:64`.

### structlog logger binding with `module=...`
**Source:** `arduino_motor.py:189`, `arduino_transport.py:201`, `__main__.py:16`
**Apply to:** `obs_camera.py` (`module="obs_camera"`). Event names follow RESEARCH.md `Event catalog` table verbatim:
- `camera_discovered` / `camera_multiple_matches` (mirror `port_discovered` / `port_multiple_matches`)
- `camera_started` / `camera_thread_exited` (mirror `handshake_complete` / `rx_thread_exited`)
- `camera_first_frame_timeout` (mirror `handshake_timeout`)
- `camera_resolution_fallback` / `camera_resolution_breach_at_720p` (no Phase 2 analog — fallback is novel)
- `camera_stall_detected` / `camera_reopen_attempt_failed` / `camera_reopen_succeeded` / `camera_stall_unrecoverable`
- `frame_stale_dropped` / `frames_queue_full` (mirror `rx_queue_full`)

### `from __future__ import annotations` at top of every `.py`
**Source:** every Phase 1/2 source file (`arduino_motor.py:29`, `arduino_transport.py:30`, `arduino_protocol.py:47`, `core/types.py:17`)
**Apply to:** all new source + test + fixture files. Required by `mypy --strict`.

### ≤ 2-level conditional nesting + guard clauses
**Source:** every Phase 1/2 source file; example: `arduino_motor.py:_wait_for_ready` (lines 289-339) has at most one nested `if` per branch
**Apply to:** every function in `obs_camera.py`. RESEARCH.md sketches comply — copy them, don't simplify, don't elaborate.

### Async test layout: docstring with requirement ID → arrange / act / assert → try / finally close
**Source:** `test_arduino_motor_handshake.py:17-30` (verbatim shape)
**Apply to:** every async test in `test_obs_camera_lifecycle.py`, `_fallback.py`, `_stall.py`, `_stale_drop.py`. Every test docstring names the requirement ID (`"""IO-CAM-04: stale frame > 100 ms is dropped on consumer side."""`).

### `valid_config_dict` fixture reuse (no Phase 3-specific fixture)
**Source:** `tests/conftest.py:7-41` — already includes the four camera-relevant fields (`obs_camera_name`, `capture_width`, `capture_height`, `capture_fps`)
**Apply to:** every async test that needs `Config`. Override single fields via `{**valid_config_dict, "capture_fps": 30}`.

### structlog log-event capture in tests
**Source:** `test_arduino_transport.py:82-95` and `:138-150`
**Apply to:** every test that asserts on a log event. Pattern:
```python
with structlog.testing.capture_logs() as caplog:
    result = ...
expected_records = [r for r in caplog if r.get("event") == "<event_name>"]
assert len(expected_records) == 1
record = expected_records[0]
assert record["<key>"] == ...
# Negative-space assertion: the OTHER event must NOT have fired.
assert not any(r.get("event") == "<other_event>" for r in caplog)
```
The negative-space assertion is critical — it pins the discriminator between two related events (e.g., `camera_discovered` vs `camera_multiple_matches`), exactly as `test_arduino_transport.py:95` does for `port_discovered` vs `port_multiple_matches`.

### `noqa: BLE001 -- documented translator` for cross-thread error translation
**Source:** `arduino_motor.py:356`, `arduino_motor.py:524` (and a similar `noqa` in `arduino_transport.py` reopen logic when added)
**Apply to:** the capture-thread's outer `try / except` around `source.read()` and `_attempt_reopen`'s `try / except` around `video_source_factory(...)`. Bare `except Exception` is normally forbidden by ruff `BLE001`; this is the ONE allowed pattern, and the `noqa` comment must be `# noqa: BLE001 -- documented translator` (verbatim — `pyproject.toml:51` enforces ruff `BLE` selection).

### Pure-core / dirty-edges layering
**Source:** CLAUDE.md rule 4 + Phase 2's `arduino_protocol.py` (pure) vs `arduino_transport.py` + `arduino_motor.py` (dirty)
**Apply:** `obs_camera.py` is squarely a dirty edge (cv2 + pygrabber + threading + asyncio). The pure-vs-dirty split inside the file is enforced by import discipline: discovery + Protocol + Fake live in the upper half (no `asyncio` / `threading` calls inside `discover_obs_camera_index`); the orchestrator + capture loop are below. `_P95Detector` is the rarity — pure (no I/O) but file-local for cohesion.

---

## No Analog Found

| File / Pattern | Role | Data Flow | Reason |
|----------------|------|-----------|--------|
| `_P95Detector` (rolling-p95 sliding-window class) | utility (pure transform) | n/a | No Phase 1/2 file does jitter detection. RESEARCH.md `Pattern 3` (lines 346–383) is the spec — copy verbatim. |
| `_maybe_fallback` (one-shot warmup-window decision) | utility (state-mutating helper inside capture thread) | n/a | Phase 2's `_recover` (lines 567–671) is the closest in shape (mid-session state change after a trigger) but has different semantics (re-issue settings, not release+reopen). Use Phase 2's `try / except (TimeoutError, LinkLostError, ValueError)` exception-discrimination pattern (lines 609–645) as the structural analog; the body is RESEARCH-defined. |
| Resolution fallback log-event names (`camera_resolution_fallback`, `camera_resolution_breach_at_720p`) | logging | n/a | Phase 2 has no resolution analog. RESEARCH.md `Event catalog` (lines 842–856) names them; planner pins. |

For all three: planner should reference RESEARCH.md sections by line number rather than searching for non-existent codebase analogs. This is the same fallback Phase 2's `02-PATTERNS.md` documented for the orchestrator concurrency machinery (lines 909–911).

---

## Metadata

**Analog search scope:**
- `pastor_tracker/src/pastor_tracker/io/*.py` (3 Phase 2 files: `arduino_motor.py`, `arduino_transport.py`, `arduino_protocol.py`)
- `pastor_tracker/src/pastor_tracker/core/types.py` (`Frame` DTO already shipped — Phase 3 produces these)
- `pastor_tracker/src/pastor_tracker/config.py` (`obs_camera_name` / `capture_width` / `capture_height` / `capture_fps` already shipped)
- `pastor_tracker/tests/test_arduino_transport.py` + `test_arduino_motor_handshake.py` (test patterns)
- `pastor_tracker/tests/conftest.py` (`valid_config_dict` fixture)
- `pastor_tracker/tests/fixtures/arduino_traces.py` (canned-data + async helpers)
- `pastor_tracker/pyproject.toml` (build config + mypy overrides + ruff lint rules)
- `.planning/phases/02-arduino-i-o/02-PATTERNS.md` (Phase 2 pattern map — same depth/style template)

**Files scanned:** 8 Phase 1/2 sources + 4 Phase 1/2 tests + 1 fixture + 1 build config + 2 phase-3 planning docs (CONTEXT.md, RESEARCH.md) + Phase 2 PATTERNS.md.

**Pattern extraction date:** 2026-05-05

**Phase 1/2 conventions confirmed in scope:**
- `dataclass(frozen=True, slots=True)` for ndarray-bearing DTOs (`Frame` already shipped)
- Frozen Pydantic v2 (`ConfigDict(frozen=True, extra="forbid")`) for config + protocol DTOs
- `from __future__ import annotations` everywhere
- structlog JSON, no `print()`, `module=` keyword binding
- `pytest` `asyncio_mode="auto"` + `valid_config_dict` shared fixture
- Module-level `_FOO: Final[type] = value  # citation` constants
- Tiger-style guard clauses: bound check → informative `<DomainError>` → happy path
- ≤ 2-level conditional nesting (no spaghetti)
- Type hints everywhere; mypy `--strict disallow_any_explicit` clean
- `noqa: BLE001 -- documented translator` is the ONE allowed bare-Exception pattern (cross-thread error translation only)
- `runtime_checkable` Protocol + Real impl + Fake (DI seam) — `SerialTransport`/`PySerialTransport`/`FakeSerialTransport` is the verbatim template for `VideoSource`/`OpenCvVideoSource`/`FakeVideoSource`
- Single bounded `asyncio.Queue` with drop-oldest + WARN log (Phase 2 `_RX_QUEUE_MAX_SIZE=256`; Phase 3 sized to 64 per RESEARCH.md `Sizing the bounded queue`)
- `loop.call_soon_threadsafe(self._on_*, ...)` is the only allowed thread→loop bridge
- Lifecycle close-order: stop_event.set() → thread.join(timeout) → source.release() (Pitfall 7 in both phases)

## PATTERN MAPPING COMPLETE

**Phase:** 3 — Camera I/O
**Files classified:** 5 (1 source + 2 test groupings + 1 fixture + 1 build config)
**Analogs found:** 5 / 5 (all five new/modified files have direct Phase 2 analogs; one novel sub-pattern — `_P95Detector` — falls back to RESEARCH.md `Pattern 3` as spec)

### Coverage
- Files with exact analog: 5 — every Phase 3 file mirrors a Phase 2 file in shape, idiom, and discipline:
  - `obs_camera.py` ← `arduino_motor.py` (orchestrator + queue bridge + lifecycle + error hierarchy + `noqa: BLE001` translator) AND `arduino_transport.py` (`Protocol` + Real + Fake + `discover_*`)
  - `test_obs_camera_*.py` ← `test_arduino_motor_handshake.py` (async + try/finally close) AND `test_arduino_transport.py` (monkeypatch + `SimpleNamespace` + `structlog.testing.capture_logs`)
  - `tests/fixtures/camera_traces.py` ← `tests/fixtures/arduino_traces.py` (canned data + `_started_*` async helper + `wait_for_state` poller)
  - `pyproject.toml` modify ← Phase 2's `pyserial` addition pattern (extend `[project.dependencies]`; mypy overrides at line 81–83 already cover `cv2.*` and `pygrabber.*`)
- Files with role-match analog: 0
- Files with partial analog only: 0
- Files with no analog: 0 — the only sub-pattern without a Phase 2 analog is `_P95Detector`, which lives inside `obs_camera.py` and is documented in RESEARCH.md `Pattern 3` lines 346–383.

### Key Patterns Identified
- The whole Phase 3 module is a **near-verbatim port of Phase 2's three-file split into one file** (no pure-parser layer needed because cv2 + pygrabber do the byte/COM work for us). `obs_camera.py` collapses `arduino_transport.py` (Protocol seam, Real impl, Fake hooks, `discover_*` helper) and `arduino_motor.py` (orchestrator, dedicated thread, asyncio queue bridge, error hierarchy, lifecycle) into one ~350-LOC file.
- The `Frame` DTO is **already shipped** in `core/types.py` — Phase 3 produces it inside the capture thread via the existing `__post_init__` validator; no new DTOs required.
- The `runtime_checkable` Protocol + Real impl + Fake pattern (`SerialTransport`/`PySerialTransport`/`FakeSerialTransport` lines 77–173 of `arduino_transport.py`) ports verbatim to `VideoSource`/`OpenCvVideoSource`/`FakeVideoSource`. Mypy + ruff already accept this exact shape.
- Discovery follows `discover_arduino_port` (lines 176–233 of `arduino_transport.py`) line-for-line: enumerate → exact-match → tiger-style raise carrying available list. The two distinct success log events (`camera_discovered` for single match; `camera_multiple_matches` for >1) are the contractual mirror of `port_discovered`/`port_multiple_matches`.
- Capture thread → asyncio bridge is a direct port of `_rx_loop` (lines 345–376 of `arduino_motor.py`): single producer, `loop.call_soon_threadsafe(self._enqueue_*, ...)` for cross-thread enqueue, drop-oldest in `_enqueue_*`, single `noqa: BLE001 -- documented translator` for cv2/serial driver errors.
- Error class hierarchy mirrors `ArduinoError` + 5 subclasses (lines 116–149 of `arduino_motor.py`): single root `CameraError` + 3 subclasses (`OBSCameraNotFoundError`, `CameraOpenError`, `CameraStallError`). Pipeline orchestrator (Phase 6) can `except CameraError` to halt all camera-related faults uniformly.
- Lifecycle close-order is verbatim Phase 2 + Pitfall 7 of Phase 3: stop_event.set() → thread.join(timeout) → source.release().
- Test layout splits per concern (one file per requirement cluster) — exact mirror of Phase 2's 6-file test split. `valid_config_dict` from `tests/conftest.py` (lines 7–41) is reused as-is; no Phase 3-specific config fixture.
- The two novel pieces (`_P95Detector` rolling-p95 sliding window, one-shot warmup-window fallback decision) have **no Phase 2 analog** — RESEARCH.md `Pattern 3` (lines 346–383) and `Reopen with linear backoff` (lines 556–595) are the spec; planner copies the sketches verbatim.
- `pyproject.toml` modification is the smallest delta in Phase 3: append two lines to `[project.dependencies]` (`opencv-python>=4.10,<5.0`, `pygrabber==0.2`); mypy overrides at lines 81–83 already cover both modules; no dev-deps changes.

### File Created
`D:\System\Documents\PastorTrackingSystem\.planning\phases\03-camera-i-o\03-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can reference Phase 2 analog patterns by file:line number AND RESEARCH.md sketches by line number when writing PLAN.md files for Phase 3.
