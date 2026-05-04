"""Asyncio orchestrator for the OBS Virtual Camera frame source.

Bridges the blocking ``cv2.VideoCapture`` (hidden behind the
:class:`VideoSource` Protocol DI seam) and the asyncio event loop into a
working camera link. Mirrors :mod:`pastor_tracker.io.arduino_motor` almost
line-for-line: a daemon capture thread runs the blocking ``read()`` loop,
constructs a typed :class:`pastor_tracker.core.types.Frame` DTO inside the
thread (timestamp + shape validation off the asyncio hot path), and bridges
to the loop via ``loop.call_soon_threadsafe(_enqueue_frame, frame)`` against
a bounded :class:`asyncio.Queue` with drop-oldest semantics.

Concurrency invariants (mirror Phase 2 -- arduino_motor.py:9-27):
    * Single producer: only the capture thread (lives in Plan 03-02) calls
      ``source.read()``.
    * Single consumer: only the asyncio loop drains ``frames_queue``.
    * Bounded queue: :data:`_FRAMES_QUEUE_MAX_SIZE` = 64; drop-oldest +
      ``frames_queue_full`` WARN on full.
    * Boot-only one-shot DirectShow enumeration (no background re-poll).
    * Stall detection at the capture thread; stale-drop at the consumer.
    * One-shot resolution fallback gated to a 2 s warmup window; never
      re-promote.
    * The two-factory injection pattern (``video_source_factory``,
      ``filter_graph_factory``) defends Pitfall 10 (FilterGraph
      ``CoInitialize`` race in pytest workers).

Sibling split (this plan vs. Plan 03-02):
    * Plan 03-01 (this file): VideoSource Protocol + OpenCvVideoSource real
      impl + discover_obs_camera_index + CameraError hierarchy + _CamState
      enum + _P95Detector helper. All seam + discovery + helpers.
    * Plan 03-02 (appended below the marker): ObsCamera orchestrator class
      (``start``/``stop``/``frames``/``_capture_loop``/``_attempt_reopen``/
      ``_maybe_fallback``/``_on_capture_failed``/``_enqueue_frame``).
"""
from __future__ import annotations

import collections
import enum
from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

import cv2
import numpy as np
import numpy.typing as npt
import structlog
from pygrabber.dshow_graph import FilterGraph

# ---------------------------------------------------------------------------
# Module-level Final constants -- every literal cited (CLAUDE.md rule 6).
# Matches RESEARCH.md Constants table verbatim.
# ---------------------------------------------------------------------------

# Bounded queue + memory ceiling -- 1080p frame ~= 6 MiB; 64 frames -> ~380 MiB.
# RESEARCH "Sizing the bounded queue".
_FRAMES_QUEUE_MAX_SIZE: Final[int] = 64
# CAP_DSHOW first-frame latency [CITED OpenCV forum -- "Slow camera initialization"].
_FIRST_FRAME_TIMEOUT_SEC: Final[float] = 3.0
# Mirrors arduino_motor _RX_JOIN_TIMEOUT_SEC.
_CAPTURE_JOIN_TIMEOUT_SEC: Final[float] = 1.0
# IO-CAM-04 spec -- 200 ms inter-grab ceiling.
_STALL_THRESHOLD_NS: Final[int] = 200_000_000
# IO-CAM-04 spec -- 100 ms consumer-side drop.
_STALE_FRAME_MAX_AGE_NS: Final[int] = 100_000_000
# CONTEXT.md Area 4 lock -- linear backoff 200 / 500 / 1000 ms.
_REOPEN_BACKOFFS_MS: Final[tuple[int, int, int]] = (200, 500, 1000)
# CONTEXT.md Area 3 lock -- 2 s warmup window for one-shot fallback decision.
_WARMUP_WINDOW_SEC: Final[float] = 2.0
# CONTEXT.md Area 3 lock -- p95 budget = 1.2 x target frame interval.
_BUDGET_MULTIPLIER: Final[float] = 1.2
# CONTEXT.md Area 3 -- 1 s sustained breach before fallback fires.
_BUDGET_PERSIST_NS: Final[int] = 1_000_000_000
# CONTEXT.md Area 3 -- 30-frame window @ 30 fps == 1 s of frames.
_P95_WINDOW_SIZE: Final[int] = 30
# int(0.95 * 30) - 1 -- index of the p95 element in a sorted 30-element list.
_P95_INDEX: Final[int] = 28
# IO-CAM-03 spec -- fallback resolution.
_FALLBACK_WIDTH: Final[int] = 1280
_FALLBACK_HEIGHT: Final[int] = 720
_NS_PER_MS: Final[int] = 1_000_000
_MS_PER_SEC: Final[float] = 1_000.0
# Mirror arduino_motor _RX_THREAD_READ_TIMEOUT_SEC -- responsive stop_event tick.
_CAPTURE_THREAD_TICK_SEC: Final[float] = 0.1
_NS_PER_SEC: Final[int] = 1_000_000_000

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

type _ImageArray = npt.NDArray[np.uint8]
type _FilterGraphFactory = Callable[[], FilterGraph]


# ---------------------------------------------------------------------------
# Public surface re-export -- mirror arduino_motor.py:74-84 shape.
# FakeVideoSource is intentionally not exported (lives in
# tests/fixtures/camera_traces.py per Phase 2 fixture discipline).
# ---------------------------------------------------------------------------

__all__ = [
    "CameraError",
    "CameraOpenError",
    "CameraStallError",
    "OBSCameraNotFoundError",
    "OpenCvVideoSource",
    "VideoSource",
    "_CamState",
    "discover_obs_camera_index",
]


# ---------------------------------------------------------------------------
# Error class hierarchy: Root CameraError + 3 typed subclasses.
# Mirror arduino_motor.py ArduinoError + 5 subclasses (Phase 2 line 116-149).
# ---------------------------------------------------------------------------


class CameraError(Exception):
    """Root for all obs_camera-originated errors."""


class OBSCameraNotFoundError(CameraError):
    """OBS VCam friendly-name not found in DirectShow enumeration. -- IO-CAM-02.

    Distinct from :class:`CameraOpenError`: the device is not even enumerated.
    Carries the full enumerated friendly-name list (``available``) for
    operator triage -- mirrors Phase 2's ``port_multiple_matches`` log shape.
    """

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(f"expected camera_name={expected!r}, available={available}")
        self.expected: str = expected
        self.available: list[str] = available


class CameraOpenError(CameraError):
    """VideoSource opened but no first frame within timeout. -- IO-CAM-01.

    Distinct from :class:`OBSCameraNotFoundError`: the device IS enumerated;
    the most likely cause is OBS running but with 'Start Virtual Camera' not
    toggled [CITED OBS Studio Issue #8057, OpenCV Issue #19746].
    """


class CameraStallError(CameraError):
    """Steady-state stall: > 200 ms inter-grab delta, 3 reopen attempts failed. -- IO-CAM-04.

    Carries restart-attempt history (``attempt_index``, ``backoff_ms``,
    ``reason``) for operator triage. The capture thread (Plan 03-02)
    populates ``attempts`` from its ``_reopen_history``.
    """

    def __init__(self, *, attempts: list[tuple[int, int, str]]) -> None:
        super().__init__(f"camera stall unrecoverable after {len(attempts)} attempts")
        self.attempts: list[tuple[int, int, str]] = attempts  # (attempt_idx, backoff_ms, reason)


# ---------------------------------------------------------------------------
# Internal state machine. Full ObsCamera lifecycle ships in Plan 03-02.
# ---------------------------------------------------------------------------


class _CamState(enum.Enum):
    """Internal state machine for ``ObsCamera`` (added in Plan 03-02)."""

    DISCONNECTED = "disconnected"
    OPENING = "opening"
    RUNNING = "running"
    REOPENING = "reopening"
    FAULTED = "faulted"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Section 1: VideoSource Protocol DI seam + OpenCvVideoSource real impl.
# Mirror arduino_transport.py:77-128 (SerialTransport + PySerialTransport).
# ---------------------------------------------------------------------------


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


class OpenCvVideoSource:
    """Real ``cv2.VideoCapture`` wrapper. Single owner of the OS-level handle.

    Constructor opens immediately + applies properties so a missing device
    fails at construction (mirrors PySerialTransport.__init__ idiom).
    ``set_resolution`` is provided for completeness but the orchestrator
    prefers release+reopen on fallback / recovery (cap.set() is flaky on
    CAP_DSHOW [CITED OpenCV Issue #23533]).

    Pitfall 1 mitigation: ``cv2.CAP_DSHOW`` is passed explicitly so both
    pygrabber's enumeration order AND cv2's open path use the same
    ``ICreateDevEnum`` ordering.
    """

    def __init__(self, device_index: int, width: int, height: int, fps: int) -> None:
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        self._apply_props(width, height, fps)

    def _apply_props(self, width: int, height: int, fps: int) -> None:
        # Order per kurokesu.com guide [CITED]: width, height, FPS. FOURCC
        # skipped -- OBS VCam advertises a single stream and DirectShow
        # negotiates the format (Pitfall 3 -- cap.set is advisory; the actual
        # frame shape is read from bgr.shape at capture time).
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        self._cap.set(cv2.CAP_PROP_FPS, float(fps))

    def read(self) -> tuple[bool, _ImageArray | None]:
        # cv2 returns BGR uint8 HxWx3 by default; None on failure (Pitfall 5).
        # cv2 stubs widen the dtype to int|float -- VideoCapture's runtime
        # guarantee is uint8, so we narrow at the seam. Downstream consumers
        # see a typed ``_ImageArray`` (uint8) and ``Frame.__post_init__``
        # validates the shape on construction.
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return False, None
        return True, frame  # type: ignore[return-value]

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        # Phase 3 fallback path: orchestrator prefers release+reopen over
        # in-place set; CAP_DSHOW in-place sets are flaky across vendors per
        # OpenCV Issue #23533 [CITED]. This method exists for tests / future
        # re-use; the orchestrator does NOT call it on the fallback path.
        self._apply_props(width, height, fps)

    def is_opened(self) -> bool:
        return bool(self._cap.isOpened())

    def release(self) -> None:
        self._cap.release()


# ---------------------------------------------------------------------------
# Section 2: Discovery helper -- exact-match by friendly name.
# Mirror arduino_transport.py:176-233 (discover_arduino_port).
# ---------------------------------------------------------------------------


def discover_obs_camera_index(
    expected_name: str,
    *,
    factory: _FilterGraphFactory,
    logger: structlog.stdlib.BoundLogger,
) -> int:
    """Enumerate DirectShow devices; return index whose friendly name matches. -- IO-CAM-01.

    Tiger-style: no exact match -> raise :class:`OBSCameraNotFoundError`
    carrying the full device list. Multiple matches with the same name ->
    first hit, log all (mirrors arduino_transport multi-match rule -- Phase
    2 WARN 5).

    The ``factory`` closure exists for Pitfall 10 (FilterGraph ctor calls
    ``CoInitialize`` -- tests monkeypatch the factory rather than constructing
    a real :class:`FilterGraph`, which avoids ``RPC_E_CHANGED_MODE`` in
    pytest workers). Production wires ``lambda: FilterGraph()``.

    Two distinct success log events (mirrors Phase 2 ``port_discovered`` vs
    ``port_multiple_matches``):

    * ``camera_discovered`` -- single match; keys: ``index``, ``name``,
      ``available_count``.
    * ``camera_multiple_matches`` -- multi-match; keys: ``chosen_index``,
      ``all_indexes``, ``name``.
    """
    graph = factory()
    # pygrabber.dshow_graph.FilterGraph is unstubbed (mypy override at
    # pyproject.toml:81-83). The runtime contract returns list[str].
    devices: list[str] = list(graph.get_input_devices())  # type: ignore[no-untyped-call]
    matches = [(idx, name) for idx, name in enumerate(devices) if name == expected_name]
    if not matches:
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


# ---------------------------------------------------------------------------
# Section 3: Helpers (pure transforms, no I/O).
# ---------------------------------------------------------------------------


class _P95Detector:
    """Rolling p95 of inter-grab nanosecond deltas. Pure transform on a deque.

    No Phase 2 analog -- see 03-RESEARCH.md "Pattern 3: Rolling p95 sliding
    window". Sort cost on 30 ints is < 1 us; negligible vs ~33 ms grab.

    Decision rules (CONTEXT.md Area 3 lock):
        * Need a full window (30 samples) before deciding -- short windows
          return ``False`` so warmup doesn't false-trip.
        * p95 must exceed budget AND the breach must persist for at least
          :data:`_BUDGET_PERSIST_NS` (1 s) before returning ``True``.
        * Recovery (p95 returns to budget) resets ``_first_breach_ns`` to
          ``None`` -- partial breaches do NOT accumulate (one-shot detector).
    """

    def __init__(self, target_fps: int) -> None:
        target_interval_ns = int(_NS_PER_SEC / target_fps)
        self._budget_ns: int = int(target_interval_ns * _BUDGET_MULTIPLIER)
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
        if len(self._window) < _P95_WINDOW_SIZE:
            return 0
        return sorted(self._window)[_P95_INDEX]

    @property
    def budget_ns(self) -> int:
        return self._budget_ns


# ----------------------------------------------------------------------------
# Plan 03-02 appends:
#   - ObsCamera orchestrator class (start/stop/frames/_capture_loop/_attempt_reopen
#     /_maybe_fallback/_on_capture_failed/_enqueue_frame/status properties)
# ----------------------------------------------------------------------------
